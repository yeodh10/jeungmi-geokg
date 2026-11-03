"""상호(소상공인 상가정보)를 범위로 자르고 우리 건물, 필지에 잇는다.

실행: python -m src.model.build_business      (표를 만들고 화면 자료까지 쓴다)
입력: data/raw/sbiz/의 서울 CSV (python -m src.ingest.unpack --only sbiz 로 푼다. 없으면 _archives의 압축에서 바로 읽는다),
      data/processed/objects.gpkg, data/processed/parcel.csv, config/area.yaml, config/places_extra.yaml
출력: data/processed/business.csv            그래프 입력 (src.graph.build_graph가 jgk:Business로 넣는다). 범위 안 모든 행
      data/processed/site/assets/map/business.geojson   공개 지도의 상호 찾기용 (공개하는 것만)

- 범위: 경도, 위도가 config/area.yaml의 원 안인 행.
- 객체: 원천 행 하나가 객체 하나다. URI는 상가업소번호 그대로 business/{상가업소번호}. 같은 가게가 두 행이어도 지우지 않는다.
- 잇기 (linked_by): 상가정보의 건물관리번호가 우리 건물과 같으면 그 건물(jgk:inBuilding),
  아니면 PNU가 같은 건물이 하나일 때만 그 건물, 나머지는 PNU가 우리 필지에 있으면 필지에만(jgk:onParcel) 잇는다.
  주소 미연결 건물(building-gis)도 PNU가 있어 PNU로 이어질 수 있다.
- 글자: 원천 값의 가운뎃점('과학'과 '기술' 사이의 점)은 쉼표로 바꿔 넣는다(과학, 기술). 이 프로젝트의 글자 규칙이다. 다른 글자는 그대로 둔다.
- 공개: 주택 건물 안 업체는 개인 집 주소일 수 있어 config/area.yaml의 publish.business 규칙에 맞는 곳만 공개한다(is_public).
  공개 사이트의 상호 페이지(src.graph.publish)도 이 함수로 고른다.
- 화면 목록: 같은 건물 안에서 한 이름이 다른 이름을 품고 업종이 가까우면(same_store) 한 줄로 합치고 원천 행 수(rows)를 적는다
  (올리브영증미역점과 씨제이올리브영증미역점). 원천 행은 표와 그래프에 그대로 있다.
- 값을 지어내지 않는다: 층 정보가 없으면 비워 둔다. 영업시간, 전화, 리뷰는 이 자료에 없다.
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

from src.model import uri

ROOT = Path(__file__).resolve().parents[2]
PROC = ROOT / "data" / "processed"
RAW = ROOT / "data" / "raw" / "sbiz"
ARCHIVES = ROOT / "data" / "raw" / "_archives"
ARCHIVE_GLOB = "소상공인시장진흥공단_상가(상권)정보_*.zip"
COLUMNS = ["상가업소번호", "상호명", "지점명", "상권업종대분류명", "상권업종중분류명", "상권업종소분류명", "지번코드", "건물관리번호",
           "건물명", "도로명주소", "층정보", "경도", "위도"]
# 원천 칸 -> 표 칸
RENAME = {"상가업소번호": "store_no", "상호명": "name", "지점명": "branch", "상권업종대분류명": "cat_large", "상권업종중분류명": "cat_middle",
          "상권업종소분류명": "cat_small", "지번코드": "src_pnu", "건물관리번호": "src_bldg_mgt_no", "건물명": "src_building_name",
          "도로명주소": "road_address", "층정보": "floor", "경도": "lon", "위도": "lat"}
LINK_NO, LINK_PNU, LINK_PARCEL = "건물관리번호", "PNU", "필지만"  # jgk:linkedBy 값 (src/graph/ontology.ttl, shapes.ttl과 같다)
DOT = re.compile(r"\s*[\u00b7\u2027\u30fb\u318d\uff65]\s*")  # 가운뎃점 다섯 가지 (글자 그대로 쓰지 않는다)
TEXT_COLS = ["name", "branch", "cat_large", "cat_middle", "cat_small", "src_building_name", "road_address", "floor"]


def plain(v):
    """가운뎃점을 쉼표로. 빈 값은 None."""
    if not isinstance(v, str) or not v.strip():
        return None
    return DOT.sub(", ", v.strip())


def zip_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:
        return info.filename
    return info.filename.encode("cp437").decode("cp949", "replace")


def read_seoul() -> pd.DataFrame:
    found = sorted(RAW.glob("*서울*.csv")) if RAW.exists() else []
    if found:
        return pd.read_csv(found[-1], dtype=str, encoding="utf-8-sig", usecols=COLUMNS)
    archives = sorted(ARCHIVES.glob(ARCHIVE_GLOB))
    if not archives:
        raise SystemExit(f"상가정보가 없습니다. {ARCHIVE_GLOB}을 data/raw/_archives/에 넣고 python -m src.ingest.unpack --only sbiz 를 실행하세요.")
    with zipfile.ZipFile(archives[-1]) as z:
        info = next(i for i in z.infolist() if "서울" in zip_name(i))
        return pd.read_csv(io.BytesIO(z.read(info)), dtype=str, encoding="utf-8-sig", usecols=COLUMNS)


def load_rule() -> dict:
    cfg = yaml.safe_load((ROOT / "config" / "area.yaml").read_text(encoding="utf-8"))
    r = cfg["publish"]["business"]
    return {"home_uses": set(r["home_uses"]), "walk_in": set(r["walk_in"])}


def open_floor(floor) -> bool:
    """1층이나 지하. 원천 층정보는 '1', '지', 'B1' 같은 값이다."""
    return isinstance(floor, str) and (floor == "1" or floor.startswith("지") or re.fullmatch(r"B\d+", floor) is not None)


def is_public(uses: list, cat_large, floor, rule: dict, linked: bool = True) -> bool:
    """공개 사이트에 내도 되는 업체인지. uses는 이은 건물(필지에만 이었으면 그 필지의 모든 건물)의 용도 원천 값.

    주택 건물이 하나라도 있으면 1층이나 지하이고 손님이 찾아오는 대분류일 때만 공개한다. 어디에도 잇지 못했으면 공개하지 않는다.
    """
    if not linked:
        return False
    if not any(u in rule["home_uses"] for u in uses):
        return True
    return cat_large in rule["walk_in"] and open_floor(floor)


def link(df: pd.DataFrame, buildings: gpd.GeoDataFrame, parcel_uris: set[str]) -> pd.DataFrame:
    by_no = dict(zip(buildings["bldg_mgt_no"], buildings["uri"]))
    on_lot = buildings[buildings["pnu"].notna()].groupby("pnu")["uri"].agg(list)
    only_one = {pnu: uris[0] for pnu, uris in on_lot.items() if len(uris) == 1}
    b_uri, p_uri, how = [], [], []
    for no, pnu in zip(df["src_bldg_mgt_no"], df["src_pnu"]):
        parcel = uri.parcel(pnu) if isinstance(pnu, str) and len(pnu) == 19 else None
        if no in by_no:
            b_uri.append(by_no[no]), p_uri.append(None), how.append(LINK_NO)
        elif pnu in only_one:
            b_uri.append(only_one[pnu]), p_uri.append(None), how.append(LINK_PNU)
        elif parcel in parcel_uris:
            b_uri.append(None), p_uri.append(parcel), how.append(LINK_PARCEL)
        else:
            b_uri.append(None), p_uri.append(None), how.append(None)
    return df.assign(building_uri=b_uri, parcel_uri=p_uri, linked_by=how)


def mark_public(df: pd.DataFrame, buildings: gpd.GeoDataFrame, rule: dict) -> pd.DataFrame:
    uses = dict(zip(buildings["uri"], buildings["use_name"]))
    lot_uses = buildings[buildings["pnu"].notna()].groupby("pnu")["use_name"].agg(list).to_dict()
    public, home = [], []
    for r in df.itertuples():
        if isinstance(r.building_uri, str):
            here = [uses.get(r.building_uri)]
        elif isinstance(r.parcel_uri, str):
            here = lot_uses.get(r.parcel_uri.rsplit("/", 1)[-1], [])
        else:
            here = []
        public.append(is_public(here, r.cat_large, r.floor, rule, linked=isinstance(r.linked_by, str)))
        home.append(any(u in rule["home_uses"] for u in here))
    return df.assign(public=public, in_home=home)


def build(proc: Path = PROC) -> dict:
    """data/processed/business.csv를 만든다 (그래프 입력)."""
    cfg = yaml.safe_load((ROOT / "config" / "area.yaml").read_text(encoding="utf-8"))
    lon, lat = cfg["center"]["epsg4326_lonlat"]
    circle = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(cfg["crs"]["metric"]).buffer(cfg["radius_m"]).iloc[0]
    df = read_seoul()
    pts = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["경도"].astype(float), df["위도"].astype(float)), crs=4326).to_crs(cfg["crs"]["metric"])
    df = df[pts.within(circle).values].rename(columns=RENAME).copy()
    assert df["store_no"].is_unique, "상가업소번호가 겹친다"
    for c in TEXT_COLS:
        df[c] = df[c].map(plain)
    df.insert(0, "uri", df["store_no"].map(uri.business))
    buildings = gpd.read_file(proc / "objects.gpkg", layer="building")
    parcels = set(pd.read_csv(proc / "parcel.csv", dtype=str)["uri"])
    df = mark_public(link(df, buildings, parcels), buildings, load_rule())
    df.to_csv(proc / "business.csv", index=False, encoding="utf-8")
    return {"in_area": len(df), "linked_by": df["linked_by"].fillna("없음").value_counts().to_dict(),
            "in_home_building": int(df["in_home"].sum()), "public": int(df["public"].sum()), "not_public": int((~df["public"]).sum()),
            "no_floor": int(df["floor"].isna().sum())}


def tidy(s) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", str(s).lower()) if isinstance(s, str) else ""


def same_store(short: dict, long: dict) -> bool:
    """짧은 이름(short)이 긴 이름(long)에 들어 있을 때, 같은 가게로 볼 만큼 업종이 가까운지.

    같은 소분류면 같은 가게로 본다. 소분류가 달라도 대분류가 같고 긴 이름이 세 글자 이하만 더 길면 같은 가게로 본다.
    앞말이 붙은 같은 가게(올리브영증미역점은 화장품 소매업, 씨제이올리브영증미역점은 그 외 기타 상품 전문 소매업)가 소분류만으로는 갈라지기 때문이다.
    대분류만 보면 큰 매장 안의 다른 가게(홈플러스와 그 안 의류 매장들)까지 합쳐져서 글자 수를 함께 본다.
    """
    return short["cat"] == long["cat"] or (short["big"] == long["big"] and len(long["key"]) - len(short["key"]) <= 3)


def merge_same_store(rows: list[dict]) -> list[dict]:
    """같은 건물 안에서 한 이름이 다른 이름을 품고 업종이 가까우면(same_store) 한 줄로 합치고 원천 행 수를 rows에 적는다.

    회사 앞말('씨제이' 같은) 목록을 쓰지 않고 글자 포함 관계로 본다. 네 글자 미만은 우연히 겹치기 쉬워 합치지 않는다.
    """
    out: list[dict] = []
    for r in sorted(rows, key=lambda r: (len(r["key"]), r["p"])):
        twin = next((o for o in out if r["bu"] and o["bu"] == r["bu"] and len(o["key"]) >= 4 and o["key"] in r["key"] and same_store(o, r)), None)
        if twin:
            twin["rows"] += 1
        else:
            out.append(r | {"rows": 1})
    return out


def site_rows(proc: Path = PROC) -> list[dict]:
    """공개 화면에 내는 상호 줄 (합친 뒤)과 직접 넣은 장소."""
    df = pd.read_csv(proc / "business.csv", dtype=str, keep_default_na=False)
    buildings = gpd.read_file(proc / "objects.gpkg", layer="building")
    names = {u: n for u, n in zip(buildings["uri"], buildings["label"].fillna(buildings["road_address"])) if isinstance(n, str)}
    rows = []
    for r in df[df["public"] == "True"].itertuples():
        bu = r.building_uri.rsplit("/id/", 1)[-1] if r.building_uri else ""
        rows.append({"n": r.name + (f" {r.branch}" if r.branch else ""), "key": tidy(r.name) + tidy(r.branch), "p": r.uri.rsplit("/id/", 1)[-1],
                     "big": r.cat_large, "cat": r.cat_small, "floor": r.floor, "addr": r.road_address, "bu": bu,
                     "bn": names.get(r.building_uri, "") or "" if r.building_uri else "", "x": round(float(r.lon), 6), "y": round(float(r.lat), 6), "src": ""})
    rows = merge_same_store(rows)

    # 상가정보에 없는 곳을 출처와 함께 직접 넣은 장소 (그래프 객체가 아니다)
    path = ROOT / "config" / "places_extra.yaml"
    extra = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else []
    by_no = buildings[buildings["bldg_mgt_no"].notna()].set_index("bldg_mgt_no")
    for e in extra or []:
        b = by_no.loc[e["building"]]
        c = b.geometry.representative_point() if b.geometry.geom_type != "Point" else b.geometry
        rows.append({"n": e["name"], "key": tidy(e["name"]), "p": "", "big": "", "cat": "", "floor": "", "addr": e["address"],
                     "bu": b["uri"].rsplit("/id/", 1)[-1], "bn": names.get(b["uri"], ""), "x": round(c.x, 6), "y": round(c.y, 6),
                     "src": f"직접 넣은 장소, 출처: {e['source_name']} ({e['source']}, {e['checked']} 확인)", "rows": 1})
    return rows


def write_site(proc: Path = PROC) -> dict:
    """공개 지도의 상호 찾기 자료. src.graph.publish가 site 폴더를 새로 만들므로 publish 뒤에 부른다."""
    rows = site_rows(proc)
    keys = ("n", "p", "big", "cat", "floor", "addr", "bu", "bn", "src")
    features = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["x"], r["y"]]},
                 "properties": {k: r[k] for k in keys if r[k]} | ({"rows": r["rows"]} if r["rows"] > 1 else {}) | {"u": f"place/{i}"}}
                for i, r in enumerate(rows)]
    out = proc / "site" / "assets" / "map" / "business.geojson"
    out.parent.mkdir(parents=True, exist_ok=True)
    # allow_nan=False: 브라우저는 NaN이 든 JSON을 읽지 못한다
    out.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    return {"lines": len(features), "merged_lines": sum(1 for r in rows if r["rows"] > 1), "extra": sum(1 for r in rows if r["src"]), "bytes": out.stat().st_size}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    report = {"table": build()}
    if (PROC / "site").exists():
        report["site"] = write_site()
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
