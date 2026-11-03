"""고시공고처럼 믿을 수 없는 공문서 전용 신호.

rules.py는 사람이 챗봇에 치는 일반 공격을 본다. 여기서는 이 프로젝트를 노리는 글을 본다.
점수 55 이상은 하나만 나와도 막는 확실한 신호고, 30은 사람이 보도록 검토로 넘기는 넓은 신호다.

막는 것: 프롬프트를 지칭한 명령, 추출 형식의 필드 이름, 역할 표시 위조, 기계를 불러 없앤 것으로
적으라는 지시, 건물을 없앤 것으로 적으라는 명령문.
검토로 넘기는 것: 프롬프트 같은 낱말만 나옴, 기계를 부르지만 내용이 평범함, 문서를 읽는 사람에게 하는 말,
명령형이 아닌 일괄 철거 문장. 교육 공고에 '프롬프트'가 나오고, 철거 고시에 '모든 건축물'이 나온다.
행정 글에 흔한 낱말(이전, 무효, 폐지, 철거, 시스템, 프로그램)은 그것만으로 잡지 않는다.

뷰는 normalize.views()가 만든 것을 rules.py와 함께 쓴다. 공격 문장을 줄바꿈이나 표 칸으로 쪼개도 이어서 본다.
모든 반복은 횟수를 묶었다. 어떤 입력도 시간이 길이의 제곱으로 늘면 안 된다.
"""
from __future__ import annotations

import re
import time

from . import normalize

# 앞뒤가 영어 글자면 메일(mail), 태국(thai)의 일부다. '로봇', '건설기계'는 글을 읽는 기계가 아니다
MACHINE = r"(?:(?<![a-z])ai(?![a-z])|a\.i\.|인공\s*지능|챗\s*봇|언어\s*모델|모델|판독\s*(?:기|엔진|모델|프로그램|시스템)|ocr(?:\s*엔진)?|llm|gpt|claude|클로드|어시스턴트|assistant|에이전트|agent|(?<!로)봇|추출기|파서|분류기|(?<!건설)기계|자동\s*(?:처리|추출|분석)\s*(?:기|도구|시스템|프로그램|모델))"
CALL = r"\s*(?:은|는|께|에게|님|들|,|:|reading)"
# 명령형 끝말. '바랍니다'가 있어 혼자서는 신호가 못 된다
# 앞의 글자 검사는 시간 때문이다. 이 말은 건너뛰기 뒤에 붙어 글자마다 시도된다. GONE도 같다
ORDER = r"(?=[하해어아시세주바요할따pms])(?:하라|해라|어라|아라|시오|세요|주시기|주길|바랍니다|바람|요망|할\s*것|따를\s*것|please|must|should)"
WRITE = r"(?:표시|기록|분류|입력|적어|적으|적는|적습|추출|반환|출력|mark|record|flag|classify|set|return|output)"
RECORD = rf"(?:{WRITE}|등록|넣|처리)"
GONE = r"(?=[폐철멸말삭없사dra])(?:폐지|철거|멸실|말소|삭제|없어진|사라진|demolish\w{0,8}|retire\w{0,4}|remov\w{0,4}|delet\w{0,4}|abolish\w{0,8})"
ALL = r"(?:모든|모두|전체|전부|일체|일괄|빠짐\s*없이|all|every|entire)"
OBJECT = r"(?:건물|건축물|주소|도로명\s*주소|객체|시설|필지|building|address|object|feature|parcel)"
# 도로명주소의 끝부분. '화곡로 302', '마곡중앙로 161-8'
ROAD = r"(?<=[가-힣0-9])(?:로|길)\s*\d{1,5}(?:-\d{1,5})?"
# '지시 문서', '지시 문건'은 공사 감독에서 흔하다
PROMPT = r"(?:프롬프트|prompt|시스템\s*메시지|시스템\s*지시|system\s*(?:message|instruction)s?|지시\s*문(?![서건])|명령\s*문(?![서건]))"
SPILL = r"(?:출력|적어|적으|옮겨|복사|공개|알려|보여|인용|반복|되풀이|무시|따르|따라|지우|지워|삭제|바꾸|바꿔|덮어)"
# '이 문서를 처리하는'까지. '읽어 보시고', '읽으신 분'은 사람에게 하는 말이다
READS = r"(?:이|본|해당)\s*(?:문서|글|고시|공고)\s*(?:를|을)\s*(?:읽(?!어\s*보시|으시|으신)|처리|분석|요약|추출|판독)"
FIELDS = "change_type|is_spatial|new_value|effective_date|zone_kind|building_demolished|building_use_change|building_name_change|address_change|facility_closed|building_new|zone_designated|zone_released|plan_approved|project_completed|road_address|jibun_address"

