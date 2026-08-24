"""
무료/로컬 백엔드 — OpenAI 호환 엔드포인트.

이 하나로 다음을 전부 커버한다(전부 무료 또는 무료 티어):
  - Ollama (로컬, 완전 무료):   base_url=http://localhost:11434/v1, key="ollama"
  - Cerebras 무료 티어:          base_url=https://api.cerebras.ai/v1 (gpt-oss-120b, 1M tok/일)
  - Groq 무료 티어:             base_url=https://api.groq.com/openai/v1
  - NVIDIA NIM(Kimi 등):        base_url=https://integrate.api.nvidia.com/v1 (moonshotai/kimi-k2-instruct)
  - OpenRouter 무료 모델:        base_url=https://openrouter.ai/api/v1 (모델명에 :free)
  - Gemini OpenAI 호환:          base_url=https://generativelanguage.googleapis.com/v1beta/openai

여러 제공자를 백업으로 잇는 법(주=Cerebras → NVIDIA/Kimi → Gemini → Groq):
  UNTIL_BASE_URL/UNTIL_API_KEY/UNTIL_MODEL          = 주 제공자
  UNTIL_BASE_URL_2/UNTIL_API_KEY_2/UNTIL_MODEL_2    = 1차 백업
  UNTIL_BASE_URL_3/…_3, UNTIL_BASE_URL_4/…_4 …      = 그 다음 백업(최대 _9)
주 제공자를 '더 못 쓰게 되면'(429 한도, 402 결제, 잔액·쿼터 소진) 번호 순서대로
자동 강등한다. 키 없는 슬롯은 건너뛴다. 판정은 `_should_degrade` 하나에 모여 있고,
네트워크 오류·잘못된 요청·5xx는 강등하지 않고 즉시 표면화한다(정직이 먼저).
자세한 무료 제공자·한도 목록: https://github.com/cheahjs/free-llm-api-resources

Anthropic 전용 기능(citations/prompt caching)은 여기서 graceful degrade —
documents는 프롬프트에 인라인되고, citations는 빈 리스트로 반환된다.
"""
from __future__ import annotations
import logging
import os
from typing import List, Optional

from .base import LLMResult, SourceDoc

_LOG = logging.getLogger(__name__)


def build_messages(system: str, user: str, documents: Optional[List[SourceDoc]]) -> list:
    """documents를 user 프롬프트 앞에 **번호와 함께** 인라인. SDK 없이 테스트 가능한 순수 함수.

    번호([자료1], [자료2]...)는 모델이 근거 인용을 `[자료N]`으로 달 수 있게 하기 위함이며,
    UI의 '근거 자료' 범례와 같은 1-기반 순서를 공유한다."""
    parts = []
    for i, d in enumerate(documents or [], 1):
        parts.append(f"[자료{i}: {d.title}]\n{d.text}")
    parts.append(user)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def fallback_models(base_url: str, primary: str, env_val: "str | None") -> list:
    """한도 소진(429) 시 갈아탈 모델 사슬 — env(UNTIL_MODEL_FALLBACK, 쉼표 복수) 우선.

    제공자별 기본 사슬(무료 일일 한도가 큰 순서로 강등):
      Groq     → llama-3.3-70b-versatile → llama-3.1-8b-instant
      Cerebras → gpt-oss-120b (zai-glm-4.7 등 프리뷰 소진 시 프로덕션 모델로)
      NVIDIA   → moonshotai/kimi-k2-instruct → meta/llama-3.3-70b → meta/llama-3.1-8b
    주 모델과 같은 항목은 건너뛴다(재시도 의미 없음)."""
    raw = (env_val or "").strip()
    if raw:
        chain = [m.strip() for m in raw.split(",") if m.strip()]
    elif "groq.com" in (base_url or ""):
        chain = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
    elif "cerebras.ai" in (base_url or ""):
        chain = ["gpt-oss-120b"]
    elif "nvidia.com" in (base_url or ""):
        # NVIDIA NIM(무료 개발자 티어) — Kimi 우선, 소진 시 NVIDIA 호스팅 Llama로 강등.
        chain = ["moonshotai/kimi-k2-instruct",
                 "meta/llama-3.3-70b-instruct", "meta/llama-3.1-8b-instruct"]
    else:
        chain = []
    out, seen = [], {primary}
    for m in chain:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def fallback_model(base_url: str, primary: str, env_val: "str | None") -> "str | None":
    """(하위호환) 첫 폴백 모델 하나 — fallback_models의 머리."""
    chain = fallback_models(base_url, primary, env_val)
    return chain[0] if chain else None


