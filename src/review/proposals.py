"""변경 제안: 형식, 저장, 그래프에 반영할 SPARQL 만들기.

제안은 어디서 왔든(규칙 후보, 데이터 갱신 비교, LLM 문서 추출) 같은 형식을 쓴다.
검토에서 승인한 제안만 그래프에 반영된다. 반려한 제안도 지우지 않고 기록으로 남긴다.
승인은 검토 화면에 기록된 결정이고, 누가 했는지는 결정의 검토자 이름에 남는다.
결정이 틀렸으면 지우지 않고 정정한다. 정정은 corrections에 앞의 결정과 함께 새 기록으로 쌓인다.
결정과 정정에는 단조 증가 일련번호(seq)가 붙는다. 그래프는 승인과 정정을 이 번호 순서로 재생한다 (timeline, src/graph/store.py).

저장 위치: data/processed/review/proposals.json (환경변수 JGK_PROPOSALS로 바꿀 수 있다)
파일을 고칠 때는 프로세스끼리도 OS 파일 잠금(같은 폴더의 <파일 이름>.lock)을 잡고, 잠금 안에서 다시 읽은 내용에 고쳐 쓴다.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import pyoxigraph as ox
from pydantic import BaseModel, Field, field_validator, model_validator

from src.model import uri

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "data" / "processed" / "review" / "proposals.json"

JGK = "https://yeodh10.github.io/jeungmi-geokg/def#"
PROV = "http://www.w3.org/ns/prov#"
DCTERMS = "http://purl.org/dc/terms/"
XSD = "http://www.w3.org/2001/XMLSchema#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
INFLUENCED_BY = PROV + "wasInfluencedBy"
LANG_TAG = re.compile(r"[a-zA-Z]{1,8}(-[a-zA-Z0-9]{1,8})*")
PROPOSAL_ID = re.compile(r"[A-Za-z0-9._:-]+")  # 제안 URI 끝에 그대로 붙으므로 IRI와 SPARQL에서 뜻이 없는 글자만 받는다
NOTICE_KEY = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]*")
NOTICE_NO = re.compile(r"고시\s*제\s*([\d-]+)\s*호")
ECHAR = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}  # SPARQL 문자열 안의 이스케이프
KST = timezone(timedelta(hours=9))
ORDER_STEP = 1_000_000  # 재생 순서 번호 = 일련번호 * ORDER_STEP + 제안 안의 차례
LOCK_TIMEOUT = 30  # 초. 다른 프로세스가 이만큼 잠금을 놓지 않으면 저장을 포기하고 오류를 낸다
_lock = threading.RLock()  # 같은 프로세스 안에서 읽고 고치고 저장하는 동안 다른 결정이 끼어들지 않게 한다
_unnumbered = itertools.count(1)  # 일련번호 없는 결정을 그래프에 바로 얹을 때 쓰는 순서 (시험, 승인 전 검사)
log = logging.getLogger(__name__)

if os.name == "nt":
    import msvcrt

    def _try_lock(f) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(f) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(f) -> None:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(f) -> None:
        fcntl.flock(f, fcntl.LOCK_UN)


@contextmanager
def _file_lock(path: Path):
    """같은 프로세스의 다른 스레드와 다른 프로세스를 함께 막는다. 안에서 다시 잡으면 안 된다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(path.with_name(path.name + ".lock"), "a+b", buffering=0) as f:
        deadline = time.monotonic() + LOCK_TIMEOUT
        while True:
            try:
                _try_lock(f)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"제안 파일 잠금을 {LOCK_TIMEOUT}초 안에 잡지 못했다: {path}") from None
                time.sleep(0.02)
        try:
            yield
        finally:
            _unlock(f)


def iri(v: str) -> str:
    """SPARQL의 <...> 안에 그대로 넣어도 되는 IRI인지 pyoxigraph로 만들어 보고 확인한다."""
    try:
        ox.NamedNode(v)
    except ValueError as e:
        raise ValueError(f"올바른 IRI가 아니다: {v!r} ({e})") from None
    return v


def lang_tag(v: str) -> str:
    if not LANG_TAG.fullmatch(v):
        raise ValueError(f"올바른 언어 태그가 아니다: {v!r}")
    return v


def proposal_id(v: str) -> str:
    if not PROPOSAL_ID.fullmatch(v):
        raise ValueError(f"제안 id에는 영문, 숫자, 점, 쌍점, 하이픈만 쓴다: {v!r}")
    return v


