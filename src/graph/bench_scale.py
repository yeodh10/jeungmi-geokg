"""그래프 저장소가 커질 때 무엇이 느려지는지 잰다 (docs/scale-design.md의 근거).

실행: python -m src.graph.bench_scale [배수 ...]   (기본 1 5 20)
지금 그래프(data/processed/jeungmi_geokg.ttl)를 배수만큼 복사해(URI 끝에 복사 번호를 붙여) 디스크 저장소에 넣고,
불러오기 시간, 저장소 크기, URI 하나 찾기, 속성 값으로 찾기, 모양으로 찾기(geof:sfIntersects) 시간을 잰다.
모양은 복사해도 같은 자리라서, 모양으로 찾기의 결과 수도 배수만큼 늘어난다.
결과: data/processed/bench_scale.json
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pyoxigraph as ox

from src.model.uri import BASE

ROOT = Path(__file__).resolve().parents[2]
TTL = ROOT / "data" / "processed" / "jeungmi_geokg.ttl"
OUT = ROOT / "data" / "processed" / "bench_scale.json"
STATION = f"{BASE}/station/1150010200106660040000001"
P = """PREFIX jgk: <https://yeodh10.github.io/jeungmi-geokg/def#>
PREFIX geo: <http://www.opengis.net/ont/geosparql#>
PREFIX geof: <http://www.opengis.net/def/function/geosparql/>
"""
# 증미역 둘레 약 200m 사각형 (경도, 위도)
WINDOW = "POLYGON((126.8590 37.5560, 126.8635 37.5560, 126.8635 37.5595, 126.8590 37.5595, 126.8590 37.5560))"
QUERIES = {
    "uri_lookup": f"SELECT ?p ?o WHERE {{ <{STATION}> ?p ?o }}",
    "value_lookup": P + 'SELECT ?b WHERE { ?b jgk:buildingMgtNo "1150010200106660040000001" }',
    "spatial_window": P + f"""SELECT (COUNT(?b) AS ?n) WHERE {{ ?b a jgk:Building ; geo:hasDefaultGeometry/geo:asWKT ?w .
  FILTER(geof:sfIntersects(?w, "{WINDOW}"^^geo:wktLiteral)) }}""",
}


def copies(n: int):
    """원래 그래프의 트리플을 n벌 만든다. 우리 URI(BASE로 시작)의 끝에 '-c{i}'를 붙여 객체가 겹치지 않게 한다."""
    base = ox.Store()
    base.load(path=str(TTL), format=ox.RdfFormat.TURTLE)
    quads = list(base.quads_for_pattern(None, None, None, None))
    rename = lambda t, i: ox.NamedNode(t.value + f"-c{i}") if i and isinstance(t, ox.NamedNode) and t.value.startswith(BASE) else t
    for i in range(n):
        for q in quads:
            yield ox.Quad(rename(q.subject, i), q.predicate, rename(q.object, i), q.graph_name)


def measure(n: int) -> dict:
    d = Path(tempfile.mkdtemp())
    try:
        store = ox.Store(str(d / "store"))
        t0 = time.perf_counter()
        store.bulk_extend(copies(n))
        store.flush()
        load = time.perf_counter() - t0
        store.optimize()
        size = sum(f.stat().st_size for f in (d / "store").rglob("*") if f.is_file())
        out = {"copies": n, "triples": len(store), "load_seconds": round(load, 1), "store_mb": round(size / 1e6, 1)}
        for name, q in QUERIES.items():
            store.query(q)  # 한 번 데우기
            t0 = time.perf_counter()
            res = list(store.query(q))
            out[name + "_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            if name == "spatial_window":
                out["spatial_window_hits"] = int(res[0]["n"].value)
        del store
        return out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main() -> None:
    ns = [int(a) for a in sys.argv[1:]] or [1, 5, 20]
    rows = []
    for n in ns:
        r = measure(n)
        print(json.dumps(r, ensure_ascii=False))
        rows.append(r)
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
