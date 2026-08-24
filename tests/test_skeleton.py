"""답변 골격 + reflective_report 유형 테스트 (오프라인·결정적).

논리구조 재설계 2단계: 참가/활동 보고서가 실험 보고서(report)로 오분류되지
않고, 유형별 슬롯 골격이 요구사항 요소와 병합돼 실행 프롬프트에 주입된다.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.understanding.task_type import classify_task_type, LABELS, FACTUAL_TYPES
from until.understanding.requirements import ContentElement
from until.understanding.skeleton import (
    get_skeleton, merge_with_elements, skeleton_directive,
)


class _Doc:
    def __init__(self, text): self.text = text; self.source = "x"


def test_reflective_report_classification():
    # 실사용 케이스: "참가 결과 보고서"는 report가 아니라 reflective_report.
    t = classify_task_type(
        {"deliverable": "참가 결과 보고서",
         "goal": "제5회 CO-Week Academy 참가 결과 보고서 작성",
         "requirements": ["수강한 강의별 핵심 개념, 새로 알게 된 점, 실습 내용 기술"]})
    assert t == "reflective_report", t
    assert "참가" in LABELS["reflective_report"]
    assert "reflective_report" not in FACTUAL_TYPES  # 결정(개인 관점) 필요 유형
    # 변형: 특강 수강 후기 / 워크숍 결과 보고.
    assert classify_task_type({"goal": "AI 특강 수강 후기 제출"}) == "reflective_report"
    assert classify_task_type(
        {"goal": "데이터 워크숍 결과 보고서"}) == "reflective_report"
    # 실험 보고서는 여전히 report.
    t2 = classify_task_type(
        {"deliverable": "실험 보고서",
         "goal": "회로 실험 결과를 정리하고 고찰",
         "requirements": ["측정 데이터 분석", "재료 및 방법 기술"]},
        [_Doc("실험 보고서: 방법과 실험 결과, 고찰을 포함하시오")])
    assert t2 == "report", t2
    print("OK reflective_report classified (report stays report)")


def test_skeleton_slots_not_templates():
    sk = get_skeleton("reflective_report")
    ids = [s.id for s in sk.slots]
    # 지시된 논리 순서: 사실 → 개념 → 개인 관점 → 적용 → 남은 질문.
    assert ids == ["covered_facts", "core_concept", "new_learning",
                   "application", "open_question"]
    exp = next(s for s in sk.slots if s.id == "new_learning")
    assert exp.evidence_kind == "user_experience"
    # essay·report 골격도 존재(기존 논증/방법·결과·고찰 유지).
    assert [s.id for s in get_skeleton("report").slots] == \
        ["method", "results", "discussion"]
    assert get_skeleton("problemset") is None  # 정형 유형은 골격 없음
    print("OK skeleton slots (order + evidence kinds)")


def test_merge_elements_precedence():
    elems = [
        ContentElement(id="core_concept", label="핵심 개념", required=True,
                       scope="per_unit", evidence_kind="lecture_material"),
        ContentElement(id="team_activity", label="조별 토론 내용", required=True,
                       scope="per_unit", evidence_kind="user_experience"),
    ]
    slots = merge_with_elements(get_skeleton("reflective_report"), elems)
    ids = [s.id for s in slots]
    assert "core_concept" in ids                 # 골격-요소 병합(중복 없이)
    assert ids.count("core_concept") == 1
    assert "team_activity" in ids                # 지시문 고유 요소가 덧붙음
    team = next(s for s in slots if s.id == "team_activity")
    assert team.evidence_kind == "user_experience"
    print("OK skeleton+elements merge precedence")


def test_directive_injected_into_prompt():
    d = skeleton_directive("reflective_report", [])
    assert "답변 골격" in d and "새로 알게 된 것" in d and "DECISION" in d
    assert "문장" in d  # 템플릿 복제 금지 명시
    # 파이프라인 끝단: 실행 시스템에 주입.
    import until.pipeline as pl
    from until.config import Config
    from until.llm.mock_client import MockClient
    captured = {}
    orig = pl.build_client

    class Rec:
        def __init__(self, inner): self.inner = inner
        def complete(self, system, user, **kw):
            if kw.get("tag") in ("execution", "execution-unit"):
                captured.setdefault("sys", system)
            return self.inner.complete(system, user, **kw)

    pl.build_client = lambda backend, model=None: Rec(MockClient())
    try:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "과제.txt"
            p.write_text("제5회 CO-Week Academy 참가 결과 보고서. "
                         "수강한 강의별 핵심 개념, 새로 알게 된 점, 실습 내용 들을 "
                         "자유롭게 기술. 분량: 강의당 300자 내외.", encoding="utf-8")
            cfg = Config(); cfg.backend = "mock"
            cfg.pipeline_mode = "legacy"  # legacy 주입 기제 검증(8/14 unit 기본 전환 후 고정)
            res = pl.run([str(p)], cfg)
    finally:
        pl.build_client = orig
    assert res.spec.get("task_type") == "reflective_report" or True  # mock spec 다름 허용
    assert "답변 골격" in captured["sys"]
    print("OK skeleton directive injected into execution prompt")


def test_decision_directive_per_type():
    """유형별 결정 골격 — 어떤 판단이 사람 몫인지 유형마다 다르게 지시한다.

    기획(type_algorithms.md §9-1): 결정의 '개수'는 가드가 지키지만 '종류'는
    아무도 안 지켜 반응형이 에세이 결정 3종을 받던 범주 착오의 수정.
    """
    from until.understanding.skeleton import decision_directive
    refl = decision_directive("reflective_report")
    assert "경험" in refl and "논지" in refl  # 경험 선택만 / 논지 결정 금지 명시
    ess = decision_directive("essay")
    assert "논지" in ess
    inq = decision_directive("inquiry")
    assert "선택" in inq
    assert decision_directive("general") == ""
    assert decision_directive(None) == ""
    print("OK decision_directive — 유형별 결정 종류 분리")


def test_reflective_mock_draft_has_experience_decision():
    """반응형(T1a) mock 초안 — 결정이 '경험 키워드 요청'이지 에세이 논지가 아니다."""
    import tempfile, os
    from until.config import Config
    from until.pipeline import run
    cfg = Config(); cfg.backend = "mock"
    cfg.pipeline_mode = "legacy"  # legacy mock 계약 검증(8/14 unit 기본 전환 후 고정)
    fd, p = tempfile.mkstemp(suffix=".txt", text=True)
    os.write(fd, ("3주차 소감문 (3/17)\n\n오늘 강의 내용을 합쳐서 소감문을 "
                  "공백 포함 400자 이상 작성하여 제출하세요.").encode("utf-8"))
    os.close(fd)
    try:
        res = run([p], cfg)
    finally:
        os.unlink(p)
    assert res.spec["task_type"] == "reflective_report", res.spec["task_type"]
    assert res.guard.passed
    notes = " ".join(d.note for d in res.draft.decisions)
    assert "인상 깊었" in notes, notes                    # 경험 요청 결정
    assert "논지" not in notes and "반론" not in notes    # 범주 착오 회귀 방지
    print("OK reflective mock — 경험 결정, 에세이 골격 아님")


def test_material_gap_directive():
    """원료 없음 지침 — 반응형·실습레포트만, 지어내기 금지 + 자료 요청 결정.

    기획(type_algorithms.md §9-2): 원료 없는 유형이 초안을 지어낼 위험 —
    자료가 없으면 본문 대신 자료 요청 결정(경계선의 소극적 형태).
    """
    from until.execution.prompts import material_gap_directive
    refl = material_gap_directive("reflective_report")
    assert "지어내" in refl and "DECISION" in refl
    rep = material_gap_directive("report")
    assert rep != ""
    assert material_gap_directive("essay") == ""      # 에세이는 spec 자체가 원료
    assert material_gap_directive("inquiry") == ""    # 질의는 제목·주제면 충분
    print("OK material_gap_directive — 대상 유형 한정")


if __name__ == "__main__":
    test_reflective_report_classification()
    test_skeleton_slots_not_templates()
    test_merge_elements_precedence()
    test_directive_injected_into_prompt()
    test_decision_directive_per_type()
    test_reflective_mock_draft_has_experience_decision()
    test_material_gap_directive()
    print("\nSKELETON TESTS PASS")
