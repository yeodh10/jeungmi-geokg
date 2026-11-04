"""시점별 판과 제안 저장 확인: 지난 시점 값, 승인과 정정의 재생 순서, 재적용 충돌, 제안 파일 다루기.

실행: python -m tests.test_editions
실제 제안 파일은 임시 복사본으로만 쓴다. 폐지 제안 하나를 10시에, 주소 연결 제안 하나를 11시에 승인했다고 두고 견준다.
작은 시나리오는 원천 그래프를 작은 Turtle로 바꿔 돌린다. 그래서 한 건에 1초도 걸리지 않는다.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import pyoxigraph as ox

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "data" / "processed" / "review" / "proposals.json"
ID = "https://yeodh10.github.io/jeungmi-geokg/id"
JGK = "https://yeodh10.github.io/jeungmi-geokg/def#"
PROV = "http://www.w3.org/ns/prov#"
GEO = "http://www.opengis.net/ont/geosparql#"
XSD = "http://www.w3.org/2001/XMLSchema#"
NOTE = JGK + "testNote"
B = f"{ID}/building/1"
GIS = f"{ID}/building-gis/2"
GEOM = f"{B}/geometry"
POINT = "POINT (126.86 37.55)"
SMALL = f"""@prefix jgk: <{JGK}> . @prefix geo: <{GEO}> .
<{B}> a jgk:Building, geo:Feature ; jgk:status jgk:Active ; geo:hasGeometry <{GEOM}> ; geo:hasDefaultGeometry <{GEOM}> .
<{GEOM}> a geo:Geometry ; geo:asWKT "{POINT}"^^geo:wktLiteral .
<{GIS}> a jgk:Building, geo:Feature ; jgk:status jgk:AddressUnlinked .
"""


def t(h: int, m: int = 0) -> str:
    return f"2026-10-30T{h:02d}:{m:02d}:00+09:00"


@contextmanager
def small_graph(tmp: Path, extra: str = "", source: str = SMALL):
    """원천 그래프 파일을 작은 Turtle로 잠깐 바꾼다."""
    from src.graph import store as S
    path = tmp / f"small-{time.time_ns()}.ttl"
    path.write_text(source + extra, encoding="utf-8")
    real, S.TTL = S.TTL, path
    try:
        yield
    finally:
        S.TTL = real


def term(v: str):
    from src.review.proposals import Term
    return Term(kind="literal", value=v)


def proposal(pid: str, *changes, at: str | None = None, seq: int | None = None, target: str = B, action: str = "update"):
    """승인 시각 at을 주면 승인된 제안, 없으면 대기 제안. changes는 (op, s, p, o) 또는 (op, p, o)."""
    from src.review.proposals import Change, Decision, Proposal
    rows = [c if len(c) == 4 else (c[0], target, c[1], c[2]) for c in changes]
    return Proposal(id=pid, created_at=t(0), source_kind="rule-candidate", source_detail="시험", action=action,
                    target=target, target_type="Building", summary=pid,
                    changes=[Change(op=o, s=s, p=p, o=v) for o, s, p, v in rows],
                    status="approved" if at else "pending",
                    decision=Decision(result="approved", reviewer="시험", at=at, seq=seq) if at else None)


def notes(store, s: str = B) -> list[str]:
    return sorted(str(q.object) for q in store.quads_for_pattern(ox.NamedNode(s), ox.NamedNode(NOTE), None, None))


def check_as_of_scenarios(tmp: Path) -> None:
    """11개 시나리오: 그래프를 다시 불러온 경우와 실행 중에 바로 얹은 경우 둘 다 시각 T의 값이 맞아야 한다."""
    from src.graph.editions import as_of
    from src.graph.store import load_store
    from src.review.proposals import ProposalStore

    X, Y = term("X"), term("Y")
    has_x = f'<{B}> <{NOTE}> "X" .\n'
    add, rem = ("add", NOTE, X), ("remove", NOTE, X)
    cases = [
        ("넣고 뺌, 사이", "", [("a", 10, add), ("b", 12, rem)], t(11), ["X"]),
        ("뺐다 다시 넣음, 앞", has_x, [("a", 10, rem), ("b", 12, add)], t(9), ["X"]),
        ("뺐다 다시 넣음, 사이", has_x, [("a", 10, rem), ("b", 12, add)], t(11), []),
        ("넣고 빼고 다시 넣음, 첫 사이", "", [("a", 10, add), ("b", 12, rem), ("c", 14, add)], t(11), ["X"]),
        ("넣고 빼고 다시 넣음, 둘째 사이", "", [("a", 10, add), ("b", 12, rem), ("c", 14, add)], t(13), []),
        ("두 제안이 같은 값을 넣음", "", [("a", 10, add), ("b", 12, add)], t(11), ["X"]),
        ("두 제안이 같은 값을 뺌", has_x, [("a", 10, rem), ("b", 12, rem)], t(11), []),
        ("있는 값을 다시 넣음", has_x, [("a", 10, add)], t(9), ["X"]),
        ("없는 값을 뺌", "", [("a", 10, ("remove", NOTE, Y))], t(9), []),
        ("같은 시각에 넣고 뺌, 그 시각", "", [("a", 10, add), ("b", 10, rem)], t(10), []),
        ("같은 시각에 넣고 뺌, 앞", "", [("a", 10, add), ("b", 10, rem)], t(9), []),
    ]
    failed = []
    for i, (name, extra, plan, at, want) in enumerate(cases):
        props = [proposal(f"s{i}-{pid}", ch, at=t(h), seq=n) for n, (pid, h, ch) in enumerate(plan, 1)]
        ps = ProposalStore(tmp / f"s{i}.json")
        ps.save(props)
        with small_graph(tmp, extra):
            reloaded = load_store(ps)
            live = load_store(ProposalStore(tmp / "none.json"))
            for p in props:  # 서버는 승인을 다시 불러오지 않고 지금 그래프에 바로 얹는다
                live.update(p.sparql_update())
        for how, g in (("다시 불러옴", reloaded), ("바로 얹음", live)):
            got = as_of(g, B, at).get(NOTE, [])
            if got != want:
                failed.append((name, how, got, want))
    assert not failed, failed


def check_reapply_conflict(tmp: Path) -> None:
    """새 자료로 다시 만든 뒤: 빼려던 값이 없으면 그 제안 전체를 적용하지 않고 충돌로 남긴다."""
    from src.graph.store import last_report, load_store
    from src.review.proposals import ProposalStore, Term

    wkt = GEO + "asWKT"
    old = Term(kind="literal", value=POINT, datatype=GEO + "wktLiteral")
    poly = Term(kind="literal", value="POLYGON ((126.86 37.55, 126.87 37.55, 126.87 37.56, 126.86 37.55))", datatype=GEO + "wktLiteral")
    linked = Term(kind="uri", value=JGK + "AddressLinked")
    unlinked = Term(kind="uri", value=JGK + "AddressUnlinked")
    shape = proposal("shape", ("remove", GEOM, wkt, old), ("add", GEOM, wkt, poly), at=t(10))
    link = proposal("link", ("remove", GIS, JGK + "status", unlinked), ("add", GIS, JGK + "status", linked),
                    at=t(11), target=GIS, action="link")
    ps = ProposalStore(tmp / "conflict.json")
    ps.save([shape, link])
    moved = "POINT (126.86 37.56)"
    source = SMALL.replace(POINT, moved)  # 새 자료에서 점이 옮겨졌다
    source = "\n".join(line for line in source.splitlines() if GIS not in line) + "\n"  # GIS 건물은 없어졌다
    with small_graph(tmp, source=source):
        g = load_store(ps)
    wkts = [q.object.value for q in g.quads_for_pattern(ox.NamedNode(GEOM), ox.NamedNode(wkt), None, None)]
    assert wkts == [moved], wkts  # 예전에는 모양 노드에 WKT가 2개가 됐다
    assert not list(g.quads_for_pattern(ox.NamedNode(GIS), None, None, None))  # 예전에는 종류 없이 트리플이 남았다
    report = last_report()
    assert {c["id"] for c in report.conflicts} == {"shape", "link"}, report.conflicts
    assert all(c["reason"] for c in report.conflicts), report.conflicts
    assert not report.applied, report.applied

    # 대상 객체가 없어졌는데 넣기만 하는 제안도 같다 (없는 객체에 종류 없는 트리플이 생기면 안 된다)
    ps2 = ProposalStore(tmp / "conflict2.json")
    ps2.save([proposal("ghost", ("add", NOTE, term("X")), at=t(10), target=f"{ID}/building/없음")])
    with small_graph(tmp):
        g = load_store(ps2)
    assert not list(g.quads_for_pattern(ox.NamedNode(f"{ID}/building/없음"), None, None, None))
    assert [c["id"] for c in last_report().conflicts] == ["ghost"]

    # build_store는 같은 결과를 돌려주되 last_report()를 바꾸지 않는다. 검토 서버는 정정 그래프를 쓰기로 한 뒤에 remember()로 내건다
    from src.graph.store import build_store, remember
    shown = last_report()
    with small_graph(tmp, source=source):
        _, report = build_store(ps)
    assert {c["id"] for c in report.conflicts} == {"shape", "link"} and last_report() is shown
    remember(report)
    assert last_report() is report

    # 같은 id인데 내용이 달라진 제안은 조용히 0건이 아니라 드러난다
    ps3 = ProposalStore(tmp / "differ.json")
    first = proposal("same", ("add", NOTE, term("X")))
    assert ps3.add_new([first]) == 1
    assert ps3.add_new([first]).differ == []
    again = ps3.add_new([proposal("same", ("add", NOTE, term("Y")))])
    assert again == 0 and again.differ == ["same"], again


def check_proposal_id(tmp: Path) -> None:
    """id 조작으로 SPARQL 문이 끼어들지 못한다. 재현: 승인 한 번에 DROP ALL로 트리플이 1개만 남았다."""
    from pydantic import ValidationError

    from src.review.proposals import Term

    bad_ids = ['x> <a:p> <a:o> . } ; DROP ALL ; INSERT DATA { <a:pwned> <a:by> "id" . #',
               "x> <a:p> <a:o> . } ; DROP ALL ; INSERT DATA { <a:pwned> <a:by> ''' . #", "a b", "가", "", "a\n"]
    good = proposal("rule-basemap-link-1.a:b-c", ("add", NOTE, term("X")))
    for bad in bad_ids:
        try:
            proposal(bad, ("add", NOTE, term("X")))
        except ValidationError:
            pass
        else:
            raise AssertionError(f"받으면 안 되는 id: {bad!r}")
        copied = good.model_copy(update={"id": bad})  # model_copy는 검사를 거치지 않지만 SPARQL을 만들 때 다시 본다
        try:
            copied.sparql_update()
        except ValueError:
            continue
        raise AssertionError(f"SPARQL을 만들면 안 되는 id: {bad!r}")

    # target의 ''' 는 id를 막은 뒤에는 IRI 안의 글자일 뿐이다 (재현: audit/rv/t3b.py의 target)
    evil = proposal("ok-id", ("add", "http://a/x'''.#y", NOTE, term("X")), target="http://a/x'''.#y", at=t(10))
    g = ox.Store()
    g.update('INSERT DATA { <a:orig> <a:p> "원래" }')
    g.update(evil.sparql_update())
    assert list(g.quads_for_pattern(ox.NamedNode("a:orig"), None, None, None)), "원래 값이 지워졌다"
    assert notes(g, "http://a/x'''.#y") == ['"X"']

    # 값 안의 역슬래시, 따옴표, " 도 값으로만 들어간다
    sneaky = '\\u0022 } ; DROP ALL ; # " \\'
    p = proposal("ok-id-2", ("add", NOTE, Term(kind="literal", value=sneaky)), at=t(10))
    g.update(p.sparql_update())
    assert list(g.quads_for_pattern(ox.NamedNode("a:orig"), None, None, None))
    assert [q.object.value for q in g.quads_for_pattern(ox.NamedNode(B), ox.NamedNode(NOTE), None, None)] == [sneaky]


WORKER = """
import sys
from pathlib import Path
from src.review.proposals import ProposalStore
store = ProposalStore(Path(sys.argv[1]))
for pid in sys.argv[2:]:
    store.decide(pid, "rejected", "프로세스 " + sys.argv[2], "동시 저장 시험")
