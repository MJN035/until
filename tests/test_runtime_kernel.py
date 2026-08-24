from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from until.context.assignment_router import AssignmentRoute
from until.policy_hierarchy import PolicyLayer, resolve_policy
from until.runtime import (
    AgentAvailability,
    AgentFeedback,
    AgentJob,
    AgentPlan,
    AgentReceipt,
    Approval,
    LocalAgentController,
    RuntimeOrchestrator,
    RuntimeRegistry,
    RuntimeRequest,
    SubmissionBundle,
    SubmissionFile,
    SupportDecision,
    ValidationFinding,
    ValidationResult,
    WorkspacePlan,
)
from until.runtime.security import (
    RuntimeSecurityError,
    safe_relative_path,
    sanitize_environment,
)
from until.runtime.submission_bridge import validated_submission_files
from until.runtime.workspace import sha256_file


def _policy(ai_use="allowed", **kwargs):
    return resolve_policy((PolicyLayer("assignment", "a1", ai_use=ai_use, **kwargs),))


def _request(input_path: Path, *, policy=None):
    return RuntimeRequest(
        "assignment-1",
        {"title": "local agent fixture", "required": ["answer.txt"]},
        AssignmentRoute("code_project", "fixture", ()),
        policy or _policy(),
        (input_path,),
    )


@dataclass
class MockPlugin:
    name: str = "mock"
    support: str = "supported"
    priority: int = 10
    support_calls: int = 0
    workspace: object = None

    def supports(self, request):
        self.support_calls += 1
        return SupportDecision(self.support, "fixture", self.priority)

    def prepare(self, request):
        return WorkspacePlan(files=("instructions/prompt.md",), runnable=True)

    def build_job(self, workspace):
        self.workspace = workspace
        prompt = workspace.root / "instructions" / "prompt.md"
        prompt.write_text("Create work/answer.txt with valid content.\n", encoding="utf-8")
        return AgentJob(
            "assignment-1",
            "instructions/prompt.md",
            ("instructions/prompt.md", "inputs/input.txt"),
            ("work",),
            allowed_tools=("editor",),
            intended_uses=("draft",),
            forbidden_actions=("network", "submit"),
            expected_artifacts=("work/answer.txt",),
            environment_allowlist=("PATH", "SYSTEMROOT"),
            timeout_seconds=30,
            max_repair_attempts=1,
        )

    def validate(self, workspace, receipt):
        output = workspace.root / "work" / "answer.txt"
        if not output.is_file() or output.read_text(encoding="utf-8") != "valid\n":
            return ValidationResult((
                ValidationFinding("block", "answer_invalid", "answer.txt must contain valid"),
            ))
        return ValidationResult((ValidationFinding("pass", "answer_valid", "ok"),))

    def repair_feedback(self, validation):
        return AgentFeedback(
            tuple(item.code for item in validation.findings),
            tuple(item.message for item in validation.findings),
        )

    def package(self, workspace, validation):
        output = workspace.root / "work" / "answer.txt"
        return SubmissionBundle(
            "assignment-1",
            (SubmissionFile(
                "work/answer.txt", "text/plain", sha256_file(output), output.stat().st_size,
            ),),
        )


