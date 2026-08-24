"""Until 종합 데모 — 5개 유형 샘플을 mock으로 일괄 실행(키·인터넷 불필요).

  python demo.py            # 전체 요약 출력 + 제출용 문서 저장(_until_work/demo/)
  python demo.py -v         # 초안 본문까지 출력

시연 포인트: 유형 감지 → 경계선 초안(결정 마커+근거) → 제출 준비 점검 → 제출용 내보내기.
"""
from __future__ import annotations
import sys
from pathlib import Path

from until.config import Config
from until.console import force_utf8
from until.pipeline import run
from until.readiness import assess_readiness, render_readiness_lines
from until.understanding.task_type import LABELS
from until.boundary.rationale import classify_decision
from until.report import write_submission

SAMPLES = [
    "examples/sample_assignment.txt",
    "examples/sample_problemset.txt",
    "examples/sample_code.txt",
    "examples/sample_report.txt",
    "examples/sample_presentation.txt",
    "examples/sample_extension.txt",   # 연장 공지 이해(연장된 날짜+시각+'연장됨')
]
OUT_DIR = Path("_until_work/demo")


def main(argv: list[str] | None = None) -> int:
    # Windows 기본 cp949 콘솔에서도 기호(—·⚠ 등) 때문에 실행이 죽지 않게 한다.
    force_utf8()
    verbose = "-v" in (argv or sys.argv[1:])
    cfg = Config(); cfg.backend = "mock"
    print("=" * 62)
    print(" UNTIL 데모 — 과제를 경계선 직전까지, 결정은 당신 몫 (mock)")
    print("=" * 62)
    for si, path in enumerate(SAMPLES):
        res = run([path], cfg)
        ttype = res.spec.get("task_type", "?")
        print(f"\n▶ {Path(path).name}  [{LABELS.get(ttype, ttype)}]")
        # 경계선: 남긴 결정 + 왜 사람 몫인지.
        if res.draft.decisions:
            for i, d in enumerate(res.draft.decisions, 1):
                rat = classify_decision(d.note)
                print(f"   결정 {i} ({rat.category}) {d.note[:52]}")
        else:
            print("   결정 0개 — 정형 과제(문제풀이·코드)는 정상입니다.")
        # 제출 준비 점검(결정적).
        rd = assess_readiness(res)
        for line in render_readiness_lines(rd):
            print(f"   {line}")
        if verbose:
            print("   --- 초안 ---")
            for ln in res.draft.body.strip().splitlines():
                print(f"   {ln}")
        # 제출용 문서 저장(.md — 첫 샘플은 .docx도 함께: 워드 제출 시연).
        out = write_submission(res, OUT_DIR / f"{Path(path).stem}.md")
        print(f"   제출용 저장: {out}")
        if si == 0:
            outd = write_submission(res, OUT_DIR / f"{Path(path).stem}.docx")
            print(f"   제출용 저장: {outd} (워드, 의존성 0)")
    # 재방문 시나리오 — '지난 답' 재사용 데모(임시 히스토리, 실제 기록에 안 남음).
    import tempfile
    from until.context import answer_history as ah
    print("\n" + "=" * 62)
    print(" 재방문 시나리오 — 지난 답 재사용(경계선 유지: 클릭으로만 채움)")
    print("=" * 62)
    res = run([SAMPLES[0]], cfg)
    notes = [d.note for d in res.draft.decisions]
    if notes:
        with tempfile.TemporaryDirectory() as td:
            old = ah.HISTORY_PATH
            ah.HISTORY_PATH = Path(td) / "hist.jsonl"
            try:
                answers = {i: f"{i}번은 이렇게 정합니다" for i in range(1, len(notes) + 1)}
                answers[1] = "형식 결정론을 핵심 논지로 정합니다"
                ah.record_answers(notes, answers)
                h = ah.suggest_from_history(notes[0])
                print(f"  지난 학기 답 {len(answers)}건 적립(합니다체)")
                print(f"  같은 결정 재등장 → 🕘 재제안: {h.answer} (유사도 {h.similarity})")
                style = ah.answers_style_hint()
                if style:
                    print(f"  문체 감지 → {style.lstrip('- ')}")
                print("  웹에선 결정 칸 위 '🕘 지난 답' 칩 + AI 제안이 이 문체를 따라요.")
            finally:
                ah.HISTORY_PATH = old

    print(f"\n완료 — 제출용 문서 {len(SAMPLES)}건: {OUT_DIR}/")
    print("웹으로 보려면: python -m until.web  (http://127.0.0.1:8000)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
