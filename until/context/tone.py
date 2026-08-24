"""
ToneSpec — 과제별 톤 레지스터(상속 + 델타, 결정적·LLM 0).

기존 `VoiceProfile`(voice.py)은 "이 사람이 평소 어떻게 쓰는가"만 안다 — 종결어미 1축이
speech_level과 formality를 뭉뚱그리고 있다. 하지만 같은 사람이라도 **교수에게 낼 질의**와
**실험 보고서**와 **팀 회의록**의 말투는 다르다. 그 차이는 존댓말/반말이 아니라
**존댓말 안에서** 난다(겸양 밀도·완충어·안부 표현). 이 모듈이 그 축을 모델링한다.

구조는 **상속 + 델타**다. 레지스터마다 프로파일을 복제하지 않는다:

    NEUTRAL(필드 기본값)
      → PersonaBase.defaults      (이 사람의 기준선 — VoiceProfile/사용자 설정에서)
      → REGISTER_PRESETS[key]     (이 과제·수신자가 요구하는 것 — 제약하는 축만)
      → RegisterOverride.delta    (사용자가 이 레지스터에 직접 못박은 값 — 항상 최종)
      = ToneSpec

프리셋이 페르소나 뒤에 오는 이유: 교수에게 내는 질의는 그 사람이 평소 건조하게 쓰더라도
겸양이 필요하다. 대신 프리셋은 **제약하는 축만** 적고 나머지는 비워 둬서, 손대지 않은
축에는 그 사람의 기준선이 그대로 살아남는다.

speech_level에 `한다체`가 있는 이유(설계 결정): Until의 주 산출물은 에세이·보고서이고
그건 수신자가 없는 문어체 평서형이다. 존댓말 3종(하십시오체/해요체/혼합)만 두면
에세이가 '~습니다'로 나와 오히려 퇴행한다. 존댓말 축은 수신자가 있는 레지스터에서,
`한다체`는 수신자가 없는 레지스터에서 쓴다.

핵심 계약:
  · `render_tone_spec(spec)`은 **결정적**이다 — 같은 ToneSpec이면 항상 같은 문자열.
    (tests/test_tone.py가 SHA-256으로 고정한다. 프롬프트 A/B의 기준이 이 성질이다.)
  · 저장은 `persona.json` 하나 — voice_profile.json과 같은 계보(버전 필드 + 원자적
    교체 + uid 스코프 thread-local 오버라이드).
  · 실패는 조용히 중립값 — 톤 규격이 없다고 초안 생성이 막히면 안 된다.
"""
from __future__ import annotations

import hashlib
import json
import threading as _threading
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import atomicio

STORE_VERSION = 1
PERSONA_PATH = Path("_until_work/persona.json")

#: 상대높임 등급. 앞 3개가 존댓말 축, `한다체`는 수신자 없는 문어체(위 docstring 참조).
SPEECH_LEVELS = ("하십시오체", "해요체", "혼합", "한다체")
EMOJI_POLICIES = ("금지", "최소", "허용")
SELF_REFERENCES = ("저", "저희", "없음")

#: 1~5 척도 축 — 병합·검증·직렬화가 전부 이 목록을 돈다.
SCALE_FIELDS = ("formality", "deference", "warmth", "directness")


@dataclass(frozen=True)
class ToneSpec:
    """프롬프트에 주입되기 직전의 최종 톤 규격(불변)."""

    register_key: str = "academic_prose"
    speech_level: str = "한다체"
    formality: int = 4          # 1 구어체 ↔ 5 문어체
    deference: int = 1          # 상급자 대상 겸양 표현 밀도
    warmth: int = 1             # 안부·공감 표현
    directness: int = 5         # 5 = 완충어 없이 곧바로, 1 = 완충어 매우 잦음
    target_sentences: int = 0   # 0 = 미지정(분량 감지기 length_target에 맡김)
    target_paragraphs: int = 0
    endings: Tuple[str, ...] = ()        # 종결어미 분포 지시
    emoji_policy: str = "금지"
    self_reference: str = "없음"          # 저 | 저희 | 없음
    address_form: str = ""                # 호칭 규칙(수신자가 있을 때만)
    greeting: str = ""                    # 인사 정형구
    closing: str = ""                     # 마무리 정형구
    banned: Tuple[str, ...] = ()          # 금지 표현
    signature: Tuple[str, ...] = ()       # 시그니처 표현


