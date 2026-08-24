"""LLM 호출 계측 래퍼 — 실행당 호출 수·입출력 토큰을 usage dict에 합산.

코퍼스 러너(run_corpus_validation._MeteredClient)와 같은 계보를 파이프라인
안으로 옮긴 것. usage dict는 Result.llm_usage로 부착돼 세션에 지속되고,
웹 텔레메트리의 llm_calls/llm_tokens_in/llm_tokens_out 원천이 된다.
"""
from __future__ import annotations

import threading
from typing import Any

# usage dict는 Result.llm_usage로 래퍼 인스턴스들 사이에 공유된다(run 본 패스·
# 경량 패스·2차 패스). 인스턴스별 lock이면 같은 dict를 서로 다른 lock이 보호해
# 병렬 2차 패스에서 카운트가 유실된다 — 모듈 공유 lock 하나로 직렬화(경합 미미).
_METER_LOCK = threading.Lock()


def new_usage() -> dict:
    return {"llm_calls": 0, "llm_tokens_in": 0, "llm_tokens_out": 0,
            "degraded_calls": 0}


class MeteredClient:
    """complete()를 위임하고 usage dict에 누적한다(스레드 안전 — unit 병렬 대비)."""

    def __init__(self, inner: Any, usage: dict,
                 lock: threading.Lock | None = None) -> None:
        self.inner = inner
        self.usage = usage
        self._lock = lock or _METER_LOCK

    def complete(self, *args, **kwargs):
        result = self.inner.complete(*args, **kwargs)
        with self._lock:
            self.usage["llm_calls"] = int(self.usage.get("llm_calls") or 0) + 1
            self.usage["llm_tokens_in"] = (int(self.usage.get("llm_tokens_in") or 0)
                                           + int(getattr(result, "tokens_in", 0) or 0))
            self.usage["llm_tokens_out"] = (int(self.usage.get("llm_tokens_out") or 0)
                                            + int(getattr(result, "tokens_out", 0) or 0))
            # 실제 응답 모델 — 폴백 사슬을 타면 실행 도중에 바뀔 수 있으므로
            # 마지막 값이 아니라 **본 것 전부**를 순서대로 모은다(중복 제외).
            model = str(getattr(result, "model", "") or "")
            if model:
                seen = self.usage.setdefault("models", [])
                if model not in seen:
                    seen.append(model)
            # 강등(주 제공자 실패 → 백업 응답) 횟수와 사유를 함께 모은다.
            if getattr(result, "degraded", False):
                self.usage["degraded_calls"] = int(
                    self.usage.get("degraded_calls") or 0) + 1
                reason = str(getattr(result, "degrade_reason", "") or "")
                if reason:
                    reasons = self.usage.setdefault("degrade_reasons", [])
                    if reason not in reasons:
                        reasons.append(reason)
        return result

    def __getattr__(self, name: str):
        return getattr(self.inner, name)
