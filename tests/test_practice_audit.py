"""과거 과제 연습 감사·하드 중단·화면 표시 회귀."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.practice_audit import (
    PracticePreflightError, audit_assignment, enforce_practice_preflight,
)


def test_capture_quality_and_policy():
    ok = audit_assignment(
        "마감 6/15. AI 사용 가능. 수업의 핵심 개념 두 가지를 비교하고 개인 사례에 "
        "적용한 보고서를 docx로 제출하세요. 근거와 한계를 함께 설명하세요.",
        attachment_count=1)
    assert ok.policy == "allowed" and ok.deadline_present and ok.formats == ["docx"]
    assert not ok.blockers
    unclear = audit_assignment("AI 활용 여부는 강의 원칙을 따릅니다. 보고서를 작성하세요.")
    assert unclear.policy == "unclear" and unclear.blockers
    denied = audit_assignment(
        "AI 사용 불가능. 본인의 경험과 수업 개념을 연결하여 충분히 긴 보고서를 작성하세요.")
    assert denied.policy == "prohibited" and "AI 사용을 금지" in denied.blockers[0]


def test_missing_material_role_and_measurement_block():
    audit = audit_assignment(
        "첨부파일을 참고하는 팀 과제입니다. 실험 결과 데이터를 분석하여 보고서를 작성하고 "
        "가설과 관찰의 차이, 오차 원인, 다음 실험의 개선 방향을 구체적으로 설명하세요.")
    joined = " ".join(audit.blockers)
    assert "첨부파일" in joined and "담당 범위" in joined and "결과값" in joined
    try:
        enforce_practice_preflight(audit)
        assert False, "blocker가 있으면 연습 시작 전 멈춰야 함"
    except PracticePreflightError as exc:
        assert len(exc.reasons) == 3


def test_practice_ui_hides_submission_and_shows_comparison():
    from until.config import Config
    from until.pipeline import run
    from until import web
    result = run(["examples/sample_assignment.txt"], Config())
    result.practice_mode = True
    assert "연습 모드" in web._submission_status_html("abc", result)
    assert "제출용 워드" not in web._submission_links("abc", result)
    web._WORKSPACES["abc"] = {"versions": ["이전 문장"], "excluded_sources": []}
    try:
        panel = web._version_compare_html("abc", result)
        assert "업데이트 전후 비교" in panel and "이전 결과 보기" in panel
        # 되돌릴 길은 조작 위젯이 아니라 안전장치라 남는다.
        assert "이전 버전으로 복원" in panel
        assert 'name="paragraph"' not in panel
    finally:
        web._WORKSPACES.pop("abc", None)


if __name__ == "__main__":
    test_capture_quality_and_policy()
    test_missing_material_role_and_measurement_block()
    test_practice_ui_hides_submission_and_shows_comparison()
    print("PRACTICE AUDIT TESTS PASS")
