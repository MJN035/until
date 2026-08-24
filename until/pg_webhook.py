"""PG 웹훅(결제 정산) — **코어 스텁**. 자체 호스팅에는 결제가 없다.

진짜 구현은 결제사 웹훅을 검증하고 환불을 크레딧 원장에 반영한다.
코어에는 결제 자체가 없으므로 '정산할 것이 없음'(0, 0)을 돌려준다.
"""
from __future__ import annotations

from pathlib import Path


def settle_registered_refund(root: Path, event_id: str, order_id: str, uid: str,
                             amount: int, revoke_credits,
                             *, now: float | None = None) -> tuple[int, int]:
    return (0, 0)
