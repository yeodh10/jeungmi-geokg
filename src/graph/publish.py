"""URI를 실제로 열리는 주소로: 객체마다 정적 HTML 페이지와 Turtle 파일을 만든다.

실행: python -m src.graph.publish
출력: data/processed/site/ (GitHub Pages에 그대로 올리면 https://yeodh10.github.io/jeungmi-geokg/ 아래 URI가 열린다)
  id/{종류}/{번호}/index.html  사람이 보는 페이지 (속성은 어휘의 한국어 이름표로)
  id/{종류}/{번호}/index.ttl   그 URI가 주어인 트리플 (N-Triples 줄로 쓴 Turtle)
  def/index.html, def/ontology.ttl  어휘

이용 조건을 아직 확인하지 못한 출처(config/area.yaml의 publish.hold_sources)에서 온 객체는 페이지를 만들지 않고,
다른 페이지에서도 그 객체로 가는 연결을 뺀다.
"""
from __future__ import annotations

import html
import json
import shutil
from collections import defaultdict
from pathlib import Path

import yaml

from src.graph.store import ONTOLOGY, load_store
from src.model.uri import BASE

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "processed" / "site"
SITE = BASE.rsplit("/id", 1)[0]  # https://yeodh10.github.io/jeungmi-geokg
JGK = SITE + "/def#"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
DCT_SOURCE = "http://purl.org/dc/terms/source"
EXT_LABEL = {
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#type": "종류", RDFS_LABEL: "이름", DCT_SOURCE: "출처",
    "http://www.opengis.net/ont/geosparql#hasGeometry": "모양", "http://www.opengis.net/ont/geosparql#hasDefaultGeometry": "기본 모양",
    "http://www.opengis.net/ont/geosparql#asWKT": "WKT", "http://www.w3.org/2002/07/owl#sameAs": "같은 객체",
    "http://www.w3.org/ns/prov#wasInfluencedBy": "반영된 변경 제안", "http://www.opengis.net/ont/geosparql#Feature": "공간 객체(Feature)",
    "http://www.opengis.net/ont/geosparql#Geometry": "모양(Geometry)",
}
CSS = """body{font-family:"Pretendard","Malgun Gothic",system-ui,sans-serif;max-width:860px;margin:24px auto;padding:0 16px;color:#1d2330;background:#fff}
h1{font-size:20px;margin:0 0 4px}.uri{color:#667085;font-size:13px;word-break:break-all}table{width:100%;border-collapse:collapse;margin-top:14px;font-size:14px}
td{border-top:1px solid #e3e6eb;padding:6px;vertical-align:top;word-break:break-all}td:first-child{color:#667085;width:30%}a{color:#2457d6}
.wkt{font-family:monospace;font-size:12px;max-height:6em;overflow:auto;display:block}footer{margin-top:24px;color:#667085;font-size:12px}
@media (prefers-color-scheme:dark){body{background:#14171c;color:#e6e8eb}td{border-color:#2b313a}a{color:#7aa2ff}}"""


