"""지도 앱의 첫 화면 자료: places.json, shapes.geojson, entrances.json, businesses.json (명세 5.3절, 9절, 0-1절).

공개 뷰(src.graph.public.public_view)와 전체 뷰(local_view) 어느 쪽을 줘도 동작한다. 보이는 것(view.shown)만 쓰고 각자 보류 판단을 하지 않는다.
  build_places(view, built)      -> places.json 모양 (건물 행, 묶음, 길, 동네, 우편구역, 필지, 센서, 역, 칩, 찾기 낱말)
  build_shapes(view, places)     -> shapes.geojson 모양 (면 건물만. properties: i, ug, fa, fb, y, yb)
  build_entrances(view)          -> entrances.json 모양 ({건물 경로: [[경도, 위도, 유형, 입구 번호], ...]})
  build_businesses(view, places) -> businesses.json 모양 (상호 줄. 같은 가게는 build_business.merge_same_store로 한 줄)

- 좌표는 소수 6자리로 반올림한다(round6). 공개 전 검사(src.graph.public_check)가 같은 함수로 공개 뷰 모양과 견준다.
- 값이 없으면 키를 뺀다. 층수 0은 값 없음이라 넣지 않는다.
- 묶음(단지)은 화면용 파생 값이다. 그래프 객체나 새 번호를 만들지 않는다 (명세 9절).
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

import shapely
import yaml

from src.graph.public import (BASE, BUILDING_KINDS, JGK, UNIT, UNIT_TAIL, GraphView, display_name, kind_of, short_address)

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config"
GEO = "http://www.opengis.net/ont/geosparql#"
STATION = BASE + "/station/1150010200106660040000001"
GROUP_METERS = 150      # 같은 이름의 대표점끼리 이 거리 안이면 한 묶음 (명세 9절)
PREFIX_MIN = 3          # 같은 필지에서 앞이름으로 이을 때 짧은 쪽의 최소 글자 수
SIDE_ROAD = r"^{name}[0-9]+[가-힣]?길$"   # 양천로61길, 공항대로58나길은 양천로, 공항대로의 곁길


def round6(v):
    """좌표(숫자, 목록)를 소수 6자리로. 공개 전 검사도 이 함수로 견준다."""
    if isinstance(v, (list, tuple)):
        return [round6(x) for x in v]
    return round(float(v), 6)


def geometry_json(wkt: str) -> dict:
    """WKT -> GeoJSON 모양 (좌표는 round6). 높이 값은 버린다."""
    g = shapely.force_2d(shapely.from_wkt(wkt))
    return {"type": g.geom_type, "coordinates": round6(shapely.geometry.mapping(g)["coordinates"])}


def point_of(g) -> tuple[float, float]:
    """대표점 (면은 안쪽 점, 점은 그대로). 글자와 표식, 거리 계산이 함께 쓴다."""
    p = g if g.geom_type == "Point" else g.representative_point()
    return (round(p.x, 6), round(p.y, 6))


def straight_m(a, b) -> float:
    """두 점(경도, 위도) 사이 직선 거리(m). 1km 안이라 평면 근사로 충분하다 (src.graph.publish와 같은 식)."""
    lat0 = math.radians((a[1] + b[1]) / 2)
    return math.hypot((a[0] - b[0]) * 111320 * math.cos(lat0), (a[1] - b[1]) * 110574)


@lru_cache(maxsize=None)
def load_yaml(name: str) -> dict:
    return yaml.safe_load((CONFIG / name).read_text(encoding="utf-8")) or {}


def use_group_of(use: str | None) -> str:
    """용도 원천 값 -> 주제 키 (config/use_groups.yaml). 값이 없으면 none, 표에 없는 값은 other."""
    if not use:
        return "none"
    for t in load_yaml("use_groups.yaml")["themes"]:
        if use in t["uses"]:
            return t["key"]
    return "other"


def wkt_of(view: GraphView, uri: str) -> str | None:
    """보이는 모양 노드의 WKT (기본 모양을 먼저 본다)."""
    for prop in ("hasDefaultGeometry", "hasGeometry"):
        for p, o in view.triples.get(uri, []):
            if p.value == GEO + prop and view.shown(o.value):
                w = next((x.value for q, x in view.triples.get(o.value, []) if q.value == GEO + "asWKT"), None)
                if w:
                    return w
    return None


def path_of(uri: str) -> str:
    return uri[len(BASE) + 1:]


def objects(view: GraphView, kinds) -> list[str]:
    """보이는 우리 장소(모양 노드 제외) 가운데 그 종류, 경로 순서."""
    kinds = {kinds} if isinstance(kinds, str) else set(kinds)
    return sorted(s for s in view.triples if s.startswith(BASE + "/") and not s.endswith("/geometry") and "/change/" not in s
                  and kind_of(s) in kinds and view.shown(s))


def name_source(view: GraphView, uri: str) -> int:
    """화면 이름이 무엇에서 왔나: 0 원천 이름(동 표기만이면 앞을 붙인 이름 포함), 1 주소, 2 지번."""
    if (view.labels.get(uri) or "").strip():
        return 0
    return 1 if view.addrs.get(uri) else 2


def has_words(name: str, words) -> bool:
    return any(w in name for w in words)


def building_rows(view: GraphView) -> list[dict]:
    """건물 행 (공개 뷰에서는 주소 있는 건물 2,162동과 주소 없는 건물 628동). i는 경로 순서다."""
    station_pt = None
    w = wkt_of(view, STATION)
    if w:
        station_pt = point_of(shapely.from_wkt(w))
    zone_no = {u: view.value(u, "basicZoneNo") or u.rsplit("/", 1)[-1] for u in objects(view, "basic-zone")}
    dong_name = {u: view.labels.get(u) for u in objects(view, "admin-dong")}
    rows = []
    for i, s in enumerate(objects(view, BUILDING_KINDS)):
        wkt = wkt_of(view, s)
        g = shapely.from_wkt(wkt) if wkt else None
        r = {"i": i, "p": path_of(s), "n": display_name(view, s), "nf": name_source(view, s)}
        addr = view.addrs.get(s)
        if addr:
            r["a"] = addr
        j = view.jibun(s)
        if j:
            r["j"] = j
        use = view.value(s, "useName")
        if use:
            r["use"] = use
        r["ug"] = use_group_of(use)
        for key, prop in (("fa", "floorsAbove"), ("fb", "floorsBelow")):
            v = view.value(s, prop)
            if v not in (None, "", "0"):
                r[key] = int(float(v))
        for key, prop in (("st", "structureName"), ("ad", "approvalDate")):
            v = view.value(s, prop)
            if v:
                r[key] = v
        pnu = view.pnu_of(s)
        if pnu:
            r["pnu"] = pnu
            r["ld"] = pnu[:10]
        ld = view.value(s, "inLegalDong")
        if ld and view.shown(ld):
            r["ld"] = ld.rsplit("/", 1)[-1]
        dong = view.value(s, "within")
        if dong in dong_name and dong_name[dong]:
            r["dong"] = dong_name[dong]
        zone = view.value(s, "inBasicZone")
        if zone in zone_no:
            r["zip"] = zone_no[zone]
        road = view.value(s, "addressRoad")
        if road and view.shown(road):
            r["road"] = road.rsplit("/", 1)[-1]
        status = view.value(s, "status")
        status_ko = view.labels.get(status) if status else None
        if status_ko and status_ko != "사용":
            r["s"] = status_ko
        if g is not None:
            r["pt"] = list(point_of(g))
            r["sh"] = 0 if g.geom_type == "Point" else 1
            if station_pt:
                r["d"] = int(round(straight_m(r["pt"], station_pt)))
        if addr:
            m = re.search(r"([0-9]+(?:-[0-9]+)?)\s*$", addr)
            if m:
                r["no"] = m.group(1)
        lots = sorted(view.values(s, "locatedOn"))
        r["_uri"], r["_label"], r["_lot"] = s, (view.labels.get(s) or "").strip(), (lots[0].rsplit("/", 1)[-1] if lots else None)
        rows.append(r)
    return rows


def group_buildings(rows: list[dict]) -> list[dict]:
    """명세 9절 묶음 규칙. 반환: 이름 묶음마다 {"name", "m": [건물 i, ...]} (한 동짜리도 포함). 이름 없는 건물은 붙인 뒤의 결과다.

    1) 끝의 동 표기를 뺀 이름이 같고, 같은 PNU이거나 대표점끼리 150m 안이면 잇는다 (연결 요소).
    2) 같은 PNU에서 한 이름이 다른 이름의 앞부분이면 잇는다 (짧은 쪽 3글자 이상).
    3) 이름이 없거나 동 표기만인 건물은 같은 PNU의 묶음에 붙인다. 그런 묶음이 둘 이상이면 대표점이 가장 가까운 묶음에 붙인다.
    여기서 PNU는 그래프의 필지 연결(locatedOn)만 쓴다. 필지 연결이 없는 168동은 건물관리번호 앞 19자리를 지번에만 쓰고 묶음에는 쓰지 않는다
    (명세 0절의 측정값 이름 없는 건물 217동, 단독 1,105곳과 같아진다).
    """
    named = [r for r in rows if r["_label"] and not UNIT.match(r["_label"])]
    base = {r["i"]: (UNIT_TAIL.sub("", r["_label"]).strip() or r["_label"]) for r in named}
    parent = {r["i"]: r["i"] for r in named}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    by_base = defaultdict(list)
    for r in named:
        by_base[base[r["i"]]].append(r)
    for same in by_base.values():
        for x in range(len(same)):
            for y in range(x + 1, len(same)):
                a, b = same[x], same[y]
                if (a["_lot"] and a["_lot"] == b["_lot"]) or (
                        "pt" in a and "pt" in b and straight_m(a["pt"], b["pt"]) <= GROUP_METERS):
                    union(a["i"], b["i"])
    by_pnu = defaultdict(list)
    for r in named:
        if r["_lot"]:
            by_pnu[r["_lot"]].append(r)
    for same in by_pnu.values():
        for a in same:
            for b in same:
                ba, bb = base[a["i"]], base[b["i"]]
                if ba != bb and len(ba) >= PREFIX_MIN and bb.startswith(ba):
                    union(a["i"], b["i"])

    comps = defaultdict(list)
    for r in named:
        comps[find(r["i"])].append(r["i"])
    by_i = {r["i"]: r for r in rows}

    def centre(members):
        pts = [by_i[i]["pt"] for i in members if "pt" in by_i[i]]
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)) if pts else None

    groups = []
    for root, members in sorted(comps.items()):
        names = Counter(base[i] for i in members)
        top = max(names.values())
        name = min((n for n, c in names.items() if c == top), key=lambda n: (len(n), n))
        groups.append({"name": name, "m": sorted(members), "named": set(members)})
    on_pnu = defaultdict(list)
    for gi, g in enumerate(groups):
        for i in g["m"]:
            if by_i[i]["_lot"]:
                on_pnu[by_i[i]["_lot"]].append(gi)
    centres = [centre(g["m"]) for g in groups]
    for r in rows:
        if r["i"] in parent:
            continue
        cands = sorted(set(on_pnu.get(r["_lot"], []))) if r["_lot"] else []
        if not cands:
            continue
        if len(cands) > 1 and "pt" in r:
            cands.sort(key=lambda gi: straight_m(r["pt"], centres[gi]) if centres[gi] else 1e9)
        groups[cands[0]]["m"].append(r["i"])
    return groups


def order_members(g: dict, by_i: dict) -> list[int]:
    """묶음 동 순서: 이름이 묶음 이름과 같은 동, 이름 있는 동(이름 순), 이름 없는 동. m[0]이 대표 동이다."""
    def natural(s: str):
        return [int(t) if t.isdigit() else t for t in re.split(r"([0-9]+)", s)]
    return sorted(g["m"], key=lambda i: (by_i[i]["_label"] != g["name"], i not in g["named"], natural(by_i[i]["n"]), i))


def group_lots(view: GraphView, pnus: list[str]) -> dict:
    """묶음 필지의 세대수, 대지면적 합. 값이 있는 필지가 일부면 part를 붙인다."""
    out = {}
    for key, prop in (("hh", "households"), ("site", "siteArea")):
        vals = [view.value(f"{BASE}/parcel/{p}", prop) for p in pnus if view.shown(f"{BASE}/parcel/{p}")]
        got = [float(v) for v in vals if v not in (None, "")]
        if got:
            total = sum(got)
            out[key] = int(total) if key == "hh" else round(total, 1)
            if len(got) < len(vals):
                out.setdefault("part", []).append(key)
    return out


def label_tier(name: str, floors: int, size: int) -> int:
    """글자 단계 (명세 9절): 1 학교, 기관 낱말, 5동 이상 묶음. 2 2동 이상 묶음, 10층 이상. 3 나머지 이름 있는 장소."""
    cfg = load_yaml("use_groups.yaml")
    if has_words(name, cfg["school_words"]) or has_words(name, cfg["agency_words"]) or size >= 5:
        return 1
    if size >= 2 or floors >= 10:
        return 2
    return 3


def road_counts(view: GraphView, rows: list[dict]) -> list[dict]:
    names = {u: view.labels.get(u) or "" for u in objects(view, "road")}
    per = Counter(r.get("road") for r in rows if r.get("road"))
    out = []
    for u, n in names.items():
        code = u.rsplit("/", 1)[-1]
        side = re.compile(SIDE_ROAD.format(name=re.escape(n)))
        side_n = sum(c for v, c in per.items() for w, m in names.items()
                     if w.rsplit("/", 1)[-1] == v and v[:5] == code[:5] and side.match(m))
        out.append({"p": path_of(u), "n": n, "b": per.get(code, 0), "side": side_n})
    return out


def build_places(view: GraphView, built: str) -> dict:
    rows = building_rows(view)
    by_i = {r["i"]: r for r in rows}
    cfg = load_yaml("use_groups.yaml")
    words = load_yaml("search_words.yaml")

    # 묶음: 두 동 이상만 g 번호를 준다. 한 동은 그 건물이 곧 장소다
    groups_out, place_names = [], []   # place_names: (장소 이름, 건물 i 목록) 칩 세기용
    grouped = set()
    for g in group_buildings(rows):
        if len(g["m"]) < 2:
            continue
        m = order_members(g, by_i)
        gid = len(groups_out)
        pts = [by_i[i]["pt"] for i in m if "pt" in by_i[i]]
        cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        rep = min(pts, key=lambda p: straight_m(p, (cx, cy)))
        pnus = sorted({by_i[i]["pnu"] for i in m if by_i[i].get("pnu")})
        floors = max((by_i[i].get("fa", 0) for i in m), default=0)
        item = {"g": gid, "n": g["name"], "m": m, "pt": list(rep), "t": label_tier(g["name"], floors, len(m))} | group_lots(view, pnus)
        item["pnu"] = pnus
        groups_out.append(item)
        for i in m:
            by_i[i]["g"] = gid
            grouped.add(i)
        place_names.append((g["name"], m))
    for r in rows:
        if r["i"] in grouped:
            continue
        place_names.append((r["n"], [r["i"]]))
        if r["nf"] == 0:
            r["t"] = label_tier(r["n"], r.get("fa", 0), 1)

    # 칩: 장소(묶음은 한 곳) 수를 세고 5곳 미만은 숨긴다. 상호 칩은 build_businesses가 센다
    chips = []
    for c in cfg["chips"]:
        if c["kind"] == "biz":
            chips.append(dict(c))
            continue
        n = 0
        for name, members in place_names:
            if c["kind"] == "name":
                hit = has_words(name, c["words"])
            else:
                hit = any(by_i[i].get("use") in c["uses"] for i in members) and not has_words(name, c.get("exclude_words", []))
            n += hit
        chips.append(dict(c) | {"n": n})

    use_n = Counter(r["ug"] for r in rows)
    use_groups = [{"key": t["key"], "label": t["label"], "color": t["color"], "n": use_n.get(t["key"], 0)} for t in cfg["themes"]]

    # 길, 동네, 우편구역
    roads = road_counts(view, rows)
    dongs = []
    for u in objects(view, "admin-dong"):
        n = view.labels.get(u)
        dongs.append({"p": path_of(u), "n": n, "kind": "admin", "b": sum(1 for r in rows if r.get("dong") == n)})
    for u in objects(view, "legal-dong"):
        code = u.rsplit("/", 1)[-1]
        dongs.append({"p": path_of(u), "n": view.labels.get(u), "kind": "legal", "b": sum(1 for r in rows if r.get("ld") == code)})
    zones = []
    for u in objects(view, "basic-zone"):
        no = view.value(u, "basicZoneNo") or u.rsplit("/", 1)[-1]
        zones.append({"p": path_of(u), "n": no, "b": sum(1 for r in rows if r.get("zip") == no)})
    used = {r.get("pnu") for r in rows}
    parcels = [{"p": path_of(u), "j": view.jibun(u) or u.rsplit("/", 1)[-1]} for u in objects(view, "parcel") if u.rsplit("/", 1)[-1] not in used]
    sensors = []
    for u in objects(view, "sensor"):
        w = wkt_of(view, u)
        item = {"p": path_of(u), "n": display_name(view, u)}
        if view.value(u, "installAddress"):
            item["a"] = view.value(u, "installAddress")
        if w:
            item["pt"] = list(point_of(shapely.from_wkt(w)))
        sensors.append(item)
    station = None
    if view.shown(STATION):
        w = wkt_of(view, STATION)
        b = view.value(STATION, "housedIn")
        station = {"p": path_of(STATION), "n": display_name(view, STATION), "line": view.value(STATION, "lineName")}
        if view.address_of(STATION):
            station["a"] = view.address_of(STATION)
        if w:
            station["pt"] = list(point_of(shapely.from_wkt(w)))
        if b and view.shown(b):
            station["b"] = path_of(b)
        station = {k: v for k, v in station.items() if v is not None}

    # 공공시설 (전체 뷰에서만 보인다. 로컬 서버가 kind facility로 더한다)
    facilities = []
    for u in objects(view, "public-facility"):
        w = wkt_of(view, u)
        item = {"p": path_of(u), "n": display_name(view, u), "kind": "facility"}
        if view.address_of(u):
            item["a"] = view.address_of(u)
        if w:
            item["pt"] = list(point_of(shapely.from_wkt(w)))
        facilities.append(item)

    lon, lat = load_yaml("area.yaml")["center"]["epsg4326_lonlat"]
    places = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    out = {
        "v": 1, "built": built, "center": [round(lon, 5), round(lat, 5)],
        "legalDong": {u.rsplit("/", 1)[-1]: view.labels.get(u) for u in objects(view, "legal-dong")},
        "useGroups": use_groups,
        "chips": [c for c in chips if c["kind"] == "biz" or c["n"] >= 5],
        "words": {"aliases": words.get("aliases") or {}, "suffix": words.get("suffix_aliases") or {},
                  "drop": words.get("drop_tokens") or [], "absent": words.get("absent_words") or [],
                  "partial": words.get("partial_words") or [], "outside": words.get("outside_words") or [],
                  "notes": {k: words[k] for k in ("absent_note", "partial_note", "outside_note", "none_note") if words.get(k)}},
        "count": {"buildings": len(rows), "places": len(place_names), "groups": len(groups_out)},
        "places": places, "groups": groups_out, "roads": roads, "dongs": dongs, "zones": zones,
        "parcels": parcels, "sensors": sensors, "station": station,
    }
    if facilities:
        out["facilities"] = facilities
    return out


def build_shapes(view: GraphView, places: dict) -> dict:
    """면 건물의 모양. 점만 있는 건물은 places.json의 pt로 그린다."""
    features = []
    for r in places["places"]:
        if r.get("sh") != 1:
            continue
        g = geometry_json(wkt_of(view, BASE + "/" + r["p"]))
        props = {"i": r["i"], "ug": r["ug"]}
        for k in ("fa", "fb"):
            if k in r:
                props[k] = r[k]
        if r.get("ad"):
            y = int(r["ad"][:4])
            props["y"], props["yb"] = y, y // 10 * 10
        features.append({"type": "Feature", "geometry": g, "properties": props})
    return {"type": "FeatureCollection", "features": features}


def build_entrances(view: GraphView) -> dict:
    """건물 경로 -> 그 건물 입구 [경도, 위도, 유형, 입구 번호]. 둘 이상 건물에 속한 입구는 각 건물에 모두 넣는다."""
    out = defaultdict(list)
    for b in objects(view, BUILDING_KINDS):
        for e in sorted(view.values(b, "hasEntrance")):
            if not view.shown(e):
                continue
            w = wkt_of(view, e)
            if not w:
                continue
            x, y = geometry_json(w)["coordinates"][:2]
            out[path_of(b)].append([x, y, view.value(e, "entranceType") or "", e.rsplit("/", 1)[-1]])
    return dict(sorted(out.items()))


def business_rows(view: GraphView) -> list[dict]:
    """보이는 상호마다 한 줄 (합치기 전). 원천 행 그대로이고 값을 지어내지 않는다."""
    from src.model.build_business import tidy
    rows = []
    for s in objects(view, "business"):
        name, branch = view.labels.get(s) or "", view.value(s, "branchName") or ""
        b, lot = view.value(s, "inBuilding"), view.value(s, "onParcel")
        w = wkt_of(view, s)
        r = {"n": name + (f" {branch}" if branch else ""), "key": tidy(name) + tidy(branch), "p": path_of(s),
             "big": view.value(s, "categoryLarge") or "", "mid": view.value(s, "categoryMiddle") or "", "cat": view.value(s, "categorySmall") or "",
             "floor": view.value(s, "floorInfo") or "", "addr": view.value(s, "roadAddress") or "",
             "bu": path_of(b) if b and view.shown(b) else "", "lot": path_of(lot) if lot and view.shown(lot) else "",
             "by": view.value(s, "linkedBy") or ""}
        if w:
            r["pt"] = list(point_of(shapely.from_wkt(w)))
        rows.append(r)
    return rows


def extra_rows(view: GraphView) -> list[dict]:
    """config/places_extra.yaml: 상가정보에 없어 사람이 출처와 함께 넣은 장소 (그래프 객체가 아니다). 그 건물이 보일 때만 넣는다."""
    from src.model.build_business import tidy
    path = CONFIG / "places_extra.yaml"
    extra = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else []
    out = []
    for e in extra or []:
        b = f"{BASE}/building/{e['building']}"
        if not view.shown(b):
            continue
        w = wkt_of(view, b)
        r = {"n": e["name"], "key": tidy(e["name"]), "p": "", "big": "", "mid": "", "cat": "", "floor": "", "addr": e["address"],
             "bu": path_of(b), "lot": "", "by": "", "rows": 1,
             "src": {"note": f"직접 넣은 장소, 출처: {e['source_name']}", "url": e["source"], "checked": str(e["checked"])}}
        if w:
            r["pt"] = list(point_of(shapely.from_wkt(w)))
        out.append(r)
    return out


def chip_match(c: dict, r: dict) -> bool:
    if r.get("mid") in c.get("exclude_middle", []):
        return False
    return r.get("cat") in c.get("small", []) or r.get("mid") in c.get("middle", []) or r.get("big") in c.get("large", [])


def build_businesses(view: GraphView, places: dict) -> dict:
    """상호 찾기 자료. 같은 건물 안 같은 가게는 한 줄로 합치고(merge_same_store) 원천 행 수를 r에 적는다.
    찾기창에 처음 입력할 때 받으므로 첫 화면 크기에 들지 않는다. 상호 칩의 n도 여기서 세어 places['chips']에 적는다."""
    from src.model.build_business import merge_same_store
    index = {r["p"]: r["i"] for r in places["places"]}
    merged = merge_same_store(business_rows(view)) + extra_rows(view)
    items = []
    for r in merged:
        item = {"n": r["n"]}
        for key, src in (("p", "p"), ("c", "cat"), ("m", "mid"), ("l", "big"), ("f", "floor"), ("by", "by"), ("lot", "lot")):
            if r.get(src):
                item[key] = r[src]
        if r.get("addr"):
            item["a"] = short_address(r["addr"])
        if r.get("bu"):
            item["b"] = r["bu"]
            if r["bu"] in index:
                item["i"] = index[r["bu"]]
        if r.get("pt"):
            item["pt"] = r["pt"]
        if r["rows"] > 1:
            item["r"] = r["rows"]
        if r.get("src"):
            item["src"] = r["src"]
        items.append(item)
    for c in places["chips"]:
        if c["kind"] == "biz":
            c["n"] = sum(1 for r in merged if not r.get("src") and chip_match(c, r))
    places["chips"] = [c for c in places["chips"] if c["n"] >= 5]
    return {"v": 1, "built": places["built"], "basis": "소상공인시장진흥공단 상가(상권)정보 2026년 9월 기준",
            "rows": sum(r["rows"] for r in merged if not r.get("src")), "items": items}
