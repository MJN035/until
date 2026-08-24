"""개인 원문 없이 전수 검증 러너의 판정 계약을 확인한다."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from run_corpus_validation import _confirm_live_run, _live_estimate, validate_one
from run_etl_corpus import _write_assignment_context, _write_course_context_cache
from until.llm.base import SourceDoc


def _row(directory: str, aid: str, title: str, course: str = "테스트 과목") -> dict:
    return {"dir": directory, "course_id": "1", "assignment_id": aid,
            "title": title, "course_name": course, "has_submission_text": False}


def test_validation_runs_legacy_unit_and_exclusion():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        task = root / "task"
        task.mkdir()
        (task / "spec.md").write_text(
            "# 관찰 실험 보고서\n\n실험 방법과 결과를 보고서로 작성하세요.", encoding="utf-8")
        context = task / "etl_context"
        context.mkdir()
        (context / "context.md").write_text(
            "# eTL 과목 컨텍스트\n\n관찰 기준표와 실험 안전 지침", encoding="utf-8")
        usage = {}
        result = validate_one(root, _row("task", "10", "관찰 실험 보고서"),
                              telemetry_aux=usage, backend="mock")
        assert result["status"] == "passed", result
        assert result["unit_guard_passed"] and result["unit_count"] >= 1
        assert result["checks"] == ["capture", "pipeline", "boundary",
                                     "readiness", "explicit_route"]
        assert len(result["id"]) == 12 and "title" not in result
        assert usage["llm_calls"] >= 4
        assert usage["llm_tokens_in"] == usage["llm_tokens_out"] == 0

        no_context = validate_one(
            root, _row("task", "10", "관찰 실험 보고서"),
            context_mode="no_etl_context")
        bare = validate_one(root, _row("task", "10", "관찰 실험 보고서"),
                            context_mode="bare")
        assert no_context["status"] == "passed" and bare["status"] == "passed"

        code_task = root / "code_task"
        code_task.mkdir()
        (code_task / "spec.md").write_text(
            "# 코드 과제\n\n파이썬 프로그램을 구현하고 제출하세요.", encoding="utf-8")
        intro = code_task / "intro_files"
        intro.mkdir()
        with zipfile.ZipFile(intro / "starter.zip", "w") as archive:
            archive.writestr("README.md", "starter")
        with_zip = validate_one(
            root, _row("code_task", "12", "코드 과제"),
            context_mode="no_etl_context")
        bare_without_zip = validate_one(
            root, _row("code_task", "12", "코드 과제"), context_mode="bare")
        assert with_zip["strategy"] == "zip_project"
        assert bare_without_zip["strategy"] == "code_project"
        assert not any("route_mismatch" in item
                       for item in bare_without_zip.get("failures", []))

        (context / "context.md").unlink()
        missing = validate_one(root, _row("task", "10", "관찰 실험 보고서"))
        assert missing["failures"] == ["missing_context_bundle"]

        grade = root / "grade"
        grade.mkdir()
        (grade / "spec.md").write_text("# 중간 총점", encoding="utf-8")
        excluded = validate_one(root, _row("grade", "11", "중간 총점"))
        assert excluded["status"] == "excluded" and not excluded["actionable"]
    print("OK 검증 러너 legacy+unit+비과제 제외·비식별 원장")


def test_live_estimate_and_confirmation_gate():
    estimate = _live_estimate(148)
    assert estimate == {"calls": 888, "tokens_in": 3_552_000,
                        "tokens_out": 1_332_000}
    assert _confirm_live_run("mock", 148, yes=False)
    assert _confirm_live_run("local", 1, yes=True)
    print("OK live 예상 호출·토큰 + --yes 게이트")


def test_ai_prohibited_assignment_is_policy_excluded():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        task = root / "task"
        task.mkdir()
        (task / "spec.md").write_text(
            "# 개인 보고서\n\nAI 사용 불가능. 반드시 자신의 의견으로 충분한 보고서를 작성하세요.",
            encoding="utf-8")
        context = task / "etl_context"
        context.mkdir()
        (context / "context.md").write_text("# 수업 자료\n\n핵심 개념 설명", encoding="utf-8")
        result = validate_one(root, _row("task", "99", "개인 보고서"))
        assert result["status"] == "excluded"
        assert result["checks"] == ["ai_use_prohibited"]
        assert result["policy"] == "ai_use_prohibited"
    print("OK AI 금지 과제는 실패 아닌 정책 제외")


def test_corpus_context_bundle_format_and_author_policy():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        cached = _write_course_context_cache(
            root, "42",
            [SimpleNamespace(name="3주차.pdf", url="https://example/files/3")],
            [SimpleNamespace(subject="과제 안내", body="본문", author="실명",
                             created_iso="2026-08-01", forum="공지",
                             url="https://example/ann/1")],
        )
        data = cached.read_text(encoding="utf-8")
        assert '"author": "unknown"' in data and "실명" not in data
        assert '"discussion_replies_included": false' in data

        task = root / "task"
        path = _write_assignment_context(task, [
            SourceDoc(title="[eTL 자료] 3주차", text="관련 본문", url="https://example/3")
        ])
        assert path == task / "etl_context" / "context.md"
        assert "관련 본문" in path.read_text(encoding="utf-8")
        empty = _write_assignment_context(root / "empty", [])
        assert "일치하는 과목 자료·공지가 없음" in empty.read_text(encoding="utf-8")
    print("OK 과목 캐시+과제별 컨텍스트 번들·작성자 역할 정책")


def _cli_telemetry(gate_env, *, salt: str = "corpus-algo-gate-test-salt") -> dict:
    """UNTIL_ALGO_VERSION을 지정(None=해제)하고 CLI 텔레메트리 1건을 만든다."""
    import os

    from run_corpus_validation import _telemetry_from
    from until.telemetry.schema import algo_gate

    old_gate = os.environ.get("UNTIL_ALGO_VERSION")
    old_salt = os.environ.get("UNTIL_PROJECT_SALT")
    os.environ["UNTIL_PROJECT_SALT"] = salt
    try:
        if gate_env is None:
            os.environ.pop("UNTIL_ALGO_VERSION", None)
        else:
            os.environ["UNTIL_ALGO_VERSION"] = gate_env
        local = {"strategy": "staged_writing", "task_type": "essay", "actionable": True,
                 "status": "passed", "failures": [], "checks": ["capture"]}
        return _telemetry_from(local, _row("task", "10", "관찰 실험 보고서"), {},
                               run_id="a3f1c2d4e5b60789", context_mode="full",
                               backend="mock", algo_version="1.10.0",
                               git_sha="63c7220", gate=algo_gate())
    finally:
        for key, value in (("UNTIL_ALGO_VERSION", old_gate),
                           ("UNTIL_PROJECT_SALT", old_salt)):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_cli_telemetry_carries_algo_gate():
    """CLI 코퍼스 원장도 게이트를 싣는다 — 웹 원장과 게이트 기준으로 함께 자르려면
    두 생산자가 같은 필드를 채워야 한다. 릴리스 SemVer 축은 그대로 유지된다."""
    assert _cli_telemetry(None)["algo_gate"] == "v0.1"
    assert _cli_telemetry("v0.1")["algo_gate"] == "v0.1"
    row = _cli_telemetry("v0.2")
    assert row["algo_gate"] == "v0.2" and row["algo_version"] == "1.10.0"
    print("OK CLI 텔레메트리 algo_gate 방출")


def test_cli_and_web_algo_gate_share_one_source():
    """두 생산자가 같은 함수를 부른다 — 로직이 복제되면 언젠가 값이 갈리고,
    갈라진 원장은 게이트 기준 교차 집계를 조용히 망친다."""
    import os

    from until.telemetry import web as telemetry_web
    from until.telemetry.schema import algo_gate

    # 웹 방출부가 참조하는 이름이 schema의 그 함수 객체와 동일해야 한다.
    assert telemetry_web.algo_gate is algo_gate
    # 현재 환경 그대로에서 CLI 레코드와 공용 함수가 같은 값을 낸다.
    assert _cli_telemetry(os.environ.get("UNTIL_ALGO_VERSION"))["algo_gate"] == algo_gate()
    print("OK CLI·웹 게이트 값 단일 출처")


def test_reference_kind_tolerance():
    # 실코퍼스 회귀 2건: (1) .hwp 제출(글쓰기 과제의 실제 1위 포맷)이 기대 형식
    # 집합에 없어 형식 불일치로 오판 (2) 수집 시 경로 길이 절단으로 확장자가
    # '.p'가 된 제출물이 형식 불일치로 기록 — 알 수 없는 확장자는 형식 판정이
    # 아니라 수집 결함(parse failure)으로 다뤄야 한다.
    from run_corpus_validation import reference_mismatch, KNOWN_REFERENCE_KINDS
    assert not reference_mismatch("staged_writing", ["hwp"])
    assert not reference_mismatch("activity_form", ["hwp"])
    assert "p" not in KNOWN_REFERENCE_KINDS
    assert not reference_mismatch("staged_writing", ["p"])  # 잘린 확장자
    assert not reference_mismatch("staged_writing", ["text"])  # 텍스트 폴백 중립
    assert reference_mismatch("zip_project", ["docx"])       # 진짜 불일치는 유지
    assert not reference_mismatch("evidence_report", ["docx"])  # 기대 없음 → 통과

    # 수집기 파일명 절단은 확장자를 보존해야 한다(원인 차단).
    from run_etl_corpus import _safe
    long_name = "Physics_4 실험보고서의 문제점 및 개선 방안 " * 3 + "2099-12345 홍길동_최종본.pdf"
    safe = _safe(long_name)
    assert safe.endswith(".pdf") and len(safe) <= 80, safe
    assert _safe("보고서.hwpx") == "보고서.hwpx"
    print("OK 제출 형식 관용·확장자 보존")


if __name__ == "__main__":
    test_validation_runs_legacy_unit_and_exclusion()
    test_corpus_context_bundle_format_and_author_policy()
    test_live_estimate_and_confirmation_gate()
    test_ai_prohibited_assignment_is_policy_excluded()
    test_cli_telemetry_carries_algo_gate()
    test_cli_and_web_algo_gate_share_one_source()
    test_reference_kind_tolerance()
    print("\nCORPUS VALIDATION TESTS PASS")
