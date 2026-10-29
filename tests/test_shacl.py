"""SHACL 확인: 바른 제안은 통과하고, 규칙을 어기는 제안은 승인이 막히는지 본다.

실행: python -m tests.test_shacl
실제 제안 파일은 임시 복사본으로만 쓴다.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "data" / "processed" / "review" / "proposals.json"
JGK = "https://yeodh10.github.io/jeungmi-geokg/def#"


def run() -> None:
    from fastapi.testclient import TestClient

    from src.graph.store import load_store
    from src.graph.validate import check_proposal
    from src.review import app as review_app
    from src.review.proposals import Change, ProposalStore, Term

    tmp = Path(tempfile.mkdtemp()) / "proposals.json"
    shutil.copy(REAL, tmp)
    store = ProposalStore(tmp)
    good = store.load()[0]
    g = load_store(store)
    ok, res = check_proposal(g, good)
    assert ok, res

    # 폐지로 바꾸면서 이전 상태를 빼지 않고 폐지 확인일도 없는 제안 -> 상태 2개, 폐지 확인일 없음
    station = "https://yeodh10.github.io/jeungmi-geokg/id/station/1150010200106660040000001"
    bad = good.model_copy(update={"id": "test-bad", "target": station, "action": "retire", "changes": [
        Change(op="add", s=station, p=JGK + "status", o=Term(kind="uri", value=JGK + "Retired"))]})
    ok, res = check_proposal(g, bad)
    msgs = {r["message"] for r in res}
    assert not ok and any("상태는 정확히 하나" in m for m in msgs) and any("폐지 확인일" in m for m in msgs), msgs

    # 건물 식별 번호가 둘 다 있는 건물 -> xone 위반
    b = "https://yeodh10.github.io/jeungmi-geokg/id/building/1150010200106660040000001"
    bad2 = good.model_copy(update={"id": "test-bad2", "target": b, "action": "update", "changes": [
        Change(op="add", s=b, p=JGK + "gisBuildingId", o=Term(kind="literal", value="1" * 28))]})
    ok, res = check_proposal(g, bad2)
    assert not ok and any("정확히 하나로 식별" in r["message"] for r in res), res

    # 화면 API: 규칙을 어기는 제안은 승인 409, 반려는 된다
    store.add_new([bad])
    review_app.proposals = store
    review_app.state["store"] = None
    c = TestClient(review_app.app)
    v = c.get("/api/proposals/test-bad/validate").json()
    assert not v["conforms"]
    r = c.post("/api/proposals/test-bad/decision", json={"result": "approved", "reviewer": "테스트 검토자"})
    assert r.status_code == 409 and "규칙" in r.json()["detail"], r.text
    assert c.post("/api/proposals/test-bad/decision",
                  json={"result": "rejected", "reviewer": "테스트 검토자", "comment": "규칙 위반"}).status_code == 200
    print("SHACL 확인 통과:", sorted(msgs))


def test_shacl_flow():
    run()


if __name__ == "__main__":
    run()
