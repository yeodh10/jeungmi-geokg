"""고유 주소(URI)를 실제로 열리는 주소로: 장소마다 정적 HTML 페이지와 Turtle 파일을 만든다.

실행: python -m src.graph.publish
출력: data/processed/site/ (GitHub Pages에 그대로 올리면 https://yeodh10.github.io/jeungmi-geokg/ 아래 URI가 열린다)
  index.html, map/index.html     지도 앱 (web/app/app.html을 src.graph.app_shell이 채운 것. 앱 코드는 assets/app/)
  about/index.html               소개 (그래프, 4원칙, 관계 그림, 그래프 쓰기, 공개 범위)
  id/{종류}/index.html           종류별 목록 (이름, 번호로 거르기)
  id/{종류}/{번호}/index.html    장소 페이지 (카드 순서: 머리, 주소, 행동 줄, 정보, 이어진 것, 없는 정보, 출처, 끝, 작은 지도)
  id/{종류}/{번호}/index.ttl     그 URI가 주어인 트리플 (N-Triples 줄로 쓴 Turtle)
  def/index.html, def/*.ttl      어휘
  404.html                       없는 주소 안내 (전에 공개했다가 뺀 /id/proposal/ 같은 주소도 여기로 온다)
  assets/                        style.css, common.js, site.css, site.js, app/(web/app의 .js와 .css), vendor/(web/vendor 전체),
                                 data/area.geojson (지도 앱 자료는 src.graph.appdata가 같은 폴더에 더한다)

공개 판단은 src.graph.public 한 곳에서 한다 (public_view). 공개 보류 출처(config/area.yaml의 publish.hold_sources)에서 왔거나
출처를 모르는 노드, 개인 집일 수 있는 상호는 페이지를 만들지 않고, 다른 페이지에서도 그 노드로 가는 연결을 뺀다.
다 만든 뒤 자체 검사(site_problems)를 돌려, 보류 자료의 이름이나 지도 번호가 나가면 만든 결과를 지우고 실패한다.
"""
from __future__ import annotations

import html
import json
import math
import re
import shutil
from collections import defaultdict
from pathlib import Path

import shapely
import yaml
from shapely.geometry import mapping

from src.graph.app_shell import SITE_PATH, build_stamp, render_app_html
from src.graph.public import (DCT_SOURCE, JGK, RDF_TYPE, RDFS_COMMENT, RDFS_LABEL, SITE, GraphView, display_name, hold_sources,
                              kind_of, private_businesses, public_nodes, public_view, short_address)
from src.graph.store import ALIGNMENT, ONTOLOGY
from src.model.uri import BASE

__all__ = ["hold_sources", "public_nodes", "private_businesses", "site_problems", "main"]  # 앞 둘은 src/review/app.py와 시험이 여기서 불러 쓴다

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "processed" / "site"
WEB = ROOT / "web"
REPO = "https://github.com/yeodh10/jeungmi-geokg"
GEO = "http://www.opengis.net/ont/geosparql#"
SOSA = "http://www.w3.org/ns/sosa/"
# 공개하면 안 되는 지도 번호: 국가기본도 객체ID(BLD로 시작하는 17자리), 국토부 UFID(17자리), 수치지형도 UFID(34자리)
MAP_NUMBER = re.compile(r"(?<![0-9A-Za-z])(?:BLD[0-9A-Z]{14}|B0010000[0-9A-Z]{9}|[0-9A-Z]{34})(?![0-9A-Za-z])")
OUR_PATH = re.compile(re.escape(SITE_PATH) + r"/id/[a-z-]+/[^\s\"'<>&]+")
HOLD_NOTE = ("민원행정기관 전자지도와 도로명주소 전자지도는 이용 목적 심사를 거쳐 받은 자료라서 공개하지 않기로 정했고, "
             "국가기본도와 수치지형도는 기본측량성과라서 공간정보관리법 제16조에 따라 해외 서버에 올리지 않습니다. "
             "이 네 자료에서 온 장소와 모양은 이 사이트에 넣지 않았습니다.")
UNIT_KO = {"DEG_C": "℃", "PERCENT": "%", "DeciB": "dB", "LUX": "lux", "UNITLESS": ""}
EXT_LABEL = {
    RDF_TYPE: "종류", RDFS_LABEL: "이름", DCT_SOURCE: "출처", RDFS_COMMENT: "설명",
    GEO + "hasGeometry": "모양", GEO + "hasDefaultGeometry": "기본 모양", GEO + "asWKT": "WKT",
    "http://www.w3.org/2002/07/owl#sameAs": "같은 장소", "http://www.w3.org/ns/prov#wasInfluencedBy": "반영된 변경 제안",
    GEO + "Feature": "공간 객체(Feature)", GEO + "Geometry": "모양(Geometry)", "http://purl.org/dc/terms/issued": "고시일",
    "http://purl.org/dc/terms/description": "설명", "http://purl.org/dc/terms/created": "만든 시각",
}
# 종류(URI 경로 이름) -> 화면 이름, 공식 번호 설명 (명세 8절 말)
KINDS = {
    "station": ("역", "역사 건물의 건물관리번호(25자리)를 그대로 쓴다."),
    "building": ("건물", "도로명주소 건물관리번호(25자리)를 그대로 쓴다."),
    "building-gis": ("도로명주소 없는 건물", "도로명주소가 없는 건물이라 GIS건물통합식별번호(28자리)를 그대로 쓴다. 나중에 주소가 이어져도 이 고유 주소는 남는다."),
    "entrance": ("건물 출입구", "주출입구는 '건물관리번호-main', 보조출입구는 '시군구코드-출입구일련번호'다."),
    "parcel": ("필지", "PNU(필지고유번호, 19자리)를 그대로 쓴다."),
    "road": ("도로", "도로명코드(시군구코드 5자리 + 도로명번호 7자리)를 그대로 쓴다."),
    "admin-dong": ("행정동", "행정동코드(10자리)를 그대로 쓴다."),
    "legal-dong": ("법정동", "법정동코드(10자리)를 그대로 쓴다."),
    "basic-zone": ("우편구역 (기초구역)", "국가기초구역번호(5자리, 우편번호와 같다)를 그대로 쓴다."),
    "notice": ("고시공고", "구청 고시 번호(시군구코드-연도-번호)를 그대로 쓴다."),
    "sensor": ("센서", "서울시 도시데이터 센서(S-DoT)의 모델 시리얼을 그대로 쓴다."),
    "business": ("상호", "소상공인시장진흥공단 상가업소번호(20자리)를 그대로 쓴다. 2026년 9월 기준 자료라 문을 닫거나 새로 연 가게와 다를 수 있다. "
                 "영업시간, 전화번호, 리뷰는 이 자료에 없다."),
    "observation": ("관측", "센서 시리얼, 측정 시각(연월일시분 12자리), 측정 항목을 이었다. 한 센서가 한 시각에 한 항목은 한 번만 잰다."),
    "proposal": ("변경 제안", "변경 제안 번호다. 승인된 제안만 그래프에 들어온다."),
}
# 끝에 따로 보여 줄 공식 번호 속성 (정보 표에서는 뺀다)
OFFICIAL = {"building": "buildingMgtNo", "building-gis": "gisBuildingId", "parcel": "pnu", "road": "roadCode", "admin-dong": "admDongCode",
            "legal-dong": "legalDongCode", "basic-zone": "basicZoneNo", "sensor": "sensorSerial", "business": "businessNo", "notice": "noticeNumber"}
