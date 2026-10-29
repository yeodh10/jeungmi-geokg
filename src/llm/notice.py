"""고시공고 문서에서 변경 내용을 뽑아 변경 제안으로 만든다.

실행: python -m src.llm.notice data/raw/notices/<파일>.txt [다른 파일 ...]
  (파일 첫 줄들에 '제목:', '번호:', '날짜:', '출처:'를 적어 두면 근거에 함께 남는다)

나누어 맡는 일:
1. LLM은 문서에서 바뀌는 내용만 정해진 형식으로 뽑는다 (무엇이, 어디서, 어떻게, 원문 문장).
2. 뽑힌 주소, 지번, 이름을 그래프 객체에 맞추는 일은 코드가 정해진 규칙으로 한다. LLM이 URI를 정하지 않는다.
3. 객체를 하나로 찾은 변경만 제안이 되고, 사람이 검토 화면에서 승인해야 그래프에 들어간다.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Literal

import geopandas as gpd
import pandas as pd
import shapely
from pydantic import BaseModel, Field

from src.graph.store import load_store
from src.llm.client import get_client, parse
from src.review.proposals import Change, Proposal, ProposalStore, Term, now

ROOT = Path(__file__).resolve().parents[2]
PROC = ROOT / "data" / "processed"
REPORT_DIR = PROC / "review" / "notices"
JGK = "https://yeodh10.github.io/jeungmi-geokg/def#"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
XSD_DATE = "http://www.w3.org/2001/XMLSchema#date"

ChangeType = Literal[
    "building_demolished",   # 건물 멸실, 철거
    "building_use_change",   # 건물 용도 변경
    "building_name_change",  # 건물 이름 변경
    "address_change",        # 도로명주소 변경, 부여
    "facility_closed",       # 공공시설 폐지, 이전으로 문을 닫음
    "building_new",          # 건물 신축, 사용승인
    "other",                 # 위에 없지만 공간 객체가 바뀌는 내용
]


class Target(BaseModel):
    road_address: str | None = Field(None, description="문서에 적힌 도로명주소 그대로. 예: 강서구 양천로 550")
    jibun_address: str | None = Field(None, description="문서에 적힌 지번 주소 그대로. 예: 등촌동 666-40")
    name: str | None = Field(None, description="건물 이름이나 기관 이름")


class ExtractedChange(BaseModel):
    change_type: ChangeType
    summary: str = Field(description="무엇이 어떻게 바뀌는지 한 문장")
    quote: str = Field(description="근거가 되는 원문 문장을 고치지 않고 그대로")
    target: Target
    new_value: str | None = Field(None, description="바뀐 뒤의 값 (새 용도, 새 이름, 새 주소). 없으면 비운다")
    effective_date: str | None = Field(None, description="효력 날짜 YYYY-MM-DD. 문서에 없으면 비운다")


class NoticeExtraction(BaseModel):
    is_spatial: bool = Field(description="건물, 도로, 주소, 공공시설 같은 공간 객체가 바뀌는 문서인가")
    changes: list[ExtractedChange]


SYSTEM = """너는 국가기본도 지식 그래프의 갱신 근거를 찾는 도우미다.
구청 고시공고 원문을 받으면, 건물, 도로명주소, 공공시설 같은 공간 객체가 바뀌는 내용만 뽑는다.

