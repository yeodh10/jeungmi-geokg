"""URI를 실제로 열리는 주소로: 객체마다 정적 HTML 페이지와 Turtle 파일을 만든다.

실행: python -m src.graph.publish
출력: data/processed/site/ (GitHub Pages에 그대로 올리면 https://yeodh10.github.io/jeungmi-geokg/ 아래 URI가 열린다)
  index.html                   첫 화면 (소개, 객체 종류, 전체 찾기)
  id/{종류}/index.html          종류별 목록 (이름, 번호로 거르기)
  id/{종류}/{번호}/index.html   사람이 보는 객체 페이지 (주요 정보, 연결, 출처, 작은 지도)
  id/{종류}/{번호}/index.ttl    그 URI가 주어인 트리플 (N-Triples 줄로 쓴 Turtle)
  map/index.html               전체 지도 (assets/map/*.geojson을 그린다. 서버 없이 열리는 지도 화면)
  def/index.html, def/ontology.ttl  어휘
  assets/  화면 모양(web/style.css를 그대로 씀)과 스크립트, search.json(전체 찾기용 목록)

이용 조건을 아직 확인하지 못한 출처(config/area.yaml의 publish.hold_sources)에서 온 객체는 페이지를 만들지 않고,
다른 페이지에서도 그 객체로 가는 연결을 뺀다.
"""
from __future__ import annotations

import html
import json
import re
import shutil
from collections import defaultdict
from pathlib import Path

import shapely
import yaml
from shapely.geometry import mapping

from src.graph.store import ALIGNMENT, ONTOLOGY, load_store
from src.model.uri import BASE

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "processed" / "site"
WEB = ROOT / "web"
SITE = BASE.rsplit("/id", 1)[0]  # https://yeodh10.github.io/jeungmi-geokg
SITE_PATH = "/" + SITE.split("://", 1)[1].split("/", 1)[1]  # /jeungmi-geokg (페이지 안 링크는 이 경로로 시작한다)
REPO = "https://github.com/yeodh10/jeungmi-geokg"
JGK = SITE + "/def#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
RDFS_COMMENT = "http://www.w3.org/2000/01/rdf-schema#comment"
DCT_SOURCE = "http://purl.org/dc/terms/source"
GEO = "http://www.opengis.net/ont/geosparql#"
SOSA = "http://www.w3.org/ns/sosa/"
UNIT_KO = {"DEG_C": "℃", "PERCENT": "%", "DeciB": "dB", "LUX": "lux", "UNITLESS": ""}
EXT_LABEL = {
    RDF_TYPE: "종류", RDFS_LABEL: "이름", DCT_SOURCE: "출처", RDFS_COMMENT: "설명",
    GEO + "hasGeometry": "모양", GEO + "hasDefaultGeometry": "기본 모양", GEO + "asWKT": "WKT",
    "http://www.w3.org/2002/07/owl#sameAs": "같은 객체", "http://www.w3.org/ns/prov#wasInfluencedBy": "반영된 변경 제안",
    GEO + "Feature": "공간 객체(Feature)", GEO + "Geometry": "모양(Geometry)", "http://purl.org/dc/terms/issued": "고시일",
    "http://purl.org/dc/terms/description": "설명", "http://purl.org/dc/terms/created": "만든 시각",
}
# 종류(URI 경로 이름) -> 화면 이름, 공식 번호 설명
KINDS = {
    "station": ("역", "역사 건물의 건물관리번호(25자리)를 그대로 쓴다."),
    "building": ("건물", "도로명주소 건물관리번호(25자리)를 그대로 쓴다."),
    "building-gis": ("건물 (주소 미연결)", "도로명주소가 없는 건물이라 GIS건물통합식별번호(28자리)를 그대로 쓴다. 나중에 주소가 이어져도 이 URI는 남는다."),
    "entrance": ("건물 출입구", "주출입구는 '건물관리번호-main', 보조출입구는 '시군구코드-출입구일련번호'다."),
    "parcel": ("필지", "PNU(필지고유번호, 19자리)를 그대로 쓴다."),
    "road": ("도로", "도로명코드(시군구코드 5자리 + 도로명번호 7자리)를 그대로 쓴다."),
    "admin-dong": ("행정동", "행정동코드(10자리)를 그대로 쓴다."),
    "legal-dong": ("법정동", "법정동코드(10자리)를 그대로 쓴다."),
    "basic-zone": ("기초구역", "국가기초구역번호(5자리, 우편번호와 같다)를 그대로 쓴다."),
    "notice": ("고시공고", "구청 고시 번호(시군구코드-연도-번호)를 그대로 쓴다."),
    "sensor": ("센서", "서울시 도시데이터 센서(S-DoT)의 모델 시리얼을 그대로 쓴다."),
    "observation": ("관측", "센서 시리얼, 측정 시각(연월일시분 12자리), 측정 항목을 이었다. 한 센서가 한 시각에 한 항목은 한 번만 잰다."),
    "proposal": ("변경 제안", "변경 제안 번호다. 승인된 제안만 그래프에 들어온다."),
}
# 주요 정보에서 앞에 둘 속성 순서 (어휘 이름 끝)
KEY_ORDER = ["roadAddress", "useName", "structureName", "floorsAbove", "floorsBelow", "approvalDate", "lineName",
             "facilityType", "agencyName", "agencyCode", "entranceType", "buildingMgtNo", "gisBuildingId", "pnu",
             "roadCode", "legalDongCode", "basicZoneNo", "admDongCode", "siteArea", "totalFloorArea", "households",
             "mainBuildingCount", "complexMainUse", "retiredOn", "noticeNumber", "zoneKind",
             "sensorSerial", "sensorModel", "installAddress", "installArea", "addressBuildingDistance", "hasSimpleResult", "resultTime"]
