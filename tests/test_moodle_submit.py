"""eTL(Moodle) 제출 — 4겹 게이트 · 두 걸음 · HTTP 200으로 오는 실패.

이 시험이 지키는 계약:
  1. 무장·게이트·nonce·호스트 중 하나라도 없으면 **네트워크로 아무것도 안 나간다**.
  2. 제출은 두 걸음이다 — 저장(되돌릴 수 있음) → 채점 확정.
  3. Moodle은 실패도 HTTP 200으로 준다. 상태 코드만 보면 **내지도 않고 냈다고**
     말하게 된다 — 이 제품에서 가장 나쁜 거짓말이라 본문을 반드시 본다.
"""
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from until.capture.sources import moodle_submit
from until.execution.submission_gate import SubmissionPlan, SubmitTarget
from until.execution.submit_nonce import issue_nonce


def _plan(allowed=True, base="https://myetl.snu.ac.kr", chash="H" * 12):
    return SubmissionPlan(
        allowed=allowed, blocks=[], warnings=[], content="완성된 본문입니다.",
        target=SubmitTarget(course_id="101", assignment_id="202",
                            submission_type="online_text_entry", base_url=base),
        content_hash=chash)


class _Http:
    """호출을 기록하는 가짜 전송. `bodies`로 걸음별 응답을 지정한다."""

    def __init__(self, bodies=None, status=200):
        self.calls, self.bodies, self.status = [], list(bodies or []), status

    def __call__(self, method, url, data, headers):
        self.calls.append((method, url, data.decode("utf-8")))
        body = self.bodies.pop(0) if self.bodies else "[]"
        return self.status, body


def _armed(tmp, http, **kw):
    nonce_path = pathlib.Path(tmp) / "nonce.jsonl"
    audit_path = pathlib.Path(tmp) / "audit.jsonl"
    plan = kw.pop("plan", None) or _plan()
    nonce = issue_nonce(plan.content_hash, path=nonce_path, binding="b")
    return moodle_submit.submit(
        plan, nonce, armed=True, token="tok", http=http,
        nonce_path=nonce_path, audit_path=audit_path, binding="b", **kw), audit_path


def test_request_shape_is_moodle_web_service():
    """Canvas REST가 아니라 Moodle 웹서비스로 나간다 — 그게 실제 eTL이다."""
    req = moodle_submit.build_request(_plan())
    assert req["url"] == "https://myetl.snu.ac.kr/webservice/rest/server.php"
    assert req["form"]["wsfunction"] == moodle_submit.SAVE
    assert req["form"]["assignmentid"] == "202"
    assert req["form"]["plugindata[onlinetext_editor][text]"] == "완성된 본문입니다."
    fin = moodle_submit.build_request(_plan(), wsfunction=moodle_submit.SUBMIT)
    assert fin["form"]["wsfunction"] == moodle_submit.SUBMIT
    assert fin["form"]["acceptsubmissionstatement"] == "1"
    # 허용 밖 쓰기 함수는 여기서 막힌다(퀴즈·포럼 등).
    for bad in ("mod_quiz_process_attempt", "mod_forum_add_discussion"):
        try:
            moodle_submit.build_request(_plan(), wsfunction=bad)
        except moodle_submit.SubmitFunctionBlocked:
            continue
        raise AssertionError(f"{bad}가 통과했다")
    print("OK 요청 모양 — Moodle WS · 허용 함수만")


def test_four_gates_block_every_way():
    """무장·게이트·토큰·호스트 — 하나라도 없으면 전송 0."""
    with tempfile.TemporaryDirectory() as tmp:
        nonce_path = pathlib.Path(tmp) / "n.jsonl"
        audit = pathlib.Path(tmp) / "a.jsonl"
        plan = _plan()
        cases = {
            "무장 없음": dict(armed=False, token="tok", plan=plan),
            "게이트 차단": dict(armed=True, token="tok", plan=_plan(allowed=False)),
            "토큰 없음": dict(armed=True, token="", plan=plan),
            "낯선 호스트": dict(armed=True, token="tok",
                            plan=_plan(base="https://evil.example")),
        }
        for name, kw in cases.items():
            http = _Http()
            p = kw.pop("plan")
            nonce = issue_nonce(p.content_hash, path=nonce_path, binding="b")
            receipt = moodle_submit.submit(p, nonce, http=http, nonce_path=nonce_path,
                                           audit_path=audit, binding="b", **kw)
            assert receipt.dry_run and not receipt.sent, name
            assert http.calls == [], f"{name}: 네트워크로 나갔다"
        # 유효하지 않은 nonce도 마찬가지.
        http = _Http()
        receipt = moodle_submit.submit(plan, "없는-nonce", armed=True, token="tok",
                                       http=http, nonce_path=nonce_path,
                                       audit_path=audit, binding="b")
        assert receipt.dry_run and http.calls == []
    print("OK 4겹 — 하나라도 없으면 전송 0")


def test_two_steps_on_success():
    with tempfile.TemporaryDirectory() as tmp:
        http = _Http(bodies=["[]", "true"])
        receipt, audit = _armed(tmp, http)
        assert receipt.sent and not receipt.dry_run
        fns = [c[2] for c in http.calls]
        assert moodle_submit.SAVE in fns[0] and moodle_submit.SUBMIT in fns[1]
        assert "wstoken=tok" in fns[0], "토큰은 바디로 — URL에 남으면 로그에 샌다"
        assert len(audit.read_text(encoding="utf-8").splitlines()) == 2
    print("OK 성공 — 저장 → 확정 두 걸음")


