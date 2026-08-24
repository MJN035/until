"""Fail-closed path and execution-plan validation."""
from __future__ import annotations

import os
import hashlib
import stat
from pathlib import Path, PurePosixPath
from typing import Mapping

from .models import AgentJob, RunStep, WorkspacePlan


_SECRET_MARKERS = (
    "TOKEN", "SECRET", "PASSWORD", "PASSWD", "API_KEY", "COOKIE", "SESSION",
    "AUTH", "CREDENTIAL", "BEARER", "PRIVATE_KEY", "ACCESS_KEY",
)
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL", "CLOCK$",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class RuntimeSecurityError(ValueError):
    """Raised when a runtime plan crosses a declared safety boundary."""


def safe_relative_path(value: str) -> str:
    """Return a normalized POSIX relative path or reject it.

    Backslashes are normalized first so Windows traversal cannot be smuggled
    into a manifest that is later consumed on another platform.
    """
    if not value or "\x00" in value:
        raise RuntimeSecurityError("runtime path must be non-empty and contain no NUL")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or normalized.startswith("//"):
        raise RuntimeSecurityError(f"absolute runtime path is forbidden: {value}")
    for part in path.parts:
        stem = part.split(".", 1)[0].upper()
        if part in {"", ".", ".."} or ":" in part:
            raise RuntimeSecurityError(f"unsafe runtime path: {value}")
        if part.endswith((" ", ".")) or stem in _WINDOWS_RESERVED:
            raise RuntimeSecurityError(f"platform-ambiguous runtime path: {value}")
    return path.as_posix()


def is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse)


def reject_linked_ancestors(path: Path) -> None:
    """Reject any existing symlink or reparse point in a path chain."""
    absolute = path.absolute()
    for candidate in reversed((absolute, *absolute.parents)):
        if is_link_or_reparse(candidate):
            raise RuntimeSecurityError(f"linked path ancestor is forbidden: {candidate}")


def confined_path(root: Path, relative: str, *, must_exist: bool = False) -> Path:
    """Resolve a workspace path while rejecting symlink/reparse traversal."""
    relative = safe_relative_path(relative)
    root = root.resolve(strict=True)
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if is_link_or_reparse(current):
            raise RuntimeSecurityError(f"linked runtime path is forbidden: {relative}")
    resolved = candidate.resolve(strict=must_exist)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RuntimeSecurityError(f"runtime path escapes workspace: {relative}") from exc
    return resolved


#: 커널이 **절대 넘지 않는 실행 명령 천장**. 플러그인이 자기 검증 명령을
#: 선언하지만, 선언한 것을 그대로 믿지는 않는다 — 여기 없는 명령은 플러그인이
#: 뭐라 하든 실행되지 않는다. 셸(`sh`·`bash`·`cmd`)과 네트워크 도구(`curl`·`git`)를
#: 일부러 뺐다: 셸이 열리면 allowlist 자체가 무의미해지고, 네트워크 도구는
#: 샌드박스가 막더라도 애초에 검증 단계가 쓸 이유가 없다.
KERNEL_ALLOWED_COMMANDS = (
    "python", "python3", "pytest", "node", "npm", "ruff", "mypy",
)


def kernel_allowed(commands: tuple[str, ...]) -> tuple[str, ...]:
    """플러그인이 선언한 명령 중 커널 천장 안에 있는 것만 돌려준다."""
    ceiling = {os.path.basename(c).casefold() for c in KERNEL_ALLOWED_COMMANDS}
    return tuple(c for c in commands
                 if os.path.basename(c).casefold().removesuffix(".exe") in ceiling
                 or os.path.basename(c).casefold() in ceiling)


def validate_step(step: RunStep, allowed_commands: tuple[str, ...]) -> None:
    if step.network:
        raise RuntimeSecurityError("network access is disabled by default")
    command = os.path.basename(step.argv[0]).casefold()
    allowed = {os.path.basename(value).casefold() for value in allowed_commands}
    if command not in allowed:
        raise RuntimeSecurityError(f"command is not allowlisted: {step.argv[0]}")
    if command in {"python", "python.exe", "py", "py.exe"}:
        lowered = {arg.casefold() for arg in step.argv[1:]}
        if "-c" in lowered:
            raise RuntimeSecurityError("inline Python commands are forbidden")
    for value in (*step.inputs, *step.outputs):
        safe_relative_path(value)


def validate_plan(plan: WorkspacePlan, allowed_commands: tuple[str, ...]) -> None:
    paths = [safe_relative_path(value) for value in (*plan.directories, *plan.files)]
    if len({path.casefold() for path in paths}) != len(paths):
        raise RuntimeSecurityError("workspace plan paths collide")
    if "manifest.json" in paths:
        raise RuntimeSecurityError("manifest.json is reserved by the runtime kernel")
    for step in plan.steps:
        validate_step(step, allowed_commands)


def secret_environment_name(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in _SECRET_MARKERS)


def sanitize_environment(
    source: Mapping[str, str], allowlist: tuple[str, ...]
) -> dict[str, str]:
    allowed = {name.upper() for name in allowlist if not secret_environment_name(name)}
    return {
        name: value for name, value in source.items()
        if name.upper() in allowed and not secret_environment_name(name)
    }


