"""그래프 불러오기: 어휘 + 원천 데이터로 만든 그래프 + 승인된 변경 제안.

원천 그래프(jeungmi_geokg.ttl)는 언제든 원본에서 다시 만들 수 있게 두고,
검토에서 승인한 변경은 그 위에 다시 얹는다(재생). 승인은 검토 화면에 기록된 결정이고, 누가 했는지는 결정의 검토자 이름에 남는다.
승인과 정정을 일련번호 순서로 합쳐 재생하므로, 정정으로 거둔 승인도 언제 넣고 언제 거뒀는지가 사건으로 그래프에 남는다 (src/graph/editions.py).

재적용 충돌: 새 자료로 원천을 다시 만들면 승인 때의 값이 없을 수 있다. 빼려는 값이나 바꿀 객체가 그래프에 없으면
그 제안은 적용하지 않고 충돌로 남긴다. 일부만 적용하면 모양에 WKT가 둘이 되거나 종류 없는 객체가 생긴다.
읽지 못한 제안 기록은 그 기록만 건너뛴다. 충돌과 건너뛴 기록은 last_report()로 꺼낸다.
last_report()는 지금 쓰는 그래프의 결과여야 한다. 쓸지 아직 모르는 그래프는 build_store로 만들고, 쓰기로 한 뒤에 remember()로 알린다.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pyoxigraph as ox

from src.review.proposals import Proposal, ProposalStore, timeline

ROOT = Path(__file__).resolve().parents[2]
TTL = ROOT / "data" / "processed" / "jeungmi_geokg.ttl"
ONTOLOGY = Path(__file__).resolve().parent / "ontology.ttl"
ALIGNMENT = Path(__file__).resolve().parent / "alignment.ttl"  # 바깥 표준 어휘와 잇기 (schema.org, INSPIRE, 위키데이터)
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
CHANGE_OP = ox.NamedNode("https://yeodh10.github.io/jeungmi-geokg/def#changeOp")
log = logging.getLogger(__name__)


@dataclass
class ReplayReport:
    """재생 결과. 검토 서버가 /api/status로 보여 준다."""
    applied: list[str] = field(default_factory=list)  # 지금 그래프에 살아 있는 승인의 제안 id
    conflicts: list[dict] = field(default_factory=list)  # 적용하지 못한 승인: id, at, reason, problems
    skipped: list[dict] = field(default_factory=list)  # 읽지 못한 기록: index, id, status, error


_last = ReplayReport()


def last_report() -> ReplayReport:
    """마지막으로 load_store가 만든 전체 그래프의 재생 결과. keep으로 고른 공개용 그래프는 넣지 않는다."""
    return _last


def apply_problems(store: ox.Store, p: Proposal) -> list[str]:
    """제안을 지금 그래프에 그대로 적용할 수 없는 까닭. 비어 있으면 적용할 수 있다."""
    out = []
    typed = {c.s for c in p.changes if c.op == "add" and c.p == RDF_TYPE}  # 이 제안이 새로 만드는 객체
    for s in sorted(({p.target} | {c.s for c in p.changes}) - typed):
        if next(store.quads_for_pattern(ox.NamedNode(s), None, None, None), None) is None:
            out.append(f"그래프에 없는 객체를 바꾼다: {s}")
    for c in p.changes:
        if c.op == "remove" and ox.Quad(ox.NamedNode(c.s), ox.NamedNode(c.p), c.o.to_ox(), ox.DefaultGraph()) not in store:
            out.append(f"그래프에 없는 값을 뺀다: {c.s} {c.p} {c.o.value}")
    return out


def replay(store: ox.Store, proposals: list[Proposal], report: ReplayReport | None = None) -> ReplayReport:
    """승인과 정정을 일련번호 순서로 그래프에 얹는다. 실행 중에 승인을 바로 얹은 그래프와 결과가 같다."""
    report = ReplayReport() if report is None else report
    failed = set()  # 적용하지 못한 (제안, 회차). 거둘 것도 없다
    for ev in timeline(proposals):
        p = ev.proposal
        if ev.kind == "withdraw":
            if (p.id, ev.round) in failed:
                failed.discard((p.id, ev.round))
                report.conflicts = [c for c in report.conflicts if c["id"] != p.id]  # 거둔 승인의 충돌은 이제 문제가 아니다
                continue
            real = {i for i, _ in p.steps()
                    if next(store.quads_for_pattern(ox.NamedNode(p.change_uri(i, ev.round)), CHANGE_OP, None, None), None) is not None}
            store.update(p.sparql_withdraw(ev.correction, ev.round, real))
            report.applied.remove(p.id)
            continue
        try:
            problems = apply_problems(store, p)
            if not problems:
                store.update(p.sparql_update(ev.decision, ev.round))
                report.applied.append(p.id)
                continue
        except (SyntaxError, OSError, RuntimeError, ValueError) as e:
            problems = [f"제안을 그래프에 반영하지 못했다: {e}"]
        failed.add((p.id, ev.round))
        report.conflicts.append({"id": p.id, "at": ev.decision.at, "reason": "; ".join(problems), "problems": problems})
        log.warning("승인된 제안 %s을(를) 그래프에 반영하지 못해 건너뛴다: %s", p.id, "; ".join(problems))
    return report


def remember(report: ReplayReport) -> None:
    """report를 last_report()로 내건다. build_store로 만든 그래프를 쓰기로 한 뒤에 부른다."""
    global _last
    _last = report


def build_store(proposals: ProposalStore | list[Proposal] | None = None, keep: Callable | None = None) -> tuple[ox.Store, ReplayReport]:
    """load_store와 같은 그래프와 그 재생 결과. last_report()는 바꾸지 않는다.

    검토 서버가 정정을 저장하기 전에 새 그래프를 먼저 만들 때 쓴다. 정정이 규칙에 막히거나 저장에 실패하면
    지금 그래프가 그대로 쓰이므로, /api/status의 충돌 목록도 지금 그래프의 것이 남아야 한다.
    """
    store = ox.Store()
    for path in (ONTOLOGY, ALIGNMENT, TTL):
        store.load(path=str(path), format=ox.RdfFormat.TURTLE)
    source = ProposalStore() if proposals is None else proposals
    items = source if isinstance(source, list) else source.load()
    report = ReplayReport(skipped=[{k: b[k] for k in ("index", "id", "status", "error")} for b in getattr(source, "broken", [])])
    for b in report.skipped:
        log.warning("제안 기록 %s번(%s)을 읽지 못해 건너뛴다: %s", b["index"], b["id"], b["error"])
    replay(store, items if keep is None else [p for p in items if keep(p)], report)
    return store, report


def load_store(proposals: ProposalStore | list[Proposal] | None = None, keep: Callable | None = None) -> ox.Store:
    """keep을 주면 그 조건에 맞는 승인 제안만 얹는다 (공개 사이트는 공개 자료로 만든 제안만 쓴다).

    proposals에 제안 목록을 바로 주면 파일을 읽지 않는다 (저장 전에 새 그래프를 먼저 만들 때).
    keep 없이 만든 전체 그래프의 재생 결과는 last_report()로 내건다.
    """
    store, report = build_store(proposals, keep)
    if keep is None:
        remember(report)
    return store
