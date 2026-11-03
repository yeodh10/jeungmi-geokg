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


def copy_without_basemap(dst: Path) -> None:
    """실제 제안 파일을 복사하되 이 규칙의 제안은 뺀다 (실제 파일에서 이미 결정됐어도 다시 승인해 볼 수 있게)."""
    import json
    rows = json.loads(REAL.read_text(encoding="utf-8"))
    rows = [r for r in rows if not r["id"].startswith(("rule-basemap-link-", "rule-basemap-shape-"))]
    dst.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def check_shared_polygon() -> None:
    """한 폴리곤을 함께 쓰는 건물은 모두 지도 건물과 이어진다. 전에는 하나만 이어졌다."""
    import geopandas as gpd
    from shapely.geometry import box

    from src.model.build_objects import match_by_shape

    frame = lambda rows: gpd.GeoDataFrame(rows, columns=["uri", "kind", "use", "geometry"], geometry="geometry", crs="EPSG:5186")
    ours = frame([("a", "x", "x", box(0, 0, 10, 10)), ("b", "x", "x", box(0, 0, 10, 10)), ("c", "x", "x", box(20, 0, 30, 10)),
                  ("d", "x", "x", box(40, 0, 50, 10)), ("e", "x", "x", box(40, 0, 50, 10))])
    theirs = frame([("m1", "x", "x", box(0, 0, 10, 11)), ("m2", "x", "x", box(20, 0, 30, 10)), ("m3", "x", "x", box(40, 0, 50, 30)),
                    ("m4", "x", "x", box(100, 0, 110, 10))])
    links, review, unmatched = match_by_shape(ours, theirs, box(-1, -1, 200, 200))
    assert sorted(zip(links["building_uri"], links["map_uri"])) == [("a", "m1"), ("b", "m1"), ("c", "m2")], links
    assert set(links.loc[links["map_uri"] == "m1", "iou"]) == {0.909}, links
    assert sorted(zip(review["building_uri"], review["map_uri"])) == [("d", "m3"), ("e", "m3")], review  # IoU 0.33은 목록으로 간다
    assert list(unmatched["uri"]) == ["m4"], unmatched


def check_underground_address() -> None:
    """지하 주소는 같은 번호의 지상 지도 주소와 같다고 보지 않는다. 국가기본도 주소에는 지상, 지하 구분이 없다."""
    import pandas as pd

    from src.model.build_objects import map_address_key

    df = pd.DataFrame({"road_code": ["1", "1"], "bldg_main_no": ["560", "560"], "bldg_sub_no": ["0", "0"], "underground": ["0", "1"]})
    above, below = map_address_key(df)
    assert above == "1-560-0" and below != above, (above, below)


def check_near_same_address() -> None:
    """규칙 5: 가장 가까운 모양의 주소가 달라도 5m 안에 주소 같은 모양이 있으면 그 모양을 쓴다."""
    import geopandas as gpd
    from shapely.geometry import Point, box

    from src.review.basemap_proposals import near_same_address

    points = gpd.GeoDataFrame({"uri": ["p", "q"], "bldg_mgt_no": ["1", "2"]}, geometry=[Point(0, 0), Point(100, 0)], crs="EPSG:5186")
    maps = gpd.GeoDataFrame({"map_uri": ["가까운 다른 주소", "조금 먼 같은 주소", "먼 같은 주소"], "addr_key": ["다른", "같은", "같은"]},
                            geometry=[box(1, 0, 5, 5), box(-5, 0, -3, 5), box(90, 0, 92, 5)], crs="EPSG:5186")
    near = near_same_address(points, maps, {"1": "같은", "2": "같은"})
    assert list(near["map_uri"]) == ["조금 먼 같은 주소"] and list(near["distance_m"]) == [3.0], near  # q는 8m라 빠진다


def check_rules_on_data(df, proc) -> None:
    """모양 규칙의 넓이와 층수 기준이 주소 잇기에도 걸린다. 모양 붙이기 겹침은 양쪽 모양 기준으로 잰다.
    같은 필지의 1㎡ 미만 자리표시만 빼고, 다른 필지의 자리표시는 겹침에 넣는다 (구현 함수를 쓰지 않고 따로 잰다)."""
    import geopandas as gpd

    from src.review.basemap_proposals import SHAPE_MAX_OVERLAP, SHAPE_MIN_AREA_M2, floor_gap, shape_candidates, worst_overlap

    b = gpd.read_file(proc / "objects.gpkg", layer="building").to_crs("EPSG:5186").set_index("uri")
    maps = gpd.read_file(proc / "objects.gpkg", layer="basemap_building").to_crs("EPSG:5186").set_index("uri")
    for r in df.itertuples():
        assert b.at[r.gis_uri, "geometry"].area >= SHAPE_MIN_AREA_M2 and maps.at[r.basemap_uri, "geometry"].area >= SHAPE_MIN_AREA_M2, r
        assert not floor_gap(b.at[r.building_uri, "floors_above"], maps.at[r.basemap_uri, "floors"]), r
    polys = b[b.geom_type != "Point"].reset_index()
    tiny = polys.area < 1
    assert tiny.sum() > 0  # 실제 자료에 자리표시가 있다
    skipped_tiny = 0
    for r in shape_candidates(proc)[0].itertuples():
        m = maps.at[r.map_uri, "geometry"]
        for i in polys.sindex.query(m, predicate="intersects"):
            g, same_lot_tiny = polys.geometry.iloc[i], bool(tiny.iloc[i]) and polys["pnu"].iloc[i] == r.pnu
            x = m.intersection(g).area
            if same_lot_tiny:
                skipped_tiny += 1
                continue
            assert x / m.area <= SHAPE_MAX_OVERLAP and x / g.area <= SHAPE_MAX_OVERLAP, (r.map_uri, x, g.area)
    assert skipped_tiny >= 1  # 같은 필지 자리표시를 빼는 경우가 실제로 있다
    # 다른 필지의 자리표시는 재므로, 자리표시를 품은 모양은 100% 겹침으로 걸린다
    from shapely.geometry import box
    frame = gpd.GeoDataFrame({"uri": ["t"], "pnu": ["1"], "tiny": [True]}, geometry=[box(0, 0, 0.2, 0.2)], crs="EPSG:5186")
    assert worst_overlap(box(-5, -5, 5, 5), frame, "1") == 0.0 and worst_overlap(box(-5, -5, 5, 5), frame, "2") == 1.0


