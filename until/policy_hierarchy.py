"""Resolve institutional→course→assignment policy with auditable precedence.

Specific policies may define *how* AI is used, but higher-level non-overridable
privacy, integrity and disclosure duties remain cumulative.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


SCOPE_PRIORITY = {
    "institution": 10,
    "college": 20,
    "department": 30,
    "course": 40,
    "assignment": 50,
}
VALID_AI_USE = {"prohibited", "limited", "allowed", "unclear"}


@dataclass(frozen=True)
class PolicySource:
    source_id: str
    title: str
    url: str
    effective_date: str = ""
    excerpt: str = ""


@dataclass(frozen=True)
class PolicyLayer:
    scope: str
    scope_id: str
    ai_use: str = "unclear"
    allowed_uses: tuple[str, ...] = ()
    prohibited_uses: tuple[str, ...] = ()
    required_actions: tuple[str, ...] = ()
    hard_constraints: tuple[str, ...] = ()
    approved_tools: tuple[str, ...] = ()
    source: PolicySource | None = None

    def __post_init__(self):
        if self.scope not in SCOPE_PRIORITY:
            raise ValueError(f"unknown policy scope: {self.scope}")
        if self.ai_use not in VALID_AI_USE:
            raise ValueError(f"unknown ai_use: {self.ai_use}")


@dataclass(frozen=True)
class PolicyConflict:
    code: str
    message: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class EffectivePolicy:
    ai_use: str
    controlling_scope: str
    allowed_uses: tuple[str, ...]
    prohibited_uses: tuple[str, ...]
    required_actions: tuple[str, ...]
    hard_constraints: tuple[str, ...]
    approved_tools: tuple[str, ...]
    sources: tuple[PolicySource, ...]
    conflicts: tuple[PolicyConflict, ...]

    @property
    def executable(self) -> bool:
        return self.ai_use != "unclear" and not self.conflicts

    def to_dict(self) -> dict:
        return asdict(self)


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(v for v in values if v))


def resolve_policy(layers: Iterable[PolicyLayer]) -> EffectivePolicy:
    """Resolve policy layers without allowing local text to waive safety floors.

    AI-use mode and allowed uses come from the most-specific explicit layer.
    Prohibitions, required actions and hard constraints accumulate. Approved-tool
    lists become an intersection, so a child scope can narrow but not broaden one.
    Contradictory explicit rules at the same controlling scope fail closed.
    """
    ordered = sorted(layers, key=lambda layer: SCOPE_PRIORITY[layer.scope])
    sources = _unique_sources(layer.source for layer in ordered if layer.source)
    explicit = [layer for layer in ordered if layer.ai_use != "unclear"]
    conflicts: list[PolicyConflict] = []
    controlling_scope = ""
    ai_use = "unclear"
    allowed: tuple[str, ...] = ()
    if explicit:
        top_priority = max(SCOPE_PRIORITY[layer.scope] for layer in explicit)
        controlling = [layer for layer in explicit
                       if SCOPE_PRIORITY[layer.scope] == top_priority]
        modes = {layer.ai_use for layer in controlling}
        controlling_scope = controlling[0].scope
        if len(modes) > 1:
            conflicts.append(PolicyConflict(
                "same_scope_ai_conflict",
                "같은 적용 범위에서 AI 허용 정책이 서로 충돌합니다.",
                _source_ids(controlling)))
        else:
            ai_use = controlling[0].ai_use
            allowed = _unique(v for layer in controlling for v in layer.allowed_uses)

    prohibited = _unique(v for layer in ordered for v in layer.prohibited_uses)
    required = _unique(v for layer in ordered for v in layer.required_actions)
    hard = _unique(v for layer in ordered for v in layer.hard_constraints)
    allowed = tuple(v for v in allowed if v not in prohibited)

    tool_sets = [set(layer.approved_tools) for layer in ordered if layer.approved_tools]
    tools = tuple(sorted(set.intersection(*tool_sets))) if tool_sets else ()
    if tool_sets and not tools:
        conflicts.append(PolicyConflict(
            "approved_tools_conflict", "허용 도구 목록의 교집합이 비어 있습니다.",
            _source_ids(layer for layer in ordered if layer.approved_tools)))
    if ai_use == "prohibited":
        allowed = ()
    return EffectivePolicy(
        ai_use, controlling_scope, allowed, prohibited, required, hard, tools,
        sources, tuple(conflicts))


def _unique_sources(values: Iterable[PolicySource]) -> tuple[PolicySource, ...]:
    out = {}
    for source in values:
        out[source.source_id] = source
    return tuple(out[key] for key in sorted(out))


def _source_ids(layers: Iterable[PolicyLayer]) -> tuple[str, ...]:
    return tuple(sorted({layer.source.source_id for layer in layers if layer.source}))