OFFICIAL_LABEL = {"station": "역사 건물관리번호", "entrance": "출입구 번호", "observation": "관측 번호", "proposal": "제안 번호"}
# 정보 표의 순서 (어휘 이름 끝)
KEY_ORDER = ["useName", "floorsAbove", "floorsBelow", "structureName", "approvalDate", "lineName",
             "facilityType", "agencyName", "agencyCode", "entranceType", "siteArea", "totalFloorArea", "households",
             "mainBuildingCount", "complexMainUse", "retiredOn", "zoneKind",
             "branchName", "categoryLarge", "categoryMiddle", "categorySmall", "floorInfo", "linkedBy",
             "sensorModel", "installAddress", "installArea", "addressBuildingDistance", "hasSimpleResult", "resultTime"]
# 값이 있어야 할 속성 (없으면 '없는 정보'에 적는다)와 이 자료에 아예 없는 것 (명세 3.6절, 0-1절)
EXPECT = {"building": [("useName", "용도"), ("floorsAbove", "지상 층수"), ("structureName", "구조"), ("approvalDate", "사용승인일")],
          "building-gis": [("useName", "용도"), ("floorsAbove", "지상 층수"), ("structureName", "구조"), ("approvalDate", "사용승인일")],
          "parcel": [("siteArea", "대지면적")], "business": [("floorInfo", "층")]}
ABSENT = {"station": ["시간표", "출구 번호", "도착 정보"], "building": ["사진"], "building-gis": ["도로명주소", "사진"],
          "parcel": ["경계 모양"], "road": ["도로선 모양", "폭"], "admin-dong": ["경계 모양"], "legal-dong": ["경계 모양"],
          "basic-zone": ["경계 모양"], "sensor": ["실시간 값"], "entrance": ["이름"], "business": ["영업시간", "전화번호", "리뷰", "사진"]}
PRINCIPLES = [
    ("객체 원자화", "건물, 출입구, 필지, 도로, 행정구역을 각각 하나의 장소로 쪼개고 모양(Geometry)은 따로 둡니다."),
    ("영구 식별자", "새 번호를 만들지 않고 건물관리번호, PNU, 도로명코드 같은 공식 번호를 그대로 고유 주소(URI)에 씁니다. 장소가 없어져도 주소는 남습니다."),
    ("자동 갱신", "새 자료가 오면 그래프를 덮어쓰지 않고 장소마다 견줘 신규, 변경, 폐지 제안을 만듭니다."),
    ("AI 제안 후 사람 승인", "고시공고는 LLM이 바뀌는 내용만 뽑고, 검토자가 승인한 변경만 그래프에 들어가며 누가 언제 승인했는지 남습니다. "
                       "지금까지의 결정은 사람이 맡긴 검토자 Claude가 했고, 기록의 검토자 이름에 그대로 남아 있습니다."),
]
ICON = {
    "globe": '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18"/></svg>',
    "map": '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2z"/><path d="M9 4v14M15 6v14"/></svg>',
    "code": '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m8 7-5 5 5 5M16 7l5 5-5 5"/></svg>',
    "copy": '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>',
    "file": '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>',
    "pin": '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z"/><circle cx="12" cy="10" r="2.5"/></svg>',
}
NAV = [("/", "지도"), ("/about/", "소개"), ("/id/building/", "건물"), ("/id/parcel/", "필지"), ("/id/road/", "도로"), ("/def/", "어휘")]
MAP_CREDIT = ('배경지도 <a href="https://openfreemap.org" rel="noopener">OpenFreeMap</a>, '
              '<a href="https://www.openmaptiles.org/" rel="noopener">© OpenMapTiles</a>, '
              '<a href="https://www.openstreetmap.org/copyright" rel="noopener">© OpenStreetMap 기여자</a> (ODbL).')
STATUS_SHOW = {"주소 미연결": "도로명주소 없는 건물"}  # 상태 칩의 쉬운 말. 원래 값은 정보 표에 함께 적는다
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


def site_problems(out: Path, hold: set[str], held: set[str]) -> list[str]:
    """만든 사이트에 공개하면 안 되는 것이 있으면 그 목록. 어휘(def/)는 자료 이름을 정의하는 곳이라 이름 검사에서 뺀다.
    assets/vendor/는 받아 온 라이브러리, 타일, 글꼴이라 검사하지 않는다 (공개 전 검사 public_check가 비밀 값만 본다)."""
    names = [h.rsplit("#", 1)[-1] for h in hold]
    found = [f"페이지가 만들어진 보류 노드: {s}" for s in sorted(held) if (out / s[len(SITE) + 1:]).exists()]
    for f in sorted(out.rglob("*")):
        if not f.is_file():
            continue
        where = f.relative_to(out).as_posix()
        if where.startswith("assets/vendor/"):
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        if not where.startswith("def/"):
            found += [f"{where}: 보류 자료 이름 {n}" for n in names if n in text]
        found += [f"{where}: 지도 번호 {m}" for m in sorted(set(MAP_NUMBER.findall(text)))]
        for path in sorted(set(OUR_PATH.findall(text))):
            u = BASE + path[len(SITE_PATH) + 3:].removesuffix("/index.ttl").removesuffix("/index.html").rstrip("/")
            if u in held:
                found.append(f"{where}: 보류 노드 연결 {u}")
    return found


