"""평가용 고시공고 원문을 강서구청 누리집에서 받는다.

실행: python -m src.eval.collect_notices [쪽수]   (기본 10쪽, '고시'로 제목 검색)
출력: data/raw/notices/eval/{mgtNo}.txt  (원문은 커밋하지 않는다. 정답은 eval/notice_gold.yaml에 번호로 적는다)
파일 머리에 제목, 번호, 날짜, 출처를 적어 src.llm.notice가 그대로 읽게 한다. 이미 받은 파일은 다시 받지 않는다.

사람 눈에 보이는 글만 LLM에 가도록 HTML을 브라우저처럼 읽어 숨긴 글을 버린다:
- 주석과, 안의 태그를 글로 읽는 요소(script, style, textarea, title, xmp, iframe, noscript, noembed, noframes, plaintext)는
  내용째 버린다. 이 안의 '</div>'가 바깥의 숨긴 요소를 닫지 못하게 태그보다 먼저 지운다.
- hidden, aria-hidden, template, 닫힌 details(첫 summary 빼고) 같은 요소는 안쪽까지 버린다.
- style 속성은 여럿이면 모두 보고, CSS 주석과 이스케이프(\\6e, dis\\play)를 푼 뒤 본다. display, visibility, opacity, 글자 크기,
  화면 밖 위치, 크기 0과 overflow, clip, clip-path, transform, 투명한 글자색을 숨김으로 본다.
- 브라우저와 HTML 읽는 법이 어긋날 때는 더 많이 버리는 쪽을 고른다(숨긴 요소를 브라우저보다 늦게 닫는다).
class로 숨긴 글이나 배경과 같은 글자색은 누리집의 CSS를 함께 봐야 해서 거르지 못한다. 그런 낌새(숨기는 이름의 class,
흰색에 가까운 글자색, 본문 안의 숨기는 CSS 규칙, 계산식 CSS 값)가 보이면 파일 머리에 '숨김의심:' 줄로 남긴다.
src.llm.notice가 그 문서의 제안 flags에 넣고, flags가 있는 제안은 묶음 승인에서 빠진다.
"""
from __future__ import annotations

import html
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "raw" / "notices" / "eval"
SITE = "https://www.gangseo.seoul.kr"
LIST = SITE + "/gs040301?srchKey=sj&srchText=%EA%B3%A0%EC%8B%9C&curPage={page}"
VIEW = SITE + "/gs040301/view?srchKey=sj&mgtNo={no}"
UNWRAP_ROUNDS = 5  # 엔티티를 겹겹이 싼 글을 푸는 횟수의 상한

# 브라우저가 안의 '<'를 태그로 읽지 않는 요소. 화면에 글로 나오지 않거나 본문으로 읽을 글이 아니라서 내용째 버린다
RAW_TAGS = {"script", "style", "textarea", "title", "xmp", "iframe", "noembed", "noframes", "noscript", "plaintext"}
HIDDEN_TAGS = {"head", "template", "canvas", "audio", "video", "object", "datalist"}  # 안의 글이 화면에 나오지 않는 요소
VOID_TAGS = {"area", "base", "basefont", "bgsound", "br", "col", "embed", "frame", "hr", "img", "input", "keygen", "link", "meta",
             "param", "source", "track", "wbr"}  # 닫는 태그가 없는 요소
# 끝 태그가 이 요소를 넘어 바깥 요소를 닫지 못한다 (HTML 표준의 scope 경계와 special 요소). 넘지 못하면 그 끝 태그는 버린다
SCOPE = {"applet", "caption", "html", "table", "td", "th", "marquee", "object", "template", "svg", "math"}
SPECIAL = SCOPE | {"address", "article", "aside", "blockquote", "body", "button", "center", "dd", "details", "dialog", "dir", "div",
                   "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "frameset", "h1", "h2", "h3", "h4", "h5", "h6",
                   "head", "header", "hgroup", "li", "listing", "main", "menu", "nav", "ol", "p", "pre", "search", "section", "select",
                   "summary", "tbody", "tfoot", "thead", "tr", "ul"}
TABLE_PARTS = {"table", "caption", "colgroup", "tbody", "thead", "tfoot", "tr", "td", "th"}

