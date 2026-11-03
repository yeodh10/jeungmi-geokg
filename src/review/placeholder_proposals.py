"""모양이 자리표시(넓이 1㎡ 미만 마름모)뿐인 주소 미연결 건물을 같은 건물관리번호 건물에 잇는 제안을 만든다.

실행: python -m src.review.placeholder_proposals
입력: data/processed/objects.gpkg (building), review_gis_tiny.csv (src.model.build_objects), 승인을 얹은 그래프
출력: data/processed/review/proposals.json 에 새 제안 추가 (같은 id가 있으면 건너뛴다)
      data/processed/review_gis_tiny_link.csv (자리표시마다 제안했는지와 그 까닭)

GIS건물통합정보의 자리표시 모양은 실제 외곽이 아니어서 국가기본도 모양과 견주는 잇기 규칙(basemap_proposals)으로는 잇지 못한다.
대신 건축물대장 속성(이름, 층수)과 지번으로 잇는다. 규칙 (모두 맞아야 제안한다):
1. 자리표시와 PNU가 같은 건물관리번호 건물이 자리표시에서 MAX_DISTANCE_M 안에 하나뿐이다.
2. 이름이나 지상 층수 가운데 하나 이상이 같고, 두 값이 모두 있는데 다른 것은 없다.
   이름은 대소문자, 띄어쓰기, 괄호 속 글, 끝의 'N동'을 빼고 견준다 ('루체씨티3동'과 '루체씨티', 'PlusTwinvill (B)'와 'plusTwinvill').
3. 그 건물을 다른 주소 미연결 건물이 이미 가리키지 않는다 (같은 건물 잇기는 대상마다 하나다, src/graph/shapes.ttl).
승인하면 basemap_proposals의 주소 잇기와 같은 꼴로 바뀐다 (상태를 '주소 연결됨'으로, jgk:sameBuildingAs로 대상을 가리킨다).
"""
from __future__ import annotations

import re
from pathlib import Path

import geopandas as gpd
import pandas as pd

from src.graph.store import load_store
from src.review.basemap_proposals import JGK, PROC, SAME_BUILDING
from src.review.proposals import Change, Proposal, ProposalStore, Term, now

MAX_DISTANCE_M = 10  # 자리표시와 대상 건물 모양(또는 점) 사이 (EPSG:5186)


def name_key(v) -> str | None:
    """견주기용 이름. 대소문자, 띄어쓰기, 괄호 속 글, 끝의 'N동'을 뺀다."""
    if not isinstance(v, str):
        return None
    v = re.sub(r"\([^)]*\)", "", v.lower())
    v = re.sub(r"\s+", "", v)
    return re.sub(r"\d+동$", "", v) or None


def link_state() -> tuple[set[str], set[str]]:
    """승인을 얹은 그래프에서 같은 건물로 가리켜진 대상과, 이미 주소가 이어진 주소 미연결 건물."""
    store = load_store()
    taken = {r["t"].value for r in store.query(f"SELECT DISTINCT ?t WHERE {{ ?s <{SAME_BUILDING}> ?t }}")}
    linked = {r["s"].value for r in store.query(f"SELECT ?s WHERE {{ ?s <{JGK}status> <{JGK}AddressLinked> }}")}
    return taken, linked