@dataclass
class MockAgent:
    plugin: MockPlugin
    availability_status: str = "ready"
    invalid_once: bool = False
    escape: bool = False
    mutate_during_plan: bool = False
    execute_calls: int = 0
    continue_calls: int = 0

    name: str = "mock-agent"

    def probe(self):
        reason = "" if self.availability_status == "ready" else self.availability_status
        return AgentAvailability(self.availability_status, self.name, "1.0", reason)

    def plan(self, job):
        if self.mutate_during_plan:
            (self.plugin.workspace.root / "work" / "preview.txt").write_text(
                "not allowed\n", encoding="utf-8"
            )
        return AgentPlan(
            job.fingerprint, "write the requested answer", ("work/answer.txt",), ("editor",)
        )

    def execute(self, job, approval):
        self.execute_calls += 1
        root = self.plugin.workspace.root
        content = "invalid\n" if self.invalid_once else "valid\n"
        (root / "work" / "answer.txt").write_text(content, encoding="utf-8")
        changed = ["work/answer.txt"]
        if self.escape:
            source = root / "inputs" / "input.txt"
            source.chmod(stat.S_IWRITE | stat.S_IREAD)
            source.write_text("tampered\n", encoding="utf-8")
            changed.append("inputs/input.txt")
        return AgentReceipt("succeeded", tuple(changed), ("editor",), 0)

    def continue_job(self, receipt, feedback):
        self.continue_calls += 1
        root = self.plugin.workspace.root
        (root / "work" / "answer.txt").write_text("valid\n", encoding="utf-8")
        return AgentReceipt("succeeded", ("work/answer.txt",), ("editor",), 0)


@dataclass
class MockExecutionBoundary:
    filesystem_isolated: bool = True
    environment_isolated: bool = True
    network_isolated: bool = True
    last_environment: dict[str, str] | None = None

    def preview(self, agent, job, environment):
        self.last_environment = environment
        availability = agent.probe()
        plan = agent.plan(job) if availability.status == "ready" else None
        return availability, plan

    def execute(self, agent, job, approval, environment):
        self.last_environment = environment
        return agent.execute(job, approval)

    def continue_job(self, agent, receipt, feedback, environment):
        self.last_environment = environment
        return agent.continue_job(receipt, feedback)


def _runtime(plugin, agent, root, *, boundary=None, environ=None):
    controller = LocalAgentController(
        boundary or MockExecutionBoundary(), environ=environ
    )
    return RuntimeOrchestrator(
        RuntimeRegistry((plugin,)), agent, root / "work-root", controller=controller
    )


def _preview_and_approve(runtime, request):
    preview = runtime.execute(request)
    assert preview.status == "prepared", preview.reason
    return preview, Approval(
        preview.agent_plan.fingerprint, True, preview.workspace.run_id, "test-approval"
    )


def test_approval_required_before_agent_execution():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        source = root / "input.txt"
        source.write_text("input", encoding="utf-8")
        plugin = MockPlugin()
        agent = MockAgent(plugin)
        runtime = _runtime(plugin, agent, root)
        preview = runtime.execute(_request(source))
    assert preview.status == "prepared", preview.reason
    assert preview.agent_plan is not None
    assert preview.bundle is None
    assert agent.execute_calls == 0


def test_full_lifecycle_is_deterministic_and_attempts_are_unique():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        source = root / "input.txt"
        source.write_text("input", encoding="utf-8")
        plugin = MockPlugin()
        agent = MockAgent(plugin)
        runtime = _runtime(plugin, agent, root)
        request = _request(source)
        preview, approval = _preview_and_approve(runtime, request)
        report = runtime.execute(request, approval=approval)
        replay = runtime.execute(request, approval=approval)
        next_preview = runtime.execute(request)
        assert report.status == "ready"
        assert replay.status == "blocked"
        assert "already consumed" in replay.reason
        assert agent.execute_calls == 1
        assert next_preview.status == "prepared", next_preview.reason
        assert report.workspace.plan_id == preview.workspace.plan_id == next_preview.workspace.plan_id
        assert report.workspace.run_id == preview.workspace.run_id
        assert next_preview.workspace.run_id != preview.workspace.run_id
        assert validated_submission_files(report) == report.bundle.files
        manifest = json.loads(report.workspace.manifest_path.read_text(encoding="utf-8"))
        assert manifest["inputs"][0]["path"] == "inputs/input.txt"
        assert str(root) not in json.dumps(manifest, ensure_ascii=False)


