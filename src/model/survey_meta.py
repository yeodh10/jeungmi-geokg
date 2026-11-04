"""수치지형도 도엽의 ISO 19115-3 메타데이터(국토정보플랫폼이 성과와 함께 주는 XML)를 측량 성과 표로 읽는다.

실행: python -m src.model.survey_meta          (data/processed/survey_sheet.csv를 쓴다. src.model.build_objects도 부른다)
입력: data/raw/_archives/ngii_meta/*.xml        (내려받을 때 함께 오는 '(B010)수치지도_{도엽}_{연도}_{번호}.xml')
출력: data/processed/survey_sheet.csv           (도엽마다 한 줄. 사업명, 제작 구분, 고시번호, 촬영, 조사, 제작 연도, 작업 기간,
                                                 축척, 범위, 좌표계, 이용 제한, 갱신 주기, 작업규정)

메타데이터의 '요약' 칸에는 '고시번호 : 2026-1397', '촬영년도 : 2025' 같은 값이 글로 들어 있어 이름표로 찾는다.
좌표 범위, 작업 기간, 축척, 좌표계, 작업규정은 ISO 19115-3 요소에서 읽는다. 값을 찾지 못하면 비워 두고 지어내지 않는다.
기본측량성과라 공개 사이트에는 내지 않는다 (config/area.yaml hold_sources의 dataset-ngii-digital-map).
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
META_DIR = ROOT / "data" / "raw" / "_archives" / "ngii_meta"
PROC = ROOT / "data" / "processed"
COLUMNS = ["sheet", "project", "work_kind", "notice_no", "photo_year", "field_year", "made_year", "begin", "end", "scale",
           "west", "east", "south", "north", "crs", "use_limit", "maintenance", "spec", "spec_date", "file"]


def _texts(x: str) -> list[str]:
    """글자 값 요소(CharacterString, DateTime, Date)의 값 (문서 순서)."""
    return [v.strip() for v in re.findall(r"<gco:(?:CharacterString|DateTime|Date)>([^<]*)</gco:", x) if v.strip()]


def _labeled(texts: list[str], label: str) -> str | None:
    """'이름표 : 값' 꼴 글에서 값. 'null'은 값이 없는 것으로 본다."""
    for t in texts:
        m = re.fullmatch(rf"{label}\s*:\s*(.+)", t)
        if m and m.group(1).strip().lower() != "null":
            return m.group(1).strip()
    return None


def _inside(x: str, element: str, kind: str) -> str | None:
    """<...element><gco:kind>값</gco:kind> 꼴의 첫 값."""
    m = re.search(rf"{element}>\s*<gco:{kind}>([^<]+)</gco:{kind}>", x)
    return m.group(1).strip() if m else None


def read_sheet(path: Path) -> dict:
    """XML 해석기로 읽지 않는다. 받은 파일 가운데 태그 짝이 맞지 않는 것이 있어(2025년 판) 필요한 요소만 글로 찾는다."""
    x = path.read_text(encoding="utf-8")
    texts = _texts(x)
    title = next((t for t in texts if "도엽번호" in t), "")
    m = re.search(r"(.+?)\s*-\s*도엽번호\s*:\s*(\d{8})", title)
    instants = re.findall(r"<gml:(?:TM_Instant|timePosition)[^>]*>\s*(\d{4}-\d{2}-\d{2})", x)
    bound = lambda k, axis: _inside(x, f"{k}Bound{axis}", "Decimal")
    return {
        "sheet": m.group(2) if m else None, "project": m.group(1).strip() if m else None,
        "work_kind": _labeled(texts, "제작구분"), "notice_no": _labeled(texts, "고시번호"),
        "photo_year": _labeled(texts, "촬영년도"), "field_year": _labeled(texts, "조사년도"), "made_year": _labeled(texts, "제작년도"),
        "begin": min(instants) if instants else None, "end": max(instants) if instants else None,
        "scale": _inside(x, "denominator", "Integer"),
        "west": bound("west", "Longitude"), "east": bound("east", "Longitude"),
        "south": bound("south", "Latitude"), "north": bound("north", "Latitude"),
        "crs": next((t for t in texts if re.fullmatch(r"EPSG:\d+", t)), None),
        "use_limit": next((t for t in texts if "재배포" in t), None),
        "maintenance": next((t for t in texts if "변경 주기" in t), None),
        "spec": "수치지형도 작성 작업규정" if "수치지형도 작성 작업규정" in texts else None,
        "spec_date": next((t[:10] for t in texts if re.match(r"2019-\d{2}-\d{2}", t)), None),
        "file": path.name,
    }


def read_sheets(meta_dir: Path = META_DIR) -> pd.DataFrame:
    rows = [read_sheet(p) for p in sorted(meta_dir.glob("*.xml"))] if meta_dir.exists() else []
    df = pd.DataFrame(rows, columns=COLUMNS)
    return df[df["sheet"].notna()].drop_duplicates("sheet")


def main(proc: Path = PROC) -> pd.DataFrame:
    df = read_sheets()
    if len(df):
        df.to_csv(proc / "survey_sheet.csv", index=False, encoding="utf-8")
    print(f"측량 성과 메타데이터 {len(df)}건")
    return df


if __name__ == "__main__":
    main()
