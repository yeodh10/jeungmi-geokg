"""장소 카드 자료 card.json (명세 3.6절, 5.3절, 0-1절).

  cards = CardMaker(view, places)     places는 build_places의 결과 (묶음, 건물 i, 거리)
  cards.card(uri) -> dict             한 장소의 카드
  cards.uris()                        카드를 만들 장소 (관측과 모양 노드는 뺀다)

공개 뷰와 전체 뷰 모두에서 동작한다. 보이는 것(view.shown)만 쓰고 종류를 고정해 가정하지 않는다
(전체 뷰의 공공시설, 도로 구간 같은 종류도 같은 틀로 만든다). 공개 판단은 src.graph.public 한 곳에서 한다.

- spread는 경로 목록이 아니라 places.json에서 찾을 조건이다 ({"by": "pnu"|"ld"|"dong"|"road"|"zip"|"g", "value": ...}).
  건물 카드마다 행정동의 모든 건물 경로를 넣으면 합계가 160MB가 넘기 때문이다.
- 층수 0은 값 없음이다. '0층'을 만들지 않는다.
- 없는 정보(missing)는 값이 비어 있는 칸과, 이 자료에 아예 없는 것(영업시간 같은)을 함께 적는다.
"""
from __future__ import annotations

import re
from collections import defaultdict
from statistics import mean

import shapely

from src.graph.places import path_of, point_of, straight_m, wkt_of
from src.graph.public import (BASE, DCT_SOURCE, JGK, RDF_TYPE, GraphView, display_name, kind_of, short_address)

SOSA = "http://www.w3.org/ns/sosa/"
OWL_SAME = "http://www.w3.org/2002/07/owl#sameAs"
GEO = "http://www.opengis.net/ont/geosparql#"
CARD_KINDS = ("station", "building", "building-gis", "parcel", "road", "admin-dong", "legal-dong", "basic-zone", "sensor", "entrance",
              "business", "public-facility", "road-segment")
KIND_LABEL = {"station": "역", "building": "건물", "building-gis": "건물", "parcel": "필지", "road": "길", "admin-dong": "행정동",
              "legal-dong": "법정동", "basic-zone": "우편구역", "sensor": "센서", "entrance": "건물 출입구", "business": "상호",
              "public-facility": "공공시설", "road-segment": "도로 구간"}
OFFICIAL = {"building": ("buildingMgtNo", "건물관리번호"), "building-gis": ("gisBuildingId", "GIS건물통합식별번호"),
            "parcel": ("pnu", "PNU"), "road": ("roadCode", "도로명코드"), "admin-dong": ("admDongCode", "행정동코드"),
            "legal-dong": ("legalDongCode", "법정동코드"), "basic-zone": ("basicZoneNo", "기초구역번호"),
            "sensor": ("sensorSerial", "센서 시리얼"), "business": ("businessNo", "상가업소번호")}
# 이 자료에 아예 없는 것 (명세 3.6절 표, 0-1절. 건물의 '입점 상호'는 0-1절로 뺐다)
ABSENT = {"station": ["시간표", "출구 번호", "도착 정보"], "building": ["사진"], "building-gis": ["도로명주소", "사진"],
          "parcel": ["경계 모양"], "road": ["도로선 모양", "폭"], "admin-dong": ["경계 모양"], "legal-dong": ["경계 모양"],
          "basic-zone": ["경계 모양"], "sensor": ["실시간 값"], "entrance": ["이름"],
          "business": ["영업시간", "전화번호", "리뷰", "사진"]}
# 값이 있어야 할 칸 (없으면 missing에 적는다)
EXPECT = {"building": [("useName", "용도"), ("floorsAbove", "지상 층수"), ("structureName", "구조"), ("approvalDate", "사용승인일")],
          "building-gis": [("useName", "용도"), ("floorsAbove", "지상 층수"), ("structureName", "구조"), ("approvalDate", "사용승인일")],
          "parcel": [("siteArea", "대지면적")], "business": [("floorInfo", "층")]}