"""


def check_two_processes(tmp: Path) -> None:
    """다른 프로세스가 함께 저장해도 결정이 사라지지 않는다. 재현: 20건 중 17건이 사라졌다."""
    from src.review.proposals import ProposalStore

    path = tmp / "many.json"
    store = ProposalStore(path)
    ids = [f"p-{i}" for i in range(20)]
    store.add_new([proposal(i, ("add", NOTE, term(i))) for i in ids])
    runs = [subprocess.Popen([sys.executable, "-c", WORKER, str(path), *ids[i:i + 5]], cwd=ROOT) for i in range(0, 20, 5)]
    assert [r.wait(timeout=120) for r in runs] == [0] * 4
    saved = store.load()
    assert [p.status for p in saved] == ["rejected"] * 20, [p.status for p in saved]
    assert len({p.decision.seq for p in saved}) == 20  # 일련번호도 겹치지 않는다
    assert not list(tmp.glob("many.json.*.tmp"))

    # 저장에 실패해도 임시 파일이 남지 않고 원래 파일은 그대로다
    before = path.read_bytes()
    broken = saved[0].model_copy(update={"summary": "\ud800"})
    try:
        store.save([broken])
    except UnicodeEncodeError:
        pass
    else:
        raise AssertionError("외톨이 대리문자는 저장되면 안 된다")
    assert path.read_bytes() == before and not list(tmp.glob("many.json.*.tmp"))


def check_before_save(tmp: Path) -> None:
    """저장 직전 단계: 그래프에 먼저 반영하다 실패하면 파일은 그대로다. 성공하면 일련번호가 같은 결정이 저장된다."""
    from src.graph.store import load_store
    from src.review.proposals import ProposalStore

    ps = ProposalStore(tmp / "first.json")
    ps.add_new([proposal("f1", ("add", NOTE, term("X")))])
    before = ps.path.read_bytes()

    def boom(changed, everything):
        raise RuntimeError("시험용 실패")
    try:
        ps.decide("f1", "approved", "검토자", before_save=boom)
    except RuntimeError:
        pass
    else:
        raise AssertionError("before_save의 예외가 나가야 한다")
    assert ps.path.read_bytes() == before and ps.get("f1").status == "pending"

    # 승인: 저장 전에 얹은 그래프와 저장 뒤 다시 불러온 그래프가 같다
    with small_graph(tmp):
        live = load_store(ProposalStore(tmp / "none.json"))
        ps.decide("f1", "approved", "검토자", before_save=lambda changed, _: live.update(changed[0].sparql_update()))
        assert {str(q) for q in live} == {str(q) for q in load_store(ps)}
        # 승인 거두기: 저장 전에 전체 목록으로 새 그래프를 만든다
        made = {}
        ps.correct("f1", "pending", "검토자", "틀림", before_save=lambda _, everything: made.update(g=load_store(everything)))
        assert notes(made["g"]) == [] and {str(q) for q in made["g"]} == {str(q) for q in load_store(ps)}


def check_correction_history(tmp: Path) -> None:
    """정정으로 승인을 거둬도 지난 이력은 남는다. 재현: changes_between 4줄이 0줄이 되고 as_of도 틀렸다."""
    from src.graph.editions import as_of, changes_between
    from src.graph.store import load_store
    from src.review import proposals as PM
    from src.review.proposals import ProposalStore

    status = JGK + "status"
    active, retired = (PM.Term(kind="uri", value=JGK + n) for n in ("Active", "Retired"))
    ps = ProposalStore(tmp / "correct.json")
    ps.add_new([proposal("c1", ("remove", status, active), ("add", status, retired),
                         ("add", JGK + "retiredOn", PM.Term(kind="literal", value="2026-10-30", datatype=XSD + "date")),
                         ("add", NOTE, term("X")), action="retire")])
    times = iter([t(10), t(12), t(14), t(16)])
    real_now, PM.now = PM.now, lambda: next(times)
    try:
        approved = ps.decide("c1", "approved", "검토자")
        ps.correct("c1", "pending", "정정자", "대상을 잘못 찾음")
        with small_graph(tmp):
            g = load_store(ps)
        # 지금 값은 승인 전과 같고, 제안 노드는 빠진다
        assert notes(g) == [] and [q.object.value for q in g.quads_for_pattern(
            ox.NamedNode(B), ox.NamedNode(status), None, None)] == [JGK + "Active"]
        assert not list(g.quads_for_pattern(ox.NamedNode(approved.uri), None, None, None))
        # 승인이 살아 있던 시간의 이력은 그대로 남는다
        mid = changes_between(g, t(9), t(11))
        assert {(r["proposal"], r["op"]) for r in mid} == {(approved.uri, "add"), (approved.uri, "remove")}, mid
        assert len(mid) == 4, mid
        assert as_of(g, B, t(11))[status] == [JGK + "Retired"] and as_of(g, B, t(11))[NOTE] == ["X"]
        assert as_of(g, B, t(9))[status] == [JGK + "Active"] and NOTE not in as_of(g, B, t(9))
        assert as_of(g, B, t(13))[status] == [JGK + "Active"] and NOTE not in as_of(g, B, t(13))
        back = changes_between(g, t(11), t(13))
        assert len(back) == 4 and {r["action"] for r in back} == {"withdraw"}, back
        assert {(r["op"], r["o"]) for r in back} >= {("add", JGK + "Active"), ("remove", JGK + "Retired")}, back

        # 다시 승인: 실행 중 그래프에 바로 얹은 것과 다시 불러온 것이 똑같다
        again = ps.decide("c1", "approved", "검토자")
        g.update(again.sparql_update())
        with small_graph(tmp):
            fresh = load_store(ps)
        live, reloaded = {str(q) for q in g}, {str(q) for q in fresh}
        assert live == reloaded, (sorted(live - reloaded)[:3], sorted(reloaded - live)[:3])
        assert as_of(g, B, t(13))[status] == [JGK + "Active"] and as_of(g, B, t(15))[status] == [JGK + "Retired"]

        # 승인을 반려로 정정한 뒤 다시 승인으로 정정해도 같다
        ps.correct("c1", "rejected", "정정자", "다시 보니 틀림")
        with small_graph(tmp):
            g = load_store(ps)
        assert notes(g) == [] and as_of(g, B, t(15))[NOTE] == ["X"]
    finally:
        PM.now = real_now


def check_replay_order(tmp: Path) -> None:
    """재생 순서: 문자열이 아니라 시각으로, 같은 초면 결정한 순서로. 실행 중과 재시작 뒤가 같아야 한다."""
    from src.graph.editions import as_of, moment
    from src.graph.store import load_store
    from src.review import proposals as PM
    from src.review.proposals import ProposalStore

    # 11:00(+09:00)을 UTC로 적으면 문자열로는 10:00(+09:00)보다 앞이다
    a = proposal("tz-a", ("add", NOTE, term("X")), at=t(10))
    b = proposal("tz-b", ("remove", NOTE, term("X")), at="2026-10-30T02:00:00+00:00")
    ps = ProposalStore(tmp / "tz.json")
    ps.save([a, b])
    with small_graph(tmp):
        g = load_store(ps)
    assert notes(g) == [], notes(g)
    assert as_of(g, B, t(10, 30))[NOTE] == ["X"] and NOTE not in as_of(g, B, t(11, 30))
    assert moment("2026-10-30T02:00:00Z") == "2026-10-30T02:00:00+00:00"

    # 파일 순서가 a, b여도 b를 먼저 승인하면 b, a 순서로 재생한다
    ps = ProposalStore(tmp / "same.json")
    ps.add_new([proposal("same-a", ("remove", NOTE, term("X"))), proposal("same-b", ("add", NOTE, term("X")))])
    real_now, PM.now = PM.now, lambda: t(10)
    try:
        first = ps.decide("same-b", "approved", "검토자")
        second = ps.decide("same-a", "approved", "검토자")
    finally:
        PM.now = real_now
    assert first.decision.seq < second.decision.seq
    with small_graph(tmp):
        live = load_store(ProposalStore(tmp / "none.json"))
        live.update(first.sparql_update())
        live.update(second.sparql_update())
        fresh = load_store(ps)
    assert notes(live) == [] and notes(fresh) == [], (notes(live), notes(fresh))
    assert {str(q) for q in live} == {str(q) for q in fresh}

    # 일련번호가 없는 옛 기록도 시각순으로 번호를 받고, 다음 결정은 그 뒤 번호를 받는다
    old = json.loads(json.dumps([p.model_dump() for p in [proposal("old-b", at=t(11)), proposal("old-a", at=t(10))]]))
    for row in old:
        del row["decision"]["seq"]
    path = tmp / "old.json"
    path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    ps = ProposalStore(path)
    assert [p.decision.seq for p in ps.load()] == [2, 1]
    ps.add_new([proposal("new-1", ("add", NOTE, term("X")))])
    assert ps.decide("new-1", "rejected", "검토자", "시험").decision.seq == 3


def check_broken_record(tmp: Path) -> None:
    """기록 하나가 깨져도 나머지로 그래프를 만들고, 건너뛴 승인을 드러내고, 깨진 기록을 잃지 않는다."""
    from src.graph.store import last_report, load_store
    from src.review.proposals import ProposalStore

    good = proposal("good", ("add", NOTE, term("X")), at=t(10))
    rows = [good.model_dump(), good.model_dump(), good.model_dump(), good.model_dump(), "문자열 기록"]
    rows[1].update(id="bad-iri")
    rows[1]["changes"][0]["s"] = "not an iri"
    rows[2].update(id="bad-status", status="모름")
    rows[3].update(id="bad time")
    path = tmp / "broken.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    ps = ProposalStore(path)
    assert [p.id for p in ps.load()] == ["good"]
    assert {b["id"] for b in ps.broken} == {"bad-iri", "bad-status", "bad time", None}, ps.broken
    with small_graph(tmp):
        g = load_store(ps)
    assert notes(g) == ['"X"']
    report = last_report()
    assert report.applied == ["good"] and len(report.skipped) == 4, report
    assert any(s["id"] == "bad-iri" and s["status"] == "approved" and s["error"] for s in report.skipped), report.skipped

    # 저장을 거쳐도 깨진 기록은 파일에 그대로 남는다
    ps.add_new([proposal("next", ("add", NOTE, term("Y")))])
    ps.decide("next", "approved", "검토자")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [r["id"] if isinstance(r, dict) else r for r in saved] == ["good", "bad-iri", "bad-status", "bad time", "문자열 기록", "next"]
    assert saved[1]["changes"][0]["s"] == "not an iri"


def check_add_new_and_bim_id(tmp: Path) -> None:
    import pandas as pd
    from shapely.geometry import Point

    from src.review import bim_proposals as BM
    from src.review.proposals import ProposalStore

    ps = ProposalStore(tmp / "dup.json")
    p = proposal("dup", ("add", NOTE, term("X")))
    assert ps.add_new([p, p.model_copy(deep=True)]) == 1
    assert [x.id for x in ps.load()] == ["dup"]

    # 모델이 달라도 끝 이름이 같은 BIM 건물은 id가 달라야 한다
    row = pd.Series({"label": "가", "road_address": "주소", "floors_above": 3, "floors_below": 0, "uri": B, "geometry": Point(190000, 450000)})
    make = lambda iri: BM.make({"iri": iri, "mgt": None, "point": None, "above": 3, "below": 0, "label": "", "how": "위치",
                                "distance_m": 0.0, "target_row": row}, "f.ttl")
    a, b = make("https://a.example/model1#Building_1"), make("https://b.example/model2#Building_1")
    assert a.id != b.id, a.id
    assert make("https://a.example/model1#Building_1").id == a.id  # 다시 돌려도 같다
    assert ps.add_new([a, b]) == 2


def check_value_kinds(tmp: Path) -> None:
    """언어 태그와 값 종류가 달라도 시점 비교에서 사라지지 않고, 모양 변경이 객체 이력에 보인다."""
    from src.graph.editions import as_of, changes_between, diff_states
    from src.graph.store import load_store
    from src.review.proposals import ProposalStore, Term

    ko, en, plain = (Term(kind="literal", value="가", lang="ko"), Term(kind="literal", value="가", lang="en"), term("가"))
    same_uri, same_text = Term(kind="uri", value="urn:same"), term("urn:same")
    wkt = GEO + "asWKT"
    old = Term(kind="literal", value=POINT, datatype=GEO + "wktLiteral")
    poly = Term(kind="literal", value="POLYGON ((126.86 37.55, 126.87 37.55, 126.87 37.56, 126.86 37.55))", datatype=GEO + "wktLiteral")
    ps = ProposalStore(tmp / "kinds.json")
    ps.save([proposal("l0", ("add", NOTE, ko), at=t(8)),
             proposal("l1", ("remove", NOTE, ko), ("add", NOTE, en), at=t(10)),
             proposal("l2", ("add", NOTE, plain), at=t(12)),
             proposal("l3", ("add", JGK + "testLink", same_uri), at=t(14)),
             proposal("l4", ("remove", JGK + "testLink", same_uri), ("add", JGK + "testLink", same_text), at=t(15)),
             proposal("g1", ("remove", GEOM, wkt, old), ("add", GEOM, wkt, poly), at=t(16))])
    with small_graph(tmp):
        g = load_store(ps)
    rows = diff_states(as_of(g, B, t(9)), as_of(g, B, t(11)))
    assert [r["p"] for r in rows if r["p"] == NOTE] == [NOTE], rows  # ko가 en으로 바뀌었는데 차이가 없다고 나오면 안 된다
    assert len(as_of(g, B, t(13))[NOTE]) == 2, as_of(g, B, t(13))  # 가@en과 언어 없는 가는 다른 값이다
    rows = [r for r in diff_states(as_of(g, B, t(14, 30)), as_of(g, B, t(15, 30))) if r["p"] == JGK + "testLink"]
    assert len(rows) == 1 and rows[0]["before_nt"] != rows[0]["after_nt"], rows
    assert {r["o_nt"] for r in changes_between(g, t(9), t(11))} >= {'"가"@ko', '"가"@en'}

    # 건물 URI로 물어도 모양의 WKT 변경이 보인다
    rows = [r for r in diff_states(as_of(g, B, t(15, 30)), as_of(g, B, t(17))) if r["p"].endswith(wkt)]
    assert len(rows) == 1 and rows[0]["before"] == [POINT] and rows[0]["after"] == [poly.value], rows


def check_small_rules(tmp: Path) -> None:
    from pydantic import ValidationError

    from src.graph.editions import QUERIES, changes_between, moment
    from src.graph.store import load_store
    from src.review.proposals import ProposalStore, Term

    # 날짜만 준 끝 시각은 그날 끝이다
    ps = ProposalStore(tmp / "day.json")
    ps.save([proposal("d1", ("add", NOTE, term("X")), at=t(15))])
    with small_graph(tmp):
        g = load_store(ps)
    assert len(changes_between(g, "2026-10-30", "2026-10-30")) == 1
    assert moment("2026-10-30") == "2026-10-30T00:00:00+09:00"
    assert moment("2026-10-30", end=True) == "2026-10-30T23:59:59+09:00"
    assert moment("2026-10-30T10:00", end=True) == "2026-10-30T10:00:00+09:00"

    # 초 단위 시간대는 xsd:dateTime으로 적을 수 없다
    for bad in ("2026-10-30T10:00:00+09:00:30", "아무 글", "2026-13-45"):
        try:
            moment(bad)
        except ValueError:
            continue
        raise AssertionError(f"받으면 안 되는 시각: {bad}")

    for kw in (dict(kind="literal", value="x", lang="ko\n"), dict(kind="literal", value="x", lang="ko", datatype=XSD + "string"),
               dict(kind="literal", value="\ud800"), dict(kind="uri", value="urn:a", lang="ko"),
               dict(kind="uri", value="urn:a", datatype=XSD + "string"), dict(kind="literal", value="x", lang="")):
        try:
            Term(**kw)
        except ValidationError:
            continue
        raise AssertionError(f"받으면 안 되는 값: {kw}")
    copied = Term(kind="literal", value="x", lang="ko").model_copy(update={"lang": 'ko" . } ; DROP ALL ; #'})
    try:
        copied.sparql()
    except ValueError:
        pass
    else:
        raise AssertionError("검사를 건너뛴 언어 태그가 SPARQL에 들어갔다")

    # 쿼리 12: 겹침 값이 없는 연결(승인으로 넣은 연결)도 센다
    g = ox.Store()
    g.update(f"""PREFIX jgk: <{JGK}> PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