def page(title: str, uri: str, rows: list[tuple[str, str]], extra: str = "") -> str:
    body = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows)
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><link rel="alternate" type="text/turtle" href="index.ttl"><style>{CSS}</style></head><body>
<h1>{html.escape(title)}</h1><div class="uri">{html.escape(uri)}</div><table>{body}</table>{extra}
<footer>증미역 공간 지식 그래프, <a href="index.ttl">Turtle</a>, <a href="{SITE}/">처음으로</a></footer></body></html>"""


def term_nt(t) -> str:
    return str(t)  # pyoxigraph 항은 N-Triples 표기로 바뀐다


def hold_sources() -> set[str]:
    """공개를 보류한 출처의 URI. 지도 화면 API(src.review.app)도 같은 목록으로 공개 여부를 판단한다."""
    with open(ROOT / "config" / "area.yaml", encoding="utf-8") as f:
        return {JGK + s for s in (yaml.safe_load(f).get("publish") or {}).get("hold_sources") or []}


def main() -> None:
    hold = hold_sources()
    store = load_store()

    triples = defaultdict(list)
    for s, p, o, _ in store.quads_for_pattern(None, None, None, None):
        triples[s.value if hasattr(s, "value") else str(s)].append((p, o))
    labels = {}
    for s, plist in triples.items():
        for p, o in plist:
            if p.value == RDFS_LABEL and (getattr(o, "language", None) in (None, "ko")):
                labels.setdefault(s, o.value)
    held = {s for s, plist in triples.items() if any(p.value == DCT_SOURCE and o.value in hold for p, o in plist)}
    held |= {f"{s}/geometry" for s in held}

    def show(o) -> str:
        v = o.value
        if v in held:
            return "(공개 보류 중인 객체)"
        if v.startswith(SITE):
            return f'<a href="{html.escape(v)}">{html.escape(labels.get(v) or EXT_LABEL.get(v) or v.replace(SITE, ""))}</a>'
        if hasattr(o, "datatype") and o.datatype and o.datatype.value.endswith("wktLiteral"):
            return f'<span class="wkt">{html.escape(v)}</span>'
        return html.escape(labels.get(v) or EXT_LABEL.get(v) or v)

    if OUT.exists():
        shutil.rmtree(OUT)
    inbound = defaultdict(list)
    for s, plist in triples.items():
        for p, o in plist:
            if o.value.startswith(BASE) and s.startswith(BASE) and not p.value.endswith("Geometry"):
                inbound[o.value].append((p.value, s))

    n = 0
    for s, plist in triples.items():
        if not s.startswith(BASE + "/") or s in held:
            continue
        d = OUT / s[len(SITE) + 1:]
        d.mkdir(parents=True, exist_ok=True)
        keep = [(p, o) for p, o in plist if o.value not in held]
        (d / "index.ttl").write_text("".join(f"<{s}> {term_nt(p)} {term_nt(o)} .\n" for p, o in keep), encoding="utf-8")
        rows = [(html.escape(labels.get(p.value) or EXT_LABEL.get(p.value) or p.value.rsplit("#", 1)[-1]), show(o))
                for p, o in sorted(keep, key=lambda t: t[0].value)]
        rows += [(html.escape((labels.get(p) or EXT_LABEL.get(p) or p.rsplit("#", 1)[-1]) + " (반대 방향)"),
                  f'<a href="{html.escape(src)}">{html.escape(labels.get(src) or src.replace(SITE, ""))}</a>')
                 for p, src in inbound.get(s, []) if src not in held]
        (d / "index.html").write_text(page(labels.get(s) or s.replace(BASE + "/", ""), s, rows), encoding="utf-8")
        n += 1

    # 어휘
    d = OUT / "def"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(ONTOLOGY, d / "ontology.ttl")
    terms = sorted((s, labels.get(s, "")) for s in triples if s.startswith(JGK))
    rows = [(f'<a id="{html.escape(s.split("#")[1])}">{html.escape(s.split("#")[1])}</a>', html.escape(l)) for s, l in terms]
    (d / "index.html").write_text(page("증미역 공간 지식 그래프 어휘 (jgk)", SITE + "/def", rows,
                                       '<p><a href="ontology.ttl">ontology.ttl 내려받기</a></p>').replace('href="index.ttl"', 'href="ontology.ttl"'),
                                  encoding="utf-8")

    counts = defaultdict(int)
    for s in triples:
        if s.startswith(BASE + "/") and s not in held and not s.endswith("/geometry"):
            counts[s[len(BASE) + 1:].split("/")[0]] += 1
    rows = [(k, str(v)) for k, v in sorted(counts.items())]
    station = f"{BASE}/station/1150010200106660040000001"
    (OUT / "index.html").write_text(page("증미역 공간 지식 그래프", SITE + "/", rows,
                                         f'<p>예: <a href="{station}">증미역</a>, <a href="{SITE}/def">어휘</a></p>'
                                         "<p>객체마다 바뀌지 않는 URI를 붙이고 GeoSPARQL 1.1로 엮은 그래프입니다. "
                                         "공개를 보류한 출처의 객체는 넣지 않았습니다.</p>"), encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    report = {"pages": n, "held": len([h for h in held if not h.endswith('/geometry')]), "by_kind": counts}
    (OUT / "publish_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
