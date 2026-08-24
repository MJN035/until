"""토큰 온보딩: 무저장 검증, 사용자 격리 TTL, 친화 오류 UX."""
import http.client
import json
import pathlib
import sys
import tempfile
import threading
import urllib.error
from urllib.parse import urlencode

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from until import web
from until.asgi import create_app
from until.capture.sources import canvas_api


class _FakeAdapter:
    mode = "ok"

    def __init__(self, token=None, **_kwargs):
        self.token = token

    def get_self_profile(self, _base):
        if self.mode == "auth":
            raise RuntimeError("eTL 인증 실패(토큰 무효/만료)")
        if self.mode == "net":
            raise ConnectionError("network unavailable")
        return {"name": "테스트 학생"}

    def list_courses(self, _base):
        if self.mode == "auth":
            raise RuntimeError("eTL 인증 실패(토큰 무효/만료)")
        if self.mode == "net":
            raise ConnectionError("network unavailable")
        return [object(), object()]

    def list_assignments_graphql(self, _base):
        if self.mode == "auth":
            raise RuntimeError("eTL 인증 실패(토큰 무효/만료)")
        if self.mode == "net":
            raise ConnectionError("network unavailable")
        return self.list_courses(_base)


def test_token_check_contracts_and_no_leak():
    original = canvas_api.CanvasApiAdapter
    canvas_api.CanvasApiAdapter = _FakeAdapter
    secret = "DO-NOT-ECHO-THIS-TOKEN"
    try:
        client = TestClient(create_app("mock"))
        _FakeAdapter.mode = "ok"
        response = client.post("/api/v1/token/check", json={"token": secret})
        assert response.status_code == 200
        assert response.json() == {"ok": True, "name": "테스트 학생", "course_count": 2}
        assert secret not in response.text

        # Pydantic 기본 422는 잘못된 입력을 detail.input에 반사한다. 토큰은
        # 유효성 실패 때도 응답에 단 한 글자도 노출하지 않는다.
        too_long = "SECRET-" + "x" * 500
        response = client.post("/api/v1/token/check", json={"token": too_long})
        assert response.status_code == 400
        assert response.json() == {"ok": False, "reason": "auth"}
        assert too_long not in response.text and "SECRET-" not in response.text

        for malformed in (b"not-json", b"[]", b'{"wrong":"field"}'):
            response = client.post("/api/v1/token/check", content=malformed,
                                   headers={"content-type": "application/json"})
            assert response.status_code == 400
            assert response.json() == {"ok": False, "reason": "auth"}

        _FakeAdapter.mode = "auth"
        response = client.post("/api/v1/token/check", json={"token": secret})
        assert response.json() == {"ok": False, "reason": "auth"}
        assert secret not in response.text

        _FakeAdapter.mode = "net"
        response = client.post("/api/v1/token/check", json={"token": secret})
        assert response.json() == {"ok": False, "reason": "net"}
        assert secret not in response.text
    finally:
        canvas_api.CanvasApiAdapter = original


def test_token_namespace_and_ttl():
    web._TOKENS.clear()
    web._store_canvas_token("same-sid", "token-a", uid="user-a", now=100.0)
    web._store_canvas_token("same-sid", "token-b", uid="user-b", now=100.0)
    assert web._get_canvas_token("same-sid", uid="user-a", now=101.0) == "token-a"
    assert web._get_canvas_token("same-sid", uid="user-b", now=101.0) == "token-b"
    assert web._get_canvas_token("same-sid", uid="user-a",
                                 now=100.0 + web._TOKEN_TTL) == ""
    assert web._get_canvas_token("same-sid", uid="user-b",
                                 now=100.0 + web._TOKEN_TTL) == ""

    # 다른 sid를 다루는 다음 접근에서도 만료 비밀은 실제 메모리에서 제거된다.
    web._store_canvas_token("fresh", "fresh-token", uid="user-c",
                            now=100.0 + web._TOKEN_TTL + 1)
    assert all(value[1] != "token-a" for value in web._TOKENS.values())

    web._TOKENS.clear()
    for i in range(web._TOKEN_MAX + 20):
        web._store_canvas_token(f"sid-{i}", f"token-{i}", uid="one", now=200.0 + i)
    assert len(web._TOKENS) == web._TOKEN_MAX


def test_auth_classifier_follows_wrapped_http_error():
    inner = urllib.error.HTTPError("https://example.invalid", 403, "denied", {}, None)
    try:
        try:
            raise inner
        except urllib.error.HTTPError as exc:
            raise RuntimeError("adapter failed") from exc
    except RuntimeError as wrapped:
        assert web.is_etl_auth_error(wrapped)