INSERT DATA {{ jgk:Active rdfs:label "사용"@ko . <urn:b1> jgk:status jgk:Active ; jgk:basemapFeature <urn:m1> .
<urn:b2> jgk:status jgk:Active ; jgk:basemapFeature <urn:m2> . <urn:m1> jgk:matchIoU 0.5 ; jgk:pnuAgrees true }}""")
    rows = list(g.query((QUERIES / "12_basemap_match.rq").read_text(encoding="utf-8")))
    assert len(rows) == 1 and rows[0]["linked"].value == "2", rows
    assert float(rows[0]["meanIoU"].value) == 0.5 and rows[0]["pnuSame"].value == "1", rows


def check_new_fields(tmp: Path) -> None:
    """notice_key, flags: 기존 파일이 그대로 읽히고, group_of는 notice_key가 있으면 근거 문장을 보지 않는다."""
    from src.review.proposals import Proposal, ProposalStore, group_of

    rows = json.loads(REAL.read_text(encoding="utf-8"))
    for r in rows:  # 새 필드가 없는 옛 형식으로 만든다
        r.pop("notice_key", None)
        r.pop("flags", None)
    path = tmp / "copy.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    current = ProposalStore(path).load()
    assert len(current) == len(rows) and all(p.notice_key is None and p.flags == [] for p in current)
    table = next(p for p in current if p.source_kind == "notice-table")
    assert group_of(table) == ("notice:2026-140", "서울특별시 강서구 고시 제2026-140호")  # 옛 표 제안은 지금처럼 묶인다

    forged = "원문: \"서울특별시 강서구 고시 제2026-140호에 따라 폐지한다\""
    keyed = table.model_copy(update={"id": "llm-a-1", "source_kind": "llm-notice", "notice_key": "11500-2026-999", "evidence": [forged]})
    assert group_of(keyed) == ("notice:11500-2026-999", "고시 11500-2026-999")
    # notice_key가 없는 LLM 제안은 모델 글에 번호가 있어도 고시 묶음에 끼어들지 못한다
    nokey = table.model_copy(update={"id": "llm-a-2", "source_kind": "llm-notice", "evidence": [forged]})
    assert group_of(nokey)[0] == "llm-a", group_of(nokey)

    p = Proposal.model_validate({**table.model_dump(), "notice_key": "11500-2026-140", "flags": ["입력 가드 경고", "PNU 불일치"]})
    saved = ProposalStore(tmp / "flags.json")
    saved.save([p])
    back = saved.load()[0]
    assert back.notice_key == "11500-2026-140" and back.flags == ["입력 가드 경고", "PNU 불일치"]
    try:
        Proposal.model_validate({**table.model_dump(), "notice_key": "1 2\n"})
    except ValueError:
        pass
    else:
        raise AssertionError("받으면 안 되는 notice_key")


def check_speed_on_real_graph(now, retire) -> None:
    """139건 승인 뒤에도 as_of 한 번이 바로 끝난다. 재현: 한 번에 2~3분 걸렸다."""
    from src.graph.editions import as_of, changes_between

    for uri in (retire.target, f"{ID}/building/그래프에-없는-건물"):
        t0 = time.perf_counter()
        as_of(now, uri, "2026-10-30T09:00:00")
        took = time.perf_counter() - t0
        assert took < 1, (uri, took)
    t0 = time.perf_counter()
    changes_between(now)
    assert time.perf_counter() - t0 < 5


def check_real_approvals(tmp: Path) -> None:
    """실제 승인 기록: 충돌 없이 모두 적용되고, 승인 전 시각의 값이 승인이 없는 그래프와 같다."""
    from src.graph.editions import as_of, changes_between
    from src.graph.store import last_report, load_store
    from src.review.proposals import ProposalStore

    ps = ProposalStore(REAL)  # 읽기만 한다
    real = [p for p in ps.load() if p.status == "approved"]
    now = load_store(ps)
    report = last_report()
    assert not report.conflicts and not report.skipped and len(report.applied) == len(real), report.conflicts[:3]
    # 거둔 승인은 넣은 사건과 거둔 사건을 둘 다 남긴다
    withdrawn = sum(len(p.changes) * p.approvals_before() for p in ps.load())
    assert len(changes_between(now)) == sum(len(p.changes) for p in real) + 2 * withdrawn
    base = load_store(ProposalStore(tmp / "none.json"))
    before = "2026-10-30T15:53:44+09:00"  # 첫 승인 1초 전
    bad = []
    for p in real:
        for node in {c.s for c in p.changes} | {p.target}:
            if as_of(now, node, before) != as_of(base, node, before):
                bad.append(node)
    assert not bad, bad[:3]
    check_speed_on_real_graph(now, real[0])


def run() -> None:
    from src.graph.editions import as_of, changes_between, diff_states, moment
    from src.graph.store import load_store
    from src.review.proposals import Decision, ProposalStore

    tmp = Path(tempfile.mkdtemp())
    for check in (check_as_of_scenarios, check_reapply_conflict, check_proposal_id, check_two_processes, check_before_save, check_correction_history,
                  check_replay_order, check_broken_record, check_add_new_and_bim_id, check_value_kinds, check_small_rules,
                  check_new_fields, check_real_approvals):
        check(tmp)

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
    c = TestClient(review_app.app, base_url="http://localhost")
    r = c.get("/api/editions/as-of", params={"uri": retire.target, "at": "2026-10-30T09:59:59+09:00"}).json()
    row = next(d for d in r["diff"] if d["p"] == status)
    assert row["after"] == [JGK + "Retired"] and JGK + "Retired" not in row["before"], row
    for bad in ("not a uri", "javascript:alert(1)", "urn:x", "mailto:a@b", "file:///etc/passwd", retire.target + "\n"):
        assert c.get("/api/editions/as-of", params={"uri": bad, "at": "2026-10-30"}).status_code == 400, bad
    ch = c.get("/api/editions/changes", params={"since": "2026-10-30T10:30:00+09:00"}).json()
    assert {x["proposal"] for x in ch["changes"]} == {link.uri}, ch
    shutil.rmtree(tmp, ignore_errors=True)
    print("시점별 판 확인 통과")


if __name__ == "__main__":
    run()