PRINCIPLES = [
    ("객체 원자화", "건물, 출입구, 필지, 도로, 행정구역, 공공시설을 각각 하나의 객체로 쪼개고 모양(Geometry)은 따로 둡니다."),
    ("영구 식별자", "새 번호를 만들지 않고 건물관리번호, PNU, 도로명코드 같은 공식 번호를 그대로 URI에 씁니다. 객체가 없어져도 URI는 남습니다."),
    ("자동 갱신", "새 자료가 오면 그래프를 덮어쓰지 않고 객체마다 견줘 신규, 변경, 폐지 제안을 만듭니다."),
    ("AI 제안 후 사람 승인", "고시공고는 LLM이 바뀌는 내용만 뽑고, 사람이 승인한 변경만 그래프에 들어가며 누가 언제 승인했는지 남습니다."),
]
ICON = {
    "globe": '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18"/></svg>',
    "code": '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m8 7-5 5 5 5M16 7l5 5-5 5"/></svg>',
    "copy": '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>',
    "search": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
    "file": '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>',
}
# 전체 지도에 그릴 종류 -> 레이어, 레이어 순서와 처음에 켤지
MAP_LAYER = {"building": "building", "building-gis": "building", "entrance": "entrance", "station": "station", "sensor": "sensor"}
MAP_LAYERS = [("building", "건물", True), ("entrance", "건물 출입구", False), ("sensor", "센서 (S-DoT)", True), ("station", "역", True)]
e = html.escape


def area_circle() -> dict:
    """config/area.yaml의 대상 범위 원. 지도 화면 API(src.review.app.area_geojson)와 같은 방법으로 EPSG:5186에서 그린다."""
    import geopandas as gpd
    from shapely.geometry import Point
    with open(ROOT / "config" / "area.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    lon, lat = cfg["center"]["epsg4326_lonlat"]
    metric = (cfg.get("crs") or {}).get("metric", "EPSG:5186")
    circle = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(metric) \
        .buffer(cfg["radius_m"], quad_segs=64).to_crs("EPSG:4326").iloc[0]
    g = shapely.set_precision(circle, 1e-6)
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": mapping(g), "properties": {}}],
            "center": [lon, lat], "bbox": [round(v, 6) for v in circle.bounds]}


def term_nt(t) -> str:
    return str(t)  # pyoxigraph 항은 N-Triples 표기로 바뀐다


def hold_sources() -> set[str]:
    """공개를 보류한 출처의 URI. 지도 화면 API(src.review.app)도 같은 목록으로 공개 여부를 판단한다."""
    with open(ROOT / "config" / "area.yaml", encoding="utf-8") as f:
        return {JGK + s for s in (yaml.safe_load(f).get("publish") or {}).get("hold_sources") or []}


def kind_of(uri: str) -> str:
    return uri[len(BASE) + 1:].split("/")[0]