def validate_job(workspace: Path, job: AgentJob) -> None:
    paths = (
        (job.prompt_path,) + job.readable_paths + job.editable_paths
        + job.expected_artifacts
    )
    canonical = [safe_relative_path(path) for path in paths]
    case_map = {}
    for path in canonical:
        previous = case_map.setdefault(path.casefold(), path)
        if previous != path:
            raise RuntimeSecurityError(
                "job paths collide on a case-insensitive filesystem"
            )
    for path in (job.prompt_path,) + job.readable_paths:
        confined_path(workspace, path, must_exist=True)
    for path in job.editable_paths + job.expected_artifacts:
        confined_path(workspace, path)
    editable = tuple(safe_relative_path(path).rstrip("/") for path in job.editable_paths)
    for path in editable:
        if path not in {"work", "artifacts"} and not path.startswith(("work/", "artifacts/")):
            raise RuntimeSecurityError(
                "agent editable paths must stay under work/ or artifacts/"
            )
    protected = tuple(
        safe_relative_path(path) for path in (job.prompt_path,) + job.readable_paths
    )
    for path in protected:
        if any(path == base or path.startswith(base + "/") for base in editable):
            raise RuntimeSecurityError("agent editable paths overlap protected inputs")
    for path in job.expected_artifacts:
        normalized = safe_relative_path(path)
        if not any(
            normalized == base or normalized.startswith(base + "/")
            for base in editable
        ):
            raise RuntimeSecurityError("expected artifact is outside editable paths")
    for name in job.environment_allowlist:
        if secret_environment_name(name):
            raise RuntimeSecurityError(f"secret environment variable is forbidden: {name}")


def validate_job_policy(job: AgentJob, policy) -> None:
    """정책이 **실제로 금지한 일을 하려는지**만 본다.

    예전에는 job이 정책의 `prohibited_uses`·`hard_constraints`·`required_actions`를
    문자열로 **복창하고 있는지**도 확인했다(사용자 지시 2026-08-20로 제거).
    그 검사는 안전을 더해 주지 않았다 — 같은 문자열을 job에 베껴 넣기만 하면
    통과하므로 행동을 막는 게 아니라 서식을 요구하는 것이었고, 실제로는
    기관 정책 기준선을 층에 넣는 순간 유일한 플러그인이 100% 차단됐다.

    남긴 검사는 전부 '복창'이 아니라 '행동'을 본다:
      - 정책이 승인한 도구 밖의 도구를 요구하는가
      - 정책이 금지한 용도를 job이 실제 목적(`intended_uses`)으로 삼는가
      - `ai_use=limited`인데 허용 범위 밖의 일을 하려는가
    AI 사용 금지(`prohibited`)·정책 불명은 여기 오기 전에
    `orchestrator._policy_block_reason`이 막는다.
    """
    approved = set(getattr(policy, "approved_tools", ()))
    if approved and not set(job.allowed_tools).issubset(approved):
        raise RuntimeSecurityError("agent job requests a tool not approved by policy")
    intended = set(job.intended_uses)
    prohibited_uses = set(getattr(policy, "prohibited_uses", ()))
    if intended & prohibited_uses:
        raise RuntimeSecurityError("agent job includes a prohibited use")
    if getattr(policy, "ai_use", "unclear") == "limited":
        allowed_uses = set(getattr(policy, "allowed_uses", ()))
        if not intended or not intended.issubset(allowed_uses):
            raise RuntimeSecurityError("agent job exceeds limited policy uses")


def validate_agent_plan(job: AgentJob, plan) -> None:
    if not set(plan.tool_kinds).issubset(set(job.allowed_tools)):
        raise RuntimeSecurityError("agent plan broadens the approved tool set")
    for path in plan.expected_changes:
        safe_relative_path(path)
        if not any(
            path == base or path.startswith(base.rstrip("/") + "/")
            for base in job.editable_paths
        ):
            raise RuntimeSecurityError("agent plan changes a non-editable path")


def validate_receipt(job: AgentJob, plan, receipt, changed: tuple[str, ...]) -> None:
    if not set(receipt.tool_kinds).issubset(set(plan.tool_kinds)):
        raise RuntimeSecurityError("agent receipt reports an unapproved tool")
    reported = tuple(sorted(safe_relative_path(path) for path in receipt.changed_files))
    if reported != tuple(sorted(changed)):
        raise RuntimeSecurityError("agent receipt does not match observed file changes")
    approved_changes = tuple(safe_relative_path(path) for path in plan.expected_changes)
    for path in changed:
        if not any(
            path == base or path.startswith(base.rstrip("/") + "/")
            for base in approved_changes
        ):
            raise RuntimeSecurityError("agent changed a file outside the approved plan")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_workspace(root: Path) -> dict[str, str]:
    root = root.resolve(strict=True)
    snapshot = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                if entry.is_symlink() or is_link_or_reparse(path):
                    raise RuntimeSecurityError(
                        f"linked workspace entry is forbidden: {relative}"
                    )
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    snapshot[relative] = _sha256(path)
    return dict(sorted(snapshot.items()))


def unauthorized_changes(
    before: Mapping[str, str], after: Mapping[str, str], editable_paths: tuple[str, ...]
) -> tuple[str, ...]:
    allowed = tuple(safe_relative_path(path).rstrip("/") for path in editable_paths)
    changed = {
        path for path in set(before) | set(after) if before.get(path) != after.get(path)
    }

    def permitted(path: str) -> bool:
        return any(path == base or path.startswith(base + "/") for base in allowed)

    return tuple(sorted(path for path in changed if not permitted(path)))
