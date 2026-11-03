"""객체 만들기와 어휘 확인: 없는 층수, 총괄표제부 중복, 제안이 쓰는 어휘.

실행: python -m tests.test_objects
앞쪽은 작은 표로 규칙을 보고, 뒤쪽은 data/processed의 실제 결과를 읽기만 한다.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
JGK = "https://yeodh10.github.io/jeungmi-geokg/def#"
SITE = ROOT / "src" / "graph"
COMPLEX_PNU = "1150010200106380000"  # 등촌동 638: 일반대장(공장)과 집합대장(공동주택 430세대)이 함께 있는 필지


def check_floor_counts() -> None:
    """지상, 지하가 모두 0이면 없는 값이다. 지하만 있는 건물의 지상 0은 실제 값이다."""
    from src.model.build_objects import floor_counts

    assert floor_counts("0", "0") == (None, None)
    assert floor_counts(0, None) == (None, None)
    assert floor_counts("", "") == (None, None)
    assert floor_counts("0", "3") == (0, 3)  # 지하철 역사
    assert floor_counts("5", "0") == (5, 0)
    assert floor_counts("5", "") == (5, None)


def check_split_register() -> None:
    """한 필지에 대장이 둘이면 세대수가 있는 집합대장을 쓰고 나머지는 검토 목록으로 뺀다."""
    import pandas as pd

    from src.model.build_objects import split_register

    reg = pd.DataFrame({"pnu": ["A", "A", "B", "C", "C"], "대장구분코드명": ["일반", "집합", "일반", "일반", "일반"],
                        "연면적": ["5000", "52000", "100", "10", "20"], "세대수": ["", "430", "", "", ""]})
    used, review = split_register(reg)
    assert sorted(used["pnu"]) == ["A", "B", "C"], used
    assert used.set_index("pnu").at["A", "세대수"] == "430", used
    assert used.set_index("pnu").at["C", "연면적"] == "20", used
    assert sorted(review["연면적"]) == ["10", "5000"], review


def check_vocabulary() -> None:
    """코드가 쓰는 어휘가 ontology.ttl에 정의돼 있고, 건물 관계를 개념 관계 아래에 두지 않는다."""
    from rdflib import OWL, RDF, RDFS, Graph, Namespace, URIRef

    g = Graph().parse(SITE / "ontology.ttl").parse(SITE / "alignment.ttl")
    jgk = Namespace(JGK)
    assert (jgk.sameBuildingAs, RDF.type, OWL.ObjectProperty) in g
    assert (jgk.sameBuildingAs, RDFS.domain, jgk.Building) in g and (jgk.sameBuildingAs, RDFS.range, jgk.Building) in g
    assert (jgk.changeOf, RDF.type, OWL.ObjectProperty) in g
    assert (jgk.changeOrder, RDF.type, OWL.DatatypeProperty) in g
    labels = [str(o) for s in (jgk.decidedBy, jgk.decidedAt) for o in g.objects(s, RDFS.label)]
    assert labels and not any("사람" in x for x in labels), labels
    assert "withdraw" in str(g.value(jgk.proposalAction, RDFS.comment))
    above = {str(o) for o in g.objects(jgk.bimBuilding, RDFS.subPropertyOf)}
    assert not {u for u in above if "skos" in u}, above  # skos:related 아래에 두면 추론이 건물을 skos:Concept으로 만든다
    assert (URIRef(JGK + "bimBuilding"), RDFS.subPropertyOf, RDFS.seeAlso) in g
    title = str(g.value(jgk["dataset-ngii-digital-map"], RDFS.label))
    assert "37608075" in title and "37608085" in title, title


def check_data() -> None:
    """실제 결과에 0층으로 적힌 없는 값이 없고, 중복 필지의 세대수가 남아 있다."""
    import json

    import geopandas as gpd
    import pandas as pd

    b = gpd.read_file(PROC / "objects.gpkg", layer="building")
    zero = b[b["floors_above"] == 0]
    assert (zero["floors_below"] > 0).all(), zero[["uri", "floors_above", "floors_below"]]
    assert len(zero) <= 9, len(zero)  # 지하철 역사와 지하 주차장뿐이다
    station = b[b["uri"].str.endswith("/building/1150010200106660040000001")].iloc[0]
    assert (station["floors_above"], station["floors_below"]) == (0, 3)
    parcels = pd.read_csv(PROC / "parcel.csv", dtype=str).set_index("pnu")
    assert parcels.at[COMPLEX_PNU, "households"] == "430", parcels.loc[COMPLEX_PNU]
    review = pd.read_csv(PROC / "review_register_duplicate.csv", dtype=str)
    assert len(review) == 3 and COMPLEX_PNU in set(review["pnu"]), review
    report = json.loads((PROC / "objects_report.json").read_text(encoding="utf-8"))
    assert report["parcel"]["with_complex_info"] == parcels["sources"].str.contains("register").sum() == 92, report["parcel"]
    assert report["building"]["gis_tiny_polygons_for_review"] == len(pd.read_csv(PROC / "review_gis_tiny.csv", dtype=str))


def check_shared_polygons_linked() -> None:
    """폴리곤을 함께 쓰는 건물이 지도 건물과 함께 이어진다. 한 폴리곤의 건물은 모두 이어지거나 모두 안 이어진다."""
    import geopandas as gpd
    import pandas as pd

    b = gpd.read_file(PROC / "objects.gpkg", layer="building")
    b = b[b["kind"].eq("building") & b["attr_source"].eq("gis")]
    links = pd.read_csv(PROC / "building_basemap.csv", dtype=str)
    linked = b["uri"].isin(links["building_uri"])
    shared = b.groupby("gis_building_id")["uri"].transform("size") > 1
    assert (linked[shared].groupby(b.loc[shared, "gis_building_id"]).nunique() == 1).all()
    assert linked[shared].sum() > 20, linked[shared].sum()  # 전에는 87동 중 20동만 이어졌다


def check_floor_query() -> None:
    """쿼리 13은 층수가 없는 건물과 지상층수 0을 견주지 않는다."""
    from src.graph.store import load_store

    store = load_store([])
    text = (SITE / "queries" / "13_basemap_floor_check.rq").read_text(encoding="utf-8").replace("LIMIT 20", "")
    rows = list(store.query(text))
    assert rows and all(float(r["registerFloors"].value) > 0 and float(r["mapFloors"].value) > 0 for r in rows)


def run() -> None:
    check_floor_counts()
    check_split_register()
    check_vocabulary()
    check_data()
    check_shared_polygons_linked()
    check_floor_query()
    print("객체와 어휘 확인 통과")


def test_objects():
    run()


if __name__ == "__main__":
    run()