#: 병합·검증 대상 필드 이름(register_key 제외 — 그건 병합 결과가 아니라 좌표다).
TONE_FIELDS: Tuple[str, ...] = tuple(
    f.name for f in fields(ToneSpec) if f.name != "register_key")
_TUPLE_FIELDS = ("endings", "banned", "signature")
_INT_FIELDS = SCALE_FIELDS + ("target_sentences", "target_paragraphs")


# ── 레지스터 프리셋 ──────────────────────────────────────────────────
# 임의로 정하지 않았다. docs/personalization-audit.md §8 — 코드가 실제로 분기하는
# 두 축(understanding/task_type.py의 유형, context/assignment_router.py의 전략)과
# 거기서 유도되는 수신자에서 도출한 8종이다.
# 각 프리셋은 **그 레지스터가 제약하는 축만** 적는다(빈 축은 페르소나가 채운다).
REGISTER_PRESETS: Dict[str, Dict[str, Any]] = {
    # 수신자 없음 — 채점자가 읽는 문어체 산문. Until의 기본값.
    "academic_prose": {
        "speech_level": "한다체", "formality": 5, "deference": 1, "warmth": 1,
        "directness": 5, "emoji_policy": "금지", "self_reference": "없음",
        "endings": ("다", "이다", "한다"),
    },
    # 실험·실습 보고서 — 산문보다 더 건조하고 서술이 짧다.
    "lab_report": {
        "speech_level": "한다체", "formality": 5, "deference": 1, "warmth": 1,
        "directness": 5, "emoji_policy": "금지", "self_reference": "없음",
        "endings": ("다", "하였다", "측정되었다"),
    },
    # 참가·활동 보고서, 강의 소감 — 본인 경험을 존댓말로 서술한다.
    "reflective": {
        "speech_level": "하십시오체", "formality": 3, "deference": 2, "warmth": 3,
        "directness": 4, "emoji_policy": "금지", "self_reference": "저",
        "endings": ("습니다", "했습니다"),
    },
    # 교수에게 직접 가는 질의(weekly_inquiry) — 겸양·완충어가 실제로 필요한 자리.
    "inquiry_to_professor": {
        "speech_level": "하십시오체", "formality": 4, "deference": 5, "warmth": 3,
        "directness": 2, "emoji_policy": "금지", "self_reference": "저",
        "address_form": "교수님", "endings": ("습니다", "습니까"),
        "signature": ("여쭙고 싶습니다", "궁금합니다"),
    },
    # 활동보고서·신청서 양식 칸 — 짧고 사실 위주, 명사형 종결 혼용.
    "form_admin": {
        "speech_level": "하십시오체", "formality": 5, "deference": 2, "warmth": 1,
        "directness": 5, "emoji_policy": "금지", "self_reference": "저",
        "target_sentences": 3, "endings": ("함", "됨", "습니다"),
    },
    # 팀 과제의 공유 문서·역할 정리 — 동료가 읽는다.
    "team_coordination": {
        "speech_level": "해요체", "formality": 2, "deference": 2, "warmth": 4,
        "directness": 3, "emoji_policy": "최소", "self_reference": "저",
        "endings": ("해요", "할게요", "어요"),
    },
    # 발표 대본·슬라이드 — 말로 하는 글. 문장이 짧고 청중을 향한다.
    "presentation_script": {
        "speech_level": "혼합", "formality": 2, "deference": 2, "warmth": 3,
        "directness": 4, "emoji_policy": "금지", "self_reference": "저",
        "endings": ("해요", "습니다"),
    },
    # 코드·문제풀이·노트북 — 사람 수신자가 없다. 톤 축 대부분이 무의미하다.
    "technical_neutral": {
        "speech_level": "한다체", "formality": 5, "deference": 1, "warmth": 1,
        "directness": 5, "emoji_policy": "금지", "self_reference": "없음",
        "endings": ("다", "이다"),
    },
}

DEFAULT_REGISTER = "academic_prose"

