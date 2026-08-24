"""크레딧·사용 한도 — **코어 스텁**. 자체 호스팅은 과금이 없다(무제한).

경계 규약: 코어(`web.py`·`asgi.py`)는 이 인터페이스만 알고, 실제 선불 크레딧·
PG 연동·전역 일일 상한은 운영 배포(비공개)가 같은 이름으로 교체한다.

반환값은 **'결제 계층이 없는 로컬 설치'의 정상 동작**과 일치시켰다 —
한도 없음(`can_draft()`/`global_can_draft()` = True), 잔액 개념 없음,
라이선스 활성화 없음. `remaining_credits()`가 None인 것은 진짜 구현에서도
'무제한'을 뜻하므로 UI가 남은 횟수를 아예 그리지 않는다.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

USAGE_PATH = Path("_until_work/usage.json")
CREDITS_PATH = Path("_until_work/credits.json")
LICENSE_PATH = Path("_until_work/license.txt")


def set_usage_path_override(p: Optional[Path]) -> None:
    return None


def set_credits_path_override(p: Optional[Path]) -> None:
    return None


def plan() -> str:
    return "pro"            # 자체 호스팅 = 한도 없음


def pay_url() -> str:
    return ""


def activate_license(key: str) -> bool:
    return False


def global_limit() -> int:
    return 0                # 0 = 상한 없음


def global_usage_today() -> int:
    return 0


def global_can_draft() -> bool:
    return True


def record_global_draft() -> None:
    return None


def starter_credits() -> int:
    return 0


def credit_cost() -> int:
    return 0


def credit_codes() -> dict:
    return {}


def balance() -> int:
    return 0


def remaining_credits() -> Optional[int]:
    return None             # None = 무제한


def can_draft() -> bool:
    return True


def charge(ref: str = "", n: Optional[int] = None) -> bool:
    return True


def record_draft() -> None:
    return None


def add_credits(n: int, *, code: Optional[str] = None, reason: str = "topup") -> int:
    return 0


def add_credits_checked(n: int, *, code: Optional[str] = None,
                        reason: str = "topup", event_id: str = "") -> tuple[int, bool]:
    """멱등 충전(웹훅 재전송 방어). 스텁은 항상 '이미 처리됨'으로 답한다."""
    return (0, False)


def revoke_credits(n: int, *, code: Optional[str] = None, reason: str = "revoke") -> int:
    return 0


def revoke_credits_checked(n: int, *, code: Optional[str] = None,
                           reason: str = "revoke", event_id: str = "") -> tuple[int, bool]:
    return (0, False)


def redeem(code: str) -> Tuple[bool, int, str]:
    return (False, 0, "이 배포에는 결제 계층이 없습니다.")


def ledger(limit: int = 20) -> List[dict]:
    return []
