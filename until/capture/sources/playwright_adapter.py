"""
PlaywrightBrowserAdapter — 제품용 eTL 접속(브라우저 자동화).

Claude in Chrome(에이전트 전용)의 제품 대체재. 실제 출시 코드에 들어간다.

핵심: **영속 프로필**(user_data_dir)로 SSO 세션을 유지한다. 첫 실행에서
headless=False로 띄워 사용자가 MySNU 로그인을 직접 하면, 그 세션이 프로필에
저장돼 이후 호출은 로그인 없이 동작한다. 비밀번호는 우리 코드가 저장하지 않는다.

설치: pip install playwright && python -m playwright install chromium
"""
from __future__ import annotations
from pathlib import Path

from .models import RawAssignment, Attachment
from .moodle import parse_moodle_assignment

DEFAULT_PROFILE = str(Path.home() / ".until" / "etl_profile")


class PlaywrightBrowserAdapter:
    def __init__(self, user_data_dir: str = DEFAULT_PROFILE, headless: bool = False,
                 timeout_ms: int = 30000):
        self.user_data_dir = user_data_dir
        self.headless = headless
        self.timeout_ms = timeout_ms

    def _launch(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "PlaywrightBrowserAdapter엔 playwright가 필요합니다: "
                "pip install playwright && python -m playwright install chromium"
            ) from e
        Path(self.user_data_dir).mkdir(parents=True, exist_ok=True)
        pw = sync_playwright().start()
        ctx = pw.chromium.launch_persistent_context(
            self.user_data_dir, headless=self.headless
        )
        return pw, ctx

    def fetch_assignment(self, url: str) -> RawAssignment:
        pw, ctx = self._launch()
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
            # 로그인 페이지로 튕기면, 헤디드 창에서 사용자가 로그인할 때까지 대기.
            if "login" in page.url.lower():
                page.wait_for_url("**/mod/assign/**", timeout=180000)  # 최대 3분
            raw = parse_moodle_assignment(page.content(), page.url)
            # intro 텍스트는 라이브 DOM에서 더 깨끗하게 얻을 수 있으면 보강.
            try:
                txt = page.locator("#intro").inner_text(timeout=2000).strip()
                if txt:
                    raw.description = txt
            except Exception:
                pass
            self._ctx = ctx  # download에서 같은 세션 재사용
            self._pw = pw
            return raw
        except Exception:
            ctx.close(); pw.stop(); raise

    def download(self, attachment: Attachment, dest_dir: str) -> str:
        ctx = getattr(self, "_ctx", None)
        if ctx is None:
            pw, ctx = self._launch(); self._pw, self._ctx = pw, ctx
        # 인증된 세션 쿠키로 파일 GET
        resp = ctx.request.get(attachment.url, timeout=self.timeout_ms)
        dest = Path(dest_dir) / attachment.name
        dest.write_bytes(resp.body())
        return str(dest)

    def close(self):
        ctx = getattr(self, "_ctx", None); pw = getattr(self, "_pw", None)
        if ctx: ctx.close()
        if pw: pw.stop()
