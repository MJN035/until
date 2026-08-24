"""
P12 — 토큰 없이 브라우저 SSO로 '내 과제' 보기/초안 만들기.

  python run_etl_inbox.py            # 로그인 → 미제출 과제 목록 출력
  python run_etl_inbox.py 3          # 목록 3번 과제를 골라 초안+리포트 생성

토큰 불필요(브라우저 로그인). Groq로 초안을 쓰려면 환경변수:
  UNTIL_BASE_URL / UNTIL_API_KEY / UNTIL_MODEL (없으면 backend=mock).
"""
from __future__ import annotations
import os, sys
from pathlib import Path

from until.config import Config
from until.pipeline import run
from until.report import render_markdown_report
from until.capture.sources.playwright_discovery import PlaywrightDiscoveryAdapter
from until.capture.sources.discovery import EtlInbox, SNU_ETL_BASE
from until.capture.sources.etl import EtlSource
from until.capture.sources.canvas_api import parse_assignment_url
from until.context.etl_materials import collect_related_materials, materials_to_sources
from until.console import force_utf8

WORKDIR = "_until_work"


def main() -> int:
    # Windows 기본 cp949 콘솔에서도 기호(—·⚠ 등) 때문에 실행이 죽지 않게 한다.
    force_utf8()
    pick = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
    adapter = PlaywrightDiscoveryAdapter(base_url=SNU_ETL_BASE)
    try:
        items = EtlInbox(adapter, base_url=SNU_ETL_BASE).list_assignments(
            bucket=None, only_unsubmitted=True)
        print(f"\n=== 내 미제출 과제 {len(items)}건 (마감 임박순) ===")
        for i, a in enumerate(items, 1):
            due = (a.due_at or "마감없음")[:16].replace("T", " ")
            print(f"  {i:>2}. [{a.course_name[:18]}] {a.title[:34]}  | 마감 {due}")

        if pick is None:
            print("\n→ 초안을 만들려면: python run_etl_inbox.py <번호>")
            return 0
        if not (1 <= pick <= len(items)):
            print(f"번호는 1~{len(items)} 사이여야 합니다."); return 1

        target = items[pick - 1]
        print(f"\n[선택] {target.title}  수집·초안 작성 중...")
        Path(WORKDIR).mkdir(exist_ok=True)
        collected = EtlSource(target.url, adapter).collect(WORKDIR)
        files = collected.to_files(WORKDIR)
        base, cid, _ = parse_assignment_url(target.url)
        spec_like = {"deliverable": "과제", "goal": collected.title,
                     "requirements": [collected.description[:800]]}
        mats = collect_related_materials(adapter, cid, spec_like, base, k=5)
        if mats:
            print(f"[관련자료 {len(mats)}건] " + ", ".join(m.name[:20] for m in mats[:5]))

        cfg = Config()
        cfg.backend = "local" if os.getenv("UNTIL_API_KEY") else "mock"
        res = run(files, cfg, extra_context_sources=materials_to_sources(mats))
        out = Path(WORKDIR) / "inbox_report.md"
        out.write_text(render_markdown_report(res, backend=cfg.backend), encoding="utf-8")
        g = res.guard
        print(f"\n완료 — BoundaryGuard {'통과' if g.passed else '미통과'}, "
              f"결정 {res.draft.n_decisions}개, 재요청 {g.reasks}회")
        print(f"리포트: {out.resolve()}")
        return 0
    finally:
        try:
            adapter.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
