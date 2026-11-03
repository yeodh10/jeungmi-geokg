"""SHACL 확인: 바른 제안은 통과하고, 규칙을 어기는 제안은 승인이 막히는지 본다.

실행: python -m tests.test_shacl
실제 제안 파일은 임시 복사본으로만 쓴다.
"""
from __future__ import annotations

import random
import shutil
import tempfile
import time
from pathlib import Path

import pyoxigraph as ox

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "data" / "processed" / "review" / "proposals.json"
JGK = "https://yeodh10.github.io/jeungmi-geokg/def#"
ID = "https://yeodh10.github.io/jeungmi-geokg/id/"
GEO = "http://www.opengis.net/ont/geosparql#"
OWL = "http://www.w3.org/2002/07/owl#"
DCT = "http://purl.org/dc/terms/"
XSD = "http://www.w3.org/2001/XMLSchema#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
GOOD_POINT = "POINT (126.86 37.55)"


def make(target, *changes, action="update"):
    from src.review.proposals import Change, Proposal
    return Proposal(id="test-rule", created_at="2026-10-30T00:00:00+09:00", source_kind="rule-candidate", source_detail="시험",
                    action=action, target=target, target_type="Building", summary="시험",
                    changes=[Change(op=o, s=s, p=p, o=v) for o, s, p, v in changes])


