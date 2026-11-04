"""묻기 탭의 미리 실행한 답 questions.json (명세 3.7절, 9절).

config/questions.yaml의 사람이 쓴 쿼리를 공개 범위 그래프(src.graph.public.public_query_store)에서 실행한다.
값을 손으로 적지 않는다. 오류, 0행, 500행 초과는 빌드 실패다 (QuestionError).
거리 조건 {{NEAR:URI:미터}}는 src.query.nl.fill_near로 원을 넣어 실행하고, 화면에는 자리표시가 든 원래 쿼리를 보여 준다.
빈칸 ${이름}은 slots의 기본값으로 채운다. 다른 값으로 채운 답은 src.graph.askforms가 같은 run()으로 만든다.
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


SLOT = re.compile(r"\$\{([a-z]+)\}")
COMMA_SLOTS = ("households",)   # 화면 글에서 천 단위 쉼표를 붙이는 빈칸
UNSAFE = re.compile(r'["\\\n\r<>{}]')   # 쿼리에 넣는 글 값에 있으면 안 되는 글자 (쿼리 모양을 바꾸지 못하게)


class QuestionError(RuntimeError):
    pass


def slot_text(name: str, value, for_query: bool = False) -> str:
    """빈칸 값 하나를 글로. 쿼리에는 값 그대로, 화면 글에는 세대수처럼 큰 수에 쉼표를 붙인다."""
    if not for_query and name in COMMA_SLOTS and isinstance(value, int):
        return f"{value:,}"
    return str(value)


def fill_slots(text: str, values: dict, for_query: bool = False) -> str:
    """${이름}을 값으로 바꾼다."""
    def one(m):
        name = m.group(1)
        if name not in values:
            raise QuestionError(f"빈칸 {name}의 값이 없다")
        v = values[name]
        if for_query and isinstance(v, str) and UNSAFE.search(v):
            raise QuestionError(f"빈칸 {name}의 값에 쿼리에 넣을 수 없는 글자가 있다: {v!r}")
        return slot_text(name, v, for_query)
    return SLOT.sub(one, text)


def defaults(q: dict) -> dict:
    return dict(q.get("slots") or {})


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


def run(view: GraphView, qstore: ox.Store, q: dict, values: dict | None = None, max_rows: int = MAX_ROWS,
        allow_empty: bool = False) -> dict:
    """질문 하나를 빈칸 값(values, 없으면 기본값)으로 실행한다.

    기본값 실행(questions.json)은 0행과 max_rows 초과가 빌드 실패다. allow_empty면 0행은 none 문장의 답이 되고,
    max_rows를 넘으면 행 없이 {"big": 행 수}를 돌려준다 (키 없는 질문의 다른 빈칸 값).
    """
    values = defaults(q) if values is None else values
    names, rows = select(view, qstore, q["id"], fill_slots(q["sparql"], values, for_query=True))
    if not rows:
        if not allow_empty:
            raise QuestionError(f"{q['id']}: 결과가 0행이다")
        if not q.get("none"):
            raise QuestionError(f"{q['id']}: 0행일 때의 답(none)이 없다")
        return {"id": q["id"], "answer": fill_slots(q["none"], values), "n": 0, "rows": [], "map": []}
    if len(rows) > max_rows:
        if allow_empty:
            return {"id": q["id"], "big": len(rows)}
        raise QuestionError(f"{q['id']}: 결과가 {len(rows)}행으로 {max_rows}행을 넘는다")
    cols = list(q["columns"])
    map_names, map_rows = (select(view, qstore, q["id"], fill_slots(q["map_sparql"], values, for_query=True))
                           if q.get("map_sparql") else (names, rows))
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
    answer = re.sub(r"\{([a-z]+)\}", fill, fill_slots(q["answer"], values))
    return {"id": q["id"], "q": fill_slots(q["question"], values), "featured": bool(q.get("featured")), "answer": answer, "n": n,
            "cols": [{"key": c, "label": q["columns"][c]} for c in cols],
            "rows": [[r[idx[c]] for c in cols] for r in rows],
            "map": paths, "sparql": fill_slots(q["sparql"], values, for_query=True).rstrip() + "\n",
            **({"from": q["source"]} if q.get("source") else {})}


def build_questions(view: GraphView, qstore: ox.Store) -> dict:
    items = [run(view, qstore, q) for q in load_questions()]
    ids = [x["id"] for x in items]
    if len(set(ids)) != len(ids):
        raise QuestionError("질문 id가 겹친다")
    return {"v": 1, "note": "미리 실행한 답입니다. 자유 질문은 이 저장소의 로컬 서버에서만 할 수 있습니다.", "items": items}
