"""변경 제안 검토 화면, 지도 화면과 API.

실행: uvicorn src.review.app:app --host 127.0.0.1 --port 8000
  검토 화면 http://localhost:8000 , 지도 화면 http://localhost:8000/map
이 서버는 공개 보류 자료도 그대로 읽는 로컬 화면이다. 127.0.0.1 밖으로 열지 않는다. Host와 Origin 검사도 이 전제로 짰다.
제안을 승인하면 그래프에 바로 반영되고, 반려하면 기록만 남는다.
결정은 정정할 수 있다. 승인을 거두면 그래프를 원천 그래프와 남은 승인 기록으로 다시 만든다.
승인과 정정은 새 그래프를 먼저 만들고, 파일에 저장한 뒤, 새 그래프로 바꿔 끼운다. 어느 단계가 실패해도 파일과 그래프가 어긋나지 않는다.
한 번 내건 그래프는 고치지 않는다. 그래서 읽는 요청과 검사는 잠금 없이 그래프를 본다.
승인으로 그래프가 바뀔 때마다 그래프 판 번호(state["version"])가 하나 오른다. 레이어 캐시와 ETag가 이 번호를 쓴다.
판 번호는 서버를 켤 때마다 0부터 다시 세므로, ETag에는 서버를 켠 때의 값(state["boot"])도 넣는다.
서버를 켜면 그래프를 뒤에서 미리 불러 둔다 (JGK_PRELOAD=0이면 첫 요청 때 불러온다).
"""
from __future__ import annotations

import base64
import hashlib
import itertools
import json
import logging
import math
import os
import re
import threading
import time
import unicodedata
from collections import defaultdict, deque
from contextlib import asynccontextmanager, contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import geopandas as gpd
import numpy as np
import pyoxigraph as ox
import shapely
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from shapely.geometry import Point, mapping
from starlette.datastructures import Headers
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.graph.store import ReplayReport, build_store, last_report, load_store, remember
from src.model.uri import BASE
from src.review.proposals import Change, ProposalStore, Term, group_of, iri, text

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "web" / "review.html"
NOTICE_REPORTS = ROOT / "data" / "processed" / "review" / "notices"  # 고시 추출 보고서 (src/llm/notice.py)
CONTEXT_RADIUS_M = 150
MAX_TRIPLES = 2000   # /api/feature 가 주는 나가는 트리플 수
RDF_SUBJECT = ox.NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#subject")
RDF_OBJECT = ox.NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#object")
RDF_TYPE = ox.NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
MAX_INBOUND = 30     # /api/feature 가 속성마다 주는 들어오는 객체 수
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
MAX_BODY = 64 * 1024  # 요청 본문 바이트. 의견 1000자나 질문 2000자가 이스케이프로 여섯 배가 되어도 들어간다
BATCH_SECONDS = 60  # 묶음 검사 한 번의 시간 상한. 넘긴 제안은 검사하지 않고 대기로 남긴다
SLOT_WAIT = 5  # 검사 자리가 나기를 기다리는 초. 넘으면 429로 답한다
ASK_PER_MINUTE = 10  # 질문 한 건이 유료 호출 최대 2건이고, 호출마다 SDK가 2번 더 시도한다
ASK_PER_DAY = 200
TOO_MANY = "요청이 밀려 있다. 잠시 뒤에 다시 시도해 주세요"
LOCK_WAIT = 30  # 결정과 정정이 앞 결정이 끝나기를 기다리는 초. 넘으면 503
FILE_WAIT = 5  # 다른 프로세스가 쥔 제안 파일 잠금을 앱 잠금 밖에서 기다리는 초. 넘으면 503
FILE_BUSY = "다른 작업이 제안 파일을 쓰고 있다. 잠시 뒤에 다시 시도해 주세요"
FILE_BROKEN = "제안 파일을 읽지 못했다. 파일은 그대로 두었다. 파일을 고친 뒤 다시 시도해 주세요"
# 화면이 쓰는 바깥 주소. web/review.html, web/map.html의 주소와 같아야 한다
MAP_LIBRARY = "https://unpkg.com/maplibre-gl@4.7.1/"
FONT_FILES = "https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/"
MAP_TILES = "https://tile.openstreetmap.org"
LOCKED_CSP = "default-src 'none'; frame-ancestors 'none'"  # 화면이 아닌 응답. /static/의 HTML도 여기서 스크립트가 돌지 않는다
INLINE_SCRIPT = re.compile(r"<script>(.*?)</script>", re.S)
# 글자로 보이지 않는 문자: 유니코드 Default_Ignorable_Code_Point와 점자 빈칸. 이름이나 사유가 비었는데 찬 것처럼 보이게 한다
INVISIBLE = re.compile("[­͏؜ᅟᅠ឴឵᠋-᠏​-‏‪-‮⁠-⁯⠀ㅤ"
                       "︀-️﻿ﾠ￰-￸\U0001bca0-\U0001bca3\U0001d173-\U0001d17a\U000e0000-\U000e0fff]")
API_KEY = re.compile(r"sk-ant-[\w-]+")  # 로그에 남기지 않을 글. 잘못된 키는 SDK 오류 문장에 그대로 들어간다

log = logging.getLogger(__name__)


def preload() -> None:
    t0 = time.perf_counter()
    try:
        g = store()
        system_prompt()
        from src.query.nl import prepare
        prepare(g)  # 쿼리 일꾼을 미리 띄운다. 기다리지 않는다
        # 지도 화면이 처음에 받는 레이어도 미리 만들어 둔다 (처음 화면이 비어 있는 시간을 줄인다)
        for kind in ("station", "facility", "sensor", "building", "road"):
            layer_bytes(kind, state["version"])
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


