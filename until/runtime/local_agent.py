"""Official local-agent contract and approval boundary."""
from __future__ import annotations

import os
from typing import Protocol

from .models import (
    AgentAvailability,
    AgentFeedback,
    AgentJob,
    AgentPlan,
    AgentReceipt,
    Approval,
)
from .security import sanitize_environment


class LocalAgent(Protocol):
    name: str

    def probe(self) -> AgentAvailability: ...

    def plan(self, job: AgentJob) -> AgentPlan: ...

    def execute(self, job: AgentJob, approval: Approval) -> AgentReceipt: ...

    def continue_job(self, receipt: AgentReceipt, feedback: AgentFeedback) -> AgentReceipt: ...


class AgentContractError(ValueError):
    pass


class ExecutionBoundary(Protocol):
    filesystem_isolated: bool
    environment_isolated: bool
    network_isolated: bool

    def preview(
        self, agent: LocalAgent, job: AgentJob, environment: dict[str, str],
    ) -> tuple[AgentAvailability, AgentPlan | None]: ...

    def execute(
        self, agent: LocalAgent, job: AgentJob, approval: Approval,
        environment: dict[str, str],
    ) -> AgentReceipt: ...

    def continue_job(
        self, agent: LocalAgent, receipt: AgentReceipt, feedback: AgentFeedback,
        environment: dict[str, str],
    ) -> AgentReceipt: ...

    def run_step(self, step, workspace_root, environment: dict[str, str]): ...


class DisabledExecutionBoundary:
    """Phase 0–1 default: no official isolated CLI adapter, so execution is denied."""

    filesystem_isolated = False
    environment_isolated = False
    network_isolated = False

    def preview(self, agent, job, environment):
        raise AgentContractError("isolated local-agent preview boundary is unavailable")

    def execute(self, agent, job, approval, environment):
        raise AgentContractError("isolated local-agent execution boundary is unavailable")

    def continue_job(self, agent, receipt, feedback, environment):
        raise AgentContractError("isolated local-agent execution boundary is unavailable")

    def run_step(self, step, workspace_root, environment):
        raise AgentContractError("isolated execution boundary is unavailable")


class LocalAgentController:
    """Enforce readiness and explicit plan-bound approval before execution."""

    def __init__(self, boundary: ExecutionBoundary | None = None, *, environ=None):
        self.boundary = boundary or DisabledExecutionBoundary()
        self.environ = os.environ if environ is None else environ

    def preview(
        self, agent: LocalAgent, job: AgentJob
    ) -> tuple[AgentAvailability, AgentPlan | None]:
        self._require_isolation()
        availability, plan = self.boundary.preview(
            agent, job, sanitize_environment(self.environ, job.environment_allowlist)
        )
        if availability.status != "ready":
            if plan is not None:
                raise AgentContractError("unavailable local agent returned a plan")
            return availability, None
        if plan is None:
            raise AgentContractError("ready local agent did not return a plan")
        if plan.job_fingerprint != job.fingerprint:
            raise AgentContractError("agent plan is not bound to the current job")
        return availability, plan

    def execute(
        self, agent: LocalAgent, job: AgentJob, plan: AgentPlan, approval: Approval
    ) -> AgentReceipt:
        if not approval.approved:
            raise AgentContractError("local agent execution was not approved")
        if approval.plan_fingerprint != plan.fingerprint:
            raise AgentContractError("approval does not match the current agent plan")
        self._require_isolation()
        environment = sanitize_environment(self.environ, job.environment_allowlist)
        return self.boundary.execute(agent, job, approval, environment)

    def continue_job(
        self, agent: LocalAgent, receipt: AgentReceipt, feedback: AgentFeedback
    ) -> AgentReceipt:
        self._require_isolation()
        return self.boundary.continue_job(
            agent, receipt, feedback,
            sanitize_environment(self.environ, ()),
        )

    def run_steps(self, steps, workspace_root, environment_allowlist=()):
        """검증 명령들을 순서대로 실행하고 **하나의 RunResult**로 합친다.

        누가 명령을 정하는가가 이 기능의 안전선이다: 단계는 플러그인이
        `WorkspacePlan`으로 **에이전트가 돌기 전에** 정해 두고, 커널이
        `validate_plan`으로 allowlist·네트워크·경로를 이미 검사했다. 에이전트가
        쓴 파일이 명령줄이 되는 경로는 없다.

        실패하면 거기서 멈춘다 — 첫 실패의 원인을 사람이 보게 하는 게 낫고,
        깨진 상태에서 다음 단계를 돌리면 원인이 뒤섞인다.
        """
        from .models import RunResult

        if not steps:
            return None
        self._require_isolation()
        environment = sanitize_environment(self.environ, tuple(environment_allowlist))
        outs, errs, artifacts = [], [], []
        for step in steps:
            result = self.boundary.run_step(step, workspace_root, environment)
            outs.append(_clip(getattr(result, "stdout", ""), step.stdout_limit_bytes))
            errs.append(_clip(getattr(result, "stderr", ""), step.stderr_limit_bytes))
            if not getattr(result, "launched", True):
                return RunResult("tool_missing", None, "\n".join(outs), "\n".join(errs),
                                 skipped_reason=f"명령을 찾지 못했습니다: {step.argv[0]}")
            if getattr(result, "timed_out", False):
                return RunResult("failed", None, "\n".join(outs), "\n".join(errs),
                                 skipped_reason=f"시간 초과: {' '.join(step.argv)}")
            artifacts.extend(_artifacts_of(step, workspace_root))
            if result.exit_code not in (0, None):
                return RunResult("failed", result.exit_code,
                                 "\n".join(outs), "\n".join(errs),
                                 tuple(artifacts))
        return RunResult("succeeded", 0, "\n".join(outs), "\n".join(errs),
                         tuple(artifacts))

    def _require_isolation(self) -> None:
        if not all((
            self.boundary.filesystem_isolated,
            self.boundary.environment_isolated,
            self.boundary.network_isolated,
        )):
            raise AgentContractError(
                "isolated local-agent execution boundary is unavailable"
            )


def _clip(text: str, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "…(잘림)"


def _artifacts_of(step, workspace_root) -> list:
    """단계가 만들기로 한 산출물의 지문. 없으면 조용히 건너뛴다."""
    from pathlib import Path

    from .models import Artifact
    from .workspace import sha256_file

    out = []
    for relpath in getattr(step, "outputs", ()) or ():
        path = Path(workspace_root) / relpath
        if path.is_file():
            out.append(Artifact(relpath, "file", sha256_file(path)))
    return out
