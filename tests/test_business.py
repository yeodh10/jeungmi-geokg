"""상호(소상공인 상가정보) 확인: 건물 잇기 규칙, 공개 규칙, 같은 가게 합치기, 그래프 객체, 공개 사이트.

실행: python -m tests.test_business
앞쪽은 작은 표로 규칙을 보고, 뒤쪽은 data/processed의 결과(business.csv, 그래프, 공개 사이트 assets/data와 id/business)가 있으면 읽기만 한다.
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
    # 용도를 모르는 건물(빈 값)과 위에 건물이 없는 필지는 주택일 수 있어 주택과 같은 규칙 (2026-11-03 검토)
    assert not bb.is_public([None], "부동산", "4", rule)
    assert not bb.is_public([""], "과학, 기술", None, rule)
    assert bb.is_public([None], "음식", "1", rule)
    assert not bb.is_public(["업무시설", None], "과학, 기술", "3", rule)
    assert not bb.is_public([], "과학, 기술", "1", rule)
    assert bb.is_public([None], "소매", None, rule)       # 층 정보가 없는 큰 건물의 가게 (올리브영증미역점)
    assert not bb.is_public([None], "교육", "3", rule)    # 층을 알면 1층이나 지하일 때만
    # 용도가 비어도 주택이라는 근거가 있으면 주택으로 본다
    assert bb.effective_use(None, "강나루현대아파트") == "주택" and bb.effective_use("", None, [("642", "공동주택")]) == "주택"
    assert bb.effective_use(None, "우림블루나인비즈니스센터", [(None, None)]) is None and bb.effective_use("업무시설", "아파트상가") == "업무시설"
    assert not bb.is_public([bb.effective_use(None, "가양9단지아파트")], "교육", None, rule)
    # 이름에 호수가 든 상호는 내지 않는다. '2호점' 같은 가게 번호는 호수가 아니다
    assert not bb.is_public(["업무시설"], "과학, 기술", "8", rule, name="더리브아너비즈타워810호")
    assert not bb.is_public(["업무시설"], "과학, 기술", None, rule, name="가양더리브아너비즈118;119;120호")
    assert bb.is_public(["업무시설"], "소매", "1", rule, name="별텔레콤 2호점")


def check_floor_fits() -> None:
    """층 값이 건물 지상 층수보다 크거나, 층수를 모르는데 100 이상이면 호수일 수 있어 층으로 보이지 않는다."""
    for floor, top, ok in (("1501", "15", False), ("15", "15", True), ("16", "15.0", False), ("103", None, False), ("21", None, True),
                           ("지", "1", True), ("B1", None, True), ("0", "3", False), (None, "3", False), ("", None, False), ("3", "0", True)):
        assert bb.floor_fits(floor, top) is ok, (floor, top)


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
    unknown = uri.building("1150010200106660040000003")   # 용도 값이 없는 건물
    put(unknown, JGK + "floorsAbove", lit("5"))
    flats, flats_lot = uri.building("1150010200106660041000001"), uri.parcel("1150010200106660041")   # 용도가 비었지만 필지에 세대수가 있다
    put(flats, JGK + "locatedOn", node(flats_lot))
    put(flats_lot, JGK + "households", lit("120"))
    cases = {"A": (home, None, "부동산", "1", True), "B": (home, None, "음식", "1", False), "C": (office, None, "부동산", None, False),
             "D": (None, lot, "과학, 기술", "1", True), "E": (None, None, "음식", "1", True),
             "F": (unknown, None, "부동산", "4", True), "G": (unknown, None, "음식", "1", False), "H": (office, None, "과학, 기술", "8", True),
             "I": (unknown, None, "교육", None, False), "J": (flats, None, "교육", None, True)}
    names = {"H": "타워810호"}
    for k, (b, p, big, floor, _) in cases.items():
        s = uri.business(f"MA0101202310A000139{k}")
        put(s, RDF_TYPE, node(JGK + "Business"))
        put(s, "http://www.w3.org/2000/01/rdf-schema#label", lit(names.get(k, "가게")))
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

    # 층으로 보기 어려운 층 값: 건물 층수(5)보다 큰 값과, 건물을 모를 때 100 이상
    from src.graph.public import unfit_floors
    t2 = defaultdict(list)
    t2[unknown] = list(t[unknown])
    for k, (b, floor) in {"P": (unknown, "1501"), "Q": (unknown, "5"), "R": (None, "103"), "S": (None, "지")}.items():
        s = uri.business(f"MA0101202310A000139{k}")
        t2[s] += [(node(RDF_TYPE), node(JGK + "Business")), (node(JGK + "floorInfo"), lit(floor))]
        if b:
            t2[s].append((node(JGK + "inBuilding"), node(b)))
    assert unfit_floors(t2) == {uri.business("MA0101202310A000139P"), uri.business("MA0101202310A000139R")}, unfit_floors(t2)


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
    """공개 사이트의 상호: 화면 자료(assets/data/businesses.json)와 상호 페이지가 공개 뷰(src.graph.public.public_view)와 맞는지."""
    site = PROC / "site"
    path = site / "assets" / "data" / "businesses.json"
    if not path.exists():
        print("  사이트가 없어 실제 사이트 확인은 건너뜀 (python -m src.graph.publish, python -m src.graph.appdata)")
        return
    from src.graph.public import BASE, FLOOR_WITHHELD, public_view
    view = public_view()
    biz = {s for s in view.triples if s.startswith(BASE + "/business/") and not s.endswith("/geometry")}
    shown = {s.rsplit("/id/", 1)[-1] for s in biz if view.shown(s)}
    hidden = {s.rsplit("/id/", 1)[-1] for s in biz if not view.shown(s)}
    assert len(biz) == 3332 and shown and hidden, (len(biz), len(shown))
    text = path.read_text(encoding="utf-8")
    items = json.loads(text)["items"]
    assert all(x["p"] in shown for x in items if "p" in x), "공개하지 않는 상호가 화면 자료에 있다"
    assert not DOTS.search(text), "화면 자료에 가운뎃점"
    olive = [x for x in items if x.get("b") == OLIVE_BUILDING and "올리브영증미역점" in x["n"]]
    assert len(olive) == 1 and olive[0]["r"] == 2, olive
    geo = [x for x in items if x["n"] == "지오스토리"]
    assert len(geo) == 1 and geo[0]["src"]["note"].startswith("직접 넣은 장소, 출처:") and "406" not in json.dumps(geo[0], ensure_ascii=False), geo

    # 이름에 호수가 든 상호, 층으로 보기 어려운 층 값, 용도를 모르는 건물 안의 사무형 업체와 위층 업체가 없다
    rule = bb.load_rule()
    assert not [x["n"] for x in items if rule["hide_name"].search(x["n"])]
    places = json.loads((site / "assets" / "data" / "places.json").read_text(encoding="utf-8"))["places"]
    fa = {p["p"]: p.get("fa") for p in places}
    use = {p["p"]: p.get("use") for p in places}
    assert not [x for x in items if x.get("f") and not bb.floor_fits(x["f"], fa.get(x.get("b")))]
    odd = [x for x in items if x.get("p") and x.get("b") and not use.get(x["b"]) and not (x.get("l") in rule["walk_in"] and (not x.get("f") or bb.open_floor(x["f"])))]
    assert not odd, odd[:3]

    pages = site / "id" / "business"
    made = {d.name for d in pages.iterdir() if d.is_dir()}
    assert made == {p.rsplit("/", 1)[-1] for p in shown}, (len(made), len(shown))
    assert not made & {p.rsplit("/", 1)[-1] for p in hidden}, "공개하지 않는 상호의 페이지가 있다"
    page = (pages / olive[0]["p"].rsplit("/", 1)[-1] / "index.html").read_text(encoding="utf-8")
    assert "상가업소번호" in page and "들어 있는 건물" in page and "영업시간, 전화번호, 리뷰는 이 자료에 없습니다" in page
    # 층 값을 뺀 상호는 페이지와 기계용 원본에 그 값이 없고, 뺀 까닭이 적혀 있다
    withheld = sorted(s.rsplit("/id/", 1)[-1] for s in view.unfit_floor if view.shown(s))
    assert withheld, "층 값을 뺀 상호가 없다 (검토 때 93곳)"
    for p in withheld[:20]:
        d = pages / p.rsplit("/", 1)[-1]
        assert "floorInfo" not in (d / "index.ttl").read_text(encoding="utf-8"), p
        assert FLOOR_WITHHELD in (d / "index.html").read_text(encoding="utf-8"), p
        card = json.loads((d / "card.json").read_text(encoding="utf-8"))
        assert {"k": "층", "v": FLOOR_WITHHELD} in card["facts"] and "층" not in card["missing"], card["facts"]
    # 공개하지 않는 상호는 번호가 목록 페이지와 화면 자료에 없고, 이름이 화면 줄 이름으로 나오지 않는다 (공개 상호와 이름이 같은 것은 뺀다)
    listing = (pages / "index.html").read_text(encoding="utf-8")
    assert not [p for p in hidden if p.rsplit("/", 1)[-1] in listing or p.rsplit("/", 1)[-1] in text]
    names = lambda ps: {view.labels.get(BASE + "/" + p) for p in ps}
    leaked = names(hidden) - names(shown)
    line_names = {x["n"].split(" ")[0] for x in items}
    assert not leaked & line_names, sorted(leaked & line_names)[:5]
    print(f"  사이트: 상호 페이지 {len(made):,}개, 공개하지 않음 {len(hidden):,}곳, 층 값을 뺀 상호 {len(withheld)}곳, 화면 줄 {len(items):,}개 "
          f"(합친 줄 {sum(1 for x in items if x.get('r', 1) > 1)}개)")


def run() -> None:
    check_uri()
    check_plain()
    check_link()
    check_public_rule()
    check_floor_fits()
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