def page(title: str, body: str, *, nav: str | None = None, head: str = "", desc: str = "", build: str = "") -> str:
    """지도 앱이 아닌 모든 공개 페이지가 같이 쓰는 틀. 링크는 SITE_PATH로 시작해 GitHub Pages와 같은 경로에서 열린다.
    common.js는 defer 없이 먼저 읽어 고른 화면 밝기를 첫 그림 전에 넣는다."""
    v = f"?v={build}" if build else ""
    links = "".join(f'<a href="{SITE_PATH}{p}"{" class=on aria-current=page" if nav == p else ""}>{n}</a>' for p, n in NAV)
    desc = desc or "증미역 반경 1km의 건물과 주소를 공식 번호로 엮은 공간 지식 그래프"
    full = f"{title} | 증미역 공간 지식 그래프"
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(full)}</title><meta name="description" content="{e(desc)}">
<meta property="og:title" content="{e(full)}"><meta property="og:type" content="website"><meta property="og:description" content="{e(desc)}">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css" media="print" onload="this.media='all'">
<link rel="stylesheet" href="{SITE_PATH}/assets/style.css{v}"><link rel="stylesheet" href="{SITE_PATH}/assets/site.css{v}">{head}
<script src="{SITE_PATH}/assets/common.js{v}"></script><script src="{SITE_PATH}/assets/site.js{v}" defer></script></head><body>
<header class="topbar"><a class="brand" href="{SITE_PATH}/about/">증미역 공간 지식 그래프</a><nav aria-label="사이트 메뉴">{links}</nav>
<span class="spacer"></span><label class="theme-pick"><span class="sr-only">화면 밝기</span><select id="theme-pick" class="field">
<option value="system">밝기: 시스템</option><option value="light">밝게</option><option value="dark">어둡게</option></select></label>
<a class="tb-link" href="{REPO}" target="_blank" rel="noopener">{ICON["code"]}<span>소스 코드</span></a></header>
<main class="page">{body}</main>
<footer class="foot"><div>증미역 반경 1km 공간 지식 그래프, GeoSPARQL 1.1. 장소마다 공식 번호로 바뀌지 않는 고유 주소를 붙였습니다.</div>
<div>{MAP_CREDIT} 공개하지 않는 자료의 장소는 이 사이트에 넣지 않았습니다.</div></footer></body></html>"""


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
                       .replace("http://www.w3.org/2004/02/skos/core#", "skos:").replace("http://www.w3.org/2000/01/rdf-schema#", "rdfs:")
                       .replace(BASE + "/", "id:"))
    rows = sorted((str(s), str(p), str(o)) for s, p, o in g if str(p) in kinds)
    trs = "".join(f'<tr><td class="mono">{e(short(s))}</td><td>{kinds[p]}</td><td class="mono"><a href="{e(o)}" rel="noopener">{e(short(o))}</a></td></tr>' for s, p, o in rows)
    return (f'<section class="card sec"><h2>바깥 표준 어휘와 잇기 <span class="muted">{len(rows)}</span></h2>'
            '<p class="small muted">jgk를 모르는 시스템도 schema.org, INSPIRE 지형지물 개념, 위키데이터 이름으로 이 그래프를 읽을 수 있게 이었습니다. '
            'schema.org는 하위 종류, 하위 속성으로 이어 RDFS 추론만으로 바깥 이름으로 물을 수 있고, 정의가 꼭 같지 않은 것은 가까운 개념으로만 이었습니다.</p>'
            f'<div class="tablewrap"><table class="kv vocab">{trs}</table></div></section>')


def floor_key(v: str | None) -> tuple:
    """상호 층 정보의 순서: 지하가 먼저, 그다음 1층부터, 값이 없으면 맨 뒤."""
    if not v:
        return (2, 0, "")
    m = re.fullmatch(r"(?:B|지하\s*)(\d+)", v.strip())
    if m:
        return (0, -int(m.group(1)), v)
    m = re.match(r"(\d+)", v.strip())
    return (1, int(m.group(1)), v) if m else (1, 999, v)


def floor_text(v: str) -> str:
    v = v.strip()
    m = re.fullmatch(r"B(\d+)", v)
    if m:
        return f"지하 {m.group(1)}층"
    return f"{v}층" if re.fullmatch(r"\d+", v) else v


def use_text(v: str) -> str:
    """용도의 쉬운 말 (명세 8절). 원래 값은 괄호에 함께 둔다."""
    if "근린생활시설" in v:
        return f"상가 건물 ({v})"
    if v == "노유자시설":
        return f"어르신, 어린이 시설 ({v})"
    return v


def point_of(wkt: str):
    g = shapely.from_wkt(wkt)
    return g if g.geom_type == "Point" else g.centroid


def straight_m(a, b) -> float:
    """두 점(경도, 위도) 사이 직선 거리(m). 1km 안이라 평면 근사로 충분하다."""
    lat0 = math.radians((a.y + b.y) / 2)
    return math.hypot((a.x - b.x) * 111320 * math.cos(lat0), (a.y - b.y) * 110574)


def main() -> None:
    view: GraphView = public_view()
    hold = hold_sources()
    build = build_stamp()
    triples = view.triples
    held = view.held
    pub = {s for s in triples if s.startswith(BASE + "/")} - held
    labels = view.labels
    shown = view.shown
    _names: dict[str, str] = {}

    def nm(u: str) -> str:
        if u not in _names:
            if u.startswith(BASE + "/") or u.startswith(SITE + "/id/"):
                _names[u] = display_name(view, u)
            else:
                _names[u] = labels.get(u) or EXT_LABEL.get(u) or (
                    "위키데이터 " + u.rsplit("/", 1)[-1] if u.startswith("http://www.wikidata.org/entity/") else u.rsplit("#", 1)[-1])
        return _names[u]
    pname = lambda p: labels.get(p) or EXT_LABEL.get(p) or p.rsplit("#", 1)[-1]
    href_of = lambda u: u.replace(SITE, SITE_PATH) + "/"

    def link(u: str) -> str:
        if not shown(u):
            return '<span class="muted">(공개하지 않는 장소)</span>'
        if u.startswith(SITE):
            return f'<a href="{e(href_of(u) if u.startswith(BASE) else u.replace(SITE, SITE_PATH))}">{e(nm(u))}</a>'
        return f'<a href="{e(u)}" rel="noopener">{e(nm(u))}</a>'

    def value(o) -> str:
        v = o.value
        if hasattr(o, "datatype") and o.datatype is not None or getattr(o, "language", None):
            if v in ("true", "false") and getattr(o, "datatype", None) is not None and o.datatype.value.endswith("boolean"):
                return "예" if v == "true" else "아니요"
            return e(v)
        return link(v) if v.startswith("http") else e(v)

    def info_value(prop: str, o) -> str | None:
        """정보 표의 값. 층수 0은 값 없음이라 None (화면에 '0층'이 나오지 않게)."""
        name, v = prop.rsplit("#", 1)[-1], o.value
        if name in ("floorsAbove", "floorsBelow"):
            return None if v in ("0", "") else (f"지상 {e(v)}층" if name == "floorsAbove" else f"지하 {e(v)}층")
        if name == "approvalDate":
            return f'{e(v)} <span class="muted small">2018년까지의 자료입니다</span>'
        if name == "useName":
            return e(use_text(v))
        if name == "floorInfo":
            return e(floor_text(v))
        return value(o)

    if OUT.exists():
        shutil.rmtree(OUT)
    assets = OUT / "assets"
    assets.mkdir(parents=True)
    for name in ("style.css", "common.js", "site.css", "site.js"):
        shutil.copy(WEB / name, assets / name)
    (assets / "app").mkdir()
    for f in sorted((WEB / "app").glob("*")):  # 앱은 .js와 .css만 (화면 틀 app.html은 채워서 index.html로 쓴다). web/local은 복사하지 않는다
        if f.suffix in (".js", ".css"):
            shutil.copy(f, assets / "app" / f.name)
    if (WEB / "vendor").exists():
        shutil.copytree(WEB / "vendor", assets / "vendor")
    (assets / "data").mkdir()
    (assets / "data" / "area.geojson").write_text(json.dumps(area_circle(), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    inbound = defaultdict(list)
    for s, plist in triples.items():
        for p, o in plist:
            if o.value.startswith(BASE) and s.startswith(BASE) and not p.value.startswith(GEO) and "/change/" not in s:
                inbound[o.value].append((p.value, s))
    obs_summary = observation_summary(triples)
    station_uri = f"{BASE}/station/1150010200106660040000001"
    station_wkt = next((o.value for p, o in triples.get(station_uri + "/geometry", []) if p.value == GEO + "asWKT"), None)
    station_pt = point_of(station_wkt) if station_wkt else None

    by_kind = defaultdict(list)  # 종류 -> [(uri, 이름, 상태)]
    n = 0
    for s, plist in triples.items():
        # 모양 노드(…/geometry)도 이미 공개한 고유 주소라 페이지를 계속 만든다. 목록에는 넣지 않는다
        is_geom = s.endswith("/geometry")
        if s not in pub:
            continue
        d = OUT / s[len(SITE) + 1:]
        d.mkdir(parents=True, exist_ok=True)
        keep = [(p, o) for p, o in plist if shown(o.value)]
        (d / "index.ttl").write_text("".join(f"<{s}> {term_nt(p)} {term_nt(o)} .\n" for p, o in keep), encoding="utf-8")
        kind = kind_of(s)
        kind_ko, rule = KINDS.get(kind, (kind, ""))
        by_p = defaultdict(list)
        for p, o in keep:
            by_p[p.value].append(o)
        status = [labels.get(o.value) or o.value.rsplit("#", 1)[-1] for o in by_p.get(JGK + "status", [])]
        parent = s.removesuffix("/geometry")
        title = nm(s)
        path = s[len(BASE) + 1:]
        if is_geom:
            rule = f"{nm(parent)}의 모양(Geometry)입니다. 좌표는 경도, 위도 순서의 WKT(CRS84)로 적었습니다."
            kind_ko = "모양"
        elif "/change/" not in s:  # 제안이 바꾼 트리플 기록(…/change/1)은 페이지만 두고 목록에는 넣지 않는다
            by_kind[kind].append((s, title, status[0] if status else ""))

        # 머리: 구분 칩, 상태 칩, 이름
        chips = f'<span class="chip type">{e("건물" if kind == "building-gis" and not is_geom else kind_ko)}</span>' + "".join(
            f'<span class="chip {"retire" if "폐지" in x else "src"}" title="원래 값: {e(x)}">{e(STATUS_SHOW.get(x, x))}</span>' for x in status)
        lead = ""
        if kind in ("building", "building-gis") and not is_geom:
            if not labels.get(s) and not any(by_p.get(JGK + k) for k in ("useName", "structureName", "approvalDate")) \
                    and all(o.value in ("0", "") for o in by_p.get(JGK + "floorsAbove", [])):
                lead = "위치와 주소만 있습니다." if view.addrs.get(s) else "위치와 지번만 있습니다."
            if kind == "building-gis":
                lead = (lead + " " if lead else "") + "도로명주소가 없는 건물입니다."
        if kind == "business":
            lead = "상가정보 2026년 9월 기준 자료라 문을 닫거나 새로 연 가게와 다를 수 있습니다."

        # 주소 줄: 도로명, 지번, 복사 단추, 역에서 직선 거리
        addr = None if is_geom else view.address_of(s)
        jibun_of = {"building": s, "building-gis": s, "parcel": s, "station": view.value(s, "housedIn"),
                    "business": view.value(s, "inBuilding") or view.value(s, "onParcel")}.get(kind)  # 지번을 읽을 장소
        jibun = view.jibun(jibun_of) if jibun_of and not is_geom else None
        gnode = s if is_geom else f"{s}/geometry"
        wkt = next((o.value for p, o in triples.get(gnode, []) if p.value == GEO + "asWKT"), None) if gnode in pub else None
        dist = None
        if wkt and station_pt is not None and kind != "station" and not is_geom:
            dist = round(straight_m(point_of(wkt), station_pt) / 10) * 10
        addr_row = ""
        if addr or jibun or dist is not None:
            parts = ([f'<span class="addr-road">{e(short_address(addr))}</span>'] if addr else []) \
                + ([f'<span class="addr-jibun"><span class="muted">지번</span> {e(jibun)}</span>'] if jibun else []) \
                + ([f'<span class="addr-dist muted">역에서 직선 {dist:,}m</span>'] if dist is not None else [])
            addr_row = f'<div class="addr-row">{"".join(parts)}</div>'

        # 행동 줄: 지도에서 열기, 고유 주소 복사, 주소 복사, 기계용 원본
        acts = []
        if not is_geom and kind != "observation":
            acts.append(f'<a class="btn mini primary" href="{SITE_PATH}/?focus={e(path)}">{ICON["map"]}지도에서 열기</a>')
        acts.append(f'<button type="button" class="btn ghost mini copy" data-copy="{e(s)}" data-done="고유 주소를 복사했습니다">{ICON["copy"]}고유 주소 복사</button>')
        if addr:
            acts.append(f'<button type="button" class="btn ghost mini copy" data-copy="{e(addr)}" data-done="주소를 복사했습니다">{ICON["copy"]}주소 복사</button>')
        acts.append(f'<a class="btn ghost mini" href="index.ttl" type="text/turtle">{ICON["file"]}기계용 원본</a>')

        # 정보: 값 (공식 번호와 도로명주소는 따로 보인다)
        skip = {RDF_TYPE, RDFS_LABEL, JGK + "status", DCT_SOURCE, GEO + "hasGeometry", GEO + "hasDefaultGeometry", JGK + "roadAddress"}
        if kind in OFFICIAL:
            skip.add(JGK + OFFICIAL[kind])
        lits = [p for p in by_p if p not in skip and not any(o.value.startswith("http") and not getattr(o, "datatype", None) for o in by_p[p])]
        lits.sort(key=lambda p: (KEY_ORDER.index(p.rsplit("#", 1)[-1]) if p.rsplit("#", 1)[-1] in KEY_ORDER else 99, pname(p)))
        rels = [p for p in by_p if p not in skip and p not in lits and p != RDF_TYPE]
        info_rows = []
        for p in lits:
            vals = [x for x in (info_value(p, o) for o in by_p[p]) if x]
            if vals:
                info_rows.append(f'<tr><td class="k">{e(pname(p))}</td><td>{", ".join(vals)}</td></tr>')
        for x in status:
            if x in STATUS_SHOW:
                info_rows.append(f'<tr><td class="k">상태 (원래 값)</td><td>{e(x)}</td></tr>')
        info = "".join(info_rows)

        # 이어진 것: 이 장소에서 가는 것, 이 장소로 오는 것 (상호는 건물 페이지에서 '입점 상호'로 따로 보인다)
        out_links = "".join(f'<tr><td class="k">{e(pname(p))}</td><td class="links">{" ".join(link(o.value) for o in by_p[p])}</td></tr>' for p in rels)
        back = defaultdict(list)
        tenants = []
        for p, src in inbound.get(s, []):
            if src not in pub:
                continue
            if p == JGK + "inBuilding":
                tenants.append(src)
            else:
                back[p].append(src)
        back_rows = "".join(
            f'<tr><td class="k">{e(pname(p))}</td><td class="links">{" ".join(link(x) for x in sorted(srcs, key=nm)[:40])}'
            + (f' <span class="muted">외 {len(srcs) - 40:,}곳</span>' if len(srcs) > 40 else "") + "</td></tr>"
            for p, srcs in sorted(back.items(), key=lambda t: pname(t[0])))
        tenant_sec = ""
        if tenants:
            tenants.sort(key=lambda u: (floor_key(view.value(u, "floorInfo")), nm(u)))
            rows = "".join(
                f'<tr><td>{link(u)}</td><td>{e(floor_text(view.value(u, "floorInfo"))) if view.value(u, "floorInfo") else "<span class=muted>층 정보 없음</span>"}</td>'
                f'<td>{e(view.value(u, "categorySmall") or view.value(u, "categoryMiddle") or "")}</td><td class="muted">{e(view.value(u, "linkedBy") or "")}</td></tr>'
                for u in tenants)
            tenant_sec = (f'<section class="card sec"><h2>입점 상호 <span class="muted">{len(tenants):,}곳</span></h2>'
                          '<p class="small muted">소상공인 상가정보 2026년 9월 기준입니다. 같은 가게가 두 줄로 나올 수 있습니다. 영업시간, 전화번호, 리뷰는 이 자료에 없습니다.</p>'
                          f'<div class="tablewrap"><table class="kv listing"><thead><tr><th>상호</th><th>층</th><th>업종</th><th>이은 방법</th></tr></thead>{rows}</table></div></section>')

        # 없는 정보
        missing = [label for prop, label in EXPECT.get(kind, []) if not is_geom and not [
            o for o in by_p.get(JGK + prop, []) if o.value not in ("", "0")]]
        absent = [] if is_geom else [x for x in ABSENT.get(kind, []) if not (x == "경계 모양" and wkt)]
        missing_sec = (f'<section class="card sec missing"><h2>없는 정보</h2><p>{e(", ".join(missing + absent))}</p></section>'
                       if missing or absent else "")

        # 출처: 장소의 출처와 모양의 출처
        src_names = [nm(o.value) for o in by_p.get(DCT_SOURCE, [])]
        shape_src = [nm(o.value) for p, o in triples.get(gnode, []) if p.value == DCT_SOURCE and shown(o.value)] if not is_geom and wkt else []
        src_rows = ""
        if src_names:
            src_rows += f'<tr><td class="k">{"이 모양의 출처" if is_geom else "이 장소의 출처"}</td><td>{"<br>".join(e(x) for x in src_names)}</td></tr>'
        if not is_geom and wkt:
            src_rows += f'<tr><td class="k">이 모양의 출처</td><td>{"<br>".join(e(x) for x in shape_src) if shape_src else "장소의 출처와 같습니다"}</td></tr>'
        source_sec = f'<section class="card sec graph"><h2>출처</h2><table class="kv">{src_rows}</table></section>' if src_rows else ""

        # 센서: 관측 항목마다 개수, 평균, 최소, 최대, 시간별 꺾은선
        obs_rows = ""
        for prop, pts in sorted(obs_summary.get(s, {}).items(), key=lambda t: nm(t[0])):
            pts.sort()
            vals = [v for _, v in pts]
            unit_uri = next((o.value for p, o in triples.get(prop, []) if p.value == JGK + "unit"), "")
            u = UNIT_KO.get(unit_uri.rsplit("/", 1)[-1], "")
            obs_rows += (f'<tr><td class="k">{link(prop) if prop.startswith(BASE) else e(nm(prop))}</td><td class="num">{len(vals):,}</td>'
                         f'<td class="num">{sum(vals) / len(vals):.1f}{e(u)}</td><td class="num">{min(vals):g} ~ {max(vals):g}</td><td>{sparkline(pts)}</td></tr>')
        if obs_rows:
            period = min(t for v in obs_summary[s].values() for t, _ in v)[:10] + " ~ " + max(t for v in obs_summary[s].values() for t, _ in v)[:10]
            obs_rows = (f'<section class="card sec"><h2>관측 요약 <span class="muted">{e(period)}, 1시간 평균값</span></h2>'
                        '<p class="small muted">실시간 값이 아닙니다. 정해진 기간에 받은 자료입니다.</p><div class="tablewrap"><table class="kv obs">'
                        f'<thead><tr><th>항목</th><th>개수</th><th>평균</th><th>범위</th><th>시간별</th></tr></thead>{obs_rows}</table></div></section>')

        # 끝: 고유 주소, 공식 번호, 사실 수, 기계용 원본(접힘)
        if kind in OFFICIAL and by_p.get(JGK + OFFICIAL[kind]):
            off_label, off_value = pname(JGK + OFFICIAL[kind]), by_p[JGK + OFFICIAL[kind]][0].value
        elif not is_geom and kind in OFFICIAL_LABEL:
            off_label, off_value = OFFICIAL_LABEL[kind], s.rsplit("/", 1)[-1]
        else:
            off_label, off_value = None, None
        all_rows = "".join(f'<tr><td class="k">{e(pname(p.value))}</td><td class="mono">{e(o.value[:300])}</td></tr>'
                           for p, o in sorted(keep, key=lambda t: t[0].value))
        end_sec = f"""<section class="card sec graph end"><h2>고유 주소</h2>
  <div class="uri-row"><code class="uri">{e(s)}</code><button type="button" class="btn ghost mini copy" data-copy="{e(s)}" data-done="고유 주소를 복사했습니다">{ICON["copy"]}복사</button></div>
  {f'<p class="small"><span class="muted">{e(off_label)}</span> <span class="mono">{e(off_value)}</span></p>' if off_label else ""}
  <p class="small muted">그래프의 사실 {len(keep):,}개에서 왔습니다.</p>
  <details class="raw"><summary>기계용 원본 (Turtle), 모든 사실 {len(keep):,}개</summary>
    <p class="small"><a href="index.ttl" type="text/turtle">{ICON["file"]}index.ttl 내려받기</a> 같은 주소에 기계가 읽는 파일이 있습니다.</p>
    <div class="tablewrap"><table class="kv">{all_rows}</table></div></details>
