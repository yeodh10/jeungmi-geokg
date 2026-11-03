"""장소 카드(card.json)와 묻기 답(questions.json) 확인.

실행: python -m tests.test_cards
앞쪽은 작은 그래프로 카드 규칙을 보고, 뒤쪽은 data/processed/site가 있으면 실제 카드를 읽기만 한다
(python -m src.graph.publish, python -m src.graph.appdata 뒤).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pyoxigraph as ox

from src.graph.cards import CardMaker, floor_key, floor_text, use_plain
from src.graph.public import BASE, JGK, local_view

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "data" / "processed" / "site"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
GEO = "http://www.opengis.net/ont/geosparql#"
CARD_KEYS = {"path", "uri", "kind", "kindLabel", "name", "public", "facts", "links", "missing", "sources", "factCount"}


def check_helpers() -> None:
    assert floor_text("B1") == "지하 1층" and floor_text("3") == "3층" and floor_text("지") == "지"
    assert sorted(["3", None, "B2", "1", "B1"], key=floor_key) == ["B2", "B1", "1", "3", None]
    assert use_plain("제2종근린생활시설") == "상가 건물" and use_plain("노유자시설") == "어르신, 어린이 시설" and use_plain("공장") == "공장"


def small_card() -> None:
    b = f"{BASE}/building/1150010200106660040000099"
    lot = f"{BASE}/parcel/1150010200106660040"
    biz = f"{BASE}/business/MA0000000000000000001"
    lit = lambda v: ox.Literal(v) if not str(v).startswith("http") else ox.NamedNode(v)
    rows = [(b, RDF_TYPE, JGK + "Building"), (b, JGK + "floorsAbove", "0"), (b, JGK + "locatedOn", lot), (b, JGK + "useName", "제1종근린생활시설"),
            (b, GEO + "hasDefaultGeometry", b + "/geometry"), (b + "/geometry", GEO + "asWKT", "POINT (126.86 37.55)"),
            (lot, RDF_TYPE, JGK + "Parcel"), (f"{BASE}/legal-dong/1150010200", RDFS_LABEL, "등촌동"),
            (biz, RDF_TYPE, JGK + "Business"), (biz, RDFS_LABEL, "아무 카페"), (biz, JGK + "inBuilding", b), (biz, JGK + "floorInfo", "B1")]
    st = ox.Store()
    st.bulk_extend([ox.Quad(ox.NamedNode(s), ox.NamedNode(p), lit(o), ox.DefaultGraph()) for s, p, o in rows])
    view = local_view(st)
    places = {"places": [{"i": 0, "p": "building/1150010200106660040000099", "pt": [126.86, 37.55]}], "groups": [], "roads": [], "station": None}
    c = CardMaker(view, places).card(b)
    assert not [f for f in c["facts"] if "0층" in f["v"]] and "지상 층수" in c["missing"] and "사진" in c["missing"]
    assert {"k": "용도", "v": "상가 건물", "raw": "제1종근린생활시설"} in c["facts"]
    assert c["jibun"] == "등촌동 666-40" and c["pt"] == [126.86, 37.55]
    lot_link = next(x for x in c["links"] if x["rel"] == "필지")
    assert lot_link["spread"] == {"by": "pnu", "value": "1150010200106660040"}
    assert c["tenants"] == [{"name": "아무 카페", "path": "business/MA0000000000000000001", "floor": "지하 1층"}]
    bc = CardMaker(view, places).card(biz)
    assert "영업시간" in bc["missing"] and "2026년 9월" in bc["lead"] and bc["links"][0]["path"] == places["places"][0]["p"]


def walk_spread(obj) -> bool:
    """spread 안에 경로 목록이 있으면 참."""
    if isinstance(obj, dict):
        return any((k == "spread" and (isinstance(v, list) or isinstance(v.get("value"), list))) or walk_spread(v) for k, v in obj.items())
    if isinstance(obj, list):
        return any(walk_spread(x) for x in obj)
    return False


def named_public_links() -> int:
    """공개 근거 승인으로 주소가 이어진 이름 있는 주소 미연결 건물 수. 원천의 '이름은 있는데 주소 미연결' 147동에서 빠진다.
    승인 기록은 컴퓨터마다 달라 숫자로 적지 않고 기록에서 센다."""
    import geopandas as gpd

    from src.graph.public import public_approvals
    jgk = "https://yeodh10.github.io/jeungmi-geokg/def#"
    linked = {c.s for p in public_approvals() for c in p.changes
              if c.op == "add" and c.p == jgk + "status" and c.o.value == jgk + "AddressLinked"}
    b = gpd.read_file(ROOT / "data" / "processed" / "objects.gpkg", layer="building")
    return int(b[b["uri"].isin(linked)]["label"].notna().sum())


def check_real() -> None:
    if not (SITE / "assets" / "data" / "places.json").exists():
        print("  (앱 자료가 없어 실제 카드 확인은 건너뜀)")
        return
    one = lambda kind: next((SITE / "id" / kind).glob("*/card.json"))
    cards = {k: json.loads(one(k).read_text(encoding="utf-8")) for k in
             ("building", "building-gis", "parcel", "road", "admin-dong", "legal-dong", "basic-zone", "sensor", "entrance", "business")}
    cards["sensor"] = json.loads((SITE / "id/sensor/V02Q1940130/card.json").read_text(encoding="utf-8"))  # 관측값이 있는 센서
    cards["station"] = json.loads((SITE / "id/station/1150010200106660040000001/card.json").read_text(encoding="utf-8"))
    for k, c in cards.items():
        assert CARD_KEYS <= set(c), (k, CARD_KEYS - set(c))
        assert c["uri"].endswith("/id/" + c["path"]) and c["public"] is True
        assert not walk_spread(c), k
        text = json.dumps(c, ensure_ascii=False)
        assert "0층" not in text.replace("10층", "").replace("20층", "").replace("30층", ""), k
        assert not re.search("[\u00b7\u2027\u30fb\u318d\uff65]", text)
    st = cards["station"]
    assert st["external"][0]["name"] == "Q59989" and "시간표" in st["missing"] and "출구 번호" in st["missing"]
    sensor = cards["sensor"]["sensor"]
    assert sensor["items"] and all(len(x["series"]) > 24 for x in sensor["items"]) and "~" in sensor["period"]
    biz = cards["business"]
    assert biz["officialNo"]["label"] == "상가업소번호" and "영업시간" in biz["missing"]
    # 입점 상호가 있는 건물 카드 (올리브영 증미역점이 든 건물)
    oy = json.loads((SITE / "id/building/1150010100102400021028397/card.json").read_text(encoding="utf-8"))
    t = next(x for x in oy["tenants"] if x["name"].startswith("올리브영"))
    assert t.get("rows") == 2 and "입점 상호" not in oy["missing"]
    total, n = 0, 0
    for f in (SITE / "id").rglob("card.json"):
        total += f.stat().st_size
        n += 1
    assert total <= 25_000_000, f"card.json 합계 {total:,}바이트"
    pages = sum(1 for k in ("station", "building", "building-gis", "parcel", "road", "admin-dong", "legal-dong", "basic-zone", "sensor",
                            "entrance", "business") for _ in (SITE / "id" / k).glob("*/index.html"))
    assert n == pages, (n, pages)
    q = json.loads((SITE / "assets" / "data" / "questions.json").read_text(encoding="utf-8"))
    items = {x["id"]: x for x in q["items"]}
    assert len(items) >= 12 and all(x["rows"] and x["sparql"] and x["map"] for x in items.values())
    featured = {x["id"]: x["n"] for x in items.values() if x["featured"]}
    assert featured == {"edu-500": 22, "floors-20": 21, "apt-15": 162, "entrance-2": 140, "before-1980": 314,
                        "named-no-address": 147 - named_public_links(), "parcel-3": 151, "sensor-600": 3}, featured
    assert "{{NEAR:" in items["edu-500"]["sparql"] and "미리 실행한 답" in q["note"]
    names = [c["n"] for x in items.values() for r in x["rows"] for c in r if isinstance(c, dict)]
    assert "가동" not in names and not [x for x in names if re.search(r"[0-9]{20,}", x)]
    print(f"  실제 카드: {n:,}개, 합계 {total:,}바이트, 질문 {len(items)}개")


def run() -> None:
    check_helpers()
    small_card()
    check_real()
    print("장소 카드 확인 통과")


def test_cards():
    run()


if __name__ == "__main__":
    run()
