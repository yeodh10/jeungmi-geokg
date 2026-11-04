"""화면 API 확인: URI 주입 막기, 저장과 반영이 깨지지 않는지, 동시 결정, 지도용 API.

실행: python -m tests.test_api
실제 제안 파일은 임시 복사본으로만 쓴다. data/processed/에 그래프와 제안이 만들어져 있어야 한다.
"""
from __future__ import annotations

import base64
import hashlib
import itertools
import json
import logging
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "data" / "processed" / "review" / "proposals.json"
LOCAL = "http://localhost"  # 서버가 받는 이름이다. TestClient의 기본 이름 testserver는 Host 검사에 막힌다
BASE = "https://yeodh10.github.io/jeungmi-geokg/id"
JGK = "https://yeodh10.github.io/jeungmi-geokg/def#"
GEO = "http://www.opengis.net/ont/geosparql#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
STATION = f"{BASE}/station/1150010200106660040000001"
DONG = f"{BASE}/admin-dong/1147053000"
WITHIN = JGK + "within"
OK = {"result": "approved", "reviewer": "시험 검토자"}
spare = itertools.count(100)  # 시험마다 다른 건물을 쓰게 하는 번호


def strict(r):
    """응답을 브라우저처럼 표준 JSON으로 읽는다. NaN, Infinity가 있으면 실패한다."""
    def refuse(x):
        raise AssertionError(f"표준 JSON이 아니다: {x}")
    return json.loads(r.content, parse_constant=refuse)


def fresh(review_app, n: int) -> list[str]:
    """다른 시험이 쓰지 않은, 지금 사용 중인 건물 n개 (n은 10 이하). 승인으로 같은 건물 잇기에 든 건물은 고르지 않는다."""
    q = (f"SELECT ?b WHERE {{ ?b a <{JGK}Building> ; <{JGK}status> <{JGK}Active> "
         f"FILTER NOT EXISTS {{ ?x <{JGK}sameBuildingAs> ?b }} FILTER NOT EXISTS {{ ?b <{JGK}sameBuildingAs> ?y }} }} "
         f"ORDER BY ?b LIMIT {n} OFFSET {next(spare) * 10}")
    return [s["b"].value for s in review_app.store().query(q)]


def note(pid: str, target: str, changes=None, **fields):
    """건물에 설명 한 줄을 더하는 시험 제안. 규칙에 걸리지 않고 그래프를 바꾼다."""
    from src.review.proposals import Change, Proposal, Term
    return Proposal(id=pid, created_at="2026-11-02T09:00:00+09:00", source_kind="rule-candidate", source_detail="시험",
                    action="update", target=target, target_type="Building", summary=f"시험 {pid}", evidence=["시험 근거"],
                    changes=changes or [Change(op="add", s=target, p=RDFS + "comment", o=Term(kind="literal", value=pid))],
                    **fields)


def link(op: str, s: str, p: str, o: str):
    from src.review.proposals import Change, Term
    return Change(op=op, s=s, p=p, o=Term(kind="uri", value=o))


def check_uri_injection(c, review_app) -> None:
    """URI 자리에 쿼리 조각을 넣어도 400이고, SERVICE로 바깥(여기서는 로컬 소켓)에 요청이 나가지 않는다."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    srv.settimeout(0.5)
    port = srv.getsockname()[1]
    service = f"{BASE}/x> ?p ?o }} SERVICE <http://127.0.0.1:{port}/sparql> {{ ?a ?b ?c }} {{ <{BASE}/x"
    try:
        for bad in ["not a uri", BASE + "/x>", "http://a b", service, "ftp://example.com/a", f"{BASE}/x\nSERVICE"]:
            r = c.get("/api/feature", params={"uri": bad})
            assert r.status_code == 400 and r.json()["detail"] == "올바른 URI가 아닙니다", (bad, r.text)
        # 형식이 맞는 주소는 그래프에서 찾아볼 뿐 그 주소로 요청하지 않는다
        r = c.get("/api/feature", params={"uri": f"http://127.0.0.1:{port}/sparql"})
        assert r.status_code == 200 and not r.json()["in_graph"], r.text
        try:
            conn, _ = srv.accept()
            conn.close()
            raise AssertionError("로컬 소켓에 요청이 왔다")
        except (socket.timeout, TimeoutError):
            pass
    finally:
        srv.close()
    # 이름 찾기는 형식이 틀린 값을 조용히 건너뛴다
    assert review_app.names(["not a uri", BASE + "/x>", service, None]) == {}

    # 행정동: 들어오는 객체는 속성마다 이름순 앞 30개, 전체 개수는 따로
    d = c.get("/api/feature", params={"uri": DONG}).json()
    within = [x for x in d["inbound"] if x["p"] == WITHIN]
    assert d["inbound_counts"][WITHIN] > len(within) == 30, (d["inbound_counts"], len(within))
    keys = [x["s_label"] or x["s"] for x in within]
    assert keys == sorted(keys), keys[:5]
    assert d["in_graph"] and d["triples_truncated"] is False and d["public"] is True
    assert {"label", "type"} <= set(d["names"][DONG]), d["names"].get(DONG)


def check_proposal_safety(tmp: Path) -> None:
    """줄바꿈이 든 값, 반영할 수 없는 제안, 깨진 승인 기록을 다룬다."""
    import pyoxigraph as ox

    from src.graph.store import load_store
    from src.graph.validate import check_proposal
    from src.review.proposals import Decision, ProposalStore

    current = ProposalStore(tmp).load()  # 지금 제안 파일이 새 형식 검사로 그대로 읽힌다
    assert len(current) == len(json.loads(REAL.read_text(encoding="utf-8")))
    good = next(p for p in current if p.id.startswith("table-"))

    multi = good.model_copy(update={"summary": "첫 줄\n둘째 줄\r\n\t탭과 \"따옴표\" \\ 역슬래시"})
    g = ox.Store()
    g.update(multi.sparql_update())
    got = [q.object.value for q in g.quads_for_pattern(ox.NamedNode(multi.uri), ox.NamedNode("http://purl.org/dc/terms/description"), None, None)]
    assert got == [multi.summary], got

    graph = load_store(ProposalStore(tmp))
    broken = good.model_copy(update={"id": "test-broken", "target": BASE + "/x> . <http://evil/s"})  # model_copy는 검사를 거치지 않는다
    ok, res = check_proposal(graph, broken)
    assert not ok and len(res) == 1 and res[0]["message"].startswith("제안을 그래프에 반영하지 못했다"), res

    # 깨진 승인 기록이 섞여 있어도 나머지로 그래프를 만든다
    decision = Decision(result="approved", reviewer="테스트 검토자", at="2026-10-29T10:00:00+09:00")
    approved = good.model_copy(update={"status": "approved", "decision": decision})
    bad = broken.model_copy(update={"status": "approved", "decision": decision})

    class Fake:
        def load(self):
            return [bad, approved]

    seen = []
    handler = logging.Handler()
    handler.emit = seen.append
    logging.getLogger("src.graph.store").addHandler(handler)
    try:
        g = load_store(Fake())
    finally:
        logging.getLogger("src.graph.store").removeHandler(handler)
    status = [q.object.value for q in g.quads_for_pattern(ox.NamedNode(good.target), ox.NamedNode(JGK + "status"), None, None)]
    assert status == [JGK + "Retired"], status
    assert any("test-broken" in r.getMessage() for r in seen), [r.getMessage() for r in seen]


def check_concurrency(tmp: Path, review_app) -> str:
    """동시에 결정해도 기록이 빠지지 않고, 같은 제안은 한 번만 결정된다. 승인한 제안 id를 돌려준다."""
    from fastapi.testclient import TestClient

    from src.review.proposals import ProposalStore

    store = ProposalStore(tmp)
    base = store.get("rule-facility-1494880")
    clones = [base.model_copy(update={"id": f"test-many-{i}", "target": f"{base.target}-many-{i}"}) for i in range(6)]
    assert store.add_new(clones) == 6

    codes = []

    def reject(i):
        r = TestClient(review_app.app, base_url=LOCAL).post(f"/api/proposals/test-many-{i}/decision",
                                                            json={"result": "rejected", "reviewer": f"검토자{i}", "comment": "동시 반려"})
        codes.append(r.status_code)
    threads = [threading.Thread(target=reject, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert codes == [200] * 6, codes
    saved = {p.id: p for p in store.load()}
    assert all(saved[f"test-many-{i}"].status == "rejected" for i in range(6))
    assert {saved[f"test-many-{i}"].decision.reviewer for i in range(6)} == {f"검토자{i}" for i in range(6)}
    assert not list(tmp.parent.glob("*.tmp")), "임시 파일이 남았다"

    # 같은 제안을 승인과 반려로 동시에 보내면 하나만 된다
    pid = next(p.id for p in store.load() if p.id.startswith("table-") and p.status == "pending")
    results = {}

    def send(result):
        r = TestClient(review_app.app, base_url=LOCAL).post(f"/api/proposals/{pid}/decision",
                                                            json={"result": result, "reviewer": "테스트 검토자", "comment": "동시"})
        results[result] = (r.status_code, r.json().get("detail"))
    threads = [threading.Thread(target=send, args=(x,)) for x in ("approved", "rejected")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(code for code, _ in results.values()) == [200, 409], results
    assert any(d in ("이미 승인된 제안입니다", "이미 반려된 제안입니다") for _, d in results.values()), results
    return pid if results["approved"][0] == 200 else ""


def check_approval_and_layers(c, tmp: Path, review_app) -> None:
    """검토자 이름이 SPARQL로 실행되지 않고, 폐지 승인 뒤 레이어에 바로 폐지로 나온다."""
    from src.review.proposals import ProposalStore

    p = next(p for p in ProposalStore(tmp).load() if p.id.startswith("table-") and p.status == "pending")
    first = c.get("/api/layers/building")
    etag = first.headers["etag"]
    assert first.headers["cache-control"] == "no-cache" and etag.startswith('W/"building-'), first.headers
    assert c.get("/api/layers/building", headers={"If-None-Match": etag}).status_code == 304
    before = {f["properties"]["uri"]: f["properties"]["status"] for f in first.json()["features"]}
    assert before[p.target] != "폐지", before[p.target]

    size = len(review_app.store())
    sneaky = 'x" } ; DROP ALL ; INSERT DATA { <a:s> <a:p> "y'
    r = c.post(f"/api/proposals/{p.id}/decision", json={"result": "approved", "reviewer": sneaky})
    assert r.status_code == 200, r.text
    g = review_app.store()
    assert len(g) > size, (len(g), size)
    who = [s["w"].value for s in g.query(f"SELECT ?w WHERE {{ <{p.uri}> <{JGK}decidedBy> ?w }}")]
    assert who == [sneaky], who
    assert not list(g.query("SELECT ?o WHERE { <a:s> ?p ?o }"))

    after = c.get("/api/layers/building", headers={"If-None-Match": etag})
    assert after.status_code == 200 and after.headers["etag"] != etag, after.headers
    now = {f["properties"]["uri"]: f["properties"]["status"] for f in after.json()["features"]}
    assert now[p.target] == "폐지", now[p.target]

    # 서버를 다시 켜면 판 번호가 0부터 다시 세어져도, 다시 켜기 전 ETag로는 304가 나오지 않는다
    saved = dict(review_app.state)
    review_app.state.update(boot="restarted", version=int(etag.rstrip('"').rsplit("-", 1)[1]))
    review_app.layer_cache.clear()
    try:
        again = c.get("/api/layers/building", headers={"If-None-Match": etag})
        assert again.status_code == 200 and again.headers["etag"] != etag, again.headers
    finally:
        review_app.state.update(saved)
        review_app.layer_cache.clear()

    # 바뀐 객체에는 반영된 제안 기록이, 제안 노드는 공개 페이지가 없다
    f = c.get("/api/feature", params={"uri": p.target}).json()
    assert f["history"] and f["history"][0]["id"] == p.id and f["history"][0]["decided_by"] == sneaky, f["history"]
    assert f["history"][0]["action"] == "retire" and f["geometry"]["type"] in ("Polygon", "MultiPolygon")
    node = c.get("/api/feature", params={"uri": p.uri}).json()
    assert node["in_graph"] and node["public"] is False and node["names"][p.uri]["label"] == p.summary, node["names"]


def check_map_api(c, review_app) -> None:
    from shapely.geometry import Point, shape

    a = c.get("/api/area").json()
    lon, lat = a["center"]
    x0, y0, x1, y1 = a["bbox"]
    assert x0 < lon < x1 and y0 < lat < y1 and a["radius_m"] == 1000, a["bbox"]
    circle = shape(a["features"][0]["geometry"])
    assert circle.contains(Point(lon, lat)) and a["features"][0]["properties"]["label"] == "증미역 반경 1km"

    # 공개 여부: 공개 보류 출처(전자지도)의 공공시설은 공개 페이지가 없고, 역은 있다
    fac = c.get("/api/layers/facility").json()["features"]
    held = [f for f in fac if not c.get("/api/feature", params={"uri": f["properties"]["uri"]}).json()["public"]]
    assert held, "공개 보류 공공시설이 없다"
    assert c.get("/api/feature", params={"uri": held[0]["properties"]["uri"] + "/geometry"}).json()["public"] is False
    st = c.get("/api/feature", params={"uri": STATION}).json()
    assert st["public"] is True and st["geometry"]["type"] == "Point" and st["history"] == []

    # 레이어: 한 객체가 한 번만 나오고 수는 원래 쿼리와 같다. kind, dong, 출입구 주소가 들어 있다
    g = review_app.store()
    for kind, cls in review_app.LAYER_QUERY.items():
        want = next(iter(g.query(f"""PREFIX geo: <http://www.opengis.net/ont/geosparql#>
