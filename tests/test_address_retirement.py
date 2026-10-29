"""도로명주소 폐지조서 확인: 실제 첨부(2026-10-28 강서구 고시 제2026-140호)로 폐지 제안이 맞게 나오는지 본다.

실행: python -m tests.test_address_retirement
첨부 파일(data/raw/notices/attachments/20261028_폐지조서.xlsx)이 없으면 건너뛴다. 제안은 임시 파일에만 만든다.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / "data" / "raw" / "notices" / "attachments" / "20261028_폐지조서.xlsx"
NOTICE = ROOT / "data" / "raw" / "notices" / "20261028_도로명주소_폐지_고시.txt"
JGK = "https://yeodh10.github.io/jeungmi-geokg/def#"


def run() -> None:
    if not TABLE.exists():
        print("첨부 파일이 없어 건너뜀")
        return
    from src.graph.store import load_store
    from src.graph.validate import check_proposal
    from src.llm.notice import Resolver, read_notice
    from src.review.address_retirement import make, read_table
    from src.review.proposals import ProposalStore

    rows = read_table(TABLE)
    assert len(rows) == 12 and rows[0]["도로명주소"] == "등촌로55길 11-13", rows[0]
    meta, _ = read_notice(NOTICE)
    tmp = Path(tempfile.mkdtemp())
    store = ProposalStore(tmp / "proposals.json")
    g = load_store(store)
    props, report = make(rows, meta, "test", g, Resolver())
    assert len(props) == 5, [p.target for p in props]
    assert sum(1 for r in report if r["결과"].startswith("범위 밖")) == 8
    for p in props:
        ok, res = check_proposal(g, p)
        assert ok, (p.id, res)
        assert any("폐지조서" in e for e in p.evidence) and p.location
    store.add_new(props)
    for p in props:
        store.decide(p.id, "approved", "테스트 검토자")
    g = load_store(store)
    for p in props:
        ent = [r["e"].value for r in g.query(f"SELECT ?e WHERE {{ <{p.target}> <{JGK}hasEntrance> ?e }}")]
        for u in [p.target] + ent:
            st = [r["s"].value for r in g.query(f"SELECT ?s WHERE {{ <{u}> <{JGK}status> ?s }}")]
            assert st == [JGK + "Retired"], (u, st)
            d = [r["d"].value for r in g.query(f"SELECT ?d WHERE {{ <{u}> <{JGK}retiredOn> ?d }}")]
            assert d == ["2026-10-28"], (u, d)
    print("폐지조서 확인 통과:", [p.target.rsplit("/", 1)[-1] for p in props])
    shutil.rmtree(tmp, ignore_errors=True)


def test_address_retirement():
    run()


if __name__ == "__main__":
    run()
