"""상호(소상공인 상가정보)를 범위로 자르고 우리 건물에 잇는다.

실행: python -m src.model.build_business
입력: data/raw/_archives/소상공인시장진흥공단_상가(상권)정보_*.zip 안의 서울 CSV, data/processed/objects.gpkg, config/places_extra.yaml
출력: data/processed/business.csv (전부), data/processed/site/assets/map/business.geojson (공개하는 것만)

- 범위: 경도, 위도가 config/area.yaml의 원 안인 행.
- 건물 잇기: 건물관리번호가 같으면 그 건물, 없으면 PNU가 같은 건물이 하나일 때만. 어떻게 이었는지 linked_by에 적는다.
- 공개: 주택 건물 안 업체는 개인 집 주소일 수 있어 1층이나 지하의, 손님이 찾아오는 업종만 공개한다. 나머지는 지역 화면에서만 쓴다.
- 같은 가게가 두 행일 때가 있다(올리브영증미역점, 씨제이올리브영증미역점). 원천 행은 csv에 그대로 두고, 공개 목록에서만
  같은 건물 안에서 한 이름이 다른 이름을 품으면 짧은 쪽으로 합친다.
"""
from __future__ import annotations

import io
import json
import re
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import yaml
from shapely.geometry import Point

ROOT = Path(__file__).resolve().parents[2]
PROC = ROOT / "data" / "processed"
ARCHIVES = ROOT / "data" / "raw" / "_archives"
HOME_USES = ("단독주택", "공동주택", "주택")
WALK_IN = {"음식", "소매", "보건의료", "숙박", "수리·개인", "교육", "예술·스포츠"}  # 손님이 찾아오는 대분류 (원천 값 그대로)
COLUMNS = ["상가업소번호", "상호명", "지점명", "상권업종대분류명", "상권업종중분류명", "상권업종소분류명", "지번코드", "건물관리번호",
           "건물명", "도로명주소", "층정보", "경도", "위도"]


def zip_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:
        return info.filename
    return info.filename.encode("cp437").decode("cp949", "replace")


def read_seoul() -> pd.DataFrame:
    src = sorted(ARCHIVES.glob("소상공인시장진흥공단_상가(상권)정보_*.zip"))[-1]
    with zipfile.ZipFile(src) as z:
        info = next(i for i in z.infolist() if "서울" in zip_name(i))
        return pd.read_csv(io.BytesIO(z.read(info)), dtype=str, encoding="utf-8-sig", usecols=COLUMNS)


def link_buildings(df: pd.DataFrame, buildings: gpd.GeoDataFrame) -> pd.DataFrame:
    by_no = dict(zip(buildings["bldg_mgt_no"], buildings["uri"]))
    on_lot = buildings[buildings["pnu"].notna()].groupby("pnu")["uri"].agg(list)
    only_one = {pnu: uris[0] for pnu, uris in on_lot.items() if len(uris) == 1}
    uris, how = [], []
    for no, pnu in zip(df["건물관리번호"], df["지번코드"]):
        if no in by_no:
            uris.append(by_no[no]), how.append("건물관리번호")
        elif pnu in only_one:
            uris.append(only_one[pnu]), how.append("PNU")
        else:
            uris.append(None), how.append(None)
    return df.assign(building_uri=uris, linked_by=how)


def is_public(row, use: str | None) -> bool:
    if use not in HOME_USES:
        return True
    floor = row["층정보"]
    return row["상권업종대분류명"] in WALK_IN and (floor == "1" or (isinstance(floor, str) and floor.startswith("지")))


def tidy(s) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", str(s).lower())


def merge_same_store(rows: list[dict]) -> list[dict]:
    """같은 건물 안에서 한 이름이 다른 이름을 품으면 한 줄로 합친다. 네 글자 미만은 우연히 겹치기 쉬워 합치지 않는다."""
    out: list[dict] = []
    for r in sorted(rows, key=lambda r: len(r["key"])):
        twin = next((o for o in out if o["bu"] == r["bu"] and r["bu"] and len(o["key"]) >= 4 and o["key"] in r["key"]), None)
        if twin:
            twin["rows"] += 1
        else:
            out.append(r | {"rows": 1})
    return out


def run(proc: Path = PROC) -> dict:
    cfg = yaml.safe_load((ROOT / "config" / "area.yaml").read_text(encoding="utf-8"))
    lon, lat = cfg["center"]["epsg4326_lonlat"]
    circle = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(cfg["crs"]["metric"]).buffer(cfg["radius_m"]).iloc[0]
    df = read_seoul()
    pts = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["경도"].astype(float), df["위도"].astype(float)), crs=4326).to_crs(cfg["crs"]["metric"])
    df = df[pts.within(circle).values].copy()
    buildings = gpd.read_file(proc / "objects.gpkg", layer="building")
    df = link_buildings(df, buildings)
    df.to_csv(proc / "business.csv", index=False, encoding="utf-8")

    uses = dict(zip(buildings["uri"], buildings["use_name"]))
    names = dict(zip(buildings["uri"], buildings["label"].fillna(buildings["road_address"])))
    rows, hidden = [], 0
    for r in df.to_dict("records"):
        if not is_public(r, uses.get(r["building_uri"])):
            hidden += 1
            continue
        name = r["상호명"] + (f" {r['지점명']}" if isinstance(r["지점명"], str) else "")
        rows.append({"n": name, "key": tidy(r["상호명"]) + tidy(r["지점명"] if isinstance(r["지점명"], str) else ""), "big": r["상권업종대분류명"],
                     "cat": r["상권업종소분류명"], "floor": r["층정보"] if isinstance(r["층정보"], str) else "", "addr": r["도로명주소"],
                     "bu": r["building_uri"].rsplit("/id/", 1)[-1] if isinstance(r["building_uri"], str) else "", "bn": names.get(r["building_uri"], "") or "",
                     "x": round(float(r["경도"]), 6), "y": round(float(r["위도"]), 6), "src": ""})
    rows = merge_same_store(rows)

    # 상가정보에 없는 곳을 출처와 함께 직접 넣은 장소
    path = ROOT / "config" / "places_extra.yaml"
    extra = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else []
    centers = buildings[buildings["bldg_mgt_no"].notna()].set_index("bldg_mgt_no")
    for e in extra or []:
        b = centers.loc[e["building"]]
        c = b.geometry.representative_point() if b.geometry.geom_type != "Point" else b.geometry
        rows.append({"n": e["name"], "key": tidy(e["name"]), "big": e.get("big", ""), "cat": e.get("cat", ""), "floor": "", "addr": b["road_address"],
                     "bu": b["uri"].rsplit("/id/", 1)[-1], "bn": b["label"] or "", "x": round(c.x, 6), "y": round(c.y, 6),
                     "src": f"직접 넣은 장소, 출처 {e['source']} ({e['checked']} 확인)", "rows": 1})

    features = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["x"], r["y"]]},
                 "properties": {k: r[k] for k in ("n", "big", "cat", "floor", "addr", "bu", "bn", "src") if isinstance(r[k], str) and r[k]} | {"u": f"place/{i}"}}
                for i, r in enumerate(rows)]
    out = proc / "site" / "assets" / "map" / "business.geojson"
    # allow_nan=False: 브라우저는 NaN이 든 JSON을 읽지 못한다
    out.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    return {"in_area": len(df), "linked_by": df["linked_by"].fillna("없음").value_counts().to_dict(), "hidden_home": hidden,
            "public": len(features), "bytes": out.stat().st_size}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(run())