def test_wrong_or_rejected_approval_never_executes():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        source = root / "input.txt"
        source.write_text("input", encoding="utf-8")
        plugin = MockPlugin()
        agent = MockAgent(plugin)
        runtime = _runtime(plugin, agent, root)
        request = _request(source)
        preview = runtime.execute(request)
        report = runtime.execute(
            request, approval=Approval("wrong", True, preview.workspace.run_id)
        )
        rejected_preview = runtime.execute(request)
        assert rejected_preview.status == "prepared", rejected_preview.reason
        rejected = runtime.execute(
            request,
            approval=Approval(
                rejected_preview.agent_plan.fingerprint, False,
                rejected_preview.workspace.run_id,
            ),
        )
    assert report.status == rejected.status == "blocked"
    assert agent.execute_calls == 0


def test_login_required_and_unavailable_are_distinct():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        source = root / "input.txt"
        source.write_text("input", encoding="utf-8")
        for status in ("login_required", "unavailable"):
            plugin = MockPlugin()
            agent = MockAgent(plugin, availability_status=status)
            report = _runtime(plugin, agent, root / status).execute(_request(source))
            assert report.status == "blocked"
            assert report.availability.status == status
            assert agent.execute_calls == 0


def test_validator_failure_gets_exactly_one_repair():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        source = root / "input.txt"
        source.write_text("input", encoding="utf-8")
        plugin = MockPlugin()
        agent = MockAgent(plugin, invalid_once=True)
        runtime = _runtime(plugin, agent, root)
        request = _request(source)
        _, approval = _preview_and_approve(runtime, request)
        report = runtime.execute(request, approval=approval)
    assert report.status == "ready"
    assert agent.execute_calls == 1
    assert agent.continue_calls == 1


def test_workspace_escape_blocks_without_repair():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        source = root / "input.txt"
        source.write_text("input", encoding="utf-8")
        plugin = MockPlugin()
        agent = MockAgent(plugin, escape=True)
        runtime = _runtime(plugin, agent, root)
        request = _request(source)
        _, approval = _preview_and_approve(runtime, request)
        report = runtime.execute(request, approval=approval)
    assert report.status == "blocked"
    assert any(item.code == "workspace_escape" for item in report.validation.findings)
    assert agent.continue_calls == 0
    assert report.bundle is None


def test_default_boundary_denies_execution_and_sanitizes_test_boundary_environment():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        source = root / "input.txt"
        source.write_text("input", encoding="utf-8")
        plugin = MockPlugin()
        agent = MockAgent(plugin)
        request = _request(source)
        denied_runtime = RuntimeOrchestrator(
            RuntimeRegistry((plugin,)), agent, root / "denied"
        )
        denied = denied_runtime.execute(request)
        assert denied.status == "blocked"
        assert "isolated" in denied.reason
        assert agent.execute_calls == 0

        plugin = MockPlugin()
        agent = MockAgent(plugin)
        boundary = MockExecutionBoundary()
        runtime = _runtime(
            plugin, agent, root, boundary=boundary,
            environ={"PATH": "safe", "UNTIL_API_KEY": "proof-secret"},
        )
        _, approval = _preview_and_approve(runtime, request)
        assert runtime.execute(request, approval=approval).status == "ready"
        assert boundary.last_environment == {"PATH": "safe"}


def test_preview_and_preapproval_workspace_mutations_are_blocked():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        source = root / "input.txt"
        source.write_text("input", encoding="utf-8")
        plugin = MockPlugin()
        report = _runtime(plugin, MockAgent(plugin, mutate_during_plan=True), root).execute(
            _request(source)
        )
        assert report.status == "blocked"
        assert "during preview" in report.reason

        plugin = MockPlugin()
        agent = MockAgent(plugin)
        runtime = _runtime(plugin, agent, root)
        request = _request(source)
        preview, approval = _preview_and_approve(runtime, request)
        (preview.workspace.root / "work" / "after-preview.txt").write_text(
            "tampered\n", encoding="utf-8"
        )
        blocked = runtime.execute(request, approval=approval)
        assert blocked.status == "blocked"
        assert "before approval" in blocked.reason
        assert agent.execute_calls == 0