</section>"""

        geom_json = ""
        if wkt:
            g = shapely.set_precision(shapely.from_wkt(wkt), 1e-6)
            geom_json = json.dumps(mapping(g), separators=(",", ":"))
        crumbs = (f'<nav class="crumbs" aria-label="위치"><a href="{SITE_PATH}/">지도</a><span>/</span>'
                  f'<a href="{SITE_PATH}/id/{kind}/">{e(KINDS.get(kind, (kind,))[0])}</a><span>/</span>'
                  + (f'{link(parent)}<span>/</span>' if is_geom else "") + f'<span>{e(title)}</span></nav>')
        body = f"""{crumbs}
<div class="obj-grid">
  <div class="obj-main">
    <section class="obj-head card">
      <div class="chips">{chips}</div>
      <h1>{e(title)}</h1>
      {f'<p class="lead-note">{e(lead)}</p>' if lead else ""}
      {addr_row}
      <div class="act-row" role="group" aria-label="이 장소로 할 수 있는 것">{"".join(acts)}</div>
    </section>
    {f'<section class="card sec"><h2>정보</h2><table class="kv">{info}</table></section>' if info else ""}
    {obs_rows}
    {tenant_sec}
    {f'<section class="card sec graph"><h2>이어진 것</h2><table class="kv">{out_links}{back_rows}</table></section>' if out_links or back_rows else ""}
    {missing_sec}
    {source_sec}
    {end_sec}
  </div>
  <aside class="obj-side">
    {f'<section class="card mapcard"><div id="objmap" data-geom="{e(geom_json)}" role="img" aria-label="{e(title)}의 위치 지도"></div></section>' if geom_json else ""}
    <section class="card sec"><h2>이 고유 주소</h2><p class="small">{e(rule)}</p>{f'<p class="small">장소: {link(parent)}</p>' if is_geom else ""}
      <p class="small muted">이 주소는 장소의 이름입니다. 장소가 없어져도 지우지 않고 상태만 폐지로 바꿉니다.</p></section>
  </div>