def check_rules(g, props) -> None:
    """감사에서 그대로 통과했던 제안 25종과, 바른 제안이 계속 통과하는지."""
    from src.graph.validate import check_proposal
    from src.review.proposals import Term

    uri = lambda v: Term(kind="uri", value=v)
    lit = lambda v, **k: Term(kind="literal", value=v, **k)
    wkt = lambda v: lit(v, datatype=GEO + "wktLiteral")
    status, as_wkt = JGK + "status", GEO + "asWKT"
    links = [p for p in props if p.id.startswith("rule-basemap-link") and p.status == "approved"]
    shapes = [p for p in props if p.id.startswith("rule-basemap-shape") and p.status == "approved"]
    gis_x, gis_y = links[0].target, links[1].target
    mgt_x = next(c.o.value for c in links[0].changes if c.p.endswith("sameAs"))
    mgt_y = next(c.o.value for c in links[1].changes if c.p.endswith("sameAs"))
    a, b = shapes[0].target, shapes[1].target
    a_geom, b_geom = a + "/geometry", b + "/geometry"
    first = lambda s, p: next(q.object for q in g.quads_for_pattern(ox.NamedNode(s), ox.NamedNode(p), None, None))
    a_status, b_status, a_wkt = first(a, status).value, first(b, status).value, first(a_geom, as_wkt).value
    unlinked = [r["b"].value for r in g.query(f"PREFIX jgk: <{JGK}> SELECT ?b WHERE {{ ?b jgk:status jgk:AddressUnlinked ; a jgk:Building }} LIMIT 1")][0]
    row = next(iter(g.query(f"PREFIX jgk: <{JGK}> SELECT ?b ?e WHERE {{ ?b jgk:hasEntrance ?e ; jgk:buildingMgtNo ?n }} LIMIT 1")))
    with_door, door = row["b"].value, row["e"].value
    parcel = next(iter(g.query(f"PREFIX jgk: <{JGK}> SELECT ?p WHERE {{ ?p a jgk:Parcel }} LIMIT 1")))["p"].value
    swap = lambda v: make(a, ("remove", a_geom, as_wkt, wkt(a_wkt)), ("add", a_geom, as_wkt, wkt(v)))
    to_linked = (("remove", unlinked, status, uri(JGK + "AddressUnlinked")), ("add", unlinked, status, uri(JGK + "AddressLinked")))

    must_block = {
        "대상 밖 모양에 WKT 추가": make(a, ("add", b_geom, as_wkt, wkt(GOOD_POINT))),
        "대상 밖 건물 상태 제거": make(a, ("remove", b, status, uri(b_status))),
        "대상 밖 건물에 상태 하나 더": make(a, ("add", b, status, uri(JGK + "Retired"))),
        "모양 연결을 끊고 WKT 둘": make(a, ("remove", a, GEO + "hasGeometry", uri(a_geom)), ("add", a_geom, as_wkt, wkt(GOOD_POINT))),
        "없는 객체로 잇기": make(unlinked, *to_linked, ("add", unlinked, OWL + "sameAs", uri(ID + "building/does-not-exist"))),
        "자기 자신과 잇기": make(unlinked, *to_linked, ("add", unlinked, OWL + "sameAs", uri(unlinked))),
        "사슬로 잇기": make(unlinked, *to_linked, ("add", unlinked, OWL + "sameAs", uri(gis_x))),
        "고리로 잇기": make(mgt_x, ("add", mgt_x, OWL + "sameAs", uri(gis_x))),
        "이미 이어진 대상에 둘째로 잇기": make(unlinked, *to_linked, ("add", unlinked, OWL + "sameAs", uri(mgt_x))),
        "이어진 건물을 둘째 대상에도 잇기": make(gis_x, ("add", gis_x, OWL + "sameAs", uri(mgt_y))),
        "건물을 필지와 잇기": make(unlinked, *to_linked, ("add", unlinked, OWL + "sameAs", uri(parcel))),
        "새 관계로 없는 객체와 잇기": make(unlinked, *to_linked, ("add", unlinked, JGK + "sameBuildingAs", uri(ID + "building/does-not-exist"))),
        "상태만 주소 연결됨": make(unlinked, *to_linked),
        "없는 객체로 국가기본도 잇기": make(a, ("add", a, JGK + "basemapFeature", uri(ID + "basemap/nope"))),
        "국가기본도에 글자 값": make(a, ("add", a, JGK + "basemapFeature", lit("글자"))),
        "폐지 확인일이 날짜가 아님": make(a, ("add", a, JGK + "retiredOn", lit("2026-10-30"))),
        "모양 출처를 없는 자료로": make(a, ("add", a_geom, DCT + "source", uri(JGK + "dataset-nope"))),
        "모양 출처 지우기": make(a, ("remove", a_geom, DCT + "source", uri(JGK + "dataset-ngii-basemap"))),
        "출입구 떼어 내기": make(with_door, ("remove", with_door, JGK + "hasEntrance", uri(door))),
        "종류를 지워 규칙 피하기": make(a, ("remove", a, RDF_TYPE, uri(GEO + "Feature")), ("remove", a, RDF_TYPE, uri(JGK + "Building")),
                               ("remove", a, status, uri(a_status))),
        "없는 값 빼기": make(a, ("remove", a, status, uri(JGK + "Retired"))),
        "변경이 없는 제안": make(ID + "building/ghost"),
        "없는 객체를 대상으로": make(ID + "building/ghost", ("add", ID + "building/ghost", JGK + "testNote", lit("x"))),
        "층수가 정수가 아님": make(a, ("add", a, JGK + "floorsAbove", lit("abc", datatype=XSD + "integer"))),
        "언어 태그가 붙은 용도": make(a, ("add", a, JGK + "useName", lit("주택", lang="ko"))),
    }
    notice = ID + "notice/11500-2026-1"
    notice_base = (("add", notice, RDF_TYPE, uri(JGK + "Notice")), ("add", notice, JGK + "noticeNumber", lit("제2026-1호")))
    for name, p, v in ((("표제 둘"), RDFS_LABEL, lit("다른 표제", lang="ko")), ("고시일 둘", DCT + "issued", lit("2026-01-02", datatype=XSD + "date")),
                       ("출처 둘", DCT + "source", uri("https://www.gangseo.seoul.kr/b")), ("구역 종류 둘", JGK + "zoneKind", lit("다른 구역"))):
        one = {RDFS_LABEL: lit("표제", lang="ko"), DCT + "issued": lit("2026-01-01", datatype=XSD + "date"),
               DCT + "source": uri("https://www.gangseo.seoul.kr/a"), JGK + "zoneKind": lit("구역")}[p]
        must_block[f"고시에 {name}"] = make(a, *notice_base, ("add", notice, p, one), ("add", notice, p, v))

    passed = []
    for name, p in must_block.items():
        ok, res = check_proposal(g, p)
        if ok:
            passed.append(name)
        elif name == "상태만 주소 연결됨":
            assert any("주소 연결됨" in r["message"] for r in res), res
    assert not passed, passed

    # 잘 만든 제안은 통과한다: 모양 바꾸기, 건물 주소 연결, 고시 잇기, 값 넣기
    good = {
        "모양 교체": swap(GOOD_POINT),
        "모양 출처 교체": make(a, ("remove", a_geom, DCT + "source", uri(JGK + "dataset-ngii-basemap")),
                         ("add", a_geom, DCT + "source", uri(JGK + "dataset-gis-building"))),
        "고시 잇기": make(parcel, *notice_base, ("add", parcel, JGK + "zoneDesignatedBy", uri(notice))),
    }
    for name, p in good.items():
        ok, res = check_proposal(g, p)
        assert ok, (name, res)
    assert not list(g.quads_for_pattern(ox.NamedNode(notice), None, None, None)), "검사가 실제 그래프를 바꿨다"


