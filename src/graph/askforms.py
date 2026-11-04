"""키 없는 질문의 자료 ask.json: 묻기 탭 입력칸이 Claude 없이 브라우저 안에서 답하게 한다.

질문 틀은 config/questions.yaml의 질문 가운데 forms(다른 말투)가 있는 것이다. 틀마다 빈칸(slots)이 있고,
빈칸이 받을 수 있는 값은 아래 DOMAINS가 정한다. 빈칸 값의 모든 조합마다 사람이 쓴 그 질문의 쿼리를
공개 범위 그래프에서 실행해 결과를 담는다 (src.graph.questions.run과 같은 함수). 브라우저(web/app/ask-match.js)는
  1) 입력에서 빈칸 값(거리, 층수, 용도, 법정동, 길 이름, 업종 등)을 찾아 자리표로 바꾸고
  2) 글자 조각(두 글자씩) 유사도로 가장 가까운 말투를 고르고
  3) 그 틀의 그 빈칸 조합에 담긴 결과를 보여 준다.
쿼리를 새로 만들거나 고르는 모델은 없다. 받을 수 없는 값(예: 350m)은 짐작하지 않고 고를 수 있는 값을 알려 준다.

크기: 같은 결과는 한 번만 담고(results), 장소 칸은 places.json의 건물 번호(i)로 적는다.
행이 ASK_MAX_ROWS를 넘는 조합은 행 없이 행 수만 담는다 (화면은 조건을 좁혀 달라고 한다).
"""
from __future__ import annotations

import itertools
import json
import time

import pyoxigraph as ox
import yaml

from src.graph.places import CONFIG
from src.graph.public import GraphView
from src.graph.questions import PREFIXES, QuestionError, defaults, load_questions, run

ASK_MAX_ROWS = 1000
DIST = list(range(100, 1001, 100))   # 거리 빈칸 (m). 범위가 역 둘레 1km라 그 안을 100m씩
# 이름 낱말 빈칸: 찾기 칩의 이름 낱말(config/use_groups.yaml)에 건물 이름에 흔한 끝말을 더한다
WORD_EXTRA = ["학교", "교회", "빌딩", "빌라", "맨션", "타워", "상가", "센터", "프라자", "병원", "유치원", "어린이집",
              "주차장", "주택", "하우스", "캐슬", "자이", "힐스테이트", "푸르지오", "래미안"]


def one_col(qstore: ox.Store, sparql: str) -> list:
    return [r[0].value if r[0] is not None else None for r in qstore.query(PREFIXES + sparql)]


def domains(qstore: ox.Store) -> dict[str, list]:
    """빈칸마다 받을 수 있는 값. 값 목록은 그래프에서 세어 정한다 (손으로 적는 것은 거리와 이름 낱말뿐)."""
    def ints(sparql):
        v = one_col(qstore, sparql)[0]
        return int(float(v)) if v is not None else 0
    max_f = ints("SELECT (MAX(?f) AS ?m) WHERE { ?b a jgk:Building ; jgk:floorsAbove ?f }")
    max_e = ints("SELECT (MAX(?k) AS ?m) WHERE { { SELECT ?b (COUNT(DISTINCT ?e) AS ?k) WHERE { ?b a jgk:Building ; jgk:hasEntrance ?e } GROUP BY ?b } }")
    max_p = ints("SELECT (MAX(?k) AS ?m) WHERE { { SELECT ?p (COUNT(DISTINCT ?x) AS ?k) WHERE { ?x a jgk:Building ; jgk:locatedOn ?p } GROUP BY ?p } }")
    max_h = ints("SELECT (MAX(?h) AS ?m) WHERE { ?p a jgk:Parcel ; jgk:households ?h }")
    years = one_col(qstore, "SELECT (MIN(?d) AS ?a) WHERE { ?b a jgk:Building ; jgk:approvalDate ?d }")[0]
    last = one_col(qstore, "SELECT (MAX(?d) AS ?a) WHERE { ?b a jgk:Building ; jgk:approvalDate ?d }")[0]
    chips = yaml.safe_load((CONFIG / "use_groups.yaml").read_text(encoding="utf-8"))["chips"]
    words = [w for c in chips if c["kind"] == "name" for w in c.get("words", [])]
    words += [w for w in WORD_EXTRA if w not in words]
    text = lambda sparql: sorted({v for v in one_col(qstore, sparql) if v})
    return {
        "dist": DIST,
        "floors": list(range(1, max_f + 1)),
        "entrances": list(range(2, max_e + 1)),
        "buildings": list(range(2, max_p + 1)),
        "households": list(range(100, max_h + 1, 100)),
        "year": list(range(int(years[:4]) + 1, int(last[:4]) + 2)),
        "use": text("SELECT DISTINCT ?u WHERE { ?b a jgk:Building ; jgk:useName ?u }"),
        "dong": text("SELECT DISTINCT ?u WHERE { ?b a jgk:Building ; jgk:inLegalDong/rdfs:label ?u }"),
        "road": text("SELECT DISTINCT ?u WHERE { ?b a jgk:Building ; jgk:addressRoad/rdfs:label ?u }"),
        "cat": text("SELECT DISTINCT ?u WHERE { ?s a jgk:Business ; jgk:categorySmall ?u }"),
        "word": words,
    }


