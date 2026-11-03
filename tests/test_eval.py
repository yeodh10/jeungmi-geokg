"""평가 채점 규칙 확인 (API를 부르지 않는다).

실행: python -m tests.test_eval
"""
from __future__ import annotations

B = "https://yeodh10.github.io/jeungmi-geokg/id"


def run() -> None:
    from src.eval.nl_eval import grade
    from src.eval.notice_eval import grade as notice_grade
    from src.llm.notice import ExtractedChange, NoticeExtraction, Target

    # 개수: 숫자 칸이 맞거나, 고른 객체 수가 맞으면 맞다
    q = {"kind": "count"}
    assert grade(q, 3, {"rows": [["3"]]})["correct"]
    assert grade(q, 2, {"rows": [[f"{B}/building/a", "POINT (1 2)"], [f"{B}/building/b", None]]})["correct"]
    assert not grade(q, 2, {"rows": [[f"{B}/building/a"], [f"{B}/building/a/geometry"]]})["correct"]
    # 객체 목록: 집합이 같아야 맞다. 모양 노드는 세지 않는다
    q = {"kind": "entities"}
    r = grade(q, [f"{B}/x/1", f"{B}/x/2"], {"rows": [[f"{B}/x/1", f"{B}/x/1/geometry"]]})
    assert not r["correct"] and r["precision"] == 1.0 and r["recall"] == 0.5
    # 값: 정답이 다 있고 행이 정답의 3배 이하. 빈 정답은 빈 결과여야 맞다
    q = {"kind": "values"}
    assert grade(q, ["양천로"], {"rows": [["양천로", "로"]]})["correct"]
    assert not grade(q, ["a"], {"rows": [["a"], ["b"], ["c"], ["d"]]})["correct"]
    assert grade(q, [], {"rows": []})["correct"] and not grade(q, [], {"rows": [["학교"]]})["correct"]
    assert grade(q, ["4"], {"rows": [["4.0"]]})["correct"]  # 숫자는 값으로 견준다
    # 있다 없다
    assert grade({"kind": "boolean"}, False, {"boolean": False, "rows": []})["correct"]

    # 고시공고: 근거 문장과 주소는 원문에 있어야 하고(띄어쓰기 무시), 정답 대상, 종류, 날짜를 본다
    body = "강서구 화곡동 359-126(화곡1동)\n폐쇄일자: 2023. 10. 4.(수) 부터"
    ext = NoticeExtraction(is_spatial=True, changes=[ExtractedChange(
        change_type="facility_closed", summary="경로당 폐쇄", quote="강서구  화곡동 359-126(화곡1동)",
        target=Target(jibun_address="화곡동 359-126", name="까치산경로당"), effective_date="2023-10-04")])
    gold = {"is_spatial": True, "targets": ["화곡동 359-126"], "change_types": ["facility_closed", "other"],
            "effective_date": "2023-10-04"}
    g = notice_grade(ext, body, gold)
    assert g["quote_exact"] == [True] and g["grounded"] == [True], g
    assert g["is_spatial_ok"] and g["targets_found"] == [True] and g["change_types_ok"] and g["date_ok"], g
    made_up = ext.model_copy(update={"changes": [ext.changes[0].model_copy(update={
        "quote": "지어낸 문장", "target": Target(road_address="양천로 999")})]})
    g = notice_grade(made_up, body, None)
    assert g["quote_exact"] == [False] and g["grounded"] == [False], g
    check_report()
    print("평가 채점 규칙 확인 통과")


def check_report() -> None:
    """report.py는 문서의 자동 표 표시 사이만 다시 쓰고, 사람이 고친 글은 그대로 둔다. 표시가 없으면 문서를 건드리지 않는다."""
    import json
    import tempfile
    from pathlib import Path

    import src.eval.report as report

    usage = {"input_tokens": 1, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "output_tokens": 1}
    nl = [{"id": "q01", "question": "옛 문구로 잰 질문", "kind": "count", "correct": False, "gold": 3, "n_rows": 1,
           "llm_calls": 1, "seconds": 1.0, "usage": usage}]
    nt = [{"no": "1", "title": "고시", "is_spatial": True, "changes": [{"change_type": "other"}], "quote_exact": [True],
           "grounded": [], "has_gold": False, "seconds": 2.0, "usage": usage}]
    saved = report.EVAL, report.OUT, report.QUESTIONS
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        (tmp / "nl_eval.json").write_text(json.dumps(nl, ensure_ascii=False), encoding="utf-8")
        (tmp / "notice_eval.json").write_text(json.dumps(nt, ensure_ascii=False), encoding="utf-8")
        (tmp / "q.yaml").write_text("- id: q01\n  question: 지금 문구\n", encoding="utf-8")
        report.EVAL, report.OUT, report.QUESTIONS = tmp, tmp / "llm-eval.md", tmp / "q.yaml"
        try:
            report.main()  # 문서가 없으면 처음 꼴로 만든다
            first = report.OUT.read_text(encoding="utf-8")
            assert first.count("<!-- 자동 시작:") == 4 and "지금 문구 (문구 바꿈" in first and "옛 문구로 잰 질문" not in first, first
            assert "운영과 같은 프롬프트" not in first and "결과 파일을 만든 때의 프롬프트" in first
            edited = first.replace("## 채점을 고친 기록", "사람이 고친 글.\n\n## 채점을 고친 기록").replace("| 문서 | 1건", "| 문서 | 999건")
            report.OUT.write_text(edited, encoding="utf-8")
            report.main()
            again = report.OUT.read_text(encoding="utf-8")
            assert "사람이 고친 글." in again and "| 문서 | 1건" in again and "999건" not in again, again
            report.main()
            assert report.OUT.read_text(encoding="utf-8") == again, "다시 돌려도 같다"
            broken = again.replace("<!-- 자동 끝: notice-table -->", "")
            report.OUT.write_text(broken, encoding="utf-8")
            try:
                report.main()
            except SystemExit as e:
                assert e.code == 1
            else:
                raise AssertionError("표시가 깨진 문서는 바꾸지 않고 실패로 끝나야 한다")
            assert report.OUT.read_text(encoding="utf-8") == broken
        finally:
            report.EVAL, report.OUT, report.QUESTIONS = saved
    # 저장소의 문서에도 표시가 다 있다
    doc = (Path(__file__).resolve().parents[1] / "docs" / "llm-eval.md").read_text(encoding="utf-8")
    assert sorted(m.group(2) for m in report.MARK.finditer(doc)) == ["nl-misses", "nl-table", "notice-seen", "notice-table"]


def test_eval():
    run()


if __name__ == "__main__":
    run()