def text(v: str) -> str:
    """UTF-8로 저장할 수 없는 글자(외톨이 대리문자)가 든 값은 받지 않는다."""
    try:
        v.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("저장할 수 없는 글자(외톨이 대리문자)가 들어 있다") from None
    return v


def parse_time(v: str) -> datetime:
    """시각 문자열을 시간대가 있는 datetime으로. 시간대가 없으면 한국 시간으로 본다.

    초 단위 시간대(+09:00:30)와 14시간을 넘는 시간대는 xsd:dateTime으로 적을 수 없어 받지 않는다.
    """
    dt = datetime.fromisoformat(v.strip())
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    offset = dt.utcoffset()
    if offset.seconds % 60 or offset.microseconds or abs(offset) > timedelta(hours=14):
        raise ValueError(f"xsd:dateTime으로 적을 수 없는 시간대다: {v!r}")
    return dt


class Term(BaseModel):
    """트리플의 목적어. kind가 uri면 value가 IRI, literal이면 값이다."""
    kind: Literal["uri", "literal"]
    value: str
    datatype: str | None = None
    lang: str | None = None

    @field_validator("value")
    @classmethod
    def _value(cls, v):
        return text(v)

    @field_validator("datatype")
    @classmethod
    def _datatype(cls, v):
        return None if v is None else iri(v)

    @field_validator("lang")
    @classmethod
    def _lang(cls, v):
        return None if v is None else lang_tag(v)

    @model_validator(mode="after")
    def _kind_fields(self):
        if self.kind == "uri":
            iri(self.value)
            if self.lang or self.datatype:
                raise ValueError("IRI에는 언어 태그나 자료형을 붙일 수 없다")
        elif self.lang and self.datatype:
            raise ValueError("언어 태그와 자료형을 함께 붙일 수 없다")
        return self

    def sparql(self) -> str:
        # model_copy는 검사를 거치지 않으므로 SPARQL에 넣을 때 다시 본다
        if self.kind == "uri":
            return f"<{iri(self.value)}>"
        v = re.sub(r'[\\"\n\r\t]', lambda m: ECHAR[m.group()], self.value)  # 줄바꿈이 든 값도 문법이 깨지지 않게
        if self.lang:
            return f'"{v}"@{lang_tag(self.lang)}'
        if self.datatype:
            return f'"{v}"^^<{iri(self.datatype)}>'
        return f'"{v}"'

    def to_ox(self) -> ox.NamedNode | ox.Literal:
        if self.kind == "uri":
            return ox.NamedNode(self.value)
        if self.lang:
            return ox.Literal(self.value, language=self.lang.lower())  # 저장소는 언어 태그를 소문자로 둔다
        if self.datatype:
            return ox.Literal(self.value, datatype=ox.NamedNode(self.datatype))
        return ox.Literal(self.value)

    @classmethod
    def from_ox(cls, o) -> Term:
        if isinstance(o, ox.NamedNode):
            return cls(kind="uri", value=o.value)
        if o.language:
            return cls(kind="literal", value=o.value, lang=o.language)
        return cls(kind="literal", value=o.value, datatype=None if o.datatype.value == XSD + "string" else o.datatype.value)


class Change(BaseModel):
    op: Literal["add", "remove"]
    s: str
    p: str
    o: Term

    @field_validator("s", "p")
    @classmethod
    def _iri(cls, v):
        return iri(v)


class Decision(BaseModel):
    result: Literal["approved", "rejected"]
    reviewer: str
    at: str
    comment: str = ""
    seq: int | None = None  # 결정 순서. 옛 기록에는 없어서 읽을 때 시각순으로 붙인다 (number_decisions)

    @field_validator("at")
    @classmethod
    def _at(cls, v):
        parse_time(v)
        return v

    @field_validator("reviewer", "comment")
    @classmethod
    def _text(cls, v):
        return text(v)


class Correction(BaseModel):
    """결정 정정 한 건. replaced는 정정으로 바뀌기 전의 결정이다."""
    from_status: Literal["approved", "rejected"]
    to_status: Literal["pending", "approved", "rejected"]
    reviewer: str
    at: str
    reason: str
    replaced: Decision | None = None
    seq: int | None = None  # Decision.seq와 같은 번호 줄을 쓴다

    @field_validator("at")
    @classmethod
    def _at(cls, v):
        parse_time(v)
        return v

    @field_validator("reviewer", "reason")
    @classmethod
    def _text(cls, v):
        return text(v)