def page(title: str, body: str, *, nav: str | None = None, head: str = "", desc: str = "", wide: bool = False) -> str:
    """모든 공개 페이지가 같이 쓰는 틀. 링크는 SITE_PATH로 시작해 GitHub Pages와 같은 경로에서 열린다."""
    navs = [("", "처음"), ("/map/", "지도"), ("/id/building/", "건물"), ("/id/parcel/", "필지"), ("/id/road/", "도로"), ("/def/", "어휘")]
    links = "".join(f'<a href="{SITE_PATH}{p}"{" class=on" if nav == p else ""}>{n}</a>' for p, n in navs)
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} | 증미역 공간 지식 그래프</title>{f'<meta name="description" content="{e(desc)}">' if desc else ""}
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css">
<link rel="stylesheet" href="{SITE_PATH}/assets/style.css"><link rel="stylesheet" href="{SITE_PATH}/assets/site.css">{head}
<script src="{SITE_PATH}/assets/common.js"></script><script src="{SITE_PATH}/assets/site.js" defer></script></head><body>
<header class="topbar"><a class="brand" href="{SITE_PATH}/">증미역 공간 지식 그래프<small>URI 사이트</small></a><nav>{links}</nav>
<span class="spacer"></span><a class="tb-link" href="{REPO}" target="_blank" rel="noopener">{ICON["code"]}<span>소스 코드</span></a></header>
<main class="page{" wide" if wide else ""}">{body}</main>
<footer class="foot"><div>증미역 반경 1km 공간 지식 그래프, GeoSPARQL 1.1. 객체마다 공식 번호로 바뀌지 않는 URI를 붙였습니다.</div>
<div>배경지도 © OpenStreetMap contributors. 이용 조건을 확인 중인 출처의 객체는 이 사이트에 넣지 않았습니다.</div></footer></body></html>"""


def sparkline(points: list[tuple[str, float]], w: int = 180, h: int = 34) -> str:
    """시각 순서의 값을 작은 꺾은선 SVG로. 축과 눈금은 없고, 최솟값과 최댓값이 위아래 끝이 된다."""
    vals = [v for _, v in points]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    step = w / max(len(vals) - 1, 1)
    pts = " ".join(f"{i * step:.1f},{h - 2 - (v - lo) / span * (h - 4):.1f}" for i, v in enumerate(vals))
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="시간별 값 변화">'
            f'<polyline points="{pts}" fill="none" stroke="var(--sensor)" stroke-width="1.5"/></svg>')


def observation_summary(triples: dict) -> dict:
    """센서 -> 관측 항목 -> [(시각, 값)]. 센서 페이지에 항목별 요약을 붙일 때 쓴다."""
    out = defaultdict(lambda: defaultdict(list))
    for s, plist in triples.items():
        if not any(p.value == RDF_TYPE and o.value == SOSA + "Observation" for p, o in plist):
            continue
        d = {p.value: o.value for p, o in plist}
        out[d[SOSA + "madeBySensor"]][d[SOSA + "observedProperty"]].append((d[SOSA + "resultTime"], float(d[SOSA + "hasSimpleResult"])))
    return out


def align_section() -> str:
    """어휘 페이지의 '바깥 표준 어휘와 잇기' 표. alignment.ttl을 그대로 읽어 만든다."""
    from rdflib import Graph
    g = Graph().parse(ALIGNMENT)
    kinds = {"http://www.w3.org/2000/01/rdf-schema#subClassOf": "하위 종류", "http://www.w3.org/2000/01/rdf-schema#subPropertyOf": "하위 속성",
             "http://www.w3.org/2004/02/skos/core#closeMatch": "가까운 개념", "http://www.w3.org/2002/07/owl#sameAs": "같은 실물"}
    short = lambda u: (u.replace(JGK, "jgk:").replace("https://schema.org/", "schema:").replace("http://www.wikidata.org/entity/", "wd:")
                       .replace("http://inspire.ec.europa.eu/featureconcept/", "inspire:").replace("https://w3id.org/bot#", "bot:")
                       .replace("http://www.w3.org/2004/02/skos/core#", "skos:").replace(BASE + "/", "id:"))
    rows = sorted((str(s), str(p), str(o)) for s, p, o in g if str(p) in kinds)
    trs = "".join(f'<tr><td class="mono">{e(short(s))}</td><td>{kinds[p]}</td><td class="mono"><a href="{e(o)}" rel="noopener">{e(short(o))}</a></td></tr>' for s, p, o in rows)
    return (f'<section class="card sec"><h2>바깥 표준 어휘와 잇기 <span class="muted">{len(rows)}</span></h2>'
            '<p class="small muted">jgk를 모르는 시스템도 schema.org, INSPIRE 지형지물 개념, 위키데이터 이름으로 이 그래프를 읽을 수 있게 이었습니다. '
            'schema.org는 하위 종류, 하위 속성으로 이어 RDFS 추론만으로 바깥 이름으로 물을 수 있고, 정의가 꼭 같지 않은 것은 가까운 개념으로만 이었습니다.</p>'
            f'<div class="tablewrap"><table class="kv vocab">{trs}</table></div></section>')


def main() -> None:
    hold = hold_sources()
    store = load_store()

    triples = defaultdict(list)
    for s, p, o, _ in store.quads_for_pattern(None, None, None, None):
        triples[s.value if hasattr(s, "value") else str(s)].append((p, o))
    labels, comments, addrs = {}, {}, {}
    for s, plist in triples.items():
        for p, o in plist:
            if p.value == RDFS_LABEL and (getattr(o, "language", None) in (None, "ko")):
                labels.setdefault(s, o.value)
            if p.value == RDFS_COMMENT:
                comments.setdefault(s, o.value)
            if p.value == JGK + "roadAddress":
                addrs.setdefault(s, o.value)
    # 이름이 없는 건물은 도로명주소로, 출입구는 그 출입구를 가진 건물의 주소로 부른다
    for s, plist in triples.items():
        for p, o in plist:
            if p.value == JGK + "hasEntrance" and s in addrs:
                addrs.setdefault(o.value, addrs[s] + " 출입구")
    obs_summary = observation_summary(triples)
    held = {s for s, plist in triples.items() if any(p.value == DCT_SOURCE and o.value in hold for p, o in plist)}
    held |= {f"{s}/geometry" for s in held}
    public = lambda s: s.startswith(BASE + "/") and s not in held and not s.endswith("/geometry")
    pname = lambda p: labels.get(p) or EXT_LABEL.get(p) or p.rsplit("#", 1)[-1]

    def name_of(u: str) -> str:
        label, addr = labels.get(u), addrs.get(u)
        # 원천의 건물 이름이 '1동', '.', '1'처럼 그것만으로 알아볼 수 없으면 주소를 앞에 붙인다
        if label and addr and (len(label.strip(" .-")) < 2 or re.fullmatch(r"[\d\s.\-]*동?", label)):
            return f"{addr} {label.strip(' .')}".strip()
        if label:
            return label
        if addr:
            return addr
        if u.startswith(BASE):
            k = kind_of(u)
            return f"{KINDS.get(k, (k,))[0]} {u.rsplit('/', 1)[-1]}"
        if u.startswith("http://www.wikidata.org/entity/"):
            return "위키데이터 " + u.rsplit("/", 1)[-1]
        return EXT_LABEL.get(u) or u.rsplit("#", 1)[-1]

    def link(u: str) -> str:
        if u in held:
            return '<span class="muted">(공개 보류 중인 객체)</span>'
        href = u.replace(SITE, SITE_PATH) + ("/" if u.startswith(BASE) else "") if u.startswith(SITE) else u
        return f'<a href="{e(href)}">{e(name_of(u))}</a>'

    def value(o) -> str:
        v = o.value
        if hasattr(o, "datatype") and o.datatype is not None or getattr(o, "language", None):
            if v in ("true", "false") and getattr(o, "datatype", None) is not None and o.datatype.value.endswith("boolean"):
                return "예" if v == "true" else "아니요"
            return e(v)
        return link(v) if v.startswith("http") else e(v)

    if OUT.exists():
        shutil.rmtree(OUT)
    assets = OUT / "assets"
    assets.mkdir(parents=True)
    shutil.copy(WEB / "style.css", assets / "style.css")
    shutil.copy(WEB / "common.js", assets / "common.js")
    shutil.copy(WEB / "site.css", assets / "site.css")
    shutil.copy(WEB / "site.js", assets / "site.js")

    inbound = defaultdict(list)
    for s, plist in triples.items():
        for p, o in plist:
            if o.value.startswith(BASE) and s.startswith(BASE) and not p.value.startswith(GEO) and "/change/" not in s:
                inbound[o.value].append((p.value, s))

    by_kind = defaultdict(list)  # 종류 -> [(uri, 이름, 상태)]
    map_feats = defaultdict(list)  # 지도 레이어 -> GeoJSON Feature (공개 객체만)
    n = 0
    for s, plist in triples.items():
        # 모양 노드(…/geometry)도 이미 공개한 URI라 페이지를 계속 만든다. 목록과 찾기에는 넣지 않는다
        is_geom = s.endswith("/geometry")
        if not s.startswith(BASE + "/") or s in held:
            continue
        d = OUT / s[len(SITE) + 1:]
        d.mkdir(parents=True, exist_ok=True)
        keep = [(p, o) for p, o in plist if o.value not in held]
        (d / "index.ttl").write_text("".join(f"<{s}> {term_nt(p)} {term_nt(o)} .\n" for p, o in keep), encoding="utf-8")
        kind = kind_of(s)
        kind_ko, rule = KINDS.get(kind, (kind, ""))
        by_p = defaultdict(list)
        for p, o in keep:
            by_p[p.value].append(o)
        types = [name_of(o.value) for o in by_p.get(RDF_TYPE, []) if not o.value.startswith(GEO)] or (["모양(Geometry)"] if is_geom else [])
        status = [name_of(o.value) for o in by_p.get(JGK + "status", [])]
        parent = s.removesuffix("/geometry")
        title = f"{name_of(parent)}의 모양" if is_geom else name_of(s)
        if is_geom:
            rule = f"{name_of(parent)}의 모양(Geometry)입니다. 좌표는 경도, 위도 순서의 WKT(CRS84)로 적었습니다."
        elif "/change/" not in s:  # 제안이 바꾼 트리플 기록(…/change/1)은 페이지만 두고 목록에는 넣지 않는다
            by_kind[kind].append((s, name_of(s), status[0] if status else ""))

        # 주요 정보(값), 연결(우리 객체), 출처를 나눈다
        skip = {RDF_TYPE, RDFS_LABEL, JGK + "status", DCT_SOURCE, GEO + "hasGeometry", GEO + "hasDefaultGeometry"}
        lits = [p for p in by_p if p not in skip and not any(o.value.startswith("http") and not getattr(o, "datatype", None) for o in by_p[p])]
        lits.sort(key=lambda p: (KEY_ORDER.index(p.rsplit("#", 1)[-1]) if p.rsplit("#", 1)[-1] in KEY_ORDER else 99, pname(p)))
        rels = [p for p in by_p if p not in skip and p not in lits]
        info = "".join(f'<tr><td class="k">{e(pname(p))}</td><td>{", ".join(value(o) for o in by_p[p])}</td></tr>' for p in lits)
        out_links = "".join(f'<tr><td class="k">{e(pname(p))}</td><td class="links">{" ".join(link(o.value) for o in by_p[p])}</td></tr>' for p in rels)
        back = defaultdict(list)
        for p, src in inbound.get(s, []):
            if src not in held:
                back[p].append(src)
        back_rows = "".join(
            f'<tr><td class="k">{e(pname(p))}</td><td class="links">{" ".join(link(x) for x in sorted(srcs, key=name_of)[:40])}'
            + (f' <span class="muted">외 {len(srcs) - 40:,}개</span>' if len(srcs) > 40 else "") + "</td></tr>"
            for p, srcs in sorted(back.items(), key=lambda t: pname(t[0])))
        sources = "".join(f"<li>{e(name_of(o.value))}</li>" for o in by_p.get(DCT_SOURCE, []))
        # 센서: 관측 항목마다 개수, 평균, 최소, 최대, 시간별 꺾은선
        obs_rows = ""
        for prop, pts in sorted(obs_summary.get(s, {}).items(), key=lambda t: name_of(t[0])):
            pts.sort()
            vals = [v for _, v in pts]
            unit_uri = next((o.value for p, o in triples.get(prop, []) if p.value == JGK + "unit"), "")
            u = UNIT_KO.get(unit_uri.rsplit("/", 1)[-1], "")
            obs_rows += (f'<tr><td class="k">{link(prop) if prop.startswith(BASE) else e(name_of(prop))}</td><td class="num">{len(vals):,}</td>'
                         f'<td class="num">{sum(vals) / len(vals):.1f}{e(u)}</td><td class="num">{min(vals):g} ~ {max(vals):g}</td><td>{sparkline(pts)}</td></tr>')
        if obs_rows:
            period = min(t for v in obs_summary[s].values() for t, _ in v)[:10] + " ~ " + max(t for v in obs_summary[s].values() for t, _ in v)[:10]
            obs_rows = (f'<section class="card sec"><h2>관측 요약 <span class="muted">{e(period)}, 1시간 평균값</span></h2><div class="tablewrap"><table class="kv obs">'
                        f'<thead><tr><th>항목</th><th>개수</th><th>평균</th><th>범위</th><th>시간별</th></tr></thead>{obs_rows}</table></div></section>')

        # 모양: 지도에 그릴 GeoJSON (좌표는 소수 6자리)
        geom_json = ""
        gnode = s if is_geom else f"{s}/geometry"
        if gnode not in held:
            wkt = next((o.value for p, o in triples.get(gnode, []) if p.value == GEO + "asWKT"), None)
            if wkt:
                g = shapely.set_precision(shapely.from_wkt(wkt), 1e-6)
                geom_json = json.dumps(mapping(g), separators=(",", ":"))
                layer = MAP_LAYER.get(kind)
                if layer and not is_geom:
                    use = next((o.value for o in by_p.get(JGK + "useName", [])), "")
                    map_feats[layer].append({"type": "Feature", "geometry": mapping(g), "properties": {
                        "u": s[len(BASE) + 1:], "n": title, "s": status[0] if status else "", "use": use,
                        "addr": (lambda a: a if a != title else "")(addrs.get(s) or next((o.value for o in by_p.get(JGK + "installAddress", [])), ""))}})
        all_rows = "".join(f'<tr><td class="k">{e(pname(p.value))}</td><td class="mono">{e(o.value[:300])}</td></tr>'
                           for p, o in sorted(keep, key=lambda t: t[0].value))
        crumbs = (f'<nav class="crumbs"><a href="{SITE_PATH}/">처음</a><span>/</span>'
                  f'<a href="{SITE_PATH}/id/{kind}/">{e(kind_ko)}</a><span>/</span>'
                  + (f'{link(parent)}<span>/</span>' if is_geom else "") + f'<span>{e(title)}</span></nav>')
        body = f"""{crumbs}