# 강등 사유 — 열거형으로 고정한다(로그·계측에 자유 문자열을 흘리지 않는다).
DEGRADE_RATE_LIMIT = "rate_limit"          # 429 — 잠시 뒤엔 다시 될 수도 있다
DEGRADE_PAYMENT = "payment_required"       # 402 — 결제가 막혔다
DEGRADE_EXHAUSTED = "quota_exhausted"      # 잔액·쿼터 소진(코드는 제각각)
DEGRADE_AMBIGUOUS = "auth_or_quota"        # 401/403 — 키 오설정과 구분 불가

# "이 제공자로는 더 못 쓴다"를 뜻하는 메시지 조각. 제공자마다 코드가 달라서
# (Cerebras 402, 일부는 401/403 + insufficient_quota) 문자열도 함께 본다.
_EXHAUSTED_MARKERS = (
    "insufficient_quota", "insufficient quota",
    "insufficient_credits", "insufficient credits",
    "insufficient balance", "insufficient_balance",
    "out of credits", "no credits", "credit balance",
    "payment required", "payment_required",
    "quota exceeded", "quota_exceeded", "exceeded your current quota",
    "billing", "past due", "subscription expired",
)
_RATE_MARKERS = ("rate limit", "rate_limit", "too many requests", "429")


def _should_degrade(e: Exception) -> "tuple[bool, str]":
    """다음 제공자로 강등할지와 그 사유.

    원칙은 그대로다 — **무중단보다 정직이 먼저**. 네트워크 오류·잘못된 요청·
    서버 5xx는 강등하지 않고 즉시 표면화한다. 강등 대상은 "이 제공자로는 더
    못 쓴다"에 해당하는 것뿐이다.

    왜 429만으로는 부족한가: 무료 티어에서는 한도 초과가 429로 왔지만, 유료로
    전환하면 잔액 0이 402 Payment Required나 401/403 + insufficient_quota로
    온다. 그래서 유료 전환이 폴백을 통째로 무력화했다(2026-08-20 확인).

    401/403은 키 오설정과 잔액 소진이 같은 코드로 오는 제공자가 있어 구분이
    불가능하다. 이때는 강등하되 사유를 `auth_or_quota`로 남겨, 운영자가 로그에서
    "키가 틀린 것일 수도 있다"를 볼 수 있게 한다.
    """
    status = getattr(e, "status_code", None)
    if status is None:
        status = getattr(getattr(e, "response", None), "status_code", None)
    msg = str(e).lower()

    if status == 429:
        return True, DEGRADE_RATE_LIMIT
    if status == 402:
        return True, DEGRADE_PAYMENT
    if any(m in msg for m in _EXHAUSTED_MARKERS):
        return True, DEGRADE_PAYMENT if status == 402 else DEGRADE_EXHAUSTED
    if any(m in msg for m in _RATE_MARKERS):
        return True, DEGRADE_RATE_LIMIT
    if status in (401, 403):
        # 마커가 없으면 키 오설정일 가능성이 크다. 그래도 서비스를 세우지 않되,
        # 조용히 넘어가지 않는다 — 사유를 남겨 운영자가 판단하게 한다.
        return True, DEGRADE_AMBIGUOUS
    return False, ""


def _is_rate_limit(e: Exception) -> bool:
    """(하위호환) 예전 이름 — 이제 '강등해야 하는가'를 뜻한다.

    이름이 거짓말이 되어 `_should_degrade`로 옮겼다. 기존 호출부·테스트를 위해
    얇은 별칭만 남긴다."""
    return _should_degrade(e)[0]