def test_stops_at_save_when_not_finalizing():
    with tempfile.TemporaryDirectory() as tmp:
        http = _Http(bodies=["[]"])
        receipt, audit = _armed(tmp, http, finalize=False)
        assert receipt.sent and len(http.calls) == 1
        assert len(audit.read_text(encoding="utf-8").splitlines()) == 1
        assert "확정은 아직" in receipt.detail
    print("OK finalize=False면 저장까지만")


def test_ws_failure_arrives_as_http_200():
    """상태 코드만 보면 내지도 않고 냈다고 말하게 된다."""
    with tempfile.TemporaryDirectory() as tmp:
        http = _Http(bodies=['{"exception":"moodle_exception",'
                             '"message":"토큰이 만료되었습니다"}'])
        receipt, _ = _armed(tmp, http)
        assert not receipt.sent and not receipt.dry_run
        assert "토큰이 만료" in receipt.detail
        assert len(http.calls) == 1, "저장이 실패했는데 확정으로 넘어갔다"

    # 저장은 됐는데 확정만 거부된 경우 — 그 사실을 정확히 말해야 한다.
    with tempfile.TemporaryDirectory() as tmp:
        http = _Http(bodies=["[]", '{"errorcode":"submissionnotopen"}'])
        receipt, _ = _armed(tmp, http)
        assert not receipt.sent
        assert "저장은 됐지만" in receipt.detail and "submissionnotopen" in receipt.detail

    # 경고 목록(warnings) 형태의 실패도 잡는다.
    with tempfile.TemporaryDirectory() as tmp:
        http = _Http(bodies=['[{"item":"onlinetext","message":"내용이 비었습니다"}]'])
        receipt, _ = _armed(tmp, http)
        assert not receipt.sent and "비었습니다" in receipt.detail
    print("OK HTTP 200으로 오는 실패를 성공으로 읽지 않는다")


def test_read_client_still_cannot_write():
    """읽기 어댑터의 쓰기 차단은 그대로다 — 제출은 이 모듈로만 나간다."""
    from until.capture.sources.moodle_ws import WRITE_DENYLIST, assert_read_only
    for fn in (moodle_submit.SAVE, moodle_submit.SUBMIT):
        assert fn in WRITE_DENYLIST, fn
        try:
            assert_read_only(fn)
        except Exception:
            continue
        raise AssertionError(f"읽기 클라이언트가 {fn}을 통과시켰다")
    print("OK 읽기 클라이언트는 여전히 쓰기 불가")


def test_submit_target_uses_instance_id_not_cmid():
    """URL의 `id=`는 course module id다. 제출은 assign 인스턴스 id를 받는다.

    둘을 혼동하면 **엉뚱한 과제에 제출된다**. 그래서 URL에서 뽑아 쓰지 않고 WS가 준
    과제 레코드의 id만 쓰고, 못 찾으면 빈 값으로 둬서 게이트가 막게 한다.
    """
    from until import web
    from until.capture.sources.moodle_ws import MoodleWsAdapter

    class _Res:
        def __init__(self):
            self.spec = {}

    url = "https://myetl.snu.ac.kr/mod/assign/view.php?id=555&courseid=101"

    adapter = MoodleWsAdapter.__new__(MoodleWsAdapter)
    adapter._assign_by_url = {url: {"id": "777", "cmid": "555"}}
    adapter._assign_by_cmid = {"555": {"id": "777", "cmid": "555"}}
    adapter._course_of = {url: "101"}
    res = _Res()
    web._set_submit_target(res, adapter, url)
    assert res.spec["assignment_id"] == "777", "cmid(555)를 제출 대상으로 썼다"
    assert res.spec["course_id"] == "101"

    # 확정 못 하면 비워 둔다 — 추측한 번호로 채우면 남의 과제에 낸다.
    empty = MoodleWsAdapter.__new__(MoodleWsAdapter)
    empty._assign_by_url, empty._assign_by_cmid, empty._course_of = {}, {}, {}
    empty._cache_course_assignments = lambda cid: (_ for _ in ()).throw(RuntimeError("네트워크 없음"))
    res2 = _Res()
    web._set_submit_target(res2, empty, url)
    assert "assignment_id" not in res2.spec and "course_id" not in res2.spec
    print("OK 제출 대상 — 인스턴스 id만 · 못 찾으면 빈 값")


def test_gate_blocks_when_target_unknown():
    """대상을 확정 못 하면 제출 게이트가 막는 것이 정상 동작이다."""
    from until.execution.submission_gate import build_submission_plan
    from until.capture.sources.models import AssignmentRef

    class _D:
        body, n_decisions, decisions = "완성된 본문입니다.", 0, []

    class _G:
        passed = True

    class _R:
        draft = final_draft = _D()
        guard = final_guard = _G()
        spec = {}
        source_docs = []
        deadline = None
        needs_approval = False

    plan = build_submission_plan(_R(), AssignmentRef(id="", title="t", course_id=""),
                                 base_url="https://myetl.snu.ac.kr", issue=False)
    assert not plan.allowed
    assert any(b.code == "assignment_mismatch" for b in plan.blocks), plan.blocks
    print("OK 대상 미확정이면 게이트가 막는다")


if __name__ == "__main__":
    test_request_shape_is_moodle_web_service()
    test_four_gates_block_every_way()
    test_two_steps_on_success()
    test_stops_at_save_when_not_finalizing()
    test_ws_failure_arrives_as_http_200()
    test_read_client_still_cannot_write()
    test_submit_target_uses_instance_id_not_cmid()
    test_gate_blocks_when_target_unknown()
    print("\nMOODLE SUBMIT TESTS PASS")