<section class="obj-head card">
  <div class="chips">{"".join(f'<span class="chip type">{e(t)}</span>' for t in types)}{"".join(f'<span class="chip {"retire" if "폐지" in x else "src"}">{e(x)}</span>' for x in status)}</div>
  <h1>{e(title)}</h1>
  <div class="uri-row"><code class="uri">{e(s)}</code><button type="button" class="btn ghost mini copy" data-copy="{e(s)}">{ICON["copy"]}URI 복사</button>
    <a class="btn ghost mini" href="index.ttl">{ICON["file"]}Turtle</a></div>
</section>
<div class="obj-grid">
  <div class="obj-main">
    {f'<section class="card sec"><h2>주요 정보</h2><table class="kv">{info}</table></section>' if info else ""}
    {obs_rows}
    {f'<section class="card sec"><h2>연결</h2><table class="kv">{out_links}</table></section>' if out_links else ""}
    {f'<section class="card sec"><h2>이 객체를 가리키는 것</h2><table class="kv">{back_rows}</table></section>' if back_rows else ""}
    <details class="card sec"><summary>모든 트리플 ({len(keep)}개)</summary><div class="tablewrap"><table class="kv">{all_rows}</table></div></details>
  </div>
  <aside class="obj-side">
    {f'<section class="card mapcard"><div id="objmap" data-geom="{e(geom_json)}"></div>' + (f'<a class="mapcard-link small" href="{SITE_PATH}/map/?u={e(s[len(BASE) + 1:])}">전체 지도에서 보기</a>' if MAP_LAYER.get(kind) and not is_geom else "") + '</section>' if geom_json else ""}
    <section class="card sec"><h2>이 URI</h2><p class="small">{e(rule)}</p>{f'<p class="small">객체: {link(parent)}</p>' if is_geom else ""}
      <p class="small muted">이 주소는 객체의 이름입니다. 객체가 없어져도 지우지 않고 상태만 폐지로 바꿉니다.</p></section>
    {f'<section class="card sec"><h2>출처</h2><ul class="plain small">{sources}</ul></section>' if sources else ""}
  </aside>