class OpenAICompatClient:
    def __init__(self, model: str | None = None, max_tokens: int | None = None):
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("pip install openai 필요 (로컬/무료 백엔드)") from e
        # 기본값 = Ollama 로컬(무료, 키 불필요). env는 전부 strip —
        # 대시보드 붙여넣기에 딸려온 공백/줄바꿈이 Bearer 헤더를 깨뜨린 실관측(401).
        base_url = (os.getenv("UNTIL_BASE_URL") or "").strip() or "http://localhost:11434/v1"
        api_key = (os.getenv("UNTIL_API_KEY") or "").strip() or "ollama"
        self.model = (model or os.getenv("UNTIL_MODEL") or "").strip() or "llama3.2"
        # 긴 에세이/finalize 잘림 방지: UNTIL_MAX_TOKENS로 상향 가능(기본 2048).
        if max_tokens is None:
            max_tokens = int(os.getenv("UNTIL_MAX_TOKENS", "2048"))
        self.max_tokens = max_tokens
        self.base_url = base_url
        self._client = OpenAI(base_url=base_url, api_key=api_key)
        # 백업 제공자 사슬(선택) — 주 제공자의 모델 사슬까지 전부 소진(429)하면
        # 번호 순서대로(_2, _3, _4 …) 다음 제공자로 넘어간다. 슬롯마다 독립 키·모델·폴백사슬.
        # 예: 주=Cerebras → _2=NVIDIA(Kimi) → _3=Gemini → _4=Groq(최후).
        # base_url·key가 둘 다 있는 슬롯만 활성(키 없는 슬롯은 조용히 건너뜀 → 부분 설정 허용).
        # 최대 _9까지 훑고 빈칸은 건너뛴다(중간 슬롯 미설정에도 내성).
        self._alts = []  # [(client, base_url, model, fallback_env_name), …]
        for i in range(2, 10):
            alt_url = (os.getenv(f"UNTIL_BASE_URL_{i}") or "").strip()
            alt_key = (os.getenv(f"UNTIL_API_KEY_{i}") or "").strip()
            if not (alt_url and alt_key):
                continue
            alt_model = (os.getenv(f"UNTIL_MODEL_{i}") or "").strip() or self.model
            self._alts.append((OpenAI(base_url=alt_url, api_key=alt_key), alt_url,
                               alt_model, f"UNTIL_MODEL_FALLBACK_{i}"))

    def complete(
        self, system: str, user: str, *,
        tag: str = "", json: bool = False,
        schema: Optional[dict] = None,
        documents: Optional[List[SourceDoc]] = None,
        cache: bool = True,
    ) -> LLMResult:
        messages = build_messages(system, user, documents)
        kwargs: dict = {"model": self.model, "messages": messages, "max_tokens": self.max_tokens}
        if json or schema is not None:
            # OpenAI 호환 JSON 모드(스키마 강제는 엔드포인트마다 지원 상이 → best-effort).
            kwargs["response_format"] = {"type": "json_object"}
            # Groq 등 일부 엔드포인트는 json_object를 쓰려면 메시지에 'json' 단어가 있어야 함.
            messages[-1]["content"] += "\n\n반드시 유효한 JSON 객체로만 응답하세요 (respond in JSON)."
        # 시도 순서: 주 모델 → 같은 제공자의 폴백 사슬 → 백업 제공자들(_2, _3, … 순서).
        # 429(한도 소진)에만 강등 — 다른 오류는 즉시 표면화(무중단보다 정직이 먼저).
        attempts = [(self._client, kwargs["model"])]
        attempts += [(self._client, m) for m in
                     fallback_models(self.base_url, kwargs["model"],
                                     os.getenv("UNTIL_MODEL_FALLBACK"))]
        for alt_client, alt_url, alt_model, alt_fb_env in self._alts:
            attempts.append((alt_client, alt_model))
            attempts += [(alt_client, m) for m in
                         fallback_models(alt_url, alt_model, os.getenv(alt_fb_env))]
        resp = None
        degraded_from = ""       # 강등이 있었으면 첫 사유를 남긴다
        used_index = 0
        for i, (client, model) in enumerate(attempts):
            try:
                kwargs["model"] = model
                resp = client.chat.completions.create(**kwargs)
                used_index = i
                break
            except Exception as e:
                degrade, reason = _should_degrade(e)
                if i + 1 >= len(attempts) or not degrade:
                    if degrade:
                        _LOG.error(
                            "LLM 제공자 사슬 전부 소진 — 마지막 시도 %s (%s): %s",
                            model, reason, type(e).__name__)
                    raise
                degraded_from = degraded_from or reason
                # 조용한 강등 금지 — 주 제공자가 죽은 사실이 운영자에게 보여야 한다.
                # 이게 없으면 주 제공자가 몇 주째 죽어 있어도 아무도 모른다.
                _LOG.warning(
                    "LLM 강등 %d→%d: %s → %s (사유=%s, 예외=%s)%s",
                    i, i + 1, model, attempts[i + 1][1], reason, type(e).__name__,
                    " ※ 키 오설정일 수 있음 — 주 제공자 자격을 확인하세요"
                    if reason == DEGRADE_AMBIGUOUS else "")
        text = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        return LLMResult(
            text=text.strip(),
            backend="local",
            tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
            tokens_out=getattr(usage, "completion_tokens", 0) or 0,
            citations=[],  # Anthropic 전용 기능 → 로컬에선 비활성
            # 폴백 사슬에서 **실제로 응답한** 모델. 요청한 model이 아니라
            # 응답의 model을 우선한다(제공자가 라우팅을 바꿔도 진실을 남긴다).
            model=str(getattr(resp, "model", "") or kwargs.get("model") or ""),
            # 주 제공자가 아니라 백업이 답한 사실과 그 사유. meter가 usage에
            # 모아 두면 운영자가 "언제부터 주 제공자가 죽었는지"를 볼 수 있다.
            degraded=used_index > 0,
            degrade_reason=degraded_from if used_index > 0 else "",
        )
