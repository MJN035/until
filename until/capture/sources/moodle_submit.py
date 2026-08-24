"""eTL(Moodle) 제출 — 격리된 유일한 쓰기 경로. 기본 dry-run, 실 전송은 4겹 통과 시만.

읽기 어댑터(`moodle_ws.py`)는 `assert_read_only`로 쓰기 함수를 통째로 막는다. 그
원칙은 그대로 두고, 제출만 **이 모듈을 통해서만** 나가게 한다 — 읽기 클라이언트의
`call()`은 여전히 쓰기를 못 한다.

왜 따로 만들었나: 지금까지 제출 실행 코드는 `canvas_submit.py`뿐이었는데 그건
**Canvas REST API** 기준이다. 현재 eTL은 Moodle 웹서비스 기반이라 그 경로로는
아무것도 나가지 않았다 — "제출까지 된다"고 적혀 있었지만 실제로는 되지 않았다
(사용자 보고 2026-08-23).

Moodle 제출은 두 걸음이다.
  1. `mod_assign_save_submission` — 내용을 **초안으로** 서버에 올린다. 되돌릴 수 있다.
  2. `mod_assign_submit_for_grading` — 채점 대기로 **확정**한다. 과목 설정에 따라
     되돌리기가 막혀 있을 수 있어, 이 걸음은 사람이 확인 화면에서 누른 그 클릭으로만
     실행된다.

4겹은 `canvas_submit`과 같다: armed 인자 · plan.allowed · 유효 nonce · 신뢰 호스트.
하나라도 없으면 네트워크로 아무것도 나가지 않고 dry-run 영수증을 돌려준다.
"""
from __future__ import annotations

import datetime
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from ... import atomicio
from ...execution.submission_gate import SubmissionPlan
from ...execution.submit_nonce import consume_nonce
from .canvas_submit import SubmissionReceipt
from .moodle_ws import ws_endpoint

_AUDIT = Path("_until_work") / "submit_audit.jsonl"

# 이 모듈만 부를 수 있는 쓰기 함수. `moodle_ws.WRITE_DENYLIST`는 읽기 클라이언트용
# 방어라 그대로 둔다 — 거기서 빼면 아무 읽기 경로나 쓰기를 할 수 있게 된다.
SAVE = "mod_assign_save_submission"
SUBMIT = "mod_assign_submit_for_grading"
_WRITE_ALLOWED = frozenset({SAVE, SUBMIT})


class SubmitFunctionBlocked(RuntimeError):
    """제출용으로 허용되지 않은 쓰기 함수 호출 시도."""


@dataclass(frozen=True)
class MoodleSubmitSteps:
    """두 걸음 각각의 결과 — 화면이 '어디까지 갔는지'를 정확히 말할 수 있어야 한다."""
    saved: bool = False
    submitted: bool = False
    detail: str = ""


def _trusted_target(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and parts.hostname == "myetl.snu.ac.kr"
                and parts.port in (None, 443))
    except ValueError:
        return False


def _assignment_cmid(plan: SubmissionPlan) -> str:
    """제출 대상 식별자 — Moodle은 assignment **인스턴스 id**로 제출을 받는다."""
    return str(getattr(plan.target, "assignment_id", "") or "").strip()


def build_request(plan: SubmissionPlan, *, wsfunction: str = SAVE) -> dict:
    """전송될 요청을 계산한다 — 순수 함수(네트워크·nonce·감사 없음).

    `preview_request`와 같은 성질이라 화면 렌더에서 몇 번 불러도 부작용이 없다.
    """
    if wsfunction not in _WRITE_ALLOWED:
        raise SubmitFunctionBlocked(f"제출 경로에서 허용되지 않는 함수: {wsfunction}")
    base = str(getattr(plan.target, "base_url", "") or "")
    form = {"wsfunction": wsfunction, "assignmentid": _assignment_cmid(plan)}
    if wsfunction == SAVE:
        # onlinetext 제출 — Moodle은 플러그인별 중첩 키를 쓴다.
        form["plugindata[onlinetext_editor][text]"] = plan.content
        form["plugindata[onlinetext_editor][format]"] = "1"      # HTML
        form["plugindata[onlinetext_editor][itemid]"] = "0"
    else:
        form["acceptsubmissionstatement"] = "1"
    return {"method": "POST", "url": ws_endpoint(base), "form": form}


def preview_request(plan: SubmissionPlan) -> dict:
    """확인 화면용 — 저장 걸음의 요청만 보여 준다(제출 걸음은 같은 대상·같은 호스트)."""
    return build_request(plan, wsfunction=SAVE)


def _audit(path, row: dict) -> None:
    p = Path(path) if path is not None else _AUDIT
    p.parent.mkdir(parents=True, exist_ok=True)
    with atomicio.path_lock(p):
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _post(url: str, form: dict, token: str, http, timeout: float = 30.0):
    fields = [("wstoken", token), ("moodlewsrestformat", "json")]
    fields += [(k, v) for k, v in form.items()]
    data = urllib.parse.urlencode(fields).encode("utf-8")
    headers = {"Content-Type": "application/x-www-form-urlencoded",
               "Accept": "application/json"}
    if http is not None:
        return http("POST", url, data, headers)
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:   # pragma: no cover
        return r.status, r.read().decode("utf-8", "replace")


