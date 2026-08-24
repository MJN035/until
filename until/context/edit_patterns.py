"""
반복되는 수정 패턴 → L1 스타일 카드 델타 (배치 경로의 **인터페이스**).

`edit_events.jsonl`에 쌓인 (before, after) 쌍을 훑어 "이 사람이 반복해서 고치는
것"을 ToneSpec 필드 델타 후보로 뽑는다. 전부 결정적이다(LLM 0) — 문단 짝짓기는
`diffview`가, 종결어미 판정은 `context/voice.py`가 이미 하고 있는 그 함수다.

**이번 범위에서 자동 적용은 하지 않는다.** `apply_patterns_to_persona`는
`confirm=True` 없이는 아무 것도 쓰지 않고, 스케줄링(주기 실행)은 TODO다.
이유는 두 가지다:
  1. 지금 쌓이는 수정 대부분이 `llm_revise`·`finalize`라 **사람이 직접 고친 신호가
     아니다**(edit_events 모듈 docstring 참조). 약한 신호로 문체를 자동 갱신하면
     모델이 자기 출력을 다시 학습하는 되먹임이 된다.
  2. 문체는 한 번의 관측으로 뒤집을 대상이 아니다 — 최소 표본을 넘겨야 한다.

TODO(스케줄링): 주기 실행 진입점은 아직 없다. 붙일 때는
  · 실행 주기와 최소 표본(`MIN_EVENTS`)을 함께 정하고,
  · 적용 전후 스타일 카드를 남겨 되돌릴 수 있게 하고,
  · `human` 가중치가 충분히 쌓였을 때만 켠다.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .edit_events import EditEvent, SOURCE_WEIGHT, load_edit_events

#: 이 미만이면 패턴을 말하지 않는다 — 표본 부족을 '경향'으로 포장하지 않기 위해.
MIN_EVENTS = 5
#: 가중 관측이 이 값을 넘어야 한 축을 제안한다(사람 수정 5건 ≈ 5.0).
MIN_WEIGHTED = 3.0
#: 표현 후보로 올릴 최소 반복 횟수.
MIN_PHRASE_HITS = 3

_SENT_SPLIT = re.compile(r"[.!?\n]+")
_PHRASE = re.compile(r"[가-힣]{2,8}")


@dataclass
class PatternSummary:
    """관측 결과 — 제안(suggested_delta)과 그 근거(evidence)를 항상 함께 낸다."""
    suggested_delta: Dict[str, Any] = field(default_factory=dict)
    evidence: List[str] = field(default_factory=list)
    n_events: int = 0
    weighted: float = 0.0
    enough: bool = False

    def is_empty(self) -> bool:
        return not self.suggested_delta


def _paragraph_pairs(event: EditEvent) -> List[Tuple[str, str]]:
    """'수정'으로 짝지어진 (before 문단, after 문단) 쌍만 — 추가·삭제는 제외."""
    from ..diffview import diff_drafts
    return [(c.before, c.after) for c in diff_drafts(event.before, event.after)
            if c.kind == "changed"]


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text or "") if s.strip()]


def _phrases(text: str) -> Counter:
    return Counter(_PHRASE.findall(text or ""))


def summarize_edit_patterns(events: Optional[List[EditEvent]] = None, *,
                            min_events: int = MIN_EVENTS) -> PatternSummary:
    """반복 수정에서 ToneSpec 델타 후보를 뽑는다. 표본 부족이면 빈 제안.

    출처별 가중치를 적용한다 — `llm_revise` 100건이 `human` 5건을 이기면 안 된다.
    """
    from .tone import sanitize_delta
    from .voice import _detect_ending

    rows = list(events if events is not None else load_edit_events())
    summary = PatternSummary(n_events=len(rows))
    summary.weighted = round(sum(SOURCE_WEIGHT.get(e.edit_source, 0.0) for e in rows), 2)
    summary.enough = len(rows) >= min_events and summary.weighted >= MIN_WEIGHTED
    if not summary.enough:
        return summary

    removed: Counter = Counter()
    added: Counter = Counter()
    before_sents: List[str] = []
    after_sents: List[str] = []
    len_before, len_after, n_pairs = 0, 0, 0

    for event in rows:
        weight = SOURCE_WEIGHT.get(event.edit_source, 0.0)
        if weight <= 0:
            continue
        for before, after in _paragraph_pairs(event):
            n_pairs += 1
            len_before += len(before)
            len_after += len(after)
            before_sents.extend(_sentences(before))
            after_sents.extend(_sentences(after))
            b_phr, a_phr = _phrases(before), _phrases(after)
            for word, count in (b_phr - a_phr).items():
                removed[word] += count * weight
            for word, count in (a_phr - b_phr).items():
                added[word] += count * weight

    if not n_pairs:
        summary.enough = False
        return summary

    delta: Dict[str, Any] = {}

    # ① 종결어미 — before/after의 등급이 일관되게 달라지면 speech_level 제안.
    level_before = _detect_ending(before_sents)
    level_after = _detect_ending(after_sents)
    mapping = {"합니다체": "하십시오체", "해요체": "해요체", "한다체": "한다체"}
    if (level_after in mapping and level_before != level_after
            and level_after != "미상"):
        delta["speech_level"] = mapping[level_after]
        summary.evidence.append(
            f"종결어미가 반복적으로 '{level_before}' → '{level_after}'로 고쳐짐")

    # ② 분량 감각 — 사람이 계속 줄이거나 계속 늘리면 목표 문장 수를 제안.
    if len_before and n_pairs >= 3:
        change = (len_after - len_before) / len_before
        if abs(change) >= 0.15:
            avg_sents = max(1, round(len(after_sents) / n_pairs))
            delta["target_sentences"] = avg_sents
            direction = "줄임" if change < 0 else "늘림"
            summary.evidence.append(
                f"문단 길이를 반복적으로 {direction}({round(change * 100)}%) — "
                f"수정 후 평균 {avg_sents}문장")

    # ③ 반복해서 지워지는 표현 → 금지 표현 후보.
    banned = [w for w, s in removed.most_common(12)
              if s >= MIN_PHRASE_HITS and added.get(w, 0) == 0][:6]
    if banned:
        delta["banned"] = banned
        summary.evidence.append(f"반복적으로 삭제된 표현: {', '.join(banned)}")

    # ④ 반복해서 덧붙는 표현 → 시그니처 후보.
    signature = [w for w, s in added.most_common(12)
                 if s >= MIN_PHRASE_HITS and removed.get(w, 0) == 0][:6]
    if signature:
        delta["signature"] = signature
        summary.evidence.append(f"반복적으로 추가된 표현: {', '.join(signature)}")

    summary.suggested_delta = sanitize_delta(delta)
    return summary


def apply_patterns_to_persona(summary: PatternSummary, *, confirm: bool = False,
                              path=None) -> bool:
    """제안을 L1 스타일 카드에 반영한다. **confirm=True 없이는 아무것도 하지 않는다.**

    자동 적용을 기본값으로 두지 않는 이유는 모듈 docstring 참조(약한 신호 되먹임).
    반영 시에도 덮어쓰기가 아니라 `style_card.merge_card` 병합이다.
    """
    if not confirm or summary is None or summary.is_empty() or not summary.enough:
        return False
    from .style_card import StyleCard, merge_card
    from .tone import load_persona, save_persona
    from datetime import datetime, timezone

    store = load_persona(path)
    incoming = StyleCard(
        fields=summary.suggested_delta,
        n_samples=summary.n_events, source="edit_patterns",
        notes=tuple(summary.evidence),
        updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    existing = store.style_card if store.style_card is not None else StyleCard()
    store.style_card = merge_card(existing, incoming)
    try:
        save_persona(store, path)
    except OSError:
        return False
    return True


def describe(summary: Optional[PatternSummary] = None) -> str:
    """CLI용 한 줄 — 무엇을 제안하는지, 왜 아직 적용하지 않는지."""
    s = summary if summary is not None else summarize_edit_patterns()
    if not s.enough:
        return (f"표본 부족 — 수정 {s.n_events}건(가중 {s.weighted}) · "
                f"제안하려면 {MIN_EVENTS}건·가중 {MIN_WEIGHTED} 이상 필요")
    if s.is_empty():
        return f"수정 {s.n_events}건(가중 {s.weighted}) 관측 — 일관된 패턴 없음"
    axes = ", ".join(f"{k}={v}" for k, v in sorted(s.suggested_delta.items()))
    return (f"제안(미적용): {axes} · 근거 {len(s.evidence)}건 "
            "— 적용은 apply_patterns_to_persona(confirm=True)")
