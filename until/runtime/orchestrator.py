"""Local-agent plan→approval→execute→validate→repair lifecycle."""
from __future__ import annotations

from pathlib import Path

from .models import (
    Approval,
    RuntimeReport,
    RuntimeRequest,
    SubmissionBundle,
    ValidationFinding,
    ValidationResult,
)
from .local_agent import AgentContractError, LocalAgentController
from .registry import RuntimeRegistry
from .security import (
    RuntimeSecurityError,
    kernel_allowed,
    confined_path,
    snapshot_workspace,
    unauthorized_changes,
    validate_agent_plan,
    validate_job,
    validate_job_policy,
    validate_receipt,
)
from .workspace import WorkspaceManager, sha256_file


def _policy_block_reason(policy) -> str:
    if getattr(policy, "ai_use", "unclear") == "prohibited":
        return "assignment policy prohibits AI runtime use"
    if getattr(policy, "conflicts", ()):
        return "assignment policy contains unresolved conflicts"
    if not getattr(policy, "executable", False):
        return "assignment policy is unclear"
    return ""


def _observe(plugin, run_result) -> None:
    """검증 명령 결과를 플러그인에 넘긴다 — 받을 준비가 된 플러그인에만.

    `RuntimePlugin.validate(workspace, receipt)` 시그니처를 바꾸지 않는다.
    실행 단계를 안 쓰는 플러그인(대부분)이 새 인자 때문에 깨질 이유가 없다.
    """
    hook = getattr(plugin, "observe_run", None)
    if callable(hook):
        hook(run_result)


def validate_bundle(workspace, bundle: SubmissionBundle) -> SubmissionBundle:
    checked = []
    for item in bundle.files:
        path = confined_path(workspace.root, item.path, must_exist=True)
        if not path.is_file():
            raise RuntimeSecurityError(f"submission artifact is not a file: {item.path}")
        digest = sha256_file(path)
        size = path.stat().st_size
        if item.sha256 != digest or item.size != size:
            raise RuntimeSecurityError(f"submission artifact fingerprint mismatch: {item.path}")
        checked.append(item)
    return SubmissionBundle(bundle.assignment_id, tuple(checked), bundle.missing)


