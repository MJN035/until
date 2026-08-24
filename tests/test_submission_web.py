"""제출 게이트 웹 배선 테스트 — 오프라인, FakeHTTP만 사용.

- 하드 블록이 있으면 '제출' 버튼이 아예 렌더되지 않는다.
- 허용 plan은 보낼 요청(method·url)을 미리보기로 노출하고 확인 POST만 연다.
- 미해결 결정 경고가 화면에 표시된다.
- 게이트 메시지·코드는 HTML 이스케이프된다(XSS 방지).
- 세션 spec에 assignment_id/course_id가 없으면 assignment_mismatch로 차단되는
  것이 정상 동작(오제출 방지) — 웹 헬퍼가 그 경로에서도 armed 경로를 열지 않는다.
"""
import sys
import pathlib
import os
import tempfile
import re
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.execution.submission_gate import SubmissionPlan, SubmitTarget, GateFinding
from until import web


def _target(course_id="101", assignment_id="202"):
    return SubmitTarget(course_id, assignment_id, "online_text_entry",
                        "https://myetl.snu.ac.kr")


def test_blocked_plan_hides_submit_button():
    plan = SubmissionPlan(
        allowed=False,
        blocks=[GateFinding("guard_failed", "경계선 가드 미통과 — 제출 불가")],
        warnings=[],
        content="본문",
        target=_target(),
        content_hash="H" * 12,
        confirm_nonce="",  # 차단 시 nonce 미발급(실제 게이트와 동일 전제)
    )
    out = web._submission_preview_from_plan(plan)
    assert "<button" not in out, "하드 블록이 있으면 제출 버튼이 아예 없어야 한다"
    assert "guard_failed" in out and "경계선 가드 미통과" in out
    assert "제출할 수 없습니다" in out
    print("OK 차단 plan은 제출 버튼 미표시")


def test_allowed_plan_shows_preview_request_with_active_button():
    plan = SubmissionPlan(
        allowed=True,
        blocks=[],
        warnings=[],
        content="완성된 최종 본문입니다.",
        target=_target(),
        content_hash="H" * 12,
        confirm_nonce="fixed-nonce",
    )
    out = web._submission_preview_from_plan(plan, "session-1")
    assert '<form method="post" action="/submit/prepare"' in out
    assert "disabled" not in out
    assert 'name="session" value="session-1"' in out
    assert "POST" in out
    # eTL은 Moodle이라 제출도 웹서비스로 나간다 — Canvas REST 경로로 쏘던 시절에는
    # 화면에 그 URL이 떴지만 실제로는 아무것도 나가지 않았다(2026-08-23).
    assert "https://myetl.snu.ac.kr/webservice/rest/server.php" in out
    assert "하드 블록 없음" in out
    print("OK 허용 plan은 확인 POST 버튼과 요청 미리보기를 노출")


def test_unresolved_decision_warning_is_shown_even_when_allowed():
    plan = SubmissionPlan(
        allowed=True,
        blocks=[],
        warnings=[GateFinding(
            "unresolved_decisions",
            "이 과제에는 당신의 판단(결정)이 필요했습니다 — 최종본에 제대로 반영됐는지 확인 후 제출하세요")],
        content="완성된 최종 본문입니다.",
        target=_target(),
        content_hash="H" * 12,
        confirm_nonce="fixed-nonce",
    )
    out = web._submission_preview_from_plan(plan)
    assert "unresolved_decisions" in out
    assert "판단(결정)이 필요했습니다" in out
    # 경고는 있어도 차단은 아니므로 버튼은 여전히 렌더된다.
    assert "<button" in out
    print("OK 미해결 결정 경고가 허용 상태에서도 표시된다")


def test_gate_messages_are_html_escaped():
    plan = SubmissionPlan(
        allowed=False,
        blocks=[GateFinding("raw_decision_marker", "<script>alert(1)</script>")],
        warnings=[],
        content="본문",
        target=_target(),
        content_hash="H" * 12,
        confirm_nonce="",
    )
    out = web._submission_preview_from_plan(plan)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    print("OK 게이트 메시지는 HTML 이스케이프된다")


