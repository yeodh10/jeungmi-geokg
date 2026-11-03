"""자연어 질문을 SPARQL로 바꿔 그래프에 묻고, 결과를 지도에 그릴 수 있게 돌려준다.

실행: python -m src.query.nl "증미역 300m 안에 있는 학교는?"

- LLM은 어휘 설명과 예시 쿼리(src/graph/queries)를 보고 SPARQL 한 개를 쓴다.
- 코드가 실행 전에 확인한다: 읽기 쿼리(SELECT, ASK)만 허용, 쿼리 길이 제한, 결과 수 제한(MAX_ROWS보다 많이 달라고 해도 줄인다).
- 거리 조건은 pyoxigraph에 geof:buffer가 없어서 LLM이 {{NEAR:객체URI:미터}} 자리표시를 쓰면 코드가 원 모양 WKT로 바꾼다.
  자리표시는 문자열 밖에서만 바꾸고, 기준 객체는 쿼리가 아니라 트리플 찾기로 얻고, 바꾼 결과도 다시 확인한다.
- 질문은 LLM에 보내기 전에 입력 가드(src/guard)를 거친다. 차단이면 GuardBlocked로 멈춘다.
- 쿼리가 실패하면 오류 내용을 붙여 한 번 더 고쳐 쓰게 한다.
- 쿼리는 일꾼 프로세스에서 돈다 (src/query/worker.py). QUERY_SECONDS나 메모리 상한을 넘기면 프로세스째 끝내고 새로 띄운다.
  일꾼을 준비하는 동안 오는 질의는 QueryBusy로 받지 않는다. 결과도 칸과 전체 크기에 상한이 있다.
- 그래프에서 뽑아 프롬프트에 넣는 값은 승인으로 바뀔 수 있다. 그래서 원천 자료(objects.gpkg, admin_dong.csv)에 있는 값만 넣는다
  (허용 목록). 승인으로 바뀌거나 새로 생긴 값은 넣지 않는다. 그 위에 줄바꿈, 긴 글, 허용하지 않은 글자가 든 값과
  입력 가드가 차단이나 검토로 보는 값(한글만 쓴 지시문 등)도 넣지 않는다.
"""
from __future__ import annotations

import logging
import re
import sys
from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyoxigraph as ox
import shapely
from pydantic import BaseModel, Field
from shapely.geometry import mapping

