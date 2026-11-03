"""자동 갱신 확인: 지금 객체를 복사해 일부러 바꾼 '다음 자료'를 만들고, 비교 제안이 맞게 나오는지 본다.

실행: python -m tests.test_diff
지금 자료(data/processed)는 읽기만 하고, 바꾼 자료와 제안은 임시 폴더에만 만든다.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
FILES = ["objects.gpkg", "facility_building.csv", "parcel.csv", "admin_dong.csv", "building_entrance.csv",
         "road.csv", "entrance_road.csv", "building_zone.csv", "parcel_legal_dong.csv", "building_basemap.csv",
         "building_digital_map.csv"]
EMAP_LAYERS = ["road_segment", "legal_dong", "basic_zone", "basemap_building", "digital_map_building", "parcel_shape"]
FAKE_MGT = "1150010200199990000000001"  # 시험용 건물관리번호 (실제 번호 아님)


def make_next_snapshot(dst: Path) -> dict:
    for f in FILES:
        shutil.copy(PROC / f, dst / f)
    layers = {n: gpd.read_file(PROC / "objects.gpkg", layer=n) for n in ["building", "entrance", "station", "public_facility"] + EMAP_LAYERS}
    b = layers["building"]
    busy = set(layers["station"]["building_uri"]) | set(pd.read_csv(PROC / "facility_building.csv")["building_uri"])
    addressed = b[(b["kind"] == "building") & ~b["uri"].isin(busy)]
    retire = addressed.iloc[0]["uri"]
    upd_i = addressed[addressed["floors_above"].notna()].index[1]
    update = b.loc[upd_i, "uri"]
    b.loc[upd_i, "floors_above"] = b.loc[upd_i, "floors_above"] + 1
    link_i = b[b["kind"] == "building-gis"].index[0]
    linked_gis = b.loc[link_i, "uri"]
    b.loc[link_i, ["kind", "status", "bldg_mgt_no", "uri"]] = ["building", "Active", FAKE_MGT,
                                                                f"https://yeodh10.github.io/jeungmi-geokg/id/building/{FAKE_MGT}"]
    layers["building"] = b[b["uri"] != retire]
    # 없어진 건물의 법정동, 기초구역, 도로 연결도 다음 자료에는 없다. 주소가 붙은 GIS 건물은 새 URI로 옮긴다
    zone = pd.read_csv(dst / "building_zone.csv", dtype=str, keep_default_na=False)
    zone = zone[zone["building_uri"] != retire].replace({"building_uri": {linked_gis: b.loc[link_i, "uri"]}})
    zone.to_csv(dst / "building_zone.csv", index=False)
    # 없어진 건물과 이어졌던 국가기본도 건물은 확인 결과(pnuAgrees, addressAgrees)가 빠져 '값 변경' 제안이 하나씩 더 나온다
    bm = pd.read_csv(dst / "building_basemap.csv", dtype=str)
    map_updates = sorted(bm.loc[bm["building_uri"] == retire, "map_uri"])
    for f in ("building_basemap.csv", "building_digital_map.csv"):
        bm = pd.read_csv(dst / f, dtype=str)
        bm = bm[bm["building_uri"] != retire].replace({"building_uri": {linked_gis: b.loc[link_i, "uri"]}})
        bm.to_csv(dst / f, index=False)
    (dst / "objects.gpkg").unlink()
    for n, g in layers.items():
        g.to_file(dst / "objects.gpkg", layer=n, driver="GPKG", engine="pyogrio")
    return {"retire": retire, "update": update, "link": linked_gis,
            "create": f"https://yeodh10.github.io/jeungmi-geokg/id/building/{FAKE_MGT}", "map_updates": map_updates}


def check_against_current() -> None:
    """승인으로 바뀐 객체에 대한 갱신 제안: 빼는 값을 지금 그래프에서 뽑아야 승인할 수 있다.

    재현: 원천끼리 비교하면 지금 그래프에 없는 옛 점을 빼고 새 점을 넣어, 모양에 WKT가 둘이 되고 영구히 승인할 수 없었다.
    """
    import pyoxigraph as ox
    from rdflib import Graph

    from src.graph.store import apply_problems
    from src.review.diff_proposals import diff

    jgk, geo = "https://yeodh10.github.io/jeungmi-geokg/def#", "http://www.opengis.net/ont/geosparql#"
    root = "https://yeodh10.github.io/jeungmi-geokg/id/building"
    old_point, new_point, polygon = "POINT (126.86 37.55)", "POINT (126.86 37.56)", "POLYGON ((126.86 37.55, 126.87 37.55, 126.87 37.56, 126.86 37.55))"

    def source(point: str, with_gis: bool, floors: int = 3, name: str = "가") -> str:
        text = f"""@prefix jgk: <{jgk}> . @prefix geo: <{geo}> . @prefix dcterms: <http://purl.org/dc/terms/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