PREFIX jgk: <{JGK}> PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT (COUNT(DISTINCT ?f) AS ?n) WHERE {{ ?f a {cls} ; geo:hasDefaultGeometry/geo:asWKT ?w ; jgk:status/rdfs:label ?s }}""")))["n"].value
        feats = c.get(f"/api/layers/{kind}").json()["features"]
        uris = [f["properties"]["uri"] for f in feats]
        assert len(uris) == len(set(uris)) == int(want), (kind, len(uris), len(set(uris)), want)
        assert all(f["properties"]["kind"] == kind and "dong" in f["properties"] for f in feats)
        coords = json.dumps([f["geometry"]["coordinates"] for f in feats[:50]])
        assert max(len(t.split(".")[1]) for t in coords.replace("[", " ").replace("]", " ").replace(",", " ").split() if "." in t) <= 6
    ent = c.get("/api/layers/entrance").json()["features"]
    assert sum(1 for f in ent if f["properties"]["addr"]) > len(ent) * 0.9
    bld = c.get("/api/layers/building").json()["features"]
    in_dong = next(iter(g.query(f"""PREFIX jgk: <{JGK}>
SELECT (COUNT(DISTINCT ?f) AS ?n) WHERE {{ ?f a jgk:Building ; jgk:within ?d . ?d a jgk:AdminDong }}""")))["n"].value
    assert sum(1 for f in bld if f["properties"]["dong"]) == int(in_dong) > 0, in_dong

    # 도로명주소 전자지도: 공개 보류를 유지하므로 도로 구간은 통째로, 법정동은 모양만 공개하지 않는다
    seg = c.get("/api/layers/road").json()["features"]
    assert seg and all(f["geometry"]["type"] in ("LineString", "MultiLineString") for f in seg)
    one = c.get("/api/feature", params={"uri": seg[0]["properties"]["uri"]}).json()
    assert one["public"] is False, one["public"]
    dong = next(f for f in c.get("/api/layers/legaldong").json()["features"] if f["properties"]["label"] == "등촌동")
    d = c.get("/api/feature", params={"uri": dong["properties"]["uri"]}).json()
    assert d["public"] is True and c.get("/api/feature", params={"uri": dong["properties"]["uri"] + "/geometry"}).json()["public"] is False
    # 국가기본도(nf_id 17자리)와 수치지형도(UFID 34자리) 건물: 공개를 보류한다. 이어진 짝은 IoU 0.5 이상이다.
    # 건물 하나는 지도 건물 하나와 잇는다. 지도 건물 하나에는 건물이 여럿 이어질 수 있다 (폴리곤 단위로 맞춘 뒤 나눠 준다)
    for kind, prop, n in (("basemap", "basemapFeature", 17), ("digitalmap", "digitalMapFeature", 34)):
        bm = c.get(f"/api/layers/{kind}").json()["features"]
        assert bm and all(len(f["properties"]["uri"].rsplit("/", 1)[1]) == n for f in bm), kind
        q = f"SELECT ?b ?m ?i WHERE {{ ?b <{JGK}{prop}> ?m . ?m <{JGK}matchIoU> ?i }}"
        pairs = [(x["b"].value, x["m"].value, float(x["i"].value)) for x in g.query(q)]
        assert pairs and min(i for *_, i in pairs) >= 0.5 and len({b for b, _, _ in pairs}) == len(pairs), kind
        assert c.get("/api/feature", params={"uri": pairs[0][1]}).json()["public"] is False
    # 증미역 출입구는 같은 도로명(양천로)의 도로 구간에 면한다
    q = f"""SELECT ?n WHERE {{ <{STATION}> <{JGK}housedIn>/<{JGK}hasEntrance>/<{JGK}facesRoad>/<{JGK}segmentOf> ?r .
      ?r <http://www.w3.org/2000/01/rdf-schema#label> ?n }}"""
    assert [x["n"].value for x in g.query(q)] == ["양천로"]

    r = c.get("/api/layers/building", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip", r.headers
    assert c.get("/api/layers/nothing").status_code == 404


def check_group_decision(c, tmp: Path, review_app) -> None:
    """묶어 승인: 앞 제안을 반영한 그래프에서 다음 제안을 검사하고, 통과한 것만 승인한다."""
    from src.graph.store import load_store
    from src.review.proposals import ProposalStore, group_of

    ps = ProposalStore(tmp)
    first = next(p for p in ps.load() if p.id.startswith("table-") and p.status == "pending")
    group = group_of(first)[0]
    # 같은 건물을 다시 '사용'으로 두는 제안: 혼자 보면 지금 그래프와 같아 통과하지만, 앞의 폐지를 반영하면 상태가 둘이 된다
    active = first.changes[0].o
    again = first.model_copy(update={"id": "table-test-again", "summary": "시험: 다시 사용",
                                     "changes": [first.changes[1].model_copy(update={"o": active})]})
    assert ps.add_new([again]) == 1 and group_of(again)[0] == group
    assert c.get("/api/proposals/table-test-again/validate").json()["conforms"] is True

    pending = [p.id for p in ps.load() if p.status == "pending" and group_of(p)[0] == group]
    chk = c.get("/api/groups/check", params={"group": group}).json()
    assert [x["id"] for x in chk["items"]] == pending and chk["passing"] == len(pending) - 1, chk
    bad = next(x for x in chk["items"] if x["id"] == "table-test-again")
    assert not bad["conforms"] and any("상태는 정확히 하나" in m for m in bad["messages"]), bad

    assert c.post("/api/groups/decision", json={"group": group, "result": "approved", "reviewer": ""}).status_code == 400
    version = review_app.state["version"]
    r = c.post("/api/groups/decision", json={"group": group, "result": "approved", "reviewer": "묶음 검토자"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decided"] == [x for x in pending if x != "table-test-again"], body
    assert [x["id"] for x in body["skipped"]] == ["table-test-again"], body["skipped"]
    assert review_app.state["version"] == version + 1  # 묶음 하나에 판 번호는 한 번 오른다
    saved = {p.id: p for p in ps.load()}
    assert all(saved[x].status == "approved" and saved[x].decision.reviewer == "묶음 검토자" for x in body["decided"])
    assert saved["table-test-again"].status == "pending"

    # 서버를 다시 켜서 승인 기록으로 그래프를 만들어도 지금 그래프와 같다
    q = f"SELECT ?t ?s WHERE {{ VALUES ?t {{ {' '.join(f'<{saved[x].target}>' for x in body['decided'])} }} ?t <{JGK}status> ?s }}"
    live = sorted((x["t"].value, x["s"].value) for x in review_app.store().query(q))
    fresh = sorted((x["t"].value, x["s"].value) for x in load_store(ps).query(q))
    assert live == fresh and {s for _, s in live} == {JGK + "Retired"}, (live, fresh)

    # 묶어 반려는 사유가 있어야 하고, 남은 대기 제안을 모두 반려한다
    assert c.post("/api/groups/decision", json={"group": group, "result": "rejected", "reviewer": "묶음 검토자"}).status_code == 400
    r = c.post("/api/groups/decision", json={"group": group, "result": "rejected", "reviewer": "묶음 검토자", "comment": "시험 반려"})
    assert r.status_code == 200 and r.json()["decided"] == ["table-test-again"], r.text
    assert c.get("/api/groups/check", params={"group": group}).status_code == 404
    assert c.post("/api/groups/decision", json={"group": group, "result": "rejected", "reviewer": "a", "comment": "b"}).status_code == 404

    st = c.get("/api/status").json()
    assert st["graph_loaded"] and st["triples"] == len(review_app.store()) and st["pending"] == sum(
        p.status == "pending" for p in ps.load()), st


def check_correction(c, tmp: Path, review_app) -> None:
    """결정 정정: 앞의 결정은 기록에 남고, 승인을 거두면 그래프를 다시 만들고, 뒤 승인이 기대면 막는다."""
    from src.graph.store import load_store
    from src.review.proposals import ProposalStore

    ps = ProposalStore(tmp)
    post = lambda pid, **b: c.post(f"/api/proposals/{pid}/correction", json={"reviewer": "정정 검토자", **b})
    status_of = lambda u: [x["s"].value for x in review_app.store().query(f"SELECT ?s WHERE {{ <{u}> <{JGK}status> ?s }}")]

    pending = next(p for p in ps.load() if p.status == "pending")
    r = post(pending.id, to="approved", reason="x")
    assert r.status_code == 409 and r.json()["detail"] == "아직 결정하지 않은 제안입니다", r.text
    x = next(p for p in ps.load() if p.status == "approved" and p.decision.reviewer == "묶음 검토자")
    assert post(x.id, to="pending").json()["detail"] == "정정 사유를 입력해 주세요"
    assert post(x.id, to="approved", reason="x").status_code == 409  # 이미 승인된 제안

    # 반려를 승인으로: SHACL을 어기면 막는다 (같은 건물을 다시 사용으로 두는 시험 제안)
    r = post("table-test-again", to="approved", reason="다시 보니 맞음")
    assert r.status_code == 409 and "데이터 규칙" in r.json()["detail"], r.text

    # 승인 거두기: 대상이 원래 상태로 돌아가고, 앞의 결정은 정정 기록에 남는다
    assert status_of(x.target) == [JGK + "Retired"]
    version = review_app.state["version"]
    r = post(x.id, to="pending", reason="폐지 대상 건물을 잘못 찾음")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending" and body["decision"] is None and len(body["corrections"]) == 1, body
    cor = body["corrections"][0]
    assert (cor["from_status"], cor["to_status"], cor["replaced"]["reviewer"]) == ("approved", "pending", "묶음 검토자"), cor
    assert review_app.state["version"] == version + 1 and status_of(x.target) == [JGK + "Active"]
    assert not bool(review_app.store().query(f"ASK {{ <{x.uri}> ?p ?o }}"))  # 거둔 제안의 노드도 그래프에서 빠진다
    assert all(h["id"] != x.id for h in c.get("/api/feature", params={"uri": x.target}).json()["history"])
    row = next(p for p in c.get("/api/proposals").json() if p["id"] == x.id)
    assert row["corrections"] == 1 and row["status"] == "pending"

    # 다시 승인하고, 같은 건물을 바꾸는 제안을 뒤에 승인하면 앞 제안의 승인은 거둘 수 없다
    assert c.post(f"/api/proposals/{x.id}/decision", json={"result": "approved", "reviewer": "정정 검토자"}).status_code == 200
    label = x.changes[0].model_copy(update={"op": "add", "p": "http://www.w3.org/2000/01/rdf-schema#label",
                                            "o": x.changes[0].o.model_copy(update={"kind": "literal", "value": "시험 이름", "lang": "ko"})})
    z = x.model_copy(update={"id": "test-label-z", "summary": "시험: 이름 붙이기", "action": "update", "status": "pending",
                             "decision": None, "corrections": [], "changes": [label]})
    assert ps.add_new([z]) == 1
    assert c.post("/api/proposals/test-label-z/decision", json={"result": "approved", "reviewer": "정정 검토자"}).status_code == 200
    r = post(x.id, to="rejected", reason="잘못 찾음")
    assert r.status_code == 409 and "test-label-z" in r.json()["detail"], r.text

    # 뒤의 제안부터 반려로 정정하면 앞 제안도 거둘 수 있다. 반려를 다시 승인으로 정정하면 그래프에 바로 얹는다
    assert post("test-label-z", to="rejected", reason="이름 근거 없음").status_code == 200
    assert post(x.id, to="rejected", reason="잘못 찾음").status_code == 200
    assert status_of(x.target) == [JGK + "Active"]
    r = post("test-label-z", to="approved", reason="근거 확인함")
    assert r.status_code == 200 and len(r.json()["corrections"]) == 2, r.text
    labels = [x["l"].value for x in review_app.store().query(
        f"SELECT ?l WHERE {{ <{x.target}> <http://www.w3.org/2000/01/rdf-schema#label> ?l }}")]
    assert "시험 이름" in labels, labels

    # 정정을 거친 기록으로 다시 만든 그래프도 지금 그래프와 같다
    live = {str(q) for q in review_app.store().quads_for_pattern(None, None, None, None)}
    fresh = {str(q) for q in load_store(ps).quads_for_pattern(None, None, None, None)}
    assert live == fresh, (len(live - fresh), len(fresh - live))


def check_errors(review_app) -> None:
    """처리 못 한 예외는 500과 detail 한 문장으로 준다."""
    from fastapi.testclient import TestClient

    real = review_app.proposals

    class Boom:
        def load(self):
            raise RuntimeError("시험용 오류")
    review_app.proposals = Boom()
    review_app.log.disabled = True  # 일부러 낸 오류의 traceback은 찍지 않는다
    try:
        r = TestClient(review_app.app, base_url=LOCAL, raise_server_exceptions=False).get("/api/proposals")
    finally:
        review_app.proposals = real
        review_app.log.disabled = False
    assert r.status_code == 500 and r.json() == {"detail": "서버 오류가 났다"}, r.text
    r = TestClient(review_app.app, base_url=LOCAL).post("/api/ask", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str) and isinstance(r.json()["errors"], list), r.text


def check_host_origin(c, tmp, review_app) -> None:
    """다른 이름으로 들어온 요청(DNS rebinding)과 다른 출처의 POST를 막는다."""
    from src.review.proposals import ProposalStore
    for host in ("evil.example", "evil.example:8000", "localhost.evil.example", "127.0.0.1.nip.io"):
        assert c.get("/api/status", headers={"Host": host}).status_code == 400, host
    for host in ("localhost", "localhost:8000", "127.0.0.1:8000", "[::1]:8000"):
        assert c.get("/api/status", headers={"Host": host}).status_code == 200, host

    # DNS rebinding으로 승인까지 가지 못한다
    target = fresh(review_app, 1)[0]
    assert ProposalStore(tmp).add_new([note("test-host", target)]) == 1
    r = c.post("/api/proposals/test-host/decision", json=OK, headers={"Host": "evil.example:8000"})
    assert r.status_code == 400 and ProposalStore(tmp).get("test-host").status == "pending", r.text

    url, here = "/api/proposals/없는-제안/decision", {"Host": "localhost:8000"}
    for origin in ("http://evil.example", "https://localhost:8000", "http://localhost:9000", "http://127.0.0.1:8000", "null"):
        r = c.post(url, json=OK, headers={**here, "Origin": origin})
        assert r.status_code == 403, (origin, r.status_code)
    assert c.post(url, json=OK, headers={**here, "Origin": "http://localhost:8000"}).status_code == 404  # 같은 출처는 지나간다
    assert c.post(url, json=OK, headers=here).status_code == 404  # Origin이 없으면 브라우저가 아니다

    # 다른 사이트의 페이지는 비싼 GET도 부를 수 없다. 화면으로 들어오는 링크는 된다
    assert c.get("/api/status", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.get("/api/status", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert c.get("/map", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200
    # 재현(재공격 보고 7): 대문자 SAME-SITE는 막지 않았다. 값은 대소문자를 가리지 않고, 같은 출처와 직접 연 요청만 받는다
    for site in ("SAME-SITE", "Cross-Site", " cross-site ", "same-site", "unknown", ""):
        assert c.get("/api/status", headers={"Sec-Fetch-Site": site}).status_code == 403, site
    for site in ("Same-Origin", "NONE", "none"):
        assert c.get("/api/status", headers={"Sec-Fetch-Site": site}).status_code == 200, site


def check_group_holds(c, tmp, review_app) -> None:
    """묶음 승인은 LLM 제안과 경고가 붙은 제안을 빼고 알린다. 검사 표에 종류, 근거, 경고가 있다."""
    from src.review.proposals import ProposalStore, group_of
    ps = ProposalStore(tmp)
    a, b, d = fresh(review_app, 3)
    key = "11500-2099-1"
    plain = note("test-hold-plain", a, notice_key=key)
    llm = note("test-hold-llm", b, notice_key=key).model_copy(update={"source_kind": "llm-notice"})
    flagged = note("test-hold-flag", d, notice_key=key, flags=["입력 가드 경고"])
    assert ps.add_new([plain, llm, flagged]) == 3
    group = group_of(plain)[0]

    chk = c.get("/api/groups/check", params={"group": group}).json()
    rows = {x["id"]: x for x in chk["items"]}
    assert chk["passing"] == 1, chk
    assert rows["test-hold-plain"]["action"] == "update" and rows["test-hold-plain"]["evidence"] == ["시험 근거"], rows
    assert not rows["test-hold-plain"]["held"] and rows["test-hold-plain"]["conforms"] is True, rows
    assert rows["test-hold-flag"]["flags"] == ["입력 가드 경고"] and rows["test-hold-flag"]["held"], rows
    assert rows["test-hold-llm"]["held"] and rows["test-hold-llm"]["source_kind"] == "llm-notice", rows

    r = c.post("/api/groups/decision", json={**OK, "group": group})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decided"] == ["test-hold-plain"] and {x["id"] for x in body["held"]} == {"test-hold-llm", "test-hold-flag"}, body
    assert body["skipped"] == [] and all(x["held"] for x in body["held"]), body
    saved = {p.id: p for p in ps.load()}
    assert saved["test-hold-llm"].status == saved["test-hold-flag"].status == "pending", saved["test-hold-llm"].status

    # 남은 제안이 모두 빠지면 승인하지 않고 까닭을 말한다. 하나씩은 승인할 수 있다
    r = c.post("/api/groups/decision", json={**OK, "group": group})
    assert r.status_code == 409 and "하나씩" in r.json()["detail"], r.text
    assert c.post("/api/proposals/test-hold-flag/decision", json=OK).status_code == 200
    row = next(x for x in c.get("/api/proposals").json() if x["id"] == "test-hold-flag")
    assert row["flags"] == ["입력 가드 경고"] and row["group"] == group, row


def check_bad_geometry(c, tmp, review_app) -> None:
    """읽지 못하는 모양은 그 객체만 건너뛴다. 레이어, 객체, 둘레 보기가 깨지지 않고 표준 JSON을 준다."""
    import pyoxigraph as ox

    from src.review.proposals import Change, ProposalStore, Term
    wkts = {"good": "POINT (126.86 37.55)", "junk": "이건 WKT가 아니다",
            "crs": "<http://www.opengis.net/def/crs/OGC/1.3/CRS84> POINT (126.86 37.55)",
            "nan": "POINT (NaN NaN)", "empty": "POINT EMPTY", "far": "POINT (999 999)", "inf": "POINT (Infinity 37.5)",
            # 재현: POINT ZM의 M이 NaN인 모양 하나로 건물 레이어 전체가 500이었다 (rv/a1.py)
            "zm-nan": "POINT ZM (126.86 37.55 0 nan)", "z-nan": "POINT Z (126.86 37.55 nan)", "z-inf": "POINT Z (126.86 37.55 1e999)",
            "ring": "LINEARRING (126.86 37.55, 126.87 37.55, 126.87 37.56, 126.86 37.55)",
            "curve": "CIRCULARSTRING (126.86 37.55, 126.87 37.56, 126.88 37.55)", "bag": "GEOMETRYCOLLECTION (POINT (126.86 37.55))",
            "line-nan": "LINESTRING (126.86 37.55, NaN NaN, 126.87 37.56)",
            "z-ok": "POINT Z (126.861 37.551 12.5)"}  # 그래프 밖에서 들어온 높이 값은 버리고 2차원으로 그린다
    drawn = {"good", "z-ok"}
    uri = lambda name: f"{BASE}/building/wkt-{name}"
    rows = [f'<{JGK}Active> <{RDFS}label> "사용"@ko .']
    for name, wkt in wkts.items():
        rows += [f"<{uri(name)}> a <{JGK}Building> ; <{JGK}status> <{JGK}Active> ; <{GEO}hasDefaultGeometry> <{uri(name)}/geometry> ; "
                 f"<{GEO}hasGeometry> <{uri(name)}/geometry> .",
                 f'<{uri(name)}/geometry> <{GEO}asWKT> "{wkt}"^^<{GEO}wktLiteral> .']
    g = ox.Store()
    g.update("INSERT DATA {\n" + "\n".join(rows) + "\n}")

    bad_wkt = Change(op="add", s=uri("nan") + "/geometry", p=GEO + "asWKT",
                     o=Term(kind="literal", value="POINT (NaN NaN)", datatype=GEO + "wktLiteral"))
    assert ProposalStore(tmp).add_new([note("test-geom", uri("nan"), changes=[bad_wkt], location=(126.86, 37.55))]) == 1

    keep = dict(review_app.state)
    review_app.state.update(store=g, version=keep["version"] + 1000)
    review_app.layer_cache.clear()
    try:
        r = c.get("/api/layers/building")
        assert r.status_code == 200, r.text[:200]
        body = strict(r)
        assert sorted(f["properties"]["uri"] for f in body["features"]) == sorted(uri(n) for n in drawn), body
        assert body["skipped"] == len(wkts) - len(drawn), body["skipped"]
        assert all(len(f["geometry"]["coordinates"]) == 2 for f in body["features"]), body["features"]
        for name in wkts:
            r = c.get("/api/feature", params={"uri": uri(name)})
            assert r.status_code == 200, (name, r.text[:200])
            geometry = strict(r)["geometry"]
            assert (geometry is not None) == (name in drawn), name
            assert geometry is None or geometry == {"type": "Point", "coordinates": list(map(float, wkts[name].split("(")[1].rstrip(")").split()[:2]))}, geometry
        r = c.get("/api/proposals/test-geom/context")
        assert r.status_code == 200, r.text[:200]
        assert {f["properties"]["uri"] for f in strict(r)["features"]} <= {uri(n) for n in drawn}, r.text[:200]
        assert strict(r)["skipped"] >= 1, r.text[:200]  # 제안에 든 NaN 모양
    finally:
        review_app.state.update(keep)
        review_app.layer_cache.clear()


def check_correction_reach(c, tmp, review_app) -> None:
    """목적어로 가리키는 제안도 정정을 막고, 거두면 규칙을 어기게 되면 거두지 않는다."""
    from src.review.proposals import ProposalStore
    ps = ProposalStore(tmp)
    post = lambda pid, **b: c.post(f"/api/proposals/{pid}/correction", json={"reviewer": "정정 검토자", "reason": "시험", **b})

    # 뒤 제안이 앞 제안이 바꾼 객체를 목적어로 가리킨다
    s, a = fresh(review_app, 2)
    ps.add_new([note("test-ref-p", s), note("test-ref-q", a, changes=[link("add", a, RDFS + "seeAlso", s)])])
    assert c.post("/api/proposals/test-ref-p/decision", json=OK).status_code == 200
    assert c.post("/api/proposals/test-ref-q/decision", json=OK).status_code == 200
    r = post("test-ref-p", to="pending")
    assert r.status_code == 409 and "test-ref-q" in r.json()["detail"], r.text
    assert post("test-ref-q", to="rejected").status_code == 200
    assert post("test-ref-p", to="pending").status_code == 200

    # 앞 제안을 거두면 U1의 잇기가 되살아나, 뒤 제안이 이은 U2와 함께 '한 대상에는 건물 하나만' 규칙을 어긴다.
    # 두 제안이 함께 바꾼 주어가 없어 뒤 승인을 찾아서는 막지 못한다. 거둔 뒤의 그래프를 검사해야 잡힌다
    u1, u2, target = fresh(review_app, 3)
    same = JGK + "sameBuildingAs"
    ps.add_new([note("test-same-0", u1, changes=[link("add", u1, same, target)]),
                note("test-same-1", u1, changes=[link("remove", u1, same, target)]),
                note("test-same-2", u2, changes=[link("add", u2, same, target)])])
    for pid in ("test-same-0", "test-same-1", "test-same-2"):
        r = c.post(f"/api/proposals/{pid}/decision", json=OK)
        assert r.status_code == 200, r.text
    version = review_app.state["version"]
    r = post("test-same-1", to="pending")
    assert r.status_code == 409 and "한 대상에는 건물 하나만" in r.json()["detail"], r.text
    assert ps.get("test-same-1").status == "approved" and review_app.state["version"] == version

    # 뒤 제안부터 거두면 앞 제안도 거둘 수 있다
    assert post("test-same-2", to="rejected").status_code == 200
    assert post("test-same-1", to="rejected").status_code == 200


def check_save_order(c, tmp, review_app) -> None:
    """그래프를 먼저 만들고 파일에 저장한다. 어느 쪽이 실패해도 둘이 어긋나지 않고 다시 시도할 수 있다."""
    from fastapi.testclient import TestClient

    import src.graph.store as graph_store
    from src.review import proposals as P
    ps = P.ProposalStore(tmp)
    loose = TestClient(review_app.app, base_url=LOCAL, raise_server_exceptions=False)
    a, b, d, e = fresh(review_app, 4)
    ps.add_new([note("test-order-a", a), note("test-order-b", b), note("test-order-e", d, notice_key="11500-2099-2"),
                note("test-order-f", e, notice_key="11500-2099-2")])
    in_graph = lambda pid: bool(review_app.store().query(f"ASK {{ <{ps.get(pid).uri}> ?p ?o }}"))
    group = {**OK, "group": "notice:11500-2099-2"}
    undo = {"to": "pending", "reviewer": "정정 검토자", "reason": "시험"}

    # 그래프 반영이 실패하면 저장하지 않는다
    real_update = P.Proposal.sparql_update

    def broken_update(self, *args, **kw):
        if self.decision is not None and self.decision.seq is not None:  # 저장할 결정이 정해진 뒤의 반영만 실패시킨다
            raise OSError("디스크 오류 흉내")
        return real_update(self, *args, **kw)
    P.Proposal.sparql_update = broken_update
    review_app.log.disabled = True
    try:
        one = loose.post("/api/proposals/test-order-a/decision", json=OK)
        many = loose.post("/api/groups/decision", json=group)
    finally:
        P.Proposal.sparql_update = real_update
        review_app.log.disabled = False
    assert one.status_code == 409 and ps.get("test-order-a").status == "pending" and not in_graph("test-order-a"), one.text
    assert many.status_code == 409 and ps.get("test-order-e").status == ps.get("test-order-f").status == "pending", many.text
    assert c.post("/api/proposals/test-order-a/decision", json=OK).status_code == 200 and in_graph("test-order-a")
    assert c.post("/api/groups/decision", json=group).status_code == 200 and in_graph("test-order-f")

    # 파일 저장이 실패하면 그래프가 그대로다
    real_write = P.ProposalStore._write

    def full_disk(self, *args):
        raise OSError("디스크가 찼다 흉내")
    version = review_app.state["version"]
    P.ProposalStore._write = full_disk
    review_app.log.disabled = True
    try:
        approve = loose.post("/api/proposals/test-order-b/decision", json=OK)
        withdraw = loose.post("/api/proposals/test-order-a/correction", json=undo)
    finally:
        P.ProposalStore._write = real_write
        review_app.log.disabled = False
    assert approve.status_code == 500 and withdraw.status_code == 500, (approve.text, withdraw.text)
    assert review_app.state["version"] == version and not in_graph("test-order-b") and in_graph("test-order-a")
    assert ps.get("test-order-b").status == "pending" and ps.get("test-order-a").status == "approved"
    assert c.post("/api/proposals/test-order-b/decision", json=OK).status_code == 200 and in_graph("test-order-b")

    # 정정 뒤 그래프를 다시 만들지 못하면 저장하지 않고, 다시 시도할 수 있다
    good = graph_store.TTL
    broken = tmp.parent / "broken.ttl"
    broken.write_text("<a:a> <a:b> .", encoding="utf-8")
    graph_store.TTL = broken
    review_app.log.disabled = True
    try:
        r = loose.post("/api/proposals/test-order-a/correction", json=undo)
    finally:
        graph_store.TTL = good
        review_app.log.disabled = False
    assert r.status_code == 500 and ps.get("test-order-a").status == "approved" and in_graph("test-order-a"), r.text
    r = c.post("/api/proposals/test-order-a/correction", json=undo)
    assert r.status_code == 200 and not in_graph("test-order-a"), r.text


def check_slow_checks(c, tmp, review_app) -> None:
    """검사는 잠금 밖에서 돌고, 동시에 도는 수와 시간에 상한이 있다."""
    from fastapi.testclient import TestClient

    import src.graph.validate as V
    from src.review.proposals import ProposalStore, group_of
    ps = ProposalStore(tmp)
    a, b, d = fresh(review_app, 3)
    key = "11500-2099-3"
    ps.add_new([note("test-slow-a", a, notice_key=key), note("test-slow-b", b, notice_key=key), note("test-slow-d", d)])
    group = group_of(ps.get("test-slow-a"))[0]

    started, release, out = threading.Event(), threading.Event(), {}
    real = V.check_proposal

    def slow(g, p):
        started.set()
        release.wait(20)
        return real(g, p)
    V.check_proposal = slow
    t = threading.Thread(target=lambda: out.update(
        r=TestClient(review_app.app, base_url=LOCAL).get("/api/groups/check", params={"group": group})))
    t.start()
    try:
        assert started.wait(20), "검사가 시작되지 않았다"
        free = review_app.lock.acquire(timeout=1)
        if free:
            review_app.lock.release()
        # 묶음 검사가 도는 동안 반려는 기다리지 않고, 묶음 검사는 하나만 돈다
        reject_ok = free and c.post("/api/proposals/test-slow-d/decision",
                                    json={"result": "rejected", "reviewer": "시험 검토자", "comment": "시험"}).status_code == 200
        second = c.get("/api/groups/check", params={"group": group}).status_code if free else None
    finally:
        release.set()
        t.join()
        V.check_proposal = real
    assert free and reject_ok, "묶음 검사가 전역 잠금을 잡고 있다"
    assert second == 429 and out["r"].status_code == 200, (second, out["r"].status_code)

    # 단건 검사도 동시에 도는 수를 묶는다
    held = 0
    wait = review_app.SLOT_WAIT
    review_app.SLOT_WAIT = 0.01
    try:
        while review_app.validate_slots.acquire(blocking=False):
            held += 1
        r = c.get("/api/proposals/test-slow-a/validate")
    finally:
        review_app.SLOT_WAIT = wait
        for _ in range(held):
            review_app.validate_slots.release()
    assert held > 0 and r.status_code == 429, (held, r.text)

    # 시간 상한을 넘기면 남은 제안은 검사하지 않고 승인하지도 않는다
    limit = review_app.BATCH_SECONDS
    review_app.BATCH_SECONDS = 0
    try:
        chk = c.get("/api/groups/check", params={"group": group}).json()
        r = c.post("/api/groups/decision", json={**OK, "group": group})
    finally:
        review_app.BATCH_SECONDS = limit
    assert chk["timed_out"] and chk["passing"] == 0 and all("시간 상한" in x["messages"][0] for x in chk["items"]), chk
    assert r.status_code == 409 and ps.get("test-slow-a").status == "pending", r.text


def check_ask_limit(c, tmp, review_app) -> None:
    """질문 호출량에 상한이 있고, 넘으면 모델을 부르지 않고 429로 답한다."""
    import src.query.nl as nl
    real_ask, real_system = nl.ask, review_app.system_prompt
    calls = []

    def fake(question, **kw):
        calls.append(question)
        return {"question": question, "vars": [], "rows": [], "geojson": None}
    nl.ask, review_app.system_prompt = fake, lambda: "시험"
    review_app.ask_times.clear()
    try:
        for _ in range(review_app.ASK_PER_MINUTE):
            assert c.post("/api/ask", json={"question": "시험"}).status_code == 200
        r = c.post("/api/ask", json={"question": "시험"})
        assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1, (r.status_code, r.headers)
        assert len(calls) == review_app.ASK_PER_MINUTE and c.post("/api/ask", json={"question": " "}).status_code == 400
        old = time.monotonic() - 61  # 1분이 지나면 다시 받는다
        review_app.ask_times.clear()
        review_app.ask_times.extend([old] * review_app.ASK_PER_MINUTE)
        assert c.post("/api/ask", json={"question": "시험"}).status_code == 200
        review_app.ask_times.clear()
        review_app.ask_times.extend([old] * review_app.ASK_PER_DAY)  # 하루 상한
        assert c.post("/api/ask", json={"question": "시험"}).status_code == 429
    finally:
        nl.ask, review_app.system_prompt = real_ask, real_system
        review_app.ask_times.clear()


def check_ask_errors(c, tmp, review_app) -> None:
    """상위 오류 본문은 화면으로 나가지 않고 로그로 간다. 키 꼴의 글은 로그에서도 가린다."""
    import anthropic
    import httpx

    import src.query.nl as nl
    secret = "sk-ant-api03-FAKE000000000000"
    real_ask, real_system = nl.ask, review_app.system_prompt
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    def boom(*a, **k):
        raise anthropic.APIConnectionError(message=f"Illegal header value b'{secret}' organization org-fake-1234", request=request)

    def busy(*a, **k):
        raise nl.QueryBusy("쿼리 일꾼을 준비하고 있다")
    seen = []
    handler = logging.Handler()
    handler.emit = seen.append
    review_app.log.addHandler(handler)
    nl.ask, review_app.system_prompt = boom, lambda: "시험"
    try:
        r = c.post("/api/ask", json={"question": "시험"})
        nl.ask = busy
        waiting = c.post("/api/ask", json={"question": "시험"})
    finally:
        nl.ask, review_app.system_prompt = real_ask, real_system
        review_app.log.removeHandler(handler)
        if hasattr(review_app, "ask_times"):
            review_app.ask_times.clear()
    assert r.status_code == 502 and secret not in r.text and "org-fake" not in r.text and "Illegal" not in r.text, r.text
    logged = " ".join(x.getMessage() for x in seen)
    assert "Illegal header" in logged and secret not in logged, logged
    assert waiting.status_code == 503 and waiting.json()["detail"] == "쿼리 일꾼을 준비하고 있다", waiting.text


def check_odd_requests(c, tmp, review_app) -> None:
    """비표준 JSON, 외톨이 대리문자, 보이지 않는 이름, 말이 안 되는 위치가 500이나 저장으로 가지 않는다."""
    from fastapi.testclient import TestClient

    from src.review.proposals import ProposalStore
    ps = ProposalStore(tmp)
    a, b, d = fresh(review_app, 3)
    ps.add_new([note("test-odd", a), note("test-loc-nan", b, location=(float("nan"), float("nan"))),
                note("test-loc-far", d, location=(999.0, 999.0))])
    loose = TestClient(review_app.app, base_url=LOCAL, raise_server_exceptions=False)
    raw = lambda url, text: loose.post(url, content=text.encode("utf-8"), headers={"Content-Type": "application/json"})
    decision = "/api/proposals/test-odd/decision"

    for text in ('{"question":NaN}', '{"question":Infinity}', '{"question":-Infinity}'):
        r = raw("/api/ask", text)
        assert r.status_code == 422, (text, r.status_code, r.text[:120])
        assert all("input" not in e for e in strict(r)["errors"]), r.text

    for url, text in [(decision, '{"result":"rejected","reviewer":"\\ud800","comment":"x"}'),
                      (decision, '{"result":"rejected","reviewer":"x","comment":"\\ud800"}'),
                      (decision, '{"result":"approved","reviewer":"x\\ud800"}'),
                      ("/api/groups/decision", '{"group":"\\ud800","result":"rejected","reviewer":"x","comment":"y"}'),
                      ("/api/proposals/test-odd/correction", '{"to":"pending","reviewer":"x","reason":"\\ud800"}'),
                      ("/api/ask", '{"question":"\\ud800"}')]:
        r = raw(url, text)
        assert r.status_code in (400, 422), (text, r.status_code, r.text[:120])
        strict(r)
    assert ps.get("test-odd").status == "pending" and not list(tmp.parent.glob("*.tmp"))

    for text in ("", "{", "[]", "null", "[" * 50_000, '{"result":"rejected","reviewer":1,"comment":"x"}'):
        r = raw(decision, text)
        assert r.status_code in (400, 422), (text[:20], r.status_code, r.text[:120])
    r = loose.post(decision, content=b'\xff\xfe{"result":1}', headers={"Content-Type": "application/json"})
    assert r.status_code in (400, 422), r.text[:120]

    for name in ("ㅤ", "ᅟ", "ﾠ", "⠀", "឵", "a͏", "​", "가­나", "️"):
        r = c.post(decision, json={"result": "rejected", "reviewer": name, "comment": "사유"})
        assert r.status_code == 400 and "검토자 이름" in r.json()["detail"], (name, r.status_code, r.text)
    for reason in ("ㅤ", "​ ⠀"):
        r = c.post(decision, json={"result": "rejected", "reviewer": "시험 검토자", "comment": reason})
        assert r.status_code == 400 and "반려 사유" in r.json()["detail"], (reason, r.text)
    assert ps.get("test-odd").status == "pending"

    # 위치가 말이 안 되는 제안도 상세와 결정 응답이 깨지지 않는다 (좌표만 비운다)
    for pid in ("test-loc-nan", "test-loc-far"):
        r = loose.get(f"/api/proposals/{pid}")
        assert r.status_code == 200 and strict(r)["location"] is None, (pid, r.status_code, r.text[:120])
        assert loose.get(f"/api/proposals/{pid}/context").status_code == 404, pid
        r = loose.post(f"/api/proposals/{pid}/decision", json={"result": "rejected", "reviewer": "시험 검토자", "comment": "시험"})
        assert r.status_code == 200 and strict(r)["location"] is None, (pid, r.status_code, r.text[:120])


def check_one_build_and_body_limit(c, tmp, review_app) -> None:
    """빈 레이어를 동시에 달라고 해도 한 번만 만들고, 큰 본문은 읽지 않고 돌려보낸다."""
    from fastapi.testclient import TestClient
    builds = []
    real = review_app.layer_body

    def counted(kind):
        builds.append(kind)
        time.sleep(0.3)
        return real(kind)
    review_app.layer_cache.clear()
    review_app.layer_body = counted
    codes = []
    ts = [threading.Thread(target=lambda: codes.append(TestClient(review_app.app, base_url=LOCAL).get("/api/layers/station").status_code))
          for _ in range(8)]
    try:
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    finally:
        review_app.layer_body = real
    assert codes == [200] * 8 and builds == ["station"], (codes, builds)

    big = {"question": "가" * (2 * 1024 * 1024 // 3)}
    r = c.post("/api/ask", json=big)
    assert r.status_code == 413 and isinstance(r.json()["detail"], str), (r.status_code, r.text[:120])

    def pieces():  # Content-Length 없이 조금씩 보내도 막는다
        yield b'{"question":"'
        for _ in range(20):
            yield b"a" * 8192
    r = c.post("/api/ask", content=pieces(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413, (r.status_code, r.text[:120])
    assert c.post("/api/ask", json={"question": " "}).status_code == 400  # 작은 본문은 그대로 지나간다


def check_defense_headers(c, tmp, review_app) -> None:
    """CSP는 그 화면의 인라인 스크립트만 허용하고, 바깥 파일(글꼴 하나)에는 SRI가 붙는다. 지도 라이브러리는 저장소에서 받는다.
    API 문서는 꺼져 있다."""
    for path in ("/", "/map"):
        r = c.get(path)
        policy = dict(x.strip().split(" ", 1) for x in r.headers["content-security-policy"].split(";"))
        inline = re.findall(r"<script>(.*?)</script>", r.text, re.S)
        assert len(inline) == 1, path
        digest = base64.b64encode(hashlib.sha256(inline[0].encode("utf-8")).digest()).decode()
        assert f"'sha256-{digest}'" in policy["script-src"], (path, policy["script-src"])
        assert "unsafe" not in policy["script-src"] and policy["frame-ancestors"] == "'none'" and policy["object-src"] == "'none'", policy
        assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff", r.headers
        tags = re.findall(r'<(?:script|link)\b[^>]*(?:src|href)="https://[^>]*>', r.text)
        assert len(tags) == 1 and "pretendard" in tags[0], tags
        assert all('integrity="sha384-' in t and 'crossorigin="anonymous"' in t for t in tags), tags
        assert "/static/vendor/maplibre-gl-4.7.1/maplibre-gl.js" in r.text and "unpkg.com" not in r.text, path
        assert " onload=" not in r.text, path   # CSP가 인라인 이벤트를 막으므로 화면 틀에 남기지 않는다
        assert "unpkg.com" not in r.headers["content-security-policy"], path
    for path in ("/api/status", "/api/area", "/static/common.js"):
        r = c.get(path)
        assert r.status_code == 200 and r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff", path
        assert "default-src 'none'" in r.headers["content-security-policy"], path
    assert c.get("/api/status", headers={"Host": "evil.example"}).headers["x-content-type-options"] == "nosniff"
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(path).status_code == 404, path


SCREEN_TEST = r"""
const fs = require("fs"), vm = require("vm");
const [common, ...pages] = process.argv.slice(1);
for (const page of pages) for (const m of fs.readFileSync(page, "utf8").matchAll(/<script>([\s\S]*?)<\/script>/g)) new vm.Script(m[1]);
class Bounds {
  constructor() { this.points = []; }
  extend(c) { if (Math.abs(c[1]) > 90) throw new Error("Invalid LngLat"); this.points.push(c); return this; }
  isEmpty() { return !this.points.length; }
}
const ctx = { maplibregl: { LngLatBounds: Bounds }, document: { addEventListener() {} } };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(common, "utf8"), ctx);
console.log(JSON.stringify(vm.runInContext(`(() => {
  const pt = c => ({ geometry: { type: "Point", coordinates: c } }), r = {};
  r.collection = boundsOf([{ geometry: { type: "GeometryCollection", geometries: [
    { type: "Point", coordinates: [126.8, 37.5] }, { type: "GeometryCollection", geometries: [] }] } }]).points.length;
  r.empty = boundsOf([{ geometry: null }, { geometry: { type: "Point" } }, pt([]), pt([999, 999]), pt([null, null])]).isEmpty();
  r.lines = boundsOf([{ geometry: { type: "LineString", coordinates: [[126, 37], [127, 38]] } }]).points.length;
  const fitted = [], map = { fitBounds: b => fitted.push(b) };
  fitTo(map, [pt([999, 999])], {});
  r.emptyFit = fitted.length;
  fitTo(map, [pt([126, 37])], {});
  r.fit = fitted.length;
  r.far = [viewFrom("15", "0,999"), viewFrom("15", "999,0"), viewFrom("99", "126,37"), viewFrom("15", "a,b"), viewFrom("15", "126"), viewFrom(null, null)];
  r.good = viewFrom("15.5", "126.86, 37.55");
  const by = groupBy([{ k: "constructor" }, { k: "__proto__" }, { k: "x" }, { k: "x" }], t => t.k);
  r.by = [by.constructor.length, by["__proto__"].length, by.x.length, by.toString === undefined];
  return r;
})()`, ctx)));
"""


def check_screen_tools(c, tmp, review_app) -> None:
    """화면이 멈추던 입력: GeometryCollection, 범위 밖 좌표, 주소창의 c=0,999, 속성 이름 constructor, __proto__."""
    node = shutil.which("node")
    if node is None:
        print("node가 없어 화면 도구 확인을 건너뛴다")
        return
    web = ROOT / "web"
    done = subprocess.run([node, "-e", SCREEN_TEST, str(web / "common.js"), str(web / "review.html")],
                          capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert done.returncode == 0, done.stderr[-600:]
    r = json.loads(done.stdout)
    assert r["collection"] == 1 and r["empty"] is True and r["lines"] == 2, r
    assert r["emptyFit"] == 0 and r["fit"] == 1, r
    assert r["far"] == [None] * 6 and r["good"] == {"zoom": 15.5, "center": [126.86, 37.55]}, r
    assert r["by"] == [1, 1, 2, True], r


def check_status_report(c, tmp, review_app) -> None:
    """재적용 충돌과 읽지 못해 건너뛴 승인이 /api/status에 나온다."""
    from src.review.proposals import Change, Decision, ProposalStore, Term
    a = fresh(review_app, 1)[0]
    decided = {"status": "approved", "decision": Decision(result="approved", reviewer="시험 검토자", at="2026-10-30T10:00:00+09:00")}
    gone = Change(op="remove", s=a, p=RDFS + "label", o=Term(kind="literal", value="없던 이름", lang="ko"))
    stale = note("test-stale", a, changes=[gone], **decided)
    broken = {**note("test-broken-record", a, **decided).model_dump(), "id": "깨진 id!"}
    other = tmp.parent / "status.json"
    other.write_text(json.dumps([stale.model_dump(), broken, note("test-still-pending", a).model_dump()], ensure_ascii=False), encoding="utf-8")
    reports = tmp.parent / "notices"
    reports.mkdir()
    for name, row in {"a": {"file": "많은 문서.txt", "held": "제안 21건이 상한 20건을 넘어 만들지 않았다"},
                      "b": {"file": "멈춘 문서.txt", "error": "ValueError: 시험"}, "c": {"file": "보통 문서.txt", "differ": []},
                      "e": {"file": "달라진 문서.txt", "differ": ["llm-1"]}}.items():
        (reports / f"{name}.json").write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    (reports / "d.json").write_text("{", encoding="utf-8")

    keep_proposals, keep_state, keep_reports = review_app.proposals, dict(review_app.state), review_app.NOTICE_REPORTS
    review_app.proposals = ProposalStore(other)
    review_app.NOTICE_REPORTS = reports
    review_app.state["store"] = None
    try:
        before = c.get("/api/status").json()
        assert before["graph_loaded"] is False and before["conflicts"] == [] and before["pending"] == 1, before
        review_app.store()
        st = c.get("/api/status").json()
    finally:
        review_app.proposals = keep_proposals
        review_app.NOTICE_REPORTS = keep_reports
        review_app.state.update(keep_state)
        review_app.layer_cache.clear()
    assert [x["id"] for x in st["conflicts"]] == ["test-stale"] and "없는 값" in st["conflicts"][0]["reason"], st
    assert [(x["id"], x["status"]) for x in st["skipped"]] == [("깨진 id!", "approved")] and st["skipped_approved"] == 1, st
    assert "raw" not in st["skipped"][0], st
    assert [x["file"] for x in st["notices"]] == ["많은 문서.txt", "멈춘 문서.txt", "달라진 문서.txt"] and "상한" in st["notices"][0]["why"], st


def check_edition_api(c, tmp, review_app) -> None:
    """K 묶음이 바꾼 기록과 맞는다: 정정도 이력에 남고, 날짜만 준 끝 시각은 그날 끝이다. 기록 연결은 객체 연결로 보이지 않는다."""
    from src.review.proposals import ProposalStore
    ps = ProposalStore(tmp)
    a = fresh(review_app, 1)[0]
    p = note("test-edition", a)
    ps.add_new([p])
    r = c.post("/api/proposals/test-edition/decision", json=OK)
    assert r.status_code == 200, r.text
    today = r.json()["decision"]["at"][:10]
    assert r.json()["decision"]["seq"] is not None

    mine = lambda: [x for x in c.get("/api/editions/changes", params={"since": today, "until": today}).json()["changes"]
                    if x["proposal"] == p.uri]
    assert {x["action"] for x in mine()} == {"update"}, mine()
    assert all(x["o_nt"] for x in mine()), mine()
    node = c.get("/api/feature", params={"uri": p.uri}).json()
    assert node["in_graph"] and JGK + "changeOf" not in node["inbound_counts"], node["inbound_counts"]

    r = c.post("/api/proposals/test-edition/correction", json={"to": "pending", "reviewer": "정정 검토자", "reason": "시험"})
    assert r.status_code == 200, r.text
    assert {x["action"] for x in mine()} == {"update", "withdraw"}, mine()
    assert not c.get("/api/feature", params={"uri": p.uri}).json()["in_graph"]


def check_any_wkt(c, tmp, review_app) -> None:
    """어떤 WKT를 넣어도 검사, 둘레 보기, 승인이 500이 되지 않고 까닭을 문장으로 준다. 받지 않는 모양은 승인되지 않는다.

    재현(rv/a1.py): POINT ZM의 NaN과 LINEARRING은 승인됐다. CIRCULARSTRING은 검사, 둘레 보기, 승인이 모두 500이었다.
    NaN이 든 LINESTRING은 둘레 보기가 500이고 거부 사유가 '스스로 교차'로 틀렸다.
    """
    import pyoxigraph as ox
    from fastapi.testclient import TestClient

    from src.review.proposals import Change, ProposalStore, Term
    b = fresh(review_app, 1)[0]
    g = review_app.store()
    geom = next(q.object for q in g.quads_for_pattern(ox.NamedNode(b), ox.NamedNode(GEO + "hasDefaultGeometry"), None, None)).value
    old = next(q.object for q in g.quads_for_pattern(ox.NamedNode(geom), ox.NamedNode(GEO + "asWKT"), None, None)).value
    wkt = lambda v: Term(kind="literal", value=v, datatype=GEO + "wktLiteral")
    values = ["POINT ZM (126.86 37.55 0 nan)", "POINT Z (126.86 37.55 nan)", "POINT M (126.86 37.55 1e999)", "POINT Z (126.86 37.55 10)",
              "LINEARRING (126.86 37.55, 126.87 37.55, 126.87 37.56, 126.86 37.55)", "CIRCULARSTRING (126.86 37.55, 126.87 37.56, 126.88 37.55)",
              "COMPOUNDCURVE ((126.86 37.55, 126.87 37.56))", "GEOMETRYCOLLECTION (POINT (126.86 37.55))", "MULTIPOINT ((126.86 37.55), EMPTY)",
              "LINESTRING (126.86 37.55, NaN NaN, 126.87 37.56)", "POLYGON ((126.86 37.55, 126.87 37.55, inf 37.56, 126.86 37.55))",
              "POINT (126.86", "", "POINT EMPTY", "TIN (((0 0 0, 0 1 0, 1 0 0, 0 0 0)))"]
    ps = ProposalStore(tmp)
    made = [note(f"test-any-wkt-{i}", b, changes=[Change(op="remove", s=geom, p=GEO + "asWKT", o=wkt(old)),
                                                    Change(op="add", s=geom, p=GEO + "asWKT", o=wkt(v))], location=(126.86, 37.55))
            for i, v in enumerate(values)]
    assert ps.add_new(made) == len(made)
    loose = TestClient(review_app.app, base_url=LOCAL, raise_server_exceptions=False)
    version = review_app.state["version"]
    for p, v in zip(made, values):
        r = loose.get(f"/api/proposals/{p.id}/validate")
        assert r.status_code == 200, (v, r.status_code, r.text[:200])
        found = strict(r)
        said = " ".join(x["message"] for x in found["results"])
        assert found["conforms"] is False and "WKT" in said, (v, said)
        if "NaN" in v:
            assert "숫자가 아니다" in said and "교차" not in said, (v, said)
        r = loose.get(f"/api/proposals/{p.id}/context")
        assert r.status_code == 200, (v, r.status_code, r.text[:200])
        assert all(len(f["geometry"]["coordinates"]) for f in strict(r)["features"])
        r = loose.post(f"/api/proposals/{p.id}/decision", json=OK)
        assert r.status_code == 409 and "WKT" in r.json()["detail"], (v, r.status_code, r.text[:200])
    assert review_app.state["version"] == version and all(ps.get(p.id).status == "pending" for p in made)
    assert c.get("/api/layers/building").status_code == 200


HOLD = """import sys, time
from pathlib import Path
from src.review.proposals import _file_lock
with _file_lock(Path(sys.argv[1])):
    print("held", flush=True)
    time.sleep(float(sys.argv[2]))
"""


def check_lock_wait(c, tmp, review_app) -> None:
    """다른 프로세스가 제안 파일 잠금을 오래 쥐어도, 결정은 앱 잠금 밖에서 FILE_WAIT초만 기다리고 503과 문장으로 끝난다.

    재현(k/lock.py): 잠금을 40초 쥐자 첫 결정은 30초 뒤 500 '서버 오류가 났다', 그사이 둘째 결정은 앱 잠금에 막혀 38.9초를 기다렸다.
    """
    from fastapi.testclient import TestClient

    from src.review import proposals as P
    ps = P.ProposalStore(tmp)
    a, b, d = fresh(review_app, 3)
    ps.add_new([note("test-lock-a", a), note("test-lock-b", b), note("test-lock-d", d)])
    hold = lambda seconds: subprocess.Popen([sys.executable, "-c", HOLD, str(tmp), str(seconds)], cwd=ROOT, stdout=subprocess.PIPE)
    holder = hold(60)
    try:
        assert holder.stdout.readline().strip() == b"held"
        answers = {}

        def send(pid):
            t0 = time.perf_counter()
            r = TestClient(review_app.app, base_url=LOCAL, raise_server_exceptions=False).post(f"/api/proposals/{pid}/decision", json=OK)
            answers[pid] = (r.status_code, r.json()["detail"], time.perf_counter() - t0)
        threads = [threading.Thread(target=send, args=(pid,)) for pid in ("test-lock-a", "test-lock-b")]
        for t in threads:
            t.start()
            time.sleep(1)
        t0 = time.perf_counter()
        assert c.get("/api/status").status_code == 200 and time.perf_counter() - t0 < 2
        for t in threads:
            t.join()
        assert all(code == 503 and detail == review_app.FILE_BUSY for code, detail, _ in answers.values()), answers
        assert all(took < review_app.FILE_WAIT + 3 for *_, took in answers.values()), answers
        assert ps.get("test-lock-a").status == ps.get("test-lock-b").status == "pending"
    finally:
        holder.kill()
        holder.wait()
    r = c.post("/api/proposals/test-lock-a/decision", json=OK)
    assert r.status_code == 200, r.text

    # 확인한 뒤 다른 프로세스가 먼저 잡으면 저장 쪽이 TimeoutError를 낸다. 이것도 503이다
    holder = hold(30)
    keep = review_app.wait_for_file, P.LOCK_TIMEOUT
    review_app.wait_for_file, P.LOCK_TIMEOUT = (lambda: None), 0.5
    try:
        assert holder.stdout.readline().strip() == b"held"
        r = TestClient(review_app.app, base_url=LOCAL, raise_server_exceptions=False).post("/api/proposals/test-lock-b/decision", json=OK)
        assert r.status_code == 503 and r.json()["detail"] == review_app.FILE_BUSY, r.text
    finally:
        review_app.wait_for_file, P.LOCK_TIMEOUT = keep
        holder.kill()
        holder.wait()
    assert ps.get("test-lock-b").status == "pending"

    # 앞 결정이 앱 잠금을 오래 쥐면 LOCK_WAIT 뒤에 503이다
    keep = review_app.LOCK_WAIT
    review_app.LOCK_WAIT = 0.5
    taken, done = threading.Event(), threading.Event()

    def sit():
        with review_app.lock:
            taken.set()
            done.wait(10)
    sitter = threading.Thread(target=sit)
    sitter.start()
    taken.wait(5)
    try:
        r = TestClient(review_app.app, base_url=LOCAL, raise_server_exceptions=False).post("/api/proposals/test-lock-d/decision", json=OK)
        assert r.status_code == 503 and r.json()["detail"] == review_app.TOO_MANY, r.text
    finally:
        review_app.LOCK_WAIT = keep
        done.set()
        sitter.join()
    assert c.post("/api/proposals/test-lock-d/decision", json=OK).status_code == 200


def check_failed_correction_report(c, tmp, review_app) -> None:
    """정정이 막히거나 저장에 실패하면 /api/status의 충돌 목록과 재생 결과가 지금 그래프의 것으로 남는다.

    재현: 정정 저장 전에 새 그래프를 만들며 last_report()를 바꿔, 409로 막힌 뒤에도 거둔 적 없는 승인이 applied에서 빠졌다.
    충돌한 승인을 거두다 저장에 실패하면 /api/status의 충돌 목록이 비었다. 파일에는 그 승인이 그대로 있었다.
    """
    from fastapi.testclient import TestClient

    from src.graph.store import last_report
    from src.review import proposals as P
    from src.review.proposals import Change, Decision, Term
    ps = P.ProposalStore(tmp)
    loose = TestClient(review_app.app, base_url=LOCAL, raise_server_exceptions=False)
    undo = {"to": "pending", "reviewer": "정정 검토자", "reason": "시험"}

    # 거두면 규칙을 어기는 정정(409)
    u1, u2, target, a = fresh(review_app, 4)
    same = JGK + "sameBuildingAs"
    decided = {"status": "approved", "decision": Decision(result="approved", reviewer="시험 검토자", at="2026-11-03T09:00:00+09:00")}
    gone = Change(op="remove", s=a, p=RDFS + "label", o=Term(kind="literal", value="없던 이름", lang="ko"))
    ps.add_new([note("test-rep-0", u1, changes=[link("add", u1, same, target)]), note("test-rep-1", u1, changes=[link("remove", u1, same, target)]),
                note("test-rep-2", u2, changes=[link("add", u2, same, target)]), note("test-rep-stale", a, changes=[gone], **decided)])
    for pid in ("test-rep-0", "test-rep-1", "test-rep-2"):
        assert c.post(f"/api/proposals/{pid}/decision", json=OK).status_code == 200
    review_app.state["store"] = None  # 다시 불러 충돌한 승인(test-rep-stale)이 last_report()에 들게 한다
    review_app.store()
    before = last_report()
    assert "test-rep-stale" in [x["id"] for x in before.conflicts] and "test-rep-1" in before.applied, before.conflicts
    shown = c.get("/api/status").json()["conflicts"]
    r = c.post("/api/proposals/test-rep-1/correction", json=undo)
    assert r.status_code == 409 and "한 대상에는 건물 하나만" in r.json()["detail"], r.text
    assert last_report() is before and "test-rep-1" in last_report().applied
    assert c.get("/api/status").json()["conflicts"] == shown

    # 충돌한 승인을 거두다 저장에 실패한다
    real_write = P.ProposalStore._write

    def full_disk(self, *args):
        raise OSError("디스크가 찼다 흉내")
    P.ProposalStore._write = full_disk
    review_app.log.disabled = True
    try:
        r = loose.post("/api/proposals/test-rep-stale/correction", json=undo)
    finally:
        P.ProposalStore._write = real_write
        review_app.log.disabled = False
    assert r.status_code == 500 and ps.get("test-rep-stale").status == "approved", r.text
    assert c.get("/api/status").json()["conflicts"] == shown and last_report() is before

    # 정정이 저장되면 그때 바뀐다
    assert c.post("/api/proposals/test-rep-stale/correction", json=undo).status_code == 200
    assert "test-rep-stale" not in [x["id"] for x in c.get("/api/status").json()["conflicts"]]
    assert last_report() is not before


def check_broken_file(c, tmp, review_app) -> None:
    """제안 파일 전체가 깨지면 목록, 상태, 결정, 정정, 지도가 500이 아니라 503과 문장으로 답하고, 깨진 파일을 덮어쓰지 않는다.

    재현: 파일이 '{'이면 목록과 상태 API가 500이었다. 맨 바깥이 목록이 아닌 '{}'는 승인을 하나도 얹지 않은 그래프가 아무 말 없이 떴다.
    """
    from fastapi.testclient import TestClient

    from src.review.proposals import ProposalStore
    loose = TestClient(review_app.app, base_url=LOCAL, raise_server_exceptions=False)
    path = tmp.parent / "broken-whole.json"
    keep_proposals, keep_state = review_app.proposals, dict(review_app.state)
    review_app.proposals = ProposalStore(path)  # 깨진 파일 판단은 저장소가 한다. 서버만의 하위 클래스가 아니다
    review_app.log.disabled = True
    calls = [("GET", "/api/proposals", None), ("GET", "/api/status", None), ("GET", "/api/proposals/x", None),
             ("GET", "/api/proposals/x/validate", None), ("GET", "/api/proposals/x/context", None),
             ("POST", "/api/proposals/x/decision", OK), ("POST", "/api/proposals/x/correction", {"to": "pending", "reviewer": "검토자", "reason": "시험"}),
             ("GET", "/api/groups/check?group=x", None), ("POST", "/api/groups/decision", {**OK, "group": "x"}),
             ("GET", "/api/layers/building", None)]
    try:
        # 재현(재공격 보고 6): 목록이지만 기록이 하나도 읽히지 않는 파일은 승인 0건인 그래프가 200으로 떴다
        for content in (b"{", b"", b"null", b"123", b'"[]"', b"{}", b'{"a": 1}', b"\xff\xfe[", b"[{", b"[1, 2, null]", b'[{"id": "x"}]'):
            path.write_bytes(content)
            review_app.state["store"] = None  # 서버를 새로 켠 것처럼 그래프도 파일에서 다시 불러온다
            for method, url, body in calls:
                r = loose.request(method, url, json=body)
                assert r.status_code == 503 and r.json()["detail"] == review_app.FILE_BROKEN, (content, url, r.status_code, r.text[:120])
            assert path.read_bytes() == content, content
        path.write_text("[]", encoding="utf-8")  # 빈 목록은 깨진 파일이 아니다
        assert loose.get("/api/proposals").json() == [] and loose.get("/api/status").status_code == 200
    finally:
        review_app.proposals = keep_proposals
        review_app.state.update(keep_state)
        review_app.layer_cache.clear()
        review_app.log.disabled = False


def check_local_app(c, tmp, review_app) -> None:
    """지도 앱의 로컬 모드 (명세 5.1절, 5.3절, 10절 WP6): 같은 화면 틀, 로컬 자료 입구, 전체 뷰 자료, 카드의 공개 여부, 키 값 숨김."""
    from src.graph.places import build_places
    from src.graph.public import public_view
    html = c.get("/map").text
    cfg = json.loads(re.search(r"window.APP_CONFIG = (\{.*?\});", html).group(1))
    assert cfg["mode"] == "local" and cfg["source"] == "/static/local/source-api.js" and cfg["root"] == "", cfg
    assert cfg["extra"] == list(review_app.LOCAL_EXTRA) and all(c.get(u).status_code == 200 for u in [cfg["source"], *cfg["extra"]]), cfg
    assert '<script type="module" src="/static/app/app.js?v=' in html and "map.html" not in html
    assert not (ROOT / "web" / "map.html").exists(), "옛 지도 화면 파일이 남아 있다"

    # 키는 있음, 없음만 준다
    keep = review_app.has_llm_key
    try:
        review_app.has_llm_key = lambda: False
        caps = c.get("/api/capabilities").json()
        assert caps["ask"] is False and caps["key"] is False and caps["history"] is True, caps
        review_app.has_llm_key = lambda: True
        caps = c.get("/api/capabilities")
        assert caps.json()["ask"] is True and "sk-" not in caps.text and "api_key" not in caps.text.lower(), caps.text
    finally:
        review_app.has_llm_key = keep
    assert set(caps.json()["layers"]) == {"facility", "road", "legaldong", "basemap", "digitalmap"}

    # 자료: 공개판과 같은 모양이고, 전체 뷰라 공공시설이 더해진다
    places = strict(c.get("/api/places"))
    assert len(places["facilities"]) == 23 and all(f["p"].startswith("public-facility/") and f.get("pt") for f in places["facilities"])
    assert places["count"]["buildings"] == len(places["places"]) and places["basis"] and places["station"]["p"].startswith("station/")
    shapes = strict(c.get("/api/shapes"))
    assert shapes["type"] == "FeatureCollection" and {f["properties"]["i"] for f in shapes["features"]} <= {p["i"] for p in places["places"]}
    assert any("h" in f["properties"] for f in shapes["features"]), "입체 보기의 높이(h)가 없다"
    assert strict(c.get("/api/entrances")) and strict(c.get("/api/businesses"))["items"]
    src = strict(c.get("/api/sources"))
    assert src["sources"] and "로컬" in src["history"], src["history"]
    r = c.get("/api/places")
    assert c.get("/api/places", headers={"If-None-Match": r.headers["etag"]}).status_code == 304
    assert c.get("/api/places", headers={"Accept-Encoding": "gzip"}).headers.get("content-encoding") == "gzip"

    # 카드: 공개 사이트에 페이지가 있는 장소만 public이 참이다. 공공시설과 도로 구간은 거짓 (화면이 공개 페이지 링크를 숨긴다)
    fac = strict(c.get("/api/card", params={"path": places["facilities"][0]["p"]}))
    assert fac["public"] is False and fac["kindLabel"] == "공공시설" and fac["pt"], fac
    st = strict(c.get("/api/card", params={"path": places["station"]["p"]}))
    assert st["public"] is True and st["kind"] == "station", st
    seg = c.get("/api/layers/road").json()["features"][0]["properties"]["uri"].split("/id/", 1)[1]
    assert strict(c.get("/api/card", params={"path": seg}))["public"] is False
    bm = c.get("/api/layers/basemap").json()["features"][0]["properties"]["uri"].split("/id/", 1)[1]
    bcard = strict(c.get("/api/card", params={"path": bm}))
    assert bcard["public"] is False and bcard["kindLabel"] == "국가기본도 건물", bcard["kindLabel"]
    pub = public_view()
    pub_places = build_places(pub, "")
    seen = {x["p"] for x in pub_places["places"]}
    sample = [p["p"] for p in pub_places["places"][:40]] + [p["p"] for p in places["places"] if p["p"] not in seen][:40]
    for path in sample:
        card = strict(c.get("/api/card", params={"path": path}))
        assert card["public"] is ((f"{BASE}/{path}") in pub.triples and f"{BASE}/{path}" not in pub.held), path
    for bad, code in (("../etc", 400), ("building/<x>", 400), ("building/0000", 404), ("proposal/x", 404)):
        assert c.get("/api/card", params={"path": bad}).status_code == code, bad
    q = strict(c.get("/api/questions"))
    assert q["items"] and "전체 그래프" in q["note"], q["note"]

    # 승인으로 판이 바뀌면 다시 만든다
    from src.review.proposals import ProposalStore
    v0 = review_app.state["version"]
    a = fresh(review_app, 1)[0]
    ProposalStore(tmp).add_new([note("test-local-app", a)])
    assert c.post("/api/proposals/test-local-app/decision", json=OK).status_code == 200
    assert review_app.state["version"] == v0 + 1
    r2 = c.get("/api/places")
    assert r2.headers["etag"] != r.headers["etag"] and v0 + 1 in review_app.app_cache


CHECKS = [check_host_origin, check_group_holds, check_bad_geometry, check_correction_reach, check_save_order, check_slow_checks,
          check_ask_limit, check_ask_errors, check_odd_requests, check_one_build_and_body_limit, check_defense_headers,
          check_screen_tools, check_status_report, check_edition_api, check_any_wkt, check_lock_wait, check_failed_correction_report,
          check_broken_file, check_local_app]


def run() -> None:
    tmp = Path(tempfile.mkdtemp()) / "proposals.json"
    shutil.copy(REAL, tmp)

    from fastapi.testclient import TestClient

    from src.review import app as review_app
    from src.review.proposals import ProposalStore

    review_app.proposals = ProposalStore(tmp)
    review_app.state["store"] = None
    review_app.preload()  # 서버를 켤 때 하는 미리 불러오기
    assert review_app.state["store"] is not None and review_app.system_cache
    c = TestClient(review_app.app, base_url=LOCAL)

    check_uri_injection(c, review_app)
    check_proposal_safety(tmp)
    check_concurrency(tmp, review_app)
    check_approval_and_layers(c, tmp, review_app)
    check_map_api(c, review_app)
    check_group_decision(c, tmp, review_app)
    check_correction(c, tmp, review_app)
    for check in CHECKS:
        check(c, tmp, review_app)
    check_errors(review_app)
    review_app.state["store"] = None
    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("화면 API 확인 통과")


def test_api():
    run()


if __name__ == "__main__":
    run()