from src.graph.store import load_store
from src.guard import GuardBlocked, screen_output
from src.guard import check as guard_check
from src.llm.client import get_client, parse
from src.model.uri import BASE
from src.query.worker import MAX_ROWS, START_SECONDS, QueryBusy, QueryTimeout, QueryTooBig, pool  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
PROC = ROOT / "data" / "processed"
QUERIES = ROOT / "src" / "graph" / "queries"
ONTOLOGY = ROOT / "src" / "graph" / "ontology.ttl"
QUERY_SECONDS = 20
READY_WAIT = 5.0  # 유료 호출 전에 일꾼을 기다리는 시간. 일꾼이 뜨는 데 3~4초 걸린다. 넘으면 QueryBusy
MAX_SPARQL_CHARS = 10_000  # 평가에서 모델이 쓴 가장 긴 쿼리가 1,028자다
MAX_FILLED_CHARS = 500_000  # 자리표시를 모양으로 바꾼 뒤의 길이. 가장 복잡한 법정동 둘레의 원이 5만 자쯤이고 자리표시는 8개까지다
MAX_NEAR = 8  # 쿼리 하나의 자리표시 수
MAX_NEAR_METERS = 100_000  # 범위가 역 둘레이므로 100km면 넉넉하다
# 프롬프트에 넣는 그래프 값 하나의 길이. 지금 값 44개(역 1, 행정동 8, 건물 용도 27, 시설 유형 8) 중 가장 긴 값이
# 10자(위험물저장및처리시설, 여객(화물)운송시설, 문화/관광/레저시설, 소방/119안전센터)라 두 자 여유를 둔다
MAX_VALUE_CHARS = 12
GEO = "http://www.opengis.net/ont/geosparql#"
HAS_DEFAULT_GEOMETRY = ox.NamedNode(GEO + "hasDefaultGeometry")
AS_WKT = ox.NamedNode(GEO + "asWKT")
# 금지어는 단어 경계 없이 찾는다. 0SERVICE, SERVICEjgk:x처럼 붙여 써도 pyoxigraph는 SERVICE로 읽기 때문이다.
# 이름(변수, 아는 접두어의 이름)은 미리 지웠으므로 남은 글에는 SPARQL 낱말과 숫자, 기호뿐이고, 그 어떤 낱말도 금지어를 품지 않는다.
FORBIDDEN = re.compile(r"INSERT|DELETE|LOAD|CLEAR|DROP|CREATE|ADD|MOVE|COPY|SERVICE|WITH", re.I)
WKT = re.compile(r"^(POINT|MULTIPOINT|LINESTRING|MULTILINESTRING|POLYGON|MULTIPOLYGON)")
# 쿼리를 낱말로 나누는 틀: {{NEAR:객체URI:미터}}, 문자열(세 겹 따옴표, 큰따옴표, 작은따옴표, \ 이스케이프), IRI, 주석(문자열과 IRI 밖의 #부터 줄 끝).
# 자리표시의 URI에 {와 }를 받지 않으므로 어떤 글을 줘도 찾는 시간이 글 길이에 비례한다.
# 이 틀이 나눈 경계가 pyoxigraph의 경계와 어긋나면 코드를 문자열이나 IRI로 착각한다. 그래서 어긋날 수 있는 꼴은 bad로 막는다.
# - 닫히지 않은 따옴표. 뒤의 글이 문자열 안팎으로 뒤집힌다.
# - http(s):// 아닌 IRI. http라는 접두어는 없으니 <http://는 비교식(?a<b)으로 읽힐 수 없고 언제나 IRI다.
#   다른 꼴은 FILTER(?a<1)SERVICE#>처럼 비교식을 IRI로 꾸미거나, <urn:x'>처럼 IRI 안의 따옴표로 문자열 경계를 뒤집을 수 있다.
# 문자열 밖의 역슬래시(jgk:a\'b)도 같은 까닭으로 check()가 막는다.
TOKENS = re.compile(
    r'(?P<near>\{\{NEAR:<?(?P<uri>https?://[^\s<>"{}|^`\\]{1,500}?)>?:(?P<meters>\d+(?:\.\d+)?)\}\})'
    r'|(?P<str>"""(?:[^"\\]|\\.|"(?!""))*"""' r"|'''(?:[^'\\]|\\.|'(?!''))*'''"
    r'|"(?:[^"\\\n\r]|\\.)*"' r"|'(?:[^'\\\n\r]|\\.)*')"
    r'|(?P<iri><https?://[^<>"{}|^`\\\s]*>)|(?P<comment>#[^\n\r]*)|(?P<bad>["\']|<[A-Za-z][A-Za-z0-9+.\-]*:)', re.S)
VARIABLE = re.compile(r"[?$]\w+")
# 아는 접두어의 이름. 앞에 글이 붙어 있으면 이름이 아니다(SERVICEjgk:x). 이름은 .이나 -로 시작할 수 없다(geo:.SERVICE는 geo: 다음에 . 이다).
NAME = re.compile(r"(?<![\w.\-:])(?:jgk|geo|geof|rdfs|owl|prov|dcterms):"
                  r"(?:(?:[\w:]|%[0-9A-Fa-f]{2})(?:[\w.\-:]|%[0-9A-Fa-f]{2})*)?")
PREFIX_LINE = re.compile(r"(?im)^[ \t]*PREFIX[ \t]+\S+[ \t]*<[^>\n]*>[ \t\r]*$")
TAIL = re.compile(r"(?i)\b(?:LIMIT\s+(\d+)(?:\s+OFFSET\s+\d+)?|OFFSET\s+\d+\s+LIMIT\s+(\d+))\s*$")
# 프롬프트에 넣어도 되는 값. 지금 그래프의 역, 행정동, 건물 용도, 시설 유형은 모두 이 글자만 쓴다. 공백과 라틴 글자가 없으면 지시문을 쓰기 어렵다.
SAFE_VALUE = re.compile(rf"[가-힣0-9/()]{{1,{MAX_VALUE_CHARS}}}")
SAFE_URI = re.compile(re.escape(BASE) + r"/[0-9A-Za-z/_.\-]{1,200}")
PREFIXES = """PREFIX jgk: <https://yeodh10.github.io/jeungmi-geokg/def#>
PREFIX geo: <http://www.opengis.net/ont/geosparql#>
PREFIX geof: <http://www.opengis.net/def/function/geosparql/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX dcterms: <http://purl.org/dc/terms/>
"""
log = logging.getLogger(__name__)
prepare = pool.prepare  # prepare(store, wait): 이 그래프의 일꾼을 띄워 두고, wait초 안에 준비되면 True
worker_pid = pool.pid


