"""CLI: `python -m until.cli examples/sample_assignment.txt`
맥락 주입 예: `python -m until.cli examples/sample_assignment.txt \
  --my-files examples/my_files --voice examples/voice_samples --course-materials examples/course_materials`
eTL 수집: `python -m until.cli --source etl-demo`"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

from .config import Config
from .console import force_utf8
from .pipeline import run


def _confirm_submission_from_cli(input_fn=None) -> tuple[bool, bool]:
    """사람의 y 확인과 배포 무장을 모두 만족할 때만 armed=True."""
    ask = input_fn or input
    answer = ask("위 본문과 대상을 확인했습니다. 제출하려면 y를 입력하세요 [y/N]: ")
    proceed = answer.strip().lower() == "y"
    return proceed, proceed and os.getenv("UNTIL_SUBMIT_ARMED") == "1"


def _collect_source(source: str):
    workdir = Path("_until_work"); workdir.mkdir(exist_ok=True)
    if source == "etl-demo":
        from .capture.sources.collect import collect_etl_fixture
        collected, files = collect_etl_fixture("examples/etl_fixture", str(workdir))
        print("=== 0. eTL 수집 (오프라인 fixture) ===")
        print(f"  과제: {collected.title}  |  과목: {collected.course}")
        print(f"  첨부 {len(collected.attachments)}개 다운로드 → {workdir}/")
        for a in collected.attachments:
            print(f"    - {a.name}")
        return files
    if source.startswith("etl:"):
        url = source[len("etl:"):]
        from .capture.sources.collect import collect_etl_to_files
        try:
            collected, files = collect_etl_to_files(url, str(workdir))
        except (NotImplementedError, RuntimeError) as e:
            print("라이브 eTL 접속 준비 필요:")
            print(f"  {e}")
            print("설치: pip install playwright && python -m playwright install chromium")
            print("오프라인 흐름은 `--source etl-demo` 로 확인하세요.")
            return None
        print("=== 0. eTL 수집 (라이브) ===")
        print(f"  과제: {collected.title}  |  과목: {collected.course}")
        print(f"  첨부 {len(collected.attachments)}개 → {workdir}/")
        return files
    if source.startswith("canvas-api:"):
        url = source[len("canvas-api:"):]
        from .capture.sources.collect import collect_canvas_api_to_files
        try:
            collected, files = collect_canvas_api_to_files(url, str(workdir))
        except ValueError as e:  # 토큰 없음/URL 형식 오류
            print("Canvas REST API 수집 준비 필요:")
            print(f"  {e}")
            return None
        except Exception as e:  # 네트워크/HTTP 오류
            print(f"Canvas API 호출 실패: {e}")
            return None
        print("=== 0. eTL 수집 (Canvas REST API) ===")
        print(f"  과제: {collected.title}  |  과목: {collected.course}")
        print(f"  첨부 {len(collected.attachments)}개 → {workdir}/")
        return files
    if source.startswith("elice:"):
        url = source[len("elice:"):]
        from .capture.sources.collect import collect_elice_to_files
        try:
            collected, files = collect_elice_to_files(url, str(workdir))
        except Exception:
            print("Elice 과제를 불러오지 못했습니다. 토큰·과제 URL·네트워크를 확인하세요.")
            return None
        print("=== 0. Elice 수집 (읽기 전용) ===")
        print(f"  과제: {collected.title}  |  과목: {collected.course}")
        print(f"  첨부 {len(collected.attachments)}개 → {workdir}/")
        return files
    if source.startswith("moodle-ws:"):
        url = source[len("moodle-ws:"):]
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}" if parts.netloc else ""
        from .capture.sources.collect import collect_moodle_ws_to_files
        try:
            collected, files = collect_moodle_ws_to_files(url, str(workdir), base_url=base)
        except ValueError as e:  # 토큰 없음
            print("eTL Moodle WS 수집 준비 필요:")
            print(f"  {e}")
            return None
        except Exception as e:  # 네트워크/과목 컨텍스트 없음 등
            print(f"eTL WS 호출 실패: {e}")
            print("  (과제 URL에 courseid가 있어야 무상태 조회가 됩니다 — 인박스에서 복사한 링크 권장)")
            return None
        print("=== 0. eTL 수집 (Moodle Web Services · 읽기 전용) ===")
        print(f"  과제: {collected.title}  |  과목: {collected.course}")
        print(f"  첨부 {len(collected.attachments)}개 → {workdir}/")
        return files
    print(f"알 수 없는 --source: {source!r}")
    return None


def main(argv: list[str] | None = None) -> int:
    # Windows의 기본 cp949 콘솔에서도 기호(—·⚠ 등) 때문에 실행이 죽지 않게 한다.
    force_utf8()
    ap = argparse.ArgumentParser(prog="until", description="Until MVP — draft to the boundary.")
    ap.add_argument("files", nargs="*", help="과제 지시문/참고자료 파일 (txt, md, pdf)")
    ap.add_argument("--backend", default=None, help="mock | anthropic | local")
    ap.add_argument("--auto-accept", action="store_true", help="모두 수락 모드")
    ap.add_argument("--source", default="files",
                    help="files(기본) | etl-demo | etl:<과제URL> | canvas-api:<과제URL> | elice:<과제URL> | "
                         "moodle-ws:<과제URL>(읽기 전용 Moodle WS)")
    ap.add_argument("--course-materials", default=None, help="수업자료 폴더 (관련 자료 검색)")
    ap.add_argument("--my-files", default=None, help="내 파일 폴더 (관련 파일 검색)")
    ap.add_argument("--voice", default=None, help="내 기존 글 폴더 (말투 프로파일)")
    ap.add_argument("--voice-llm", action="store_true", help="LLM 1회 호출로 말투 요약 보강")
    ap.add_argument("--out", default=None, help="Markdown 리포트(진단용) 저장 경로")
    ap.add_argument("--submission", default=None, metavar="경로",
                    help="제출용 문서 저장(.md/.html). 본문+결정 체크리스트만, 진단정보 없음.")
    ap.add_argument("--submit-dry-run", action="store_true",
                    help="제출 게이트 판정 + 보낼 요청을 렌더만(네트워크 0, 실제 제출 안 함)")
    ap.add_argument("--submit-confirm", action="store_true",
                    help="제출 게이트 확인 후 y 입력. UNTIL_SUBMIT_ARMED=1일 때만 실제 전송")
    ap.add_argument("--filled-form", nargs="?", const=True, default=None, metavar="경로",
                    help="양식 첨부(hwpx/docx)의 표 칸에 초안 값을 주입한 '원본 형식' "
                         "파일 저장. 경로 생략 시 원본 옆에 *_작성본.hwpx.")
    ap.add_argument("--readiness-json", default=None, metavar="경로",
                    help="제출 준비 점검(마감·분량·인용·결정)을 JSON으로 저장(툴 연동).")
    ap.add_argument("--suggest", nargs="?", const=True, default=None, metavar="경로",
                    help="각 결정에 AI 추천 답+근거를 출력. 경로를 주면 --resolve용 "
                         "answers JSON 템플릿으로 저장(수정 후 --resolve로 반영).")
    ap.add_argument("--resolve", default=None, help="결정 지점 답변 JSON 경로")
    ap.add_argument("--resolve-mode", default="final", choices=["final", "splice"],
                    help="final(기본): 답변을 녹여 LLM 2차 패스로 최종 완성본 작성. "
                         "splice: 마커 자리에 답변 문자열만 치환(LLM 미사용).")
    from .feedback import DEFAULT_LOG as _FB_LOG
    ap.add_argument("--feedback", nargs="?", const=_FB_LOG, default=None,
                    metavar="경로",
                    help=f"실행 기록을 JSONL로 적립(P7). 값 없으면 {_FB_LOG}. GEPA 입력으로 재사용.")
    ap.add_argument("--satisfaction", type=int, default=None, choices=[1, 2, 3, 4, 5],
                    help="이번 실행 만족도 1~5 (피드백 로그에 기록).")
    args = ap.parse_args(argv)

    cfg = Config()
    if args.backend:
        cfg.backend = args.backend
    if args.auto_accept:
        cfg.auto_accept = True
    resolved_answers = None
    if args.resolve:
        from .boundary.resolve import load_resolution_answers
        try:
            resolved_answers = load_resolution_answers(args.resolve)
        except ValueError as e:
            print(f"--resolve 오류: {e}")
            return 1

    files = args.files
    if args.source != "files":
        files = _collect_source(args.source)
        if files is None:
            return 1
    if not files:
        ap.error("입력 파일이 없습니다. 파일을 주거나 --source 를 지정하세요.")

    try:
        res = run(files, cfg,
                  course_dir=args.course_materials,
                  my_files_dir=args.my_files,
                  voice_dir=args.voice,
                  enhance_voice=args.voice_llm)
    except Exception as exc:
        from .academic_policy import AiUseProhibitedError
        if isinstance(exc, AiUseProhibitedError):
            print(f"\n⛔ {exc}", file=sys.stderr)
            return 2
        raise
    if resolved_answers is not None:
        if args.resolve_mode == "splice":
            from .boundary.resolve import apply_resolution_answers
            from .prompts.suggest import suggest_prompts
            res.draft = apply_resolution_answers(res.draft, resolved_answers)
            res.suggested_prompts = suggest_prompts(res.draft) if cfg.suggest_prompts else []
        else:  # final — Execution 2차 패스로 최종 완성본 작성
            from .pipeline import finalize
            from .prompts.suggest import suggest_prompts
            res = finalize(res, resolved_answers, cfg)
            if res.final_draft is not None:
                res.suggested_prompts = (
                    suggest_prompts(res.final_draft) if cfg.suggest_prompts else []
                )

    print(f"\n=== 1. Capture (문서 파싱, no-token) — backend={cfg.backend} ===")
    for d in res.documents:
        print(f"  • {d.source}  [{d.kind}]  {d.n_chars}자  섹션 {len(d.sections)}개")
    for w in getattr(res, "capture_warnings", []) or []:
        print(f"  ⚠ 스킵: {w}")

    print("\n=== 2. Understanding — Task Spec ===")
    print(json.dumps(res.spec, ensure_ascii=False, indent=2))
    if getattr(res, "content_elements", None):
        from .understanding.requirements import render_elements
        print("\n=== 2.2 요구사항 원자 분해 (내용 요소) ===")
        print(render_elements(res.content_elements))

    c = res.context
    if c and (c.course_hits or c.my_hits or c.voice.n_samples):
        print("\n=== 2.5 Personalization/Context — Execution에 주입 ===")
        print("  " + c.summary())
        for h in c.course_hits:
            print(f"  · [수업자료] {h.document.source} (점수 {h.score}, 매칭 {h.matched})")
        for h in c.my_hits:
            print(f"  · [내 파일] {h.document.source} (점수 {h.score}, 매칭 {h.matched})")
        if c.voice.n_samples:
            print(f"  · [말투] {c.voice.ending_style}, 평균 {c.voice.avg_sentence_len}자, "
                  f"자주: {', '.join(c.voice.frequent_terms[:5])}")
            if c.voice.llm_summary:
                print(f"    LLM 요약: {c.voice.llm_summary}")

    g = res.guard
    status = "통과" if g.passed else "미통과(경고)"
    print(f"\n=== 3. Execution — Draft (경계선까지) | BoundaryGuard: {status}, "
          f"시도 {g.attempts}회(재요청 {g.reasks}회) ===")
    if g.reasks:
        print("  ↻ 1차 위반:")
        for e in g.history[0]:
            print(f"     - {e}")
        print()
    print(res.draft.body)

    print("=== 4. Boundary — 사람이 결정할 지점 ===")
    if res.draft.crossed_boundary:
        if resolved_answers:
            print("  모든 결정 지점이 답변으로 반영됨.")
        else:
            print("  ⚠ 결정 지점 0개 — 경계선 넘었을 수 있음(검토).")
    from .boundary.rationale import classify_decision
    for i, d in enumerate(res.draft.decisions, 1):
        rat = classify_decision(d.note)
        print(f"  [{i}] {d.note}")
        print(f"       🔒 {rat.category} — {rat.why}")

    if res.final_draft is not None:
        fg = res.final_guard
        fstatus = "통과" if (fg and fg.passed) else "미통과(경고)"
        print(f"\n=== 4.5 Finalize — 최종 완성본 (결정 반영) | BoundaryGuard: {fstatus}"
              + (f", 시도 {fg.attempts}회(재요청 {fg.reasks}회)" if fg else "") + " ===")
        print(res.final_draft.body)
        if res.final_draft.decisions:
            print("  남은 결정 지점(미답):")
            for i, d in enumerate(res.final_draft.decisions, 1):
                print(f"    [{i}] {d.note}")

    if res.suggested_prompts:
        print("\n=== 5. 다음에 뭐라고 프롬프트하면 되는지 (제안) ===")
        for i, p in enumerate(res.suggested_prompts, 1):
            print(f"  [{i}] {p}")
    if args.suggest is not None and res.draft.decisions:
        from .pipeline import suggest_decision_answers
        sugg = suggest_decision_answers(res, cfg)
        if sugg:
            from .context.answer_history import suggest_from_history
            notes = {i: d.note for i, d in enumerate(res.draft.decisions, 1)}
            print("\n=== 4.7 결정 AI 제안 (확정은 당신 몫 — 수락/수정하세요) ===")
            for i in sorted(sugg):
                s = sugg[i]
                print(f"  [{i}] 제안: {s['answer']}")
                if s.get("why"):
                    print(f"       이유: {s['why']}")
                try:  # 비슷한 결정에 답한 적 있으면 병기(비치명적).
                    h = suggest_from_history(notes.get(i, ""))
                except Exception:
                    h = None
                if h:
                    print(f"       🕘 지난 답: {h.answer}")
            if args.suggest is not True:  # 경로가 주어짐 → --resolve용 템플릿 저장
                tmpl = {str(i): sugg[i]["answer"] for i in sorted(sugg)}
                sp = Path(args.suggest)
                sp.parent.mkdir(parents=True, exist_ok=True)
                sp.write_text(json.dumps(tmpl, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"  제안 템플릿 저장: {sp}  (수정 후 --resolve {sp} 로 반영)")

    from .readiness import assess_readiness, render_readiness_lines
    rd = assess_readiness(res)
    if rd.items:
        print(f"\n=== 6. 제출 준비 점검 — {rd.headline} ===")
        for line in render_readiness_lines(rd):
            print(f"  {line}")
    if args.readiness_json:
        out_p = Path(args.readiness_json)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(rd.to_dict(), ensure_ascii=False, indent=2),
                         encoding="utf-8")
        print(f"준비 점검 JSON 저장: {out_p}")

    if args.out:
        from .report import write_markdown_report
        out = write_markdown_report(res, args.out, backend=cfg.backend)
        print(f"\n리포트 저장: {out}")
    if args.submission:
        from .report import write_submission
        sub = write_submission(res, args.submission)
        print(f"제출용 문서 저장: {sub}")
    if args.submit_dry_run or args.submit_confirm:
        from until.execution.submission_gate import build_submission_plan
        from until.capture.sources.canvas_submit import submit
        from until.capture.sources.models import AssignmentRef
        ref = AssignmentRef(
            id=str(res.spec.get("assignment_id", "")),
            title=str(res.spec.get("title", "")),
            course_id=str(res.spec.get("course_id", "")))
        evidence_texts = [getattr(sd, "text", "") or ""
                         for sd in (getattr(res, "source_docs", None) or [])]
        spec_reqs = (res.spec or {}).get("requirements")
        if isinstance(spec_reqs, list):
            evidence_texts.extend(str(x) for x in spec_reqs)
        from until.capture.sources.discovery import SNU_ETL_BASE
        plan = build_submission_plan(
            res, ref, base_url=SNU_ETL_BASE, evidence_texts=evidence_texts)
        print("제출 게이트:", "허용" if plan.allowed else "차단")
        for b in plan.blocks:
            print(f"  ✗ {b.code}: {b.message}")
        for w in plan.warnings:
            print(f"  ⚠ {w.code}: {w.message}")
        armed = False
        if args.submit_confirm:
            proceed, armed = _confirm_submission_from_cli()
            if not proceed:
                print("제출을 취소했습니다.")
                return 1
        receipt = submit(plan, plan.confirm_nonce, armed=armed,
                         token=os.getenv("UNTIL_CANVAS_TOKEN", ""))
        label = "전송 결과" if receipt.sent else "보낼 요청(dry-run)"
        print(f"{label}:", receipt.request["method"], receipt.request["url"])
    if args.filled_form is not None:
        from .report import write_filled_form
        got = write_filled_form(res, None if args.filled_form is True else args.filled_form)
        if got:
            fp, stats = got
            print(f"채워진 양식 저장: {fp} (원본 서식 유지 · {stats.describe()} 주입)")
        else:
            print("채워진 양식: 양식 첨부(hwpx/docx)가 없거나 옮길 값이 없어 건너뜀")

    if args.feedback is not None:
        from .feedback import record_from_result, append_record, summarize
        rec = record_from_result(res, satisfaction=args.satisfaction, backend=cfg.backend)
        log_path = append_record(rec, args.feedback)
        s = summarize(args.feedback)
        print(f"\n피드백 적립: {log_path} (누적 {s['runs']}회, "
              f"평균 결정 {s['avg_decisions']} · 평균 재요청 {s['avg_reasks']} · "
              f"통과율 {s['pass_rate']}"
              + (f" · 평균 만족도 {s['avg_satisfaction']}" if s.get('avg_satisfaction') else "")
              + (f" · 평균 준비경고 {s['avg_readiness_warnings']}"
                 if s.get('avg_readiness_warnings') is not None else "")
              + ")")

    # 답한 결정을 히스토리에 적립 — 출력(4.7 '지난 답' 병기) 이후에 해야
    # 같은 실행의 답이 자기 반향으로 다시 뜨지 않는다(비치명적).
    if resolved_answers is not None:
        try:
            from .context.answer_history import record_answers
            record_answers([d.note for d in res.draft.decisions], resolved_answers)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
