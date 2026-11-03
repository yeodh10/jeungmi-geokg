"""공개 경계 공통 함수(src.graph.public), 화면 틀, 구운 배경 확인.

실행: python -m tests.test_public_view
앞쪽은 작은 그래프로 규칙을 보고, 뒤쪽은 실제 그래프와 web/vendor, data/processed/site가 있으면 읽기만 한다.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

import pyoxigraph as ox

from src.graph import basetiles
from src.graph.app_shell import render_app_html
from src.graph.public import (BASE, JGK, UNIT, UNIT_TAIL, display_name, jibun_from_pnu, local_view, public_query_store, public_view)

ROOT = Path(__file__).resolve().parents[1]
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
GEO = "http://www.opengis.net/ont/geosparql#"
LD = {"1150010200": "등촌동", "1150010100": "염창동"}


def store_of(rows) -> ox.Store:
    st = ox.Store()
    lit = lambda v: ox.Literal(v) if not str(v).startswith("http") else ox.NamedNode(v)
    st.bulk_extend([ox.Quad(ox.NamedNode(s), ox.NamedNode(p), lit(o), ox.DefaultGraph()) for s, p, o in rows])
    return st


def check_jibun() -> None:
    assert jibun_from_pnu("1150010200106660040", LD) == "등촌동 666-40"
    assert jibun_from_pnu("1150010200105070000", LD) == "등촌동 507"
    assert jibun_from_pnu("1150010200200120003", LD) == "등촌동 산 12-3"
    assert jibun_from_pnu("1150010999106660040", LD) is None  # 모르는 법정동
    assert jibun_from_pnu("115001020010666004", LD) is None and jibun_from_pnu(None, LD) is None


def check_unit_rules() -> None:
    for x in ("가동", "1동", "A", "제1동", "101동", ".", "--", "나", "B동", "2-1"):
        assert UNIT.match(x), x
    for x in ("매장동", "교사동", "강서아이티밸리", "가양9단지아파트 901동"):
        assert not UNIT.match(x), x
    assert UNIT_TAIL.sub("", "가양9단지아파트 901동") == "가양9단지아파트"
    assert UNIT_TAIL.sub("", "등촌중학교 교사동") == "등촌중학교 교사동"  # '교사동'이 '교'로 잘리지 않는다
    assert UNIT_TAIL.sub("", "백석초등학교 가동") == "백석초등학교"


def small_view():
    b = lambda n: f"{BASE}/building/11500102001066600400{n:05d}"
    g = lambda n: f"{BASE}/building-gis/{n:028d}"
    lot = f"{BASE}/parcel/1150010200106660040"
    rows = [(f"{BASE}/legal-dong/1150010200", RDFS_LABEL, "등촌동"), (f"{BASE}/legal-dong/1150010200", RDF_TYPE, JGK + "LegalDong")]
    rows += [(b(1), RDFS_LABEL, "가양9단지아파트 901동"), (b(1), JGK + "locatedOn", lot), (b(1), JGK + "roadAddress", "서울특별시 강서구 양천로 1")]
    rows += [(b(2), RDFS_LABEL, "가양9단지아파트 902동"), (b(2), JGK + "locatedOn", lot)]
    rows += [(b(3), RDFS_LABEL, "가동"), (b(3), JGK + "locatedOn", lot), (b(3), JGK + "roadAddress", "서울특별시 강서구 양천로 3")]
    b4 = f"{BASE}/building/1150010200199990000000004"  # 필지 연결이 없고 같은 PNU에 이름 있는 건물도 없다
    rows += [(b4, RDFS_LABEL, "나동"), (b4, JGK + "roadAddress", "서울특별시 강서구 양천로 4")]
    rows += [(b(5), JGK + "roadAddress", "서울특별시 강서구 양천로 5"), (b(5), RDF_TYPE, JGK + "Building")]
    rows += [(g(7), JGK + "locatedOn", f"{BASE}/parcel/1150010200105070024"), (g(7), RDF_TYPE, JGK + "Building")]
    rows += [(g(8), RDFS_LABEL, "1"), (g(8), JGK + "locatedOn", f"{BASE}/parcel/1150010200105070024")]
    rows += [(g(9), RDF_TYPE, JGK + "Building")]  # 이름, 주소, 필지 모두 없음
    rows += [(f"{BASE}/entrance/{b(1)[-25:]}-main", RDF_TYPE, JGK + "BuildingEntrance"), (b(1), JGK + "hasEntrance", f"{BASE}/entrance/{b(1)[-25:]}-main")]
    rows += [(f"{BASE}/parcel/1150010200105070024", RDF_TYPE, JGK + "Parcel"), (f"{BASE}/basic-zone/07601", RDFS_LABEL, "기초구역 07601")]
    return local_view(store_of(rows)), b, g


def check_display_name() -> None:
    v, b, g = small_view()
    assert display_name(v, b(1)) == "가양9단지아파트 901동"  # 1) 원천 이름
    assert display_name(v, b(3)) == "가양9단지아파트 가동"  # 2) 동 표기만: 같은 PNU에서 가장 많이 쓴 이름(끝의 동 표기를 뺀 것)
    assert display_name(v, f"{BASE}/building/1150010200199990000000004") == "강서구 양천로 4 나동"  # 2) 같은 PNU 이름이 없으면 주소 ('서울특별시 '는 뺀다)
    assert display_name(v, b(5)) == "강서구 양천로 5"  # 3) 이름 없고 주소 있음
    assert display_name(v, g(7)) == "이름 없는 건물 (등촌동 507-24)"  # 4) 주소도 없음
    assert display_name(v, g(8)) == "등촌동 507-24 1"  # 2) 동 표기만이고 이름, 주소가 없으면 지번
    assert display_name(v, g(9)) == "이름 없는 건물"
    for u in (g(7), g(8), g(9)):
        assert not re.search(r"[0-9]{20,}", display_name(v, u)), display_name(v, u)  # 5) 28자리 번호는 쓰지 않는다
    assert display_name(v, f"{BASE}/entrance/{b(1)[-25:]}-main") == "가양9단지아파트 901동 출입구"
    assert display_name(v, f"{BASE}/parcel/1150010200105070024") == "등촌동 507-24"
    assert display_name(v, f"{BASE}/basic-zone/07601") == "우편구역 07601"
    assert display_name(v, b(1) + "/geometry") == "가양9단지아파트 901동의 모양"
    assert v.jibun(f"{BASE}/building/1150010200199990000000004") == "등촌동 9999"  # 필지 연결이 없으면 건물관리번호 앞 19자리


def check_query_store() -> None:
    """보류 주어, 보류 값을 가리키는 트리플, 변경 기록을 뺀다."""
    open_b, held_b, prop = f"{BASE}/building/1", f"{BASE}/basemap/BLD1", f"{BASE}/proposal/p1"
    rows = [(open_b, RDFS_LABEL, "공개 건물"), (open_b, JGK + "basemapFeature", held_b), (held_b, RDFS_LABEL, "보류"),
            (open_b, "http://www.w3.org/ns/prov#wasInfluencedBy", prop), (prop, RDF_TYPE, JGK + "ChangeProposal"),
            (prop + "/change/1", RDF_TYPE, "http://www.w3.org/1999/02/22-rdf-syntax-ns#Statement")]
    v = local_view(store_of(rows))
    v.held = {held_b}
    q = public_query_store(v)
    subjects = {t.subject.value for t in q}
    objects = {t.object.value for t in q}
    assert subjects == {open_b}, subjects
    assert held_b not in objects and prop not in objects
    assert len(q) == 1


def check_app_shell() -> None:
    pub = render_app_html("public", build="20261103000000")
    assert not re.search(r"__[A-Z]+__", pub), "채우지 않은 자리표시가 있다"
    cfg = json.loads(re.search(r"window\.APP_CONFIG = (\{.*?\});", pub).group(1))
    assert cfg == {"mode": "public", "root": "/jeungmi-geokg", "app": "/jeungmi-geokg/assets/app", "build": "20261103000000",
                   "source": "/jeungmi-geokg/assets/app/source-static.js", "extra": []}, cfg
    assert 'href="/jeungmi-geokg/assets/vendor/maplibre-gl-4.7.1/maplibre-gl.css"' in pub and "app.js?v=20261103000000" in pub
    assert "/api/" not in pub and "localhost" not in pub and "web/local" not in pub
    local = render_app_html("local", extra=["/static/local/layers-local.js"], build="1")
    lcfg = json.loads(re.search(r"window\.APP_CONFIG = (\{.*?\});", local).group(1))
    assert lcfg["source"] == "/static/local/source-api.js" and lcfg["extra"] == ["/static/local/layers-local.js"] and lcfg["root"] == ""
    assert 'src="/static/common.js?v=1"' in local
    try:
        render_app_html("public", extra=["/static/local/x.js"])
    except ValueError:
        pass
    else:
        raise AssertionError("공개판이 로컬 모듈을 받았다")


def check_vendor() -> None:
    """구운 타일 18장에 poi, housenumber, building 층이 없고 합계 1MB 이하, 글꼴 1MB 이하, 라이브러리는 전에 쓰던 해시와 같다."""
    vendor = ROOT / "web" / "vendor"
    tiles = sorted((vendor / "base").rglob("*.pbf"))
    want = {f"{z}/{x}/{y}" for zoom in basetiles.ZOOMS for z, x, y in basetiles.tile_range(zoom, basetiles.bounds())}
    got = {t.relative_to(vendor / "base").with_suffix("").as_posix() for t in tiles}
    assert got == want and len(got) == 18, (len(got), sorted(got ^ want)[:5])
    total = 0
    for t in tiles:
        data = t.read_bytes()
        total += len(data)
        names = set(basetiles.layer_names(data))
        assert not names & {"poi", "housenumber", "building", "aerodrome_label", "mountain_peak"}, (t, names)
        assert names <= set(basetiles.KEEP), (t, names - set(basetiles.KEEP))
    assert total <= 1_000_000, total
    glyphs = vendor / "glyphs" / basetiles.FONT_DIR
    have = {f.stem for f in glyphs.glob("*.pbf")}
    spec8 = {"0-255", "256-511", "8192-8447", "8448-8703", "12288-12543", "12544-12799", "12800-13055", "65280-65535"}
    assert spec8 <= have and have == set(basetiles.RANGES), have
    gtotal = sum(f.stat().st_size for f in glyphs.glob("*.pbf"))
    assert gtotal <= 1_000_000, gtotal
    assert (vendor / "glyphs" / "OFL.txt").read_text(encoding="utf-8").count("SIL Open Font License") >= 1
    attribution = (vendor / "base" / "ATTRIBUTION.txt").read_text(encoding="utf-8")
    assert basetiles.VERSION in attribution and "ODbL" in attribution and "OpenStreetMap" in attribution
    lib = vendor / f"maplibre-gl-{basetiles.MAPLIBRE}"
    for name, sri in basetiles.MAPLIBRE_FILES.items():
        f = lib / Path(name).name
        assert f.exists(), f
        if sri:
            assert base64.b64encode(hashlib.sha384(f.read_bytes()).digest()).decode() == sri, f
    # 배경 범위는 common.js와 basetiles가 같다
    js = (ROOT / "web" / "common.js").read_text(encoding="utf-8")
    m = re.search(r"const MAP_BOUNDS = \[([^\]]+)\];", js)
    assert [float(x) for x in m.group(1).split(",")] == list(basetiles.bounds()), m.group(1)
    assert "tile.openstreetmap.org" not in js and "tiles.openfreemap.org" not in js  # 배경은 저장소에 구운 것만 쓴다
    assert re.search(r'"Map\.Title": "지도"', js) and re.search(r'"CooperativeGesturesHandler\.MobileHelpText": "[^"]*손가락', js)


def check_real_view() -> None:
    """실제 공개 뷰: 공개 근거 제안이 없는 지금, 공개 건물 모양은 jeungmi_geokg.ttl 원본과 같고, 쿼리 사본에 보류 값이 없다."""
    ttl = ROOT / "data" / "processed" / "jeungmi_geokg.ttl"
    if not ttl.exists():
        print("  그래프가 없어 실제 뷰 확인은 건너뜀")
        return
    from src.review.proposals import ProposalStore
    hold = {JGK + d for d in ["dataset-public-office", "dataset-juso-emap", "dataset-ngii-digital-map", "dataset-ngii-basemap"]}
    public_basis = [p for p in ProposalStore().load() if p.based_on and not {JGK + d for d in p.based_on} & hold]
    view = public_view()
    pub = {s for s in view.triples if s.startswith(BASE + "/")} - view.held
    kinds = {s[len(BASE) + 1:].split("/")[0] for s in pub}
    assert not kinds & {"basemap", "digital-map", "road-segment", "public-facility"}, kinds
    raw = ox.Store()
    raw.load(path=str(ttl), format=ox.RdfFormat.TURTLE)
    wkt = ox.NamedNode(GEO + "asWKT")
    original = {q.subject.value: q.object.value for q in raw.quads_for_pattern(None, wkt, None, None)}
    ours = {s: o.value for s in pub if s.endswith("/geometry") and "/building" in s for p, o in view.triples[s] if p.value == GEO + "asWKT"}
    if not public_basis:
        diff = [s for s, w in ours.items() if original.get(s) != w]
        assert not diff, diff[:3]
    points = sum(1 for s, w in ours.items() if w.startswith("POINT") and s.startswith(BASE + "/building"))
    q = public_query_store(view)
    held_subjects = sum(1 for t in q if t.subject.value in view.held)
    held_objects = sum(1 for t in q if isinstance(t.object, ox.NamedNode) and t.object.value in view.held)
    assert held_subjects == 0 and held_objects == 0, (held_subjects, held_objects)
    names = [display_name(view, s) for s in pub if s.split("/id/")[1].split("/")[0] in ("building", "building-gis") and not s.endswith("/geometry")]
    assert len(names) == 2790 and not [n for n in names if re.search(r"[0-9]{20,}", n)], len(names)
    units = [s for s in pub if "/building" in s and not s.endswith("/geometry") and UNIT.match((view.labels.get(s) or "").strip() or "x x x")]
    print(f"  공개 뷰: 공개 노드 {len(pub):,}, 보류 {len(view.held):,}, 건물 모양 {len(ours):,} (점 {points}), "
          f"쿼리 사본 {len(q):,}개 사실, 동 표기만인 이름 {len(units)}, 공개 근거 제안 {len(public_basis)}건")


def check_real_site() -> None:
    out = ROOT / "data" / "processed" / "site"
    if not (out / "about").exists():
        print("  새 공개 사이트가 없어 건너뜀 (python -m src.graph.publish)")
        return
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "window.APP_CONFIG" in index and not re.search(r"__[A-Z]+__", index) and (out / "map" / "index.html").read_text(encoding="utf-8") == index
    assert not (out / "assets" / "search.json").exists() and not (out / "assets" / "map" / "building.geojson").exists()
    assert (out / "assets" / "data" / "area.geojson").exists() and (out / "404.html").exists()
    assert not (out / "assets" / "app" / "app.html").exists() and not list(out.rglob("source-api.js"))
    assert (out / "assets" / "vendor" / "base" / "14").is_dir()
    about = (out / "about" / "index.html").read_text(encoding="utf-8")
    assert "확인하는 중" not in about and "기본측량성과" in about and "제16조" in about
    station = (out / "id" / "station" / "1150010200106660040000001" / "index.html").read_text(encoding="utf-8")
    assert 'href="/jeungmi-geokg/?focus=station/1150010200106660040000001"' in station and "<details class=\"raw\">" in station
    assert "tile.openstreetmap.org" not in station and "unpkg.com" not in station
    obs = next(d for d in (out / "id" / "observation").iterdir() if d.is_dir())
    assert "지도에서 열기" not in (obs / "index.html").read_text(encoding="utf-8")
    geom = (out / "id" / "station" / "1150010200106660040000001" / "geometry" / "index.html").read_text(encoding="utf-8")
    assert "지도에서 열기" not in geom
    vocab = (out / "def" / "index.html").read_text(encoding="utf-8") + (out / "def" / "ontology.ttl").read_text(encoding="utf-8")
    assert "도엽 37608075" not in vocab
    gis = [d for d in (out / "id" / "building-gis").iterdir() if d.is_dir()]
    titles = [re.search(r"<h1>(.*?)</h1>", (d / "index.html").read_text(encoding="utf-8")).group(1) for d in gis]
    assert not [t for t in titles if re.search(r"[0-9]{20,}", t)]
    print(f"  공개 사이트: 지도 앱 틀, 소개, 404, 장소 페이지 확인. 주소 없는 건물 {len(gis)}동 중 '이름 없는 건물 (…)' {sum(t.startswith('이름 없는 건물 (') for t in titles)}동")


def run() -> None:
    check_jibun()
    check_unit_rules()
    check_display_name()
    check_query_store()
    check_app_shell()
    check_vendor()
    check_real_view()
    check_real_site()
    print("공개 뷰 확인 통과")


def test_public_view():
    run()


if __name__ == "__main__":
    run()