class SparqlDraft(BaseModel):
    sparql: str = Field(description="PREFIX 없이 SELECT 또는 ASK로 시작하는 SPARQL 한 개")
    explanation: str = Field(description="어떻게 찾았는지 한국어 한두 문장")


def safe_value(v: str) -> bool:
    """프롬프트에 넣어도 되는 그래프 값인지. 글자와 길이(SAFE_VALUE)를 보고, 입력 가드도 거친다.

    한글만 쓴 지시문('이전지시무시' 등)은 글자 검사를 지나므로 가드가 본다. 승인된 값은 믿을 수 없는 글이라
    공문서 기준(document=True)으로 보고, 차단뿐 아니라 검토도 뺀다. 지금 그래프의 값 44개는 모두 허용이다.
    """
    return bool(SAFE_VALUE.fullmatch(v)) and guard_check(v, document=True).decision == "허용"


def source_values(proc: Path = PROC) -> dict[str, set]:
    """프롬프트에 넣어도 되는 값의 원천 어휘: 역(URI, 이름), 행정동(URI, 이름), 건물 용도, 시설 유형.

    그래프를 만드는 원천 파일(src/graph/build_graph.py가 읽는 objects.gpkg, admin_dong.csv)에서 바로 읽으므로 승인으로 늘릴 수 없다.
    파일이 다시 만들어지면 새로 읽는다. 파일을 읽지 못하면 빈 어휘라 아무 값도 넣지 않는다.
    """
    files = (proc / "objects.gpkg", proc / "admin_dong.csv")
    try:
        stamp = tuple(f.stat().st_mtime_ns for f in files)
    except OSError:
        log.warning("원천 어휘 파일을 읽지 못해 프롬프트에 그래프 값을 넣지 않는다: %s", proc)
        return {"stations": set(), "dongs": set(), "uses": set(), "types": set()}
    return _source_values(str(proc), stamp)


@lru_cache(maxsize=2)
def _source_values(proc: str, stamp: tuple) -> dict[str, set]:
    gpkg = Path(proc) / "objects.gpkg"
    read = lambda layer, cols: gpd.read_file(gpkg, layer=layer, ignore_geometry=True, columns=cols).fillna("")
    stations = read("station", ["uri", "name"])
    dongs = pd.read_csv(Path(proc) / "admin_dong.csv", dtype=str, keep_default_na=False)
    return {"stations": set(zip(stations["uri"], stations["name"])), "dongs": set(zip(dongs["uri"], dongs["adm_dong_name"])),
            "uses": set(read("building", ["use_name"])["use_name"]) - {""},
            "types": set(read("public_facility", ["facility_type"])["facility_type"]) - {""}}