</div>"""
        head = ('<link rel="alternate" type="text/turtle" href="index.ttl">'
                + ('<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css">'
                   '<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js" defer></script>' if geom_json else ""))
        nav_key = f"/id/{kind}/" if kind in ("building", "parcel", "road") else None
        (d / "index.html").write_text(page(title, body, nav=nav_key, head=head, desc=f"{kind_ko} {title}, {s}"), encoding="utf-8")
        n += 1

    # 종류별 목록
    for kind, rows in by_kind.items():
        kind_ko, rule = KINDS.get(kind, (kind, ""))
        rows.sort(key=lambda r: (addrs.get(r[0], "~"), r[1], r[0]))  # 주소순 (주소가 없으면 뒤로)
        has_addr = any(u in addrs for u, _, _ in rows)
        trs = "".join(f'<tr><td><a href="{e(u.replace(SITE, SITE_PATH))}/">{e(labels.get(u) or l or u.rsplit("/", 1)[-1])}</a></td>'
                      + (f'<td>{e(addrs.get(u, ""))}</td>' if has_addr else "")
                      + f'<td class="mono">{e(u.rsplit("/", 1)[-1])}</td><td>{e(st)}</td></tr>' for u, l, st in rows)
        body = f"""<nav class="crumbs"><a href="{SITE_PATH}/">처음</a><span>/</span><span>{e(kind_ko)}</span></nav>
