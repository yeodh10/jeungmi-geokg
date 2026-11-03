"""자연어 질의의 SPARQL을 따로 프로세스에서 돌린다.

pyoxigraph는 도는 쿼리를 멈출 방법이 없고, 쿼리가 쌓는 메모리도 막지 못한다. 그래서 쿼리는 서버 밖의 일꾼 프로세스에서 돌리고,
시간이나 메모리를 넘기면 프로세스째 끝낸 뒤 새로 띄운다.

- 일꾼은 이 파일을 스크립트로 실행한 것이다. 서버의 무거운 모듈을 불러오지 않는다.
- 표준 입력으로 그래프(N-Quads)와 쿼리를 받고 표준 출력으로 결과를 돌려준다. 묶음 하나는 8바이트 길이와 내용이다.
- 일꾼은 그래프 사본을 들고 있다. Pool은 부를 때마다 그래프 객체와 트리플 수를 견줘, 다르면 새 일꾼을 띄운다.
  승인은 언제나 사건 기록 트리플을 더하고, 정정으로 거두면 그래프 객체가 바뀌므로 이 둘로 판을 가릴 수 있다.
- 일꾼을 띄우는 데는 몇 초 걸린다. 그동안은 잠깐만 기다린 뒤 QueryBusy로 답한다. 작업 스레드가 쌓이지 않게 하려는 것이다.
- 메모리는 부르는 쪽이 짧은 간격으로 재서, 넘으면 끝낸다. 윈도는 커밋 크기, 리눅스는 /proc의 RSS를 본다.
  다른 체제에서는 재지 못해 시간 제한만 듣는다. 한 간격 동안 더 쓰는 만큼은 넘칠 수 있다. 리눅스 쪽은 코드만 있고 돌려 보지 못했다.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import signal
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pyoxigraph as ox

MAX_ROWS = 500
MAX_CELL_CHARS = 100_000  # 칸 하나의 글자 수. 지금 그래프에서 가장 긴 모양이 15,000자다
MAX_RESULT_CHARS = 4_000_000  # 결과 전체의 글자 수. 가장 긴 모양 500개를 합쳐도 60만 자다
CUT_MARK = "…(잘림)"  # 잘린 칸의 끝. 잘린 WKT는 읽히지 않아 지도에 그리지 않는다
MEMORY_LIMIT = 1_000_000_000  # 일꾼 하나의 메모리(바이트). 평소 110MB쯤이고, 평가 정답과 예시 쿼리를 다 돌려도 200MB를 넘지 않았다
START_SECONDS = 60  # 일꾼이 그래프를 다 올릴 때까지 기다리는 시간
POLL_SECONDS = 0.05  # 메모리를 재는 간격
ERRORS = {e.__name__: e for e in (SyntaxError, OSError, RuntimeError, ValueError)}  # 일꾼의 오류를 부른 쪽에서 같은 종류로 다시 낸다
log = logging.getLogger(__name__)


class QueryTimeout(Exception):
    """쿼리가 제한 시간을 넘겨 일꾼을 끝냈다. 새 일꾼이 뜨는 동안 다시 쓰게 해도 소용없으니 ask()도 바로 올린다."""


class QueryTooBig(QueryTimeout):
    """쿼리가 메모리를 넘겼거나 일꾼이 쓰러졌다. 부르는 쪽은 시간 초과와 똑같이 다룬다."""


class QueryBusy(Exception):
    """앞선 쿼리가 돌고 있거나 일꾼을 준비하고 있다."""


def read_frame(stream) -> bytes | None:
    head = stream.read(8)
    if len(head) < 8:
        return None
    size = struct.unpack(">Q", head)[0]
    body = stream.read(size)
    return body if len(body) == size else None


def write_frame(stream, body: bytes) -> None:
    stream.write(struct.pack(">Q", len(body)))
    stream.write(body)
    stream.flush()


# 일꾼 쪽


def cell(term) -> str | None:
    if term is None:
        return None
    return term.value if hasattr(term, "value") else str(term)


def execute(store: ox.Store, query: str) -> dict:
    """쿼리를 돌려 결과를 상한 안에서 읽는다. 하나 더 읽어 보고 MAX_ROWS를 넘어 잘렸는지 안다."""
    res = store.query(query)
    if isinstance(res, bool) or res.__class__.__name__ == "QueryBoolean":
        return {"boolean": bool(res), "vars": [], "rows": [], "truncated": False, "cut_cells": 0}
    names = [v.value for v in res.variables]
    rows, total, cut, truncated = [], 0, 0, False
    for solution in res:
        row = [cell(solution[n]) for n in names]
        for i, value in enumerate(row):
            if value is not None and len(value) > MAX_CELL_CHARS:
                row[i] = value[:MAX_CELL_CHARS] + CUT_MARK
                cut += 1
        total += sum(len(v) for v in row if v is not None)
        if len(rows) >= MAX_ROWS or total > MAX_RESULT_CHARS:
            truncated = True
            break
        rows.append(row)
    return {"vars": names, "rows": rows, "truncated": truncated, "cut_cells": cut}


def answer(store: ox.Store, query: str) -> dict:
    try:
        return execute(store, query)
    except Exception as e:  # 쿼리 오류는 내용을 붙여 돌려준다. 일꾼은 살아 있다
        kind = next((k for k, v in ERRORS.items() if isinstance(e, v)), "RuntimeError")
        return {"error": kind, "message": str(e)}


def serve() -> None:
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    graph = read_frame(stdin)
    if graph is None:
        return
    store = ox.Store()
    store.load(input=graph, format=ox.RdfFormat.N_QUADS)  # bulk_load는 1초쯤 빠르지만 잠깐 900MB를 쓴다
    del graph
    requests = queue.SimpleQueue()

    def listen():
        while (frame := read_frame(stdin)) is not None:
            requests.put(frame)
        os._exit(0)  # 부른 쪽이 사라졌다. 돌던 쿼리째 끝낸다
    threading.Thread(target=listen, daemon=True).start()
    write_frame(stdout, json.dumps({"pid": os.getpid()}).encode("utf-8"))
    while True:
        query = json.loads(requests.get())["query"]
        write_frame(stdout, json.dumps(answer(store, query), ensure_ascii=False).encode("utf-8"))


# 부르는 쪽


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
    _kernel32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
    _kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _QUERY_LIMITED = 0x1000
    _STILL_ACTIVE = 259

    def memory_of(pid: int) -> int | None:
        """프로세스의 커밋 크기(바이트). 프로세스가 없거나 이미 끝났으면 None."""
        handle = _kernel32.OpenProcess(_QUERY_LIMITED, False, pid)
        if not handle:
            return None
        try:
            code = wintypes.DWORD()
            if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != _STILL_ACTIVE:
                return None  # 끝난 프로세스도 누가 핸들을 쥐고 있으면 열린다
            counters = _Counters()
            counters.cb = ctypes.sizeof(counters)
            ok = _kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
            return counters.PagefileUsage if ok else None
        finally:
            _kernel32.CloseHandle(handle)
else:
    def memory_of(pid: int) -> int | None:
        """프로세스의 RSS(바이트). 프로세스가 없거나 /proc이 없는 체제면 None."""
        try:
            with open(f"/proc/{pid}/statm", encoding="ascii") as f:
                return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        except (OSError, IndexError, ValueError):
            return None


class Worker:
    """일꾼 프로세스 하나. 그래프를 다 올릴 때까지 생성자가 기다린다."""

    def __init__(self, graph: bytes):
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self.proc = subprocess.Popen([sys.executable, "-I", str(Path(__file__).resolve())],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, creationflags=flags)
        self.pid = None
        self.replies = queue.SimpleQueue()
        threading.Thread(target=self._listen, name="nl-worker-reply", daemon=True).start()
        try:
            write_frame(self.proc.stdin, graph)
            first = self.replies.get(timeout=START_SECONDS)
            if first is None:
                raise RuntimeError("일꾼이 그래프를 올리지 못했다")
            self.pid = first["pid"]  # 윈도 가상환경의 python.exe는 진짜 파이썬을 자식으로 띄우므로 proc.pid와 다르다
        except BaseException:
            self.stop()
            raise

    def _listen(self) -> None:
        stream = self.proc.stdout
        try:
            while (frame := read_frame(stream)) is not None:
                self.replies.put(json.loads(frame))
        except (OSError, ValueError):
            pass
        self.replies.put(None)

    def run(self, query: str, seconds: float) -> dict:
        try:
            write_frame(self.proc.stdin, json.dumps({"query": query}).encode("utf-8"))
        except OSError:
            raise QueryTooBig("쿼리를 돌리던 프로세스가 멈췄다. 잠시 뒤에 다시 물어 주세요") from None
        deadline = time.monotonic() + seconds
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise QueryTimeout(f"쿼리가 {seconds:g}초 안에 끝나지 않았다. 조건을 좁히거나 더 단순하게 써야 한다")
            try:
                reply = self.replies.get(timeout=min(left, POLL_SECONDS))
            except queue.Empty:
                used = memory_of(self.pid)
                if used is not None and used > MEMORY_LIMIT:
                    raise QueryTooBig("쿼리가 메모리를 너무 많이 썼다. 조건을 좁히거나 더 단순하게 써야 한다") from None
                continue
            if reply is None:
                raise QueryTooBig("쿼리를 돌리던 프로세스가 멈췄다. 조건을 좁히거나 더 단순하게 써야 한다")
            if "error" in reply:
                raise ERRORS[reply["error"]](reply["message"])
            return reply

    def stop(self) -> None:
        if self.pid is not None and self.pid != self.proc.pid:
            try:
                os.kill(self.pid, signal.SIGTERM)  # 윈도에서는 그 자리에서 끝낸다
            except OSError:
                pass
        self.proc.kill()
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.warning("일꾼 %s이(가) 끝나지 않는다", self.proc.pid)


@dataclass
class Job:
    """띄우는 중인 일꾼."""
    store: ox.Store
    size: int
    done: threading.Event = field(default_factory=threading.Event)
    worker: Worker | None = None


class Pool:
    """일꾼 하나를 지금 그래프에 맞춰 둔다. 쿼리는 한 번에 하나만 돌고, turn이 일꾼 상태도 함께 지킨다."""

    def __init__(self):
        self.turn = threading.BoundedSemaphore(1)
        self.worker = None
        self.store = None  # 일꾼이 올린 그래프. 객체를 쥐고 있어야 id가 재사용돼 판을 헷갈리는 일이 없다
        self.size = -1
        self.job = None

    def _start(self, job: Job) -> None:
        try:
            job.worker = Worker(job.store.dump(format=ox.RdfFormat.N_QUADS))
        except Exception:
            log.exception("쿼리 일꾼을 띄우지 못했다")
        with self.turn:  # 도는 쿼리가 끝난 뒤에 갈아 끼운다
            if self.job is job:
                self.job = None
                old, self.worker, self.store, self.size = self.worker, job.worker, job.store, job.size
            else:  # 그새 그래프가 또 바뀌었다
                old = job.worker
        if old is not None:
            old.stop()
        job.done.set()

    def _begin(self, store: ox.Store, size: int) -> None:
        self.job = Job(store, size)
        threading.Thread(target=self._start, args=(self.job,), name="nl-worker-start", daemon=True).start()

    def _current(self, store: ox.Store) -> Worker | None:
        """이 그래프를 올린 일꾼. 없으면 띄우기 시작하고 None. turn을 쥐고 부른다."""
        size = len(store)
        if self.worker is not None and self.store is store and self.size == size:
            return self.worker
        if self.job is None or self.job.store is not store or self.job.size != size:
            self._begin(store, size)
        return None

    def prepare(self, store: ox.Store, wait: float = 0.0) -> bool:
        """이 그래프의 일꾼을 띄워 두고, wait초 안에 준비되면 True."""
        if not self.turn.acquire(timeout=wait):
            return False
        try:
            if self._current(store) is not None:
                return True
            job = self.job
        finally:
            self.turn.release()
        return job.done.wait(wait) and job.worker is not None

    def run(self, store: ox.Store, query: str, seconds: float) -> dict:
        if not self.turn.acquire(timeout=min(seconds, 2)):
            raise QueryBusy("앞선 쿼리가 아직 돌고 있다")
        try:
            worker = self._current(store)
            if worker is None:
                raise QueryBusy("쿼리 일꾼을 준비하고 있다")
            try:
                return worker.run(query, seconds)
            except QueryTimeout:
                self.worker = None
                worker.stop()
                self._begin(store, len(store))
                raise
        finally:
            self.turn.release()

    def pid(self) -> int | None:
        return self.worker.pid if self.worker is not None else None

    def close(self) -> None:
        if self.worker is not None:
            self.worker.stop()


pool = Pool()
atexit.register(pool.close)


if __name__ == "__main__":
    serve()