def test_policy_tool_scope_and_packaging_integrity_are_enforced():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        source = root / "input.txt"
        source.write_text("input", encoding="utf-8")
        plugin = MockPlugin()
        agent = MockAgent(plugin)
        blocked = _runtime(plugin, agent, root).execute(
            _request(source, policy=_policy(approved_tools=("safe-tool",)))
        )
        assert blocked.status == "blocked"
        assert "not approved" in blocked.reason
        assert agent.execute_calls == 0

        class MutatingPackagePlugin(MockPlugin):
            def package(self, workspace, validation):
                (workspace.root / "work" / "answer.txt").write_text(
                    "replaced after validation\n", encoding="utf-8"
                )
                return super().package(workspace, validation)

        plugin = MutatingPackagePlugin()
        agent = MockAgent(plugin)
        runtime = _runtime(plugin, agent, root)
        request = _request(source)
        _, approval = _preview_and_approve(runtime, request)
        report = runtime.execute(request, approval=approval)
        assert report.status == "blocked"
        assert "packaging modified" in report.reason


def test_protected_inputs_limited_use_and_validator_integrity_are_enforced():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        source = root / "input.txt"
        source.write_text("input", encoding="utf-8")

        class EditableInputPlugin(MockPlugin):
            def build_job(self, workspace):
                job = super().build_job(workspace)
                return AgentJob(
                    job.assignment_id, job.prompt_path, job.readable_paths,
                    ("work", "inputs"), allowed_tools=job.allowed_tools,
                    intended_uses=job.intended_uses,
                    forbidden_actions=job.forbidden_actions,
                    expected_artifacts=job.expected_artifacts,
                )

        plugin = EditableInputPlugin()
        blocked = _runtime(plugin, MockAgent(plugin), root).execute(_request(source))
        assert blocked.status == "blocked"
        assert "work/ or artifacts/" in blocked.reason

        plugin = MockPlugin()
        blocked = _runtime(plugin, MockAgent(plugin), root).execute(
            _request(
                source,
                policy=_policy(
                    "limited", allowed_uses=("summarize",),
                    approved_tools=("editor",),
                ),
            )
        )
        assert blocked.status == "blocked"
        assert "limited policy" in blocked.reason

        class MutatingValidatorPlugin(MockPlugin):
            def validate(self, workspace, receipt):
                result = super().validate(workspace, receipt)
                (workspace.root / "work" / "answer.txt").write_text(
                    "changed by validator\n", encoding="utf-8"
                )
                return result

        plugin = MutatingValidatorPlugin()
        agent = MockAgent(plugin)
        runtime = _runtime(plugin, agent, root)
        request = _request(source)
        _, approval = _preview_and_approve(runtime, request)
        report = runtime.execute(request, approval=approval)
        assert report.status == "blocked"
        assert "validator modified" in report.reason


def test_policy_blocks_before_plugin_or_agent_sees_request():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        source = root / "input.txt"
        source.write_text("input", encoding="utf-8")
        plugin = MockPlugin()
        agent = MockAgent(plugin)
        report = RuntimeOrchestrator(
            RuntimeRegistry((plugin,)), agent, root / "work-root"
        ).execute(_request(source, policy=_policy("prohibited")))
    assert report.status == "blocked"
    assert plugin.support_calls == 0
    assert agent.execute_calls == 0


def test_registry_tie_and_unsupported_fail_closed():
    with tempfile.TemporaryDirectory() as raw:
        source = Path(raw) / "input.txt"
        source.write_text("input", encoding="utf-8")
        tied = RuntimeRegistry((MockPlugin("zeta"), MockPlugin("alpha")))
        assert tied.select(_request(source)).decision.status == "blocked"
        unsupported = RuntimeRegistry((MockPlugin(support="unsupported"),))
        assert unsupported.select(_request(source)).decision.status == "unsupported"


