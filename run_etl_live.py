"""
eTL(Canvas) 라이브 1회 실행 — Playwright로 접속, 사용자가 SSO 로그인,
과제 자동 추출 → 파이프라인(Groq) → report.md.

PowerShell:
  $env:UNTIL_BASE_URL="https://api.groq.com/openai/v1"
  $env:UNTIL_API_KEY="<groq키>"
  $env:UNTIL_MODEL="llama-3.3-70b-versatile"
  pip install playwright openai ; python -m playwright install chromium
  python run_etl_live.py "https://myetl.snu.ac.kr/courses/302199/assignments/369118"
"""
from __future__ import annotations
import os, sys
from pathlib import Path

from until.config import Config
from until.pipeline import run
from until.report import render_markdown_report
from until.capture.sources.etl import EtlSource
from until.capture.sources.learningx_adapter import LearningXBrowserAdapter, parse_learningx_assignment
from until.console import force_utf8

DEFAULT_URL = "https://myetl.snu.ac.kr/courses/302199/assignments/369118"
WORKDIR = "_until_work"
LOGIN_TIMEOUT_MS = 300_000


class RobustLearningXAdapter(LearningXBrowserAdapter):
    """로그인 리다이렉트와 무관하게 '과제 본문이 렌더될 때까지' 기다린다."""
    def fetch_assignment(self, url: str):
        pw, ctx = self._launch()
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
            print("\n[1/4] 열린 크롬 창에서 MySNU 로그인을 해주세요. 과제 페이지가 뜰 때까지 최대 5분 대기...")
            page.wait_for_selector("div.description.user_content", timeout=LOGIN_TIMEOUT_MS)
            print("[2/4] 과제 페이지 감지 → 추출 중...")
            raw = parse_learningx_assignment(page.content(), page.url)
            try:
                txt = page.locator("div.description.user_content").first.inner_text(timeout=3000).strip()
                if txt:
                    raw.description = txt
            except Exception:
                pass
            self._ctx, self._pw = ctx, pw
            return raw
        except Exception:
            ctx.close(); pw.stop(); raise


def _check_env() -> bool:
    missing = [k for k in ("UNTIL_BASE_URL", "UNTIL_API_KEY", "UNTIL_MODEL") if not os.getenv(k)]
    if missing:
        print("환경변수 누락:", ", ".join(missing))
        print('  $env:UNTIL_BASE_URL="https://api.groq.com/openai/v1"')
        print('  $env:UNTIL_API_KEY="<groq키>"; $env:UNTIL_MODEL="llama-3.3-70b-versatile"')
        return False
    return True


def main() -> int:
    # Windows 기본 cp949 콘솔에서도 기호(—·⚠ 등) 때문에 실행이 죽지 않게 한다.
    force_utf8()
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    if not _check_env():
        return 1
    Path(WORKDIR).mkdir(exist_ok=True)
    adapter = RobustLearningXAdapter()
    src = EtlSource(url, adapter)
    print(f"eTL 접속: {url}")
    collected = src.collect(WORKDIR)
    files = collected.to_files(WORKDIR)
    try:
        adapter.close()
    except Exception:
        pass
    print(f"[3/4] 수집 완료 — 과제: {collected.title} | 첨부 {len(collected.attachments)}개")
    print("[4/4] 파이프라인 실행(backend=local/Groq)...")
    cfg = Config(); cfg.backend = "local"
    res = run(files, cfg)
    Path("report.md").write_text(render_markdown_report(res, backend=f"local/{os.getenv('UNTIL_MODEL')}"), encoding="utf-8")
    g = res.guard
    print("\n===== 완료 =====")
    print(f"BoundaryGuard: {'통과' if g.passed else '미통과'} | 결정지점 {res.draft.n_decisions}개 | 재요청 {g.reasks}회")
    print(f"리포트: {Path('report.md').resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
