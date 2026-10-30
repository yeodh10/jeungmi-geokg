"""그래프를 pyoxigraph에 올리고 src/graph/queries/*.rq를 차례로 실행해 결과를 남긴다.

실행: python -m src.graph.check_queries
출력: data/processed/query_check.md (쿼리마다 걸린 시간, 결과 행 수, 앞쪽 10행)
"""
from __future__ import annotations

import time
from pathlib import Path

import geopandas as gpd
import yaml
from shapely.geometry import Point

from src.graph.store import load_store

ROOT = Path(__file__).resolve().parents[2]
QUERIES = Path(__file__).resolve().parent / "queries"
OUT = ROOT / "data" / "processed" / "query_check.md"
SHOW_ROWS = 10


def station_area_wkt(radius_m: float) -> str:
    with open(ROOT / "config" / "area.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    x, y = cfg["center"]["epsg5186_xy"]
    circle = gpd.GeoSeries([Point(x, y).buffer(radius_m, quad_segs=16)], crs=cfg["crs"]["metric"])
    return circle.to_crs(cfg["crs"]["storage"]).iloc[0].wkt


def term(v) -> str:
    if v is None:
        return ""
    return v.value if hasattr(v, "value") else str(v)


def main() -> None:
    t0 = time.perf_counter()
    store = load_store()
    lines = [f"# SPARQL 확인 결과", "",
             f"- 트리플 수: {len(store):,}, 불러오기 {time.perf_counter() - t0:.1f}초", ""]
    params = {"{{AREA_WKT}}": station_area_wkt(200), "{{SINCE}}": "1900-01-01T00:00:00+09:00", "{{UNTIL}}": "2999-12-31T00:00:00+09:00",
              "{{URI}}": "https://yeodh10.github.io/jeungmi-geokg/id/building/1150010200106660040000001", "{{AT}}": "2026-10-01T00:00:00+09:00"}

    for q in sorted(QUERIES.glob("*.rq")):
        text = q.read_text(encoding="utf-8")
        for k, v in params.items():
            text = text.replace(k, v)
        t = time.perf_counter()
        res = store.query(text)
        names = [v.value for v in res.variables]
        rows = [[term(s[n]) for n in names] for s in res]
        took = time.perf_counter() - t
        lines += [f"## {q.stem}", "", f"{len(rows)}행, {took:.2f}초", ""]
        if rows:
            lines.append("| " + " | ".join(names) + " |")
            lines.append("|" + "---|" * len(names))
            for r in rows[:SHOW_ROWS]:
                lines.append("| " + " | ".join(c.replace("|", "/")[:120] for c in r) + " |")
        lines.append("")
        print(f"{q.stem}: {len(rows)} rows, {took:.2f}s")

    OUT.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