def test_never_calls_submit_armed():
    """GET 미리보기 헬퍼는 submit()을 아예 호출하지 않는다(preview_request만 사용) —
    감사 로그·nonce 소비가 렌더로 일어나지 않는다는 것을 계측으로 보증.
    (armed=True가 새어 들어가지 않는다는 불변도 자연히 성립: 호출 자체가 없다.)"""
    calls = []
    from until.capture.sources import moodle_submit as canvas_submit_mod
    orig = canvas_submit_mod.submit

    def spy(plan, token, *, armed=False, **kw):
        calls.append(armed)
        return orig(plan, token, armed=armed, **kw)

    canvas_submit_mod.submit = spy
    try:
        allowed_plan = SubmissionPlan(
            allowed=True, blocks=[], warnings=[], content="본문",
            target=_target(), content_hash="H" * 12, confirm_nonce="n")
        web._submission_preview_from_plan(allowed_plan)
    finally:
        canvas_submit_mod.submit = orig
    assert not calls, "웹 미리보기 렌더는 submit()을 전혀 호출하면 안 된다(preview_request만)"
    print("OK 웹 미리보기는 submit()을 호출하지 않는다(감사 로그 0)")


class _Draft:
    def __init__(self, body, n=0):
        self.body = body
        self._n = n

    @property
    def n_decisions(self):
        return self._n


class _Guard:
    def __init__(self, passed=True):
        self.passed = passed


class _StubResult:
    """render_final 없이 _submission_preview_html만 검증하는 최소 Result 흉내."""
    def __init__(self, spec=None, final_body="완성된 최종 본문입니다."):
        self.spec = spec or {}
        self.draft = _Draft("초안", n=0)
        self.final_draft = _Draft(final_body)
        self.guard = _Guard(True)
        self.final_guard = _Guard(True)
        self.assignment_route = None
        self.deadline = None
        self.length_target = None


def test_wrapper_without_session_id_renders_nothing():
    out = web._submission_preview_html("", _StubResult())
    assert out == ""
    print("OK 세션 없으면 미리보기 패널을 렌더하지 않는다")


def test_wrapper_missing_assignment_ids_blocks_as_mismatch():
    # spec에 assignment_id/course_id가 없으면 assignment_mismatch로 차단되는 것이
    # 정상 동작(다른 과제함 오제출 방지) — 이 경로에서도 제출 버튼이 없어야 한다.
    out = web._submission_preview_html("tok123", _StubResult(spec={}))
    assert "assignment_mismatch" in out
    assert "<button" not in out
    print("OK 대상 과제 미확정이면 assignment_mismatch로 차단(버튼 없음)")


def test_wrapper_with_ids_shows_preview_when_allowed():
    out = web._submission_preview_html(
        "tok123", _StubResult(spec={"assignment_id": "202", "course_id": "101"}))
    assert "제출 미리보기" in out
    assert "확인 전에는 전송 없음" in out
    assert "<button" in out and "disabled" not in out
    print("OK 세션 spec의 assignment_id/course_id로 미리보기가 뜬다")


def test_preview_render_writes_no_ledger_files():
    """정상 GET/새로고침(=미리보기 렌더)만으로 nonce·감사 원장이 늘어나면 안
    된다. build_submission_plan(issue=False) + preview_request()는 순수해야
    하므로, 기본 상대경로(_until_work/*.jsonl)를 쓰는 cwd에서 렌더해도 두 파일
    다 생기지 않아야 한다. 허용 plan을 여러 번(새로고침 흉내) 렌더한다."""
    import os
    import tempfile
    prev_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as d:
        os.chdir(d)
        try:
            result = _StubResult(spec={"assignment_id": "202", "course_id": "101"})
            for _ in range(5):  # 새로고침 5회 흉내
                out = web._submission_preview_html("tok123", result)
                assert "<button" in out and "disabled" not in out  # 허용 상태 확인
            nonce_path = pathlib.Path(d) / "_until_work" / "submit_nonce.jsonl"
            audit_path = pathlib.Path(d) / "_until_work" / "submit_audit.jsonl"
            assert not nonce_path.exists(), "미리보기 렌더가 nonce 원장을 만들면 안 된다"
            assert not audit_path.exists(), "미리보기 렌더가 감사 원장을 만들면 안 된다"
        finally:
            os.chdir(prev_cwd)
    print("OK 미리보기 렌더(새로고침 반복)는 nonce·감사 원장을 만들지 않는다")


