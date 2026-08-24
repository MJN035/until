"""Until Runner 프로토콜 — 요청/응답 스키마와 서명 (순수 함수, 네트워크 0).

이 파일이 지키는 것은 하나다: **요청자가 실행할 명령을 정하지 못하게 한다.**
요청에는 "무엇을 실행하라"가 없다. 파일과 `job`(무슨 종류의 검사인지)만 있고,
argv는 러너가 자기 목록에서 고른다. argv를 실어 보내는 API는 원격 셸이다.

크기·개수 상한도 여기서 건다. 러너 쪽에서 파싱하기 **전에** 걸러야
"10GB 본문으로 메모리 터뜨리기"가 안 된다.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field

#: 한 요청에 담을 수 있는 파일 수·크기. 과제 하나를 돌리는 데 이보다 필요할 이유가 없다.
MAX_FILES = 20
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
#: 서명 시각이 이보다 오래됐으면 거절 — 가로챈 요청을 무한히 재사용하지 못하게.
MAX_CLOCK_SKEW_SECONDS = 300

#: 러너가 아는 작업 종류. 요청은 이 중 하나를 **고를 뿐** 명령을 정하지 못한다.
JOB_KINDS = ("python_unittest", "python_pytest", "python_syntax")

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._-]*$")


class ProtocolError(ValueError):
    """요청이 계약을 어겼다 — 러너는 이 경우 본문을 실행하지 않는다."""


@dataclass(frozen=True)
class RunRequest:
    job: str
    files: dict = field(default_factory=dict)   # {상대경로: 텍스트}
    timeout_seconds: int = 60

    def to_json(self) -> str:
        return json.dumps({"job": self.job, "files": self.files,
                           "timeout_seconds": self.timeout_seconds},
                          ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_request(raw: bytes) -> RunRequest:
    """본문을 계약대로 검증해 요청으로 바꾼다. 어긋나면 `ProtocolError`."""
    if len(raw) > MAX_TOTAL_BYTES:
        raise ProtocolError("요청이 너무 큽니다")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"JSON을 읽지 못했습니다: {exc}") from None
    if not isinstance(data, dict):
        raise ProtocolError("요청은 JSON 오브젝트여야 합니다")

    job = str(data.get("job") or "")
    if job not in JOB_KINDS:
        raise ProtocolError(f"모르는 작업 종류입니다: {job or '(없음)'}")

    files = data.get("files")
    if not isinstance(files, dict) or not files:
        raise ProtocolError("files가 비어 있습니다")
    if len(files) > MAX_FILES:
        raise ProtocolError(f"파일이 너무 많습니다({len(files)} > {MAX_FILES})")

    clean: dict[str, str] = {}
    total = 0
    for name, body in files.items():
        safe = _safe_name(str(name))
        if not isinstance(body, str):
            raise ProtocolError(f"파일 내용은 문자열이어야 합니다: {safe}")
        size = len(body.encode("utf-8"))
        if size > MAX_FILE_BYTES:
            raise ProtocolError(f"파일이 너무 큽니다: {safe}")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ProtocolError("파일 총량이 너무 큽니다")
        clean[safe] = body

    timeout = data.get("timeout_seconds", 60)
    if not isinstance(timeout, int) or not 1 <= timeout <= 120:
        raise ProtocolError("timeout_seconds는 1~120 사이 정수여야 합니다")
    return RunRequest(job, clean, timeout)


def _safe_name(name: str) -> str:
    """작업공간 안의 평평한 파일 이름만 허용한다.

    디렉터리도 상위 이동도 받지 않는다 — 러너가 푸는 위치를 요청이 고르게 하면
    경로 탈출이 열린다. 과제 하나를 돌리는 데 하위 디렉터리는 필요 없다.
    """
    if "/" in name or "\\" in name or not _SAFE_NAME_RE.match(name):
        raise ProtocolError(f"허용되지 않는 파일 이름입니다: {name!r}")
    if not name.endswith((".py", ".txt", ".md", ".json", ".csv")):
        raise ProtocolError(f"허용되지 않는 확장자입니다: {name!r}")
    return name


# ── 서명 ────────────────────────────────────────────────────────────
def sign(body: bytes, key: str, timestamp: str) -> str:
    """`timestamp.body`에 대한 HMAC-SHA256 hex.

    시각을 서명에 포함해야 가로챈 요청을 나중에 그대로 재생할 수 없다.
    """
    message = timestamp.encode("utf-8") + b"." + body
    return hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify(body: bytes, key: str, timestamp: str, signature: str,
           *, now: float) -> None:
    """서명·시각을 확인한다. 어긋나면 `ProtocolError`(본문은 읽지 않는다)."""
    if not key:
        raise ProtocolError("러너 키가 설정돼 있지 않습니다")
    try:
        sent_at = float(timestamp)
    except (TypeError, ValueError):
        raise ProtocolError("서명 시각이 올바르지 않습니다") from None
    if abs(now - sent_at) > MAX_CLOCK_SKEW_SECONDS:
        raise ProtocolError("서명이 만료됐거나 시각이 어긋납니다")
    if not hmac.compare_digest(sign(body, key, timestamp), str(signature or "")):
        raise ProtocolError("서명이 올바르지 않습니다")


def encode_result(status: str, *, exit_code=None, stdout: str = "",
                  stderr: str = "", detail: str = "") -> bytes:
    return json.dumps({"status": status, "exit_code": exit_code,
                       "stdout": stdout, "stderr": stderr, "detail": detail},
                      ensure_ascii=False).encode("utf-8")


def decode_result(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"러너 응답을 읽지 못했습니다: {exc}") from None
    if not isinstance(data, dict) or "status" not in data:
        raise ProtocolError("러너 응답 형식이 올바르지 않습니다")
    return data
