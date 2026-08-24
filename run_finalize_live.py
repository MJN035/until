"""
P6 라이브 검증 — 초안을 한 번만 받아 저장하고, 그 저장본에 사람의 결정을 반영해
finalize(2차 패스)로 최종 완성본을 만든다. (라이브 LLM 비결정성 회피: 재생성 금지)

  1) python run_finalize_live.py draft                 # 경계선 초안 생성·저장, 결정 출력
  2) python run_finalize_live.py finalize answers.json # 저장된 초안 + 답변 → 최종 완성본

answers.json 형식: {"1": "내 답", "3": "내 답"}  (1-based 결정 번호 → 답변; 미기재는 마커 유지)
환경변수: UNTIL_BASE_URL / UNTIL_API_KEY / UNTIL_MODEL (Groq).
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path

from until.config import Config
from until.pipeline import run, finalize, Result
from until.boundary.models import Draft
from until.boundary.resolve import load_resolution_answers
from until.execution.boundary_guard import GuardReport
from until.report import render_markdown_report
from until.console import force_utf8

BOUNDARY = Path("_until_work/boundary.json")
ASSIGNMENT = "_until_work/assignment.md"


def _check_env() -> bool:
    miss = [k for k in ("UNTIL_BASE_URL", "UNTIL_API_KEY", "UNTIL_MODEL") if not os.getenv(k)]
    if miss:
        print("환경변수 누락:", ", ".join(miss)); return False
    return True


def do_draft() -> int:
    cfg = Config(); cfg.backend = "local"
    print(f"[draft] 경계선 초안 생성(Groq)... ({ASSIGNMENT})")
    res = run([ASSIGNMENT], cfg)
    BOUNDARY.parent.mkdir(parents=True, exist_ok=True)
    BOUNDARY.write_text(json.dumps({
        "body": res.draft.body, "spec": res.spec,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  → 결정 지점 {res.draft.n_decisions}개 (BoundaryGuard "
          f"{'통과' if res.guard.passed else '미통과'}, 재요청 {res.guard.reasks}) — 저장: {BOUNDARY}")
    for i, dp in enumerate(res.draft.decisions, 1):
        print(f"  [{i}] {dp.note}")
    return 0


def do_finalize(answers_path: str) -> int:
    if not BOUNDARY.exists():
        print(f"먼저 'draft'를 실행하세요. ({BOUNDARY} 없음)"); return 1
    saved = json.loads(BOUNDARY.read_text(encoding="utf-8"))
    draft = Draft.from_text(saved["body"])
    answers = load_resolution_answers(answers_path)
    # 저장된 초안으로 최소 Result 구성 → finalize 호출(재생성 없음).
    res = Result(
        documents=[], spec=saved["spec"], draft=draft,
        guard=GuardReport(passed=True, attempts=1, reasks=0), context=None,
    )
    print(f"[finalize] 저장된 초안의 결정 {draft.n_decisions}개 중 {len(answers)}개에 답 반영:")
    for i, dp in enumerate(draft.decisions, 1):
        tag = f"  ←답: {answers[i]}" if i in answers else "  (미답 → 마커 유지)"
        print(f"  [{i}] {dp.note[:55]}…{tag}")

    cfg = Config(); cfg.backend = "local"
    print("[finalize] 2차 패스(Groq)...")
    res = finalize(res, answers, cfg)
    out = Path("_until_work/report_final.md")
    out.write_text(render_markdown_report(res, backend=f"local/{os.getenv('UNTIL_MODEL')}"),
                   encoding="utf-8")
    fg = res.final_guard
    print("\n===== 최종 완성본 =====")
    print(res.final_draft.body)
    print("\n===== 요약 =====")
    print(f"Finalize BoundaryGuard: {'통과' if fg.passed else '미통과'} | "
          f"시도 {fg.attempts}회(재요청 {fg.reasks}) | 남은(미답) 결정 {res.final_draft.n_decisions}개")
    print(f"리포트: {out.resolve()}")
    return 0


def main() -> int:
    # Windows 기본 cp949 콘솔에서도 기호(—·⚠ 등) 때문에 실행이 죽지 않게 한다.
    force_utf8()
    if not _check_env():
        return 1
    cmd = sys.argv[1] if len(sys.argv) > 1 else "draft"
    if cmd == "draft":
        return do_draft()
    if cmd == "finalize":
        if len(sys.argv) < 3:
            print("사용법: python run_finalize_live.py finalize answers.json"); return 1
        return do_finalize(sys.argv[2])
    print(f"알 수 없는 명령: {cmd!r} (draft | finalize)"); return 1


if __name__ == "__main__":
    raise SystemExit(main())
