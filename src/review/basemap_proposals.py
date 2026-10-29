"""국가기본도의 주소로 주소 미연결 건물을 건물관리번호 건물에 잇는 제안을 만든다.

실행: python -m src.review.basemap_proposals
입력: data/processed/objects.gpkg (building, basemap_building), building_basemap.csv, jeungmi_area.gpkg (navi_building_center)
출력: data/processed/review/proposals.json 에 새 제안 추가 (같은 id가 있으면 건너뛴다)

규칙 (모두 맞아야 제안한다):
1. 주소 미연결 건물(building-gis)이 국가기본도 건물과 이어져 있고(서로 가장 많이 겹치고 IoU 0.5 이상), 그 국가기본도 건물에 도로명주소가 있다.
2. 그 주소를 쓰는 내비게이션용DB 건물 가운데 모양 없이 점만 있는 건물의 점이 이 건물 모양에서 MAX_DISTANCE_M 안에 하나만 있다.
3. 그 점 건물을 더 가까운 다른 주소 미연결 건물이 차지하지 않았다 (점 건물 하나에 제안 하나).
주소만으로는 고르지 않는다. 아파트 단지처럼 한 주소에 건물이 여러 동이라서다.
승인하면 주소 미연결 건물의 URI는 남기고 상태를 '주소 연결됨'으로 바꾸고 owl:sameAs로 건물관리번호 건물을 가리킨다 (자동 갱신의 link 제안과 같은 꼴).
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd

from src.model import uri
from src.review.proposals import Change, Proposal, ProposalStore, Term, now

ROOT = Path(__file__).resolve().parents[2]
PROC = ROOT / "data" / "processed"
JGK = "https://yeodh10.github.io/jeungmi-geokg/def#"
OWL_SAMEAS = "http://www.w3.org/2002/07/owl#sameAs"
MAX_DISTANCE_M = 10  # 점 건물의 점과 주소 미연결 건물 모양 사이 (EPSG:5186)


def candidates(proc: Path = PROC) -> pd.DataFrame:
    """규칙에 맞는 (주소 미연결 건물, 점 건물) 짝과 근거 값."""
    gpkg = proc / "objects.gpkg"
    links = pd.read_csv(proc / "building_basemap.csv", dtype=str)
    links = links[links["building_uri"].str.contains("/building-gis/")]
    basemap = gpd.read_file(gpkg, layer="basemap_building").set_index("uri")
    buildings = gpd.read_file(gpkg, layer="building").to_crs("EPSG:5186")
    centers = gpd.read_file(proc / "jeungmi_area.gpkg", layer="navi_building_center").to_crs("EPSG:5186")
    centers["addr_key"] = centers["road_code"] + "-" + centers["bldg_main_no"] + "-" + centers["bldg_sub_no"]
    point_only = set(buildings.loc[buildings.geom_type == "Point", "bldg_mgt_no"])
    by_uri = buildings.set_index("uri")

    rows = []
    for r in links.itertuples():
        m = basemap.loc[r.map_uri]
        if not isinstance(m["addr_key"], str):
            continue
        poly = by_uri.at[r.building_uri, "geometry"]
        cand = centers[(centers["addr_key"] == m["addr_key"]) & centers["bldg_mgt_no"].isin(point_only)]
        dist = cand.distance(poly)
        near = cand[dist <= MAX_DISTANCE_M]
        if len(near) != 1:
            continue
        c = near.iloc[0]
        target = by_uri.loc[uri.building(c["bldg_mgt_no"])]
        rows.append({"gis_uri": r.building_uri, "building_uri": target.name, "mgt": c["bldg_mgt_no"],
                     "distance_m": round(float(dist[near.index[0]]), 1), "basemap_uri": r.map_uri,
                     "nf_id": m["nf_id"], "basemap_name": m["name"], "iou": float(r.iou),
                     "address": target["road_address"], "label": target["label"],
                     "pnu_gis": by_uri.at[r.building_uri, "pnu"], "pnu_basemap": m["pnu"], "pnu_navi": target["pnu"],
                     "lonlat": tuple(gpd.GeoSeries([poly.centroid], crs="EPSG:5186").to_crs("EPSG:4326").iloc[0].coords[0])})
    df = pd.DataFrame(rows)
    if len(df):  # 점 건물 하나에 제안 하나: 가장 가까운 주소 미연결 건물만 남긴다
        df = df.sort_values("distance_m").drop_duplicates("building_uri").sort_values("gis_uri")
    return df


def make(row) -> Proposal:
    gis_id = row["gis_uri"].rsplit("/", 1)[-1]
    name = row["label"] if isinstance(row["label"], str) else row["address"]
    pnus = {"GIS": row["pnu_gis"], "국가기본도": row["pnu_basemap"], "내비게이션용DB": row["pnu_navi"]}
    pnu = ", ".join(f"{k} {v if isinstance(v, str) else '없음'}" for k, v in pnus.items())
    same_pnu = len({v for v in pnus.values() if isinstance(v, str)}) == 1
    status = lambda op, v: Change(op=op, s=row["gis_uri"], p=JGK + "status", o=Term(kind="uri", value=JGK + v))
    return Proposal(
        id=f"rule-basemap-link-{gis_id}", created_at=now(), source_kind="rule-candidate",
        source_detail="국가기본도 주소로 주소 미연결 건물 잇기 (src/review/basemap_proposals.py 규칙 1~3)",
        action="link", target=row["gis_uri"], target_type="Building",
        summary=f"주소 미연결 건물을 '{name}'(건물관리번호 {row['mgt']})과 같은 건물로 잇는다",
        evidence=[
            f"국가기본도 건물 {row['nf_id']}" + (f" '{row['basemap_name']}'" if isinstance(row["basemap_name"], str) else "")
            + f"과 모양이 겹친다 (IoU {row['iou']:.3f}), 그 건물의 도로명주소는 {row['address']}",
            f"같은 주소의 내비게이션용DB 건물 {row['mgt']}은(는) 모양 없이 점만 있고, 그 점이 이 건물 모양에서 {row['distance_m']}m 떨어져 있다 ({MAX_DISTANCE_M}m 안에 하나뿐)",
            f"PNU: {pnu}" + ("" if same_pnu else " (서로 다르다, 확인 필요)"),
            "기존 URI는 남기고 상태를 '주소 연결됨'으로 바꾼다 (docs/uri-policy.md 건물 규칙 4)",
        ],
        changes=[status("remove", "AddressUnlinked"), status("add", "AddressLinked"),
                 Change(op="add", s=row["gis_uri"], p=OWL_SAMEAS, o=Term(kind="uri", value=row["building_uri"]))],
        location=row["lonlat"],
    )


def main() -> None:
    df = candidates()
    props = [make(r) for _, r in df.iterrows()]
    for p in props:
        print(f"[제안] {p.id}: {p.summary}")
    added = ProposalStore().add_new(props)
    print(f"새 제안 {added}건 (후보 {len(props)}건)")


if __name__ == "__main__":
    main()