class RuntimeOrchestrator:
    def __init__(
        self, registry: RuntimeRegistry, agent,
        work_root: Path = Path("_until_work"), *, controller=None,
    ):
        self.registry = registry
        self.agent = agent
        self.workspaces = WorkspaceManager(work_root)
        self.controller = controller or LocalAgentController()
        self._pending = {}

    def execute(
        self, request: RuntimeRequest, *, approval: Approval | None = None
    ) -> RuntimeReport:
        policy_reason = _policy_block_reason(request.policy)
        if policy_reason:
            return RuntimeReport("blocked", reason=policy_reason)
        if approval is not None:
            return self._execute_approved(request, approval)
        selection = self.registry.select(request)
        if selection.plugin is None:
            return RuntimeReport(selection.decision.status, reason=selection.decision.reason)
        plugin = selection.plugin
        try:
            plan = plugin.prepare(request)
            # 실행 단계를 쓰는 플러그인은 자기 명령을 선언한다. 선언을 그대로 믿지
            # 않고 커널 천장(`KERNEL_ALLOWED_COMMANDS`)으로 한 번 더 조인다 —
            # 플러그인이 셸을 열어 달라고 해도 열리지 않는다.
            allowed = kernel_allowed(tuple(getattr(plugin, "allowed_commands", ()) or ()))
            workspace = self.workspaces.materialize(
                plugin.name, request, plan, allowed)
            job = plugin.build_job(workspace)
            if job.assignment_id != request.assignment_id:
                raise RuntimeSecurityError("agent job assignment does not match request")
            validate_job(workspace.root, job)
            validate_job_policy(job, request.policy)
            sealed = snapshot_workspace(workspace.root)
            availability, agent_plan = self.controller.preview(self.agent, job)
            if agent_plan is None:
                return RuntimeReport(
                    "blocked", plugin.name,
                    reason=availability.reason or availability.status,
                    workspace=workspace, availability=availability,
                )
            if snapshot_workspace(workspace.root) != sealed:
                raise RuntimeSecurityError("local agent modified workspace during preview")
            validate_agent_plan(job, agent_plan)
            self._pending[workspace.run_id] = (
                request, plugin, workspace, job, availability, agent_plan, sealed, plan,
            )
            return RuntimeReport(
                "prepared", plugin.name, workspace=workspace,
                availability=availability, agent_plan=agent_plan,
            )
        except (AgentContractError, OSError, ValueError, RuntimeSecurityError) as exc:
            return RuntimeReport("blocked", plugin.name, reason=str(exc))

    def _execute_approved(self, request: RuntimeRequest, approval: Approval) -> RuntimeReport:
        pending = self._pending.pop(approval.run_id, None)
        if pending is None:
            return RuntimeReport("blocked", reason="approval run is missing or already consumed")
        (stored_request, plugin, workspace, job, availability, agent_plan,
         sealed, plan) = pending
        if stored_request != request:
            return RuntimeReport(
                "blocked", plugin.name,
                reason="approval request does not match the prepared run",
                workspace=workspace, availability=availability, agent_plan=agent_plan,
            )
        try:
            before = snapshot_workspace(workspace.root)
            if before != sealed:
                raise RuntimeSecurityError("prepared workspace changed before approval")
            receipt = self.controller.execute(self.agent, job, agent_plan, approval)
            after = snapshot_workspace(workspace.root)
            # 검증 명령은 에이전트의 변경 범위를 확인한 **뒤에** 돌린다. 순서를
            # 바꾸면 테스트가 만든 부산물(__pycache__ 등)이 '에이전트가 허용
            # 범위 밖을 고쳤다'로 오인된다.
            run_result = self._run_steps(plan, job, workspace, receipt, before, after)
            # 검증 명령이 만든 부산물(`__pycache__` 등)을 포함한 상태. 재시도
            # 라운드의 **기준선**은 이것이어야 한다 — `after`(단계 이전)를 쓰면
            # 우리가 돌린 테스트의 부산물이 다음 라운드에서 '에이전트가 허용
            # 범위 밖을 고쳤다'로 뒤집혀 나온다(실측: 테스트 실패 → 재시도 시
            # workspace_escape·agent_plan_scope 오검출).
            post_run = snapshot_workspace(workspace.root)
            _observe(plugin, run_result)
            validation = self._validate_attempt(
                plugin, workspace, job, agent_plan, receipt, before, after
            )

            repairable = (
                validation.blocked
                and job.max_repair_attempts == 1
                and receipt.status == "succeeded"
                and not any(
                    finding.code in {"workspace_escape", "agent_plan_scope"}
                    for finding in validation.findings
                )
            )
            if repairable:
                feedback = plugin.repair_feedback(validation)
                repair_before = post_run
                receipt = self.controller.continue_job(self.agent, receipt, feedback)
                repair_after = snapshot_workspace(workspace.root)
                run_result = self._run_steps(
                    plan, job, workspace, receipt, repair_before, repair_after)
                _observe(plugin, run_result)
                validation = self._validate_attempt(
                    plugin, workspace, job, agent_plan, receipt,
                    repair_before, repair_after,
                )
            if validation.blocked:
                return RuntimeReport(
                    "blocked", plugin.name, workspace=workspace,
                    availability=availability, agent_plan=agent_plan,
                    receipt=receipt, run_result=run_result, validation=validation,
                )
            validated_snapshot = snapshot_workspace(workspace.root)
            bundle = plugin.package(workspace, validation)
            if snapshot_workspace(workspace.root) != validated_snapshot:
                raise RuntimeSecurityError("packaging modified validated workspace files")
            bundle = validate_bundle(workspace, bundle)
            if bundle.assignment_id != request.assignment_id or bundle.missing:
                return RuntimeReport(
                    "blocked", plugin.name, reason="submission bundle is incomplete",
                    workspace=workspace, availability=availability,
                    agent_plan=agent_plan, receipt=receipt, run_result=run_result,
                    validation=validation,
                )
            return RuntimeReport(
                "ready", plugin.name, workspace=workspace,
                availability=availability, agent_plan=agent_plan,
                receipt=receipt, run_result=run_result, validation=validation,
                bundle=bundle,
            )
        except (AgentContractError, OSError, ValueError, RuntimeSecurityError) as exc:
            return RuntimeReport(
                "blocked", plugin.name, reason=str(exc), workspace=workspace,
                availability=availability, agent_plan=agent_plan,
            )

    def _run_steps(self, plan, job, workspace, receipt, before, after):
        """플러그인이 미리 정해 둔 검증 명령을 격리 안에서 돌린다.

        에이전트가 실패했으면 돌리지 않는다 — 깨진 산출물에 테스트를 걸어 봐야
        원인이 두 겹이 될 뿐이고, 사람이 볼 것은 에이전트 실패 쪽이다.

        결과는 플러그인이 `observe_run`으로 받아 자기 검증에 쓴다. 이 메서드는
        판정하지 않는다 — '테스트 실패'가 차단인지 경고인지는 과제 유형이 정할
        일이지 커널이 정할 일이 아니다.
        """
        steps = tuple(getattr(plan, "steps", ()) or ())
        if not steps or not getattr(plan, "runnable", False):
            return None
        if receipt.status != "succeeded":
            return None
        try:
            return self.controller.run_steps(
                steps, workspace.root, job.environment_allowlist)
        except (AgentContractError, OSError, ValueError) as exc:
            from .models import RunResult
            return RunResult("blocked", skipped_reason=str(exc))

    @staticmethod
    def _validate_attempt(plugin, workspace, job, agent_plan, receipt, before, after):
        findings = []
        if receipt.status != "succeeded":
            findings.append(ValidationFinding(
                "block", f"agent_{receipt.status}",
                receipt.reason or "local agent execution did not succeed",
            ))
        outside = unauthorized_changes(before, after, job.editable_paths)
        changed = tuple(sorted(
            path for path in set(before) | set(after)
            if before.get(path) != after.get(path)
        ))
        try:
            validate_receipt(job, agent_plan, receipt, changed)
        except RuntimeSecurityError as exc:
            findings.append(ValidationFinding(
                "block", "agent_plan_scope", str(exc),
            ))
        if outside:
            findings.append(ValidationFinding(
                "block", "workspace_escape",
                "local agent modified files outside the editable allowlist",
                outside[0],
            ))
        validation_before = snapshot_workspace(workspace.root)
        plugin_validation = plugin.validate(workspace, receipt)
        if snapshot_workspace(workspace.root) != validation_before:
            raise RuntimeSecurityError("validator modified workspace files")
        return ValidationResult(tuple(findings) + plugin_validation.findings)
