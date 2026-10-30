"""상호운용성 검사: jgk 어휘를 모르는 시스템도 이 그래프를 읽고 쓸 수 있는지 잰다.

실행: python -m src.graph.interop            (바깥 주소 확인까지, 인터넷 필요)
      python -m src.graph.interop --offline  (바깥 주소 확인은 건너뛴다)
출력: data/processed/interop_report.json, interop_report.md

1. 바깥 어휘 주소: alignment.ttl의 schema.org, INSPIRE, 위키데이터 주소가 실제로 열리는지 (없는 이름은 404를 주는 곳만 쓴다)
2. 바깥 어휘로만 묻기: 그래프 일부에 RDFS 추론(owlrl)을 돌린 뒤 schema.org와 GeoSPARQL 이름만으로 쿼리한다
3. 형식 바꾸기: Turtle을 JSON-LD와 N-Triples로 쓰고 다시 읽어 트리플이 하나도 안 바뀌는지
4. GeoSPARQL 모양: 모든 WKT가 읽히고 경도, 위도 순서(CRS84)로 한국 범위 안에 있는지
5. 위키데이터와 견주기: 증미역(owl:sameAs wd:Q59989)의 좌표와 이름을 위키데이터 값과 견준다
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

import pyproj
import shapely
from rdflib import OWL, RDF, RDFS, Graph, Namespace, URIRef

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
TTL = ROOT / "data" / "processed" / "jeungmi_geokg.ttl"
OUT = ROOT / "data" / "processed"
UA = "jeungmi-geokg/0.1 (https://github.com/yeodh10/jeungmi-geokg)"

JGK = Namespace("https://yeodh10.github.io/jeungmi-geokg/def#")
GEO = Namespace("http://www.opengis.net/ont/geosparql#")
SCHEMA = Namespace("https://schema.org/")
WD = "http://www.wikidata.org/entity/"
INSPIRE = "http://inspire.ec.europa.eu/featureconcept/"
STATION = URIRef("https://yeodh10.github.io/jeungmi-geokg/id/station/1150010200106660040000001")


def fetch(url: str, timeout: int = 30) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def external_iris(align: Graph) -> list[dict]:
    """정렬 파일에 나오는 바깥 주소를 하나씩 열어 본다. 위키데이터는 항목 자료(JSON)에서 이름까지 읽는다."""
    iris = sorted({str(o) for o in align.objects() if isinstance(o, URIRef)} | {str(s) for s in align.subjects() if isinstance(s, URIRef)})
    out = []
    for iri in iris:
        if iri.startswith(str(SCHEMA)):
            code, _ = fetch(iri)
            out.append({"iri": iri, "where": "schema.org", "status": code, "ok": code == 200})
        elif iri.startswith(INSPIRE):
            code, _ = fetch(iri.replace("http://", "https://"))
            out.append({"iri": iri, "where": "INSPIRE", "status": code, "ok": code == 200})
        elif iri.startswith(WD):
            qid = iri.rsplit("/", 1)[-1]
            code, body = fetch(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json")
            label = ""
            if code == 200:
                labels = json.loads(body)["entities"][qid]["labels"]
                label = (labels.get("ko") or labels.get("en") or {}).get("value", "")
            out.append({"iri": iri, "where": "위키데이터", "status": code, "ok": code == 200 and bool(label), "label": label})
        time.sleep(0.2)  # 바깥 서버에 몰아서 묻지 않는다
    return out


def outsider_view(data: Graph, onto: Graph, align: Graph) -> dict:
    """역, 역사 건물과 그 출입구, 법정동, 건물 몇 동만 떼어 RDFS 추론을 돌리고 바깥 어휘로만 묻는다."""
    import owlrl

    sub = Graph()
    seeds = {STATION}
    seeds |= set(data.objects(STATION, JGK.housedIn))
    for b in list(seeds):
        seeds |= set(data.objects(b, JGK.hasEntrance)) | set(data.objects(b, JGK.inLegalDong)) | set(data.objects(b, JGK.within))
    # 건물은 도로명주소가 있는 것을 번호순으로 50동 (실행할 때마다 같은 표본)
    seeds |= set(sorted(set(data.subjects(RDF.type, JGK.Building)) & set(data.subjects(JGK.roadAddress, None)))[:50])
    seeds |= set(data.subjects(RDF.type, JGK.Sensor))
    for s in seeds:
        for p, o in data.predicate_objects(s):
            sub.add((s, p, o))
    base = len(sub)
    g = sub + onto + align
    owlrl.DeductiveClosure(owlrl.RDFS_Semantics).expand(g)
    q = {
        "지하철역(schema:SubwayStation)과 위키데이터":"""PREFIX schema: <https://schema.org/> PREFIX owl: <http://www.w3.org/2002/07/owl#>
            SELECT ?s ?name ?wd WHERE { ?s a schema:SubwayStation ; <http://www.w3.org/2000/01/rdf-schema#label> ?name . OPTIONAL { ?s owl:sameAs ?wd } }""",
        "장소(schema:Place) 수": "PREFIX schema: <https://schema.org/> SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE { ?s a schema:Place }",
        "주소(schema:address)가 있는 장소 수": "PREFIX schema: <https://schema.org/> SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE { ?s a schema:Place ; schema:address ?a }",
        "식별자(schema:identifier)가 있는 장소 수": "PREFIX schema: <https://schema.org/> SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE { ?s a schema:Place ; schema:identifier ?a }",
        "역이 속한 장소(schema:containedInPlace)": """PREFIX schema: <https://schema.org/>
            SELECT ?place WHERE { <%s> schema:containedInPlace ?place }""" % STATION,
        "모양이 있는 장소 (GeoSPARQL만으로)": """PREFIX schema: <https://schema.org/> PREFIX geo: <http://www.opengis.net/ont/geosparql#>
            SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE { ?s a schema:Place ; geo:hasGeometry ?g }""",
        "센서(sosa:Sensor)와 관측 항목": """PREFIX sosa: <http://www.w3.org/ns/sosa/>
            SELECT ?s (COUNT(?p) AS ?n) WHERE { ?s a sosa:Sensor ; sosa:observes ?p } GROUP BY ?s ORDER BY ?s""",
    }
    answers = {}
    for name, text in q.items():
        rows = [[str(v) for v in r] for r in g.query(text)]
        answers[name] = rows
    return {"subgraph_triples": base, "after_inference": len(g), "answers": answers}


