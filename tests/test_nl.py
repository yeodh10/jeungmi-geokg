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
    # 화면 API: 가짜 클라이언트로 /api/ask가 지도용 결과를 주는지
    from fastapi.testclient import TestClient

    import src.query.nl as nl
    from src.review import app as review_app
    nl.get_client = lambda: FakeClient([SparqlDraft(sparql=near, explanation="시험")])
    resp = TestClient(review_app.app).post("/api/ask", json={"question": "증미역 500m 안 공공시설"})
    assert resp.status_code == 200 and resp.json()["geojson"]["features"], resp.text[:200]
    print("자연어 질의 확인 통과:", sorted(names))


def test_nl_flow():
    run()


if __name__ == "__main__":
    run()
