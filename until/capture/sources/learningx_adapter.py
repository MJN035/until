"""
LearningX/Canvas 과제 페이지 파서 + 브라우저 어댑터.

서울대 새 eTL(myetl.snu.ac.kr)은 Moodle 과제 페이지가 아니라 Canvas 계열의
LearningX UI를 쓴다. 파싱은 HTML만 받는 순수 함수로 분리하고, 라이브 접속은
Playwright 영속 프로필 어댑터 뒤에 둔다.
"""
from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse
from typing import List

from .models import Attachment, RawAssignment, safe_filename
from .playwright_adapter import DEFAULT_PROFILE


_COURSE_ID_RE = re.compile(r"/courses/(\d+)")


def is_learningx_url(url: str) -> bool:
    """LearningX/Canvas 과제 URL인지 가볍게 판별한다."""
    parsed = urlparse(url)
    return (
        "myetl.snu.ac.kr" in parsed.netloc
        and "/courses/" in parsed.path
        and "/assignments/" in parsed.path
    )


class _LearningXParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title_parts: List[str] = []
        self.description_parts: List[str] = []
        self.attachments: List[Attachment] = []
        self.env_course_id = ""
        self._depth = 0
        self._in_title = False
        self._title_depth: int | None = None
        self._description_depth: int | None = None
        self._cur_href: str | None = None
        self._cur_title = ""
        self._cur_link_text: List[str] = []
        self._cur_is_file = False

    def handle_starttag(self, tag, attrs):
        self._depth += 1
        a = dict(attrs)
        cls = a.get("class", "") or ""
        classes = set(cls.split())

        if tag in {"h1", "h2"} and ("title" in classes or "assignment-title" in cls):
            self._in_title = True
            self._title_depth = self._depth

        if self._description_depth is None and "description" in classes and "user_content" in classes:
            self._description_depth = self._depth

        if tag == "a":
            href = a.get("href", "") or ""
            api_endpoint = a.get("data-api-endpoint", "") or ""
            self._cur_href = href
            self._cur_title = a.get("title", "") or a.get("download", "") or ""
            self._cur_link_text = []
            self._cur_is_file = (
                "instructure_file_link" in classes
                or "/files/" in href and "/download" in href
                or "/api/v1/" in api_endpoint and "/files/" in api_endpoint
            )

    def handle_endtag(self, tag):
        if self._cur_href is not None and tag == "a":
            text = unescape("".join(self._cur_link_text)).strip()
            if self._cur_is_file:
                self.attachments.append(
                    Attachment(name=_attachment_name(self._cur_title, text, self._cur_href), url=self._cur_href)
                )
            self._cur_href = None
            self._cur_title = ""
            self._cur_link_text = []
            self._cur_is_file = False

        if self._title_depth is not None and self._depth == self._title_depth:
            self._in_title = False
            self._title_depth = None

        if self._description_depth is not None and self._depth == self._description_depth:
            self._description_depth = None

        self._depth -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        if self._description_depth is not None:
            self.description_parts.append(data)
        if self._cur_href is not None:
            self._cur_link_text.append(data)


def _clean_text(parts: List[str]) -> str:
    return unescape(" ".join("".join(parts).split())).strip()


def _attachment_name(title: str, text: str, href: str) -> str:
    name = title.strip() or text.strip()
    if name:
        return unescape(name)
    path = unquote(urlparse(href).path.rstrip("/"))
    tail = path.rsplit("/", 1)[-1]
    return tail or "attachment"


def _course_label(page_url: str, html: str) -> str:
    m = _COURSE_ID_RE.search(page_url) or _COURSE_ID_RE.search(html)
    if m:
        return f"LearningX course {m.group(1)}"
    return "(과목 미상)"


def parse_learningx_assignment(html: str, page_url: str) -> RawAssignment:
    """LearningX/Canvas 과제 HTML에서 제목, 본문, 첨부를 추출한다."""
    p = _LearningXParser()
    p.feed(html)

    title = _clean_text(p.title_parts) or "(제목 없음)"
    description = _clean_text(p.description_parts) or "(과제 설명을 페이지에서 추출하지 못함)"

    attachments: List[Attachment] = []
    seen = set()
    for att in p.attachments:
        url = urljoin(page_url, att.url)
        if url in seen:
            continue
        seen.add(url)
        attachments.append(Attachment(name=att.name, url=url))

    return RawAssignment(
        title=title,
        course=_course_label(page_url, html),
        description=description,
        attachments=attachments,
        url=page_url,
    )


class LearningXBrowserAdapter:
    """제품용 LearningX 접속 어댑터. 로그인은 Playwright 영속 프로필에서 사용자가 직접 한다."""

    def __init__(self, user_data_dir: str = DEFAULT_PROFILE, headless: bool = False, timeout_ms: int = 30000):
        self.user_data_dir = user_data_dir
        self.headless = headless
        self.timeout_ms = timeout_ms

    def _launch(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "LearningXBrowserAdapter엔 playwright가 필요합니다: "
                "pip install playwright && python -m playwright install chromium"
            ) from e
        Path(self.user_data_dir).mkdir(parents=True, exist_ok=True)
        pw = sync_playwright().start()
        ctx = pw.chromium.launch_persistent_context(self.user_data_dir, headless=self.headless)
        return pw, ctx

    def fetch_assignment(self, url: str) -> RawAssignment:
        pw, ctx = self._launch()
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
            if "login" in page.url.lower():
                page.wait_for_url("**/courses/*/assignments/**", timeout=180000)
            raw = parse_learningx_assignment(page.content(), page.url)
            try:
                txt = page.locator("div.description.user_content").inner_text(timeout=2000).strip()
                if txt:
                    raw.description = txt
            except Exception:
                pass
            self._ctx = ctx
            self._pw = pw
            return raw
        except Exception:
            ctx.close()
            pw.stop()
            raise

    def download(self, attachment: Attachment, dest_dir: str) -> str:
        ctx = getattr(self, "_ctx", None)
        if ctx is None:
            pw, ctx = self._launch()
            self._pw, self._ctx = pw, ctx
        resp = ctx.request.get(attachment.url, timeout=self.timeout_ms)
        dest = Path(dest_dir) / safe_filename(attachment.name)
        dest.write_bytes(resp.body())
        return str(dest)

    def close(self):
        ctx = getattr(self, "_ctx", None)
        pw = getattr(self, "_pw", None)
        if ctx:
            ctx.close()
        if pw:
            pw.stop()