def graph_context(store, source: dict[str, set] | None = None) -> str:
    """질문에 자주 나오는 이름과 URI를 그래프에서 뽑아 프롬프트에 넣는다 (그래프가 바뀌면 같이 바뀐다).

    승인된 값이 시스템 프롬프트에 들어가지 않게 원천 어휘(source_values)에 있는 값만 넣고, 그 값도 safe_value, SAFE_URI를 거친다.
    역과 행정동은 URI와 이름이 함께 원천과 맞아야 한다. 빠진 값은 로그에 남는다. source는 시험에서 어휘를 바꿀 때 준다.
    """
    source = source_values() if source is None else source

    def rows(q):
        return [[t.value if t is not None else "" for t in r] for r in store.query(PREFIXES + q)]

    def values(found, allowed):
        kept = [v for v in found if v in allowed and safe_value(v)]
        if len(kept) < len(found):
            log.warning("프롬프트에 넣지 않은 그래프 값 %d개", len(found) - len(kept))
        return kept

    def named(found, allowed):
        kept = [(s, l) for s, l in found if (s, l) in allowed and SAFE_URI.fullmatch(s) and safe_value(l)]
        if len(kept) < len(found):
            log.warning("프롬프트에 넣지 않은 그래프 이름 %d개", len(found) - len(kept))
        return kept
    stations = named(rows("SELECT ?s ?l WHERE { ?s a jgk:Station ; rdfs:label ?l }"), source["stations"])
    dongs = named(rows("SELECT ?s ?l WHERE { ?s a jgk:AdminDong ; rdfs:label ?l } ORDER BY ?l"), source["dongs"])
    uses = values([u for u, _ in rows("SELECT ?u (COUNT(*) AS ?n) WHERE { ?b jgk:useName ?u } GROUP BY ?u ORDER BY DESC(?n) LIMIT 30")],
                  source["uses"])
    types = values([t for (t,) in rows("SELECT DISTINCT ?t WHERE { ?f jgk:facilityType ?t } ORDER BY ?t")], source["types"])
    return "\n".join([
        "역: " + ", ".join(f"{l} <{s}>" for s, l in stations),
        "행정동: " + ", ".join(f"{l} <{s}>" for s, l in dongs),
        "건물 용도(jgk:useName) 값: " + ", ".join(uses),
        "공공시설 유형(jgk:facilityType) 값: " + ", ".join(types),
        "상태: jgk:Active(사용), jgk:AddressUnlinked(주소 미연결), jgk:AddressLinked(주소 연결됨), jgk:Retired(폐지)",
    ])


def build_system(store) -> str:
    examples = "\n\n".join(q.read_text(encoding="utf-8") for q in sorted(QUERIES.glob("*.rq")))
    return f"""너는 증미역 공간 지식 그래프에 묻는 SPARQL을 쓴다. 저장소는 pyoxigraph이고 GeoSPARQL 위상 함수
(geof:sfIntersects, sfWithin, sfContains, sfTouches, sfOverlaps, sfDisjoint, sfEquals, sfCrosses)를 쓸 수 있다.
geof:distance와 geof:buffer는 없다.

규칙:
- SELECT 또는 ASK 쿼리 하나만 쓴다. PREFIX 줄은 쓰지 않는다 (jgk, geo, geof, rdfs, owl, prov, dcterms는 미리 붙인다).
- 지도에 그릴 수 있게 객체 변수(?f 등)와 그 모양 ?wkt (geo:hasDefaultGeometry/geo:asWKT)를 같이 고른다.
- 이름표가 있으면 rdfs:label을 OPTIONAL로 같이 고른다.
- 거리 조건("~에서 300m 안")은 {{{{NEAR:<기준 객체 URI>:<미터>}}}} 자리표시를 wktLiteral 자리에 따옴표 없이 쓴다.
  쿼리 하나에 {MAX_NEAR}개, {MAX_NEAR_METERS:,}m까지 쓸 수 있다.
  예: FILTER(geof:sfIntersects(?wkt, {{{{NEAR:https://yeodh10.github.io/jeungmi-geokg/id/station/1150010200106660040000001:300}}}}))
- 그래프에 없는 속성이나 값을 지어내지 않는다. 아래 어휘와 값 목록만 쓴다.

어휘 (Turtle):
{ONTOLOGY.read_text(encoding="utf-8")}

그래프에 있는 주요 이름과 값:
{graph_context(store)}

예시 쿼리:
{examples}"""


def scan(sparql: str) -> tuple[str, str]:
    """쿼리를 낱말로 나눠 (주석을 지운 쿼리, 금지어를 찾을 글)을 돌려준다.

    금지어를 찾을 글은 주석, 문자열, IRI, 거리 자리표시를 지운 것이다. 실행하는 글은 앞의 것이라 주석에 숨긴 코드는 함께 사라진다.
    """
    body, code, at = [], [], 0
    for m in TOKENS.finditer(sparql):
        if m.lastgroup == "bad":
            raise ValueError("따옴표는 닫아야 하고, IRI는 http(s)://로 시작해야 한다")
        body.append(sparql[at:m.start()])
        code.append(sparql[at:m.start()] + " ")
        if m.lastgroup != "comment":
            body.append(m.group())
        at = m.end()
    return "".join(body) + sparql[at:], "".join(code) + sparql[at:]


