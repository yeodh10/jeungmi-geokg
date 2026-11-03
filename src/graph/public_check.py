"""공개 전 검사: 만든 공개 사이트(data/processed/site)를 처음부터 끝까지 보고, 공개하면 안 되는 것이 하나라도 있으면 실패한다 (명세 6절).

실행: python -m src.graph.public_check [사이트 폴더]      (publish, appdata 뒤. src/pipeline.py의 마지막 단계)
결과: 문제가 없으면 '통과'와 숫자를 보여 주고, 있으면 문제 목록을 보여 주고 종료 코드 1로 끝난다.

검사 (대상은 HTML, Turtle, JSON, GeoJSON, JS, CSS. assets/vendor/의 라이브러리, 타일, 글꼴은 비밀 값만 본다)
  보류 주소       /id/road-segment/, /id/basemap/, /id/digital-map/, /id/public-facility/ 주소나 폴더
  보류 관련 속성  facesRoad, basemapFeature 같은 보류 자료에서만 쓰는 속성 이름 (어휘 def/는 예외)
  보류 출처       config/area.yaml hold_sources의 자료 이름이 출처 값이나 카드 sources에 나옴 (def/, 자료 탭 sources.json은 예외)
  모양 대조       shapes.geojson, entrances.json, 점 건물, 센서, 역, 상호의 좌표가 공개 뷰의 같은 객체 모양과 소수 6자리까지 다름.
                  점만 있는 건물이 POINT_BUILDINGS동이 아님. 공개 건물이 places.json에 정확히 한 번씩 있지 않음
  보류 이름       보류 객체(공개하지 않는 상호 포함) 이름 가운데 공개 객체 이름에 없는 것이 places.json, card.json, questions.json,
                  businesses.json에 나옴 (places.json의 words 칸, 자료 탭은 예외)
  공개하지 않는 상호  그 상가업소번호가 어느 파일에든 나오거나 그 페이지가 있음
  보류 노드 연결  앱 자료의 장소 경로가 공개하지 않는 노드를 가리킴
  상태 수         places.json의 s로 센 '주소 연결됨', 사용, 주소 미연결이 STATUS_COUNTS와 다름
  앱 코드         assets/app/의 JS에 /api/, localhost, web/local. 공개 빌드에 local 모듈 파일
  비밀 값         Claude API 키와 키 환경변수 이름 모양 (vendor와 .pbf 바이트 포함)
  낡은 문구       '확인하는 중', '약 600m', '도엽 37608075'
  글자 규칙       가운뎃점 다섯 가지 (U+00B7, U+2027, U+30FB, U+318D, U+FF65)
  번호 이름       화면 이름에 20자리 넘는 번호 (공식 번호 칸은 예외)
  옛 자료         assets/map/, assets/search.json (지도 앱 자료는 assets/data/에 있다)
  지도 번호       src.graph.publish.site_problems (보류 자료 이름, 국가기본도와 수치지형도 번호, 보류 노드로 가는 링크)

두 지도 서비스 이름은 이 파일에 적지 않는다. tests/test_public_output.py가 유니코드 이스케이프로 따로 검사한다.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "processed" / "site"
TEXT = {".html", ".ttl", ".json", ".geojson", ".js", ".css", ".txt", ".xml", ".svg"}
HELD_PATHS = ("/id/road-segment/", "/id/basemap/", "/id/digital-map/", "/id/public-facility/")
HELD_KINDS = ("road-segment", "basemap", "digital-map", "public-facility")
# 보류 자료(도로 구간, 국가기본도, 수치지형도, 공공시설)에서만 쓰는 속성 이름 (src/graph/ontology.ttl)
HELD_PROPS = ("facesRoad", "basemapFeature", "digitalMapFeature", "matchIoU", "mapFloors", "pnuAgrees", "addressAgrees",
              "roadSegmentNo", "segmentOf", "roadClass", "roadWidth", "roadLength", "basemapId", "molitUfid", "ufid", "sheetCode",
              "featureCode", "mapKind", "mapUse", "mapKindCode", "mapUseCode", "agencyCode", "agencyName", "facilityType")
HELD_PROP_RE = re.compile(r"(?<![A-Za-z0-9_])(?:" + "|".join(HELD_PROPS) + r")(?![A-Za-z0-9_])")
# 비밀 값 모양. 이 파일 자체가 커밋 전 키 검사에 걸리지 않게 조각을 이어 만든다
SECRET = re.compile(b"sk-" + b"ant" + b"|ANTHROPIC_API" + b"_KEY")
STALE = ("확인하는 중", "약 600m", "도엽 37608075")
DOTS = re.compile("[\u00b7\u2027\u30fb\u318d\uff65]")
LONG_NO = re.compile(r"[0-9]{20,}")
APP_BANNED = ("/api/", "localhost", "web/local")
# 공개 근거 제안이 승인되면 바뀌는 숫자 (명세 6절 '상태 수', 0절). 사람이 공개 근거 제안을 승인하면 함께 고친다
STATUS_COUNTS = {"주소 연결됨": 7, "사용": 2162, "주소 미연결": 621}  # 2026-11-03 자리표시 잇기 7건 (GIS, 내비게이션용DB 근거)
POINT_BUILDINGS = 324
NAME_KEYS = {"n", "name"}                 # 앱 자료에서 화면 이름이 든 칸
PATH_KEYS = {"p", "path", "b", "lot"}     # 앱 자료에서 장소 경로가 든 칸
PATH_RE = re.compile(r"^(?:station|building|building-gis|entrance|parcel|road|admin-dong|legal-dong|basic-zone|sensor|business|"
                     r"notice|observation|proposal|road-segment|basemap|digital-map|public-facility)/[^\s]+$")


def strings(obj, key=None):
    """JSON 값의 모든 (칸 이름, 글자)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from strings(v, k)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v, key)
    elif isinstance(obj, str):
        yield key, obj


