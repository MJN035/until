# -*- coding: utf-8 -*-
"""LLM 제공자 폴백 — "이 제공자로는 더 못 쓴다"에서 백업으로 넘어가는가.

배경(2026-08-20): `_is_rate_limit`이 429/"rate limit"만 봐서, 유료 전환 후
잔액 0이 402/insufficient_quota로 오면 강등 조건에 걸리지 않았다. 백업 제공자
3개를 하나도 시도하지 않고 서비스가 통째로 죽는 경로였다.

원칙은 유지한다 — 무중단보다 정직이 먼저. 네트워크 오류·잘못된 요청·5xx는
강등하지 않고 즉시 표면화한다. 실제 API는 부르지 않고 예외를 주입해 검증한다.
"""
from __future__ import annotations

import logging
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.llm import openai_compat as oc
from until.llm.meter import MeteredClient, new_usage


class ApiError(Exception):
    """openai SDK 예외 흉내 — status_code를 들고 온다."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status_code = status


class _Resp:
    def __init__(self, model: str, text: str = "ok"):
        self.model = model
        self.choices = [types.SimpleNamespace(
            message=types.SimpleNamespace(content=text))]
        self.usage = types.SimpleNamespace(prompt_tokens=3, completion_tokens=5)


class _FakeClient:
    """attempts 순서대로 정해진 결과/예외를 돌려준다. 호출 모델을 기록한다."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[str] = []
        outer = self

        class _Completions:
            def create(self, **kwargs):
                model = kwargs["model"]
                outer.calls.append(model)
                item = outer.script.pop(0) if outer.script else _Resp(model)
                if isinstance(item, Exception):
                    raise item
                return _Resp(model)

        self.chat = types.SimpleNamespace(completions=_Completions())


def _client(script, *, alts=0):
    """OpenAICompatClient를 SDK 없이 조립한다(__init__을 건너뛴다)."""
    obj = oc.OpenAICompatClient.__new__(oc.OpenAICompatClient)
    obj.model = "primary-model"
    obj.max_tokens = 128
    obj.base_url = "https://api.cerebras.ai/v1"
    fake = _FakeClient(script)
    obj._client = fake
    obj._alts = []
    for i in range(alts):
        obj._alts.append((fake, f"https://backup{i}.example/v1",
                          f"backup-{i}-model", f"UNTIL_MODEL_FALLBACK_{i + 2}"))
    return obj, fake


# ── ① 402 Payment Required ─────────────────────────────────────────
def test_payment_required_degrades_to_backup():
    """유료 전환 후 잔액 0 = 402. 예전 코드가 여기서 서비스를 죽였다."""
    client, fake = _client([ApiError("Payment Required", 402),
                            ApiError("Payment Required", 402),
                            _Resp("backup-0-model")], alts=1)
    result = client.complete("sys", "user")
    assert result.text == "ok"
    assert result.degraded is True
    assert result.degrade_reason == oc.DEGRADE_PAYMENT
    assert result.model == "backup-0-model", result.model
    assert fake.calls[0] == "primary-model"
    assert "backup-0-model" in fake.calls
    print("OK 402 Payment Required → 백업 제공자로 강등")


# ── ② insufficient_quota 메시지 ─────────────────────────────────────
def test_insufficient_quota_message_degrades():
    """상태 코드가 400/401/403으로 와도 메시지로 잔액 소진을 알아본다."""
    for status in (400, 401, 403, None):
        client, fake = _client(
            [ApiError("You exceeded your current quota: insufficient_quota", status),
             ApiError("insufficient credits remaining", status),
             _Resp("backup-0-model")], alts=1)
        result = client.complete("sys", "user")
        assert result.degraded is True, status
        assert result.degrade_reason == oc.DEGRADE_EXHAUSTED, (status, result.degrade_reason)
        assert len(fake.calls) >= 2, status
    print("OK insufficient_quota/credits 메시지 → 강등 (상태 코드 무관)")


