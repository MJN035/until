"""제출 형식 요건 추출 — 과제 본문에서 '어떤 모양으로 내야 하는가'를 뽑는다. LLM 0.

분량(글자 수·매수)은 `length_target`이 이미 다룬다. 여기서 뽑는 건 분량 말고 남는
형식 — 실제 코퍼스(3인 1,524개 문서)에서 세어 본 빈도순이다:

    파일 형식 214 · 표지 123 · 파일명 규칙 98 · 인용/참고문헌 96 · 서식 15

이 요건들은 지금까지 아무도 안 봤다. 초안 본문은 그럴듯한데 `.pdf`로 내라는 과제에
`.docx`를 주거나, `학번_이름`으로 파일명을 지으라는데 `until-submission.pdf`를 주거나,
표지에 이름·학번을 넣으라는데 표지가 없는 채로 나갔다.

**과제가 말한 것만 규칙이 된다** — 추측하지 않는다. 근거 문장(`source`)을 함께 들고
다니는 이유가 그것이다. 화면에서 "왜 이렇게 고쳤나"를 원문으로 보여줄 수 있어야 한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 요건 종류. 검증기(execution/format_guard)가 이 kind로 분기한다.
FILE_TYPE = "file_type"        # .pdf / .hwp / .docx … (forbidden=True면 '이 형식 금지')
FILE_NAME = "file_name"        # 파일명 규칙("학번_이름")
COVER = "cover"                # 표지 + 표지에 들어갈 것
REFERENCES = "references"      # 참고문헌 · 인용 양식(APA/IEEE…)
TYPOGRAPHY = "typography"      # 글꼴·글자 크기·줄간격


@dataclass
class FormatRule:
    """과제가 요구한 형식 하나. `value`는 기계 판정용, `source`는 근거 원문."""
    kind: str
    label: str                 # 사람이 읽는 요구 ("PDF로 제출")
    value: str = ""            # 기계 판정용 값 (".pdf", "학번_이름", "APA")
    source: str = ""           # 근거가 된 과제 원문 조각
    forbidden: bool = False    # True면 '이렇게 하지 마라'(예: "pdf x")
    extras: list = field(default_factory=list)   # 표지에 들어갈 항목 등

    def describe(self) -> str:
        head = ("금지: " if self.forbidden else "") + self.label
        return f"{head} ({self.value})" if self.value else head


_EXT_WORDS = {
    "pdf": ".pdf", "hwp": ".hwp", "hwpx": ".hwpx", "doc": ".docx", "docx": ".docx",
    "ppt": ".pptx", "pptx": ".pptx", "zip": ".zip", "한글": ".hwp", "워드": ".docx",
}

# "pdf 파일로 제출" · "pdf 형식)로 만들어 제출" · "워드 파일 원본으로 제출"
# 확장자와 '~로 제출' 사이에 오는 수식어(파일·형식·본·원본·닫는 괄호)를 몇 개든 흘린다 —
# 실코퍼스는 "한글 or 워드 파일 원본으로 제출", "파일(가급적 pdf 형식)로 만들어 제출"처럼 쓴다.
_REQ_EXT = re.compile(
    r"(pdf|hwpx|hwp|docx|doc|pptx|ppt|zip|한글|워드)\s*(?:(?:파일|형식|본|원본|\))\s*){0,3}"
    r"(?:으?로|로)\s*(?:변환\s*(?:하[여어]|해서)\s*)?(?:만들어\s*)?"
    r"(?:제출|올려|업로드|저장|작성)", re.IGNORECASE)

# "(pdf x)" · "pdf는 안 됩니다" · "pdf 불가" · "폰트 변환하시면 안됩니다"
_FORBID_EXT = re.compile(
    r"(pdf|hwpx|hwp|docx|doc|pptx|ppt|zip|한글|워드)\s*(?:파일|형식)?\s*"
    r"(?:[x×]\s*[\),\.]|[x×]$|(?:는|은)?\s*(?:안\s*(?:됩니다|돼요|되[며,])|불가|금지))",
    re.IGNORECASE)

# 파일명 규칙: 파일명을 "학번_이름" 으로 / 파일 이름은 학번_이름
_FILE_NAME = re.compile(
    r"파일\s*(?:명|이름)\s*(?:을|은|는|이)?\s*[\"'“”「\[]?\s*"
    r"([0-9A-Za-z가-힣]{1,12}(?:\s*[_\-]\s*[0-9A-Za-z가-힣]{1,12}){1,4})")
# 예시: (예. 123456_홍길동.pdf)
_FILE_NAME_EG = re.compile(r"[（(]\s*예\s*[.:]?\s*([^)）\n]{3,60})[)）]")

# 표지: "표지에 조와 조원분들의 이름과 학번을 추가" · "표지 포함"
_COVER = re.compile(
    r"(?:표지|겉장|커버\s*페이지|첫\s*장)(?:\s*슬라이드)?\s*(?:에|를|은|는|가|로|이)?\s*"
    # 요구 동사가 붙거나("표지에 …를 추가"), 괄호로 들어갈 항목을 나열하거나
    # ("표지 슬라이드(과제명·학번·이름)"), 목록 항목으로 단독으로 서 있으면 요구다.
    # 실사용에서 셋째 형태를 놓쳤다(라이브 확인 2026-08-23, '피피티 제출').
    r"(?:[^.\n]{0,40}?(?:포함|추가|작성|넣|실[려린]|붙[이여]|만들|필수|이다|입니다)"
    r"|\s*[（(][^)）\n]{2,60}[)）])")
# 표지를 **요구하지 않는** 문장에도 '표지'는 나온다 — "시험지 표지에 함수 목록이
# 있으니", "이름과 학번이 적혀있는 표지는 삭제하였습니다"(자료 설명). 위 정규식이
# 요구 동사를 함께 요구하는 이유다.
# "표지 없음" · "표지 없이" · "표지는 생략" — 요구가 아니라 **금지**다. 실코퍼스에서
# 표지 검출 109건 중 82건이 이것이었다: 표지를 만들지 말라는 과제에 표지를 붙이면
# 검증기가 과제를 어기는 쪽으로 초안을 고친다.
_COVER_NEG = re.compile(r"(?:표지|겉장|커버\s*페이지)\s*(?:는|은)?\s*"
                        r"(?:없[이음는]|생략|제외|불필요|필요\s*없)")
_COVER_ITEMS = (("이름", "이름"), ("성명", "이름"), ("학번", "학번"),
                ("학과", "학과"), ("전공", "학과"), ("조원", "조원"),
                ("조\\b", "조"), ("과목", "과목명"), ("교수", "담당교수"),
                ("제목", "제목"), ("날짜", "날짜"), ("분반", "분반"))

# 인용 양식 · 참고문헌
# `\b`는 한글과 알파벳 사이에서 성립하지 않는다 — 파이썬 정규식은 한글도 단어
# 문자로 보기 때문에 "Reference는 IEEE"의 IEEE가 `\bIEEE`로 안 잡혔다. 알파벳
# 인접만 배제하는 룩어라운드로 바꾼다(한글 조사가 바로 붙어도 잡힌다).
_CITE_STYLE = re.compile(r"(?<![A-Za-z])(APA|IEEE|MLA|Chicago|시카고|Vancouver|밴쿠버)(?![A-Za-z])",
                         re.IGNORECASE)
_REFERENCES = re.compile(r"참고\s*문헌|references|출처\s*(?:를|는|을)?\s*(?:표기|명시|밝히)|각주|미주")

# 서식: 11pt · 줄간격 180 · 글꼴 바탕체
# `(10 pts)` 같은 배점은 서식이 아니다 — 문제지마다 나오므로 먼저 잘라 낸다.
_SCORE_PTS = re.compile(r"[（(]\s*\d{1,3}\s*(?:pts?|점|포인트)\s*[)）]", re.IGNORECASE)
_PT = re.compile(r"(?:한글|워드)?\s*(?:기준)?\s*(\d{1,2})\s*(?:pt|포인트)\b", re.IGNORECASE)
_LINE_SPACING = re.compile(r"(?:줄\s*간격|행간)\s*(?:은|는|을)?\s*([\d.]{1,5})\s*(%|퍼센트)?")
# 글꼴은 **이름처럼 생긴 것만** 받는다. "폰트 변환하시면 안됩니다"를 글꼴 이름
# '변환하시면 안됩니다'로 읽던 오탐이 실코퍼스에 있었다 — 서술어를 값으로 삼으면
# 화면에 "글꼴: 변환하시면 안됩니다"가 뜬다.
_FONT = re.compile(
    r"(?:글꼴|폰트|서체)\s*(?:은|는|을|이)?\s*[:\s]\s*"
    r"([가-힣A-Za-z][가-힣A-Za-z0-9 ]{0,18}?(?:체|Times New Roman|Arial|Calibri|굴림|바탕|맑은\s*고딕))"
    r"(?=[,.\n)]|\s|$)", re.IGNORECASE)

# 제출 경로(이메일 등)는 뽑지 않는다. 실코퍼스의 메일 언급 155건을 읽어 보니 정규
# 제출 경로는 소수였고 나머지는 "문의는 메일로", "지각·부득이한 경우 조교 메일로",
# "미제출조는 메일로"였다. 둘을 정규식으로 가를 수 없는데, **틀린 제출 경로를 알리는
# 것은 침묵보다 나쁘다** — 제때 내는 학생을 엉뚱한 곳으로 보낸다. eTL은 Until이 이미
# 가리키는 기본값이라 규칙으로 만들 값어치도 없다.

_SNIP = 90    # 근거 원문 조각 길이


def _snippet(text: str, m: re.Match) -> str:
    start = max(0, m.start() - 25)
    return " ".join(text[start:m.end() + 25].split())[:_SNIP]


def _dedupe(rules: list) -> list:
    """(kind, value, forbidden)이 같으면 첫 것만 — 공지가 여러 번 반복 인용된다."""
    seen, out = set(), []
    for r in rules:
        key = (r.kind, r.value.lower(), r.forbidden)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _file_type_rules(text: str) -> list:
    out = []
    for m in _FORBID_EXT.finditer(text):
        ext = _EXT_WORDS.get(m.group(1).lower())
        if ext:
            out.append(FormatRule(FILE_TYPE, f"{ext} 제출 금지", ext,
                                  _snippet(text, m), forbidden=True))
    forbidden = {r.value for r in out}
    for m in _REQ_EXT.finditer(text):
        ext = _EXT_WORDS.get(m.group(1).lower())
        # 같은 확장자를 금지하면서 요구할 수는 없다 — 금지가 이긴다("pdf x"가 더 명시적).
        if ext and ext not in forbidden:
            out.append(FormatRule(FILE_TYPE, f"{ext[1:].upper()}로 제출", ext,
                                  _snippet(text, m)))
    return out


# "제출 형식: PowerPoint 파일(PPT)" · "파일 형식 — PDF" 처럼 **문장이 아니라 항목**으로
# 적힌 형태. 실사용에서 이걸 놓쳤다(라이브 확인 2026-08-23, '피피티 제출').
_LABELLED_TYPE = re.compile(
    r"(?:제출\s*형식|파일\s*형식|형식|확장자)\s*[:：\-—]\s*[^\n]{0,40}?"
    r"(pdf|hwpx|hwp|docx|doc|pptx|ppt|zip|powerpoint|파워포인트|한글|워드)",
    re.IGNORECASE)
_LABEL_WORDS = {"powerpoint": ".pptx", "파워포인트": ".pptx"}


def _labelled_file_type_rules(text: str) -> list:
    out = []
    for m in _LABELLED_TYPE.finditer(text):
        word = m.group(1).lower()
        ext = _EXT_WORDS.get(word) or _LABEL_WORDS.get(word)
        if ext:
            out.append(FormatRule(FILE_TYPE, f"{ext[1:].upper()}로 제출", ext,
                                  _snippet(text, m)))
    return out


def _file_name_rule(text: str):
    m = _FILE_NAME.search(text)
    if not m:
        return None
    pattern = re.sub(r"\s*([_\-])\s*", r"\1", m.group(1)).strip()
    if len(pattern) < 3:
        return None
    eg = _FILE_NAME_EG.search(text, m.end(), m.end() + 200)
    src = _snippet(text, m)
    if eg:
        src = (src + " / 예: " + " ".join(eg.group(1).split()))[:_SNIP + 40]
    return FormatRule(FILE_NAME, "파일명 규칙", pattern, src)


def _cover_rule(text: str):
    neg = _COVER_NEG.search(text)
    if neg:
        return FormatRule(COVER, "표지 없이", "", _snippet(text, neg), forbidden=True)
    m = _COVER.search(text)
    if not m:
        return None
    # 표지에 들어갈 항목은 문장 경계를 넘어 나열되기도 한다 —
    # "첫장은 표지이다. 제목/주제문/개요/학과/학번/성명이 실려…". 앞뒤로 넉넉히 본다.
    tail = text[max(0, m.start() - 20):m.end() + 120]
    items = []
    for pat, name in _COVER_ITEMS:
        if re.search(pat, tail) and name not in items:
            items.append(name)
    return FormatRule(COVER, "표지", ", ".join(items), _snippet(text, m), extras=items)


def _reference_rules(text: str) -> list:
    out = []
    style = _CITE_STYLE.search(text)
    if style:
        out.append(FormatRule(REFERENCES, "인용 양식", style.group(1).upper(),
                              _snippet(text, style)))
    ref = _REFERENCES.search(text)
    if ref:
        out.append(FormatRule(REFERENCES, "참고문헌", "참고문헌", _snippet(text, ref)))
    return out


def _typography_rules(text: str) -> list:
    out = []
    text = _SCORE_PTS.sub(" ", text)            # 배점 "(10 pts)"는 서식이 아니다
    pt = _PT.search(text)
    if pt and 6 <= int(pt.group(1)) <= 20:      # 상식적인 본문 글자 크기 범위
        out.append(FormatRule(TYPOGRAPHY, "글자 크기", f"{int(pt.group(1))}pt",
                              _snippet(text, pt)))
    sp = _LINE_SPACING.search(text)
    if sp:
        out.append(FormatRule(TYPOGRAPHY, "줄간격", sp.group(1) + (sp.group(2) or ""),
                              _snippet(text, sp)))
    fo = _FONT.search(text)
    if fo:
        out.append(FormatRule(TYPOGRAPHY, "글꼴", fo.group(1).strip(), _snippet(text, fo)))
    return out


def detect_format_rules(text: str, spec: dict | None = None) -> list:
    """과제 본문(+spec의 requirements·constraints)에서 제출 형식 요건을 뽑는다.

    분량은 여기서 다루지 않는다 — `length_target`이 이미 갖고 있고, readiness의
    '분량' 항목이 표면화한다. 중복으로 경고하면 같은 말을 두 번 하게 된다.
    """
    body = str(text or "")
    for key in ("requirements", "constraints"):
        items = (spec or {}).get(key) or []
        if isinstance(items, list):
            body += "\n" + "\n".join(str(x) for x in items)
    if not body.strip():
        return []
    rules = _file_type_rules(body) + _labelled_file_type_rules(body)
    for maybe in (_file_name_rule(body), _cover_rule(body)):
        if maybe:
            rules.append(maybe)
    rules += _reference_rules(body) + _typography_rules(body)
    return _dedupe(rules)


def required_extension(rules: list) -> str:
    """제출 파일 확장자 — 요구된 것 중 첫 번째. 없으면 ""(기존 동작 유지)."""
    for r in rules:
        if r.kind == FILE_TYPE and not r.forbidden:
            return r.value
    return ""


def forbidden_extensions(rules: list) -> set:
    return {r.value for r in rules if r.kind == FILE_TYPE and r.forbidden}