<{root}/1> a jgk:Building, geo:Feature ; jgk:status jgk:Active ; jgk:floorsAbove {floors} ; rdfs:label "{name}"@ko ;
    dcterms:source jgk:dataset-a ; geo:hasGeometry <{root}/1/geometry> .
<{root}/1/geometry> a geo:Geometry ; geo:asWKT "{point}"^^geo:wktLiteral .
"""
        if with_gis:
            text += f"""<{root}/2> a jgk:Building, geo:Feature ; jgk:status jgk:AddressUnlinked ; dcterms:source jgk:dataset-a .
<{root}/3> a jgk:Building, geo:Feature ; jgk:status jgk:Active ; dcterms:source jgk:dataset-a .
"""
        return text

    old = Graph().parse(data=source(old_point, True), format="turtle")
    new = Graph().parse(data=source(new_point, False, floors=4, name="나"), format="turtle")
    # 지금 그래프: 원천에 승인 두 건이 얹혔다 (건물 1에 폴리곤, 건물 2는 주소 연결), 건물 3은 이미 폐지
    current = ox.Store()
    current.load(input=source(polygon, True).encode("utf-8"), format=ox.RdfFormat.TURTLE)
    current.update(f"""DELETE DATA {{ <{root}/2> <{jgk}status> <{jgk}AddressUnlinked> . <{root}/3> <{jgk}status> <{jgk}Active> }} ;
INSERT DATA {{ <{root}/2> <{jgk}status> <{jgk}AddressLinked> . <{root}/3> <{jgk}status> <{jgk}Retired> }}""")

    props = {(p.action, p.target): p for p in diff(old, new, "t", "시험", current=current)}
    assert set(props) == {("update", f"{root}/1"), ("retire", f"{root}/2")}, sorted(props)  # 이미 폐지된 건물 3은 다시 제안하지 않는다
    upd = props[("update", f"{root}/1")]
    wkt_changes = {(c.op, c.o.value) for c in upd.changes if c.p == geo + "asWKT"}
    assert wkt_changes == {("remove", polygon), ("add", new_point)}, wkt_changes
    assert upd.flags == ["승인된 값을 덮어쓴다"] and any("승인" in e for e in upd.evidence), upd
    assert not apply_problems(current, upd), apply_problems(current, upd)
    ret = props[("retire", f"{root}/2")]
    assert [(c.op, c.o.value) for c in ret.changes if c.p == jgk + "status"] == [("remove", jgk + "AddressLinked"), ("add", jgk + "Retired")], ret.changes
    assert not apply_problems(current, ret), apply_problems(current, ret)

    # 자료의 값이 지금 값과 같으면 승인된 값을 건드리지 않고, 모두 같으면 제안을 만들지 않는다
    same = ox.Store()
    same.load(input=source(new_point, True, floors=4, name="나").encode("utf-8"), format=ox.RdfFormat.TURTLE)
    assert not [p for p in diff(old, new, "t", "시험", current=same) if p.target == f"{root}/1"]

    # 지금 그래프를 주지 않으면 자료끼리 견준다 (이전과 같다)
    plain = next(p for p in diff(old, new, "t", "시험") if p.target == f"{root}/1")
    assert ("remove", old_point) in {(c.op, c.o.value) for c in plain.changes} and plain.flags == []


def check_same_building() -> None:
    """주소 연결 제안은 jgk:sameBuildingAs로 잇고, 옛 승인이 owl:sameAs로 이은 건물은 다시 잇지 않는다."""
    import pyoxigraph as ox
    from rdflib import Graph

    from src.review.diff_proposals import diff

    jgk, geo = "https://yeodh10.github.io/jeungmi-geokg/def#", "http://www.opengis.net/ont/geosparql#"
    root = "https://yeodh10.github.io/jeungmi-geokg/id"
    gis, mgt = f"{root}/building-gis/1", f"{root}/building/2"
    head = f"""@prefix jgk: <{jgk}> . @prefix geo: <{geo}> . @prefix dcterms: <http://purl.org/dc/terms/> .
