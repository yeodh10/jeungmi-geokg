"""자연어 질문을 SPARQL로 바꿔 그래프에 묻고, 결과를 지도에 그릴 수 있게 돌려준다.

실행: python -m src.query.nl "증미역 300m 안에 있는 학교는?"

- LLM은 어휘 설명과 예시 쿼리(src/graph/queries)를 보고 SPARQL 한 개를 쓴다.
- 코드가 실행 전에 확인한다: 읽기 쿼리(SELECT, ASK)만 허용, 결과 수 제한(MAX_ROWS보다 많이 달라고 해도 줄인다).
- 거리 조건은 pyoxigraph에 geof:buffer가 없어서 LLM이 {{NEAR:객체URI:미터}} 자리표시를 쓰면 코드가 원 모양 WKT로 바꾼다.
- 쿼리가 실패하면 오류 내용을 붙여 한 번 더 고쳐 쓰게 한다.
- 쿼리는 QUERY_SECONDS 안에 끝나야 한다. pyoxigraph는 도는 쿼리를 멈출 수 없어서, 시간을 넘긴 쿼리는 뒤에서 끝까지 돌게 두고
  그동안 오는 질의는 QueryBusy로 받지 않는다 (무거운 쿼리가 쌓여 서버가 느려지지 않게).
"""
from __future__ import annotations

import itertools
import re
import sys
import threading
from pathlib import Path

import geopandas as gpd
import shapely
from pydantic import BaseModel, Field
from shapely.geometry import mapping

from src.graph.store import load_store
from src.llm.client import get_client, parse
from src.model.uri import BASE

ROOT = Path(__file__).resolve().parents[2]
QUERIES = ROOT / "src" / "graph" / "queries"
ONTOLOGY = ROOT / "src" / "graph" / "ontology.ttl"
MAX_ROWS = 500
QUERY_SECONDS = 20
FORBIDDEN = re.compile(r"\b(INSERT|DELETE|LOAD|CLEAR|DROP|CREATE|ADD|MOVE|COPY|SERVICE|WITH)\b", re.I)
NEAR = re.compile(r"\{\{NEAR:(\S+?):(\d+(?:\.\d+)?)\}\}")
WKT = re.compile(r"^(POINT|MULTIPOINT|LINESTRING|MULTILINESTRING|POLYGON|MULTIPOLYGON)")
# 금지어를 찾기 전에 지울 것: 문자열(세 겹 따옴표, 큰따옴표, 작은따옴표, \ 이스케이프), IRI, 주석(IRI와 문자열 밖의 #부터 줄 끝)
TOKENS = re.compile(
    r'(?P<str>"""(?:[^"\\]|\\.|"(?!""))*"""' r"|'''(?:[^'\\]|\\.|'(?!''))*'''"
    r'|"(?:[^"\\\n\r]|\\.)*"' r"|'(?:[^'\\\n\r]|\\.)*')"
    r'|(?P<iri><[^<>"{}|^`\\\s]*>)|(?P<comment>#[^\n\r]*)', re.S)
TAIL = re.compile(r"(?i)\b(?:LIMIT\s+(\d+)(?:\s+OFFSET\s+\d+)?|OFFSET\s+\d+\s+LIMIT\s+(\d+))\s*$")
PREFIXES = """PREFIX jgk: <https://yeodh10.github.io/jeungmi-geokg/def#>
PREFIX geo: <http://www.opengis.net/ont/geosparql#>
PREFIX geof: <http://www.opengis.net/def/function/geosparql/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX dcterms: <http://purl.org/dc/terms/>
"""


class QueryTimeout(Exception):
    """쿼리가 제한 시간을 넘겼다. 그 쿼리가 뒤에서 아직 돌고 있으니 ask()도 다시 쓰게 하지 않고 바로 올린다."""


class QueryBusy(Exception):
    """앞서 시간을 넘긴 쿼리가 아직 돌고 있다."""


_running = threading.BoundedSemaphore(1)  # 쿼리는 한 번에 하나만 돈다. 시간을 넘긴 쿼리가 끝나야 풀린다