def check_floor_gap() -> None:
    """층수 규칙의 경계: 3층 차이부터 부속 구조물로 본다. 값이 없거나 지하만 있는 건물은 보지 않는다."""
    from src.review.basemap_proposals import floor_gap

    assert floor_gap(5, 2) and floor_gap(15, 1) and not floor_gap(4, 2) and not floor_gap(10, 3)
    assert not floor_gap(float("nan"), 1) and not floor_gap(5, float("nan")) and not floor_gap(0, 1)


def check_audit(proc) -> None:
    """지금 승인이 지금 규칙으로 다시 뽑히는지 나눠 본다. 규칙 밖 목록과 다시 뽑힌 제안을 합치면 승인 전체다. 승인 기록은 고치지 않는다."""
    from src.review.basemap_proposals import audit, candidates, shape_candidates
    from src.review.proposals import ProposalStore

    approved = {p.id for p in ProposalStore(REAL).load() if p.status == "approved" and p.id.startswith("rule-basemap-")}
    picked = {f"rule-basemap-link-{u.rsplit('/', 1)[-1]}" for u in candidates(proc)["gis_uri"]}
    picked |= {f"rule-basemap-shape-{m}" for m in shape_candidates(proc)[0]["mgt"]}
    out = audit(proc)
    outside = set(out.loc[out["reason"] != "지금 규칙으로 뽑으면 바꾸는 내용이 다르다", "id"])
    assert outside == approved - picked and not picked - approved, (outside ^ (approved - picked), picked - approved)
    assert out["id"].isin(approved).all(), out  # 내용이 다르다는 줄도 승인한 제안에만 붙는다
    assert not out["reason"].eq("다른 규칙").any(), out  # 까닭을 하나도 대지 못한 줄이 없어야 한다


def run() -> None:
    import geopandas as gpd

    from src.graph.store import load_store
    from src.graph.validate import check_proposal
    from src.review.basemap_proposals import MAX_DISTANCE_M, PROC, candidates, make
    from src.review.proposals import ProposalStore, group_of

    check_shared_polygon()
    check_underground_address()
    check_near_same_address()
    check_floor_gap()
    df = candidates()
    assert len(df) >= 1, "후보가 없다"
    check_rules_on_data(df, PROC)
    check_audit(PROC)
    # 규칙: 대상은 모양 없는 점 건물, 거리 한계 안, 점 건물 하나에 제안 하나, 주소 미연결 건물에서 출발
    b = gpd.read_file(ROOT / "data" / "processed" / "objects.gpkg", layer="building").set_index("uri")
    assert (b.loc[df["building_uri"]].geometry.geom_type == "Point").all()
    assert (df["distance_m"] <= MAX_DISTANCE_M).all() and df["building_uri"].is_unique
    assert df["gis_uri"].str.contains("/building-gis/").all() and (df["iou"] >= 0.5).all()

    base = Path(tempfile.mkdtemp()) / "proposals.json"  # 이 규칙의 제안을 결정하기 전 그래프
    copy_without_basemap(base)
    g = load_store(ProposalStore(base))
    shutil.rmtree(base.parent, ignore_errors=True)
    props = [make(r) for _, r in df.iterrows()]
    for p in props:
        ok, res = check_proposal(g, p)
        assert ok, (p.id, res)
        assert "규칙 1~5" in p.source_detail, p.source_detail  # 규칙 수와 근거 문구가 맞는다
    assert len({group_of(p)[0] for p in props}) == 1  # 검토 화면에서 한 묶음

    # 승인하면 상태가 주소 연결됨이 되고 jgk:sameBuildingAs로 건물관리번호 건물을 가리킨다. owl:sameAs는 쓰지 않는다
    tmp = Path(tempfile.mkdtemp()) / "proposals.json"
    copy_without_basemap(tmp)
    try:
        store = ProposalStore(tmp)
        store.add_new(props)
        p = store.decide(props[0].id, "approved", "시험 검토자")
        g2 = load_store(store)
        q = f"SELECT ?s ?same WHERE {{ <{p.target}> <{JGK}status> ?s ; <{JGK}sameBuildingAs> ?same }}"
        got = [(r["s"].value, r["same"].value) for r in g2.query(q)]
        assert got == [(JGK + "AddressLinked", df.iloc[0]["building_uri"])], got
        assert not any(c.p.endswith("owl#sameAs") for c in p.changes), p.changes
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
        assert "규칙 1~8" in p.source_detail, p.source_detail
        removed = [c for c in p.changes if c.op == "remove"]
        assert len(removed) == 1 and g.query(f"ASK {{ <{removed[0].s}> <{removed[0].p}> {removed[0].o.sparql()} }}"), p.id
    assert len({group_of(p)[0] for p in props}) == 1

    tmp = Path(tempfile.mkdtemp()) / "proposals.json"
    copy_without_basemap(tmp)
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
