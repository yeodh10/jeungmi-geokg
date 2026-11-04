"""가짜 모델로 로컬 서버를 띄운다. 자유 질문 화면(web/local/ask-local.js)을 실제 API 호출 없이 확인할 때 쓴다 (명세 10절 WP6).

실행: python -m tests.serve_fake_llm [--port 8000]
  http://localhost:8000/map 의 묻기 탭 위 칸에서 질문하면 미리 정한 쿼리가 이 컴퓨터의 그래프에서 돈다.

바꾸는 것은 src.query.nl.parse 하나다. 서버 코드에는 가짜 모드를 넣지 않는다.
- ANTHROPIC_BASE_URL을 http://127.0.0.1:9 로 둔다. 가짜를 빠뜨린 호출이 있어도 아무 데도 닿지 못한다.
- 키가 없는 컴퓨터에서도 화면을 볼 수 있게, 키가 없으면 가짜 값('fake-key-not-used')을 넣는다. 이 값은 어디로도 나가지 않는다.
- 승인 기록은 임시 복사본을 쓴다 (--real-proposals를 주지 않으면). 화면에서 승인, 반려를 눌러도 실제 기록은 그대로다.
"""
from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "data" / "processed" / "review" / "proposals.json"
STATION = "https://yeodh10.github.io/jeungmi-geokg/id/station/1150010200106660040000001"

# 질문에 이 낱말이 있으면 이 쿼리를 쓴다. 맞는 낱말이 없으면 마지막 줄을 쓴다
CANNED = [
    ("학교", "증미역에서 500m 안에 있는 학교 이름의 건물을 찾았습니다.",
     f"""SELECT ?b ?name ?use WHERE {{
  ?b a jgk:Building ; rdfs:label ?name ; geo:hasDefaultGeometry/geo:asWKT ?w .
  OPTIONAL {{ ?b jgk:useName ?use }}
  FILTER(CONTAINS(?name, "학교"))
  FILTER(geof:sfIntersects(?w, {{{{NEAR:{STATION}:500}}}}))
}} ORDER BY ?name"""),
    ("공동주택", "용도가 공동주택이고 지상 15층 이상인 건물을 찾았습니다.",
     """SELECT ?b ?f WHERE {
  ?b a jgk:Building ; jgk:useName "공동주택" ; jgk:floorsAbove ?f .
  FILTER(?f >= 15)
} ORDER BY DESC(?f) ?b"""),
    ("", "지상 20층 이상인 건물을 층수가 높은 순으로 찾았습니다.",
     """SELECT ?b ?f ?use WHERE {
  ?b a jgk:Building ; jgk:floorsAbove ?f .
  OPTIONAL { ?b jgk:useName ?use }
  FILTER(?f >= 20)
} ORDER BY DESC(?f) ?b"""),
]


def fake_parse(client, schema, system, user, effort=None, model=None):
    """src.llm.client.parse와 같은 꼴. 모델을 부르지 않고 질문 낱말로 미리 정한 쿼리를 돌려준다."""
    text = user if isinstance(user, str) else str(user)
    question = text.split("\n", 1)[0]
    for word, explanation, sparql in CANNED:
        if word in question:
            return schema(sparql=sparql, explanation=explanation)
    raise AssertionError("맞는 쿼리가 없다")


def main() -> None:
    ap = argparse.ArgumentParser(description="가짜 모델로 로컬 서버 띄우기")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--real-proposals", action="store_true", help="임시 복사본 대신 실제 승인 기록을 읽는다 (쓰지 않도록 조심)")
    args = ap.parse_args()

    os.environ["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:9"   # 빠뜨린 호출이 있어도 닿지 못하게
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    os.environ["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:9"   # .env에 다른 값이 있어도 이 값을 쓴다
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        os.environ["ANTHROPIC_API_KEY"] = "fake-key-not-used"

    import src.query.nl as nl
    nl.parse = fake_parse

    from src.review import app as review_app
    from src.review.proposals import ProposalStore
    if not args.real_proposals:
        tmp = Path(tempfile.mkdtemp(prefix="jgk-fake-")) / "proposals.json"
        shutil.copy(REAL, tmp)
        review_app.proposals = ProposalStore(tmp)
        print(f"승인 기록 임시 복사본: {tmp}")
    import uvicorn
    print(f"가짜 모델 서버: http://localhost:{args.port}/map (실제 모델 호출 없음)")
    uvicorn.run(review_app.app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
