"""고시공고 제안 확인: LLM 대신 정해진 추출 결과를 돌려주는 가짜 클라이언트로 객체 찾기와 제안 만들기를 본다.

실행: python -m tests.test_notice
API 키 없이 돈다. 실제 LLM 호출은 src.llm.notice를 키가 있을 때 직접 돌려 확인한다.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"


class FakeClient:
    """client.beta.messages.parse(...)만 흉내 낸다."""

    def __init__(self, parsed):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))
        self.parsed = parsed

    def _parse(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=self.parsed)


def run() -> None:
    from src.graph.store import load_store
    from src.llm.client import MODEL
    from src.llm.notice import ExtractedChange, NoticeExtraction, Resolver, Target, process
    from src.review.proposals import ProposalStore

    b = gpd.read_file(PROC / "objects.gpkg", layer="building")
    b = b[(b["kind"] == "building") & b["use_name"].notna()]
    counts = b["road_address"].value_counts()
    unique = b[b["road_address"].map(counts) == 1].iloc[0]
    road_tail = " ".join(unique["road_address"].split()[1:])  # '강서구 양천로 ...'
    shared = counts[counts > 1].index[0]

    parsed = NoticeExtraction(is_spatial=True, changes=[
        ExtractedChange(change_type="building_use_change", summary="용도를 업무시설로 바꾼다",
                        quote=f"{road_tail} 건축물의 용도를 업무시설로 변경", target=Target(road_address=road_tail),
                        new_value="업무시설"),
        ExtractedChange(change_type="facility_closed", summary="염창동주민센터 폐지",
                        quote="염창동주민센터를 폐지한다", target=Target(name="염창동주민센터"), effective_date="2026-12-01"),
        ExtractedChange(change_type="building_use_change", summary="여러 건물이 같은 주소",
                        quote=shared, target=Target(road_address=" ".join(shared.split()[1:])), new_value="판매시설"),
        ExtractedChange(change_type="building_new", summary="신축", quote="신축 사용승인",
                        target=Target(road_address=road_tail)),
    ])
    tmp = Path(tempfile.mkdtemp())
    doc = tmp / "notice.txt"
    doc.write_text("제목: 시험용 고시\n번호: 강서구 고시 제0000-0호\n\n본문은 시험용이다.", encoding="utf-8")
    store = ProposalStore(tmp / "proposals.json")
    fake = FakeClient(parsed)

    import src.llm.notice as notice
    notice.REPORT_DIR = tmp / "reports"
    report = process(doc, client=fake, resolver=Resolver(), store=load_store(store), proposals=store)

    kw = fake.calls[0]
    assert kw["model"] == MODEL and kw["output_format"] is NoticeExtraction and kw["fallbacks"] == "default"
    hows = [c["how"] for c in report["changes"]]
    assert hows[0] == "도로명주소" and hows[1] == "이름" and hows[2] == "여러 후보", hows
    assert [c["proposal"] is not None for c in report["changes"]] == [True, True, False, False]

    props = store.load()
    for p in props:
        assert p.location is not None and any("시험용 고시" in e for e in p.evidence)
        store.decide(p.id, "approved", "테스트 검토자")
    g = load_store(store)
    use = [r["o"].value for r in g.query(f"SELECT ?o WHERE {{ <{unique['uri']}> <https://yeodh10.github.io/jeungmi-geokg/def#useName> ?o }}")]
    assert use == ["업무시설"], use
    fac = props[1].target
    st = [r["s"].value for r in g.query(f"SELECT ?s WHERE {{ <{fac}> <https://yeodh10.github.io/jeungmi-geokg/def#status> ?s }}")]
    assert st == ["https://yeodh10.github.io/jeungmi-geokg/def#Retired"], st
    print("고시공고 제안 확인 통과:", [c["how"] for c in report["changes"]])
    shutil.rmtree(tmp, ignore_errors=True)


def test_notice_flow():
    run()


if __name__ == "__main__":
    run()