UNIT_KO = {"DEG_C": "℃", "PERCENT": "%", "DeciB": "dB", "LUX": "lux", "UNITLESS": ""}
BUSINESS_NOTE = "상가정보 2026년 9월 기준 자료라 문을 닫거나 새로 연 가게와 다를 수 있습니다."


def use_plain(v: str) -> str:
    """용도의 쉬운 말 (명세 8절)."""
    if "근린생활시설" in v:
        return "상가 건물"
    if v == "노유자시설":
        return "어르신, 어린이 시설"
    return v


def floor_text(v: str) -> str:
    """상호 층 정보: 'B1' -> '지하 1층', '3' -> '3층'. 그 밖의 값은 그대로."""
    v = v.strip()
    m = re.fullmatch(r"B([0-9]+)", v)
    if m:
        return f"지하 {m.group(1)}층"
    return f"{v}층" if re.fullmatch(r"[0-9]+", v) else v


def floor_key(v: str | None) -> tuple:
    """층 순서: 지하(깊은 곳부터), 1층부터 위로, 값이 없으면 맨 뒤."""
    if not v:
        return (2, 0, "")
    m = re.fullmatch(r"(?:B|지하\s*)([0-9]+)", v.strip())
    if m:
        return (0, -int(m.group(1)), v)
    m = re.match(r"([0-9]+)", v.strip())
    return (1, int(m.group(1)), v) if m else (1, 999, v)


def num(v: str):
    f = float(v)
    return int(f) if f.is_integer() else f


