"""
prompt_version / model_version — 나중에 원인을 가릴 수 있게 하는 최소 장치.

이게 없으면 3주 뒤 "톤이 왜 바뀌었지?"에 답할 방법이 없다. 모델이 바뀐 건지
(라이브 운영은 Cerebras → Kimi → Gemini → Groq 폴백 사슬이라 같은 요청도 다른
모델이 답할 수 있다), 프롬프트를 우리가 고친 건지 구분할 수 없기 때문이다.
그래서 **두 축을 따로** 남긴다.

  · prompt_version — 사람이 올리는 SemVer(의도된 변경의 이름표) +
    실제 조립된 시스템 프롬프트의 지문(의도치 않은 변경까지 잡는 실측값).
    둘 다 필요하다. 버전만 있으면 "안 올리고 고친" 변경을 놓치고,
    지문만 있으면 무엇이 왜 바뀌었는지 사람이 읽을 수 없다.
  · model_version — 설정값이 아니라 **응답한 모델**. `LLMResult.model`을
    `llm/meter.py`가 `Result.llm_usage["models"]`에 순서대로 모아 둔다.

모르면 빈 문자열이다. **지어내지 않는다** — 틀린 출처 기록은 없는 것보다 나쁘다.
"""
from __future__ import annotations

import hashlib
from typing import Any, List, Optional, Sequence

#: 프롬프트 계약의 사람용 버전. Execution 프롬프트·톤 직렬화 형식이 의미 있게
#: 바뀔 때 손으로 올린다(자동 증가 금지 — 올리는 행위가 곧 "의도한 변경"의 선언이다).
PROMPT_VERSION = "1.2.0"


def prompt_fingerprint(*parts: str) -> str:
    """조립된 프롬프트 조각들의 안정적 지문(12자리 hex).

    조각을 구분자로 이어 붙여 해싱한다 — 이어 붙이기만 하면 ("ab","c")와
    ("a","bc")가 같은 지문이 되어 서로 다른 조립을 같다고 보고한다.
    """
    h = hashlib.sha256()
    for part in parts:
        h.update(str(part or "").encode("utf-8"))
        h.update(b"\x1f")          # unit separator — 경계를 해시에 포함
    return h.hexdigest()[:12]


def model_fingerprint(model_version: str) -> str:
    """모델 식별자의 지문(12자리 hex) — 비식별 신호 파이프용.

    텔레메트리에는 자유 문자열을 실을 수 없다(`telemetry/schema.py`가 fail-closed로
    차단한다). 모델명은 제공자·티어가 드러나는 자유 문자열이라 열거형에 등재하는
    대신 지문으로 보낸다. 같은 모델이면 같은 지문이라 추세 비교는 그대로 된다.
    """
    text = str(model_version or "").strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12] if text else ""


def _models_from_usage(usage: Any) -> List[str]:
    if not isinstance(usage, dict):
        return []
    models = usage.get("models")
    if isinstance(models, list):
        return [str(m) for m in models if str(m).strip()]
    single = usage.get("model_version")
    return [str(single)] if str(single or "").strip() else []


def resolve_model_version(result: Any = None, *, usage: Any = None,
                          config: Any = None) -> str:
    """이번 실행에서 실제로 응답한 모델. 모르면 빈 문자열.

    여러 모델이 관여했으면(폴백 발동) `"a+b"`로 이어 붙여 **그 사실 자체를** 남긴다.
    하나로 뭉개면 폴백이 일어난 실행을 나중에 구분할 수 없다.
    설정값(`Config.model`)은 마지막 폴백이며, 그마저 없으면 빈 문자열이다.
    """
    models = _models_from_usage(usage if usage is not None
                                else getattr(result, "llm_usage", None))
    if models:
        return "+".join(models)
    configured = str(getattr(config, "model", "") or "").strip()
    return configured


