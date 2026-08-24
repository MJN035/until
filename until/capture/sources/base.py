"""
소스 커넥터 추상화.

핵심 분리: '브라우저에서 과제 페이지를 읽고 파일을 받는' 부분(BrowserAdapter)과,
'그 결과를 조립·저장하는' 부분(Source)을 나눈다. 덕분에:
  - 라이브: ChromeBrowserAdapter(=Claude in Chrome MCP)로 실제 eTL 접속
  - 오프라인: FixtureBrowserAdapter로 키·로그인 없이 end-to-end 테스트
어느 쪽이든 Source 조립 로직은 동일하게 재사용된다.
"""
from __future__ import annotations
from typing import Protocol

from .models import RawAssignment, Attachment, CollectedAssignment


class BrowserAdapter(Protocol):
    """과제 페이지 1개를 읽고, 첨부를 내려받는 능력. (로그인된 세션 전제)"""

    def fetch_assignment(self, url: str) -> RawAssignment: ...

    def download(self, attachment: Attachment, dest_dir: str) -> str:
        """첨부를 dest_dir로 저장하고 로컬 경로를 반환."""
        ...


class Source(Protocol):
    def collect(self, dest_dir: str) -> CollectedAssignment: ...
