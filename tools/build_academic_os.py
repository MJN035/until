"""Build a local, de-identified Academic OS audit from a corpus manifest."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from until.academic_graph import build_assignment_graph
from until.control_tower import inspect_assignment
from until.policy_compiler import compile_policy, compile_policy_layer
from until.policy_hierarchy import resolve_policy
from until.policy_profiles import snu_2026_baseline
from until.student_memory import Outcome, derive_memory


def _rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def build(manifest: Path, outcomes_path: Path | None = None,
          course_policies_path: Path | None = None) -> dict:
    rows = _rows(manifest)
    outcome_rows = _rows(outcomes_path) if outcomes_path and outcomes_path.exists() else []
    outcomes = [Outcome(
        str(row.get("assignment_id") or ""), str(row.get("course_id") or ""),
        row.get("score"), tuple(row.get("comments") or ()),
        tuple(row.get("readiness_blocks") or ())) for row in outcome_rows]
    course_policy_rows = _rows(course_policies_path) if (
        course_policies_path and course_policies_path.exists()) else []
    course_policy_text = {
        str(row.get("course_id") or ""): str(row.get("text") or "")
        for row in course_policy_rows if row.get("course_id") and row.get("text")}
    assignments = [{
        "id": str(row.get("assignment_id") or row.get("id") or ""),
        "title": str(row.get("title") or row.get("assignment_id") or ""),
        "course_id": str(row.get("course_id") or row.get("course") or "unknown"),
        "course_name": str(row.get("course_name") or row.get("course") or ""),
        "due_at": row.get("due_at") or "",
    } for row in rows]
    graph = build_assignment_graph(assignments)
    reports = []
    for row in rows:
        aid = str(row.get("assignment_id") or row.get("id") or "")
        text = str(row.get("description") or row.get("spec_text") or row.get("body") or "")
        if not text and row.get("dir"):
            spec = manifest.parent / str(row["dir"]) / "spec.md"
            try:
                text = spec.read_text(encoding="utf-8")
            except OSError:
                pass
        attachment_count = len(row.get("attachments") or [])
        attachment_count += int(row.get("n_intro_attachments") or 0)
        attachment_count += int(row.get("n_submission_files") or 0)
        course_id = str(row.get("course_id") or row.get("course") or "unknown")
        memory = derive_memory(outcomes, course_id) if outcomes else []
        layers = [snu_2026_baseline()]
        if course_id in course_policy_text:
            layers.append(compile_policy_layer(
                course_policy_text[course_id], scope="course", scope_id=course_id,
                source_id=f"course:{course_id}", title="강의계획서"))
        layers.append(compile_policy_layer(
            text, scope="assignment", scope_id=aid,
            source_id=f"assignment:{aid}", title="과제 지시문",
            url=str(row.get("url") or "")))
        effective = resolve_policy(layers)
        reports.append(inspect_assignment(
            aid, policy=compile_policy(text), graph=graph, memory=memory,
            attachment_count=attachment_count,
            effective_policy=effective).to_dict())
    counts = {"review": 0, "blocked": 0}
    for report in reports:
        counts[report["submit_state"]] = counts.get(report["submit_state"], 0) + 1
    return {
        "schema_version": 1,
        "graph_fingerprint": graph.fingerprint(),
        "assignment_count": len(assignments),
        "states": counts,
        "reports": reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Until Academic OS 로컬 감사")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--outcomes", type=Path,
                        help="선택: 점수·교수 피드백 결과 JSONL")
    parser.add_argument("--course-policies", type=Path,
                        help="선택: course_id와 text를 가진 강의계획서 정책 JSONL")
    args = parser.parse_args()
    payload = build(args.manifest, args.outcomes, args.course_policies)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"saved={args.out} assignments={payload['assignment_count']} states={payload['states']}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
