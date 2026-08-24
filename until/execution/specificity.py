"""구체성 검증 — "사례를 소개받고 개념을 체험하였다"를 잡는다(재설계 6단계).

결정적 지표만 사용(LLM 판정 금지):
- 공허 문형: 블랙리스트 패턴에 걸리는 문장 비율
- 고유 용어 밀도: 자료·제목에서 뽑은 용어 사전 대비 본문 등장 수 / 문장 수
  (강의 '제목' 단어만 반복하는 것은 가점하지 않음 — 별도 집계)
- 구체 신호: 숫자·단위·[자료N] 인용의 존재
임계값은 config(UNTIL_SPECIFICITY_MIN) — 초기값은 eval로 조정 전제.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

# 공허 문형 — 강의 제목만 알면 누구나 쓸 수 있는 문장(뒤에 구체 대상 없이 끝남).
_EMPTY_PATTERNS = [
    r"에\s*대해\s*(?:알게\s*되었|배웠|이해하)",
    r"(?:를|을)\s*통해\s*(?:직접\s*)?체험하",
    r"많은\s*도움이\s*되었",
    r"이해할\s*수\s*있었",
    r"다양한\s*[가-힣]{1,8}(?:을|를)\s*(?:배우|접하|경험)",
    r"여러\s*가지\s*[가-힣]{1,8}(?:을|를)\s*(?:배우|접하)",
    r"등(?:을|를)\s*배웠",
    r"좋은\s*(?:기회|경험)(?:였|이었)",
    r"유익(?:했|한\s*시간)",
    r"인상\s*깊었[다습]",          # 뒤에 구체 대상 없이 문장이 끝나는 꼴
    r"소개(?:받|되)[^.\n]{0,6}[.\n]",
    r"실습(?:을|를)?\s*진행하였다[.\n]",
]
_EMPTY_RES = [re.compile(p) for p in _EMPTY_PATTERNS]

_SENT_SPLIT_RE = re.compile(r"(?<=[.!?다요])\s+")
_NUM_RE = re.compile(r"\d")
_CITE_RE = re.compile(r"\[자료\d+\]")
_TERM_RE = re.compile(r"[가-힣A-Za-z0-9]{2,}")
_TERM_STOP = {"강의", "수강", "내용", "그리고", "하지만", "대해", "통해", "것을",
              "있다", "한다", "했다", "되었다", "있었다"}


@dataclass
class SpecificityReport:
    score: float                      # 0~1 (높을수록 구체적)
    n_sentences: int = 0
    empty_sentences: List[str] = field(default_factory=list)  # 위반 문장 원문
    term_hits: int = 0                # 자료 용어 등장 수(제목 단어 제외)
    title_only_hits: int = 0          # 제목 단어 반복(가점 없음, 진단용)
    has_numbers: bool = False
    has_citation: bool = False

    @property
    def ok(self) -> bool:
        return not self.empty_sentences and self.score >= 0.0


def _sentences(body: str) -> List[str]:
    out = []
    for raw in _SENT_SPLIT_RE.split(" ".join((body or "").split())):
        s = raw.strip()
        if len(s) >= 8:
            out.append(s)
    return out


def _vocab(texts: List[str], stop: set) -> set:
    v = set()
    for t in texts or []:
        for tok in _TERM_RE.findall(t or ""):
            if tok.lower() not in _TERM_STOP and tok not in stop:
                v.add(tok)
    return v


def assess_specificity(body: str, *, source_texts: Optional[List[str]] = None,
                       title: str = "") -> SpecificityReport:
    """단위 본문의 구체성 점수(0~1)와 위반 문장 목록(결정적).

    점수 = 1 - 공허문장비율(0.6 가중) + 용어밀도(0.3 상한) + 구체신호(0.1)
    형태의 합성(0~1로 절단). 임계 판정은 호출부(config)가 한다.
    """
    r = SpecificityReport(score=0.0)
    sents = _sentences(body)
    r.n_sentences = len(sents)
    if not sents:
        return r
    for s in sents:
        if any(rx.search(s) for rx in _EMPTY_RES):
            r.empty_sentences.append(s)
    empty_ratio = len(r.empty_sentences) / len(sents)

    title_terms = set(_TERM_RE.findall(title or ""))
    vocab = _vocab(source_texts or [], stop=title_terms)
    joined = body or ""
    r.term_hits = sum(1 for t in vocab if t in joined)
    r.title_only_hits = sum(1 for t in title_terms
                            if t and t.lower() not in _TERM_STOP and t in joined)
    density = min(0.3, 0.06 * r.term_hits)  # 용어 5개면 상한

    r.has_numbers = bool(_NUM_RE.search(joined))
    r.has_citation = bool(_CITE_RE.search(joined))
    signal = 0.05 * int(r.has_numbers) + 0.05 * int(r.has_citation)

    r.score = max(0.0, min(1.0, 0.6 * (1.0 - empty_ratio) + density + signal))
    return r


class SpecificityValidator:
    """구체성 미달 시 reask — 위반 문장을 그대로 인용해 교정을 지시한다.

    재생성 지침: "이 문장은 강의 제목만 알면 쓸 수 있다. 근거 원장의 구체 내용으로
    대체하거나, 근거가 없으면 DECISION으로 남겨라."(LLM 자기평가가 아니라 결정적
    패턴·용어 사전 판정)
    """

    def __init__(self, source_texts: Optional[List[str]] = None,
                 title: str = "", min_score: float = 0.55,
                 max_empty: int = 0):
        self.source_texts = source_texts or []
        self.title = title
        self.min_score = min_score
        self.max_empty = max_empty

    def validate(self, draft) -> "object":
        from .boundary_guard import ValidationResult
        body = getattr(draft, "body", "") or ""
        rep = assess_specificity(body, source_texts=self.source_texts,
                                 title=self.title)
        errors: List[str] = []
        if len(rep.empty_sentences) > self.max_empty:
            for s in rep.empty_sentences[:3]:
                errors.append(
                    f"공허한 문장: \"{s[:100]}\" — 강의 제목만 알면 쓸 수 있는 "
                    "문장이다. 근거 자료의 구체 내용(사례·수치·개념)으로 대체하거나, "
                    "근거가 없으면 그 자리를 [[DECISION: ...]]으로 남겨라.")
        if rep.score < self.min_score:
            errors.append(
                f"구체성 부족(점수 {rep.score:.2f} < {self.min_score:.2f}) — "
                f"자료 용어 {rep.term_hits}개만 등장. 근거 발췌의 구체 내용을 "
                "인용([자료N])하며 서술하라. 제목 단어 반복은 구체성이 아니다.")
        return ValidationResult(passed=not errors, errors=errors)
