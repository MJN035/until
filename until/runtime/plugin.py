"""Plugin protocol implemented by each assignment runtime."""
from __future__ import annotations

from typing import Protocol

from .models import (
    AgentFeedback,
    AgentJob,
    AgentReceipt,
    RuntimeRequest,
    RuntimeWorkspace,
    SubmissionBundle,
    SupportDecision,
    ValidationResult,
    WorkspacePlan,
)


class RuntimePlugin(Protocol):
    name: str

    def supports(self, request: RuntimeRequest) -> SupportDecision: ...

    def prepare(self, request: RuntimeRequest) -> WorkspacePlan: ...

    def build_job(self, workspace: RuntimeWorkspace) -> AgentJob: ...

    def validate(
        self, workspace: RuntimeWorkspace, receipt: AgentReceipt
    ) -> ValidationResult: ...

    def repair_feedback(self, validation: ValidationResult) -> AgentFeedback: ...

    def package(
        self, workspace: RuntimeWorkspace, validation: ValidationResult
    ) -> SubmissionBundle: ...
