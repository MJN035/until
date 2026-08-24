"""
생성 품질 안전장치 — 프롬프트 지시가 아니라 **생성 후 검증**으로 강제한다.

지시만으로는 안 지켜진다는 것이 이 리포의 반복된 관측이다(수치 날조 금지가
`measured_check`로, 분량이 `LengthValidator`로 코드화된 것과 같은 계보).
여기 있는 것도 전부 결정적이고, `BoundaryGuard`의 validator 프로토콜
(`validate(draft) -> ValidationResult`)을 그대로 따르므로 `extra_validators`에
꽂으면 실패 시 reask 루프에 자동으로 실린다.

  · `RepetitionValidator` — 최근 생성물과 n-gram이 겹치면 실패. 같은 문장이
    과제마다 재활용되는 것을 막는다(에피소드 few-shot을 켜면 특히 위험해진다).
  · `BannedPhraseValidator` — ToneSpec의 금지 표현을 사후에 검사한다.

민감·고위험 상황 판정은 `execution/sensitive.py`에 따로 있다(생성 차단이 아니라
사람 승인 대기이므로 성격이 다르다).
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence

from .boundary_guard import ValidationResult

#: 중복 판정에 쓸 n-gram 크기(어절 단위). 8어절이면 우연 일치가 사실상 없다.
DEFAULT_N = 8
#: 한 초안에서 허용할 최대 재사용 n-gram 수. 인용·정형구 때문에 0은 비현실적이다.
DEFAULT_MAX_HITS = 2
#: 비교에 쓸 최근 생성물 수 — 오래된 글까지 보면 정상적인 문체 일관성도 걸린다.
DEFAULT_RECENT = 5

_DECISION_RE = re.compile(r"\[\[DECISION:.*?\]\]", re.DOTALL)
_CITATION_RE = re.compile(r"\[자료\s*\d+\]|\[출처\?\]")
_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> List[str]:
    """비교용 어절 목록 — 결정 마커·인용 표식·표 줄은 뺀다.

    이것들은 시스템이 만드는 정형 문자열이라 과제가 달라도 당연히 겹친다.
    빼지 않으면 검사기가 자기 자신이 만든 형식을 표절로 신고한다.
    """
    body = _DECISION_RE.sub(" ", text or "")
    body = _CITATION_RE.sub(" ", body)
    body = _TABLE_LINE_RE.sub(" ", body)
    return [w for w in _WS_RE.sub(" ", body).strip().split(" ") if w]


def ngrams(text: str, n: int = DEFAULT_N) -> set:
    """어절 n-gram 집합. 어절이 n개 미만이면 빈 집합."""
    words = _normalize(text)
    if len(words) < n:
        return set()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def overlap_ngrams(text: str, previous: Iterable[str], n: int = DEFAULT_N) -> List[str]:
    """text가 이전 글들과 공유하는 n-gram 목록(정렬 — 결정적)."""
    current = ngrams(text, n)
    if not current:
        return []
    seen: set = set()
    for prev in previous or ():
        seen |= ngrams(prev, n)
    return sorted(current & seen)


class RepetitionValidator:
    """최근 생성물과의 n-gram 중복 검사 — 같은 문장 재활용을 막는다."""

    def __init__(self, recent_bodies: Sequence[str], n: int = DEFAULT_N,
                 max_hits: int = DEFAULT_MAX_HITS,
                 recent: int = DEFAULT_RECENT) -> None:
        self.recent_bodies = [b for b in list(recent_bodies or [])[-recent:] if b]
        self.n = max(3, int(n))
        self.max_hits = max(0, int(max_hits))

    def validate(self, draft) -> ValidationResult:
        if not self.recent_bodies:
            return ValidationResult(passed=True)
        hits = overlap_ngrams(getattr(draft, "body", "") or "",
                              self.recent_bodies, self.n)
        if len(hits) <= self.max_hits:
            return ValidationResult(passed=True)
        sample = "; ".join(f'"{h}"' for h in hits[:3])
        return ValidationResult(passed=False, errors=[
            f"최근 제출물과 똑같은 문장이 {len(hits)}곳 반복된다({sample} …). "
            "재사용한 문장을 이번 과제의 내용으로 다시 써라 — 문체는 유지하되 "
            "문장 자체를 베끼지 말 것."])


class BannedPhraseValidator:
    """ToneSpec의 금지 표현을 생성 후에 검사한다(프롬프트 지시만으로는 안 지켜짐)."""

    def __init__(self, phrases: Sequence[str]) -> None:
        self.phrases = [p for p in (phrases or ()) if str(p).strip()]

    def validate(self, draft) -> ValidationResult:
        if not self.phrases:
            return ValidationResult(passed=True)
        body = _DECISION_RE.sub(" ", getattr(draft, "body", "") or "")
        found = [p for p in self.phrases if p in body]
        if not found:
            return ValidationResult(passed=True)
        return ValidationResult(passed=False, errors=[
            f"금지 표현이 남아 있다: {', '.join(found)}. "
            "해당 표현을 쓰지 않고 같은 뜻을 전달하도록 그 문장을 고쳐라."])


def build_quality_validators(tone=None, recent_bodies: Optional[Sequence[str]] = None,
                             *, n: int = DEFAULT_N,
                             max_hits: int = DEFAULT_MAX_HITS) -> List[object]:
    """활성 검증기만 담아 반환한다(비어 있으면 [] — 기존 동작 그대로).

    금지 표현이 없고 비교할 최근 글도 없으면 아무 것도 만들지 않는다. 빈 검증기를
    끼워 넣지 않는 것이 reask 루프의 비용을 0으로 유지하는 방법이다.
    """
    out: List[object] = []
    banned = tuple(getattr(tone, "banned", ()) or ()) if tone is not None else ()
    if banned:
        out.append(BannedPhraseValidator(banned))
    bodies = [b for b in (recent_bodies or ()) if b]
    if bodies:
        out.append(RepetitionValidator(bodies, n=n, max_hits=max_hits))
    return out