<section class="list-head"><h1>{e(kind_ko)} <span class="muted">{len(rows):,}개</span></h1><p class="muted">{e(rule)}</p>
<div class="search-field"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
<input class="field" type="search" id="filter" placeholder="이름, 주소, 번호로 거르기" autocomplete="off" aria-label="목록 거르기"></div></section>
<section class="card"><div class="tablewrap"><table class="kv listing" id="listing"><thead><tr><th>이름</th>{"<th>도로명주소</th>" if has_addr else ""}<th>번호</th><th>상태</th></tr></thead><tbody>{trs}</tbody></table></div>
<div class="empty" id="nohit" hidden>맞는 객체가 없습니다.</div></section>"""
        d = OUT / "id" / kind
        d.mkdir(parents=True, exist_ok=True)
        nav_key = f"/id/{kind}/" if kind in ("building", "parcel", "road") else None
        (d / "index.html").write_text(page(f"{kind_ko} 목록", body, nav=nav_key), encoding="utf-8")

    # 전체 지도: 레이어마다 GeoJSON 하나, 대상 범위 원 하나
    (assets / "map").mkdir()
    for layer, feats in map_feats.items():
        (assets / "map" / f"{layer}.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (assets / "map" / "area.geojson").write_text(json.dumps(area_circle(), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    layer_rows = "".join(
        f'<label class="lp-row"><input type="checkbox" data-layer="{k}"{" checked" if on else ""}><i class="sw {k}"></i>{e(t)}'
        f'<span class="n">{len(map_feats.get(k, [])):,}</span></label>' for k, t, on in MAP_LAYERS if map_feats.get(k))
    body = f"""<div class="sitemap">
  <div id="sitemap" role="region" aria-label="증미역 반경 1km 지도"></div>
  <aside class="card sm-panel" id="sm-panel">
    <div class="sm-head"><h1>증미역 반경 1km 지도</h1>
      <p class="small muted">이 사이트에 공개한 객체를 모두 그렸습니다. 객체를 누르면 그 URI 페이지로 갈 수 있습니다.</p></div>
    <div class="search-field">{ICON["search"]}<input id="sm-q" class="field" type="search" autocomplete="off" placeholder="지도에서 찾기 (이름, 주소)" aria-label="지도에서 찾기"></div>
    <div id="sm-hits"></div>
    <h2 class="sm-sub">레이어</h2>{layer_rows}
    <h2 class="sm-sub">건물 상태</h2><div class="sm-legend" id="sm-legend"></div>
    <p class="small muted sm-note">민원행정기관, 도로명주소 전자지도, 국가기본도, 수치지형도에서 온 객체와 모양은 이용 조건을 확인하는 중이라 넣지 않았습니다.
    도로 구간, 법정동 경계, 국가기본도 건물까지 보이는 지도 화면은 <a href="{REPO}#실행">저장소에서 서버를 켜면</a> 열립니다.</p>
    <div id="sm-info"></div>
  </aside>