def check_wkt_values(g, props) -> None:
    """WKT 값까지 본다. 재현: 깨진 WKT 하나로 건물 레이어 전체가 내려갔다."""
    from src.graph.validate import check_proposal
    from src.review.proposals import Term

    a = next(p.target for p in props if p.id.startswith("rule-basemap-shape") and p.status == "approved")
    geom = a + "/geometry"
    now = next(q.object.value for q in g.quads_for_pattern(ox.NamedNode(geom), ox.NamedNode(GEO + "asWKT"), None, None))
    wkt = lambda v: Term(kind="literal", value=v, datatype=GEO + "wktLiteral")
    bad = {"쓰레기": "아무 글자", "깨진 괄호": "POLYGON ((((", "빈 값": "", "빈 모양": "POINT EMPTY", "숫자 아님": "POINT(NaN NaN)", "무한대": "POINT (1e999 0)",
           "CRS 접두어": "<http://www.opengis.net/def/crs/EPSG/0/5186> POINT (190000 450000)", "범위 밖": "POINT (999 999)", "0, 0": "POINT (0 0)",
           "위경도 뒤바뀜": "POINT (37.55 126.86)", "EPSG:5186 좌표": "POINT (190000 450000)",
           "자기교차": "POLYGON ((126.86 37.55, 126.87 37.56, 126.87 37.55, 126.86 37.56, 126.86 37.55))",
           # 재현: Z, M 값의 NaN은 x, y 범위만 보는 검사를 지나 승인됐고, 그 뒤 건물 레이어 전체가 500이었다
           "Z NaN": "POINT Z (126.86 37.55 nan)", "ZM NaN": "POINT ZM (126.86 37.55 0 nan)", "M 무한대": "POINT M (126.86 37.55 1e999)",
           "Z 높이": "POINT Z (126.86 37.55 10)", "LinearRing": "LINEARRING (126.86 37.55, 126.87 37.55, 126.87 37.56, 126.86 37.55)",
           "곡선": "CIRCULARSTRING (126.86 37.55, 126.87 37.56, 126.88 37.55)", "모음": "GEOMETRYCOLLECTION (POINT (126.86 37.55))",
           "부분 NaN 선": "LINESTRING (126.86 37.55, NaN NaN, 126.87 37.56)", "빈 부분": "MULTIPOINT ((126.86 37.55), EMPTY)"}
    passed, said = [], {}
    for name, value in bad.items():
        ok, res = check_proposal(g, make(a, ("remove", geom, GEO + "asWKT", wkt(now)), ("add", geom, GEO + "asWKT", wkt(value))))
        said[name] = " ".join(r["message"] for r in res)
        if ok or not any("WKT" in r["message"] for r in res):
            passed.append((name, res))
    assert not passed, passed
    # NaN이 든 선에 '스스로 교차'처럼 틀린 사유를 대지 않는다
    assert "숫자가 아니다" in said["부분 NaN 선"] and "교차" not in said["부분 NaN 선"], said["부분 NaN 선"]
    assert "숫자가 아니다" in said["Z NaN"] and "숫자가 아니다" in said["M 무한대"] and "2차원" in said["Z 높이"], said
    assert all("모양 종류" in said[n] for n in ("LinearRing", "곡선", "모음")), said
    # 속성이 달라도 값의 종류가 WKT면 같이 본다
    ok, res = check_proposal(g, make(a, ("add", geom, JGK + "testShape", wkt("POINT (999 999)"))))
    assert not ok and any("WKT" in r["message"] for r in res), res


