"""공용 원자적 쓰기 + 파일 잠금 유틸 (로드맵 Tier3-11 — 다중 사용자 경합 방어).

배경: 여러 요청(스레드·프로세스)이 같은 JSON/JSONL 파일에 읽기-수정-쓰기(RMW)를
동시에 수행하면 나중에 쓴 쪽이 이긴다(lost update) — nonce 리플레이 방지, 크레딧
잔액, 이벤트 카운터 등에서 치명적이다(예: 리플레이 방지 nonce는 두 동시 요청이
둘 다 "미소비"를 보고 둘 다 성공 처리될 수 있음). 이 모듈은 두 계층을 제공한다.

1. `atomic_write_bytes` / `atomic_write_json` — tmp 파일에 쓰고 `os.replace`로
   교체한다(중단 시에도 파일이 절반만 쓰인 상태로 남지 않는다). Windows는
   대상이 잠깐 다른 핸들에 열려 있으면 `replace`가 실패할 수 있어 재시도
   루프를 둔다(`billing._atomic_write_json`의 기존 구현을 일반화·이관).
   재시도로도 안 되면 **던진다** — 비원자적으로 덮어쓰지 않는다
   (`AtomicWriteError`). 쓰기 실패는 호출자가 다룰 수 있지만, 조용히 잘린
   파일은 읽은 쪽이 사고인 줄도 모른다.
2. `path_lock(path)` — 경로별 RMW를 직렬화하는 컨텍스트매니저. 이중 방어:
   - **프로세스 내부**: 경로별 `threading.Lock`(같은 프로세스의 여러 스레드/요청
     사이). 이건 항상 걸린다.
   - **프로세스 간**: `<path>.lock` 사이드카 파일에 대한 OS 파일락
     (Windows `msvcrt.locking` / POSIX `fcntl.flock`). uvicorn을 여러 워커
     **프로세스**로 띄우는 배포에서는 스레드락만으로 막을 수 없는 경합을 막는다.
   - OS 락 자체를 걸 수 없는 경우(임포트 실패, 락 파일 오픈 실패, 락 획득 예외
     등 플랫폼/환경 특이 사유)에는 **프로세스 내 threading.Lock만으로 폴백**한다
     (베스트에포트). 이 경우 **다른 프로세스**가 동시에 같은 파일을 건드리는
     경합까지는 막지 못한다 — uvicorn 멀티 워커 배포에서 이 폴백이 발동하면
     여전히 경합 가능성이 남는다는 뜻이다.

요청마다 여러 번 타는 핫패스(nonce 소비, 세션 저장, 프로필 병합, 이벤트 기록)이므로
락 범위는 최소로 유지한다 — 파일 I/O(읽기→수정→원자적 재작성)만 감싸고, 그 안에서
LLM 호출 등 무거운 작업을 하지 않는다.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Union

PathLike = Union[str, "os.PathLike[str]"]

try:
    if sys.platform == "win32":
        import msvcrt
    else:
        msvcrt = None  # type: ignore[assignment]
    if sys.platform != "win32":
        import fcntl
    else:
        fcntl = None  # type: ignore[assignment]
except ImportError:  # pragma: no cover - 플랫폼 특이 사유(예: 임베디드 파이썬)
    msvcrt = None  # type: ignore[assignment]
    fcntl = None  # type: ignore[assignment]


# ─────────────────────────────────────────────────────────────────────────────
# 원자적 쓰기 — tmp + os.replace(Windows 재시도 폴백)
# ─────────────────────────────────────────────────────────────────────────────
#: 무잠금 리더(플레인 read_text/read_bytes)가 os.replace 순간 대상 파일을 잠깐
#: 열고 있으면 Windows에서 replace가 EACCES/PermissionError로 실패할 수 있다
#: (Python의 기본 오픈이 FILE_SHARE_DELETE를 늘 보장하진 않음). 리더의 열기는
#: 보통 마이크로초 단위라 **짧은 간격 없이 빠르게 재시도**하면 거의 항상 그
#: 틈을 잡는다 — sleep 기반 재시도보다 훨씬 효과적임을 부하테스트로 확인.
_FAST_RETRIES = 500      # 슬립 없이 즉시 재시도(리더의 짧은 열림 구간을 잡음)
_SLOW_RETRIES = 100      # 그래도 안 되면 짧게 슬립하며 재시도(더 긴 경합 대비)
_SLOW_SLEEP_S = 0.02     # 합계 약 2초 — CPU가 포화돼도 대개 이 안에 잡힌다


class AtomicWriteError(OSError):
    """원자적 교체를 끝내 못 했다 — **대상 파일은 손대지 않았다.**

    예전에는 이 상황에서 `p.write_bytes(data)`로 폴백했다. 그건 truncate 후
    쓰기라서 그 찰나 다른 리더가 **길이 0**을 본다 — 이 모듈이 보장한다고
    적어 둔 "중간 상태 없음"이 바로 거기서 깨졌다(부하 테스트에서 `[0, 0, 0]`
    으로 재현). 조용히 계약을 깨는 것보다 실패를 알리는 편이 낫다: 실패는
    호출자가 재시도하거나 무시할 수 있지만, 반쯤 지워진 파일을 읽은 쪽은
    그게 사고인 줄도 모른다.
    """


def atomic_write_bytes(path: PathLike, data: bytes) -> None:
    """`path`에 바이트를 원자적으로 쓴다(tmp 파일 + `os.replace`).

    프로세스가 쓰는 도중 죽어도 대상 파일은 이전 내용 그대로거나(교체 전)
    새 내용 그대로다(교체 후) — 절반만 쓰인 손상 상태가 없다. Windows에서
    `os.replace`가 일시적으로 실패하면(대상이 잠깐 다른 핸들에 열려 있는 등)
    먼저 슬립 없이 빠르게, 그래도 안 되면 짧게 슬립하며 약 2초까지 재시도한다.

    끝내 못 하면 `AtomicWriteError`를 던진다 — **대상 파일은 손대지 않은 상태
    그대로다.** 비원자적으로 덮어쓰지 않는 이유는 그 클래스의 docstring에 있다.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    for _ in range(_FAST_RETRIES):
        try:
            os.replace(tmp, p)
            return
        except OSError:
            pass
    last: "OSError | None" = None
    for _ in range(_SLOW_RETRIES):
        try:
            os.replace(tmp, p)
            return
        except OSError as exc:
            last = exc
            time.sleep(_SLOW_SLEEP_S)
    try:
        tmp.unlink()
    except OSError:
        pass
    logging.getLogger(__name__).warning(
        "atomic replace failed after retries: %s (%s)", p, last)
    raise AtomicWriteError(
        f"원자적 교체를 완료하지 못했습니다(대상 파일은 그대로): {p}") from last


