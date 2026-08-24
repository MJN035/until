"""Atomic deterministic workspace materialization."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
import uuid
from pathlib import Path

from .manifest import manifest_data, plan_id_for
from .models import (
    InputFingerprint,
    RuntimeRequest,
    RuntimeWorkspace,
    WorkspacePlan,
)
from .security import (
    RuntimeSecurityError,
    confined_path,
    is_link_or_reparse,
    reject_linked_ancestors,
    safe_relative_path,
    validate_plan,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _promote_workspace(staging: Path, final_root: Path) -> None:
    """Atomically promote a staging directory, tolerating brief Windows locks."""
    last_error = None
    for delay in (0.0, 0.01, 0.02, 0.05, 0.1, 0.2):
        if delay:
            time.sleep(delay)
        try:
            os.replace(staging, final_root)
            return
        except FileExistsError as exc:
            raise RuntimeSecurityError(
                f"runtime workspace collision: {final_root.name}"
            ) from exc
        except PermissionError as exc:
            if final_root.exists():
                raise RuntimeSecurityError(
                    f"runtime workspace collision: {final_root.name}"
                ) from exc
            if os.name != "nt":
                raise
            last_error = exc
    raise last_error


def fingerprint_inputs(inputs: tuple[Path, ...]) -> tuple[tuple[Path, InputFingerprint], ...]:
    output = []
    used_names: set[str] = set()
    for index, source in enumerate(inputs):
        reject_linked_ancestors(source)
        if is_link_or_reparse(source):
            raise RuntimeSecurityError(f"linked input is forbidden: {source}")
        if not source.is_file():
            raise RuntimeSecurityError(f"runtime input must be a regular file: {source}")
        name = source.name
        target_name = name if name.casefold() not in used_names else f"{index:03d}-{name}"
        used_names.add(target_name.casefold())
        relative = safe_relative_path(f"inputs/{target_name}")
        output.append((source, InputFingerprint(relative, sha256_file(source), source.stat().st_size)))
    return tuple(output)


def deterministic_plan_id(
    plugin_name: str,
    request: RuntimeRequest,
    plan: WorkspacePlan,
    inputs: tuple[tuple[Path, InputFingerprint], ...],
) -> str:
    return plan_id_for(
        plugin_name, request, plan, tuple(fingerprint for _, fingerprint in inputs)
    )


class WorkspaceManager:
    def __init__(self, base_root: Path):
        self.base_root = Path(base_root)

    def materialize(
        self,
        plugin_name: str,
        request: RuntimeRequest,
        plan: WorkspacePlan,
        allowed_commands: tuple[str, ...] = (),
    ) -> RuntimeWorkspace:
        validate_plan(plan, allowed_commands)
        inputs = fingerprint_inputs(request.inputs)
        plan_id = deterministic_plan_id(plugin_name, request, plan, inputs)
        run_id = uuid.uuid4().hex
        assignment_key = hashlib.sha256(
            request.assignment_id.encode("utf-8")
        ).hexdigest()[:16]
        runtime_root = self.base_root / "runtime" / assignment_key
        reject_linked_ancestors(self.base_root)
        runtime_root.mkdir(parents=True, exist_ok=True)
        if is_link_or_reparse(runtime_root):
            raise RuntimeSecurityError("runtime root cannot be linked")
        final_root = runtime_root / run_id
        manifest_path = final_root / "manifest.json"
        staging = Path(tempfile.mkdtemp(prefix=f".{run_id}-", dir=runtime_root))
        try:
            for directory in plan.directories:
                confined_path(staging, directory).mkdir(parents=True, exist_ok=True)
            for relative in plan.files:
                target = confined_path(staging, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.touch(exist_ok=False)
            for source, fingerprint in inputs:
                target = confined_path(staging, fingerprint.path)
                target.parent.mkdir(parents=True, exist_ok=True)
                source_identity = source.stat(follow_symlinks=False)
                if is_link_or_reparse(source):
                    raise RuntimeSecurityError(f"linked input is forbidden: {source}")
                shutil.copyfile(source, target, follow_symlinks=False)
                if is_link_or_reparse(target):
                    raise RuntimeSecurityError("copied input became a linked file")
                current_identity = source.stat(follow_symlinks=False)
                if (
                    source_identity.st_dev,
                    source_identity.st_ino,
                    source_identity.st_size,
                    source_identity.st_mtime_ns,
                ) != (
                    current_identity.st_dev,
                    current_identity.st_ino,
                    current_identity.st_size,
                    current_identity.st_mtime_ns,
                ):
                    raise RuntimeSecurityError(f"input changed while copying: {source}")
                if sha256_file(target) != fingerprint.sha256:
                    raise RuntimeSecurityError(f"input changed while copying: {source}")
                target.chmod(stat.S_IREAD)
            manifest = manifest_data(
                plugin_name, request.assignment_id, plan_id, run_id,
                tuple(item for _, item in inputs),
            )
            (staging / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            _promote_workspace(staging, final_root)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise
        return RuntimeWorkspace(
            final_root, manifest_path, plan_id, run_id,
            tuple(item for _, item in inputs),
        )