# ── ③ 백업까지 전부 소진 ────────────────────────────────────────────
def test_all_providers_exhausted_raises_last_error():
    """전부 소진되면 조용히 삼키지 않고 마지막 오류를 그대로 올린다."""
    client, fake = _client([ApiError("Payment Required", 402)] * 8, alts=2)
    try:
        client.complete("sys", "user")
        raise AssertionError("전부 소진인데 예외가 안 났다")
    except ApiError as exc:
        assert exc.status_code == 402
    assert len(fake.calls) >= 3, fake.calls          # 주 + 백업들을 실제로 시도
    print(f"OK 전 제공자 소진 → 마지막 오류 표면화 (시도 {len(fake.calls)}회)")


# ── 원칙 유지: 강등하면 안 되는 것 ──────────────────────────────────
def test_non_degradable_errors_surface_immediately():
    """네트워크·5xx·잘못된 요청은 백업을 태우지 않고 즉시 올린다(정직이 먼저)."""
    for err in (ApiError("500 internal server error", 500),
                ApiError("bad request: unknown field", 400),
                ConnectionError("connection reset by peer")):
        client, fake = _client([err, _Resp("backup-0-model")], alts=1)
        try:
            client.complete("sys", "user")
            raise AssertionError(f"강등되면 안 되는 오류가 강등됐다: {err}")
        except (ApiError, ConnectionError):
            pass
        assert len(fake.calls) == 1, (str(err), fake.calls)
    print("OK 5xx·잘못된 요청·네트워크 오류 → 강등 없이 즉시 표면화")


def test_ambiguous_auth_degrades_but_says_so():
    """401/403은 키 오설정과 잔액 소진이 겹친다 — 강등하되 사유를 남긴다."""
    client, _fake = _client([ApiError("Unauthorized", 401),
                             ApiError("Unauthorized", 401),
                             _Resp("backup-0-model")], alts=1)
    result = client.complete("sys", "user")
    assert result.degraded and result.degrade_reason == oc.DEGRADE_AMBIGUOUS
    print("OK 401/403 → 강등 + auth_or_quota 사유 기록")


# ── 조용한 강등 금지 ────────────────────────────────────────────────
def test_degradation_is_logged_and_metered():
    """운영자가 볼 수 있어야 한다 — 경고 로그 + usage 집계 둘 다."""
    client, _fake = _client([ApiError("Payment Required", 402),
                             ApiError("Payment Required", 402),
                             _Resp("backup-0-model")], alts=1)
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("until.llm.openai_compat")
    handler = _Capture()
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.WARNING)
    try:
        usage = new_usage()
        metered = MeteredClient(client, usage)
        metered.complete("sys", "user")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)

    warnings = [r for r in records if r.levelno >= logging.WARNING]
    assert warnings, "강등이 로그에 남지 않았다 — 조용한 강등"
    assert any("강등" in r.getMessage() for r in warnings)
    assert usage["degraded_calls"] == 1, usage
    assert usage["degrade_reasons"] == [oc.DEGRADE_PAYMENT], usage
    assert "backup-0-model" in usage["models"]
    print("OK 강등이 경고 로그 + usage(degraded_calls·사유)에 남는다")


def test_no_degradation_leaves_signal_clean():
    """정상 응답에는 강등 신호가 붙지 않는다(오탐 방지)."""
    client, _fake = _client([_Resp("primary-model")])
    usage = new_usage()
    result = MeteredClient(client, usage).complete("sys", "user")
    assert result.degraded is False and result.degrade_reason == ""
    assert usage["degraded_calls"] == 0
    assert "degrade_reasons" not in usage
    print("OK 정상 경로 — 강등 신호 없음")


def test_legacy_alias_still_works():
    """_is_rate_limit은 하위호환 별칭으로 남아 있다(기존 테스트가 부른다)."""
    assert oc._is_rate_limit(ApiError("rate limit", 429))
    assert oc._is_rate_limit(ApiError("Payment Required", 402))
    assert not oc._is_rate_limit(ApiError("500 internal error", 500))
    print("OK _is_rate_limit 별칭 유지")


TESTS = [
    test_payment_required_degrades_to_backup,
    test_insufficient_quota_message_degrades,
    test_all_providers_exhausted_raises_last_error,
    test_non_degradable_errors_surface_immediately,
    test_ambiguous_auth_degrades_but_says_so,
    test_degradation_is_logged_and_metered,
    test_no_degradation_leaves_signal_clean,
    test_legacy_alias_still_works,
]

if __name__ == "__main__":
    for fn in TESTS:
        fn()
    print("LLM FALLBACK TESTS PASS")