def check(sparql: str, max_rows: int = MAX_ROWS, max_chars: int = MAX_SPARQL_CHARS) -> str:
    if len(sparql) > max_chars:
        raise ValueError(f"쿼리가 너무 길다 ({len(sparql):,}자). {max_chars:,}자 안으로 써야 한다")
    body, code = scan(PREFIX_LINE.sub("", sparql))
    body = body.strip()
    words = NAME.sub(" ", VARIABLE.sub(" ", code))
    if "{{NEAR" in words:
        raise ValueError("거리 자리표시는 {{NEAR:객체URI:미터}} 꼴로 써야 한다")
    if "\\" in words:
        raise ValueError("문자열 밖에는 역슬래시를 쓸 수 없다")
    if FORBIDDEN.search(words):
        raise ValueError("읽기 쿼리(SELECT, ASK)만 실행한다")
    if not re.match(r"(?is)^\s*(SELECT|ASK)\b", body):
        raise ValueError("SELECT 또는 ASK로 시작해야 한다")
    tail = TAIL.search(body)
    if tail:  # 끝의 LIMIT이 너무 크면 max_rows로 줄인다 (OFFSET은 그대로 둔다)
        g = 1 if tail.group(1) else 2
        body = body[:tail.start(g)] + str(min(int(tail.group(g)), max_rows)) + body[tail.end(g):]
    elif body.upper().lstrip().startswith("SELECT"):
        body += f"\nLIMIT {max_rows}"
    return body


def readable(wkt: str):
    """WKT를 2차원 모양으로 읽는다. 읽지 못하거나, 지도에 그리는 종류가 아니거나, 비었거나, 어느 좌표 차원에든 숫자가 아닌 값(nan, inf)이
    있으면 None. 유한한 높이(Z)와 M 값은 버린다. 응답은 표준 JSON이라 NaN을 적을 수 없다 (src/graph/validate.py의 flat_shape)."""
    from src.graph.validate import flat_shape  # 검사 모듈은 무거워서(pyshacl) 처음 쓸 때 불러온다
    return flat_shape(wkt)


def near_area(store, uri: str, meters: str) -> str:
    """객체 모양 둘레의 원을 wktLiteral로. 쿼리 글을 만들지 않고 트리플을 바로 찾으므로 uri가 쿼리에 끼어들 수 없다."""
    try:
        node = ox.NamedNode(uri)
    except ValueError:
        raise ValueError(f"거리 기준 객체의 URI가 올바르지 않다: {uri}") from None
    if not 0 < float(meters) <= MAX_NEAR_METERS:
        raise ValueError(f"거리는 0보다 크고 {MAX_NEAR_METERS:,}m 이하여야 한다")
    default = ox.DefaultGraph()
    wkts = [w.object.value for g in store.quads_for_pattern(node, HAS_DEFAULT_GEOMETRY, None, default)
            if isinstance(g.object, (ox.NamedNode, ox.BlankNode))
            for w in store.quads_for_pattern(g.object, AS_WKT, None, default)]
    if not wkts:
        raise ValueError(f"거리 기준 객체를 그래프에서 찾지 못했다: {uri}")
    base = readable(min(wkts))
    if base is None:
        raise ValueError(f"거리 기준 객체의 모양을 읽지 못했다: {uri}")
    try:
        area = gpd.GeoSeries([base], crs="EPSG:4326").to_crs("EPSG:5186").buffer(float(meters)).to_crs("EPSG:4326").iloc[0]
    except shapely.errors.GEOSException:
        area = None
    if area is None or readable(area.wkt) is None:
        raise ValueError(f"거리 조건의 원을 만들지 못했다: {uri}")
    return f'"{area.wkt}"^^<{GEO}wktLiteral>'


def fill_near(sparql: str, store) -> str:
    """{{NEAR:URI:미터}}를 그 객체 모양 둘레의 원(WKT)으로 바꾼다. 거리는 EPSG:5186에서 잰다. 문자열과 주석 안의 자리표시는 그대로 둔다."""
    count = 0

    def repl(m):
        nonlocal count
        if m.lastgroup != "near":
            return m.group()
        count += 1
        if count > MAX_NEAR:
            raise ValueError(f"거리 자리표시는 쿼리 하나에 {MAX_NEAR}개까지 쓸 수 있다")
        return near_area(store, m.group("uri"), m.group("meters"))
    return TOKENS.sub(repl, sparql)


