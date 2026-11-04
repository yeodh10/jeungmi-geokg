"""공개 출력 확인: 두 지도 서비스 이름이 없고, 공개 전 검사(src.graph.public_check)가 망가진 출력을 잡는지.

실행: python -m tests.test_public_output
- 두 지도 서비스(이 프로젝트의 모티브)의 한글, 영문 이름을 data/processed/site의 글자 파일과 .pbf 바이트에서 찾는다.
  이름은 이 파일 안에만 유니코드 이스케이프로 둔다 (명세 6절. 코드와 문서 어디에도 글자 그대로 쓰지 않는다). public_check는 이 검사를 하지 않으므로
  재배포 전에 이 시험을 꼭 돌린다.
- 실제 앱 자료를 임시 폴더에 복사한 뒤 보류 주소를 넣거나, 건물 모양 하나를 옮기거나, 점 건물 하나를 면으로 바꾸면 각각 실패하는지 본다.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "data" / "processed" / "site"
# 두 지도 서비스의 한글 이름과 영문 이름 (유니코드 이스케이프)
BANNED = ["\uce74\uce74\uc624", "\ub124\uc774\ubc84", "\u006b\u0061\u006b\u0061\u006f", "\u006e\u0061\u0076\u0065\u0072"]
TEXT = {".html", ".ttl", ".json", ".geojson", ".js", ".css", ".txt", ".xml", ".svg", ".md"}
APP_FILES = ("places.json", "shapes.geojson", "entrances.json", "businesses.json", "ask.json")


def company_hits(root: Path) -> list[str]:
    hits = []
    needles = [w.encode("utf-8") for w in BANNED] + [w.upper().encode() for w in BANNED[2:]] + [w.capitalize().encode() for w in BANNED[2:]]
    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        if f.suffix.lower() in TEXT:
            text = f.read_text(encoding="utf-8", errors="replace").lower()
            hits += [f"{f.relative_to(root).as_posix()}: {i}번 이름" for i, w in enumerate(BANNED) if w in text]
        elif f.suffix.lower() == ".pbf":
            data = f.read_bytes()
            hits += [f"{f.relative_to(root).as_posix()}: 바이트" for w in needles if w in data]
    return hits


def check_company_names() -> None:
    if not SITE.exists():
        print("  (공개 출력이 없어 이름 검사는 건너뜀)")
        return
    hits = company_hits(SITE)
    assert not hits, hits[:10]
    # 화면 코드와 설정에도 없어야 한다 (이 파일은 이스케이프로만 적었다)
    for d in ("web", "config", "src"):
        hits = company_hits(ROOT / d)
        assert not hits, hits[:10]


def mini_site(tmp: Path) -> Path:
    """실제 앱 자료 다섯 파일만 담은 작은 사이트. 공개 전 검사가 같은 규칙으로 본다."""
    d = tmp / "site" / "assets" / "data"
    d.mkdir(parents=True)
    for name in APP_FILES:
        shutil.copy(SITE / "assets" / "data" / name, d / name)
    return tmp / "site"


def check_public_check() -> None:
    if not (SITE / "assets" / "data" / "places.json").exists():
        print("  (앱 자료가 없어 공개 전 검사 실패 시험은 건너뜀)")
        return
    from src.graph.public import public_view
    from src.graph.public_check import check
    view = public_view()
    with tempfile.TemporaryDirectory() as t:
        site = mini_site(Path(t))
        problems, _ = check(site, view)
        assert not problems, problems[:5]

        # 1) 보류 주소를 넣는다
        bad = site / "assets" / "data" / "extra.json"
        bad.write_text(json.dumps({"x": "https://yeodh10.github.io/jeungmi-geokg/id/public-facility/1234567/"}), encoding="utf-8")
        problems, _ = check(site, view)
        assert any("보류 주소" in p for p in problems), problems[:5]
        bad.unlink()

        # 2) 건물 모양 하나를 다른 좌표로
        f = site / "assets" / "data" / "shapes.geojson"
        orig = f.read_text(encoding="utf-8")
        shapes = json.loads(orig)
        ring = shapes["features"][0]["geometry"]["coordinates"][0]
        ring[0][0] = round(ring[0][0] + 0.0001, 6)
        f.write_text(json.dumps(shapes), encoding="utf-8")
        problems, _ = check(site, view)
        assert any("모양이 공개 뷰와 다르다" in p for p in problems), problems[:5]
        f.write_text(orig, encoding="utf-8")

        # 3) 점 건물 하나를 면으로
        pf = site / "assets" / "data" / "places.json"
        places = json.loads(pf.read_text(encoding="utf-8"))
        r = next(x for x in places["places"] if x.get("sh") == 0)
        r["sh"] = 1
        x, y = r["pt"]
        shapes = json.loads(orig)
        shapes["features"].append({"type": "Feature", "properties": {"i": r["i"], "ug": r["ug"]}, "geometry": {
            "type": "Polygon", "coordinates": [[[x, y], [x + 0.0001, y], [x + 0.0001, y + 0.0001], [x, y]]]}})
        pf.write_text(json.dumps(places, ensure_ascii=False), encoding="utf-8")
        f.write_text(json.dumps(shapes), encoding="utf-8")
        problems, _ = check(site, view)
        assert any("점만 있는 건물 323동" in p for p in problems) and any("점과 면 구분" in p for p in problems), problems[:5]

        # 4) 공개하지 않는 상호의 번호를 넣는다
        f.write_text(orig, encoding="utf-8")
        private = sorted(s for s in view.private if not s.endswith("/geometry"))[0].rsplit("/", 1)[-1]
        r["sh"] = 0
        places["note"] = f"상호 {private}"
        pf.write_text(json.dumps(places, ensure_ascii=False), encoding="utf-8")
        problems, _ = check(site, view)
        assert any("공개하지 않는 상호 번호" in p for p in problems), problems[:5]


def run() -> None:
    check_company_names()
    check_public_check()
    print("공개 출력 확인 통과")


def test_public_output():
    run()


if __name__ == "__main__":
    run()
