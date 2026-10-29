"""그래프 불러오기: 어휘 + 원천 데이터로 만든 그래프 + 승인된 변경 제안.

원천 그래프(jeungmi_geokg.ttl)는 언제든 원본에서 다시 만들 수 있게 두고,
사람이 승인한 변경은 그 위에 승인 순서대로 SPARQL UPDATE로 얹는다.
"""
from __future__ import annotations

from pathlib import Path

import pyoxigraph as ox

from src.review.proposals import ProposalStore

ROOT = Path(__file__).resolve().parents[2]
TTL = ROOT / "data" / "processed" / "jeungmi_geokg.ttl"
ONTOLOGY = Path(__file__).resolve().parent / "ontology.ttl"


def load_store(proposals: ProposalStore | None = None) -> ox.Store:
    store = ox.Store()
    for path in (ONTOLOGY, TTL):
        store.load(path=str(path), format=ox.RdfFormat.TURTLE)
    approved = [p for p in (proposals or ProposalStore()).load() if p.status == "approved"]
    for p in sorted(approved, key=lambda p: p.decision.at):
        store.update(p.sparql_update())
    return store