TAG_START = re.compile(r"<(?:!--|[!?]|/(?![a-z])|(/?)([a-z][^\t\n\f\r />]*))", re.I)
COMMENT_END = re.compile(r"-?>|.*?(?:--!?>|\Z)", re.S)  # 주석은 '-->'나 '--!>'에서 끝난다. '<!-->'는 빈 주석이다
ATTR_NAME = re.compile(r"[^\t\n\f\r />][^\t\n\f\r /=>]*")
UNQUOTED = re.compile(r"[^\t\n\f\r >]*")
SPACE = "\t\n\f\r "
SCRIPT_TOKEN = re.compile(r"<!--|-->|<script(?=[\t\n\f\r />])|</script(?=[\t\n\f\r />])", re.I)

# ---- CSS ----
CSS_COMMENT = re.compile(r"/\*.*?(?:\*/|\Z)", re.S)
CSS_ESCAPE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})[ \t\n\r\f]?|(\n)|(.))", re.S)
LENGTH = re.compile(r"([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)([a-z%]*)")
# 길이를 픽셀로 어림한다. %, vw 같은 상대 단위는 1을 10px로 본다(글자 크기의 %만 1을 0.16px로 본다)
UNIT_PX = {"": 1, "px": 1, "pt": 4 / 3, "pc": 16, "in": 96, "cm": 37.8, "mm": 3.78, "q": 0.945, "em": 16, "rem": 16, "ex": 8, "ch": 8,
           "%": 10, "vw": 10, "vh": 10, "vmin": 10, "vmax": 10}
OFFSCREEN = (-100, 2000)  # 이만큼 왼쪽, 위로 밀거나 오른쪽, 아래로 멀리 보낸 글은 화면 밖이다. 내어쓰기(-22.7pt 등)는 이보다 작다
MIN_FONT_PX = 4  # 이보다 작은 글자는 사람이 읽을 수 없다
MIN_OPACITY = 0.1  # 이보다 투명한 글자는 사람이 읽을 수 없다
LIGHT = 230  # 빨강, 초록, 파랑이 모두 이 이상이면 흰 바탕에서 안 보이는 글자색일 수 있다
CALC = re.compile(r"\b(?:var|calc|min|max|clamp|env|attr)\(")
WATCHED = {"display", "visibility", "content-visibility", "opacity", "filter", "font-size", "font", "left", "right", "top", "bottom",
           "inset", "text-indent", "margin", "margin-left", "margin-top", "margin-right", "margin-bottom", "padding-left", "height",
           "max-height", "width", "max-width", "transform", "translate", "scale", "clip", "clip-path", "color",
           "-webkit-text-fill-color"}
HIDE_CLASS = re.compile(r"hid(?:e|den)|blind|sr-?only|screen-?reader|visually|invisib|off-?screen|d-none|display-?none|collapse|clip",
                        re.I)


def get(url: str, tries: int = 4) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (jeungmi-geokg eval)"})
    for i in range(tries):  # 누리집이 가끔 느리게 답해 읽다가 끊긴다
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
            break
        except (TimeoutError, OSError):
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))
    try:  # 같은 누리집이 요청에 따라 UTF-8이나 EUC-KR로 준다
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp949", "replace")


# ---- CSS 읽기 ----

def css_unescape(s: str) -> str:
    """CSS 이스케이프를 푼다. '\\6e one' -> 'none', 'dis\\play' -> 'display'."""
    def one(m: re.Match) -> str:
        if m.group(1):
            n = int(m.group(1), 16)
            return chr(n) if 0 < n <= 0x10FFFF and not 0xD800 <= n <= 0xDFFF else "�"
        return "" if m.group(2) else m.group(3)
    return CSS_ESCAPE.sub(one, s)


def css_decls(style: str) -> list[tuple[str, str]]:
    """style 값을 (속성, 값) 목록으로. 주석과 이스케이프를 풀고 소문자로, 값의 공백은 하나로 줄인다."""
    out = []
    for part in css_unescape(CSS_COMMENT.sub(" ", style)).split(";"):
        name, sep, value = part.partition(":")
        if sep:
            value = re.sub(r"!\s*important\s*$", "", value.strip(), flags=re.I)
            out.append((name.strip().lower(), " ".join(value.lower().split())))
    return out


