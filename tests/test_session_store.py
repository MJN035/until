"""Signed JSON session persistence regression tests (offline, stdlib only)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from until.config import Config
from until.pipeline import run
from until.session_store import decode, encode, to_jsonable


def _result():
    cfg = Config()
    cfg.backend = "mock"
    return run(["examples/sample_assignment.txt"], cfg)


def _payload(result=None) -> dict:
    return {"result": result or _result(), "answers": {1: "내 답"},
            "suggestions": {1: {"answer": "제안", "why": "근거"}}, "review": None,
            "workspace": {"excluded_sources": [2], "versions": ["이전 초안"]}}


def test_roundtrip_preserves_result():
    result = _result()
    restored = decode(encode(_payload(result), 1234.5))
    assert restored is not None
    actual = restored["result"]
    assert actual.draft.body == result.draft.body
    assert actual.draft.decisions == result.draft.decisions
    assert actual.spec["task_type"] == result.spec["task_type"]
    assert actual.guard.passed == result.guard.passed
    assert restored["answers"] == {1: "내 답"}
    assert restored["workspace"] == {"excluded_sources": [2], "versions": ["이전 초안"]}
    # LLM 사용량이 세션 왕복에서 보존된다(텔레메트리 원가 원천).
    assert actual.llm_usage == result.llm_usage
    assert isinstance(actual.llm_usage, dict) and actual.llm_usage["llm_calls"] >= 1


def test_old_session_without_llm_usage_restores():
    """llm_usage 키가 없는 구세션 payload도 None 폴백으로 복원된다."""
    from until.session_store import _result_from
    data = to_jsonable(_payload())["result"]
    data.pop("llm_usage")
    assert _result_from(data).llm_usage is None


def test_length_target_mode_roundtrip():
    """LengthTarget.mode가 세션 왕복에서 보존된다(상한 전용 요건이 min으로 되돌지 않게).

    빠지면 v0.2의 '초과' 감축 reask가 복원한 세션에서만 사라져, 같은 과제인데
    새 세션과 복원 세션의 분량 판정이 달라진다.
    """
    from until.understanding.length_target import LengthTarget

    result = _result()
    result.length_target = LengthTarget(unit="자", min=None, max=200,
                                        raw="200자 이내", per_item="", mode="max")
    if result.units:
        result.units[0].length_target = LengthTarget(unit="자", min=300, max=500,
                                                     raw="300~500자", per_item="문항",
                                                     mode="range")

    data = to_jsonable(_payload(result))["result"]
    assert data["length_target"]["mode"] == "max"

    restored = decode(encode(_payload(result), 1234.5))
    assert restored is not None
    actual = restored["result"]
    assert actual.length_target == result.length_target
    assert actual.length_target.mode == "max"
    if result.units:
        assert actual.units[0].length_target.mode == "range"


def test_old_session_without_length_mode_restores():
    """mode 키가 없는 구세션 length_target도 기본값 'min'으로 복원된다."""
    from until.session_store import _length_from

    data = {"unit": "자", "min": 500, "max": None, "raw": "500자 이상", "per_item": ""}
    restored = _length_from(data)
    assert restored is not None and restored.mode == "min"
    assert _length_from(None) is None


def test_tampered_signature_rejected():
    envelope = json.loads(encode(_payload(), 1234.5))
    envelope["payload"]["result"]["draft"]["body"] += "x"
    assert decode(json.dumps(envelope, ensure_ascii=False).encode()) is None


def test_unknown_version_rejected():
    envelope = json.loads(encode(_payload(), 1234.5))
    envelope["v"] = 3
    assert decode(json.dumps(envelope, ensure_ascii=False).encode()) is None


def test_no_pickle_in_runtime():
    root = Path(__file__).resolve().parents[1] / "until"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    for forbidden in ("import pickle", "pickle.loads", "pickle.dumps"):
        assert forbidden not in source


def test_unserializable_type_raises():
    result = _result()
    result.spec["unknown"] = object()
    try:
        to_jsonable(_payload(result))
    except TypeError:
        pass
    else:
        raise AssertionError("미지 타입은 TypeError를 내야 함")


if __name__ == "__main__":
    old = os.environ.get("UNTIL_SESSION_KEY")
    os.environ["UNTIL_SESSION_KEY"] = "offline-test-session-key"
    try:
        test_roundtrip_preserves_result()
        test_old_session_without_llm_usage_restores()
        test_length_target_mode_roundtrip()
        test_old_session_without_length_mode_restores()
        test_tampered_signature_rejected()
        test_unknown_version_rejected()
        test_no_pickle_in_runtime()
        test_unserializable_type_raises()
    finally:
        if old is None:
            os.environ.pop("UNTIL_SESSION_KEY", None)
        else:
            os.environ["UNTIL_SESSION_KEY"] = old
    print("SESSION STORE TESTS PASS")
