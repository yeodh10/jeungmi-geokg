"""원본에서 그래프, 검사, 공개 페이지까지 한 번에 다시 만든다.

실행: python -m src.pipeline            (원본을 이미 풀어 두었으면)
      python -m src.pipeline --unpack   (data/raw/_archives의 압축부터 풀 때)

단계마다 걸린 시간과 핵심 숫자를 보여 주고, data/processed/pipeline_report.json에 남긴다.
변경 제안 기록(data/processed/review/proposals.json)은 건드리지 않는다. 사람의 승인 기록이기 때문이다.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"


def step(name: str, fn, report: dict) -> None:
    t = time.perf_counter()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):  # 단계별 자세한 출력은 보고서에만 남긴다
        fn()
    took = round(time.perf_counter() - t, 1)
    report["steps"].append({"step": name, "seconds": took, "output": buf.getvalue()[-2000:]})
    print(f"[{took:>6.1f}초] {name}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--unpack", action="store_true", help="_archives의 압축 파일부터 다시 푼다")
    args = ap.parse_args()

    from src.graph import build_graph, check_queries, interop, publish, validate
    from src.ingest import clip_area, unpack
    from src.model import build_objects, build_sensors

    report = {"steps": []}
    t0 = time.perf_counter()
    if args.unpack:
        step("원본 압축 풀기", lambda: _unpack(unpack), report)
    step("대상 범위 자르기 (증미역 반경 1km)", clip_area.main, report)
    step("객체 쪼개기와 URI 붙이기", build_objects.main, report)
    if (ROOT / "data" / "raw" / "sdot" / "location.xlsx").exists():
        step("센서와 관측 (S-DoT, SOSA)", build_sensors.main, report)
    step("GeoSPARQL 그래프 만들기", build_graph.main, report)
    step("예시 SPARQL 쿼리 확인", check_queries.main, report)
    step("SHACL 데이터 규칙 검사", validate.main, report)
    step("상호운용성 검사 (바깥 주소 확인은 python -m src.graph.interop)", lambda: _interop_offline(interop), report)
    step("URI 공개 페이지 만들기", publish.main, report)
    report["seconds"] = round(time.perf_counter() - t0, 1)

    objects = json.loads((PROC / "objects_report.json").read_text(encoding="utf-8"))
    report["summary"] = {
        "건물": objects["building"]["total"], "출입구": objects["entrance"]["main"] + objects["entrance"]["sub"],
        "필지": objects["parcel"]["total"], "행정동": objects["admin_dong"], "역": objects["station"],
        "공공시설": objects["public_facility"]["with_agency_code"],
        "도로": objects["road"]["total"], "도로 구간": objects["road"]["segments"],
        "법정동": objects["legal_dong"], "기초구역": objects["basic_zone"],
        "국가기본도 건물": objects["basemap"].get("map_buildings", 0), "국가기본도와 이은 건물": objects["basemap"].get("linked", 0),
        "수치지형도 건물": objects["digital_map"].get("map_buildings", 0), "수치지형도와 이은 건물": objects["digital_map"].get("linked", 0),
    }
    sensors = PROC / "sensors_report.json"
    if sensors.exists():
        s = json.loads(sensors.read_text(encoding="utf-8"))
        report["summary"] |= {"센서": s["sensors_in_area"], "관측": s["observations"]}
    (PROC / "pipeline_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"끝 ({report['seconds']}초): " + ", ".join(f"{k} {v:,}" for k, v in report["summary"].items()))
    print("결과: data/processed/ (그래프 jeungmi_geokg.ttl, 검사 validation_report.md, 쿼리 query_check.md, 페이지 site/)")


def _interop_offline(interop_module) -> None:
    import sys
    argv = sys.argv
    sys.argv = [argv[0], "--offline"]
    try:
        interop_module.main()
    finally:
        sys.argv = argv


def _unpack(unpack_module) -> None:
    import sys
    argv = sys.argv
    sys.argv = [argv[0]]  # unpack.main은 명령줄 인자를 읽으므로 비워서 기본값으로 돌린다
    try:
        unpack_module.main()
    finally:
        sys.argv = argv


if __name__ == "__main__":
    main()
