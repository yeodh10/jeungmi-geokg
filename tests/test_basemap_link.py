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
    run_shape(g, b)


def run_shape(g, b) -> None:
    """점만 있는 건물에 국가기본도 모양을 붙이는 제안: 규칙을 지키고, 승인하면 모양이 면 하나로 바뀌는지 본다."""
    from src.graph.store import load_store
    from src.graph.validate import check_proposal
    from src.review.basemap_proposals import SHAPE_NEAR_M, candidates, make_shape, shape_candidates
    from src.review.proposals import ProposalStore, group_of

    df, skipped = shape_candidates()
    assert len(df) >= 1, ("후보가 없다", skipped)
    assert df["building_uri"].is_unique and df["map_uri"].is_unique
    assert (b.loc[df["building_uri"]].geometry.geom_type == "Point").all()
    inside = df["inside"].astype(bool)
    assert all(poly.contains(pt) for pt, poly in zip(df.loc[inside, "point"], df.loc[inside, "polygon"]))
    assert (df["distance_m"] <= SHAPE_NEAR_M).all() and df.loc[~inside, "address_same"].all()  # 모양 밖이면 주소가 같아야 한다
    assert not set(df["building_uri"]) & set(candidates()["building_uri"])  # 주소 연결 제안과 겹치지 않는다
    props = [make_shape(r) for _, r in df.iterrows()]
    for p in props:
        ok, res = check_proposal(g, p)
        assert ok, (p.id, res)
        removed = [c for c in p.changes if c.op == "remove"]
        assert len(removed) == 1 and g.query(f"ASK {{ <{removed[0].s}> <{removed[0].p}> {removed[0].o.sparql()} }}"), p.id
    assert len({group_of(p)[0] for p in props}) == 1

    tmp = Path(tempfile.mkdtemp()) / "proposals.json"
    shutil.copy(REAL, tmp)
    try:
        store = ProposalStore(tmp)
        store.add_new(props)
        p = store.decide(props[0].id, "approved", "시험 검토자")
        g2 = load_store(store)
        q = f"SELECT ?w WHERE {{ <{p.target}> <http://www.opengis.net/ont/geosparql#hasGeometry>/<http://www.opengis.net/ont/geosparql#asWKT> ?w }}"
        wkts = [r["w"].value for r in g2.query(q)]
        assert len(wkts) == 1 and wkts[0].startswith("POLYGON"), wkts  # 점은 빠지고 면만 남는다
        q = f"ASK {{ <{p.target}> <{JGK}basemapFeature> <{df.iloc[0]['map_uri']}> }}"
        assert g2.query(q)
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)
    print(f"국가기본도 모양 붙이기 제안 확인 통과: {len(props)}건, 제안하지 않음 {skipped}")


def test_basemap_link():
    run()


if __name__ == "__main__":
    run()
