"""2026-11-02 앞에 승인한 주소 잇기 제안이 남긴 owl:sameAs를 jgk:sameBuildingAs로 옮기는 제안을 만든다.

실행: python -m src.review.same_building_migration
입력: data/processed/review/proposals.json (승인한 제안), 그 승인을 얹은 그래프 (src.graph.store.load_store)
출력: data/processed/review/proposals.json 에 새 제안 추가 (같은 id가 있으면 건너뛴다)

승인 기록은 이력이라 고쳐 쓰지 않는다. 대신 건물마다 'owl:sameAs 빼기, jgk:sameBuildingAs 넣기'를 한 제안으로 내고
사람이 승인한다. 그러면 옮긴 일도 시점별 판 기록(jgk:hasChange)에 남고, 옛 시점의 값은 그대로 되살아난다.
지금 그래프에 그 owl:sameAs가 남아 있는 것만 낸다 (승인을 거둔 제안, 이미 옮긴 건물은 빠진다).
위키데이터로 잇는 owl:sameAs(증미역)는 우리 객체끼리 잇는 것이 아니라 다루지 않는다.
"""
from __future__ import annotations

import pyoxigraph as ox

from src.graph.store import load_store
from src.review.basemap_proposals import SAME_BUILDING
from src.review.proposals import Change, Proposal, ProposalStore, Term, now

OWL_SAME = "http://www.w3.org/2002/07/owl#sameAs"


def candidates(store: ox.Store, proposals: list[Proposal]) -> list[Proposal]:
    """승인한 제안이 넣은 owl:sameAs 가운데 지금 그래프에 남은 것마다 옮기는 제안 하나."""
    out = []
    for p in proposals:
        if p.status != "approved":
            continue
        for c in p.changes:
            if c.op != "add" or c.p != OWL_SAME:
                continue
            s, o = ox.NamedNode(c.s), ox.NamedNode(c.o.value)
            if not any(store.quads_for_pattern(s, ox.NamedNode(OWL_SAME), o, ox.DefaultGraph())):
                continue
            gis_id = c.s.rsplit("/", 1)[-1]
            out.append(Proposal(
                id=f"fix-same-building-{gis_id}", created_at=now(), source_kind="rule-candidate",
                source_detail="옛 승인의 owl:sameAs를 jgk:sameBuildingAs로 옮기기 (src/review/same_building_migration.py)",
                action="update", target=c.s, target_type="Building",
                summary=f"같은 건물 연결을 owl:sameAs에서 jgk:sameBuildingAs로 옮긴다 (대상 {c.o.value.rsplit('/', 1)[-1]})",
                evidence=[
                    f"제안 {p.id}을(를) 승인하며 owl:sameAs로 이었다 ({p.decision.at if p.decision else '시각 없음'})",
                    "2026-11-02부터 같은 건물 잇기는 jgk:sameBuildingAs로 한다. owl:sameAs로 추론하면 두 객체의 값과 번호가 한 객체에 섞인다 (src/graph/ontology.ttl)",
                    "잇는 대상은 바꾸지 않고 관계만 바꾼다. 옛 승인 기록은 그대로 둔다",
                ],
                changes=[Change(op="remove", s=c.s, p=OWL_SAME, o=c.o),
                         Change(op="add", s=c.s, p=SAME_BUILDING, o=c.o)],
                location=p.location, based_on=p.based_on,
            ))
    return out


def main() -> None:
    proposals = ProposalStore().load()
    props = candidates(load_store(proposals), proposals)
    for p in props:
        print(f"[제안] {p.id}: {p.summary}")
    added = ProposalStore().add_new(props)
    print(f"새 제안 {added}건 (후보 {len(props)}건)")


if __name__ == "__main__":
    main()
