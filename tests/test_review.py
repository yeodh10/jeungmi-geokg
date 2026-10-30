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

    # 목록: 결정 정보와 묶음. 같은 고시(제2026-140호)에서 나온 대기 제안 5건은 한 묶음이다
    assert all(x["decision"] is None and x["decided_at"] is None and x["reviewer"] is None for x in pending)
    notice = [x for x in pending if x["group"] == "notice:2026-140"]
    assert len(notice) == 5 and {x["group_label"] for x in notice} == {"서울특별시 강서구 고시 제2026-140호"}, notice
    assert p["group"] == f"single:{p['id']}" and p["group_label"] is None, p
    detail = c.get(f"/api/proposals/{p['id']}").json()
    assert detail["uri"] == f"https://yeodh10.github.io/jeungmi-geokg/id/proposal/{p['id']}" and detail["group"] == p["group"]
    assert c.get("/api/proposals/없는-제안").status_code == 404

    # 잘못된 상태 이름은 422, 오류 본문은 detail 한 문장과 원래 목록
    bad = c.get("/api/proposals?status=bogus")
    assert bad.status_code == 422 and bad.json()["detail"].startswith("요청 형식이 맞지 않다") and bad.json()["errors"], bad.text

    # 승인 전: 대상이 그래프에 없다
    assert not c.get("/api/feature", params={"uri": p["target"]}).json()["in_graph"]

    ctx = c.get(f"/api/proposals/{p['id']}/context").json()
    roles = {f["properties"]["role"] for f in ctx["features"]}
    assert "target" in roles and "building" in roles, roles

    # 검토자 이름 없이는 결정할 수 없다
    assert c.post(f"/api/proposals/{p['id']}/decision", json={"result": "approved", "reviewer": " "}).status_code == 400
    # 검토자 이름은 한 줄 50자까지, 반려에는 사유가, 의견은 1000자까지
    for body, msg in [({"result": "approved", "reviewer": "가\n나"}, "검토자 이름은"),
                      ({"result": "approved", "reviewer": "가\t나"}, "검토자 이름은"),
                      ({"result": "approved", "reviewer": "가" * 51}, "검토자 이름은"),
                      ({"result": "rejected", "reviewer": "테스트 검토자"}, "반려 사유"),
                      ({"result": "rejected", "reviewer": "테스트 검토자", "comment": "  \n "}, "반려 사유"),
                      ({"result": "approved", "reviewer": "테스트 검토자", "comment": "가" * 1001}, "의견은")]:
        r = c.post(f"/api/proposals/{p['id']}/decision", json=body)
        assert r.status_code == 400 and r.json()["detail"].startswith(msg), (body, r.text)
    r = c.post(f"/api/proposals/{p['id']}/decision", json={"result": "maybe", "reviewer": "x"})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str), r.text

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
    r = c.post(f"/api/proposals/{p['id']}/decision", json={"result": "rejected", "reviewer": "x", "comment": "다시"})
    assert r.status_code == 409 and r.json()["detail"] == "이미 승인된 제안이다", r.text
    done = next(x for x in c.get("/api/proposals?status=approved").json() if x["id"] == p["id"])
    assert done["reviewer"] == "테스트 검토자" and done["decided_at"] == done["decision"]["at"], done

    # 반려: 새 제안을 하나 만들어 반려하면 그래프에 들어가지 않는다
    store = ProposalStore(tmp)
    clone = store.get(p["id"]).model_copy(update={"id": "test-reject", "status": "pending", "decision": None,
                                                   "target": p["target"] + "-test"})
    clone.changes = [ch.model_copy(update={"s": p["target"] + "-test"}) for ch in clone.changes if ch.s == p["target"]]
    store.add_new([clone])
    assert c.post("/api/proposals/test-reject/decision",
                  json={"result": "rejected", "reviewer": "테스트 검토자", "comment": "시험용 반려"}).status_code == 200
    review_app.state["store"] = None
    assert not c.get("/api/feature", params={"uri": p["target"] + "-test"}).json()["in_graph"]
    assert "test-reject" in {x["id"] for x in c.get("/api/proposals?status=rejected").json()}

    print("검토 흐름 확인 통과:", p["id"])


def test_review_flow():
    run()


if __name__ == "__main__":
    run()
