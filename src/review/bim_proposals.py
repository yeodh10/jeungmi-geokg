"""BIM 모델의 건물을 그래프의 건물에 잇는 제안을 만든다 (BIM 연결 설계 docs/bim.md의 프로토타입).

실행: python -m src.review.bim_proposals <BOT Turtle 파일>
입력: IFC 모델을 BOT(W3C Building Topology Ontology)로 바꾼 Turtle. IFC에서 BOT로는 IFCtoLBD 같은 변환기로 바꾼다.
      건물(bot:Building)에는 다음 가운데 있는 것을 쓴다.
        jgk:buildingMgtNo   IfcBuilding 속성 세트에 적은 건물관리번호
        geo:hasGeometry/geo:asWKT  IfcSite 기준점이나 IfcMapConversion으로 구한 건물 위치 (CRS84 경도, 위도)
        bot:hasStorey       층. 층 이름(rdfs:label)이 B로 시작하면 지하층으로 센다
출력: data/processed/review/proposals.json 에 새 제안 추가 (같은 id가 있으면 건너뛴다)

규칙:
1. 건물관리번호가 있고 그래프에 같은 번호의 건물이 있으면 그 건물에 잇는다. 위치도 있으면 그 건물 모양에서 MAX_DISTANCE_M 안인지 함께 본다.
2. 번호가 없으면 위치 점이 그래프 건물 모양 안에 들어가는 건물이 꼭 하나일 때만 잇는다 (번호 없이 위치로만 찾았다고 근거에 적는다).
3. 둘 다 아니면 제안하지 않고 까닭을 돌려준다.
층 수는 BIM과 대장 값을 견줘 근거에 적는다. 다르면 '확인 필요'로 적고, 사람이 보고 승인한다.
BIM의 층, 공간, 부재는 BIM 그래프에 그대로 두고, 이 그래프에는 건물끼리의 연결(jgk:bimBuilding)만 넣는다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import shapely
from rdflib import RDF, RDFS, Graph, Namespace, URIRef

from src.review.proposals import Change, Proposal, ProposalStore, Term, now

ROOT = Path(__file__).resolve().parents[2]
PROC = ROOT / "data" / "processed"
JGK = Namespace("https://yeodh10.github.io/jeungmi-geokg/def#")
BOT = Namespace("https://w3id.org/bot#")
GEO = Namespace("http://www.opengis.net/ont/geosparql#")
MAX_DISTANCE_M = 30  # 번호로 찾은 건물과 BIM 위치 점 사이 (EPSG:5186). 지하 역사처럼 모양이 점뿐인 건물도 있어 넉넉히 둔다


def bim_buildings(bot: Graph) -> list[dict]:
    """BOT 그래프의 건물마다 번호, 위치, 지상과 지하 층 수."""
    out = []
    for b in sorted(set(bot.subjects(RDF.type, BOT.Building))):
        mgt = next((str(o) for o in bot.objects(b, JGK.buildingMgtNo)), None)
        wkt = next((str(w) for g in bot.objects(b, GEO.hasGeometry) for w in bot.objects(g, GEO.asWKT)), None)
        storeys = [str(next(bot.objects(s, RDFS.label), "")) for s in bot.objects(b, BOT.hasStorey)]
        below = sum(1 for n in storeys if n.upper().startswith("B"))
        out.append({"iri": str(b), "mgt": mgt, "point": shapely.from_wkt(wkt).centroid if wkt else None,
                    "above": len(storeys) - below, "below": below, "label": str(next(bot.objects(b, RDFS.label), ""))})
    return out


def match(bot: Graph, proc: Path = PROC) -> tuple[list[dict], list[dict]]:
    """규칙에 맞는 (BIM 건물, 그래프 건물) 짝과, 잇지 못한 BIM 건물과 그 까닭."""
    buildings = gpd.read_file(proc / "objects.gpkg", layer="building").to_crs("EPSG:5186")
    by_mgt = buildings.dropna(subset=["bldg_mgt_no"]).set_index("bldg_mgt_no")
    polys = buildings[buildings.geom_type != "Point"]
    to5186 = lambda p: gpd.GeoSeries([p], crs="EPSG:4326").to_crs("EPSG:5186").iloc[0]
    found, missed = [], []
    for b in bim_buildings(bot):
        pt = to5186(b["point"]) if b["point"] is not None else None
        if b["mgt"] and b["mgt"] in by_mgt.index:
            t = by_mgt.loc[b["mgt"]]
            dist = round(float(t.geometry.distance(pt)), 1) if pt is not None else None
            found.append({**b, "target": t["uri"], "how": "번호", "distance_m": dist, "target_row": t})
            continue
        if b["mgt"]:
            missed.append({**b, "why": f"건물관리번호 {b['mgt']}인 건물이 그래프에 없다"})
            continue
        if pt is None:
            missed.append({**b, "why": "건물관리번호도 위치도 없다"})
            continue
        inside = polys[polys.contains(pt)]
        if len(inside) != 1:
            missed.append({**b, "why": f"위치 점을 품은 건물 모양이 {len(inside)}개다 (꼭 하나일 때만 잇는다)"})
            continue
        t = inside.iloc[0]
        found.append({**b, "target": t["uri"], "how": "위치", "distance_m": 0.0, "target_row": t})
    return found, missed


def make(row: dict, source: str) -> Proposal:
    t = row["target_row"]
    name = t["label"] if isinstance(t["label"], str) and t["label"] else t["road_address"]
    fa = None if t["floors_above"] != t["floors_above"] else int(t["floors_above"])  # NaN 거르기
    fb = None if t["floors_below"] != t["floors_below"] else int(t["floors_below"])
    floors_same = (fa, fb) == (row["above"], row["below"])
    evidence = []
    if row["how"] == "번호":
        evidence.append(f"BIM 건물에 적힌 건물관리번호 {row['mgt']}이(가) 그래프 건물과 같다")
        if row["distance_m"] is not None:
            ok = row["distance_m"] <= MAX_DISTANCE_M
            evidence.append(f"BIM 위치 점이 그 건물 모양에서 {row['distance_m']}m" + ("" if ok else f" 떨어져 있다 ({MAX_DISTANCE_M}m 넘음, 확인 필요)"))
    else:
        evidence.append("BIM 건물에 건물관리번호가 없어 위치로만 찾았다: 위치 점을 품은 그래프 건물 모양이 하나뿐이다 (확인 필요)")
    evidence.append(f"층 수: BIM 지상 {row['above']}, 지하 {row['below']} / 그래프(대장) 지상 {fa if fa is not None else '없음'}, 지하 {fb if fb is not None else '없음'}"
                    + ("" if floors_same else " (다르다, 확인 필요)"))
    evidence.append("BIM의 층, 공간, 부재는 BIM 그래프에 두고 이 그래프에는 건물끼리의 연결만 넣는다 (docs/bim.md)")
    key = row["iri"].rstrip("/#").rsplit("/", 1)[-1].rsplit("#", 1)[-1]
    return Proposal(
        id=f"rule-bim-link-{key}", created_at=now(), source_kind="rule-candidate",
        source_detail=f"BIM 모델(BOT)의 건물을 그래프 건물에 잇기 (src/review/bim_proposals.py, {source})",
        action="link", target=t["uri"], target_type="Building",
        summary=f"'{name}'에 BIM 모델 건물 {row['label'] or key}을(를) 잇는다",
        evidence=evidence,
        changes=[Change(op="add", s=t["uri"], p=str(JGK.bimBuilding), o=Term(kind="uri", value=row["iri"]))],
        location=tuple(gpd.GeoSeries([t.geometry.centroid], crs="EPSG:5186").to_crs("EPSG:4326").iloc[0].coords[0]),
    )


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        return
    path = Path(sys.argv[1])
    found, missed = match(Graph().parse(path))
    props = [make(r, path.name) for r in found]
    for p in props:
        print(f"[제안] {p.id}: {p.summary}")
    for m in missed:
        print(f"[못 이음] {m['iri']}: {m['why']}")
    print(f"새 제안 {ProposalStore().add_new(props)}건 (짝 {len(found)}, 못 이음 {len(missed)})")


if __name__ == "__main__":
    main()
