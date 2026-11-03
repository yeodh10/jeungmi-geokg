"""LLM에 넣기 전에 프롬프트 인젝션을 거르는 입력 가드.

rules.py와 normalize.py는 yeodh10/prompt-guard(커밋 787ddcd)의 룰 층을 가져와 고쳐 쓴다. 원본과 달라진 점:
- 공백 덩어리를 줄이고 모든 반복 횟수를 묶었다. 원본은 공백 1,600자에 2분이 넘게 걸렸다.
- 정규화 입구를 normalize.views() 하나로 합쳤다. rules.py의 제로폭, base64 사본과 쓰지 않는 detect_spans를 지웠다.
- 숨은 문자를 목록 대신 범주로 본다. 호환 자모 잇기, 키릴 대문자와 소형 대문자, 표 구분자와 HTML 태그 지우기,
  글자 사이 문장 부호 지우기, 줄바꿈 낀 영어 단어, 표 세로 읽기를 더했다. 복원은 단계마다 따로가 아니라 겹쳐서 건다.
- 디코드에 URL 안전 base64, 띄어 쓴 토큰, 공백 낀 16진수, HTML 엔터티, 유니코드 이스케이프를 더했다.
  깨진 바이트를 허용하고 두 겹까지 푼다. 뷰 수는 정해져 있다.
- 규칙 쪽: 상기, 앞선, 종전, 지침, 안내, 어순 뒤집기, 영어 어순 변형, 구분자 변형을 더했다.
  앞말 없는 '규칙 삭제'와 '무효'를 지시 무시에서 뺐고, 쿼리 문자가 있어야 하던 마크다운 그림 규칙은 그림 주소만 본다.
  CERTAIN_CATEGORIES와 BROAD로 규칙에 등급을 나눴다. scan()은 뷰 목록과 시한을 받는다.

규칙 층(이 파일, rules.py, normalize.py, domain.py)은 표준 라이브러리만 쓰고 입력을 밖으로 보내지 않는다.
LLM 층은 classifier.py에 있다. 고시 하나에 작은 모델 호출이 하나 더 는다. 자연어 질문에는 쓰지 않는다.
출력 층은 egress.py로, 뽑은 글에서 전화번호, 이메일, 비밀값을 가린다. 둘 다 prompt-guard에서 옮겼다.
처음 넣을 때는 규칙 층만 가져왔다가 블라인드 측정에서 공격 92개 중 23개만 막혀 나머지 층을 더했다.

입력 가드는 100%가 아니다. 정규식이 잡는 것은 난독화, 역할 표시, 필드 이름, 명령형 문장까지다.
위조 사실(건물을 폐지한다고 평서문으로 적은 글), 업무 문체의 작성 지시, 바꿔 말하기, 권위 사칭은 행정 글과
모양이 같아서 정규식으로 못 막는다. 낱말을 늘려 쫓으면 오탐만 는다. 이 유형은 뒤의 층이 맡는다:
고시는 LLM이 URI를 정하지 않고 원문 대조와 사람 승인을 거쳐야 그래프에 들어가고, 질의는 읽기 쿼리만 실행한다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import domain, egress, normalize, rules


class GuardBlocked(ValueError):
    """입력 가드가 막았다. 검토 API는 ValueError를 422로 돌려준다."""


@dataclass
class Verdict:
    decision: str  # 차단, 검토, 허용
    score: int
    reasons: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.decision == "차단"


def screen_output(text: str, kinds: tuple = ("pii", "secret", "channel", "leak"), system_prompt: str | None = None) -> tuple[str, list[str]]:
    """모델이 낸 글에서 개인정보, 비밀값, 유출 통로를 가린다. 가린 글과 가린 종류를 돌려준다."""
    found = [f for f in egress.find(text or "", system_prompt=system_prompt) if f["type"] in kinds]
    return egress.redact(text or "", found), sorted({f["label"] for f in found})


# 질문 길이 한계. 평가 질문은 가장 길어야 수십 글자다
MAX_QUESTION = 2000
# 공문서 길이 한계. 실제 고시 97건 중 가장 긴 것이 1,033자다. 길이는 NFKC로 늘어난 뒤에도 잰다
MAX_DOCUMENT = 50_000
# 검사 시간 상한(초). 넘기면 끝까지 못 본 글이므로 막는다. 정규식은 횟수를 묶어 여기까지 갈 일이 드물다
TIME_LIMIT = 1.2


def check(text: str, document: bool = False) -> Verdict:
    """document=False는 사람이 직접 친 질문, True는 고시공고처럼 믿을 수 없는 공문서다.

    질문은 prompt-guard의 guard.evaluate(use_llm=False)처럼 점수로 막는다. 질문은 읽기 쿼리로만 이어진다.
    공문서는 domain.py의 신호를 더하고, 확실한 규칙이 하나라도 맞을 때만 막는다. 넓은 규칙은 몇 개가
    맞아도 검토로 넘긴다. 글이 길수록 넓은 규칙이 쌓이기 쉬워서 점수 합으로 막으면 오탐이 된다.
    """
    text = text or ""
    limit, kind = (MAX_DOCUMENT, "공문서가") if document else (MAX_QUESTION, "질문이")
    base = normalize.canonical(text) if len(text) <= limit else None
    if base is None or len(base) > limit:
        return Verdict("차단", 100, [f"{kind} {limit}자를 넘는다"])
    deadline = time.thread_time() + TIME_LIMIT
    views = normalize.views(base)
    r = rules.scan(text, views, deadline)
    extra = domain.scan(views, deadline) if document else []
    reasons = ([d for d, _ in extra] + [f"{h['category']}: {h['desc']}" for h in r["hits"]])[:5]
    if time.thread_time() > deadline:
        return Verdict("차단", 100, ["검사가 시간 안에 끝나지 않았다"] + reasons)
    score = min(100, max(r["score"], max((w for _, w in extra), default=0)))
    if document:
        block = any(w >= rules.BLOCK_THRESHOLD for _, w in extra) or any(
            h["category"] in rules.CERTAIN_CATEGORIES and h["rule"] not in rules.BROAD for h in r["hits"])
    else:
        block = score >= rules.BLOCK_THRESHOLD
    flag = bool(extra) or r["score"] >= rules.FLAG_THRESHOLD or r["signals"]["zero_width"] or r["signals"]["encoded"]
    return Verdict("차단" if block else "검토" if flag else "허용", score, reasons)
