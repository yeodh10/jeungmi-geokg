"""국가기본도의 주소로 주소 미연결 건물을 건물관리번호 건물에 잇는 제안을 만든다.

실행: python -m src.review.basemap_proposals
입력: data/processed/objects.gpkg (building, basemap_building), building_basemap.csv, jeungmi_area.gpkg (navi_building_center)
출력: data/processed/review/proposals.json 에 새 제안 추가 (같은 id가 있으면 건너뛴다)

규칙 (모두 맞아야 제안한다):
1. 주소 미연결 건물(building-gis)이 국가기본도 건물과 이어져 있고(서로 가장 많이 겹치고 IoU 0.5 이상), 그 국가기본도 건물에 도로명주소가 있다.
2. 그 주소를 쓰는 내비게이션용DB 건물 가운데 모양 없이 점만 있는 건물의 점이 이 건물 모양에서 MAX_DISTANCE_M 안에 하나만 있다.
3. 그 점 건물을 더 가까운 다른 주소 미연결 건물이 차지하지 않았다 (점 건물 하나에 제안 하나).
4. 출처끼리 PNU가 다르면, GIS 쪽 PNU가 그 점 건물의 지번(내비게이션용DB 대표 지번과 관련 지번) 가운데 하나여야 한다.
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
SHAPE_NEAR_M = 5  # 모양 붙이기에서 모양 밖 점을 가장 가까운 모양에 이을 때의 한계 (EPSG:5186)
# 아래 셋은 2026-10-30 제안 검토에서 정한 기준이다. 이미 이은 국가기본도 건물 1,834쌍에서 잰 값으로 정했다 (docs/decisions.md)
SHAPE_MAX_OVERLAP = 0.1  # 국가기본도 모양이 우리 다른 건물 모양과 겹치는 비율의 한계 (이은 쌍의 99%가 0.13 이하)
SHAPE_MIN_AREA_M2 = 15  # 이은 국가기본도 건물 넓이의 하위 1%
SHAPE_FLOOR_GAP = 3  # 국가기본도 2층 이하인데 건축물대장이 이만큼 높으면 부속 구조물로 본다 (이은 쌍의 0.4%)


def related_lots(proc: Path = PROC) -> dict[str, set[str]]:
    """건물관리번호마다 내비게이션용DB가 적은 관련 지번의 PNU (대표 지번은 건물 객체의 pnu에 있다)."""
    nj = pd.read_csv(proc / "navi_jibun.csv", dtype=str)
    pnu = (nj["legal_dong_code"] + nj["is_mountain"].map({"0": "1", "1": "2"})
           + nj["jibun_main"].str.zfill(4) + nj["jibun_sub"].str.zfill(4))
    return pnu.groupby(nj["bldg_mgt_no"]).apply(set).to_dict()


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
    lots = related_lots(proc)

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
        pnus = {v for v in (by_uri.at[r.building_uri, "pnu"], m["pnu"], target["pnu"]) if isinstance(v, str)}
        if len(pnus) > 1 and by_uri.at[r.building_uri, "pnu"] not in lots.get(c["bldg_mgt_no"], set()) | {target["pnu"]}:
            continue  # 규칙 4
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


def shape_candidates(proc: Path = PROC) -> tuple[pd.DataFrame, dict]:
    """모양 없이 점만 있는 건물에 국가기본도 건물 모양을 붙일 짝과, 제안하지 않은 까닭별 개수.

    규칙 (모두 맞아야 제안한다):
    1. 점이 국가기본도 건물 모양 하나에만 들어 있고, 그 모양 안에 다른 점 건물이 없다.
    2. 그 국가기본도 건물이 우리 다른 건물과 이어져 있지 않다 (이어져 있으면 이미 모양이 있는 건물의 것이다).
    3. 같은 점 건물을 주소 미연결 건물 잇기 제안(candidates)이 다루지 않는다 (그쪽은 sameAs로 모양을 얻는다).
    4. 국가기본도 건물의 도로명주소가 점 건물과 같다. 국가기본도에 주소가 없으면 '확인 필요'로 제안하고, 다르면 제안하지 않는다.
    5. 점이 어느 모양에도 들지 않으면(중심점이 외곽선 바로 밖에 찍힌 경우), SHAPE_NEAR_M 안에서 가장 가까운 모양이
       도로명주소까지 같을 때만 2, 3을 따져 제안한다. 이때는 주소가 없는 모양은 쓰지 않는다.
    6. 국가기본도 모양이 우리 다른 건물 모양과 SHAPE_MAX_OVERLAP보다 많이 겹치지 않는다.
    7. 국가기본도 PNU가 점 건물과 다르면, 그 PNU가 점 건물의 관련 지번 가운데 하나여야 한다.
    8. 국가기본도 모양이 SHAPE_MIN_AREA_M2㎡ 이상이고, 국가기본도 2층 이하인데 건축물대장이 SHAPE_FLOOR_GAP층 넘게 높지 않다
       (아파트 동 중심점이 경비실 같은 작은 부속 구조물 모양에 떨어진 경우를 거른다).
    """
    gpkg = proc / "objects.gpkg"
    buildings = gpd.read_file(gpkg, layer="building")
    stored_crs = buildings.crs
    pts = buildings[buildings.geom_type == "Point"].to_crs("EPSG:5186")
    basemap = gpd.read_file(gpkg, layer="basemap_building")
    bm = basemap.to_crs("EPSG:5186").rename(columns={"uri": "map_uri", "pnu": "map_pnu", "floors": "map_floors"})
    centers = gpd.read_file(proc / "jeungmi_area.gpkg", layer="navi_building_center")
    key_of = dict(zip(centers["bldg_mgt_no"], centers["road_code"] + "-" + centers["bldg_main_no"] + "-" + centers["bldg_sub_no"]))
    taken = set(pd.read_csv(proc / "building_basemap.csv", dtype=str)["map_uri"])
    by_link = set(candidates(proc)["building_uri"])
    lots = related_lots(proc)

    j = gpd.sjoin(pts, bm[["map_uri", "nf_id", "name", "addr_key", "map_pnu", "map_floors", "geometry"]],
                  predicate="within", how="inner")
    j["distance_m"], j["inside"] = 0.0, True
    outside = pts[~pts["uri"].isin(j["uri"])]
    near = gpd.sjoin_nearest(outside, bm[["map_uri", "nf_id", "name", "addr_key", "map_pnu", "map_floors", "geometry"]],
                             max_distance=SHAPE_NEAR_M, distance_col="distance_m").drop_duplicates("uri")
    near = near[near["addr_key"].notna() & (near["addr_key"] == near["bldg_mgt_no"].map(key_of))].assign(inside=False)
    skipped = {f"국가기본도 건물 밖 ({SHAPE_NEAR_M}m 안에 주소 같은 모양 없음)": len(outside) - len(near)}
    per_point = j.groupby("uri").size()
    j = pd.concat([j, near], ignore_index=True)
    per_map = j.groupby("map_uri").size()
    poly_of = basemap.set_index("uri").geometry.to_crs(stored_crs)
    metric_of = bm.set_index("map_uri").geometry
    ours = buildings[buildings.geom_type != "Point"].to_crs("EPSG:5186").geometry.reset_index(drop=True)

    def overlap(map_uri: str) -> float:
        poly = metric_of[map_uri]
        return max((poly.intersection(g).area / poly.area for g in ours.iloc[ours.sindex.query(poly, predicate="intersects")]), default=0.0)

    def floor_gap(r) -> bool:
        return not pd.isna(r.floors_above) and not pd.isna(r.map_floors) and r.floors_above > 0             and r.map_floors <= 2 and r.floors_above - r.map_floors >= SHAPE_FLOOR_GAP
    rows = []
    for r in j.itertuples():
        reason = ("주소 미연결 건물 잇기 제안이 다룸" if r.uri in by_link
                  else "국가기본도 건물 여럿에 걸침" if per_point.get(r.uri, 1) > 1
                  else "한 국가기본도 건물에 점 건물이 여럿" if per_map[r.map_uri] > 1
                  else "국가기본도 건물이 우리 다른 건물과 이어짐" if r.map_uri in taken
                  else "도로명주소가 다름" if isinstance(r.addr_key, str) and r.addr_key != key_of.get(r.bldg_mgt_no)
                  else "PNU가 다르고 관련 지번에도 없음" if isinstance(r.pnu, str) and isinstance(r.map_pnu, str)
                  and r.pnu != r.map_pnu and r.map_pnu not in lots.get(r.bldg_mgt_no, set())
                  else "국가기본도 모양이 너무 작음" if metric_of[r.map_uri].area < SHAPE_MIN_AREA_M2
                  else "층수 차이로 부속 구조물로 보임" if floor_gap(r)
                  else "우리 다른 건물 모양과 많이 겹침" if overlap(r.map_uri) > SHAPE_MAX_OVERLAP
                  else None)
        if reason:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        rows.append({"building_uri": r.uri, "mgt": r.bldg_mgt_no, "label": r.label, "address": r.road_address,
                     "point": buildings.set_index("uri").at[r.uri, "geometry"], "polygon": poly_of[r.map_uri],
                     "map_uri": r.map_uri, "nf_id": r.nf_id, "map_name": r.name, "address_same": isinstance(r.addr_key, str),
                     "inside": r.inside, "distance_m": round(float(r.distance_m), 1),
                     "pnu": r.pnu, "map_pnu": r.map_pnu, "floors": r.floors_above, "map_floors": r.map_floors})
    return pd.DataFrame(rows), skipped


def make_shape(row) -> Proposal:
    import shapely

    from src.model import uri as uri_mod
    geo = "http://www.opengis.net/ont/geosparql#"
    gu = uri_mod.geometry(row["building_uri"])
    wkt = lambda g: Term(kind="literal", value=shapely.to_wkt(g, rounding_precision=7), datatype=geo + "wktLiteral")
    name = row["label"] if isinstance(row["label"], str) else row["address"]
    num = lambda v: "없음" if pd.isna(v) else str(int(v))
    pnu = lambda v: v if isinstance(v, str) else "없음"
    floors_same = not pd.isna(row["floors"]) and not pd.isna(row["map_floors"]) and int(row["floors"]) == int(row["map_floors"])
    return Proposal(
        id=f"rule-basemap-shape-{row['mgt']}", created_at=now(), source_kind="rule-candidate",
        source_detail="점만 있는 건물에 국가기본도 건물 모양 붙이기 (src/review/basemap_proposals.py shape_candidates 규칙 1~4)",
        action="update", target=row["building_uri"], target_type="Building",
        summary=f"'{name}'(건물관리번호 {row['mgt']})에 국가기본도 건물 {row['nf_id']}의 모양을 붙인다",
        evidence=[
            f"이 건물은 내비게이션용DB 건물 중심점만 있고 모양이 없다. 그 점이 국가기본도 건물 {row['nf_id']}"
            + (f" '{row['map_name']}'" if isinstance(row["map_name"], str) else "")
            + (" 안에 있다" if row["inside"] else f"에서 {row['distance_m']}m 밖에 있고, {SHAPE_NEAR_M}m 안에서 가장 가까운 모양이다")
            + " (그 모양에 이은 점 건물은 이것 하나)",
            "그 국가기본도 건물은 우리 다른 건물과 이어져 있지 않다",
            "국가기본도 건물의 도로명주소가 이 건물과 같다" if row["address_same"]
            else "국가기본도 건물에 도로명주소가 없다 (확인 필요)",
            f"PNU: 이 건물 {pnu(row['pnu'])}, 국가기본도 {pnu(row['map_pnu'])}"
            + ("" if pnu(row["pnu"]) == pnu(row["map_pnu"])
               else " (한쪽에 없어 견주지 못함)" if "없음" in (pnu(row["pnu"]), pnu(row["map_pnu"]))
               else " (서로 다르다, 확인 필요)"),
            f"층수: 건축물대장 {num(row['floors'])}, 국가기본도 {num(row['map_floors'])}" + ("" if floors_same else " (다르다, 참고)"),
            "모양 출처는 국가기본도라 이용 조건 확인 전까지 공개 페이지에서 모양만 빠진다. 바꾸기 전 점은 시점별 판 기록에 남는다",
        ],
        changes=[Change(op="remove", s=gu, p=geo + "asWKT", o=wkt(row["point"])),
                 Change(op="add", s=gu, p=geo + "asWKT", o=wkt(row["polygon"])),
                 Change(op="add", s=gu, p="http://purl.org/dc/terms/source", o=Term(kind="uri", value=JGK + "dataset-ngii-basemap")),
                 Change(op="add", s=row["building_uri"], p=JGK + "basemapFeature", o=Term(kind="uri", value=row["map_uri"]))],
        location=tuple(row["point"].coords[0]),
    )


def main() -> None:
    df = candidates()
    props = [make(r) for _, r in df.iterrows()]
    shapes, skipped = shape_candidates()
    props += [make_shape(r) for _, r in shapes.iterrows()]
    for p in props:
        print(f"[제안] {p.id}: {p.summary}")
    print("모양 붙이기에서 제안하지 않은 점 건물:", skipped)
    added = ProposalStore().add_new(props)
    print(f"새 제안 {added}건 (후보 {len(props)}건)")


if __name__ == "__main__":
    main()
