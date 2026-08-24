# -*- coding: utf-8 -*-
"""과제 유형별 런타임 플러그인 — 코드 · 발표 · 활동 양식.

산출물 모양이 다르면 검증기도 달라야 한다. 보고서 검증기(분량·인용)를 코드나
양식에 들이대면 "검증 통과"가 거짓말이 된다 — 마크다운 초안 하나 써 놓고
통과시켜 버린다. 이 스위트는 각 플러그인이 **자기 산출물에 맞는 것만** 보고,
맡을 수 없는 유형은 **정직하게 거절**하는지 지킨다.

특히 두 가지를 못 박는다.
  - `hdl_lab`·`rmd_notebook`은 아무도 맡지 않는다. 파형·통계 출력은 도구를
    실제로 돌려야 나오고, 커널에 실행 엔진이 없는 지금 통과를 주면 CLAUDE.md가
    금지한 수치 날조를 제품이 승인하는 꼴이 된다.
  - 활동 양식은 **모르는 사실을 채우면 막는다**. 다른 런타임과 방향이 반대다.

전부 오프라인 — 프로세스도 네트워크도 없다.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from until.context.assignment_router import AssignmentRoute
from until.policy_hierarchy import PolicyLayer, resolve_policy
from until.runtime.code_runtime import CodeRuntime
from until.runtime.form_runtime import FILLED_RELPATH, FormRuntime
from until.runtime.models import AgentReceipt, RuntimeRequest
from until.runtime.presentation_runtime import SLIDES_RELPATH, PresentationRuntime
from until.runtime.registry import RuntimeRegistry
from until.runtime.workspace import WorkspaceManager

OK = AgentReceipt("succeeded")


def _request(strategy, spec=None, decisions=None):
    return RuntimeRequest(
        "a1", {"title": "과제", **(spec or {})},
        AssignmentRoute(strategy, "fixture", ()),
        resolve_policy((PolicyLayer("assignment", "a1", ai_use="allowed"),)),
        (), decisions or {})


def _materialize(runtime, request, root):
    """오케스트레이터와 **같은 방식**으로 작업공간을 만든다.

    allowed_commands를 빼먹으면 실행 단계를 쓰는 플러그인이 여기서만 죽는다 —
    테스트 도우미가 실제 경로와 다르면 테스트가 실제를 못 지킨다."""
    from until.runtime.security import kernel_allowed

    plan = runtime.prepare(request)
    allowed = kernel_allowed(tuple(getattr(runtime, "allowed_commands", ()) or ()))
    workspace = WorkspaceManager(root).materialize(
        runtime.name, request, plan, allowed)
    runtime.build_job(workspace)
    return workspace


def _write(workspace, relpath, text):
    path = Path(workspace.root) / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _codes(result):
    return {f.code for f in result.findings}


# ── 라우팅 ──────────────────────────────────────────────────────────
def test_each_strategy_goes_to_the_right_runtime():
    from until.runtime.cli import build_plugins

    registry = RuntimeRegistry(tuple(build_plugins()))
    expected = {
        "evidence_report": "report", "reflective_series": "report",
        "problem_set": "report", "team_project": "report",
        "code_project": "code", "zip_project": "code",
        "presentation_conversion": "presentation",
        "activity_form": "form",
    }
    for strategy, name in expected.items():
        selection = registry.select(_request(strategy))
        assert selection.plugin is not None, (strategy, selection.decision.reason)
        assert selection.plugin.name == name, (strategy, selection.plugin.name)
    print("OK 라우팅 — 유형마다 맞는 런타임")


def test_execution_dependent_types_are_refused_not_faked():
    """실행해야 나오는 산출물은 아무도 맡지 않는다 — 거절이 정답이다."""
    from until.runtime.cli import build_plugins

    registry = RuntimeRegistry(tuple(build_plugins()))
    for strategy in ("hdl_lab", "rmd_notebook"):
        selection = registry.select(_request(strategy))
        assert selection.plugin is None, (
            f"{strategy}를 맡아 버렸다 — 실행 엔진 없이 통과를 주면 수치 날조를 "
            "승인하는 것이다")
    print("OK 실행 의존 유형 — 가짜로 통과시키지 않고 거절")


# ── 코드 런타임 ─────────────────────────────────────────────────────
def test_code_runtime_blocks_syntax_errors():
    runtime = CodeRuntime()
    request = _request("code_project", {"expected_files": ["solution.py"]})
    with tempfile.TemporaryDirectory() as raw:
        workspace = _materialize(runtime, request, Path(raw) / "wr")
        _write(workspace, "work/solution.py", "def f(:\n    pass\n")
        _write(workspace, "work/NOTES.md",
               "[[DECISION: 자료구조 선택 — 트레이드오프는 본인 판단]]\n")
        result = runtime.validate(workspace, OK)
    assert "syntax_error" in _codes(result) and result.blocked
    print("OK 코드 — 문법 오류 차단")


def test_code_runtime_blocks_emptied_starter_files():
    """'테스트가 안 통과하니 테스트를 지운다'를 막는다."""
    runtime = CodeRuntime()
    request = _request("code_project", {"expected_files": ["solution.py"],
                                        "preserve_files": ["test_solution.py"]})
    with tempfile.TemporaryDirectory() as raw:
        workspace = _materialize(runtime, request, Path(raw) / "wr")
        _write(workspace, "work/solution.py", "def f():\n    return 1\n")
        _write(workspace, "work/NOTES.md", "[[DECISION: 알고리즘 선택 — 본인 판단]]\n")
        _write(workspace, "work/test_solution.py", "   \n")     # 비워 버림
        result = runtime.validate(workspace, OK)
    assert "starter_emptied" in _codes(result) and result.blocked
    print("OK 코드 — 스켈레톤·테스트 비우기 차단")


def test_code_runtime_blocks_invented_benchmark_numbers():
    """실행하지 않고 적은 결과 수치를 막는다(CLAUDE.md 수치 날조 금지)."""
    runtime = CodeRuntime()
    request = _request("code_project", {"expected_files": ["solution.py"]})
    with tempfile.TemporaryDirectory() as raw:
        workspace = _materialize(runtime, request, Path(raw) / "wr")
        _write(workspace, "work/solution.py", "def f():\n    return 1\n")
        _write(workspace, "work/NOTES.md",
               "측정 결과 실행 시간은 0.42초였다.\n"
               "[[DECISION: 최적화 방향 — 본인 판단]]\n")
        result = runtime.validate(workspace, OK)
    assert "ungrounded_measurement" in _codes(result), _codes(result)
    print("OK 코드 — 지어낸 실행 결과 차단")


def test_code_runtime_passes_and_says_it_did_not_run_the_code():
    """통과 문구가 '동작이 맞다'로 읽히면 안 된다 — 실행하지 않았기 때문."""
    runtime = CodeRuntime()
    request = _request("code_project", {"expected_files": ["solution.py"]})
    with tempfile.TemporaryDirectory() as raw:
        workspace = _materialize(runtime, request, Path(raw) / "wr")
        _write(workspace, "work/solution.py", "def f(x):\n    return x + 1\n")
        _write(workspace, "work/NOTES.md",
               "[[DECISION: 예외 처리 범위 — 본인 판단]]\n")
        result = runtime.validate(workspace, OK)
        assert not result.blocked, _codes(result)
        message = next(f.message for f in result.findings if f.code == "code_ok")
        assert "돌리지 않았습니다" in message, message
        bundle = runtime.package(workspace, result)
        assert [f.path for f in bundle.files] == ["work/solution.py"]
    print("OK 코드 — 통과해도 '실행하지 않았다'를 밝힌다")


def test_code_runtime_requires_a_human_decision():
    runtime = CodeRuntime()
    request = _request("code_project", {"expected_files": ["solution.py"]})
    with tempfile.TemporaryDirectory() as raw:
        workspace = _materialize(runtime, request, Path(raw) / "wr")
        _write(workspace, "work/solution.py", "def f():\n    return 1\n")
        _write(workspace, "work/NOTES.md", "다 정했습니다.\n")
        result = runtime.validate(workspace, OK)
    assert "boundary_crossed" in _codes(result)
    print("OK 코드 — 사람 몫을 안 남기면 차단")


# ── 실행 엔진 ───────────────────────────────────────────────────────
def test_kernel_ceiling_ignores_what_a_plugin_asks_for():
    """플러그인이 셸을 달라고 해도 열리지 않는다.

    실행 단계의 안전선은 두 겹이다: 플러그인이 명령을 선언하고, 커널이 자기
    천장으로 다시 거른다. 천장이 없으면 플러그인 한 줄로 임의 실행이 열린다."""
    from until.runtime.security import kernel_allowed

    asked = ("python", "pytest", "sh", "bash", "cmd", "curl", "git", "powershell")
    assert kernel_allowed(asked) == ("python", "pytest"), kernel_allowed(asked)
    print("OK 실행 엔진 — 커널 천장이 플러그인 선언을 다시 거른다")


def test_steps_are_declared_before_the_agent_runs():
    """명령은 plan에 박혀 있고, 에이전트가 쓴 파일이 명령줄이 되지 않는다."""
    runtime = CodeRuntime()
    request = _request("code_project", {"expected_files": ["solution.py"],
                                        "preserve_files": ["test_solution.py"]})
    plan = runtime.prepare(request)
    assert plan.runnable and len(plan.steps) == 1
    argv = plan.steps[0].argv
    assert argv[:4] == ("python", "-m", "pytest", "-q"), argv
    assert plan.steps[0].network is False

    # 테스트가 없다고 판단되면 아예 안 돌린다 — "테스트 0개 통과" 초록불 방지.
    bare = CodeRuntime().prepare(_request("code_project",
                                          {"expected_files": ["solution.py"]}))
    assert not bare.runnable and bare.steps == ()
    print("OK 실행 엔진 — 단계는 사전 선언, 테스트 없으면 안 돌린다")


def test_failed_tests_block_but_unrunnable_tests_only_warn():
    """못 돌린 것과 실패한 것을 구분한다 — 섞으면 멀쩡한 코드를 고치게 된다."""
    from until.runtime.code_runtime import _check_tests
    from until.runtime.models import RunResult

    failed = _check_tests(RunResult("failed", 1, "2 failed", ""))
    assert [f.level for f in failed] == ["block"] and failed[0].code == "tests_failed"

    missing = _check_tests(RunResult("tool_missing", None,
                                     skipped_reason="pytest 없음"))
    assert [f.level for f in missing] == ["warn"], missing
    assert "코드가 틀렸다는 뜻이 아닙니다" in missing[0].message

    assert _check_tests(RunResult("succeeded", 0)) == []
    assert _check_tests(None) == []
    print("OK 실행 엔진 — 실패는 차단, 못 돌림은 경고")


def test_controller_refuses_to_run_steps_without_isolation():
    """격리가 없으면 검증 명령도 돌지 않는다 — 에이전트와 같은 기준."""
    from until.runtime.local_agent import AgentContractError, LocalAgentController
    from until.runtime.models import RunStep

    controller = LocalAgentController()          # 기본 = DisabledExecutionBoundary
    try:
        controller.run_steps((RunStep(argv=("python", "-m", "pytest")),),
                             Path("."), ())
        raise AssertionError("격리 없이 실행됐다")
    except AgentContractError:
        pass
    print("OK 실행 엔진 — 격리 없으면 검증 명령도 거부")


def test_run_steps_stops_at_the_first_failure():
    from until.runtime.local_agent import LocalAgentController
    from until.runtime.cli_agent import CommandResult
    from until.runtime.models import RunStep

    calls = []

    class _Boundary:
        filesystem_isolated = environment_isolated = network_isolated = True

        def run_step(self, step, workspace_root, environment):
            calls.append(step.argv)
            return CommandResult(1 if len(calls) == 1 else 0, "out", "err")

    controller = LocalAgentController(_Boundary(), environ={})
    steps = (RunStep(argv=("python", "-m", "pytest")),
             RunStep(argv=("ruff", "check")))
    result = controller.run_steps(steps, Path("."), ())
    assert result.status == "failed" and result.exit_code == 1
    assert len(calls) == 1, calls          # 두 번째 단계는 돌지 않는다
    print("OK 실행 엔진 — 첫 실패에서 멈춘다")


# ── 발표 런타임 ─────────────────────────────────────────────────────
def test_presentation_runtime_checks_slide_shape():
    runtime = PresentationRuntime()
    request = _request("presentation_conversion")
    with tempfile.TemporaryDirectory() as raw:
        workspace = _materialize(runtime, request, Path(raw) / "wr")
        _write(workspace, SLIDES_RELPATH, "## 슬라이드 1: 한 장뿐\n- 내용\n")
        assert "too_few_slides" in _codes(runtime.validate(workspace, OK))

        _write(workspace, SLIDES_RELPATH,
               "## 슬라이드 1: 배경\n- 하나\n\n## 슬라이드 2: 분석\n\n"
               "## 슬라이드 3: 결론\n- 셋\n"
               "[[DECISION: 어떤 주장을 앞세울지 — 본인 판단]]\n")
        codes = _codes(runtime.validate(workspace, OK))
        assert "empty_slide" in codes, codes          # 내용 없는 장

        _write(workspace, SLIDES_RELPATH,
               "## 슬라이드 1: 배경\n- 하나\n\n## 슬라이드 2: 분석\n- 둘\n\n"
               "## 슬라이드 3: 결론\n- 셋\n"
               "[[DECISION: 어떤 주장을 앞세울지 — 본인 판단]]\n")
        result = runtime.validate(workspace, OK)
        assert not result.blocked, _codes(result)
    print("OK 발표 — 장수·빈 슬라이드·결정 표식")


def test_presentation_runtime_warns_on_crowded_slides():
    runtime = PresentationRuntime()
    request = _request("presentation_conversion")
    with tempfile.TemporaryDirectory() as raw:
        workspace = _materialize(runtime, request, Path(raw) / "wr")
        bullets = "".join(f"- 줄{i}\n" for i in range(12))
        _write(workspace, SLIDES_RELPATH,
               f"## 슬라이드 1: 배경\n{bullets}\n## 슬라이드 2: 분석\n- 둘\n\n"
               "## 슬라이드 3: 결론\n- 셋\n"
               "[[DECISION: 강조점 — 본인 판단]]\n")
        result = runtime.validate(workspace, OK)
    # 경고이지 차단은 아니다 — 발표 밀도는 취향의 영역이 섞여 있다.
    assert "crowded_slide" in _codes(result) and not result.blocked
    print("OK 발표 — 과밀 슬라이드는 경고(차단 아님)")


# ── 활동 양식 런타임 ────────────────────────────────────────────────
def test_form_runtime_blocks_invented_activity_facts():
    """다른 런타임과 방향이 반대다 — '덜 썼다'가 아니라 '모르는 걸 썼다'를 막는다."""
    runtime = FormRuntime()
    request = _request("activity_form", {"form_fields": ["참여자", "일시", "결과"]})
    with tempfile.TemporaryDirectory() as raw:
        workspace = _materialize(runtime, request, Path(raw) / "wr")
        _write(workspace, FILLED_RELPATH,
               "## 참여자\n3명이 참여했다.\n\n## 일시\n2026년 5월 12일 14시\n\n"
               "## 결과\n[[DECISION: 결과 — 실제 사실을 학생이 채워야 함]]\n")
        result = runtime.validate(workspace, OK)
    assert "invented_activity_fact" in _codes(result), _codes(result)
    print("OK 양식 — 근거 없는 활동 사실 차단")


def test_form_runtime_accepts_facts_the_student_supplied():
    """학생이 알려 준 사실은 그대로 써도 된다 — 근거가 있기 때문."""
    runtime = FormRuntime()
    request = _request(
        "activity_form", {"form_fields": ["참여자", "결과"]},
        decisions={"참여자": "김민준, 이서연 2명이 참여했다",
                   "결과": "설문 30부를 회수했다"})
    with tempfile.TemporaryDirectory() as raw:
        workspace = _materialize(runtime, request, Path(raw) / "wr")
        _write(workspace, FILLED_RELPATH,
               "## 참여자\n김민준, 이서연 2명이 참여했다.\n\n"
               "## 결과\n설문 30부를 회수했다.\n\n"
               "[[DECISION: 소감 — 본인만 쓸 수 있음]]\n")
        result = runtime.validate(workspace, OK)
    assert not result.blocked, _codes(result)
    print("OK 양식 — 학생이 준 사실은 통과")


def test_form_runtime_blocks_filling_everything_in():
    runtime = FormRuntime()
    request = _request("activity_form", {"form_fields": ["참여자"]})
    with tempfile.TemporaryDirectory() as raw:
        workspace = _materialize(runtime, request, Path(raw) / "wr")
        _write(workspace, FILLED_RELPATH, "## 참여자\n모두 잘 참여했다.\n")
        result = runtime.validate(workspace, OK)
    assert "boundary_crossed" in _codes(result)
    print("OK 양식 — 사람 몫을 다 채워 버리면 차단")


def test_form_scaffold_marks_every_field_as_the_students():
    runtime = FormRuntime()
    request = _request("activity_form", {"form_fields": ["참여자", "일시"]})
    with tempfile.TemporaryDirectory() as raw:
        workspace = _materialize(runtime, request, Path(raw) / "wr")
        body = (Path(workspace.root) / FILLED_RELPATH).read_text(encoding="utf-8")
    assert body.count("[[DECISION:") == 2 and "참여자" in body and "일시" in body
    print("OK 양식 — 칸마다 '학생이 채울 자리'로 시작한다")


TESTS = [
    test_each_strategy_goes_to_the_right_runtime,
    test_execution_dependent_types_are_refused_not_faked,
    test_code_runtime_blocks_syntax_errors,
    test_code_runtime_blocks_emptied_starter_files,
    test_code_runtime_blocks_invented_benchmark_numbers,
    test_code_runtime_passes_and_says_it_did_not_run_the_code,
    test_code_runtime_requires_a_human_decision,
    test_kernel_ceiling_ignores_what_a_plugin_asks_for,
    test_steps_are_declared_before_the_agent_runs,
    test_failed_tests_block_but_unrunnable_tests_only_warn,
    test_controller_refuses_to_run_steps_without_isolation,
    test_run_steps_stops_at_the_first_failure,
    test_presentation_runtime_checks_slide_shape,
    test_presentation_runtime_warns_on_crowded_slides,
    test_form_runtime_blocks_invented_activity_facts,
    test_form_runtime_accepts_facts_the_student_supplied,
    test_form_runtime_blocks_filling_everything_in,
    test_form_scaffold_marks_every_field_as_the_students,
]

if __name__ == "__main__":
    for case in TESTS:
        case()
    print("\nRUNTIME PLUGIN TESTS PASS")