def round_trip(data: Graph) -> dict:
    """JSON-LD와 N-Triples로 쓰고 다시 읽어 트리플 집합이 같은지 본다 (빈 노드가 없어서 집합으로 견줄 수 있다)."""
    original = set(data)
    out = {}
    for fmt in ("json-ld", "nt"):
        t = time.perf_counter()
        text = data.serialize(format=fmt, encoding="utf-8")
        back = Graph().parse(data=text, format=fmt)
        same = set(back) == original
        out[fmt] = {"bytes": len(text), "triples": len(back), "same": same, "seconds": round(time.perf_counter() - t, 1)}
    return out


def geosparql_shapes(data: Graph) -> dict:
    """모든 WKT가 읽히고, 좌표가 경도(124~132), 위도(33~39) 순서인지."""
    total = bad_parse = bad_order = 0
    for w in data.objects(None, GEO.asWKT):
        total += 1
        try:
            g = shapely.from_wkt(str(w))
        except Exception:  # noqa: BLE001 - 읽히지 않는 WKT를 센다
            bad_parse += 1
            continue
        x0, y0, x1, y1 = g.bounds
        if not (124 <= x0 <= x1 <= 132 and 33 <= y0 <= y1 <= 39):
            bad_order += 1
    no_geom = sum(1 for s in set(data.subjects(RDF.type, GEO.Feature)) if (s, GEO.hasDefaultGeometry, None) not in data)
    return {"wkt": total, "unparsable": bad_parse, "outside_korea_or_swapped": bad_order, "features_without_geometry": no_geom}


