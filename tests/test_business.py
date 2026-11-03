"""상호(소상공인 상가정보) 확인: 건물 잇기 규칙, 공개 규칙, 같은 가게 합치기, 그래프 객체, 공개 사이트.

실행: python -m tests.test_business
앞쪽은 작은 표로 규칙을 보고, 뒤쪽은 data/processed의 결과(business.csv, 그래프, 사이트)가 있으면 읽기만 한다.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyoxigraph as ox
from shapely.geometry import Point

from src.model import build_business as bb
from src.model import uri

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
JGK = "https://yeodh10.github.io/jeungmi-geokg/def#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
DOTS = re.compile("[\u00b7\u2027\u30fb\u318d\uff65]")
OLIVE_BUILDING = "building/1150010100102400021028397"  # 올리브영 증미역점이 든 건물 (양천로 583)


def check_uri() -> None:
    assert uri.business("MA0101202310A0001390").endswith("/id/business/MA0101202310A0001390")
    for bad in ("MA01", "MA0101202310A000139가", "MA0101202310A0001390X"):
        try:
            uri.business(bad)
        except AssertionError:
            continue
        raise AssertionError(f"잘못된 상가업소번호를 받았다: {bad}")


def check_plain() -> None:
    assert bb.plain("과학\u00b7기술") == "과학, 기술"
    assert bb.plain("섬유 \u00b7 의복\u318d신발 소매") == "섬유, 의복, 신발 소매"
    assert bb.plain("  ") is None and bb.plain(float("nan")) is None
    assert bb.plain("양천로 583") == "양천로 583"


def check_link() -> None:
    """건물관리번호가 먼저, 다음은 PNU가 같은 건물이 하나일 때, 나머지는 필지에만."""
    a, b, c = (uri.building(f"11500102001066600{i}0000001") for i in (1, 2, 3))
    buildings = gpd.GeoDataFrame({"uri": [a, b, c], "bldg_mgt_no": [a[-25:], b[-25:], c[-25:]],
                                  "pnu": ["1150010200106660001", "1150010200106660002", "1150010200106660002"],
                                  "use_name": ["업무시설", "단독주택", "제1종근린생활시설"]}, geometry=[Point(0, 0)] * 3)
    df = pd.DataFrame({"src_bldg_mgt_no": [a[-25:], "x" * 25, "y" * 25, "z" * 25, "w" * 25],
                       "src_pnu": ["1150010200106660009", "1150010200106660001", "1150010200106660002", "1150010200106669999", None]})
    parcels = {uri.parcel("1150010200106660001"), uri.parcel("1150010200106660002")}
    got = bb.link(df, buildings, parcels)
    col = lambda c: got[c].fillna("").tolist()
    assert col("linked_by") == [bb.LINK_NO, bb.LINK_PNU, bb.LINK_PARCEL, "", ""], col("linked_by")
    assert col("building_uri") == [a, a, "", "", ""]
    assert col("parcel_uri") == ["", "", uri.parcel("1150010200106660002"), "", ""]

    # 공개 규칙: 필지에만 이은 상호는 그 필지의 모든 건물 용도를 본다 (b가 단독주택)
    rule = bb.load_rule()
    got = got.assign(cat_large=["과학, 기술", "과학, 기술", "과학, 기술", "음식", "음식"], floor=[None, None, "1", "1", "1"])
    marked = bb.mark_public(got, buildings, rule)
    assert marked["public"].tolist() == [True, True, False, False, False], marked["public"].tolist()
    assert marked["in_home"].tolist() == [False, False, True, False, False]


def check_public_rule() -> None:
    rule = bb.load_rule()
    assert {"음식", "소매", "보건의료", "숙박", "수리, 개인", "교육", "예술, 스포츠"} == rule["walk_in"], rule
    assert not any(DOTS.search(x) for x in rule["walk_in"])
    for floor, ok in (("1", True), ("지", True), ("지1", True), ("B1", True), ("B2", True), ("2", False), ("1501", False), (None, False), ("", False)):
        assert bb.open_floor(floor) is ok, floor
    assert bb.is_public(["업무시설"], "부동산", None, rule)  # 주택이 아니면 모두
    assert bb.is_public(["공동주택"], "음식", "1", rule)
    assert bb.is_public(["단독주택"], "보건의료", "B1", rule)
    assert not bb.is_public(["단독주택"], "부동산", "1", rule)  # 사무형 업종은 집 주소일 수 있다
    assert not bb.is_public(["공동주택"], "음식", "3", rule)
    assert not bb.is_public(["주택"], "소매", None, rule)  # 층을 모르면 내지 않는다
    assert not bb.is_public(["업무시설", "단독주택"], "과학, 기술", "1", rule)  # 필지 위에 주택이 하나라도 있으면
    assert not bb.is_public([], "음식", "1", rule, linked=False)  # 어디에도 잇지 못하면 내지 않는다


def check_merge() -> None:
    row = lambda n, bu, big, cat, p: {"n": n, "key": bb.tidy(n), "bu": bu, "big": big, "cat": cat, "p": p}
    rows = [row("올리브영증미역점", "building/1", "소매", "화장품 소매업", "business/A"),
            row("씨제이올리브영증미역점", "building/1", "소매", "그 외 기타 상품 전문 소매업", "business/B"),
            row("올리브영증미역점", "building/2", "소매", "화장품 소매업", "business/C"),  # 다른 건물
            row("카페", "building/1", "음식", "카페", "business/D"), row("카페모카", "building/1", "음식", "카페", "business/E"),  # 네 글자 미만
            row("행복부동산", "building/3", "부동산", "부동산 중개", "business/F"), row("행복부동산학원", "building/3", "교육", "학원", "business/G"),
            row("홈플러스", "building/4", "소매", "슈퍼마켓", "business/H"), row("일룸홈플러스강서점", "building/4", "소매", "가구 소매업", "business/I"),  # 큰 매장 안 다른 가게
            row("홈플러스강서점", "building/4", "소매", "슈퍼마켓", "business/J")]
    out = bb.merge_same_store(rows)
    by_p = {r["p"]: r["rows"] for r in out}
    assert by_p == {"business/A": 2, "business/C": 1, "business/D": 1, "business/E": 1, "business/F": 1, "business/G": 1,
                    "business/H": 2, "business/I": 1}, by_p


def check_private_businesses() -> None:
    """공개 사이트는 그래프 값만으로 같은 규칙을 쓴다."""
    from src.graph.publish import private_businesses
    t = defaultdict(list)
    lit, node = ox.Literal, ox.NamedNode

    def put(s, p, o):
        t[s].append((node(p), o))
    home, office = uri.building("1150010200106660040000001"), uri.building("1150010200106660040000002")
    lot = uri.parcel("1150010200106660040")
    put(home, JGK + "useName", lit("단독주택"))
    put(home, JGK + "locatedOn", node(lot))
    put(office, JGK + "useName", lit("업무시설"))
    cases = {"A": (home, None, "부동산", "1", True), "B": (home, None, "음식", "1", False), "C": (office, None, "부동산", None, False),
             "D": (None, lot, "과학, 기술", "1", True), "E": (None, None, "음식", "1", True)}
    for k, (b, p, big, floor, _) in cases.items():
        s = uri.business(f"MA0101202310A000139{k}")
        put(s, RDF_TYPE, node(JGK + "Business"))
        put(s, JGK + "categoryLarge", lit(big))
        if floor:
            put(s, JGK + "floorInfo", lit(floor))
        if b:
            put(s, JGK + "inBuilding", node(b))
        if p:
            put(s, JGK + "onParcel", node(p))
    got = private_businesses(t)
    want = {uri.business(f"MA0101202310A000139{k}") for k, v in cases.items() if v[4]}
    assert got == want | {u + "/geometry" for u in want}, sorted(got)


def check_shapes() -> None:
    """SHACL: 바른 상호는 통과하고, 이은 방법과 대상이 어긋나거나 번호가 틀리면 걸린다."""
    from pyshacl import validate
    from rdflib import Graph
    graph_dir = ROOT / "src" / "graph"
    head = (f"@prefix jgk: <{JGK}> . @prefix geo: <http://www.opengis.net/ont/geosparql#> . @prefix dcterms: <http://purl.org/dc/terms/> .\n"
            "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
            f"<{uri.building('1150010200106660040000001')}> a jgk:Building . <{uri.parcel('1150010200106660040')}> a jgk:Parcel .\n")

    def problems(no: str, extra: str) -> set[str]:
        s = f"{uri.BASE}/business/{no}"
        data = Graph().parse(data=head + f"""<{s}> a jgk:Business, geo:Feature ; jgk:status jgk:Active ; dcterms:source jgk:dataset-sbiz-store ;
            jgk:businessNo "{no}" ; rdfs:label "가게"@ko ; jgk:categoryLarge "음식" ; geo:hasDefaultGeometry <{s}/geometry> ; {extra} .
            <{s}/geometry> a geo:Geometry ; geo:asWKT "POINT (126.86 37.55)"^^geo:wktLiteral .""", format="turtle")
        data.parse(graph_dir / "ontology.ttl")  # 출처 자료의 종류(jgk:Dataset)는 어휘에 있다
        _, report, _ = validate(data, shacl_graph=str(graph_dir / "shapes.ttl"), inference="none")
        q = "SELECT ?f ?m WHERE { ?r <http://www.w3.org/ns/shacl#focusNode> ?f ; <http://www.w3.org/ns/shacl#resultMessage> ?m }"
        return {str(r.m) for r in report.query(q) if "/business/" in str(r.f)}
    building, parcel = uri.building("1150010200106660040000001"), uri.parcel("1150010200106660040")
    assert not problems("MA0101202310A0001390", f'jgk:inBuilding <{building}> ; jgk:linkedBy "건물관리번호"')
    assert not problems("MA0101202310A0001391", f'jgk:onParcel <{parcel}> ; jgk:linkedBy "필지만"')
    assert any("이은 방법과 이은 대상" in m for m in problems("MA0101202310A0001392", f'jgk:onParcel <{parcel}> ; jgk:linkedBy "PNU"'))
    assert any("이은 방법과 이은 대상" in m for m in problems("MA0101202310A0001393", f'jgk:inBuilding <{building}> ; jgk:onParcel <{parcel}> ; jgk:linkedBy "PNU"'))
    assert any("상가업소번호" in m for m in problems("ma01", f'jgk:inBuilding <{building}> ; jgk:linkedBy "건물관리번호"'))
    assert any("이은 방법은" in m for m in problems("MA0101202310A0001394", f'jgk:inBuilding <{building}> ; jgk:linkedBy "주소"'))


def check_real_table() -> None:
    path = PROC / "business.csv"
    if not path.exists():
        print("  business.csv가 없어 실제 표 확인은 건너뜀")
        return
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert len(df) == 3332 and df["store_no"].is_unique, len(df)
    assert (df["uri"] == df["store_no"].map(uri.business)).all()
    counts = df["linked_by"].replace("", "없음").value_counts().to_dict()
    assert counts == {bb.LINK_NO: 3300, bb.LINK_PNU: 22, bb.LINK_PARCEL: 10}, counts
    assert ((df["building_uri"] != "") ^ (df["parcel_uri"] != "")).all(), "건물과 필지 가운데 하나에만 잇는다"
    for c in bb.TEXT_COLS:
        assert not df[c].str.contains(DOTS).any(), f"{c}에 가운뎃점이 남았다"
    olive = df[df["name"].str.contains("올리브영증미역점")]
    assert len(olive) == 2 and set(olive["building_uri"].str.rsplit("/id/", n=1).str[-1]) == {OLIVE_BUILDING}, olive
    assert (df["floor"] == "").sum() == 1239, "층 정보 없음 수"
    assert set(df["public"]) == {"True", "False"}
    print(f"  표: {len(df):,}행, 이은 방법 {counts}, 공개 {int((df['public'] == 'True').sum()):,}, 공개 안 함 {int((df['public'] == 'False').sum()):,}")


def check_real_graph() -> None:
    ttl = PROC / "jeungmi_geokg.ttl"
    biz = PROC / "business.csv"
    if not ttl.exists() or not biz.exists():
        print("  그래프가 없어 실제 그래프 확인은 건너뜀")
        return
    store = ox.Store()
    store.load(path=str(ttl), format=ox.RdfFormat.TURTLE)
    q = lambda text: list(store.query("PREFIX jgk: <" + JGK + ">\nPREFIX geo: <http://www.opengis.net/ont/geosparql#>\n"
                                      "PREFIX dcterms: <http://purl.org/dc/terms/>\n" + text))
    n = int(q("SELECT (COUNT(?s) AS ?n) WHERE { ?s a jgk:Business }")[0]["n"].value)
    if n == 0:
        print("  그래프에 상호가 아직 없어 건너뜀 (python -m src.graph.build_graph)")
        return
    assert n == 3332, n
    rows = q("""SELECT ?how (COUNT(?s) AS ?n) WHERE { ?s a jgk:Business ; jgk:linkedBy ?how } GROUP BY ?how""")
    assert {r["how"].value: int(r["n"].value) for r in rows} == {bb.LINK_NO: 3300, bb.LINK_PNU: 22, bb.LINK_PARCEL: 10}
    bad = q("""SELECT ?s WHERE { ?s a jgk:Business . FILTER NOT EXISTS { ?s geo:hasDefaultGeometry/geo:asWKT ?w ; dcterms:source jgk:dataset-sbiz-store ;
               jgk:businessNo ?no ; jgk:status jgk:Active } }""")
    assert not bad, bad[:3]
    dangling = q("""SELECT ?s ?x WHERE { ?s a jgk:Business . { ?s jgk:inBuilding ?x FILTER NOT EXISTS { ?x a jgk:Building } }
                    UNION { ?s jgk:onParcel ?x FILTER NOT EXISTS { ?x a jgk:Parcel } } }""")
    assert not dangling, dangling[:3]
    olive = q(f"""SELECT ?s WHERE {{ ?s a jgk:Business ; jgk:inBuilding <{uri.BASE}/{OLIVE_BUILDING}> ; <http://www.w3.org/2000/01/rdf-schema#label> ?n .
                  FILTER (CONTAINS(STR(?n), "올리브영증미역점")) }}""")
    assert len(olive) == 2, olive
    print(f"  그래프: 상호 {n:,}개, 모두 모양, 출처, 번호, 상태가 있고 이은 대상이 그래프에 있다")


def check_real_site() -> None:
    site = PROC / "site"
    path = site / "assets" / "map" / "business.geojson"
    biz = PROC / "business.csv"
    if not path.exists() or not biz.exists():
        print("  사이트가 없어 실제 사이트 확인은 건너뜀")
        return
    df = pd.read_csv(biz, dtype=str, keep_default_na=False)
    public = set(df.loc[df["public"] == "True", "uri"].str.rsplit("/id/", n=1).str[-1])
    private = df.loc[df["public"] == "False"]
    feats = json.loads(path.read_text(encoding="utf-8"))["features"]
    props = [f["properties"] for f in feats]
    assert all(p["p"] in public for p in props if "p" in p), "공개하지 않는 상호가 화면 자료에 있다"
    text = path.read_text(encoding="utf-8")
    assert not DOTS.search(text), "화면 자료에 가운뎃점"
    olive = [p for p in props if p.get("bu") == OLIVE_BUILDING and "올리브영증미역점" in p["n"]]
    assert len(olive) == 1 and olive[0]["rows"] == 2, olive
    geo = [p for p in props if p["n"] == "지오스토리"]
    assert len(geo) == 1 and geo[0]["src"].startswith("직접 넣은 장소, 출처:") and "406" not in json.dumps(geo[0], ensure_ascii=False), geo
    for p in props:
        assert p["u"].startswith("place/") and p["n"], p
    pages = site / "id" / "business"
    if pages.exists():
        made = {d.name for d in pages.iterdir() if d.is_dir()}
        assert made == {p.rsplit("/", 1)[-1] for p in public}, (len(made), len(public))
        assert not made & set(private["store_no"]), "공개하지 않는 상호의 페이지가 있다"
        page = (pages / olive[0]["p"].rsplit("/", 1)[-1] / "index.html").read_text(encoding="utf-8")
        assert "상가업소번호" in page and "들어 있는 건물" in page and "영업시간, 전화번호, 리뷰는 이 자료에 없다" in page
        # 공개하지 않는 상호는 번호가 목록 페이지와 화면 자료에 없고, 이름이 화면 줄 이름으로 나오지 않는다 (공개 상호와 이름이 같은 것은 뺀다)
        listing = (pages / "index.html").read_text(encoding="utf-8")
        assert not [no for no in private["store_no"] if no in listing or no in text]
        shown = {p["n"].split(" ")[0] for p in props}
        leaked = set(private["name"]) - set(df.loc[df["public"] == "True", "name"])
        assert not leaked & shown, sorted(leaked & shown)[:5]
        print(f"  사이트: 상호 페이지 {len(made):,}개, 화면 줄 {len(feats):,}개 (합친 줄 {sum(1 for p in props if p.get('rows', 1) > 1)}개)")
    else:
        print(f"  사이트: 상호 페이지가 아직 없음 (python -m src.graph.publish), 화면 줄 {len(feats):,}개")


def run() -> None:
    check_uri()
    check_plain()
    check_link()
    check_public_rule()
    check_merge()
    check_private_businesses()
    check_shapes()
    check_real_table()
    check_real_graph()
    check_real_site()
    print("상호 확인 통과")


def test_business():
    run()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    run()
