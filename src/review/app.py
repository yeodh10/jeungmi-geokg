"""변경 제안 검토 화면, 지도 화면과 API.

실행: uvicorn src.review.app:app --port 8000
  검토 화면 http://localhost:8000 , 지도 화면 http://localhost:8000/map
제안을 승인하면 그래프에 바로 반영되고, 반려하면 기록만 남는다.
결정은 정정할 수 있다. 승인을 거두면 그래프를 원천 그래프와 남은 승인 기록으로 다시 만든다.
승인으로 그래프가 바뀔 때마다 그래프 판 번호(state["version"])가 하나 오른다. 레이어 캐시와 ETag가 이 번호를 쓴다.
판 번호는 서버를 켤 때마다 0부터 다시 세므로, ETag에는 서버를 켠 때의 값(state["boot"])도 넣는다.
서버를 켜면 그래프를 뒤에서 미리 불러 둔다 (JGK_PRELOAD=0이면 첫 요청 때 불러온다).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import unicodedata
from collections import defaultdict
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Literal

import geopandas as gpd
import numpy as np
import pyoxigraph as ox
import shapely
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from shapely.geometry import Point, mapping

from src.graph.store import load_store
from src.model.uri import BASE
from src.review.proposals import Decision, ProposalStore, group_of, iri, now

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "web" / "review.html"
CONTEXT_RADIUS_M = 150
MAX_TRIPLES = 2000   # /api/feature 가 주는 나가는 트리플 수
RDF_SUBJECT = ox.NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#subject")
RDF_OBJECT = ox.NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#object")
MAX_INBOUND = 30     # /api/feature 가 속성마다 주는 들어오는 객체 수

log = logging.getLogger(__name__)


def preload() -> None:
    t0 = time.perf_counter()
    try:
        g = store()
        system_prompt()
        # 지도 화면이 처음에 받는 레이어도 미리 만들어 둔다 (처음 화면이 비어 있는 시간을 줄인다)
        for kind in ("station", "facility", "sensor", "building", "road"):
            version = state["version"]
            body = layer_body(kind)
            if state["version"] == version:
                layer_cache[(kind, version)] = body
        area_geojson()
    except Exception:  # 미리 불러오기에 실패해도 서버는 뜨고, 첫 요청이 다시 불러 보며 오류를 낸다
        log.exception("그래프를 미리 불러오지 못했다")
        return
    log.info("그래프와 지도 레이어를 미리 불러왔다: 트리플 %d개, %.1f초", len(g), time.perf_counter() - t0)


@asynccontextmanager
async def lifespan(_app):
    if os.environ.get("JGK_PRELOAD", "1") != "0":
        threading.Thread(target=preload, name="graph-preload", daemon=True).start()
    yield


app = FastAPI(title="증미역 GeoKG 변경 제안 검토", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")


@app.middleware("http")
async def no_stale_ui(request: Request, call_next):
    """화면 파일(HTML, CSS, JS)은 매번 바뀌었는지 묻게 한다. 고친 화면이 브라우저에 남은 옛 파일에 가려지지 않게."""
    response = await call_next(request)
    if request.url.path in ("/", "/map") or request.url.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response
proposals = ProposalStore()
# boot: 서버를 켤 때마다 달라지는 값. 다시 켜면 판 번호가 0으로 돌아가도 ETag가 겹치지 않게 한다
state = {"store": None, "version": 0, "boot": format(time.time_ns(), "x")}
lock = threading.RLock()  # 그래프를 불러오고 승인을 반영하는 동안 다른 요청이 끼어들지 않게 한다

RDFS_LABEL = ox.NamedNode("http://www.w3.org/2000/01/rdf-schema#label")
DCT_DESCRIPTION = ox.NamedNode("http://purl.org/dc/terms/description")
DCT_SOURCE = ox.NamedNode("http://purl.org/dc/terms/source")
DCT_CREATED = ox.NamedNode("http://purl.org/dc/terms/created")
HAS_GEOMETRY = ox.NamedNode("http://www.opengis.net/ont/geosparql#hasGeometry")
HAS_DEFAULT_GEOMETRY = ox.NamedNode("http://www.opengis.net/ont/geosparql#hasDefaultGeometry")
AS_WKT = ox.NamedNode("http://www.opengis.net/ont/geosparql#asWKT")
WAS_INFLUENCED_BY = ox.NamedNode("http://www.w3.org/ns/prov#wasInfluencedBy")
JGK = "https://yeodh10.github.io/jeungmi-geokg/def#"
PROPOSAL_PROPS = {k: ox.NamedNode(JGK + k) for k in ("proposalAction", "sourceKind", "decidedBy", "decidedAt")}

NAME_QUERY = """PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX jgk: <https://yeodh10.github.io/jeungmi-geokg/def#>
SELECT ?u ?l ?addr ?tl ?baddr ?et ?desc WHERE {{ VALUES ?u {{ {values} }}
  OPTIONAL {{ ?u rdfs:label ?l FILTER(LANG(?l) = "ko" || LANG(?l) = "") }}
  OPTIONAL {{ ?u jgk:roadAddress ?addr }}
  OPTIONAL {{ ?u jgk:entranceType ?et . ?b jgk:hasEntrance ?u ; jgk:roadAddress ?baddr }}
  OPTIONAL {{ ?u dcterms:description ?desc }}
  OPTIONAL {{ ?u a ?t . ?t rdfs:label ?tl FILTER(STRSTARTS(STR(?t), STR(jgk:))) }} }}"""
BAD_URI_CHARS = re.compile(r'[\s<>"{}|^`\\]')


def store():
    s = state["store"]
    if s is None:
        with lock:  # 여러 요청이 한꺼번에 와도 한 번만 불러온다
            if state["store"] is None:
                state["store"] = load_store(proposals)
                layer_cache.clear()
            s = state["store"]
    return s


def term(v) -> str | None:
    return None if v is None else (v.value if hasattr(v, "value") else str(v))


def check_uri(uri) -> str:
    """SPARQL의 <...> 자리에 넣거나 그래프에서 찾을 URI인지 본다. http(s) 주소만 받는다."""
    if not isinstance(uri, str) or not re.match(r"https?://", uri) or BAD_URI_CHARS.search(uri):
        raise HTTPException(400, "올바른 URI가 아니다")
    try:
        ox.NamedNode(uri)
    except ValueError:
        raise HTTPException(400, "올바른 URI가 아니다")
    return uri


def is_uri(uri) -> bool:
    try:
        check_uri(uri)
        return True
    except HTTPException:
        return False


def names(uris) -> dict[str, dict]:
    """URI마다 사람이 읽을 이름: 이름표, 없으면 도로명주소, 제안 노드는 요약. 종류 이름도 같이 준다.

    URI 형식이 아닌 값은 조용히 건너뛴다.
    """
    uris = sorted({u for u in uris if is_uri(u)})
    out: dict[str, dict] = {}
    for i in range(0, len(uris), 200):
        values = " ".join(f"<{u}>" for u in uris[i:i + 200])
        for s in store().query(NAME_QUERY.format(values=values)):
            u = term(s["u"])
            d = out.setdefault(u, {})
            entrance = f"{term(s['baddr'])} {term(s['et'])}" if s["et"] is not None and s["baddr"] is not None else None
            if not d.get("label"):  # 출입구는 이름이 없어서 건물 주소와 출입구 유형으로 부른다
                d["label"] = term(s["l"]) or term(s["addr"]) or entrance or term(s["desc"])
            if not d.get("type"):
                d["type"] = term(s["tl"])
    return out


def rounded(geom) -> dict:
    """GeoJSON geometry. 좌표는 소수 6자리(약 10cm)로 줄여 응답을 가볍게 한다."""
    return mapping(shapely.transform(geom, lambda c: np.round(c, 6)))


class DecisionIn(BaseModel):
    result: Literal["approved", "rejected"]
    reviewer: str
    comment: str = ""

    @field_validator("reviewer")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    def problem(self) -> str | None:
        """받을 수 없는 결정이면 그 까닭(400 응답 문장)."""
        if not self.reviewer:
            return "검토자 이름이 필요하다"
        if len(self.reviewer) > 50 or any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") for ch in self.reviewer):
            return "검토자 이름은 줄바꿈 없이 50자 이하로 쓴다"
        if len(self.comment) > 1000:
            return "의견은 1000자 이하로 쓴다"
        if self.result == "rejected" and not self.comment.strip():
            return "반려 사유가 필요하다"
        return None


@app.exception_handler(RequestValidationError)
async def bad_request(request: Request, exc: RequestValidationError):
    """요청 형식 오류도 다른 오류처럼 detail 한 문장으로 준다. 원래 목록은 errors에 둔다."""
    errors = jsonable_encoder(exc.errors(), custom_encoder={Exception: str})
    first = errors[0] if errors else {}
    where = ".".join(str(x) for x in first.get("loc", []) if x not in ("body", "query", "path"))
    if first.get("type") == "missing":
        why = f"'{where}' 항목이 빠졌다" if where else "필요한 항목이 빠졌다"
    elif first.get("type") == "json_invalid" or not where:
        why = "본문을 JSON으로 읽지 못했다"
    else:
        why = f"'{where}' 값이 맞지 않는다"
    return JSONResponse(status_code=422, content={"detail": f"요청 형식이 맞지 않다: {why}", "errors": errors})


@app.exception_handler(Exception)
async def server_error(request: Request, exc: Exception):
    log.exception("처리하지 못한 오류: %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "서버 오류가 났다"})


@app.get("/")
def page():
    return FileResponse(PAGE)


@app.get("/api/proposals")
def list_proposals(status: Literal["pending", "approved", "rejected"] | None = None):
    items = [p for p in proposals.load() if status in (None, p.status)]
    named = names(p.target for p in items)

    def name(p):  # 그래프에 아직 없는 새 객체는 제안 안의 이름표를 쓴다
        return named.get(p.target, {}).get("label") or next(
            (c.o.value for c in p.changes if c.s == p.target and c.p.endswith("rdf-schema#label")), None)

    def row(p):
        d = p.decision
        group, group_label = group_of(p)
        return {"id": p.id, "status": p.status, "summary": p.summary, "action": p.action,
                "source_kind": p.source_kind, "target": p.target, "created_at": p.created_at,
                "target_name": name(p), "target_type": p.target_type,
                "decision": d.model_dump() if d else None, "decided_at": d.at if d else None,
                "reviewer": d.reviewer if d else None, "group": group, "group_label": group_label,
                "corrections": len(p.corrections)}
    return [row(p) for p in items]


@app.get("/api/proposals/{pid}")
def get_proposal(pid: str):
    p = proposals.get(pid)
    if p is None:
        raise HTTPException(404, "없는 제안")
    uris = {p.target} | {c.s for c in p.changes} | {c.p for c in p.changes} \
        | {c.o.value for c in p.changes if c.o.kind == "uri"}
    group, group_label = group_of(p)
    return {**p.model_dump(), "sparql_update": p.sparql_update(), "names": names(uris),
            "uri": p.uri, "group": group, "group_label": group_label}


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
    problem = body.problem()
    if problem:
        raise HTTPException(400, problem)
    with lock:  # 검사, 저장, 그래프 반영을 한 번에 해서 같은 제안이 두 번 결정되거나 반영이 빠지지 않게 한다
        p = proposals.get(pid)
        if p is None:
            raise HTTPException(404, "없는 제안")
        if body.result == "approved" and p.status == "pending":
            check = validate_proposal(pid)
            if not check["conforms"]:  # 규칙을 어기는 제안은 승인할 수 없다 (반려는 된다)
                raise HTTPException(409, "데이터 규칙을 어겨 승인할 수 없다: "
                                    + "; ".join(sorted({r["message"] for r in check["results"]})))
            trial = p.model_copy(update={"status": "approved", "decision": Decision(
                result="approved", reviewer=body.reviewer, at=now(), comment=body.comment)})
            try:  # 검토자 이름까지 넣은 SPARQL이 실행되는지 빈 저장소에서 먼저 본다
                ox.Store().update(trial.sparql_update())
            except (SyntaxError, OSError, RuntimeError, ValueError) as e:
                raise HTTPException(409, f"제안을 그래프에 반영하지 못했다: {e}")
        try:
            p = proposals.decide(pid, body.result, body.reviewer, body.comment)
        except KeyError:
            raise HTTPException(404, "없는 제안")
        except ValueError as e:
            raise HTTPException(409, str(e))
        if p.status == "approved":
            apply_approved([p])
    return p.model_dump()


def apply_approved(ps) -> None:
    """승인된 제안을 다시 불러오지 않고 지금 그래프에 바로 얹는다. lock 안에서 부른다."""
    g = store()
    for p in ps:
        g.update(p.sparql_update())
    state["version"] += 1
    layer_cache.clear()
    system_cache.clear()


def group_pending(group: str) -> list:
    """묶음 안에서 대기 중인 제안, 제안 파일 순서대로."""
    return [p for p in proposals.load() if p.status == "pending" and group_of(p)[0] == group]


def batch_plan(ps, decision: Decision | None = None) -> tuple[list, list[dict]]:
    """앞 제안을 반영한 그래프에서 다음 제안을 검사한다. 통과한 제안만 다음 검사의 바탕에 얹는다.

    decision을 주면 그 결정(검토자 이름 포함)을 넣은 SPARQL로 반영해 본다. 통과한 제안과 제안마다의 결과를 돌려준다.
    """
    from src.graph.validate import check_proposal
    trial = ox.Store()
    trial.bulk_extend(store().quads_for_pattern(None, None, None, None))
    ok, rows = [], []
    for p in ps:
        conforms, results = check_proposal(trial, p)
        messages = sorted({r["message"] for r in results})
        if conforms:
            q = p if decision is None else p.model_copy(update={"status": "approved", "decision": decision})
            try:
                trial.update(q.sparql_update())
                ok.append(p)
            except (SyntaxError, OSError, RuntimeError, ValueError) as e:
                conforms, messages = False, [f"제안을 그래프에 반영하지 못했다: {e}"]
        rows.append({"id": p.id, "summary": p.summary, "target": p.target, "action": p.action,
                     "conforms": conforms, "messages": messages})
    return ok, rows


class GroupDecisionIn(DecisionIn):
    group: str


@app.get("/api/groups/check")
def check_group(group: str):
    """묶음의 대기 제안을 차례로 반영해 가며 SHACL로 검사한다. 묶어 승인하기 전에 화면이 보여 준다."""
    with lock:
        ps = group_pending(group)
        if not ps:
            raise HTTPException(404, "대기 중인 제안이 없는 묶음")
        ok, rows = batch_plan(ps)
    return {"group": group, "group_label": group_of(ps[0])[1], "items": rows, "passing": len(ok)}


@app.post("/api/groups/decision")
def decide_group(body: GroupDecisionIn):
    """묶음의 대기 제안을 한 번에 결정한다. 승인은 차례 검사를 통과한 제안만 하고, 나머지는 대기로 남긴다."""
    problem = body.problem()
    if problem:
        raise HTTPException(400, problem)
    with lock:
        ps = group_pending(body.group)
        if not ps:
            raise HTTPException(404, "대기 중인 제안이 없는 묶음")
        skipped = []
        if body.result == "approved":
            decision = Decision(result="approved", reviewer=body.reviewer, at=now(), comment=body.comment)
            ps, rows = batch_plan(ps, decision)
            skipped = [r for r in rows if not r["conforms"]]
            if not ps:
                raise HTTPException(409, "데이터 규칙을 통과한 제안이 없어 승인하지 않았다")
        done = proposals.decide_many([p.id for p in ps], body.result, body.reviewer, body.comment)
        if body.result == "approved":
            apply_approved(done)
    return {"group": body.group, "result": body.result, "decided": [p.id for p in done], "skipped": skipped}


class CorrectionIn(BaseModel):
    to: Literal["pending", "approved", "rejected"]
    reviewer: str
    reason: str = ""

    @field_validator("reviewer")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    def problem(self) -> str | None:
        p = DecisionIn(result="rejected", reviewer=self.reviewer, comment=self.reason).problem()
        return "정정 사유가 필요하다" if p == "반려 사유가 필요하다" else p


def subjects(p) -> set[str]:
    return {p.target} | {c.s for c in p.changes}


def approved_after(p) -> list:
    """p보다 뒤에 승인되어 p가 바꾼 객체를 다시 바꾼 제안. 그래프를 불러올 때와 같은 순서로 본다."""
    order = sorted((q for q in proposals.load() if q.status == "approved"), key=lambda q: q.decision.at if q.decision else "")
    later = order[next(i for i, q in enumerate(order) if q.id == p.id) + 1:]
    mine = subjects(p)
    return [q for q in later if subjects(q) & mine]


@app.post("/api/proposals/{pid}/correction")
def correct(pid: str, body: CorrectionIn):
    """결정을 정정한다. 앞의 결정은 지우지 않고 정정 기록으로 남는다.

    승인을 거둘 때: 뒤에 승인된 제안이 같은 객체를 바꿨으면 그쪽을 먼저 정정해야 한다. 거두고 나면 그래프를 다시 만든다.
    반려를 승인으로 바꿀 때: 보통 승인처럼 SHACL 검사를 통과해야 하고, 지금 그래프에 바로 얹는다.
    """
    problem = body.problem()
    if problem:
        raise HTTPException(400, problem)
    with lock:
        p = proposals.get(pid)
        if p is None:
            raise HTTPException(404, "없는 제안")
        if p.status == "approved" and body.to != "approved":
            later = approved_after(p)
            if later:
                raise HTTPException(409, "뒤에 승인된 제안이 같은 객체를 바꿨다. 그 제안부터 정정해야 한다: "
                                    + ", ".join(q.id for q in later))
        if body.to == "approved" and p.status == "rejected":
            check = validate_proposal(pid)
            if not check["conforms"]:
                raise HTTPException(409, "데이터 규칙을 어겨 승인할 수 없다: "
                                    + "; ".join(sorted({r["message"] for r in check["results"]})))
        try:
            was = p.status
            p = proposals.correct(pid, body.to, body.reviewer, body.reason)
        except KeyError:
            raise HTTPException(404, "없는 제안")
        except ValueError as e:
            raise HTTPException(409, str(e))
        if was == "approved":  # 거둔 변경만 되돌리는 대신, 원천 그래프와 남은 승인 기록으로 다시 만든다
            state["store"] = load_store(proposals)
            state["version"] += 1
            layer_cache.clear()
            system_cache.clear()
        elif p.status == "approved":
            apply_approved([p])
    return p.model_dump()


@app.get("/api/status")
def status():
    """그래프를 다 불러왔는지, 지금 그래프 판 번호와 대기 제안 수."""
    g = state["store"]
    return {"graph_loaded": g is not None, "triples": len(g) if g is not None else None,
            "version": state["version"], "pending": sum(p.status == "pending" for p in proposals.load())}


@lru_cache(maxsize=1)
def held_sources() -> frozenset[str]:
    from src.graph.publish import hold_sources
    return frozenset(hold_sources())


def is_held(g, node) -> bool:
    """공개 페이지(src.graph.publish)에서 빼는 객체인지. 그 객체의 모양(/geometry)도 같이 뺀다."""
    hold = held_sources()
    nodes = [node]
    if node.value.endswith("/geometry"):
        nodes.append(ox.NamedNode(node.value.removesuffix("/geometry")))
    return any(q.object.value in hold for n in nodes for q in g.quads_for_pattern(n, DCT_SOURCE, None, None))


@app.get("/api/feature")
def feature(uri: str):
    """그래프에 지금 들어 있는 객체의 속성과 이 객체를 가리키는 객체. 이름은 어휘의 한국어 이름표를 쓴다.

    URI는 문자열로 쿼리에 넣지 않고 pyoxigraph 항으로 만들어 찾는다.
    """
    check_uri(uri)
    g = store()
    node = ox.NamedNode(uri)
    labels: dict = {}

    def label(n, ko_only=True):  # 한국어(또는 언어 없는) 이름표, ko_only가 아니면 아무 이름표나
        key = (n, ko_only)
        if key not in labels:
            found = None
            if isinstance(n, ox.NamedNode):
                ls = [q.object for q in g.quads_for_pattern(n, RDFS_LABEL, None, None)]
                found = next((l.value for l in ls if getattr(l, "language", None) in ("ko", None)), None)
                if found is None and not ko_only and ls:
                    found = ls[0].value
            labels[key] = found
        return labels[key]

    out = []
    for q in g.quads_for_pattern(node, None, None, None):
        out.append(q)
        if len(out) > MAX_TRIPLES:
            break
    truncated = len(out) > MAX_TRIPLES
    out = sorted(out[:MAX_TRIPLES], key=lambda q: (q.predicate.value, term(q.object)))
    rows = [{"p": q.predicate.value, "o": term(q.object), "p_label": label(q.predicate), "o_label": label(q.object)}
            for q in out]

    by_p = defaultdict(list)
    for q in g.quads_for_pattern(None, None, node, None):
        # 모양 노드와, 승인된 제안이 바꾼 트리플 기록(rdf:subject, rdf:object)은 연결로 보이지 않는다 (변경 이력에서 따로 본다)
        if q.predicate not in (HAS_GEOMETRY, HAS_DEFAULT_GEOMETRY, RDF_SUBJECT, RDF_OBJECT):
            by_p[q.predicate].append(q.subject)
    counts = {p.value: len(set(subs)) for p, subs in by_p.items()}
    inbound = []
    for p in sorted(by_p, key=lambda p: p.value):  # 속성마다 이름순 앞 MAX_INBOUND개
        subs = sorted(({"s": term(s), "s_label": label(s, ko_only=False)} for s in set(by_p[p])),
                      key=lambda r: (r["s_label"] or r["s"], r["s"]))
        inbound += [{"s": r["s"], "p": p.value, "p_label": label(p), "s_label": r["s_label"]} for r in subs[:MAX_INBOUND]]

    geometry = None
    for gq in g.quads_for_pattern(node, HAS_DEFAULT_GEOMETRY, None, None):
        wkt = next((w.object.value for w in g.quads_for_pattern(gq.object, AS_WKT, None, None)), None)
        if wkt:
            try:
                geometry = rounded(shapely.from_wkt(wkt))
            except shapely.errors.GEOSException:
                pass
            break

    in_graph = bool(rows)
    public = (in_graph and uri.startswith(BASE + "/") and not uri.startswith(BASE + "/proposal/")
              and not is_held(g, node))

    history = []
    for q in g.quads_for_pattern(node, WAS_INFLUENCED_BY, None, None):
        pn = q.object

        def one(pred):
            return next((term(t.object) for t in g.quads_for_pattern(pn, pred, None, None)), None)
        history.append({"proposal": term(pn), "id": term(pn).rsplit("/", 1)[-1], "summary": one(DCT_DESCRIPTION),
                        "action": one(PROPOSAL_PROPS["proposalAction"]), "source_kind": one(PROPOSAL_PROPS["sourceKind"]),
                        "decided_by": one(PROPOSAL_PROPS["decidedBy"]), "decided_at": one(PROPOSAL_PROPS["decidedAt"]),
                        "created": one(DCT_CREATED)})
    history.sort(key=lambda h: h["decided_at"] or "", reverse=True)

    named = names([uri] + [r["o"] for r in rows] + [r["s"] for r in inbound] + [h["proposal"] for h in history])
    return {"uri": uri, "in_graph": in_graph, "triples": rows, "inbound": inbound, "names": named,
            "inbound_counts": counts, "triples_truncated": truncated, "geometry": geometry,
            "public": public, "history": history}


@app.get("/api/editions/as-of")
def edition_as_of(uri: str, at: str):
    """객체 하나의 시각 at 값과 지금 값의 차이 (시점별 판). 승인된 변경 기록을 거꾸로 되돌려 구한다."""
    from src.graph.editions import as_of, diff_states, moment
    try:
        iri(uri)
        when = moment(at)
    except ValueError as e:
        raise HTTPException(400, f"URI나 시각이 올바르지 않다: {e}") from None
    g = store()
    rows = diff_states(as_of(g, uri, when), as_of(g, uri, "2999-12-31T00:00:00+09:00"))
    shown = [u for r in rows for u in [r["p"], *r["before"], *r["after"]] if u.startswith("http")]
    return {"uri": uri, "at": when, "diff": rows, "names": names(shown)}


@app.get("/api/editions/changes")
def edition_changes(since: str = "1900-01-01", until: str = "2999-12-31"):
    """두 시각 사이에 승인된 변경과 그 변경이 넣고 뺀 트리플."""
    from src.graph.editions import changes_between, moment
    try:
        since, until = moment(since), moment(until)
    except ValueError as e:
        raise HTTPException(400, f"시각이 올바르지 않다: {e}") from None
    rows = changes_between(store(), since, until)
    shown = [r[k] for r in rows for k in ("target", "s", "p", "o") if r[k].startswith("http")]
    return {"since": since, "until": until, "changes": rows, "names": names(shown)}


LAYER_QUERY = {
    "building": "jgk:Building", "entrance": "jgk:BuildingEntrance",
    "facility": "jgk:PublicFacility", "station": "jgk:Station", "sensor": "jgk:Sensor",
    "road": "jgk:RoadSegment", "legaldong": "jgk:LegalDong", "basemap": "jgk:BaseMapFeature",
    "digitalmap": "jgk:DigitalMapFeature",
}
LAYER_FIELDS = ("label", "status", "use", "addr", "type", "dong", "baddr", "bkind")
layer_cache: dict = {}  # (레이어, 그래프 판 번호) -> 응답 본문(bytes)


def layer_body(kind: str) -> bytes:
    # 한 객체가 행정동, 출입구를 가진 건물 등으로 여러 줄이 되지 않게 객체마다 묶는다
    entrance = "OPTIONAL { ?b jgk:hasEntrance ?f ; jgk:roadAddress ?baddr0 }" if kind == "entrance" else ""
    q = f"""PREFIX geo: <http://www.opengis.net/ont/geosparql#>
