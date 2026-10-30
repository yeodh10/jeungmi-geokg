"""상호운용성 확인 (인터넷 없이): 정렬 파일이 어휘에 있는 이름만 쓰는지, 바깥 어휘로만 물어도 답이 나오는지,
형식을 바꿔도 트리플이 그대로인지.

실행: python -m tests.test_interop
data/processed/jeungmi_geokg.ttl이 있어야 한다. 바깥 주소가 실제로 열리는지는 python -m src.graph.interop이 확인한다.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JGK = "https://yeodh10.github.io/jeungmi-geokg/def#"


def run() -> None:
    from rdflib import Graph, URIRef

    from src.graph import interop

    onto = Graph().parse(ROOT / "src" / "graph" / "ontology.ttl")
    align = Graph().parse(ROOT / "src" / "graph" / "alignment.ttl")
    defined = {str(s) for s in onto.subjects() if str(s).startswith(JGK)}
    used = {str(x) for t in align for x in t if isinstance(x, URIRef) and str(x).startswith(JGK)}
    assert used and used <= defined, f"어휘에 없는 jgk 이름을 정렬에 썼다: {used - defined}"
    # 바깥 주소는 확인할 수 있는 곳만: schema.org, INSPIRE 등록부, 위키데이터 (그리고 표준 어휘 자체)
    allowed = ("https://schema.org/", "http://inspire.ec.europa.eu/featureconcept/", "http://www.wikidata.org/entity/",
               "http://www.w3.org/", "https://yeodh10.github.io/jeungmi-geokg/")
    bad = {str(x) for t in align for x in t if isinstance(x, URIRef) and not str(x).startswith(allowed)}
    assert not bad, f"확인할 수 없는 바깥 주소: {bad}"

    data = Graph().parse(interop.TTL)
    o = interop.outsider_view(data, onto, align)
    station = o["answers"]["지하철역(schema:SubwayStation)과 위키데이터"]
    assert len(station) == 1 and station[0][2] == "http://www.wikidata.org/entity/Q59989", station
    assert int(o["answers"]["주소(schema:address)가 있는 장소 수"][0][0]) >= 50
    assert len(o["answers"]["역이 속한 장소(schema:containedInPlace)"]) >= 1

    # 형식 바꾸기는 작은 조각으로 (역 둘레 트리플)
    small = Graph()
    for s in [interop.STATION, *data.objects(interop.STATION, None)]:
        for p, x in data.predicate_objects(s):
            small.add((s, p, x))
    rt = interop.round_trip(small)
    assert all(v["same"] for v in rt.values()), rt

    gs = interop.geosparql_shapes(data)
    assert gs["unparsable"] == 0 and gs["outside_korea_or_swapped"] == 0, gs
    print("상호운용성 확인 통과")


if __name__ == "__main__":
    run()
