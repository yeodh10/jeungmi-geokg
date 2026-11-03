"""공개 사이트 확인 서버: data/processed/site를 GitHub Pages와 같은 경로(/jeungmi-geokg/) 아래로 연다.

실행: python -m src.graph.serve_site [포트] [--fail 경로 ...]
  python -m src.graph.serve_site 8772  -> http://127.0.0.1:8772/jeungmi-geokg/
  --fail assets/data/places.json       -> 그 경로의 첫 요청에만 503을 준다 (자료를 못 받았을 때 화면을 시험할 때)

GitHub Pages처럼 폴더 주소는 index.html을 열고, 끝에 /가 없는 폴더 주소는 /를 붙여 옮기며,
없는 주소는 사이트의 404.html을 상태 404로 준다. 이 컴퓨터 안(127.0.0.1)에서만 연다.
"""
from __future__ import annotations

import argparse
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from src.graph.app_shell import SITE_PATH

ROOT = Path(__file__).resolve().parents[2]
SITE_DIR = ROOT / "data" / "processed" / "site"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json", ".geojson": "application/geo+json",
         ".ttl": "text/turtle; charset=utf-8", ".pbf": "application/x-protobuf", ".txt": "text/plain; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png"}


def make_handler(site: Path, fail: set[str]):
    failed_once: set[str] = set()
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "serve_site"

        def log_message(self, fmt, *args):  # 요청마다 줄을 남기지 않는다
            pass

        def send_file(self, f: Path, status: int = 200, head: bool = False) -> None:
            data = f.read_bytes()
            self.send_response(status)
            self.send_header("Content-Type", TYPES.get(f.suffix.lower()) or mimetypes.guess_type(f.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if not head:
                self.wfile.write(data)

        def not_found(self, head: bool) -> None:
            page = site / "404.html"
            if page.is_file():
                self.send_file(page, 404, head)
            else:
                self.send_error(404)

        def handle_any(self, head: bool) -> None:
            path = unquote(urlsplit(self.path).path)
            if path in ("/", ""):
                self.send_response(302)
                self.send_header("Location", SITE_PATH + "/")
                self.end_headers()
                return
            if not (path == SITE_PATH or path.startswith(SITE_PATH + "/")):
                self.not_found(head)
                return
            rel = path[len(SITE_PATH):].lstrip("/")
            key = rel.rstrip("/")
            with lock:
                if key in fail and key not in failed_once:
                    failed_once.add(key)
                    body = "확인용으로 한 번 실패시킨 요청입니다. 다시 요청하면 받습니다.".encode("utf-8")
                    self.send_response(503)  # 상태 줄은 latin-1만 되므로 한국어 설명은 본문에 둔다
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    if not head:
                        self.wfile.write(body)
                    return
            target = (site / rel).resolve()
            if site.resolve() not in (target, *target.parents):  # 사이트 폴더 밖으로 나가는 주소는 막는다
                self.not_found(head)
                return
            if target.is_dir():
                if not path.endswith("/"):
                    self.send_response(301)
                    self.send_header("Location", path + "/")
                    self.end_headers()
                    return
                target = target / "index.html"
            if target.is_file():
                self.send_file(target, 200, head)
            else:
                self.not_found(head)

        def do_GET(self):
            self.handle_any(False)

        def do_HEAD(self):
            self.handle_any(True)

    return Handler


class SiteServer(ThreadingHTTPServer):
    """접속 대기열을 늘린 서버. 기본값 5로는 첫 화면이 파일 수십 개를 한꺼번에 받을 때 연결이 거절되어
    앱 모듈과 글꼴이 통째로 빠지는 일이 있었다 (ERR_CONNECTION_REFUSED)."""
    request_queue_size = 128
    daemon_threads = True


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="공개 사이트 확인 서버")
    ap.add_argument("port", nargs="?", type=int, default=8772)
    ap.add_argument("--fail", action="append", default=[], help="첫 요청에 503을 줄 경로 (사이트 안 경로, 예: assets/data/places.json)")
    ap.add_argument("--site", type=Path, default=SITE_DIR, help="열 폴더 (기본 data/processed/site)")
    args = ap.parse_args(argv)
    fail = {f.strip().lstrip("/").removeprefix(SITE_PATH.lstrip("/") + "/").rstrip("/") for f in args.fail}
    server = SiteServer(("127.0.0.1", args.port), make_handler(args.site, fail))
    print(f"http://127.0.0.1:{args.port}{SITE_PATH}/ 에서 {args.site} 를 엽니다" + (f" (첫 요청 실패: {', '.join(sorted(fail))})" if fail else ""), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
