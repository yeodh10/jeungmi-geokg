"""지도 앱 첫 화면 자료(src.graph.places) 확인.

실행: python -m tests.test_places
앞쪽은 작은 표로 묶음 규칙을 보고, 뒤쪽은 data/processed/site/assets/data/places.json이 있으면 읽기만 한다
(python -m src.graph.publish, python -m src.graph.appdata 뒤).
"""
from __future__ import annotations

import gzip
import json
import re
from collections import Counter
from pathlib import Path

import yaml

from src.graph import places as P

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "processed" / "site" / "assets" / "data"


def row(i, label="", lot=None, pt=(126.86, 37.55), n=None):
    return {"i": i, "_label": label, "_lot": lot, "pnu": lot, "pt": list(pt), "n": n or label or f"이름 없는 건물 {i}"}


def check_grouping() -> None:
    near = (126.86, 37.55)
    far = (126.875, 37.55)   # 약 1.3km 떨어진 곳
    rows = [
        row(0, "가양9단지아파트 901동", "A", near), row(1, "가양9단지아파트 902동", "B", (126.8601, 37.5501)),  # 다른 필지, 150m 안
        row(2, "가양9단지아파트 903동", "C", far),                                                       # 같은 이름이지만 멀다
        row(3, "등촌중학교", "D"), row(4, "등촌중학교 다목적동", "D"),                                         # 같은 필지의 앞이름
        row(5, "", "D"), row(6, "가동", "D"),                                                            # 이름 없음, 동 표기만 -> 붙임
        row(7, "", "Z"),                                                                                # 붙일 곳이 없다
        row(8, "교사동", "E"), row(9, "등촌중", "D"),                                                     # '교사동'은 동 표기만이 아니다
    ]
    groups = P.group_buildings(rows)
    by_member = {i: g for g in groups for i in g["m"]}
    assert by_member[0] is by_member[1] and by_member[0]["name"] == "가양9단지아파트"
    assert by_member[2] is not by_member[0]
    assert by_member[3] is by_member[4] is by_member[5] is by_member[6] is by_member[9]  # '등촌중'도 앞이름(3글자)이다
    assert by_member[3]["name"] in ("등촌중학교", "등촌중")
    assert 7 not in by_member
    assert by_member[8]["name"] == "교사동"
    # 같은 필지에 묶음이 둘이면 대표점이 가까운 묶음에 붙인다
    rows = [row(0, "가나아파트", "L", (126.8600, 37.55)), row(1, "다라빌딩", "L", (126.8640, 37.55)), row(2, "", "L", (126.8638, 37.55))]
    groups = P.group_buildings(rows)
    assert next(g for g in groups if 2 in g["m"])["name"] == "다라빌딩"


def check_helpers() -> None:
    assert P.round6([126.12345678, [37.1234564]]) == [126.123457, [37.123456]]
    g = P.geometry_json("POLYGON ((126.1 37.1, 126.2 37.1, 126.2 37.2, 126.1 37.1))")
    assert g["type"] == "Polygon" and g["coordinates"][0][0] == [126.1, 37.1]
    assert P.geometry_json("POINT Z (126.8612469 37.5577194 3)") == {"type": "Point", "coordinates": [126.861247, 37.557719]}
    assert P.use_group_of(None) == "none" and P.use_group_of("제2종근린생활시설") == "shop" and P.use_group_of("모르는시설") == "other"
    assert P.label_tier("백석초등학교", 3, 1) == 1 and P.label_tier("강서소방서", 2, 1) == 1 and P.label_tier("아무개빌딩", 12, 1) == 2
    assert P.label_tier("아무개빌딩", 3, 2) == 2 and P.label_tier("아무개빌딩", 3, 5) == 1 and P.label_tier("아무개빌딩", 3, 1) == 3
    assert abs(P.straight_m((126.86, 37.55), (126.86, 37.551)) - 110.6) < 1
    assert P.chip_match({"small": ["카페"]}, {"cat": "카페"}) and not P.chip_match({"large": ["음식"], "exclude_middle": ["비알코올"]},
                                                                                      {"big": "음식", "mid": "비알코올"})


def check_config() -> None:
    cfg = yaml.safe_load((ROOT / "config" / "use_groups.yaml").read_text(encoding="utf-8"))
    keys = [t["key"] for t in cfg["themes"]]
    assert keys == ["home", "shop", "office", "edu", "factory", "car", "other", "none"], keys
    uses = [u for t in cfg["themes"] for u in t["uses"]]
    assert len(uses) == len(set(uses)) == 27, len(uses)  # 원래 값 27개가 한 곳씩
    for c in cfg["chips"]:
        assert c["kind"] in ("name", "use", "biz") and c["label"] and "," not in c["key"]
    words = yaml.safe_load((ROOT / "config" / "search_words.yaml").read_text(encoding="utf-8"))
    for w in ("카페", "약국", "은행", "편의점"):
        assert w not in words["absent_words"], w   # 0-1절: 상호로 찾는다
    assert "버스" in words["absent_words"] and "영업시간" in words["absent_words"] and "병원" in words["partial_words"]
    assert "공공데이터에 등록된 상호만 찾습니다" in words["partial_note"]
    for text in (json.dumps(cfg, ensure_ascii=False), json.dumps(words, ensure_ascii=False)):
        assert not re.search("[\u00b7\u2027\u30fb\u318d\uff65]", text)


