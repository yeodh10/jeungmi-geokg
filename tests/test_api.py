"""화면 API 확인: URI 주입 막기, 저장과 반영이 깨지지 않는지, 동시 결정, 지도용 API.

실행: python -m tests.test_api
실제 제안 파일은 임시 복사본으로만 쓴다. data/processed/에 그래프와 제안이 만들어져 있어야 한다.
"""
from __future__ import annotations

import json
import logging
import shutil
import socket
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "data" / "processed" / "review" / "proposals.json"
BASE = "https://yeodh10.github.io/jeungmi-geokg/id"
JGK = "https://yeodh10.github.io/jeungmi-geokg/def#"
STATION = f"{BASE}/station/1150010200106660040000001"
DONG = f"{BASE}/admin-dong/1147053000"
WITHIN = JGK + "within"


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
            assert r.status_code == 400 and r.json()["detail"] == "올바른 URI가 아니다", (bad, r.text)
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
        r = TestClient(review_app.app).post(f"/api/proposals/test-many-{i}/decision",
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
        r = TestClient(review_app.app).post(f"/api/proposals/{pid}/decision",
                                           json={"result": result, "reviewer": "테스트 검토자", "comment": "동시"})
        results[result] = (r.status_code, r.json().get("detail"))
    threads = [threading.Thread(target=send, args=(x,)) for x in ("approved", "rejected")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(code for code, _ in results.values()) == [200, 409], results
    assert any(d in ("이미 승인된 제안이다", "이미 반려된 제안이다") for _, d in results.values()), results
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

    # 공개 여부: 이용 조건 확인 중인 출처의 공공시설은 공개 페이지가 없고, 역은 있다
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

    # 도로명주소 전자지도: 이용 조건 확인 전이라 도로 구간은 통째로, 법정동은 모양만 공개를 보류한다
    seg = c.get("/api/layers/road").json()["features"]
    assert seg and all(f["geometry"]["type"] in ("LineString", "MultiLineString") for f in seg)
    one = c.get("/api/feature", params={"uri": seg[0]["properties"]["uri"]}).json()
    assert one["public"] is False, one["public"]
    dong = next(f for f in c.get("/api/layers/legaldong").json()["features"] if f["properties"]["label"] == "등촌동")
    d = c.get("/api/feature", params={"uri": dong["properties"]["uri"]}).json()
    assert d["public"] is True and c.get("/api/feature", params={"uri": dong["properties"]["uri"] + "/geometry"}).json()["public"] is False
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
    assert r.status_code == 409 and r.json()["detail"] == "아직 결정하지 않은 제안이다", r.text
    x = next(p for p in ps.load() if p.status == "approved" and p.decision.reviewer == "묶음 검토자")
    assert post(x.id, to="pending").json()["detail"] == "정정 사유가 필요하다"
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
        r = TestClient(review_app.app, raise_server_exceptions=False).get("/api/proposals")
    finally:
        review_app.proposals = real
        review_app.log.disabled = False
    assert r.status_code == 500 and r.json() == {"detail": "서버 오류가 났다"}, r.text
    r = TestClient(review_app.app).post("/api/ask", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str) and isinstance(r.json()["errors"], list), r.text


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
    c = TestClient(review_app.app)

    check_uri_injection(c, review_app)
    check_proposal_safety(tmp)
    check_concurrency(tmp, review_app)
    check_approval_and_layers(c, tmp, review_app)
    check_map_api(c, review_app)
    check_group_decision(c, tmp, review_app)
    check_correction(c, tmp, review_app)
    check_errors(review_app)
    review_app.state["store"] = None
    shutil.rmtree(tmp.parent, ignore_errors=True)
    print("화면 API 확인 통과")


def test_api():
    run()


if __name__ == "__main__":
    run()
