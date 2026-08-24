"""FastAPI/Jinja2/HTMX 점진 전환 표면 테스트."""
import pathlib
import os
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from until.asgi import create_app

# until.config intentionally loads deployment values from .env at import time.
# Most ASGI tests exercise an ungated local app; the dedicated beta-gate test
# below sets and restores this value explicitly.
os.environ.pop("UNTIL_BETA_CODE", None)


class _Resp:
    def __init__(self, body, headers=None):
        self.body = body; self.headers = headers or {}
    def read(self): return self.body
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def geturl(self): return "https://myetl.snu.ac.kr/final"


def test_pages_and_static_assets():
    client = TestClient(create_app("mock"))
    home = client.get("/")
    assert home.status_code == 200 and "가장 가까운 과제 하나 해결하기" in home.text
    assert '/asset/app.css' in home.text and '/asset/app.js' in home.text
    css = client.get("/asset/app.css")
    js = client.get("/asset/app.js")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    assert js.status_code == 200 and "until-ans:" in js.text
    assert client.get("/healthz").json() == {"ok": True, "runtime": "asgi"}
    print("OK ASGI 페이지·정적 자산·healthz")


def test_json_draft_and_readiness():
    client = TestClient(create_app("mock"))
    response = client.post("/api/v1/drafts", json={
        "assignment": "에세이를 작성하세요. 주제는 본인이 선택합니다."
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["session"] and data["draft"] and data["decisions"]
    assert data["html_url"].startswith("/v/")
    assert client.get(data["html_url"]).status_code == 200
    ready = client.get(f"/api/v1/sessions/{data['session']}/readiness")
    assert ready.status_code == 200 and isinstance(ready.json(), dict)
    assert client.post("/api/v1/drafts", json={"assignment": ""}).status_code == 422
    revised = client.post("/revise", data={
        "session": data["session"], "mode": "paragraph", "paragraph": "1",
        "instruction": "더 자연스럽고 짧게"}, follow_redirects=False)
    assert revised.status_code == 303 and revised.headers["location"] == f"/v/{data['session']}"
    revised_page = client.get(revised.headers["location"])
    assert "이전 버전으로 복원" in revised_page.text
    restored = client.post("/revise", data={"session": data["session"], "mode": "restore"},
                           follow_redirects=False)
    assert restored.status_code == 303
    # 결정 반영 → 완성본 + 제안/점검 + 제출 파일까지 같은 ASGI 세션 경계.
    answers = {str(i): "내가 선택한 방향" for i, _ in enumerate(data["decisions"], 1)}
    final = client.post(f"/api/v1/sessions/{data['session']}/finalize",
                        json={"answers": answers})
    assert final.status_code == 200 and final.json()["session"] == data["session"]
    assert client.post(f"/api/v1/sessions/{data['session']}/suggest").status_code == 200
    assert client.post(f"/api/v1/sessions/{data['session']}/review").status_code == 200
    download = client.get(f"/dl/{data['session']}.md")
    assert download.status_code == 200 and "attachment" in download.headers["content-disposition"]
    print("OK ASGI JSON 초안·세션·readiness")


def test_explicit_ai_prohibition_returns_safe_422():
    client = TestClient(create_app("mock"))
    response = client.post("/api/v1/drafts", json={
        "assignment": "AI 사용 여부: 불가능. 반드시 자신의 의견을 작성하세요."
    })
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "ai_use_prohibited"
    assert "초안이나 답안을 생성하지 않습니다" in response.json()["detail"]
    form = client.post("/draft", data={
        "assignment": "생성형 AI 사용 금지. 본인이 직접 작성하세요."
    })
    assert form.status_code == 422
    assert "이 과제는 AI 사용 금지입니다" in form.text
    assert "초안이나 답안을 생성하지 않았습니다" in form.text
    print("OK ASGI AI 금지 과제 하드 차단")


def test_htmx_fragment():
    client = TestClient(create_app("mock"))
    response = client.post("/hx/draft", data={
        "assignment": "보고서를 작성하세요. 분석 방향은 본인이 선택합니다."
    }, headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "당신이 정할 것" in response.text and "<!doctype html>" not in response.text
    print("OK HTMX 초안 fragment")


def test_legacy_html_forms_on_asgi():
    client = TestClient(create_app("mock"))
    made = client.post(
        "/draft",
        data={"assignment": "자료를 근거로 보고서를 작성하세요.", "ui": "simple"},
        files={"files": ("evidence.txt", "검증된 근거", "text/plain")},
        follow_redirects=False,
    )
    assert made.status_code == 303 and made.headers["location"].startswith("/sv/")
    token = made.headers["location"].rsplit("/", 1)[1]
    assert client.get(f"/readiness/{token}.json").status_code == 200
    rated = client.post("/rate", data={"session": token, "score": "5", "ui": "simple"},
                         follow_redirects=False)
    assert rated.headers["location"] == f"/svf/{token}"
    assert client.post("/sessions/delete", data={"token": token},
                       follow_redirects=False).status_code == 303
    assert client.get(f"/v/{token}").status_code == 404
    assert client.post("/profile", data={"name": "테스트 학생"},
                       follow_redirects=False).status_code == 303
    assert client.post("/history/clear", follow_redirects=False).status_code == 303
    about = client.get("/about")
    assert about.status_code == 200
    assert 'var APP_URL = "/";' in about.text
    assert 'src="/asset/draft.jpg"' in about.text
    assert 'data-app-path="/new"' not in about.text
    assert 'data-app-path="/simple"' not in about.text
    # 시작 CTA는 접수 폼(#beta)으로, 초대 코드 보유자 입구만 앱으로 남는다.
    assert 'href="#beta"' in about.text and 'id="beta"' in about.text
    assert 'data-app-path="/connect?mode=fast"' not in about.text
    assert 'data-app-path="/beta-request"' in about.text
    print("OK ASGI legacy HTML form parity")


def test_cloud_manual_start_routes_require_etl_connection():
    client = TestClient(create_app("mock", cloud=True))
    for path in ("/simple", "/simple?demo=1", "/new"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/connect?mode=fast"
    response = client.post(
        "/draft", data={"assignment": "직접 입력한 과제"},
        follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/connect?mode=fast"
    response = client.post(
        "/hx/draft", data={"assignment": "직접 입력한 과제"},
        follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/connect?mode=fast"

    local = TestClient(create_app("mock"))
    assert local.get("/simple").status_code == 200
    assert local.get("/new").status_code == 200


def test_upload_profile_account_and_delete():
    from until import web
    old_users, old_sessions = web._USERS_DIR, web._SESS_DIR
    with tempfile.TemporaryDirectory() as d:
        web._USERS_DIR = pathlib.Path(d) / "users"
        web._SESS_DIR = pathlib.Path(d) / "sessions"
        try:
            client = TestClient(create_app("mock", cloud=True))
            made = client.post("/api/v1/upload-drafts",
                               data={"assignment": "자료를 바탕으로 보고서를 작성하세요."},
                               files={"files": ("근거.txt", "검증된 근거 내용", "text/plain")})
            assert made.status_code == 200, made.text
            token = made.json()["session"]
            saved = client.put("/api/v1/profile", json={"values": {
                "name": "테스트 학생", "department": "전기정보공학부", "evil": "제외"}})
            assert saved.status_code == 200 and saved.json()["name"] == "테스트 학생"
            account = client.get("/api/v1/account").json()
            assert account["profile"]["department"] == "전기정보공학부"
            assert "evil" not in account["profile"] and account["can_draft"] is True
            assert client.get("/sessions").status_code == 200
            assert client.get("/profile").status_code == 200
            assert client.delete(f"/api/v1/sessions/{token}").status_code == 204
            assert client.get(f"/v/{token}").status_code == 404
        finally:
            web._USERS_DIR, web._SESS_DIR = old_users, old_sessions
            web.CLOUD = False
    print("OK ASGI 업로드·프로필·계정·세션 삭제")


def test_etl_inbox_and_draft_api():
    import json
    from until.capture.sources import canvas_api
    fixture = pathlib.Path("examples/canvas_fixture")
    courses = (fixture / "courses_api.json").read_bytes()
    assignments = (fixture / "assignments_api.json").read_bytes()
    one = (fixture / "assignment_api.json").read_bytes()
    files = (fixture / "files_api.json").read_bytes()
    modules = (fixture / "modules_api.json").read_bytes()
    empty = json.dumps([]).encode()
    def fake(req, timeout=None):
        url = req.full_url
        if "/students/submissions" in url or "/discussion_topics" in url: return _Resp(empty)
        if "/modules" in url: return _Resp(modules)
        if url.rstrip("/").endswith("/files"): return _Resp(files)
        if "/assignments/" in url: return _Resp(one)
        if "/assignments" in url: return _Resp(assignments)
        if "/courses" in url: return _Resp(courses)
        return _Resp(b"sample text")
    original = canvas_api.urllib.request.urlopen
    canvas_api.urllib.request.urlopen = fake
    try:
        client = TestClient(create_app("mock"))
        inbox = client.post("/api/v1/inbox", json={"token": "TESTTOKEN"})
        assert inbox.status_code == 200 and inbox.json()
        item = inbox.json()[0]
        made = client.post("/api/v1/etl-drafts", json={
            "token": "TESTTOKEN", "url": item["url"]})
        assert made.status_code == 200, made.text
        assert made.json()["session"] and made.json()["draft"]
    finally:
        canvas_api.urllib.request.urlopen = original
    print("OK ASGI eTL 인박스→자료 수집→초안")


def test_form_inbox_fast_one_click():
    """라이브(ASGI)에서도 '가장 가까운 과제 하나 해결하기'가 목록이 아니라
    초안까지 간다 — fast=1이 무시돼 목록만 나오던 실사용 회귀."""
    import json
    from until import web
    from until.capture.sources import canvas_api
    fixture = pathlib.Path("examples/canvas_fixture")
    courses = (fixture / "courses_api.json").read_bytes()
    assignments = (fixture / "assignments_api.json").read_bytes()
    one = (fixture / "assignment_api.json").read_bytes()
    files = (fixture / "files_api.json").read_bytes()
    modules = (fixture / "modules_api.json").read_bytes()
    empty = json.dumps([]).encode()
    def fake(req, timeout=None):
        url = req.full_url
        if "/students/submissions" in url or "/discussion_topics" in url: return _Resp(empty)
        if "/modules" in url: return _Resp(modules)
        if url.rstrip("/").endswith("/files"): return _Resp(files)
        if "/assignments/" in url: return _Resp(one)
        if "/assignments" in url: return _Resp(assignments)
        if "/courses" in url: return _Resp(courses)
        return _Resp(b"sample text")
    original = canvas_api.urllib.request.urlopen
    canvas_api.urllib.request.urlopen = fake
    try:
        client = TestClient(create_app("mock"))
        r = client.post("/inbox", data={"token": "TESTTOKEN", "fast": "1",
                                        "ui": "simple"},
                        follow_redirects=False)
        assert r.status_code == 303, (r.status_code, r.text[:200])
        assert r.headers["location"].startswith("/sv/")
        tok = r.headers["location"].rsplit("/", 1)[1]
        # 딸깍 완주 — AI 제안이 미리 생성돼 간단 화면 답칸이 프리필된다.
        res = web._get_session(tok)
        if res is not None and res.draft.decisions:
            assert web._SUGGESTIONS.get(tok), "fast 경로는 AI 제안을 미리 생성해야 한다"
        page = client.get(r.headers["location"])
        assert page.status_code == 200
        if res is not None and res.draft.decisions:
            # 답칸이 먼저, 초안 본문은 접힌 채 함께(2026-08-22, 원장 F9~F11).
            assert 'name="answer_' in page.text
            assert 'class="draft-peek"' in page.text
        # 연습 모드 — 이미 낸/지난 과제로도 같은 딸깍이 돈다(필터 해제 + practice 픽).
        r = client.post("/inbox", data={"token": "TESTTOKEN", "practice": "1",
                                        "ui": "simple", "unsubmitted": "1",
                                        "hide_past": "1"},
                        follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/sv/")
    finally:
        canvas_api.urllib.request.urlopen = original
    print("OK ASGI fast 원-클릭(목록 아닌 초안 + 제안 프리필)")


def test_cloud_uid_isolation_beta_and_security():
    from until import web
    old_users, old_sessions = web._USERS_DIR, web._SESS_DIR
    old_code, old_admin = os.environ.get("UNTIL_BETA_CODE"), os.environ.get("UNTIL_ADMIN_KEY")
    with tempfile.TemporaryDirectory() as d:
        web._USERS_DIR = pathlib.Path(d) / "users"
        web._SESS_DIR = pathlib.Path(d) / "sessions"
        os.environ.pop("UNTIL_BETA_CODE", None)
        os.environ["UNTIL_ADMIN_KEY"] = "secret-admin"
        try:
            app = create_app("mock", cloud=True)
            a, b = TestClient(app), TestClient(app)
            made = a.post("/api/v1/drafts", json={"assignment": "에세이. 주제는 자유."})
            assert made.status_code == 200
            token = made.json()["session"]
            assert a.get(f"/v/{token}").status_code == 200
            assert b.get(f"/v/{token}").status_code == 404
            assert a.get("/").headers["x-frame-options"] == "DENY"
            assert "Content-Security-Policy" in a.get("/").headers
            # 관리자 키는 URL 쿼리로 절대 인증되지 않는다(기록·리퍼러 유출 방지).
            query_auth = a.get("/admin?key=secret-admin", follow_redirects=False)
            assert query_auth.status_code == 200
            assert "관리자 로그인" in query_auth.text
            assert "until_admin=" not in (query_auth.headers.get("set-cookie") or "")

            os.environ["UNTIL_BETA_CODE"] = "INVITE-ONE"
            gated = TestClient(create_app("mock", cloud=True))
            assert gated.get("/").status_code == 403
            passed = gated.post("/beta", data={"code": "INVITE-ONE"},
                                follow_redirects=False)
            assert passed.status_code == 303 and "beta=" in passed.headers["set-cookie"]
            assert gated.get("/").status_code == 200
        finally:
            web._USERS_DIR, web._SESS_DIR = old_users, old_sessions
            if old_code is None: os.environ.pop("UNTIL_BETA_CODE", None)
            else: os.environ["UNTIL_BETA_CODE"] = old_code
            if old_admin is None: os.environ.pop("UNTIL_ADMIN_KEY", None)
            else: os.environ["UNTIL_ADMIN_KEY"] = old_admin
            web.CLOUD = False
    print("OK ASGI uid 격리·베타 게이트·관리자·보안 헤더")


def test_cloud_consent_notice_flow():
    """ASGI 클라우드: 수집 켜짐+선택 전엔 고지 1회 → 어느 선택이든 통과,
    /consent 설정 페이지에서 변경. 수집 꺼짐이면 고지 없음."""
    from until import web
    old_users, old_sessions = web._USERS_DIR, web._SESS_DIR
    old_tel = os.environ.get("UNTIL_TELEMETRY")
    with tempfile.TemporaryDirectory() as d:
        web._USERS_DIR = pathlib.Path(d) / "users"
        web._SESS_DIR = pathlib.Path(d) / "sessions"
        os.environ["UNTIL_TELEMETRY"] = "1"
        try:
            client = TestClient(create_app("mock", cloud=True))
            first = client.get("/")
            assert first.status_code == 200 and "동의하고 시작" in first.text
            assert "동의하고 시작" in client.get("/plan").text  # 선택 전 전 경로 고지
            done = client.post("/consent", data={"choice": "no"}, follow_redirects=True)
            assert done.status_code == 200 and "동의하고 시작" not in done.text
            settings = client.get("/consent")
            assert '<span class="pill">수집 안 함</span>' in settings.text
            changed = client.post("/consent", data={"choice": "yes", "back": "settings"},
                                  follow_redirects=True)
            assert '<span class="pill ok">수집 중</span>' in changed.text
            os.environ.pop("UNTIL_TELEMETRY", None)  # 수집 꺼짐 → 새 사용자도 고지 없음
            fresh = TestClient(create_app("mock", cloud=True))
            assert "동의하고 시작" not in fresh.get("/").text
        finally:
            web._USERS_DIR, web._SESS_DIR = old_users, old_sessions
            web.CLOUD = False
            if old_tel is None:
                os.environ.pop("UNTIL_TELEMETRY", None)
            else:
                os.environ["UNTIL_TELEMETRY"] = old_tel
    print("OK ASGI 동의 고지·설정 흐름")




def _canvas_fake():
    """examples/canvas_fixture로 eTL을 대신하는 urlopen(네트워크 0)."""
    import json
    fixture = pathlib.Path("examples/canvas_fixture")
    courses = (fixture / "courses_api.json").read_bytes()
    assignments = (fixture / "assignments_api.json").read_bytes()
    one = (fixture / "assignment_api.json").read_bytes()
    files = (fixture / "files_api.json").read_bytes()
    modules = (fixture / "modules_api.json").read_bytes()
    empty = json.dumps([]).encode()

    def fake(req, timeout=None):
        url = req.full_url
        if "/students/submissions" in url or "/discussion_topics" in url: return _Resp(empty)
        if "/modules" in url: return _Resp(modules)
        if url.rstrip("/").endswith("/files"): return _Resp(files)
        if "/assignments/" in url: return _Resp(one)
        if "/assignments" in url: return _Resp(assignments)
        if "/courses" in url: return _Resp(courses)
        return _Resp(b"sample text")
    return fake


def _practice_session(client):
    """연습 모드(지난 과제) 딸깍 1회 → 세션 토큰."""
    r = client.post("/inbox", data={"token": "TESTTOKEN", "practice": "1",
                                    "ui": "simple", "unsubmitted": "1",
                                    "hide_past": "1"}, follow_redirects=False)
    assert r.status_code == 303, (r.status_code, r.text[:200])
    return r.headers["location"].rsplit("/", 1)[1]


def test_healthz_reports_deployed_commit():
    """재배포가 실제로 반영됐는지 밖에서 확인할 수 있어야 한다.

    stdlib은 커밋을 병기하는데 운영 엔트리포인트인 ASGI만 빠져 있었다 — 고친 게
    안 올라갔는데 올라간 줄 알기 딱 좋은 상태였다(2026-08-21).
    """
    import os as _os

    client = TestClient(create_app("mock"))
    old = _os.environ.get("RENDER_GIT_COMMIT")
    try:
        _os.environ.pop("RENDER_GIT_COMMIT", None)
        body = client.get("/healthz").json()
        assert body["ok"] is True and "sha" not in body   # 로컬은 키가 없다
        _os.environ["RENDER_GIT_COMMIT"] = "abc1234567890"
        body = TestClient(create_app("mock")).get("/healthz").json()
        assert body["sha"] == "abc1234", body             # 앞 7자리
    finally:
        if old is None:
            _os.environ.pop("RENDER_GIT_COMMIT", None)
        else:
            _os.environ["RENDER_GIT_COMMIT"] = old
    print("OK /healthz 배포 커밋 병기")


def test_asgi_second_pass_failure_is_not_bare_500():
    """2차 패스 LLM이 죽어도 사용자에게 맨 "Internal Server Error"를 주지 않는다.

    stdlib 서버는 같은 예외를 `user_error_message()` 안내로 바꿔 보내는데 ASGI에는
    그 그물이 없었다 — 운영 엔트리포인트가 ASGI(`uvicorn until.asgi:app`)라
    실사용자는 다섯 단어 영문만 봤다(2026-08-21 실사용 보고). 로컬 stdlib으로는
    영원히 재현되지 않는 종류라, 이 케이스는 **반드시 ASGI로** 세워 둔다.
    """
    from until import pipeline, web
    from until.capture.sources import canvas_api

    class _ProviderError(Exception):
        status_code = 400            # 강등 대상이 아니라 즉시 표면화되는 부류

    class _Broken:
        def complete(self, *a, **k):
            raise _ProviderError("400 bad request")

    original = canvas_api.urllib.request.urlopen
    original_build = pipeline.build_client
    canvas_api.urllib.request.urlopen = _canvas_fake()
    try:
        client = TestClient(create_app("mock", cloud=True),
                            raise_server_exceptions=False)
        token = _practice_session(client)
        web._SUGGESTIONS.pop(token, None)
        pipeline.build_client = lambda *a, **k: _Broken()

        for path, data in (("/suggest", {"session": token, "ui": "simple"}),
                           ("/finalize", {"session": token, "ui": "simple",
                                          "answer_1": "무난한 방향"})):
            r = client.post(path, data=data, follow_redirects=False,
                            headers={"accept": "text/html"})
            assert r.status_code == 500, (path, r.status_code)
            assert r.text.strip() != "Internal Server Error", path
            assert "지금은 끝내지 못했어요" in r.text, path
            assert "초안은 그대로 남아 있어요" in r.text, path

        # 기계가 읽는 표면은 JSON을 유지한다 — HTML로 덮으면 호출자가 깨진다.
        r = client.post(f"/api/v1/sessions/{token}/finalize", json={"answers": {}})
        assert r.status_code == 500 and r.json()["detail"] == "internal_error"
    finally:
        canvas_api.urllib.request.urlopen = original
        pipeline.build_client = original_build
        web.CLOUD = False
    print("OK ASGI 2차 패스 실패 — 맨 500 대신 안내")


def test_asgi_finalize_autofills_blank_decisions():
    """빈칸 두고 '완성하기' → AI가 채운다. **ASGI에도** 배선돼 있어야 한다.

    `bf912e4`가 stdlib(web.py)만 고쳐서 라이브에는 이 기능이 통째로 없었다
    (2026-08-21 발견). stdlib 쪽은 test_submit_ready가 이미 잡고 있으므로,
    여기 쌍둥이 케이스를 두어 "한쪽만 고치고 다른 쪽을 빠뜨림"을 막는다.
    """
    from until import web
    from until.capture.sources import canvas_api

    original = canvas_api.urllib.request.urlopen
    canvas_api.urllib.request.urlopen = _canvas_fake()
    try:
        client = TestClient(create_app("mock", cloud=True))
        token = _practice_session(client)
        # 클라우드에서 `_get_session`은 uid 스코프라 요청 밖에서는 None이다 —
        # 여기서는 보관소를 직접 본다(스코프 밖 조회로 이 시험이 조용히
        # 건너뛰어 버리면 아무것도 못 잡는다).
        result = web._SESSIONS[token]
        assert result.draft.decisions, "픽스처 과제에 결정 칸이 있어야 이 시험이 성립한다"
        web._ANSWERS.pop(token, None)
        web._AUTOFILLED.pop(token, None)

        # answer_* 를 **하나도** 싣지 않는다 = 한 칸도 안 채우고 완성 클릭.
        r = client.post("/finalize", data={"session": token, "ui": "simple"},
                        follow_redirects=False)
        assert r.status_code == 303, (r.status_code, r.text[:200])
        assert web._AUTOFILLED.get(token), "빈칸이 AI 답으로 채워져야 한다"
        assert web._SESSIONS[token].final_draft is not None
        # 채운 사실을 화면에 밝히지 않으면 학생이 자기가 정한 줄 알고 제출한다.
        page = client.get(f"/svf/{token}")
        assert page.status_code == 200 and "AI가 대신 정한 곳" in page.text
    finally:
        canvas_api.urllib.request.urlopen = original
        web.CLOUD = False
    print("OK ASGI 자동채움 배선 + 화면 고지")


def test_pick_falls_back_to_remembered_token_and_400_is_readable():
    """목록에서 과제를 눌렀을 때 원시 JSON이 뜨지 않는다 + 보관 연결로 이어진다.

    실사용(2026-08-23, 물리학1): 과제를 눌렀더니 화면에
    `{"detail": "assignment_or_token_missing"}` 가 그대로 떴다. 둘이 겹친 결과다.
      · 세션 토큰 저장소는 **메모리 + TTL**이라 Render 무료 티어가 잠들면 사라진다
        → /pick이 토큰을 못 찾는다.
      · 400은 예외 핸들러에 없어 기본 JSON 응답으로 떨어진다 → 사용자는 무슨
        일인지 알 수 없다.
    """
    import shutil

    from fastapi.testclient import TestClient

    from until import web
    from until.asgi import create_app

    old_root, old_cloud = web._USERS_DIR, web.CLOUD
    old_key = os.environ.get("UNTIL_SESSION_KEY")
    old_code = os.environ.pop("UNTIL_BETA_CODE", None)
    d = pathlib.Path(tempfile.mkdtemp())
    try:
        web._USERS_DIR = d / "users"
        os.environ["UNTIL_SESSION_KEY"] = "k" * 32
        app = create_app("mock", cloud=True)
        client = TestClient(app)
        client.get("/")

        # 토큰도 보관본도 없다 — 그래도 **사람이 읽는 화면**이어야 한다.
        # 브라우저 폼 전송 — Accept가 없어도 사람이 읽는 화면이어야 한다.
        r = client.post("/pick", data={"url": "https://etl.example/a/1"},
                        follow_redirects=False)
        assert r.status_code == 400
        assert "text/html" in r.headers.get("content-type", ""), r.headers
        assert "detail" not in r.text[:200], "원시 JSON이 그대로 나갔다"
        assert "다시 연결" in r.text

        # API 경로는 기존대로 JSON을 유지한다(클라이언트 계약).
        api = client.post("/api/v1/drafts", json={})
        assert "application/json" in api.headers.get("content-type", "")
    finally:
        web._USERS_DIR, web.CLOUD = old_root, old_cloud
        if old_key is None:
            os.environ.pop("UNTIL_SESSION_KEY", None)
        else:
            os.environ["UNTIL_SESSION_KEY"] = old_key
        if old_code is not None:
            os.environ["UNTIL_BETA_CODE"] = old_code
        shutil.rmtree(d, ignore_errors=True)
    print("OK /pick 400이 사람이 읽는 화면 (API는 JSON 유지)")


def test_router_errors_are_human_pages_not_raw_json():
    """라우터가 던지는 404·405는 starlette의 HTTPException이라 핸들러를 지나쳤다.

    실사용 2026-08-23: `/inbox`를 주소창(북마크·새로고침)으로 열었더니 화면에
    `{"detail":"Method Not Allowed"}`가 그대로 떴다. 사용자는 앱이 깨진 줄 안다.
    fastapi의 서브클래스에만 핸들러를 걸어 두면 그 둘은 분기를 통째로 지나친다.
    """
    from fastapi.testclient import TestClient

    from until.asgi import create_app

    client = TestClient(create_app("mock", cloud=False))
    resp = client.get("/inbox", headers={"Accept": "text/html"})
    assert resp.status_code == 405
    assert "text/html" in resp.headers.get("content-type", "")
    assert "detail" not in resp.text[:200], "원시 JSON이 그대로 나갔다"
    assert "바로 열 수 없어요" in resp.text
    assert "/connect?mode=list" in resp.text, "되돌아갈 길이 없다"

    missing = client.get("/none-such-page", headers={"Accept": "text/html"})
    assert missing.status_code == 404 and "찾을 수 없어요" in missing.text

    # API 경로는 기계가 읽는다 — JSON 계약 유지.
    api = client.get("/api/v1/drafts", headers={"Accept": "text/html"})
    assert "application/json" in api.headers.get("content-type", "")
    print("OK 라우터 404·405도 사람이 읽는 화면 (API는 JSON)")


if __name__ == "__main__":
    test_pages_and_static_assets()
    test_json_draft_and_readiness()
    test_explicit_ai_prohibition_returns_safe_422()
    test_htmx_fragment()
    test_legacy_html_forms_on_asgi()
    test_cloud_manual_start_routes_require_etl_connection()
    test_upload_profile_account_and_delete()
    test_etl_inbox_and_draft_api()
    test_form_inbox_fast_one_click()
    test_cloud_uid_isolation_beta_and_security()
    test_cloud_consent_notice_flow()
    test_healthz_reports_deployed_commit()
    test_asgi_second_pass_failure_is_not_bare_500()
    test_asgi_finalize_autofills_blank_decisions()
    test_pick_falls_back_to_remembered_token_and_400_is_readable()
    test_router_errors_are_human_pages_not_raw_json()
    print("\nASGI TESTS PASS")
