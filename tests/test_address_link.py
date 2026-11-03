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
    """이름은 대소문자, 띄어쓰기, 괄호 속 글, 끝의 'N동'을 빼고 견준다."""
    from src.review.placeholder_proposals import name_key
    assert name_key("루체씨티3동") == name_key("루체씨티") == "루체씨티"
    assert name_key("PlusTwinvill (B)") == name_key("plusTwinvill")
    assert name_key("등촌중학교 다목적동") != name_key("등촌중학교")  # 끝이 숫자 동이 아니면 그대로 둔다
    assert name_key(None) is None and name_key("(B)") is None


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


def check_rules_on_data(tmp: Path) -> None:
    """실제 자료에서 다시 뽑으면 2026-11-03에 승인한 제안이 그대로 나오고, 차례로 반영해도 SHACL을 지난다."""
    from src.graph.store import load_store
    from src.graph.validate import check_proposal
    from src.review import emap_proposals, placeholder_proposals, same_building_migration
    from src.review.proposals import ProposalStore

    approved = {r["id"] for r in json.loads(REAL.read_text(encoding="utf-8"))
                if r["id"].startswith(RULES) and r["status"] == "approved"}
    os.environ["JGK_PROPOSALS"] = str(tmp)
    store = ProposalStore(tmp)
    ph = placeholder_proposals.candidates()
    props = [placeholder_proposals.make(r) for _, r in ph[ph["target"].notna()].iterrows()]
    assert len(props) == 7, ph[["label", "reason"]]
    em = emap_proposals.candidates()
    props += [emap_proposals.make(r) for _, r in em[em["target"].notna()].iterrows()]
    assert not (em["iou"] < 0).any() and (em["target"].notna() <= (em["iou"] >= emap_proposals.MIN_IOU)).all(), em
    props += same_building_migration.candidates(load_store(), store.load())
    if approved:  # 실제 기록에 승인이 있으면 같은 제안이 다시 나와야 한다
        assert {p.id for p in props} == approved, sorted({p.id for p in props} ^ approved)
    g = load_store()
    for p in props:
        ok, res = check_proposal(g, p)
        assert ok, (p.id, sorted({r["message"] for r in res}))
        g.update(p.sparql_update())
    same = list(g.query(f"SELECT (COUNT(*) AS ?n) WHERE {{ ?s <http://www.w3.org/2002/07/owl#sameAs> ?o FILTER(CONTAINS(STR(?s), '/building')) }}"))
    assert int(same[0]["n"].value) == 0, same  # 옮기기를 모두 반영하면 건물끼리의 owl:sameAs는 남지 않는다
    print(f"자리표시 7건, 전자지도 {len(em[em['target'].notna()])}건, 옮기기 {len(props) - 7 - len(em[em['target'].notna()])}건 확인")


def run() -> None:
    for fn in (check_name_key, check_edge_centers, check_diff_bundles_entrance):
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