def _ws_failed(body: str) -> str:
    """Moodle은 실패도 HTTP 200으로 준다 — 본문의 exception을 봐야 한다.

    상태 코드만 보고 성공으로 처리하면 **내지도 않고 냈다고 말하게 된다**. 이 제품에서
    그건 가장 나쁜 거짓말이다(마감이 지나서야 학생이 안다).
    """
    try:
        data = json.loads(body or "")
    except (json.JSONDecodeError, TypeError):
        return "eTL가 JSON이 아닌 응답을 보냈습니다(로그인 만료 가능)"
    if isinstance(data, dict) and (data.get("exception") or data.get("errorcode")):
        return str(data.get("message") or data.get("errorcode") or "eTL가 거부했습니다")
    # save_submission은 성공 시 [] 또는 경고 목록을 돌려준다.
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("item") and item.get("message"):
                return str(item["message"])
    return ""


def submit(plan: SubmissionPlan, confirm_token: str, *, armed: bool = False,
           token: Optional[str] = None, http=None, audit_path=None,
           nonce_path=None, binding: str = "",
           finalize: bool = True) -> SubmissionReceipt:
    """4겹(armed·plan.allowed·유효 nonce·신뢰 호스트) 통과 시만 실제 전송.

    `finalize=False`면 저장(1걸음)까지만 하고 채점 확정은 하지 않는다 — 되돌릴 수
    있는 상태로 eTL에 올려 두고 마지막 버튼만 사람에게 남기고 싶을 때 쓴다.
    """
    save_req = build_request(plan, wsfunction=SAVE)
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    base = {"at": ts, "source": "moodle",
            "course_id": plan.target.course_id,
            "assignment_id": plan.target.assignment_id,
            "content_hash": plan.content_hash[:12], "allowed": plan.allowed}

    access = (token or "").strip()
    target_ok = _trusted_target(save_req["url"])
    nonce_ok = armed and plan.allowed and bool(access) and target_ok and consume_nonce(
        confirm_token, plan.content_hash, path=nonce_path, binding=binding)
    if not (armed and plan.allowed and access and target_ok and nonce_ok):
        _audit(audit_path, {**base, "mode": "dry", "sent": False})
        return SubmissionReceipt(False, True, save_req, None,
                                 "dry-run(무장·게이트·nonce 중 하나 미충족)")

    # 1걸음 — 내용 저장(되돌릴 수 있는 상태).
    try:
        status, body = _post(save_req["url"], save_req["form"], access, http)
    except Exception:
        _audit(audit_path, {**base, "mode": "live", "step": "save", "sent": False,
                            "error": "transport_error"})
        return SubmissionReceipt(False, False, save_req, None, "전송 실패")
    problem = _ws_failed(body) if 200 <= int(status) < 300 else "eTL가 저장을 거부했습니다"
    if problem:
        _audit(audit_path, {**base, "mode": "live", "step": "save", "sent": False,
                            "status": status})
        return SubmissionReceipt(False, False, save_req, int(status), problem)
    # 저장도 eTL에 쓰는 행위다 — 확정까지 갈 때도 원장에 남긴다. 두 걸음 중 어디서
    # 멈췄는지 사후에 못 가리면 원장이 있으나 마나다.
    _audit(audit_path, {**base, "mode": "live", "step": "save", "sent": True,
                        "status": status})
    if not finalize:
        return SubmissionReceipt(True, False, save_req, int(status),
                                 "eTL에 초안으로 올렸어요 — 확정은 아직입니다")

    # 2걸음 — 채점 대기로 확정. 과목 설정에 따라 되돌리기가 막힐 수 있다.
    fin_req = build_request(plan, wsfunction=SUBMIT)
    try:
        status2, body2 = _post(fin_req["url"], fin_req["form"], access, http)
    except Exception:
        _audit(audit_path, {**base, "mode": "live", "step": "submit", "sent": False,
                            "error": "transport_error"})
        return SubmissionReceipt(False, False, fin_req, None,
                                 "저장은 됐지만 확정 전송이 실패했어요 — eTL에서 '제출' 버튼을 눌러 주세요")
    problem2 = _ws_failed(body2) if 200 <= int(status2) < 300 else "eTL가 확정을 거부했습니다"
    if problem2:
        _audit(audit_path, {**base, "mode": "live", "step": "submit", "sent": False,
                            "status": status2})
        return SubmissionReceipt(False, False, fin_req, int(status2),
                                 f"저장은 됐지만 확정하지 못했어요 — {problem2}")
    _audit(audit_path, {**base, "mode": "live", "step": "submit", "sent": True,
                        "status": status2})
    return SubmissionReceipt(True, False, fin_req, int(status2), "제출을 확정했어요")