def stamp(at: str) -> str:
    return Term(kind="literal", value=parse_time(at).isoformat(), datatype=XSD + "dateTime").sparql()


def order_base(who) -> int:
    """이 결정이나 정정이 남기는 사건들의 시작 번호. 일련번호가 없으면 얹는 차례를 쓴다."""
    seq = who.seq if who is not None else None
    return (next(_unnumbered) if seq is None else seq) * ORDER_STEP


def change_step(op: str, triple: str, record: str) -> list[str]:
    """트리플 하나를 바꾸는 두 문장. 그래프가 실제로 바뀔 때만 record를 남긴다."""
    if op == "remove":
        return [f"INSERT {{\n{record}\n}} WHERE {{ {triple} }}", f"DELETE DATA {{ {triple} . }}"]
    return [f"INSERT {{\n{record}\n}} WHERE {{ FILTER NOT EXISTS {{ {triple} }} }}", f"INSERT DATA {{ {triple} . }}"]


class Proposal(BaseModel):
    id: str
    created_at: str
    source_kind: Literal["rule-candidate", "data-diff", "llm-notice", "notice-table"]
    source_detail: str
    action: Literal["create", "update", "retire", "link"]
    target: str
    target_type: str
    summary: str
    evidence: list[str] = Field(default_factory=list)
    changes: list[Change]
    location: tuple[float, float] | None = None  # 경도, 위도 (검토 화면 지도용)
    # 이 제안의 근거 자료 (어휘의 dataset-* 이름). 비어 있으면 공개 사이트에 얹지 않는다 (src/graph/publish.py)
    based_on: list[str] = Field(default_factory=list)
    status: Literal["pending", "approved", "rejected"] = "pending"
    decision: Decision | None = None
    corrections: list[Correction] = Field(default_factory=list)
    # 같은 고시에서 나온 제안을 묶는 열쇠. 수집기가 정한 시군구코드와 고시 번호로 만든다(예: 11500-2026-140). 모델 글에서 뽑지 않는다
    notice_key: str | None = None
    # 사람이 봐야 하는 경고 (예: '입력 가드 경고', 'PNU 불일치')
    flags: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id(cls, v):
        return proposal_id(v)

    @field_validator("target")
    @classmethod
    def _target(cls, v):
        return iri(v)

    @field_validator("notice_key")
    @classmethod
    def _notice_key(cls, v):
        if v is not None and not NOTICE_KEY.fullmatch(v):
            raise ValueError(f"올바른 고시 열쇠가 아니다: {v!r}")
        return v

    @field_validator("source_detail", "target_type", "summary")
    @classmethod
    def _text(cls, v):
        return text(v)

    @field_validator("evidence", "flags", "based_on")
    @classmethod
    def _texts(cls, v):
        return [text(x) for x in v]

    @model_validator(mode="after")
    def _decided(self):
        if self.status != "pending" and self.decision is None:
            raise ValueError("결정된 제안에 결정 기록이 없다")
        return self

    @property
    def uri(self) -> str:
        return f"{uri.BASE}/proposal/{self.id}"

    def approvals_before(self) -> int:
        """정정으로 거둔 승인의 수. 다음 승인이 이 제안의 몇 번째 승인인지 정한다."""
        return sum(c.from_status == "approved" for c in self.corrections)

    def steps(self) -> list[tuple[int, Change]]:
        """반영 차례: 빼기 먼저, 그다음 넣기. 번호는 changes 안의 자리(1부터)다."""
        numbered = list(enumerate(self.changes, 1))
        return [x for x in numbered if x[1].op == "remove"] + [x for x in numbered if x[1].op == "add"]

    def change_uri(self, i: int, round_: int = 0, undo: bool = False) -> str:
        """바꾼 트리플 기록의 URI. 첫 승인은 {제안 URI}/change/{번호}, 다시 승인하면 {회차}-{번호}, 거두면 undo-{회차}-{번호}."""
        if undo:
            return f"{self.uri}/change/undo-{round_ + 1}-{i}"
        return f"{self.uri}/change/{i}" if round_ == 0 else f"{self.uri}/change/{round_ + 1}-{i}"

    def _check(self) -> None:
        """model_copy는 검사를 거치지 않으므로 SPARQL을 만들기 전에 다시 본다."""
        proposal_id(self.id)
        iri(self.target)
        for c in self.changes:
            iri(c.s)
            iri(c.p)

    def _record(self, node: str, op: str, s: str, p: str, o: str, action: str, order: int, who) -> str:
        """그래프가 바뀐 사건 하나: 무엇을 넣거나 뺐는지, 어느 제안이, 누가, 언제, 몇 번째로."""
        rows = [f"  <{node}> a <{RDF}Statement> ; <{JGK}changeOp> {Term(kind='literal', value=op).sparql()} ;",
                f"    <{RDF}subject> <{s}> ; <{RDF}predicate> <{p}> ; <{RDF}object> {o} ;",
                f"    <{JGK}changeOf> <{self.uri}> ; <{JGK}proposalAction> {Term(kind='literal', value=action).sparql()} ; <{JGK}changeOrder> {order}"]
        if who is not None:
            rows.append(f"    ; <{JGK}decidedBy> {Term(kind='literal', value=who.reviewer).sparql()} ; <{JGK}decidedAt> {stamp(who.at)}")
        return "\n".join(rows) + " ."

    def sparql_update(self, decision: Decision | None = None, round_: int | None = None) -> str:
        """승인됐을 때 그래프에 실행할 SPARQL UPDATE. 바꾸는 내용과 출처 기록을 함께 넣는다.

        바꾼 트리플 하나하나도 RDF 진술(rdf:Statement, {제안 URI}/change/{번호})로 남긴다.
        그래프가 실제로 바뀐 변경만 남겨야 시각 T의 값을 그 트리플의 마지막 사건으로 정할 수 있다 (src/graph/editions.py).
        그래서 트리플마다 '바뀔 때만 기록하기'와 '바꾸기'를 차례로 실행한다.
        decision, round_는 지난 승인을 재생할 때 준다. 없으면 지금 결정이다.
        """
        self._check()
        d = decision or self.decision
        r = self.approvals_before() if round_ is None else round_
        base = order_base(d)
        parts = []
        for k, (i, c) in enumerate(self.steps(), 1):
            node = self.change_uri(i, r)
            record = self._record(node, c.op, c.s, c.p, c.o.sparql(), self.action, base + k, d)
            parts += change_step(c.op, f"<{c.s}> <{c.p}> {c.o.sparql()}", f"{record}\n  <{self.uri}> <{JGK}hasChange> <{node}> .")
        prov = [
            f"  <{self.uri}> a <{JGK}ChangeProposal>, <{PROV}Entity> .",
            f"  <{self.uri}> <{JGK}proposalAction> {Term(kind='literal', value=self.action).sparql()} .",
            f"  <{self.uri}> <{JGK}affects> <{self.target}> .",
            f"  <{self.uri}> <{DCTERMS}description> {Term(kind='literal', value=self.summary, lang='ko').sparql()} .",
            f"  <{self.uri}> <{JGK}sourceKind> {Term(kind='literal', value=self.source_kind).sparql()} .",
            f"  <{self.uri}> <{DCTERMS}created> {Term(kind='literal', value=self.created_at, datatype=XSD + 'dateTime').sparql()} .",
            f"  <{self.target}> <{INFLUENCED_BY}> <{self.uri}> .",
            self._record(self.change_uri(0, r), "add", self.target, INFLUENCED_BY, f"<{self.uri}>", self.action, base, d),
        ]
        if d:
            prov += [
                f"  <{self.uri}> <{JGK}decidedBy> {Term(kind='literal', value=d.reviewer).sparql()} .",
                f"  <{self.uri}> <{JGK}decidedAt> {stamp(d.at)} .",
            ]
        parts.append("INSERT DATA {\n" + "\n".join(prov) + "\n}")
        return " ;\n".join(parts)

    def sparql_withdraw(self, correction: Correction, round_: int, real: set[int]) -> str:
        """승인을 거둘 때 실행할 SPARQL UPDATE. real은 그 승인이 실제로 바꾼 변경의 번호다.

        바뀐 값은 거꾸로 되돌리고 그것도 사건으로 남긴다. 제안 노드는 빠지지만 지난 사건 기록은 남아 이력이 사라지지 않는다.
        """
        self._check()
        base = order_base(correction)
        parts = []
        for k, (i, c) in enumerate((x for x in reversed(self.steps()) if x[0] in real), 1):
            op = "remove" if c.op == "add" else "add"
            record = self._record(self.change_uri(i, round_, undo=True), op, c.s, c.p, c.o.sparql(), "withdraw", base + k, correction)
            parts += change_step(op, f"<{c.s}> <{c.p}> {c.o.sparql()}", record)
        link = self._record(self.change_uri(0, round_, undo=True), "remove", self.target, INFLUENCED_BY, f"<{self.uri}>",
                            "withdraw", base, correction)
        parts += [f"DELETE WHERE {{ <{self.uri}> ?p ?o }}", f"DELETE DATA {{ <{self.target}> <{INFLUENCED_BY}> <{self.uri}> . }}",
                  f"INSERT DATA {{\n{link}\n}}"]
        return " ;\n".join(parts)


