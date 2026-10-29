"""고시공고 추출 평가: 실제 강서구청 고시를 모델에 넣고, 뽑은 내용을 원문과 사람이 단 정답에 견준다.

실행: python -m src.eval.notice_eval [mgtNo ...]   (Claude API 키 필요, 문서마다 1번 호출)
      python -m src.eval.notice_eval --regrade      (모델을 다시 부르지 않고 저장된 추출 결과를 다시 채점)
입력: data/raw/notices/eval/{mgtNo}.txt (python -m src.eval.collect_notices로 받는다), eval/notice_gold.yaml
출력: data/processed/eval/notice_eval.json
제안 파일은 건드리지 않는다. 운영과 같은 프롬프트(src.llm.notice.SYSTEM)와 같은 출력 형식으로 추출만 한다.

원문만으로 재는 것 (모든 문서). 원문은 모델에 넣은 것 그대로(제목과 본문)다:
  quote_exact   뽑은 근거 문장(quote)이 원문에 그대로 있는가 (띄어쓰기는 무시)
  grounded      뽑은 도로명주소, 지번이 원문에 있는가 (지어낸 주소가 없는가)
정답으로 재는 것 (eval/notice_gold.yaml에 있는 문서):
  is_spatial    공간 객체가 바뀌는 문서인지 판단이 맞는가 (정답이 null이면 재지 않는다)
  targets       정답 대상(지번, 주소, 이름)을 뽑은 대상이 모두 담고 있는가
  change_types  뽑은 변경 종류가 정답이 허용하는 종류 안에 있는가
  date          정답 효력 날짜를 어느 변경이든 맞게 뽑았는가
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import yaml

from src.eval.nl_eval import USAGE_KEYS, Recording
from src.llm.client import get_client, parse
from src.llm.notice import SYSTEM, NoticeExtraction, read_notice

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "data" / "raw" / "notices" / "eval"
GOLD = ROOT / "eval" / "notice_gold.yaml"
OUT = ROOT / "data" / "processed" / "eval" / "notice_eval.json"


def squash(s: str | None) -> str:
    return re.sub(r"\s+", "", s or "")


def grade(ext: NoticeExtraction, body: str, gold: dict | None, title: str = "") -> dict:
    doc = squash(title) + squash(body)
    quotes = [squash(c.quote) in doc for c in ext.changes]
    addrs = [a for c in ext.changes for a in (c.target.road_address, c.target.jibun_address) if a]
    out = {"quote_exact": quotes, "grounded": [squash(a) in doc for a in addrs]}
    if gold is None:
        return out
    if gold.get("is_spatial") is not None:
        out["is_spatial_ok"] = ext.is_spatial == gold["is_spatial"]
    if gold.get("targets"):
        got = [squash(" ".join(filter(None, (c.target.road_address, c.target.jibun_address, c.target.name)))) for c in ext.changes]
        out["targets_found"] = [any(squash(t) in g for g in got) for t in gold["targets"]]
    if gold.get("change_types"):
        out["change_types_ok"] = bool(ext.changes) and all(c.change_type in gold["change_types"] for c in ext.changes)
    if gold.get("effective_date"):
        out["date_ok"] = any(c.effective_date == gold["effective_date"] for c in ext.changes)
    return out


def regrade(gold: dict) -> None:
    rows = json.loads(OUT.read_text(encoding="utf-8"))
    for r in rows:
        if "error" in r:
            continue
        meta, body = read_notice(DOCS / f"{r['no']}.txt")
        ext = NoticeExtraction(is_spatial=r["is_spatial"], changes=r["changes"])
        for k in ("quote_exact", "grounded", "is_spatial_ok", "targets_found", "change_types_ok", "date_ok"):
            r.pop(k, None)
        r.update(grade(ext, body, gold.get(r["no"]), meta.get("제목", "")), has_gold=r["no"] in gold)
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"다시 채점 {len(rows)}건")


def main() -> None:
    gold = {str(g["mgt"]): g for g in (yaml.safe_load(GOLD.read_text(encoding="utf-8")) or [])} if GOLD.exists() else {}
    if sys.argv[1:] == ["--regrade"]:
        regrade(gold)
        return
    only = set(sys.argv[1:])
    paths = [p for p in sorted(DOCS.glob("*.txt")) if not only or p.stem in only]
    rec = Recording(get_client())
    out = []
    for path in paths:
        meta, body = read_notice(path)
        before = len(rec.calls)
        t0 = time.perf_counter()
        try:
            ext = parse(rec, NoticeExtraction, SYSTEM, f"고시공고 제목: {meta.get('제목', '')}\n\n{body}")
            row = {"is_spatial": ext.is_spatial, "changes": [c.model_dump() for c in ext.changes],
                   **grade(ext, body, gold.get(path.stem), meta.get("제목", ""))}
        except Exception as e:  # 거절이나 형식 오류도 기록한다
            row = {"error": f"{type(e).__name__}: {e}"}
        calls = rec.calls[before:]
        row.update({"no": path.stem, "title": meta.get("제목"), "has_gold": path.stem in gold,
                    "seconds": round(time.perf_counter() - t0, 1), "usage": {k: sum(c[k] for c in calls) for k in USAGE_KEYS}})
        out.append(row)
        print(f"{path.stem} spatial={row.get('is_spatial')} changes={len(row.get('changes', []))} {row['seconds']}s", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
