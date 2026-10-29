"""잘라낸 자료를 객체(건물, 출입구, 필지, 행정동)로 쪼개고 URI를 붙인다.

실행: python -m src.model.build_objects
입력: data/processed/jeungmi_area.gpkg, data/processed/navi_jibun.csv (src.ingest.clip_area 결과)
출력: data/processed/objects.gpkg (building, entrance 레이어, EPSG:4326)
      data/processed/parcel.csv, admin_dong.csv, building_entrance.csv
      data/processed/review_gis_id_conflict.csv (사람이 볼 목록), objects_report.json
규칙: docs/uri-policy.md
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
import yaml

from src.model import uri

ROOT = Path(__file__).resolve().parents[2]
PROC = ROOT / "data" / "processed"
AREA_GPKG = PROC / "jeungmi_area.gpkg"
OUT_GPKG = PROC / "objects.gpkg"

# 내비게이션용DB 보조출입구 유형 (레이아웃 문서 붙임 3)
SUB_ENTRANCE_TYPE = {"01": "공용", "02": "차량용", "03": "보행자용"}
# 내비게이션용DB 지번 산여부 -> PNU 대지구분
MOUNTAIN_TO_LAND = {"0": "1", "1": "2"}
ADDRESS_KEY = ["road_code", "underground", "bldg_main_no", "bldg_sub_no"]


def load_cfg() -> dict:
    with open(ROOT / "config" / "area.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def text(v) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s or None


def to_int(v) -> int | None:
    s = text(v)
    if s is None:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def to_date(v) -> str | None:
    s = text(v)
    if s is None:
        return None
    try:
        return date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        return None


def road_address(r) -> str:
    under = "지하 " if r["underground"] == "1" else ""
    sub = f"-{r['bldg_sub_no']}" if r["bldg_sub_no"] not in ("", "0") else ""
    return f"{r['sido']} {r['sigungu']} {r['road_name']} {under}{r['bldg_main_no']}{sub}"


def gis_label(r) -> str | None:
    name, dong = text(r.get("A24")), text(r.get("A25"))
    if dong == name:  # 동명에 건물명이 그대로 들어 있는 경우가 있다
        dong = None
    return " ".join(p for p in (name, dong) if p) or None


def dedupe_gis(bld: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, pd.DataFrame, int]:
    """A0만 다르고 속성과 모양이 같은 행은 하나로 합친다. 그래도 A1이 겹치면 검토 목록으로 뺀다."""
    key = bld.drop(columns=["A0", "geometry"]).astype(str)
    # 꼭짓점 시작 위치만 다른 같은 모양도 같게 보도록 정규화한 뒤 비교한다
    key["_wkb"] = shapely.to_wkb(shapely.normalize(bld.geometry.values))
    keep = ~key.duplicated()
    merged = int((~keep).sum())
    bld = bld[keep.values]
    conflict = bld["A1"].duplicated(keep=False)
    return bld[~conflict.values], bld[conflict.values].drop(columns="geometry"), merged


def main() -> None:
    cfg = load_cfg()
    metric = cfg["crs"]["metric"]
    storage = cfg["crs"]["storage"]

    gis = gpd.read_file(AREA_GPKG, layer="gis_building")
    centers = gpd.read_file(AREA_GPKG, layer="navi_building_center")
    main_ent = gpd.read_file(AREA_GPKG, layer="navi_building_entrance")
    sub_ent = gpd.read_file(AREA_GPKG, layer="navi_sub_entrance")
    register = gpd.read_file(AREA_GPKG, layer="building_register_summary")
    jibun = pd.read_csv(PROC / "navi_jibun.csv", dtype=str, keep_default_na=False)

    gis, conflicts, merged = dedupe_gis(gis)

    # 1. 건물중심점이 들어 있는 GIS 폴리곤 찾기 (EPSG:5186에서 계산)
    hit = gpd.sjoin(centers[["bldg_mgt_no", "geometry"]].to_crs(metric),
                    gis[["A1", "geometry"]].to_crs(metric), predicate="within", how="left")
    poly_of = hit.dropna(subset=["A1"]).set_index("bldg_mgt_no")["A1"]
    assert poly_of.index.is_unique, "건물중심점 하나가 두 폴리곤에 걸쳤다"
    gis_by_id = gis.set_index("A1")

    # 대표지번(지번일련번호 0)으로 PNU 만들기, GIS 폴리곤이 없는 건물에만 쓴다
    rep = jibun[jibun["jibun_seq"] == "0"].drop_duplicates("bldg_mgt_no").set_index("bldg_mgt_no")
    rep_pnu = (rep["legal_dong_code"] + rep["is_mountain"].map(MOUNTAIN_TO_LAND)
               + rep["jibun_main"].str.zfill(4) + rep["jibun_sub"].str.zfill(4))

    rows = []
    # 2. 주소가 연결된 건물: building/{건물관리번호}
    for _, c in centers.iterrows():
        mgt = c["bldg_mgt_no"]
        a1 = poly_of.get(mgt)
        row = {
            "uri": uri.building(mgt), "kind": "building", "status": "Active",
            "bldg_mgt_no": mgt, "gis_building_id": a1,
            "road_address": road_address(c), "adm_dong_code": c["adm_dong_code"],
        }
        if a1 is not None:
            g = gis_by_id.loc[a1]
            row.update(
                label=text(c["sigungu_bldg_name"]) or gis_label(g),
                use_name=text(g["A9"]), structure_name=text(g["A11"]),
                approval_date=to_date(g["A13"]),
                floors_above=to_int(g["A26"]), floors_below=to_int(g["A27"]),
                pnu=text(g["A2"]), attr_source="gis", geometry=g.geometry,
            )
        else:
            # 폴리곤이 없으면 대장 속성도 없다. 내비게이션용DB 값으로 대신한다 (docs/decisions.md)
            row.update(
                label=text(c["sigungu_bldg_name"]),
                use_name=text(c["bldg_use"]), structure_name=None, approval_date=None,
                floors_above=to_int(c["floors_above"]), floors_below=to_int(c["floors_below"]),
                pnu=rep_pnu.get(mgt), attr_source="navi", geometry=c.geometry,
            )
        rows.append(row)

    # 3. 주소가 연결되지 않은 건물: building-gis/{A1}
    linked = set(poly_of.values)
    for a1, g in gis_by_id.iterrows():
        if a1 in linked:
            continue
        rows.append({
            "uri": uri.building_gis(a1), "kind": "building-gis", "status": "AddressUnlinked",
            "bldg_mgt_no": None, "gis_building_id": a1, "road_address": None, "adm_dong_code": None,
            "label": gis_label(g), "use_name": text(g["A9"]), "structure_name": text(g["A11"]),
            "approval_date": to_date(g["A13"]),
            "floors_above": to_int(g["A26"]), "floors_below": to_int(g["A27"]),
            "pnu": text(g["A2"]), "attr_source": "gis", "geometry": g.geometry,
        })
    buildings = gpd.GeoDataFrame(rows, geometry="geometry", crs=storage)
    assert buildings["uri"].is_unique
    bld_uri = dict(zip(centers["bldg_mgt_no"], buildings.loc[buildings["kind"] == "building", "uri"]))
    dong_of = dict(zip(centers["bldg_mgt_no"], centers["adm_dong_code"]))

    # 4. 출입구
    ents, links = [], []
    for _, e in main_ent.iterrows():
        mgt = e["bldg_mgt_no"]
        u = uri.main_entrance(mgt)
        ents.append({"uri": u, "kind": "main", "entrance_type": "주출입구", "sub_entrance_seq": None,
                     "adm_dong_code": dong_of[mgt], "geometry": e.geometry})
        links.append({"building_uri": bld_uri[mgt], "entrance_uri": u})
    # 보조출입구는 같은 주소를 쓰는 건물 모두에 잇는다 (건물 6개 이상인 주소에만 있다)
    matched = sub_ent.merge(centers[ADDRESS_KEY + ["bldg_mgt_no"]], on=ADDRESS_KEY, how="left")
    for (sgg, seq), grp in matched.groupby(["sigungu_code", "entrance_seq"]):
        u = uri.sub_entrance(sgg, seq)
        mgts = grp["bldg_mgt_no"].dropna().tolist()
        ents.append({"uri": u, "kind": "sub", "entrance_type": SUB_ENTRANCE_TYPE.get(grp["entrance_type"].iloc[0]),
                     "sub_entrance_seq": seq, "adm_dong_code": dong_of[mgts[0]] if mgts else None,
                     "geometry": grp.geometry.iloc[0]})
        links += [{"building_uri": bld_uri[m], "entrance_uri": u} for m in mgts]
    entrances = gpd.GeoDataFrame(ents, geometry="geometry", crs=storage)
    links = pd.DataFrame(links).drop_duplicates()

    # 5. 필지: 건물의 PNU와 총괄표제부 PNU. 총괄표제부 값은 단지 정보로만 붙인다
    reg = pd.DataFrame(register.drop(columns="geometry"))
    reg["pnu"] = (reg["법정동코드"] + reg["대지구분코드명"].map({"대지": "1", "산": "2"})
                  + reg["주지번"].str.zfill(4) + reg["부지번"].str.zfill(4))
    reg_dup = int(reg["pnu"].duplicated().sum())
    reg = reg.drop_duplicates("pnu").set_index("pnu")
    pnus = sorted(set(buildings["pnu"].dropna()) | set(reg.index.dropna()))
    parcels = []
    for p in pnus:
        assert len(p) == 19 and p.isdigit(), p
        r = reg.loc[p] if p in reg.index else None
        parcels.append({
            "uri": uri.parcel(p), "pnu": p,
            "site_area": text(r["대지면적"]) if r is not None else None,
            "total_floor_area": text(r["연면적"]) if r is not None else None,
            "households": to_int(r["세대수"]) if r is not None else None,
            "main_building_count": to_int(r["주건축물수"]) if r is not None else None,
            "complex_main_use": text(r["주용도코드명"]) if r is not None else None,
        })
    parcels = pd.DataFrame(parcels)
    # 빈 값이 섞인 정수 열이 2.0처럼 실수로 저장되지 않게 한다
    parcels = parcels.astype({"households": "Int64", "main_building_count": "Int64"})

    # 6. 행정동 (내비게이션용DB 행정동코드)
    dongs = (centers[["adm_dong_code", "adm_dong_name"]].drop_duplicates().sort_values("adm_dong_code"))
    dongs.insert(0, "uri", dongs["adm_dong_code"].map(uri.admin_dong))

    # 저장
    if OUT_GPKG.exists():
        OUT_GPKG.unlink()
    buildings.to_file(OUT_GPKG, layer="building", driver="GPKG", engine="pyogrio")
    entrances.to_file(OUT_GPKG, layer="entrance", driver="GPKG", engine="pyogrio")
    parcels.to_csv(PROC / "parcel.csv", index=False, encoding="utf-8")
    dongs.to_csv(PROC / "admin_dong.csv", index=False, encoding="utf-8")
    links.to_csv(PROC / "building_entrance.csv", index=False, encoding="utf-8")
    conflicts.to_csv(PROC / "review_gis_id_conflict.csv", index=False, encoding="utf-8")

    geom_kind = buildings.geometry.geom_type
    report = {
        "building": {
            "total": len(buildings),
            "address_linked": int((buildings["kind"] == "building").sum()),
            "address_linked_polygon": int(((buildings["kind"] == "building") & geom_kind.isin(["Polygon", "MultiPolygon"])).sum()),
            "address_linked_point": int(((buildings["kind"] == "building") & (geom_kind == "Point")).sum()),
            "address_unlinked": int((buildings["kind"] == "building-gis").sum()),
            "shared_polygons": int(poly_of.value_counts().gt(1).sum()),
            "gis_exact_duplicates_merged": merged,
            "gis_id_conflicts_for_review": len(conflicts),
            "without_pnu": int(buildings["pnu"].isna().sum()),
        },
        "entrance": {"main": int((entrances["kind"] == "main").sum()),
                     "sub": int((entrances["kind"] == "sub").sum()),
                     "building_links": len(links)},
        "parcel": {"total": len(parcels),
                   "with_complex_info": int(parcels["site_area"].notna().sum()),
                   "register_duplicate_pnu_rows": reg_dup},
        "admin_dong": len(dongs),
    }
    with open(PROC / "objects_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