def atomic_write_json(path: PathLike, obj: Any, **json_kwargs: Any) -> None:
    """`atomic_write_bytes`로 JSON을 원자적으로 쓴다. 기본 `ensure_ascii=False`."""
    json_kwargs.setdefault("ensure_ascii", False)
    atomic_write_bytes(path, json.dumps(obj, **json_kwargs).encode("utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# path_lock — 프로세스 내 threading.Lock + 프로세스 간 OS 파일락(이중 방어)
# ─────────────────────────────────────────────────────────────────────────────
_REGISTRY_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.Lock] = {}


def _thread_lock_for(key: str) -> threading.Lock:
    with _REGISTRY_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[key] = lock
        return lock


def _os_lock_acquire(fd: int) -> bool:
    """사이드카 lock 파일 fd에 배타 락을 건다. 성공하면 True, 미지원/실패면 False."""
    try:
        if sys.platform == "win32":
            if msvcrt is None:
                return False
            # LK_LOCK: 즉시 못 잡으면 내부적으로 짧게 재시도하다 실패 시 예외.
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            if fcntl is None:
                return False
            fcntl.flock(fd, fcntl.LOCK_EX)
        return True
    except OSError:
        return False


def _os_lock_release(fd: int) -> None:
    try:
        if sys.platform == "win32" and msvcrt is not None:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
            except OSError:
                pass
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        elif fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def path_lock(path: PathLike) -> Iterator[None]:
    """`path`에 대한 읽기-수정-쓰기를 프로세스 내외로 직렬화하는 컨텍스트매니저.

    사용:
        with atomicio.path_lock(p):
            rows = _read(p)
            ...
            atomicio.atomic_write_bytes(p, new_bytes)

    프로세스 내부는 경로별 `threading.Lock`으로 항상 직렬화된다. 프로세스
    간(uvicorn 멀티 워커 등)은 `<path>.lock` 사이드카 파일의 OS 파일락으로
    막는다. OS 락 획득이 실패하면(플랫폼 미지원, 락 파일 오픈 실패 등)
    threading.Lock만으로 계속 진행한다 — **이 경우 다른 프로세스와의 경합은
    막지 못하는 베스트에포트 폴백**이다.
    """
    p = Path(path)
    key = str(p.resolve())
    lock = _thread_lock_for(key)
    with lock:
        lock_path = p.with_name(p.name + ".lock")
        fd = None
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
        except OSError:
            fd = None  # 락 파일조차 못 열면 프로세스 내 락만으로 폴백.
        os_locked = False
        try:
            if fd is not None:
                os_locked = _os_lock_acquire(fd)
            yield
        finally:
            if fd is not None:
                if os_locked:
                    _os_lock_release(fd)
                try:
                    os.close(fd)
                except OSError:
                    pass