PREFIX jgk: <https://yeodh10.github.io/jeungmi-geokg/def#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?f (SAMPLE(?wkt0) AS ?wkt) {" ".join(f"(SAMPLE(?{k}0) AS ?{k})" for k in LAYER_FIELDS)} WHERE {{
  ?f a {LAYER_QUERY[kind]} ; geo:hasDefaultGeometry/geo:asWKT ?wkt0 ; jgk:status ?s .
  ?s rdfs:label ?status0 .
  OPTIONAL {{ ?f rdfs:label ?label0 }} OPTIONAL {{ ?f jgk:useName ?use0 }}
  OPTIONAL {{ ?f jgk:roadAddress ?addr0 }} OPTIONAL {{ ?f jgk:entranceType ?type0 }} OPTIONAL {{ ?f jgk:mapKind ?bkind0 }}
  OPTIONAL {{ ?f jgk:within ?d . ?d a jgk:AdminDong ; rdfs:label ?dong0 }}
  {entrance}
}} GROUP BY ?f"""
    feats = []
    for s in store().query(q):
        props = {k: term(s[k]) for k in LAYER_FIELDS}
        baddr = props.pop("baddr")
        props["type"] = props["type"] or props.pop("bkind")  # 수치지형도 건물은 종류(아파트, 무벽건물 등)
        props.pop("bkind", None)
        props["addr"] = props["addr"] or baddr  # 출입구는 그 출입구를 가진 건물의 도로명주소
        feats.append({"type": "Feature", "geometry": rounded(shapely.from_wkt(term(s["wkt"]))),
                      "properties": {k: props[k] for k in ("label", "status", "use", "addr", "type")}
                      | {"uri": term(s["f"]), "kind": kind, "dong": props["dong"]}})
    return json.dumps({"type": "FeatureCollection", "features": feats},
                      ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@app.get("/api/layers/{kind}")
def layer(kind: str, request: Request):
    """지도에 그릴 객체를 그래프에서 꺼내 GeoJSON으로 준다 (원천 그래프 + 승인된 변경).

    서버를 켠 때의 값과 그래프 판 번호로 ETag를 붙여, 그래프가 그대로면 브라우저가 가진 것을 다시 쓰게(304) 한다.
    """
    if kind not in LAYER_QUERY:
        raise HTTPException(404, "없는 레이어")
    store()
    version = state["version"]
    etag = f'W/"{kind}-{state["boot"]}-{version}"'
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    sent = [t.strip() for t in request.headers.get("if-none-match", "").split(",")]
    if etag in sent or "*" in sent:
        return Response(status_code=304, headers=headers)
    key = (kind, version)
    body = layer_cache.get(key)
    if body is None:
        body = layer_body(kind)
        if state["version"] == version:  # 만드는 사이에 승인이 들어왔으면 담아 두지 않는다
            layer_cache[key] = body
    return Response(body, media_type="application/json", headers=headers)


@lru_cache(maxsize=1)
def area_geojson() -> dict:
    """config/area.yaml의 대상 범위(중심에서 반경 radius_m 원). 범위 자르기처럼 EPSG:5186에서 원을 그린다."""
    with open(ROOT / "config" / "area.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    lon, lat = cfg["center"]["epsg4326_lonlat"]
    r = cfg["radius_m"]
    metric = (cfg.get("crs") or {}).get("metric", "EPSG:5186")
    circle = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(metric) \
        .buffer(r, quad_segs=64).to_crs("EPSG:4326").iloc[0]
    name = cfg["center"]["name"]
    size = f"{r // 1000}km" if r % 1000 == 0 else f"{r}m"
    props = {"label": f"{name.split(' (')[0]} 반경 {size}", "name": name, "radius_m": r}
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": rounded(circle), "properties": props}],
            "center": [lon, lat], "radius_m": r, "bbox": [round(v, 6) for v in circle.bounds]}


@app.get("/api/area")
def area():
    return area_geojson()


class AskIn(BaseModel):
    question: str


system_cache: dict = {}  # 그래프 판 번호 -> 자연어 질의 프롬프트


def system_prompt() -> str:
    from src.query.nl import build_system
    version = state["version"]
    if version not in system_cache:
        text = build_system(store())
        system_cache.clear()
        system_cache[version] = text
    return system_cache[version]


@app.post("/api/ask")
def ask_question(body: AskIn):
    """자연어 질문 -> SPARQL -> 결과와 지도용 GeoJSON. LLM 키가 없으면 503."""
    import anthropic

    from src.llm.client import LLMRefused, MissingAPIKey
    from src.query.nl import QueryBusy, QueryTimeout, ask
    if not body.question.strip():
        raise HTTPException(400, "질문이 비었다")
    try:
        r = ask(body.question.strip(), store=store(), system=system_prompt())
    except MissingAPIKey:
        raise HTTPException(503, "Claude API를 쓸 수 없다 (.env의 ANTHROPIC_API_KEY 확인)")
    except LLMRefused:
        raise HTTPException(422, "질문을 처리하지 못했다 (모델이 거절)")
    except QueryTimeout as e:
        raise HTTPException(504, str(e))
    except QueryBusy:
        raise HTTPException(503, "앞서 오래 걸린 쿼리가 아직 돌고 있다. 잠시 뒤에 다시 물어 주세요")
    except anthropic.APIError as e:
        raise HTTPException(502, f"Claude API 오류: {e}")
    except ValueError as e:
        raise HTTPException(422, str(e))
    r["names"] = names(v for row in r["rows"] for v in row if isinstance(v, str) and v.startswith(BASE))
    return r


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
    changed = {c.s for c in p.changes} - {p.target}  # 대상 말고 함께 바뀌는 객체 (예: 폐지되는 출입구)
    role = lambda u: "target" if u == p.target else "changed" if u in changed else "linked" if u in linked else "building"
    features = [{
        "type": "Feature", "geometry": mapping(shapely.from_wkt(term(s["wkt"]))),
        "properties": {"uri": term(s["b"]), "label": term(s["label"]), "status": term(s["status"]), "role": role(term(s["b"]))},
    } for s in store().query(q)]
    drawn = {f["properties"]["uri"] for f in features}
    # 대상과 함께 바뀌는 객체의 모양: 제안에 새 모양이 있으면 그것을, 없으면 지금 그래프의 모양을 쓴다
    new_wkt = {c.s.removesuffix("/geometry"): c.o.value for c in p.changes if c.p.endswith("#asWKT")}
    for u in [p.target, *sorted(changed)]:
        if u in drawn or not is_uri(u):  # 형식이 맞지 않는 URI는 쿼리에 넣지 않는다
            continue
        wkt = new_wkt.get(u) or next((term(s["w"]) for s in store().query(
            f"SELECT ?w WHERE {{ <{u}> <http://www.opengis.net/ont/geosparql#hasDefaultGeometry>/"
            f"<http://www.opengis.net/ont/geosparql#asWKT> ?w }}")), None)
        if wkt:
            features.append({"type": "Feature", "geometry": mapping(shapely.from_wkt(wkt)),
                             "properties": {"uri": u, "label": p.summary if u == p.target else None, "role": role(u)}})
    return {"type": "FeatureCollection", "features": features}
