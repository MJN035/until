"""
L1 스타일 카드 — 문체 특징의 **구조화된** 요약(느리게 변함, 항상 주입).

기억 3계층 중 첫 층이다. 핵심 규칙 하나: **자유 서술로 저장하지 않는다.**
LLM으로 뽑든 통계로 뽑든, 결과는 반드시 `tone.ToneSpec`의 필드로 매핑되는
구조화된 값이어야 한다. 자유 서술 요약은 프롬프트에 넣는 순간 모델이 제멋대로
해석하고, 시간이 지나면 무엇이 왜 바뀌었는지 추적할 수 없다.

  · 통계 경로(결정적, LLM 0): `VoiceProfile` → `tone.base_from_voice`
  · LLM 경로(선택, 호출자가 llm을 주입해야만 켜짐): 구조화 출력(schema)으로
    ToneSpec 필드만 뽑는다. `context/voice.py::enhance_voice_profile`과 같은
    예외 형태 — 기본값 `llm=None`이면 이 모듈은 완전히 결정적이다.

사람이 읽을 근거는 `notes`에만 담고 **프롬프트에는 절대 넣지 않는다**(자유 서술
금지 규칙의 실제 강제 지점). 카드는 `persona.json` 안에 함께 저장돼 톤 규격의
기준선(`PersonaBase.defaults`)이 비어 있을 때 그 자리를 채운다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .tone import SPEECH_LEVELS, EMOJI_POLICIES, SELF_REFERENCES, sanitize_delta

#: LLM 추출에 쓸 샘플 상한(문체 판정엔 충분 — 토큰 폭주 방지).
MAX_SAMPLE_CHARS = 6000
MAX_SAMPLE_DOCS = 6


@dataclass
class StyleCard:
    """ToneSpec 필드로 매핑되는 문체 요약 + 그 근거."""
    fields: Dict[str, Any] = field(default_factory=dict)
    n_samples: int = 0
    source: str = "voice_profile"     # voice_profile | llm | edit_patterns | user
    notes: Tuple[str, ...] = ()       # 사람용 근거 — 프롬프트에 넣지 않는다
    updated_at: str = ""

    def is_empty(self) -> bool:
        return not self.fields

    def to_dict(self) -> dict:
        return {"fields": dict(self.fields), "n_samples": int(self.n_samples),
                "source": self.source, "notes": list(self.notes),
                "updated_at": self.updated_at}

    @classmethod
    def from_dict(cls, raw: Any) -> "StyleCard":
        if not isinstance(raw, dict):
            return cls()
        notes = raw.get("notes")
        clean_notes = tuple(str(n)[:200] for n in notes[:10]) \
            if isinstance(notes, list) else ()
        n = raw.get("n_samples")
        return cls(
            fields=sanitize_delta(raw.get("fields")),
            n_samples=int(n) if isinstance(n, int) and n >= 0 else 0,
            source=str(raw.get("source") or "voice_profile")[:32],
            notes=clean_notes,
            updated_at=str(raw.get("updated_at") or "")[:32],
        )


#: LLM 구조화 출력 스키마 — 자유 서술이 끼어들 자리를 아예 만들지 않는다.
#: (요약 문장 필드가 없다는 것이 이 스키마의 요점이다.)
EXTRACTION_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "speech_level": {"type": "string", "enum": list(SPEECH_LEVELS)},
        "formality": {"type": "integer", "minimum": 1, "maximum": 5},
        "deference": {"type": "integer", "minimum": 1, "maximum": 5},
        "warmth": {"type": "integer", "minimum": 1, "maximum": 5},
        "directness": {"type": "integer", "minimum": 1, "maximum": 5},
        "emoji_policy": {"type": "string", "enum": list(EMOJI_POLICIES)},
        "self_reference": {"type": "string", "enum": list(SELF_REFERENCES)},
        "endings": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "signature": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "banned": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
    },
    "required": ["speech_level", "formality", "deference", "warmth", "directness"],
}

_EXTRACT_SYSTEM = (
    "당신은 한국어 문체 분석기다. 글의 내용·주장·사실 관계는 판단하지 말고 "
    "**말투의 형식적 특징만** 측정한다. 반드시 주어진 JSON 스키마에 맞는 객체 하나만 "
    "출력하고, 스키마에 없는 필드나 설명 문장을 덧붙이지 마라."
)

_AXIS_GUIDE = (
    "각 축의 정의:\n"
    "- speech_level: 하십시오체(~습니다) | 해요체(~해요) | 혼합 | 한다체(~다, 존댓말 아님)\n"
    "- formality: 1 완전한 구어체 ↔ 5 완전한 문어체\n"
    "- deference: 1 겸양 없음 ↔ 5 '~드립니다/~주시면 감사하겠습니다'가 잦음\n"
    "- warmth: 1 용건만 ↔ 5 안부·공감이 잦음\n"
    "- directness: 1 완충어('혹시', '괜찮으시다면')가 매우 잦음 ↔ 5 완충어 없이 직설\n"
    "- endings: 실제로 자주 쓰인 종결어미\n"
    "- signature: 이 사람이 반복해서 쓰는 특징적 표현\n"
    "- banned: 이 사람이 (거의) 쓰지 않는 표현\n"
)


def _samples_text(texts: List[str]) -> str:
    picked = [t.strip() for t in (texts or []) if t and t.strip()][:MAX_SAMPLE_DOCS]
    joined = "\n\n---\n\n".join(t[:2000] for t in picked)
    return joined[:MAX_SAMPLE_CHARS]


def extract_style_fields(texts: List[str], llm=None) -> Dict[str, Any]:
    """사용자 과거 글에서 ToneSpec 필드를 뽑는다(LLM 1콜). 실패는 빈 dict.

    `llm=None`이면 호출 자체가 없다 — 이 모듈의 기본 경로는 결정적이다.
    반환값은 `sanitize_delta`를 통과한 값만 담는다(모델이 스키마 밖 값을 내도 차단).
    """
    if llm is None:
        return {}
    sample = _samples_text(texts)
    if not sample:
        return {}
    user = f"{_AXIS_GUIDE}\n아래 글 샘플의 말투를 위 축으로 측정하라.\n\n{sample}"
    try:
        res = llm.complete(_EXTRACT_SYSTEM, user, tag="style-card", json=True,
                           schema=EXTRACTION_SCHEMA)
        data = json.loads(res.text)
    except Exception:
        return {}      # 문체 추출 실패가 초안 생성을 막지 않는다
    return sanitize_delta(data)


def build_style_card(voice=None, texts: Optional[List[str]] = None,
                     llm=None) -> StyleCard:
    """통계(항상) + LLM(선택)로 스타일 카드를 만든다.

    LLM 값이 통계 값을 덮는다 — 통계는 종결어미·문장 길이 같은 표층만 볼 수 있고,
    겸양·완충어 밀도는 세지 못한다. 단 LLM이 실패하면 통계 카드가 그대로 남는다.
    """
    from .tone import base_from_voice

    stat_fields = base_from_voice(voice).defaults
    n_samples = int(getattr(voice, "n_samples", 0) or 0)
    notes: List[str] = []
    if stat_fields:
        notes.append(f"통계 추출: 표본 {n_samples}건, "
                     f"종결어미 '{getattr(voice, 'ending_style', '미상')}', "
                     f"평균 문장 {getattr(voice, 'avg_sentence_len', 0)}자")

    merged = dict(stat_fields)
    source = "voice_profile" if stat_fields else "default"
    llm_fields = extract_style_fields(texts or [], llm=llm)
    if llm_fields:
        merged.update(llm_fields)
        source = "llm"
        notes.append(f"LLM 구조화 추출: {', '.join(sorted(llm_fields))} 축 갱신")
        if texts:
            n_samples = max(n_samples, len(texts))

    return StyleCard(
        fields=sanitize_delta(merged), n_samples=n_samples, source=source,
        notes=tuple(notes),
        updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))


def merge_card(existing: StyleCard, incoming: StyleCard) -> StyleCard:
    """느리게 변하는 층답게 **덮어쓰기가 아니라 병합**한다.

    새 카드가 말하지 않는 축은 기존 값을 유지한다. 표본이 적은 재학습이 이미
    쌓인 축을 통째로 지우는 사고를 막는다(문체는 한 번의 관측으로 뒤집을 대상이 아니다).
    """
    if incoming.is_empty():
        return existing
    fields = dict(existing.fields)
    fields.update(incoming.fields)
    notes = tuple(list(existing.notes)[-4:] + list(incoming.notes))[-8:]
    return StyleCard(
        fields=sanitize_delta(fields),
        n_samples=max(existing.n_samples, incoming.n_samples),
        source=incoming.source, notes=notes, updated_at=incoming.updated_at)


def describe(card: StyleCard) -> str:
    """CLI·설정 화면용 한 줄 요약 — 무엇이 기억되고 있는지 투명하게."""
    if card.is_empty():
        return "저장된 스타일 카드 없음"
    axes = ", ".join(f"{k}={v}" for k, v in sorted(card.fields.items()))
    return f"[{card.source} · 표본 {card.n_samples}건] {axes}"
