"""웹에서 러너를 부르는 쪽 — **실패해도 초안·제출은 그대로 간다.**

웹 앱이 러너에 대해 아는 것은 이 파일뿐이다. 여기서 지키는 것:

- **기본은 꺼짐.** `UNTIL_RUNNER_URL`이 없으면 아무 일도 하지 않고 `None`을
  돌려준다 — 코드 경로가 러너 도입 전과 완전히 동일해진다.
- **모든 실패는 비치명.** 러너가 죽었든 느리든 서명이 틀렸든, 사용자는 초안을
  받고 제출할 수 있어야 한다. 점검 항목 하나가 '실행 못 함'으로 뜰 뿐이다.
- **짧은 타임아웃.** 웹 요청이 러너를 기다리다 같이 죽으면 안 된다.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from . import protocol

#: 웹이 러너를 기다리는 한도. 러너 자신의 실행 상한(job timeout)보다 여유를 조금 둔다.
DEFAULT_HTTP_TIMEOUT = 20


def runner_url() -> str:
    return (os.getenv("UNTIL_RUNNER_URL") or "").strip().rstrip("/")


def configured() -> bool:
    """러너를 쓸 준비가 됐는가. 주소와 키가 **둘 다** 있어야 한다."""
    return bool(runner_url() and (os.getenv("UNTIL_RUNNER_KEY") or "").strip())


def run(job: str, files: dict, *, timeout_seconds: int = 60,
        http_timeout: int = DEFAULT_HTTP_TIMEOUT, opener=None) -> "dict | None":
    """러너에 실행을 맡기고 결과 dict를 돌려준다. 못 부르면 `None`.

    `None`은 "실행하지 못했다"이지 "실패했다"가 아니다 — 호출부는 둘을 구분해
    보여 줘야 한다(못 돌린 것을 실패로 적으면 멀쩡한 코드를 고치게 된다).
    """
    if not configured():
        return None
    body = protocol.RunRequest(job, files, timeout_seconds).to_json().encode("utf-8")
    if len(body) > protocol.MAX_TOTAL_BYTES:
        return {"status": "error", "detail": "보낼 파일이 너무 큽니다"}
    stamp = f"{time.time():.0f}"
    request = urllib.request.Request(
        f"{runner_url()}/run", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "X-Until-Timestamp": stamp,
                 "X-Until-Signature": protocol.sign(
                     body, (os.getenv("UNTIL_RUNNER_KEY") or "").strip(), stamp)})
    send = opener or urllib.request.urlopen
    try:
        with send(request, timeout=http_timeout) as response:
            return protocol.decode_result(response.read())
    except urllib.error.HTTPError as exc:
        try:
            return protocol.decode_result(exc.read())
        except Exception:
            return {"status": "error", "detail": f"러너 오류({exc.code})"}
    except (urllib.error.URLError, OSError, ValueError,
            protocol.ProtocolError) as exc:
        return {"status": "error", "detail": f"러너에 연결하지 못했습니다: {exc}"}


def health(*, http_timeout: int = 5, opener=None) -> dict:
    """러너 상태 — 운영자가 '왜 실행이 안 되지'를 바로 볼 수 있게."""
    if not runner_url():
        return {"status": "off", "detail": "UNTIL_RUNNER_URL 미설정"}
    send = opener or urllib.request.urlopen
    try:
        with send(f"{runner_url()}/healthz", timeout=http_timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}
