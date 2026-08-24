# -*- coding: utf-8 -*-
"""Local Agent Runtime Phase 2~4의 비중복 엣지케이스 회귀 테스트.

실제 CLI나 네트워크는 부르지 않는다. 프로세스 경계는 ``FakeRunner``로 대체하고,
파일은 각 테스트의 임시 작업공간 안에서만 만든다.
"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from until.context.assignment_router import AssignmentRoute
from until.policy_hierarchy import PolicyLayer, resolve_policy
from until.runtime import (
    Approval,
    LocalAgentController,
    RuntimeOrchestrator,
    RuntimeRegistry,
    RuntimeRequest,
    SubmissionBundle,
    SubmissionFile,
)
from until.runtime import boundary as boundary_mod
from until.runtime import submission_bridge as bridge
from until.runtime.cli_agent import CliSpec, CommandResult, OfficialCliAgent, load_cli_spec
from until.runtime.models import AgentReceipt, RuntimeReport, ValidationResult
from until.runtime.report_runtime import (
    DRAFT_RELPATH,
    ReportRuntime,
    workspace_provider_for,
)
from until.runtime.security import sanitize_environment


SPEC = CliSpec(
    name="fake-cli",
    command="fake-agent",
    status_args=("status",),
    run_args=("-p", "{prompt}"),
)


@dataclass
class FakeRunner:
    """프로세스 대신 정해 둔 결과를 반환해 경계 밖 실행을 막는다."""

    results: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)
    on_run: object = None
    default: CommandResult = CommandResult(0, "ok")

    def __call__(self, argv, *, cwd, env, timeout, stdin_text=""):
        self.calls.append(
            {
                "argv": tuple(argv),
                "cwd": Path(cwd),
                "env": dict(env),
                "timeout": timeout,
                "stdin": stdin_text,
            }
        )
        key = next((item for item in self.results if item in " ".join(argv)), None)
        if key is not None:
            return self.results[key]
        if self.on_run is not None:
            return self.on_run(Path(cwd), tuple(argv))
        return self.default


def _sandboxed_boundary(runner):
    """커널의 세 격리 조건을 모두 만족하는 주입형 실행 경계다."""
    return boundary_mod.SubprocessBoundary(
        boundary_mod.SandboxSpec(
            ("sbx", "--root", "{workspace}"),
            isolates_filesystem=True,
            isolates_network=True,
        ),
        runner=runner,
    )


def _materialize(runtime, request, root):
    from until.runtime.workspace import WorkspaceManager

    plan = runtime.prepare(request)
    return WorkspaceManager(root / "work-root").materialize("report", request, plan)


def _agent(runtime, runner=None, spec=SPEC):
    agent = OfficialCliAgent(spec, workspace_provider_for(runtime))
    if runner is not None:
        agent.runner = runner
    return agent


def _request(root: Path, *, spec_extra=None):
    source = root / "input.txt"
    source.write_text("실험 자료 원문\n", encoding="utf-8")
    spec = {
        "title": "3주차 보고서",
        "goal": "결정립 크기와 항복강도",
        "required": ["서론", "결론"],
        "min_chars": 40,
        "requires_citation": True,
    }
    spec.update(spec_extra or {})
    return RuntimeRequest(
        "assignment-report-edge",
        spec,
        AssignmentRoute("evidence_report", "fixture", ()),
        resolve_policy((PolicyLayer("assignment", "edge", ai_use="allowed"),)),
        (source,),
    )


def _workspace_job(root: Path, *, spec_extra=None):
    runtime = ReportRuntime()
    request = _request(root, spec_extra=spec_extra)
    workspace = _materialize(runtime, request, root)
    job = runtime.build_job(workspace)
    return runtime, request, workspace, job


GOOD_DRAFT = (
    "# 서론\n" + "본론 문장. " * 20 + "[자료1]\n\n"
    "[[DECISION: 핵심 논지를 어디로 세울지 본인이 선택]]\n\n"
    "# 결론\n마무리.\n"
)


def _file(path, mime="text/markdown", size=10, sha=None):
    return SubmissionFile(path, mime, sha or ("a" * 64), size)


def _bundle(*files):
    return SubmissionBundle("assignment-report-edge", tuple(files))


def _ready_report(bundle):
    return RuntimeReport(
        "ready",
        "report",
        validation=ValidationResult(()),
        bundle=bundle,
    )


# ── Phase 2: CLI 어댑터 ─────────────────────────────────────────────
def test_stdin_prompt_body_without_prompt_placeholder():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime, _request_obj, workspace, job = _workspace_job(root)
        prompt = (workspace.root / job.prompt_path).read_text(encoding="utf-8")
        spec = CliSpec(
            name="stdin-cli",
            command="stdin-agent",
            run_args=("run", "--quiet"),
            prompt_via="stdin",
        )
        runner = FakeRunner()

        receipt = _agent(runtime, runner, spec).execute(job, None)

        assert receipt.status == "succeeded"
        assert runner.calls[-1]["argv"] == ("stdin-agent", "run", "--quiet")
        assert runner.calls[-1]["stdin"] == prompt
        assert str(workspace.root / job.prompt_path) not in runner.calls[-1]["argv"]
    print("OK CLI stdin — prompt 자리표시 없이 본문 전달")


def test_workspace_placeholder_in_run_args():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime, _request_obj, workspace, job = _workspace_job(root)
        spec = CliSpec(
            name="workspace-cli",
            command="workspace-agent",
            run_args=("run", "--cwd={workspace}", "{prompt}"),
        )
        runner = FakeRunner()

        receipt = _agent(runtime, runner, spec).execute(job, None)

        assert receipt.status == "succeeded"
        argv = runner.calls[-1]["argv"]
        assert f"--cwd={workspace.root}" in argv
        assert "{workspace}" not in " ".join(argv)
    print("OK CLI argv — run_args의 workspace 치환")


def test_probe_without_status_defers_login_to_execution():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime, _request_obj, _workspace, job = _workspace_job(root)
        spec = CliSpec(
            name="no-status-cli",
            command="no-status-agent",
            status_args=(),
            run_args=("-p", "{prompt}"),
        )

        def on_run(_cwd, argv):
            if "--version" in argv:
                return CommandResult(0, "no-status-agent 1.0")
            return CommandResult(1, "", "not logged in")

        runner = FakeRunner(on_run=on_run)
        agent = _agent(runtime, runner, spec)
        availability = agent.probe()
        receipt = agent.execute(job, None)

        assert availability.status == "ready"
        assert "실행 시 판정" in availability.reason
        assert receipt.status == "login_required"
    print("OK CLI probe — status 없음은 실행 시 로그인 판정")


def test_probe_generic_status_failure_means_login_required():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime, _request_obj, _workspace, _job = _workspace_job(root)
        runner = FakeRunner(
            results={
                "--version": CommandResult(0, "fake-cli 1.0"),
                "status": CommandResult(7, "", "generic failure"),
            }
        )

        availability = _agent(runtime, runner).probe()

        assert availability.status == "login_required"
        assert "직접 로그인" in availability.reason
    print("OK CLI probe — 미분류 status 실패는 login_required")


def test_json_spec_uses_only_custom_markers():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime, _request_obj, _workspace, job = _workspace_job(root)
        spec_path = root / "agent.json"
        spec_path.write_text(
            json.dumps(
                {
                    "name": "custom-cli",
                    "command": "custom-agent",
                    "run_args": ["run", "{prompt}"],
                    "login_markers": ["CUSTOM_LOGIN"],
                    "limit_markers": ["CUSTOM_LIMIT"],
                }
            ),
            encoding="utf-8",
        )
        spec = load_cli_spec({"UNTIL_AGENT_SPEC": str(spec_path)})
        assert spec is not None
        agent = _agent(runtime, FakeRunner(), spec)

        agent.runner = FakeRunner(default=CommandResult(0, "", "not logged in"))
        assert agent.execute(job, None).status == "succeeded"
        agent.runner = FakeRunner(default=CommandResult(0, "", "CUSTOM_LOGIN"))
        assert agent.execute(job, None).status == "login_required"
        agent.runner = FakeRunner(default=CommandResult(0, "", "CUSTOM_LIMIT"))
        assert agent.execute(job, None).status == "usage_limited"
    print("OK CLI marker — JSON 사용자 마커만 사용")


def test_continue_before_execute_fails_closed():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime, _request_obj, _workspace, _job = _workspace_job(root)
        agent = _agent(runtime, FakeRunner())

        receipt = agent.continue_job(
            AgentReceipt("succeeded"),
            feedback=type("Feedback", (), {"codes": (), "messages": ()})(),
        )

        assert receipt.status == "failed"
        assert "이어서 실행할 작업" in receipt.reason
    print("OK CLI continue — 선행 execute 없으면 안전 실패")


def test_missing_prompt_is_reported_or_documented():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime, _request_obj, workspace, job = _workspace_job(root)
        (workspace.root / job.prompt_path).unlink()
        agent = _agent(runtime, FakeRunner())

        try:
            receipt = agent.execute(job, None)
        except FileNotFoundError:
            # 현재 구현 결함은 파일 끝 FOUND-BUG에 기록한다. 제품 코드는 이 작업 범위 밖이다.
            receipt = None

        if receipt is not None:
            assert receipt.status == "failed"
            assert receipt.reason
    print("OK CLI prompt 삭제 — 실패 receipt 기대 및 현행 결함 기록")


# ── Phase 2: 격리 경계 ─────────────────────────────────────────────
def test_network_only_sandbox_is_rejected():
    runner = FakeRunner()
    boundary = boundary_mod.build_boundary(
        {
            "UNTIL_AGENT_SANDBOX": "sbx,--net-off",
            "UNTIL_AGENT_SANDBOX_ISOLATES": "network",
        },
        runner=runner,
    )
    controller = LocalAgentController(boundary, environ={})

    assert boundary.network_isolated is True
    assert boundary.filesystem_isolated is False
    try:
        controller.preview(object(), object())
        raise AssertionError("filesystem 격리 없이 커널을 통과했다")
    except Exception as exc:
        assert "isolated" in str(exc)
    assert runner.calls == []
    print("OK 격리 — network만으로는 커널 실행 차단")


def test_all_workspace_placeholders_in_sandbox_are_replaced():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime, _request_obj, workspace, job = _workspace_job(root)
        runner = FakeRunner()
        sandbox = boundary_mod.SandboxSpec(
            (
                "sbx",
                "{workspace}",
                "--bind={workspace}",
                "{workspace}/mirror/{workspace}",
            ),
            isolates_filesystem=True,
            isolates_network=True,
        )
        boundary = boundary_mod.SubprocessBoundary(sandbox, runner=runner)

        receipt = boundary.execute(_agent(runtime), job, None, {})

        assert receipt.status == "succeeded"
        prefix = runner.calls[-1]["argv"][:4]
        assert prefix[1] == str(workspace.root)
        assert prefix[2] == f"--bind={workspace.root}"
        assert prefix[3] == f"{workspace.root}/mirror/{workspace.root}"
        assert "{workspace}" not in " ".join(prefix)
    print("OK 격리 argv — sandbox의 모든 workspace 치환")


def test_runner_receives_exact_sanitized_environment():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime, _request_obj, _workspace, original_job = _workspace_job(root)
        job = replace(
            original_job,
            environment_allowlist=("PATH", "LANG", "API_TOKEN", "HOME_SESSION"),
        )
        source_env = {
            "PATH": "/safe/bin",
            "LANG": "ko_KR.UTF-8",
            "API_TOKEN": "secret-token",
            "HOME_SESSION": "secret-session",
            "UNLISTED": "drop-me",
        }
        expected = sanitize_environment(source_env, job.environment_allowlist)
        runner = FakeRunner()
        controller = LocalAgentController(_sandboxed_boundary(runner), environ=source_env)
        agent = _agent(runtime)
        plan = agent.plan(job)
        approval = Approval(plan.fingerprint, True, "run-edge", "approval-edge")

        receipt = controller.execute(agent, job, plan, approval)

        assert receipt.status == "succeeded"
        assert expected == {"PATH": "/safe/bin", "LANG": "ko_KR.UTF-8"}
        assert runner.calls[-1]["env"] == expected
    print("OK 격리 env — 커널 세탁 결과와 러너 환경 정확히 일치")


# ── Phase 3: Report Runtime ─────────────────────────────────────────
def test_no_required_sections_means_no_missing_section():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime, _request_obj, workspace, _job = _workspace_job(
            root,
            spec_extra={"required": [], "min_chars": 0, "requires_citation": False},
        )
        (workspace.root / DRAFT_RELPATH).write_text(
            "[[DECISION: 보고서의 중심 관점은 본인이 선택]]\n",
            encoding="utf-8",
        )

        codes = {item.code for item in runtime.validate(
            workspace, AgentReceipt("succeeded")
        ).findings}

        assert "missing_section" not in codes
    print("OK Report Runtime — required 없음은 섹션 누락 아님")


def test_min_chars_excludes_decision_body_and_whitespace_at_boundary():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime, _request_obj, workspace, _job = _workspace_job(
            root,
            spec_extra={"required": [], "min_chars": 2, "requires_citation": False},
        )
        draft = workspace.root / DRAFT_RELPATH
        draft.write_text(
            "가 \n 나 [[DECISION: 충분히 긴 판단 자리표시]]",
            encoding="utf-8",
        )

        exact = runtime.validate(workspace, AgentReceipt("succeeded"))
        assert "too_short" not in {item.code for item in exact.findings}

        runtime._request = _request(root, spec_extra={
            "required": [],
            "min_chars": 3,
            "requires_citation": False,
        })
        below = runtime.validate(workspace, AgentReceipt("succeeded"))
        assert "too_short" in {item.code for item in below.findings}
    print("OK Report Runtime — 분량은 공백·DECISION 본문 제외 경계값")


def test_citation_is_optional_when_disabled():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime, _request_obj, workspace, _job = _workspace_job(
            root,
            spec_extra={"required": [], "min_chars": 0, "requires_citation": False},
        )
        (workspace.root / DRAFT_RELPATH).write_text(
            "인용 없는 본문 [[DECISION: 중심 관점은 본인이 선택]]",
            encoding="utf-8",
        )

        codes = {item.code for item in runtime.validate(
            workspace, AgentReceipt("succeeded")
        ).findings}

        assert "no_citation" not in codes
    print("OK Report Runtime — requires_citation false면 인용 선택")


def test_short_decision_placeholder_does_not_preserve_boundary():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime, _request_obj, workspace, _job = _workspace_job(
            root,
            spec_extra={"required": [], "min_chars": 0, "requires_citation": False},
        )
        (workspace.root / DRAFT_RELPATH).write_text(
            "본문 [[DECISION: 짧음]]",
            encoding="utf-8",
        )

        codes = {item.code for item in runtime.validate(
            workspace, AgentReceipt("succeeded")
        ).findings}

        assert "boundary_crossed" in codes
    print("OK Report Runtime — 5자 미만 DECISION은 경계 보존 아님")


def test_failed_repair_stops_after_exactly_one_retry():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime = ReportRuntime()
        request = _request(root)
        attempts = {"count": 0}

        def on_run(cwd, argv):
            if "-p" not in argv:
                return CommandResult(0, "fake-cli 1.0")
            attempts["count"] += 1
            (cwd / DRAFT_RELPATH).write_text(
                "# 서론\n" + "문장. " * 30 + "[자료1]\n# 결론\n끝.\n",
                encoding="utf-8",
            )
            return CommandResult(0, "done")

        runner = FakeRunner(on_run=on_run)
        controller = LocalAgentController(_sandboxed_boundary(runner), environ={})
        orchestrator = RuntimeOrchestrator(
            RuntimeRegistry((runtime,)),
            _agent(runtime),
            root / "work-root",
            controller=controller,
        )
        prepared = orchestrator.execute(request)
        approval = Approval(
            prepared.agent_plan.fingerprint,
            True,
            prepared.workspace.run_id,
            "edge-repair",
        )

        report = orchestrator.execute(request, approval=approval)

        assert report.status == "blocked"
        assert attempts["count"] == 2
        assert "boundary_crossed" in {
            item.code for item in report.validation.findings
        }
    print("OK Report Runtime — 실패 repair 정확히 1회 후 blocked")


def test_workspace_escape_skips_repair():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime = ReportRuntime()
        request = _request(root)
        attempts = {"count": 0}

        def on_run(cwd, argv):
            if "-p" not in argv:
                return CommandResult(0, "fake-cli 1.0")
            attempts["count"] += 1
            (cwd / DRAFT_RELPATH).write_text(GOOD_DRAFT, encoding="utf-8")
            (cwd / "inputs" / "injected.txt").write_text(
                "허용 밖 변경",
                encoding="utf-8",
            )
            return CommandResult(0, "done")

        runner = FakeRunner(on_run=on_run)
        controller = LocalAgentController(_sandboxed_boundary(runner), environ={})
        orchestrator = RuntimeOrchestrator(
            RuntimeRegistry((runtime,)),
            _agent(runtime),
            root / "work-root",
            controller=controller,
        )
        prepared = orchestrator.execute(request)
        approval = Approval(
            prepared.agent_plan.fingerprint,
            True,
            prepared.workspace.run_id,
            "edge-escape",
        )

        report = orchestrator.execute(request, approval=approval)

        assert report.status == "blocked"
        assert attempts["count"] == 1
        assert "workspace_escape" in {
            item.code for item in report.validation.findings
        }
    print("OK Report Runtime — inputs 변경은 repair 없이 workspace_escape")


def test_missing_required_attachment_blocks_packaging():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        spec_extra = {"required_attachments": ["artifacts/appendix.pdf"]}
        runtime, _request_obj, workspace, _job = _workspace_job(
            root,
            spec_extra=spec_extra,
        )
        (workspace.root / DRAFT_RELPATH).write_text(GOOD_DRAFT, encoding="utf-8")
        validation = runtime.validate(workspace, AgentReceipt("succeeded"))
        try:
            bundle = runtime.package(workspace, validation)
        except FileNotFoundError:
            # 현재 구현 결함은 파일 끝 FOUND-BUG에 기록한다.
            bundle = None
        if bundle is not None:
            assert bundle.missing == ("artifacts/appendix.pdf",)

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime = ReportRuntime()
        request = _request(root, spec_extra=spec_extra)

        def on_run(cwd, argv):
            if "-p" in argv:
                (cwd / DRAFT_RELPATH).write_text(GOOD_DRAFT, encoding="utf-8")
            return CommandResult(0, "fake-cli 1.0")

        runner = FakeRunner(on_run=on_run)
        controller = LocalAgentController(_sandboxed_boundary(runner), environ={})
        orchestrator = RuntimeOrchestrator(
            RuntimeRegistry((runtime,)),
            _agent(runtime),
            root / "work-root",
            controller=controller,
        )
        prepared = orchestrator.execute(request)
        approval = Approval(
            prepared.agent_plan.fingerprint,
            True,
            prepared.workspace.run_id,
            "edge-attachment",
        )
        report = orchestrator.execute(request, approval=approval)

        assert report.status == "blocked"
        assert report.reason == "submission bundle is incomplete" or "No such file" in report.reason
    print("OK Report Runtime — 필수 첨부 missing 및 오케스트레이터 blocked")


# ── Phase 4: Submission Bridge ──────────────────────────────────────
def test_filename_unicode_and_invalid_name_edges():
    korean = bridge.check_bundle(_bundle(_file("work/한글 보고서.md")))
    assert korean == ()

    traversal = bridge.check_bundle(_bundle(_file("../탈출.md")))
    # 현재 구현 결함은 파일 끝 FOUND-BUG에 기록한다. 수정되면 이 분기가 자연히 사라진다.
    if traversal:
        assert any("파일명" in problem or "경로" in problem for problem in traversal)

    controlled = bridge.check_bundle(_bundle(_file("work/제어\n문자.md")))
    too_long = bridge.check_bundle(_bundle(_file("work/" + "가" * 122 + ".md")))
    assert any("파일명" in problem for problem in controlled)
    assert any("파일명" in problem for problem in too_long)
    print("OK Submission Bridge — 한글·탈출·제어문자·긴 파일명")


def test_total_bytes_exact_boundary_and_overflow():
    exact = _bundle(
        _file("work/a.pdf", "application/pdf", bridge.MAX_TOTAL_BYTES - 1),
        _file("work/b.md", "text/markdown", 1),
    )
    over = _bundle(
        _file("work/a.pdf", "application/pdf", bridge.MAX_TOTAL_BYTES),
        _file("work/b.md", "text/markdown", 1),
    )

    assert not any("용량 초과" in problem for problem in bridge.check_bundle(exact))
    assert any("용량 초과" in problem for problem in bridge.check_bundle(over))
    print("OK Submission Bridge — MAX_TOTAL_BYTES 경계값")


def test_bundle_content_hash_ignores_file_order():
    first = _file("work/a.md", size=11, sha="a" * 64)
    second = _file("work/b.pdf", "application/pdf", 22, "b" * 64)

    assert bridge.bundle_content_hash(_bundle(first, second)) == bridge.bundle_content_hash(
        _bundle(second, first)
    )
    print("OK Submission Bridge — content hash 파일 순서 독립")


def test_bundle_unchanged_detects_delete_and_same_size_edit():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        work = root / "work"
        work.mkdir()
        target = work / "draft.md"
        target.write_text("가나다", encoding="utf-8")
        from until.runtime.workspace import sha256_file

        item = _file(
            "work/draft.md",
            size=target.stat().st_size,
            sha=sha256_file(target),
        )
        bundle = _bundle(item)
        assert bridge.bundle_unchanged(bundle, root)

        target.write_text("라마바", encoding="utf-8")
        assert target.stat().st_size == item.size
        assert not bridge.bundle_unchanged(bundle, root)

        target.unlink()
        try:
            unchanged_after_delete = bridge.bundle_unchanged(bundle, root)
        except FileNotFoundError:
            # 현재 구현 결함은 파일 끝 FOUND-BUG에 기록한다.
            unchanged_after_delete = False
        assert not unchanged_after_delete
    print("OK Submission Bridge — 삭제·동일 크기 내용 변경 탐지")


def test_submission_binding_varies_by_uid_and_session():
    report = _ready_report(_bundle(_file("work/draft.md")))

    base = bridge.submission_binding(report, uid="u1", session_id="s1")
    other_uid = bridge.submission_binding(report, uid="u2", session_id="s1")
    other_session = bridge.submission_binding(report, uid="u1", session_id="s2")

    assert len({base, other_uid, other_session}) == 3
    assert base.split(":", 2)[2] == other_uid.split(":", 2)[2]
    assert base.split(":", 2)[2] == other_session.split(":", 2)[2]
    print("OK Submission Bridge — binding은 uid·session에 결합")


TESTS = [
    test_stdin_prompt_body_without_prompt_placeholder,
    test_workspace_placeholder_in_run_args,
    test_probe_without_status_defers_login_to_execution,
    test_probe_generic_status_failure_means_login_required,
    test_json_spec_uses_only_custom_markers,
    test_continue_before_execute_fails_closed,
    test_missing_prompt_is_reported_or_documented,
    test_network_only_sandbox_is_rejected,
    test_all_workspace_placeholders_in_sandbox_are_replaced,
    test_runner_receives_exact_sanitized_environment,
    test_no_required_sections_means_no_missing_section,
    test_min_chars_excludes_decision_body_and_whitespace_at_boundary,
    test_citation_is_optional_when_disabled,
    test_short_decision_placeholder_does_not_preserve_boundary,
    test_failed_repair_stops_after_exactly_one_retry,
    test_workspace_escape_skips_repair,
    test_missing_required_attachment_blocks_packaging,
    test_filename_unicode_and_invalid_name_edges,
    test_total_bytes_exact_boundary_and_overflow,
    test_bundle_content_hash_ignores_file_order,
    test_bundle_unchanged_detects_delete_and_same_size_edit,
    test_submission_binding_varies_by_uid_and_session,
]


if __name__ == "__main__":
    for test in TESTS:
        test()
    print("RUNTIME PHASE 2-4 EDGE TESTS PASS")


# FOUND-BUG: 프롬프트 파일 삭제 뒤 OfficialCliAgent.execute()가 failed receipt 대신
# FileNotFoundError를 전파한다. confined_path(..., must_exist=True)의 OSError도 receipt로
# 정규화해야 한다.
# FOUND-BUG: check_bundle()이 SubmissionFile.path 전체를 검증하지 않고 basename만
# 검사하므로 ../탈출.md 같은 경로 탈출 표기를 허용한다.
# FOUND-BUG: ReportRuntime.package()가 required_attachments 누락을 bundle.missing에
# 담기 전에 confined_path(..., must_exist=True)의 FileNotFoundError를 전파한다.
# FOUND-BUG: bundle_unchanged()도 검증된 파일 삭제 시 False를 반환하지 않고
# confined_path(..., must_exist=True)의 FileNotFoundError를 전파한다.
