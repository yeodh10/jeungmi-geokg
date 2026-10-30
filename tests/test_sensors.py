"""센서와 관측(SOSA) 확인: URI 규칙, SHACL, 원본 평균과 그래프 평균이 같은지.

실행: python -m tests.test_sensors
data/processed/에 src.model.build_sensors와 src.graph.build_graph 결과가 있어야 한다.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"


def run() -> None:
    import pandas as pd
    from pyshacl import validate
    from rdflib import Graph

    from src.graph.build_graph import add_sensors
    from src.graph.publish import hold_sources
    from src.graph.store import load_store
    from src.model import uri

    # URI 규칙: 공식 시리얼 그대로, 관측은 시리얼-시각 12자리-항목
    assert uri.sensor("V02Q1940104").endswith("/id/sensor/V02Q1940104")
    assert uri.observation("V02Q1940104", "202610120007", "noise").endswith("/id/observation/V02Q1940104-202610120007-noise")
    for bad in (("V02Q1940104", "2026101200", "noise"), ("V02Q1940104", "202610120007", "no-ise")):
        try:
            uri.observation(*bad)
        except AssertionError:
            continue
        raise AssertionError(f"잘못된 관측 URI를 막지 못했다: {bad}")

    sensors = pd.read_csv(PROC / "sensor.csv", dtype=str, keep_default_na=False)
    obs = pd.read_csv(PROC / "sensor_obs.csv")
    assert len(sensors) >= 1 and obs.uri.is_unique and set(obs.sensor_uri) <= set(sensors.uri)

    # 관측이 있는 센서 하나로 작은 그래프를 만들어 SHACL 검사 (설치 주소의 건물은 이 작은 그래프에 없어 그 규칙은 뺀다)
    one = sensors[sensors.uri.isin(obs.sensor_uri)].iloc[:1]
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        one.to_csv(tmp / "sensor.csv", index=False)
        obs[obs.sensor_uri == one.uri.iloc[0]].to_csv(tmp / "sensor_obs.csv", index=False)
        g = Graph()
        add_sensors(g, tmp)
    g.parse(ROOT / "src" / "graph" / "ontology.ttl")
    shapes = Graph().parse(ROOT / "src" / "graph" / "shapes.ttl")
    _, _, text = validate(g, shacl_graph=shapes, inference="none", allow_warnings=True)
    bad = [line for line in text.splitlines() if "Message:" in line and "설치 주소의 건물" not in line]
    assert not bad, text[:2000]

    # 그래프에서 낸 평균이 원본 CSV 평균과 같다
    q = """PREFIX sosa: <http://www.w3.org/ns/sosa/> PREFIX jgk: <https://yeodh10.github.io/jeungmi-geokg/def#>
    SELECT ?s (AVG(?v) AS ?m) (COUNT(?o) AS ?n) WHERE { ?o sosa:madeBySensor ?s ; sosa:observedProperty jgk:noise ; sosa:hasSimpleResult ?v } GROUP BY ?s"""
    got = {r["s"].value: (float(r["m"].value), int(r["n"].value)) for r in load_store().query(q)}
    want = obs[obs.prop == "noise"].groupby("sensor_uri").value.agg(["mean", "count"])
    assert set(got) == set(want.index), (set(got), set(want.index))
    for s, (m, n) in got.items():
        assert n == want.loc[s, "count"] and abs(m - want.loc[s, "mean"]) < 1e-6, (s, m, want.loc[s].tolist())

    # 공공누리 1유형이라 공개 보류 목록에 없다
    assert not any("sdot" in h for h in hold_sources())
    print("센서와 관측 확인 통과")


if __name__ == "__main__":
    run()