def resolve_prompt_version(*parts: str) -> str:
    """`"1.0.0+<지문>"` 형태의 프롬프트 버전. 조각이 없으면 버전만.

    SemVer 뒤의 `+build` 표기라 `telemetry/schema.py`의 버전 정규식을 그대로 통과한다
    (자유 문자열 차단에 걸리지 않는다).
    """
    usable = [p for p in parts if str(p or "").strip()]
    if not usable:
        return PROMPT_VERSION
    return f"{PROMPT_VERSION}+{prompt_fingerprint(*usable)}"


def describe(result: Any = None, config: Any = None) -> str:
    """CLI·리포트용 한 줄 — 이 산출물이 어떤 프롬프트·모델로 나왔는지."""
    model = resolve_model_version(result, config=config) or "(모름)"
    version = str(getattr(result, "prompt_version", "") or PROMPT_VERSION)
    return f"prompt {version} · model {model}"


def used_fallback(result: Any = None, *, usage: Any = None) -> bool:
    """폴백 사슬이 실제로 발동했는가(모델이 둘 이상 관여) — 원가·품질 분석용."""
    return len(_models_from_usage(
        usage if usage is not None else getattr(result, "llm_usage", None))) > 1


#: 프롬프트 표면 — 여기 있는 것이 바뀌면 산출물의 톤·구조가 바뀐다.
#: 새 프롬프트 블록을 만들면 **여기에 등록해야** 버전 규율이 그것도 지킨다.
def prompt_surface_fingerprints() -> dict:
    """프롬프트에 실제로 들어가는 문자열들의 지문(결정적).

    `tools/check_prompt_version.py`가 이 값을 기준선과 대조해, 프롬프트를 고치고도
    `PROMPT_VERSION`을 안 올린 커밋을 잡는다. 버전을 손으로 올리게 두는 이상
    사람은 반드시 잊는다 — 잊었을 때 기계가 알려주는 것이 이 함수의 존재 이유다.

    지연 import를 쓰는 이유: 이 모듈은 이벤트 로깅 경로에서도 불리는데, 그때마다
    execution/prompts와 context/tone을 끌고 올 이유가 없다.
    """
    from ..context.episodes import Episode, EpisodeHit, episodes_block
    from ..context.facts import facts_block, make_fact
    from ..context.tone import REGISTER_PRESETS, render_tone_spec, resolve_tone_spec
    from ..execution import prompts as ep

    out = {"PROMPT_VERSION": PROMPT_VERSION}
    out["SYSTEM"] = prompt_fingerprint(ep.SYSTEM)
    out["FINALIZE_SYSTEM"] = prompt_fingerprint(ep.FINALIZE_SYSTEM)
    out["FEWSHOT"] = prompt_fingerprint(ep.FEWSHOT)
    out["TYPE_GUIDANCE"] = prompt_fingerprint(
        *[f"{k}={v}" for k, v in sorted(ep.TYPE_GUIDANCE.items())])
    out["user_message"] = prompt_fingerprint(ep.user_message("{}", "(자료)"))
    out["finalize_user_message"] = prompt_fingerprint(
        ep.finalize_user_message("{}", "본문", "결정", "(자료)", kept_block="[[X]]"))
    out["reask_message"] = prompt_fingerprint(ep.reask_message("초안", ["오류"]))
    for key in sorted(REGISTER_PRESETS):
        out[f"tone:{key}"] = prompt_fingerprint(
            render_tone_spec(resolve_tone_spec(key)))
    out["episodes_block"] = prompt_fingerprint(episodes_block([EpisodeHit(
        episode=Episode(episode_id="x", input_context="상황",
                        generated_draft="초안", final_output="최종"),
        score=1.0)]))
    out["facts_block"] = prompt_fingerprint(facts_block(
        facts=[make_fact("사안", "주제", "확정됨")]))
    return out


def normalize_models(models: Optional[Sequence[str]]) -> List[str]:
    """중복 제거 + 순서 보존. 저장·비교 전 정규화 지점을 한 곳으로 모은다."""
    out: List[str] = []
    for m in models or ():
        text = str(m or "").strip()
        if text and text not in out:
            out.append(text)
    return out
