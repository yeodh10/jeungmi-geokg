"""시점별 판: 승인과 정정의 사건 기록으로 그래프를 지난 시점으로 되돌려 묻는다.

그래프는 원천 자료로 만든 판 위에 승인과 정정을 일련번호 순서로 얹은 것이다(src/graph/store.py).
그래프가 실제로 바뀐 변경만 사건(rdf:Statement, jgk:changeOf)으로 남으므로, 한 트리플의 사건은 넣기와 빼기가 번갈아 나온다.
그래서 어느 시각 T의 값은 트리플마다 이렇게 정한다.
  - 사건이 없는 트리플: 지금 값 그대로
  - T 이전 사건이 있으면: 그 가운데 마지막 사건이 넣기면 있고, 빼기면 없다
  - T 이전 사건이 없으면: 첫 사건의 반대다 (첫 사건이 빼기면 원래 있던 값이다)
승인을 정정으로 거둔 것도 사건이라, 거두기 전 시각의 값은 그대로 되살아난다.

실행: python -m src.graph.editions changes [시작] [끝]      두 시각 사이의 변경 (날짜만 준 끝 시각은 그날 끝이다)
      python -m src.graph.editions as-of <URI> <시각>        그 시각의 객체 값
시각은 2026-10-30T15:00:00+09:00 꼴이다. 시간대를 적지 않으면 한국 시간(+09:00)으로 본다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pyoxigraph as ox

from src.review.proposals import parse_time

QUERIES = Path(__file__).resolve().parent / "queries"
CHANGES = (QUERIES / "15_changes_between.rq").read_text(encoding="utf-8")  # 두 시각 사이의 변경
AS_OF = (QUERIES / "16_as_of.rq").read_text(encoding="utf-8")  # 한 객체의 시각 T 값
HAS_GEOMETRY = "http://www.opengis.net/ont/geosparql#hasGeometry"


def moment(v: str, end: bool = False) -> str:
    """시각 문자열을 xsd:dateTime으로. 시간대가 없으면 +09:00을 붙인다(시간대가 있는 값과 없는 값은 SPARQL에서 견줄 수 없다).

    날짜만 주면 그날 처음으로 본다. end면 그날 끝으로 본다.
    """
    v = v.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        v += "T23:59:59" if end else "T00:00:00"
    return parse_time(v).isoformat(timespec="seconds")


def term(t) -> str:
    return t.value if hasattr(t, "value") else str(t)


class Value(str):
    """화면에 보이는 글자에 N-Triples 표기(nt)를 더한 값. 글자가 같아도 언어 태그나 값 종류가 다르면 다른 값이다."""
    nt: str

    def __new__(cls, t):
        self = super().__new__(cls, term(t))
        self.nt = str(t)
        return self


def changes_between(store: ox.Store, since: str = "1900-01-01", until: str = "2999-12-31") -> list[dict]:
    """두 시각 사이의 변경을 일어난 순서대로. 승인을 거둔 정정은 action이 withdraw인 줄로 나온다."""
    q = CHANGES.replace("{{SINCE}}", moment(since)).replace("{{UNTIL}}", moment(until, end=True))
    names = ["decidedAt", "proposal", "action", "target", "op", "s", "p", "o"]
    return [{**{n: term(r[n]) for n in names}, "o_nt": str(r["o"])} for r in store.query(q)]


def state_at(store: ox.Store, uri: str, when: str) -> dict[str, dict[str, Value]]:
    """노드 하나의 시각 when 값: 속성 -> {N-Triples 표기: 값}."""
    out: dict[str, dict[str, Value]] = {}
    for r in store.query(AS_OF.replace("{{URI}}", uri).replace("{{AT}}", when)):
        v = Value(r["o"])
        out.setdefault(term(r["p"]), {})[v.nt] = v
    return out


def as_of(store: ox.Store, uri: str, at: str) -> dict[str, list[Value]]:
    """객체의 시각 at 값. 모양은 따로 된 노드라서 그 값도 'hasGeometry 속성' 이름으로 함께 준다 (모양 변경이 이력에 보이게)."""
    ox.NamedNode(uri)  # 쿼리에 넣기 전에 올바른 IRI인지 확인
    when = moment(at)
    state = state_at(store, uri, when)
    for geometry in list(state.get(HAS_GEOMETRY, {}).values()):
        if geometry.nt.startswith("<"):
            for p, values in state_at(store, geometry, when).items():
                state.setdefault(f"{HAS_GEOMETRY} {p}", {}).update(values)
    return {p: [values[k] for k in sorted(values)] for p, values in state.items()}


def diff_states(before: dict[str, list[str]], after: dict[str, list[str]]) -> list[dict]:
    """두 시점의 값 차이: 속성마다 앞 값, 뒤 값. N-Triples 표기로 견주고, 그 표기도 before_nt, after_nt로 준다."""
    nt = lambda values: [getattr(v, "nt", v) for v in values]
    rows = []
    for p in sorted(set(before) | set(after)):
        a, b = before.get(p, []), after.get(p, [])
        if nt(a) != nt(b):
            rows.append({"p": p, "before": a, "after": b, "before_nt": nt(a), "after_nt": nt(b)})
    return rows


def main() -> None:
    from src.graph.store import load_store
    args = sys.argv[1:]
    store = load_store()
    if args and args[0] == "changes":
        rows = changes_between(store, *(args[1:3]))
        for r in rows:
            print(f"{r['decidedAt']}  {r['action']:<8} {r['op']:<6} {r['s']} {r['p'].rsplit('#', 1)[-1]} {r['o_nt']}")
        print(f"{len(rows)}줄")
    elif len(args) == 3 and args[0] == "as-of":
        for p, vs in as_of(store, args[1], args[2]).items():
            print(p.rsplit("#", 1)[-1].rsplit("/", 1)[-1], ", ".join(vs))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
