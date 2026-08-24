"""형식 검증기 — 알리기 전에 고친다. 고친 것은 반드시 밝힌다.

이 시험이 지키는 계약 셋:
  1. 기계적으로 확실한 것만 고친다(표지·참고문헌·마커·인용범위·슬롯라벨).
  2. 고친 것은 `fix_note`로 화면에 밝힌다 — 몰래 고치면 학생이 자기가 쓴 줄 안다.
  3. 지어내지 않는다 — 표지의 학번은 프로필에 있을 때만, 참고문헌은 실제 자료만.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from until.execution.format_guard import (
    COVER_MARK,
    check_and_fix,
    fixed_notes,
    remaining_notes,
)


class _Doc:
    def __init__(self, text):
        self.text, self.source = text, "assignment.md"


class _Draft:
    def __init__(self, body):
        self.body = body


class _Result:
    def __init__(self, body, assignment="", sources=None, spec=None):
        self.documents = [_Doc(assignment)] if assignment else []
        self.draft = _Draft(body)
        self.final_draft = None
        self.sources = list(sources or [])
        self.spec = dict(spec or {})


def _kinds(issues, kind):
    return [i for i in issues if i.kind == kind]


def test_broken_decision_markers_are_repaired():
    """파손된 마커는 결정 지점으로 안 잡혀 **묻지 않고 지나간다** — 제일 나쁜 실패."""
    body = ("서론입니다. [DECISION: 어느 관점을 택할지]\n\n"
            "본론입니다. [[DECISION 결론 방향]]\n")
    out, issues = check_and_fix(_Result(body))
    assert out.count("[[DECISION:") == 2, out
    assert "[DECISION:" not in out.replace("[[DECISION:", "")
    got = _kinds(issues, "decision_marker")
    assert len(got) == 1 and got[0].fixed
    assert "2개" in got[0].message
    # 정상 마커는 건드리지 않는다.
    ok = "[[DECISION: 그대로]]"
    out2, issues2 = check_and_fix(_Result(ok))
    assert out2 == ok and not _kinds(issues2, "decision_marker")
    print("OK 파손된 결정 마커 복구")


def test_citations_beyond_source_count_are_demoted():
    """자료 2개인데 [자료5]는 지어낸 근거 — 지우지 않고 [출처?]로 낮춘다."""
    body = "첫 문장[자료1]. 둘째[자료2]. 셋째[자료5]. 넷째[자료0]."
    out, issues = check_and_fix(_Result(body, sources=["강의노트", "공지"]))
    assert "[자료1]" in out and "[자료2]" in out
    assert "[자료5]" not in out and "[자료0]" not in out
    assert out.count("[출처?]") == 2
    got = _kinds(issues, "citation_range")
    assert len(got) == 1 and got[0].fixed and "[자료5]" in got[0].message
    # 자료가 아예 없으면 모든 번호 인용이 근거 없음.
    out2, _ = check_and_fix(_Result("문장[자료1].", sources=[]))
    assert "[출처?]" in out2
    print("OK 범위 밖 인용 강등")


def test_internal_slot_labels_are_stripped():
    """'① 항목'은 Until 내부 슬롯 이름이지 과제 용어가 아니다(실사용 2026-08-23)."""
    body = "[[DECISION: '① 항목' 강의에서 본인의 고찰을 적으세요]]"
    out, issues = check_and_fix(_Result(body))
    assert "① 항목" not in out
    assert "강의에서 본인의 고찰" in out
    got = _kinds(issues, "slot_label")
    assert len(got) == 1 and got[0].fixed
    print("OK 내부 슬롯 라벨 제거")


def test_cover_is_added_with_profile_and_blanks():
    """표지의 학번·이름은 **아는 값만** 채우고 모르면 빈칸 DECISION."""
    assignment = "레포트 표지에 조와 조원분들의 이름과 학번을 추가해주시길 바랍니다."
    r = _Result("본문입니다.", assignment=assignment)
    out, issues = check_and_fix(r, profile={"name": "홍길동"})
    assert out.startswith(COVER_MARK)
    assert "- 이름: 홍길동" in out
    assert "- 학번: [[DECISION: 학번]]" in out, out
    assert "본문입니다." in out
    note = fixed_notes(issues)[0]
    assert "표지" in note and "프로필" in note

    # 두 번 돌려도 표지를 두 번 붙이지 않는다(재작성·finalize에서 다시 탄다).
    r2 = _Result(out, assignment=assignment)
    out2, issues2 = check_and_fix(r2, profile={"name": "홍길동"})
    assert out2.count(COVER_MARK) == 1
    assert not [i for i in issues2 if i.kind == "cover" and i.fixed]
    print("OK 표지 자동 삽입 · 빈칸 유지 · 멱등")


def test_cover_is_removed_when_assignment_forbids_it():
    """'표지 없이'라고 한 과제에 표지를 붙이면 검증기가 과제를 어긴다."""
    body = f"{COVER_MARK}\n- 이름: 홍길동\n\n본문입니다."
    r = _Result(body, assignment="파일로 eTL 제출 표지 없이 A4 8페이지 분량 이내로 작성하여")
    out, issues = check_and_fix(r)
    assert COVER_MARK not in out and "본문입니다." in out
    got = [i for i in _kinds(issues, "cover") if i.fixed]
    assert got and "표지를 뺐" in got[0].fix_note
    print("OK 표지 금지 시 제거")


def test_references_use_only_real_sources():
    """있지도 않은 문헌을 채우면 학문적 부정 — 자료가 없으면 절을 만들지 않는다."""
    assignment = "보고서 말미에 참고문헌을 반드시 표기하세요."
    out, issues = check_and_fix(
        _Result("본문.", assignment=assignment, sources=["3주차 강의노트", "실험 지침서"]))
    assert "## 참고문헌" in out
    assert "1. 3주차 강의노트" in out and "2. 실험 지침서" in out
    assert any(i.fixed and "참고문헌" in i.fix_note for i in issues)

    # 자료 0건이면 만들지 않고 알리기만.
    out2, issues2 = check_and_fix(_Result("본문.", assignment=assignment, sources=[]))
    assert "## 참고문헌" not in out2
    got = [i for i in _kinds(issues2, "references") if not i.fixed]
    assert got, issues2

    # 이미 참고문헌 절이 있으면 또 붙이지 않는다.
    out3, _ = check_and_fix(_Result("본문.\n\n## 참고문헌\n\n1. 내가 쓴 것",
                                    assignment=assignment, sources=["강의노트"]))
    assert out3.count("참고문헌") == 1
    print("OK 참고문헌 — 실제 자료만 · 없으면 안 만듦 · 멱등")


def test_unfixable_requirements_are_reported_not_guessed():
    """파일 형식·파일명·서식은 본문으로 못 지킨다 — 알리기만 한다."""
    assignment = ('보고서를 pdf파일로 제출하세요. 파일명을 "학번_이름" 으로 하여 '
                  '제출하세요. 글자 크기 11pt, 줄간격 160%')
    out, issues = check_and_fix(_Result("본문.", assignment=assignment))
    assert out == "본문.", "본문은 건드리지 않는다"
    by = {i.kind: i for i in issues}
    # 서식은 사람이 한글·워드에서 맞춘다 — 맞췄다고 말할 수 없다.
    assert not by["typography"].fixed
    # 파일 형식은 제출 버튼 순서로 실제 반영된다 — 반영된 것은 반영됐다고 말한다.
    assert by["file_type"].fixed and ".pdf" in by["file_type"].fix_note
    # 파일명은 프로필을 모르면 못 짓는다 → 그때만 사람에게 넘긴다.
    assert not by["file_name"].fixed and "프로필" in by["file_name"].message
    assert remaining_notes(issues)
    print("OK 반영된 것은 반영됐다고 · 남는 것만 사람에게")


def test_delivery_requirements_report_as_applied_when_they_are():
    """이미 맞춰 놓고 '제출 전에 맞출 것'이라 하면 진짜 남은 일이 묻힌다."""
    assignment = ('보고서를 pdf파일로 제출하세요. 파일명을 "학번_이름" 으로 하여 제출하세요.')
    _out, issues = check_and_fix(_Result("본문.", assignment=assignment),
                                 profile={"student_id": "2023-12345", "name": "홍길동"})
    by = {i.kind: i for i in issues}
    assert by["file_name"].fixed and "2023-12345_홍길동" in by["file_name"].fix_note
    assert not remaining_notes(issues), remaining_notes(issues)
    print("OK 프로필이 있으면 파일명도 '맞춰 뒀다'")


def test_no_rules_no_noise():
    """형식 요구가 없고 산출물도 멀쩡하면 아무 말도 하지 않는다."""
    body = "정상 본문입니다[자료1]. [[DECISION: 관점 선택]]"
    out, issues = check_and_fix(
        _Result(body, assignment="공백 포함 400자 이상 작성해주시기 바랍니다.",
                sources=["강의노트"]))
    assert out == body and issues == [], issues
    # 빈 본문에도 터지지 않는다.
    assert check_and_fix(_Result("")) == ("", [])
    print("OK 요구 없으면 침묵")


def test_submission_filename_follows_the_rule_or_stays_default():
    """`학번_이름.pdf`처럼 못 채운 칸이 남으면 이름을 아예 바꾸지 않는다."""
    from until.execution.format_guard import submission_filename
    assignment = ('보고서를 pdf파일로 제출하세요. 파일명을 "학번_이름" 으로 하여 '
                  '제출하세요.(예. 123456_홍길동.pdf)')
    r = _Result("본문.", assignment=assignment)
    assert submission_filename(r, "pdf", profile={"student_id": "2023-12345",
                                                  "name": "홍길동"}) == "2023-12345_홍길동.pdf"
    # 학번을 모르면 규칙 적용을 포기한다 — '학번_홍길동.pdf'를 주면 그대로 낸다.
    assert submission_filename(r, "pdf", profile={"name": "홍길동"}) == ""
    # 규칙이 없으면 기본 이름(빈 문자열 = 호출부가 기본값 유지).
    assert submission_filename(_Result("본문.", assignment="본문만 있음"), "pdf",
                               profile={"name": "홍길동"}) == ""
    print("OK 제출 파일명 — 규칙대로 · 못 채우면 기본값")


def test_readiness_reports_fixed_and_remaining():
    """고친 것도 반드시 알린다 — 몰래 고치면 학생이 자기가 쓴 줄 안다."""
    from until.readiness import _format_items
    assignment = ('레포트 표지에 이름과 학번을 추가해주세요. '
                  '보고서를 pdf파일로 제출하세요. 한글 기준 11pt, 줄간격 160%')
    r = _Result("본문입니다.", assignment=assignment)
    body, issues = check_and_fix(r, profile={"name": "홍길동", "student_id": "2023-12345"})
    r.format_issues = issues
    labels = _format_items(r)
    assert [i.status for i in labels] == ["info", "warn"], labels
    assert "표지" in labels[0].message and "맞춰 뒀" in labels[0].message
    # 남는 것은 사람이 한글·워드에서 맞출 서식뿐 — 이미 맞춘 pdf는 여기 없어야 한다.
    assert "11pt" in labels[1].message and "pdf" not in labels[1].message.lower()

    # 아무 문제가 없으면 점검 줄도 없다.
    clean = _Result("본문.", assignment="공백 포함 400자 이상 작성")
    clean.format_issues = check_and_fix(clean)[1]
    assert _format_items(clean) == []
    print("OK readiness에 고친 것·남은 것 표면화")


if __name__ == "__main__":
    test_broken_decision_markers_are_repaired()
    test_citations_beyond_source_count_are_demoted()
    test_internal_slot_labels_are_stripped()
    test_cover_is_added_with_profile_and_blanks()
    test_cover_is_removed_when_assignment_forbids_it()
    test_references_use_only_real_sources()
    test_unfixable_requirements_are_reported_not_guessed()
    test_delivery_requirements_report_as_applied_when_they_are()
    test_no_rules_no_noise()
    test_submission_filename_follows_the_rule_or_stays_default()
    test_readiness_reports_fixed_and_remaining()
    print("\nFORMAT GUARD TESTS PASS")
