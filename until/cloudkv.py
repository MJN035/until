"""클라우드 KV 미러 — **코어 스텁**. 자체 호스팅에는 미러가 없다.

**왜 이 스텁이 안전한가.** 진짜 구현의 `kv()`도 자격증명이 없으면 `None`을 돌려주고,
코어의 호출 지점 50곳이 전부 `if c is None:` 분기를 이미 갖고 있다. 즉 '미러 없음'
경로는 새로 만든 게 아니라 **로컬 실행에서 매일 타던 경로**다.

미러가 필요한 이유는 무료 티어 재시작 시 디스크가 휘발되기 때문인데,
자체 호스팅에서는 디스크가 곧 원본이라 미러 자체가 불필요하다.
"""
from __future__ import annotations

from typing import Optional

TTL_SESS = 60 * 24 * 3600      # 세션 60일
TTL_HIST = 365 * 24 * 3600     # 히스토리 1년
TTL_USAGE = 3 * 24 * 3600      # 일일 사용량 3일
TTL_TELEM = 180 * 24 * 3600    # 텔레메트리 180일


class CloudKV:  # pragma: no cover - 스텁에서는 생성되지 않는다
    """인터페이스 자리표시자. `kv()`가 항상 None이라 실제로 만들어지지 않는다."""


def kv() -> Optional[CloudKV]:
    return None


def reset_for_tests() -> None:
    return None


def put_async(key: str, data: bytes, ttl: Optional[int] = None) -> None:
    return None


def delete_async(key: str) -> None:
    return None
