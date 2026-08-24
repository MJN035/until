"""One inspectable decision surface joining graph, policy, memory and gate."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .academic_graph import AcademicGraph
from .policy_compiler import AssignmentPolicy
from .student_memory import MemoryRule


@dataclass(frozen=True)
class TowerFinding:
    severity: str
    code: str
    message: str
    basis: tuple[str, ...] = ()


@dataclass(frozen=True)
class ControlTowerReport:
    assignment_id: str
    submit_state: str
    findings: tuple[TowerFinding, ...]
    provenance_count: int

    def to_dict(self) -> dict:
        return {
            "assignment_id": self.assignment_id,
            "submit_state": self.submit_state,
            "findings": [asdict(f) for f in self.findings],
            "provenance_count": self.provenance_count,
        }


def inspect_assignment(assignment_id: str, *, policy: AssignmentPolicy,
                       graph: AcademicGraph, memory: list[MemoryRule],
                       draft: str = "", attachment_count: int = 0,
                       team_role_confirmed: bool = False,
                       effective_policy=None) -> ControlTowerReport:
    findings: list[TowerFinding] = []
    policy_basis = tuple(e.excerpt for e in policy.evidence)
    ai_use = getattr(effective_policy, "ai_use", policy.ai_use)
    if effective_policy is not None and getattr(effective_policy, "conflicts", ()):
        findings.append(TowerFinding(
            "block", "policy_conflict", "적용되는 정책 문서가 서로 충돌합니다.",
            tuple(source.url for source in effective_policy.sources)))
    if ai_use == "prohibited":
        findings.append(TowerFinding(
            "block", "ai_use_prohibited", "AI 사용 금지 과제라 생성·제출 기능을 중단합니다.", policy_basis))
    elif ai_use == "unclear":
        findings.append(TowerFinding(
            "block", "ai_policy_unclear", "AI 허용 범위를 확인하기 전에는 생성·제출할 수 없습니다."))
    if effective_policy is not None:
        for action in effective_policy.required_actions:
            findings.append(TowerFinding(
                "info", f"required_action:{action}", f"적용 정책 의무: {action}",
                tuple(source.url for source in effective_policy.sources)))
    if attachment_count < policy.required_file_count:
        findings.append(TowerFinding(
            "block", "required_files_missing",
            f"필수 첨부 {policy.required_file_count}개 중 {attachment_count}개만 확인되었습니다.", policy_basis))
    if policy.team_required and not team_role_confirmed:
        findings.append(TowerFinding(
            "block", "team_role_missing", "팀 과제의 본인 역할 확인이 필요합니다.", policy_basis))
    for section in policy.required_sections:
        if section.lower() not in draft.lower():
            findings.append(TowerFinding(
                "block", "required_section_missing", f"필수 섹션 '{section}'이 없습니다.", policy_basis))
    for rule in memory:
        findings.append(TowerFinding(
            "warn", f"memory:{rule.code}", rule.message, rule.assignment_ids))

    node_id = next((nid for nid, n in graph.nodes.items()
                    if n.kind == "assignment" and (n.attributes.get("external_id") == assignment_id
                                                   or n.title == assignment_id)), "")
    provenance_count = len(policy.evidence)
    if node_id:
        provenance_count += len(graph.evidence_for(node_id))
    state = "blocked" if any(f.severity == "block" for f in findings) else "review"
    return ControlTowerReport(assignment_id, state, tuple(findings), provenance_count)
