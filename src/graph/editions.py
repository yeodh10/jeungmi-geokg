"""시점별 판: 승인된 변경 기록으로 그래프를 지난 시점으로 되돌려 묻는다.

그래프는 원천 자료로 만든 판 위에 사람이 승인한 변경을 승인 순서대로 얹은 것이다(src/graph/store.py).
승인된 제안은 바꾼 트리플을 rdf:Statement로 남기므로(jgk:hasChange), 어느 시각 T의 값은 이렇게 되살린다.
  - 지금 값 가운데, T보다 뒤에 승인된 변경이 넣은 것은 뺀다
  - T보다 뒤에 승인된 변경이 뺀 값은 되돌려 넣는다 (그 값이 T보다 뒤에 넣어진 것이 아니라면)
  - T보다 뒤에 승인된 제안을 가리키는 기록(prov:wasInfluencedBy)도 뺀다

실행: python -m src.graph.editions changes [시작] [끝]      두 시각 사이에 승인된 변경
      python -m src.graph.editions as-of <URI> <시각>        그 시각의 객체 값
시각은 2026-10-30T15:00:00+09:00 꼴이다. 시간대를 적지 않으면 한국 시간(+09:00)으로 본다.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

import pyoxigraph as ox

QUERIES = Path(__file__).resolve().parent / "queries"
CHANGES = (QUERIES / "15_changes_between.rq").read_text(encoding="utf-8")  # 두 시각 사이에 승인된 변경
AS_OF = (QUERIES / "16_as_of.rq").read_text(encoding="utf-8")  # 한 객체의 시각 T 값


def moment(v: str) -> str:
    """시각 문자열을 xsd:dateTime으로. 시간대가 없으면 +09:00을 붙인다(시간대가 있는 값과 없는 값은 SPARQL에서 견줄 수 없다)."""
    v = v.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        v += "T00:00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        v = dt.isoformat(timespec="seconds") + "+09:00"
    else:
        v = dt.isoformat(timespec="seconds")
    return v


def term(t) -> str:
    return t.value if hasattr(t, "value") else str(t)


def changes_between(store: ox.Store, since: str = "1900-01-01", until: str = "2999-12-31") -> list[dict]:
    q = CHANGES.replace("{{SINCE}}", moment(since)).replace("{{UNTIL}}", moment(until))
    names = ["decidedAt", "proposal", "action", "target", "op", "s", "p", "o"]
    return [{n: term(r[n]) for n in names} for r in store.query(q)]


def as_of(store: ox.Store, uri: str, at: str) -> dict[str, list[str]]:
    ox.NamedNode(uri)  # 쿼리에 넣기 전에 올바른 IRI인지 확인
    q = AS_OF.replace("{{URI}}", uri).replace("{{AT}}", moment(at))
    out: dict[str, list[str]] = {}
    for r in store.query(q):
        out.setdefault(term(r["p"]), []).append(term(r["o"]))
    return {k: sorted(set(v)) for k, v in out.items()}


def diff_states(before: dict[str, list[str]], after: dict[str, list[str]]) -> list[dict]:
    """두 시점의 값 차이: 속성마다 앞 값, 뒤 값."""
    rows = []
    for p in sorted(set(before) | set(after)):
        a, b = before.get(p, []), after.get(p, [])
        if a != b:
            rows.append({"p": p, "before": a, "after": b})
    return rows


def main() -> None:
    from src.graph.store import load_store
    args = sys.argv[1:]
    store = load_store()
    if args and args[0] == "changes":
        rows = changes_between(store, *(args[1:3]))
        for r in rows:
            print(f"{r['decidedAt']}  {r['action']:<7} {r['op']:<6} {r['s']} {r['p'].rsplit('#', 1)[-1]} {r['o']}")
        print(f"{len(rows)}줄")
    elif len(args) == 3 and args[0] == "as-of":
        for p, vs in as_of(store, args[1], args[2]).items():
            print(p.rsplit("#", 1)[-1].rsplit("/", 1)[-1], ", ".join(vs))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