def px(value: str, percent: float = UNIT_PX["%"]) -> float | None:
    """길이 하나의 픽셀 어림. 길이가 아니면 None."""
    m = LENGTH.fullmatch(value.strip())
    if not m or m.group(2) not in UNIT_PX:
        return None
    return float(m.group(1)) * (percent if m.group(2) == "%" else UNIT_PX[m.group(2)])


def far(v: float | None) -> bool:
    return v is not None and not OFFSCREEN[0] < v < OFFSCREEN[1]


def color_of(value: str) -> tuple[float, float, float, float] | None:
    """글자색을 (빨강, 초록, 파랑, 불투명도)로. 읽지 못하는 꼴이면 None."""
    v = value.strip()
    if v == "transparent":
        return 0, 0, 0, 0
    if v == "white":
        return 255, 255, 255, 1
    m = re.fullmatch(r"#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})", v)
    if m:
        h = m.group(1) if len(m.group(1)) > 4 else "".join(c * 2 for c in m.group(1))
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16) / 255 if len(h) == 8 else 1
    m = re.fullmatch(r"rgba?\((.*)\)", v)
    parts = re.split(r"[\s,/]+", m.group(1).strip()) if m else []
    if len(parts) in (3, 4):
        try:
            nums = [float(p.rstrip("%")) * (2.55 if p.endswith("%") else 1) for p in parts[:3]]
            alpha = float(parts[3].rstrip("%")) / (100 if parts[3].endswith("%") else 1) if len(parts) == 4 else 1
        except ValueError:
            return None
        return nums[0], nums[1], nums[2], alpha
    return None


def color_problem(value: str, background: str | None = None) -> tuple[bool, set[str]]:
    """글자색으로 숨겼는가. 투명하거나 같은 요소의 바탕색과 같으면 숨김, 흰색에 가까우면 의심으로 남긴다."""
    c = color_of(value)
    if c is None:
        return False, set()
    if c[3] < MIN_OPACITY or (background is not None and color_of(background) == c):
        return True, set()
    return False, {"흰색에 가까운 글자색"} if min(c[:3]) >= LIGHT else set()


def css_hides(decls: list[tuple[str, str]]) -> tuple[bool, set[str]]:
    """선언 목록이 글을 숨기는가와, 거르지 못하는 숨김 낌새. 같은 속성이 여럿이면 하나라도 숨기면 숨김이다."""
    values: dict[str, list[str]] = {}
    notes = set()
    for name, value in decls:
        values.setdefault(name, []).append(value)
        if name in WATCHED and CALC.search(value):
            notes.add(f"계산식 CSS 값({name})")
    get_ = lambda name: values.get(name, [])
    numbers = lambda name, percent=UNIT_PX["%"]: [px(t, percent) for v in get_(name) for t in v.replace(",", " ").split()]

    def number(v: str) -> float | None:
        try:
            return float(v[:-1]) / 100 if v.endswith("%") else float(v)
        except ValueError:
            return None

    hide = any(v == "none" for v in get_("display")) or any(v in ("hidden", "collapse") for v in get_("visibility")) \
        or "hidden" in get_("content-visibility") or any(v != "none" for v in get_("clip-path"))
    hide |= any((n := number(v)) is not None and n < MIN_OPACITY for v in get_("opacity"))
    for v in get_("filter"):
        for arg in re.findall(r"opacity\(([^)]*)\)|alpha\(opacity=([\d.]+)", v):
            n = number(arg[0]) if arg[0] else (number(arg[1]) or 0) / 100
            hide |= n is not None and n < MIN_OPACITY
    hide |= any(n is not None and n < MIN_FONT_PX for n in numbers("font-size", 0.16))
    for v in get_("font"):  # 'font: 0/0 a'. '/' 뒤는 줄 높이라 보지 않는다
        tokens = v.replace("/", " / ").split()
        sizes = [t for i, t in enumerate(tokens) if (i == 0 or tokens[i - 1] != "/") and LENGTH.fullmatch(t)
                 and (LENGTH.fullmatch(t).group(2) or float(LENGTH.fullmatch(t).group(1)) == 0)]
        hide |= any((n := px(t, 0.16)) is not None and n < MIN_FONT_PX for t in sizes)
    # 화면 밖 위치. 가로는 내어쓰기(음수 text-indent와 양수 margin-left)를 함께 더해 본다
    for name in ("left", "right", "top", "bottom", "inset", "margin", "margin-top", "margin-bottom", "margin-right"):
        hide |= any(far(n) for n in numbers(name))
    hide |= any(far(n) for n in numbers("translate", 1))  # 옮기기의 %는 제 크기의 비율이다. 가운데 맞춤(-50%)은 화면 밖이 아니다
    side = lambda name: next((n for n in numbers(name) if n is not None), 0)
    margin_left = side("margin-left") or next((n for n in reversed(numbers("margin")) if n is not None), 0)
    hide |= far(side("text-indent") + margin_left + side("padding-left"))
    for v in get_("transform"):
        for fn, args in re.findall(r"([a-z0-9]+)\(([^)]*)\)", v):
            nums = [t for t in args.replace(",", " ").split()]
            if fn.startswith("scale"):
                hide |= any((n := number(t)) is not None and abs(n) < 0.05 for t in nums)
            elif fn.startswith("translate"):
                hide |= any(far(px(t, 1)) for t in nums)
            elif fn == "matrix" and len(nums) == 6 and all(number(t) is not None for t in nums):
                a, b, c, d, e, f = (number(t) for t in nums)
                hide |= abs(a * d - b * c) < 0.01 or far(e) or far(f)
            elif fn not in ("rotate", "skew", "skewx", "skewy"):
                notes.add(f"읽지 못한 transform({fn})")
    hide |= any((n := number(t)) is not None and abs(n) < 0.05 for v in get_("scale") for t in v.split())
    hide |= any(v.startswith("rect(") and set(re.split(r"[\s,]+", v[5:-1].strip())) != {"auto"} for v in get_("clip"))
    small = any(n is not None and n <= 3 for name in ("height", "max-height", "width", "max-width") for n in numbers(name))
    clipped = any(v in ("hidden", "clip") for name in ("overflow", "overflow-x", "overflow-y") for v in get_(name))
    hide |= small and clipped
    background = (get_("background-color") or get_("background") or [None])[-1]
    for name in ("color", "-webkit-text-fill-color"):
        for v in get_(name):
            h, n = color_problem(v, background)
            hide, notes = hide or h, notes | n
    return hide, notes


