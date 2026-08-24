"""
P12 — 토큰 없는 브라우저 SSO 탐색 폴백.

Canvas REST API는 보통 액세스 토큰이 필요하지만, **로그인된 브라우저 세션의 쿠키**로도
같은 /api/v1 엔드포인트를 호출할 수 있다. 그래서 토큰이 없어도 사용자가 한 번 SSO
로그인하면, 그 세션으로 과목·과제·자료를 조회할 수 있다.

- 영속 프로필(PlaywrightBrowserAdapter와 동일 패턴)로 SSO 세션 유지.
- 파싱은 canvas_api의 순수 파서를 그대로 재사용(접속 방식만 다름).
- DiscoveryAdapter / BrowserAdapter 인터페이스를 모두 만족 → EtlInbox·EtlSource와 호환.

설치: pip install playwright && python -m playwright install chromium
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from .models import Attachment, RawAssignment, CourseRef, AssignmentRef, safe_filename
from .playwright_adapter import DEFAULT_PROFILE
from . import canvas_api as C


class PlaywrightDiscoveryAdapter:
    """로그인된 브라우저 세션으로 Canvas API를 호출하는 토큰리스 어댑터."""

    def __init__(self, base_url: str = "https://myetl.snu.ac.kr",
                 user_data_dir: str = DEFAULT_PROFILE, headless: bool = False,
                 timeout_ms: int = 30000, login_timeout_ms: int = 300_000):
        self.base_url = base_url.rstrip("/")
        self.user_data_dir = user_data_dir
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.login_timeout_ms = login_timeout_ms
        self._ctx = None
        self._pw = None

    # ── 세션 ────────────────────────────────────────────────────────
    def _ensure_session(self):
        if self._ctx is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("playwright 필요: pip install playwright && "
                               "python -m playwright install chromium") from e
        Path(self.user_data_dir).mkdir(parents=True, exist_ok=True)
        # sync_playwright 엔진은 한 스레드에서 '딱 한 번만' 시작한다(두 번 start하면
        # asyncio 루프 충돌). 재연결 시엔 엔진은 두고 브라우저 컨텍스트(창)만 다시 연다.
        if self._pw is None:
            self._pw = sync_playwright().start()
        ctx = self._pw.chromium.launch_persistent_context(self.user_data_dir, headless=self.headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(self.base_url, timeout=self.timeout_ms, wait_until="domcontentloaded")
        print("\n[로그인] 열린 창에서 MySNU 로그인을 해주세요(최대 5분 대기)...")
        # /api/v1/users/self 가 200이 될 때까지 = 로그인 완료 신호.
        page.wait_for_function(
            """async () => {
                try { const r = await fetch('/api/v1/users/self', {headers:{Accept:'application/json'}});
                      return r.ok; } catch (e) { return false; }
            }""",
            timeout=self.login_timeout_ms,
        )
        print("[로그인] 완료 — 세션으로 eTL을 조회합니다.")
        self._ctx = ctx

    def _reset(self) -> None:
        """브라우저 컨텍스트(창)만 닫는다. sync_playwright 엔진(self._pw)은 살려 둠
        (같은 스레드에서 재start하면 asyncio 충돌). 다음 호출 때 컨텍스트만 재오픈."""
        try:
            if self._ctx:
                self._ctx.close()
        except Exception:
            pass
        self._ctx = None

    def _abs(self, path_or_url: str) -> str:
        return path_or_url if path_or_url.startswith("http") else f"{self.base_url}{path_or_url}"

    def _raw_get(self, url: str):
        """세션으로 GET. 창이 닫혀 끊긴 경우 영속 프로필로 재오픈 후 1회 재시도
        (쿠키가 디스크에 저장돼 있어 보통 재로그인 불필요)."""
        self._ensure_session()
        try:
            return self._ctx.request.get(url, headers={"Accept": "application/json"},
                                         timeout=self.timeout_ms)
        except Exception as e:
            if "closed" in str(e).lower():
                self._reset()
                self._ensure_session()
                return self._ctx.request.get(url, headers={"Accept": "application/json"},
                                             timeout=self.timeout_ms)
            raise

    def _fetch(self, url: str):
        """세션 쿠키로 GET → (JSON, 다음 페이지 URL). 비-JSON은 명확한 에러로."""
        resp = self._raw_get(url)
        text = resp.text()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                "eTL가 JSON이 아닌 응답을 보냈습니다(SSO 세션 만료 가능). 다시 로그인하세요."
            ) from e
        try:
            link = resp.headers.get("link", "") or ""
        except Exception:
            link = ""
        return data, C._next_link(link)

    def _get_json(self, path_or_url: str):
        """단일 객체 GET(과제 1건 등). path는 base_url 기준."""
        self._ensure_session()
        data, _ = self._fetch(self._abs(path_or_url))
        return data

    def _get_paginated(self, path_or_url: str, cap_pages: int = 12) -> list:
        """list 엔드포인트를 rel=next로 끝까지 따라간다(토큰 어댑터와 동일, 100개 초과 누락 방지)."""
        self._ensure_session()
        url = self._abs(path_or_url)
        out: list = []
        pages = 0
        while url and pages < cap_pages:
            data, nxt = self._fetch(url)
            if not isinstance(data, list):
                # 목록이 와야 하는데 에러 객체(dict) 등이 옴 → 사람이 읽는 메시지로.
                msg = ""
                if isinstance(data, dict):
                    err = (data.get("errors") or data.get("message")
                           or data.get("status") or data)
                    msg = str(err)[:300]
                raise RuntimeError(
                    "eTL가 과제 목록 대신 예상 밖 응답을 보냈습니다 "
                    "(SSO 세션/권한 문제일 수 있음): " + (msg or repr(data)[:300]))
            out.extend(data)
            url = nxt
            pages += 1
        return out

    # ── DiscoveryAdapter ────────────────────────────────────────────
    def list_courses(self, base_url: Optional[str] = None) -> List[CourseRef]:
        return C.parse_courses(self._get_paginated(
            "/api/v1/courses?enrollment_state=active&include[]=term&per_page=100"))

    def list_assignments(self, course: CourseRef, base_url: Optional[str] = None,
                         bucket: Optional[str] = None) -> List[AssignmentRef]:
        path = f"/api/v1/courses/{course.id}/assignments?per_page=100&include[]=submission"
        if bucket:
            path += f"&bucket={bucket}"
        return C.parse_assignments(self._get_paginated(path), self.base_url, course=course)

    def list_course_files(self, course_id: str, base_url: Optional[str] = None) -> List[Attachment]:
        return C.parse_canvas_files(
            self._get_paginated(f"/api/v1/courses/{course_id}/files?per_page=100"), self.base_url)

    def list_modules(self, course_id: str, base_url: Optional[str] = None) -> List[Attachment]:
        return C.parse_modules(
            self._get_paginated(f"/api/v1/courses/{course_id}/modules?include[]=items&per_page=100"),
            self.base_url)

    # ── BrowserAdapter (과제 1건 수집) ──────────────────────────────
    def fetch_assignment(self, url: str) -> RawAssignment:
        base, cid, aid = C.parse_assignment_url(url)
        return C.parse_canvas_api_assignment(
            self._get_json(C.api_assignment_url(self.base_url, cid, aid)), self.base_url)

    def download(self, attachment: Attachment, dest_dir: str) -> str:
        resp = self._raw_get(attachment.url)  # 창 닫힘 시 자동 재연결
        dest = Path(dest_dir) / safe_filename(attachment.name)
        dest.write_bytes(resp.body())
        return str(dest)

    def close(self):
        if self._ctx:
            self._ctx.close()
        if self._pw:
            self._pw.stop()
        self._ctx = self._pw = None