def check_real() -> None:
    f = DATA / "places.json"
    if not f.exists():
        print("  (places.json이 없어 실제 자료 확인은 건너뜀)")
        return
    raw = f.read_bytes()
    d = json.loads(raw)
    rows, groups = d["places"], d["groups"]
    assert len(rows) == 2790, len(rows)
    assert 1900 <= d["count"]["places"] <= 1940, d["count"]          # 명세 측정 1,922곳
    assert 240 <= len(groups) <= 255, len(groups)                    # 명세 측정 247곳
    assert [r["i"] for r in rows] == list(range(len(rows)))
    assert not [r for r in rows if re.search(r"[0-9]{20,}", r["n"])]
    assert not [r for r in rows if r.get("fa") == 0 or r.get("fb") == 0]
    assert not [r for r in rows if not r.get("j")], "지번 없는 건물"
    st = Counter(r.get("s", "사용") for r in rows)
    from src.graph.public_check import expected_status_counts
    assert st == Counter({k: n for k, n in expected_status_counts().items() if n}), st  # 원천 수 + 공개 근거 승인의 상태 변경
    assert sum(1 for r in rows if r.get("sh") == 0) == 324
    by_name = {g["n"]: g for g in groups}
    g9 = by_name["가양9단지아파트"]
    named = sum(1 for i in g9["m"] if rows[i]["nf"] == 0)
    assert (len(g9["m"]), named, g9.get("hh")) == (38, 31, 1919), (len(g9["m"]), named, g9.get("hh"))
    assert rows[g9["m"][0]]["n"].startswith("가양9단지아파트")
    bs = by_name["백석초등학교"]
    assert (len(bs["m"]), sum(1 for i in bs["m"] if rows[i]["nf"] == 0)) == (17, 16)
    for g in groups:
        assert len(g["m"]) >= 2 and all(rows[i].get("g") == g["g"] for i in g["m"])
    road = next(r for r in d["roads"] if r["n"] == "양천로")
    assert (road["b"], road["b"] + road["side"]) == (195, 424), road
    assert d["station"]["n"] == "증미역" and d["station"]["b"].startswith("building/")
    assert len(d["parcels"]) == 1 and len(d["sensors"]) == 5
    chips = {c["key"]: c for c in d["chips"]}
    for k in ("school", "apt", "shop", "office", "cafe", "conv", "pharmacy", "clinic", "academy", "food"):
        assert k in chips and chips[k]["n"] >= 5, k
    assert all(c["n"] >= 5 for c in d["chips"])
    assert d["words"]["absent"] and d["words"]["partial"] and d["words"]["outside"]
    # 첫 화면이 받는 자료 전부 (places, shapes, area). 홈의 대표 질문과 기준 표기는 places.json에 들어 있어
    # questions.json, sources.json은 첫 화면에서 받지 않는다 (web/app/datatab.js, app.js)
    first = sum(len(gzip.compress((DATA / n).read_bytes() if n != "places.json" else raw, 6)) for n in ("places.json", "shapes.geojson", "area.geojson"))
    assert first <= 300_000, f"첫 화면 자료 압축 {first:,}바이트"
    assert len(d["featured"]) >= 3 and all(q["id"] and q["q"] and q["n"] for q in d["featured"]), d.get("featured")
    assert d["basis"].startswith("건물 "), d.get("basis")
    biz = json.loads((DATA / "businesses.json").read_text(encoding="utf-8"))
    names = [x["n"] for x in biz["items"]]
    assert any("올리브영" in n for n in names) and "지오스토리" in names
    oy = next(x for x in biz["items"] if x["n"].startswith("올리브영") and x.get("a", "").endswith("양천로 583"))
    assert oy.get("r") == 2, oy   # 같은 가게 두 행을 한 줄로
    geo = next(x for x in biz["items"] if x["n"] == "지오스토리")
    assert "직접 넣은 장소" in geo["src"]["note"] and "406" not in geo["a"]   # 호수는 넣지 않는다
    print(f"  실제 자료: 건물 {len(rows):,}, 장소 {d['count']['places']:,}, 묶음 {len(groups)}, 첫 화면 압축 {first:,}바이트")


def run() -> None:
    check_grouping()
    check_helpers()
    check_config()
    check_real()
    print("앱 첫 화면 자료 확인 통과")


def test_places():
    run()


if __name__ == "__main__":
    run()
