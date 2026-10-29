"""변경 제안: 형식, 저장, 그래프에 반영할 SPARQL 만들기.

제안은 어디서 왔든(규칙 후보, 데이터 갱신 비교, LLM 문서 추출) 같은 형식을 쓴다.
사람이 승인한 제안만 그래프에 반영된다. 반려한 제안도 지우지 않고 기록으로 남긴다.
결정이 틀렸으면 지우지 않고 정정한다. 정정은 corrections에 앞의 결정과 함께 새 기록으로 쌓인다.

저장 위치: data/processed/review/proposals.json (환경변수 JGK_PROPOSALS로 바꿀 수 있다)
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
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
LANG_TAG = re.compile(r"^[a-zA-Z]{1,8}(-[a-zA-Z0-9]{1,8})*$")
NOTICE_NO = re.compile(r"고시\s*제\s*([\d-]+)\s*호")
ECHAR = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}  # SPARQL 문자열 안의 이스케이프
_lock = threading.Lock()  # 같은 프로세스 안에서 읽고 고치고 저장하는 동안 다른 결정이 끼어들지 않게 한다


def iri(v: str) -> str:
    """SPARQL의 <...> 안에 그대로 넣어도 되는 IRI인지 pyoxigraph로 만들어 보고 확인한다."""
    try:
        ox.NamedNode(v)
    except ValueError as e:
        raise ValueError(f"올바른 IRI가 아니다: {v!r} ({e})") from None
    return v


class Term(BaseModel):
    """트리플의 목적어. kind가 uri면 value가 IRI, literal이면 값이다."""
    kind: Literal["uri", "literal"]
    value: str
    datatype: str | None = None
    lang: str | None = None

    @field_validator("datatype")
    @classmethod
    def _datatype(cls, v):
        return None if v is None else iri(v)

    @field_validator("lang")
    @classmethod
    def _lang(cls, v):
        if v is not None and not LANG_TAG.match(v):
            raise ValueError(f"올바른 언어 태그가 아니다: {v!r}")
        return v

    @model_validator(mode="after")
    def _uri_value(self):
        if self.kind == "uri":
            iri(self.value)
        return self

    def sparql(self) -> str:
        if self.kind == "uri":
            return f"<{self.value}>"
        v = re.sub(r'[\\"\n\r\t]', lambda m: ECHAR[m.group()], self.value)  # 줄바꿈이 든 값도 문법이 깨지지 않게
        if self.lang:
            return f'"{v}"@{self.lang}'
        if self.datatype:
            return f'"{v}"^^<{self.datatype}>'
        return f'"{v}"'


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


class Correction(BaseModel):
    """결정 정정 한 건. replaced는 정정으로 바뀌기 전의 결정이다."""
    from_status: Literal["approved", "rejected"]
    to_status: Literal["pending", "approved", "rejected"]
    reviewer: str
    at: str
    reason: str
    replaced: Decision | None = None


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
    status: Literal["pending", "approved", "rejected"] = "pending"
    decision: Decision | None = None
    corrections: list[Correction] = Field(default_factory=list)

    @field_validator("target")
    @classmethod
    def _target(cls, v):
        return iri(v)

    @property
    def uri(self) -> str:
        return f"{uri.BASE}/proposal/{self.id}"

    def sparql_update(self) -> str:
        """승인됐을 때 그래프에 실행할 SPARQL UPDATE. 바꾸는 내용과 출처 기록을 함께 넣는다."""
        removes = [c for c in self.changes if c.op == "remove"]
        adds = [c for c in self.changes if c.op == "add"]
        parts = []
        if removes:
            parts.append("DELETE DATA {\n" + "\n".join(
                f"  <{c.s}> <{c.p}> {c.o.sparql()} ." for c in removes) + "\n}")
        prov = [
            f"  <{self.uri}> a <{JGK}ChangeProposal>, <{PROV}Entity> .",
            f"  <{self.uri}> <{JGK}proposalAction> {Term(kind='literal', value=self.action).sparql()} .",
            f"  <{self.uri}> <{JGK}affects> <{self.target}> .",
            f"  <{self.uri}> <{DCTERMS}description> {Term(kind='literal', value=self.summary, lang='ko').sparql()} .",
            f"  <{self.uri}> <{JGK}sourceKind> {Term(kind='literal', value=self.source_kind).sparql()} .",
            f"  <{self.uri}> <{DCTERMS}created> {Term(kind='literal', value=self.created_at, datatype=XSD + 'dateTime').sparql()} .",
            f"  <{self.target}> <{PROV}wasInfluencedBy> <{self.uri}> .",
        ]
        if self.decision:
            prov += [
                f"  <{self.uri}> <{JGK}decidedBy> {Term(kind='literal', value=self.decision.reviewer).sparql()} .",
                f"  <{self.uri}> <{JGK}decidedAt> {Term(kind='literal', value=self.decision.at, datatype=XSD + 'dateTime').sparql()} .",
            ]
        parts.append("INSERT DATA {\n" + "\n".join(
            [f"  <{c.s}> <{c.p}> {c.o.sparql()} ." for c in adds] + prov) + "\n}")
        return " ;\n".join(parts)


def group_of(p: Proposal) -> tuple[str, str | None]:
    """검토 화면에서 함께 볼 묶음. 같은 고시에서 나왔으면 고시 번호로, 아니면 같은 표나 같은 비교에서 나온 것끼리 묶는다."""
    for e in p.evidence:
        m = NOTICE_NO.search(e)
        if m:
            named = re.search(r"\(([^()]*" + NOTICE_NO.pattern + r"[^()]*)\)", e)
            return f"notice:{m.group(1)}", (named.group(1) if named else m.group(0)).strip()
    if p.id.startswith(("table-", "llm-", "diff-", "rule-basemap-link-")) and "-" in p.id:
        return p.id.rsplit("-", 1)[0], p.source_detail
    return f"single:{p.id}", None


DONE_KO = {"approved": "승인된", "rejected": "반려된"}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class ProposalStore:
    """제안 목록을 JSON 파일 하나에 둔다. 같은 프로세스 안에서는 잠그고, 여러 프로세스가 함께 쓰는 것은 막지 않는다."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path or os.environ.get("JGK_PROPOSALS", DEFAULT_PATH))

    def load(self) -> list[Proposal]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return [Proposal.model_validate(p) for p in data]

    def save(self, proposals: list[Proposal]) -> None:
        """같은 폴더의 임시 파일에 다 쓴 뒤 바꿔 끼운다. 쓰다가 멈춰도 원래 파일은 그대로 남는다."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=self.path.parent,
                                         prefix=self.path.name + ".", suffix=".tmp") as f:
            f.write(json.dumps([p.model_dump() for p in proposals], ensure_ascii=False, indent=2))
        for i in range(10):  # Windows에서는 누가 파일을 읽는 중이면 잠깐 바꿔 끼울 수 없다
            try:
                os.replace(f.name, self.path)
                return
            except PermissionError:
                if i == 9:
                    os.unlink(f.name)
                    raise
                time.sleep(0.03)

    def get(self, pid: str) -> Proposal | None:
        return next((p for p in self.load() if p.id == pid), None)

    def add_new(self, new: list[Proposal]) -> int:
        """같은 id가 이미 있으면 건너뛴다. 결정이 난 제안을 덮어쓰지 않기 위해서다."""
        with _lock:
            current = self.load()
            known = {p.id for p in current}
            fresh = [p for p in new if p.id not in known]
            self.save(current + fresh)
        return len(fresh)

    def decide(self, pid: str, result: str, reviewer: str, comment: str = "") -> Proposal:
        return self.decide_many([pid], result, reviewer, comment)[0]

    def decide_many(self, pids: list[str], result: str, reviewer: str, comment: str = "") -> list[Proposal]:
        """여러 제안을 같은 결정으로 한 번에 저장한다. 하나라도 결정할 수 없으면 아무것도 바꾸지 않는다.

        그래프를 다시 불러올 때 승인 시각이 같으면 파일 순서대로 반영하므로, pids도 파일 순서로 다룬다.
        """
        with _lock:
            proposals = self.load()
            by_id = {p.id: p for p in proposals}
            for pid in pids:
                p = by_id.get(pid)
                if p is None:
                    raise KeyError(pid)
                if p.status != "pending":
                    raise ValueError(f"이미 {DONE_KO[p.status]} 제안이다" + (f" ({pid})" if len(pids) > 1 else ""))
            at = now()
            chosen = [p for p in proposals if p.id in set(pids)]
            for p in chosen:
                p.status = result
                p.decision = Decision(result=result, reviewer=reviewer, at=at, comment=comment)
            self.save(proposals)
        return chosen

    def correct(self, pid: str, to_status: str, reviewer: str, reason: str) -> Proposal:
        """결정된 제안을 다른 상태로 바로잡는다. 앞의 결정은 지우지 않고 정정 기록에 넣는다.

        대기로 되돌리면 결정이 없어지고, 승인이나 반려로 바꾸면 정정 사유를 의견으로 한 새 결정이 된다.
        """
        with _lock:
            proposals = self.load()
            p = next((p for p in proposals if p.id == pid), None)
            if p is None:
                raise KeyError(pid)
            if p.status == "pending":
                raise ValueError("아직 결정하지 않은 제안이다")
            if p.status == to_status:
                raise ValueError(f"이미 {DONE_KO[p.status]} 제안이다")
            at = now()
            p.corrections.append(Correction(from_status=p.status, to_status=to_status, reviewer=reviewer,
                                            at=at, reason=reason, replaced=p.decision))
            p.status = to_status
            p.decision = None if to_status == "pending" else Decision(result=to_status, reviewer=reviewer, at=at, comment=reason)
            self.save(proposals)
        return p
