"""분량 요건 감지·측정·판정 — 결정적(LLM 0).

과제 명세/원문에서 "2000자 이상", "500~800자", "5페이지", "300 words" 같은 분량
요건을 찾아, 초안(결정 마커 제외)이 이를 충족하는지 사람이 볼 수 있게 판정한다.
경계선 철학 유지: 판정만 하고 억지로 늘리거나 자르지 않는다.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional
import re

# [[DECISION: ...]] 마커는 분량 측정에서 제외(에이전트가 채운 실제 본문만 센다).
_DECISION_RE = re.compile(r"\[\[DECISION:.*?\]\]", re.DOTALL)

# 단위 정규화: 표면형 → 표준 단위.
# '장'은 챕터("교재 5장")·사진 매수와 혼동되므로 일반 단위에서 제외하고,
# 분량 문맥이 확실한 패턴(_JANG_*)으로만 페이지로 인정한다.
_UNIT_ALIASES = {
    "자": "자", "글자": "자", "字": "자",
    "단어": "단어", "words": "단어", "word": "단어",
    "페이지": "페이지", "쪽": "페이지", "page": "페이지", "pages": "페이지",
    "매": "매",  # 원고지 매수(1매 = 200자)
}
# 원고지 매수·페이지는 대략적 글자 환산(하한 판정 참고용).
#
# 페이지 900 = 3인 코퍼스 **실측 중앙값**(2026-08-22). 페이지 단위 요건이 붙은
# 과제의 실제 제출본을 파싱해 공백 제외 글자 수 ÷ 요건 쪽수로 잰 값이다:
#   기말리포트(5매 내외) 6,631자 → 1,326 · 과제1(1페이지) 933 · 과제2(1페이지) 729
#   · 최종보고서(8페이지 이내) 3,961자 → 495
# 아래로 벌어진 495는 '이내' 요건에 도표가 많은 보고서고, 위로 벌어진 1,326은
# 도표 없는 논증문이다. 산문 과제는 900~1,300 사이에 모인다.
# 종전 값 600은 이 표의 어느 산문 표본보다도 낮아, A4 5매 과제의 하한이 실제
# 필요량의 3분의 1로 잡혔다 — 검사를 통과한 초안이 그대로 짧았다.
# ⚠ 이 상수는 경고 하한과 생성 목표를 겸한다. 바꾸면 페이지 단위 과제의 초안
#   길이가 전부 움직인다(표본 4건 — 실사용 데이터가 쌓이면 다시 볼 것).
_APPROX_CHARS = {"매": 200, "페이지": 900}

# 숫자(콤마 허용) + 선택적 단위. 범위(A~B, A-B)와 수식어(이상/이하/내외/최소/최대)를 함께 본다.
_NUM = r"(\d[\d,]*)"
_UNIT = r"(자|글자|字|단어|words?|페이지|쪽|pages?|매)"
# '장'을 페이지로 인정하는 분량 문맥 — "A4 2장", "3장 분량/이내/이상/내외".
_JANG_A4 = re.compile(r"[Aa]4\s*" + _NUM + r"\s*장\s*(이상|이하|이내|내외|안팎|정도)?")
_JANG_CTX = re.compile(r"(?<!\d)" + _NUM + r"\s*장\s*(분량|이내|이상|이하|내외|안팎|정도)")
# 용지 문맥의 '매'는 원고지 매수가 아니라 **페이지**다 — "A4 5매", "A4 용지 3매".
# 이 앞머리가 없으면 종전대로 원고지 매수(200자)로 본다.
# (2026-08-22 실사용: '대학 글쓰기 1 기말리포트 — 분량은 A4 5매 내외'가 원고지
#  5매로 읽혀 하한이 800자로 잡혔다. 실제 A4 5매는 그 열 배 규모다.)
_MAE_PAGE_LEAD = re.compile(r"(?:[Aa]4|용지|페이지|쪽)\s*(?:용지\s*)?$")


@dataclass
class LengthTarget:
    unit: str                      # 표준 단위: 자 | 단어 | 페이지 | 매
    min: Optional[int] = None      # 하한(있으면)
    max: Optional[int] = None      # 상한(있으면)
    raw: str = ""                  # 매칭된 원문 조각(근거)
    # 항목당 요건("강의당 300자 내외" → "강의") — 빈 문자열이면 전체 본문 요건.
    # 전체에 적용하면 "강의 3개 → 전체 300자"로 잘리는 실사용 버그가 된다.
    per_item: str = ""
    # 요건의 방향(COURSE_ALGORITHMS_2026F §4.6(a)) — "min" | "max" | "range".
    #   min   = 하한만(현행 다수 — 미달 방지가 목적, 기본값=현행 유지)
    #   max   = 상한만("200자 이내/이하" — 초과 차단이 목적)
    #   range = 하한·상한 둘 다("500~800자", "300자 내외")
    # 값 산출 자체는 v0.1에서도 무해하다(기존 describe()·check_length()는 이
    # 필드를 읽지 않아 v0.1 출력이 불변). 이 값을 소비하는 신규 판정 분기는
    # 반드시 algo_version()=="v0.2" 게이트 안에만 둔다(boundary_guard 참조).
    mode: str = "min"

    def describe(self) -> str:
        scope = f"{self.per_item}당 " if getattr(self, "per_item", "") else ""
        if self.min is not None and self.max is not None:
            return f"{scope}{self.min}~{self.max}{self.unit}"
        if self.min is not None:
            return f"{scope}{self.min}{self.unit} 이상"
        if self.max is not None:
            return f"{scope}{self.max}{self.unit} 이하"
        return f"{scope}{self.unit} 요건"


@dataclass
class LengthCheck:
    target: Optional[LengthTarget]
    chars: int              # 공백 제외 글자수
    chars_with_space: int
    words: int
    status: str             # ok | short | over | mismatch | unknown
    message: str
    # 항목당 판정의 항목별 상세 [(라벨, 현재값, status), ...] — 검증기가 항목별
    # 델타 에러를 만들 때 재사용(문자열 파싱 없이).
    items: List[tuple] = None  # type: ignore[assignment]

    @property
    def has_target(self) -> bool:
        return self.target is not None


def _norm_unit(surface: str) -> str:
    return _UNIT_ALIASES.get(surface.lower(), surface)


def _to_int(s: str) -> int:
    return int(s.replace(",", ""))


def target_in_chars(target, *, prefer: str = "max") -> int:
    """분량 요건을 **글자 수**로 환산한다. 감지 안 됨·환산 불가면 0.

    `LengthTarget.min/max`는 단위가 붙은 수치다 — 페이지 5는 5자가 아니라 약
    4,500자다. 이 환산 없이 `.max`를 그대로 글자 수로 쓰면 페이지·매 요건이
    한 자릿수로 쪼그라들어 분량 판정이 통째로 무력해진다(2026-08-22 실측:
    unit 경로에서 'A4 5매' 요건의 `target_chars`가 5로 잡혀 하한 게이트(60자)에
    걸려 검증기가 아예 안 붙었다).

    '단어'는 글자로 환산하지 않는다 — 한국어/영어 혼합에서 안정적인 계수가 없다.
    잘못된 계수로 강제하느니 분량 강제를 걸지 않는 편이 낫다(0을 준다).
    """
    if target is None:
        return 0
    n = getattr(target, prefer, None)
    if n is None:
        n = getattr(target, "min" if prefer == "max" else "max", None)
    if not n:
        return 0
    unit = getattr(target, "unit", "")
    if unit == "자":
        return int(n)
    if unit in _APPROX_CHARS:
        return int(n) * _APPROX_CHARS[unit]
    return 0


def _resolve_mae(unit: str, text: str, start: int) -> str:
    """'매' 앞에 용지 표지(A4·용지)가 있으면 페이지로 승격, 없으면 원고지 매수 유지.

    같은 뜻을 '장'으로 쓰면 이미 페이지로 잡히는데(_JANG_A4) '매'로 쓰면
    원고지로 잡혀 목표 분량이 3분의 1 아래로 떨어지던 비대칭을 없앤다.
    """
    if unit != "매":
        return unit
    return "페이지" if _MAE_PAGE_LEAD.search(text[:start]) else unit


# 페이지 '참조'(읽기 지시) 배제 — "교재 120페이지를 참고", "20~35쪽을 읽고"는
# 분량 요건이 아니다. 매치 앞의 출처 표지 / 뒤의 읽기 동사를 본다.
_READ_BEFORE = re.compile(r"(교재|책|본문|자료|원서|논문|pp?\.)\s*$")
_READ_AFTER = re.compile(r"^\s*(을|를)?\s*(읽|참고|참조|펴|펼)")

# 슬라이드 장수는 **분량 요건이 아니다.** "슬라이드 8~12장"을 산문 12페이지로 읽으면
# 목표가 10,800자가 되는데, 같은 과제가 대개 "슬라이드당 글자 수는 최소화"라고
# 못박는다(examples/sample_presentation.txt 실측). 발표 자료의 크기는 장수로
# 세는 것이지 글자로 세는 것이 아니므로, 이 문맥의 페이지/매/장은 분량으로 보지
# 않는다(분량 요건 미감지 = 강제 없음이 정답).
_SLIDE_CTX = re.compile(r"(슬라이드|장표|프레젠테이션|발표\s*자료|[Pp][Pp][Tt])")


def _is_slide_count(text: str, start: int, end: int) -> bool:
    """매치 주변(앞 25자·뒤 15자)에 슬라이드 표지가 있으면 장수 세기다."""
    return bool(_SLIDE_CTX.search(text[max(0, start - 25):end + 15]))


def _is_reading_ref(text: str, start: int, end: int) -> bool:
    return bool(_READ_BEFORE.search(text[max(0, start - 8):start])
                or _READ_AFTER.match(text[end:end + 8]))


# 항목당 스코프 — "강의당 300자", "문항별 500자", "각 강좌 300자", "강의마다 …".
# '해당/상당'처럼 '당'으로 끝나는 일반 단어는 항목 단위가 아니다(어간 블록리스트).
_PER_ITEM_STOP = {"해", "상", "온", "정", "합", "타", "마"}
_PER_SUFFIX_RE = re.compile(r"([가-힣]{1,6}?)(?:당|별로|별|마다)\s*[:：]?\s*$")
_PER_EACH_RE = re.compile(r"각\s*([가-힣]{1,6})\s*[:：]?\s*$")


def _per_item_scope(text: str, start: int) -> str:
    """분량 매치 직전 문맥에서 '항목당' 단위 명사를 찾는다. 없으면 ""."""
    win = text[max(0, start - 16):start]
    m = _PER_SUFFIX_RE.search(win)
    if m and m.group(1) not in _PER_ITEM_STOP:
        return m.group(1)
    m = _PER_EACH_RE.search(win)
    if m:
        return m.group(1)
    return ""


def _scan_text(text: str) -> Optional[LengthTarget]:
    """한 덩어리 텍스트에서 첫 분량 요건을 찾는다(범위 우선)."""
    # 1) 범위: "500~800자", "500-800 words". 단위는 뒤에 한 번만 와도 됨.
    for m in re.finditer(_NUM + r"\s*[~\-–]\s*" + _NUM + r"\s*" + _UNIT, text):
        unit = _resolve_mae(_norm_unit(m.group(3)), text, m.start())
        # 페이지 범위가 읽기 참조("20~35쪽을 읽고")면 분량이 아니다.
        if unit == "페이지" and _is_reading_ref(text, m.start(), m.end()):
            continue
        if unit in ("페이지", "매") and _is_slide_count(text, m.start(), m.end()):
            continue
        lo, hi = _to_int(m.group(1)), _to_int(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        # 명시적 범위는 정의상 min·max 둘 다 → mode="range".
        return LengthTarget(unit=unit, min=lo, max=hi, raw=m.group(0),
                            per_item=_per_item_scope(text, m.start()),
                            mode="range")

    # 2) 단일 수치 후보 수집 — 수식어는 앞("최대 3000자")·뒤("3000자 이하") 모두 인정.
    #    (unit, n, mod, raw) 목록을 텍스트 순서로 모아 뒤에서 병합한다.
    cands: list[tuple] = []
    single = re.compile(r"(최소|최대)?\s*" + _NUM + r"\s*" + _UNIT
                        + r"\s*(이상|이하|이내|내외|안팎|정도|최소|최대)?")
    for m in single.finditer(text):
        lead, num, unit, trail = m.group(1), _to_int(m.group(2)), _norm_unit(m.group(3)), m.group(4)
        unit = _resolve_mae(unit, text, m.start())
        if unit == "페이지" and _is_reading_ref(text, m.start(), m.end()):
            continue  # "교재 120페이지를 참고하여" — 분량 아님
        if unit in ("페이지", "매") and _is_slide_count(text, m.start(), m.end()):
            continue  # "슬라이드 10장" — 장수 세기지 분량 아님
        mod = trail or lead or ""
        cands.append((unit, num, mod, m.group(0), m.start()))
    # '장'은 분량 문맥이 있을 때만 페이지 후보로.
    for m in _JANG_A4.finditer(text):
        if _is_slide_count(text, m.start(), m.end()):
            continue
        cands.append(("페이지", _to_int(m.group(1)), m.group(2) or "", m.group(0), m.start()))
    for m in _JANG_CTX.finditer(text):
        if _is_slide_count(text, m.start(), m.end()):
            continue  # "슬라이드 8~12장 분량" — 발표 크기는 글자로 세지 않는다
        mod = m.group(2)
        cands.append(("페이지", _to_int(m.group(1)), "" if mod == "분량" else mod,
                      m.group(0), m.start()))
    if not cands:
        return None

    # 병합: '자/단어' 후보를 페이지/매보다 우선, 같은 단위의 min/max를 합친다
    # ("최소 1000자, 최대 3000자" → min=1000, max=3000). 같은 경계 중복은 첫 값 유지.
    pref = [c for c in cands if c[0] in ("자", "단어")] or cands
    unit = pref[0][0]
    tgt = LengthTarget(unit=unit, raw=pref[0][3],
                       per_item=_per_item_scope(text, pref[0][4]))
    for u, n, mod, _raw, _pos in pref:
        if u != unit:
            continue
        if mod in ("이하", "이내", "최대"):
            if tgt.max is None:
                tgt.max = n
        elif mod in ("내외", "안팎", "정도"):
            # 내외 = 대략 ±10%를 하/상한으로.
            if tgt.min is None:
                tgt.min = int(n * 0.9)
            if tgt.max is None:
                tgt.max = int(n * 1.1)
        else:  # 이상/최소/무수식어 → 하한으로 본다(가장 흔함).
            if tgt.min is None:
                tgt.min = n
    # 감지 결과로 mode 확정 — max만 있는 요건("200자 이내/이하")은 "max",
    # 둘 다면 "range"(내외·최소+최대 병합 포함), 현행 다수(하한)는 "min".
    # 산출만 하고 여기서 소비하지 않는다(판정 분기는 v0.2 게이트 안, §4.6(a)).
    if tgt.min is not None and tgt.max is not None:
        tgt.mode = "range"
    elif tgt.max is not None:
        tgt.mode = "max"
    else:
        tgt.mode = "min"
    return tgt


def detect_length_target(spec: dict, docs: Optional[List] = None,
                         extra_sources: Optional[List] = None) -> Optional[LengthTarget]:
    """명세(requirements·constraints·goal)와 원문에서 분량 요건을 감지. 없으면 None.

    명세를 먼저 보고(요약돼 노이즈가 적음), 못 찾으면 원문 앞부분을 훑는다.
    extra_sources(eTL 관련자료·공지 — SourceDoc)는 마지막 순위로 훑는다:
    실코퍼스에서 과제 본문에 분량 요건이 있는 경우는 12%뿐, 나머지는 첨부·공지
    같은 '숨은 명세'에 실린다. 명세·원문이 이기고, 없을 때만 보조 소스를 믿는다.
    """
    chunks: List[str] = []
    if isinstance(spec, dict):
        for key in ("requirements", "constraints"):
            v = spec.get(key)
            if isinstance(v, list):
                chunks.extend(str(x) for x in v)
        for key in ("goal", "deliverable"):
            if isinstance(spec.get(key), str):
                chunks.append(spec[key])
    for c in chunks:
        t = _scan_text(c)
        if t is not None:
            return t
    # 명세에서 못 찾으면 원문(과제 지시문) 앞부분에서.
    for d in docs or []:
        text = getattr(d, "text", "") or ""
        t = _scan_text(text[:4000])
        if t is not None:
            return t
    # 그래도 없으면 eTL 관련자료·공지(숨은 명세) 앞부분에서.
    for s in extra_sources or []:
        text = getattr(s, "text", "") or ""
        t = _scan_text(text[:4000])
        if t is not None:
            return t
    return None


def measure_length(body: str) -> tuple[int, int, int]:
    """(공백 제외 글자수, 공백 포함 글자수, 단어수). 결정 마커는 제외."""
    clean = _DECISION_RE.sub("", body or "")
    with_space = len(clean.strip())
    no_space = len(re.sub(r"\s+", "", clean))
    words = len([w for w in clean.split() if w])
    return no_space, with_space, words


# 항목 헤드 후보 — 우선순위대로 시도(섞어 쓰면 오분할되므로 한 종류만 채택).
_HEAD_CIRCLED = re.compile(r"^\s*[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]", re.M)
_HEAD_NUMBERED = re.compile(r"^\s*\d{1,2}[.)]\s", re.M)
_HEAD_MD = re.compile(r"^\s*#{1,4}\s", re.M)


def split_items(body: str) -> List[tuple]:
    """항목형 본문(①②…, '1.' 번호, ## 헤딩)을 (라벨, 항목 본문) 목록으로 나눈다.

    항목당 분량 요건("강의당 300자")을 각 항목에 대조하기 위한 결정적 분할.
    같은 종류의 헤드가 2개 이상일 때만 그 종류로 나눈다(혼합 분할 방지). 못 나누면 [].
    """
    text = (body or "").strip()
    if not text:
        return []
    lines = text.splitlines()
    for head_re in (_HEAD_CIRCLED, _HEAD_NUMBERED, _HEAD_MD):
        starts = [i for i, ln in enumerate(lines) if head_re.match(ln)]
        if len(starts) < 2:
            continue
        if head_re is _HEAD_MD and not all(
                any(c.isdigit() for c in lines[s]) for s in starts):
            # MD 헤딩은 서론/본론/결론 같은 산문 구획에도 쓰인다 — 전부 번호를
            # 담은 헤딩(1주차·강의 1 등)일 때만 '항목'으로 본다. 아니면 판정
            # 불가(unknown)가 오판(항목당 분량을 구획에 들이댐)보다 안전하다.
            continue
        items = []
        for k, s in enumerate(starts):
            end = starts[k + 1] if k + 1 < len(starts) else len(lines)
            label = lines[s].strip().lstrip("# ").strip()[:30]
            chunk = "\n".join(lines[s + 1:end]).strip()
            items.append((label, chunk))
        return items
    return []


def _check_per_item(target: LengthTarget, body: str, totals: tuple,
                    expected_items: Optional[int] = None) -> Optional[LengthCheck]:
    """항목당 요건을 각 항목에 대조. 항목을 못 나누면 None(호출부가 unknown 처리).

    expected_items(양식에서 유도한 기대 항목 수)가 주어지면 개수 불일치를
    status="mismatch"로 '실패' 판정한다 — 양식을 무시한 산문이 '판정 불가'로
    조용히 빠져나가는 구멍을 막는다(실사용 갭).
    """
    no_space, with_space, words = totals
    items = split_items(body)
    if expected_items == 1:
        # split_items는 같은 헤드 2개 미만이면 못 나눠(혼합 분할 방지) 1개는
        # 구조적으로 안 나온다 — 단일 항목 양식은 본문 전체가 그 항목이다.
        # (0개 발견 mismatch로 판정하면 어떤 출력도 통과 불가가 된다.)
        items = [("", (body or "").strip())]
    if expected_items and len(items) != expected_items:
        found = len(items)
        return LengthCheck(
            target, no_space, with_space, words, "mismatch",
            f"항목 수 불일치 — 양식 기준 {expected_items}개 항목이 필요한데 "
            f"본문에서 {found}개 발견"
            + ("(항목 구분 없음 — 양식의 ①②… 구조를 유지해 각 항목을 작성할 것)"
               if found < 2 else ". 빠진 항목을 채우거나 번호 구조를 맞출 것"),
            items=[(lb, measure_length(ch)[0], "?") for lb, ch in items])
    if len(items) < 2 and expected_items != 1:
        return None
    lo, hi = target.min, target.max
    unit = "단어" if target.unit == "단어" else "자"
    parts, detail, n_short, n_over = [], [], 0, 0
    for label, chunk in items:
        ns, _, w = measure_length(chunk)
        cur = w if target.unit == "단어" else ns
        mark = label[:1] if label else "·"  # ①/1/# 첫 글자로 항목 지목
        if lo is not None and cur < lo:
            n_short += 1
            parts.append(f"{mark} {cur}{unit} 부족(약 {lo - cur}{unit} 더)")
            detail.append((label, cur, "short"))
        elif hi is not None and cur > hi:
            n_over += 1
            parts.append(f"{mark} {cur}{unit} 초과(약 {cur - hi}{unit} 줄이기)")
            detail.append((label, cur, "over"))
        else:
            parts.append(f"{mark} {cur}{unit} 충족")
            detail.append((label, cur, "ok"))
    status = "short" if n_short else ("over" if n_over else "ok")
    head = "분량 충족" if status == "ok" else ("분량 부족" if status == "short" else "분량 초과")
    msg = (f"{head} — 요건 {target.describe()}(항목 {len(items)}개 각각): "
           + " · ".join(parts))
    return LengthCheck(target, no_space, with_space, words, status, msg, items=detail)


def check_length(target: Optional[LengthTarget], body: str,
                 expected_items: Optional[int] = None) -> LengthCheck:
    """초안 본문을 요건과 대조. 요건이 없으면 측정치만 담아 status=unknown.

    항목당 요건(per_item)은 본문을 항목으로 나눠 '각각' 판정한다 — 전체 글자수에
    항목당 한도를 들이대면 정상 답안(항목 N개 × 300자)을 초과로 오판한다.
    expected_items가 있으면 항목 수 불일치를 mismatch(실패)로 판정한다.
    """
    no_space, with_space, words = measure_length(body)
    if target is not None and getattr(target, "per_item", ""):
        per = _check_per_item(target, body, (no_space, with_space, words),
                              expected_items=expected_items)
        if per is not None:
            return per
        return LengthCheck(target, no_space, with_space, words, "unknown",
                           f"요건 {target.describe()} — 항목(①·번호·헤딩) 구분을 찾지 "
                           f"못해 각각 판정 불가 · 전체 {no_space}자(공백 제외)")
    if target is None:
        return LengthCheck(None, no_space, with_space, words, "unknown",
                           f"분량 요건 미감지 · 현재 {no_space}자(공백 제외)")

    # 단위별 현재값 선택.
    if target.unit == "단어":
        current, unit = words, "단어"
    elif target.unit in ("페이지", "매"):
        current, unit = no_space, "자"  # 페이지/매는 글자로 환산해 근사 판정
    else:
        current, unit = no_space, "자"

    lo = target.min
    hi = target.max
    # 페이지/매 하한을 글자로 환산.
    if target.unit in ("페이지", "매") and lo is not None:
        lo = lo * _APPROX_CHARS[target.unit]
    if target.unit in ("페이지", "매") and hi is not None:
        hi = hi * _APPROX_CHARS[target.unit]

    status = "ok"
    if lo is not None and current < lo:
        status = "short"
    elif hi is not None and current > hi:
        status = "over"

    approx = " (근사)" if target.unit in ("페이지", "매") else ""
    if status == "short":
        msg = f"분량 부족 — 요건 {target.describe()}, 현재 {current}{unit}{approx} (약 {lo - current}{unit} 더 필요)"
    elif status == "over":
        msg = f"분량 초과 — 요건 {target.describe()}, 현재 {current}{unit}{approx} (약 {current - hi}{unit} 초과)"
    else:
        msg = f"분량 충족 — 요건 {target.describe()}, 현재 {current}{unit}{approx}"
    return LengthCheck(target, no_space, with_space, words, status, msg)
