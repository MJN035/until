"""Canvas 제출 — 격리된 쓰기 경로. 기본 dry-run, 실 POST는 4겹 방어 통과 시만.

읽기 어댑터(canvas_api.py)와 분리된 유일한 쓰기 지점. 자동 호출 경로 없음 —
확인 화면의 사람 클릭이 armed=True와 유효 nonce를 넘겨야만 네트워크로 나간다.
"""
from __future__ import annotations

import datetime
import json
import urllib.parse
import urllib.request
from urllib.parse import urlsplit
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ...execution.submission_gate import SubmissionPlan
from ...execution.submit_nonce import consume_nonce
from ... import atomicio

_AUDIT = Path("_until_work") / "submit_audit.jsonl"


@dataclass(frozen=True)
class SubmissionReceipt:
    sent: bool
    dry_run: bool
    request: dict
    status: Optional[int] = None
    detail: str = ""


def _build_request(plan: SubmissionPlan) -> dict:
    t = plan.target
    url = (f"{t.base_url}/api/v1/courses/{t.course_id}"
           f"/assignments/{t.assignment_id}/submissions")
    form = {
        "submission[submission_type]": t.submission_type,
        "submission[body]": plan.content,
    }
    return {"method": "POST", "url": url, "form": form}


def preview_request(plan: SubmissionPlan) -> dict:
    """제출 시 나갈 요청을 렌더용으로 계산 — 순수 함수(감사 로그·네트워크·nonce
    소비 없음). 웹 미리보기는 이 함수만 쓴다. submit()과 달리 원장에 아무것도
    쓰지 않으므로 페이지 렌더(새로고침 포함)로 호출해도 부작용이 없다."""
    return _build_request(plan)


def _audit(path, row: dict) -> None:
    p = Path(path) if path is not None else _AUDIT
    p.parent.mkdir(parents=True, exist_ok=True)
    with atomicio.path_lock(p):
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _trusted_target(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and parts.hostname == "myetl.snu.ac.kr"
                and parts.port in (None, 443))
    except ValueError:
        return False


def submit(plan: SubmissionPlan, confirm_token: str, *, armed: bool = False,
           token: Optional[str] = None, http=None, audit_path=None,
           nonce_path=None, binding: str = "") -> SubmissionReceipt:
    """4겹(armed·plan.allowed·유효 nonce·명시 armed 인자) 통과 시만 live POST."""
    req = _build_request(plan)
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    base = {"at": ts, "course_id": plan.target.course_id,
            "assignment_id": plan.target.assignment_id,
            "content_hash": plan.content_hash[:12], "allowed": plan.allowed}

    # 4겹 검사 — 하나라도 없으면 dry-run.
    access = (token or "").strip()
    target_ok = _trusted_target(req["url"])
    nonce_ok = armed and plan.allowed and bool(access) and target_ok and consume_nonce(
        confirm_token, plan.content_hash, path=nonce_path, binding=binding)
    if not (armed and plan.allowed and access and target_ok and nonce_ok):
        _audit(audit_path, {**base, "mode": "dry", "sent": False})
        return SubmissionReceipt(False, True, req, None,
                                 "dry-run(무장·게이트·nonce 중 하나 미충족)")

    # live POST — Canvas는 form-encoded.
    data = urllib.parse.urlencode(req["form"]).encode("utf-8")
    headers = {"Authorization": f"Bearer {access}",
               "Content-Type": "application/x-www-form-urlencoded"}
    try:
        if http is not None:
            status, body = http(req["method"], req["url"], data, headers)
        else:  # pragma: no cover — 실 네트워크, 테스트는 http 주입
            r = urllib.request.Request(req["url"], data=data, headers=headers,
                                       method="POST")
            with urllib.request.urlopen(r, timeout=30) as resp:
                status, body = resp.status, resp.read().decode("utf-8", "replace")
    except Exception:
        _audit(audit_path, {**base, "mode": "live", "sent": False,
                            "error": "transport_error"})
        return SubmissionReceipt(False, False, req, None, "전송 실패")
    sent = 200 <= int(status) < 300
    _audit(audit_path, {**base, "mode": "live", "sent": sent, "status": status})
    return SubmissionReceipt(sent, False, req, status,
                             body[:200] if sent else "Canvas가 제출을 거부했습니다")
