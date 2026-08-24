"""관리자 보드 — **코어 스텁**. 운영자용 화면·집계라 코어에 없다.

진짜 구현은 사용자별 이벤트를 적립하고 운영 지표를 표로 그린다. 코어에는 관리자
개념이 없으므로 **적립은 조용히 버리고**(`record_event`), 조회는 빈 목록을 준다.

`verify_admin_token()`이 항상 False인 것이 중요하다 — `/admin` 라우트는 이 반환값을
게이트로 쓰므로, 스텁 상태에서는 어떤 토큰으로도 관리자 화면에 들어갈 수 없다.
**보안을 여는 방향으로 스텁을 만들지 않는다**(fail-closed).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

ADMIN_COOKIE = "until_admin"
ADMIN_TOKEN_TTL = 12 * 60 * 60


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def issue_admin_token(key: str, *, now: int | None = None) -> str:
    return ""


def verify_admin_token(token: str, key: str, *, now: int | None = None) -> bool:
    return False            # fail-closed — 코어에는 관리자 화면이 없다


def render_admin_login() -> str:
    return ('<div class="sec"><h2>관리자</h2>'
            '<p class="meta">이 배포에는 관리자 보드가 포함되어 있지 않습니다.</p>'
            '<p><a class="btn ghost back" href="/">← 돌아가기</a></p></div>')


def render_admin_html(records: List[dict], *, include_internal: bool = False,
                      telemetry_records: List[dict] | None = None,
                      now: datetime | None = None, me: str = "") -> str:
    return render_admin_login()


def inbox_failure_event(exc: BaseException) -> str:
    return "inbox_fail"


def record_event(root: Path, uid: str, event: str, *, token: str = "",
                 profile: Optional[Dict[str, str]] = None) -> None:
    return None             # 적립 대상이 없다


def parse_record(raw: bytes | str) -> Optional[dict]:
    return None


def load_telemetry(path: Path | None = None) -> List[dict]:
    return []


def load_web_telemetry(users_dir: Path, use_kv: bool = False) -> List[dict]:
    return []


def load_all(users_dir: Path) -> List[dict]:
    return []


def merge_records(*groups: List[dict]) -> List[dict]:
    return []
