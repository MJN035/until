"""Requirement → evidence → draft trace rows (deterministic, LLM 0)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class RequirementTrace:
    label: str
    status: str  # reflected | partial | missing | decision
    evidence_titles: list[str] = field(default_factory=list)
    unit_titles: list[str] = field(default_factory=list)
    paragraph_index: int = 0


_TOKENS = re.compile(r"[가-힣A-Za-z0-9]{2,}")
_STOP = {"과제", "작성", "제출", "포함", "대한", "통해", "내용", "요구사항"}


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKENS.findall(text or "") if t.lower() not in _STOP}


def _legacy_rows(result) -> list[RequirementTrace]:
    body = str(getattr(result.draft, "body", "") or "")
    decisions = "\n".join(str(getattr(d, "note", ""))
                          for d in (getattr(result.draft, "decisions", None) or []))
    source_titles = list(getattr(result, "sources", None) or [])
    requirements = [str(x).strip() for x in
                    ((getattr(result, "spec", None) or {}).get("requirements") or [])
                    if str(x).strip()]
    rows = []
    for label in requirements:
        toks = _tokens(label)
        body_hits = sum(t in body.lower() for t in toks)
        decision_hits = sum(t in decisions.lower() for t in toks)
        if decision_hits:
            status = "decision"
        elif toks and body_hits >= max(1, min(2, len(toks))):
            status = "reflected"
        elif body_hits or source_titles:
            status = "partial"
        else:
            status = "missing"
        rows.append(RequirementTrace(label=label, status=status,
                                     evidence_titles=source_titles[:2]))
    return rows


def trace_requirements(result) -> list[RequirementTrace]:
    """Use unit plans when available; otherwise apply a conservative text fallback."""
    by_id: dict[str, RequirementTrace] = {}
    elements = list(getattr(result, "content_elements", None) or [])
    for element in elements:
        by_id[str(getattr(element, "id", ""))] = RequirementTrace(
            label=str(getattr(element, "label", "요구사항")), status="missing")

    saw_plan = False
    rank = {"missing": 0, "partial": 1, "reflected": 2, "decision": 3}
    for unit in (getattr(result, "units", None) or []):
        plan = getattr(unit, "plan", None)
        if plan is None:
            continue
        saw_plan = True
        for item in plan.items:
            row = by_id.get(str(item.element_id))
            if row is None:
                row = RequirementTrace(label=item.label, status="missing")
                by_id[str(item.element_id)] = row
            status = {"write": "reflected", "write_thin": "partial",
                      "decision": "decision"}.get(item.action, "missing")
            if rank[status] > rank[row.status]:
                row.status = status
            row.evidence_titles.extend(x for x in item.evidence_titles
                                       if x not in row.evidence_titles)
            title = str(getattr(unit, "title", "") or getattr(unit, "mark", ""))
            if title and title not in row.unit_titles:
                row.unit_titles.append(title)
    rows = list(by_id.values()) if saw_plan and by_id else _legacy_rows(result)
    body = str(getattr(result.draft, "body", "") or "")
    paragraphs = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    for row in rows:
        query = _tokens(" ".join([row.label] + row.unit_titles))
        scored = [(sum(token in paragraph.lower() for token in query), index)
                  for index, paragraph in enumerate(paragraphs, 1)]
        if scored:
            score, index = max(scored)
            row.paragraph_index = index if score else 0
    return rows
