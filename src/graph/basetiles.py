"""공개 지도와 장소 페이지의 배경을 저장소에 굽는다: 벡터 타일, 지도 글꼴, MapLibre 라이브러리.

실행: python -m src.graph.basetiles
출력: web/vendor/ (publish가 공개 사이트의 assets/vendor/로 복사한다. 로컬 서버는 /static/vendor/로 연다)
  base/{z}/{x}/{y}.pbf            증미역 중심에서 동서남북 2.5km 사각형의 z13, z14 타일 (압축을 푼 것, 쓰는 층만)
  base/ATTRIBUTION.txt            제공처 표기, 받은 판, 받은 날, 라이선스
  glyphs/MapMedium/{범위}.pbf     지도 글자 글꼴 8개 범위 (제공처의 Noto Sans Regular 원본)
  glyphs/OFL.txt                  글꼴 라이선스
  maplibre-gl-4.7.1/              maplibre-gl.js, maplibre-gl.css, LICENSE.txt (전에 쓰던 SRI 해시와 같은지 확인한다)

제공처는 키가 필요 없는 OpenFreeMap이다. 받은 판을 고정해 두 컴퓨터에서 같은 결과가 나온다.
타일의 poi(상호), housenumber(번지), building(건물) 층은 구울 때 뺀다. 그리지 않을 뿐 남겨 두면 그래프에 없는 상호가
저장소와 공개 파일에 들어가기 때문이다. 타일 맨 위 구조는 layers 반복 필드뿐이라 protobuf 바깥 껍질만 읽어 층을 고른다.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import math
import sys
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen

import yaml

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "web" / "vendor"
PROVIDER = "https://tiles.openfreemap.org"
VERSION = "20261025_080012_pt"  # 2026-11-02에 본 판. 바꾸면 ATTRIBUTION.txt도 같이 바뀐다
ZOOMS = (13, 14)
HALF_M = 2500  # 중심에서 동서남북 거리
KEEP = ("transportation", "transportation_name", "water", "water_name", "waterway", "park", "landuse", "landcover", "place", "boundary")
FONT = "Noto Sans Regular"  # 제공처에 Medium은 없다 (404)
FONT_DIR = "MapMedium"
# 명세 7.4절의 8개에 65024-65279(이체 선택자 등)를 더했다. 배경 지명에 이 범위 글자가 있어 없으면 404가 난다 (2026-11-03 확인)
RANGES = ("0-255", "256-511", "8192-8447", "8448-8703", "12288-12543", "12544-12799", "12800-13055", "65024-65279", "65280-65535")
OFL_URL = "https://raw.githubusercontent.com/notofonts/latin-greek-cyrillic/main/OFL.txt"
MAPLIBRE = "4.7.1"
MAPLIBRE_FILES = {  # 파일 -> sha384 (web/review.html, web/map.html의 SRI 값과 같다). LICENSE는 확인할 값이 없다
    "dist/maplibre-gl.js": "SYKAG6cglRMN0RVvhNeBY0r3FYKNOJtznwA0v7B5Vp9tr31xAHsZC0DqkQ/pZDmj",
    "dist/maplibre-gl.css": "MinO0mNliZ3vwppuPOUnGa+iq619pfMhLVUXfC4LHwSCvF9H+6P/KO4Q7qBOYV5V",
    "LICENSE.txt": None,
}
ATTRIBUTION = ("OpenFreeMap (https://openfreemap.org), © OpenMapTiles (https://www.openmaptiles.org/), "
               "© OpenStreetMap 기여자 (https://www.openstreetmap.org/copyright)")


def bounds() -> tuple[float, float, float, float]:
    """중심에서 동서남북 2.5km 사각형 [서, 남, 동, 북]. 거리는 config/area.yaml의 거리 좌표계(EPSG:5186)로 잰다.

    web/common.js의 MAP_BOUNDS와 같아야 한다 (tests/test_public_view.py가 본다). 소수 6자리에서 바깥으로 올린다.
    """
    from pyproj import Transformer
    with open(ROOT / "config" / "area.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    lon, lat = cfg["center"]["epsg4326_lonlat"]
    metric = (cfg.get("crs") or {}).get("metric", "EPSG:5186")
    to_m = Transformer.from_crs("EPSG:4326", metric, always_xy=True)
    to_ll = Transformer.from_crs(metric, "EPSG:4326", always_xy=True)
    x, y = to_m.transform(lon, lat)
    corners = [to_ll.transform(x + dx, y + dy) for dx in (-HALF_M, HALF_M) for dy in (-HALF_M, HALF_M)]
    w, s = min(c[0] for c in corners), min(c[1] for c in corners)
    e, n = max(c[0] for c in corners), max(c[1] for c in corners)
    down, up = lambda v: math.floor(v * 1e6) / 1e6, lambda v: math.ceil(v * 1e6) / 1e6
    return down(w), down(s), up(e), up(n)


def tile_range(z: int, box: tuple[float, float, float, float]) -> list[tuple[int, int, int]]:
    """사각형과 겹치는 웹 메르카토르 타일 (z, x, y)."""
    w, s, e, n = box

    def xy(lon, lat):
        k = 2 ** z
        x = (lon + 180) / 360 * k
        r = math.radians(lat)
        y = (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * k
        return int(x), int(y)
    x0, y0 = xy(w, n)
    x1, y1 = xy(e, s)
    return [(z, x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def varint(buf: bytes, i: int) -> tuple[int, int]:
    out = shift = 0
    while True:
        b = buf[i]
        i += 1
        out |= (b & 0x7F) << shift
        if b < 0x80:
            return out, i
        shift += 7


def layer_names(tile: bytes) -> list[str]:
    """타일 안 층 이름 (순서대로). 층은 맨 위 필드 3, 층 이름은 층 안 필드 1이다."""
    return [name for name, _ in split_layers(tile)]


def split_layers(tile: bytes) -> list[tuple[str, bytes]]:
    """타일을 (층 이름, 그 층의 맨 위 필드 바이트 전체) 목록으로 나눈다. 층이 아닌 필드가 있으면 멈춘다."""
    out, i = [], 0
    while i < len(tile):
        start = i
        key, i = varint(tile, i)
        field, wire = key >> 3, key & 7
        if field != 3 or wire != 2:
            raise ValueError(f"타일 맨 위에 층이 아닌 필드가 있다: 필드 {field}, 형식 {wire}")
        size, i = varint(tile, i)
        body = tile[i:i + size]
        i += size
        name, j = None, 0
        while j < len(body):
            k, j = varint(body, j)
            f, w = k >> 3, k & 7
            if w == 2:
                n, j = varint(body, j)
                if f == 1:
                    name = body[j:j + n].decode("utf-8")
                    break
                j += n
            elif w == 0:
                _, j = varint(body, j)
            elif w == 5:
                j += 4
            elif w == 1:
                j += 8
            else:
                raise ValueError(f"층 안에 모르는 형식 {w}")
        if name is None:
            raise ValueError("이름 없는 층")
        out.append((name, tile[start:i]))
    return out


def keep_layers(tile: bytes, keep=KEEP) -> bytes:
    return b"".join(raw for name, raw in split_layers(tile) if name in keep)


def unzip(data: bytes) -> bytes:
    return gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data


def get(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "jeungmi-geokg basetiles (https://github.com/yeodh10/jeungmi-geokg)"})
    with urlopen(req, timeout=60) as r:  # 200이 아니면 HTTPError
        data = r.read()
        return gzip.decompress(data) if r.headers.get("Content-Encoding") == "gzip" else data


def main() -> None:
    box = bounds()
    base = VENDOR / "base"
    tiles = [t for z in ZOOMS for t in tile_range(z, box)]
    total = 0
    seen = set()
    for z, x, y in tiles:
        raw = unzip(get(f"{PROVIDER}/planet/{VERSION}/{z}/{x}/{y}.pbf"))
        seen |= set(layer_names(raw))
        out = keep_layers(raw)
        f = base / str(z) / str(x) / f"{y}.pbf"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(out)
        total += len(out)
        print(f"타일 {z}/{x}/{y}: {len(raw):,} -> {len(out):,} 바이트")
    (base / "ATTRIBUTION.txt").write_text(
        "증미역 지도 배경 타일\n\n"
        f"표기: {ATTRIBUTION}\n"
        f"제공처: {PROVIDER}/planet (키 없음)\n"
        f"받은 판: {VERSION}\n"
        f"받은 날: {date.today().isoformat()}\n"
        f"범위: 증미역 중심에서 동서남북 {HALF_M / 1000:g}km 사각형 [서, 남, 동, 북] = {list(box)}, 배율 {ZOOMS[0]}~{ZOOMS[-1]}, {len(tiles)}장\n"
        f"남긴 층: {', '.join(KEEP)}\n"
        f"뺀 층: {', '.join(sorted(seen - set(KEEP)))}\n\n"
        "지도 자료는 OpenStreetMap 기여자의 자료이고 Open Database License(ODbL 1.0, https://opendatacommons.org/licenses/odbl/)를 따릅니다.\n"
        "타일 형식과 층 구성은 OpenMapTiles 스키마(https://openmaptiles.org/schema/)입니다.\n"
        "이 폴더의 타일은 위 제공처에서 받은 것에서 층만 뺐고, 값은 고치지 않았습니다.\n", encoding="utf-8")

    glyphs = VENDOR / "glyphs" / FONT_DIR
    glyphs.mkdir(parents=True, exist_ok=True)
    gtotal = 0
    for r in RANGES:
        data = unzip(get(f"{PROVIDER}/fonts/{FONT.replace(' ', '%20')}/{r}.pbf"))
        (glyphs / f"{r}.pbf").write_bytes(data)
        gtotal += len(data)
    (VENDOR / "glyphs" / "OFL.txt").write_bytes(get(OFL_URL))

    lib = VENDOR / f"maplibre-gl-{MAPLIBRE}"
    lib.mkdir(parents=True, exist_ok=True)
    for name, sri in MAPLIBRE_FILES.items():
        data = get(f"https://unpkg.com/maplibre-gl@{MAPLIBRE}/{name}")
        if sri and base64.b64encode(hashlib.sha384(data).digest()).decode() != sri:
            sys.exit(f"{name}의 해시가 전에 쓰던 값과 다르다. 받지 않는다")
        (lib / Path(name).name).write_bytes(data)
    print(f"사각형 {box}, 타일 {len(tiles)}장 {total:,} 바이트, 글꼴 {len(RANGES)}개 {gtotal:,} 바이트")
    print("뺀 층:", ", ".join(sorted(seen - set(KEEP))))


if __name__ == "__main__":
    main()