#: 라우팅 전략 → 레지스터. 전략이 유형보다 구체적이라 먼저 본다.
_STRATEGY_TO_REGISTER: Dict[str, str] = {
    "weekly_inquiry": "inquiry_to_professor",
    "reflective_series": "reflective",
    "activity_form": "form_admin",
    "personal_upload": "form_admin",
    "team_project": "team_coordination",
    "presentation_conversion": "presentation_script",
    "evidence_report": "lab_report",
    "lab_report_cycle": "lab_report",
    "hdl_lab": "lab_report",
    "rmd_notebook": "technical_neutral",
    "code_project": "technical_neutral",
    "zip_project": "technical_neutral",
    "problem_set": "technical_neutral",
    "textbook_problem_set": "technical_neutral",
    "staged_writing": "academic_prose",
    "distributed_spec": "academic_prose",
    "spec_clarification": "academic_prose",
}

#: '형식 미확정' 성격의 일반 전략 — 라우터가 무엇을 내는지 확정하지 못했거나
#: 넓은 바구니로 잡은 경우다. 이 전략들만은 과제 유형 분류에 자리를 내준다.
_GENERIC_STRATEGIES = frozenset({
    "evidence_report", "staged_writing", "spec_clarification", "distributed_spec",
})

#: 유형 분류기가 **의도적으로 구분해 둔** 라벨들. task_type.py의 `_WEIGHTS`가
#: 가중치까지 주며 지켜 낸 분류라, 일반 전략이 이걸 덮으면 그 보정이 통째로
#: 무효가 된다(실측: CO-Week 참가결과보고서가 evidence_report로 잡혀 실험
#: 보고서 톤이 되던 회귀 — run_tone_ab.py가 잡았다).
_SPECIFIC_TASK_TYPES = frozenset({"reflective_report", "inquiry"})

#: 과제 유형 → 레지스터(전략이 못 잡았을 때의 2차 폴백).
_TASK_TYPE_TO_REGISTER: Dict[str, str] = {
    "essay": "academic_prose",
    "report": "lab_report",
    "reflective_report": "reflective",
    "inquiry": "inquiry_to_professor",
    "problemset": "technical_neutral",
    "code": "technical_neutral",
    "presentation": "presentation_script",
    "hdl_lab": "lab_report",
    "general": "academic_prose",
}


# ── 저장 모델 ────────────────────────────────────────────────────────

@dataclass
class RegisterOverride:
    """한 레지스터에 얹는 델타. `pinned`면 사용자가 직접 못박은 값이다."""
    delta: Dict[str, Any] = field(default_factory=dict)
    pinned: bool = False


@dataclass
class PersonaBase:
    """사용자 1명의 기준선. 레지스터별 복제 없이 이것 하나만 존재한다."""
    actor_id: str = "local"
    defaults: Dict[str, Any] = field(default_factory=dict)
    #: 기준선의 출처 — default | voice_profile | user. provenance 표시·디버깅용.
    source: str = "default"


@dataclass
class PersonaStore:
    base: PersonaBase = field(default_factory=PersonaBase)
    registers: Dict[str, RegisterOverride] = field(default_factory=dict)
    #: 사용자가 UI에서 "항상 이 레지스터로"라고 정한 값(자동 추론을 이긴다).
    pinned_register: str = ""
    #: L1 스타일 카드(style_card.StyleCard) — 기준선이 비어 있을 때 그 자리를 채운다.
    #: 순환 import를 피하려고 여기서는 타입을 강제하지 않는다(dict 또는 StyleCard).
    style_card: Any = None
    updated_at: str = ""

    def base_defaults(self) -> Dict[str, Any]:
        """기준선으로 쓸 필드 — 사용자가 직접 정한 값 > L1 스타일 카드.

        사용자가 설정에서 못박은 값이 학습 결과를 이겨야 한다(학습이 사용자 설정을
        조용히 덮으면 '내가 바꿨는데 왜 돌아오지' 문제가 된다).
        """
        if self.base.defaults:
            return dict(self.base.defaults)
        card = self.style_card
        fields_ = getattr(card, "fields", None)
        if isinstance(fields_, dict) and fields_:
            return sanitize_delta(fields_)
        return {}


