"""마감일 파싱·D-day 계산 — 결정적(LLM 0).

과제 명세(spec.deadline)·원문에서 마감 날짜를 뽑아 '오늘' 기준 남은 일수를 계산한다.
표시·안내용이며 경계선 철학과 무관(사람 판단 아님). 파싱 실패 시 조용히 None.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from typing import List, Optional
import re

# 연-월-일: 2026-07-10 / 2026.07.10 / 2026/7/10 / 2026년 7월 10일
_YMD = re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?")
# 월-일(한국어 형태): 7월 10일 — 소수·버전과 혼동 없음.
_MD_KR = re.compile(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일?(?!\d)")
# 월-일(숫자 형태): 7/10, 07.10 — 소수(3.5)·버전(3.11)·문제번호(2.1)·분수(1/2)와
# 구분이 안 되므로, 근처에 마감 문맥 키워드가 있을 때만 날짜로 인정한다.
_MD_NUM = re.compile(r"(?<!\d)(\d{1,2})\s*[./]\s*(\d{1,2})(?!\d)")
_DEADLINE_CTX = re.compile(r"마감|제출|기한|까지|due|deadline", re.IGNORECASE)
# 숫자형 M.D가 번호 참조인 신호 — 앞의 표지어("버전 1.2", "연습문제는 3.2") 또는
# 뒤의 단위어("5.2절"). 마감 문맥과 우연히 인접해도 날짜가 아니다.
_NUMREF_BEFORE = re.compile(r"(문제|예제|버전|절|섹션|장|챕터|단원|과)\s*[은는이가]?\s*$")
_NUMREF_AFTER = re.compile(r"^(절|장|항|조|번|점|판)")
# 상대 날짜: "내일까지", "다음 주 월요일까지 제출" — 문맥 게이트 필수(산문 오탐 방지).
# '내일모레'(=모레)를 최좌선으로 먼저 잡고, 관용어 '오늘날'은 (?!날)로 배제.
_REL_DAY = re.compile(r"(내일모레|오늘|내일|모레)(?!날)")
_REL_WEEK = re.compile(r"(이번\s*주|다음\s*주|담주)?\s*([월화수목금토일])요일")
_WEEKDAYS = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}


@dataclass
class Deadline:
    due: date
    had_year: bool          # 원문에 연도가 명시됐는지(없으면 추론)
    raw: str = ""
    time_str: str = ""      # 마감 시각 표기("23:59", "오후 6시", "자정" 등, 없으면 "")
    extended: bool = False  # 연장 공지에서 채택된 마감인지(라벨에 '연장됨' 병기)
    raw_pos: int = -1       # 원문에서 raw가 매치된 위치(시각 탐지 앵커, 내부용)

    def days_from(self, today: date) -> int:
        return (self.due - today).days

    def dday_label(self, today: date) -> str:
        d = self.days_from(today)
        if d > 0:
            tag = f"D-{d}"
        elif d == 0:
            tag = "D-DAY"
        else:
            tag = f"D+{-d} (지남)"
        time_part = f" {self.time_str}" if self.time_str else ""
        ext_part = " · 연장됨" if self.extended else ""
        return f"{tag} · 마감 {self.due.isoformat()}{time_part}{ext_part}"


def _valid(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


# 마감 시각 표기 — 날짜 주변에서 함께 찾는다(판정엔 미사용, 표시용).
# 자정/정오 > HH:MM > 오전/오후 N시(M분) > 단독 N시('9시간' 등 기간 표현 배제).
_TIME_RE = re.compile(
    r"(자정|정오)"
    r"|(\d{1,2}:\d{2})"
    r"|((?:오전|오후)\s*\d{1,2}시(?:\s*\d{1,2}분)?)"
    r"|((?<![\d시])\d{1,2}시(?!간)(?:\s*\d{1,2}분)?)"
)


def _valid_time(s: str) -> bool:
    """시각 표기 유효성(시 0-24, 분 0-59). '자정/정오'는 항상 유효."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if m:
        return int(m.group(1)) <= 24 and int(m.group(2)) <= 59
    m = re.fullmatch(r"(?:오전|오후)?\s*(\d{1,2})시(?:\s*(\d{1,2})분)?", s)
    if m:
        return int(m.group(1)) <= 24 and int(m.group(2) or 0) <= 59
    return True  # 자정/정오


