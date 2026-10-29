"""국가기본도 주소로 주소 미연결 건물을 잇는 제안 확인.

실행: python -m tests.test_basemap_link
실제 제안 파일은 임시 복사본으로만 쓴다. data/processed/에 국가기본도까지 넣은 결과가 있어야 한다.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "data" / "processed" / "review" / "proposals.json"
JGK = "https://yeodh10.github.io/jeungmi-geokg/def#"


def run() -> None:
    import geopandas as gpd

    from src.graph.store import load_store
    from src.graph.validate import check_proposal
    from src.review.basemap_proposals import MAX_DISTANCE_M, candidates, make
    from src.review.proposals import ProposalStore, group_of

    df = candidates()
    assert len(df) >= 1, "후보가 없다"
    # 규칙: 대상은 모양 없는 점 건물, 거리 한계 안, 점 건물 하나에 제안 하나, 주소 미연결 건물에서 출발
    b = gpd.read_file(ROOT / "data" / "processed" / "objects.gpkg", layer="building").set_index("uri")
    assert (b.loc[df["building_uri"]].geometry.geom_type == "Point").all()
    assert (df["distance_m"] <= MAX_DISTANCE_M).all() and df["building_uri"].is_unique
    assert df["gis_uri"].str.contains("/building-gis/").all() and (df["iou"] >= 0.5).all()

    g = load_store()
    props = [make(r) for _, r in df.iterrows()]
    for p in props:
        ok, res = check_proposal(g, p)
        assert ok, (p.id, res)
    assert len({group_of(p)[0] for p in props}) == 1  # 검토 화면에서 한 묶음

    # 승인하면 상태가 주소 연결됨이 되고 sameAs로 건물관리번호 건물을 가리킨다
    tmp = Path(tempfile.mkdtemp()) / "proposals.json"
    shutil.copy(REAL, tmp)
    try:
        store = ProposalStore(tmp)
        store.add_new(props)
        p = store.decide(props[0].id, "approved", "시험 검토자")
        g2 = load_store(store)
        q = f"SELECT ?s ?same WHERE {{ <{p.target}> <{JGK}status> ?s ; <http://www.w3.org/2002/07/owl#sameAs> ?same }}"
        got = [(r["s"].value, r["same"].value) for r in g2.query(q)]
        assert got == [(JGK + "AddressLinked", df.iloc[0]["building_uri"])], got
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)
    print(f"국가기본도 주소 연결 제안 확인 통과: {len(props)}건")


def test_basemap_link():
    run()


if __name__ == "__main__":
    run()