# ── 값 검증 (손상·조작 입력을 조용히 중립화) ─────────────────────────

def _clamp_scale(value: Any, fallback: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return fallback
    return 1 if n < 1 else 5 if n > 5 else n


def _clamp_count(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return 0 if n < 0 else 200 if n > 200 else n


def _clean_strings(value: Any, cap: int = 12) -> Tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return ()
    out: List[str] = []
    for item in value:
        text = " ".join(str(item).split())[:40]
        if text and text not in out:
            out.append(text)
    return tuple(out[:cap])


def sanitize_delta(raw: Any) -> Dict[str, Any]:
    """임의 dict를 ToneSpec 필드만 남긴 안전한 델타로. 알 수 없는 키는 버린다."""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    for key, value in raw.items():
        if key not in TONE_FIELDS:
            continue
        if key in _TUPLE_FIELDS:
            cleaned = _clean_strings(value)
            if cleaned:
                out[key] = list(cleaned)
        elif key in SCALE_FIELDS:
            out[key] = _clamp_scale(value, 3)
        elif key in ("target_sentences", "target_paragraphs"):
            out[key] = _clamp_count(value)
        elif key == "speech_level":
            if value in SPEECH_LEVELS:
                out[key] = value
        elif key == "emoji_policy":
            if value in EMOJI_POLICIES:
                out[key] = value
        elif key == "self_reference":
            if value in SELF_REFERENCES:
                out[key] = value
        else:  # address_form / greeting / closing — 자유 문자열(길이만 제한)
            text = " ".join(str(value).split())[:60]
            if text:
                out[key] = text
    return out


# ── 병합 ─────────────────────────────────────────────────────────────

def resolve_tone_spec(register_key: str, *,
                      base: Optional[PersonaBase] = None,
                      override: Optional[RegisterOverride] = None) -> ToneSpec:
    """NEUTRAL → base → preset → override 순으로 병합한 최종 ToneSpec.

    프리셋이 base보다 뒤인 이유는 모듈 docstring 참조(레지스터의 요구가 개인
    기준선을 이겨야 하는 축이 있다). override는 사용자가 못박은 값이라 언제나 최종.
    """
    key = register_key if register_key in REGISTER_PRESETS else DEFAULT_REGISTER
    merged: Dict[str, Any] = {}
    merged.update(sanitize_delta(base.defaults if base else {}))
    merged.update(sanitize_delta(REGISTER_PRESETS[key]))
    merged.update(sanitize_delta(override.delta if override else {}))
    spec = ToneSpec(register_key=key)
    for name, value in merged.items():
        if name in _TUPLE_FIELDS:
            value = tuple(value)
        spec = replace(spec, **{name: value})
    return spec


def resolve_register_key(spec: Optional[dict] = None, route: object = None, *,
                         explicit: str = "") -> Tuple[str, str]:
    """(register_key, source) — source ∈ explicit | inferred | default.

    **명시 지정과 자동 추론은 여기서 갈린다.** explicit이 유효하면 무조건 이긴다.
    자동 추론은 전략 → 유형 순으로 보되, **일반 전략 vs 구체 유형**은 예외로
    유형이 이긴다(`_GENERIC_STRATEGIES` / `_SPECIFIC_TASK_TYPES` 주석 참조).
    둘 다 못 잡으면 기본 프리셋으로 폴백한다 — 조합 폭발을 막는 방식이라
    미정의 조합에 새 프리셋을 만들지 않고 가장 가까운 것으로 떨어뜨린다.
    """
    if explicit and explicit in REGISTER_PRESETS:
        return explicit, "explicit"
    strategy = str(getattr(route, "strategy", "") or "")
    task_type = str((spec or {}).get("task_type") or "")
    specific_type = (task_type in _SPECIFIC_TASK_TYPES
                     and task_type in _TASK_TYPE_TO_REGISTER)
    if specific_type and strategy in _GENERIC_STRATEGIES:
        return _TASK_TYPE_TO_REGISTER[task_type], "inferred"
    if strategy in _STRATEGY_TO_REGISTER:
        return _STRATEGY_TO_REGISTER[strategy], "inferred"
    if task_type in _TASK_TYPE_TO_REGISTER:
        return _TASK_TYPE_TO_REGISTER[task_type], "inferred"
    return DEFAULT_REGISTER, "default"


# ── VoiceProfile → 페르소나 기준선 (기존 개념을 흡수, 대체하지 않는다) ──

_ENDING_TO_SPEECH_LEVEL = {
    "합니다체": "하십시오체",
    "해요체": "해요체",
    "한다체": "한다체",
    "혼합": "혼합",
}


def base_from_voice(voice: object, actor_id: str = "local") -> PersonaBase:
    """VoiceProfile에서 페르소나 기준선을 만든다. 표본 0이면 빈 기준선.

    VoiceProfile은 버리지 않는다 — ToneSpec의 **입력 소스 하나**로 흡수한다.
    통계로 알 수 있는 축(speech_level·formality·emoji)만 채우고, 통계로 알 수 없는
    축(deference·warmth·directness)은 비워 프리셋·사용자 설정이 채우게 둔다.
    """
    if voice is None or int(getattr(voice, "n_samples", 0) or 0) <= 0:
        return PersonaBase(actor_id=actor_id)
    defaults: Dict[str, Any] = {}
    level = _ENDING_TO_SPEECH_LEVEL.get(str(getattr(voice, "ending_style", "")))
    if level:
        defaults["speech_level"] = level
    # 평균 문장 길이 — 긴 문장은 문어체 쪽 신호. 경계값은 한국어 글쓰기 통설이
    # 아니라 이 리포의 실측 코퍼스에 맞춘 보수적 3구간(감으로 더 쪼개지 말 것).
    avg = int(getattr(voice, "avg_sentence_len", 0) or 0)
    if avg:
        defaults["formality"] = 2 if avg < 30 else (3 if avg < 60 else 4)
    if getattr(voice, "uses_emoji", False):
        defaults["emoji_policy"] = "최소"
    return PersonaBase(actor_id=actor_id, defaults=defaults, source="voice_profile")


# ── 결정적 직렬화 (이 모듈의 핵심 계약) ──────────────────────────────

_FORMALITY_WORDS = ("완전한 구어체로", "구어체에 가깝게", "구어와 문어의 중간으로",
                    "문어체에 가깝게", "완전한 문어체로")
_DEFERENCE_WORDS = (
    "겸양 표현을 쓰지 않는다",
    "꼭 필요한 자리에만 최소한으로 쓴다",
    "보통 수준으로 쓴다",
    "'~드립니다', '~주시면 감사하겠습니다' 같은 표현을 자주 쓴다",
    "'~드립니다', '~주시면 감사하겠습니다', '~여쭙고자 합니다'를 적극적으로 쓴다")
_WARMTH_WORDS = (
    "안부·공감 표현 없이 용건만 쓴다",
    "안부는 생략하고 필요한 곳에만 짧게 공감한다",
    "간단한 안부나 감사 한 문장을 넣는다",
    "안부·감사·공감을 문단마다 자연스럽게 섞는다",
    "안부와 공감을 앞뒤로 충분히 넣는다")
_DIRECTNESS_WORDS = (
    "'혹시', '괜찮으시다면', '다름이 아니라' 같은 완충어를 매우 자주 쓴다",
    "완충어를 자주 써서 요청을 부드럽게 감싼다",
    "완충어를 필요한 곳에만 쓴다",
    "완충어를 거의 쓰지 않고 요지를 앞세운다",
    "완충어 없이 곧바로 요지를 쓴다")
_SPEECH_LEVEL_WORDS = {
    "하십시오체": "하십시오체 — 종결은 '~습니다/~습니까'를 기본으로 한다.",
    "해요체": "해요체 — 종결은 '~해요/~어요'를 기본으로 한다.",
    "혼합": ("혼합 — 기본은 해요체로 쓰되, 핵심 주장·요청 문장만 "
             "하십시오체('~습니다')로 맺는다."),
    "한다체": ("한다체 — 수신자에게 말 거는 글이 아니다. 종결은 '~다/~이다/~한다'로 "
               "하고 존댓말을 쓰지 않는다."),
}
_EMOJI_WORDS = {
    "금지": "쓰지 않는다(느낌표도 최소).",
    "최소": "꼭 필요한 한두 곳에만.",
    "허용": "자연스러운 범위에서 사용 가능.",
}
_SELF_REF_WORDS = {
    "저": "'저'로 쓴다('나' 금지).",
    "저희": "'저희'로 쓴다(팀·소속을 대표하는 글).",
    "없음": "1인칭 자기지칭을 쓰지 않는다.",
}

TONE_BLOCK_HEADER = "【톤 레지스터 — 이 과제의 말투 규격】"


def render_tone_spec(tone: ToneSpec) -> str:
    """ToneSpec → 프롬프트 주입 문자열. **결정적**(같은 입력 → 같은 바이트).

    필드 순서·표기를 여기서 한 번만 고정한다. pipeline이 문자열을 이어 붙이던
    기존 방식과 달리, 이 함수를 지나야만 톤이 프롬프트에 들어간다 —
    그래야 프롬프트 버전 해시가 의미를 갖고 A/B가 성립한다.
    """
    lines: List[str] = [
        f"{TONE_BLOCK_HEADER} (register: {tone.register_key})",
        f"- 상대높임: {_SPEECH_LEVEL_WORDS[tone.speech_level]}",
        f"- 문체 격식: {tone.formality}/5 — {_FORMALITY_WORDS[tone.formality - 1]} 쓴다.",
        f"- 겸양 밀도: {tone.deference}/5 — {_DEFERENCE_WORDS[tone.deference - 1]}.",
        f"- 온도: {tone.warmth}/5 — {_WARMTH_WORDS[tone.warmth - 1]}.",
        f"- 직설성: {tone.directness}/5 — {_DIRECTNESS_WORDS[tone.directness - 1]}.",
    ]
    if tone.target_sentences or tone.target_paragraphs:
        parts = []
        if tone.target_paragraphs:
            parts.append(f"{tone.target_paragraphs}단락")
        if tone.target_sentences:
            parts.append(f"항목·단락당 {tone.target_sentences}문장 내외")
        lines.append("- 분량 감각: " + " · ".join(parts)
                     + " (과제 명세의 분량 요건이 있으면 그쪽이 우선한다).")
    if tone.endings:
        lines.append(f"- 종결어미: {', '.join(tone.endings)}를 주로 쓴다.")
    lines.append(f"- 이모지: {_EMOJI_WORDS[tone.emoji_policy]}")
    lines.append(f"- 자기지칭: {_SELF_REF_WORDS[tone.self_reference]}")
    if tone.address_form:
        lines.append(f"- 호칭: 상대를 '{tone.address_form}'으로 부른다.")
    if tone.greeting:
        lines.append(f"- 첫인사: \"{tone.greeting}\" 형태로 연다.")
    if tone.closing:
        lines.append(f"- 마무리: \"{tone.closing}\" 형태로 맺는다.")
    if tone.banned:
        lines.append(f"- 금지 표현(절대 쓰지 말 것): {', '.join(tone.banned)}.")
    if tone.signature:
        lines.append(f"- 즐겨 쓰는 표현(어울리는 자리에만): {', '.join(tone.signature)}.")
    lines.append(
        "- 이 규격은 **문체만** 규정한다. 경계선 규칙(사람의 판단은 [[DECISION]]으로 "
        "남긴다)과 근거 규칙(자료에 없는 사실 금지)이 언제나 우선한다. 톤을 맞추려고 "
        "없는 내용을 지어내지 말 것.")
    return "\n".join(lines)


def tone_fingerprint(tone: ToneSpec) -> str:
    """ToneSpec의 안정적 지문(12자리) — 이벤트 로그·A/B 비교의 좌표."""
    payload = json.dumps(
        {name: list(getattr(tone, name)) if name in _TUPLE_FIELDS else getattr(tone, name)
         for name in ("register_key",) + TONE_FIELDS},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


# ── 저장/로드 (voice_autolearn.py와 같은 계보) ───────────────────────

_TL_PATH = _threading.local()


def set_persona_path_override(p: Optional[Path]) -> None:
    """이 스레드(요청)의 페르소나 경로 오버라이드 — 클라우드 사용자별 경로."""
    _TL_PATH.value = p


def _resolve_path(path: Optional[Path]) -> Path:
    if path is not None:
        return Path(path)
    o = getattr(_TL_PATH, "value", None)
    return Path(o) if o is not None else PERSONA_PATH


def persona_path() -> Path:
    return _resolve_path(None)


def load_persona(path: Optional[Path] = None) -> PersonaStore:
    """저장된 페르소나. 파일 없음·손상·미래 버전은 전부 빈 스토어(비치명적)."""
    p = _resolve_path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return PersonaStore()
    if not isinstance(raw, dict) or raw.get("v") != STORE_VERSION:
        return PersonaStore()
    base_raw = raw.get("base") if isinstance(raw.get("base"), dict) else {}
    base = PersonaBase(
        actor_id=str(base_raw.get("actor_id") or "local")[:64],
        defaults=sanitize_delta(base_raw.get("defaults")),
        source=str(base_raw.get("source") or "default")[:32],
    )
    registers: Dict[str, RegisterOverride] = {}
    for key, item in (raw.get("registers") or {}).items():
        if key not in REGISTER_PRESETS or not isinstance(item, dict):
            continue
        delta = sanitize_delta(item.get("delta"))
        if not delta and not item.get("pinned"):
            continue
        registers[key] = RegisterOverride(delta=delta, pinned=bool(item.get("pinned")))
    pinned = raw.get("pinned_register")
    card = None
    if raw.get("style_card") is not None:
        from .style_card import StyleCard          # 지연 import — 순환 방지
        card = StyleCard.from_dict(raw.get("style_card"))
        if card.is_empty() and not card.notes:
            card = None
    return PersonaStore(
        base=base, registers=registers,
        pinned_register=pinned if pinned in REGISTER_PRESETS else "",
        style_card=card,
        updated_at=str(raw.get("updated_at") or "")[:32],
    )


def save_persona(store: PersonaStore, path: Optional[Path] = None) -> Path:
    """원자적 저장(profile.py와 같은 path_lock + atomic_write_json)."""
    p = _resolve_path(path)
    payload = {
        "v": STORE_VERSION,
        "base": {"actor_id": store.base.actor_id,
                 "defaults": sanitize_delta(store.base.defaults),
                 "source": store.base.source},
        "registers": {k: {"delta": sanitize_delta(v.delta), "pinned": bool(v.pinned)}
                      for k, v in sorted(store.registers.items())},
        "pinned_register": store.pinned_register,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    card = store.style_card
    if card is not None and hasattr(card, "to_dict"):
        payload["style_card"] = card.to_dict()
    p.parent.mkdir(parents=True, exist_ok=True)
    with atomicio.path_lock(p):
        atomicio.atomic_write_json(p, payload, indent=1)
    return p


def clear_persona(path: Optional[Path] = None) -> None:
    """페르소나 삭제 — 다음 실행부터 프리셋 기본값으로 돌아간다."""
    try:
        _resolve_path(path).unlink()
    except OSError:
        pass


# ── 파이프라인 진입점 ────────────────────────────────────────────────

@dataclass(frozen=True)
class ToneResolution:
    """이번 실행에 확정된 톤 — 파이프라인이 이것만 들고 다닌다."""
    tone: ToneSpec
    register_key: str
    source: str        # explicit | inferred | default
    block: str         # 프롬프트 주입 문자열
    fingerprint: str


def resolve_tone(spec: Optional[dict] = None, route: object = None, *,
                 voice: object = None, explicit: str = "",
                 path: Optional[Path] = None) -> ToneResolution:
    """spec·route·VoiceProfile·저장된 페르소나에서 이번 실행의 톤을 확정한다.

    우선순위: 저장된 pinned_register/명시 인자 > 라우팅 전략 > 과제 유형 > 기본.
    어떤 단계가 실패해도 예외를 내지 않는다 — 최악의 경우 중립 기본 프리셋이다.
    """
    try:
        store = load_persona(path)
    except Exception:
        store = PersonaStore()
    key, source = resolve_register_key(
        spec, route, explicit=explicit or store.pinned_register)
    # 기준선 우선순위: 사용자 설정 > L1 스타일 카드 > 이번 실행의 VoiceProfile.
    defaults = store.base_defaults()
    if defaults:
        base = PersonaBase(actor_id=store.base.actor_id, defaults=defaults,
                           source=store.base.source if store.base.defaults
                           else "style_card")
    else:
        # 저장된 것이 아무것도 없으면 이번 실행의 VoiceProfile에서 즉석으로 만든다
        # (기존 사용자가 설정 화면을 거치지 않아도 톤이 개인화되게).
        base = base_from_voice(voice, actor_id=store.base.actor_id)
    tone = resolve_tone_spec(key, base=base, override=store.registers.get(key))
    return ToneResolution(tone=tone, register_key=key, source=source,
                          block=render_tone_spec(tone),
                          fingerprint=tone_fingerprint(tone))


# ── 명시 지정 진입점 (자동 추론과 분리된 사용자 경로) ────────────────

def describe(path: Optional[Path] = None) -> str:
    """현재 페르소나 상태 한 줄 요약 — 무엇이 적용 중인지 투명하게 보여준다."""
    store = load_persona(path)
    pinned = store.pinned_register or "(자동 추론)"
    overrides = ", ".join(sorted(store.registers)) or "없음"
    base = ", ".join(f"{k}={v}" for k, v in sorted(store.base.defaults.items())) or "없음"
    return (f"고정 레지스터: {pinned} · 기준선({store.base.source}): {base} · "
            f"레지스터별 조정: {overrides}")


def main(argv: Optional[list] = None) -> int:
    """`python -m until.context.tone` — 보기 / 명시 지정 / 해제.

        python -m until.context.tone                          # 현재 상태 + 프리셋 목록
        python -m until.context.tone --pin inquiry_to_professor
        python -m until.context.tone --unpin
        python -m until.context.tone --set reflective warmth=4 deference=3
        python -m until.context.tone --show reflective        # 확정 규격 미리보기
        python -m until.context.tone --clear                  # 페르소나 삭제

    웹 UI가 붙기 전까지의 명시 지정 경로다. 자동 추론(resolve_register_key)과 완전히
    분리돼 있고, 여기서 지정한 값은 언제나 추론을 이긴다.
    """
    import sys
    args = list(argv if argv is not None else sys.argv[1:])

    if args and args[0] == "--clear":
        clear_persona()
        print(f"삭제됨: {persona_path()}")
        return 0
    if args and args[0] == "--show":
        key = args[1] if len(args) > 1 else DEFAULT_REGISTER
        if key not in REGISTER_PRESETS:
            print(f"알 수 없는 레지스터: {key}")
            return 2
        store = load_persona()
        print(render_tone_spec(resolve_tone_spec(
            key, base=store.base, override=store.registers.get(key))))
        return 0
    if args and args[0] in ("--pin", "--unpin", "--set"):
        store = load_persona()
        if args[0] == "--unpin":
            store.pinned_register = ""
        elif args[0] == "--pin":
            key = args[1] if len(args) > 1 else ""
            if key not in REGISTER_PRESETS:
                print(f"알 수 없는 레지스터: {key or '(없음)'}")
                return 2
            store.pinned_register = key
        else:  # --set <register> k=v ...
            key = args[1] if len(args) > 1 else ""
            if key not in REGISTER_PRESETS:
                print(f"알 수 없는 레지스터: {key or '(없음)'}")
                return 2
            delta = dict(store.registers.get(key, RegisterOverride()).delta)
            for pair in args[2:]:
                if "=" not in pair:
                    continue
                field_name, _, value = pair.partition("=")
                delta[field_name.strip()] = value.strip()
            clean = sanitize_delta(delta)
            dropped = sorted(set(delta) - set(clean))
            if dropped:
                print(f"무시된 값(필드·범위·열거형 밖): {', '.join(dropped)}")
            store.registers[key] = RegisterOverride(delta=clean, pinned=True)
        save_persona(store)
        print(f"저장됨: {persona_path()}")

    print(describe())
    print(f"프리셋: {', '.join(REGISTER_PRESETS)}")
    print(f"조정 가능한 필드: {', '.join(TONE_FIELDS)}")
    import os as _os
    if _os.getenv("UNTIL_TONE_REGISTER", "0") != "1":
        print("(주의: UNTIL_TONE_REGISTER=1 이어야 실제 생성에 적용됩니다)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