def _find_time(text: str, raw: str, pos: int = -1) -> str:
    """마감 날짜 매치 위치(pos) 주변(앞 5자·뒤 25자)의 시각 표기를 찾는다.

    윈도우 '슬라이스'가 아니라 원문 전체에 finditer를 돌려 위치로 거른다 —
    슬라이스 경계가 lookbehind를 무력화해 '18시'가 '8시'로 잘리는 조작 방지.
    같은 날짜 문자열이 여러 번 나올 때를 위해 매치 위치(pos)를 앵커로 쓰고,
    날짜 '뒤'의 시각을 앞의 것보다 우선한다("7월 10일 23:59" 관행).
    """
    if not raw:
        return ""
    # 앵커 후보: 매치 위치 우선, 이어서 같은 날짜 문자열의 다른 등장 위치
    # ("7월 10일 안내 … 제출: 7월 10일 23:59"처럼 재언급 옆에 시각이 붙는 경우).
    anchors: List[int] = [pos] if pos >= 0 else []
    start = 0
    while True:
        i = text.find(raw, start)
        if i < 0:
            break
        if i not in anchors:
            anchors.append(i)
        start = i + 1
    times = []
    for m in _TIME_RE.finditer(text):
        t = next((g for g in m.groups() if g), "").strip()
        if t and _valid_time(t):
            times.append((m.start(), t))
    for anchor in anchors:
        lo, hi = anchor - 5, anchor + len(raw) + 25
        rear = [t for s, t in times if anchor <= s <= hi]
        front = [t for s, t in times if lo <= s < anchor]
        if rear or front:
            return (rear or front)[0]
    return ""


def parse_deadline(text: str, *, today: Optional[date] = None) -> Optional[Deadline]:
    """텍스트에서 마감 날짜(+표기된 시각)를 파싱. 연도 생략 시 다가오는 미래로 추론."""
    got = _parse_deadline_date(text, today=today)
    if got:
        got.time_str = _find_time(text, got.raw, got.raw_pos)
    return got