</div>"""
        head = ('<link rel="alternate" type="text/turtle" href="index.ttl">'
                + (f'<link rel="stylesheet" href="{SITE_PATH}/assets/vendor/maplibre-gl-4.7.1/maplibre-gl.css">'
                   f'<script src="{SITE_PATH}/assets/vendor/maplibre-gl-4.7.1/maplibre-gl.js" defer></script>' if geom_json else ""))
        nav_key = f"/id/{kind}/" if kind in ("building", "parcel", "road") else None
        (d / "index.html").write_text(page(title, body, nav=nav_key, head=head, desc=f"{KINDS.get(kind, (kind,))[0]} {title}, {s}", build=build),
                                      encoding="utf-8")
        n += 1

    # 종류별 목록
    for kind, rows in by_kind.items():
        kind_ko, rule = KINDS.get(kind, (kind, ""))
        rows.sort(key=lambda r: (view.addrs.get(r[0], "~"), r[1], r[0]))  # 주소순 (주소가 없으면 뒤로)
        has_addr = any(u in view.addrs for u, _, _ in rows)
        trs = "".join(f'<tr><td><a href="{e(href_of(u))}">{e(l)}</a></td>'
                      + (f'<td>{e(short_address(view.addrs[u])) if u in view.addrs else ""}</td>' if has_addr else "")
                      + f'<td class="mono">{e(u.rsplit("/", 1)[-1])}</td><td>{e(STATUS_SHOW.get(st, st))}</td></tr>' for u, l, st in rows)
        body = f"""<nav class="crumbs" aria-label="위치"><a href="{SITE_PATH}/">지도</a><span>/</span><span>{e(kind_ko)}</span></nav>
