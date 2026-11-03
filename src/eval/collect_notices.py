"""평가용 고시공고 원문을 강서구청 누리집에서 받는다.

실행: python -m src.eval.collect_notices [쪽수]   (기본 10쪽, '고시'로 제목 검색)
출력: data/raw/notices/eval/{mgtNo}.txt  (원문은 커밋하지 않는다. 정답은 eval/notice_gold.yaml에 번호로 적는다)
파일 머리에 제목, 번호, 날짜, 출처를 적어 src.llm.notice가 그대로 읽게 한다. 이미 받은 파일은 다시 받지 않는다.

사람 눈에 보이는 글만 LLM에 가도록 HTML을 브라우저처럼 읽어 숨긴 글을 버린다. 숨기는 방법을 하나씩 막지 않고 허용 목록으로 본다:
- 허용한 요소(ALLOWED_TAGS), 속성(ALLOWED_ATTRS), class(ALLOWED_CLASSES), CSS 속성과 값(SAFE_CSS) 밖의 것이 나오면
  파일 머리에 '숨김의심:' 줄을 남긴다. src.llm.notice가 그 문서의 제안 flags에 넣고, flags가 있는 제안은 묶음 승인에서 빠진다.
  허용 목록은 실제 강서구 고시 게시판 본문을 보고 정했다. 2026-11-03에 받은 95건의 본문은 모두
  <div class="gosi-con"><pre>글</pre></div> 꼴이었고 style 속성은 없었다. 그 위에 보통 게시판 글에 나오는 요소만 더했다.
- 허용 목록 밖 요소(svg, math, select, option, ruby, details, form 등)는 내용째 버린다. 그 안의 끝 태그는 바깥 요소를 닫지 못한다.
- 주석과, 안의 태그를 글로 읽는 요소(script, style, textarea, title, xmp, iframe, noscript, noembed, noframes, plaintext)는
  내용째 버린다. 이 안의 '</div>'가 바깥의 숨긴 요소를 닫지 못하게 태그보다 먼저 지운다.
- 숨김이 분명한 것(display, visibility, hidden 속성, 작은 글자, 투명, 화면 밖 위치 등)은 거른다. 글자 크기, 불투명도,
  여백은 바깥 요소부터 겹쳐 계산한다. 글자색은 바탕색과 WCAG 대비로 견준다. 같은 색이면 거르고, 대비가 낮으면 의심으로 남긴다.
- 브라우저와 HTML 읽는 법이 어긋날 때는 더 많이 버리는 쪽을 고른다(숨긴 요소를 브라우저보다 늦게 닫는다).
class로 숨긴 글은 누리집의 CSS를 함께 봐야 해서 거르지 못하고 의심으로만 남긴다.
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

# ---- 허용 목록 ----
# 글을 그대로 보여 주는 요소. 실제 게시판 본문(div, pre)에 보통 게시판 글의 문단, 표, 목록, 강조, 링크, 그림을 더했다
ALLOWED_TAGS = {"div", "p", "span", "pre", "br", "hr", "strong", "b", "em", "i", "u", "sub", "sup", "ul", "ol", "li", "a", "img",
                "table", "caption", "colgroup", "col", "thead", "tbody", "tfoot", "tr", "td", "th"}
# 보이는 모양을 숨기지 않는 속성. hidden, aria-hidden, popover, bgcolor, id, data-* 등은 밖이다
ALLOWED_ATTRS = {"style", "class", "href", "src", "alt", "title", "width", "height", "colspan", "rowspan", "align", "valign", "border",
                 "cellpadding", "cellspacing", "scope", "target", "lang"}
ALLOWED_CLASSES = {"gosi-con"}  # 실제 게시판 본문에 나온 class. 다른 class는 누리집 CSS가 숨길 수 있다
LENGTH_UNITS = {"", "px", "pt", "pc", "in", "cm", "mm", "em", "rem", "%"}  # vw, vh처럼 화면 크기에 따르는 단위는 밖이다
NAMED_COLORS = {"black": (0, 0, 0), "silver": (192, 192, 192), "gray": (128, 128, 128), "grey": (128, 128, 128), "white": (255, 255, 255),
                "maroon": (128, 0, 0), "red": (255, 0, 0), "purple": (128, 0, 128), "fuchsia": (255, 0, 255), "green": (0, 128, 0),
                "lime": (0, 255, 0), "olive": (128, 128, 0), "yellow": (255, 255, 0), "navy": (0, 0, 128), "blue": (0, 0, 255),
                "teal": (0, 128, 128), "aqua": (0, 255, 255)}  # CSS 기본 16색. 다른 이름 색은 읽지 못한 색으로 본다
MIN_CONTRAST = 3  # 글자색과 바탕색의 대비가 이보다 낮으면 의심한다. WCAG 2의 큰 글자 최소 대비(3:1)다
BORDER_WORDS = {"none", "hidden", "solid", "dotted", "dashed", "double", "groove", "ridge", "inset", "outset", "thin", "medium", "thick"}
FONT_WORDS = {"xx-small": 9, "x-small": 10, "small": 13, "medium": 16, "large": 18, "x-large": 24, "xx-large": 32}
ROOT_FONT_PX = 16


def lengths(value: str, negative: bool = True, words: frozenset = frozenset({"auto"})) -> bool:
    """값이 허용한 단위의 길이(또는 words의 낱말)로만 되어 있는가."""
    tokens = value.split()
    for t in tokens:
        if t in words:
            continue
        m = LENGTH.fullmatch(t)
        if not m or m.group(2) not in LENGTH_UNITS or (not negative and float(m.group(1)) < 0):
            return False
    return bool(tokens)


def one_of(*words: str):
    return lambda v: v in words


def border_ok(value: str) -> bool:
    return all(t in BORDER_WORDS or lengths(t, negative=False) or color_of(t) is not None for t in value.split()) and bool(value)


def line_height_ok(value: str) -> bool:
    m = LENGTH.fullmatch(value)
    return value == "normal" or bool(m and m.group(2) in LENGTH_UNITS and float(m.group(1)) > 0)


def decoration_ok(value: str) -> bool:
    words = {"none", "underline", "line-through", "overline", "solid", "double", "dotted", "dashed", "wavy"}
    return all(t in words or color_of(t) is not None for t in value.split()) and bool(value)


# 보이는 글을 숨기지 않는 CSS 속성과 그 값의 꼴. 글자 정렬, 굵기, 기울임, 밑줄, 줄바꿈, 여백, 테두리, 크기, 글자색과 바탕색, 글자 크기다.
# 글자 크기, 여백, 색은 값만으로 숨김을 알 수 없어 VisibleText가 바깥 요소와 겹쳐 다시 본다
SAFE_CSS = {
    "text-align": one_of("left", "right", "center", "justify", "start", "end"),
    "vertical-align": lambda v: v in {"top", "middle", "bottom", "baseline", "sub", "super", "text-top", "text-bottom"} or lengths(v),
    "font-weight": lambda v: v in {"normal", "bold", "bolder", "lighter"} or bool(re.fullmatch(r"[1-9]00", v)),
    "font-style": one_of("normal", "italic", "oblique"),
    "text-decoration": decoration_ok, "text-decoration-line": decoration_ok,
    "white-space": one_of("normal", "pre", "pre-wrap", "pre-line", "nowrap", "break-spaces"),
    "word-break": one_of("normal", "keep-all", "break-all", "break-word"),
    "overflow-wrap": one_of("normal", "break-word", "anywhere"), "word-wrap": one_of("normal", "break-word", "anywhere"),
    "list-style-type": lambda v: bool(re.fullmatch(r"[a-z-]+", v)),
    "border-collapse": one_of("collapse", "separate"),
    "border-spacing": lambda v: lengths(v, negative=False, words=frozenset()),
    "text-indent": lambda v: lengths(v, words=frozenset()),
    "line-height": line_height_ok,
    "font-size": lambda v: v in FONT_WORDS or v in ("smaller", "larger") or lengths(v, negative=False, words=frozenset()),
    "color": lambda v: color_of(v) is not None, "background-color": lambda v: color_of(v) is not None,
    "background": lambda v: v == "none" or color_of(v) is not None,
}
SAFE_CSS |= {name: lengths for name in ("margin", "margin-top", "margin-right", "margin-bottom", "margin-left")}
SAFE_CSS |= {name: lambda v: lengths(v, negative=False) for name in ("padding", "padding-top", "padding-right", "padding-bottom", "padding-left")}
SAFE_CSS |= {name: lambda v: lengths(v, negative=False, words=frozenset({"auto", "none"}))
             for name in ("width", "height", "min-width", "min-height", "max-width", "max-height")}
SAFE_CSS |= {f"border{side}{part}": border_ok for side in ("", "-top", "-right", "-bottom", "-left") for part in ("", "-width", "-style", "-color")}


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
    if v in NAMED_COLORS:
        return *NAMED_COLORS[v], 1
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
        if not all(0 <= n <= 255 for n in nums) or not 0 <= alpha <= 1:
            return None
        return nums[0], nums[1], nums[2], alpha
    return None


def contrast(a: tuple, b: tuple) -> float:
    """두 색의 WCAG 2 대비(1~21). 1이면 같은 밝기다."""
    def luminance(c):
        ch = [x / 255 for x in c[:3]]
        ch = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in ch]
        return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def css_outside(decls: list[tuple[str, str]]) -> set[str]:
    """허용 목록(SAFE_CSS) 밖의 CSS 선언. 속성 이름이 밖이거나, 값이 허용한 꼴이 아니면 의심으로 남긴다."""
    notes = set()
    for name, value in decls:
        check = SAFE_CSS.get(name)
        if check is None:
            notes.add(f"허용 목록 밖 CSS({clean_note(name)})")
        elif not check(value):
            notes.add(f"허용 목록 밖 CSS 값({clean_note(name)})")
    return notes


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
    """본문 안 style 요소. 어느 요소에 걸리는지는 흉내 내지 못해 의심으로만 남긴다. 숨기는 규칙이 있으면 그것도 적는다."""
    notes = {"허용 목록 밖 요소(style)"}
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

@dataclass
class Look:
    """바깥 요소부터 겹쳐 계산한 보이는 모양. 글자 크기와 불투명도는 곱해지고, 여백은 더해진다."""
    font: float = ROOT_FONT_PX  # 글자 크기(px)
    opacity: float = 1.0
    dx: float = 0.0  # 가로로 민 거리(px): 왼쪽 여백, 안쪽 여백, 들여쓰기의 합
    dy: float = 0.0  # 세로로 민 거리(px): 위쪽 여백의 합
    color: tuple | None = None  # 글자색. None이면 기본 검정
    backdrop: tuple | None = None  # 이 글 뒤의 바탕색. None이면 누리집 바탕(알 수 없어 흰색으로 본다)


def side_of(values: list[float | None], side: int) -> float:
    """margin, padding 줄임 값(1~4개)에서 한 쪽(0 위, 3 왼쪽)의 길이."""
    v = [x for x in values]
    if not v:
        return 0
    pick = {1: (0, 0, 0, 0), 2: (0, 1, 0, 1), 3: (0, 1, 2, 1), 4: (0, 1, 2, 3)}.get(len(v), (0, 1, 2, 3))[side]
    return v[pick] or 0


def font_px(value: str, parent: float) -> float | None:
    """글자 크기 값의 픽셀. em과 %는 바깥 글자 크기에 곱한다. 읽지 못하면 None."""
    if value in FONT_WORDS:
        return FONT_WORDS[value]
    if value in ("smaller", "larger"):
        return parent * (0.83 if value == "smaller" else 1.2)
    m = LENGTH.fullmatch(value)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2)
    if unit == "em":
        return n * parent
    if unit == "%":
        return n * parent / 100
    if unit == "rem":
        return n * ROOT_FONT_PX
    return px(value, 0.16)


def look_of(decls: list[tuple[str, str]], parent: Look) -> tuple[Look, bool, set[str]]:
    """바깥 모양에 이 요소의 CSS를 얹은 모양, 그것으로 보아 숨겼는가, 의심."""
    values: dict[str, list[str]] = {}
    for name, value in decls:
        values.setdefault(name, []).append(value)
    look, hide, notes = Look(**vars(parent)), False, set()
    for v in values.get("font-size", []):
        n = font_px(v, parent.font)
        if n is not None:
            look.font = n
    for v in values.get("opacity", []):
        try:
            look.opacity = parent.opacity * (float(v[:-1]) / 100 if v.endswith("%") else float(v))
        except ValueError:
            pass
    nums = lambda name: [px(t) for v in values.get(name, []) for t in v.split()]
    first = lambda name: next((n for n in nums(name) if n is not None), 0)
    look.dx = parent.dx + (first("margin-left") or side_of(nums("margin"), 3)) + (first("padding-left") or side_of(nums("padding"), 3))         + first("text-indent") + first("left") - first("right")
    look.dy = parent.dy + (first("margin-top") or side_of(nums("margin"), 0)) + first("top") - first("bottom")
    for name in ("color", "-webkit-text-fill-color"):
        for v in values.get(name, []):
            look.color = color_of(v) or look.color
    for v in values.get("background-color", []) + values.get("background", []):
        c = color_of(v)
        if c is not None and c[3] > 0:  # 투명한 바탕은 뒤의 바탕이 그대로 보인다
            look.backdrop = c
            if c[3] < 1:
                notes.add("반투명 바탕색")
    hide |= look.font < MIN_FONT_PX or look.opacity < MIN_OPACITY or far(look.dx) or far(look.dy)
    if look.color is not None and MIN_OPACITY <= look.color[3] < 1:
        notes.add("반투명 글자색")
    if look.color is not None or look.backdrop is not None:
        ratio = contrast(look.color or (0, 0, 0), look.backdrop or (255, 255, 255))
        if ratio < 1.01 and look.backdrop is not None:  # 본문 안에서 정한 바탕과 같은 색이다
            hide = True
        elif ratio < MIN_CONTRAST:
            notes.add("바탕과 대비가 낮은 글자색")
    return look, hide, notes


def judge(tag: str, attrs: list[tuple[str, str]], parent: Look) -> tuple[bool, set[str], Look]:
    """허용한 요소가 안의 글을 숨기는가, 허용 목록 밖의 것(의심), 이 요소의 모양. style 속성이 여럿이면 모두 본다."""
    names = {k for k, _ in attrs}
    hide = "hidden" in names or "popover" in names or any(k == "aria-hidden" and v.strip().lower() == "true" for k, v in attrs)
    notes = {f"허용 목록 밖 속성({clean_note(k)})" for k in names - ALLOWED_ATTRS}
    decls = []
    for k, v in attrs:
        if k == "style":
            decls += css_decls(v)
        elif k == "class":
            for c in v.split():
                if HIDE_CLASS.search(c):
                    notes.add(f"숨기는 이름의 class({clean_note(c)})")
                elif c not in ALLOWED_CLASSES:
                    notes.add(f"허용 목록 밖 class({clean_note(c)})")
    h, n = css_hides(decls)
    painted = decls + [("background-color", " ".join(v.lower().split())) for k, v in attrs if k == "bgcolor"]
    look, h2, n2 = look_of(painted, parent)
    notes |= n | n2 | css_outside(decls)
    for name, value in decls:
        if CALC.search(value):
            notes.add(f"계산식 CSS 값({clean_note(name)})")
    return hide or h or h2, notes, look


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
    look: Look
    barrier: bool = False  # 허용 목록 밖 요소: 안의 끝 태그가 이 요소를 넘어 바깥 요소를 닫지 못한다


class VisibleText:
    """보이는 글만 모은다. 숨긴 요소는 안쪽까지 버리고, 끝 태그가 경계를 넘지 못하면 닫지 않는다(브라우저와 같다).

    허용 목록 밖 요소는 내용째 버리고 의심으로 남긴다. 그 안의 끝 태그는 바깥을 닫지 못한다. 브라우저는 select 안의 '</div>'를
    버리고, svg 안의 <p>처럼 요소를 일찍 닫기도 한다. 늦게 닫는 쪽이라 언제나 브라우저보다 더 많이 버린다.
    브라우저가 끝 태그 없이 요소를 닫는 경우(<p> 뒤의 <div> 등)는 흉내 내지 않는다. 그때도 숨긴 요소가 더 오래 열려 있어 더 많이 버린다.
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
                    self.notes |= style_sheet_notes(src[pos:end]) if tag == "style" else {f"허용 목록 밖 요소({tag})"}
                    pos = end
                else:
                    self.start(tag, attrs)
        return "".join(self.parts)

    def data(self, text: str) -> None:
        if text and not self.hidden:
            self.parts.append(html.unescape(text))

    def start(self, tag: str, attrs: list[tuple[str, str]]) -> None:
        parent = self.stack[-1].look if self.stack else Look()
        if tag in VOID_TAGS:  # 브라우저는 <div .../>의 /를 무시하지만 빈 요소는 열지 않는다
            if tag == "link" and "stylesheet" in " ".join(v.lower() for k, v in attrs if k == "rel"):
                self.notes.add("본문 안에서 바깥 CSS를 불러온다")
            if tag not in ALLOWED_TAGS:
                self.notes.add(f"허용 목록 밖 요소({clean_note(tag)})")
            return
        if tag not in ALLOWED_TAGS:  # svg, math, select, ruby, details 등. 안의 글이 보이는지 흉내 내지 않고 내용째 버린다
            self.notes.add(f"허용 목록 밖 요소({clean_note(tag)})")
            self.stack.append(Element(tag, True, parent, barrier=True))
            self.hidden += 1
            return
        hide, notes, look = judge(tag, attrs, parent)
        if hide:  # 숨긴 글은 거르지만, 숨기는 꼴이 나온 문서라는 것도 남긴다
            notes.add("숨긴 요소를 걸렀다")
        self.notes |= notes
        self.stack.append(Element(tag, hide, look))
        self.hidden += hide

    def end(self, tag: str) -> None:
        stop = {"html", "table", "template"} if tag in TABLE_PARTS else SCOPE if tag in SPECIAL else SPECIAL
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i].tag == tag:
                self.hidden -= sum(e.hide for e in self.stack[i:])
                del self.stack[i:]
                return
            if self.stack[i].barrier or self.stack[i].tag in stop:
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