def candidates(proc: Path = PROC) -> pd.DataFrame:
    """자리표시마다 대상 건물과 근거 값. 제안하지 않은 자리표시도 까닭(reason)과 함께 돌려준다."""
    tiny = pd.read_csv(proc / "review_gis_tiny.csv", dtype={"uri": str})
    buildings = gpd.read_file(proc / "objects.gpkg", layer="building").to_crs("EPSG:5186")
    by_uri = buildings.set_index("uri")
    ours = buildings[buildings["uri"].str.contains("/building/")]
    taken, linked = link_state()

    rows = []
    for t in tiny.itertuples():
        me = by_uri.loc[t.uri]
        row = {"uri": t.uri, "label": me["label"], "floors": me["floors_above"], "pnu": me["pnu"],
               "register_area_m2": t.register_building_area_m2, "target": None, "reason": None}
        rows.append(row)
        if me["status"] != "AddressUnlinked" or t.uri in linked:
            row["reason"] = "이미 주소 연결됨"
            continue
        near = ours[(ours["pnu"] == me["pnu"]) & (ours.distance(me.geometry) <= MAX_DISTANCE_M)]
        if len(near) != 1:
            row["reason"] = f"같은 PNU 건물이 {MAX_DISTANCE_M}m 안에 {len(near)}개"
            continue
        c = near.iloc[0]
        names = (name_key(me["label"]), name_key(c["label"]))
        floors = (me["floors_above"], c["floors_above"])
        name_same = None if None in names else names[0] == names[1]
        floor_same = None if any(pd.isna(v) for v in floors) else floors[0] == floors[1]
        if False in (name_same, floor_same) or True not in (name_same, floor_same):
            row["reason"] = f"이름 {'같음' if name_same else '다름' if name_same is False else '모름'}, 층수 {'같음' if floor_same else '다름' if floor_same is False else '모름'}"
            continue
        if c["uri"] in taken:
            row["reason"] = "대상 건물을 다른 주소 미연결 건물이 이미 가리킨다"
            continue
        row |= {"target": c["uri"], "mgt": c["bldg_mgt_no"], "target_label": c["label"], "target_address": c["road_address"],
                "target_floors": c["floors_above"], "distance_m": round(float(c.geometry.distance(me.geometry)), 1),
                "name_same": name_same, "floor_same": floor_same,
                "lonlat": tuple(gpd.GeoSeries([me.geometry.centroid], crs="EPSG:5186").to_crs("EPSG:4326").iloc[0].coords[0])}
    return pd.DataFrame(rows)


def make(row) -> Proposal:
    gis_id = row["uri"].rsplit("/", 1)[-1]
    name = row["target_label"] if isinstance(row["target_label"], str) else row["target_address"]
    show = lambda v: "없음" if pd.isna(v) else str(int(v)) if isinstance(v, float) else str(v)
    status = lambda op, v: Change(op=op, s=row["uri"], p=JGK + "status", o=Term(kind="uri", value=JGK + v))
    return Proposal(
        id=f"rule-placeholder-link-{gis_id}", created_at=now(), source_kind="rule-candidate",
        source_detail="자리표시 모양뿐인 주소 미연결 건물 잇기 (src/review/placeholder_proposals.py 규칙 1~3)",
        action="link", target=row["uri"], target_type="Building",
        summary=f"자리표시 모양뿐인 주소 미연결 건물을 '{name}'(건물관리번호 {row['mgt']})과 같은 건물로 잇는다",
        evidence=[
            f"이 건물의 모양은 넓이 1㎡ 미만의 자리표시다 (건축물대장 건축면적 {show(row['register_area_m2'])}㎡)",
            f"PNU {row['pnu']}가 같은 건물관리번호 건물은 {MAX_DISTANCE_M}m 안에 이것 하나다 ({row['distance_m']}m)",
            f"이름: 이 건물 {show(row['label'])}, 대상 {show(row['target_label'])}" + (" (같다)" if row["name_same"] else ""),
            f"지상 층수: 이 건물 {show(row['floors'])}, 대상 {show(row['target_floors'])}" + (" (같다)" if row["floor_same"] else ""),
            "기존 URI는 남기고 상태를 '주소 연결됨'으로 바꾼다 (docs/uri-policy.md 건물 규칙 4)",
        ],
        changes=[status("remove", "AddressUnlinked"), status("add", "AddressLinked"),
                 Change(op="add", s=row["uri"], p=SAME_BUILDING, o=Term(kind="uri", value=row["target"]))],
        location=row["lonlat"], based_on=["dataset-gis-building", "dataset-navi-db"],
    )


def main() -> None:
    df = candidates()
    df.drop(columns=["lonlat"], errors="ignore").to_csv(PROC / "review_gis_tiny_link.csv", index=False, encoding="utf-8")
    props = [make(r) for _, r in df[df["target"].notna()].iterrows()]
    for p in props:
        print(f"[제안] {p.id}: {p.summary}")
    print("제안하지 않은 자리표시:", df.loc[df["target"].isna(), "reason"].value_counts().to_dict())
    added = ProposalStore().add_new(props)
    print(f"새 제안 {added}건 (후보 {len(props)}건)")


if __name__ == "__main__":
    main()
