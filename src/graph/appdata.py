"""지도 앱 자료를 공개 사이트 폴더에 쓴다 (명세 5.3절, 0-1절).

실행: python -m src.graph.appdata      (python -m src.graph.publish 뒤에. publish가 사이트 폴더를 새로 만든다)
출력: data/processed/site/
  assets/data/places.json       건물 행, 묶음, 길, 동네, 우편구역, 위에 건물이 없는 필지, 센서, 역, 칩, 찾기 낱말
  assets/data/shapes.geojson    면 건물 모양
  assets/data/entrances.json    건물별 입구
  assets/data/businesses.json   상호 찾기 (찾기창에 처음 입력할 때 받는다)
  assets/data/questions.json    묻기 탭의 미리 실행한 답
  assets/data/sources.json      자료 탭
  id/{종류}/{번호}/card.json     장소 카드 (관측과 모양 노드는 빼고, 공개 페이지가 있는 장소마다)
  (area.geojson은 publish가 쓴다)

공개 판단은 src.graph.public.public_view 한 곳에서 한다. 빌드 값(built)은 publish_report.json의 build와 같다
(앱 틀의 ?v= 값과 같아서 재배포 직후 옛 파일과 새 파일이 섞이지 않는다).
"""
from __future__ import annotations

import gzip
import json
import sys
import time
from pathlib import Path

from src.graph.cards import CardMaker
from src.graph.places import build_businesses, build_entrances, build_places, build_shapes, load_yaml
from src.graph.public import public_query_store, public_view
from src.graph.questions import build_questions

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "processed" / "site"


def dump(obj) -> bytes:
    # allow_nan=False: 브라우저는 NaN이 든 JSON을 읽지 못한다
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def build_sources(hidden_business: int) -> dict:
    cfg = load_yaml("sources.yaml")
    keep = ("name", "by", "as_of", "public", "used_for", "license", "url", "note", "reason")
    sources = [{k: (str(s[k]) if k != "public" else bool(s[k])) for k in keep if k in s} for s in cfg["sources"]]
    return {"v": 1, "basis": cfg["basis"], "sources": sources, "absent": cfg["absent"], "principle": cfg["principle"],
            "scope": cfg["scope"].strip(), "history": cfg["history"], "credits": cfg["credits"], "creditsNote": cfg["credits_note"],
            "hiddenBusinesses": hidden_business,
            "hiddenBusinessesNote": f"주택 건물 안 상호 {hidden_business:,}곳은 개인 집 주소일 수 있어 싣지 않았습니다."}


def write(out: Path = OUT) -> dict:
    t0 = time.perf_counter()
    report_path = out / "publish_report.json"
    if not report_path.exists():
        raise SystemExit("publish_report.json이 없습니다. python -m src.graph.publish를 먼저 실행하세요.")
    built = json.loads(report_path.read_text(encoding="utf-8"))["build"]
    view = public_view()
    qstore = public_query_store(view)
    places = build_places(view, built)
    shapes = build_shapes(view, places)
    entrances = build_entrances(view)
    businesses = build_businesses(view, places)   # 상호 칩의 n도 places에 적는다
    questions = build_questions(view, qstore)
    hidden = len([s for s in view.private if not s.endswith("/geometry")])
    sources = build_sources(hidden)
    data = out / "assets" / "data"
    data.mkdir(parents=True, exist_ok=True)
    sizes = {}
    for name, obj in (("places.json", places), ("shapes.geojson", shapes), ("entrances.json", entrances),
                      ("businesses.json", businesses), ("questions.json", questions), ("sources.json", sources)):
        b = dump(obj)
        (data / name).write_bytes(b)
        sizes[name] = {"bytes": len(b), "gzip": len(gzip.compress(b, 6))}

    maker = CardMaker(view, places, business_lines=businesses["items"])
    n, total = 0, 0
    for uri in maker.uris():
        d = out / uri.split("/jeungmi-geokg/", 1)[1]
        if not d.exists():   # 공개 페이지가 없는 장소는 카드도 만들지 않는다 (publish와 같은 공개 범위)
            raise RuntimeError(f"공개 페이지가 없는 장소의 카드를 만들려 했다: {uri}")
        b = dump(maker.card(uri))
        (d / "card.json").write_bytes(b)
        n += 1
        total += len(b)
    report = {"built": built, "buildings": places["count"]["buildings"], "places": places["count"]["places"],
              "groups": places["count"]["groups"], "questions": len(questions["items"]), "businessLines": len(businesses["items"]),
              "hiddenBusinesses": hidden, "cards": n, "cardBytes": total, "files": sizes,
              "firstScreenGzip": sizes["places.json"]["gzip"] + sizes["shapes.geojson"]["gzip"],
              "seconds": round(time.perf_counter() - t0, 1)}
    return report


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(write(), ensure_ascii=False))


if __name__ == "__main__":
    main()