def geometry_vars(names: list[str], rows: list[list]) -> list[str]:
    """값이 WKT 모양으로 보이는 변수 이름. 화면도 이 목록으로 모양 열을 고른다."""
    return [n for i, n in enumerate(names) if any(isinstance(r[i], str) and WKT.match(r[i]) for r in rows)]


def run_sparql(store, sparql: str, seconds: float | None = None) -> dict:
    """쿼리를 확인하고 일꾼에서 돌린다. 하나 더 읽어 보고 MAX_ROWS를 넘어 잘렸는지 안다.

    거리 자리표시를 바꾼 글도 다시 확인한다. 실행하는 글은 언제나 check()를 지난 글이다.
    """
    body = fill_near(check(sparql, MAX_ROWS + 1), store)
    query = PREFIXES + check(body, MAX_ROWS + 1, MAX_FILLED_CHARS)
    result = pool.run(store, query, QUERY_SECONDS if seconds is None else seconds)
    return {**result, "geometry_vars": geometry_vars(result["vars"], result["rows"])}


def to_geojson(result: dict) -> dict:
    """WKT로 보이는 열이 있으면 그 행들을 지도용 GeoJSON으로 만든다. 지도에서 객체를 열 수 있게 uri를 넣는다."""
    rows = result.get("rows", [])
    gvars = result.get("geometry_vars")
    if gvars is None:
        gvars = geometry_vars(result.get("vars", []), rows)
    feats = []
    for row in rows:
        props = dict(zip(result["vars"], row))
        gv = next((k for k in gvars if isinstance(props.get(k), str) and WKT.match(props[k])), None)
        if gv is None:
            continue
        shape = readable(props[gv])
        if shape is None:  # 모양처럼 보이지만 읽을 수 없는 값은 지도에 그리지 않는다
            continue
        geom = mapping(shape)
        props = {k: x for k, x in props.items() if k != gv}
        if not props.get("uri"):
            props["uri"] = next((x for x in row if isinstance(x, str) and x.startswith(BASE)
                                 and not x.endswith("/geometry")), None)
        feats.append({"type": "Feature", "geometry": geom, "properties": props})
    return {"type": "FeatureCollection", "features": feats}


def ask(question: str, client=None, store=None, system: str | None = None) -> dict:
    """system을 주면 그 프롬프트를 쓴다 (검토 API는 그래프 판마다 한 번만 만들어 둔다)."""
    verdict = guard_check(question)
    if verdict.blocked:
        raise GuardBlocked("입력 가드가 막았다: " + ", ".join(verdict.reasons))
    store = store or load_store()
    client = client or get_client()
    system = system or build_system(store)
    if not prepare(store, READY_WAIT):  # 쿼리를 돌릴 수 없는데 유료 호출부터 하지 않는다
        raise QueryBusy("쿼리 일꾼을 준비하고 있다")
    prompt = question
    last_error = None
    for _ in range(2):
        draft = parse(client, SparqlDraft, system, prompt, effort="medium")
        try:
            result = run_sparql(store, draft.sparql)
            return {"question": question, "sparql": draft.sparql, "explanation": screen_output(draft.explanation, system_prompt=system)[0],
                    **result, "geojson": to_geojson(result), "names": {}}
        # pyoxigraph 쿼리 오류(문법은 SyntaxError, 없는 함수 같은 실행 오류는 RuntimeError, HTTP와 저장소는 OSError)와
        # check(), fill_near()의 ValueError만 내용을 붙여 한 번 더 쓰게 한다. 나머지는 그대로 올린다.
        except (SyntaxError, OSError, RuntimeError, ValueError) as e:
            last_error = e
            prompt = (f"질문: {question}\n\n앞서 쓴 쿼리:\n{draft.sparql}\n\n"
                      f"실행하니 오류가 났다: {e}\n오류를 고친 쿼리를 다시 써라.")
    raise ValueError(f"쿼리를 만들지 못했다: {last_error}")


def main() -> None:
    store = load_store()
    prepare(store, START_SECONDS)
    r = ask(" ".join(sys.argv[1:]), store=store)
    print(r["explanation"])
    print(r["sparql"])
    print(f"결과 {len(r['rows'])}행")
    for row in r["rows"][:20]:
        print(" | ".join(str(v)[:60] for v in row))


if __name__ == "__main__":
    main()
