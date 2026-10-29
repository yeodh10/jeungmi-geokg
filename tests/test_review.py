"""검토 흐름 확인: 제안을 승인하면 그래프에 들어가고, 반려하면 들어가지 않는다.

실행: python -m pytest tests  (pytest가 없으면 python -m tests.test_review)
실제 제안 파일을 건드리지 않도록 임시 복사본으로 돌린다.
data/processed/에 그래프와 제안이 만들어져 있어야 한다.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "data" / "processed" / "review" / "proposals.json"


def run() -> None:
    tmp = Path(tempfile.mkdtemp()) / "proposals.json"
    shutil.copy(REAL, tmp)
    os.environ["JGK_PROPOSALS"] = str(tmp)

    from fastapi.testclient import TestClient

    from src.review import app as review_app
    from src.review.proposals import ProposalStore

    review_app.proposals = ProposalStore(tmp)
    review_app.state["store"] = None
    c = TestClient(review_app.app)

    assert c.get("/").status_code == 200
    pending = c.get("/api/proposals?status=pending").json()
    assert pending, "대기 중인 제안이 없다"
    p = pending[0]

    # 승인 전: 대상이 그래프에 없다
    assert not c.get("/api/feature", params={"uri": p["target"]}).json()["in_graph"]

    ctx = c.get(f"/api/proposals/{p['id']}/context").json()
    roles = {f["properties"]["role"] for f in ctx["features"]}
    assert "target" in roles and "building" in roles, roles

    # 검토자 이름 없이는 결정할 수 없다
    assert c.post(f"/api/proposals/{p['id']}/decision", json={"result": "approved", "reviewer": " "}).status_code == 400

    r = c.post(f"/api/proposals/{p['id']}/decision",
               json={"result": "approved", "reviewer": "테스트 검토자", "comment": "전화번호는 다르지만 같은 기관"})
    assert r.status_code == 200, r.text

    # 승인 후: 대상과 출처 기록이 그래프에 있다
    after = c.get("/api/feature", params={"uri": p["target"]}).json()
    preds = {t["p"].rsplit("#", 1)[-1].rsplit("/", 1)[-1] for t in after["triples"]}
    assert after["in_graph"] and {"agencyCode", "housedIn", "wasInfluencedBy"} <= preds, preds
    prop = c.get("/api/feature", params={"uri": f"https://yeodh10.github.io/jeungmi-geokg/id/proposal/{p['id']}"}).json()
    assert any(t["o"] == "테스트 검토자" for t in prop["triples"])

    # 같은 제안을 두 번 결정할 수 없다
    assert c.post(f"/api/proposals/{p['id']}/decision", json={"result": "rejected", "reviewer": "x"}).status_code == 409

    # 반려: 새 제안을 하나 만들어 반려하면 그래프에 들어가지 않는다
    store = ProposalStore(tmp)
    clone = store.get(p["id"]).model_copy(update={"id": "test-reject", "status": "pending", "decision": None,
                                                   "target": p["target"] + "-test"})
    clone.changes = [ch.model_copy(update={"s": p["target"] + "-test"}) for ch in clone.changes if ch.s == p["target"]]
    store.add_new([clone])
    assert c.post("/api/proposals/test-reject/decision", json={"result": "rejected", "reviewer": "테스트 검토자"}).status_code == 200
    review_app.state["store"] = None
    assert not c.get("/api/feature", params={"uri": p["target"] + "-test"}).json()["in_graph"]
    assert c.get("/api/proposals?status=rejected").json()[0]["id"] == "test-reject"

    print("검토 흐름 확인 통과:", p["id"])


def test_review_flow():
    run()


if __name__ == "__main__":
    run()
