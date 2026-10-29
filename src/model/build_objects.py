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
import re
from datetime import date
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
import yaml

from src.model import uri

ROOT = Path(__file__).resolve().parents[2]
PROC = ROOT / "data" / "processed"
AGENCY_DIR = ROOT / "data" / "raw" / "agency_code"
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


def load_agencies(agency_dir: Path = AGENCY_DIR) -> pd.DataFrame:
    """행정표준코드 기관코드 중 현존(존폐여부 0)인 기관만 읽는다."""
    path = next(agency_dir.glob("*유형분류 의미추가*.txt"))
    cols = ["기관코드", "전체기관명", "최하위기관명", "존폐여부"]
    ag = pd.read_csv(path, sep="\t", dtype=str, encoding="cp949", keep_default_na=False,
                     quoting=3, usecols=cols)
    ag = ag[ag["존폐여부"] == "0"].copy()
    ag["name"] = ag["최하위기관명"].str.replace(r"\s+", "", regex=True)
    return ag


def match_agency(name: str, sido: str, sigungu: str, ag: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """민원행정기관 이름을 기관코드에 맞춘다. 앞 규칙에서 후보가 나오면 그 후보로 끝낸다.

    1. 이름이 같은 기관 (시도 이름이 전체기관명에 들어 있어야 한다)
    2. "OO동주민센터"는 시군구 아래의 "OO동"
    3. 기관코드 이름이 우리 이름의 끝부분과 같은 기관 (예: 서울강서염창지구대 -> 염창지구대)
    """
    name = re.sub(r"\s+", "", name)
    in_sido = ag[ag["전체기관명"].str.contains(sido[:2])]
    c = in_sido[in_sido["name"] == name]
    if len(c):
        return c, "이름 일치"
    m = re.fullmatch(r"(.+동)(주민센터|행정복지센터)", name)
    if m:
        c = in_sido[(in_sido["name"] == m.group(1)) & in_sido["전체기관명"].str.contains(sigungu)]
        if len(c):
            return c, "동 이름 일치"
    c = in_sido[in_sido["name"].str.len().ge(4) & in_sido["name"].map(name.endswith)]
    return c, "이름 끝부분 일치" if len(c) else "후보 없음"


def parse_road_address(addr: str) -> tuple[str, str, str]:
    """'서울특별시 강서구 양천로 646-28' -> (지하여부, 본번, 부번)"""
    under = "1" if " 지하 " in f" {addr} " else "0"
    main, _, sub = addr.split()[-1].partition("-")
    return under, main, sub or "0"


def housing_buildings(office, addr_index: pd.Series, bld_geom: pd.Series) -> list[str]:
    """민원행정기관 한 곳이 들어 있는 건물의 건물관리번호 목록.

    도로명주소가 같은 건물을 찾고, 한 주소에 건물이 여럿이면(단지) 시설 위치를 품은 건물만 남긴다.
    품은 건물이 없으면 그 주소의 건물을 모두 돌려준다.
    """
    under, main_no, sub_no = parse_road_address(office["도로명주소"])
    road_code = str(office["시군구코드"]).zfill(5) + str(office["도로명코드"]).zfill(7)
    key = (road_code, under, main_no, sub_no)
    mgts = addr_index.loc[[key]].tolist() if key in addr_index.index else []
    inside = [m for m in mgts if bld_geom[m].contains(office.geometry)]
    return inside or mgts


def main(proc: Path = PROC, agency_dir: Path = AGENCY_DIR) -> None:
    """proc 폴더의 잘라낸 자료(jeungmi_area.gpkg)로 객체를 만들어 같은 폴더에 저장한다."""
    area_gpkg, out_gpkg = proc / "jeungmi_area.gpkg", proc / "objects.gpkg"
    cfg = load_cfg()
    metric = cfg["crs"]["metric"]
    storage = cfg["crs"]["storage"]

    gis = gpd.read_file(area_gpkg, layer="gis_building")
    centers = gpd.read_file(area_gpkg, layer="navi_building_center")
    main_ent = gpd.read_file(area_gpkg, layer="navi_building_entrance")
    sub_ent = gpd.read_file(area_gpkg, layer="navi_sub_entrance")
    register = gpd.read_file(area_gpkg, layer="building_register_summary")
    jibun = pd.read_csv(proc / "navi_jibun.csv", dtype=str, keep_default_na=False)

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
    # 필지 번호를 어느 자료에서 얻었는지 (GIS A2, 내비 대표지번, 총괄표제부)
    pnu_sources = buildings.dropna(subset=["pnu"]).groupby("pnu")["attr_source"].agg(set).to_dict()
    parcels = []
    for p in pnus:
        assert len(p) == 19 and p.isdigit(), p
        r = reg.loc[p] if p in reg.index else None
        srcs = set(pnu_sources.get(p, set())) | ({"register"} if r is not None else set())
        parcels.append({
            "uri": uri.parcel(p), "pnu": p, "sources": ";".join(sorted(srcs)),
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

    # 7. 역: config/area.yaml에 적은 역사 건물의 건물관리번호를 그대로 쓴다
    by_mgt = buildings.set_index("bldg_mgt_no")
    stations = gpd.GeoDataFrame([{
        "uri": uri.station(s["bldg_mgt_no"]), "name": s["name"], "line": s["line"],
        "building_uri": uri.building(s["bldg_mgt_no"]),
        "adm_dong_code": by_mgt.loc[s["bldg_mgt_no"], "adm_dong_code"],
        "geometry": by_mgt.loc[s["bldg_mgt_no"], "geometry"],
    } for s in cfg["stations"]], geometry="geometry", crs=storage)

    # 8. 공공시설: 행정표준코드 기관코드로 URI를 붙이고, 도로명주소가 같은 건물에 housedIn으로 잇는다
    offices = gpd.read_file(area_gpkg, layer="public_office")
    agencies = load_agencies(agency_dir)
    addr_index = centers.set_index(ADDRESS_KEY)["bldg_mgt_no"].sort_index()
    bld_geom = by_mgt["geometry"]  # 건물관리번호 -> 모양 (EPSG:4326, 포함 여부만 보므로 좌표계는 그대로 둔다)
    facilities, facility_links, facility_review = [], [], []
    for _, o in offices.iterrows():
        sido, sigungu = o["도로명주소"].split()[:2]
        cand, rule = match_agency(o["기관명"], sido, sigungu, agencies)
        if len(cand) != 1:
            facility_review.append({"기관명": o["기관명"], "유형": o["유형"], "상세분류": o["상세분류"],
                                    "도로명주소": o["도로명주소"], "규칙": rule,
                                    "후보": "; ".join(cand["기관코드"] + " " + cand["전체기관명"])})
            continue
        a = cand.iloc[0]
        u = uri.public_facility(a["기관코드"])
        mgts = housing_buildings(o, addr_index, bld_geom)
        facilities.append({
            "uri": u, "agency_code": a["기관코드"], "label": o["기관명"], "agency_name": a["전체기관명"],
            "facility_type": f"{o['유형']}/{o['상세분류']}", "match_rule": rule,
            "adm_dong_code": dong_of[mgts[0]] if mgts else None, "geometry": o.geometry,
        })
        facility_links += [{"facility_uri": u, "building_uri": bld_uri[m]} for m in mgts]
    facilities = gpd.GeoDataFrame(facilities, geometry="geometry", crs=storage)
    assert facilities["uri"].is_unique, "기관코드 하나가 두 시설에 붙었다"
    facility_links = pd.DataFrame(facility_links, columns=["facility_uri", "building_uri"])

    # 저장
    if out_gpkg.exists():
        out_gpkg.unlink()
    buildings.to_file(out_gpkg, layer="building", driver="GPKG", engine="pyogrio")
    stations.to_file(out_gpkg, layer="station", driver="GPKG", engine="pyogrio")
    facilities.to_file(out_gpkg, layer="public_facility", driver="GPKG", engine="pyogrio")
    facility_links.to_csv(proc / "facility_building.csv", index=False, encoding="utf-8")
    pd.DataFrame(facility_review).to_csv(proc / "review_public_facility.csv", index=False, encoding="utf-8")
    entrances.to_file(out_gpkg, layer="entrance", driver="GPKG", engine="pyogrio")
    parcels.to_csv(proc / "parcel.csv", index=False, encoding="utf-8")
    dongs.to_csv(proc / "admin_dong.csv", index=False, encoding="utf-8")
    links.to_csv(proc / "building_entrance.csv", index=False, encoding="utf-8")
    conflicts.to_csv(proc / "review_gis_id_conflict.csv", index=False, encoding="utf-8")

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
        "station": len(stations),
        "public_facility": {
            "source_points": len(offices), "with_agency_code": len(facilities),
            "for_review": len(facility_review),
            "by_rule": facilities["match_rule"].value_counts().to_dict(),
            "housed_in_building": int(facility_links["facility_uri"].nunique()),
        },
    }
    with open(proc / "objects_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
