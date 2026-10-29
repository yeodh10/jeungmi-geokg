"""자연어 질의 확인: 가짜 클라이언트가 돌려주는 SPARQL로 거리 자리표시, 안전 검사, 다시 쓰기, 지도 결과를 본다.

실행: python -m tests.test_nl
"""
from __future__ import annotations

from types import SimpleNamespace

STATION = "https://yeodh10.github.io/jeungmi-geokg/id/station/1150010200106660040000001"


class FakeClient:
    def __init__(self, drafts):
        self.drafts, self.calls = list(drafts), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=self.drafts.pop(0))


def run() -> None:
    from src.graph.store import load_store
    from src.query.nl import SparqlDraft, ask, check

    store = load_store()
    near = f"""SELECT ?f ?label ?type ?wkt WHERE {{
  ?f a jgk:PublicFacility ; rdfs:label ?label ; jgk:facilityType ?type ; geo:hasDefaultGeometry/geo:asWKT ?wkt .
  FILTER(geof:sfIntersects(?wkt, {{{{NEAR:{STATION}:500}}}}))
}}"""
    # 처음엔 틀린 쿼리(없는 함수), 오류를 받고 고친 쿼리
    fake = FakeClient([SparqlDraft(sparql="SELECT ?x WHERE { BIND(geof:buffer(1) AS ?x) }", explanation="틀림"),
                       SparqlDraft(sparql=near, explanation="증미역 500m 원에 걸치는 공공시설")])
    r = ask("증미역 500m 안 공공시설은?", client=fake, store=store)
    assert len(fake.calls) == 2 and "오류" in fake.calls[1]["messages"][0]["content"]
    assert fake.calls[0]["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "증미역" in fake.calls[0]["system"][0]["text"] and "jgk:PublicFacility" in fake.calls[0]["system"][0]["text"]
    assert r["rows"] and len(r["geojson"]["features"]) == len(r["rows"])
    names = {row[1] for row in r["rows"]}
    assert "등촌119안전센터" in names and "강서소방서" in names, names

    # 안전 검사
    for bad in ["DELETE WHERE { ?s ?p ?o }", "INSERT DATA { <a> <b> <c> }", "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }"]:
        try:
            check(bad)
            raise AssertionError(f"막지 못함: {bad}")
        except ValueError:
            pass
    assert check("SELECT ?add WHERE { ?add ?p ?o }").endswith("LIMIT 500")  # 변수 이름은 문제 삼지 않는다
    assert "\"DELETE\"" in check('SELECT ?s WHERE { ?s ?p "DELETE" } LIMIT 3')
    # 작은따옴표 안에 큰따옴표를 넣어 금지어를 숨기는 우회, 주석 뒤에 숨긴 SERVICE
    for bad in ["""SELECT ?s WHERE { ?s ?p '"' } DROP ALL { '"' }""",
                "SELECT ?s WHERE { ?s ?p ?o }\n# 설명 \"\nSERVICE <http://127.0.0.1:1/> { ?a ?b ?c }"]:
        try:
            check(bad)
            raise AssertionError(f"막지 못함: {bad}")
        except ValueError:
            pass
    assert check("SELECT ?s WHERE { ?s ?p 'DELETE' } # DROP 이라고 써도 주석이다").endswith("LIMIT 500")
    assert check("SELECT ?s WHERE { ?s <http://x/#DELETE> ?o }").endswith("LIMIT 500")  # IRI 안의 #은 주석이 아니다
    # 결과 수: 너무 큰 LIMIT은 줄이고, OFFSET이 붙은 꼴은 문법을 그대로 둔다
    assert check("SELECT ?s WHERE { ?s ?p ?o } LIMIT 100000").endswith("LIMIT 500")
    assert check("SELECT ?s WHERE { ?s ?p ?o } OFFSET 5 LIMIT 100000").endswith("OFFSET 5 LIMIT 500")
    paged = check("SELECT ?s WHERE { ?s ?p ?o } ORDER BY ?s LIMIT 10 OFFSET 5")
    assert paged.endswith("LIMIT 10 OFFSET 5") and paged.count("LIMIT") == 1, paged
    from src.query.nl import run_sparql
    r2 = run_sparql(store, "SELECT ?s WHERE { ?s ?p ?o } ORDER BY ?s LIMIT 10 OFFSET 5")
    assert len(r2["rows"]) == 10 and r2["truncated"] is False
    big = run_sparql(store, "SELECT ?s ?p WHERE { ?s ?p ?o } LIMIT 100000")
    assert len(big["rows"]) == 500 and big["truncated"] is True, (len(big["rows"]), big["truncated"])
    exact = run_sparql(store, "SELECT ?s WHERE { ?s ?p ?o } LIMIT 500")
    assert len(exact["rows"]) == 500 and exact["truncated"] is False
    # 지도 결과: 모양 열 이름과 객체 uri
    assert r["geometry_vars"] == ["wkt"] and r["truncated"] is False, r["geometry_vars"]
    props = r["geojson"]["features"][0]["properties"]
    assert props["uri"] == props["f"] and props["uri"].startswith("https://yeodh10.github.io/jeungmi-geokg/id/"), props

    # 화면 API: 가짜 클라이언트로 /api/ask가 지도용 결과를 주는지
    from fastapi.testclient import TestClient

    import src.query.nl as nl
    from src.llm.client import MissingAPIKey
    from src.review import app as review_app
    nl.get_client = lambda: FakeClient([SparqlDraft(sparql=near, explanation="시험")])
    resp = TestClient(review_app.app).post("/api/ask", json={"question": "증미역 500m 안 공공시설"})
    assert resp.status_code == 200 and resp.json()["geojson"]["features"], resp.text[:200]
    body = resp.json()
    assert body["geometry_vars"] == ["wkt"] and body["truncated"] is False
    some = body["geojson"]["features"][0]["properties"]["uri"]
    assert body["names"][some]["label"], body["names"].get(some)

    # 오류: 키가 없으면 503, 모델이 거절하면 422, 모두 detail 한 문장
    def no_key():
        raise MissingAPIKey("시험")
    nl.get_client = no_key
    resp = TestClient(review_app.app).post("/api/ask", json={"question": "아무거나"})
    assert resp.status_code == 503 and "ANTHROPIC_API_KEY" in resp.json()["detail"], resp.text
    refusing = FakeClient([])
    refusing.beta.messages.parse = lambda **kw: SimpleNamespace(stop_reason="refusal", parsed_output=None)
    nl.get_client = lambda: refusing
    resp = TestClient(review_app.app).post("/api/ask", json={"question": "아무거나"})
    assert resp.status_code == 422 and resp.json()["detail"] == "질문을 처리하지 못했다 (모델이 거절)", resp.text
    assert TestClient(review_app.app).post("/api/ask", json={"question": "  "}).status_code == 400

    # 시간 제한: 오래 걸리는 쿼리는 QueryTimeout, 그 쿼리가 도는 동안 오는 쿼리는 QueryBusy, 끝나면 다시 받는다
    import time

    from src.query.nl import QueryBusy, QueryTimeout

    class Slow:
        def query(self, q):
            time.sleep(1.0)
            return True
    t0 = time.perf_counter()
    for slow_store, err in ((Slow(), QueryTimeout), (store, QueryBusy)):
        try:
            run_sparql(slow_store, "ASK { ?s ?p ?o }", seconds=0.2)
            raise AssertionError(f"{err.__name__}가 나지 않았다")
        except err:
            pass
    assert time.perf_counter() - t0 < 0.9, time.perf_counter() - t0
    time.sleep(1.0)
    assert run_sparql(store, "ASK { ?s ?p ?o }")["boolean"] is True
    real_run = nl.run_sparql

    def timeout(*a, **k):
        raise QueryTimeout("시험 시간 초과")
    nl.get_client = lambda: FakeClient([SparqlDraft(sparql=near, explanation="시험")])
    nl.run_sparql = timeout
    try:
        resp = TestClient(review_app.app).post("/api/ask", json={"question": "아무거나"})
    finally:
        nl.run_sparql = real_run
    assert resp.status_code == 504 and resp.json()["detail"] == "시험 시간 초과", resp.text
    print("자연어 질의 확인 통과:", sorted(names))


def test_nl_flow():
    run()


if __name__ == "__main__":
    run()
