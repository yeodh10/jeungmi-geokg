"""도로명주소 폐지 고시의 첨부 '폐지조서'(엑셀 표)를 읽어 건물 폐지 제안을 만든다.

실행: python -m src.review.address_retirement data/raw/notices/attachments/20261028_폐지조서.xlsx \
        --notice data/raw/notices/20261028_도로명주소_폐지_고시.txt

표로 된 첨부는 LLM 없이 코드가 바로 읽는다 (본문 글만 LLM이 맡는다).
폐지조서 한 줄은 도로명주소 하나다. 주소가 없어지면 그 주소를 쓰는 건물이 모두 영향을 받으므로,
도로명주소로 찾은 건물과 지번(비고 칸)으로 찾은 건물이 똑같을 때만 그 건물 전부와 출입구를 폐지로 제안한다.
둘이 다르면 사람이 볼 수 있게 보고만 한다.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import openpyxl

from src.graph.store import load_store
from src.llm.notice import JGK, Resolver, current_values, location_of, read_notice, to_term
from src.review.proposals import Change, Proposal, ProposalStore, Term, now

XSD_DATE = "http://www.w3.org/2001/XMLSchema#date"
HEADER = ["연번", "행정동", "도로명주소", "폐지일", "폐지사유", "비고"]


def read_table(path: Path) -> list[dict]:
    """머리글 줄('연번 ...')을 찾아 그 아래 줄을 읽는다."""
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets[0]
    rows = [[("" if v is None else str(v).strip()) for v in r] for r in ws.iter_rows(values_only=True)]
    start = next(i for i, r in enumerate(rows) if r[:2] == HEADER[:2])
    out = []
    for r in rows[start + 1:]:
        if not any(r):
            continue
        out.append(dict(zip(HEADER, r[:len(HEADER)])))
    return out


def to_iso(d: str) -> str | None:
    m = re.match(r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})", d)
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


def retire_changes(store, uri: str, day: str) -> list[Change]:
    ch = [Change(op="remove", s=uri, p=JGK + "status", o=to_term(o)) for o in current_values(store, uri, JGK + "status")]
    return ch + [Change(op="add", s=uri, p=JGK + "status", o=Term(kind="uri", value=JGK + "Retired")),
                 Change(op="add", s=uri, p=JGK + "retiredOn", o=Term(kind="literal", value=day, datatype=XSD_DATE))]


def make(rows: list[dict], meta: dict, tag: str, store, resolver: Resolver) -> tuple[list[Proposal], list[dict]]:
    props, report = [], []
    for r in rows:
        by_road, by_jibun = set(resolver.by_road(r["도로명주소"])), set(resolver.by_jibun(r["비고"]))
        item = {**r, "도로명주소로 찾은 건물": len(by_road), "지번으로 찾은 건물": len(by_jibun), "제안": []}
        day = to_iso(r["폐지일"]) or meta.get("날짜")
        if not by_road and not by_jibun:
            item["결과"] = "범위 밖이거나 그래프에 없음"
        elif by_road != by_jibun:
            item["결과"] = "도로명주소와 지번으로 찾은 건물이 달라 사람이 확인해야 함"
        else:
            for b in sorted(by_road):
                changes = retire_changes(store, b, day)
                entrances = [o.value for o in current_values(store, b, JGK + "hasEntrance")]
                for e in entrances:  # 건물이 철거되면 출입구도 없어진다
                    changes += retire_changes(store, e, day)
                pid = f"table-{tag}-{b.rsplit('/', 1)[-1]}"
                props.append(Proposal(
                    id=pid, created_at=now(), source_kind="notice-table",
                    source_detail=f"{meta.get('제목', '')} 첨부 폐지조서 (표를 코드로 읽음)",
                    action="retire", target=b, target_type="Building",
                    summary=f"도로명주소 '{r['도로명주소']}' 폐지({r['폐지사유']})로 건물과 출입구 {len(entrances)}개를 폐지한다",
                    evidence=[f"고시공고: {meta.get('제목', '')} ({meta.get('번호', '')})",
                              f"폐지조서 {r['연번']}번: {r['행정동']}, {r['도로명주소']}, {r['폐지일']}, {r['폐지사유']}, {r['비고']}",
                              f"도로명주소와 지번이 같은 건물 {len(by_road)}개를 가리킨다",
                              f"출처: {meta.get('출처', '')}"],
                    changes=changes, location=location_of(store, b)))
                item["제안"].append(pid)
            item["결과"] = f"폐지 제안 {len(by_road)}건"
        report.append(item)
    return props, report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("table", type=Path)
    ap.add_argument("--notice", type=Path, required=True, help="같은 고시의 본문 파일 (제목, 번호, 날짜, 출처를 근거로 쓴다)")
    args = ap.parse_args()
    meta, _ = read_notice(args.notice)
    tag = re.sub(r"\D", "", meta.get("번호", ""))[-7:] or "notice"
    props, report = make(read_table(args.table), meta, tag, load_store(), Resolver())
    added = ProposalStore().add_new(props)
    for item in report:
        print(f"{item['연번']:>2} {item['도로명주소']:<16} {item['결과']}")
    print(f"제안 {len(props)}건, 새로 추가 {added}건")


if __name__ == "__main__":
    main()