def style_sheet_notes(css: str) -> set[str]:
    """본문 안 style 요소의 규칙 가운데 글을 숨기는 것. 어느 요소에 걸리는지는 흉내 내지 못해 의심으로만 남긴다."""
    notes = set()
    for selector, block in re.findall(r"([^{}]*)\{([^{}]*)\}", CSS_COMMENT.sub(" ", css)):
        hide, more = css_hides(css_decls(block))
        if hide:
            notes.add(f"본문 style 요소에 숨기는 규칙({clean_note(selector)})")
        notes |= more
    return notes


def clean_note(s: str) -> str:
    """의심 표시에 넣을 남의 글(class 이름 등). 파일 머리 한 줄을 깨지 못하게 글자를 줄이고 자른다."""
    return re.sub(r"[^\w.#-]", "", s)[:30]


# ---- HTML 읽기 ----

def hidden(tag: str, attrs: list[tuple[str, str]]) -> tuple[bool, set[str]]:
    """이 요소가 안의 글을 숨기는가와, 거르지 못하는 숨김 낌새. style 속성이 여럿이면 모두 본다."""
    names = {k for k, _ in attrs}
    if tag in HIDDEN_TAGS or "hidden" in names or (tag == "dialog" and "open" not in names):
        return True, set()
    if any(k == "aria-hidden" and v.strip().lower() == "true" for k, v in attrs):
        return True, set()
    hide, notes = False, set()
    for k, v in attrs:
        if k == "style":
            h, n = css_hides(css_decls(v))
            hide, notes = hide or h, notes | n
        elif k == "class":
            notes |= {f"숨기는 이름의 class({clean_note(c)})" for c in v.split() if HIDE_CLASS.search(c)}
        elif k == "color" and tag == "font":
            h, n = color_problem(" ".join(v.lower().split()))
            hide, notes = hide or h, notes | n
    return hide, notes


