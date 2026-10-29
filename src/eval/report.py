"""평가 결과(data/processed/eval/*.json)를 docs/llm-eval.md로 정리한다.

실행: python -m src.eval.report
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "data" / "processed" / "eval"
OUT = ROOT / "docs" / "llm-eval.md"
KIND_KO = {"count": "개수", "entities": "객체 목록", "values": "값", "boolean": "있다 없다"}


def pct(a: int, b: int) -> str:
    return f"{a}/{b} ({a / b:.0%})" if b else "0/0"


def tokens(rows) -> dict:
    keys = ["input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"]
    return {k: sum(r["usage"][k] for r in rows) for k in keys}


def token_line(t: dict, calls: int) -> str:
    return (f"호출 {calls}번, 입력 {t['input_tokens']:,} (캐시에서 읽음 {t['cache_read_input_tokens']:,}, 캐시에 씀 "
            f"{t['cache_creation_input_tokens']:,}), 출력 {t['output_tokens']:,} 토큰")


def nl_section(rows: list[dict]) -> list[str]:
    n = len(rows)
    strict = sum(r["correct"] for r in rows)
    # 정답 포함: 값 문제는 정답 값이 모두 결과에 있으면(행이 많아도) 맞은 것으로 본다
    contains = sum(r["correct"] or (r["kind"] == "values" and r.get("recall") == 1.0) for r in rows)
    retried = sum(r["llm_calls"] > 1 for r in rows)
    out = ["## 1. 자연어 질의 (질문 -> SPARQL)", "",
           "질문마다 사람이 정답 SPARQL을 쓰고 지금 그래프에 돌려 정답을 정했다. 모델이 쓴 SPARQL을 실행한 결과가 정답과 같은지 본다(실행 정확도).",
           "문제와 정답 쿼리는 [eval/nl_questions.yaml](../eval/nl_questions.yaml)에 있다.", "",
           "| 항목 | 값 |", "|---|---|",
           f"| 엄격한 정답 | {pct(strict, n)} |",
           f"| 정답을 포함 (값 문제에서 행이 많아도 정답이 다 들어 있음) | {pct(contains, n)} |",
           f"| 오류를 보고 다시 쓴 질문 | {retried} |",
           f"| 걸린 시간 (질문 하나) | 평균 {sum(r['seconds'] for r in rows) / n:.1f}초, 가장 긴 것 {max(r['seconds'] for r in rows):.1f}초 |",
           f"| 토큰 | {token_line(tokens(rows), sum(r['llm_calls'] for r in rows))} |", "",
           "| 문제 | 종류 | 결과 | 호출 | 초 |", "|---|---|---|---|---|"]
    for r in rows:
        mark = "맞음" if r["correct"] else ("정답 포함" if r["kind"] == "values" and r.get("recall") == 1.0 else "틀림")
        out.append(f"| {r['id']} {r['question']} | {KIND_KO[r['kind']]} | {mark} | {r['llm_calls']} | {r['seconds']} |")
    misses = [r for r in rows if not r["correct"]]
    if misses:
        out += ["", "틀린 문제:"]
        for r in misses:
            why = r.get("error") or f"결과 {r.get('n_rows')}행, 정답 {r['gold']}"
            out.append(f"- {r['id']}: {why}")
    return out


def notice_section(rows: list[dict]) -> list[str]:
    ok = [r for r in rows if "error" not in r]
    quotes = [q for r in ok for q in r["quote_exact"]]
    grounded = [g for r in ok for g in r["grounded"]]
    gold = [r for r in ok if r["has_gold"]]
    spatial = [r["is_spatial_ok"] for r in gold if "is_spatial_ok" in r]
    targets = [t for r in gold for t in r.get("targets_found", [])]
    types = [r["change_types_ok"] for r in gold if "change_types_ok" in r]
    dates = [r["date_ok"] for r in gold if "date_ok" in r]
    out = ["## 2. 고시공고 추출 (글 -> 바뀌는 내용)", "",
           "강서구청 누리집의 실제 고시(제목에 '고시'가 든 글)를 운영과 같은 프롬프트로 넣었다. 원문은 커밋하지 않고 번호로만 적는다",
           "(`python -m src.eval.collect_notices`로 다시 받는다). 정답은 [eval/notice_gold.yaml](../eval/notice_gold.yaml)에 사람이 달았다.", "",
           "| 항목 | 값 |", "|---|---|",
           f"| 문서 | {len(rows)}건 (정답을 단 문서 {len(gold)}건, 오류 {len(rows) - len(ok)}건) |",
           f"| 근거 문장이 원문 그대로 | {pct(sum(quotes), len(quotes))} |",
           f"| 뽑은 주소, 지번이 원문에 있음 (지어내지 않음) | {pct(sum(grounded), len(grounded))} |",
           f"| 공간 문서 판단 | {pct(sum(spatial), len(spatial))} |",
           f"| 정답 대상(지번, 주소, 이름)을 뽑음 | {pct(sum(targets), len(targets))} |",
           f"| 변경 종류가 정답이 허용하는 범위 | {pct(sum(types), len(types))} |",
           f"| 효력 날짜 | {pct(sum(dates), len(dates))} |",
           f"| 걸린 시간 (문서 하나) | 평균 {sum(r['seconds'] for r in rows) / max(1, len(rows)):.1f}초 |",
           f"| 토큰 | {token_line(tokens(rows), len(rows))} |", ""]
    bad = [r for r in gold if not all([r.get("is_spatial_ok", True), all(r.get("targets_found", [])),
                                       r.get("change_types_ok", True), r.get("date_ok", True)])]
    if bad:
        out.append("정답과 다른 문서:")
        for r in bad:
            parts = [k for k in ("is_spatial_ok", "change_types_ok", "date_ok") if r.get(k) is False]
            if r.get("targets_found") and not all(r["targets_found"]):
                parts.append("targets")
            out.append(f"- {r['no']} {r['title']}: {', '.join(parts)}")
        out.append("")
    from collections import Counter
    kinds = Counter(c["change_type"] for r in ok for c in r["changes"])
    n_changes = sum(kinds.values())
    out += ["운영에서 본 것:", "",
            f"- 문서 {len(ok)}건 중 {sum(r['is_spatial'] for r in ok)}건을 공간 문서로 보고 {sum(bool(r['changes']) for r in ok)}건에서 변경 {n_changes}개를 뽑았다.",
            "- 변경 종류: " + ", ".join(f"{k} {v}" for k, v in kinds.most_common()) + ".",
            f"- {kinds.get('other', 0)}개({kinds.get('other', 0) / max(1, n_changes):.0%})가 other다. 정비사업 인가, 구역 지정, 계획 결정은 지금 종류(건물 멸실, 용도 변경, 주소 변경 등)에 맞는 칸이 없어서다.",
            "  other는 제안으로 이어지지 않으므로 사람이 볼 양은 줄지만, 구역(정비구역, 산사태취약지역, 지구단위계획구역)을 객체로 두려면 종류와 어휘를 넓혀야 한다.", ""]
    wrong_quotes = [r["no"] for r in ok if not all(r["quote_exact"])]
    ungrounded = [r["no"] for r in ok if not all(r["grounded"])]
    if wrong_quotes or ungrounded:
        out.append(f"원문과 다른 근거 문장이 있는 문서: {', '.join(wrong_quotes) or '없음'}. 원문에 없는 주소가 있는 문서: {', '.join(ungrounded) or '없음'}.")
    return out


def main() -> None:
    lines = ["# LLM 평가", "",
             "이 프로젝트에서 LLM이 맡은 일 두 가지(자연어 질문을 SPARQL로 바꾸기, 고시공고에서 바뀌는 내용 뽑기)를 실제 모델로 잰 결과다.",
             "모델은 src/llm/client.py의 설정(claude-opus-5-5, 구조화 출력, 시스템 프롬프트 캐시) 그대로다. 다시 재려면:", "",
             "```bash", ".venv/Scripts/python -m src.eval.nl_eval", ".venv/Scripts/python -m src.eval.collect_notices",
             ".venv/Scripts/python -m src.eval.notice_eval", ".venv/Scripts/python -m src.eval.report", "```", ""]
    nl, nt = EVAL / "nl_eval.json", EVAL / "notice_eval.json"
    if nl.exists():
        lines += nl_section(json.loads(nl.read_text(encoding="utf-8"))) + [""]
    if nt.exists():
        lines += notice_section(json.loads(nt.read_text(encoding="utf-8"))) + [""]
    lines += ["## 채점을 고친 기록", "",
              "결과를 처음 채점했을 때 틀린 것 5건을 하나씩 보니 모두 채점 쪽 문제여서 고쳤다. 모델을 다시 부르지 않고 저장된 결과를 다시 채점했다.",
              "",
              "- 자연어 질의 q18(정답이 빈 결과): 빈 정답일 때 결과가 3행까지 맞다고 보던 규칙을, 결과가 비어야 맞다로 고쳤다. 모델은 실제로 빈 결과를 내서 점수는 그대로다 (tests/test_eval.py가 이 규칙을 지킨다).",
              "- 고시 55402, 60941: 모델이 근거와 주소를 제목에서 가져왔는데 채점이 본문만 원문으로 봤다. 제목도 모델에 넣은 입력이라 원문에 넣었다 (처음 점수: 근거 문장 80/82, 주소 57/58).",
              "- 고시 68668(도로명주소 폐지): 폐지 목록이 첨부에만 있어 모델이 대상을 비웠는데, 정답이 변경을 하나 이상 요구했다. 정답 원칙(첨부에만 있는 내용은 넣지 않음)에 맞춰 이 문서의 종류 정답을 뺐다 (처음 점수: 변경 종류 8/9).",
              "- 자연어 질의 q13, q23은 고치지 않았다. 정답이 결과에 들어 있지만 행이 너무 많아(전체 순위, 구간마다 한 줄) 엄격한 기준으로는 틀린 것으로 둔다.",
              "",
              "## 한계", "",
              "- 자연어 질의 문제 25개는 한 사람이 이 그래프를 보고 썼다. 실제 사용자의 질문 분포와 다를 수 있다.",
              "- 고시공고 정답도 한 사람이 달았고 23건뿐이다. 정비사업 인가처럼 공간 객체가 '바뀌는' 문서인지 애매한 것은 공간 판단을 재지 않았다(null). 정답 문서가 적어 100%라는 값을 넓게 믿으면 안 된다.",
              "- 고시는 본문만 넣는다. 폐지조서 같은 첨부(엑셀, PDF)는 이 평가에 없고, 첨부표는 코드가 읽는다(src/review/address_retirement.py).",
              "- 비용은 토큰 수로만 적는다. 단가는 Anthropic 요금표를 따른다.", ""]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