class Checker:
    def __init__(self, site: Path, view=None):
        from src.graph.public import BASE, UNIT_TAIL, display_name, hold_sources, kind_of, public_view
        self.site = site
        self.view = view or public_view()
        self.problems: list[str] = []
        v = self.view
        self.BASE = BASE
        ours = [s for s in v.triples if s.startswith(BASE + "/") and not s.endswith("/geometry") and "/change/" not in s]
        public = [s for s in ours if s not in v.held]
        held = [s for s in ours if s in v.held]
        self.public_paths = {s[len(BASE) + 1:] for s in public}
        self.held_paths = {s[len(BASE) + 1:] for s in held}
        pub_names = {(v.labels.get(s) or "").strip() for s in public} | {display_name(v, s) for s in public}
        # 상호의 화면 이름은 상호명과 지점명을 잇는다
        pub_names |= {f"{v.labels.get(s)} {v.value(s, 'branchName')}" for s in public if kind_of(s) == "business" and v.value(s, "branchName")}
        # 묶음 이름은 공개 이름에서 끝의 동 표기를 뺀 것이다 ('태영아파트 1동' -> '태영아파트')
        pub_names |= {UNIT_TAIL.sub("", n).strip() for n in pub_names}
        pub_names.discard("")
        self.pub_names = pub_names
        held_names = {(v.labels.get(s) or "").strip() for s in held} - pub_names - {""}
        self.held_names = held_names
        long_names = sorted((n for n in held_names if len(n) >= 4), key=len, reverse=True)
        self.held_name_re = re.compile("|".join(re.escape(n) for n in long_names)) if long_names else None
        # 보류 이름을 품은 공개 이름 (예: 공개 상호 이름 안에 보류 건물 이름이 들어 있는 경우는 누출이 아니다)
        self.covers = {n: [p for p in pub_names if n in p and p != n] for n in long_names}
        private = [s for s in v.private if not s.endswith("/geometry")]
        self.private_nos = {s.rsplit("/", 1)[-1] for s in private}
        self.private_no_re = re.compile("|".join(re.escape(x) for x in sorted(self.private_nos))) if self.private_nos else None
        hold = hold_sources()
        self.hold_ids = [h.rsplit("#", 1)[-1] for h in hold]
        self.hold_labels = {(v.labels.get(h) or "").strip() for h in hold} - {""}

    def add(self, where: str, what: str):
        self.problems.append(f"{where}: {what}")

    # 파일마다 ------------------------------------------------------------------------------------------------
    def files(self):
        for f in sorted(self.site.rglob("*")):
            if f.is_file():
                yield f, f.relative_to(self.site).as_posix()

    def run(self) -> list[str]:
        n_files = 0
        for f, where in self.files():
            n_files += 1
            data = f.read_bytes()
            for m in sorted(set(SECRET.findall(data))):
                self.add(where, f"비밀 값 모양 {m.decode()}")
            if where.startswith("assets/vendor/") or f.suffix.lower() not in TEXT:
                continue
            self.text_file(where, data.decode("utf-8", errors="replace"))
        self.n_files = n_files
        for kind in HELD_KINDS:
            if (self.site / "id" / kind).exists():
                self.add(f"id/{kind}/", "보류 주소의 폴더가 있다")
        for no in sorted(self.private_nos):
            if (self.site / "id" / "business" / no).exists():
                self.add(f"id/business/{no}/", "공개하지 않는 상호의 페이지가 있다")
        for old in ("assets/map", "assets/search.json"):
            if (self.site / old).exists():
                self.add(old, "옛 자료 파일이 남아 있다 (지도 앱 자료는 assets/data/에 있다)")
        app_dir = self.site / "assets" / "app"
        local = [f for f in self.site.rglob("*-local.js")] + ([self.site / "assets" / "local"] if (self.site / "assets" / "local").exists() else [])
        for f in local:
            self.add(f.relative_to(self.site).as_posix(), "로컬 전용 모듈이 공개 빌드에 들어 있다")
        if app_dir.exists() and (app_dir / "source-api.js").exists():
            self.add("assets/app/source-api.js", "로컬 자료 입구가 공개 빌드에 들어 있다")
        self.app_data()
        self.publish_layer()
        return self.problems

    def text_file(self, where: str, text: str):
        for p in HELD_PATHS:
            if p in text:
                self.add(where, f"보류 주소 {p}")
        if not where.startswith("def/"):
            for m in sorted(set(HELD_PROP_RE.findall(text))):
                self.add(where, f"보류 관련 속성 {m}")
            if where != "assets/data/sources.json":
                for h in self.hold_ids:
                    if h in text:
                        self.add(where, f"보류 출처 {h}")
        for s in STALE:
            if s in text:
                self.add(where, f"낡은 문구 '{s}'")
        for m in sorted(set(DOTS.findall(text))):
            self.add(where, f"가운뎃점 U+{ord(m):04X}")
        if self.private_no_re:
            for m in sorted(set(self.private_no_re.findall(text))):
                self.add(where, f"공개하지 않는 상호 번호 {m}")
        if where.startswith("assets/app/") and where.endswith(".js"):
            for b in APP_BANNED:
                if b in text:
                    self.add(where, f"앱 코드에 로컬 전용 낱말 {b}")
        if where.endswith(".html"):
            for tag in ("title", "h1"):
                for m in re.findall(rf"<{tag}[^>]*>(.*?)</{tag}>", text, re.S):
                    if LONG_NO.search(m):
                        self.add(where, f"<{tag}> 화면 이름에 긴 번호")
        if where.endswith("card.json") or where in ("assets/data/places.json", "assets/data/questions.json", "assets/data/businesses.json"):
            self.json_names(where, text)

    def json_names(self, where: str, text: str):
        """앱 자료 하나: 보류 이름, 화면 이름의 긴 번호, 보류 노드 경로, 카드 출처."""
        obj = json.loads(text)
        if where == "assets/data/places.json":
            obj = {k: v for k, v in obj.items() if k != "words"}
            text = json.dumps(obj, ensure_ascii=False)
        quick = bool(self.held_name_re and self.held_name_re.search(text))
        for key, s in strings(obj):
            if key in NAME_KEYS and LONG_NO.search(s):
                self.add(where, f"화면 이름에 긴 번호: {s[:60]}")
            if s in self.held_names:
                self.add(where, f"보류 이름 '{s}'")
            elif quick:
                for m in set(self.held_name_re.findall(s)):
                    if not any(c in s for c in self.covers.get(m, [])):
                        self.add(where, f"보류 이름 '{m}' (글 '{s[:40]}' 안)")
            if key in PATH_KEYS and PATH_RE.match(s):
                if s in self.held_paths or s.split("/", 1)[0] in HELD_KINDS:
                    self.add(where, f"공개하지 않는 장소 경로 {s}")
                elif s not in self.public_paths:
                    self.add(where, f"그래프에 없는 장소 경로 {s}")
        if where.endswith("card.json"):
            for side in ("object", "shape"):
                for name in (obj.get("sources") or {}).get(side, []):
                    if name in self.hold_labels:
                        self.add(where, f"보류 출처 {name}")

    # 앱 자료 구조 -------------------------------------------------------------------------------------------
    def load(self, name: str):
        f = self.site / "assets" / "data" / name
        if not f.exists():
            self.add(f"assets/data/{name}", "파일이 없다")
            return None
        return json.loads(f.read_text(encoding="utf-8"))

    def app_data(self):
        from src.graph.places import BUILDING_KINDS, geometry_json, objects, wkt_of
        v, BASE = self.view, self.BASE
        places = self.load("places.json")
        shapes = self.load("shapes.geojson")
        entrances = self.load("entrances.json")
        businesses = self.load("businesses.json")
        if places is None:
            return
        rows = places["places"]
        want = [s[len(BASE) + 1:] for s in objects(v, BUILDING_KINDS)]
        got = Counter(r["p"] for r in rows)
        for p in sorted(set(want) - set(got)):
            self.add("places.json", f"공개 건물이 빠졌다 {p}")
        for p, c in sorted(got.items()):
            if c > 1:
                self.add("places.json", f"건물이 {c}번 나온다 {p}")
            if p not in set(want):
                self.add("places.json", f"공개하지 않는 건물이 들어 있다 {p}")
        if [r["i"] for r in rows] != list(range(len(rows))):
            self.add("places.json", "i가 0부터 차례대로가 아니다")
        status = Counter(r.get("s", "사용") for r in rows)
        for k, n in STATUS_COUNTS.items():
            if status.get(k, 0) != n:
                self.add("places.json", f"상태 '{k}' {status.get(k, 0)}동 (기준 {n}동)")

        def expected(path):
            w = wkt_of(v, BASE + "/" + path)
            return geometry_json(w) if w else None

        points = [r for r in rows if r.get("sh") == 0]
        if len(points) != POINT_BUILDINGS:
            self.add("places.json", f"점만 있는 건물 {len(points)}동 (기준 {POINT_BUILDINGS}동)")
        for r in rows:
            g = expected(r["p"]) if r["p"] in set(want) else None
            if g is None:
                continue
            if (g["type"] == "Point") != (r.get("sh") == 0):
                self.add("places.json", f"점과 면 구분이 공개 뷰와 다르다 {r['p']}")
            if g["type"] == "Point" and r.get("pt") != g["coordinates"]:
                self.add("places.json", f"점 건물 좌표가 공개 뷰와 다르다 {r['p']}")
        if shapes is not None:
            seen = set()
            polygons = {r["p"] for r in rows if r.get("sh") == 1}
            for f in shapes["features"]:
                i = f["properties"].get("i")
                if not isinstance(i, int) or not 0 <= i < len(rows):
                    self.add("shapes.geojson", f"places.json에 없는 i {i}")
                    continue
                p = rows[i]["p"]
                seen.add(p)
                g = expected(p)
                if g is None or g["type"] != f["geometry"]["type"] or g["coordinates"] != f["geometry"]["coordinates"]:
                    self.add("shapes.geojson", f"모양이 공개 뷰와 다르다 {p}")
            for p in sorted(polygons - seen):
                self.add("shapes.geojson", f"면 건물 모양이 빠졌다 {p}")
            for p in sorted(seen - polygons):
                self.add("shapes.geojson", f"면 건물이 아닌데 모양이 있다 {p}")
        if entrances is not None:
            from src.graph.places import build_entrances
            truth = build_entrances(v)
            if entrances != truth:
                diff = sorted(set(entrances) ^ set(truth)) + sorted(k for k in set(entrances) & set(truth) if entrances[k] != truth[k])
                self.add("entrances.json", f"입구가 공개 뷰와 다르다 ({len(diff)}개 건물, 예: {diff[:3]})")
        for item in places.get("sensors", []) + ([places["station"]] if places.get("station") else []):
            g = expected(item["p"])
            if g is None or g["type"] != "Point" or item.get("pt") != g["coordinates"]:
                self.add("places.json", f"좌표가 공개 뷰와 다르다 {item['p']}")
        if businesses is not None:
            for item in businesses["items"]:
                if item.get("p") and item.get("pt"):
                    g = expected(item["p"])
                    if g is None or item["pt"] != g["coordinates"]:
                        self.add("businesses.json", f"상호 좌표가 공개 뷰와 다르다 {item['p']}")
        self.counts = {"places": len(rows), "shapes": len(shapes["features"]) if shapes else 0, "points": len(points),
                       "status": dict(status)}

    def publish_layer(self):
        """publish의 자체 검사(보류 자료 이름, 지도 번호, 보류 노드로 가는 링크)를 공개 출력 전체에 한 번 더 돌린다."""
        from src.graph.public import hold_sources
        from src.graph.publish import site_problems
        for p in site_problems(self.site, hold_sources(), self.view.held):
            self.problems.append("publish 검사: " + p)


def check(site: Path = OUT, view=None) -> tuple[list[str], dict]:
    c = Checker(site, view)
    problems = c.run()
    return problems, {"files": getattr(c, "n_files", 0)} | getattr(c, "counts", {})


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    site = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    problems, info = check(site)
    if problems:
        print(f"공개 전 검사 실패: {len(problems)}건")
        for p in problems[:200]:
            print(" -", p)
        if len(problems) > 200:
            print(f" ... 외 {len(problems) - 200}건")
        sys.exit(1)
    print("공개 전 검사 통과: " + json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    main()
