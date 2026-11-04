"""주소 미연결 건물을 잇는 2026-11-03 규칙 확인: 자리표시, 전자지도 모양, 범위 경계 건물, 옛 owl:sameAs 옮기기.

실행: python -m tests.test_address_link
실제 제안 파일은 임시 복사본으로만 쓴다. data/processed/에 전자지도 건물(emap_building)까지 잘라 둔 결과가 있어야 한다.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "data" / "processed" / "review" / "proposals.json"
JGK = "https://yeodh10.github.io/jeungmi-geokg/def#"
RULES = ("rule-placeholder-link-", "rule-emap-link-", "fix-same-building-")


def copy_without(dst: Path, prefixes: tuple[str, ...]) -> None:
    """실제 제안 파일을 복사하되 이 규칙들의 제안은 뺀다 (실제 파일에서 이미 결정됐어도 다시 뽑아 볼 수 있게)."""
    rows = json.loads(REAL.read_text(encoding="utf-8"))
    dst.write_text(json.dumps([r for r in rows if not r["id"].startswith(prefixes)], ensure_ascii=False, indent=2), encoding="utf-8")


def check_name_key() -> None:
    """이름과 동 표시를 나눠 견준다. 동 표시가 한쪽에만 있으면 같고, 양쪽에 다 있는데 다르면 다르다."""
    from src.review.placeholder_proposals import same_name
    assert same_name("루체씨티3동", "루체씨티") and same_name("PlusTwinvill (B)", "plusTwinvill")
    assert same_name("루체씨티1동", "루체씨티3동") is False and same_name("PlusTwinvill (A)", "PlusTwinvill (B)") is False
    assert same_name("101동", "102동") is False and same_name("주건축물제2동(B동)", "주건축물제1동(A동)") is False
    assert same_name("등촌중학교 다목적동", "등촌중학교") is False  # 끝이 숫자 동이 아니면 이름의 일부다
    assert same_name(None, "x") is None and same_name("  ", "x") is None


def check_edge_centers() -> None:
    """범위 밖 중심점은 범위에 걸친 GIS 건물 모양 안에 들 때만 더한다. 이미 들어온 건물은 다시 넣지 않는다."""
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import box

    from src.ingest.clip_area import edge_centers
    nb = pd.DataFrame({"bldg_mgt_no": ["in-shape", "outside", "already"],
                       "center_x": ["5", "50", "6"], "center_y": ["5", "50", "6"]})
    gis = gpd.GeoDataFrame({"A1": ["g"]}, geometry=[box(0, 0, 10, 10)], crs="EPSG:5179")
    centers = gpd.GeoDataFrame({"bldg_mgt_no": ["already"]}, geometry=gpd.points_from_xy([6], [6]), crs="EPSG:5179")
    got = edge_centers(nb, centers, gis, "EPSG:5179")
    assert list(got["bldg_mgt_no"]) == ["in-shape"], got


def check_diff_bundles_entrance() -> None:
    """새 건물의 새 출입구는 건물 제안에 함께 들어가고, 그 제안 하나로 SHACL 연결 규칙을 지난다."""
    from rdflib import RDF, Graph, URIRef

    from src.review.diff_proposals import GEO, diff
    J = lambda v: URIRef(JGK + v)
    b, e = URIRef("https://yeodh10.github.io/jeungmi-geokg/id/building/x"), URIRef("https://yeodh10.github.io/jeungmi-geokg/id/entrance/x-main")
    new = Graph()
    for s, cls in ((b, J("Building")), (e, J("BuildingEntrance"))):
        new.add((s, RDF.type, GEO.Feature))
        new.add((s, RDF.type, cls))
    new.add((b, J("hasEntrance"), e))
    props = diff(Graph(), new, "t", "시험")
    assert [p.action for p in props] == ["create"] and props[0].target == str(b), [p.id for p in props]
    assert {c.s for c in props[0].changes} == {str(b), str(e)}, props[0].changes
    assert any("새 출입구 1개" in v for v in props[0].evidence), props[0].evidence


def check_diff_after_approvals() -> None:
    """승인으로 만든 객체는 다음 갱신에서 옛 값이 있던 것으로 견준다. 있던 건물의 새 출입구도 건물 제안에 함께 든다.
    한 GIS 모양을 새 건물 둘이 쓰면 실행마다 같은 건물을 고르고 확인 필요로 둔다."""
    from rdflib import RDF, Graph, Literal, URIRef

    from src.review.diff_proposals import AMBIGUOUS_LINK, DCTERMS, GEO, diff
    from src.review.proposals import Change, Decision, Proposal, Term
    J = lambda v: URIRef(JGK + v)
    ID = "https://yeodh10.github.io/jeungmi-geokg/id/"
    src = J("dataset-a")

    def building(g, s, floors=None, gis=None):
        for t in (GEO.Feature, J("Building")):
            g.add((s, RDF.type, t))
        g.add((s, DCTERMS.source, src))
        if floors is not None:
            g.add((s, J("floorsAbove"), Literal(floors)))
        if gis:
            geom = URIRef(f"{s}/geometry")
            g.add((s, GEO.hasGeometry, geom))
            g.add((geom, J("sourceGisBuildingId"), Literal(gis)))

    made = URIRef(ID + "building/made")
    created = Proposal(id="diff-x-create-made", created_at="2026-11-03T00:00:00+09:00", source_kind="data-diff", source_detail="시험",
                       action="create", target=str(made), target_type="Building", summary="시험", status="approved",
                       decision=Decision(result="approved", reviewer="시험", at="2026-11-03T00:00:00+09:00", seq=1),
                       changes=[Change(op="add", s=str(made), p=str(RDF.type), o=Term(kind="uri", value=str(GEO.Feature))),
                                Change(op="add", s=str(made), p=str(RDF.type), o=Term(kind="uri", value=JGK + "Building")),
                                Change(op="add", s=str(made), p=str(DCTERMS.source), o=Term(kind="uri", value=str(src))),
                                Change(op="add", s=str(made), p=JGK + "floorsAbove", o=Term(kind="literal", value="3",
                                       datatype="http://www.w3.org/2001/XMLSchema#integer"))])
    old, keep = Graph(), URIRef(ID + "building/keep")
    building(old, keep)
    new = Graph()
    building(new, keep)
    building(new, made, floors=8)
    e = URIRef(ID + "entrance/keep-main")
    new.add((keep, J("hasEntrance"), e))
    for t in (GEO.Feature, J("BuildingEntrance")):
        new.add((e, RDF.type, t))
    props = {p.target: p for p in diff(old, new, "t", "시험", approved=[created])}
    assert set(props) == {str(made), str(keep)} and props[str(made)].action == "update", {k: p.action for k, p in props.items()}
    ops = {(c.op, c.o.value) for c in props[str(made)].changes}
    assert ("remove", "3") in ops and ("add", "8") in ops, ops  # 승인 때 넣은 값을 빼고 새 값을 넣는다
    assert props[str(keep)].action == "update" and {c.s for c in props[str(keep)].changes} == {str(keep), str(e)}

    gone = Graph()
    building(gone, keep)
    assert [p.action for p in diff(old, gone, "t", "시험", approved=[created])] == ["retire"]  # 승인으로 만든 건물도 폐지된다

    # 있던 건물의 update에 함께 넣어 승인한 새 출입구도, 다음 자료에서 빠지면 폐지 제안이 나온다
    bundle = props[str(keep)].model_copy(update={"status": "approved", "decision": Decision(result="approved", reviewer="시험", at="2026-11-03T00:00:00+09:00", seq=2)})
    after = Graph()
    building(after, keep)
    got = {p.target: p.action for p in diff(old, after, "t", "시험", approved=[bundle])}
    assert got.get(str(e)) == "retire", got

    gis = URIRef(ID + "building-gis/g")
    old2 = Graph()
    building(old2, gis)
    old2.add((gis, J("gisBuildingId"), Literal("G1")))
    new2 = Graph()
    for name in ("b2", "b1"):
        building(new2, URIRef(ID + f"building/{name}"), gis="G1")
    runs = {tuple((p.action, p.changes[-1].o.value, tuple(p.flags)) for p in diff(old2, new2, "t", "시험") if p.action == "link")
            for _ in range(3)}
    assert runs == {(("link", ID + "building/b1", (AMBIGUOUS_LINK,)),)}, runs


def check_rules_on_data(tmp: Path) -> None:
    """실제 자료에서 다시 뽑으면 2026-11-03에 승인한 제안이 그대로 나오고, 차례로 반영해도 SHACL을 지난다."""
    from src.graph.store import load_store
    from src.graph.validate import check_proposal
    from src.review import emap_proposals, placeholder_proposals, same_building_migration
    from src.review.proposals import ProposalStore

    def link_of(changes) -> tuple:
        return tuple(sorted((c["p"] if isinstance(c, dict) else c.p, (c["o"]["value"] if isinstance(c, dict) else c.o.value))
                            for c in changes if (c["op"] if isinstance(c, dict) else c.op) == "add"))

    rows = json.loads(REAL.read_text(encoding="utf-8")) if REAL.exists() else []
    # 2026-11-03에 정정 스크립트로 먼저 만든 컴퓨터는 옮기기 제안 번호가 'fix-same-building-rule-basemap-link-{GIS 번호}'다.
    # 모듈(same_building_migration)은 'fix-same-building-{GIS 번호}'로 만들므로, 같은 꼴로 맞춰 견준다
    old_fix = "fix-same-building-rule-basemap-link-"
    approved = {r["id"].replace(old_fix, "fix-same-building-"): link_of(r["changes"])
                for r in rows if r["id"].startswith(RULES) and r["status"] == "approved"}
    os.environ["JGK_PROPOSALS"] = str(tmp)
    store = ProposalStore(tmp)
    ph = placeholder_proposals.candidates()
    props = [placeholder_proposals.make(r) for _, r in ph[ph["target"].notna()].iterrows()]
    assert len(props) == 7, ph[["label", "reason"]]
    em = emap_proposals.candidates()
    props += [emap_proposals.make(r) for _, r in em[em["target"].notna()].iterrows()]
    assert not (em["iou"] < 0).any() and (em["target"].notna() <= (em["iou"] >= emap_proposals.MIN_IOU)).all(), em
    props += same_building_migration.candidates(load_store(), store.load())
    if approved:  # 실제 기록에 이 규칙들의 승인이 있으면 같은 제안(같은 대상, 같은 변경)이 다시 나와야 한다
        got = {p.id: link_of(p.changes) for p in props}
        assert got == approved, sorted(set(got.items()) ^ set(approved.items()))
    else:
        print("실제 기록에 이 규칙들의 승인이 없어 다시 뽑기 대조는 건너뛴다 (다른 컴퓨터의 기록)")
    g = load_store()
    for p in props:
        ok, res = check_proposal(g, p)
        assert ok, (p.id, sorted({r["message"] for r in res}))
        g.update(p.sparql_update())
    same = list(g.query(f"SELECT (COUNT(*) AS ?n) WHERE {{ ?s <http://www.w3.org/2002/07/owl#sameAs> ?o FILTER(CONTAINS(STR(?s), '/building')) }}"))
    assert int(same[0]["n"].value) == 0, same  # 옮기기를 모두 반영하면 건물끼리의 owl:sameAs는 남지 않는다
    print(f"자리표시 7건, 전자지도 {len(em[em['target'].notna()])}건, 옮기기 {len(props) - 7 - len(em[em['target'].notna()])}건 확인")


def run() -> None:
    for fn in (check_name_key, check_edge_centers, check_diff_bundles_entrance, check_diff_after_approvals):
        fn()
        print(f"통과 {fn.__name__}")
    tmp = Path(tempfile.mkdtemp()) / "proposals.json"
    copy_without(tmp, RULES)
    old = os.environ.get("JGK_PROPOSALS")
    try:
        check_rules_on_data(tmp)
        print("통과 check_rules_on_data")
    finally:
        if old is None:
            os.environ.pop("JGK_PROPOSALS", None)
        else:
            os.environ["JGK_PROPOSALS"] = old
        shutil.rmtree(tmp.parent, ignore_errors=True)


def test_address_link():
    run()


if __name__ == "__main__":
    run()
