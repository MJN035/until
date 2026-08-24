from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from until.policy_hierarchy import PolicyLayer, PolicySource, resolve_policy
from until.policy_profiles import snu_2026_baseline
from until.policy_compiler import compile_policy, compile_policy_layer
from until.academic_graph import build_assignment_graph
from until.control_tower import inspect_assignment


def _source(key):
    return PolicySource(key, key, f"https://example.edu/{key}")


def test_assignment_controls_use_but_cannot_waive_university_floor():
    course = PolicyLayer(
        "course", "c1", "limited", allowed_uses=("brainstorming",),
        required_actions=("course_disclosure",), source=_source("course"))
    assignment = PolicyLayer(
        "assignment", "a1", "allowed", allowed_uses=("drafting",),
        source=_source("assignment"))
    policy = resolve_policy([snu_2026_baseline(), course, assignment])
    assert policy.ai_use == "allowed" and policy.controlling_scope == "assignment"
    assert policy.allowed_uses == ("drafting",)
    assert "no_sensitive_or_private_data_upload" in policy.hard_constraints
    assert "disclose_ai_use" in policy.required_actions
    assert policy.executable
    print("OK 과제별 허용 범위 우선·대학 안전 의무 누적")


def test_course_policy_fills_assignment_silence():
    policy = resolve_policy([
        snu_2026_baseline(),
        PolicyLayer("course", "c1", "limited", allowed_uses=("brainstorming",),
                    source=_source("course")),
        PolicyLayer("assignment", "a1", "unclear", source=_source("assignment")),
    ])
    assert policy.ai_use == "limited"
    assert policy.allowed_uses == ("brainstorming",)
    assert policy.controlling_scope == "course"
    print("OK 과제 침묵 시 강의계획서 정책 상속")


def test_prohibition_and_tool_restrictions_accumulate():
    policy = resolve_policy([
        PolicyLayer("institution", "u", approved_tools=("edu", "local"),
                    prohibited_uses=("personal_data",), source=_source("u")),
        PolicyLayer("course", "c", "limited", allowed_uses=("brainstorming", "exam"),
                    prohibited_uses=("exam",), approved_tools=("local", "other"),
                    source=_source("c")),
    ])
    assert policy.allowed_uses == ("brainstorming",)
    assert policy.approved_tools == ("local",)
    assert policy.prohibited_uses == ("personal_data", "exam")
    print("OK 금지·도구 제한은 하위에서 넓힐 수 없음")


def test_same_scope_conflict_fails_closed():
    policy = resolve_policy([
        PolicyLayer("assignment", "a", "allowed", source=_source("prompt")),
        PolicyLayer("assignment", "a", "prohibited", source=_source("rubric")),
    ])
    assert not policy.executable
    assert policy.ai_use == "unclear"
    assert policy.conflicts[0].code == "same_scope_ai_conflict"
    print("OK 동일 범위 충돌은 추측하지 않고 차단")


def test_compiled_course_policy_drives_control_tower():
    course = compile_policy_layer(
        "AI 사용은 아이디어 구상에만 허용하며 사용 사실을 명시하세요.",
        scope="course", scope_id="c1", source_id="syllabus",
        title="강의계획서")
    effective = resolve_policy([snu_2026_baseline(), course])
    graph = build_assignment_graph([{"id": "a1", "title": "a1", "course_id": "c1"}])
    report = inspect_assignment(
        "a1", policy=compile_policy(""), effective_policy=effective,
        graph=graph, memory=[])
    codes = {finding.code for finding in report.findings}
    assert "ai_policy_unclear" not in codes
    assert "required_action:disclose_ai_use" in codes
    print("OK 강의계획서 상속 정책이 실제 관제실 판단을 구동")


if __name__ == "__main__":
    test_assignment_controls_use_but_cannot_waive_university_floor()
    test_course_policy_fills_assignment_silence()
    test_prohibition_and_tool_restrictions_accumulate()
    test_same_scope_conflict_fails_closed()
    test_compiled_course_policy_drives_control_tower()
