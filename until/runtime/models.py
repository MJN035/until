"""Shared immutable models for assignment runtimes.

The runtime deliberately uses plain standard-library values so manifests and
reports can be serialized without importing an execution or web framework.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


SUPPORT_STATUSES = {"supported", "unsupported", "blocked"}
RUN_STATUSES = {"dry_run", "succeeded", "failed", "tool_missing", "blocked"}
AGENT_AVAILABILITY_STATUSES = {"unavailable", "login_required", "ready", "busy", "failed"}
AGENT_RECEIPT_STATUSES = {
    "succeeded", "failed", "timeout", "cancelled", "usage_limited", "login_required"
}
FINDING_LEVELS = {"pass", "warn", "block"}
REPORT_STATUSES = {"unsupported", "blocked", "prepared", "failed", "ready"}
MAX_RECEIPT_SUMMARY_CHARS = 8192


_SECRET_TEXT_PATTERNS = (
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)\S+"), r"\1[REDACTED]"),
    (re.compile(
        r"(?i)([\"']?(?:api[_-]?key|token|password|secret|cookie|session)"
        r"[\"']?\s*[:=]\s*[\"']?)[^\s,\"'}]+"
    ), r"\1[REDACTED]"),
)


def redact_secret_text(value: str) -> str:
    for pattern, replacement in _SECRET_TEXT_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    frozen = {}
    for key, item in sorted(copy.deepcopy(dict(value)).items()):
        if isinstance(item, Mapping):
            item = _freeze_mapping(item)
        elif isinstance(item, list):
            item = tuple(item)
        frozen[str(key)] = item
    return MappingProxyType(frozen)


def canonical_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): canonical_json(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [canonical_json(item) for item in value]
    if hasattr(value, "to_dict"):
        return canonical_json(value.to_dict())
    if hasattr(value, "__dataclass_fields__"):
        return canonical_json(asdict(value))
    if isinstance(value, Path):
        return value.as_posix()
    return value


def stable_fingerprint(value: Any) -> str:
    payload = json.dumps(
        canonical_json(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class SupportDecision:
    status: str
    reason: str
    priority: int = 0

    def __post_init__(self):
        if self.status not in SUPPORT_STATUSES:
            raise ValueError(f"unknown support status: {self.status}")


@dataclass(frozen=True)
class RuntimeRequest:
    assignment_id: str
    spec: Mapping[str, Any]
    route: Any
    policy: Any
    inputs: tuple[Path, ...] = ()
    decisions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not self.assignment_id.strip():
            raise ValueError("assignment_id is required")
        object.__setattr__(self, "spec", _freeze_mapping(self.spec))
        object.__setattr__(self, "decisions", _freeze_mapping(self.decisions))
        object.__setattr__(self, "inputs", tuple(Path(path) for path in self.inputs))


@dataclass(frozen=True)
class RunStep:
    argv: tuple[str, ...]
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    timeout_seconds: int = 30
    network: bool = False
    stdout_limit_bytes: int = 65536
    stderr_limit_bytes: int = 65536

    def __post_init__(self):
        if not self.argv or not self.argv[0].strip():
            raise ValueError("run step argv is required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.stdout_limit_bytes <= 0 or self.stderr_limit_bytes <= 0:
            raise ValueError("output limits must be positive")


@dataclass(frozen=True)
class WorkspacePlan:
    directories: tuple[str, ...] = ("inputs", "work", "artifacts", "logs")
    files: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    steps: tuple[RunStep, ...] = ()
    runnable: bool = False
    reason: str = ""


@dataclass(frozen=True)
class InputFingerprint:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class RuntimeWorkspace:
    root: Path
    manifest_path: Path
    plan_id: str
    run_id: str
    inputs: tuple[InputFingerprint, ...]


@dataclass(frozen=True)
class AgentAvailability:
    status: str
    name: str
    version: str = ""
    reason: str = ""

    def __post_init__(self):
        if self.status not in AGENT_AVAILABILITY_STATUSES:
            raise ValueError(f"unknown agent availability: {self.status}")


@dataclass(frozen=True)
class AgentJob:
    assignment_id: str
    prompt_path: str
    readable_paths: tuple[str, ...]
    editable_paths: tuple[str, ...]
    allowed_tools: tuple[str, ...] = ()
    intended_uses: tuple[str, ...] = ()
    forbidden_actions: tuple[str, ...] = ()
    policy_requirements: tuple[str, ...] = ()
    expected_artifacts: tuple[str, ...] = ()
    environment_allowlist: tuple[str, ...] = ()
    timeout_seconds: int = 300
    max_repair_attempts: int = 1

    def __post_init__(self):
        if not self.assignment_id.strip():
            raise ValueError("assignment_id is required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_repair_attempts not in {0, 1}:
            raise ValueError("max_repair_attempts must be 0 or 1")

    @property
    def fingerprint(self) -> str:
        return stable_fingerprint(self)


@dataclass(frozen=True)
class AgentPlan:
    job_fingerprint: str
    summary: str
    expected_changes: tuple[str, ...] = ()
    tool_kinds: tuple[str, ...] = ()

    @property
    def fingerprint(self) -> str:
        return stable_fingerprint(self)


@dataclass(frozen=True)
class Approval:
    plan_fingerprint: str
    approved: bool
    run_id: str
    approval_id: str = ""


@dataclass(frozen=True)
class AgentReceipt:
    status: str
    changed_files: tuple[str, ...] = ()
    tool_kinds: tuple[str, ...] = ()
    exit_code: int | None = None
    stdout_summary: str = ""
    stderr_summary: str = ""
    reason: str = ""

    def __post_init__(self):
        if self.status not in AGENT_RECEIPT_STATUSES:
            raise ValueError(f"unknown agent receipt status: {self.status}")
        object.__setattr__(
            self, "stdout_summary",
            redact_secret_text(self.stdout_summary)[:MAX_RECEIPT_SUMMARY_CHARS],
        )
        object.__setattr__(
            self, "stderr_summary",
            redact_secret_text(self.stderr_summary)[:MAX_RECEIPT_SUMMARY_CHARS],
        )
        object.__setattr__(
            self, "reason", redact_secret_text(self.reason)[:MAX_RECEIPT_SUMMARY_CHARS]
        )


@dataclass(frozen=True)
class AgentFeedback:
    codes: tuple[str, ...]
    messages: tuple[str, ...]


@dataclass(frozen=True)
class Artifact:
    path: str
    kind: str = "file"
    sha256: str = ""


@dataclass(frozen=True)
class RunResult:
    status: str
    exit_code: int | None = None
    stdout_summary: str = ""
    stderr_summary: str = ""
    artifacts: tuple[Artifact, ...] = ()
    skipped_reason: str = ""

    def __post_init__(self):
        if self.status not in RUN_STATUSES:
            raise ValueError(f"unknown run status: {self.status}")


@dataclass(frozen=True)
class ValidationFinding:
    level: str
    code: str
    message: str
    artifact: str = ""

    def __post_init__(self):
        if self.level not in FINDING_LEVELS:
            raise ValueError(f"unknown finding level: {self.level}")


@dataclass(frozen=True)
class ValidationResult:
    findings: tuple[ValidationFinding, ...] = ()

    @property
    def blocked(self) -> bool:
        return any(finding.level == "block" for finding in self.findings)


@dataclass(frozen=True)
class SubmissionFile:
    path: str
    mime_type: str
    sha256: str
    size: int


@dataclass(frozen=True)
class SubmissionBundle:
    assignment_id: str
    files: tuple[SubmissionFile, ...]
    missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeReport:
    status: str
    plugin_name: str = ""
    reason: str = ""
    workspace: RuntimeWorkspace | None = None
    availability: AgentAvailability | None = None
    agent_plan: AgentPlan | None = None
    receipt: AgentReceipt | None = None
    run_result: RunResult | None = None
    validation: ValidationResult | None = None
    bundle: SubmissionBundle | None = None

    def __post_init__(self):
        if self.status not in REPORT_STATUSES:
            raise ValueError(f"unknown report status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        if self.workspace:
            value["workspace"]["root"] = str(self.workspace.root)
            value["workspace"]["manifest_path"] = str(self.workspace.manifest_path)
        return value
