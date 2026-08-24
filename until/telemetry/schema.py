"""Allowlist-built, source-leak-checked telemetry records.

Telemetry is constructed from a fixed set of aggregate signals.  It is never
made by copying an audit record and deleting known-sensitive fields.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import date
from pathlib import Path
from typing import Any

# 1.1 (2026-08-13): COURSE_ALGORITHMS_2026F §7 측정 필드 추가 —
# route_strategy(신설 3 포함)·route_source·lab_stage·evidence_missing.
# algo_version 열거값 "v0.1"/"v0.2"와 기존 strategy 누락분(personal_upload·
# problem_set)도 이때 등재. elapsed_ms(정수 밀리초 dict)·algo_version 키
# 자체는 1.0부터 allowlist에 있어 키 추가는 없다.
SCHEMA_VERSION = "1.2"
USER_KEY_PATH = Path("_until_work/.user_key")

TELEMETRY_ALLOWLIST: frozenset[str] = frozenset({
    "schema_version", "salt_version", "run_id", "user_key", "assignment_key", "date",
    "algo_version", "git_sha", "context_mode", "pipeline_mode", "backend",
    "parser_backend", "strategy", "unit_strategy", "task_type", "actionable",
    "route_agreement", "route_confidence", "unmatched_route", "status", "failures",
    "checks", "capture_warnings", "readiness_warning_labels",
    "unit_readiness_warning_labels", "guard_passed", "unit_guard_passed", "decisions",
    "unit_decisions", "unit_count", "spec_chars_bucket", "intro_files",
    "intro_file_exts", "draft_chars", "unit_draft_chars", "deadline_bucket",
    "has_reference_submission", "reference_kinds", "reference_parse_failures",
    "reference_format_match", "elapsed_ms", "llm_calls", "llm_tokens_in",
    "llm_tokens_out", "edit_ratio", "edit_ops", "user_rating",
    "user_reported_minutes", "decision_total", "decision_answered",
    "decision_partial", "decision_skipped", "decision_response_rate",
    "decision_median_seconds", "decision_kinds", "ai_suggestion_offered",
    "ai_suggestion_accepted", "ai_suggestion_edited", "ai_suggestion_rejected",
    "warning_shown", "warning_resolved", "stage", "source", "user_seconds",
    "revision_count", "voice_match",
    # §7 측정 계획(COURSE_ALGORITHMS_2026F, schema 1.1) — 값은 전부 _ENUMS의
    # 고정 어휘만 허용된다(자유 문자열은 assert_no_source_leak가 차단).
    "route_strategy",    # 과제별 라우팅 분포(기존 strategy 전부 + 신설 3)
    "route_source",      # rule | profile_hint | llm_inferred | clarify
    "lab_stage",         # pre | notebook | result | ""(해당 없음)
    "evidence_missing",  # 어떤 근거가 자주 비는지 — EVIDENCE_KINDS 열거형 배열
    # PHASE 3 출처 기록 — 이게 없으면 "톤이 바뀐 게 모델 때문인지 프롬프트
    # 때문인지" 영원히 가릴 수 없다. prompt_version은 "1.0.0+<지문>" 형태라
    # _VERSION_RE를 통과하고, model_version은 자유 문자열(제공자·티어 노출)이라
    # 이름 대신 12자리 지문을 싣는다(_HEX_RE 통과, 같은 모델=같은 지문).
    "prompt_version", "model_fingerprint", "used_fallback",
    # 알고리즘 동결 게이트 값(UNTIL_ALGO_VERSION → config.algo_version()).
    # `algo_version`(릴리스 SemVer)과 다른 축이다: 같은 릴리스라도 실행 시
    # 게이트가 v0.1이었는지 v0.2였는지는 릴리스 번호로 알 수 없다. 8월
    # 동결·측정은 이 값으로만 갈린다. 열거형 "v0.1"|"v0.2"만 나간다.
    "algo_gate",
})

_ENUMS = frozenset({
    SCHEMA_VERSION, "1", "draft", "review", "final", "export", "etl", "manual",
    "full", "no_etl_context", "bare", "legacy", "unit", "mock", "local",
    "anthropic", "live", "basic",
    "non_actionable", "weekly_inquiry", "presentation_conversion", "team_project",
    "activity_form", "rmd_notebook", "zip_project", "reflective_series",
    "distributed_spec", "code_project", "evidence_report", "staged_writing",
    "spec_clarification", "essay", "report", "reflective_report", "inquiry",
    "problemset", "code", "presentation", "general", "passed", "failed", "excluded",
    "capture", "pipeline", "boundary", "readiness", "explicit_route",
    # readiness 경고 라벨 — `readiness_warning_labels`로 나간다. 코드 사전에서만
    # 나오는 고정 어휘이므로 열거형이 맞다. **여기 없는 라벨이 경고로 뜨면 그 실행의
    # 텔레메트리가 통째로 차단된다** — 세션 중 4회 본 드문 간헐 테스트 실패의 정체가
    # 이것이었다(2026-08-23 트레이스백 확보: `free string blocked in telemetry: '발표'`).
    # 유형별 점검(발표·코드·실측·실행결과·활동기록)이 뜨는 표본이 걸릴 때만 터져서
    # 무작위처럼 보였다. test_telemetry_web이 이제 누락을 기계로 막는다.
    "마감", "인용", "근거", "양식", "분량", "경계선", "자료",
    "발표", "코드", "실측", "실행결과", "활동기록", "형식",
    "가치판단", "관점·논지", "진로·경험", "취향·스타일", "범위·선택", "고유 판단",
    "<500", "500-2k", "2k-8k", "8k+", "D-7+", "D-3~6", "D-1~2", "D0",
    "overdue", "pdf", "docx", "hwpx", "html", "pptx", "xlsx", "xls", "csv",
    "txt", "text", "md", "rmd", "zip", "hwp", "ino", "png", "none",
    "rmd-template", "zip-project",
    # ── schema 1.1 (COURSE_ALGORITHMS_2026F §7) ──
    # 기존 라우터 strategy 중 1.0 등재 누락분 — course_id 같은 원문이 아니라
    # 코드 사전에서만 나오는 고정 어휘라 열거형 등재가 맞다.
    "personal_upload", "problem_set",
    # 신설 strategy 3종(v0.2 라우팅) — route_strategy·strategy 공용.
    "hdl_lab", "lab_report_cycle", "textbook_problem_set",
    # route_source — §3 폴백(course_profiles 힌트)이 얼마나 쓰이는지.
    "rule", "profile_hint", "llm_inferred", "clarify",
    # lab_stage — 실험과목 3단계(AssignmentRoute.stage와 동일 어휘, ""는 공백
    # 문자열 허용 규칙으로 이미 통과).
    "pre", "notebook", "result",
    # algo_version 게이트 값 — A/B 비교의 축. SemVer("1.10.0")는 _VERSION_RE로
    # 계속 허용되지만 동결 게이트 표기("v0.1"/"v0.2")는 열거형으로만 나간다.
    "v0.1", "v0.2", "1.2", "yes", "no",
    # evidence_missing 배열 원소 — requirements.EVIDENCE_KINDS의 고정 4종.
    # route.required_evidence의 한국어 자유 문구는 절대 싣지 않는다(§3 금지).
    "lecture_material", "user_experience", "source_document", "general_knowledge",
})
# algo_gate로 나갈 수 있는 값 전체. config.algo_version()이 이미 알 수 없는 값을
# v0.1로 정규화하지만, 방출부는 그 정규화를 신뢰하지 않고 이 목록으로 한 번 더
# 거른다 — 게이트 표기가 늘어나면(v0.3 등) 여기와 _ENUMS를 함께 늘린다.
ALGO_GATE_VALUES: frozenset[str] = frozenset({"v0.1", "v0.2"})

_HEX_RE = re.compile(r"^[0-9a-f]{12,}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def algo_gate() -> str | None:
    """실행 시점의 알고리즘 동결 게이트("v0.1"|"v0.2"). 열거형 밖이면 None.

    `algo_version`(릴리스 SemVer)만으로는 같은 릴리스의 v0.1 실행과 v0.2 실행을
    구분할 수 없다 — 게이트는 런타임 env(`UNTIL_ALGO_VERSION`)라 빌드에 안 남는다.
    이 필드가 없으면 이벤트 로그만 보고 "어느 알고리즘이 이 결과를 냈나"를 사후에
    가릴 수 없어 8월 동결·측정이 성립하지 않는다.

    **웹(telemetry/web.py)과 CLI(run_corpus_validation.py) 두 생산자가 이 함수
    하나만 부른다.** 각자 config를 읽어 각자 거르면 언젠가 값이 갈리고, 그때
    갈라진 원장은 게이트 기준 교차 집계를 조용히 망친다 — 로직을 복제하지 마라.
    """
    from ..config import algo_version  # config는 stdlib만 쓰므로 순환 없음
    value = algo_version()
    return value if value in ALGO_GATE_VALUES else None
_FAILURE_RE = re.compile(
    r"^(?:unit:)?(?:missing_spec|no_documents|empty_draft|boundary_guard_failed|"
    r"missing_context_bundle|"
    r"missing_task_type|missing_pipeline_strategy|reference_format_mismatch|"
    r"capture_replacement_character_contamination|capture_raw_html_contamination|"
    r"all_intro_files_unparsed_without_question|readiness:(?:양식|분량|경계선|자료)|"
    r"unit_readiness:(?:양식|분량|경계선|자료)|readiness:invalid_citation|"
    r"unit_readiness:invalid_citation|pipeline_exception:[A-Za-z_][A-Za-z0-9_]*|"
    r"route_mismatch:[a-z_]+->[a-z_]+)$")
_REFERENCE_FAILURE_RE = re.compile(r"^[a-z0-9]+:[A-Za-z_][A-Za-z0-9_]*$")


class TelemetryLeakBlocked(RuntimeError):
    """A record contained a non-enumerated string or source-text fragment."""


def spec_chars_bucket(chars: int) -> str:
    """Bucket source-spec length so the exact quasi-identifier never leaves."""
    count = max(0, int(chars))
    if count < 500:
        return "<500"
    if count < 2_000:
        return "500-2k"
    if count < 8_000:
        return "2k-8k"
    return "8k+"


def deadline_bucket(days_remaining: int) -> str:
    """Bucket days from today; negative means the deadline has passed."""
    days = int(days_remaining)
    if days < 0:
        return "overdue"
    if days == 0:
        return "D0"
    if days <= 2:
        return "D-1~2"
    if days <= 6:
        return "D-3~6"
    return "D-7+"


def _project_salt() -> bytes:
    value = os.getenv("UNTIL_PROJECT_SALT")
    if not value:
        raise RuntimeError("UNTIL_PROJECT_SALT is required for telemetry fingerprints")
    return value.encode("utf-8")


def assignment_fingerprint(course_id: Any, assignment_id: Any) -> str:
    raw = f"{course_id}:{assignment_id}".encode("utf-8")
    return hmac.new(_project_salt(), raw, hashlib.sha256).hexdigest()[:12]


def user_key(path: Path | None = None) -> str:
    target = path or USER_KEY_PATH
    try:
        current = target.read_text(encoding="ascii").strip()
        if re.fullmatch(r"[0-9a-f]{16}", current):
            return current
    except OSError:
        pass
    target.parent.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_hex(8)
    try:
        with target.open("x", encoding="ascii") as handle:
            handle.write(generated)
        if os.name == "posix":
            os.chmod(target, 0o600)
        return generated
    except FileExistsError:
        current = target.read_text(encoding="ascii").strip()
        if not re.fullmatch(r"[0-9a-f]{16}", current):
            raise RuntimeError("existing telemetry user key is invalid") from None
        return current


def web_user_key(uid: str) -> str:
    """Return a stable per-browser key, requiring a web-specific secret salt."""
    salt = os.getenv("UNTIL_TELEMETRY_SALT")
    if not salt:
        raise RuntimeError("UNTIL_TELEMETRY_SALT is required for web telemetry")
    return hmac.new(salt.encode("utf-8"), uid.encode("utf-8"),
                    hashlib.sha256).hexdigest()[:16]


def _all_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _all_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _all_strings(item)


def _allowed_string(value: str) -> bool:
    return bool(value == "" or value in _ENUMS or _HEX_RE.fullmatch(value)
                or _DATE_RE.fullmatch(value) or _VERSION_RE.fullmatch(value)
                or _GIT_SHA_RE.fullmatch(value)
                or _FAILURE_RE.fullmatch(value) or _REFERENCE_FAILURE_RE.fullmatch(value))


def _fixed_vocabulary_string(value: str) -> bool:
    """원문에서 파생될 수 없는 고정 사전·고정 실패 코드인지 판정한다."""
    return bool(value in _ENUMS or _FAILURE_RE.fullmatch(value)
                or _REFERENCE_FAILURE_RE.fullmatch(value))


def assert_no_source_leak(out: dict, sources: dict) -> None:
    """자유 문자열과 원문 8-gram 누출을 fail-closed로 차단한다.

    고정 열거형과 정규식으로 제한된 실패 코드는 원문에서 파생될 수 없는 어휘라서
    우연히 같은 단어가 원문에 있어도 n-gram 대상에서 제외한다. 반면 hex·날짜·버전·
    git SHA 형태는 허용 형식 안에 내용 해시가 숨을 수 있으므로 계속 스캔한다.
    """
    output_strings = list(_all_strings(out))
    for value in output_strings:
        if not _allowed_string(value):
            raise TelemetryLeakBlocked(f"free string blocked in telemetry: {value[:24]!r}")
    searchable = [value for value in output_strings
                  if len(value) >= 8 and not _fixed_vocabulary_string(value)]
    for source in _all_strings(sources):
        if len(source) < 8:
            continue
        for value in searchable:
            if any(value[i:i + 8] in source for i in range(len(value) - 7)):
                raise TelemetryLeakBlocked("source 8-gram blocked in telemetry")


def to_telemetry(record: dict, *, sources: dict) -> dict:
    """Build a telemetry record exclusively from explicitly allowed aggregate fields."""
    out = {key: record[key] for key in TELEMETRY_ALLOWLIST if key in record}
    out["schema_version"] = SCHEMA_VERSION
    out["salt_version"] = str(record.get("salt_version") or "1")
    out["run_id"] = record.get("run_id") or secrets.token_hex(8)
    out["user_key"] = record.get("user_key") or user_key()
    course_id, assignment_id = sources.get("course_id"), sources.get("assignment_id")
    out["assignment_key"] = (assignment_fingerprint(course_id, assignment_id)
                             if course_id is not None and assignment_id is not None else None)
    out["date"] = record.get("date") or date.today().isoformat()
    if "decision_total" in out:
        total = out["decision_total"]
        answered = out.get("decision_answered")
        out["decision_response_rate"] = (
            round(answered / total, 2)
            if isinstance(total, int) and total > 0 and isinstance(answered, int)
            else None
        )
    assert_no_source_leak(out, sources)
    return out
