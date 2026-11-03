"""공개 사이트 확인: 출처를 모르는 노드는 공개하지 않고, 보류 자료의 이름과 지도 번호가 나가지 않는다.

실행: python -m tests.test_publish
앞쪽은 작은 그래프와 임시 폴더로 규칙을 보고, 마지막은 data/processed/site가 있으면 실제 사이트를 읽기만 한다.
"""
from __future__ import annotations

import re
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path

import pyoxigraph as ox

from src.graph.publish import BASE, DCT_SOURCE, HOLD_NOTE, JGK, OUT, PRINCIPLES, RDF_TYPE, SITE, hold_sources, public_nodes, site_problems

ROOT = Path(__file__).resolve().parents[1]


def triples_of(rows) -> dict:
    out = defaultdict(list)
    for s, p, o in rows:
        out[s].append((ox.NamedNode(p), ox.NamedNode(o)))
    return out


def check_public_nodes() -> None:
    """출처가 없으면 부모를 따르고, 그래도 없으면 공개하지 않는다."""
    a, b, c = JGK + "dataset-open", JGK + "dataset-held", JGK + "dataset-other"
    src = lambda s, *d: [(s, DCT_SOURCE, x) for x in d]
    rows = [(x, RDF_TYPE, JGK + "Dataset") for x in (a, b, c)]
    open_node, held_node, unknown, both, typo = (f"{BASE}/{k}/1" for k in ("building", "public-facility", "road", "parcel", "sensor"))
    plain, own_held, own_open, proposal, stray = f"{BASE}/road/2", f"{BASE}/legal-dong/1", f"{BASE}/station/1", f"{BASE}/proposal/p", f"{BASE}/proposal/q"
    notice, fake_notice = f"{BASE}/notice/11500-2026-140", f"{BASE}/building/9"
    rows += src(open_node, a) + src(held_node, b) + src(both, a, b) + src(typo, JGK + "dataset-nope")
    rows += [(open_node + "/geometry", RDF_TYPE, "http://www.opengis.net/ont/geosparql#Geometry"),
             (held_node + "/geometry", RDF_TYPE, "http://www.opengis.net/ont/geosparql#Geometry"),
             (unknown + "/geometry", RDF_TYPE, "http://www.opengis.net/ont/geosparql#Geometry"),
             (unknown, RDF_TYPE, JGK + "Road"), (plain, RDF_TYPE, JGK + "Road")]
    rows += src(own_held, a) + src(own_held + "/geometry", b)  # 객체는 공개 자료, 모양만 보류 자료
    rows += src(own_open, b) + src(own_open + "/geometry", a)  # 객체가 보류 자료면 모양의 출처가 공개 자료여도 보류다
    rows += [(proposal, RDF_TYPE, JGK + "ChangeProposal"), (proposal + "/change/1", RDF_TYPE, "x:Statement"),
             (stray, RDF_TYPE, JGK + "ChangeProposal"), (stray + "/change/1", RDF_TYPE, "x:Statement")]
    rows += src(notice, "https://www.gangseo.seoul.kr/gs040301/view?mgtNo=1") + src(fake_notice, "https://example.com/a")
    pub = public_nodes(triples_of(rows), {b}, {proposal: {a}})
    assert pub == {open_node, open_node + "/geometry", own_held, proposal, proposal + "/change/1", notice}, sorted(pub)


def check_site_problems() -> None:
    """보류 자료 이름, 지도 번호, 보류 노드가 나가면 잡고, 어휘의 자료 이름은 잡지 않는다."""
    hold = {JGK + "dataset-ngii-basemap"}
    held = {f"{BASE}/basemap/BLD00000000TESTBB", f"{BASE}/road-segment/11500-1"}
    tmp = Path(tempfile.mkdtemp())
    try:
        clean = tmp / "id" / "building" / "1" / "index.html"
        clean.parent.mkdir(parents=True)
        clean.write_text("건물관리번호 1150010200106660040000001, GIS 번호 2010186967634510464800000000, 서로 다른 값", encoding="utf-8")
        (tmp / "def").mkdir()
        (tmp / "def" / "ontology.ttl").write_text("jgk:dataset-ngii-basemap a jgk:Dataset .", encoding="utf-8")
        assert site_problems(tmp, hold, held) == []

        def leak(text: str, name: str = "id/building/1/index.ttl") -> list[str]:
            f = tmp / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")
            found = site_problems(tmp, hold, held)
            f.unlink()
            return found

        assert "보류 자료 이름" in leak("dct:source jgk:dataset-ngii-basemap")[0]
        assert "지도 번호" in leak("nf_id BLD00000000TESTAA")[0]
        assert "지도 번호" in leak("국토부 B00100000000TESTA", "assets/search.json")[0]
        assert "지도 번호" in leak('"0000000000000B00000000000000000000"', "map/index.html")[0]
        assert "지도 번호" in leak("번호 BLD00000000TESTAA", "def/index.html")[0]  # 번호는 어휘 페이지에서도 나가면 안 된다
        assert "보류 노드 연결" in leak(f'<a href="/jeungmi-geokg/id/road-segment/11500-1/">구간</a>')[0]
        assert "보류 노드 연결" in leak(f"<{BASE}/road-segment/11500-1> a x .")[0]
        (tmp / "id" / "road-segment" / "11500-1").mkdir(parents=True)
        assert "페이지가 만들어진" in site_problems(tmp, hold, held)[0]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_wording() -> None:
    """문구가 결정과 맞다. '이용 조건 확인 중'이 아니고, 지금까지의 결정은 사람이 하지 않았다."""
    source = (ROOT / "src" / "graph" / "publish.py").read_text(encoding="utf-8")
    assert "확인하는 중" not in source and "확인 중" not in source
    assert "공개하지 않기로 정했고" in HOLD_NOTE and "기본측량성과" in HOLD_NOTE
    assert not any("사람이 승인한" in text for _, text in PRINCIPLES), PRINCIPLES
    assert any("사람이 맡긴 검토자" in text for _, text in PRINCIPLES), PRINCIPLES


def check_real_site() -> None:
    """실제 사이트: 자체 검사를 다시 돌리고, 국가기본도와 수치지형도, 전자지도 객체의 페이지가 없는지 본다."""
    if not OUT.exists():
        print("실제 사이트가 없어 건너뜀")
        return
    hold = hold_sources()
    assert hold, "보류 목록이 비었다"
    assert site_problems(OUT, hold, set()) == []
    kinds = {p.name for p in (OUT / "id").iterdir() if p.is_dir()}
    assert not kinds & {"basemap", "digital-map", "road-segment", "public-facility"}, kinds
    page = (OUT / "about" / "index.html").read_text(encoding="utf-8")  # 첫 화면(/)은 지도 앱이고 공개 범위 글은 소개에 있다
    assert "확인하는 중" not in page and "기본측량성과" in page
    assert re.search(r"id/building/[0-9]{25}", (OUT / "id" / "building" / "index.html").read_text(encoding="utf-8"))
    assert SITE.endswith("/jeungmi-geokg")


def run() -> None:
    check_public_nodes()
    check_site_problems()
    check_wording()
    check_real_site()
    print("공개 사이트 확인 통과")


def test_publish():
    run()


if __name__ == "__main__":
    run()
