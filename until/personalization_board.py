"""개인화 패널(관리자) — **코어 스텁**. 운영자용 화면이라 코어에 없다.

'Until이 이 사용자에 대해 무엇을 아는가'를 파생 지표로 보여 주는 운영 도구다.
코어에는 관리자 화면 자체가 없으므로 빈 목록·빈 HTML을 돌려준다.
"""
from __future__ import annotations

from pathlib import Path
from typing import List


def collect_rows(users_root: Path, limit: int = 200) -> List:
    return []


def render_html(rows: List, *, me: str = "") -> str:
    return ""