def combo_key(values: dict, names: list[str]) -> str:
    return "|".join(str(values[n]) for n in names)


def build_ask(view: GraphView, qstore: ox.Store, places: dict, built: str = "") -> dict:
    t0 = time.perf_counter()
    dom = domains(qstore)
    index = {r["p"]: r["i"] for r in places["places"]}
    results, seen = [], {}

    def ref(c):
        """장소 칸: places.json에 있는 건물은 번호, 그 밖의 장소는 {p, n} 그대로."""
        return index.get(c["p"], c) if isinstance(c, dict) else c

    items = []
    for q in load_questions():
        if not q.get("forms"):
            continue
        names = list(defaults(q))
        for n in names:
            if n not in dom:
                raise QuestionError(f"{q['id']}: 빈칸 {n}이 받을 값을 정하지 않았다 (askforms.domains)")
            if q["slots"][n] not in dom[n]:
                raise QuestionError(f"{q['id']}: 빈칸 {n}의 기본값 {q['slots'][n]}이 받을 수 있는 값에 없다")
        combos, ref_cols = {}, set()
        for vals in itertools.product(*(dom[n] for n in names)):
            values = dict(zip(names, vals))
            r = run(view, qstore, q, values, max_rows=ASK_MAX_ROWS, allow_empty=True)
            key = combo_key(values, names)
            if "big" in r:
                combos[key] = {"big": r["big"]}
                continue
            rows = r["rows"]
            for row in rows:
                for j, c in enumerate(row):
                    if isinstance(c, dict):
                        ref_cols.add(j)
            body = {"n": r["n"], "rows": [[ref(c) for c in row] for row in rows],
                    "map": [index.get(p, p) for p in r["map"]]}
            k = json.dumps(body, ensure_ascii=False, sort_keys=True)
            if k not in seen:
                seen[k] = len(results)
                results.append(body)
            combos[key] = [seen[k], r["answer"]]
        items.append({"id": q["id"], "q": q["question"], "sparql": q["sparql"].rstrip() + "\n",
                      "slots": names, "defaults": defaults(q), "forms": q["forms"],
                      "cols": [{"key": c, "label": q["columns"][c], **({"ref": True} if j in ref_cols else {})}
                               for j, c in enumerate(q["columns"])],
                      "combos": combos, **({"from": q["source"]} if q.get("source") else {})})
    slots = {n: {"values": v} for n, v in dom.items()}
    return {"v": 1, "built": built, "maxRows": ASK_MAX_ROWS, "slots": slots, "items": items, "results": results,
            "seconds": round(time.perf_counter() - t0, 1)}
