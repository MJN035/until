"""Canonical runtime manifest and deterministic plan identity."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from .models import InputFingerprint, RuntimeRequest, WorkspacePlan, canonical_json


def plan_id_for(
    plugin_name: str,
    request: RuntimeRequest,
    plan: WorkspacePlan,
    inputs: tuple[InputFingerprint, ...],
) -> str:
    payload = {
        "plugin": plugin_name,
        "assignment_id": request.assignment_id,
        "spec": canonical_json(request.spec),
        "route": canonical_json(request.route),
        "policy": canonical_json(request.policy),
        "decisions": canonical_json(request.decisions),
        "plan": asdict(plan),
        "inputs": [asdict(item) for item in inputs],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def manifest_data(
    plugin_name: str,
    assignment_id: str,
    plan_id: str,
    run_id: str,
    inputs: tuple[InputFingerprint, ...],
) -> dict:
    return {
        "version": 1,
        "plugin": plugin_name,
        "assignment_id": assignment_id,
        "plan_id": plan_id,
        "run_id": run_id,
        "inputs": [asdict(item) for item in inputs],
    }
