"""SHACL로 그래프의 데이터 규칙을 검사한다.

실행: python -m src.graph.validate
출력: data/processed/validation_report.md (규칙마다 어긴 개수와 예시)

검토 화면은 check_proposal()로 제안을 승인하기 전에, 그 제안을 반영한 뒤 바뀌는 객체가 규칙을 지키는지 본다.
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path

import pyoxigraph as ox
from pyshacl import validate
from rdflib import Graph, Namespace, URIRef

from src.graph.store import load_store

ROOT = Path(__file__).resolve().parents[2]
SHAPES = Path(__file__).resolve().parent / "shapes.ttl"
OUT = ROOT / "data" / "processed" / "validation_report.md"
SH = Namespace("http://www.w3.org/ns/shacl#")


def to_rdflib(store: ox.Store) -> Graph:
    data = store.dump(format=ox.RdfFormat.N_TRIPLES, from_graph=ox.DefaultGraph())
    g = Graph()
    g.parse(data=data.decode("utf-8"), format="nt")
    return g


def shapes() -> Graph:
    g = Graph()
    g.parse(SHAPES, format="turtle")
    return g


def run(data: Graph) -> tuple[bool, list[dict]]:
    conforms, results, _ = validate(data, shacl_graph=shapes(), inference="none", allow_warnings=True)
    out = []
    for r in results.subjects(predicate=SH.resultSeverity):
        msg = results.value(r, SH.resultMessage)
        out.append({"focus": str(results.value(r, SH.focusNode)), "path": str(results.value(r, SH.resultPath) or ""),
                    "shape": str(results.value(r, SH.sourceShape) or ""), "message": str(msg or ""),
                    "value": str(results.value(r, SH.value) or "")})
    return bool(conforms), out


RDF_TYPE = ox.NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
HAS_GEOMETRY = ox.NamedNode("http://www.opengis.net/ont/geosparql#hasGeometry")
HAS_ENTRANCE = ox.NamedNode("https://yeodh10.github.io/jeungmi-geokg/def#hasEntrance")


def check_proposal(store: ox.Store, proposal) -> tuple[bool, list[dict]]:
    """제안을 지금 그래프의 복사본에 반영해 보고, 대상 객체와 그 모양만 규칙으로 검사한다.

    전체를 다시 검사하면 1분이 넘게 걸려서, 대상 객체 둘레(자기 트리플, 모양, 이어진 객체의 종류)만 떼어 검사한다.
    """
    trial = ox.Store()
    trial.bulk_extend(store.quads_for_pattern(None, None, None, None))
    trial.update(proposal.sparql_update())
    target = ox.NamedNode(proposal.target)
    focus = [target] + [q.object for q in trial.quads_for_pattern(target, HAS_GEOMETRY, None, None)]
    lines = set()
    for f in focus:
        for q in trial.quads_for_pattern(f, None, None, None):
            lines.add(f"{q.subject} {q.predicate} {q.object} .")
            if isinstance(q.object, ox.NamedNode):  # sh:class 검사를 위해 이어진 객체의 종류도 넣는다
                lines |= {f"{t.subject} {t.predicate} {t.object} ." for t in trial.quads_for_pattern(q.object, RDF_TYPE, None, None)}
    lines |= {f"{q.subject} {q.predicate} {q.object} ." for q in trial.quads_for_pattern(None, HAS_ENTRANCE, target, None)}
    sub = Graph()
    sub.parse(data="\n".join(lines), format="nt")
    conforms, results = run(sub)
    keep = {str(f.value) for f in focus}
    results = [r for r in results if r["focus"] in keep]
    return not results, results


def main() -> None:
    t0 = time.perf_counter()
    store = load_store()
    data = to_rdflib(store)
    conforms, results = run(data)
    took = time.perf_counter() - t0
    by_msg = Counter(r["message"] for r in results)
    examples = defaultdict(list)
    for r in results:
        if len(examples[r["message"]]) < 3:
            examples[r["message"]].append(r["focus"].replace("https://yeodh10.github.io/jeungmi-geokg/id/", "id:"))
    lines = ["# SHACL 검사 결과", "", f"- 트리플 {len(data):,}개, 규칙 파일 `src/graph/shapes.ttl`, {took:.0f}초",
             f"- 결과: {'모든 규칙 통과' if conforms else f'어긴 곳 {len(results)}건'}", ""]
    if results:
        lines += ["| 규칙 | 건수 | 예시 |", "|---|---|---|"]
        lines += [f"| {m} | {n} | {', '.join(examples[m])} |" for m, n in by_msg.most_common()]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"conforms": conforms, "violations": len(results), "by_message": by_msg, "seconds": round(took)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
