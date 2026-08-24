"""
Moodle 과제 페이지 파서 — 순수 함수(브라우저/네트워크 불필요, 테스트 가능).

eTL은 Moodle 기반이라 과제 페이지(mod/assign/view.php) HTML에서:
  - 제목(h2)
  - 과제 설명(intro 영역)
  - 첨부 링크(Moodle 파일 URL은 'pluginfile.php'를 포함)
를 추출한다. 라이브 어댑터(Playwright 등)는 page.content()를 이 함수에 넘기기만 하면 된다.
"""
from __future__ import annotations
from html.parser import HTMLParser
from html import unescape
from urllib.parse import urljoin
from typing import List

from .models import RawAssignment, Attachment


class _MoodleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title_parts: List[str] = []
        self.intro_parts: List[str] = []
        self.course: str = ""
        self.attachments: List[Attachment] = []
        self._depth = 0
        self._in_h2 = False
        self._h2_done = False
        self._intro_depth = None       # intro 영역 시작 깊이
        self._cur_href = None
        self._cur_link_text: List[str] = []
        self._cur_is_file = False
        self._cur_is_course = False

    def handle_starttag(self, tag, attrs):
        self._depth += 1
        a = dict(attrs)
        if tag == "h2" and not self._h2_done:
            self._in_h2 = True
        # intro 영역 진입 (id=intro 또는 class에 activity-description/box generalbox)
        cls = a.get("class", "") or ""
        if self._intro_depth is None and (
            a.get("id") == "intro"
            or "activity-description" in cls
            or ("box" in cls and "generalbox" in cls)
        ):
            self._intro_depth = self._depth
        if tag == "a":
            href = a.get("href", "") or ""
            self._cur_href = href
            self._cur_link_text = []
            self._cur_is_file = "pluginfile.php" in href
            self._cur_is_course = "course/view.php" in href

    def handle_endtag(self, tag):
        if tag == "h2" and self._in_h2:
            self._in_h2 = False
            self._h2_done = True
        if tag == "a" and self._cur_href is not None:
            text = unescape("".join(self._cur_link_text)).strip()
            if self._cur_is_file:
                name = text or self._cur_href.rstrip("/").split("/")[-1].split("?")[0]
                self.attachments.append(Attachment(name=name, url=self._cur_href))
            elif self._cur_is_course and not self.course and text:
                self.course = text
            self._cur_href = None
        if self._intro_depth is not None and self._depth == self._intro_depth:
            self._intro_depth = None
        self._depth -= 1

    def handle_data(self, data):
        if self._in_h2:
            self.title_parts.append(data)
        if self._cur_href is not None:
            self._cur_link_text.append(data)
        if self._intro_depth is not None:
            self.intro_parts.append(data)


def parse_moodle_assignment(html: str, page_url: str) -> RawAssignment:
    p = _MoodleParser()
    p.feed(html)
    title = unescape(" ".join("".join(p.title_parts).split())).strip() or "(제목 없음)"
    intro = unescape(" ".join("".join(p.intro_parts).split())).strip()
    # 상대경로 첨부 URL을 절대경로로
    atts: List[Attachment] = []
    seen = set()
    for a in p.attachments:
        url = urljoin(page_url, a.url)
        if url in seen:
            continue
        seen.add(url)
        atts.append(Attachment(name=a.name, url=url))
    return RawAssignment(
        title=title, course=p.course or "(과목 미상)",
        description=intro or "(과제 설명을 페이지에서 추출하지 못함)",
        attachments=atts, url=page_url,
    )
