"""Deterministic, provenance-first academic context graph (LLM 0).

The graph is deliberately small and serialisable.  It records *why* a task
decision was made without copying credentials or depending on a vector DB.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Iterable


def stable_id(kind: str, external_id: str) -> str:
    raw = f"{kind}:{external_id}".encode("utf-8")
    return f"{kind}:{hashlib.sha256(raw).hexdigest()[:16]}"


@dataclass(frozen=True)
class AcademicNode:
    id: str
    kind: str
    title: str
    source: str = ""
    attributes: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AcademicEdge:
    source: str
    relation: str
    target: str
    evidence: str = ""


@dataclass
class AcademicGraph:
    nodes: dict[str, AcademicNode] = field(default_factory=dict)
    edges: list[AcademicEdge] = field(default_factory=list)

    def add_node(self, kind: str, external_id: str, title: str, *,
                 source: str = "", attributes: dict | None = None) -> str:
        node_id = stable_id(kind, external_id)
        self.nodes[node_id] = AcademicNode(
            node_id, kind, title, source, dict(attributes or {}))
        return node_id

    def link(self, source: str, relation: str, target: str, evidence: str = "") -> None:
        edge = AcademicEdge(source, relation, target, evidence[:500])
        if edge not in self.edges:
            self.edges.append(edge)

    def neighbors(self, node_id: str, relation: str = "") -> list[AcademicNode]:
        ids = [e.target for e in self.edges
               if e.source == node_id and (not relation or e.relation == relation)]
        return [self.nodes[i] for i in ids if i in self.nodes]

    def evidence_for(self, node_id: str) -> list[str]:
        return [e.evidence for e in self.edges
                if e.source == node_id and e.evidence]

    def to_dict(self) -> dict:
        return {
            "nodes": [asdict(self.nodes[k]) for k in sorted(self.nodes)],
            "edges": [asdict(e) for e in sorted(
                self.edges, key=lambda x: (x.source, x.relation, x.target))],
        }

    def fingerprint(self) -> str:
        canonical = json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_assignment_graph(assignments: Iterable[dict], *,
                           materials: Iterable[dict] = (),
                           feedback: Iterable[dict] = (),
                           outcomes: Iterable[dict] = ()) -> AcademicGraph:
    """Build the shared assignment→material→feedback→outcome topology."""
    graph = AcademicGraph()
    assignment_nodes: dict[str, str] = {}
    course_nodes: dict[str, str] = {}
    for row in assignments:
        aid = str(row.get("id") or row.get("assignment_id") or row.get("title") or "")
        cid = str(row.get("course_id") or row.get("course") or "unknown")
        course = course_nodes.setdefault(
            cid, graph.add_node("course", cid, str(row.get("course_name") or cid)))
        assignment = graph.add_node(
            "assignment", aid, str(row.get("title") or aid),
            source=str(row.get("url") or ""),
            attributes={"external_id": aid, "due_at": row.get("due_at") or ""})
        assignment_nodes[aid] = assignment
        graph.link(course, "contains", assignment, "LMS course membership")
    for kind, rows, relation in (
        ("material", materials, "supported_by"),
        ("feedback", feedback, "informed_by"),
        ("outcome", outcomes, "resulted_in"),
    ):
        for i, row in enumerate(rows):
            aid = str(row.get("assignment_id") or row.get("id") or "")
            target = assignment_nodes.get(aid)
            if not target:
                continue
            ext = str(row.get("external_id") or row.get("id") or f"{aid}:{i}")
            node = graph.add_node(
                kind, ext, str(row.get("title") or row.get("assignment") or kind),
                source=str(row.get("url") or ""), attributes=dict(row))
            graph.link(target, relation, node, str(row.get("evidence") or ""))
    return graph
