"""Compile assignment prose into an executable, evidence-backed policy (LLM 0)."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class PolicyEvidence:
    rule: str
    excerpt: str
    source_hash: str


@dataclass
class AssignmentPolicy:
    ai_use: str = "unclear"  # prohibited | limited | allowed | unclear
    required_sections: list[str] = field(default_factory=list)
    required_file_count: int = 0
    citation_style: str = ""
    team_required: bool = False
    evidence: list[PolicyEvidence] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {**asdict(self), "evidence": [asdict(e) for e in self.evidence]}


_AI_PROHIBITED = re.compile(
    r"AI\s*(?:사용|활용)?\s*(?:불가|금지|허용하지)|생성형\s*AI[^.\n]{0,30}(?:금지|불가)|"
    r"(?:no|without)\s+(?:generative\s+)?ai", re.I)
_AI_LIMITED = re.compile(
    r"AI[^.\n]{0,35}(?:아이디어|브레인스토밍|교정|번역|계획)[^.\n]{0,20}(?:만|한정|허용)|"
    r"AI\s*(?:사용|활용)[^.\n]{0,30}(?:출처|표기|명시)", re.I)
_AI_ALLOWED = re.compile(r"AI\s*(?:사용|활용)?\s*(?:가능|허용)|AI를 사용해도", re.I)
_SECTIONS = re.compile(r"(?:구성|포함|섹션|목차)[^\n:：]{0,20}[:：]\s*([^\n.。]+)", re.I)
_FILES = re.compile(r"(?:첨부|제출)\s*(?:파일)?\s*(\d+)\s*(?:개|건)", re.I)
_CITATION = re.compile(r"\b(APA|MLA|Chicago|IEEE)\b", re.I)


def _excerpt(text: str, match: re.Match) -> str:
    start, end = max(0, match.start() - 30), min(len(text), match.end() + 30)
    return " ".join(text[start:end].split())[:180]


def compile_policy(text: str) -> AssignmentPolicy:
    text = text or ""
    policy = AssignmentPolicy()
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

    for state, pattern in (("prohibited", _AI_PROHIBITED),
                           ("limited", _AI_LIMITED), ("allowed", _AI_ALLOWED)):
        match = pattern.search(text)
        if match:
            policy.ai_use = state
            policy.evidence.append(PolicyEvidence("ai_use", _excerpt(text, match), digest))
            break
    section_match = _SECTIONS.search(text)
    if section_match:
        policy.required_sections = [
            item.strip(" -·•") for item in re.split(r"[,·/]|\s+및\s+", section_match.group(1))
            if item.strip(" -·•")][:12]
        policy.evidence.append(PolicyEvidence(
            "required_sections", _excerpt(text, section_match), digest))
    file_match = _FILES.search(text)
    if file_match:
        policy.required_file_count = int(file_match.group(1))
        policy.evidence.append(PolicyEvidence("required_files", _excerpt(text, file_match), digest))
    citation_match = _CITATION.search(text)
    if citation_match:
        policy.citation_style = citation_match.group(1).upper()
        policy.evidence.append(PolicyEvidence("citation_style", _excerpt(text, citation_match), digest))
    team_match = re.search(r"(?:팀|조별|group)\s*(?:과제|보고서|project)", text, re.I)
    if team_match:
        policy.team_required = True
        policy.evidence.append(PolicyEvidence("team_required", _excerpt(text, team_match), digest))
    return policy


def compile_policy_layer(text: str, *, scope: str, scope_id: str,
                         source_id: str, title: str, url: str = ""):
    """Compile prose directly into a hierarchy layer with source provenance."""
    from .policy_hierarchy import PolicyLayer, PolicySource
    policy = compile_policy(text)
    allowed = ()
    if policy.ai_use == "limited":
        allowed = ("planning_or_revision_only",)
    elif policy.ai_use == "allowed":
        allowed = ("assignment_permitted_use",)
    required = ("disclose_ai_use",) if policy.ai_use in {"limited", "allowed"} else ()
    excerpt = policy.evidence[0].excerpt if policy.evidence else text[:180]
    return PolicyLayer(
        scope, scope_id, policy.ai_use, allowed_uses=allowed,
        required_actions=required,
        source=PolicySource(source_id, title, url, excerpt=excerpt))
