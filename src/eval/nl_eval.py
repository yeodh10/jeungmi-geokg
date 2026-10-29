"""자연어 질의 평가: 모델이 쓴 SPARQL의 실행 결과가 사람이 쓴 정답 SPARQL의 결과와 같은지 본다 (실행 정확도).

실행: python -m src.eval.nl_eval [문제 id ...]   (Claude API 키 필요, 문제마다 1~2번 호출)
입력: eval/nl_questions.yaml
출력: data/processed/eval/nl_eval.json (문제마다 모델 SPARQL, 결과, 채점, 토큰, 걸린 시간)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import yaml

from src.graph.store import load_store
from src.llm.client import get_client
from src.model.uri import BASE
from src.query.nl import PREFIXES, ask, build_system, fill_near

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = ROOT / "eval" / "nl_questions.yaml"
OUT = ROOT / "data" / "processed" / "eval" / "nl_eval.json"
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


class Recording:
    """실제 클라이언트를 감싸 호출마다 토큰 사용량을 적는다."""

    def __init__(self, client):
        self.calls = []
        outer = self

        class Messages:
            def parse(self, **kw):
                r = client.beta.messages.parse(**kw)
                outer.calls.append({k: getattr(r.usage, k, 0) or 0 for k in USAGE_KEYS})
                return r

        class Beta:
            messages = Messages()

        self.beta = Beta()


def gold_answer(store, q: dict):
    res = store.query(PREFIXES + fill_near(q["gold"], store))
    if q["kind"] == "boolean":
        return bool(res)
    rows = [[t.value if t is not None else None for t in row] for row in res]
    if q["kind"] == "count":
        return int(float(rows[0][0]))
    return sorted({r[0] for r in rows})


def our_uris(rows) -> set[str]:
    return {v for r in rows for v in r if isinstance(v, str) and v.startswith(BASE) and not v.endswith("/geometry")}


def same_value(a: str, b: str) -> bool:
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def grade(q: dict, gold, result: dict) -> dict:
    rows = result.get("rows", [])
    cells = [v for r in rows for v in r if v is not None]
    if q["kind"] == "boolean":
        got = result["boolean"] if "boolean" in result else bool(rows)
        return {"correct": got == gold}
    if q["kind"] == "count":
        by_cell = any(same_value(c, str(gold)) for c in cells if isinstance(c, str) and c.replace(".", "", 1).isdigit())
        by_rows = len(our_uris(rows)) == gold and gold > 0 and not result.get("truncated")
        return {"correct": by_cell or by_rows}
    if q["kind"] == "entities":
        got = our_uris(rows)
        hit = len(got & set(gold))
        return {"correct": got == set(gold), "precision": round(hit / len(got), 3) if got else None,
                "recall": round(hit / len(gold), 3) if gold else None}
    # values
    if not gold:  # 정답이 비어 있으면 아무것도 내지 않아야 맞다 (없는 답을 지어내는지 본다)
        return {"correct": not rows, "recall": None}
    found = all(any(same_value(c, g) for c in cells) for g in gold)
    return {"correct": found and len(rows) <= 3 * max(1, len(gold)),
            "recall": round(sum(any(same_value(c, g) for c in cells) for g in gold) / len(gold), 3) if gold else None}


def main() -> None:
    only = set(sys.argv[1:])
    questions = [q for q in yaml.safe_load(QUESTIONS.read_text(encoding="utf-8")) if not only or q["id"] in only]
    store = load_store()
    system = build_system(store)
    rec = Recording(get_client())
    out = []
    for q in questions:
        gold = gold_answer(store, q)
        before = len(rec.calls)
        t0 = time.perf_counter()
        try:
            result = ask(q["question"], client=rec, store=store, system=system)
            row = {"sparql": result["sparql"], "rows": result.get("rows", [])[:20], "n_rows": len(result.get("rows", [])),
                   "boolean": result.get("boolean"), **grade(q, gold, result)}
        except Exception as e:  # 쿼리를 끝내 못 만든 것도 오답으로 적는다
            row = {"correct": False, "error": f"{type(e).__name__}: {e}"}
        calls = rec.calls[before:]
        row.update({"id": q["id"], "question": q["question"], "kind": q["kind"], "gold": gold,
                    "seconds": round(time.perf_counter() - t0, 1), "llm_calls": len(calls),
                    "usage": {k: sum(c[k] for c in calls) for k in USAGE_KEYS}})
        out.append(row)
        print(f"{q['id']} {'O' if row['correct'] else 'X'} {row['seconds']}s calls={len(calls)}", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    n = sum(r["correct"] for r in out)
    print(f"맞힘 {n}/{len(out)}")


if __name__ == "__main__":
    main()
