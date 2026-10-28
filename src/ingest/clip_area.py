"""원본 데이터에서 대상 범위(증미역 반경 1km)만 잘라 data/processed/에 저장한다.

실행: python -m src.ingest.clip_area
입력: data/raw/ 아래 원본 (받는 방법은 docs/data-sources.md)
출력: data/processed/jeungmi_area.gpkg (EPSG:4326), data/processed/clip_report.json
"""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import yaml
from shapely.geometry import Point

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
OUT_DIR = ROOT / "data" / "processed"
OUT_GPKG = OUT_DIR / "jeungmi_area.gpkg"

# 내비게이션용DB 레이아웃 ([가이드]내비게이션용DB 활용방법.pdf, 붙임 1~3)
NAVI_BUILD_COLS = [
    "addr_emd_code", "sido", "sigungu", "emd", "road_code", "road_name",
    "underground", "bldg_main_no", "bldg_sub_no", "zip_code", "bldg_mgt_no",
    "sigungu_bldg_name", "bldg_use", "adm_dong_code", "adm_dong_name",
    "floors_above", "floors_below", "apt_type", "bldg_count",
    "detail_bldg_name", "bldg_name_history", "detail_bldg_name_history",
    "residential", "center_x", "center_y", "entrance_x", "entrance_y",
    "sido_en", "sigungu_en", "emd_en", "road_name_en", "eup_or_dong",
    "change_code",
]
NAVI_JIBUN_COLS = [
    "legal_dong_code", "sido", "sigungu", "emd", "ri", "is_mountain",
    "jibun_main", "jibun_sub", "road_code", "underground", "bldg_main_no",
    "bldg_sub_no", "jibun_seq", "sido_en", "sigungu_en", "emd_en", "ri_en",
    "change_code", "bldg_mgt_no", "addr_emd_code",
]
NAVI_RS_ENTRC_COLS = [
    "sigungu_code", "entrance_seq", "road_code", "underground",
    "bldg_main_no", "bldg_sub_no", "legal_dong_code", "entrance_type",
    "entrance_x", "entrance_y", "change_code",
]
NAVI_CRS = "EPSG:5179"  # 레이아웃 문서: GRS80 UTM-K


def load_area() -> dict:
    with open(ROOT / "config" / "area.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_navi_txt(path: Path, cols: list[str]) -> pd.DataFrame:
    # 줄 끝에 구분자가 하나 더 붙어 있어 빈 열이 하나 생긴다
    df = pd.read_csv(path, sep="|", header=None, dtype=str, encoding="cp949",
                     keep_default_na=False, quoting=3)
    df = df.iloc[:, : len(cols)]
    df.columns = cols
    return df


def points_from_xy(df: pd.DataFrame, xcol: str, ycol: str, crs: str) -> gpd.GeoDataFrame:
    # 비공개, 공개제한 건물은 좌표가 비어 있다
    has_xy = (df[xcol] != "") & (df[ycol] != "")
    df = df[has_xy].copy()
    geom = gpd.points_from_xy(df[xcol].astype(float), df[ycol].astype(float))
    return gpd.GeoDataFrame(df, geometry=geom, crs=crs)


def clip(gdf: gpd.GeoDataFrame, area, metric_crs: str, rule: str) -> gpd.GeoDataFrame:
    g = gdf.to_crs(metric_crs)
    mask = g.intersects(area) if rule == "intersects" else g.within(area)
    return gdf[mask.values]


def main() -> None:
    cfg = load_area()
    metric = cfg["crs"]["metric"]
    storage = cfg["crs"]["storage"]
    cx, cy = cfg["center"]["epsg5186_xy"]
    area = Point(cx, cy).buffer(cfg["radius_m"], quad_segs=64)
    bbox = area.bounds
    rule_poly = cfg["clip_rule"]["polygon"]
    rule_point = cfg["clip_rule"]["point"]

    layers: dict[str, gpd.GeoDataFrame] = {}

    # 1. GIS건물통합정보 (브이월드, EPSG:5186 폴리곤)
    shp = next((RAW / "vworld_gis_building").glob("AL_D010_*.shp"))
    bld = gpd.read_file(shp, encoding="cp949", bbox=bbox, engine="pyogrio")
    layers["gis_building"] = clip(bld, area, metric, rule_poly)

    # 2. 내비게이션용DB 건물 (건물중심점, 주출입구)
    navi_dir = RAW / "juso_navi_db"
    nb = read_navi_txt(navi_dir / "match_build_seoul.txt", NAVI_BUILD_COLS)
    centers = clip(points_from_xy(nb, "center_x", "center_y", NAVI_CRS), area, metric, rule_point)
    layers["navi_building_center"] = centers
    # 중심점이 범위 안에 있는 건물의 주출입구만 남긴다 (출입구가 범위 밖에 살짝 걸쳐도 같은 건물로 묶기 위해)
    ent = points_from_xy(nb[nb["bldg_mgt_no"].isin(centers["bldg_mgt_no"])],
                         "entrance_x", "entrance_y", NAVI_CRS)
    layers["navi_building_entrance"] = ent

    # 3. 내비게이션용DB 보조출입구
    rs = read_navi_txt(navi_dir / "match_rs_entrc.txt", NAVI_RS_ENTRC_COLS)
    rs = rs[rs["sigungu_code"].str.startswith("11")]
    layers["navi_sub_entrance"] = clip(points_from_xy(rs, "entrance_x", "entrance_y", NAVI_CRS),
                                       area, metric, rule_point)

    # 4. 내비게이션용DB 지번 (좌표 없음, 범위 안 건물과 건물관리번호로 연결)
    nj = read_navi_txt(navi_dir / "match_jibun_seoul.txt", NAVI_JIBUN_COLS)
    navi_jibun = nj[nj["bldg_mgt_no"].isin(centers["bldg_mgt_no"])]

    # 5. 민원행정기관 전자지도 (EPSG:5179 점)
    po_shp = next((RAW / "juso_public_office").glob("*.shp"))
    po = gpd.read_file(po_shp, encoding="cp949", engine="pyogrio")
    layers["public_office"] = clip(po, area, metric, rule_point)

    # 6. 서울시 건축물대장 총괄표제부 (CSV, X/Y는 EPSG:5179로 확인됨, docs/data-sources.md)
    reg_csv = next((RAW / "seoul_building_register").glob("*.csv"))
    reg = pd.read_csv(reg_csv, dtype=str, encoding="cp949", keep_default_na=False)
    reg = points_from_xy(reg, "X좌표", "Y좌표", "EPSG:5179")
    layers["building_register_summary"] = clip(reg, area, metric, rule_point)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if OUT_GPKG.exists():
        OUT_GPKG.unlink()
    report = {"center": cfg["center"]["epsg4326_lonlat"], "radius_m": cfg["radius_m"], "layers": {}}
    for name, gdf in layers.items():
        out = gdf.to_crs(storage)
        out.to_file(OUT_GPKG, layer=name, driver="GPKG", engine="pyogrio")
        report["layers"][name] = {"count": len(out), "source_crs": str(gdf.crs)}
    navi_jibun.to_csv(OUT_DIR / "navi_jibun.csv", index=False, encoding="utf-8")
    report["tables"] = {"navi_jibun": len(navi_jibun)}

    with open(OUT_DIR / "clip_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