class CardMaker:
    def __init__(self, view: GraphView, places: dict, public: set[str] | None = None, business_lines: list[dict] | None = None):
        """public: 공개 사이트에 있는 우리 URI (로컬 카드의 public 값). 주지 않으면 이 뷰에서 보이는 것을 공개로 본다.
        business_lines: build_business 줄 (합친 줄). 주면 건물, 필지 카드의 '입점 상호'를 합친 줄로 보여 준다."""
        self.view = view
        self.places = places
        self.public = public
        self.by_path = {r["p"]: r for r in places["places"]}
        self.groups = {g["g"]: g for g in places["groups"]}
        self.station_pt = tuple(places["station"]["pt"]) if places.get("station") and places["station"].get("pt") else None
        self.inbound = defaultdict(list)   # 대상 -> [(술어, 주어)]
        for s, plist in view.triples.items():
            if not s.startswith(BASE + "/") or s.endswith("/geometry") or "/change/" in s or not view.shown(s):
                continue
            for p, o in plist:
                if o.value.startswith(BASE + "/") and not p.value.startswith(GEO):
                    self.inbound[o.value].append((p.value, s))
        self.lines = defaultdict(list)     # 건물이나 필지 경로 -> 상호 줄
        for r in business_lines or []:
            key = r.get("b") or r.get("lot")
            if key:
                self.lines[key].append(r)
        self._obs = None

    # 공통 ---------------------------------------------------------------------------------------------------
    def uris(self) -> list[str]:
        v = self.view
        return sorted(s for s in v.triples if s.startswith(BASE + "/") and not s.endswith("/geometry") and "/change/" not in s
                      and kind_of(s) in CARD_KINDS and v.shown(s))

    def name(self, uri: str) -> str:
        return display_name(self.view, uri)

    def link(self, rel: str, uri: str, spread: dict | None = None) -> dict | None:
        if not self.view.shown(uri):
            return None
        out = {"rel": rel, "kind": kind_of(uri), "path": path_of(uri), "name": self.name(uri)}
        if spread:
            out["spread"] = spread
        return out

    def source_names(self, uri: str) -> list[str]:
        v = self.view
        return [v.labels.get(o.value) or o.value.rsplit("#", 1)[-1] for p, o in v.triples.get(uri, [])
                if p.value == DCT_SOURCE and v.shown(o.value)]

    def facts_count(self, uri: str) -> int:
        return sum(1 for p, o in self.view.triples.get(uri, []) if self.view.shown(o.value))

    def observations(self) -> dict:
        """센서 -> 관측 항목 -> [(시각, 값)]."""
        if self._obs is None:
            out = defaultdict(lambda: defaultdict(list))
            for s, plist in self.view.triples.items():
                if not any(p.value == RDF_TYPE and o.value == SOSA + "Observation" for p, o in plist) or not self.view.shown(s):
                    continue
                d = {p.value: o.value for p, o in plist}
                out[d[SOSA + "madeBySensor"]][d[SOSA + "observedProperty"]].append((d[SOSA + "resultTime"], float(d[SOSA + "hasSimpleResult"])))
            self._obs = out
        return self._obs

    # 카드 ---------------------------------------------------------------------------------------------------
    def card(self, uri: str) -> dict:
        v = self.view
        kind = kind_of(uri)
        path = path_of(uri)
        status = v.value(uri, "status")
        c = {"path": path, "uri": uri, "kind": kind, "kindLabel": KIND_LABEL.get(kind, kind), "name": self.name(uri)}
        if status:
            c["status"] = v.labels.get(status) or status.rsplit("#", 1)[-1]
        w = wkt_of(v, uri)
        row = self.by_path.get(path)
        if row and "pt" in row:
            c["pt"] = row["pt"]
        elif w:
            c["pt"] = list(point_of(shapely.from_wkt(w)))
        c["public"] = (uri in self.public) if self.public is not None else True
        no_prop, no_label = OFFICIAL.get(kind, (None, None))
        no = v.value(uri, no_prop) if no_prop else None
        if no:
            c["officialNo"] = {"label": no_label, "value": no}
        elif kind == "station":
            c["officialNo"] = {"label": "역사 건물관리번호", "value": uri.rsplit("/", 1)[-1]}
        elif kind == "entrance":
            c["officialNo"] = {"label": "출입구 번호", "value": uri.rsplit("/", 1)[-1]}
        addr = v.address_of(uri)
        if addr:
            c["addr"] = addr
        jibun_of = {"building": uri, "building-gis": uri, "parcel": uri, "station": v.value(uri, "housedIn"),
                    "business": v.value(uri, "inBuilding") or v.value(uri, "onParcel")}.get(kind)
        jibun = v.jibun(jibun_of) if jibun_of and v.shown(jibun_of) else None
        if jibun:
            c["jibun"] = jibun
        if row and "d" in row:
            c["d"] = row["d"]
        elif "pt" in c and self.station_pt and kind not in ("station",) and w:
            c["d"] = int(round(straight_m(c["pt"], self.station_pt)))
        facts, links, missing, lead = [], [], [], []
        getattr(self, "_" + kind.replace("-", "_"), self._other)(uri, c, facts, links, lead)
        for prop, label in EXPECT.get(kind, []):
            if v.value(uri, prop) in (None, "", "0"):
                missing.append(label)
        if kind == "sensor" and "sensor" not in c:
            missing.append("관측값")
            lead.append("받은 기간에 이 센서의 관측값이 없습니다.")
        absent = [x for x in ABSENT.get(kind, []) if not (x == "경계 모양" and w)]
        c["facts"] = facts
        if lead:
            c["lead"] = " ".join(lead)
        c["links"] = [x for x in links if x]
        external = [{"rel": "위키데이터", "url": o.value, "name": o.value.rsplit("/", 1)[-1]}
                    for p, o in v.triples.get(uri, []) if p.value == OWL_SAME and o.value.startswith("http://www.wikidata.org/")]
        if external:
            c["external"] = external
        c["missing"] = missing + absent
        gnode = uri + "/geometry"
        shape_src = self.source_names(gnode) if w and v.shown(gnode) else []
        c["sources"] = {"object": self.source_names(uri), "shape": (shape_src or self.source_names(uri)) if w else []}
        c["factCount"] = self.facts_count(uri)
        return c

    def _num_fact(self, uri, prop, label, facts, fmt=None):
        val = self.view.value(uri, prop)
        if val in (None, ""):
            return
        facts.append({"k": label, "v": fmt(val) if fmt else val})

    def _building(self, uri, c, facts, links, lead):
        v = self.view
        use = v.value(uri, "useName")
        if use:
            plain = use_plain(use)
            facts.append({"k": "용도", "v": plain} | ({"raw": use} if plain != use else {}))
        fa, fb = v.value(uri, "floorsAbove"), v.value(uri, "floorsBelow")
        if fa not in (None, "", "0"):
            facts.append({"k": "지상 층수", "v": f"{int(float(fa))}층"})
        if fb not in (None, "", "0"):
            facts.append({"k": "지하 층수", "v": f"{int(float(fb))}층"})
        self._num_fact(uri, "structureName", "구조", facts)
        ad = v.value(uri, "approvalDate")
        if ad:
            facts.append({"k": "사용승인일", "v": ad, "note": "2018년까지의 자료입니다"})
        if c.get("status") == "주소 미연결":
            facts.append({"k": "상태", "v": "도로명주소 없는 건물", "raw": "주소 미연결"})
            lead.append("도로명주소가 없는 건물입니다.")
        if not v.labels.get(uri) and not use and not v.value(uri, "structureName") and not ad and fa in (None, "", "0"):
            lead.insert(0, "위치와 주소만 있습니다." if v.addrs.get(uri) else "위치와 지번만 있습니다.")
        row = self.by_path.get(c["path"], {})
        for e in sorted(v.values(uri, "hasEntrance")):
            links.append(self.link("입구", e))
        for lot in sorted(v.values(uri, "locatedOn")):
            links.append(self.link("필지", lot, {"by": "pnu", "value": lot.rsplit("/", 1)[-1]}))
        if "g" in row:
            g = self.groups[row["g"]]
            rep = self.places["places"][g["m"][0]]["p"]
            links.append({"rel": "같은 단지", "kind": "group", "path": rep, "unit": "group", "name": f"{g['n']} ({len(g['m'])}동)",
                          "spread": {"by": "g", "value": g["g"]}})
        d = v.value(uri, "within")
        if d:
            links.append(self.link("행정동", d, {"by": "dong", "value": v.labels.get(d)}))
        ld = v.value(uri, "inLegalDong")
        if ld:
            links.append(self.link("법정동", ld, {"by": "ld", "value": ld.rsplit("/", 1)[-1]}))
        z = v.value(uri, "inBasicZone")
        if z:
            links.append(self.link("우편구역", z, {"by": "zip", "value": v.value(z, "basicZoneNo") or z.rsplit("/", 1)[-1]}))
        r = v.value(uri, "addressRoad")
        if r:
            links.append(self.link("길", r, {"by": "road", "value": r.rsplit("/", 1)[-1]}))
        for p, s in sorted(self.inbound.get(uri, [])):
            if p == JGK + "housedIn" and kind_of(s) == "station":
                links.append(self.link("이 건물 안의 역", s))
            elif p == JGK + "addressBuilding":
                links.append(self.link("가까운 센서", s))
            elif p == JGK + "housedIn" and kind_of(s) == "public-facility":
                links.append(self.link("들어 있는 공공시설", s))
        self._tenants(uri, c)

    _building_gis = _building

    def _tenants(self, uri, c):
        """입점 상호: 층 순서, 업종, 이어진 방법. 같은 가게 두 줄은 합친 줄로(rows) 보여 준다."""
        lines = self.lines.get(path_of(uri))
        if lines is None:  # 합친 줄을 받지 않았으면 원천 행 그대로
            lines = []
            for p, s in self.inbound.get(uri, []):
                if p in (JGK + "inBuilding", JGK + "onParcel") and kind_of(s) == "business":
                    v = self.view
                    lines.append({"p": path_of(s), "n": self.name(s) + (f" {v.value(s, 'branchName')}" if v.value(s, "branchName") else ""),
                                  "f": v.value(s, "floorInfo") or "", "c": v.value(s, "categorySmall") or "", "by": v.value(s, "linkedBy") or ""})
        if not lines:
            return
        out = []
        for r in sorted(lines, key=lambda r: (floor_key(r.get("f")), r["n"])):
            t = {"name": r["n"]}
            if r.get("p"):
                t["path"] = r["p"]
            if r.get("f"):
                t["floor"] = floor_text(r["f"])
            if r.get("c"):
                t["cat"] = r["c"]
            if r.get("by"):
                t["by"] = r["by"]
            if r.get("r"):
                t["rows"] = r["r"]
            if r.get("src"):
                t["src"] = r["src"]["note"]
            out.append(t)
        c["tenants"] = out
        c["tenantsNote"] = "소상공인 상가정보 2026년 9월 기준입니다. 같은 가게의 두 행은 한 줄로 합치고 행 수를 적었습니다. 영업시간, 전화번호, 리뷰는 이 자료에 없습니다."

    def _station(self, uri, c, facts, links, lead):
        v = self.view
        self._num_fact(uri, "lineName", "노선", facts)
        b = v.value(uri, "housedIn")
        if b and v.shown(b):
            fb = v.value(b, "floorsBelow")
            if fb not in (None, "", "0"):
                facts.append({"k": "지하 층수", "v": f"{int(float(fb))}층", "note": "역사 건물의 값입니다"})
            links.append(self.link("역사 건물", b))
        d = v.value(uri, "within")
        if d:
            links.append(self.link("행정동", d, {"by": "dong", "value": v.labels.get(d)}))

    def _parcel(self, uri, c, facts, links, lead):
        v = self.view
        self._num_fact(uri, "siteArea", "대지면적", facts, lambda x: f"{num(x):,}㎡")
        self._num_fact(uri, "totalFloorArea", "연면적", facts, lambda x: f"{num(x):,}㎡")
        self._num_fact(uri, "households", "세대수", facts, lambda x: f"{num(x):,}세대")
        self._num_fact(uri, "mainBuildingCount", "주건축물 수", facts, lambda x: f"{num(x):,}동")
        self._num_fact(uri, "complexMainUse", "단지 주용도", facts)
        pnu = uri.rsplit("/", 1)[-1]
        on = [p for p, s in self.inbound.get(uri, []) if p == JGK + "locatedOn"]
        if on:
            links.append({"rel": "위에 선 건물", "kind": "spread", "name": f"건물 {len(on):,}동", "spread": {"by": "pnu", "value": pnu}})
        else:
            lead.append("이 필지 위에는 건물 자료가 없습니다.")
        ld = v.value(uri, "inLegalDong")
        if ld:
            links.append(self.link("법정동", ld, {"by": "ld", "value": ld.rsplit("/", 1)[-1]}))
        self._tenants(uri, c)

    def _road(self, uri, c, facts, links, lead):
        r = next((x for x in self.places["roads"] if x["p"] == c["path"]), None)
        if r:
            facts.append({"k": "이 길을 주소로 쓰는 건물", "v": f"{r['b']:,}동"})
            if r["side"]:
                facts.append({"k": "곁길까지 합친 건물", "v": f"{r['b'] + r['side']:,}동", "note": f"{c['name']}+번호+길 꼴의 곁길 {r['side']:,}동을 더한 값입니다"})
            if r["b"]:
                links.append({"rel": "이 길을 주소로 쓰는 건물", "kind": "spread", "name": f"건물 {r['b']:,}동",
                              "spread": {"by": "road", "value": uri.rsplit("/", 1)[-1]}})

    def _area(self, uri, c, facts, links, lead, by, value, note=None):
        n = sum(1 for r in self.places["places"] if r.get(by) == value)
        facts.append({"k": "속한 건물", "v": f"{n:,}동"} | ({"note": note} if note else {}))
        if n:
            links.append({"rel": "속한 건물", "kind": "spread", "name": f"건물 {n:,}동", "spread": {"by": by, "value": value}})

    def _admin_dong(self, uri, c, facts, links, lead):
        self._area(uri, c, facts, links, lead, "dong", self.view.labels.get(uri), "도로명주소가 있는 건물만 셉니다")

    def _legal_dong(self, uri, c, facts, links, lead):
        self._area(uri, c, facts, links, lead, "ld", uri.rsplit("/", 1)[-1])

    def _basic_zone(self, uri, c, facts, links, lead):
        self._area(uri, c, facts, links, lead, "zip", self.view.value(uri, "basicZoneNo") or uri.rsplit("/", 1)[-1])

    def _sensor(self, uri, c, facts, links, lead):
        v = self.view
        self._num_fact(uri, "sensorModel", "모델", facts)
        self._num_fact(uri, "installAddress", "설치 주소", facts, short_address)
        self._num_fact(uri, "addressBuildingDistance", "설치 주소 건물까지", facts, lambda x: f"{num(x)}m")
        b = v.value(uri, "addressBuilding")
        if b:
            links.append(self.link("설치 주소의 건물", b))
        obs = self.observations().get(uri, {})
        items, times = [], []
        for prop, pts in sorted(obs.items(), key=lambda t: self.name(t[0])):
            pts.sort()
            vals = [x for _, x in pts]
            times += [t for t, _ in pts]
            unit_uri = v.value(prop, "unit") or ""
            items.append({"name": v.labels.get(prop) or prop.rsplit("#", 1)[-1], "unit": UNIT_KO.get(unit_uri.rsplit("/", 1)[-1], ""),
                          "n": len(vals), "avg": round(mean(vals), 1), "min": round(min(vals), 1), "max": round(max(vals), 1),
                          "series": [[t[:16], round(x, 1)] for t, x in pts]})
        if items:
            c["sensor"] = {"period": f"{min(times)[:10]} ~ {max(times)[:10]}", "note": "1시간 평균값입니다. 실시간 값이 아닙니다.", "items": items}

    def _entrance(self, uri, c, facts, links, lead):
        v = self.view
        self._num_fact(uri, "entranceType", "유형", facts)
        for p, s in sorted(self.inbound.get(uri, [])):
            if p == JGK + "hasEntrance":
                links.append(self.link("소속 건물", s))

    def _business(self, uri, c, facts, links, lead):
        v = self.view
        self._num_fact(uri, "branchName", "지점명", facts)
        cats = [v.value(uri, x) for x in ("categoryLarge", "categoryMiddle", "categorySmall")]
        if any(cats):
            facts.append({"k": "업종", "v": cats[2] or cats[1] or cats[0], "raw": " > ".join(x for x in cats if x)})
        fl = v.value(uri, "floorInfo")
        if fl:
            facts.append({"k": "층", "v": floor_text(fl)})
        self._num_fact(uri, "linkedBy", "건물과 이은 방법", facts)
        lead.append(BUSINESS_NOTE)
        b, lot = v.value(uri, "inBuilding"), v.value(uri, "onParcel")
        if b:
            links.append(self.link("들어 있는 건물", b))
        if lot:
            links.append(self.link("필지", lot, {"by": "pnu", "value": lot.rsplit("/", 1)[-1]}))

    def _other(self, uri, c, facts, links, lead):
        """정해 두지 않은 종류 (전체 뷰의 공공시설, 도로 구간 등): 글자 값은 정보로, 우리 장소를 가리키는 값은 이어진 것으로."""
        v = self.view
        skip = {RDF_TYPE, DCT_SOURCE, JGK + "status", JGK + "roadAddress", "http://www.w3.org/2000/01/rdf-schema#label"}
        for p, o in v.triples.get(uri, []):
            if p.value in skip or p.value.startswith(GEO):
                continue
            if o.value.startswith(BASE + "/"):
                links.append(self.link(v.labels.get(p.value) or p.value.rsplit("#", 1)[-1], o.value))
            elif not o.value.startswith("http"):
                facts.append({"k": v.labels.get(p.value) or p.value.rsplit("#", 1)[-1], "v": o.value})


def build_card(view: GraphView, uri: str, places: dict | None = None, **kw) -> dict:
    """카드 하나 (명세 5.3절의 build_card). places를 주지 않으면 같은 뷰로 만든다. 여러 장을 만들 때는 CardMaker를 한 번 만들어 쓴다."""
    if places is None:
        from src.graph.places import build_places
        places = build_places(view, "")
    return CardMaker(view, places, **kw).card(uri)
