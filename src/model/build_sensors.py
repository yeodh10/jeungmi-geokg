"""서울시 도시데이터 센서(S-DoT) 가운데 대상 범위 안의 센서와 그 관측값을 객체로 만든다.

실행: python -m src.model.build_sensors
입력: data/raw/sdot/location.xlsx            설치 위치정보 (열린데이터광장 OA-15969, 시리얼, 주소, WGS84 위경도)
      data/raw/sdot/nature_*.csv             주간 환경정보 (1시간마다 최대, 평균, 최소)
      data/processed/objects.gpkg 의 building  센서를 단 건물을 찾을 때 쓴다
출력: data/processed/sensor.csv, sensor_obs.csv, sensors_report.json

관측값은 1시간 평균만 쓴다. 진동은 열린데이터광장이 2022년 12월부터 단위를 잘못 적었다고 알려서 넣지 않는다.
"""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import yaml
from shapely.geometry import Point

from src.model import uri

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "sdot"
PROC = ROOT / "data" / "processed"

# 측정 항목: 키 -> (주간 CSV의 '평균' 열 이름, 열 번호). 열 번호는 2026년 10월 파일 머리줄 기준이고, 머리줄로 다시 확인한다
PROPS = {
    "temperature": ("온도 평균(℃)", 7),
    "humidity": ("습도 평균(%)", 10),
    "illuminance": ("조도 평균(lux)", 19),
    "uv": ("자외선 평균(UV)", 22),
    "noise": ("소음 평균(dB)", 25),
}


def read_locations() -> pd.DataFrame:
    x = pd.read_excel(RAW / "location.xlsx", header=0, dtype={"모델 시리얼(*)": str})
    need = ["모델 시리얼(*)", "주소", "좌표 구분코드", "위도", "경도"]
    assert all(c in x.columns for c in need), list(x.columns)
    assert set(x["좌표 구분코드"]) == {"W84"}, set(x["좌표 구분코드"])  # 모두 WGS84 위경도
    return x.rename(columns={"모델 시리얼(*)": "serial", "주소": "address", "위도": "lat", "경도": "lon"})[["serial", "address", "lat", "lon"]]


def read_weeks(serials: set[str]) -> pd.DataFrame:
    """주간 CSV를 모두 읽어 대상 센서의 행만 남긴다. 데이터 줄이 머리줄보다 열이 많아(빈 열) 열 번호로 읽는다."""
    frames = []
    for path in sorted(RAW.glob("nature_*.csv")):
        header = path.read_bytes().split(b"\n", 1)[0].decode("cp949").strip().split(",")
        for name, idx in PROPS.values():
            assert header[idx] == name, (path.name, idx, header[idx], name)
        assert header[1] == "시리얼" and header[2] == "측정시간" and header[3] == "지역", header[:6]
        cols = [0, 1, 2, 3] + [i for _, i in PROPS.values()]
        df = pd.read_csv(path, encoding="cp949", dtype=str, header=None, skiprows=1, usecols=cols, keep_default_na=False)
        df.columns = ["model", "serial", "time", "area"] + list(PROPS)
        frames.append(df[df.serial.isin(serials)].assign(file=path.name))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["model", "serial", "time", "area", *PROPS, "file"])


def main() -> None:
    with open(ROOT / "config" / "area.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    metric = cfg["crs"]["metric"]
    center = Point(*cfg["center"]["epsg5186_xy"])

    loc = read_locations()
    pts = gpd.GeoDataFrame(loc, geometry=gpd.points_from_xy(loc.lon, loc.lat), crs="EPSG:4326").to_crs(metric)
    pts["dist_station_m"] = pts.distance(center)
    near = pts[pts.dist_station_m <= cfg["radius_m"]].copy()

    # 센서를 단 건물: 설치 주소와 도로명주소가 같은 건물. 한 주소에 건물이 여러 동이면(아파트 단지) 센서 점에서 가장 가까운 동
    buildings = gpd.read_file(PROC / "objects.gpkg", layer="building").to_crs(metric)
    buildings = buildings[buildings.road_address.notna()]
    rows = []
    for r in near.itertuples():
        same = buildings[buildings.road_address == r.address]
        best, dist = None, None
        if len(same):
            d = same.distance(r.geometry)
            best, dist = same.loc[d.idxmin(), "uri"], round(float(d.min()), 1)
        rows.append({"uri": uri.sensor(r.serial), "serial": r.serial, "address": r.address, "lon": r.lon, "lat": r.lat,
                     "dist_station_m": round(r.dist_station_m, 1), "building_uri": best or "", "building_dist_m": dist,
                     "same_address_buildings": len(same)})
    sensors = pd.DataFrame(rows)

    weeks = read_weeks(set(near.serial))
    obs = []
    for r in weeks.itertuples():
        stamp = r.time.replace("_", "T")  # 2026-10-12_00:07:00 -> 2026-10-12T00:07:00 (파일에 시간대 표기가 없어 그대로 둔다)
        compact = stamp[:16].replace("-", "").replace("T", "").replace(":", "")
        for key in PROPS:
            v = getattr(r, key)
            if v == "":
                continue
            obs.append({"uri": uri.observation(r.serial, compact, key), "sensor_uri": uri.sensor(r.serial), "prop": key,
                        "time": stamp, "value": float(v), "file": r.file})
    obs = pd.DataFrame(obs, columns=["uri", "sensor_uri", "prop", "time", "value", "file"])
    assert obs.uri.is_unique, "한 센서가 한 시각에 같은 항목을 두 번 쟀다"

    # 센서 모델 번호와 설치 지역 종류는 관측 파일에만 있다
    meta = weeks.groupby("serial").agg(model=("model", "first"), area=("area", "first"))
    sensors["model"] = sensors.serial.map(meta.model).fillna("")
    sensors["area_type"] = sensors.serial.map(meta.area).fillna("")
    sensors.to_csv(PROC / "sensor.csv", index=False, encoding="utf-8")
    obs.to_csv(PROC / "sensor_obs.csv", index=False, encoding="utf-8")

    report = {
        "sensors_in_area": len(sensors), "with_building": int((sensors.building_uri != "").sum()),
        "with_observations": int(sensors.serial.isin(weeks.serial).sum()),
        "observations": len(obs), "by_prop": obs.prop.value_counts().to_dict(),
        "period": [obs.time.min(), obs.time.max()] if len(obs) else [], "files": sorted(set(obs.file)),
    }
    (PROC / "sensors_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