@dataclass
class GraphEvent:
    """그래프를 바꾸는 사건 하나: 승인(approve) 또는 승인 거두기(withdraw)."""
    kind: Literal["approve", "withdraw"]
    proposal: Proposal
    round: int  # 이 제안의 몇 번째 승인인가 (0부터)
    seq: int | None
    decision: Decision | None = None  # approve일 때 그 결정
    correction: Correction | None = None  # withdraw일 때 그 정정


def number_decisions(proposals: list[Proposal]) -> None:
    """일련번호가 없는 옛 결정과 정정에 시각순으로 번호를 붙인다. 같은 시각이면 파일 순서다.

    파일이 그대로면 언제 읽어도 같은 번호가 나오고, 다음 저장 때 파일에 함께 들어간다.
    """
    todo, top = [], 0
    for i, p in enumerate(proposals):
        records = [r for c in p.corrections for r in (c.replaced, c) if r is not None]
        records += [p.decision] if p.decision else []
        for j, r in enumerate(records):
            if r.seq is None:
                todo.append((parse_time(r.at), i, j, r))
            else:
                top = max(top, r.seq)
    todo.sort(key=lambda x: x[:3])
    for n, (*_, r) in enumerate(todo, top + 1):
        if r.seq is None:  # 같은 기록을 두 제안이 함께 가리키면 한 번만 붙인다
            r.seq = n


