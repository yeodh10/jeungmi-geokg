"""도로명주소 폐지조서 확인: 실제 첨부(2026-10-28 강서구 고시 제2026-140호)로 폐지 제안이 맞게 나오는지 본다.

실행: python -m tests.test_address_retirement
첨부 파일(data/raw/notices/attachments/20261028_폐지조서.xlsx)이 없으면 건너뛴다. 제안은 임시 파일에만 만든다.
"""
from __future__ import annotations

import re
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
    from src.graph.publish import hold_sources
    from src.graph.store import load_store
    from src.graph.validate import check_proposal
    from src.llm.notice import Resolver, read_notice
    from src.review.address_retirement import make, read_table
    from src.review.proposals import ProposalStore, group_of

    rows = read_table(TABLE)
    assert len(rows) == 12 and rows[0]["도로명주소"] == "등촌로55길 11-13", rows[0]
    meta, _ = read_notice(NOTICE)
    tmp = Path(tempfile.mkdtemp())
    store = ProposalStore(tmp / "proposals.json")
    g = load_store(store)
    props, report = make(rows, meta, "test", g, Resolver())
    assert len(props) == 5, [p.target for p in props]
    assert sum(1 for r in report if r["결과"].startswith("범위 밖")) == 8
    names = set(re.findall(r"jgk:(dataset-[\w-]+) a jgk:Dataset", (ROOT / "src" / "graph" / "ontology.ttl").read_text(encoding="utf-8")))
    hold = {h.rsplit("#", 1)[-1] for h in hold_sources()}
    for p in props:
        ok, res = check_proposal(g, p)
        assert ok, (p.id, res)
        assert any("폐지조서" in e for e in p.evidence) and p.location
        # 고시 묶음은 근거 문장이 아니라 코드가 정한 시군구코드와 번호로 만든다
        assert p.notice_key == "11500-2026-140", p.notice_key
        assert group_of(p) == ("notice:11500-2026-140", "고시 11500-2026-140"), group_of(p)
        # 근거 자료: 고시공고, 주소와 지번을 찾은 내비게이션용DB, 건물과 출입구의 출처 (고시 제안과 같은 방식)
        assert {"dataset-gangseo-notice", "dataset-navi-db"} <= set(p.based_on) <= names, p.based_on
        sources = {r["s"].value.rsplit("#", 1)[-1] for u in [p.target] + [c.s for c in p.changes]
                   for r in g.query(f"SELECT ?s WHERE {{ <{u}> <http://purl.org/dc/terms/source> ?s }}")}
        assert sources <= set(p.based_on), (sources, p.based_on)
        print(" ", p.target.rsplit("/", 1)[-1], p.based_on, "보류 자료 섞임" if set(p.based_on) & hold else "공개 자료만")
    # 범위를 정할 말이 없는 표의 주소는 고시를 낸 시군구로 본다. 고시 번호를 읽지 못하면 찾지 않는다
    assert make(rows, meta | {"번호": "번호 없음"}, "test", g, Resolver())[0] == []
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
