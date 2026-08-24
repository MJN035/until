"""
eTL Moodle Web Services(읽기 전용) 라이브 검증 러너.

  python run_etl_ws_live.py                # 지형 조사(활성 함수) + 미제출 과제 목록
  python run_etl_ws_live.py 3              # 목록 3번 과제 → 자료·공지 수집 + 초안/리포트

토큰 필요(읽기 전용): 환경변수 UNTIL_ETL_WS_TOKEN (또는 UNTIL_CANVAS_TOKEN).
베이스: UNTIL_ETL_BASE (기본 SNU eTL). Groq로 초안을 쓰려면:
  UNTIL_BASE_URL / UNTIL_API_KEY / UNTIL_MODEL (없으면 backend=mock).

⚠ 이 러너는 쓰기 함수를 절대 호출하지 않는다(moodle_ws allowlist가 코드로 강제).
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

from until.config import Config
from until.pipeline import run
from until.report import render_markdown_report
from until.capture.sources.discovery import EtlInbox, SNU_ETL_BASE
from until.capture.sources.etl import EtlSource
from until.capture.sources.moodle_ws import MoodleWsAdapter, print_site_inventory
from until.capture.sources.models import CourseRef
from until.context.etl_materials import (
    collect_related_materials, fetch_material_texts, materials_to_sources,
)
from until.context.etl_announcements import (
    collect_related_announcements, announcements_to_sources, spec_announcements,
)
from until.console import force_utf8

WORKDIR = "_until_work"


def _base() -> str:
    return (os.getenv("UNTIL_ETL_BASE") or SNU_ETL_BASE).strip()


def main() -> int:
    # Windows 기본 cp949 콘솔에서도 기호(—·⚠ 등) 때문에 실행이 죽지 않게 한다.
    force_utf8()
    pick = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
    base = _base()
    try:
        adapter = MoodleWsAdapter(base)  # 토큰은 env에서(없으면 여기서 친절한 예외)
    except ValueError as e:
        print(e)
        return 1

    # 0) 지형 조사 — 팀 요청 '최초 1회 활성 함수 확인'.
    print_site_inventory(base, token=adapter.token)

    items = EtlInbox(adapter, base_url=base).list_assignments(
        bucket=None, only_unsubmitted=True, max_workers=1)
    print(f"\n=== 내 미제출 과제 {len(items)}건 (마감 임박순) ===")
    for i, a in enumerate(items, 1):
        due = (a.due_at or "마감없음")[:16].replace("T", " ")
        print(f"  {i:>2}. [{a.course_name[:18]}] {a.title[:34]}  | 마감 {due}")

    if pick is None:
        print("\n→ 초안을 만들려면: python run_etl_ws_live.py <번호>")
        return 0
    if not (1 <= pick <= len(items)):
        print(f"번호는 1~{len(items)} 사이여야 합니다.")
        return 1

    target = items[pick - 1]
    print(f"\n[선택] {target.title}  수집·초안 작성 중...")
    Path(WORKDIR).mkdir(exist_ok=True)
    collected = EtlSource(target.url, adapter).collect(WORKDIR)
    files = collected.to_files(WORKDIR)
    cid = adapter.course_id_for_url(target.url) or target.course_id
    spec_like = {"deliverable": "과제", "goal": collected.title,
                 "requirements": [collected.description[:800]]}

    extra = []
    mats = collect_related_materials(adapter, cid, spec_like, base, k=5)
    if mats:
        print(f"[관련자료 {len(mats)}건] " + ", ".join(m.name[:20] for m in mats[:5]))
        extra += materials_to_sources(mats, fetch_material_texts(adapter, mats))
    course = CourseRef(id=cid, name=target.course_name or collected.course)
    anns = collect_related_announcements(adapter, course, spec_like, k=3)
    if anns:
        print(f"[관련공지 {len(anns)}건] " + ", ".join(a.subject[:20] for a in anns))
        extra += announcements_to_sources(spec_announcements(anns))

    cfg = Config()
    cfg.backend = "local" if os.getenv("UNTIL_API_KEY") else "mock"
    res = run(files, cfg, extra_context_sources=extra or None)
    res.etl_materials = mats
    res.etl_announcements = anns
    out = Path(WORKDIR) / "ws_report.md"
    out.write_text(render_markdown_report(res, backend=cfg.backend), encoding="utf-8")
    g = res.guard
    print(f"\n완료 — BoundaryGuard {'통과' if g.passed else '미통과'}, "
          f"결정 {res.draft.n_decisions}개, 재요청 {g.reasks}회")
    print(f"리포트: {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