"""
    old = Graph().parse(data=head + f"""<{gis}> a jgk:Building, geo:Feature ; jgk:status jgk:AddressUnlinked ; jgk:gisBuildingId "1" ; dcterms:source jgk:dataset-a .""", format="turtle")
    new = Graph().parse(data=head + f"""<{mgt}> a jgk:Building, geo:Feature ; jgk:status jgk:Active ; dcterms:source jgk:dataset-a ; geo:hasGeometry <{mgt}/geometry> .
<{mgt}/geometry> a geo:Geometry ; jgk:sourceGisBuildingId "1" ; geo:asWKT "POINT (126.86 37.55)"^^geo:wktLiteral .""", format="turtle")
    link = next(p for p in diff(old, new, "t", "시험") if p.action == "link")
    assert [c.p for c in link.changes if c.o.value == mgt] == [jgk + "sameBuildingAs"], link.changes

    linked = ox.Store()  # 옛 승인: 상태는 이미 주소 연결됨이고 owl:sameAs로 이어져 있다
    linked.load(input=(head + f"""@prefix owl: <http://www.w3.org/2002/07/owl#> .
<{gis}> a jgk:Building, geo:Feature ; jgk:status jgk:AddressLinked ; owl:sameAs <{mgt}> ; dcterms:source jgk:dataset-a .""").encode("utf-8"),
                format=ox.RdfFormat.TURTLE)
    assert not [p for p in diff(old, new, "t", "시험", current=linked) if p.action == "link"]


def run() -> None:
    from src.graph import build_graph
    from src.graph.store import load_store
    from src.review.diff_proposals import diff
    from src.review.proposals import ProposalStore

    check_against_current()
    check_same_building()
    tmp = Path(tempfile.mkdtemp())
    new_dir = tmp / "next"
    new_dir.mkdir()
    expect = make_next_snapshot(new_dir)
    map_updates = expect.pop("map_updates")

    props = diff(build_graph.build(PROC), build_graph.build(new_dir), "test", "시험용 다음 자료")
    got = {(p.action, p.target) for p in props}
    want = {(a, u) for a, u in expect.items()} | {("update", u) for u in map_updates}
    assert got == want, f"\n기대 {sorted(want)}\n결과 {sorted(got)}"
    for p in props:
        if p.target in map_updates:
            assert {c.op for c in p.changes} == {"remove"}, p.changes  # 확인 결과만 빠진다
    props = [p for p in props if p.target not in map_updates]
    upd = next(p for p in props if p.action == "update")
    assert any("floorsAbove" in c.p and c.op == "add" for c in upd.changes), upd.changes

    store = ProposalStore(tmp / "proposals.json")
    store.add_new(props)
    for p in sorted(props, key=lambda p: p.action != "create"):  # 새 건물을 먼저 승인
        store.decide(p.id, "approved", "테스트 검토자")
    g = load_store(store)

    def ask(q: str):
        return [[s[v].value for v in range(len(s))] for s in g.query(
            "PREFIX jgk: <https://yeodh10.github.io/jeungmi-geokg/def#>\nPREFIX owl: <http://www.w3.org/2002/07/owl#>\n" + q)]

    assert ask(f"SELECT ?s WHERE {{ <{expect['retire']}> jgk:status ?s }}") == [[str(build_graph.JGK.Retired)]]
    assert ask(f"SELECT ?d WHERE {{ <{expect['retire']}> jgk:retiredOn ?d }}")
    floors = ask(f"SELECT ?f WHERE {{ <{expect['update']}> jgk:floorsAbove ?f }}")
    assert len(floors) == 1, floors  # 이전 값은 빠지고 새 값만 남는다
    assert ask(f"SELECT ?s WHERE {{ <{expect['link']}> jgk:status ?s }}") == [[str(build_graph.JGK.AddressLinked)]]
    assert ask(f"SELECT ?o WHERE {{ <{expect['link']}> jgk:sameBuildingAs ?o }}") == [[expect["create"]]]
    assert not ask(f"SELECT ?o WHERE {{ <{expect['link']}> owl:sameAs ?o }}")
    assert bool(g.query(f"ASK {{ <{expect['create']}> a <https://yeodh10.github.io/jeungmi-geokg/def#Building> }}"))
    print("자동 갱신 확인 통과:", {a: u.rsplit("/", 1)[-1] for a, u in expect.items()})
    shutil.rmtree(tmp, ignore_errors=True)


def test_diff_flow():
    run()


if __name__ == "__main__":
    run()