</div>"""
    head = ('<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css">'
            '<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js" defer></script>')
    (OUT / "map").mkdir()
    (OUT / "map" / "index.html").write_text(page("지도", body, nav="/map/", head=head, wide=True,
                                                 desc="증미역 반경 1km 공간 지식 그래프의 공개 객체 지도"), encoding="utf-8")

    # 전체 찾기용 목록 (이름이 있는 객체와 번호)
    search = [[l or u.rsplit("/", 1)[-1], u[len(BASE) + 1:], KINDS.get(k, (k,))[0]] for k, rows in by_kind.items() for u, l, _ in rows]
    (assets / "search.json").write_text(json.dumps(search, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # 어휘
    d = OUT / "def"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(ONTOLOGY, d / "ontology.ttl")
    shutil.copy(ALIGNMENT, d / "alignment.ttl")
    terms = sorted(s for s in triples if s.startswith(JGK))
    types_of = {s: {o.value for p, o in triples[s] if p.value == RDF_TYPE} for s in terms}
    OWL = "http://www.w3.org/2002/07/owl#"
    groups = [("객체 종류", lambda t: OWL + "Class" in t), ("관계", lambda t: OWL + "ObjectProperty" in t),
              ("속성", lambda t: OWL + "DatatypeProperty" in t or OWL + "AnnotationProperty" in t),
              ("국가기본도 지형지물 분류", lambda t: "http://www.w3.org/2004/02/skos/core#Concept" in t),
              ("상태 값과 원천 데이터", lambda t: True)]
    used, sections = set(), []
    for gname, test in groups:
        rows = [s for s in terms if s not in used and test(types_of[s])]
        used |= set(rows)
        if rows:
            trs = "".join(f'<tr id="{e(s.split("#")[1])}"><td class="mono">jgk:{e(s.split("#")[1])}</td><td>{e(labels.get(s, ""))}</td>'
                          f'<td class="small muted">{e(comments.get(s, ""))}</td></tr>' for s in rows)
            sections.append(f'<section class="card sec"><h2>{e(gname)} <span class="muted">{len(rows)}</span></h2><div class="tablewrap"><table class="kv vocab">{trs}</table></div></section>')
    body = (f'<nav class="crumbs"><a href="{SITE_PATH}/">처음</a><span>/</span><span>어휘</span></nav>'
            f'<section class="list-head"><h1>어휘 <span class="muted">jgk</span></h1><p class="muted">이 그래프가 쓰는 객체 종류, 관계, 속성입니다. GeoSPARQL 1.1 위에 세웠고, 국가기본도 지형지물 분류(수치지도 작성 작업규칙 제9조)를 SKOS로 넣었습니다.</p>'
            f'<p><a class="btn ghost mini" href="ontology.ttl">{ICON["file"]}ontology.ttl 내려받기</a> <a class="btn ghost mini" href="alignment.ttl">{ICON["file"]}alignment.ttl (바깥 어휘 정렬)</a></p></section>'
            + align_section() + "".join(sections))
    (d / "index.html").write_text(page("어휘", body, nav="/def/"), encoding="utf-8")

    # 첫 화면
    counts = {k: len(v) for k, v in by_kind.items()}
    order = ["building", "building-gis", "entrance", "parcel", "road", "legal-dong", "admin-dong", "basic-zone", "station", "sensor", "observation", "notice", "proposal"]
    kind_cards = "".join(f'<a class="kcard card" href="{SITE_PATH}/id/{k}/"><span class="kn">{counts[k]:,}</span><span class="kl">{e(KINDS.get(k, (k,))[0])}</span></a>'
                         for k in order + sorted(set(counts) - set(order)) if k in counts)
    principles = "".join(f'<div class="pcard card"><span class="pn">{i}</span><h3>{e(t)}</h3><p>{e(x)}</p></div>' for i, (t, x) in enumerate(PRINCIPLES, 1))
    station = f"{BASE}/station/1150010200106660040000001"
    examples = [(station, "증미역"), (f"{BASE}/building/1150010200106660040000001", "증미역 역사 건물"),
                (f"{BASE}/legal-dong/1150010200", "등촌동 (법정동)"), (f"{BASE}/road/115003115008", "양천로 (도로)")]
    ex = "".join(f'<a class="chip type" href="{e(u.replace(SITE, SITE_PATH))}/">{e(t)}</a>' for u, t in examples if public(u))
    body = f"""<section class="hero"><div class="hero-text">
  <p class="eyebrow">서울 9호선 증미역 반경 1km, GeoSPARQL 1.1</p>
  <h1>공식 번호로 엮은<br>증미역 공간 지식 그래프</h1>
  <p class="lead">건물, 출입구, 필지, 도로, 행정구역을 객체로 쪼개고 객체마다 나라에서 쓰는 관리번호로 바뀌지 않는 URI를 붙였습니다.
  이 사이트의 모든 주소는 그 객체의 이름이자 내용 페이지입니다.</p>
  <div class="find hero-find"><div class="search-field">{ICON["search"]}<input id="site-q" class="field" type="search" autocomplete="off" placeholder="이름, 주소, 번호로 찾기 (예: 증미역, 양천로)" aria-label="객체 찾기"></div>
  <div id="site-hits" aria-live="polite"></div></div>
  <div class="examples"><span class="muted small">예:</span>{ex}</div>
  <p class="hero-cta"><a class="btn primary" href="{SITE_PATH}/map/">{ICON["globe"]}지도로 보기</a></p>
