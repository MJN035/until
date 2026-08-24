"""근거 없는 실측 수치 사후 검출 — 결정적(LLM 0, 네트워크 0).

legacy 생성 경로(hdl_lab·lab_report_cycle(result))는 프롬프트 지침(measured_ban)만으로
"파형·합성 수치·실측값을 지어내지 말라"고 지시한다. LLM이 그 지침을 무시하면 지어낸
수치가 그대로 초안에 실려 제출된다(학문적 부정 위험). 이 모듈은 초안 본문에서 실측성
수치 표현을 정규식으로 뽑아, 그 숫자가 제공된 근거 자료(evidence_texts) 어디에도
등장하지 않으면 '근거 없음'으로 표시한다. 판정만 하고 아무것도 고치지 않는다
(경계선 철학 — capture/context/boundary와 같은 결정적 검증기 계열).
"""
from __future__ import annotations
from typing import List
import re

# [[DECISION: ...]] 안은 이미 빈칸이므로 검사 대상에서 제외.
_DECISION_RE = re.compile(r"\[\[DECISION:.*?\]\]", re.DOTALL)

# 합성/타이밍 수치 — 숫자가 단위 앞에 오는 형태.
_SYNTH_TIMING_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:MHz|GHz|ns|ps|LUT|FF|slices?)", re.IGNORECASE)

# LUT/FF 사용량·개수 문맥 — 단위(LUT/FF)가 숫자 앞에 오는 형태.
_LUTFF_USAGE_RE = re.compile(
    r"(?:LUT|FF)\s*(?:사용량|개수)?\s*[:：=]?\s*\d+(?:\.\d+)?\s*개?", re.IGNORECASE)

# 측정/오차 값.
_MEASURE_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:mV|V|mA|A|Ω|ohm|Hz|kHz|dB|%)", re.IGNORECASE)
_ERROR_KO_RE = re.compile(r"오차\s*\d+(?:\.\d+)?\s*%?")
_PLUSMINUS_RE = re.compile(r"±\s*\d+(?:\.\d+)?")

_PATTERNS = [
    _SYNTH_TIMING_RE,
    _LUTFF_USAGE_RE,
    _MEASURE_RE,
    _ERROR_KO_RE,
    _PLUSMINUS_RE,
]

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")

# 이 검증기가 활성화되는 strategy — hdl_lab 전체, lab_report_cycle은 result 단계만.
_ALWAYS_ON = {"hdl_lab"}
_RESULT_ONLY = {"lab_report_cycle"}


def _is_active(strategy: str, stage: str) -> bool:
    if strategy in _ALWAYS_ON:
        return True
    if strategy in _RESULT_ONLY and stage == "result":
        return True
    return False


def find_ungrounded_measurements(
    body: str, evidence_texts: list[str], *, strategy: str = "", stage: str = ""
) -> List[str]:
    """초안 본문에서 근거 없는 실측성 수치 조각을 찾아 문맥과 함께 반환.

    strategy가 hdl_lab이거나 (lab_report_cycle이고 stage=="result")일 때만 동작한다.
    그 외에는 항상 빈 리스트(순수 함수, 결정적, LLM/네트워크 없음).

    각 수치 표현에서 숫자 토큰을 뽑아, evidence_texts(제공 자료·사용자 답변 등)에서
    같은 숫자 정규식으로 추출한 숫자 토큰 집합과 정확히 일치(==)하는지 본다.
    (부분 문자열 검사는 폐지 — "1200"이 "12000" 안에 우연히 포함되는 식의 오판을 막는다.)
    일치하면 근거 있음(통과), 아니면 그 조각(앞뒤 문맥 40자 포함)을 결과에 담는다.
    """
    if not _is_active(strategy, stage):
        return []
    if not body:
        return []

    decision_spans = [m.span() for m in _DECISION_RE.finditer(body)]

    def _in_decision(start: int, end: int) -> bool:
        return any(ds <= start and end <= de for ds, de in decision_spans)

    evidence_joined = "".join(t or "" for t in (evidence_texts or []))
    evidence_nums = set(_NUM_RE.findall(evidence_joined))

    raw_matches = []
    for pat in _PATTERNS:
        for m in pat.finditer(body):
            if _in_decision(*m.span()):
                continue
            raw_matches.append(m)

    # 겹치는 매치는 더 넓은 것 하나만 남긴다(같은 수치를 여러 패턴이 중복 검출 방지).
    raw_matches.sort(key=lambda m: (m.start(), -(m.end() - m.start())))
    accepted = []
    for m in raw_matches:
        if any(a.start() <= m.start() and m.end() <= a.end() for a in accepted):
            continue
        accepted.append(m)
    accepted.sort(key=lambda m: m.start())

    results: List[str] = []
    for m in accepted:
        num_m = _NUM_RE.search(m.group())
        if not num_m:
            continue
        num_token = num_m.group()
        if num_token in evidence_nums:
            continue  # 근거 있음 — 통과 (숫자 토큰 정확 일치)
        start, end = m.span()
        ctx_start = max(0, start - 40)
        ctx_end = min(len(body), end + 40)
        snippet = body[ctx_start:ctx_end].strip()
        results.append(snippet)
    return results
