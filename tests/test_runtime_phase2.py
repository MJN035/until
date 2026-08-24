# -*- coding: utf-8 -*-
"""Local Agent Runtime Phase 2~4 — 공식 CLI 어댑터 · 격리 경계 · Report Runtime ·
Submission Bridge.

전부 오프라인: 프로세스는 주입 러너(fake)로 대체하고, 실제 CLI·네트워크는 한 번도
부르지 않는다. 계획서(docs/ASSIGNMENT_RUNTIME_PLAN.md §7)의 완료 조건을 케이스로
옮겼다 — 미로그인·사용량 제한·timeout·취소 구분, 경계선 강제, 검증 후 변조 시
nonce 무효, block 시 발급 0회.
"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass, field
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
from until.runtime.cli_agent import (
    CliSpec,
    CliSpecError,
    CommandResult,
    OfficialCliAgent,
    load_cli_spec,
)
from until.runtime.report_runtime import (
    DRAFT_RELPATH,
    PROMPT_RELPATH,
    REPAIR_RELPATH,
    SPEC_RELPATH,
    ReportRuntime,
    workspace_provider_for,
)

SPEC = CliSpec(name="fake-cli", command="fake-agent", status_args=("status",),
               run_args=("-p", "{prompt}"))


# ── 가짜 러너 ───────────────────────────────────────────────────────
@dataclass
class FakeRunner:
    """프로세스 대신 미리 정한 결과를 돌려주고, 호출 인자를 기록한다."""
    results: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)
    on_run: object = None
    default: CommandResult = CommandResult(0, "ok")

    def __call__(self, argv, *, cwd, env, timeout, stdin_text=""):
        self.calls.append({"argv": tuple(argv), "cwd": Path(cwd), "env": dict(env),
                           "timeout": timeout, "stdin": stdin_text})
        key = next((k for k in self.results if k in " ".join(argv)), None)
        if key is not None:
            return self.results[key]
        if self.on_run is not None:
            return self.on_run(Path(cwd), tuple(argv))
        return self.default


def _sandboxed_boundary(runner):
    """격리를 보장한다고 신고하는 샌드박스 — 커널이 실행을 허용하는 유일한 형태."""
    return boundary_mod.SubprocessBoundary(
        boundary_mod.SandboxSpec(("sbx", "--root", "{workspace}"),
                                 isolates_filesystem=True, isolates_network=True),
        runner=runner)


def _agent(runtime, runner=None):
    agent = OfficialCliAgent(SPEC, workspace_provider_for(runtime))
    if runner is not None:
        agent.runner = runner
    return agent


def _request(root: Path, *, strategy="evidence_report", spec_extra=None):
    source = root / "input.txt"
    source.write_text("실험 자료 원문\n", encoding="utf-8")
    spec = {"title": "3주차 보고서", "goal": "결정립 크기와 항복강도",
            "required": ["서론", "결론"], "min_chars": 40, "requires_citation": True}
    spec.update(spec_extra or {})
    return RuntimeRequest("assignment-report-1", spec,
                          AssignmentRoute(strategy, "fixture", ()),
                          resolve_policy((PolicyLayer("assignment", "a1",
                                                      ai_use="allowed"),)),
                          (source,))


GOOD_DRAFT = (
    "# 서론\n" + "본론 문장. " * 20 + "[자료1]\n\n"
    "[[DECISION: 핵심 논지를 어디로 세울지 — 본인 관점]]\n\n# 결론\n마무리.\n"
)


# ── Phase 2: CLI 어댑터 ─────────────────────────────────────────────
def test_spec_rejects_auto_approve_and_missing_prompt():
    """승인 게이트는 Until plan 하나뿐 — CLI 자동 승인 플래그는 설정 자체를 거부."""
    for bad in ("--yes", "--dangerously-skip-permissions", "--auto-approve", "-y"):
        try:
            CliSpec(name="x", command="c", run_args=("-p", bad, "{prompt}"))
            raise AssertionError(f"{bad} 가 통과했다")
        except CliSpecError as exc:
            assert "auto-approve" in str(exc)
    try:
        CliSpec(name="x", command="c", run_args=("-p",))
        raise AssertionError("{prompt} 없이 통과했다")
    except CliSpecError:
        pass
    print("OK CLI 설정 — auto-approve 차단 · prompt 자리표시 강제")


def test_spec_is_configured_never_guessed():
    """벤더 플래그를 코드에 못 박지 않는다 — 설정이 없으면 기능 자체가 꺼진다."""
    assert load_cli_spec({}) is None
    got = load_cli_spec({"UNTIL_AGENT_CMD": "claude",
                         "UNTIL_AGENT_RUN_ARGS": "-p,{prompt}"})
    assert got.command == "claude" and got.run_args == ("-p", "{prompt}")
    with tempfile.TemporaryDirectory() as raw:
        path = Path(raw) / "agent.json"
        path.write_text(json.dumps({"command": "codex", "run_args": ["exec", "{prompt}"],
                                    "status_args": ["login", "status"]}),
                        encoding="utf-8")
        spec = load_cli_spec({"UNTIL_AGENT_SPEC": str(path)})
        assert spec.command == "codex" and spec.status_args == ("login", "status")
    print("OK CLI 설정 — 전부 사용자 제공(추측 0)")


def test_agent_never_launches_without_isolated_runner():
    """러너(=격리 경계)가 붙기 전에는 어떤 프로세스도 뜨지 않는다."""
    with tempfile.TemporaryDirectory() as raw:
        runtime = ReportRuntime()
        request = _request(Path(raw))
        runtime.prepare(request)
        agent = _agent(runtime)          # runner 미주입
        availability = agent.probe()
        assert availability.status == "unavailable"
        assert "격리" in availability.reason            # 확인조차 실행이므로 안 한다
        assert agent.runner is None
    print("OK 어댑터 — 격리 러너 없이는 실행 0")


def test_receipt_distinguishes_login_limit_timeout_cancel():
    """Phase 2 완료 조건: 미로그인·사용량 제한·timeout·취소를 구분한다."""
    cases = {
        "login_required": CommandResult(1, "", "Error: not logged in. Please log in."),
        "usage_limited": CommandResult(1, "", "usage limit reached for your plan"),
        "timeout": CommandResult(None, "", "", timed_out=True),
        "cancelled": CommandResult(130, "", "interrupted"),
        "failed": CommandResult(7, "", "boom"),
        "succeeded": CommandResult(0, "done"),
    }
    for expected, result in cases.items():
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            runtime = ReportRuntime()
            request = _request(root)
            runtime.prepare(request)
            workspace = _materialize(runtime, request, root)
            job = runtime.build_job(workspace)
            runner = FakeRunner(default=result)
            agent = _agent(runtime, runner)
            receipt = agent.execute(job, None)
            assert receipt.status == expected, (expected, receipt.status, receipt.reason)
            if expected == "usage_limited":
                # 결제 우회 없음 — 재시도·업그레이드 시도를 하지 않고 끝난다.
                assert len(runner.calls) == 1
    print("OK receipt — 미로그인·한도·timeout·취소·실패·성공 구분")


def test_receipt_redacts_secrets_and_bounds_output():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime = ReportRuntime()
        request = _request(root)
        runtime.prepare(request)
        workspace = _materialize(runtime, request, root)
        job = runtime.build_job(workspace)
        noisy = CommandResult(0, "api_key=sk-super-secret " + "x" * 40000, "")
        receipt = _agent(runtime, FakeRunner(default=noisy)).execute(job, None)
        assert "sk-super-secret" not in receipt.stdout_summary
        assert "[REDACTED]" in receipt.stdout_summary
        assert len(receipt.stdout_summary) <= 8192
    print("OK receipt — 시크릿 마스킹 + 출력 상한")


def test_sandbox_wraps_argv_and_uses_sanitized_environment():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime = ReportRuntime()
        request = _request(root)
        runtime.prepare(request)
        workspace = _materialize(runtime, request, root)
        job = runtime.build_job(workspace)
        runner = FakeRunner(default=CommandResult(0, "ok"))
        bound = _sandboxed_boundary(runner)
        bound.execute(_agent(runtime), job, None, {"PATH": "/usr/bin"})
        call = runner.calls[-1]
        assert call["argv"][:2] == ("sbx", "--root")
        assert call["argv"][2] == str(workspace.root)      # {workspace} 치환
        assert "fake-agent" in call["argv"]
        assert call["env"] == {"PATH": "/usr/bin"}          # 커널이 세탁한 환경만
        assert call["cwd"] == workspace.root
    print("OK 경계 — 샌드박스 래핑 · 세탁된 환경 · 작업공간 cwd")


def test_boundary_without_sandbox_is_denied_by_kernel():
    """샌드박스 미설정 = 격리 거짓 신고 금지 → 커널이 실행을 막는다."""
    plain = boundary_mod.build_boundary({}, runner=FakeRunner())
    assert plain.filesystem_isolated is False
    assert plain.network_isolated is False
    controller = LocalAgentController(plain, environ={})
    try:
        controller.preview(object(), object())
        raise AssertionError("격리 없이 preview가 통과했다")
    except Exception as exc:
        assert "isolated" in str(exc)
    # 샌드박스가 파일시스템만 보장한다고 신고해도 네트워크가 빠지면 여전히 거부.
    partial = boundary_mod.build_boundary(
        {"UNTIL_AGENT_SANDBOX": "sbx", "UNTIL_AGENT_SANDBOX_ISOLATES": "filesystem"},
        runner=FakeRunner())
    assert partial.filesystem_isolated and not partial.network_isolated
    print("OK 경계 — 격리 미보장 시 커널이 실행 거부(기본 fail-closed)")


# ── Phase 3: Report Runtime ─────────────────────────────────────────
def _materialize(runtime, request, root):
    from until.runtime.workspace import WorkspaceManager
    plan = runtime.prepare(request)
    return WorkspaceManager(root / "work-root").materialize("report", request, plan)


def test_report_runtime_prepares_workspace_files():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime = ReportRuntime()
        request = _request(root)
        workspace = _materialize(runtime, request, root)
        job = runtime.build_job(workspace)
        for rel in (SPEC_RELPATH, PROMPT_RELPATH, DRAFT_RELPATH):
            assert (workspace.root / rel).exists(), rel
        spec_text = (workspace.root / SPEC_RELPATH).read_text(encoding="utf-8")
        assert "3주차 보고서" in spec_text and "서론" in spec_text
        prompt = (workspace.root / PROMPT_RELPATH).read_text(encoding="utf-8")
        assert "[[DECISION:" in prompt                # 경계선을 프롬프트가 먼저 말한다
        assert "지어내지 마세요" in prompt             # 수치 날조 금지
        # 편집 허용은 초안 하나뿐 — inputs는 읽기 전용.
        # 제출물은 초안 하나뿐 + 재시도 지시 파일(REPAIR.md)만 쓸 수 있다.
        assert job.editable_paths == (DRAFT_RELPATH, REPAIR_RELPATH)
        assert job.expected_artifacts == (DRAFT_RELPATH,)
        assert "inputs" in job.readable_paths
        assert DRAFT_RELPATH not in job.readable_paths   # 편집 가능과 겹치면 커널이 거부
        assert job.max_repair_attempts == 1
    print("OK Report Runtime — 작업공간·명세·프롬프트 준비")


def test_report_runtime_supports_only_report_strategies():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime = ReportRuntime()
        assert runtime.supports(_request(root)).status == "supported"
        assert runtime.supports(_request(root, strategy="hdl_lab")).status == "unsupported"
        blank = _request(root, spec_extra={"title": "", "goal": ""})
        assert runtime.supports(blank).status == "unsupported"
    print("OK Report Runtime — 보고서 전략만 맡는다")


def test_validator_blocks_boundary_crossing_and_missing_requirements():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime = ReportRuntime()
        request = _request(root)
        workspace = _materialize(runtime, request, root)
        runtime.build_job(workspace)
        draft = workspace.root / DRAFT_RELPATH

        from until.runtime.models import AgentReceipt
        ok = AgentReceipt("succeeded", (DRAFT_RELPATH,), ("editor",), 0)

        # ① 결정 표식을 다 지워버림 = 대신 결정 → block.
        draft.write_text("# 서론\n" + "문장. " * 30 + "[자료1]\n# 결론\n끝.\n",
                         encoding="utf-8")
        codes = {f.code for f in runtime.validate(workspace, ok).findings}
        assert "boundary_crossed" in codes

        # ② 필수 항목 누락 + 분량 미달 + 인용 없음.
        draft.write_text("짧음 [[DECISION: 관점을 어디로 세울지 정해야 함]]\n",
                         encoding="utf-8")
        codes = {f.code for f in runtime.validate(workspace, ok).findings}
        assert {"missing_section", "too_short", "no_citation"} <= codes, codes

        # ③ 전부 만족 → pass.
        draft.write_text(GOOD_DRAFT, encoding="utf-8")
        result = runtime.validate(workspace, ok)
        assert not result.blocked, [f.code for f in result.findings]

        # ④ 에이전트가 성공하지 못했으면 내용을 보기 전에 block.
        from until.runtime.models import AgentReceipt as R
        blocked = runtime.validate(workspace, R("usage_limited", reason="한도"))
        assert blocked.blocked
        assert blocked.findings[0].code == "agent_usage_limited"
    print("OK Report Runtime 검증 — 경계선·필수·분량·인용·에이전트 상태")


def test_full_run_repairs_once_then_packages():
    """검증 실패 → 1회 수정 → 통과 → 번들. (오케스트레이터 전체 경로)"""
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime = ReportRuntime()
        request = _request(root)
        state = {"attempts": 0}

        def on_run(cwd: Path, argv):
            if "-p" not in argv:          # probe(version/status)는 파일을 건드리지 않는다
                return CommandResult(0, "fake-cli 1.0")
            state["attempts"] += 1
            draft = cwd / DRAFT_RELPATH
            # 1회차는 경계선을 넘어버린 초안, 2회차(수정 요청 후)는 올바른 초안.
            draft.write_text(
                GOOD_DRAFT if state["attempts"] > 1
                else "# 서론\n" + "문장. " * 30 + "[자료1]\n# 결론\n끝.\n",
                encoding="utf-8")
            return CommandResult(0, "done")

        runner = FakeRunner(on_run=on_run)
        agent = _agent(runtime)
        controller = LocalAgentController(_sandboxed_boundary(runner), environ={})
        orch = RuntimeOrchestrator(RuntimeRegistry((runtime,)), agent,
                                   root / "work-root", controller=controller)
        prepared = orch.execute(request)
        assert prepared.status == "prepared", prepared.reason
        assert state["attempts"] == 0            # 승인 전에는 실행 0

        approval = Approval(prepared.agent_plan.fingerprint, True,
                            prepared.workspace.run_id, "test")
        report = orch.execute(request, approval=approval)
        assert report.status == "ready", (report.status, report.reason)
        assert state["attempts"] == 2            # 정확히 1회만 자동 수정
        assert report.bundle is not None and report.bundle.files
        # 수정 지시는 **편집 허용 파일**에 남는다 — 읽기 전용 PROMPT.md에 덧붙이면
        # 커널이 workspace_escape로 막기 때문이다.
        repair = (prepared.workspace.root / REPAIR_RELPATH).read_text(encoding="utf-8")
        assert "수정 요청" in repair and "boundary_crossed" in repair
        original = (prepared.workspace.root / PROMPT_RELPATH).read_text(encoding="utf-8")
        assert "수정 요청" not in original
    print("OK 전체 경로 — 승인 후 실행 · 1회 수정 · 번들 생성")


# ── Phase 4: Submission Bridge ──────────────────────────────────────
def _bundle(*files):
    return SubmissionBundle("a1", tuple(files))


def _file(path, mime, size=10, sha=None):
    return SubmissionFile(path, mime, sha or ("a" * 64), size)


def test_bundle_checks_names_formats_and_counts():
    assert bridge.check_bundle(_bundle(_file("work/draft.md", "text/markdown"))) == ()
    problems = bridge.check_bundle(_bundle(_file("work/x.exe", "application/octet-stream")))
    assert any("허용되지 않는 형식" in p for p in problems)
    problems = bridge.check_bundle(_bundle(_file("work/a.pdf", "text/plain")))
    assert any("MIME이 어긋" in p for p in problems)
    problems = bridge.check_bundle(_bundle(_file("work/a.md", "text/markdown", size=0)))
    assert any("빈 파일" in p for p in problems)
    problems = bridge.check_bundle(_bundle(_file("work/a.md", "text/markdown", sha="short")))
    assert any("해시가 없" in p for p in problems)
    many = _bundle(*[_file(f"work/f{i}.md", "text/markdown") for i in range(11)])
    assert any("파일이 너무 많" in p for p in bridge.check_bundle(many))
    dup = _bundle(_file("work/a.md", "text/markdown"), _file("other/a.md", "text/markdown"))
    assert any("중복" in p for p in bridge.check_bundle(dup))
    assert any("제출할 파일이 없" in p for p in bridge.check_bundle(_bundle()))
    missing = SubmissionBundle("a1", (_file("work/a.md", "text/markdown"),), ("표지.docx",))
    assert any("빠진 파일" in p for p in bridge.check_bundle(missing))
    need = bridge.check_bundle(_bundle(_file("work/a.md", "text/markdown")),
                               required_suffixes=(".docx",))
    assert any("요구하는 형식이 없" in p for p in need)
    print("OK 번들 검사 — 파일명·확장자·MIME·개수·빠진 파일·요구 형식")


def test_content_hash_binds_nonce_and_breaks_on_change():
    a = _bundle(_file("work/draft.md", "text/markdown", 10, "a" * 64))
    same = _bundle(_file("work/draft.md", "text/markdown", 10, "a" * 64))
    changed = _bundle(_file("work/draft.md", "text/markdown", 10, "b" * 64))
    assert bridge.bundle_content_hash(a) == bridge.bundle_content_hash(same)
    assert bridge.bundle_content_hash(a) != bridge.bundle_content_hash(changed)
    print("OK content hash — 같은 내용은 같은 값, 바뀌면 달라짐(nonce 무효)")


def test_preview_blocks_and_never_issues_for_blocked_runtime():
    """runtime이 block이면 미리보기가 거부하고 nonce 결합값도 만들지 않는다."""
    from until.runtime.models import RuntimeReport, ValidationFinding, ValidationResult
    blocked = RuntimeReport(
        "ready", "report",
        validation=ValidationResult((ValidationFinding("block", "too_short", "분량 부족"),)),
        bundle=_bundle(_file("work/draft.md", "text/markdown")))
    preview = bridge.preview_submission(blocked)
    assert preview.allowed is False and "분량 부족" in preview.describe()
    try:
        bridge.submission_binding(blocked, uid="u", session_id="s")
        raise AssertionError("block 상태에서 nonce 결합값이 나왔다")
    except bridge.BundleRejected:
        pass
    assert bridge.validated_submission_files(blocked) == ()
    print("OK 미리보기 — block이면 거부 · nonce 발급 0")


def test_preview_detects_post_validation_tampering():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        runtime = ReportRuntime()
        request = _request(root)
        workspace = _materialize(runtime, request, root)
        runtime.build_job(workspace)
        draft = workspace.root / DRAFT_RELPATH
        draft.write_text(GOOD_DRAFT, encoding="utf-8")
        from until.runtime.models import AgentReceipt, RuntimeReport
        validation = runtime.validate(
            workspace, AgentReceipt("succeeded", (DRAFT_RELPATH,), ("editor",), 0))
        bundle = runtime.package(workspace, validation)
        report = RuntimeReport("ready", "report", workspace=workspace,
                               validation=validation, bundle=bundle)
        good = bridge.preview_submission(report)
        assert good.allowed, good.problems
        assert good.content_hash and len(good.content_hash) == 64
        binding = bridge.submission_binding(report, uid="u1", session_id="s1")
        assert binding.startswith("u1:s1:") and good.content_hash in binding

        # 검증 이후 파일을 고치면 즉시 무효 — 기존 nonce가 못 쓰게 된다.
        draft.write_text(GOOD_DRAFT + "\n몰래 추가한 문단.\n", encoding="utf-8")
        after = bridge.preview_submission(report)
        assert after.allowed is False
        assert any("검증 이후 파일이 바뀌" in p for p in after.problems)
    print("OK 미리보기 — 검증 후 변조 탐지(네트워크 0)")


TESTS = [
    test_spec_rejects_auto_approve_and_missing_prompt,
    test_spec_is_configured_never_guessed,
    test_agent_never_launches_without_isolated_runner,
    test_receipt_distinguishes_login_limit_timeout_cancel,
    test_receipt_redacts_secrets_and_bounds_output,
    test_sandbox_wraps_argv_and_uses_sanitized_environment,
    test_boundary_without_sandbox_is_denied_by_kernel,
    test_report_runtime_prepares_workspace_files,
    test_report_runtime_supports_only_report_strategies,
    test_validator_blocks_boundary_crossing_and_missing_requirements,
    test_full_run_repairs_once_then_packages,
    test_bundle_checks_names_formats_and_counts,
    test_content_hash_binds_nonce_and_breaks_on_change,
    test_preview_blocks_and_never_issues_for_blocked_runtime,
    test_preview_detects_post_validation_tampering,
]

if __name__ == "__main__":
    for fn in TESTS:
        fn()
    print("RUNTIME PHASE 2-4 TESTS PASS")
