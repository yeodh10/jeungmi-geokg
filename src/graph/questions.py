"""묻기 탭의 미리 실행한 답 questions.json (명세 3.7절, 9절).

config/questions.yaml의 사람이 쓴 쿼리를 공개 범위 그래프(src.graph.public.public_query_store)에서 실행한다.
값을 손으로 적지 않는다. 오류, 0행, 500행 초과는 빌드 실패다 (QuestionError).
거리 조건 {{NEAR:URI:미터}}는 src.query.nl.fill_near로 원을 넣어 실행하고, 화면에는 자리표시가 든 원래 쿼리를 보여 준다.
"""
from __future__ import annotations

import re

import pyoxigraph as ox
import yaml

from src.graph.places import CONFIG, path_of
from src.graph.public import BASE, GraphView, display_name, short_address

MAX_ROWS = 500
PREFIXES = """PREFIX jgk: <https://yeodh10.github.io/jeungmi-geokg/def#>
PREFIX geo: <http://www.opengis.net/ont/geosparql#>
PREFIX geof: <http://www.opengis.net/def/function/geosparql/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
"""
NUMERIC = ("integer", "decimal", "double", "float", "int", "long")


class QuestionError(RuntimeError):
    pass


def load_questions() -> list[dict]:
    return yaml.safe_load((CONFIG / "questions.yaml").read_text(encoding="utf-8")) or []


def cell(view: GraphView, term):
    """쿼리 값 -> 표 칸. 우리 장소는 {p: 경로, n: 화면 이름}, 숫자는 숫자, 그 밖은 글자. 값이 없으면 None."""
    if term is None:
        return None
    if isinstance(term, ox.NamedNode):
        if term.value.startswith(BASE + "/"):
            return {"p": path_of(term.value), "n": display_name(view, term.value)}
        return term.value
    v = term.value
    dt = getattr(term, "datatype", None)
    if dt is not None and dt.value.rsplit("#", 1)[-1] in NUMERIC:
        f = float(v)
        return int(f) if f.is_integer() else f
    return v


def text_of(c) -> str:
    if isinstance(c, dict):
        return c["n"]
    if isinstance(c, (int, float)):
        return f"{c:,}"
    return short_address(str(c)) if isinstance(c, str) and c.startswith("서울특별시 ") else str(c)


def select(view: GraphView, qstore: ox.Store, qid: str, sparql: str) -> tuple[list[str], list[list]]:
    from src.query.nl import fill_near   # 거리 자리표시를 원으로 (불러 쓰기만 한다)
    try:
        res = qstore.query(PREFIXES + fill_near(sparql, qstore))
        names = [v.value for v in res.variables]
        return names, [[cell(view, sol[n]) for n in names] for sol in res]
    except Exception as e:  # 쿼리 오류는 빌드를 멈춘다
        raise QuestionError(f"{qid}: 쿼리 실행 실패: {e}") from e


def run(view: GraphView, qstore: ox.Store, q: dict) -> dict:
    names, rows = select(view, qstore, q["id"], q["sparql"])
    if not rows:
        raise QuestionError(f"{q['id']}: 결과가 0행이다")
    if len(rows) > MAX_ROWS:
        raise QuestionError(f"{q['id']}: 결과가 {len(rows)}행으로 {MAX_ROWS}행을 넘는다")
    cols = list(q["columns"])
    map_names, map_rows = select(view, qstore, q["id"], q["map_sparql"]) if q.get("map_sparql") else (names, rows)
    for c in cols + ([q["count_var"]] if q.get("count_var") else []):
        if c not in names:
            raise QuestionError(f"{q['id']}: 쿼리에 없는 변수 {c}")
    if q["map_var"] not in map_names:
        raise QuestionError(f"{q['id']}: 지도 변수 {q['map_var']}가 쿼리에 없다")
    idx = {n: names.index(n) for n in names}
    count_var = q.get("count_var") or q["map_var"]
    key = lambda c: c["p"] if isinstance(c, dict) else c
    n = len({key(r[idx[count_var]]) for r in rows if r[idx[count_var]] is not None})
    paths = []
    for r in map_rows:
        c = r[map_names.index(q["map_var"])]
        if isinstance(c, dict) and c["p"] not in paths:
            paths.append(c["p"])
    if not paths:
        raise QuestionError(f"{q['id']}: 지도에 그릴 장소가 없다")
    first = rows[0]

    def fill(m):
        name = m.group(1)
        if name == "n":
            return f"{n:,}"
        if name in idx and first[idx[name]] is not None:
            return text_of(first[idx[name]])
        raise QuestionError(f"{q['id']}: 답 문장의 {{{name}}}를 채울 값이 없다")
    answer = re.sub(r"\{([a-z]+)\}", fill, q["answer"])
    return {"id": q["id"], "q": q["question"], "featured": bool(q.get("featured")), "answer": answer, "n": n,
            "cols": [{"key": c, "label": q["columns"][c]} for c in cols],
            "rows": [[r[idx[c]] for c in cols] for r in rows],
            "map": paths, "sparql": q["sparql"].rstrip() + "\n", **({"from": q["source"]} if q.get("source") else {})}


def build_questions(view: GraphView, qstore: ox.Store) -> dict:
    items = [run(view, qstore, q) for q in load_questions()]
    ids = [x["id"] for x in items]
    if len(set(ids)) != len(ids):
        raise QuestionError("질문 id가 겹친다")
    return {"v": 1, "note": "미리 실행한 답입니다. 자유 질문은 이 저장소의 로컬 서버에서만 할 수 있습니다.", "items": items}
