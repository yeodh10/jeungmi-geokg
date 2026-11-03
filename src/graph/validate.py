"""SHACL로 그래프의 데이터 규칙을 검사한다.

실행: python -m src.graph.validate
출력: data/processed/validation_report.md (규칙마다 어긴 개수와 예시)

검토 화면은 check_proposal()로 제안을 승인하기 전에, 그 제안을 반영한 뒤 바뀌는 객체가 규칙을 지키는지 본다.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

import numpy as np
import pyoxigraph as ox
import shapely
from pyshacl import validate
from rdflib import Graph, Namespace, URIRef

from src.graph.store import apply_problems, load_store
from src.model.uri import BASE

ROOT = Path(__file__).resolve().parents[2]
SHAPES = Path(__file__).resolve().parent / "shapes.ttl"
OUT = ROOT / "data" / "processed" / "validation_report.md"
SH = Namespace("http://www.w3.org/ns/shacl#")
# 대한민국 경도, 위도 범위. 저장 좌표는 CRS84(경도, 위도)다. 위경도가 뒤바뀌거나 평면 좌표를 넣으면 이 범위를 벗어난다
KOREA_LON, KOREA_LAT = (124.0, 132.0), (33.0, 39.0)
MAX_INBOUND = 200  # 바뀌는 객체로 들어오는 링크가 이보다 많으면 그 주어들은 검사하지 않는다 (행정동처럼 수천 건물이 가리키는 객체)
# 받는 WKT 모양 종류. GeoJSON이 그대로 그리는 종류다. LinearRing, GeometryCollection, 곡선 모양은 받지 않는다
SHAPE_TYPES = ("Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon")
SHAPE_NAMES = ", ".join(t.upper() for t in SHAPE_TYPES)
INVALID_KO = {"Self-intersection": "스스로 교차한다", "Ring Self-intersection": "고리가 스스로 교차한다",
              "Too few points in geometry component": "점이 모자란다", "Interior is disconnected": "안쪽이 끊겨 있다",
              "Hole lies outside shell": "구멍이 바깥 고리 밖에 있다", "Nested shells": "바깥 고리가 겹쳐 있다",
              "Nested holes": "구멍이 겹쳐 있다", "Duplicate Rings": "같은 고리가 둘이다", "Invalid Coordinate": "좌표가 숫자가 아니다",
              "Ring is not closed": "고리가 닫히지 않았다"}  # GEOS의 is_valid_reason 문장


def to_rdflib(store: ox.Store) -> Graph:
    data = store.dump(format=ox.RdfFormat.N_TRIPLES, from_graph=ox.DefaultGraph())
    g = Graph()
    g.parse(data=data.decode("utf-8"), format="nt")
    return g


@lru_cache(maxsize=1)
def shapes() -> Graph:
    g = Graph()
    g.parse(SHAPES, format="turtle")
    return g


def run(data: Graph) -> tuple[bool, list[dict]]:
    conforms, results, _ = validate(data, shacl_graph=shapes(), inference="none", allow_warnings=True)
    out = []
    for r in results.subjects(predicate=SH.resultSeverity):
        msg = results.value(r, SH.resultMessage)
        out.append({"focus": str(results.value(r, SH.focusNode)), "path": str(results.value(r, SH.resultPath) or ""),
                    "shape": str(results.value(r, SH.sourceShape) or ""), "message": str(msg or ""),
                    "value": str(results.value(r, SH.value) or "")})
    return bool(conforms), out


RDF_TYPE = ox.NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
RDF_SUBJECT = ox.NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#subject")
RDF_OBJECT = ox.NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#object")
GEO = "http://www.opengis.net/ont/geosparql#"
HAS_GEOMETRY = ox.NamedNode(GEO + "hasGeometry")
HAS_DEFAULT_GEOMETRY = ox.NamedNode(GEO + "hasDefaultGeometry")
AS_WKT = GEO + "asWKT"
WKT_LITERAL = GEO + "wktLiteral"
DCT_SOURCE = "http://purl.org/dc/terms/source"
JGK = "https://yeodh10.github.io/jeungmi-geokg/def#"
HAS_ENTRANCE = ox.NamedNode(JGK + "hasEntrance")
SAME_LINKS = {ox.NamedNode("http://www.w3.org/2002/07/owl#sameAs"), ox.NamedNode(JGK + "sameBuildingAs")}
INBOUND_RULES = SAME_LINKS | {HAS_ENTRANCE}  # 규칙이 들어오는 링크를 보는 속성. 언제나 검사 그래프에 넣는다
HISTORY_LINKS = {RDF_SUBJECT, RDF_OBJECT, ox.NamedNode(JGK + "changeOf"), ox.NamedNode(JGK + "affects"),
                 ox.NamedNode("http://www.w3.org/ns/prov#wasInfluencedBy")}  # 객체 관계가 아니라 제안과 변경 기록의 연결이다


def has_empty_part(geom) -> bool:
    """빈 부분(MULTIPOINT의 EMPTY, 빈 구멍 고리)이 있는지. GeoJSON으로 바꾸면 오류가 나거나 빈 좌표 목록이 된다."""
    parts = shapely.get_parts(geom)
    return bool(shapely.is_empty(parts).any() or shapely.is_empty(shapely.get_rings(parts)).any())


def finite(geom) -> bool:
    """모든 좌표 차원(경도, 위도, 높이 Z, M)이 유한한 수인지. 범위(bounds)는 NaN을 건너뛰고 재므로 좌표마다 본다."""
    coords = shapely.get_coordinates(geom, include_z=geom.has_z, include_m=bool(shapely.has_m(geom)))
    return bool(np.isfinite(coords).all())


def flat_shape(value: str | None):
    """WKT를 지도에 그릴 2차원 모양으로 읽는다. 그릴 수 없으면 None.

    None이 되는 값: 못 읽는 글, SHAPE_TYPES가 아닌 종류, 빈 모양이나 빈 부분, 어느 좌표 차원에든 숫자가 아닌 값(NaN, 무한대).
    유한한 높이(Z)와 M 값은 버리고 2차원으로 그린다. 지도 화면과 자연어 질의 응답이 쓴다.
    그래프에 이미 든 값이나 쿼리가 만든 값도 여기를 지난다. 높이나 M에만 NaN이 있어도 깨진 값으로 보고 그리지 않는다.
    """
    try:
        geom = shapely.from_wkt(value) if value else None
    except Exception:  # 못 읽는 글은 GEOSException, 곡선 모양은 NotImplementedError. 어떤 글이든 None으로 받는다
        return None
    if geom is None or geom.is_empty or geom.geom_type not in SHAPE_TYPES or has_empty_part(geom) or not finite(geom):
        return None
    return shapely.force_2d(geom)


def invalid_reason(geom) -> str:
    """GEOS가 올바르지 않다고 본 까닭을 한국어로. 모르는 까닭은 GEOS 문장 그대로 둔다."""
    reason = shapely.is_valid_reason(geom).split("[", 1)[0].strip()
    return INVALID_KO.get(reason, reason)


# OGC WKT 문법의 겉모양: 모양 이름(로마자), 괄호, 쉼표, 숫자, ASCII 공백만 쓴다. NUL 같은 제어 글자, 전각 숫자, 특수 공백,
# 뒤에 붙은 다른 글은 GEOS가 읽기 전에 막는다. GEOS는 NUL에서 읽기를 멈춰 그 뒤의 글을 보지 못한다
WKT_CHARS = re.compile(r"[ \t\r\n]*[A-Za-z][A-Za-z \t\r\n]*(?:\([A-Za-z0-9+\-., \t\r\n()]*\))?[ \t\r\n]*")
# 좌표 숫자의 꼴(OGC: 부호, 정수부, 소수부, 지수). 16진수(0x7E)나 '1.2.3'은 밖이다. NaN, 무한대는 꼴만 받고 finite()가 막는다
WKT_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?|[+-]?(?:nan|inf|infinity)", re.I)
WKT_WORD = re.compile(r"[A-Za-z]+")


def wkt_grammar_problem(value: str) -> str | None:
    """OGC WKT 문법 밖의 글자나 숫자 꼴이 있으면 그 까닭. 모양이 올바른지는 GEOS가 본다."""
    if not WKT_CHARS.fullmatch(value):
        return "WKT에 OGC 문법 밖의 글자가 있다(제어 글자, 전각 숫자, 뒤에 붙은 글 등)"
    for token in re.split(r"[ \t\r\n(),]+", value):
        if token and not WKT_WORD.fullmatch(token) and not WKT_NUMBER.fullmatch(token):
            return f"WKT 좌표가 OGC 숫자 꼴이 아니다({token[:20]!r})"
    return None


def wkt_problem(value: str) -> str | None:
    """WKT 값이 지도에 그릴 수 없는 값이면 그 까닭. 깨진 WKT 하나가 건물 레이어 전체를 내린다.

    받는 값: SHAPE_TYPES 종류, 경도와 위도 2차원, 모든 좌표가 유한한 수, 한국 범위 안, GEOS가 올바르다고 보는 모양.
    높이(Z)와 M 값은 받지 않는다. 저장 그래프의 모양 13,805개(2026-11-03)는 모두 2차원이고, 지도(GeoJSON)와 공간 질의도
    경도, 위도만 쓴다. 쓰지 않는 차원을 받으면 NaN, 무한대가 숨을 자리만 생긴다. 건물 높이는 층수 속성으로 적는다.
    숫자가 아닌 값은 모든 차원에서 먼저 본다. NaN이 든 선을 GEOS에 넘기면 '스스로 교차한다'처럼 틀린 까닭이 나온다.
    GEOS보다 먼저 OGC 문법(글자와 숫자 꼴)을 본다. GEOS는 NUL 뒤의 글을 보지 않고 16진 숫자도 읽어서, 받은 값이 그래프에 그대로 남는다.
    """
    shown = repr(value[:60])
    grammar = wkt_grammar_problem(value)
    if grammar:
        return f"{grammar}: {shown}"
    try:
        geom = shapely.from_wkt(value)
    except NotImplementedError:  # 곡선 모양(CIRCULARSTRING, COMPOUNDCURVE 등)
        return f"WKT 모양 종류는 {SHAPE_NAMES}만 받는다: {shown}"
    except Exception:  # GEOS가 못 읽는 글은 GEOSException이다. 어떤 글을 넣어도 오류 대신 까닭을 돌려준다
        return f"WKT를 읽을 수 없다(CRS 접두어 없이 적어야 한다): {shown}"
    if geom is None or geom.is_empty:
        return f"WKT가 비어 있다: {shown}"
    if geom.geom_type not in SHAPE_TYPES:  # LinearRing, GeometryCollection 등
        return f"WKT 모양 종류는 {SHAPE_NAMES}만 받는다: {shown}"
    if has_empty_part(geom):
        return f"WKT에 빈 부분(EMPTY)이 있다: {shown}"
    if not finite(geom):
        return f"WKT 좌표가 숫자가 아니다(NaN, 무한대, 높이 Z와 M 값까지 본다): {shown}"
    if geom.has_z or shapely.has_m(geom):
        return f"WKT는 경도, 위도 2차원만 받는다(높이 Z나 M 값을 넣지 않는다): {shown}"
    xy = shapely.get_coordinates(geom)
    (x0, y0), (x1, y1) = xy.min(axis=0), xy.max(axis=0)
    if x0 < KOREA_LON[0] or x1 > KOREA_LON[1] or y0 < KOREA_LAT[0] or y1 > KOREA_LAT[1]:
        return f"WKT 좌표가 한국 범위 밖이다(경도, 위도 순서여야 한다): {shown}"
    if not geom.is_valid:
        return f"WKT 모양이 올바르지 않다({invalid_reason(geom)}): {shown}"
    return None


def vocabulary_problem(proposal) -> str | None:
    """바꾸는 주어가 우리 객체(BASE/ 아래)가 아니면 그 까닭. 어휘(상태 이름표 등)와 제안 기록은 제안으로 바꿀 수 없다.

    어휘 노드는 SHACL 규칙이 보지 않는다. jgk:Active의 이름표를 빼면 레이어 쿼리가 사용 중 건물을 모두 놓친다.
    목적어로 어휘를 가리키는 것(상태를 jgk:Retired로 바꾸기 등)은 그대로 된다.
    """
    bad = sorted({c.s for c in proposal.changes if not c.s.startswith(BASE + "/") or c.s.startswith(BASE + "/proposal/")})
    if bad:
        return "어휘나 제안 기록은 제안으로 바꿀 수 없다. 바꾸는 주어는 객체(id 아래)여야 한다: " + ", ".join(bad[:3])
    return None


def change_problems(store: ox.Store, proposal) -> list[str]:
    """SHACL로 볼 수 없는, 변경 자체의 규칙."""
    out = list(apply_problems(store, proposal))
    if not proposal.changes:
        out.append("바꾸는 내용이 없는 제안이다")
    vocabulary = vocabulary_problem(proposal)
    if vocabulary:
        out.append(vocabulary)
    added_sources = {c.s for c in proposal.changes if c.op == "add" and c.p == DCT_SOURCE}
    for c in proposal.changes:
        if c.op == "remove" and c.p == RDF_TYPE.value:
            out.append(f"객체의 종류를 빼는 제안은 승인할 수 없다: {c.s}")
        elif c.op == "remove" and c.p == DCT_SOURCE and c.s.endswith("/geometry") and c.s not in added_sources:
            out.append(f"모양의 출처를 새 출처 없이 빼는 제안은 승인할 수 없다: {c.s}")  # 출처를 지우면 공개 보류를 비켜 간다
        elif c.op == "add" and (c.p == AS_WKT or c.o.datatype == WKT_LITERAL):
            problem = wkt_problem(c.o.value)
            if problem:
                out.append(problem)
    return out


class AfterChange:
    """제안을 반영한 뒤의 트리플을 지금 그래프와 변경 목록으로 읽는다. 그래프를 복사하지 않는다."""

    def __init__(self, store: ox.Store, proposal):
        self.store = store
        triples = [(c.op, ox.NamedNode(c.s), ox.NamedNode(c.p), c.o.to_ox()) for c in proposal.changes]
        self.removed = {t[1:] for t in triples if t[0] == "remove"}
        self.added = {t[1:] for t in triples if t[0] == "add"}

    def _pick(self, s, p, o) -> set[tuple]:
        now = {(q.subject, q.predicate, q.object) for q in self.store.quads_for_pattern(s, p, o, None)}
        new = {t for t in self.added if all(x is None or x == y for x, y in zip((s, p, o), t))}
        return (now - self.removed) | new

    def out(self, node, p=None) -> set[tuple]:
        return self._pick(node, p, None)

    def into(self, node) -> set[tuple]:
        return self._pick(None, None, node)


def focus_nodes(after: AfterChange, proposal) -> set:
    """검사할 노드: 모든 변경의 주어와 목적어, 그 노드로 들어오는 링크의 주어, 그리고 이들의 모양.

    목적어로만 나오는 객체(행정동처럼 수백 건물이 가리키는 객체)는 규칙이 보는 링크(INBOUND_RULES)의 주어만 넣는다.
    """
    subjects = {ox.NamedNode(proposal.target)} | {ox.NamedNode(c.s) for c in proposal.changes}
    objects = {c.o.to_ox() for c in proposal.changes if c.o.kind == "uri" and c.o.value.startswith(BASE + "/")}
    nodes = subjects | objects
    for n in subjects:
        inbound = {s for s, p, _ in after.into(n) if p not in HISTORY_LINKS and isinstance(s, ox.NamedNode)}
        if len(inbound) <= MAX_INBOUND:
            nodes |= inbound
    for n in subjects | objects:
        nodes |= {s for s, p, _ in after.into(n) if p in INBOUND_RULES}
    for n in list(nodes):
        for p in (HAS_GEOMETRY, HAS_DEFAULT_GEOMETRY):
            nodes |= {o for _, _, o in after.out(n, p)}
    return nodes


def check_proposal(store: ox.Store, proposal) -> tuple[bool, list[dict]]:
    """제안을 반영한 뒤의 모습을 규칙으로 검사한다. 지금 그래프는 바꾸지 않는다.

    전체를 다시 검사하면 1분이 넘게 걸려서, 바뀌는 노드 둘레(focus_nodes, 자기 트리플, 이어진 객체의 종류)만 떼어 검사한다.
    종류를 빼는 제안을 막으므로, 들어오는 링크의 주어를 다 검사하지 않아도 그 주어의 sh:class 규칙이 깨지지는 않는다.
    """
    trial = ox.Store()  # SPARQL이 실행되는지와 제안 노드의 기록만 빈 저장소에서 본다
    try:
        trial.update(proposal.sparql_update())
    except (SyntaxError, OSError, RuntimeError, ValueError) as e:  # 반영조차 못 하는 제안은 승인할 수 없다
        return False, [{"focus": proposal.target, "path": "", "shape": "", "value": "",
                        "message": f"제안을 그래프에 반영하지 못했다: {e}"}]
    problems = change_problems(store, proposal)
    after = AfterChange(store, proposal)
    nodes = focus_nodes(after, proposal)
    lines = {f"{q.subject} {q.predicate} {q.object} ." for q in trial.quads_for_pattern(ox.NamedNode(proposal.uri), None, None, None)}
    for f in nodes:
        for s, p, o in after.out(f):
            lines.add(f"{s} {p} {o} .")
            if isinstance(o, ox.NamedNode):  # sh:class 검사를 위해 이어진 객체의 종류도 넣는다
                lines |= {f"{a} {b} {c} ." for a, b, c in after.out(o, RDF_TYPE)}
        lines |= {f"{s} {p} {o} ." for s, p, o in after.into(f) if p in INBOUND_RULES}
    sub = Graph()
    sub.parse(data="\n".join(sorted(lines)), format="nt")
    conforms, results = run(sub)
    keep = {f.value for f in nodes} | {proposal.uri}
    results = [r for r in results if r["focus"] in keep]
    found = [{"focus": proposal.target, "path": "", "shape": "", "value": "", "message": m} for m in problems] + results
    return not found, found


def main() -> None:
    t0 = time.perf_counter()
    store = load_store()
    data = to_rdflib(store)
    conforms, results = run(data)
    took = time.perf_counter() - t0
    by_msg = Counter(r["message"] for r in results)
    examples = defaultdict(list)
    for r in results:
        if len(examples[r["message"]]) < 3:
            examples[r["message"]].append(r["focus"].replace("https://yeodh10.github.io/jeungmi-geokg/id/", "id:"))
    lines = ["# SHACL 검사 결과", "", f"- 트리플 {len(data):,}개, 규칙 파일 `src/graph/shapes.ttl`, {took:.0f}초",
             f"- 결과: {'모든 규칙 통과' if conforms else f'어긴 곳 {len(results)}건'}", ""]
    if results:
        lines += ["| 규칙 | 건수 | 예시 |", "|---|---|---|"]
        lines += [f"| {m} | {n} | {', '.join(examples[m])} |" for m, n in by_msg.most_common()]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"conforms": conforms, "violations": len(results), "by_message": by_msg, "seconds": round(took)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
