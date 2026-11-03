"""도로명주소 전자지도 건물 모양으로 주소 미연결 건물을 건물관리번호 건물에 잇는 제안을 만든다.

실행: python -m src.review.emap_proposals
입력: data/processed/jeungmi_area.gpkg (emap_building), objects.gpkg (building), 승인을 얹은 그래프
출력: data/processed/review/proposals.json 에 새 제안 추가 (같은 id가 있으면 건너뛴다)
      data/processed/review_emap_link.csv (전자지도 건물과 겹친 주소 미연결 건물마다 제안했는지와 그 까닭)

전자지도 건물(TL_SPBD_BULD)은 도로명주소를 가진 건물의 모양과 건물관리번호(BD_MGT_SN)를 함께 담는다.
규칙 (모두 맞아야 제안한다):
1. 주소 미연결 건물(모양 1㎡ 이상)과 전자지도 건물이 서로 가장 많이 겹치고 IoU가 MIN_IOU 이상이다.
2. 그 전자지도 건물의 건물관리번호가 우리 그래프의 건물이고, 그 건물에 모양이 없다(점만 있다).
   모양이 있으면 그 건물은 이미 다른 GIS 모양을 가졌으므로 같은 건물이라 단정하지 않는다.
3. 그 건물을 다른 주소 미연결 건물이 이미 가리키지 않는다.
지상 층수가 다르면 제안은 내되 확인 필요(flags)로 둔다. 학교처럼 내비게이션용DB가 단지 전체를 1층으로 적은 경우가 있다.
건물관리번호가 우리 그래프에 없는 건물(반경 경계에 걸친 건물)은 여기서 다루지 않는다. 자동 갱신의 경계 건물 규칙이
'새 건물 만들기 + 잇기'로 낸다 (src.ingest.clip_area.edge_centers).
전자지도는 공개 보류 자료라, 이 제안과 그 변경 기록은 공개 사이트에 나가지 않는다 (config/area.yaml publish.hold_sources).
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd

from src.graph.store import load_store
from src.review.basemap_proposals import JGK, PROC, SAME_BUILDING
from src.review.proposals import Change, Proposal, ProposalStore, Term, now

MIN_IOU = 0.5  # 국가기본도 건물 잇기(build_objects.match_by_shape)와 같은 기준
MIN_AREA_M2 = 1  # 이보다 작은 모양은 자리표시라 placeholder_proposals가 다룬다 (build_objects.GIS_TINY_M2)


def candidates(proc: Path = PROC) -> pd.DataFrame:
    """전자지도 건물과 겹친 주소 미연결 건물마다 가장 많이 겹친 짝, 대상 건물, 제안하지 않은 까닭(reason)."""
    buildings = gpd.read_file(proc / "objects.gpkg", layer="building").to_crs("EPSG:5186")
    emap = gpd.read_file(proc / "jeungmi_area.gpkg", layer="emap_building").to_crs("EPSG:5186")
    store = load_store()
    taken = {r["t"].value for r in store.query(f"SELECT DISTINCT ?t WHERE {{ ?s <{SAME_BUILDING}> ?t }}")}
    linked = {r["s"].value for r in store.query(f"SELECT ?s WHERE {{ ?s <{JGK}status> <{JGK}AddressLinked> }}")}
    unlinked = buildings[(buildings["status"] == "AddressUnlinked") & (buildings.geom_type != "Point")]
    unlinked = unlinked[(unlinked.area >= MIN_AREA_M2) & ~unlinked["uri"].isin(linked)]  # 승인으로 이미 이어진 건물은 뺀다
    ours = buildings[buildings["kind"] == "building"].set_index("bldg_mgt_no")

    a = unlinked[["uri", "geometry"]].assign(area_gis=unlinked.area)
    b = emap[["BD_MGT_SN", "BULD_NM", "GRO_FLO_CO", "geometry"]].assign(area_emap=emap.area, emap_i=range(len(emap)))
    j = gpd.overlay(a, b, how="intersection", keep_geom_type=True)
    j["iou"] = j.area / (j["area_gis"] + j["area_emap"] - j.area)
    best_gis = j.sort_values("iou", ascending=False).drop_duplicates("uri")
    best_emap = j.sort_values("iou", ascending=False).drop_duplicates("emap_i").set_index("emap_i")["uri"]

    rows = []
    gis_by_uri = buildings.set_index("uri")
    for r in best_gis.sort_values("uri").itertuples():
        me = gis_by_uri.loc[r.uri]
        row = {"uri": r.uri, "label": me["label"], "floors": me["floors_above"], "mgt": r.BD_MGT_SN, "emap_name": r.BULD_NM,
               "emap_floors": r.GRO_FLO_CO, "iou": round(float(r.iou), 3), "target": None, "reason": None}
        rows.append(row)
        if r.iou < MIN_IOU:
            row["reason"] = f"IoU {MIN_IOU} 미만"
            continue
        if best_emap[r.emap_i] != r.uri:
            row["reason"] = "전자지도 건물이 다른 주소 미연결 건물과 더 많이 겹친다"
            continue
        if r.BD_MGT_SN not in ours.index:
            row["reason"] = "건물관리번호가 그래프에 없다 (범위 경계 건물)"
            continue
        t = ours.loc[r.BD_MGT_SN]
        if t.geometry.geom_type != "Point":
            row["reason"] = "대상 건물에 이미 모양이 있다"
            continue
        if t["uri"] in taken:
            row["reason"] = "대상 건물을 다른 주소 미연결 건물이 이미 가리킨다"
            continue
        row |= {"target": t["uri"], "target_label": t["label"], "target_address": t["road_address"],
                "target_floors": t["floors_above"], "pnu": me["pnu"], "target_pnu": t["pnu"],
                "lonlat": tuple(gpd.GeoSeries([me.geometry.centroid], crs="EPSG:5186").to_crs("EPSG:4326").iloc[0].coords[0])}
    return pd.DataFrame(rows)


def make(row) -> Proposal:
    gis_id = row["uri"].rsplit("/", 1)[-1]
    name = row["target_label"] if isinstance(row["target_label"], str) else row["target_address"]
    show = lambda v: "없음" if pd.isna(v) else str(int(v)) if isinstance(v, float) else str(v)
    emap_floors = int(row["emap_floors"]) if str(row["emap_floors"]).isdigit() and int(row["emap_floors"]) > 0 else None
    floors = {v for v in (row["floors"], row["target_floors"], emap_floors) if not pd.isna(v)}
    status = lambda op, v: Change(op=op, s=row["uri"], p=JGK + "status", o=Term(kind="uri", value=JGK + v))
    return Proposal(
        id=f"rule-emap-link-{gis_id}", created_at=now(), source_kind="rule-candidate",
        source_detail="전자지도 건물 모양으로 주소 미연결 건물 잇기 (src/review/emap_proposals.py 규칙 1~3)",
        action="link", target=row["uri"], target_type="Building",
        summary=f"주소 미연결 건물을 '{name}'(건물관리번호 {row['mgt']})과 같은 건물로 잇는다",
        evidence=[
            f"도로명주소 전자지도 건물 '{show(row['emap_name'])}'(건물관리번호 {row['mgt']})과 모양이 겹친다 (IoU {row['iou']:.3f}, 서로 가장 많이 겹침)",
            f"그 건물관리번호의 건물은 내비게이션용DB 중심점만 있고 모양이 없다. 주소: {show(row['target_address'])}",
            f"지상 층수: 이 건물 {show(row['floors'])}, 대상 {show(row['target_floors'])}, 전자지도 {show(emap_floors)}",
            f"PNU: 이 건물 {show(row['pnu'])}, 대상 {show(row['target_pnu'])}",
            "기존 URI는 남기고 상태를 '주소 연결됨'으로 바꾼다 (docs/uri-policy.md 건물 규칙 4)",
        ],
        changes=[status("remove", "AddressUnlinked"), status("add", "AddressLinked"),
                 Change(op="add", s=row["uri"], p=SAME_BUILDING, o=Term(kind="uri", value=row["target"]))],
        location=row["lonlat"], based_on=["dataset-juso-emap", "dataset-gis-building", "dataset-navi-db"],
        flags=["지상 층수가 자료마다 다르다 (확인 필요)"] if len(floors) > 1 else [],
    )


def main() -> None:
    df = candidates()
    df.drop(columns=["lonlat"], errors="ignore").to_csv(PROC / "review_emap_link.csv", index=False, encoding="utf-8")
    props = [make(r) for _, r in df[df["target"].notna()].iterrows()]
    for p in props:
        print(f"[제안] {p.id}: {p.summary}" + (f" ({', '.join(p.flags)})" if p.flags else ""))
    print("제안하지 않은 건물:", df.loc[df["target"].isna(), "reason"].value_counts().to_dict())
    added = ProposalStore().add_new(props)
    print(f"새 제안 {added}건 (후보 {len(props)}건)")


if __name__ == "__main__":
    main()
