"""Deterministic runtime plugin registration and selection."""
from __future__ import annotations

from dataclasses import dataclass

from .models import RuntimeRequest, SupportDecision
from .plugin import RuntimePlugin


@dataclass(frozen=True)
class PluginSelection:
    plugin: RuntimePlugin | None
    decision: SupportDecision


class RuntimeRegistry:
    def __init__(self, plugins=()):
        self._plugins: dict[str, RuntimePlugin] = {}
        for plugin in plugins:
            self.register(plugin)

    def register(self, plugin: RuntimePlugin) -> None:
        name = plugin.name.strip()
        if not name:
            raise ValueError("runtime plugin name is required")
        if name in self._plugins:
            raise ValueError(f"duplicate runtime plugin: {name}")
        self._plugins[name] = plugin

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._plugins))

    def select(self, request: RuntimeRequest) -> PluginSelection:
        candidates = []
        blocked = []
        reasons = []
        for name in sorted(self._plugins):
            plugin = self._plugins[name]
            decision = plugin.supports(request)
            if decision.status == "supported":
                candidates.append((decision.priority, name, plugin, decision))
            elif decision.status == "blocked":
                blocked.append((decision.priority, name, decision))
            else:
                reasons.append(f"{name}: {decision.reason}")
        if candidates:
            candidates = sorted(candidates, key=lambda item: (-item[0], item[1]))
            top_priority = candidates[0][0]
            tied = [candidate for candidate in candidates if candidate[0] == top_priority]
            if len(tied) > 1:
                names = ", ".join(candidate[1] for candidate in tied)
                return PluginSelection(
                    None,
                    SupportDecision(
                        "blocked",
                        f"ambiguous runtime plugins at priority {top_priority}: {names}",
                    ),
                )
            _, _, plugin, decision = candidates[0]
            return PluginSelection(plugin, decision)
        if blocked:
            _, name, decision = sorted(blocked, key=lambda item: (-item[0], item[1]))[0]
            return PluginSelection(
                None, SupportDecision("blocked", f"{name}: {decision.reason}", decision.priority)
            )
        reason = "; ".join(reasons) or "registered runtime cannot handle this assignment"
        return PluginSelection(None, SupportDecision("unsupported", reason))
