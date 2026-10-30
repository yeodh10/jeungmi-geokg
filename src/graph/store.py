"""그래프 불러오기: 어휘 + 원천 데이터로 만든 그래프 + 승인된 변경 제안.

원천 그래프(jeungmi_geokg.ttl)는 언제든 원본에서 다시 만들 수 있게 두고,
사람이 승인한 변경은 그 위에 승인 순서대로 SPARQL UPDATE로 얹는다.
반영하지 못하는 승인 기록이 하나 있어도 나머지로 그래프를 만들고, 그 제안은 경고 기록만 남긴다.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pyoxigraph as ox

from src.review.proposals import ProposalStore

ROOT = Path(__file__).resolve().parents[2]
TTL = ROOT / "data" / "processed" / "jeungmi_geokg.ttl"
ONTOLOGY = Path(__file__).resolve().parent / "ontology.ttl"
ALIGNMENT = Path(__file__).resolve().parent / "alignment.ttl"  # 바깥 표준 어휘와 잇기 (schema.org, INSPIRE, 위키데이터)


def load_store(proposals: ProposalStore | None = None) -> ox.Store:
    store = ox.Store()
    for path in (ONTOLOGY, ALIGNMENT, TTL):
        store.load(path=str(path), format=ox.RdfFormat.TURTLE)
    approved = [p for p in (proposals or ProposalStore()).load() if p.status == "approved"]
    for p in sorted(approved, key=lambda p: p.decision.at if p.decision else ""):
        try:
            store.update(p.sparql_update())
        except (SyntaxError, OSError, RuntimeError, ValueError) as e:
            logging.getLogger(__name__).warning("승인된 제안 %s을(를) 그래프에 반영하지 못해 건너뛴다: %s", p.id, e)
    return store
