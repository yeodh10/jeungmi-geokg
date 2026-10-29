"""평가용 고시공고 원문을 강서구청 누리집에서 받는다.

실행: python -m src.eval.collect_notices [쪽수]   (기본 10쪽, '고시'로 제목 검색)
출력: data/raw/notices/eval/{mgtNo}.txt  (원문은 커밋하지 않는다. 정답은 eval/notice_gold.yaml에 번호로 적는다)
파일 머리에 제목, 번호, 날짜, 출처를 적어 src.llm.notice가 그대로 읽게 한다. 이미 받은 파일은 다시 받지 않는다.
"""
from __future__ import annotations

import html
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "raw" / "notices" / "eval"
SITE = "https://www.gangseo.seoul.kr"
LIST = SITE + "/gs040301?srchKey=sj&srchText=%EA%B3%A0%EC%8B%9C&curPage={page}"
VIEW = SITE + "/gs040301/view?srchKey=sj&mgtNo={no}"


def get(url: str, tries: int = 4) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (jeungmi-geokg eval)"})
    for i in range(tries):  # 누리집이 가끔 느리게 답해 읽다가 끊긴다
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
            break
        except (TimeoutError, OSError):
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))
    try:  # 같은 누리집이 요청에 따라 UTF-8이나 EUC-KR로 준다
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp949", "replace")


def text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def parse_view(page: str) -> dict:
    subject = text(re.search(r'<div class="subject">(.*?)</div>', page, re.S).group(1))
    info = dict(re.findall(r"<th>(.*?)</th>\s*<td>(.*?)</td>", page, re.S))
    body = re.search(r'<div class="view-content"[^>]*>(.*?)</div>\s*</div>', page, re.S)
    return {"title": subject, "number": text(info.get("고시공고번호", "")), "date": text(info.get("등록일자", "")),
            "body": text(body.group(1)) if body else ""}


def main() -> None:
    pages = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    OUT.mkdir(parents=True, exist_ok=True)
    nos = []
    for p in range(1, pages + 1):
        found = list(dict.fromkeys(re.findall(r"mgtNo=(\d+)", get(LIST.format(page=p)))))
        nos += [n for n in found if n not in nos]
        time.sleep(0.5)
    got = 0
    for no in nos:
        path = OUT / f"{no}.txt"
        if path.exists():
            continue
        v = parse_view(get(VIEW.format(no=no)))
        path.write_text(f"제목: {v['title']}\n번호: {v['number']}\n날짜: {v['date']}\n출처: {VIEW.format(no=no)}\n\n{v['body']}\n",
                        encoding="utf-8")
        got += 1
        time.sleep(0.5)
    print(f"목록 {len(nos)}건, 새로 받음 {got}건 -> {OUT}")


if __name__ == "__main__":
    main()
