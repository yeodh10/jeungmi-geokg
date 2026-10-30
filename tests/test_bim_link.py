"""BIM 연결 확인: BOT로 바꾼 BIM 모델의 건물을 규칙대로 그래프 건물에 잇는 제안을 만드는지.

실행: python -m tests.test_bim_link
이 건물들의 공개 IFC가 없어서, 시험용 BOT 그래프를 여기서 만든다(실제 BIM 자료가 아니다).
  A: 건물관리번호를 적은 증미역 역사 건물 (지하 3층)  -> 번호로 잇는다, 층 수 같음
  B: 번호 없이 위치만 있는 건물 (그래프 건물 모양 안의 점) -> 위치로 잇고 '확인 필요'
  C: 번호도 없고 범위 밖 위치 -> 잇지 않는다
  D: 그래프에 없는 건물관리번호 -> 잇지 않는다
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "data" / "processed" / "review" / "proposals.json"
STATION_BLDG = "1150010200106660040000001"


def run() -> None:
    import geopandas as gpd
    from rdflib import RDF, RDFS, Graph, Literal, Namespace, URIRef

    from src.graph.store import load_store
    from src.graph.validate import check_proposal
    from src.review.bim_proposals import BOT, GEO, JGK, make, match
    from src.review.proposals import Decision, ProposalStore

    b = gpd.read_file(ROOT / "data" / "processed" / "objects.gpkg", layer="building")
    station = b[b.bldg_mgt_no == STATION_BLDG].iloc[0]
    polys = b[b.geom_type != "Point"].to_crs("EPSG:5186")
    # B에 쓸 건물: 대표점을 품은 모양이 자기 하나뿐인 첫 건물
    pick = next(r for _, r in polys.iterrows() if polys.contains(r.geometry.representative_point()).sum() == 1)
    pick_pt = gpd.GeoSeries([pick.geometry.representative_point()], crs="EPSG:5186").to_crs("EPSG:4326").iloc[0]

    M = Namespace("https://example.org/bim-test/")
    g = Graph()

    def building(key, label, mgt=None, lonlat=None, storeys=()):
        node = M[key]
        g.add((node, RDF.type, BOT.Building))
        g.add((node, RDFS.label, Literal(label)))
        if mgt:
            g.add((node, JGK.buildingMgtNo, Literal(mgt)))
        if lonlat:
            geom = M[f"{key}-geom"]
            g.add((node, GEO.hasGeometry, geom))
            g.add((geom, GEO.asWKT, Literal(f"POINT ({lonlat[0]} {lonlat[1]})", datatype=GEO.wktLiteral)))
        for s in storeys:
            st = M[f"{key}-{s}"]
            g.add((node, BOT.hasStorey, st))
            g.add((st, RDFS.label, Literal(s)))

    building("A", "시험 역사", STATION_BLDG, (station.geometry.x, station.geometry.y), ["B1", "B2", "B3"])
    building("B", "시험 건물", None, (pick_pt.x, pick_pt.y), ["1F", "2F"])
    building("C", "범위 밖", None, (129.0, 35.1))
    building("D", "없는 번호", "9999999999999999999999999")

    found, missed = match(g)
    by = {r["iri"]: r for r in found}
    assert set(by) == {str(M.A), str(M.B)}, (list(by), missed)
    assert by[str(M.A)]["how"] == "번호" and by[str(M.A)]["target"].endswith(STATION_BLDG)
    assert by[str(M.B)]["how"] == "위치" and by[str(M.B)]["target"] == pick["uri"]
    assert {m["iri"] for m in missed} == {str(M.C), str(M.D)}

    props = {p.id: p for p in (make(r, "시험") for r in found)}
    a, bb = props["rule-bim-link-A"], props["rule-bim-link-B"]
    assert any("층 수: BIM 지상 0, 지하 3 / 그래프(대장) 지상 0, 지하 3" in e and "확인 필요" not in e for e in a.evidence), a.evidence
    assert any("위치로만 찾았다" in e for e in bb.evidence)

    store = load_store()
    for p in props.values():
        ok, res = check_proposal(store, p)
        assert ok, (p.id, res)

    # 승인하면 그래프 건물에서 BIM 건물로 jgk:bimBuilding이 생긴다
    tmp = Path(tempfile.mkdtemp()) / "proposals.json"
    shutil.copy(REAL, tmp)
    ps = ProposalStore(tmp)
    ps.add_new([a])
    items = ps.load()
    for p in items:
        if p.id == a.id:
            p.status, p.decision = "approved", Decision(result="approved", reviewer="시험", at="2026-10-30T12:00:00+09:00")
    ps.save(items)
    after = load_store(ps)
    import pyoxigraph as ox
    assert list(after.quads_for_pattern(ox.NamedNode(a.target), ox.NamedNode(str(JGK.bimBuilding)), ox.NamedNode(str(M.A)), None))
    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("BIM 연결 확인 통과")


if __name__ == "__main__":
    run()
