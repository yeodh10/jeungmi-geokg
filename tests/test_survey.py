"""측량 성과 계보 확인: 도엽 메타데이터(ISO 19115-3) 읽기, 그래프의 성과와 작업, 건물에서 작업까지 거슬러 오르기, SHACL.

실행: python -m tests.test_survey
data/raw/_archives/ngii_meta/의 메타데이터와 data/processed/ 결과가 있어야 한다. 메타데이터가 없는 컴퓨터에서는 건너뛴다.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JGK = "https://yeodh10.github.io/jeungmi-geokg/def#"


def check_read() -> None:
    """메타데이터의 요약문 속 값과 ISO 요소 값을 읽는다. 태그 짝이 맞지 않는 파일도 읽힌다."""
    from src.model.survey_meta import read_sheets
    df = read_sheets().set_index("sheet")
    assert {"37608075", "37608085"} <= set(df.index), df.index
    r = df.loc["37608075"]
    assert (r.notice_no, r.work_kind, r.photo_year, r.begin, r.end, r.scale, r.crs) == (
        "2026-1397", "수정", "2025", "2025-03-25", "2025-12-30", "5000", "EPSG:5186"), r.to_dict()
    assert float(r.west) < float(r.east) and float(r.south) < float(r.north), r.to_dict()
    assert r.use_limit == "재배포 금지" and r.spec == "수치지형도 작성 작업규정" and r.spec_date == "2019-05-22", r.to_dict()


def check_graph() -> None:
    """건물에서 측량 작업까지 이어지고, 성과와 작업이 SHACL을 지나며, 측량 방법은 분류의 개념이다."""
    import pyoxigraph as ox
    from rdflib import Graph
    from pyshacl import validate

    from src.graph.build_graph import add_catalog, add_survey
    from src.graph.store import ONTOLOGY, load_store
    store = load_store()
    q = (ROOT / "src" / "graph" / "queries" / "17_survey_lineage.rq").read_text(encoding="utf-8")
    rows = [{k: r[k].value for k in ("sheet", "method", "photoYear", "buildings")} for r in store.query(q)]
    assert {r["sheet"] for r in rows} == {"37608075", "37608085"}, rows
    assert all(r["method"] == "항공사진측량" and r["photoYear"] == "2025" for r in rows), rows
    n = sum(int(r["buildings"]) for r in rows)
    linked = int(list(store.query(f"SELECT (COUNT(DISTINCT ?b) AS ?n) WHERE {{ ?b <{JGK}digitalMapFeature> ?m }}"))[0]["n"].value)
    assert n == linked, (n, linked)  # 수치지형도와 이은 건물은 모두 성과까지 거슬러 오른다

    g = Graph()
    add_survey(g, ROOT / "data" / "processed")
    add_catalog(g)
    g.parse(ONTOLOGY)
    keep = Graph()
    for s, p, o in g:  # 성과와 작업 노드만 검사한다 (수치지형도 건물 노드는 이 작은 그래프에 모양이 없다)
        if "/survey-" in str(s) or not str(s).startswith("https://yeodh10.github.io/jeungmi-geokg/id/"):
            keep.add((s, p, o))
    shapes = Graph().parse(ROOT / "src" / "graph" / "shapes.ttl")
    ok, _, text = validate(keep, shacl_graph=shapes, inference="none", allow_warnings=True)
    assert ok, text[:2000]
    assert isinstance(store, ox.Store)


def run() -> None:
    if not list((ROOT / "data" / "raw" / "_archives" / "ngii_meta").glob("*.xml")):
        print("도엽 메타데이터가 없어 건너뛴다 (data/raw/_archives/ngii_meta/)")
        return
    for fn in (check_read, check_graph):
        fn()
        print(f"통과 {fn.__name__}")
    print("측량 성과 계보 확인 통과")


def test_survey():
    run()


if __name__ == "__main__":
    run()
