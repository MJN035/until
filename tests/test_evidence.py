"""근거 원장·충분성 판정 테스트 (오프라인·결정적) — 재설계 4단계.

수용 기준 1·2의 기반: 근거 없는 user_experience는 absent(생성 금지 → 구체적
질문), 사용자가 한 줄 답하면 user_input 근거로 충족된다.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.context.evidence import (
    EvidenceLedger, absent_decision_question, build_ledger, sufficiency,
)
from until.execution.units import ResponseUnit
from until.understanding.skeleton import SkeletonSlot
from until.llm.base import SourceDoc

_UNIT = ResponseUnit(index=1, title="생성형 인공지능과 산업의 재편",
                     meta={"분야": "AI", "수강 일시": "2026-07-01 10:00"})

_DOCS = [
    SourceDoc(title="과제: 참가결과보고서_양식.hwpx",
              text="제5회 CO-Week Academy 참가 결과 보고서. 분량 제한: 강의당 300자 내외."),
    SourceDoc(title="[수업자료] 수강내역.txt",
              text=("1) 분야 AI · 강좌명 '생성형 인공지능과 산업의 재편' · 2026-07-01\n"
                    "강의 요지: 생성형 모델의 제조·금융 적용 사례, 도메인 데이터가 "
                    "가치 사슬을 재편하는 구조를 다룸.\n"
                    "2) 분야 데이터 · 강좌명 '데이터 윤리' · 2026-07-02\n"
                    "차등 프라이버시와 동의 설계.")),
]


def test_ledger_is_unit_scoped():
    led = build_ledger(_UNIT, _DOCS)
    assert led.items, "단위 제목 매칭 근거가 있어야"
    top = led.items[0]
    assert "생성형" in top.excerpt and top.kind == "etl_material"
    # 다른 강의(데이터 윤리) 라인은 이 단위의 상위 근거가 아니다(단위별 검색).
    assert not any("차등 프라이버시" in i.excerpt and i.relevance >= top.relevance
                   for i in led.items[1:])
    print("OK ledger is unit-scoped")


def test_user_experience_never_satisfied_by_docs():
    led = build_ledger(_UNIT, _DOCS)  # 자료는 풍부하지만 user_input 없음
    assert sufficiency(led, "user_experience") == "absent"
    # 사람이 한 줄 답하면 → user_input 근거 → 충족(수용 기준 2).
    led2 = build_ledger(_UNIT, _DOCS, user_answers={
        "생성형 인공지능과 산업의 재편 강의에서 인상 깊었던 점?":
            "스마트 팩토리 사례에서 검사 공정이 절반으로 준 게 인상적이었음"})
    assert led2.of_kinds({"user_input"})
    assert sufficiency(led2, "user_experience") == "sufficient"
    print("OK user_experience: docs never satisfy, one-line answer does")


def test_doc_kinds_sufficiency_ladder():
    led = build_ledger(_UNIT, _DOCS)
    assert sufficiency(led, "lecture_material") == "sufficient"
    thin = EvidenceLedger(items=[])
    assert sufficiency(thin, "lecture_material") == "absent"
    assert sufficiency(thin, "general_knowledge") == "sufficient"
    print("OK sufficiency ladder (sufficient/thin/absent)")


def test_absent_question_is_specific():
    from until.understanding.length_target import LengthTarget
    unit = ResponseUnit(index=2, title="AI 에이전트 시대의 건설산업",
                        meta={"수강 일시": "2026-07-03"},
                        length_target=LengthTarget(unit="자", min=270, max=330,
                                                   per_item="강의"))
    slot = SkeletonSlot("new_learning", "새로 알게 된 점",
                        evidence_kind="user_experience")
    q = absent_decision_question(unit, slot)
    # 좋은 질문 요건: 어떤 강의인지 특정 + 한 줄 안내 + 무엇이 채워지는지.
    assert "AI 에이전트 시대의 건설산업" in q and "2026-07-03" in q
    assert "한 줄" in q and "채웁니다" in q
    assert "알려주세요" not in q  # 나쁜 예(막연한 요청) 아님
    print("OK absent question is specific (names lecture + why + fill)")


if __name__ == "__main__":
    test_ledger_is_unit_scoped()
    test_user_experience_never_satisfied_by_docs()
    test_doc_kinds_sufficiency_ladder()
    test_absent_question_is_specific()
    print("\nEVIDENCE TESTS PASS")
