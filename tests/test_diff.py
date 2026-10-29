"""자동 갱신 확인: 지금 객체를 복사해 일부러 바꾼 '다음 자료'를 만들고, 비교 제안이 맞게 나오는지 본다.

실행: python -m tests.test_diff
지금 자료(data/processed)는 읽기만 하고, 바꾼 자료와 제안은 임시 폴더에만 만든다.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
FILES = ["objects.gpkg", "facility_building.csv", "parcel.csv", "admin_dong.csv", "building_entrance.csv",
         "road.csv", "entrance_road.csv", "building_zone.csv", "parcel_legal_dong.csv", "building_basemap.csv"]
EMAP_LAYERS = ["road_segment", "legal_dong", "basic_zone", "basemap_building"]
FAKE_MGT = "1150010200199990000000001"  # 시험용 건물관리번호 (실제 번호 아님)


def make_next_snapshot(dst: Path) -> dict:
    for f in FILES:
        shutil.copy(PROC / f, dst / f)
    layers = {n: gpd.read_file(PROC / "objects.gpkg", layer=n) for n in ["building", "entrance", "station", "public_facility"] + EMAP_LAYERS}
    b = layers["building"]
    busy = set(layers["station"]["building_uri"]) | set(pd.read_csv(PROC / "facility_building.csv")["building_uri"])
    addressed = b[(b["kind"] == "building") & ~b["uri"].isin(busy)]
    retire = addressed.iloc[0]["uri"]
    upd_i = addressed[addressed["floors_above"].notna()].index[1]
    update = b.loc[upd_i, "uri"]
    b.loc[upd_i, "floors_above"] = b.loc[upd_i, "floors_above"] + 1
    link_i = b[b["kind"] == "building-gis"].index[0]
    linked_gis = b.loc[link_i, "uri"]
    b.loc[link_i, ["kind", "status", "bldg_mgt_no", "uri"]] = ["building", "Active", FAKE_MGT,
                                                                f"https://yeodh10.github.io/jeungmi-geokg/id/building/{FAKE_MGT}"]
    layers["building"] = b[b["uri"] != retire]
    # 없어진 건물의 법정동, 기초구역, 도로 연결도 다음 자료에는 없다. 주소가 붙은 GIS 건물은 새 URI로 옮긴다
    zone = pd.read_csv(dst / "building_zone.csv", dtype=str, keep_default_na=False)
    zone = zone[zone["building_uri"] != retire].replace({"building_uri": {linked_gis: b.loc[link_i, "uri"]}})
    zone.to_csv(dst / "building_zone.csv", index=False)
    bm = pd.read_csv(dst / "building_basemap.csv", dtype=str)
    bm = bm[bm["building_uri"] != retire].replace({"building_uri": {linked_gis: b.loc[link_i, "uri"]}})
    bm.to_csv(dst / "building_basemap.csv", index=False)
    (dst / "objects.gpkg").unlink()
    for n, g in layers.items():
        g.to_file(dst / "objects.gpkg", layer=n, driver="GPKG", engine="pyogrio")
    return {"retire": retire, "update": update, "link": linked_gis,
            "create": f"https://yeodh10.github.io/jeungmi-geokg/id/building/{FAKE_MGT}"}


def run() -> None:
    from src.graph import build_graph
    from src.graph.store import load_store
    from src.review.diff_proposals import diff
    from src.review.proposals import ProposalStore

    tmp = Path(tempfile.mkdtemp())
    new_dir = tmp / "next"
    new_dir.mkdir()
    expect = make_next_snapshot(new_dir)

    props = diff(build_graph.build(PROC), build_graph.build(new_dir), "test", "시험용 다음 자료")
    got = {(p.action, p.target) for p in props}
    want = {(a, u) for a, u in expect.items()}
    assert got == want, f"\n기대 {sorted(want)}\n결과 {sorted(got)}"
    upd = next(p for p in props if p.action == "update")
    assert any("floorsAbove" in c.p and c.op == "add" for c in upd.changes), upd.changes

    store = ProposalStore(tmp / "proposals.json")
    store.add_new(props)
    for p in sorted(props, key=lambda p: p.action != "create"):  # 새 건물을 먼저 승인
        store.decide(p.id, "approved", "테스트 검토자")
    g = load_store(store)

    def ask(q: str):
        return [[s[v].value for v in range(len(s))] for s in g.query(
            "PREFIX jgk: <https://yeodh10.github.io/jeungmi-geokg/def#>\nPREFIX owl: <http://www.w3.org/2002/07/owl#>\n" + q)]

    assert ask(f"SELECT ?s WHERE {{ <{expect['retire']}> jgk:status ?s }}") == [[str(build_graph.JGK.Retired)]]
    assert ask(f"SELECT ?d WHERE {{ <{expect['retire']}> jgk:retiredOn ?d }}")
    floors = ask(f"SELECT ?f WHERE {{ <{expect['update']}> jgk:floorsAbove ?f }}")
    assert len(floors) == 1, floors  # 이전 값은 빠지고 새 값만 남는다
    assert ask(f"SELECT ?s WHERE {{ <{expect['link']}> jgk:status ?s }}") == [[str(build_graph.JGK.AddressLinked)]]
    assert ask(f"SELECT ?o WHERE {{ <{expect['link']}> owl:sameAs ?o }}") == [[expect["create"]]]
    assert bool(g.query(f"ASK {{ <{expect['create']}> a <https://yeodh10.github.io/jeungmi-geokg/def#Building> }}"))
    print("자동 갱신 확인 통과:", {a: u.rsplit("/", 1)[-1] for a, u in expect.items()})
    shutil.rmtree(tmp, ignore_errors=True)


def test_diff_flow():
    run()


if __name__ == "__main__":
    run()