def next_seq(proposals: list[Proposal]) -> int:
    seqs = [r.seq for p in proposals for c in p.corrections for r in (c.replaced, c) if r is not None and r.seq is not None]
    seqs += [p.decision.seq for p in proposals if p.decision and p.decision.seq is not None]
    return max(seqs, default=0) + 1


def events_of(p: Proposal) -> list[GraphEvent]:
    """제안 하나의 지난 승인과 거두기, 그리고 지금 살아 있는 승인."""
    out, round_ = [], 0
    for c in p.corrections:
        if c.from_status == "approved" and c.replaced is not None:
            out += [GraphEvent("approve", p, round_, c.replaced.seq, decision=c.replaced),
                    GraphEvent("withdraw", p, round_, c.seq, correction=c)]
            round_ += 1
    if p.status == "approved" and p.decision is not None:
        out.append(GraphEvent("approve", p, round_, p.decision.seq, decision=p.decision))
    return out


def timeline(proposals: list[Proposal]) -> list[GraphEvent]:
    """승인과 정정을 합친 재생 순서."""
    number_decisions(proposals)
    return sorted((e for p in proposals for e in events_of(p)), key=lambda e: e.seq)


def group_of(p: Proposal) -> tuple[str, str | None]:
    """검토 화면에서 함께 볼 묶음. 같은 고시에서 나왔으면 고시로, 아니면 같은 표나 같은 비교에서 나온 것끼리 묶는다.

    notice_key가 있으면 그것만 쓴다. 근거 문장은 모델이 쓴 글일 수 있어서 번호를 찾지 않는다.
    notice_key가 생기기 전에 코드로 만든 표 제안만 첫 근거 줄(파일 머리의 고시 번호)로 묶는다.
    """
    if p.notice_key:
        return f"notice:{p.notice_key}", f"고시 {p.notice_key}"
    m = NOTICE_NO.search(p.evidence[0]) if p.source_kind == "notice-table" and p.evidence else None
    if m:
        named = re.search(r"\(([^()]*" + NOTICE_NO.pattern + r"[^()]*)\)", p.evidence[0])
        return f"notice:{m.group(1)}", (named.group(1) if named else m.group(0)).strip()
    if p.id.startswith(("table-", "llm-", "diff-", "rule-basemap-link-", "rule-basemap-shape-")) and "-" in p.id:
        return p.id.rsplit("-", 1)[0], p.source_detail
    return f"single:{p.id}", None


