"""
통합 테스트 러너 — 오프라인 스위트를 병렬로 돌리고 종료코드로 결과 보고.

  python run_tests.py            # 전부 실행(병렬, 기본 CPU 수·최대 8)
  python run_tests.py -q         # 요약만
  python run_tests.py -j 1       # 순차 실행(직렬 디버깅용)

키·인터넷 불필요(전부 mock). Windows 콘솔 인코딩은 자동으로 UTF-8 강제.
각 스위트는 독립 프로세스·임시 디렉터리·랜덤 포트를 쓰므로 병렬 안전.
"""
from __future__ import annotations
import os, sys, subprocess, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

SUITES = [
    "test_pipeline", "test_integrations", "test_llm_fallback", "test_etl_source", "test_moodle_parse",
    "test_context", "test_learningx_parse", "test_resolve", "test_feedback",
    "test_web", "test_canvas_api", "test_discovery", "test_materials",
    "test_suggest", "test_task_type", "test_review", "test_submission",
    "test_length", "test_citation", "test_deadline", "test_readiness", "test_readiness_types",
    "test_format_spec", "test_format_guard", "test_moodle_submit",
    "test_rationale", "test_diffview", "test_answer_history", "test_series",
    "test_runners",
    "test_moodle_ws", "test_announcements",
    "test_plan", "test_formfill", "test_formfill_hwp", "test_profile", "test_enforce", "test_evals",
    "test_evals_grading",
    "test_requirements", "test_skeleton", "test_units", "test_evidence", "test_specificity", "test_unit_pipeline",
    "test_voice_autolearn", "test_spec_check", "test_teacher_feedback",
    "test_tone", "test_memory_layers", "test_edit_capture", "test_human_edit", "test_quality_guards",
    "test_persona_portability",
    "test_voice_feedback",
    "test_integrity",
    "test_inquiry_assignment",
    "test_presentation_conversion",
    "test_distributed_spec",
    "test_structured_assignment",
    "test_assignment_router",
    "test_course_profiles",
    "test_route_inference",
    "test_corpus_validation",
    "test_submission_gate",
    "test_measured_check",
    "test_measured_enforce",
    "test_submission_web",
    "test_elice_api",
    "test_elice_inbox",
    "test_asgi",
    "test_session_store",
    "test_telemetry",
    "test_atomicio",
    "test_token_onboarding", "test_submit_ready",
    "test_practice_audit",
    "test_academic_os",
    "test_policy_hierarchy", "test_weekly_brief",
    "test_runtime_kernel", "test_runtime_phase2", "test_runtime_phase2_edge",
    "test_runtime_cli", "test_runtime_etl", "test_runtime_plugins", "test_runner", "test_license_boundary",
]


def _run_one(name: str, root: Path, env: dict) -> tuple[str, int, str]:
    path = root / "tests" / f"{name}.py"
    r = subprocess.run([sys.executable, str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    return name, r.returncode, (r.stdout + r.stderr)


def main() -> int:
    root = Path(__file__).resolve().parent
    argv = sys.argv[1:]
    quiet = "-q" in argv
    jobs = min(8, os.cpu_count() or 4)
    if "-j" in argv:
        try:
            jobs = max(1, int(argv[argv.index("-j") + 1]))
        except (IndexError, ValueError):
            pass
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    t0 = time.time()
    results: dict[str, tuple[int, str]] = {}
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = {ex.submit(_run_one, name, root, env): name for name in SUITES}
        for fut in as_completed(futs):
            name, code, out = fut.result()
            results[name] = (code, out)

    passed, failed = [], []
    for name in SUITES:  # 결정적 출력 순서
        code, out = results[name]
        ok = code == 0
        (passed if ok else failed).append(name)
        if ok:
            tail = (out.strip().splitlines() or [""])[-1]
            print(f"[PASS] {name}" + ("" if quiet else f" :: {tail}"))
        else:
            print(f"[FAIL {code}] {name}")
            if not quiet:
                print(out.strip()[-1500:])
    print(f"\n==== pass={len(passed)} fail={len(failed)} / {len(SUITES)} "
          f"({time.time() - t0:.1f}s, jobs={jobs}) ====")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