class SparqlDraft(BaseModel):
    sparql: str = Field(description="PREFIX 없이 SELECT 또는 ASK로 시작하는 SPARQL 한 개")
    explanation: str = Field(description="어떻게 찾았는지 한국어 한두 문장")


def graph_context(store) -> str:
    """질문에 자주 나오는 이름과 URI를 그래프에서 뽑아 프롬프트에 넣는다 (그래프가 바뀌면 같이 바뀐다)."""
    def rows(q):
        return [[t.value if t is not None else "" for t in r] for r in store.query(PREFIXES + q)]
    stations = rows("SELECT ?s ?l WHERE { ?s a jgk:Station ; rdfs:label ?l }")
    dongs = rows("SELECT ?s ?l WHERE { ?s a jgk:AdminDong ; rdfs:label ?l } ORDER BY ?l")
    uses = rows("SELECT ?u (COUNT(*) AS ?n) WHERE { ?b jgk:useName ?u } GROUP BY ?u ORDER BY DESC(?n) LIMIT 30")
    types = rows("SELECT DISTINCT ?t WHERE { ?f jgk:facilityType ?t } ORDER BY ?t")
    return "\n".join([
        "역: " + ", ".join(f"{l} <{s}>" for s, l in stations),
        "행정동: " + ", ".join(f"{l} <{s}>" for s, l in dongs),
        "건물 용도(jgk:useName) 값: " + ", ".join(u for u, _ in uses),
        "공공시설 유형(jgk:facilityType) 값: " + ", ".join(t for (t,) in types),
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
- 거리 조건("~에서 300m 안")은 {{{{NEAR:<기준 객체 URI>:<미터>}}}} 자리표시를 wktLiteral 자리에 쓴다.
  예: FILTER(geof:sfIntersects(?wkt, {{{{NEAR:https://yeodh10.github.io/jeungmi-geokg/id/station/1150010200106660040000001:300}}}}))
- 그래프에 없는 속성이나 값을 지어내지 않는다. 아래 어휘와 값 목록만 쓴다.

어휘 (Turtle):
{ONTOLOGY.read_text(encoding="utf-8")}

그래프에 있는 주요 이름과 값:
{graph_context(store)}

예시 쿼리:
{examples}"""


def check(sparql: str, max_rows: int = MAX_ROWS) -> str:
    body = re.sub(r"(?im)^\s*PREFIX\s+\S+\s*<[^>]*>\s*$", "", sparql).strip()
    body = TOKENS.sub(lambda m: "" if m.group("comment") else m.group(), body).strip()  # 주석은 지운다
    blank = TOKENS.sub(lambda m: '""' if m.group("str") else "<>", body)
    words = re.sub(r"[?$]\w+", "?v", blank)  # 문자열, IRI, 변수 이름은 빼고 본다
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


def fill_near(sparql: str, store) -> str:
    """{{NEAR:URI:미터}}를 그 객체 모양 둘레의 원(WKT)으로 바꾼다. 거리는 EPSG:5186에서 잰다."""
    def repl(m):
        uri, meters = m.group(1).strip("<>"), float(m.group(2))
        q = f"SELECT ?w WHERE {{ <{uri}> <http://www.opengis.net/ont/geosparql#hasDefaultGeometry>/<http://www.opengis.net/ont/geosparql#asWKT> ?w }}"
        hit = [r["w"].value for r in store.query(q)]
        if not hit:
            raise ValueError(f"거리 기준 객체를 그래프에서 찾지 못했다: {uri}")
        area = gpd.GeoSeries([shapely.from_wkt(hit[0])], crs="EPSG:4326").to_crs("EPSG:5186") \
            .buffer(meters).to_crs("EPSG:4326").iloc[0]
        return f'"{area.wkt}"^^<http://www.opengis.net/ont/geosparql#wktLiteral>'
    return NEAR.sub(repl, sparql)


def geometry_vars(names: list[str], rows: list[list]) -> list[str]:
    """값이 WKT 모양으로 보이는 변수 이름. 화면도 이 목록으로 모양 열을 고른다."""
    return [n for i, n in enumerate(names) if any(isinstance(r[i], str) and WKT.match(r[i]) for r in rows)]


def run_sparql(store, sparql: str, seconds: float | None = None) -> dict:
    query = PREFIXES + fill_near(check(sparql, MAX_ROWS + 1), store)
    seconds = QUERY_SECONDS if seconds is None else seconds
    if not _running.acquire(timeout=min(seconds, 2)):
        raise QueryBusy("앞서 오래 걸린 쿼리가 아직 돌고 있다")
    box = {}

    def work():
        try:
            box["result"] = _execute(store, query)
        except BaseException as e:  # 오류는 부른 쪽에서 다시 올린다
            box["error"] = e
        finally:
            _running.release()
    t = threading.Thread(target=work, name="nl-query", daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise QueryTimeout(f"쿼리가 {seconds:g}초 안에 끝나지 않았다. 조건을 좁히거나 더 단순하게 써야 한다")
    if "error" in box:
        raise box["error"]
    return box["result"]


def _execute(store, query: str) -> dict:
    # 하나 더 읽어 보고 MAX_ROWS를 넘어 잘렸는지 안다
    res = store.query(query)
    if isinstance(res, bool) or res.__class__.__name__ == "QueryBoolean":
        return {"boolean": bool(res), "vars": [], "rows": [], "truncated": False, "geometry_vars": []}
    names = [v.value for v in res.variables]
    rows = [[(s[n].value if s[n] is not None else None) for n in names]
            for s in itertools.islice(res, MAX_ROWS + 1)]
    return {"vars": names, "rows": rows[:MAX_ROWS], "truncated": len(rows) > MAX_ROWS,
            "geometry_vars": geometry_vars(names, rows[:MAX_ROWS])}


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
        try:
            geom = mapping(shapely.from_wkt(props[gv]))
        except shapely.errors.GEOSException:  # 모양처럼 보이지만 읽을 수 없는 값은 지도에 그리지 않는다
            continue
        props = {k: x for k, x in props.items() if k != gv}
        if not props.get("uri"):
            props["uri"] = next((x for x in row if isinstance(x, str) and x.startswith(BASE)
                                 and not x.endswith("/geometry")), None)
        feats.append({"type": "Feature", "geometry": geom, "properties": props})
    return {"type": "FeatureCollection", "features": feats}


def ask(question: str, client=None, store=None, system: str | None = None) -> dict:
    """system을 주면 그 프롬프트를 쓴다 (검토 API는 그래프 판마다 한 번만 만들어 둔다)."""
    store = store or load_store()
    client = client or get_client()
    system = system or build_system(store)
    prompt = question
    last_error = None
    for _ in range(2):
        draft = parse(client, SparqlDraft, system, prompt, effort="medium")
        try:
            result = run_sparql(store, draft.sparql)
            return {"question": question, "sparql": draft.sparql, "explanation": draft.explanation,
                    **result, "geojson": to_geojson(result), "names": {}}
        # pyoxigraph 쿼리 오류(문법은 SyntaxError, 없는 함수 같은 실행 오류는 RuntimeError, HTTP와 저장소는 OSError)와
        # check(), fill_near()의 ValueError만 내용을 붙여 한 번 더 쓰게 한다. 나머지는 그대로 올린다.
        except (SyntaxError, OSError, RuntimeError, ValueError) as e:
            last_error = e
            prompt = (f"질문: {question}\n\n앞서 쓴 쿼리:\n{draft.sparql}\n\n"
                      f"실행하니 오류가 났다: {e}\n오류를 고친 쿼리를 다시 써라.")
    raise ValueError(f"쿼리를 만들지 못했다: {last_error}")


def main() -> None:
    r = ask(" ".join(sys.argv[1:]))
    print(r["explanation"])
    print(r["sparql"])
    print(f"결과 {len(r['rows'])}행")
    for row in r["rows"][:20]:
        print(" | ".join(str(v)[:60] for v in row))


if __name__ == "__main__":
    main()