규칙:
- 문서에 실제로 적힌 내용만 뽑는다. 추측해서 주소나 값을 만들지 않는다.
- quote에는 근거 문장을 원문 그대로 옮긴다. 줄이거나 다듬지 않는다.
- 한 문서에 대상이 여럿이면 대상마다 하나씩 나눠서 뽑는다.
- 공간 객체와 상관없는 공고(채용, 행사, 세금 안내 등)는 is_spatial을 false로 하고 changes를 비운다.
- 주소는 문서에 적힌 형태 그대로 쓴다. 지번과 도로명주소가 둘 다 있으면 둘 다 적는다."""


# ---- 객체 맞추기 (코드가 정해진 규칙으로 한다) ----

def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


class Resolver:
    """뽑힌 주소, 지번, 이름을 그래프 객체 URI에 맞춘다."""

    def __init__(self, proc: Path = PROC):
        b = gpd.read_file(proc / "objects.gpkg", layer="building")
        f = gpd.read_file(proc / "objects.gpkg", layer="public_facility")
        jibun = pd.read_csv(proc / "navi_jibun.csv", dtype=str, keep_default_na=False)
        self.buildings = b[["uri", "label", "road_address", "pnu", "kind"]].fillna("")
        self.facilities = f[["uri", "label"]].fillna("")
        # 법정동 이름 -> 법정동코드 (내비게이션용DB 지번 표에서)
        self.dong_code = dict(zip(jibun["emd"], jibun["legal_dong_code"]))

    def by_road(self, text: str) -> list[str]:
        # '강서구 양천로 550' -> 도로명주소 끝부분 '양천로 550'이 같은 건물
        m = re.search(r"(\S+(?:로|길))\s*(지하\s*)?(\d+(?:-\d+)?)", text)
        if not m:
            return []
        tail = norm(f"{m.group(1)} {'지하 ' if m.group(2) else ''}{m.group(3)}")
        hit = self.buildings[self.buildings["road_address"].map(lambda a: norm(a).endswith(tail))]
        return hit["uri"].tolist()

    def by_jibun(self, text: str) -> list[str]:
        m = re.search(r"(\S+동)\s*(산\s*)?(\d+)(?:-(\d+))?", text)
        if not m or m.group(1) not in self.dong_code:
            return []
        pnu = self.dong_code[m.group(1)] + ("2" if m.group(2) else "1") + m.group(3).zfill(4) + (m.group(4) or "0").zfill(4)
        return self.buildings.loc[self.buildings["pnu"] == pnu, "uri"].tolist()

    def by_name(self, name: str, facility: bool) -> list[str]:
        table = self.facilities if facility else self.buildings
        return table.loc[table["label"].map(norm) == norm(name), "uri"].tolist()

    def resolve(self, t: Target, facility: bool = False) -> tuple[list[str], str]:
        """앞 방법에서 정확히 하나가 나오면 그것으로 정한다."""
        tries = []
        if t.name:
            tries.append(("이름", self.by_name(t.name, facility)))
        if not facility:
            if t.road_address:
                tries.append(("도로명주소", self.by_road(t.road_address)))
            if t.jibun_address:
                tries.append(("지번", self.by_jibun(t.jibun_address)))
        for how, uris in tries:
            if len(uris) == 1:
                return uris, how
        found = [u for _, uris in tries for u in uris]
        return sorted(set(found)), "여러 후보" if found else "찾지 못함"


# ---- 제안 만들기 ----

UPDATE_PREDICATE = {
    "building_use_change": JGK + "useName",
    "building_name_change": RDFS_LABEL,
    "address_change": JGK + "roadAddress",
}


def current_values(store, s: str, p: str) -> list:
    return [row["o"] for row in store.query(f"SELECT ?o WHERE {{ <{s}> <{p}> ?o }}")]


def location_of(store, s: str) -> tuple[float, float] | None:
    q = f"SELECT ?w WHERE {{ <{s}> <http://www.opengis.net/ont/geosparql#hasDefaultGeometry>/<http://www.opengis.net/ont/geosparql#asWKT> ?w }}"
    for row in store.query(q):
        c = shapely.from_wkt(row["w"].value).centroid
        return (c.x, c.y)
    return None


def to_term(o) -> Term:
    if hasattr(o, "language") and o.language:
        return Term(kind="literal", value=o.value, lang=o.language)
    if hasattr(o, "datatype"):
        dt = o.datatype.value if o.datatype else None
        return Term(kind="literal", value=o.value,
                    datatype=None if dt == "http://www.w3.org/2001/XMLSchema#string" else dt)
    return Term(kind="uri", value=o.value)


def make_proposal(c: ExtractedChange, uri: str, how: str, meta: dict, store, doc_id: str, n: int) -> Proposal | None:
    evidence = [f"고시공고: {meta.get('제목', '(제목 없음)')}" + (f" ({meta['번호']})" if meta.get("번호") else ""),
                f"원문: \"{c.quote}\"", f"객체 찾기: {how}로 {uri.rsplit('/', 1)[-1]}"]
    if meta.get("출처"):
        evidence.append(f"출처: {meta['출처']}")
    changes: list[Change] = []
    if c.change_type in ("building_demolished", "facility_closed"):
        action = "retire"
        for o in current_values(store, uri, JGK + "status"):
            changes.append(Change(op="remove", s=uri, p=JGK + "status", o=to_term(o)))
        changes += [Change(op="add", s=uri, p=JGK + "status", o=Term(kind="uri", value=JGK + "Retired")),
                    Change(op="add", s=uri, p=JGK + "retiredOn",
                           o=Term(kind="literal", value=c.effective_date or meta.get("날짜") or now()[:10], datatype=XSD_DATE))]
    elif c.change_type in UPDATE_PREDICATE and c.new_value:
        action = "update"
        p = UPDATE_PREDICATE[c.change_type]
        for o in current_values(store, uri, p):
            changes.append(Change(op="remove", s=uri, p=p, o=to_term(o)))
        changes.append(Change(op="add", s=uri, p=p,
                              o=Term(kind="literal", value=c.new_value, lang="ko" if p == RDFS_LABEL else None)))
    else:
        return None  # 모양이 필요한 신축 등은 지금 그래프 규칙으로 만들 수 없다. 보고서에만 남긴다
    return Proposal(id=f"llm-{doc_id}-{n}", created_at=now(), source_kind="llm-notice",
                    source_detail=f"{meta.get('제목', doc_id)} (LLM 추출, 객체 찾기는 규칙)",
                    action=action, target=uri, target_type="PublicFacility" if "/public-facility/" in uri else "Building",
                    summary=c.summary, evidence=evidence, changes=changes, location=location_of(store, uri))


def read_notice(path: Path) -> tuple[dict, str]:
    """앞부분의 '키: 값' 줄을 메타정보로 읽고 나머지를 본문으로 쓴다."""
    meta, body = {}, []
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(제목|번호|날짜|출처):\s*(.+)$", line)
        if m and not body:
            meta[m.group(1)] = m.group(2).strip()
        else:
            body = lines[i:]
            break
    return meta, "\n".join(body).strip()


def process(path: Path, client=None, resolver: Resolver | None = None, store=None,
            proposals: ProposalStore | None = None) -> dict:
    meta, body = read_notice(path)
    doc_id = hashlib.sha1(body.encode("utf-8")).hexdigest()[:10]
    ext = parse(client or get_client(), NoticeExtraction, SYSTEM,
                f"고시공고 제목: {meta.get('제목', '')}\n\n{body}")
    resolver = resolver or Resolver()
    store = store or load_store()
    made, report = [], {"file": path.name, "doc_id": doc_id, "meta": meta, "is_spatial": ext.is_spatial, "changes": []}
    for n, c in enumerate(ext.changes, 1):
        uris, how = resolver.resolve(c.target, facility=c.change_type == "facility_closed")
        item = {**c.model_dump(), "resolved": uris, "how": how, "proposal": None}
        if len(uris) == 1:
            p = make_proposal(c, uris[0], how, meta, store, doc_id, n)
            if p:
                made.append(p)
                item["proposal"] = p.id
        report["changes"].append(item)
    added = (proposals or ProposalStore()).add_new(made)
    report["proposals_added"] = added
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / f"{doc_id}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    resolver, store = Resolver(), load_store()
    for arg in sys.argv[1:]:
        r = process(Path(arg), resolver=resolver, store=store)
        print(f"{r['file']}: 변경 {len(r['changes'])}건, 새 제안 {r['proposals_added']}건")


if __name__ == "__main__":
    main()