def wikidata_station(data: Graph, align: Graph) -> dict:
    qid = next(str(o) for o in align.objects(STATION, OWL.sameAs)).rsplit("/", 1)[-1]
    code, body = fetch(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json")
    if code != 200:
        return {"qid": qid, "status": code}
    ent = json.loads(body)["entities"][qid]
    coord = ent["claims"]["P625"][0]["mainsnak"]["datavalue"]["value"]
    code_908 = [c["mainsnak"]["datavalue"]["value"] for c in ent["claims"].get("P296", [])]
    ours_wkt = next(data.objects(next(data.objects(STATION, GEO.hasDefaultGeometry)), GEO.asWKT))
    ours = shapely.from_wkt(str(ours_wkt))
    to5186 = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:5186", always_xy=True)
    a = to5186.transform(ours.x, ours.y)
    b = to5186.transform(coord["longitude"], coord["latitude"])
    ours_name = next(str(o) for o in data.objects(STATION, RDFS.label))
    return {"qid": qid, "wikidata_label_ko": ent["labels"].get("ko", {}).get("value", ""), "our_label": ours_name,
            "wikidata_lonlat": [coord["longitude"], coord["latitude"]], "our_lonlat": [ours.x, ours.y],
            "distance_m": round(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5, 1), "station_code": code_908}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="바깥 주소와 위키데이터 확인을 건너뛴다")
    args = ap.parse_args()
    data = Graph().parse(TTL)
    onto = Graph().parse(HERE / "ontology.ttl")
    align = Graph().parse(HERE / "alignment.ttl")
    report = {"triples": len(data), "alignment_triples": len(align)}
    report["outsider"] = outsider_view(data, onto, align)
    report["round_trip"] = round_trip(data)
    report["geosparql"] = geosparql_shapes(data)
    if not args.offline:
        report["external"] = external_iris(align)
        report["wikidata_station"] = wikidata_station(data, align)
    (OUT / "interop_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# 상호운용성 검사 결과", "", f"- 그래프 트리플 {report['triples']:,}개, 정렬 트리플 {report['alignment_triples']}개", ""]
    if "external" in report:
        ok = sum(r["ok"] for r in report["external"])
        lines += [f"## 바깥 어휘 주소 ({ok}/{len(report['external'])} 열림)", "", "| 주소 | 어디 | 응답 | 이름 |", "|---|---|---|---|"]
        lines += [f"| {r['iri']} | {r['where']} | {r['status']} | {r.get('label', '')} |" for r in report["external"]]
        lines.append("")
    o = report["outsider"]
    lines += [f"## 바깥 어휘로만 묻기 (그래프 일부 {o['subgraph_triples']:,}개, RDFS 추론 뒤 {o['after_inference']:,}개)", ""]
    for name, rows in o["answers"].items():
        lines.append(f"- {name}: " + ("; ".join(", ".join(r) for r in rows[:5]) or "없음"))
    lines += ["", "## 형식 바꾸기", "", "| 형식 | 크기(바이트) | 다시 읽은 트리플 | 같은가 | 초 |", "|---|---|---|---|---|"]
    lines += [f"| {k} | {v['bytes']:,} | {v['triples']:,} | {'같음' if v['same'] else '다름'} | {v['seconds']} |" for k, v in report["round_trip"].items()]
    gs = report["geosparql"]
    lines += ["", "## GeoSPARQL 모양", "", f"- WKT {gs['wkt']:,}개, 읽히지 않음 {gs['unparsable']}, 범위 밖이거나 경도, 위도가 뒤바뀜 {gs['outside_korea_or_swapped']}",
              f"- 모양이 없는 geo:Feature {gs['features_without_geometry']:,}개 (행정동, 필지처럼 경계 자료를 아직 넣지 않은 것)"]
    if "wikidata_station" in report:
        w = report["wikidata_station"]
        lines += ["", "## 위키데이터와 견주기", "", f"- {w['qid']}: 위키데이터 이름 '{w.get('wikidata_label_ko')}', 우리 이름 '{w.get('our_label')}'",
                  f"- 좌표 차이 {w.get('distance_m')}m (위키데이터 {w.get('wikidata_lonlat')}, 우리 {w.get('our_lonlat')}), 위키데이터 역 번호 {w.get('station_code')}"]
    (OUT / "interop_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("external",)}, ensure_ascii=False)[:1500])


if __name__ == "__main__":
    main()
