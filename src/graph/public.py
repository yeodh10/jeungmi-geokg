"""공개 판단의 한 곳: 공개 뷰와 전체 뷰, 공개 쿼리 저장소, 화면 이름, 지번.

publish.py, 그리고 앞으로 places.py, cards.py, questions.py는 이 함수만 쓰고 각자 보류 판단을 하지 않는다.
  public_view()            공개 사이트용. 공개 근거가 있는 승인만 얹고, 출처를 모르거나 보류 출처에서 온 노드와 공개하지 않는 상호를 held에 둔다
  local_view(store)        로컬 서버용. 같은 모양이고 held가 비어 있다
  public_query_store(view) 보류 주어, 보류 값을 가리키는 트리플, 변경 기록을 뺀 쿼리용 사본
  display_name(view, uri)  화면 이름 (명세 9절 이름 규칙). 28자리 번호는 쓰지 않는다
  jibun_from_pnu(pnu, 법정동 이름)  PNU 19자리 -> '등촌동 666-40'

src.graph.publish는 hold_sources, public_nodes를 계속 내보낸다 (src/review/app.py, tests가 그곳에서 불러 쓴다).
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pyoxigraph as ox
import yaml

from src.model.uri import BASE

ROOT = Path(__file__).resolve().parents[2]
SITE = BASE.rsplit("/id", 1)[0]  # https://yeodh10.github.io/jeungmi-geokg
JGK = SITE + "/def#"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
# 원천 층 값이 건물 층수보다 크거나 호수처럼 보일 때 화면에 적는 말 (unfit_floors). 값 자체는 싣지 않는다
FLOOR_WITHHELD = "원천 층 값이 이 건물 층수와 맞지 않아 싣지 않았습니다 (호수일 수 있습니다)"
RDFS_COMMENT = "http://www.w3.org/2000/01/rdf-schema#comment"
DCT_SOURCE = "http://purl.org/dc/terms/source"
PROV_INFLUENCED = "http://www.w3.org/ns/prov#wasInfluencedBy"
# 동 표기만인 이름 ('가동', '1동', 'A', '.'). [가-하]로 쓰면 '매장동'도 잘려 글자를 하나씩 적는다
UNIT = re.compile(r"^(제?\s*)?([0-9]{1,4}|[A-Za-z]{1,2}|[가나다라마바사아자차카타파하]|[일이삼사오]|[0-9]{1,3}-[0-9]{1,3})\s*(동|호|층|관)?$|^\.+$|^-+$")
# 이름 끝의 동 표기. (?<![가-힣])가 없으면 '교사동'이 '교'로 잘린다
UNIT_TAIL = re.compile(r"\s*(제?[0-9]{1,4}|[A-Za-z]|(?<![가-힣])[가나다라마바사아자차카타파하])\s*동\s*$")
LONG_NO = re.compile(r"[0-9]{20,}")  # 화면 이름에 쓰지 않는 긴 번호 (GIS 28자리, 건물관리번호 25자리)
BUILDING_KINDS = ("building", "building-gis")
KIND_NAMES = {  # 이름이 없을 때 앞에 붙일 종류 이름 (명세 8절 말)
    "station": "역", "building": "건물", "building-gis": "건물", "entrance": "건물 출입구", "parcel": "필지", "road": "도로",
    "admin-dong": "행정동", "legal-dong": "법정동", "basic-zone": "우편구역", "notice": "고시공고", "sensor": "센서",
    "business": "상호", "observation": "관측", "proposal": "변경 제안",
}


def hold_sources() -> set[str]:
    """공개를 보류한 출처의 URI (config/area.yaml publish.hold_sources). 지도 화면 API(src.review.app)도 같은 목록을 쓴다."""
    with open(ROOT / "config" / "area.yaml", encoding="utf-8") as f:
        return {JGK + s for s in (yaml.safe_load(f).get("publish") or {}).get("hold_sources") or []}


def kind_of(uri: str) -> str:
    return uri[len(BASE) + 1:].split("/")[0]


def public_nodes(triples: dict, hold: set[str], given: dict[str, set[str]] | None = None) -> set[str]:
    """공개해도 되는 우리 URI. 출처가 있고 그 출처가 모두 공개 자료인 노드만 들어간다. 출처를 모르면 공개하지 않는다.

    모양 노드와 제안의 변경 기록은 자기 출처가 없으면 부모(객체, 제안)의 출처를 따른다.
    부모를 공개할 수 없으면 자기 출처가 무엇이든 공개하지 않는다.
    제안에는 출처 트리플이 없어 근거 자료를 쓴다 (given: 제안 URI -> 출처 URI).
    """
    given = given or {}
    open_sets = {s for s, plist in triples.items() if any(p.value == RDF_TYPE and o.value == JGK + "Dataset" for p, o in plist)} - hold

    def parent(s: str) -> str | None:
        if s.endswith("/geometry"):
            return s.removesuffix("/geometry")
        return s.rsplit("/change/", 1)[0] if "/change/" in s else None

    def sources(s: str) -> set[str]:
        found = {o.value for p, o in triples.get(s, []) if p.value == DCT_SOURCE} | given.get(s, set())
        up = parent(s)
        return sources(up) if not found and up else found

    def known(s: str, src: str) -> bool:  # 고시는 자료 이름 대신 구청 게시물 주소를 출처로 적는다
        return src in open_sets or (kind_of(s) == "notice" and src.startswith("https://"))

    def allowed(s: str) -> bool:
        found, up = sources(s), parent(s)
        return bool(found) and all(known(s, x) for x in found) and (up is None or allowed(up))

    return {s for s in triples if s.startswith(BASE + "/") and allowed(s)}


def private_businesses(triples: dict) -> set[str]:
    """공개 사이트에 내지 않는 상호와 그 모양 노드. 주택이나 용도를 모르는 건물 안 업체는 개인 집 주소일 수 있어서다.

    규칙은 config/area.yaml의 publish.business이고 판단은 화면 자료와 같은 함수(src.model.build_business.is_public)로 한다.
    건물 용도가 비었으면 이름의 '아파트', 선 필지의 세대수나 단지 주용도를 근거로 주택인지 본다(effective_use).
    필지에만 이은 상호는 그 필지 위 모든 건물의 용도를 본다. 이름에 호수가 든 상호도 뺀다.
    """
    from src.model.build_business import effective_use, is_public, load_rule
    rule = load_rule()

    def val(s, name):
        return next((o.value for p, o in triples.get(s, []) if p.value == JGK + name), None)

    def label(s):
        return next((o.value for p, o in triples.get(s, []) if p.value == RDFS_LABEL), None)

    def lot_facts(lot):
        return (val(lot, "households"), val(lot, "complexMainUse"))

    def use_of(b):
        lots = [o.value for p, o in triples.get(b, []) if p.value == JGK + "locatedOn"]
        return effective_use(val(b, "useName"), label(b), [lot_facts(x) for x in lots])

    lot_uses = defaultdict(list)
    for s, plist in triples.items():
        for p, o in plist:
            if p.value == JGK + "locatedOn":
                lot_uses[o.value].append(use_of(s))
    out = set()
    for s, plist in triples.items():
        if not any(p.value == RDF_TYPE and o.value == JGK + "Business" for p, o in plist):
            continue
        b, lot = val(s, "inBuilding"), val(s, "onParcel")
        uses = [use_of(b)] if b else (lot_uses.get(lot) or [effective_use(None, None, [lot_facts(lot)])] if lot else [])
        if not is_public(uses, val(s, "categoryLarge"), val(s, "floorInfo"), rule, linked=bool(b or lot), name=label(s)):
            out |= {s, s + "/geometry"}
    return out


def unfit_floors(triples: dict) -> set[str]:
    """층 값을 층으로 보여 주지 않을 상호 (src.model.build_business.floor_fits). 숫자 층이 이은 건물의 지상 층수보다 크거나,
    건물 층수를 모르는데 100 이상이면 호수('1501')일 수 있다. 공개 뷰는 이 상호의 층 값을 그래프에서 뺀다."""
    from src.model.build_business import floor_fits

    def val(s, name):
        return next((o.value for p, o in triples.get(s, []) if p.value == JGK + name), None)
    out = set()
    for s, plist in triples.items():
        if not any(p.value == RDF_TYPE and o.value == JGK + "Business" for p, o in plist):
            continue
        fl = val(s, "floorInfo")
        b = val(s, "inBuilding")
        if fl and not floor_fits(fl, val(b, "floorsAbove") if b else None):
            out.add(s)
    return out


@dataclass
class GraphView:
    store: ox.Store            # 쿼리용 저장소 (공개 뷰는 공개 근거 승인만 얹은 그래프. 보류 노드는 public_query_store로 뺀다)
    triples: dict              # 주어 -> [(술어, 값)] (pyoxigraph 항)
    held: set[str]             # 보류 객체와 그 모양. 전체 뷰에서는 빈 집합
    labels: dict               # 주어 -> 이름 (한국어나 언어 없는 rdfs:label)
    addrs: dict                # 주어 -> 도로명주소
    comments: dict             # 주어 -> rdfs:comment
    private: set[str] = field(default_factory=set)  # 공개하지 않는 상호와 그 모양 (held에도 들어 있다)
    unfit_floor: set[str] = field(default_factory=set)  # 층 값을 층으로 보여 주지 않는 상호 (공개 뷰는 그 층 값을 뺐다)
    _cache: dict = field(default_factory=dict, repr=False)

    def shown(self, uri: str) -> bool:
        """우리 URI는 그래프에 있고 보류가 아니어야 보인다. 바깥 URI는 늘 보인다."""
        return not uri.startswith(BASE + "/") or (uri in self.triples and uri not in self.held)

    def value(self, s: str, name: str) -> str | None:
        """주어의 jgk 속성 값 하나 (없으면 None)."""
        return next((o.value for p, o in self.triples.get(s, []) if p.value == JGK + name), None)

    def floor_of(self, business: str) -> str | None:
        """상호의 층 값. 층으로 보기 어려운 값(unfit_floor)이면 None."""
        return None if business in self.unfit_floor else self.value(business, "floorInfo")

    def values(self, s: str, name: str) -> list[str]:
        return [o.value for p, o in self.triples.get(s, []) if p.value == JGK + name]

    def legal_dongs(self) -> dict[str, str]:
        """법정동코드 10자리 -> 이름."""
        if "ld" not in self._cache:
            self._cache["ld"] = {u.rsplit("/", 1)[-1]: n for u, n in self.labels.items() if u.startswith(BASE + "/legal-dong/") and "/geometry" not in u}
        return self._cache["ld"]

    def pnu_of(self, uri: str) -> str | None:
        """건물의 PNU. 필지 연결이 있으면 그 필지(여럿이면 번호 순 첫째), 없으면 건물관리번호 앞 19자리 (형식이 PNU와 같다)."""
        lots = sorted(self.values(uri, "locatedOn"))
        if lots:
            return lots[0].rsplit("/", 1)[-1]
        if kind_of(uri) == "parcel":
            return uri.rsplit("/", 1)[-1]
        no = self.value(uri, "buildingMgtNo") or (uri.rsplit("/", 1)[-1] if kind_of(uri) in ("building", "station") else "")
        return no[:19] if re.fullmatch(r"[0-9]{25}", no or "") else None

    def jibun(self, uri: str) -> str | None:
        pnu = self.pnu_of(uri)
        return jibun_from_pnu(pnu, self.legal_dongs()) if pnu else None

    def names_on_pnu(self) -> dict[str, Counter]:
        """PNU -> 그 필지 건물들의 이름(끝의 동 표기를 뺀 것) 횟수. 동 표기만인 이름은 세지 않는다."""
        if "pnu_names" not in self._cache:
            out = defaultdict(Counter)
            for s in self.triples:
                if kind_of(s) in BUILDING_KINDS and s.startswith(BASE + "/") and not s.endswith("/geometry") and self.shown(s):
                    label = (self.labels.get(s) or "").strip()
                    if label and not UNIT.match(label):
                        base = UNIT_TAIL.sub("", label).strip() or label
                        pnu = self.pnu_of(s)
                        if pnu:
                            out[pnu][base] += 1
            self._cache["pnu_names"] = out
        return self._cache["pnu_names"]

    def owner_of(self, uri: str, prop: str) -> str | None:
        """uri를 prop로 가리키는 주어 하나 (예: 출입구 -> 건물). 보이는 것만, 번호 순 첫째."""
        key = "inv:" + prop
        if key not in self._cache:
            inv = defaultdict(list)
            for s, plist in self.triples.items():
                for p, o in plist:
                    if p.value == JGK + prop:
                        inv[o.value].append(s)
            self._cache[key] = inv
        found = sorted(s for s in self._cache[key].get(uri, []) if self.shown(s))
        return found[0] if found else None

    def address_of(self, uri: str) -> str | None:
        """장소의 도로명주소. 역은 역사 건물, 출입구는 그 건물, 센서는 설치 주소를 쓴다."""
        if uri in self.addrs:
            return self.addrs[uri]
        k = kind_of(uri)
        if k == "station":
            b = self.value(uri, "housedIn")
            return self.addrs.get(b) if b else None
        if k == "entrance":
            b = self.owner_of(uri, "hasEntrance")
            return self.addrs.get(b) if b else None
        if k == "sensor":
            return self.value(uri, "installAddress")
        return None


def _view(store: ox.Store, held: set[str] | None = None, private: set[str] | None = None) -> GraphView:
    triples = defaultdict(list)
    for s, p, o, _ in store.quads_for_pattern(None, None, None, None):
        triples[s.value if hasattr(s, "value") else str(s)].append((p, o))
    labels, comments, addrs = {}, {}, {}
    for s, plist in triples.items():
        for p, o in plist:
            if p.value == RDFS_LABEL and (getattr(o, "language", None) in (None, "ko")):
                labels.setdefault(s, o.value)
            if p.value == RDFS_COMMENT:
                comments.setdefault(s, o.value)
            if p.value == JGK + "roadAddress":
                addrs.setdefault(s, o.value)
    return GraphView(store, triples, held or set(), labels, addrs, comments, private or set(), unfit_floors(triples))


def public_view() -> GraphView:
    """공개 사이트용 뷰. 승인 제안은 근거 자료가 적혀 있고 모두 공개 자료일 때만 얹는다. 모르면 공개하지 않는다.
    국가기본도로 만든 제안은 그 결론(잇기, 모양)까지 공개 사이트에서 빠진다."""
    from src.graph.store import load_store
    from src.review.proposals import ProposalStore
    hold = hold_sources()
    usable = lambda p: bool(p.based_on) and not {JGK + d for d in p.based_on} & hold
    store = load_store(keep=usable)
    given = {p.uri: {JGK + d for d in p.based_on} for p in ProposalStore().load() if usable(p)}
    view = _view(store)
    private = private_businesses(view.triples)
    pub = public_nodes(view.triples, hold, given) - private
    view.held = {s for s in view.triples if s.startswith(BASE + "/")} - pub
    view.private = private
    # 층으로 보기 어려운 층 값(호수일 수 있다)은 공개 페이지, 기계용 원본, 화면 자료 어디에도 내지 않는다
    for s in view.unfit_floor:
        view.triples[s] = [(p, o) for p, o in view.triples[s] if p.value != JGK + "floorInfo"]
    return view


def local_view(store: ox.Store) -> GraphView:
    """로컬 서버용 전체 뷰. 공개 판단을 하지 않는다 (held가 비어 있다)."""
    return _view(store)


def is_change_record(s: str, plist) -> bool:
    """변경 기록(제안, 제안이 바꾼 트리플 기록)인가."""
    return kind_of(s) == "proposal" if s.startswith(BASE + "/") else False


def public_query_store(view: GraphView) -> ox.Store:
    """쿼리용 공개 사본: 보류 주어, 보류 값을 가리키는 트리플, 변경 기록(제안과 그 기록, 제안을 가리키는 영향 표시)을 뺀다."""
    out = ox.Store()
    keep = []
    for s, plist in view.triples.items():
        if s in view.held or is_change_record(s, plist):
            continue
        node = ox.NamedNode(s) if not s.startswith("_:") else ox.BlankNode(s[2:])
        for p, o in plist:
            if p.value == PROV_INFLUENCED:
                continue
            if isinstance(o, ox.NamedNode) and (o.value in view.held or (o.value.startswith(BASE + "/proposal/"))):
                continue
            keep.append(ox.Quad(node, p, o, ox.DefaultGraph()))
    out.bulk_extend(keep)
    return out


def jibun_from_pnu(pnu: str, legal_dong_names: dict[str, str]) -> str | None:
    """PNU 19자리(법정동코드 10, 산 여부 1, 본번 4, 부번 4)를 '등촌동 666-40'으로. 법정동 이름을 모르면 None."""
    if not re.fullmatch(r"[0-9]{19}", pnu or ""):
        return None
    dong = legal_dong_names.get(pnu[:10])
    if not dong:
        return None
    main, sub = int(pnu[11:15]), int(pnu[15:19])
    return f"{dong} {'산 ' if pnu[10] == '2' else ''}{main}{f'-{sub}' if sub else ''}"


def short_address(a: str) -> str:
    """화면용 주소: '서울특별시 '만 뺀다 (구 이름은 남긴다)."""
    return a.removeprefix("서울특별시 ").strip()


def display_name(view: GraphView, uri: str) -> str:
    """화면 이름 (명세 9절). 묶음 계산 없이 동작한다.

    건물: 1) 원천 이름이 동 표기만이 아니면 그 이름 2) 동 표기만이면 같은 PNU에서 가장 많이 쓴 이름, 없으면 주소, 그것도 없으면 지번을 앞에 붙인다
          3) 이름이 없고 주소가 있으면 주소 ('서울특별시 '를 뺀 값) 4) 주소도 없으면 '이름 없는 건물 (등촌동 507-24)'
    다른 종류는 이름, 주소, 종류 이름과 번호 순서로 쓴다. 20자리가 넘는 번호는 화면 이름에 쓰지 않는다.
    """
    label = (view.labels.get(uri) or "").strip()
    kind = kind_of(uri) if uri.startswith(BASE + "/") else ""
    if uri.endswith("/geometry") and uri.startswith(BASE + "/"):
        return display_name(view, uri.removesuffix("/geometry")) + "의 모양"
    if kind in BUILDING_KINDS:
        addr = view.addrs.get(uri)
        if label and not UNIT.match(label):
            return label
        jibun = view.jibun(uri)
        if label:  # 동 표기만
            pnu = view.pnu_of(uri)
            common = view.names_on_pnu().get(pnu) if pnu else None
            front = (common.most_common(1)[0][0] if common else None) or (short_address(addr) if addr else None) or jibun
            unit = label.strip(" .-")
            if front:
                return f"{front} {unit}".strip()
            return f"이름 없는 건물 {unit}".strip()
        if addr:
            return short_address(addr)
        return f"이름 없는 건물 ({jibun})" if jibun else "이름 없는 건물"
    if kind == "parcel":
        return label or view.jibun(uri) or f"필지 {uri.rsplit('/', 1)[-1]}"
    if kind == "entrance":
        if label:
            return label
        b = view.owner_of(uri, "hasEntrance")
        if b:
            return display_name(view, b) + " 출입구"
        return f"건물 출입구 {uri.rsplit('/', 1)[-1]}"
    if kind == "basic-zone":
        return f"우편구역 {uri.rsplit('/', 1)[-1]}"
    if label and not LONG_NO.search(label):
        return label
    addr = view.address_of(uri) if uri.startswith(BASE + "/") else None
    if addr:
        return short_address(addr)
    if uri.startswith(BASE + "/"):
        no = uri.rsplit("/", 1)[-1]
        return KIND_NAMES.get(kind, kind) + ("" if LONG_NO.search(no) else f" {no}")
    if uri.startswith("http://www.wikidata.org/entity/"):
        return "위키데이터 " + uri.rsplit("/", 1)[-1]
    return label or uri.rsplit("#", 1)[-1]
