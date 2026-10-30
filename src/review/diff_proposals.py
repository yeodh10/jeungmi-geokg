"""자동 갱신: 이전 자료와 새 자료로 만든 그래프를 객체마다 비교해 변경 제안을 만든다.

실행 (새 원본을 받은 뒤):
  python -m src.review.diff_proposals --raw <새 원본 폴더> --label 202610
  python -m src.review.diff_proposals --new <이미 만든 새 객체 폴더> --label 202610

새 원본은 data/processed/snapshots/<label>/ 에 잘라내고 객체로 만든 뒤, 지금 그래프의 바탕(data/processed)과 비교한다.
그래프를 새 자료로 통째로 바꾸지 않고, 바뀐 점을 사람이 승인해야 반영되게 하기 위해서다.

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

import shapely
from rdflib import RDF, Graph, Literal, URIRef
from rdflib.namespace import DCTERMS

from src.graph import build_graph
from src.model import uri
from src.review.proposals import Change, Proposal, ProposalStore, Term, now

ROOT = Path(__file__).resolve().parents[2]
PROC = ROOT / "data" / "processed"
JGK = build_graph.JGK
GEO = build_graph.GEO
OWL_SAMEAS = URIRef("http://www.w3.org/2002/07/owl#sameAs")
XSD_DATE = "http://www.w3.org/2001/XMLSchema#date"
KIND_KO = {"Building": "건물", "BuildingEntrance": "출입구", "Parcel": "필지", "PublicFacility": "공공시설",
           "Station": "역", "AdminDong": "행정동", "Sensor": "센서"}


def to_term(o) -> Term:
    if isinstance(o, URIRef):
        return Term(kind="uri", value=str(o))
    return Term(kind="literal", value=str(o), datatype=str(o.datatype) if o.datatype else None,
                lang=o.language)


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


def diff(g_old: Graph, g_new: Graph, tag: str, source: str) -> list[Proposal]:
    old, new = features(g_old), features(g_new)
    today = date.today().isoformat()
    created_at = now()
    props: list[Proposal] = []

    def make(action, s, kind, summary, evidence, changes, g):
        tail = str(s).split("/id/", 1)[-1].replace("/", "_")
        return Proposal(id=f"diff-{tag}-{action}-{tail}", created_at=created_at, source_kind="data-diff",
                        source_detail=source, action=action, target=str(s), target_type=kind,
                        summary=summary, evidence=evidence, changes=changes, location=location(g, s))

    # 새로 생긴 건물이 가져온 GIS 번호 -> 그 건물 URI (주소 연결 판단용)
    new_by_gis = {}
    for s, kind in new.items():
        if s not in old and kind == "Building":
            for geom in g_new.objects(s, GEO.hasGeometry):
                for gid in g_new.objects(geom, JGK.sourceGisBuildingId):
                    new_by_gis[str(gid)] = s

    for s, kind in new.items():
        if s in old:
            continue
        triples = feature_triples(g_new, s)
        props.append(make("create", s, kind, f"{KIND_KO.get(kind, kind)} '{label(g_new, s)}' 신규 등록",
                          [f"새 자료에만 있다 ({source})", f"트리플 {len(triples)}개"],
                          [Change(op="add", s=str(a), p=str(b), o=to_term(c)) for a, b, c in sorted(triples)], g_new))

    new_sources = set(g_new.objects(None, DCTERMS.source))
    for s, kind in old.items():
        if s in new:
            continue
        sources = set(g_old.objects(s, DCTERMS.source))
        if sources and not sources & new_sources:
            continue  # 이 객체의 출처 자료를 새로 받지 않았다. 없어진 것이 아니라 견줄 자료가 없는 것이다
        gis_ids = [str(o) for o in g_old.objects(s, JGK.gisBuildingId)]
        linked_to = next((new_by_gis[i] for i in gis_ids if i in new_by_gis), None)
        if linked_to is not None:
            changes = [Change(op="remove", s=str(s), p=str(JGK.status), o=Term(kind="uri", value=str(JGK.AddressUnlinked))),
                       Change(op="add", s=str(s), p=str(JGK.status), o=Term(kind="uri", value=str(JGK.AddressLinked))),
                       Change(op="add", s=str(s), p=str(OWL_SAMEAS), o=Term(kind="uri", value=str(linked_to)))]
            props.append(make("link", s, kind, f"주소 미연결 건물 '{label(g_old, s)}'을(를) 새 건물관리번호 건물과 잇는다",
                              [f"GIS 번호 {gis_ids[0]}가 새 자료에서 {str(linked_to).rsplit('/', 1)[-1]} 건물의 모양으로 쓰인다",
                               "기존 URI는 남기고 상태를 '주소 연결됨'으로 바꾼다 (docs/uri-policy.md 건물 규칙 4)"],
                              changes, g_old))
            continue
        status = next(g_old.objects(s, JGK.status), JGK.Active)
        changes = [Change(op="remove", s=str(s), p=str(JGK.status), o=to_term(status)),
                   Change(op="add", s=str(s), p=str(JGK.status), o=Term(kind="uri", value=str(JGK.Retired))),
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
        props.append(make("update", s, kind, f"{KIND_KO.get(kind, kind)} '{label(g_new, s)}' 정보 변경",
                          [f"이전: {short(t)}" for t in sorted(removed)] + [f"새로: {short(t)}" for t in sorted(added)],
                          [Change(op="remove", s=str(a), p=str(b), o=to_term(c)) for a, b, c in sorted(removed)]
                          + [Change(op="add", s=str(a), p=str(b), o=to_term(c)) for a, b, c in sorted(added)], g_new))
    return props


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="새 자료 이름 (예: 202610)")
    ap.add_argument("--raw", type=Path, help="새 원본 폴더 (data/raw와 같은 구조)")
    ap.add_argument("--new", type=Path, help="이미 객체로 만든 새 자료 폴더")
    ap.add_argument("--old", type=Path, default=PROC)
    args = ap.parse_args()

    new_dir = args.new or PROC / "snapshots" / args.label
    if args.raw:
        from src.ingest import clip_area
        from src.model import build_objects
        new_dir.mkdir(parents=True, exist_ok=True)
        clip_area.main(args.raw, new_dir)
        build_objects.main(new_dir)

    props = diff(build_graph.build(args.old), build_graph.build(new_dir), args.label, f"자료 {args.label}")
    added = ProposalStore().add_new(props)
    counts = {}
    for p in props:
        counts[p.action] = counts.get(p.action, 0) + 1
    print(f"제안 {len(props)}건 {counts}, 새로 추가 {added}건")


if __name__ == "__main__":
    main()