def read_tag(src: str, pos: int) -> tuple[list[tuple[str, str]], int] | None:
    """태그 이름 뒤부터 속성을 브라우저처럼 읽는다. (속성 목록, '>' 다음 자리). 문서가 태그 안에서 끝나면 None."""
    attrs, n = [], len(src)
    while True:
        while pos < n and src[pos] in SPACE + "/":
            pos += 1
        if pos >= n:
            return None
        if src[pos] == ">":
            return attrs, pos + 1
        m = ATTR_NAME.match(src, pos)
        name, pos, value = m.group().lower(), m.end(), ""
        j = pos
        while j < n and src[j] in SPACE:
            j += 1
        if j < n and src[j] == "=":  # 따옴표는 '=' 바로 뒤에서만 값을 묶는다
            j += 1
            while j < n and src[j] in SPACE:
                j += 1
            if j < n and src[j] in "\"'":
                close = src.find(src[j], j + 1)
                if close < 0:
                    return None
                value, pos = src[j + 1:close], close + 1
            else:
                m = UNQUOTED.match(src, j)
                value, pos = m.group(), m.end()
        attrs.append((name, html.unescape(value)))


def raw_end(src: str, pos: int, tag: str) -> int:
    """글로만 읽는 요소의 내용이 끝나는 자리(끝 태그 다음). 끝 태그가 없으면 문서 끝이다."""
    if tag == "plaintext":
        return len(src)
    if tag == "script":  # '<!--' 안에 '<script>'가 다시 열리면 그다음 '</script>'는 끝이 아니다 (브라우저 규칙)
        state = "data"
        for m in SCRIPT_TOKEN.finditer(src, pos):
            token = m.group().lower()
            if token == "<!--" and state == "data":
                state = "escaped"
            elif token == "-->":
                state = "data"
            elif token == "<script" and state == "escaped":
                state = "double"
            elif token == "</script":
                if state == "double":
                    state = "escaped"
                else:
                    got = read_tag(src, m.end())
                    return len(src) if got is None else got[1]
        return len(src)
    m = re.compile(rf"</{tag}(?=[\t\n\f\r />])", re.I).search(src, pos)
    got = read_tag(src, m.end()) if m else None
    return len(src) if got is None else got[1]


@dataclass
class Element:
    tag: str
    hide: bool
    shut: bool = False  # 닫힌 details: 첫 summary 말고는 보이지 않는다
    summary: bool = False


class VisibleText:
    """보이는 글만 모은다. 숨긴 요소는 안쪽까지 버리고, 끝 태그가 경계를 넘지 못하면 닫지 않는다(브라우저와 같다).

    브라우저가 끝 태그 없이 요소를 닫는 경우(<p> 뒤의 <div> 등)는 흉내 내지 않는다. 그때는 숨긴 요소가 더 오래 열려 있어 더 많이 버린다.
    """

    def __init__(self):
        self.parts: list[str] = []
        self.notes: set[str] = set()
        self.stack: list[Element] = []
        self.hidden = 0  # 쌓인 요소 가운데 숨긴 요소의 수

    def feed(self, src: str) -> str:
        pos, n = 0, len(src)
        while pos < n:
            m = TAG_START.search(src, pos)
            if not m:
                self.data(src[pos:])
                break
            self.data(src[pos:m.start()])
            head = m.group()
            if head == "<!--":
                pos = COMMENT_END.match(src, m.end()).end()
            elif m.group(2) is None:  # <!doctype>, <![CDATA[ ]]>, <?xml ?>, </ 뒤 글자 아님: 브라우저는 '>'까지 주석으로 본다
                end = src.find(">", m.end())
                pos = n if end < 0 else end + 1
            else:
                got = read_tag(src, m.end())
                if got is None:  # 문서가 태그 안에서 끝났다. 브라우저도 이 태그를 버린다
                    break
                attrs, pos = got
                tag = m.group(2).lower()
                if m.group(1):
                    self.end(tag)
                elif tag in RAW_TAGS:
                    end = raw_end(src, pos, tag)
                    if tag == "style":
                        self.notes |= style_sheet_notes(src[pos:end])
                    pos = end
                else:
                    self.start(tag, attrs)
        return "".join(self.parts)

    def data(self, text: str) -> None:
        if text and not self.hidden and not (self.stack and self.stack[-1].shut):
            self.parts.append(html.unescape(text))

    def start(self, tag: str, attrs: list[tuple[str, str]]) -> None:
        if tag in VOID_TAGS:  # 브라우저는 <div .../>의 /를 무시하지만 빈 요소는 열지 않는다
            if tag == "link" and "stylesheet" in " ".join(v.lower() for k, v in attrs if k == "rel"):
                self.notes.add("본문 안에서 바깥 CSS를 불러온다")
            return
        hide, notes = hidden(tag, attrs)
        self.notes |= notes
        parent = self.stack[-1] if self.stack else None
        if parent and parent.shut:  # 닫힌 details 안에서는 첫 summary만 보인다
            if tag == "summary" and not parent.summary:
                parent.summary = True
            else:
                hide = True
        shut = tag == "details" and not any(k == "open" for k, _ in attrs)
        self.stack.append(Element(tag, hide, shut))
        self.hidden += hide

    def end(self, tag: str) -> None:
        stop = {"html", "table", "template"} if tag in TABLE_PARTS else SCOPE if tag in SPECIAL else SPECIAL
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i].tag == tag:
                self.hidden -= sum(e.hide for e in self.stack[i:])
                del self.stack[i:]
                return
            if self.stack[i].tag in stop:
                return  # 열린 적 없거나 경계 너머에 있는 요소의 끝 태그는 버린다


