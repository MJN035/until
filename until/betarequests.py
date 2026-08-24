"""베타 초대 요청 — **코어 스텁**. 초대 접수는 운영 계층의 일이다.

`save()`가 False를 돌려주면 폼이 "지금은 접수할 수 없다"는 경로를 타고,
`render_form()`은 이 배포에 접수 기능이 없다는 사실을 그대로 밝힌다.
받은 척하고 버리는 것이 가장 나쁘다 — 사용자는 답을 기다리게 된다.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

MAX_PER_DAY = 200


def normalize(form: Dict[str, str]) -> tuple[Optional[dict], str]:
    return (None, "이 배포에는 베타 초대 접수 기능이 없습니다.")


def save(record: dict) -> bool:
    return False


def today_count(*, now: datetime | None = None) -> int:
    return 0


def load_all(*, use_kv: bool = False, limit: int = 500) -> List[dict]:
    return []


def render_form(*, error: str = "", values: Optional[Dict[str, str]] = None) -> str:
    return ('<div class="sec"><h2>베타 초대 요청</h2>'
            '<p class="meta">이 배포에는 초대 접수 기능이 포함되어 있지 않습니다. '
            '직접 실행해 바로 쓰실 수 있어요 — <code>python demo.py</code></p>'
            '<p><a class="btn ghost back" href="/">← 돌아가기</a></p></div>')


def render_thanks() -> str:
    return render_form()


def render_admin_section(records: List[dict]) -> str:
    return ""