def check_any_wkt() -> None:
    """어떤 글을 WKT로 줘도 검사가 오류를 내지 않고, 검사를 지난 값은 지도에 그릴 수 있다(2차원, 유한한 좌표).

    재현: CIRCULARSTRING은 검사에서 NotImplementedError로 500이었고, LINEARRING은 검사를 지나 GeoJSON 밖의 종류로 나갔다.
    """
    import numpy as np
    import shapely

    from src.graph.validate import flat_shape, wkt_problem
    kinds = ["POINT", "MULTIPOINT", "LINESTRING", "MULTILINESTRING", "POLYGON", "MULTIPOLYGON", "LINEARRING", "GEOMETRYCOLLECTION",
             "CIRCULARSTRING", "COMPOUNDCURVE", "CURVEPOLYGON", "MULTICURVE", "TRIANGLE", "TIN", "POLYHEDRALSURFACE", "point", " POINT"]
    dims = ["", " Z", " M", " ZM"]
    nums = ["126.86", "37.55", "nan", "NaN", "inf", "-inf", "1e999", "0", "-1e308", "1e-320", "EMPTY", "", "x"]
    rng = random.Random(20261103)
    values = ["", " ", "(", ")", "POINT", "POINT()", "POINT(())", "POINT (126.86 37.55", "POINT (126.86 37.55))", "\x00POINT (1 2)",
              "SRID=4326;POINT (126.86 37.55)", "<http://www.opengis.net/def/crs/OGC/1.3/CRS84> POINT (126.86 37.55)",
              "GEOMETRYCOLLECTION (" * 50 + "POINT (126.86 37.55)" + ")" * 50, "POINT (" + "1" * 400 + " 37.55)"]
    for _ in range(600):
        coords = lambda n: ", ".join(" ".join(rng.choice(nums) for _ in range(rng.choice([1, 2, 3, 4]))) for _ in range(n))
        body = rng.choice([f"({coords(rng.randint(1, 5))})", f"(({coords(rng.randint(1, 5))}))", f"((({coords(4)})))", "EMPTY",
                           f"(({coords(4)}), EMPTY)"])
        values.append(f"{rng.choice(kinds)}{rng.choice(dims)} {body}")
    good = ["POINT (126.86 37.55)", " point( 126.86 37.55 ) ", "MULTIPOINT ((126.86 37.55), (126.87 37.56))",
            "LINESTRING (126.86 37.55, 126.87 37.56)", "POLYGON ((126.86 37.55, 126.87 37.55, 126.87 37.56, 126.86 37.55))"]
    for v in values + good:
        problem = wkt_problem(v)
        assert problem is None or isinstance(problem, str), (v, problem)
        shape = flat_shape(v)
        if problem is None:  # 검사를 지난 값은 지도에 그릴 수 있어야 한다
            xy = shapely.get_coordinates(shape)
            assert shape is not None and not shape.has_z and np.isfinite(xy).all(), v
    assert not [v for v in good if wkt_problem(v)], [wkt_problem(v) for v in good]


def check_vocabulary(g, props) -> None:
    """어휘 노드와 제안 기록은 제안으로 바꿀 수 없다. 목적어로 어휘를 가리키는 것은 된다. 기존 승인은 이 규칙에 걸리지 않는다.

    재현: jgk:Active의 이름표를 빼는 제안이 검사를 지나 승인됐고, 건물 레이어가 2,790개에서 628개로 줄었다.
    """
    from src.graph.validate import check_proposal, vocabulary_problem
    from src.model.uri import BASE
    from src.review.proposals import Term

    lit = lambda v, **k: Term(kind="literal", value=v, **k)
    a = next(p.target for p in props if p.id.startswith("rule-basemap-shape") and p.status == "approved")
    record = next(p for p in props if p.status == "approved").uri
    blocked = {
        "상태 이름표 빼기": make(JGK + "Active", ("remove", JGK + "Active", RDFS_LABEL, lit("사용", lang="ko"))),
        "대상은 건물, 바꾸는 것은 어휘": make(a, ("remove", JGK + "Active", RDFS_LABEL, lit("사용", lang="ko"))),
        "자료 이름표 바꾸기": make(a, ("add", JGK + "dataset-gis-building", RDFS_LABEL, lit("다른 이름", lang="ko"))),
        "바깥 어휘 바꾸기": make(a, ("add", GEO + "Feature", RDFS_LABEL, lit("x"))),
        "제안 기록 바꾸기": make(a, ("add", record, DCT + "description", lit("고친 요약"))),
    }
    for name, p in blocked.items():
        ok, res = check_proposal(g, p)
        assert not ok and any("어휘나 제안 기록" in r["message"] for r in res), (name, res)
    assert record.startswith(BASE + "/proposal/")
    approved = [p for p in props if p.status == "approved"]
    assert approved and not [p.id for p in approved if vocabulary_problem(p)]