def visible_text(fragment: str) -> tuple[str, set[str]]:
    """보이는 글과 숨김 의심. 엔티티를 두 번 싼 글과 그 안에 숨은 태그도 풀릴 때까지 되풀이한다."""
    notes = set()
    for _ in range(UNWRAP_ROUNDS):
        reader = VisibleText()
        plain = reader.feed(fragment)
        notes |= reader.notes
        if plain == fragment:
            break
        fragment = plain
    return fragment.strip(), notes


def text(fragment: str) -> str:
    return visible_text(fragment)[0]


def one_line(s: str) -> str:
    """파일 머리는 한 줄에 하나씩 읽으므로, 값 안의 줄바꿈으로 다른 머리 줄을 꾸미지 못하게 한다."""
    return " ".join(s.split())


def parse_view(page: str) -> dict:
    body = re.search(r'<div class="view-content"[^>]*>(.*?)</div>\s*</div>', page, re.S)
    # 제목과 번호는 본문 밖에서만 읽고 처음 나온 값을 쓴다. 본문에 표를 넣어 번호를 바꾸지 못하게 한다
    frame = page[:body.start()] + page[body.end():] if body else page
    subject, notes = visible_text(re.search(r'<div class="subject">(.*?)</div>', frame, re.S).group(1))
    info = {}
    for key, value in re.findall(r"<th>(.*?)</th>\s*<td>(.*?)</td>", frame, re.S):
        info.setdefault(text(key), value)
    content, more = visible_text(body.group(1)) if body else ("", set())
    return {"title": one_line(subject), "number": one_line(text(info.get("고시공고번호", ""))),
            "date": one_line(text(info.get("등록일자", ""))), "body": content, "suspect": sorted(notes | more)}


def header(v: dict, source: str) -> str:
    """src.llm.notice.read_notice가 읽는 파일 머리. 숨김 의심은 있을 때만 적는다."""
    rows = [("제목", v["title"]), ("번호", v["number"]), ("날짜", v["date"]), ("출처", source)]
    if v.get("suspect"):
        rows.append(("숨김의심", one_line(", ".join(v["suspect"]))[:300]))
    return "\n".join(f"{k}: {value}" for k, value in rows)


def main() -> None:
    pages = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    OUT.mkdir(parents=True, exist_ok=True)
    nos = []
    for p in range(1, pages + 1):
        found = list(dict.fromkeys(re.findall(r"mgtNo=(\d+)", get(LIST.format(page=p)))))
        nos += [n for n in found if n not in nos]
        time.sleep(0.5)
    got, failed, suspect = 0, [], 0
    for no in nos:
        path = OUT / f"{no}.txt"
        if path.exists():
            continue
        try:
            v = parse_view(get(VIEW.format(no=no)))
        except Exception as e:  # 받지 못했거나 쪽 모양이 다른 문서는 건너뛰고 다음 문서를 받는다
            failed.append(no)
            print(f"{no}: 건너뜀 {type(e).__name__}: {e}")
            continue
        path.write_text(f"{header(v, VIEW.format(no=no))}\n\n{v['body']}\n", encoding="utf-8")
        got += 1
        suspect += bool(v["suspect"])
        time.sleep(0.5)
    print(f"목록 {len(nos)}건, 새로 받음 {got}건(숨김 의심 {suspect}건), 실패 {len(failed)}건 -> {OUT}")


if __name__ == "__main__":
    main()
