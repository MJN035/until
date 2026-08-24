"""
서울대학교 eTL (LMS) 커넥터.

새 eTL은 LearningX/Canvas, 구 eTL은 Moodle 기반이며 모두 MySNU SSO 뒤에 있다.
따라서 '로그인된 브라우저 세션'을 통해 접근한다(BrowserAdapter 주입).

  - 라이브:   LearningXBrowserAdapter / PlaywrightBrowserAdapter — 영속 프로필 기반.
  - 오프라인: FixtureBrowserAdapter — 로그인·네트워크 없이 동일 흐름 테스트.

EtlSource.collect() 는 어댑터로부터 과제 본문 + 첨부 목록을 받아 첨부를 내려받고,
CollectedAssignment 를 돌려준다. 그 결과(.to_files())를 기존 파이프라인에 흘려보낸다.
"""
from __future__ import annotations
import shutil
from pathlib import Path
from typing import List

from .base import BrowserAdapter
from .models import Attachment, RawAssignment, CollectedAssignment

SNU_ETL_BASE = "https://etl.snu.ac.kr"


class EtlSource:
    def __init__(self, assignment_url: str, adapter: BrowserAdapter):
        self.assignment_url = assignment_url
        self.adapter = adapter

    def collect(self, dest_dir: str) -> CollectedAssignment:
        raw: RawAssignment = self.adapter.fetch_assignment(self.assignment_url)
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        collected: List[Attachment] = []
        for att in raw.attachments:
            try:
                att.local_path = self.adapter.download(att, dest_dir)
            except Exception:
                # 첨부 하나 실패(권한/만료/네트워크)가 전체 수집을 막지 않게 한다.
                att.local_path = None
            collected.append(att)
        return CollectedAssignment(
            title=raw.title, course=raw.course, description=raw.description,
            url=raw.url or self.assignment_url, attachments=collected,
        )


# ────────────────────────────────────────────────────────────────────
# 오프라인 어댑터 — 로그인/네트워크 없이 end-to-end 흐름 검증용.
# ────────────────────────────────────────────────────────────────────
class FixtureBrowserAdapter:
    """
    examples/etl_fixture/ 의 파일들을 '가짜 eTL 과제 페이지'처럼 제공한다.
    실제 eTL 페이지 구조를 흉내내어, 라이브 어댑터와 동일한 인터페이스로 동작.
    """
    def __init__(self, fixture_dir: str):
        self.fixture_dir = Path(fixture_dir)

    def fetch_assignment(self, url: str) -> RawAssignment:
        atts = [
            Attachment(name=p.name, url=f"fixture://{p.name}")
            for p in sorted(self.fixture_dir.glob("*"))
            if p.is_file() and p.name != "_description.md"
        ]
        desc_file = self.fixture_dir / "_description.md"
        description = desc_file.read_text(encoding="utf-8") if desc_file.exists() else \
            "이 과제는 첨부 자료를 바탕으로 분석 보고서를 작성하는 것이다."
        return RawAssignment(
            title="중간 보고서 (Fixture)",
            course="미디어와 사회 (Fixture)",
            description=description,
            attachments=atts,
            url=url,
        )

    def download(self, attachment: Attachment, dest_dir: str) -> str:
        src = self.fixture_dir / attachment.name
        dst = Path(dest_dir) / attachment.name
        shutil.copyfile(src, dst)
        return str(dst)


# ────────────────────────────────────────────────────────────────────
# 제품용 라이브 어댑터는 playwright_adapter.PlaywrightBrowserAdapter 참고.
# 아래 ChromeBrowserAdapter는 *개발/에이전트 전용* 메모 — Claude in Chrome은
# 에이전트(나)의 능력이지 제품이 import할 라이브러리가 아니므로, 제품에는 쓰지 않는다.
# (출시 대체재 비교: docs/ETL_CONNECTOR.md)
# ────────────────────────────────────────────────────────────────────
class ChromeBrowserAdapter:
    """개발/데모 전용 표식. 제품에선 PlaywrightBrowserAdapter(또는 브라우저 확장)를 쓴다."""
    def fetch_assignment(self, url: str) -> RawAssignment:
        raise NotImplementedError(
            "Claude in Chrome은 에이전트 전용(제품 코드 아님). "
            "제품 라이브는 PlaywrightBrowserAdapter를 사용하세요. (docs/ETL_CONNECTOR.md)"
        )

    def download(self, attachment: Attachment, dest_dir: str) -> str:
        raise NotImplementedError("제품 라이브는 PlaywrightBrowserAdapter 사용")