<section class="list-head"><h1>{e(kind_ko)} <span class="muted">{len(rows):,}곳</span></h1><p class="muted">{e(rule)}</p>
<div class="search-field"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
<input class="field" type="search" id="filter" placeholder="이름, 주소, 번호로 거르기" autocomplete="off" aria-label="목록 거르기"></div></section>
<section class="card"><div class="tablewrap"><table class="kv listing" id="listing"><thead><tr><th>이름</th>{"<th>도로명주소</th>" if has_addr else ""}<th>번호</th><th>상태</th></tr></thead><tbody>{trs}</tbody></table></div>
<div class="empty" id="nohit" hidden>맞는 장소가 없습니다.</div></section>"""
        d = OUT / "id" / kind
        d.mkdir(parents=True, exist_ok=True)
        nav_key = f"/id/{kind}/" if kind in ("building", "parcel", "road") else None
        (d / "index.html").write_text(page(f"{kind_ko} 목록", body, nav=nav_key, build=build), encoding="utf-8")

    # 지도 앱: / 와 /map/ (옛 링크 /map/?u=...를 지킨다)
    app = render_app_html("public", build=build)
    (OUT / "index.html").write_text(app, encoding="utf-8")
    (OUT / "map").mkdir()
    (OUT / "map" / "index.html").write_text(app, encoding="utf-8")

    # 어휘
    d = OUT / "def"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(ONTOLOGY, d / "ontology.ttl")
    shutil.copy(ALIGNMENT, d / "alignment.ttl")
    terms = sorted(s for s in triples if s.startswith(JGK))
    types_of = {s: {o.value for p, o in triples[s] if p.value == RDF_TYPE} for s in terms}
    OWL = "http://www.w3.org/2002/07/owl#"
    groups = [("장소 종류", lambda t: OWL + "Class" in t), ("이어진 것 (관계)", lambda t: OWL + "ObjectProperty" in t),
              ("속성", lambda t: OWL + "DatatypeProperty" in t or OWL + "AnnotationProperty" in t),
              ("국가기본도 지형지물 분류", lambda t: "http://www.w3.org/2004/02/skos/core#Concept" in t),
              ("상태 값과 원천 데이터", lambda t: True)]
    used, sections = set(), []
    for gname, test in groups:
        rows = [s for s in terms if s not in used and test(types_of[s])]
        used |= set(rows)
        if rows:
            trs = "".join(f'<tr id="{e(s.split("#")[1])}"><td class="mono">jgk:{e(s.split("#")[1])}</td><td>{e(labels.get(s, ""))}</td>'
                          f'<td class="small muted">{e(view.comments.get(s, ""))}</td></tr>' for s in rows)
            sections.append(f'<section class="card sec"><h2>{e(gname)} <span class="muted">{len(rows)}</span></h2><div class="tablewrap"><table class="kv vocab">{trs}</table></div></section>')
    body = (f'<nav class="crumbs" aria-label="위치"><a href="{SITE_PATH}/">지도</a><span>/</span><span>어휘</span></nav>'
            f'<section class="list-head"><h1>어휘 <span class="muted">jgk</span></h1><p class="muted">이 그래프가 쓰는 장소 종류, 이어진 것(관계), 속성입니다. GeoSPARQL 1.1 위에 세웠고, 국가기본도 지형지물 분류(수치지도 작성 작업규칙 제9조)를 SKOS로 넣었습니다.</p>'
            f'<p><a class="btn ghost mini" href="ontology.ttl">{ICON["file"]}ontology.ttl 내려받기</a> <a class="btn ghost mini" href="alignment.ttl">{ICON["file"]}alignment.ttl (바깥 어휘 정렬)</a></p></section>'
            + align_section() + "".join(sections))
    (d / "index.html").write_text(page("어휘", body, nav="/def/", build=build), encoding="utf-8")

    # 소개 (옛 첫 화면 내용)
    counts = {k: len(v) for k, v in by_kind.items()}
    order = ["building", "building-gis", "entrance", "parcel", "road", "legal-dong", "admin-dong", "basic-zone", "station", "sensor", "observation", "business", "notice", "proposal"]
    kind_cards = "".join(f'<a class="kcard card" href="{SITE_PATH}/id/{k}/"><span class="kn">{counts[k]:,}</span><span class="kl">{e(KINDS.get(k, (k,))[0])}</span></a>'
                         for k in order + sorted(set(counts) - set(order)) if k in counts)
    principles = "".join(f'<div class="pcard card"><span class="pn">{i}</span><h3>{e(t)}</h3><p>{e(x)}</p></div>' for i, (t, x) in enumerate(PRINCIPLES, 1))
    examples = [(station_uri, "증미역"), (f"{BASE}/building/1150010200106660040000001", "증미역 역사 건물"),
                (f"{BASE}/legal-dong/1150010200", "등촌동 (법정동)"), (f"{BASE}/road/115003115008", "양천로 (도로)")]
    ex = "".join(f'<a class="chip type" href="{e(href_of(u))}">{e(t)}</a>' for u, t in examples if u in pub)
    body = f"""<section class="hero"><div class="hero-text">
  <p class="eyebrow">서울 9호선 증미역 반경 1km, GeoSPARQL 1.1</p>
  <h1>공식 번호로 엮은<br>증미역 공간 지식 그래프</h1>
  <p class="lead">건물, 출입구, 필지, 도로, 행정구역을 장소로 쪼개고 장소마다 나라에서 쓰는 관리번호로 바뀌지 않는 고유 주소(URI)를 붙였습니다.
  이 사이트의 모든 주소는 그 장소의 이름이자 내용 페이지입니다.</p>
  <div class="examples"><span class="muted small">예:</span>{ex}</div>
  <p class="hero-cta"><a class="btn primary" href="{SITE_PATH}/">{ICON["map"]}증미역 지도 열기</a></p>
