"""Privacy boundary tests for allowlist-built telemetry."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from until.telemetry.schema import (TELEMETRY_ALLOWLIST, TelemetryLeakBlocked,
                                    _allowed_string, assert_no_source_leak,
                                    assignment_fingerprint,
                                    to_telemetry, web_user_key)

ROOT = Path(__file__).resolve().parents[1]


def _record() -> dict:
    return {
        # 아래 값들은 소금 해시 자리의 **합성 hex**다(스키마가 hex만 허용 —
        # until/telemetry/schema.py). 시크릿이 아니지만 gitleaks의 generic-api-key가
        # 모양만 보고 잡아 공개 저장소 스캔에 오탐이 뜬다.
        "run_id": "a3f1c2d4e5b60789", "user_key": "9c4e7a1b2d3f4e5a",  # gitleaks:allow
        "algo_version": "1.8.0", "git_sha": "63c7220", "context_mode": "full",
        "pipeline_mode": "legacy", "backend": "mock", "parser_backend": "basic",
        "strategy": "staged_writing", "unit_strategy": "staged_writing",
        "task_type": "essay", "actionable": True, "route_agreement": True,
        "unmatched_route": False, "status": "passed", "failures": [],
        "checks": ["capture", "pipeline", "boundary", "readiness", "explicit_route"],
        "capture_warnings": 0, "readiness_warning_labels": ["마감"],
        "unit_readiness_warning_labels": ["마감", "인용", "근거"],
        "guard_passed": True, "unit_guard_passed": True, "decisions": 3,
        "unit_decisions": 1, "unit_count": 1, "spec_chars_bucket": "500-2k",
        "intro_files": 0, "intro_file_exts": [], "draft_chars": 471,
        "unit_draft_chars": 485, "deadline_bucket": "overdue",
        "has_reference_submission": True, "reference_kinds": ["docx"],
        "reference_parse_failures": [], "reference_format_match": True,
        "elapsed_ms": {"capture": 10, "pipeline": 20},
    }


def _sources(text: str = "과제 원문은 텔레메트리에 절대로 포함되면 안 됩니다.") -> dict:
    return {"course_id": "302199", "assignment_id": "369118", "spec_text": text,
            "draft_body": "생성 초안 본문도 공유하지 않습니다.",
            "attachment_text": "첨부 문서 내용 역시 로컬에만 남습니다."}


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _actual_corpus_text() -> str:
    manifests = sorted((ROOT / "_until_work" / "corpus").glob("*/manifest.jsonl"))
    if manifests:
        row = json.loads(manifests[0].read_text(encoding="utf-8").splitlines()[0])
        spec = manifests[0].parent / row["dir"] / "spec.md"
        if spec.exists():
            return spec.read_text(encoding="utf-8")
    return (ROOT / "examples" / "sample_assignment.txt").read_text(encoding="utf-8")


def test_telemetry_only_allowlisted_fields():
    record = {**_record(), "title": "비밀 과제명", "course_id": "302199",
              "new_future_field": "기본적으로 나가면 안 됨"}
    out = to_telemetry(record, sources=_sources())
    assert set(out) <= TELEMETRY_ALLOWLIST
    assert not {"title", "course_id", "new_future_field"} & set(out)


def test_salt_version_present():
    old = os.environ.get("UNTIL_PROJECT_SALT")
    os.environ["UNTIL_PROJECT_SALT"] = "test-project-salt"
    try:
        assert to_telemetry(_record(), sources=_sources())["salt_version"] == "1"
    finally:
        if old is None:
            os.environ.pop("UNTIL_PROJECT_SALT", None)
        else:
            os.environ["UNTIL_PROJECT_SALT"] = old


def test_web_user_key_requires_salt():
    old = os.environ.pop("UNTIL_TELEMETRY_SALT", None)
    try:
        try:
            web_user_key("browser-a")
            raise AssertionError("missing web telemetry salt was accepted")
        except RuntimeError:
            pass
    finally:
        if old is not None:
            os.environ["UNTIL_TELEMETRY_SALT"] = old


def test_manual_assignment_key_is_null():
    sources = {**_sources(), "course_id": None, "assignment_id": None}
    record = {**_record(), "stage": "draft", "source": "manual"}
    assert to_telemetry(record, sources=sources)["assignment_key"] is None
def test_telemetry_contains_no_source_text():
    source = _actual_corpus_text()
    out = to_telemetry(_record(), sources=_sources(source))
    output_values = list(_strings(out))
    assert not any(source[i:i + 8] in value
                   for i in range(max(0, len(source) - 7)) for value in output_values)


def test_telemetry_no_free_strings():
    out = to_telemetry(_record(), sources=_sources())
    assert all(_allowed_string(value) for value in _strings(out))


def test_telemetry_leak_makes_no_request():
    requests = []

    def emit(record, sources):
        out = to_telemetry(record, sources=sources)
        requests.append(out)  # 전송 지점: 검사를 통과해야만 도달

    record = {**_record(), "parser_backend": "원문여덟글자누출문장"}
    try:
        emit(record, _sources("앞부분 원문여덟글자누출문장 뒷부분"))
    except TelemetryLeakBlocked:
        pass
    else:
        raise AssertionError("원문 누출은 fail-closed여야 함")
    assert requests == []


def test_enum_colliding_with_source_is_allowed():
    record = {**_record(), "task_type": "presentation"}
    source = "Submit a copy of your persuasive presentation outline..."
    out = to_telemetry(record, sources=_sources(source))
    assert out["task_type"] == "presentation"


def test_content_hash_still_blocked():
    try:
        assert_no_source_leak(
            {"assignment_key": "abcdef123456"},  # gitleaks:allow — 합성 hex
            {"spec_text": "prefix abcdef123456 suffix"},
        )
    except TelemetryLeakBlocked:
        pass
    else:
        raise AssertionError("hex 내용 해시는 원문 8-gram 검사 대상이어야 함")


def test_fingerprint_requires_salt():
    old = os.environ.pop("UNTIL_PROJECT_SALT", None)
    try:
        try:
            assignment_fingerprint("302199", "369118")
        except RuntimeError:
            pass
        else:
            raise AssertionError("소금 없는 지문 폴백은 금지")
    finally:
        if old is not None:
            os.environ["UNTIL_PROJECT_SALT"] = old


def test_grade_fields_never_present():
    record = {**_record(), "grade": 99, "rubric": {"score": 10},
              "comments": "교수 자유 코멘트", "message": "마감 절대시각"}
    out = to_telemetry(record, sources=_sources())
    encoded = json.dumps(out, ensure_ascii=False)
    for forbidden in ("grade", "rubric", "comments", "message"):
        assert not re.search(rf'"{forbidden}"\s*:', encoded)


def test_decision_text_never_present():
    question = "졸업 후 어떤 진로를 선택할지 본인의 가치관으로 답하세요"
    answer = "나는 가족 상황 때문에 이 진로를 선택하려고 한다"
    record = {**_record(), "decision_total": 1, "decision_answered": 1,
              "decision_question": question, "decision_answer": answer,
              "decision_kinds": ["진로·경험"]}
    sources = {**_sources(), "decision_questions": question,
               "decision_answers": answer}
    out = to_telemetry(record, sources=sources)
    encoded_values = "\n".join(_strings(out))
    assert question not in encoded_values and answer not in encoded_values
    assert "decision_question" not in out and "decision_answer" not in out


def test_decision_rate_null_when_no_decisions():
    record = {**_record(), "decision_total": 0, "decision_answered": 0,
              "decision_response_rate": 0.0}
    out = to_telemetry(record, sources=_sources())
    assert out["decision_response_rate"] is None


if __name__ == "__main__":
    old = os.environ.get("UNTIL_PROJECT_SALT")
    os.environ["UNTIL_PROJECT_SALT"] = "offline-telemetry-test-salt"
    try:
        test_telemetry_only_allowlisted_fields()
        test_salt_version_present()
        test_web_user_key_requires_salt()
        test_manual_assignment_key_is_null()
        test_telemetry_contains_no_source_text()
        test_telemetry_no_free_strings()
        test_telemetry_leak_makes_no_request()
        test_enum_colliding_with_source_is_allowed()
        test_content_hash_still_blocked()
        test_fingerprint_requires_salt()
        test_grade_fields_never_present()
        test_decision_text_never_present()
        test_decision_rate_null_when_no_decisions()
    finally:
        if old is None:
            os.environ.pop("UNTIL_PROJECT_SALT", None)
        else:
            os.environ["UNTIL_PROJECT_SALT"] = old
    print("TELEMETRY TESTS PASS")