def test_asgi_auth_failure_is_friendly():
    original = canvas_api.CanvasApiAdapter
    canvas_api.CanvasApiAdapter = _FakeAdapter
    try:
        _FakeAdapter.mode = "auth"
        response = TestClient(create_app("mock")).post(
            "/inbox", data={"token": "expired-token"})
        assert response.status_code == 401
        assert "eTL 연결이 만료됐어요" in response.text
        assert "profile/settings" in response.text and "expired-token" not in response.text
    finally:
        canvas_api.CanvasApiAdapter = original


def test_connect_step_has_check_button_and_numbered_guide():
    # 토큰 입력·연결 확인·발급 안내는 홈이 아니라 2단계(/connect)에 있다.
    page = web.render_connect("fast")
    assert "연결 확인" in page and "/api/v1/token/check" in page
    assert "만료일은 비운 채" in page and "aria-live" in page
    # 홈은 클릭만 받는다 — 토큰 입력칸이 남아 있으면 순서가 되돌아간 것.
    home = web.render_index()
    assert "eTL ACCESS TOKEN" not in home and "/api/v1/token/check" not in home


def test_legacy_token_check_and_auth_page_parity():
    from until.capture.sources import canvas_api
    original = canvas_api.CanvasApiAdapter
    canvas_api.CanvasApiAdapter = _FakeAdapter
    web.CLOUD = False
    web._Handler.backend = "mock"
    web._Handler.sso = False
    web._Handler.ws = False
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
        secret = "LEGACY-DO-NOT-ECHO"
        _FakeAdapter.mode = "ok"
        raw = json.dumps({"token": secret}).encode()
        conn.request("POST", "/api/v1/token/check", raw,
                     {"Content-Type": "application/json", "Content-Length": str(len(raw))})
        response = conn.getresponse()
        body = response.read().decode("utf-8")
        assert response.status == 200 and json.loads(body)["ok"] is True
        assert secret not in body

        too_long = "LEGACY-SECRET-" + "x" * 500
        raw = json.dumps({"token": too_long}).encode()
        conn.request("POST", "/api/v1/token/check", raw,
                     {"Content-Type": "application/json", "Content-Length": str(len(raw))})
        response = conn.getresponse(); body = response.read().decode("utf-8")
        assert response.status == 400 and too_long not in body

        for malformed in (b"not-json", b"[]", b'{"wrong":"field"}'):
            conn.request("POST", "/api/v1/token/check", malformed,
                         {"Content-Type": "application/json",
                          "Content-Length": str(len(malformed))})
            response = conn.getresponse(); body = response.read().decode("utf-8")
            assert response.status == 400
            assert json.loads(body) == {"ok": False, "reason": "auth"}

        _FakeAdapter.mode = "auth"
        raw = urlencode({"token": secret})
        conn.request("POST", "/inbox", raw,
                     {"Content-Type": "application/x-www-form-urlencoded"})
        response = conn.getresponse(); body = response.read().decode("utf-8")
        assert response.status == 401
        assert "eTL 연결이 만료됐어요" in body and "profile/settings" in body
        assert secret not in body
        conn.close()
    finally:
        httpd.shutdown(); httpd.server_close()
        canvas_api.CanvasApiAdapter = original