def _parse_deadline_date(text: str, *, today: Optional[date] = None) -> Optional[Deadline]:
    """날짜 파싱 코어(시각 미부착)."""
    if not text:
        return None
    today = today or date.today()

    # '연장' 공지는 여러 마감 후보 중 가장 늦은(연장된) 날짜가 진짜 마감이다.
    # 트리거는 마감 문맥과 결합된 '연장'만('연장전' 등 무관 어휘 배제).
    extension = bool(re.search(r"(마감|기한|제출|까지).{0,12}연장|연장(되|합|했|됐)", text))
    cands: List[Deadline] = []
    past_bumped: List[Deadline] = []  # 연장 모드용 — 과거 후보의 내년 범프(연말 걸침 대비)

    def _emit(d: Deadline) -> Optional[Deadline]:
        # 연장 모드면 후보로 모으고, 아니면 우선순위대로 즉시 반환.
        if extension:
            cands.append(d)
            return None
        return d

    # YMD가 차지한 구간은 MD_KR/MD_NUM이 부분문자열("2025년 12월 20일"의 "12월 20일")을
    # 재매칭하지 않도록 기록해 둔다.
    ymd_spans: List[tuple] = []
    for m in _YMD.finditer(text):
        ymd_spans.append(m.span())
        dt = _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if dt:
            got = _emit(Deadline(due=dt, had_year=True, raw=m.group(0), raw_pos=m.start()))
            if got:
                return got

    def _in_ymd(m: "re.Match") -> bool:
        return any(s < m.end() and m.start() < e for s, e in ymd_spans)

    def _has_ctx(m: "re.Match") -> bool:
        window = text[max(0, m.start() - 15): m.end() + 10]
        return bool(_DEADLINE_CTX.search(window))

    # 연도 생략 월/일 공통 처리 — 미래면 즉시 후보, 과거면(참고 언급 가능성)
    # 문맥 있을 때만 내년 범프 폴백으로 보관. 연장 모드에선 과거 범프를 별도 수집
    # (연말 걸침: "12월 20일에서 1월 10일로 연장" — 새 마감이 내년 초).
    bumped: Optional[Deadline] = None

    def _md_candidate(mm: int, dd: int, raw: str, has_ctx: bool, pos: int) -> Optional[Deadline]:
        nonlocal bumped
        if not (1 <= mm <= 12 and 1 <= dd <= 31):
            return None
        dt = _valid(today.year, mm, dd)
        if not dt:
            return None
        if dt >= today:
            return _emit(Deadline(due=dt, had_year=False, raw=raw, raw_pos=pos))
        nxt = _valid(today.year + 1, mm, dd)
        if nxt and has_ctx:
            if extension:
                past_bumped.append(Deadline(due=nxt, had_year=False, raw=raw, raw_pos=pos))
            if bumped is None:
                bumped = Deadline(due=nxt, had_year=False, raw=raw, raw_pos=pos)
        return None

    # "7월 10일" 형태 — 다가오는(미래) 날짜는 문맥 없이도 신뢰.
    for m in _MD_KR.finditer(text):
        if _in_ymd(m):
            continue
        got = _md_candidate(int(m.group(1)), int(m.group(2)), m.group(0), _has_ctx(m), m.start())
        if got:
            return got

    # "7/10"·"07.10" 형태 — 마감 문맥 키워드가 주변(앞 15자·뒤 10자)에 있는 매치만.
    # 단 번호 참조("버전 1.2"·"연습문제 3.2"·"5.2절")는 문맥이 인접해도 배제.
    for m in _MD_NUM.finditer(text):
        if _in_ymd(m) or not _has_ctx(m):
            continue
        if (_NUMREF_BEFORE.search(text[max(0, m.start() - 8):m.start()])
                or _NUMREF_AFTER.match(text[m.end():m.end() + 2])):
            continue
        got = _md_candidate(int(m.group(1)), int(m.group(2)), m.group(0), True, m.start())
        if got:
            return got

    # 상대 날짜(오늘/내일/모레) — 문맥 게이트("내일까지 제출" 등).
    from datetime import timedelta
    for m in _REL_DAY.finditer(text):
        if not _has_ctx(m):
            continue
        delta = {"오늘": 0, "내일": 1, "모레": 2, "내일모레": 2}[m.group(1)]
        got = _emit(Deadline(due=today + timedelta(days=delta), had_year=False, raw=m.group(0), raw_pos=m.start()))
        if got:
            return got

    # 요일 형태("금요일까지", "다음 주 월요일까지 제출") — 문맥 게이트.
    for m in _REL_WEEK.finditer(text):
        if not _has_ctx(m):
            continue
        wd = _WEEKDAYS[m.group(2)]
        qualifier = (m.group(1) or "").replace(" ", "")
        if qualifier in ("다음주", "담주"):
            # 다음 주의 해당 요일(다음 주 월요일 기준).
            next_monday = today + timedelta(days=7 - today.weekday())
            due = next_monday + timedelta(days=wd)
        elif qualifier == "이번주":
            # 이번 주의 해당 요일(지났으면 D+로 표시됨 — 사실 그대로).
            due = today - timedelta(days=today.weekday()) + timedelta(days=wd)
        else:
            # 무수식 요일 = 다가오는 그 요일(오늘이면 오늘).
            due = today + timedelta(days=(wd - today.weekday()) % 7)
        got = _emit(Deadline(due=due, had_year=False, raw=m.group(0), raw_pos=m.start()))
        if got:
            return got

    # 연장 모드: 모인 후보 중 가장 늦은 날짜(연장된 마감)를 채택하고 '연장됨' 표시.
    if extension:
        chosen: Optional[Deadline] = None
        if cands:
            chosen = max(cands, key=lambda d: d.due)
        # 후보가 전부 과거(연말 걸침)면, 내년 범프 중 가장 이른 날짜가 새 마감
        # (max는 옛 마감+1년을 골라버림).
        elif past_bumped:
            chosen = min(past_bumped, key=lambda d: d.due)
        if chosen:
            chosen.extended = True
            return chosen
    # 다른 단서가 전혀 없으면, 문맥 있는 과거 날짜의 내년 범프를 마지막으로 사용.
    return bumped


def detect_deadline(spec: dict, docs: Optional[List] = None,
                    *, today: Optional[date] = None) -> Optional[Deadline]:
    """명세 deadline 필드를 먼저, 없으면 원문 앞부분에서 마감일을 찾는다.

    단, spec은 LLM 산출물이라 연도를 지어낼 수 있다(라이브 실측: 원문
    '마감: 8월 20일'을 '2023-08-20'으로). 원문의 같은 월·일이 **무연도**면
    그 연도는 문서에서 나올 수 없는 값이므로 원문의 결정적 연도 추론을
    우선한다. 원문에 진짜 연도가 있거나 원문에서 마감을 못 찾으면 spec 유지.
    """
    spec_got = None
    if isinstance(spec, dict):
        dl = spec.get("deadline")
        if isinstance(dl, str):
            spec_got = parse_deadline(dl, today=today)
    doc_got = None
    for d in docs or []:
        text = getattr(d, "text", "") or ""
        doc_got = parse_deadline(text[:4000], today=today)
        if doc_got:
            break
    if (spec_got and doc_got and spec_got.had_year and not doc_got.had_year
            and (spec_got.due.month, spec_got.due.day)
            == (doc_got.due.month, doc_got.due.day)
            and spec_got.due != doc_got.due):
        return doc_got
    return spec_got or doc_got