def test_asgi_confirm_local_live_cloud_dry_and_replay():
    """무장 스위치 하나 · Moodle 두 걸음 · 소비 nonce 재사용 거부.

    계약이 2026-08-23에 바뀌었다(사용자 지시: 최종 제출까지 가능하게).
      · 예전에는 `not cloud`가 무장 조건에 있어 **라이브 앱에서는 절대 실전송이
        안 됐다** — 사용자가 쓰는 곳이 라이브인데 거기서만 꺼져 있으면 기능이 없는
        것과 같다. 이제 스위치는 `UNTIL_SUBMIT_ARMED` 하나다.
      · eTL은 Moodle이라 제출이 **두 걸음**이다(save_submission → submit_for_grading).
        성공 한 번에 HTTP 호출이 2회 나간다.
      · 스위치가 켜져 있어도 한 건은 여전히 4겹을 통과해야 나간다 — 그 증거가
        아래 nonce 재사용 거부다.
    """
    from fastapi.testclient import TestClient
    from until.asgi import create_app
    from until.capture.sources import moodle_submit as canvas_submit

    result = _StubResult(spec={"assignment_id": "202", "course_id": "101"})
    original = canvas_submit.submit
    calls, issued = [], []
    previous_cwd = os.getcwd()
    old_armed = os.environ.get("UNTIL_SUBMIT_ARMED")
    old_token = os.environ.get("UNTIL_CANVAS_TOKEN")
    old_beta = os.environ.pop("UNTIL_BETA_CODE", None)
    old_tel = os.environ.pop("UNTIL_TELEMETRY", None)
    with tempfile.TemporaryDirectory() as d:
        os.chdir(d)
        nonce_path = pathlib.Path("_until_work/submit_nonce.jsonl")
        audit_path = pathlib.Path("_until_work/submit_audit.jsonl")

        def fake_http(method, url, data, headers):
            calls.append((method, url))
            return 201, '{"id":1}'

        def safe_submit(plan, nonce, *, armed=False, **kwargs):
            issued.append((plan.confirm_nonce, armed))
            return original(plan, nonce, armed=armed, http=fake_http,
                            nonce_path=nonce_path, audit_path=audit_path, **kwargs)

        canvas_submit.submit = safe_submit
        try:
            os.environ["UNTIL_SUBMIT_ARMED"] = "1"
            os.environ["UNTIL_CANVAS_TOKEN"] = "local-secret"
            web._SESSIONS["local-submit"] = result
            local = TestClient(create_app("mock", cloud=False))
            missing = local.post("/submit/confirm", data={"session": "local-submit"})
            assert missing.status_code == 400 and not calls
            prepared = local.post("/submit/prepare", data={"session": "local-submit"})
            nonce = re.search(r'name="confirm_nonce" value="([^"]+)"', prepared.text).group(1)
            live = local.post("/submit/confirm", data={
                "session": "local-submit", "confirm_nonce": nonce})
            assert live.status_code == 200 and "제출을 완료했어요" in live.text
            assert issued[-1][1] is True
            # Moodle은 두 걸음 — 저장 뒤 채점 확정.
            assert [u for _m, u in calls] == [
                "https://myetl.snu.ac.kr/webservice/rest/server.php"] * 2, calls

            replay = local.post("/submit/confirm", data={
                "session": "local-submit", "confirm_nonce": nonce})
            assert replay.status_code == 200 and "dry-run" in replay.text
            assert len(calls) == 2, "소비된 nonce 재사용은 FakeHTTP에도 도달하면 안 됨"

            web._SESSIONS["cloud-submit"] = result
            # 클라우드 메모리 세션은 소유자(uid)가 확정돼야 조회된다(fail-closed).
            # 운영에서는 요청 스코프의 _persist_session이 소유권을 박지만, 여기서는
            # 세션을 밖에서 주입하므로 고정 uid 쿠키 + 소유자 표를 직접 맞춘다.
            cloud_uid = "cloudsubmituid0000000000"
            web._OWNER["cloud-submit"] = cloud_uid
            cloud = TestClient(create_app("mock", cloud=True),
                               cookies={"uid": cloud_uid})
            prepared = cloud.post("/submit/prepare", data={"session": "cloud-submit"})
            cloud_nonce = re.search(
                r'name="confirm_nonce" value="([^"]+)"', prepared.text).group(1)
            # 클라우드도 같은 스위치를 쓴다 — 껐을 때만 dry-run이다.
            os.environ.pop("UNTIL_SUBMIT_ARMED", None)
            dry = cloud.post("/submit/confirm", data={
                "session": "cloud-submit", "confirm_nonce": cloud_nonce})
            assert dry.status_code == 200 and "실제 전송이 아직 열려 있지 않아요" in dry.text
            assert issued[-1][1] is False and len(calls) == 2
            # 원장 4줄 — 성공 제출의 두 걸음(저장·확정) + 재사용 dry + 클라우드 dry.
            # 두 걸음을 각각 남기는 이유: 어디서 멈췄는지 사후에 못 가리면
            # 원장이 있으나 마나다("저장은 됐는데 확정이 안 됐다"가 실제로 난다).
            assert len(audit_path.read_text(encoding="utf-8").splitlines()) == 4
        finally:
            canvas_submit.submit = original
            web._SESSIONS.pop("local-submit", None)
            web._SESSIONS.pop("cloud-submit", None)
            web.CLOUD = False
            os.chdir(previous_cwd)
            if old_armed is None: os.environ.pop("UNTIL_SUBMIT_ARMED", None)
            else: os.environ["UNTIL_SUBMIT_ARMED"] = old_armed
            if old_token is None: os.environ.pop("UNTIL_CANVAS_TOKEN", None)
            else: os.environ["UNTIL_CANVAS_TOKEN"] = old_token
            if old_beta is not None: os.environ["UNTIL_BETA_CODE"] = old_beta
            if old_tel is not None: os.environ["UNTIL_TELEMETRY"] = old_tel
    print("OK ASGI confirm: 무장 스위치 · Moodle 두 걸음 · nonce 재사용 거부")