def test_etl_token_remember_is_account_only_and_encrypted():
    """로그인 계정에만, 암호화해서, 언제든 지울 수 있게.

    콜드 스타트의 세 번째 벽이 eTL 토큰 발급 왕복이다(원장 F2). 한 번 연결한
    사람에게 매번 다시 시키지 않으려면 보관해야 하는데, eTL 토큰은 **그 사람의
    LMS 계정 전체를 여는 열쇠**라 범위를 좁혀 뒀다:

      · 익명 uid에는 보관하지 않는다 — 쿠키 하나가 새면 남의 LMS가 열린다.
      · 평문으로 두지 않는다 — KV 자격증명 하나만 새도 전원의 eTL이 열린다.
        키는 Render env, 암호문은 디스크·KV로 **다른 시스템**에 분리한다.
      · 세션 키가 없으면 저장하지 않는다(fail-closed).
    """
    import shutil
    old_root, old_cloud = web._USERS_DIR, web.CLOUD
    old_uid = getattr(web._REQ, "uid", "")
    old_auth = getattr(web._REQ, "auth", None)
    d = pathlib.Path(tempfile.mkdtemp())

    class _Auth:
        uid = "acct-abc"
        email = "x@snu.ac.kr"

    try:
        web._USERS_DIR = d / "users"
        web.CLOUD = True

        # 미로그인 — 보관 경로 자체가 없다.
        web._REQ.uid, web._REQ.auth = "anon-123", None
        assert web._remembered_token_path() is None
        assert web._remember_token("tok-secret") is False

        # 로그인 — 보관되고, 파일에 평문이 없다.
        web._REQ.uid, web._REQ.auth = "acct-abc", _Auth()
        assert web._remember_token("tok-secret-xyz") is True
        assert web._remembered_token() == "tok-secret-xyz"
        blob = (web._USERS_DIR / "acct-abc" / "etl_token.json").read_text(
            encoding="utf-8")
        assert "tok-secret-xyz" not in blob, "평문이 저장됐다"

        # 다른 계정에는 보이지 않는다.
        web._REQ.uid = "acct-other"
        assert web._remembered_token() == ""

        # 해제하면 파일까지 사라진다.
        web._REQ.uid = "acct-abc"
        web._forget_token()
        assert web._remembered_token() == ""
        assert not (web._USERS_DIR / "acct-abc" / "etl_token.json").exists()

        # 화면: 보관 전에는 보관 제안 체크박스.
        before = web.render_connect(mode="fast")
        assert 'name="remember"' in before
        assert "저장하지 않고, 과제를" not in before, "낡은 약속 문구가 남아 있다"

        # 보관 후에는 **화면 모양을 바꾸지 않고** 입력칸이 채워져 보인다
        # (사용자 지시 2026-08-23 — 별도 안내 화면으로 갈아 끼우지 않는다).
        # 채워 보이는 값은 가림표이지 실토큰이 아니다: 실값을 HTML에 실으면
        # 페이지 소스·캐시·스크린샷에 자격증명이 남는다.
        web._remember_token("tok-2")
        after = web.render_connect(mode="fast")
        assert 'id="tok"' in after, "입력칸이 사라지면 화면이 두 벌이 된다"
        assert web.SAVED_TOKEN_MASK in after
        assert "tok-2" not in after, "실토큰이 HTML로 나갔다"
        assert web.uses_saved_token(web.SAVED_TOKEN_MASK) is True
        assert web.uses_saved_token("사용자가-새로-넣은-토큰") is False
        assert "/profile/etl-forget" in web._render_etl_panel()
    finally:
        web._USERS_DIR, web.CLOUD = old_root, old_cloud
        web._REQ.uid, web._REQ.auth = old_uid, old_auth
        shutil.rmtree(d, ignore_errors=True)
    print("OK eTL 토큰 보관 — 계정 한정·암호화·해제 가능")


def test_etl_token_envelope_rejects_tampering_and_expiry():
    """봉투는 위조·만료·키 불일치를 전부 ""로 흡수한다(예외를 내지 않는다)."""
    import json as _json
    import time as _time

    from until import etltoken

    d = pathlib.Path(tempfile.mkdtemp()) / "etl_token.json"
    tok = "1234~abcdefGHIJKL~secret"
    assert etltoken.save(d, tok) is True
    assert etltoken.load(d) == tok

    # 암호문 한 글자만 바뀌어도 열리지 않는다(encrypt-then-MAC).
    env = _json.loads(d.read_text(encoding="utf-8"))
    env["c"] = env["c"][:-4] + ("AAAA" if not env["c"].endswith("AAAA") else "BBBB")
    d.write_text(_json.dumps(env), encoding="utf-8")
    assert etltoken.load(d) == ""

    # 만료본은 읽히지 않고 **파일도 남기지 않는다**.
    etltoken.save(d, tok)
    assert etltoken.load(d, now=_time.time() + etltoken.TTL_SECONDS + 1) == ""
    assert not d.exists()

    # 깨진 JSON·없는 파일도 조용히 "".
    d.write_text("{ not json", encoding="utf-8")
    assert etltoken.load(d) == ""
    etltoken.clear(d)
    assert etltoken.load(d) == ""
    print("OK eTL 토큰 봉투 — 위조·만료·손상 흡수")


if __name__ == "__main__":
    test_token_check_contracts_and_no_leak()
    test_token_namespace_and_ttl()
    test_auth_classifier_follows_wrapped_http_error()
    test_asgi_auth_failure_is_friendly()
    test_connect_step_has_check_button_and_numbered_guide()
    test_legacy_token_check_and_auth_page_parity()
    test_etl_token_remember_is_account_only_and_encrypted()
    test_etl_token_envelope_rejects_tampering_and_expiry()
    print("TOKEN ONBOARDING TESTS PASS")