</div><figure class="hero-fig card"><svg class="kg-diagram" viewBox="0 0 440 290" role="img" aria-label="증미역 장소가 서로 이어진 모양: 역은 역사 건물 안에 있고, 건물은 출입구, 법정동, 필지, 국가기본도 건물과 이어지며, 출입구는 도로 구간에 면한다">
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
    <rect x="16" y="20" width="100" height="40" rx="10" fill="var(--station-mark)"/><text x="66" y="45" fill="var(--on-station)">증미역</text>
    <rect x="170" y="120" width="104" height="52" rx="12" fill="var(--brand)"/><text x="222" y="142" fill="var(--panel)">역사 건물</text>
    <text x="222" y="159" fill="var(--panel)" font-size="9.5" font-weight="500">건물관리번호 25자리</text>
    <rect x="318" y="20" width="106" height="40" rx="10" fill="var(--accent-soft)" stroke="var(--accent-line)"/><text x="371" y="45" fill="var(--accent)">주출입구</text>
    <rect x="318" y="100" width="106" height="40" rx="10" fill="var(--soft)" stroke="var(--road)"/><text x="371" y="125" fill="var(--road)">양천로 구간</text>
    <rect x="318" y="180" width="106" height="40" rx="10" fill="var(--soft)" stroke="var(--line-2)"/><text x="371" y="205" fill="var(--text-2)">등촌동</text>
    <rect x="170" y="238" width="104" height="40" rx="10" fill="var(--soft)" stroke="var(--line-2)"/><text x="222" y="263" fill="var(--text-2)">필지 PNU</text>
    <rect x="16" y="186" width="112" height="40" rx="10" fill="var(--panel)" stroke="var(--basemap)" stroke-dasharray="4 3"/><text x="72" y="211" fill="var(--basemap)">국가기본도 건물</text>
  </g>