def test_asgi_confirm_rejects_blocked_plan():
    from fastapi.testclient import TestClient
    from until.asgi import create_app
    web._SESSIONS["blocked-submit"] = _StubResult(spec={})
    web._OWNER["blocked-submit"] = "blockedsubmituid00000000"
    try:
        response = TestClient(create_app("mock")).post(
            "/submit/prepare", data={"session": "blocked-submit"})
        assert response.status_code == 409 and "하드 블록" in response.text
    finally:
        web._SESSIONS.pop("blocked-submit", None)
        web.CLOUD = False
    print("OK ASGI confirm은 차단 plan을 409로 거부")


def test_cli_confirm_requires_y_and_armed_env():
    from until import cli
    old = os.environ.get("UNTIL_SUBMIT_ARMED")
    try:
        os.environ["UNTIL_SUBMIT_ARMED"] = "1"
        assert cli._confirm_submission_from_cli(lambda _: "n") == (False, False)
        assert cli._confirm_submission_from_cli(lambda _: "y") == (True, True)
        os.environ.pop("UNTIL_SUBMIT_ARMED", None)
        assert cli._confirm_submission_from_cli(lambda _: "y") == (True, False)
    finally:
        if old is None: os.environ.pop("UNTIL_SUBMIT_ARMED", None)
        else: os.environ["UNTIL_SUBMIT_ARMED"] = old
    print("OK CLI confirm은 y + UNTIL_SUBMIT_ARMED=1을 모두 요구")


if __name__ == "__main__":
    test_blocked_plan_hides_submit_button()
    test_allowed_plan_shows_preview_request_with_active_button()
    test_unresolved_decision_warning_is_shown_even_when_allowed()
    test_gate_messages_are_html_escaped()
    test_never_calls_submit_armed()
    test_wrapper_without_session_id_renders_nothing()
    test_wrapper_missing_assignment_ids_blocks_as_mismatch()
    test_wrapper_with_ids_shows_preview_when_allowed()
    test_preview_render_writes_no_ledger_files()
    test_asgi_confirm_local_live_cloud_dry_and_replay()
    test_asgi_confirm_rejects_blocked_plan()
    test_cli_confirm_requires_y_and_armed_env()
    print("\nSUBMISSION WEB TESTS PASS")