def test_paths_symlinks_and_secret_environment_fail_closed():
    for unsafe in (
        "../escape", "/absolute", "C:\\absolute", "x/../escape",
        "file.txt:stream", "CON", "trailing. ",
    ):
        try:
            safe_relative_path(unsafe)
        except RuntimeSecurityError:
            pass
        else:
            raise AssertionError(f"unsafe path accepted: {unsafe}")
    clean = sanitize_environment(
        {
            "PATH": "safe", "SYSTEMROOT": "safe", "UNTIL_API_KEY": "secret",
            "SESSION_COOKIE": "secret", "AWS_ACCESS_KEY_ID": "secret",
            "OTHER": "ignored",
        },
        (
            "PATH", "SYSTEMROOT", "UNTIL_API_KEY", "SESSION_COOKIE",
            "AWS_ACCESS_KEY_ID",
        ),
    )
    assert clean == {"PATH": "safe", "SYSTEMROOT": "safe"}

    if hasattr(os, "symlink"):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.txt"
            link = root / "link.txt"
            source.write_text("input", encoding="utf-8")
            try:
                link.symlink_to(source)
            except OSError:
                return
            plugin = MockPlugin()
            report = RuntimeOrchestrator(
                RuntimeRegistry((plugin,)), MockAgent(plugin), root / "work-root"
            ).execute(_request(link))
            assert report.status == "blocked"
            assert "linked" in report.reason


def test_receipt_output_is_bounded_and_timeout_is_explicit():
    receipt = AgentReceipt(
        "timeout", stdout_summary="x" * 9000, stderr_summary="y" * 9000,
        reason="deadline exceeded",
    )
    assert len(receipt.stdout_summary) == 8192
    assert len(receipt.stderr_summary) == 8192
    assert receipt.status == "timeout"
    redacted = AgentReceipt(
        "failed", stdout_summary='{"api_key":"proof-secret"}',
        stderr_summary="Authorization: Bearer proof-secret",
        reason="session=proof-secret",
    )
    assert "proof-secret" not in redacted.stdout_summary
    assert "proof-secret" not in redacted.stderr_summary
    assert "proof-secret" not in redacted.reason


def test_golden_fixture_catalog_is_complete_and_safe():
    fixture_root = Path(__file__).parent / "runtime_fixtures"
    catalog = json.loads((fixture_root / "catalog.json").read_text(encoding="utf-8"))
    fixtures = catalog["fixtures"]
    assert len(fixtures) == 10
    families = {}
    for fixture in fixtures:
        families[fixture["family"]] = families.get(fixture["family"], 0) + 1
        assert (fixture_root / fixture["assignment_path"]).is_file()
        for relative in fixture["source_inputs"]:
            assert (fixture_root / relative).is_file()
        assert fixture["policy"]["network_allowed"] is False
        if not fixture["policy"]["approval_granted"]:
            assert fixture["expected"]["package_status"] != "pass"
    assert families == {
        "data_rmd": 2, "form": 2, "hdl": 2, "presentation": 2, "report": 2,
    }
    serialized = json.dumps(catalog).upper()
    assert not any(marker in serialized for marker in ("API_KEY", "PASSWORD", "BEARER"))


if __name__ == "__main__":
    test_approval_required_before_agent_execution()
    test_full_lifecycle_is_deterministic_and_attempts_are_unique()
    test_wrong_or_rejected_approval_never_executes()
    test_login_required_and_unavailable_are_distinct()
    test_validator_failure_gets_exactly_one_repair()
    test_workspace_escape_blocks_without_repair()
    test_default_boundary_denies_execution_and_sanitizes_test_boundary_environment()
    test_preview_and_preapproval_workspace_mutations_are_blocked()
    test_policy_tool_scope_and_packaging_integrity_are_enforced()
    test_protected_inputs_limited_use_and_validator_integrity_are_enforced()
    test_policy_blocks_before_plugin_or_agent_sees_request()
    test_registry_tie_and_unsupported_fail_closed()
    test_paths_symlinks_and_secret_environment_fail_closed()
    test_receipt_output_is_bounded_and_timeout_is_explicit()
    test_golden_fixture_catalog_is_complete_and_safe()
    print("LOCAL AGENT RUNTIME TESTS PASS")
