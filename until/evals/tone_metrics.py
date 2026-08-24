"""톤 준수 지표 — 생성된 본문에서 결정적으로 측정한다(LLM 0).

'톤이 좋아졌는가'는 자동 채점이 어렵다. 하지만 **규격을 지켰는가**의 상당 부분은
셀 수 있다: 종결어미 분포, 겸양 표현 밀도, 완충어 빈도, 이모지, 금지 표현.
사람이 읽고 판단할 부분(자연스러움·설득력)은 side-by-side로 넘기고, 여기서는
숫자로 말할 수 있는 것만 정직하게 잰다.

종결어미 판정은 `context/voice.py`의 탐지기를 그대로 재사용한다 — 학습(추출) 쪽과
측정(검증) 쪽이 다른 기준을 쓰면 지표가 자기 자신을 속인다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List

from ..context.voice import _detect_ending, _split_sentences

#: 겸양 표현 — deference 축의 관측 대리값.
_DEFERENCE_RE = re.compile(
    r"드립니다|드리고자|주시면\s*감사|여쭙|여쭤|말씀드리|올립니다|"
    r"괜찮으시다면|양해\s*부탁|번거로우시겠지만")
#: 완충어 — directness 축의 관측 대리값(낮을수록 직설적).
_HEDGE_RE = re.compile(
    r"혹시|괜찮으시다면|다름이\s*아니라|실례가\s*되지\s*않는다면|가능하시다면|"
    r"조심스럽게|어쩌면|다소")
#: 안부·공감 — warmth 축의 관측 대리값.
_WARMTH_RE = re.compile(
    r"안녕하|감사합니다|감사드립니다|고맙|건강하|잘\s*지내|수고|바쁘신")
_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")
_DECISION_RE = re.compile(r"\[\[DECISION:.*?\]\]", re.DOTALL)

#: speech_level → 그 등급에서 나와야 할 종결어미 정규식.
_LEVEL_ENDING_RE = {
    "하십시오체": re.compile(r"(습니다|습니까|입니다|ㅂ니다)\s*[.?!]?\s*$"),
    "해요체": re.compile(r"(어요|아요|해요|에요|예요|세요)\s*[.?!]?\s*$"),
    "한다체": re.compile(r"(다|이다|한다|였다|았다|었다)\s*[.?!]?\s*$"),
}


@dataclass
class ToneMetrics:
    chars: int = 0
    sentences: int = 0
    observed_level: str = "미상"
    level_match_ratio: float = 0.0     # 규격 종결어미로 끝난 문장 비율
    deference_hits: int = 0
    hedge_hits: int = 0
    warmth_hits: int = 0
    emoji_hits: int = 0
    banned_hits: List[str] = field(default_factory=list)
    decisions: int = 0

    def to_row(self) -> Dict[str, object]:
        return {
            "글자": self.chars, "문장": self.sentences,
            "관측 종결": self.observed_level,
            "규격 준수": f"{round(self.level_match_ratio * 100)}%",
            "겸양": self.deference_hits, "완충어": self.hedge_hits,
            "안부": self.warmth_hits, "이모지": self.emoji_hits,
            "금지어": len(self.banned_hits), "결정": self.decisions,
        }


def _body_without_markers(body: str) -> str:
    """결정 마커를 뺀 본문 — 마커 안의 질문 문장이 종결어미 통계를 흐리지 않게."""
    return _DECISION_RE.sub(" ", body or "")


def measure_tone(body: str, tone=None) -> ToneMetrics:
    """생성 본문의 톤 지표. tone(ToneSpec)이 있으면 규격 준수율·금지어까지 잰다."""
    raw = body or ""
    clean = _body_without_markers(raw)
    sentences = _split_sentences(clean)
    m = ToneMetrics(
        chars=len(clean.replace(" ", "").replace("\n", "")),
        sentences=len(sentences),
        observed_level=_detect_ending(sentences),
        deference_hits=len(_DEFERENCE_RE.findall(clean)),
        hedge_hits=len(_HEDGE_RE.findall(clean)),
        warmth_hits=len(_WARMTH_RE.findall(clean)),
        emoji_hits=len(_EMOJI_RE.findall(clean)),
        decisions=len(_DECISION_RE.findall(raw)),
    )
    if tone is not None:
        pattern = _LEVEL_ENDING_RE.get(getattr(tone, "speech_level", ""))
        if pattern is not None and sentences:
            hit = sum(1 for s in sentences if pattern.search(s.rstrip()))
            m.level_match_ratio = round(hit / len(sentences), 3)
        elif getattr(tone, "speech_level", "") == "혼합" and sentences:
            # 혼합은 두 등급 중 하나면 통과 — '기본 해요체 + 핵심 하십시오체'.
            polite = _LEVEL_ENDING_RE["해요체"]
            formal = _LEVEL_ENDING_RE["하십시오체"]
            hit = sum(1 for s in sentences
                      if polite.search(s.rstrip()) or formal.search(s.rstrip()))
            m.level_match_ratio = round(hit / len(sentences), 3)
        for phrase in getattr(tone, "banned", ()) or ():
            if phrase and phrase in clean:
                m.banned_hits.append(phrase)
    return m


def diff_row(before: ToneMetrics, after: ToneMetrics) -> Dict[str, str]:
    """off → on 변화량. 사람이 표에서 바로 읽도록 부호를 붙인다."""
    def d(a: int, b: int) -> str:
        delta = b - a
        return "0" if delta == 0 else (f"+{delta}" if delta > 0 else str(delta))
    return {
        "글자": d(before.chars, after.chars),
        "문장": d(before.sentences, after.sentences),
        "종결": (f"{before.observed_level}→{after.observed_level}"
                 if before.observed_level != after.observed_level else "="),
        "규격 준수": (f"{round(before.level_match_ratio * 100)}%"
                      f"→{round(after.level_match_ratio * 100)}%"),
        "겸양": d(before.deference_hits, after.deference_hits),
        "완충어": d(before.hedge_hits, after.hedge_hits),
        "안부": d(before.warmth_hits, after.warmth_hits),
        "이모지": d(before.emoji_hits, after.emoji_hits),
        "금지어": d(len(before.banned_hits), len(after.banned_hits)),
        "결정": d(before.decisions, after.decisions),
    }