def check_speed(g, props) -> None:
    """검사 때마다 그래프를 복사하지 않는다. 재현: 한 건에 1.2~3.7초, 묶음 208건에 약 6.6분."""
    from src.graph.validate import check_proposal

    sample = [p for p in props if p.status == "pending"][:6]
    t0 = time.perf_counter()
    for p in sample:
        check_proposal(g, p)
    took = (time.perf_counter() - t0) / len(sample)
    assert took < 1.0, took
    print(f"제안 검사 한 건 평균 {took:.2f}초")


def run() -> None:
    from fastapi.testclient import TestClient

    from src.graph.store import load_store
    from src.graph.validate import check_proposal
    from src.review import app as review_app
    from src.review.proposals import Change, ProposalStore, Term

    tmp = Path(tempfile.mkdtemp()) / "proposals.json"
    shutil.copy(REAL, tmp)
    store = ProposalStore(tmp)
    good = store.load()[0]
    g = load_store(store)
    ok, res = check_proposal(g, good)
    assert ok, res

    # 폐지로 바꾸면서 이전 상태를 빼지 않고 폐지 확인일도 없는 제안 -> 상태 2개, 폐지 확인일 없음
    station = "https://yeodh10.github.io/jeungmi-geokg/id/station/1150010200106660040000001"
    bad = good.model_copy(update={"id": "test-bad", "target": station, "action": "retire", "changes": [
        Change(op="add", s=station, p=JGK + "status", o=Term(kind="uri", value=JGK + "Retired"))]})
    ok, res = check_proposal(g, bad)
    msgs = {r["message"] for r in res}
    assert not ok and any("상태는 정확히 하나" in m for m in msgs) and any("폐지 확인일" in m for m in msgs), msgs

    # 건물 식별 번호가 둘 다 있는 건물 -> xone 위반
    b = "https://yeodh10.github.io/jeungmi-geokg/id/building/1150010200106660040000001"
    bad2 = good.model_copy(update={"id": "test-bad2", "target": b, "action": "update", "changes": [
        Change(op="add", s=b, p=JGK + "gisBuildingId", o=Term(kind="literal", value="1" * 28))]})
    ok, res = check_proposal(g, bad2)
    assert not ok and any("정확히 하나로 식별" in r["message"] for r in res), res

    props = store.load()
    check_speed(g, props)
    check_rules(g, props)
    check_wkt_values(g, props)
    check_any_wkt()
    check_vocabulary(g, props)

    # 화면 API: 규칙을 어기는 제안은 승인 409, 반려는 된다
    store.add_new([bad])
    review_app.proposals = store
    review_app.state["store"] = None
    c = TestClient(review_app.app, base_url="http://localhost")
    v = c.get("/api/proposals/test-bad/validate").json()
    assert not v["conforms"]
    r = c.post("/api/proposals/test-bad/decision", json={"result": "approved", "reviewer": "테스트 검토자"})
    assert r.status_code == 409 and "규칙" in r.json()["detail"], r.text
    assert c.post("/api/proposals/test-bad/decision",
                  json={"result": "rejected", "reviewer": "테스트 검토자", "comment": "규칙 위반"}).status_code == 200
    print("SHACL 확인 통과:", sorted(msgs))


def test_shacl_flow():
    run()


if __name__ == "__main__":
    run()