</div><figure class="hero-fig card"><svg class="kg-diagram" viewBox="0 0 440 290" role="img" aria-label="증미역 객체가 서로 이어진 모양: 역은 역사 건물 안에 있고, 건물은 출입구, 법정동, 필지, 국가기본도 건물과 이어지며, 출입구는 도로 구간에 면한다">
  <defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 10 5 0 10z" fill="var(--muted)"/></marker></defs>
  <g stroke="var(--line-2)" stroke-width="1.6" fill="none" marker-end="url(#ar)">
    <path d="M74 62 L186 120"/><path d="M276 128 L318 58"/><path d="M371 62 L371 98"/>
    <path d="M276 160 L318 194"/><path d="M222 172 L222 236"/><path d="M170 158 L128 186" stroke-dasharray="4 4"/>
  </g>
  <g font-size="10.5" fill="var(--muted)" font-family="var(--font)">
    <text x="96" y="84">건물 안에 있다</text><text x="300" y="96">출입구</text><text x="378" y="84">면한다</text>
    <text x="300" y="168">법정동</text><text x="228" y="210">필지 위</text><text x="112" y="164">같은 건물</text>
  </g>
  <g font-family="var(--font)" font-size="12.5" font-weight="700" text-anchor="middle">
    <rect x="16" y="20" width="100" height="40" rx="10" fill="var(--station)"/><text x="66" y="45" fill="#fff">증미역</text>
    <rect x="170" y="120" width="104" height="52" rx="12" fill="var(--brand)"/><text x="222" y="142" fill="var(--panel)">역사 건물</text>
    <text x="222" y="159" fill="var(--panel)" font-size="9.5" font-weight="500">건물관리번호 25자리</text>
    <rect x="318" y="20" width="106" height="40" rx="10" fill="var(--accent-soft)" stroke="var(--accent-line)"/><text x="371" y="45" fill="var(--accent)">주출입구</text>
    <rect x="318" y="100" width="106" height="40" rx="10" fill="var(--soft)" stroke="var(--road)"/><text x="371" y="125" fill="var(--road)">양천로 구간</text>
    <rect x="318" y="180" width="106" height="40" rx="10" fill="var(--soft)" stroke="var(--line-2)"/><text x="371" y="205" fill="var(--text-2)">등촌동</text>
    <rect x="170" y="238" width="104" height="40" rx="10" fill="var(--soft)" stroke="var(--line-2)"/><text x="222" y="263" fill="var(--text-2)">필지 PNU</text>
    <rect x="16" y="186" width="112" height="40" rx="10" fill="var(--panel)" stroke="var(--basemap)" stroke-dasharray="4 3"/><text x="72" y="211" fill="var(--basemap)">국가기본도 건물</text>
  </g>
</svg><figcaption class="small muted">객체는 공식 번호로 이름 붙고, 관계로 서로 이어진다.</figcaption></figure>
</section>
<section><h2 class="sec-title">객체 종류</h2><div class="kgrid">{kind_cards}</div></section>
<section><h2 class="sec-title">국가기본도 대전환 4원칙과 이 그래프</h2><div class="pgrid">{principles}</div></section>
<section class="card sec two"><div><h2>그래프 쓰기</h2><p class="small">페이지마다 같은 주소에 Turtle 파일(<code>index.ttl</code>)이 있어 기계가 그대로 읽을 수 있습니다.
어휘는 <a href="{SITE_PATH}/def/">jgk</a>이고, 원본에서 그래프를 다시 만드는 코드는 <a href="{REPO}">GitHub 저장소</a>에 있습니다.</p></div>
<div><h2>공개 범위</h2><p class="small">민원행정기관 전자지도, 도로명주소 전자지도, 국가기본도, 수치지형도에서 온 객체와 모양은 이용 조건을 확인하는 중이라 이 사이트에 넣지 않았습니다.</p></div></section>"""
    (OUT / "index.html").write_text(page("처음", body, nav="", desc="증미역 반경 1km 공간 지식 그래프의 URI 사이트"), encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    report = {"pages": n, "held": len([h for h in held if not h.endswith('/geometry')]), "by_kind": counts}
    (OUT / "publish_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
