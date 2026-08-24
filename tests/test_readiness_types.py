# -*- coding: utf-8 -*-
"""과제 유형별 제출 점검 — 웹 유저도 받는 결정적 검증.

로컬 에이전트 런타임에만 있던 검증기 중 **모델이 필요 없는 것**을 웹 경로의
제출 점검(readiness)으로 옮겼다. 웹앱 유저는 CLI·WSL을 쓸 수 없으므로 런타임
플러그인의 가치를 못 받고 있었는데, 이 판정들은 순수 함수라 그대로 이동한다.

두 표면이 **같은 규칙**을 쓰는 것이 핵심이다 — 다르면 "CLI에선 걸리는데
웹에선 안 걸린다"가 된다. 그래서 판정기(`runtime.grounding`,
`presentation_export.parse_slide_markdown`)를 공유한다.

옮기지 못한 것은 **코드 실행**뿐이다. 서버에서 학생 코드를 돌리는 건 별개의
인프라·보안 문제다.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.boundary.models import Draft
from until.context.assignment_router import AssignmentRoute
from until.execution.boundary_guard import GuardReport
from until.pipeline import Result
from until.readiness import assess_readiness


def _routed(body, strategy, *, sources=None, answers=()):
    """라우팅 전략이 붙은 Result — 유형별 점검은 전략을 보고 켜진다."""
    result = Result(documents=[], spec={"title": "T"},
                    draft=Draft.from_text(body),
                    guard=GuardReport(passed=True, attempts=1, reasks=0),
                    sources=list(sources or []))
    result.assignment_route = AssignmentRoute(strategy, "fixture", ())
    for point, answer in zip(result.draft.decisions, answers, strict=False):
        point.human_input = answer
    return result


def _labels(result):
    return {item.label: item for item in assess_readiness(result).items}


DECISION = "[[DECISION: 방향을 어디로 — 본인 판단]]"


def test_python_code_block_syntax_is_checked():
    """제출 문서 안의 코드가 문법부터 깨져 있으면 알려 준다."""
    broken = _routed("설명.\n\n```python\ndef f(:\n    pass\n```\n" + DECISION,
                     "code_project")
    item = _labels(broken)["코드"]
    assert item.status == "warn" and "문법" in item.message, item.message

    ok = _routed("설명.\n\n```python\ndef f():\n    return 1\n```\n" + DECISION,
                 "code_project")
    assert "코드" not in _labels(ok)

    # 언어를 안 밝힌 블록은 의사코드·출력 예시일 수 있으므로 검사하지 않는다.
    pseudo = _routed("```\nif x then y\n```\n" + DECISION, "code_project")
    assert "코드" not in _labels(pseudo)
    print("OK 코드 블록 문법 점검(웹)")


def test_invented_run_results_are_flagged_but_code_constants_are_not():
    """돌려 보지 않고 적은 결과 수치만 잡는다 — 코드 안의 상수는 아니다."""
    claimed = _routed("실행 시간은 0.42초였다.\n" + DECISION, "code_project")
    item = _labels(claimed)["실행결과"]
    assert item.status == "warn"
    # 같은 값을 여러 패턴이 겹쳐 잡아도 **한 건**으로 센다.
    assert "1건" in item.message, item.message

    inside = _routed("```python\ntimeout = 30\n```\n" + DECISION, "code_project")
    assert "실행결과" not in _labels(inside)

    # 산문 과제에는 걸지 않는다 — '매출 3배'까지 잡으면 노이즈가 된다.
    prose = _routed("매출이 3배 늘었다.\n" + DECISION, "evidence_report")
    assert "실행결과" not in _labels(prose)
    print("OK 지어낸 실행 결과 점검(웹)")


def test_slide_structure_is_checked_for_presentations():
    thin = _routed("## 슬라이드 1: 하나\n- a\n" + DECISION,
                   "presentation_conversion")
    item = _labels(thin)["발표"]
    assert item.status == "warn" and "3장" in item.message, item.message

    good = _routed("## 슬라이드 1: A\n- a\n\n## 슬라이드 2: B\n- b\n\n"
                   "## 슬라이드 3: C\n- c\n" + DECISION,
                   "presentation_conversion")
    assert "발표" not in _labels(good)

    empty = _routed("## 슬라이드 1: A\n- a\n\n## 슬라이드 2: B\n\n"
                    "## 슬라이드 3: C\n- c\n" + DECISION,
                    "presentation_conversion")
    assert "내용이 없는" in _labels(empty)["발표"].message
    print("OK 발표 구조 점검(웹)")


def test_activity_form_flags_facts_the_student_never_gave():
    """방향이 반대인 점검 — '덜 썼다'가 아니라 '모르는 걸 썼다'를 잡는다."""
    invented = _routed("3명이 참여했다.\n[[DECISION: 소감 — 본인만 씀]]",
                       "activity_form")
    assert _labels(invented)["활동기록"].status == "warn"

    # 학생이 결정 답변으로 알려 준 사실은 근거가 있으므로 통과한다.
    grounded = _routed("3명이 참여했다.\n[[DECISION: 참여자 — 실제 인원]]",
                       "activity_form", answers=("3명이 참여했다",))
    assert "활동기록" not in _labels(grounded)
    print("OK 활동 기록 사실 점검(웹)")


def test_unrelated_assignments_get_no_extra_noise():
    """유형이 아니면 새 점검이 하나도 늘지 않는다 — 기존 화면이 시끄러워지면 안 된다."""
    plain = _routed("본문. " * 30 + DECISION, "evidence_report")
    labels = set(_labels(plain))
    for added in ("코드", "실행결과", "발표", "활동기록"):
        assert added not in labels, added
    print("OK 무관한 과제에는 항목이 늘지 않는다")


def test_web_and_runtime_share_the_same_rule():
    """같은 본문을 웹 점검과 런타임 검증기에 넣으면 같은 판단이 나와야 한다."""
    from until.runtime.grounding import ACTIVITY_PATTERNS, ungrounded_numbers

    body = "3명이 참여했다."
    assert ungrounded_numbers(body, [], ACTIVITY_PATTERNS)          # 런타임 쪽
    assert "활동기록" in _labels(_routed(body + "\n" + DECISION, "activity_form"))
    # 근거를 주면 양쪽 다 조용해진다.
    assert not ungrounded_numbers(body, ["참여자 3명"], ACTIVITY_PATTERNS)
    print("OK 웹·런타임이 같은 규칙을 쓴다")


TESTS = [
    test_python_code_block_syntax_is_checked,
    test_invented_run_results_are_flagged_but_code_constants_are_not,
    test_slide_structure_is_checked_for_presentations,
    test_activity_form_flags_facts_the_student_never_gave,
    test_unrelated_assignments_get_no_extra_noise,
    test_web_and_runtime_share_the_same_rule,
]

if __name__ == "__main__":
    for case in TESTS:
        case()
    print("\nREADINESS TYPE TESTS PASS")
