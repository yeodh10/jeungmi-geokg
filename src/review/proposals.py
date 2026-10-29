"""변경 제안: 형식, 저장, 그래프에 반영할 SPARQL 만들기.

제안은 어디서 왔든(규칙 후보, 데이터 갱신 비교, LLM 문서 추출) 같은 형식을 쓴다.
사람이 승인한 제안만 그래프에 반영된다. 반려한 제안도 지우지 않고 기록으로 남긴다.

저장 위치: data/processed/review/proposals.json (환경변수 JGK_PROPOSALS로 바꿀 수 있다)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from src.model import uri

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "data" / "processed" / "review" / "proposals.json"

JGK = "https://yeodh10.github.io/jeungmi-geokg/def#"
PROV = "http://www.w3.org/ns/prov#"
DCTERMS = "http://purl.org/dc/terms/"
XSD = "http://www.w3.org/2001/XMLSchema#"


class Term(BaseModel):
    """트리플의 목적어. kind가 uri면 value가 IRI, literal이면 값이다."""
    kind: Literal["uri", "literal"]
    value: str
    datatype: str | None = None
    lang: str | None = None

    def sparql(self) -> str:
        if self.kind == "uri":
            return f"<{self.value}>"
        v = self.value.replace("\\", "\\\\").replace('"', '\\"')
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


class Decision(BaseModel):
    result: Literal["approved", "rejected"]
    reviewer: str
    at: str
    comment: str = ""


class Proposal(BaseModel):
    id: str
    created_at: str
    source_kind: Literal["rule-candidate", "data-diff", "llm-notice"]
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


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class ProposalStore:
    """제안 목록을 JSON 파일 하나에 둔다. 혼자 쓰는 시연용이라 파일 잠금은 하지 않는다."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path or os.environ.get("JGK_PROPOSALS", DEFAULT_PATH))

    def load(self) -> list[Proposal]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return [Proposal.model_validate(p) for p in data]

    def save(self, proposals: list[Proposal]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([p.model_dump() for p in proposals], ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self.path)

    def get(self, pid: str) -> Proposal | None:
        return next((p for p in self.load() if p.id == pid), None)

    def add_new(self, new: list[Proposal]) -> int:
        """같은 id가 이미 있으면 건너뛴다. 결정이 난 제안을 덮어쓰지 않기 위해서다."""
        current = self.load()
        known = {p.id for p in current}
        fresh = [p for p in new if p.id not in known]
        self.save(current + fresh)
        return len(fresh)

    def decide(self, pid: str, result: str, reviewer: str, comment: str = "") -> Proposal:
        proposals = self.load()
        p = next((p for p in proposals if p.id == pid), None)
        if p is None:
            raise KeyError(pid)
        if p.status != "pending":
            raise ValueError(f"이미 {p.status} 처리된 제안이다")
        p.status = result
        p.decision = Decision(result=result, reviewer=reviewer, at=now(), comment=comment)
        self.save(proposals)
        return p
