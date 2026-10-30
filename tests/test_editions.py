"""시점별 판 확인: 승인 기록으로 되살린 지난 시점 값이 그 변경을 넣기 전 그래프와 똑같은지.

실행: python -m tests.test_editions
실제 제안 파일은 임시 복사본으로만 쓴다. 폐지 제안 하나를 10시에, 주소 연결 제안 하나를 11시에 승인했다고 두고 견준다.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "data" / "processed" / "review" / "proposals.json"
JGK = "https://yeodh10.github.io/jeungmi-geokg/def#"
PROV = "http://www.w3.org/ns/prov#"


def run() -> None:
    from src.graph.editions import as_of, changes_between, diff_states, moment
    from src.graph.store import load_store
    from src.review.proposals import Decision, ProposalStore

    tmp = Path(tempfile.mkdtemp())
    shutil.copy(REAL, tmp / "proposals.json")
    ps = ProposalStore(tmp / "proposals.json")
    props = ps.load()
    for p in props:  # 실제 기록의 결정은 지우고 시작한다 (이 시험이 고른 두 승인만 그래프에 있게)
        p.status, p.decision, p.corrections = "pending", None, []
    retire = next(p for p in props if p.action == "retire" and p.status == "pending")
    link = next(p for p in props if p.action == "link" and p.status == "pending")
    for p, at in ((retire, "2026-10-30T10:00:00+09:00"), (link, "2026-10-30T11:00:00+09:00")):
        p.status, p.decision = "approved", Decision(result="approved", reviewer="시험", at=at)
    ps.save(props)

    empty = ProposalStore(tmp / "none.json")  # 승인이 하나도 없는 그래프 (원천 판)
    base, now = load_store(empty), load_store(ps)

    # 두 시각 사이의 변경: 10시 것만, 10~11시 사이는 11시 것만
    all_rows = changes_between(now)
    assert {r["proposal"] for r in all_rows} == {retire.uri, link.uri}, all_rows[:3]
    assert {r["proposal"] for r in changes_between(now, "2026-10-30T10:30", "2026-10-30T12:00")} == {link.uri}
    assert len(all_rows) == len(retire.changes) + len(link.changes)

    for p, at in ((retire, "2026-10-30T10:00:00+09:00"), (link, "2026-10-30T11:00:00+09:00")):
        before_t = "2026-10-30T09:00:00"
        # 승인 전 시각으로 되살린 값 == 승인이 없는 원천 그래프의 값
        revived = as_of(now, p.target, before_t)
        original = as_of(base, p.target, before_t)
        assert revived == original, diff_states(original, revived)
        # 승인 뒤 시각의 값 == 지금 그래프의 값, 그리고 승인 전과 다르다
        after = as_of(now, p.target, "2026-10-30T12:00:00")
        assert after == as_of(now, p.target, "2999-01-01"), "승인 뒤 값은 지금 값과 같아야 한다"
        changed = {d["p"] for d in diff_states(revived, after)}
        assert changed and PROV + "wasInfluencedBy" in changed, changed
        assert moment(at) == at

    # 폐지 제안: 9시에는 폐지가 아니었고 12시에는 폐지다
    status = JGK + "status"
    assert JGK + "Retired" not in as_of(now, retire.target, "2026-10-30T09:00")[status]
    assert as_of(now, retire.target, "2026-10-30T12:00")[status] == [JGK + "Retired"]
    # 화면 API: 승인 1초 전 값과 지금 값의 차이, 두 시각 사이 변경 목록
    from fastapi.testclient import TestClient

    from src.review import app as review_app
    review_app.proposals = ps
    review_app.state["store"] = None
    c = TestClient(review_app.app)
    r = c.get("/api/editions/as-of", params={"uri": retire.target, "at": "2026-10-30T09:59:59+09:00"}).json()
    row = next(d for d in r["diff"] if d["p"] == status)
    assert row["after"] == [JGK + "Retired"] and JGK + "Retired" not in row["before"], row
    assert c.get("/api/editions/as-of", params={"uri": "not a uri", "at": "2026-10-30"}).status_code == 400
    ch = c.get("/api/editions/changes", params={"since": "2026-10-30T10:30:00+09:00"}).json()
    assert {x["proposal"] for x in ch["changes"]} == {link.uri}, ch
    shutil.rmtree(tmp, ignore_errors=True)
    print("시점별 판 확인 통과")


if __name__ == "__main__":
    run()
