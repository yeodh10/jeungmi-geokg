"""변경 제안 검토 화면, 지도 화면과 API.

실행: uvicorn src.review.app:app --port 8000
  검토 화면 http://localhost:8000 , 지도 화면 http://localhost:8000/map
제안을 승인하면 그래프에 바로 반영되고, 반려하면 기록만 남는다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import geopandas as gpd
import shapely
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from shapely.geometry import Point, mapping

from src.graph.store import load_store
from src.review.proposals import ProposalStore

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "web" / "review.html"
CONTEXT_RADIUS_M = 150

app = FastAPI(title="증미역 GeoKG 변경 제안 검토")
proposals = ProposalStore()
state = {"store": None}


def store():
    if state["store"] is None:
        state["store"] = load_store(proposals)
    return state["store"]


def term(v) -> str | None:
    return None if v is None else (v.value if hasattr(v, "value") else str(v))


class DecisionIn(BaseModel):
    result: Literal["approved", "rejected"]
    reviewer: str
    comment: str = ""


@app.get("/")
def page():
    return FileResponse(PAGE)


@app.get("/api/proposals")
def list_proposals(status: str | None = None):
    items = [p for p in proposals.load() if status in (None, p.status)]
    return [{"id": p.id, "status": p.status, "summary": p.summary, "action": p.action,
             "source_kind": p.source_kind, "target": p.target, "created_at": p.created_at} for p in items]


@app.get("/api/proposals/{pid}")
def get_proposal(pid: str):
    p = proposals.get(pid)
    if p is None:
        raise HTTPException(404, "없는 제안")
    return {**p.model_dump(), "sparql_update": p.sparql_update()}


@app.get("/api/proposals/{pid}/validate")
def validate_proposal(pid: str):
    """제안을 반영했을 때 대상 객체가 SHACL 규칙(src/graph/shapes.ttl)을 지키는지."""
    from src.graph.validate import check_proposal
    p = proposals.get(pid)
    if p is None:
        raise HTTPException(404, "없는 제안")
    conforms, results = check_proposal(store(), p)
    return {"conforms": conforms, "results": results}


@app.post("/api/proposals/{pid}/decision")
def decide(pid: str, body: DecisionIn):
    if not body.reviewer.strip():
        raise HTTPException(400, "검토자 이름이 필요하다")
    if body.result == "approved":
        check = validate_proposal(pid)
        if not check["conforms"]:  # 규칙을 어기는 제안은 승인할 수 없다 (반려는 된다)
            raise HTTPException(409, "데이터 규칙을 어겨 승인할 수 없다: "
                                + "; ".join(sorted({r["message"] for r in check["results"]})))
    try:
        p = proposals.decide(pid, body.result, body.reviewer.strip(), body.comment)
    except KeyError:
        raise HTTPException(404, "없는 제안")
    except ValueError as e:
        raise HTTPException(409, str(e))
    if p.status == "approved":
        state["store"] = None  # 승인된 변경을 넣어 다시 불러온다
    return p.model_dump()


@app.get("/api/feature")
def feature(uri: str):
    """그래프에 지금 들어 있는 객체의 속성과 이 객체를 가리키는 객체. 이름은 어휘의 한국어 이름표를 쓴다."""
    q = f"""PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?p ?o ?pl ?ol WHERE {{ <{uri}> ?p ?o .
  OPTIONAL {{ ?p rdfs:label ?pl FILTER(LANG(?pl) = "ko") }}
  OPTIONAL {{ ?o rdfs:label ?ol FILTER(LANG(?ol) = "ko") }} }} ORDER BY ?p"""
    rows = [{"p": term(s["p"]), "o": term(s["o"]), "p_label": term(s["pl"]), "o_label": term(s["ol"])}
            for s in store().query(q)]
    q_in = f"""PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?s ?p ?pl ?sl WHERE {{ ?s ?p <{uri}> . FILTER(?p != <http://www.opengis.net/ont/geosparql#hasGeometry>
  && ?p != <http://www.opengis.net/ont/geosparql#hasDefaultGeometry>)
  OPTIONAL {{ ?p rdfs:label ?pl FILTER(LANG(?pl) = "ko") }}
  OPTIONAL {{ ?s rdfs:label ?sl }} }} LIMIT 200"""
    inbound = [{"s": term(s["s"]), "p": term(s["p"]), "p_label": term(s["pl"]), "s_label": term(s["sl"])}
               for s in store().query(q_in)]
    return {"uri": uri, "in_graph": bool(rows), "triples": rows, "inbound": inbound}


LAYER_QUERY = {
    "building": "jgk:Building", "entrance": "jgk:BuildingEntrance",
    "facility": "jgk:PublicFacility", "station": "jgk:Station",
}
layer_cache: dict = {}


@app.get("/api/layers/{kind}")
def layer(kind: str):
    """지도에 그릴 객체를 그래프에서 꺼내 GeoJSON으로 준다 (원천 그래프 + 승인된 변경)."""
    if kind not in LAYER_QUERY:
        raise HTTPException(404, "없는 레이어")
    key = (kind, id(store()))
    if key not in layer_cache:
        q = f"""PREFIX geo: <http://www.opengis.net/ont/geosparql#>
PREFIX jgk: <https://yeodh10.github.io/jeungmi-geokg/def#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?f ?wkt ?label ?status ?use ?addr ?type WHERE {{
  ?f a {LAYER_QUERY[kind]} ; geo:hasDefaultGeometry/geo:asWKT ?wkt ; jgk:status ?s .
  ?s rdfs:label ?status .
  OPTIONAL {{ ?f rdfs:label ?label }} OPTIONAL {{ ?f jgk:useName ?use }}
  OPTIONAL {{ ?f jgk:roadAddress ?addr }} OPTIONAL {{ ?f jgk:entranceType ?type }}
}}"""
        feats = [{"type": "Feature", "geometry": mapping(shapely.from_wkt(term(s["wkt"]))),
                  "properties": {k: term(s[k]) for k in ("label", "status", "use", "addr", "type")} | {"uri": term(s["f"])}}
                 for s in store().query(q)]
        if len(layer_cache) > 8:  # 승인으로 그래프가 바뀌면 예전 것은 버린다
            layer_cache.clear()
        layer_cache[key] = {"type": "FeatureCollection", "features": feats}
    return layer_cache[key]


class AskIn(BaseModel):
    question: str


@app.post("/api/ask")
def ask_question(body: AskIn):
    """자연어 질문 -> SPARQL -> 결과와 지도용 GeoJSON. LLM 호출이 안 되면 503."""
    import anthropic

    from src.query.nl import ask
    if not body.question.strip():
        raise HTTPException(400, "질문이 비었다")
    try:
        return ask(body.question.strip(), store=store())
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, TypeError) as e:
        raise HTTPException(503, f"Claude API를 쓸 수 없다 (.env의 ANTHROPIC_API_KEY 확인): {e}")
    except anthropic.APIError as e:
        raise HTTPException(502, f"Claude API 오류: {e}")
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/map")
def map_page():
    return FileResponse(ROOT / "web" / "map.html")


@app.get("/api/proposals/{pid}/context")
def context(pid: str):
    """제안 위치 둘레의 건물(그래프에서 GeoSPARQL로 조회)과 제안 대상의 모양을 GeoJSON으로 준다."""
    p = proposals.get(pid)
    if p is None or p.location is None:
        raise HTTPException(404, "위치가 없는 제안")
    circle = gpd.GeoSeries([Point(p.location)], crs="EPSG:4326").to_crs("EPSG:5186") \
        .buffer(CONTEXT_RADIUS_M).to_crs("EPSG:4326").iloc[0]
    linked = {c.o.value for c in p.changes if c.p.endswith("#housedIn")}
    q = f"""PREFIX geo: <http://www.opengis.net/ont/geosparql#>
PREFIX geof: <http://www.opengis.net/def/function/geosparql/>
PREFIX jgk: <https://yeodh10.github.io/jeungmi-geokg/def#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?b ?wkt ?label ?status WHERE {{
  ?b a jgk:Building ; geo:hasGeometry/geo:asWKT ?wkt ; jgk:status/rdfs:label ?status .
  OPTIONAL {{ ?b rdfs:label ?label }}
  FILTER(geof:sfIntersects(?wkt, "{circle.wkt}"^^geo:wktLiteral))
}}"""
    features = [{
        "type": "Feature", "geometry": mapping(shapely.from_wkt(term(s["wkt"]))),
        "properties": {"uri": term(s["b"]), "label": term(s["label"]), "status": term(s["status"]),
                       "role": "linked" if term(s["b"]) in linked else "building"},
    } for s in store().query(q)]
    for c in p.changes:
        if c.p.endswith("#asWKT"):
            features.append({"type": "Feature", "geometry": mapping(shapely.from_wkt(c.o.value)),
                             "properties": {"uri": p.target, "label": p.summary, "role": "target"}})
    return {"type": "FeatureCollection", "features": features}