SIGNATURES: list[tuple[str, str, int]] = [
    (rf"{PROMPT}\s*(?:(?:전문|원문|내용|전체|문구)\s*(?:을|를|은|는|도|만)?|을|를|은|는|도|만)[^.。!?]{{0,30}}{SPILL}[^.。!?]{{0,12}}{ORDER}", "프롬프트를 지칭한 명령", 60),
    (PROMPT, "모델을 가리키는 말", 30),
    # \b는 한글 조사 앞을 경계로 보지 않아서 ASCII 글자만 따로 본다. change-type, changeType도 같은 이름이다
    (r"(?<![a-z0-9_])(?:" + FIELDS.replace("_", r"[\s_-]{0,3}") + r"|sparql)(?![a-z0-9_])", "추출 형식의 필드 이름", 60),
    (r"(?<![a-z0-9_])(?:quote|target|changes|summary)(?:에는|에|은|는|을|를|칸|란)", "추출 형식의 필드 이름", 60),
    (r"<\|[^|\n]{1,30}\|>|\[\s*/?\s*(?:inst|sys)\s*\]|<\s*/?\s*(?:system|assistant|user|developer|instructions?)\s*>|<<\s*/?\s*sys\s*>>|<\s*/?\s*(?:start|end)_of_turn\s*>|<<<[^<>\n]{0,20}(?:원문|고시|문서)[^<>\n]{0,20}>>>", "역할 표시 위조", 60),
    (r"<!--|<script|<img\s|!\[[^\]\n]{0,80}\]\(\s*http", "숨긴 글이나 유출 통로", 60),
    (rf"{MACHINE}{CALL}[^.。!?]{{0,40}}{GONE}[^.。!?]{{0,12}}{RECORD}", "문서를 읽는 기계에게 하는 지시", 55),
    (rf"{READS}\S{{0,8}}\s*(?:자동\s*)?(?:(?<![a-z])ai(?![a-z])|인공\s*지능|모델|프로그램|시스템|도구|엔진|판독기|기계)[^.。!?]{{0,60}}{ORDER}", "문서를 읽는 기계에게 하는 지시", 55),
    (rf"{GONE}[^.。!?]{{0,6}}(?:로|으로)\s*{WRITE}[^.。!?]{{0,8}}{ORDER}", "없앤 것으로 적으라는 지시", 55),
    (r"(?:mark|record|flag|classify|set)\b[^.。!?]{0,40}\bas\s+" + GONE, "없앤 것으로 적으라는 지시", 55),
    (rf"{ROAD}[^.。!?]{{0,12}}(?:삭제|제거|말소|폐지)\s*(?:하|해)[^.。!?]{{0,8}}{ORDER}", "건물 하나를 지우라는 지시", 55),
    (rf"{ALL}[^.。!?]{{0,12}}{OBJECT}[^.。!?]{{0,20}}{GONE}[^.。!?]{{0,12}}{RECORD}[^.。!?]{{0,8}}{ORDER}", "건물을 한꺼번에 없앤 것으로 적으라는 지시", 55),
    (rf"{ALL}[^.。!?]{{0,12}}{OBJECT}[^.。!?]{{0,12}}{RECORD}[^.。!?]{{0,12}}{GONE}[^.。!?]{{0,8}}{ORDER}", "건물을 한꺼번에 없앤 것으로 적으라는 지시", 55),
    (r"(?:새|새로운|다음|아래)\s*(?:지시|지침|명령|임무|역할)\s*(?:사항)?\s*[:：\-]|(?:새|새로운)\s*규칙\s*[:：\-]", "새 지시를 내리는 글", 55),
    (r"(?:위|앞|앞선|이전|상기|종전)\S{0,3}\s*(?:모든\s*)?(?:내용|지시|지침|규칙|안내|설명)\S{0,3}\s*(?:는|은|을|를)?\s*(?:참고\s*하지|따르지|고려\s*하지|신경\s*쓰지|상관\s*하지)\s*(?:마|말|않아도)", "앞의 지시를 따르지 말라는 글", 55),
    # 여기부터 검토로 넘기는 넓은 신호
    (rf"{MACHINE}{CALL}[^.。!?]{{0,40}}{ORDER}", "기계를 부르는 말", 30),
    (rf"{READS}[^.。!?]{{0,60}}{ORDER}", "문서를 읽는 쪽에게 하는 말", 30),
    (rf"{ALL}[^.。!?]{{0,12}}{OBJECT}[^.。!?]{{0,20}}{GONE}[^.。!?]{{0,12}}{RECORD}", "건물을 한꺼번에 없앤다는 글", 30),
    (r"새\s*기준\s*[:：]", "새 기준을 내리는 글", 30),
]
_COMPILED = [(re.compile(rx, re.IGNORECASE | re.DOTALL), desc, w, not normalize.HANGUL.search(rx)) for rx, desc, w in SIGNATURES]


def scan(views: list[normalize.View], deadline: float | None = None) -> list[tuple[str, int]]:
    """맞은 (설명, 점수) 목록. deadline(thread_time 시각)을 넘기면 거기까지 찾은 것을 돌려준다."""
    seen: dict[str, int] = {}
    for _, view, short in views:
        for rx, desc, w, no_hangul in _COMPILED:
            if deadline is not None and time.thread_time() > deadline:
                return list(seen.items())
            if w > seen.get(desc, 0) and rx.search(short if no_hangul else view):
                seen[desc] = w
    return list(seen.items())
