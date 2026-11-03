"""자동 갱신: 이전 자료와 새 자료로 만든 그래프를 객체마다 비교해 변경 제안을 만든다.

실행 (새 원본을 받은 뒤):
  python -m src.review.diff_proposals --raw <새 원본 폴더> --label 202610
  python -m src.review.diff_proposals --new <이미 만든 새 객체 폴더> --label 202610

새 원본은 data/processed/snapshots/<label>/ 에 잘라내고 객체로 만든 뒤, 지금 그래프의 바탕(data/processed)과 비교한다.
그래프를 새 자료로 통째로 바꾸지 않고, 바뀐 점을 검토에서 승인해야 반영되게 하기 위해서다.
무엇이 바뀌었는지는 자료끼리 견주어 찾고, 빼는 값은 승인까지 반영한 지금 그래프에서 뽑는다.
승인으로 값이 바뀐 객체에 옛 자료의 값을 빼겠다고 제안하면 그래프에 없는 값이라 영영 승인할 수 없기 때문이다.

만드는 제안:
- create: 새 자료에만 있는 객체
- retire: 새 자료에서 사라진 객체. 지우지 않고 상태를 폐지로 바꾼다 (URI는 남는다)
          객체의 출처 자료가 새 자료에 하나도 없으면(예: 새 원본에 센서 자료가 없음) 사라졌다고 보지 않고 견주지 않는다
- update: 양쪽에 있지만 값이 달라진 객체
- link:   주소 미연결 건물(building-gis)이 사라지고 같은 GIS 번호를 가진 건물관리번호 건물이 새로 생긴 경우
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pyoxigraph as ox
import shapely
from rdflib import RDF, Graph, Literal, URIRef
from rdflib.namespace import DCTERMS

from src.graph import build_graph
from src.graph.store import load_store
from src.model import uri
from src.review.proposals import Change, Proposal, ProposalStore, Term, now

ROOT = Path(__file__).resolve().parents[2]
PROC = ROOT / "data" / "processed"
JGK = build_graph.JGK
GEO = build_graph.GEO
SAME_BUILDING = JGK.sameBuildingAs
OLD_SAME_BUILDING = URIRef("http://www.w3.org/2002/07/owl#sameAs")  # 2026-11-02 앞에 승인한 제안은 이 관계로 이어져 있다
XSD_DATE = "http://www.w3.org/2001/XMLSchema#date"
KIND_KO = {"Building": "건물", "BuildingEntrance": "출입구", "Parcel": "필지", "PublicFacility": "공공시설",
           "Station": "역", "AdminDong": "행정동", "Sensor": "센서"}
OVERWRITES = "승인된 값을 덮어쓴다"
AMBIGUOUS_LINK = "같은 GIS 모양을 쓰는 새 건물이 여럿이다 (확인 필요)"


def to_term(o) -> Term:
    if isinstance(o, URIRef):
        return Term(kind="uri", value=str(o))
    return Term(kind="literal", value=str(o), datatype=str(o.datatype) if o.datatype and not o.language else None,
                lang=o.language)


def now_values(current: ox.Store, s, p) -> set:
    """승인까지 반영한 지금 그래프의 값."""
    return {q.object for q in current.quads_for_pattern(ox.NamedNode(str(s)), ox.NamedNode(str(p)), None, None)}


def fit_to_current(current: ox.Store, before: set[tuple], after: set[tuple]) -> tuple[list[Change], list[Change], list[str]]:
    """자료끼리 비교한 차이를 지금 그래프에 맞춘다. 빼기, 넣기, 승인으로 바뀌어 덮어쓰는 값을 돌려준다.

    옛 자료의 값이 지금 없다면 승인이 그 값을 바꾼 것이다. 그때는 승인이 넣은 값(지금 있는데 옛 자료에 없는 값)을 뺀다.
    이미 지금 그래프에 있는 값은 넣지 않는다.
    """
    removes, adds, overwritten = [], [], []
    for s, p in sorted({(a, b) for a, b, _ in before ^ after}):
        now_set = now_values(current, s, p)
        old = {to_term(c).to_ox() for a, b, c in before if (a, b) == (s, p)}
        new = {to_term(c).to_ox() for a, b, c in after if (a, b) == (s, p)}
        approved = (now_set - old - new) if (old - new) - now_set else set()
        overwritten += [o.value for o in approved]
        removes += [Change(op="remove", s=str(s), p=str(p), o=Term.from_ox(o))
                    for o in sorted(((old - new) & now_set) | approved, key=str)]
        adds += [Change(op="add", s=str(s), p=str(p), o=Term.from_ox(o)) for o in sorted((new - old) - now_set, key=str)]
    return removes, adds, overwritten


def feature_triples(g: Graph, s: URIRef) -> set[tuple]:
    """객체 자신과 그 모양 노드의 트리플."""
    out = set(g.triples((s, None, None)))
    for geom in g.objects(s, GEO.hasGeometry):
        out |= set(g.triples((geom, None, None)))
    return out


def features(g: Graph) -> dict[URIRef, str]:
    """객체 URI -> 종류 이름 (jgk 클래스)"""
    out = {}
    for s in g.subjects(RDF.type, GEO.Feature):
        kinds = [str(c).split("#")[-1] for c in g.objects(s, RDF.type) if str(c).startswith(str(JGK))]
        out[s] = kinds[0] if kinds else "Feature"
    return out


def location(g: Graph, s: URIRef) -> tuple[float, float] | None:
    for geom in g.objects(s, GEO.hasGeometry):
        for wkt in g.objects(geom, GEO.asWKT):
            c = shapely.from_wkt(str(wkt)).centroid
            return (c.x, c.y)
    return None


def label(g: Graph, s: URIRef) -> str:
    for o in g.objects(s, URIRef("http://www.w3.org/2000/01/rdf-schema#label")):
        return str(o)
    return str(s).rsplit("/", 1)[-1]


def short(t) -> str:
    v = str(t[1]).split("#")[-1].rsplit("/", 1)[-1]
    o = str(t[2])
    return f"{v} = {o[:60] + '...' if len(o) > 60 else o}"


def missing_layers(old_dir: Path, new_dir: Path) -> list[str]:
    """옛 자료 objects.gpkg에는 있는데 새 자료에는 없는 층. 예: 새 원본 폴더에 연속지적도를 넣지 않으면 parcel_shape가 빠진다."""
    import pyogrio
    layers = lambda d: set(pyogrio.list_layers(d / "objects.gpkg")[:, 0])
    return sorted(layers(old_dir) - layers(new_dir))


def term_to_rdflib(t: Term):
    if t.kind == "uri":
        return URIRef(t.value)
    return Literal(t.value, lang=t.lang) if t.lang else Literal(t.value, datatype=URIRef(t.datatype) if t.datatype else None)


def with_approved_creations(g_old: Graph, approved: list[Proposal]) -> Graph:
    """옛 자료에 승인으로 새로 만든 객체를 더한 비교 기준. g_old는 바꾸지 않는다.

    승인으로 만든 객체(예: 경계 건물)는 바탕 자료에 없어서, 더하지 않으면 다음 갱신에서 늘 '신규'로 보여
    값이 바뀌어도 새 값만 더하고(옛 값이 남아 규칙에 걸림), 새 자료에서 사라져도 폐지 제안이 나오지 않는다.
    만들 때 넣은 트리플을 옛 값으로 쓰고, 그 뒤 승인으로 바뀐 값은 fit_to_current가 지금 그래프에 맞춘다.
    """
    # 승인으로 새로 생긴 객체: create 제안의 객체와, 다른 제안(예: 있던 건물의 update)에 함께 넣은 새 객체(rdf:type을 더한 주어).
    # 그 객체와 모양 노드에 넣은 트리플을 옛 값으로 쓴다
    adds = []
    for p in approved:
        if p.status != "approved":
            continue
        made = {c.s for c in p.changes if c.op == "add" and c.p == str(RDF.type)} if p.action != "create" else None
        for c in p.changes:
            if c.op == "add" and (made is None or c.s in made or c.s.rsplit("/geometry", 1)[0] in made):
                adds.append(c)
    if not adds:
        return g_old
    g = Graph()
    for t in g_old:
        g.add(t)
    for c in adds:
        g.add((URIRef(c.s), URIRef(c.p), term_to_rdflib(c.o)))
    return g


def diff(g_old: Graph, g_new: Graph, tag: str, source: str, current: ox.Store | None = None,
         approved: list[Proposal] | None = None) -> list[Proposal]:
    """current는 승인까지 반영한 지금 그래프다. 주지 않으면 자료끼리만 견준다.

    approved(제안 기록)를 주면 승인으로 새로 만든 객체도 옛 자료에 있던 것으로 보고 견준다 (with_approved_creations).
    """
    if approved:
        g_old = with_approved_creations(g_old, approved)
    old, new = features(g_old), features(g_new)
    today = date.today().isoformat()
    created_at = now()
    props: list[Proposal] = []

    def make(action, s, kind, summary, evidence, changes, g, flags=()):
        tail = str(s).split("/id/", 1)[-1].replace("/", "_")
        return Proposal(id=f"diff-{tag}-{action}-{tail}", created_at=created_at, source_kind="data-diff",
                        source_detail=source, action=action, target=str(s), target_type=kind,
                        summary=summary, evidence=evidence, changes=changes, location=location(g, s), flags=list(flags))

    def statuses(s, fallback) -> list[Term]:
        """지금 그래프의 상태. 지금 그래프를 주지 않으면 옛 자료의 상태다."""
        return [to_term(fallback)] if current is None else [Term.from_ox(o) for o in sorted(now_values(current, s, JGK.status), key=str)]

    # 새로 생긴 건물이 가져온 GIS 번호 -> 그 건물 URI들 (주소 연결 판단용). 한 GIS 모양을 새 건물 여럿이 함께 쓸 수 있다
    new_by_gis: dict[str, set] = {}
    for s, kind in new.items():
        if s not in old and kind == "Building":
            for geom in g_new.objects(s, GEO.hasGeometry):
                for gid in g_new.objects(geom, JGK.sourceGisBuildingId):
                    new_by_gis.setdefault(str(gid), set()).add(s)

    # 새 출입구는 그 출입구를 가리키는 건물의 제안(새 건물이면 create, 있던 건물이면 update)에 함께 넣는다.
    # 건물은 출입구를 가리키고(jgk:hasEntrance) 출입구는 건물에 이어져야 해서 따로 내면 어느 쪽도 먼저 SHACL을 통과하지 못한다.
    # 두 건물이 한 출입구를 함께 쓰면 URI 순서로 앞 건물에 넣는다 (실행마다 같은 결과)
    bundled = {}
    for s in sorted(s for s, kind in new.items() if kind == "Building"):
        for e in sorted(g_new.objects(s, JGK.hasEntrance)):
            if e in new and e not in old:
                bundled.setdefault(e, s)

    for s, kind in new.items():
        if s in old or s in bundled:
            continue
        entrances = [e for e, b in bundled.items() if b == s]
        triples = feature_triples(g_new, s).union(*(feature_triples(g_new, e) for e in entrances))
        if current is not None:  # 이미 승인으로 들어온 값은 다시 넣지 않는다
            triples = {(a, b, c) for a, b, c in triples if to_term(c).to_ox() not in now_values(current, a, b)}
            if not triples:
                continue
        props.append(make("create", s, kind, f"{KIND_KO.get(kind, kind)} '{label(g_new, s)}' 신규 등록",
                          [f"새 자료에만 있다 ({source})", f"트리플 {len(triples)}개"]
                          + ([f"새 출입구 {len(entrances)}개를 함께 등록한다: " + ", ".join(str(e).rsplit("/", 1)[-1] for e in sorted(entrances))]
                             if entrances else []),
                          [Change(op="add", s=str(a), p=str(b), o=to_term(c)) for a, b, c in sorted(triples)], g_new))

    new_sources = set(g_new.objects(None, DCTERMS.source))
    for s, kind in old.items():
        if s in new:
            continue
        sources = set(g_old.objects(s, DCTERMS.source))
        if sources and not sources & new_sources:
            continue  # 이 객체의 출처 자료를 새로 받지 않았다. 없어진 것이 아니라 견줄 자료가 없는 것이다
        gis_ids = [str(o) for o in g_old.objects(s, JGK.gisBuildingId)]
        candidates = set().union(*(new_by_gis.get(i, set()) for i in gis_ids))
        linked_now = set() if current is None else now_values(current, s, SAME_BUILDING) | now_values(current, s, OLD_SAME_BUILDING)
        status_now = statuses(s, next(g_old.objects(s, JGK.status), JGK.Active))
        if any(URIRef(t.value) in new for t in linked_now):
            continue  # 승인으로 이미 새 자료에 있는 건물과 이어졌다. 이 주소 미연결 건물이 새 자료에서 빠지는 것은 당연하다
        ambiguous = len(candidates) > 1
        linked_to = min(candidates) if candidates else None  # 여럿이면 URI 순서로 하나를 고르고 확인 필요로 둔다
        if linked_to is not None:
            linked = Term(kind="uri", value=str(JGK.AddressLinked))
            changes = [Change(op="remove", s=str(s), p=str(JGK.status), o=o) for o in status_now if o != linked]
            if linked not in status_now:
                changes.append(Change(op="add", s=str(s), p=str(JGK.status), o=linked))
            if ox.NamedNode(str(linked_to)) not in linked_now:
                changes.append(Change(op="add", s=str(s), p=str(SAME_BUILDING), o=Term(kind="uri", value=str(linked_to))))
            if not changes:
                continue  # 이미 승인으로 이어져 있다
            others = sorted(str(c).rsplit("/", 1)[-1] for c in candidates if c != linked_to)
            props.append(make("link", s, kind, f"주소 미연결 건물 '{label(g_old, s)}'을(를) 새 건물관리번호 건물과 잇는다",
                              [f"GIS 번호 {gis_ids[0]}가 새 자료에서 {str(linked_to).rsplit('/', 1)[-1]} 건물의 모양으로 쓰인다",
                               "기존 URI는 남기고 상태를 '주소 연결됨'으로 바꾼다 (docs/uri-policy.md 건물 규칙 4)"]
                              + ([f"같은 GIS 모양을 새 건물 {', '.join(others)}도 쓴다. 한 건물에만 이을 수 있어 URI 순서로 골랐다"] if ambiguous else []),
                              changes, g_old, [AMBIGUOUS_LINK] if ambiguous else []))
            continue
        if Term(kind="uri", value=str(JGK.Retired)) in status_now:
            continue  # 이미 승인으로 폐지됐다
        changes = [Change(op="remove", s=str(s), p=str(JGK.status), o=o) for o in status_now]
        changes += [Change(op="add", s=str(s), p=str(JGK.status), o=Term(kind="uri", value=str(JGK.Retired))),
                    Change(op="add", s=str(s), p=str(JGK.retiredOn), o=Term(kind="literal", value=today, datatype=XSD_DATE))]
        props.append(make("retire", s, kind, f"{KIND_KO.get(kind, kind)} '{label(g_old, s)}' 폐지",
                          [f"새 자료에서 사라졌다 ({source})", "URI와 기존 속성은 남기고 상태만 폐지로 바꾼다"],
                          changes, g_old))

    for s, kind in new.items():
        if s not in old:
            continue
        before, after = feature_triples(g_old, s), feature_triples(g_new, s)
        removed, added = before - after, after - before
        if not removed and not added:
            continue
        evidence = [f"이전: {short(t)}" for t in sorted(removed)] + [f"새로: {short(t)}" for t in sorted(added)]
        if current is None:
            removes = [Change(op="remove", s=str(a), p=str(b), o=to_term(c)) for a, b, c in sorted(removed)]
            adds = [Change(op="add", s=str(a), p=str(b), o=to_term(c)) for a, b, c in sorted(added)]
            overwritten = []
        else:
            removes, adds, overwritten = fit_to_current(current, before, after)
            if not removes and not adds:
                continue  # 승인으로 이미 새 자료와 같아졌다
        evidence += [f"승인으로 바뀐 값을 덮어쓴다 (확인 필요): {v[:60]}" for v in overwritten]
        entrances = sorted(e for e, b in bundled.items() if b == s)
        if entrances:
            more = set().union(*(feature_triples(g_new, e) for e in entrances))
            if current is not None:
                more = {(a, b, c) for a, b, c in more if to_term(c).to_ox() not in now_values(current, a, b)}
            adds += [Change(op="add", s=str(a), p=str(b), o=to_term(c)) for a, b, c in sorted(more)]
            evidence.append(f"새 출입구 {len(entrances)}개를 함께 등록한다: " + ", ".join(str(e).rsplit("/", 1)[-1] for e in entrances))
        props.append(make("update", s, kind, f"{KIND_KO.get(kind, kind)} '{label(g_new, s)}' 정보 변경", evidence,
                          removes + adds, g_new, [OVERWRITES] if overwritten else []))
    return props


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="새 자료 이름 (예: 202610)")
    ap.add_argument("--raw", type=Path, help="새 원본 폴더 (data/raw와 같은 구조)")
    ap.add_argument("--new", type=Path, help="이미 객체로 만든 새 자료 폴더")
    ap.add_argument("--old", type=Path, default=PROC)
    ap.add_argument("--allow-missing", action="store_true", help="옛 자료에 있는 객체 층이 새 자료에 없어도 견준다")
    args = ap.parse_args()

    new_dir = args.new or PROC / "snapshots" / args.label
    if args.raw:
        from src.ingest import clip_area
        from src.model import build_objects
        new_dir.mkdir(parents=True, exist_ok=True)
        clip_area.main(args.raw, new_dir, edge_buildings=True)  # 새 자료는 경계 건물 규칙으로 자른다 (clip_area.main)
        build_objects.main(new_dir)

    missing = missing_layers(args.old, new_dir)
    if missing and not args.allow_missing:
        raise SystemExit(f"새 자료에 옛 자료의 객체 층이 없다: {', '.join(missing)}. 빠진 층의 값이 모두 사라졌다고 제안하게 되므로 멈춘다. "
                         "그 원본도 새 원본 폴더에 넣거나, 일부러 뺐다면 --allow-missing을 쓴다")
    on_base = args.old == PROC  # 지금 그래프의 바탕이 아닌 자료와 견줄 때는 자료끼리만 본다
    current = load_store() if on_base else None
    approved = ProposalStore().load() if on_base else None
    props = diff(build_graph.build(args.old), build_graph.build(new_dir), args.label, f"자료 {args.label}", current, approved)
    added = ProposalStore().add_new(props)
    counts = {}
    for p in props:
        counts[p.action] = counts.get(p.action, 0) + 1
    print(f"제안 {len(props)}건 {counts}, 새로 추가 {added}건")
    for pid in added.differ:
        print(f"건너뜀 {pid}: 같은 id의 제안이 이미 있는데 내용이 다르다")


if __name__ == "__main__":
    main()
