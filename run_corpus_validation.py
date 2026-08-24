"""eTL 코퍼스를 과제별로 실제 Capture→Pipeline→Boundary→Readiness 검증한다.

개인정보가 든 원문과 제목은 출력·원장에 쓰지 않는다. 과제 ID도 SHA-256 앞 12자리로
지문화한다. 결과 원장은 기본적으로 gitignored `_until_work/` 아래에 저장한다.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import tomllib

from until.config import Config
from until.capture.ingest import ingest_file
from until.context.assignment_router import route_assignment
from until.readiness import assess_readiness
from until.telemetry.schema import (algo_gate, deadline_bucket, spec_chars_bucket,
                                    to_telemetry)
from until.academic_policy import AiUseProhibitedError
from until.console import force_utf8


class _MeteredClient:
    """러너 범위에서 LLMResult 사용량을 합산하는 투명 프록시."""

    def __init__(self, inner, usage: dict, lock: threading.Lock):
        self.inner = inner
        self.usage = usage
        self.lock = lock

    def complete(self, *args, **kwargs):
        result = self.inner.complete(*args, **kwargs)
        with self.lock:
            self.usage["llm_calls"] += 1
            self.usage["llm_tokens_in"] += int(getattr(result, "tokens_in", 0) or 0)
            self.usage["llm_tokens_out"] += int(getattr(result, "tokens_out", 0) or 0)
        return result


def _run_metered(paths: list[str], config: Config):
    """pipeline의 모든 client 생성(경량·unit 포함)을 계측해 한 실행 사용량을 반환."""
    import until.pipeline as pipeline_module
    original = pipeline_module.build_client
    usage = {"llm_calls": 0, "llm_tokens_in": 0, "llm_tokens_out": 0}
    lock = threading.Lock()

    def factory(backend: str, model: str):
        return _MeteredClient(original(backend, model), usage, lock)

    pipeline_module.build_client = factory
    try:
        return pipeline_module.run(paths, config), usage
    finally:
        pipeline_module.build_client = original


def _live_estimate(n_rows: int) -> dict:
    """legacy+unit 최소 6콜/과제 기준의 실행 전 보수적 비용 가시화."""
    calls = max(0, n_rows) * 6
    return {"calls": calls, "tokens_in": calls * 4_000,
            "tokens_out": calls * 1_500}


def _confirm_live_run(backend: str, n_rows: int, *, yes: bool) -> bool:
    if backend == "mock":
        return True
    estimate = _live_estimate(n_rows)
    print(f"LIVE 예상(최소): 과제 {n_rows}건 × legacy+unit = "
          f"LLM {estimate['calls']}회, 입력 약 {estimate['tokens_in']:,} tokens, "
          f"출력 약 {estimate['tokens_out']:,} tokens")
    print("reask·unit 개수에 따라 실제 호출/토큰은 이보다 늘 수 있습니다. "
          "한 번 실행은 선택한 context-mode 하나만 처리합니다.")
    if yes:
        return True
    if not sys.stdin.isatty():
        print("비대화형 live 실행은 --yes가 필요합니다.")
        return False
    return input("과금 가능한 live 검증을 계속할까요? [y/N] ").strip().lower() in {"y", "yes"}


def _fingerprint(row: dict) -> str:
    raw = f"{row.get('course_id')}:{row.get('assignment_id')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


# ingest가 낼 수 있는 정상 파싱 종류 — 여기 없는 kind(잘린 확장자 등)는 형식
# 판정 대상이 아니라 수집 결함(reference_parse_failures)으로 기록한다.
KNOWN_REFERENCE_KINDS = {
    "pdf", "docx", "pptx", "hwpx", "hwp", "html", "htm", "rmd-template",
    "zip-project", "md", "markdown", "text", "txt", "rmd",
}

# 전략별 기대 제출 형식. .hwp(한글 5.x 이진)는 글쓰기 계열 과제의 실제 1위
# 제출 포맷이라 문서 계열 집합에 포함한다(ingest 내장 폴백이 파싱함).
EXPECTED_REFERENCE = {
    "rmd_notebook": {"rmd-template", "html"},
    "zip_project": {"zip-project"},
    "activity_form": {"pdf", "docx", "hwpx", "hwp", "html"},
    "reflective_series": {"pdf", "docx", "hwpx", "hwp", "html"},
    "staged_writing": {"pdf", "docx", "hwpx", "hwp", "html"},
    "presentation_conversion": {"pptx", "pdf", "docx", "zip-project"},
}


# 일반 텍스트 계열 kind — 어떤 전략에서든 정답셋으로 읽을 수 있고, 확장자가
# 깨진 파일(잘린 '.p' 등)도 여기로 폴백되므로 형식 판정에서는 중립으로 둔다.
NEUTRAL_REFERENCE_KINDS = {"text", "txt", "md", "markdown"}


def reference_mismatch(strategy: str, kinds: list) -> bool:
    """제출물 형식이 전략의 기대와 진짜로 어긋나는지 — 특정 형식 kind만 판정."""
    expected = EXPECTED_REFERENCE.get(strategy)
    specific = [k for k in kinds
                if k in KNOWN_REFERENCE_KINDS - NEUTRAL_REFERENCE_KINDS]
    return bool(expected and specific and not set(specific) <= expected)


def _files(directory: Path, folder: str) -> list[Path]:
    root = directory / folder
    return sorted((p for p in root.glob("*") if p.is_file()), key=lambda p: p.name.lower())


def _critical_readiness(readiness, draft, prefix: str = "readiness") -> list[str]:
    out = []
    for item in readiness.warnings:
        # 미답 결정의 본인 경험·관점 몫 때문에 짧은 것은 정상적인 입력 대기 상태다.
        # 결정이 하나도 없는데 짧을 때만 시스템 과소작업으로 실패 처리한다.
        if item.label == "분량" and getattr(draft, "n_decisions", 0):
            continue
        if item.label in {"양식", "분량", "경계선", "자료"}:
            out.append(f"{prefix}:{item.label}")
        elif item.label == "인용" and "오류" in item.message:
            out.append(f"{prefix}:invalid_citation")
    return out


def validate_one(root: Path, row: dict, *, context_mode: str = "full",
                 telemetry_aux: dict | None = None, backend: str = "mock") -> dict:
    timings: dict[str, int] = {}
    total_started = time.perf_counter()
    capture_started = time.perf_counter()
    directory = root / row["dir"]
    spec_path = directory / "spec.md"
    intro = _files(directory, "intro_files")
    context_path = (root / row["context_path"] if row.get("context_path")
                    else directory / "etl_context" / "context.md")
    submissions = _files(directory, "submission_files")
    description = spec_path.read_text(encoding="utf-8") if spec_path.exists() else ""
    # 기대 경로도 실제 파이프라인 입력과 같은 정보만 보아야 한다. bare에서
    # 제외한 첨부 파일명을 계속 쓰면 ZIP 과제를 거짓 route_mismatch로 판정한다.
    route_intro = [] if context_mode == "bare" else intro
    route = route_assignment(
        title=row.get("title", ""), description=description,
        attachment_names=[p.name for p in route_intro],
        course_name=row.get("course_name", ""))
    base = {
        "id": _fingerprint(row), "strategy": route.strategy,
        "actionable": route.actionable, "intro_files": len(intro),
        "has_reference_submission": bool(row.get("has_submission_text") or submissions),
    }
    timings["capture"] = round((time.perf_counter() - capture_started) * 1000)
    if not route.actionable:
        if telemetry_aux is not None:
            telemetry_aux.update({"elapsed_ms": timings, "spec_chars": len(description),
                                  "intro_file_exts": [p.suffix.lower().lstrip(".") for p in intro]})
        return {**base, "status": "excluded", "checks": ["non_actionable"],
                }
    if not spec_path.exists():
        return {**base, "status": "failed", "failures": ["missing_spec"],
                }

    failures = []
    reference_kinds = []
    reference_parse_failures = []
    for path in submissions:
        try:
            reference = ingest_file(path, backend="basic")
            reference_kinds.append(reference.kind)
            if reference.kind not in KNOWN_REFERENCE_KINDS:
                # 수집 시 경로 길이 절단으로 확장자가 깨진 파일(예: '.p') —
                # 형식 불일치가 아니라 수집 결함으로 표면화한다.
                reference_parse_failures.append(
                    f"{path.suffix.lower() or '<none>'}:unknown_kind")
            if reference.text.count("�") > max(3, len(reference.text) // 100):
                reference_parse_failures.append(f"{path.suffix.lower()}:replacement_chars")
        except Exception as exc:
            # 이미지 제출은 현재 텍스트 정답셋으로 쓸 수 없음을 명시적으로 기록한다.
            reference_parse_failures.append(f"{path.suffix.lower() or '<none>'}:{type(exc).__name__}")
    if context_mode == "bare":
        pipeline_paths = [str(spec_path)]
    elif context_mode == "no_etl_context":
        pipeline_paths = [str(spec_path), *map(str, intro)]
    else:
        if not context_path.is_file():
            return {**base, "status": "failed", "failures": ["missing_context_bundle"]}
        pipeline_paths = [str(spec_path), *map(str, intro), str(context_path)]
    try:
        pipeline_started = time.perf_counter()
        # 이 러너는 legacy/unit A/B 비교가 목적이라 두 경로를 명시 고정한다 —
        # 기본값이 unit으로 바뀌어도(2026-08-14) 원장 비교 축은 유지.
        legacy_cfg = Config(backend=backend, parser_backend="basic")
        legacy_cfg.pipeline_mode = "legacy"
        result, legacy_usage = _run_metered(pipeline_paths, legacy_cfg)
        timings["pipeline"] = round((time.perf_counter() - pipeline_started) * 1000)
    except AiUseProhibitedError:
        return {**base, "status": "excluded", "checks": ["ai_use_prohibited"],
                "policy": "ai_use_prohibited"}
    except Exception as exc:
        return {**base, "status": "failed",
                "failures": [f"pipeline_exception:{type(exc).__name__}"],
                }
    readiness_started = time.perf_counter()
    readiness = assess_readiness(result)
    timings["readiness"] = round((time.perf_counter() - readiness_started) * 1000)
    readiness_warns = [{"label": item.label, "message": item.message}
                       for item in readiness.warnings]
    failures.extend(_critical_readiness(readiness, result.draft))
    pipeline_strategy = getattr(getattr(result, "assignment_route", None), "strategy", "")
    if not result.documents:
        failures.append("no_documents")
    for doc in result.documents:
        text = doc.text or ""
        if text.count("�") > max(3, len(text) // 100):
            failures.append("capture_replacement_character_contamination")
        if "<html" in text[:1000].lower() or "<!doctype" in text[:1000].lower():
            failures.append("capture_raw_html_contamination")
    if not result.draft.body.strip():
        failures.append("empty_draft")
    if not result.guard.passed:
        failures.append("boundary_guard_failed")
    if not result.spec.get("task_type"):
        failures.append("missing_task_type")
    if not pipeline_strategy:
        failures.append("missing_pipeline_strategy")
    if pipeline_strategy != route.strategy:
        failures.append(f"route_mismatch:{route.strategy}->{pipeline_strategy}")
    # 첨부가 전부 실패해도 조용히 성공시키지 않는다. 원료 의존 과제는 material_gap
    # 또는 구체 질문으로 수렴해야 한다.
    if intro and len(result.capture_warnings) >= len(intro):
        if not (result.spec.get("material_gap") or route.questions):
            failures.append("all_intro_files_unparsed_without_question")
    if reference_mismatch(route.strategy, reference_kinds):
        failures.append("reference_format_mismatch")
    unit_fields = {}
    unit_result = None
    unit_route = ""
    unit_usage = {"llm_calls": 0, "llm_tokens_in": 0, "llm_tokens_out": 0}
    try:
        unit_cfg = Config(backend=backend, parser_backend="basic")
        unit_cfg.pipeline_mode = "unit"
        unit_started = time.perf_counter()
        unit_result, unit_usage = _run_metered(pipeline_paths, unit_cfg)
        timings["unit_pipeline"] = round((time.perf_counter() - unit_started) * 1000)
        unit_route = getattr(getattr(unit_result, "assignment_route", None), "strategy", "")
        if not unit_result.draft.body.strip():
            failures.append("unit:empty_draft")
        if not unit_result.guard.passed:
            failures.append("unit:boundary_guard_failed")
        if unit_route != route.strategy:
            failures.append(f"unit:route_mismatch:{route.strategy}->{unit_route}")
        unit_readiness_started = time.perf_counter()
        unit_readiness = assess_readiness(unit_result)
        timings["unit_readiness"] = round(
            (time.perf_counter() - unit_readiness_started) * 1000)
        failures.extend(_critical_readiness(
            unit_readiness, unit_result.draft, "unit_readiness"))
        unit_fields = {
            "unit_guard_passed": unit_result.guard.passed,
            "unit_count": len(unit_result.units),
            "unit_draft_chars": len(unit_result.draft.body),
            "unit_decisions": len(unit_result.draft.decisions),
            "unit_readiness_warnings": len(unit_readiness.warnings),
            "unit_readiness_warning_labels": [i.label for i in unit_readiness.warnings],
            "unit_readiness_warning_details": [
                {"label": i.label, "message": i.message} for i in unit_readiness.warnings],
        }
    except Exception as exc:
        failures.append(f"unit:pipeline_exception:{type(exc).__name__}")
    checks = ["capture", "pipeline", "boundary", "readiness", "explicit_route"]
    timings["total"] = round((time.perf_counter() - total_started) * 1000)
    if telemetry_aux is not None:
        from until.boundary.rationale import classify_decision
        decision_questions = [decision.note for decision in result.draft.decisions]
        deadline = getattr(result, "deadline", None)
        days_remaining = deadline.days_from(date.today()) if deadline is not None else None
        telemetry_aux.update({
            "elapsed_ms": timings, "spec_chars": len(description),
            "intro_file_exts": [p.suffix.lower().lstrip(".") for p in intro],
            "guard_passed": result.guard.passed, "unit_strategy": unit_route,
            "deadline_days": days_remaining,
            "source_texts": [description, *[d.text for d in result.documents]],
            "draft_bodies": [result.draft.body,
                             unit_result.draft.body if unit_result is not None else ""],
            "decision_questions": decision_questions,
            "decision_answers": [],
            "decision_kinds": [classify_decision(note).category
                               for note in decision_questions],
            "llm_calls": legacy_usage["llm_calls"] + unit_usage["llm_calls"],
            "llm_tokens_in": (legacy_usage["llm_tokens_in"]
                              + unit_usage["llm_tokens_in"]),
            "llm_tokens_out": (legacy_usage["llm_tokens_out"]
                               + unit_usage["llm_tokens_out"]),
        })
    return {
        **base, "status": "failed" if failures else "passed", "failures": failures,
        "checks": checks, "task_type": result.spec.get("task_type"),
        "capture_warnings": len(result.capture_warnings),
        "reference_kinds": sorted(set(reference_kinds)),
        "reference_parse_failures": reference_parse_failures,
        "readiness_warnings": len(readiness.warnings),
        "readiness_warning_labels": [x["label"] for x in readiness_warns],
        "readiness_warning_details": readiness_warns,
        "decisions": len(result.draft.decisions),
        "draft_chars": len(result.draft.body),
        **unit_fields,
    }


def _package_version() -> str:
    try:
        data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
        return str(data["project"]["version"])
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
        return "0.0.0"


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True,
            text=True, encoding="utf-8", check=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    except OSError:
        return ""


def _telemetry_from(local: dict, row: dict, aux: dict, *, run_id: str,
                    context_mode: str, backend: str,
                    algo_version: str, git_sha: str, gate: str | None) -> dict:
    reference_failures = []
    for value in local.get("reference_parse_failures", []):
        ext, _, error = str(value).partition(":")
        ext = ext.lower().lstrip(".") or "none"
        reference_failures.append(f"{ext}:{error}")
    deadline = (deadline_bucket(aux["deadline_days"])
                if aux.get("deadline_days") is not None else None)
    record = {
        "run_id": run_id, "algo_version": algo_version, "git_sha": git_sha,
        # algo_gate는 릴리스 SemVer와 다른 축이다 — 게이트는 런타임 env라
        # 릴리스 번호에 안 남는다. 웹 원장과 게이트 기준으로 함께 자르려면
        # 두 생산자가 같은 값을 실어야 하므로 schema.algo_gate() 하나만 쓴다.
        "algo_gate": gate,
        "context_mode": context_mode, "pipeline_mode": "legacy", "backend": backend,
        "parser_backend": "basic", "strategy": local.get("strategy"),
        "unit_strategy": aux.get("unit_strategy") or local.get("strategy"),
        "task_type": local.get("task_type"), "actionable": local.get("actionable"),
        "route_agreement": not any("route_mismatch:" in failure
                                   for failure in local.get("failures", [])),
        "unmatched_route": local.get("strategy") == "spec_clarification",
        "status": local.get("status"), "failures": list(local.get("failures", [])),
        "checks": list(local.get("checks", [])),
        "capture_warnings": int(local.get("capture_warnings", 0)),
        "readiness_warning_labels": list(local.get("readiness_warning_labels", [])),
        "unit_readiness_warning_labels": list(local.get("unit_readiness_warning_labels", [])),
        "guard_passed": aux.get("guard_passed"),
        "unit_guard_passed": local.get("unit_guard_passed"),
        "decisions": int(local.get("decisions", 0)),
        "unit_decisions": int(local.get("unit_decisions", 0)),
        "unit_count": int(local.get("unit_count", 0)),
        "spec_chars_bucket": spec_chars_bucket(aux.get("spec_chars", 0)),
        "intro_files": int(local.get("intro_files", 0)),
        "intro_file_exts": sorted(set(aux.get("intro_file_exts", []))),
        "draft_chars": int(local.get("draft_chars", 0)),
        "unit_draft_chars": int(local.get("unit_draft_chars", 0)),
        "has_reference_submission": bool(local.get("has_reference_submission")),
        "reference_kinds": list(local.get("reference_kinds", [])),
        "reference_parse_failures": reference_failures,
        "reference_format_match": "reference_format_mismatch" not in local.get("failures", []),
        "elapsed_ms": dict(aux.get("elapsed_ms", {})),
        "decision_total": int(local.get("decisions", 0)),
        "decision_answered": None, "decision_partial": None,
        "decision_skipped": None, "decision_response_rate": None,
        "decision_median_seconds": None,
        "decision_kinds": list(aux.get("decision_kinds", [])),
        "ai_suggestion_offered": None, "ai_suggestion_accepted": None,
        "ai_suggestion_edited": None, "ai_suggestion_rejected": None,
        "warning_shown": None, "warning_resolved": None,
        "llm_calls": int(aux.get("llm_calls", 0)),
        "llm_tokens_in": int(aux.get("llm_tokens_in", 0)),
        "llm_tokens_out": int(aux.get("llm_tokens_out", 0)),
    }
    if deadline is not None:
        record["deadline_bucket"] = deadline
    sources = {
        "course_id": row.get("course_id"), "assignment_id": row.get("assignment_id"),
        "spec_text": "\n".join(aux.get("source_texts", [])),
        "draft_body": "\n".join(aux.get("draft_bodies", [])),
        "decision_questions": "\n".join(aux.get("decision_questions", [])),
        "decision_answers": "\n".join(aux.get("decision_answers", [])),
        "title": row.get("title", ""), "course_name": row.get("course_name", ""),
    }
    return to_telemetry(record, sources=sources)


def main() -> int:
    # Windows 기본 cp949 콘솔에서도 기호(—·⚠ 등) 때문에 실행이 죽지 않게 한다.
    force_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="_until_work/corpus/minjun")
    ap.add_argument("--out", default="_until_work/corpus_validation.jsonl")
    ap.add_argument("--minimum", type=int, default=109)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--emit-telemetry", default="",
                    help="비식별 텔레메트리 JSONL 경로(미지정=방출 off)")
    ap.add_argument("--context-mode", choices=("full", "no_etl_context", "bare"),
                    default="full")
    ap.add_argument("--backend", choices=("mock", "local", "anthropic"), default="mock")
    ap.add_argument("--yes", action="store_true",
                    help="live 백엔드 예상 비용 확인 후 대화형 질문 없이 실행")
    args = ap.parse_args()
    root = Path(args.root)
    rows = [json.loads(line) for line in (root / "manifest.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        rows = rows[:args.limit]
    if not _confirm_live_run(args.backend, len(rows), yes=args.yes):
        return 2
    started = time.perf_counter()
    aux_rows = [{} for _ in rows]
    records = [validate_one(root, row, context_mode=args.context_mode,
                            telemetry_aux=aux, backend=args.backend)
               for row, aux in zip(rows, aux_rows, strict=True)]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True)
                              for r in records) + "\n", encoding="utf-8")
    telemetry_path = None
    if args.emit_telemetry:
        import secrets
        telemetry_path = Path(args.emit_telemetry)
        run_id = secrets.token_hex(8)
        algo_version, git_sha = _package_version(), _git_sha()
        # 한 run 안의 모든 레코드는 같은 게이트여야 한다 — 행마다 다시 읽지 않고
        # 여기서 한 번만 구해 내려보낸다(algo_version·git_sha와 같은 취급).
        gate = algo_gate()
        telemetry = [_telemetry_from(local, row, aux, run_id=run_id,
                                     context_mode=args.context_mode,
                                     backend=args.backend,
                                     algo_version=algo_version, git_sha=git_sha,
                                     gate=gate)
                     for local, row, aux in zip(records, rows, aux_rows, strict=True)]
        telemetry_path.parent.mkdir(parents=True, exist_ok=True)
        telemetry_path.write_text(
            "\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True)
                      for item in telemetry) + "\n", encoding="utf-8")
    statuses = Counter(r["status"] for r in records)
    failures = Counter(f for r in records for f in r.get("failures", []))
    print(f"validated={len(records)} minimum={args.minimum} statuses={dict(statuses)} "
          f"seconds={time.perf_counter() - started:.1f}")
    print("strategies=", dict(Counter(r["strategy"] for r in records)))
    print("failures=", dict(failures))
    print(f"ledger={out}")
    if telemetry_path is not None:
        print(f"telemetry={telemetry_path}")
    return 1 if len(records) < args.minimum or statuses.get("failed", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