def same_origin(origin: str, scope: dict, host: str) -> bool:
    try:
        u = urlsplit(origin)
    except ValueError:
        return False
    return u.scheme == scope["scheme"] and u.netloc.lower() == host.lower()


def refusal(scope: dict, headers: Headers) -> tuple[int, str] | None:
    """입구에서 돌려보낼 요청이면 상태 코드와 문장. Host가 맞아도 다른 사이트의 페이지가 브라우저를 시켜 보낸 요청은 막는다."""
    if scope["path"].startswith("/api/") and headers.get("sec-fetch-site") in ("cross-site", "same-site"):
        return 403, "다른 사이트에서 온 요청은 받지 않는다"
    if scope["method"] not in ("GET", "HEAD", "OPTIONS"):
        origin = headers.get("origin")
        if origin is not None and not same_origin(origin, scope, headers.get("host", "")):
            return 403, "다른 출처에서 온 요청은 받지 않는다"
    length = headers.get("content-length", "0")
    if not length.isdecimal() or int(length) > MAX_BODY:
        return 413, f"요청 본문은 {MAX_BODY // 1024}KB까지 받는다"
    return None


class RequestGate:
    """Origin과 본문 크기를 본다. Content-Length 없이 보낸 본문은 읽으면서 센다."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        refused = refusal(scope, Headers(scope=scope))
        if refused:
            await JSONResponse(status_code=refused[0], content={"detail": refused[1]})(scope, receive, send)
            return
        seen = 0

        async def counted():
            nonlocal seen
            message = await receive()
            seen += len(message.get("body", b""))
            if seen > MAX_BODY:
                raise HTTPException(413, f"요청 본문은 {MAX_BODY // 1024}KB까지 받는다")
            return message
        await self.app(scope, counted, send)


app = FastAPI(title="증미역 GeoKG 변경 제안 검토", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(RequestGate)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS, www_redirect=False)  # DNS rebinding 막기
app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")


@app.middleware("http")
async def response_headers(request: Request, call_next):
    """모든 응답에 방어 헤더를 붙인다. 입구에서 돌려보낸 응답에도 붙도록 맨 바깥에 둔다.

    화면 파일(HTML, CSS, JS)은 매번 바뀌었는지 묻게 한다. 고친 화면이 브라우저에 남은 옛 파일에 가려지지 않게.
    """
    response = await call_next(request)
    if request.url.path in ("/", "/map") or request.url.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "no-cache")
    response.headers.setdefault("Content-Security-Policy", LOCKED_CSP)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


class ProposalFileError(Exception):
    """제안 파일 전체를 읽지 못했다. 기록 하나가 아니라 파일이 깨졌다(JSON이 아니거나 맨 바깥이 목록이 아니다)."""


class ServerProposals(ProposalStore):
    """서버가 쓰는 제안 저장소. 파일 전체를 읽지 못하면 ProposalFileError를 내고, 서버는 503으로 답한다.

    읽기 단계에서 멈추므로 결정과 정정도 저장까지 가지 않아 깨진 파일을 덮어쓰지 않는다.
    맨 바깥이 목록이 아닌 JSON({} 등)도 막는다. 그대로 두면 승인을 하나도 얹지 않은 그래프가 아무 경고 없이 뜬다.
    """

    def _read(self):
        try:
            good, broken = super()._read()
            if not good and self.path.exists() and not isinstance(json.loads(self.path.read_text(encoding="utf-8")), list):
                raise TypeError("맨 바깥이 목록이 아니다")
        except (ValueError, TypeError) as e:  # JSONDecodeError와 UnicodeDecodeError도 ValueError다. null, 숫자는 TypeError
            log.error("제안 파일을 읽지 못했다: %s: %s", self.path, e)
            raise ProposalFileError(str(e)) from e
        return good, broken


proposals = ServerProposals()
# boot: 서버를 켤 때마다 달라지는 값. 다시 켜면 판 번호가 0으로 돌아가도 ETag가 겹치지 않게 한다
state = {"store": None, "version": 0, "boot": format(time.time_ns(), "x")}
lock = threading.RLock()  # 그래프를 불러오고 승인을 반영하는 동안 다른 요청이 끼어들지 않게 한다
validate_slots = threading.BoundedSemaphore(4)  # 단건 검사가 동시에 도는 수
batch_slots = threading.BoundedSemaphore(1)  # 묶음 검사는 그래프를 복사해 오래 돌므로 하나씩
ask_times: deque[float] = deque()  # 최근 하루의 질문 시각 (time.monotonic)
ask_lock = threading.Lock()

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
CHANGE_OF = ox.NamedNode(JGK + "changeOf")

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


def geometry_of(wkt: str | None) -> dict | None:
    """WKT를 2차원 GeoJSON geometry로. 지도에 그릴 수 없는 값이면 None이고, 부르는 쪽이 그 객체만 건너뛴다.

    None이 되는 값은 flat_shape(src/graph/validate.py)를 따르고, 지구 밖 좌표도 더한다. 유한한 높이(Z)와 M 값은 버린다.
    모양 하나가 레이어 전체를 내리지 않게, 표준 JSON으로 적을 수 있는지도 객체마다 여기서 본다.
    """
    from src.graph.validate import flat_shape
    geom = flat_shape(wkt)
    if geom is None:
        return None
    xy = shapely.get_coordinates(geom)
    if (np.abs(xy[:, 0]) > 180).any() or (np.abs(xy[:, 1]) > 90).any():
        return None
    out = rounded(geom)
    try:
        json.dumps(out, allow_nan=False)
    except (TypeError, ValueError):
        return None
    return out


def place(p) -> tuple[float, float] | None:
    """제안의 위치. 좌표가 숫자가 아니거나 한국 범위 밖이면 없는 것으로 본다."""
    from src.graph.validate import KOREA_LAT, KOREA_LON
    if p.location is None:
        return None
    lon, lat = p.location
    return p.location if KOREA_LON[0] <= lon <= KOREA_LON[1] and KOREA_LAT[0] <= lat <= KOREA_LAT[1] else None


def shown(p) -> dict:
    """화면에 줄 제안. NaN 위치가 남으면 표준 JSON으로 적을 수 없어 저장한 뒤에 500이 난다."""
    return {**p.model_dump(), "location": place(p)}


def visible(s: str) -> str:
    return INVISIBLE.sub("", s).strip()


class DecisionIn(BaseModel):
    result: Literal["approved", "rejected"]
    reviewer: str
    comment: str = ""

    @field_validator("reviewer")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("reviewer", "comment")
    @classmethod
    def _text(cls, v: str) -> str:
        return text(v)

    def problem(self) -> str | None:
        """받을 수 없는 결정이면 그 까닭(400 응답 문장)."""
        if not self.reviewer:
            return "검토자 이름이 필요하다"
        if len(self.reviewer) > 50 or any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") for ch in self.reviewer):
            return "검토자 이름은 줄바꿈 없이 50자 이하로 쓴다"
        if INVISIBLE.search(self.reviewer):
            return "검토자 이름에 보이지 않는 글자를 쓸 수 없다"
        if len(self.comment) > 1000:
            return "의견은 1000자 이하로 쓴다"
        if self.result == "rejected" and not visible(self.comment):
            return "반려 사유가 필요하다"
        return None


@app.exception_handler(RequestValidationError)
async def bad_request(request: Request, exc: RequestValidationError):
    """요청 형식 오류도 다른 오류처럼 detail 한 문장으로 준다. 원래 목록은 errors에 둔다.

    보낸 값(input)은 되돌려 주지 않는다. NaN이나 외톨이 대리문자는 응답을 표준 JSON으로 적을 수 없다.
    """
    errors = jsonable_encoder([{k: e.get(k) for k in ("type", "loc", "msg")} for e in exc.errors()])
    first = errors[0] if errors else {}
    where = ".".join(str(x) for x in first.get("loc", []) if x not in ("body", "query", "path"))
    if first.get("type") == "missing":
        why = f"'{where}' 항목이 빠졌다" if where else "필요한 항목이 빠졌다"
    elif first.get("type") == "json_invalid" or not where:
        why = "본문을 JSON으로 읽지 못했다"
    else:
        why = f"'{where}' 값이 맞지 않는다"
    return JSONResponse(status_code=422, content={"detail": f"요청 형식이 맞지 않다: {why}", "errors": errors})


@app.exception_handler(ProposalFileError)
async def proposal_file_error(request: Request, exc: ProposalFileError):
    return JSONResponse(status_code=503, content={"detail": FILE_BROKEN})


@app.exception_handler(Exception)
async def server_error(request: Request, exc: Exception):
    log.exception("처리하지 못한 오류: %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "서버 오류가 났다"})


def page_csp(html: str) -> str:
    """이 화면의 인라인 스크립트만 돌게 하는 CSP. 글이 한 글자라도 바뀌면 해시가 달라지므로 응답마다 잰다.

    style-src의 'unsafe-inline'은 화면의 style 속성 때문이다. worker-src blob:은 MapLibre가 쓴다.
    """
    hashes = " ".join("'sha256-" + base64.b64encode(hashlib.sha256(s.encode("utf-8")).digest()).decode() + "'"
                      for s in INLINE_SCRIPT.findall(html))
    return "; ".join([
        "default-src 'none'", f"script-src 'self' {hashes} {MAP_LIBRARY}",
        f"style-src 'self' 'unsafe-inline' {MAP_LIBRARY} {FONT_FILES}", f"font-src {FONT_FILES}",
        f"img-src 'self' data: blob: {MAP_TILES}", f"connect-src 'self' {MAP_TILES}", "worker-src blob:", "child-src blob:",
        "base-uri 'none'", "form-action 'self'", "frame-ancestors 'none'", "object-src 'none'"])


def page_file(path: Path) -> HTMLResponse:
    html = path.read_text(encoding="utf-8")  # 브라우저처럼 줄바꿈을 LF로 읽어야 해시가 맞는다
    return HTMLResponse(html, headers={"Content-Security-Policy": page_csp(html)})


@app.get("/")
def page():
    return page_file(PAGE)


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
                "corrections": len(p.corrections), "flags": p.flags}
    return [row(p) for p in items]


@app.get("/api/proposals/{pid}")
def get_proposal(pid: str):
    p = proposals.get(pid)
    if p is None:
        raise HTTPException(404, "없는 제안")
    uris = {p.target} | {c.s for c in p.changes} | {c.p for c in p.changes} \
        | {c.o.value for c in p.changes if c.o.kind == "uri"}
    group, group_label = group_of(p)
    return {**shown(p), "sparql_update": p.sparql_update(), "names": names(uris),
            "uri": p.uri, "group": group, "group_label": group_label}


@contextmanager
def slot(slots: threading.BoundedSemaphore, wait: float):
    """검사 자리를 잡는다. 자리가 나지 않으면 쌓이지 않고 429로 돌려보낸다."""
    if not slots.acquire(timeout=wait):
        raise HTTPException(429, TOO_MANY)
    try:
        yield
    finally:
        slots.release()


def wait_for_file() -> None:
    """다른 프로세스가 쥔 제안 파일 잠금이 풀리기를 앱 잠금 밖에서 FILE_WAIT초까지 기다린다. 넘으면 503.

    저장 쪽(src/review/proposals.py)과 같은 방식으로 잠금을 잡아 보고 바로 놓는다. 그 사이 다른 프로세스가 먼저 잡으면
    저장 쪽이 LOCK_TIMEOUT까지 기다리다 TimeoutError를 내고, writing()이 503으로 바꾼다.
    """
    from src.review.proposals import _try_lock, _unlock
    path = getattr(proposals, "path", None)
    if path is None or not path.parent.exists():
        return
    deadline = time.monotonic() + FILE_WAIT
    with open(path.with_name(path.name + ".lock"), "a+b", buffering=0) as f:
        while True:
            try:
                _try_lock(f)
            except OSError:
                if time.monotonic() > deadline:
                    raise HTTPException(503, FILE_BUSY) from None
                time.sleep(0.05)
            else:
                _unlock(f)
                return


@contextmanager
def writing():
    """결정과 정정의 차례를 잡는다. 기다림마다 상한을 두고, 넘으면 500이 아니라 503으로 돌려보낸다.

    다른 프로세스(고시 추출, 자동 갱신 등)가 제안 파일 잠금을 쥐고 있으면 앱 잠금 밖에서 기다린다.
    앱 잠금을 쥔 채 저장 쪽 상한(30초)까지 기다리면, 그동안 다른 결정이 모두 그 뒤에 줄을 선다.
    """
    wait_for_file()
    if not lock.acquire(timeout=LOCK_WAIT):
        raise HTTPException(503, TOO_MANY)
    try:
        yield
    except TimeoutError:  # 확인한 뒤 다른 프로세스가 먼저 잠금을 잡아 저장 쪽 상한을 넘겼다
        log.warning("제안 파일 잠금을 잡지 못해 저장하지 않았다")
        raise HTTPException(503, FILE_BUSY) from None
    finally:
        lock.release()


def check_now(p) -> dict:
    from src.graph.validate import check_proposal
    conforms, results = check_proposal(store(), p)
    return {"conforms": conforms, "results": results}


def require_rules(p) -> None:
    """규칙을 어기는 제안은 승인할 수 없다 (반려는 된다)."""
    check = check_now(p)
    if not check["conforms"]:
        raise HTTPException(409, "데이터 규칙을 어겨 승인할 수 없다: " + "; ".join(sorted({r["message"] for r in check["results"]})))


@app.get("/api/proposals/{pid}/validate")
def validate_proposal(pid: str):
    """제안을 반영했을 때 대상 객체가 SHACL 규칙(src/graph/shapes.ttl)을 지키는지."""
    p = proposals.get(pid)
    if p is None:
        raise HTTPException(404, "없는 제안")
    with slot(validate_slots, SLOT_WAIT):
        return check_now(p)


def copy_graph(g: ox.Store) -> ox.Store:
    new = ox.Store()
    new.bulk_extend(g.quads_for_pattern(None, None, None, None))
    return new


def graph_with(ps) -> ox.Store:
    """지금 그래프를 복사해 승인된 제안을 얹는다. 파일에 저장하기 전에 불러, 반영하지 못하면 저장하지 않게 한다."""
    new = copy_graph(store())
    try:
        for p in ps:
            new.update(p.sparql_update())
    except (SyntaxError, OSError, RuntimeError, ValueError) as e:
        log.warning("제안을 그래프에 반영하지 못했다: %s", e)
        raise HTTPException(409, f"제안을 그래프에 반영하지 못했다: {e}")
    return new


def adopt(g: ox.Store, report: ReplayReport | None = None) -> None:
    """저장을 마친 새 그래프로 바꿔 끼운다. lock 안에서 부른다. 판 번호를 뒤에 올려, 새 번호로 옛 그래프를 담는 일이 없게 한다.

    report는 새로 다시 만든 그래프의 재생 결과다. 바꿔 끼울 때에만 /api/status의 충돌 목록으로 내건다.
    """
    state["store"] = g
    if report is not None:
        remember(report)
    state["version"] += 1
    layer_cache.clear()
    system_cache.clear()


@app.post("/api/proposals/{pid}/decision")
def decide(pid: str, body: DecisionIn):
    problem = body.problem()
    if problem:
        raise HTTPException(400, problem)
    with writing():  # 검사, 그래프 반영, 저장을 한 번에 해서 같은 제안이 두 번 결정되거나 반영이 빠지지 않게 한다
        p = proposals.get(pid)
        if p is None:
            raise HTTPException(404, "없는 제안")
        if body.result == "approved" and p.status == "pending":
            require_rules(p)
        made = []

        def reflect(changed, _all):  # 일련번호까지 정해진 결정으로 저장 직전에 새 그래프를 만든다
            if body.result == "approved":
                made.append(graph_with(changed))
        try:
            p = proposals.decide(pid, body.result, body.reviewer, body.comment, before_save=reflect)
        except KeyError:
            raise HTTPException(404, "없는 제안")
        except ValueError as e:
            raise HTTPException(409, str(e))
        if made:
            adopt(made[0])
    return shown(p)


def group_pending(group: str) -> list:
    """묶음 안에서 대기 중인 제안, 제안 파일 순서대로."""
    return [p for p in proposals.load() if p.status == "pending" and group_of(p)[0] == group]


def hold_reason(p) -> str | None:
    """묶음 승인에서 뺄 까닭. 모델이 읽어 낸 제안과 경고가 붙은 제안은 사람이 하나씩 본다."""
    if p.source_kind == "llm-notice":
        return "LLM이 고시에서 뽑은 제안이라 하나씩 승인한다"
    if p.flags:
        return "경고가 있어 하나씩 승인한다"
    return None


def batch_plan(ps) -> tuple[list, list[dict], bool]:
    """앞 제안을 반영한 그래프에서 다음 제안을 검사한다. 통과한 제안만 다음 검사의 바탕에 얹는다.

    오래 걸려서 잠금 밖에서 부른다. 내건 그래프를 복사해 쓰므로 다른 승인과 부딪히지 않는다.
    통과한 제안, 제안마다의 결과, 시간 상한을 넘겼는지를 돌려준다. held가 있는 제안과 시간을 넘긴 제안은 conforms가 None이다.
    """
    from src.graph.validate import check_proposal
    trial = copy_graph(store())
    deadline = time.monotonic() + BATCH_SECONDS
    ok, rows, late = [], [], False
    for p in ps:
        row = {"id": p.id, "summary": p.summary, "target": p.target, "action": p.action, "source_kind": p.source_kind,
               "evidence": p.evidence, "flags": p.flags, "held": hold_reason(p), "conforms": None, "messages": []}
        rows.append(row)
        if row["held"]:
            continue
        if time.monotonic() >= deadline:
            late = True
            row["messages"] = [f"시간 상한({BATCH_SECONDS}초)을 넘어 검사하지 못했다"]
            continue
        row["conforms"], results = check_proposal(trial, p)
        row["messages"] = sorted({r["message"] for r in results})
        if row["conforms"]:
            try:
                trial.update(p.sparql_update())
                ok.append(p)
            except (SyntaxError, OSError, RuntimeError, ValueError) as e:
                row["conforms"], row["messages"] = False, [f"제안을 그래프에 반영하지 못했다: {e}"]
    return ok, rows, late


class GroupDecisionIn(DecisionIn):
    group: str

    @field_validator("group")
    @classmethod
    def _group(cls, v: str) -> str:
        return text(v)


@app.get("/api/groups/check")
def check_group(group: str):
    """묶음의 대기 제안을 차례로 반영해 가며 SHACL로 검사한다. 묶어 승인하기 전에 화면이 보여 준다."""
    ps = group_pending(group)
    if not ps:
        raise HTTPException(404, "대기 중인 제안이 없는 묶음")
    with slot(batch_slots, 1):
        ok, rows, late = batch_plan(ps)
    return {"group": group, "group_label": group_of(ps[0])[1], "items": rows, "passing": len(ok), "timed_out": late}


@app.post("/api/groups/decision")
def decide_group(body: GroupDecisionIn):
    """묶음의 대기 제안을 한 번에 결정한다. 승인은 차례 검사를 통과한 제안만 하고, 나머지는 대기로 남긴다.

    LLM 제안과 경고가 붙은 제안은 묶어 승인하지 않는다. 뺀 제안은 held로, 규칙을 어긴 제안은 skipped로 알린다.
    검사는 잠금 밖에서 하므로, 그사이 그래프가 바뀌었으면 검사가 낡은 것이라 승인하지 않는다.
    """
    problem = body.problem()
    if problem:
        raise HTTPException(400, problem)
    ps = group_pending(body.group)
    if not ps:
        raise HTTPException(404, "대기 중인 제안이 없는 묶음")
    skipped, held, version = [], [], None
    if body.result == "approved":
        store()
        version = state["version"]
        with slot(batch_slots, 1):
            ps, rows, _ = batch_plan(ps)
        held = [r for r in rows if r["held"]]
        skipped = [r for r in rows if not r["held"] and not r["conforms"]]
        if not ps:
            raise HTTPException(409, "묶어 승인할 제안이 없다: " + (
                "데이터 규칙을 통과한 제안이 없어 승인하지 않았다" if skipped else "남은 제안은 하나씩 승인한다"))
    with writing():
        if version not in (None, state["version"]):
            raise HTTPException(409, "검사하는 동안 그래프가 바뀌었다. 다시 검사해 주세요")
        made = []

        def reflect(changed, _all):
            if body.result == "approved":
                made.append(graph_with(changed))
        try:
            done = proposals.decide_many([p.id for p in ps], body.result, body.reviewer, body.comment, before_save=reflect)
        except KeyError:
            raise HTTPException(404, "없는 제안")
        except ValueError as e:
            raise HTTPException(409, str(e))
        if made:
            adopt(made[0])
    return {"group": body.group, "result": body.result, "decided": [p.id for p in done], "skipped": skipped, "held": held}


class CorrectionIn(BaseModel):
    to: Literal["pending", "approved", "rejected"]
    reviewer: str
    reason: str = ""

    @field_validator("reviewer")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("reviewer", "reason")
    @classmethod
    def _text(cls, v: str) -> str:
        return text(v)

    def problem(self) -> str | None:
        p = DecisionIn(result="rejected", reviewer=self.reviewer, comment=self.reason).problem()
        return "정정 사유가 필요하다" if p == "반려 사유가 필요하다" else p


def subjects(p) -> set[str]:
    return {p.target} | {c.s for c in p.changes}


def objects(p) -> set[str]:
    """제안이 목적어로 가리키는 우리 객체. 종류나 상태 같은 어휘 값은 들지 않는다."""
    return {c.o.value for c in p.changes if c.o.kind == "uri" and c.o.value.startswith(BASE + "/")}


def approved_after(p) -> list:
    """p보다 뒤에 승인되어 p가 바꾼 객체를 다시 바꾸거나 가리키는 제안. 그래프를 불러올 때와 같은 순서로 본다."""
    order = sorted((q for q in proposals.load() if q.status == "approved"), key=lambda q: q.decision.seq)
    later = order[next(i for i, q in enumerate(order) if q.id == p.id) + 1:]
    mine = subjects(p)
    return [q for q in later if (subjects(q) | objects(q)) & mine]


def probe(g: ox.Store, p, nodes: set[str]):
    """nodes를 검사하게 만드는 시험 제안. 이미 있는 트리플을 넣는 변경이라 아무것도 바꾸지 않고, check_proposal의 검사 범위만 빌린다."""
    changes = []
    for u in sorted(nodes):
        n = ox.NamedNode(u)
        for q in itertools.chain(g.quads_for_pattern(n, RDF_TYPE, None, None), g.quads_for_pattern(n, None, None, None)):
            if isinstance(q.object, (ox.NamedNode, ox.Literal)):
                changes.append(Change(op="add", s=u, p=q.predicate.value, o=Term.from_ox(q.object)))
                break
    return p.model_copy(update={"target": changes[0].s, "changes": changes}) if changes else None


def broken_rules(g: ox.Store, p, nodes: set[str]) -> set[tuple[str, str]]:
    from src.graph.validate import check_proposal
    q = probe(g, p, nodes)
    return {(r["focus"], r["message"]) for r in check_proposal(g, q)[1]} if q else set()


def require_clean_withdrawal(old: ox.Store, new: ox.Store, p) -> None:
    """승인을 거둔 그래프에서 영향받는 객체가 전에 없던 규칙을 어기면 막는다.

    볼 객체: 바꾼 주어와 가리킨 객체. 거두어 사라진 객체는 그 객체를 가리키는 주어를 본다.
    """
    from src.graph.validate import HISTORY_LINKS
    nodes = subjects(p) | objects(p)
    for u in list(nodes):
        n = ox.NamedNode(u)
        if next(new.quads_for_pattern(n, None, None, None), None) is None:
            nodes |= {q.subject.value for q in new.quads_for_pattern(None, None, n, None)
                      if q.predicate not in HISTORY_LINKS and isinstance(q.subject, ox.NamedNode)}
    fresh = broken_rules(new, p, nodes) - broken_rules(old, p, nodes)
    if fresh:
        raise HTTPException(409, "승인을 거두면 데이터 규칙을 어긴다: " + "; ".join(sorted({m for _, m in fresh})))


def rebuilt_without(p, everything: list) -> tuple[ox.Store, ReplayReport]:
    """원천 그래프와 남은 승인 기록으로 새 그래프를 만들어 검사한다. 저장 전에 불러, 실패하면 저장하지 않게 한다.

    재생 결과(충돌 목록)는 함께 돌려주고 바꿔 끼울 때 내건다. 정정이 막히거나 저장에 실패하면 지금 그래프의 결과가 그대로 남는다.
    """
    try:
        new, report = build_store(everything)
    except (SyntaxError, OSError, RuntimeError, ValueError):
        log.exception("그래프를 다시 만들지 못했다")
        raise HTTPException(500, "그래프를 다시 만들지 못해 정정을 저장하지 않았다")
    require_clean_withdrawal(store(), new, p)
    return new, report


@app.post("/api/proposals/{pid}/correction")
def correct(pid: str, body: CorrectionIn):
    """결정을 정정한다. 앞의 결정은 지우지 않고 정정 기록으로 남는다.

    승인을 거둘 때: 뒤에 승인된 제안이 같은 객체를 바꾸거나 가리키면 그쪽을 먼저 정정해야 한다.
    거둔 변경만 되돌리는 대신 원천 그래프와 남은 승인 기록으로 다시 만들고, 영향받는 객체를 다시 검사한다.
    반려를 승인으로 바꿀 때: 보통 승인처럼 SHACL 검사를 통과해야 하고, 지금 그래프에 얹는다.
    """
    problem = body.problem()
    if problem:
        raise HTTPException(400, problem)
    with writing():
        p = proposals.get(pid)
        if p is None:
            raise HTTPException(404, "없는 제안")
        if p.status == "approved" and body.to != "approved":
            later = approved_after(p)
            if later:
                raise HTTPException(409, "뒤에 승인된 제안이 같은 객체를 바꾸거나 가리킨다. 그 제안부터 정정해야 한다: "
                                    + ", ".join(q.id for q in later))
        if body.to == "approved" and p.status == "rejected":
            require_rules(p)
        made = []

        def reflect(changed, everything):
            q = changed[0]
            if q.corrections[-1].from_status == "approved":
                made.append(rebuilt_without(q, everything))
            elif q.status == "approved":
                made.append((graph_with(changed), None))
        try:
            p = proposals.correct(pid, body.to, body.reviewer, body.reason, before_save=reflect)
        except KeyError:
            raise HTTPException(404, "없는 제안")
        except ValueError as e:
            raise HTTPException(409, str(e))
        if made:
            adopt(*made[0])
    return shown(p)


def notices_for_people() -> list[dict]:
    """사람이 직접 볼 고시: 제안 수 상한을 넘겼거나, 거부됐거나, 처리하다 멈췄거나, 다시 뽑은 제안이 앞 제안과 달라 건너뛴 문서."""
    out = []
    for path in sorted(NOTICE_REPORTS.glob("*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        differ = report.get("differ")
        why = report.get("held") or report.get("rejected") or report.get("error") or (differ and f"같은 id의 제안이 내용이 달라 건너뛰었다: {differ}")
        if why:
            out.append({"file": str(report.get("file")), "why": str(why)})
    return out


@app.get("/api/status")
def status():
    """그래프를 다 불러왔는지, 지금 그래프 판 번호, 대기 제안 수, 재적용 충돌, 읽지 못해 건너뛴 기록, 사람에게 넘긴 고시."""
    g = state["store"]
    pending = sum(p.status == "pending" for p in proposals.load())
    skipped = [{"index": b["index"], "id": str(b["id"]), "status": str(b["status"]), "error": b["error"]} for b in proposals.broken]
    return {"graph_loaded": g is not None, "triples": len(g) if g is not None else None, "version": state["version"],
            "pending": pending, "conflicts": last_report().conflicts if g is not None else [], "skipped": skipped,
            "skipped_approved": sum(b["status"] == "approved" for b in skipped), "notices": notices_for_people()}


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
        # 모양 노드와, 승인된 제안이 바꾼 트리플 기록(rdf:subject, rdf:object, jgk:changeOf)은 연결로 보이지 않는다 (변경 이력에서 따로 본다)
        if q.predicate not in (HAS_GEOMETRY, HAS_DEFAULT_GEOMETRY, RDF_SUBJECT, RDF_OBJECT, CHANGE_OF):
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
            geometry = geometry_of(wkt)
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
    """객체 하나의 시각 at 값과 지금 값의 차이 (시점별 판). 트리플마다 그 시각 이전의 마지막 사건으로 구한다.

    모양의 값은 속성 이름이 '…#hasGeometry …#asWKT'처럼 공백으로 이어져 온다.
    """
    from src.graph.editions import as_of, diff_states, moment
    try:
        iri(uri)
        when = moment(at)
    except ValueError as e:
        raise HTTPException(400, f"URI나 시각이 올바르지 않다: {e}") from None
    g = store()
    rows = diff_states(as_of(g, uri, when), as_of(g, uri, "2999-12-31T00:00:00+09:00"))
    uris = [u for r in rows for u in [*r["p"].split(" "), *r["before"], *r["after"]] if u.startswith("http")]
    return {"uri": uri, "at": when, "diff": rows, "names": names(uris)}


@app.get("/api/editions/changes")
def edition_changes(since: str = "1900-01-01", until: str = "2999-12-31"):
    """두 시각 사이의 변경과 그 변경이 넣고 뺀 트리플. 날짜만 준 끝 시각은 그날 끝이고, 승인을 거둔 정정은 action이 withdraw다."""
    from src.graph.editions import changes_between, moment
    try:
        since, until = moment(since), moment(until, end=True)
    except ValueError as e:
        raise HTTPException(400, f"시각이 올바르지 않다: {e}") from None
    rows = changes_between(store(), since, until)
    uris = [r[k] for r in rows for k in ("target", "s", "p", "o") if r[k].startswith("http")]
    return {"since": since, "until": until, "changes": rows, "names": names(uris)}


LAYER_QUERY = {
    "building": "jgk:Building", "entrance": "jgk:BuildingEntrance",
    "facility": "jgk:PublicFacility", "station": "jgk:Station", "sensor": "jgk:Sensor",
    "road": "jgk:RoadSegment", "legaldong": "jgk:LegalDong", "basemap": "jgk:BaseMapFeature",
    "digitalmap": "jgk:DigitalMapFeature",
}
LAYER_FIELDS = ("label", "status", "use", "addr", "type", "dong", "baddr", "bkind")
layer_cache: dict = {}  # (레이어, 그래프 판 번호) -> 응답 본문(bytes)
layer_locks = {kind: threading.Lock() for kind in LAYER_QUERY}  # 같은 레이어를 동시에 달라고 해도 한 번만 만든다


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
    feats, skipped = [], 0
    for s in store().query(q):
        geometry = geometry_of(term(s["wkt"]))
        if geometry is None:  # 모양 하나를 못 읽어도 레이어 전체가 내려가면 안 된다
            skipped += 1
            log.warning("모양을 읽지 못해 레이어에서 건너뛴다: %s", term(s["f"]))
            continue
        props = {k: term(s[k]) for k in LAYER_FIELDS}
        baddr = props.pop("baddr")
        props["type"] = props["type"] or props.pop("bkind")  # 수치지형도 건물은 종류(아파트, 무벽건물 등)
        props.pop("bkind", None)
        props["addr"] = props["addr"] or baddr  # 출입구는 그 출입구를 가진 건물의 도로명주소
        feats.append({"type": "Feature", "geometry": geometry,
                      "properties": {k: props[k] for k in ("label", "status", "use", "addr", "type")}
                      | {"uri": term(s["f"]), "kind": kind, "dong": props["dong"]}})
    return json.dumps({"type": "FeatureCollection", "features": feats, "skipped": skipped},
                      ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def layer_bytes(kind: str, version: int) -> bytes:
    key = (kind, version)
    body = layer_cache.get(key)
    if body is None:
        with layer_locks[kind]:
            body = layer_cache.get(key)
            if body is None:
                body = layer_body(kind)
                if state["version"] == version:  # 만드는 사이에 승인이 들어왔으면 담아 두지 않는다
                    layer_cache[key] = body
    return body


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
    return Response(layer_bytes(kind, version), media_type="application/json", headers=headers)


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

    @field_validator("question")
    @classmethod
    def _text(cls, v: str) -> str:
        return text(v)


system_cache: dict = {}  # 그래프 판 번호 -> 자연어 질의 프롬프트


def system_prompt() -> str:
    from src.query.nl import build_system
    version = state["version"]
    if version not in system_cache:
        text = build_system(store())
        system_cache.clear()
        system_cache[version] = text
    return system_cache[version]


def spend_ask() -> None:
    """질문 호출량 상한. 질문 하나가 유료 호출이라 넘으면 모델을 부르지 않고 429와 기다릴 초를 준다."""
    t = time.monotonic()
    with ask_lock:
        while ask_times and t - ask_times[0] >= 86400:
            ask_times.popleft()
        recent = [x for x in ask_times if t - x < 60]
        wait = None
        if len(recent) >= ASK_PER_MINUTE:
            wait = 60 - (t - recent[-ASK_PER_MINUTE])
        elif len(ask_times) >= ASK_PER_DAY:
            wait = 86400 - (t - ask_times[-ASK_PER_DAY])
        if wait is None:
            ask_times.append(t)
            return
    raise HTTPException(429, "질문이 너무 많다. 잠시 뒤에 다시 물어 주세요", headers={"Retry-After": str(math.ceil(wait))})


@app.post("/api/ask")
def ask_question(body: AskIn):
    """자연어 질문 -> SPARQL -> 결과와 지도용 GeoJSON. LLM 키가 없으면 503."""
    import anthropic

    from src.llm.client import LLMRefused, MissingAPIKey
    from src.query.nl import QueryBusy, QueryTimeout, ask
    if not body.question.strip():
        raise HTTPException(400, "질문이 비었다")
    spend_ask()
    try:
        r = ask(body.question.strip(), store=store(), system=system_prompt())
    except MissingAPIKey:
        raise HTTPException(503, "Claude API를 쓸 수 없다 (.env의 ANTHROPIC_API_KEY 확인)")
    except LLMRefused:
        raise HTTPException(422, "질문을 처리하지 못했다 (모델이 거절)")
    except QueryTimeout as e:
        raise HTTPException(504, str(e))
    except QueryBusy as e:
        raise HTTPException(503, str(e))
    except anthropic.APIError as e:  # 오류 본문에는 조직 번호나 잘못 넣은 키가 들어 있을 수 있어 로그로만 남긴다
        log.warning("Claude API 오류: %s", API_KEY.sub("sk-ant-(가림)", str(e)))
        raise HTTPException(502, "Claude API를 부르지 못했다. 잠시 뒤에 다시 물어 주세요")
    except ValueError as e:
        raise HTTPException(422, str(e))
    r["names"] = names(v for row in r["rows"] for v in row if isinstance(v, str) and v.startswith(BASE))
    return r


@app.get("/map")
def map_page():
    return page_file(ROOT / "web" / "map.html")


@app.get("/api/proposals/{pid}/context")
def context(pid: str):
    """제안 위치 둘레의 건물(그래프에서 GeoSPARQL로 조회)과 제안 대상의 모양을 GeoJSON으로 준다. 읽지 못하는 모양은 건너뛴다."""
    p = proposals.get(pid)
    where = None if p is None else place(p)
    if where is None:
        raise HTTPException(404, "위치가 없는 제안")
    circle = gpd.GeoSeries([Point(where)], crs="EPSG:4326").to_crs("EPSG:5186") \
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
    features, skipped = [], 0
    for s in store().query(q):
        geometry = geometry_of(term(s["wkt"]))
        if geometry:
            features.append({"type": "Feature", "geometry": geometry, "properties": {
                "uri": term(s["b"]), "label": term(s["label"]), "status": term(s["status"]), "role": role(term(s["b"]))}})
        else:
            skipped += 1
    drawn = {f["properties"]["uri"] for f in features}
    # 대상과 함께 바뀌는 객체의 모양: 제안에 새 모양이 있으면 그것을, 없으면 지금 그래프의 모양을 쓴다
    new_wkt = {c.s.removesuffix("/geometry"): c.o.value for c in p.changes if c.p.endswith("#asWKT")}
    for u in [p.target, *sorted(changed)]:
        if u in drawn or not is_uri(u):  # 형식이 맞지 않는 URI는 쿼리에 넣지 않는다
            continue
        wkt = new_wkt.get(u) or next((term(s["w"]) for s in store().query(
            f"SELECT ?w WHERE {{ <{u}> <http://www.opengis.net/ont/geosparql#hasDefaultGeometry>/"
            f"<http://www.opengis.net/ont/geosparql#asWKT> ?w }}")), None)
        geometry = geometry_of(wkt)
        if geometry:
            features.append({"type": "Feature", "geometry": geometry,
                             "properties": {"uri": u, "label": p.summary if u == p.target else None, "role": role(u)}})
        elif wkt:
            skipped += 1
    return {"type": "FeatureCollection", "features": features, "skipped": skipped}