DONE_KO = {"approved": "승인된", "rejected": "반려된"}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Added(int):
    """새로 넣은 건수. differ에는 같은 id가 이미 있는데 내용이 달라 건너뛴 제안의 id를 담는다."""
    differ: list[str]

    def __new__(cls, n: int, differ: list[str]):
        self = super().__new__(cls, n)
        self.differ = differ
        return self


def content(p: Proposal) -> tuple:
    return p.action, p.target, [c.model_dump() for c in p.changes]


class ProposalFileError(Exception):
    """제안 파일 전체를 읽지 못했다. 기록 하나가 아니라 파일이 깨졌다.

    JSON이 아니거나, 맨 바깥이 목록이 아니거나({}, null, 숫자), 목록인데 기록이 하나도 읽히지 않는다([1, 2, null], [{"id": "x"}]).
    검토 서버는 503으로 답하고, 깨진 파일을 덮어쓰지 않는다.
    """


class ProposalStore:
    """제안 목록을 JSON 파일 하나에 둔다. 스레드끼리, 프로세스끼리 잠그고, 잠금 안에서 다시 읽어 고친다.

    읽지 못하는 기록은 그 기록만 건너뛴다. 건너뛴 기록은 broken으로 꺼내 볼 수 있고, 저장해도 파일에서 사라지지 않는다.
    기록이 하나도 읽히지 않거나 파일 전체를 읽지 못하면 ProposalFileError를 낸다. 그대로 두면 승인을 하나도 얹지 않은 그래프가
    아무 경고 없이 뜬다. 읽기 단계에서 멈추므로 저장까지 가지 않아 깨진 파일을 덮어쓰지 않는다.
    """

    def __init__(self, path: Path | None = None):
        self.path = Path(path or os.environ.get("JGK_PROPOSALS", DEFAULT_PATH))
        self.broken: list[dict] = []  # 마지막 load에서 읽지 못한 기록: index, id, status, error, raw

    def _read(self) -> tuple[list[Proposal], list[dict]]:
        if not self.path.exists():
            return [], []
        for i in range(50):  # Windows에서는 다른 프로세스가 바꿔 끼우는 순간에 잠깐 열 수 없다
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                break
            except PermissionError:
                if i == 49:
                    raise
                time.sleep(0.02)
            except ValueError as e:  # JSONDecodeError와 UnicodeDecodeError도 ValueError다
                raise ProposalFileError(f"JSON으로 읽지 못했다: {e}") from e
        if not isinstance(data, list):
            raise ProposalFileError("맨 바깥이 목록이 아니다")
        good, broken = [], []
        for i, raw in enumerate(data):
            try:
                good.append(Proposal.model_validate(raw))
            except ValueError as e:
                row = raw if isinstance(raw, dict) else {}
                broken.append({"index": i, "id": row.get("id"), "status": row.get("status"), "error": str(e), "raw": raw})
        if data and not good:  # 일부만 깨지면 그 기록만 건너뛰지만, 모두 깨진 목록은 파일이 깨진 것이다
            raise ProposalFileError(f"기록 {len(data)}개를 하나도 읽지 못했다")
        number_decisions(good)
        return good, broken

    def _write(self, proposals: list[Proposal], broken: list[dict]) -> None:
        """같은 폴더의 임시 파일에 다 쓴 뒤 바꿔 끼운다. 쓰다가 멈춰도 원래 파일은 그대로 남고 임시 파일도 남지 않는다."""
        rows = [p.model_dump() for p in proposals]
        for b in sorted(broken, key=lambda b: b["index"]):  # 읽지 못한 기록은 있던 자리에 그대로 둔다
            rows.insert(b["index"], b["raw"])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(rows, ensure_ascii=False, indent=2))
            for i in range(50):  # Windows에서는 누가 파일을 읽는 중이면 잠깐 바꿔 끼울 수 없다
                try:
                    os.replace(name, self.path)
                    return
                except PermissionError:
                    if i == 49:
                        raise
                    time.sleep(0.03)
        except BaseException:
            with suppress(OSError):
                os.unlink(name)
            raise

    def load(self) -> list[Proposal]:
        proposals, self.broken = self._read()
        return proposals

    def save(self, proposals: list[Proposal]) -> None:
        with _file_lock(self.path):
            self._write(proposals, self._read()[1])

    def get(self, pid: str) -> Proposal | None:
        return next((p for p in self.load() if p.id == pid), None)

    def add_new(self, new: list[Proposal]) -> Added:
        """같은 id가 이미 있으면 건너뛴다. 결정이 난 제안을 덮어쓰지 않기 위해서다.

        돌려주는 값은 새로 넣은 건수이고, 같은 id인데 내용이 달라진 제안은 .differ에 담고 경고를 남긴다.
        """
        with _file_lock(self.path):
            current, broken = self._read()
            known = {p.id: p for p in current} | {b["id"]: None for b in broken if isinstance(b["id"], str)}
            fresh, differ = [], []
            for p in new:
                if p.id not in known:
                    known[p.id] = p
                    fresh.append(p)
                elif known[p.id] is not None and content(known[p.id]) != content(p):
                    differ.append(p.id)
            self._write(current + fresh, broken)
        for pid in differ:
            log.warning("같은 id의 제안 %s이(가) 이미 있는데 내용이 달라 건너뛴다", pid)
        return Added(len(fresh), differ)

    def decide(self, pid: str, result: str, reviewer: str, comment: str = "", before_save: Callable | None = None) -> Proposal:
        return self.decide_many([pid], result, reviewer, comment, before_save)[0]

    def decide_many(self, pids: list[str], result: str, reviewer: str, comment: str = "",
                    before_save: Callable | None = None) -> list[Proposal]:
        """여러 제안을 같은 결정으로 한 번에 저장한다. 하나라도 결정할 수 없으면 아무것도 바꾸지 않는다.

        그래프를 다시 불러올 때 일련번호 순서대로 반영하므로, pids도 파일 순서로 번호를 받는다.
        before_save(바뀐 제안 목록, 전체 제안 목록)는 저장 직전에 잠금 안에서 부른다. 일련번호까지 정해진 결정을
        그래프에 먼저 반영할 때 쓴다. 여기서 예외가 나면 아무것도 저장하지 않는다.
        """
        with _file_lock(self.path):
            proposals, broken = self._read()
            by_id = {p.id: p for p in proposals}
            for pid in pids:
                p = by_id.get(pid)
                if p is None:
                    raise KeyError(pid)
                if p.status != "pending":
                    raise ValueError(f"이미 {DONE_KO[p.status]} 제안이다" + (f" ({pid})" if len(pids) > 1 else ""))
            at = now()
            chosen = [p for p in proposals if p.id in set(pids)]
            for n, p in enumerate(chosen, next_seq(proposals)):
                p.status = result
                p.decision = Decision(result=result, reviewer=reviewer, at=at, comment=comment, seq=n)
            if before_save:
                before_save(chosen, proposals)
            self._write(proposals, broken)
        return chosen

    def correct(self, pid: str, to_status: str, reviewer: str, reason: str, before_save: Callable | None = None) -> Proposal:
        """결정된 제안을 다른 상태로 바로잡는다. 앞의 결정은 지우지 않고 정정 기록에 넣는다.

        대기로 되돌리면 결정이 없어지고, 승인이나 반려로 바꾸면 정정 사유를 의견으로 한 새 결정이 된다.
        before_save는 decide_many와 같다. 승인을 거둘 때는 전체 제안 목록을 load_store에 그대로 넘겨 새 그래프를 먼저 만들 수 있다.
        """
        with _file_lock(self.path):
            proposals, broken = self._read()
            p = next((p for p in proposals if p.id == pid), None)
            if p is None:
                raise KeyError(pid)
            if p.status == "pending":
                raise ValueError("아직 결정하지 않은 제안이다")
            if p.status == to_status:
                raise ValueError(f"이미 {DONE_KO[p.status]} 제안이다")
            at, n = now(), next_seq(proposals)
            p.corrections.append(Correction(from_status=p.status, to_status=to_status, reviewer=reviewer,
                                            at=at, reason=reason, replaced=p.decision, seq=n))
            p.status = to_status
            p.decision = None if to_status == "pending" else Decision(result=to_status, reviewer=reviewer, at=at, comment=reason, seq=n)
            if before_save:
                before_save([p], proposals)
            self._write(proposals, broken)
        return p
