"""변경 제안 검토 화면과 API.

실행: uvicorn src.review.app:app --port 8000  (그다음 브라우저에서 http://localhost:8000)
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


@app.post("/api/proposals/{pid}/decision")
def decide(pid: str, body: DecisionIn):
    if not body.reviewer.strip():
        raise HTTPException(400, "검토자 이름이 필요하다")
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
    """그래프에 지금 들어 있는 객체의 속성. 승인 전후를 비교해 볼 때 쓴다."""
    q = f"SELECT ?p ?o WHERE {{ <{uri}> ?p ?o }} ORDER BY ?p"
    rows = [{"p": term(s["p"]), "o": term(s["o"])} for s in store().query(q)]
    return {"uri": uri, "in_graph": bool(rows), "triples": rows}


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