</svg><figcaption class="small muted">장소는 공식 번호로 이름 붙고, 이어진 것으로 서로 이어진다. 도로 구간과 국가기본도 건물은 그래프에만 있고 공개 사이트에는 싣지 않는다.</figcaption></figure>
</section>
<section><h2 class="sec-title">장소 종류</h2><div class="kgrid">{kind_cards}</div></section>
<section><h2 class="sec-title">국가기본도 대전환 4원칙과 이 그래프</h2><div class="pgrid">{principles}</div></section>
<section class="card sec two"><div><h2>그래프 쓰기</h2><p class="small">페이지마다 같은 주소에 기계용 원본(Turtle, <code>index.ttl</code>)이 있어 기계가 그대로 읽을 수 있습니다.
어휘는 <a href="{SITE_PATH}/def/">jgk</a>이고, 원본에서 그래프를 다시 만드는 코드는 <a href="{REPO}">GitHub 저장소</a>에 있습니다.</p></div>
<div><h2>공개 범위</h2><p class="small">{e(HOLD_NOTE)} 출처를 알 수 없는 장소도 넣지 않고, 승인한 변경은 근거 자료가 적혀 있고 모두 공개 자료일 때만 얹습니다.
개인 집 주소일 수 있는 주택 건물 안 상호 일부도 싣지 않았습니다.</p></div></section>"""
    (OUT / "about").mkdir()
    (OUT / "about" / "index.html").write_text(page("소개", body, nav="/about/", build=build,
                                                   desc="증미역 반경 1km의 건물과 주소를 공식 번호로 엮은 공간 지식 그래프 소개"), encoding="utf-8")

    # 없는 주소 (GitHub Pages가 사이트 안의 없는 주소에 이 파일을 준다)
    body = f"""<section class="notfound card sec"><h1>이 주소를 찾지 못했습니다</h1>
<p>없는 주소이거나 공개 범위가 바뀌어 더 보여 주지 않는 주소입니다.</p>
<p class="act-row"><a class="btn mini primary" href="{SITE_PATH}/">{ICON["map"]}증미역 지도</a> <a class="btn ghost mini" href="{SITE_PATH}/id/building/">건물 목록</a>
<a class="btn ghost mini" href="{SITE_PATH}/about/">소개</a></p></section>"""
    (OUT / "404.html").write_text(page("없는 주소", body, build=build), encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    report = {"pages": n, "build": build, "held": len([h for h in held if not h.endswith('/geometry')]),
              "business_not_public": len([h for h in view.private if not h.endswith("/geometry")]), "by_kind": counts}
    (OUT / "publish_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    problems = site_problems(OUT, hold, held)
    if problems:
        shutil.rmtree(OUT)  # 잘못 만든 결과가 그대로 올라가지 않게 지운다
        raise RuntimeError(f"공개하면 안 되는 값이 {len(problems)}곳에 있어 사이트를 지웠다: " + "; ".join(problems[:10]))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
