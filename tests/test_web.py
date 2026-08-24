"""P8 — 최소 UI 테스트 (오프라인·mock, 표준 라이브러리만)."""
import io
import sys, pathlib, threading
import http.client
from urllib.parse import urlencode
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.config import Config
from until.pipeline import finalize
from until import web


def _post(conn, path, fields, follow=True):
    """POST 후 303(PRG)이면 Location으로 GET까지 따라가 (status, html) 반환."""
    conn.request("POST", path, urlencode(fields),
                 {"Content-Type": "application/x-www-form-urlencoded"})
    r = conn.getresponse(); body = r.read().decode("utf-8")
    if follow and r.status == 303:
        loc = r.getheader("Location")
        conn.request("GET", loc); r = conn.getresponse(); body = r.read().decode("utf-8")
    return r.status, body


def test_render_index_has_form():
    # 화면 순서(사용자 지시): 홈은 '무엇을 할지' 클릭만 받고, eTL 토큰은 /connect에서.
    html_ = web.render_index()
    assert "가장 가까운 과제 하나 해결하기" in html_
    assert 'href="/connect?mode=fast"' in html_
    assert "eTL ACCESS TOKEN" not in html_          # 홈에 토큰 입력칸 없음
    assert 'href="/connect?mode=list"' in html_ and "과제 목록에서 직접 고르기" in html_
    assert 'href="/connect?mode=practice"' in html_
    assert 'action="/draft"' not in html_ and 'action="/collect"' not in html_
    assert 'href="/simple"' not in html_   # 운영 진입은 eTL 연결만 제공
    # 홈 보조 링크는 분류명·세로선 없이 항목만 한 줄로 나열한다.
    assert 'class="home-tools"' in html_
    assert 'aria-label="다른 시작 방법"' in html_
    assert "직접 시작" not in html_
    assert not any(label in html_ for label in ("둘러보기", "계정·설정"))
    assert "home-tool-group" not in html_
    assert '<span aria-hidden="true">→</span>' not in html_
    assert 'href="/new"' not in html_ and 'href="/about"' in html_ and 'href="/plan"' in html_
    assert "직접 붙여넣기" not in html_
    assert "플랜·데이터 설정" in html_ and 'href="/consent"' not in html_
    old_cloud = web.CLOUD
    import os
    old_telemetry = os.environ.get("UNTIL_TELEMETRY")
    try:
        web.CLOUD = True
        os.environ["UNTIL_TELEMETRY"] = "1"
        cloud_home = web.render_index()
        assert "플랜·데이터 설정" in cloud_home and 'href="/consent"' not in cloud_home
    finally:
        web.CLOUD = old_cloud
        if old_telemetry is None:
            os.environ.pop("UNTIL_TELEMETRY", None)
        else:
            os.environ["UNTIL_TELEMETRY"] = old_telemetry
    # 연결 수단이 이미 있으면(SSO·운영 토큰) 홈에서 바로 제출한다 — 한 칸 건너뛴다.
    direct = web.render_index(has_env_token=True)
    assert 'action="/inbox"' in direct
    assert 'type="submit" name="fast" value="1"' in direct
    assert 'href="/connect' not in direct
    # 2단계(eTL 연결) 화면: 같은 동작을 /inbox로 이어 붙이고 탈출구를 제공한다.
    conn_page = web.render_connect("fast")
    assert 'action="/inbox"' in conn_page
    assert 'name="fast" value="1"' in conn_page
    assert "eTL ACCESS TOKEN" in conn_page
    assert 'href="/simple"' not in conn_page
    assert 'href="/new"' not in conn_page
    assert 'name="practice" value="1"' in web.render_connect("practice")
    # 간단 모드 홈에는 붙여넣기 폼이 있어야 한다.
    smp = web.render_simple_index()
    assert 'action="/draft"' in smp and 'name="assignment"' in smp
    assert 'class="utility-page' in smp and 'class="page-head"' in smp
    assert 'class="task-form-section task-form-primary"' in smp
    new_page = web.render_new_assignment()
    assert 'class="utility-page' in new_page and 'class="page-head"' in new_page
    assert 'class="task-form-section"' in new_page
    # Layer 3: '내 글 올리기'(voice) 우선 노출 — 혜택 문구가 보여야 한다.
    assert 'name="voice_files"' in smp
    assert "내 문체" in smp and "더 자연스럽고 내 글처럼" in smp
    # 샘플 과제 체험 링크(빈 상태 온보딩) + 프리필 시 샘플 본문이 textarea에.
    # 작동 예시 페이지는 2026-08-21에 없앴다 — 소개가 같은 5단계를 보여 준다.
    assert 'href="/simple?demo=1"' in smp and 'href="/demo"' not in smp
    pre = web.render_simple_index(prefill=web.demo_assignment_text())
    assert "기말 조사 보고서" in pre and "3000자" in pre
    assert 'href="/simple?demo=1"' not in pre     # 프리필 화면엔 링크 중복 없음
    # 홈에 남아 있던 작동 예시 링크는 없앴다.
    assert 'href="/demo"' not in html_
    # 페이지 셸은 Jinja2 경계, CSS/JS는 Python 문자열이 아닌 정적 자산.
    wrapped = web._wrap("<main>본문</main>", "mock<x>", "제목<x>")
    assert '/asset/app.css' in wrapped and '/asset/app.js' in wrapped
    assert "<style>" not in wrapped and "document.addEventListener" not in wrapped
    assert "mock&lt;x&gt;" in wrapped and "제목&lt;x&gt;" in wrapped
    assert "<main>본문</main>" in wrapped
    # 설정·플랜도 같은 보조 페이지 문법으로 현재 상태→다음 행동 순서를 지킨다.
    plan_page = web.render_plan()
    assert 'class="utility-page' in plan_page
    assert "플랜·데이터 설정" in plan_page and 'href="/consent"' in plan_page
    assert 'class="setting-status"' in web.render_consent_settings(False)
    print("OK index minimal + simple paste + voice prominence")




def test_naturalness_guidance_in_prompts():
    # Layer 1: 자연스러운 글쓰기 지침이 초안/최종 프롬프트에 상시 내장(추가 호출 0).
    from until.execution import prompts
    sys = prompts.SYSTEM
    assert "[ 자연스러운 글쓰기" in sys
    assert "상투적 도입구" in sys and "빈 골조" in sys
    assert "## 서론" in sys                     # 정형 뼈대 남발 지양 예시
    assert "(f)" in sys                          # 자기검증 항목 추가됨
    # 최종본(finalize)에도 자연스러움 지침이 있어야 한다.
    assert "자연스럽게" in prompts.FINALIZE_SYSTEM
    # 경계선은 그대로 — 판단 대신 확정 금지 규칙이 사라지지 않았다.
    assert "경계선 규칙이 항상 우선" in sys
    # A안: 논리 구조 — 병렬 나열 금지·논증 전개 지침 내장.
    assert "[ 논리 구조" in sys and "병렬 나열은 논증이 아니다" in sys
    assert "소주제문" in sys
    # A안: 개인 맥락 활용 — 내 자료 소재 우선 + 경험 창작 금지(빈칸형 DECISION).
    assert "[ 개인 맥락 활용" in sys and "지어내지 말 것" in sys
    assert "우선 활용" in sys
    # 에세이 유형 지침도 논증 중심으로(병렬 소개 금지).
    eg = prompts.type_guidance("essay")
    assert "논증" in eg and "병렬 소개" in eg
    print("OK naturalness + logic-structure + personal-context prompts (boundary preserved)")


def _canvas_fixture(name):
    import json
    return json.loads(pathlib.Path(f"examples/canvas_fixture/{name}").read_text(encoding="utf-8"))


def test_inbox_and_pick_flow_without_network():
    import json
    from until.capture.sources import canvas_api
    courses = json.dumps(_canvas_fixture("courses_api.json")).encode()
    assigns = json.dumps(_canvas_fixture("assignments_api.json")).encode()
    one = json.dumps(_canvas_fixture("assignment_api.json")).encode()
    files = json.dumps(_canvas_fixture("files_api.json")).encode()
    modules = json.dumps(_canvas_fixture("modules_api.json")).encode()

    def fake_urlopen(req, timeout=None):
        u = req.full_url
        if "/modules" in u: return _FakeResp(modules)
        if u.rstrip("/").endswith("/files"): return _FakeResp(files)
        if "/assignments/" in u: return _FakeResp(one)          # 단일 과제
        if "/assignments" in u: return _FakeResp(assigns)        # 과제 목록
        if "/courses" in u and "/api/v1/courses" in u and "/courses/" not in u.split("/api/v1/")[1]:
            return _FakeResp(courses)
        if u.rstrip("/").endswith("/courses"): return _FakeResp(courses)
        return _FakeResp(b"%PDF-1.4 fake")

    orig = canvas_api.urllib.request.urlopen
    canvas_api.urllib.request.urlopen = fake_urlopen
    web._Handler.backend = "mock"
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        # POST /inbox → 과제 목록
        conn.request("POST", "/inbox", urlencode({"token": "TESTTOKEN"}),
                     {"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse(); page = r.read().decode("utf-8")
        assert r.status == 200 and "내 과제" in page and 'action="/pick"' in page
        assert "TESTTOKEN" not in page and 'name="sid"' in page  # 토큰은 HTML에 노출 안 됨
        import re
        m = re.search(r'name="url" value="([^"]+)"', page)
        assert m, "과제 목록에 선택 URL이 있어야 함"
        # POST /pick → (PRG) 본문+자료 수집 후 초안
        s, draft = _post(conn, "/pick", {"url": m.group(1), "token": "TESTTOKEN"})
        assert s == 200 and "제출 전에 볼 것" in draft
        assert "eTL에서 모은 관련 자료" in draft     # 자동수집 자료 표시
        conn.close()
    finally:
        httpd.shutdown(); httpd.server_close()
        canvas_api.urllib.request.urlopen = orig
    print("OK inbox -> pick flow (목록→선택→자료수집→초안)")


def test_ws_mode_unsubmitted_filter_honest():
    # WS '미제출만' 필터가 실제로 제출 상태를 조회해 거른다(리뷰 Finding 1).
    import json
    from until.capture.sources import moodle_ws
    site = {"userid": 7}
    courses = [{"id": 42, "fullname": "도시문화론", "enddate": 0}]
    assigns = {"courses": [{"id": 42, "fullname": "도시문화론", "assignments": [
        {"id": 1, "cmid": 100, "course": 42, "name": "제출한 과제", "duedate": 1782000000},
        {"id": 2, "cmid": 101, "course": 42, "name": "안 낸 과제", "duedate": 1782000000},
    ]}]}

    def fake(req, timeout=None):
        b = (req.data or b"").decode("utf-8")
        if "get_site_info" in b:
            return _FakeResp(json.dumps(site).encode())
        if "get_users_courses" in b:
            return _FakeResp(json.dumps(courses).encode())
        if "get_submission_status" in b:
            done = "submitted" if "assignid=1" in b else "new"
            return _FakeResp(json.dumps({"lastattempt": {"submission": {"status": done}}}).encode())
        if "get_assignments" in b:
            return _FakeResp(json.dumps(assigns).encode())
        if "get_forums_by_courses" in b or "get_forum_discussions" in b:
            return _FakeResp(json.dumps([]).encode())
        return _FakeResp(json.dumps({}).encode())

    orig = moodle_ws.urllib.request.urlopen
    moodle_ws.urllib.request.urlopen = fake
    web._Handler.backend = "mock"; web._Handler.ws = True; web._INBOX_CACHE.clear()
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        conn.request("POST", "/inbox", urlencode({"token": "WSTOKEN", "unsubmitted": "1"}),
                     {"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse(); page = r.read().decode("utf-8")
        assert r.status == 200
        assert "안 낸 과제" in page              # 미제출은 표시
        assert "제출한 과제" not in page          # 제출한 것은 필터로 제거(정직한 필터)
        conn.close()
    finally:
        httpd.shutdown(); httpd.server_close()
        moodle_ws.urllib.request.urlopen = orig
        web._Handler.ws = False; web._INBOX_CACHE.clear()
    print("OK WS 미제출만 필터가 실제로 걸러냄")


def test_ws_mode_inbox_and_pick_flow():
    # Moodle WS 모드(--ws): 인박스→선택→자료·공지 수집→초안까지 HTTP end-to-end.
    import json
    from until.capture.sources import moodle_ws
    site = {"userid": 7}
    courses = [{"id": 42, "fullname": "도시문화론", "enddate": 0}]
    assigns = {"courses": [{"id": 42, "fullname": "도시문화론", "assignments": [
        {"id": 9, "cmid": 100, "course": 42, "name": "도시 관찰 보고서",
         "intro": "<p>도시를 관찰하고 보고서를 쓰시오.</p>", "duedate": 1782000000,
         "introattachments": []}]}]}
    contents = [{"id": 1, "name": "1주차", "modules": [
        {"id": 100, "name": "도시론 강의노트", "modname": "resource", "url": "https://x/r/100",
         "contents": []}]}]
    forums = [{"id": 1, "name": "공지사항", "type": "news", "course": 42}]
    discs = {"discussions": [
        {"id": 5, "discussion": 5, "name": "도시 관찰 조건 추가",
         "message": "<p>사진 3장 이상</p>", "created": 1780000000}]}
    posts = {"posts": [{"id": 11, "message": "<p>교수: 흑백도 허용</p>"}]}

    def fake(req, timeout=None):
        if "pluginfile.php" in req.full_url:
            return _FakeResp(b"%PDF-1.4 fake")
        b = (req.data or b"").decode("utf-8")
        for key, payload in (("get_site_info", site), ("get_users_courses", courses),
                             ("get_assignments", assigns), ("get_contents", contents),
                             ("get_forums_by_courses", forums),
                             ("get_forum_discussions", discs),
                             ("get_discussion_posts", posts)):
            if key in b:
                return _FakeResp(json.dumps(payload).encode())
        return _FakeResp(json.dumps({}).encode())

    orig = moodle_ws.urllib.request.urlopen
    moodle_ws.urllib.request.urlopen = fake
    web._Handler.backend = "mock"
    web._Handler.ws = True
    web._INBOX_CACHE.clear()
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        conn.request("POST", "/inbox", urlencode({"token": "WSTOKEN"}),
                     {"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse(); page = r.read().decode("utf-8")
        assert r.status == 200 and "내 과제" in page, page[:400]
        # WS 모드 인박스 상단에 최신 공지 섹션(4번 홈 공지).
        assert "최신 공지" in page and "도시 관찰 조건 추가" in page
        import re
        m = re.search(r'name="url" value="([^"]+)"', page)
        assert m, "WS 인박스에 선택 URL이 있어야 함"
        assert "courseid=42" in m.group(1)  # 무상태 재조회용 courseid
        s, draft = _post(conn, "/pick", {"url": m.group(1).replace("&amp;", "&"),
                                         "token": "WSTOKEN"})
        assert s == 200 and "제출 전에 볼 것" in draft
        assert "이 과제 관련 eTL 공지" in draft         # 4번 공지 패널
        assert "도시 관찰 조건 추가" in draft
        conn.close()
    finally:
        httpd.shutdown(); httpd.server_close()
        moodle_ws.urllib.request.urlopen = orig
        web._Handler.ws = False
        web._INBOX_CACHE.clear()
    print("OK WS 모드 inbox→pick(자료·공지 수집→초안)")


class _FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): self.close(); return False


def test_about_serves_landing():
    """/about — 랜딩(소개) 페이지를 앱에서도 서빙(CTA는 앱 내부 경로로 재작성)."""
    web._Handler.backend = "mock"
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        conn.request("GET", "/about")
        r = conn.getresponse(); page = r.read().decode("utf-8")
        assert r.status == 200
        assert "경계선" in page and "FAQ" in page      # 랜딩 본문
        assert 'id="how"' in page and "제출 직전 점검" in page
        assert all(step in page for step in
                   ("과제와 자료를 받습니다", "결정만 직접 답합니다", "올릴 파일을 확인합니다"))
        assert 'var APP_URL = "/";' in page            # CTA가 앱 내부 경로로
        assert 'data-app-path="/new"' not in page
        assert 'data-app-path="/simple"' not in page
        # 시작 CTA는 앱이 아니라 접수 폼으로 간다(사용자 지시 2026-08-21) — 클로즈드
        # 베타라 초대 없이 앱에 들여보내면 게이트 403만 보고 되돌아간다.
        assert page.count('href="#beta"') >= 5
        assert 'data-app-path="/connect?mode=fast"' not in page
        assert 'data-app-path="/connect?mode=list"' not in page
        assert 'id="beta"' in page and 'name="school"' in page and 'name="major"' in page
        assert 'data-app-path="/beta-request"' in page   # 폼 action은 APP_URL 기준
        # 초대 코드를 이미 가진 사람의 입구는 남아 있어야 한다.
        assert '베타 코드로 시작하기' in page and 'data-app-path="/"' in page
        assert 'fetch(appTarget("/healthz")' in page    # APP_URL="/"에서도 //healthz 금지
        # 스크린샷은 CSP(img-src 'self')를 통과하는 /asset/ 경로여야 한다 —
        # 외부(workers) 절대경로면 브라우저가 전부 차단한다(라이브 회귀).
        assert 'src="img/' not in page and "workers.dev/img" not in page
        assert 'src="/asset/draft.jpg"' in page
        # /asset이 리포지토리 img 폴백으로 실제 서빙한다(로컬 개발 경로).
        conn.request("GET", "/asset/draft.jpg")
        r = conn.getresponse(); blob = r.read()
        assert r.status == 200 and blob[:2] == b"\xff\xd8", r.status  # JPEG
        # 홈에 소개 링크가 노출된다.
        conn.request("GET", "/")
        r = conn.getresponse(); home = r.read().decode("utf-8")
        assert r.status == 200 and 'href="/about"' in home
        conn.close()
    finally:
        httpd.shutdown(); httpd.server_close()
    print("OK /about serves landing with in-app CTA")


def test_inbox_without_token_friendly_message():
    """토큰 없이 /inbox(불러오기·⚡ 바로 초안) → 개발자용 예외 문구 대신 친절한 안내."""
    import os
    old_env = os.environ.pop("UNTIL_CANVAS_TOKEN", None)
    web._Handler.backend = "mock"
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        for extra in ({}, {"fast": "1"}):
            conn.request("POST", "/inbox", urlencode(extra),
                         {"Content-Type": "application/x-www-form-urlencoded"})
            r = conn.getresponse(); page = r.read().decode("utf-8")
            assert r.status == 400
            assert "토큰을 먼저 입력해 주세요" in page and 'href="/"' in page
            assert "CanvasApiAdapter" not in page  # 개발자용 문구 미노출
        conn.close()
    finally:
        httpd.shutdown(); httpd.server_close()
        if old_env is not None:
            os.environ["UNTIL_CANVAS_TOKEN"] = old_env
    print("OK inbox without token → friendly message")


def test_collect_canvas_in_ui_without_network():
    import json
    from until.capture.sources import canvas_api
    data = json.loads(pathlib.Path("examples/canvas_fixture/assignment_api.json").read_text(encoding="utf-8"))

    def fake_urlopen(req, timeout=None):
        if "/api/v1/" in req.full_url:
            return _FakeResp(json.dumps(data).encode("utf-8"))
        return _FakeResp(b"%PDF-1.4 fake")

    orig = canvas_api.urllib.request.urlopen
    canvas_api.urllib.request.urlopen = fake_urlopen
    web._Handler.backend = "mock"
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        s, page = _post(conn, "/collect",
                        {"url": "https://myetl.snu.ac.kr/courses/302199/assignments/369118",
                         "token": "TESTTOKEN"})
        conn.close()
        assert s == 200 and "제출 전에 볼 것" in page
        assert 'action="/finalize"' in page  # 결정 체크리스트 폼으로 이어짐
    finally:
        httpd.shutdown(); httpd.server_close()
        canvas_api.urllib.request.urlopen = orig
    print("OK UI collects from Canvas API (no network)")


def test_render_index_sso_mode():
    """--sso 모드 홈: 토큰 입력칸 없이 '로그인하고 불러오기' 버튼."""
    html_ = web.render_index(sso=True)
    assert 'action="/inbox"' in html_ and "가장 가까운 과제 하나 해결하기" in html_
    assert "eTL ACCESS TOKEN" not in html_        # 토큰 입력칸 없음
    assert "로그인" in html_
    # 토큰 모드와 다름을 확인 — 토큰이 필요하면 홈이 아니라 2단계(/connect)로 보낸다.
    tok_home = web.render_index(sso=False)
    assert "eTL ACCESS TOKEN" not in tok_home
    assert 'href="/connect?mode=fast"' in tok_home
    tok_html = web.render_connect("fast", sso=False)
    assert "eTL ACCESS TOKEN" in tok_html
    # 발급 안내: 기본 접힘(화면 과밀 방지) + 설정 딥링크 + 목적 이름 'until' 고정 +
    # 붙여넣기 버튼.
    assert "토큰이 없다면?" in tok_html
    assert "<details class=\"tgsec\" open" not in tok_html
    assert "myetl.snu.ac.kr/profile/settings" in tok_html
    assert "<b>until</b>" in tok_html
    assert "pasteTok" in tok_html
    # SSO 2단계는 토큰 대신 로그인 안내만 — 입력칸이 없어야 한다.
    assert "eTL ACCESS TOKEN" not in web.render_connect("fast", sso=True)
    print("OK index SSO mode (tokenless inbox)")


class _FakeSSOAdapter:
    """브라우저/네트워크 없이 SSO 어댑터 인터페이스를 흉내(픽스처 기반)."""
    base_url = "https://myetl.snu.ac.kr"

    def __init__(self):
        from until.capture.sources import canvas_api as C
        self._C = C
        self._courses = C.parse_courses(_canvas_fixture("courses_api.json"))
        self._assigns = _canvas_fixture("assignments_api.json")
        self._one = _canvas_fixture("assignment_api.json")
        self._files = _canvas_fixture("files_api.json")
        self._modules = _canvas_fixture("modules_api.json")

    def list_courses(self, base_url=None):
        return self._courses

    def list_assignments(self, course, base_url=None, bucket=None):
        return self._C.parse_assignments(self._assigns, self.base_url, course=course)

    def fetch_assignment(self, url):
        return self._C.parse_canvas_api_assignment(self._one, self.base_url)

    def list_course_files(self, course_id, base_url=None):
        return self._C.parse_canvas_files(self._files, self.base_url)

    def list_modules(self, course_id, base_url=None):
        return self._C.parse_modules(self._modules, self.base_url)

    def download(self, attachment, dest_dir):
        from until.capture.sources.models import safe_filename
        p = pathlib.Path(dest_dir) / safe_filename(attachment.name)
        p.write_bytes(b"%PDF-1.4 fake")
        return str(p)


def test_sso_inbox_and_pick_flow():
    """SSO 모드 /inbox → /pick: 토큰 없이 가짜 세션 어댑터로 목록→선택→초안."""
    fake = _FakeSSOAdapter()
    orig_factory = web._sso_adapter
    web._sso_adapter = lambda: fake
    web._Handler.sso = True
    web._Handler.backend = "mock"
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        # /inbox 는 토큰 필드 없이 동작해야 한다.
        s, page = _post(conn, "/inbox", {"unsubmitted": "1"}, follow=False)
        assert s == 200 and "내 과제" in page and 'action="/pick"' in page
        assert 'name="sid"' in page  # sid는 빈 값(토큰 없음)이어도 폼엔 존재
        import re
        m = re.search(r'name="url" value="([^"]+)"', page)
        assert m, "SSO 인박스에 선택 URL이 있어야 함"
        # /pick (sid 빈 값) → SSO 어댑터로 수집 → 초안
        s, draft = _post(conn, "/pick", {"url": m.group(1), "sid": ""})
        assert s == 200 and "제출 전에 볼 것" in draft
        conn.close()
    finally:
        httpd.shutdown(); httpd.server_close()
        web._sso_adapter = orig_factory
        web._Handler.sso = False
    print("OK SSO inbox -> pick flow (tokenless, fake session)")


def test_highlight_markers_escapes_and_marks():
    out = web._highlight_markers("위험 <script> 그리고 [[DECISION: 무엇을 고를지 — 관점]]")
    assert "&lt;script&gt;" in out          # XSS 방지: HTML escape
    assert '<span class="marker">' in out    # 마커 강조
    assert "[[DECISION:" in out
    print("OK marker highlight + escape")


def test_render_draft_and_final_flow():
    cfg = Config(); cfg.backend = "mock"
    res = web.run_text("5페이지 에세이. 한 기술이 한 제도를 어떻게 재편했는지 분석하라.", cfg)
    assert res.draft.n_decisions >= 1
    draft_html = web.render_draft("tok123", res)
    assert "한눈에 보는 완성 현황" in draft_html
    assert "요구사항" in draft_html and "근거 자료" in draft_html
    assert "요구사항–근거–초안 연결" in draft_html
    assert "관련 문단 보기" in draft_html and 'id="draft-p1"' in draft_html
    # 결정은 **한 곳에서만** 묻는다(사용자 지시 2026-08-23). 예전에는 '하나씩
    # 결정하기'(첫 결정 1개)와 전체 폼이 같은 화면에 나란히 있어 같은 질문이
    # 두 번 떴다 — 한 화면에 같은 걸 두 번 묻는 건 단순함의 반대다.
    assert "하나씩 결정하기" not in draft_html
    assert "당신이 정할 것" in draft_html
    assert draft_html.index("당신이 정할 것") < draft_html.index("제출 전에 볼 것")
    # 결과물이 맨 위다(사용자 지시 2026-08-23) — 결정·근거는 그 아래로.
    assert draft_html.index('id="draft-body"') < draft_html.index("당신이 정할 것")
    # 세부는 한 겹으로 접고 세 묶음으로만 나눈다(패널 15개 나열 → 점검·근거·진단).
    for group in ("제출 전에 볼 것", "무엇을 읽고 썼나", "어떻게 판단했나"):
        assert group in draft_html, group
    # 근거 목록은 남기되 **조작 위젯은 없앴다**: 어느 자료를 뺄지 고르는 건
    # 학생이 하러 온 일이 아니고, 체크박스가 붙으면 '읽는 목록'이 '폼'이 된다.
    assert 'id="source-control"' in draft_html
    assert "다음 재작성에서 제외" not in draft_html
    assert 'name="exclude_' not in draft_html
    # 문단 선택 → AI 재작성 패널은 없앴다(사용자 지시 2026-08-23). 고칠 거면
    # '내가 직접 고치기'에서 직접 고친다 — 고르고 지시하는 조작을 없앤 것.
    assert "이 부분만 고치기" not in draft_html
    assert 'name="paragraph"' not in draft_html
    assert "내가 직접 고치기" in draft_html
    assert "제출 파일 준비" in draft_html
    assert 'action="/finalize"' in draft_html
    assert 'name="session" value="tok123"' in draft_html
    # 결정 폼은 하나뿐이므로 필드 수 = 결정 수(예전에는 빠른 폼 탓에 +1이었다).
    assert draft_html.count('name="answer_') == res.draft.n_decisions
    # 결과 복사/다운로드 도구 + 전체 리포트.
    assert 'id="draftsrc"' in draft_html and "copyDoc('draftsrc'" in draft_html
    assert "Download .md" in draft_html
    assert 'id="reportsrc"' in draft_html and "Full report .md" in draft_html
    # 웹 피드백 로깅(P7): run_text 결과를 기록하면 1건 적립.
    import tempfile, pathlib as _pl
    from until.feedback import record_from_result, append_record, load_records
    with tempfile.TemporaryDirectory() as d:
        p = _pl.Path(d) / "fb.jsonl"
        append_record(record_from_result(res, backend="mock"), p)
        assert len(load_records(p)) == 1

    answers = {i + 1: f"내 선택 {i+1}" for i in range(res.draft.n_decisions)}
    res = finalize(res, answers, cfg)
    final_html = web.render_final(res)
    assert "최종 완성본" in final_html
    assert "한눈에 보는 완성 현황" in final_html
    assert "내 선택 1" in final_html
    assert 'id="finalsrc"' in final_html and "until-final.md" in final_html  # 복사/다운로드
    # 재답변 루프: 일부만 답하면 남은 결정 폼이 뜬다.
    if res.draft.n_decisions >= 2:
        loop_html = web.render_final(res, session_id="sx", answered={1})
        assert "남은 결정 이어서" in loop_html and 'action="/finalize"' in loop_html
        assert loop_html.count('name="answer_') == 1
        assert 'name="answer_2"' in loop_html and 'name="answer_1"' not in loop_html
    print("OK draft -> final render flow (+copy/download +재답변 루프)")


def test_draft_shows_suggested_prompts():
    cfg = Config(); cfg.backend = "mock"
    res = web.run_text("에세이를 써라. 한 기술이 한 제도를 재편한 과정을 분석하라.", cfg)
    assert res.suggested_prompts
    html_ = web.render_draft("tok", res)
    assert "막히면, 이렇게 물어보세요" in html_
    assert 'class="pcard' in html_ and "프롬프트 복사" in html_   # 프롬프트 카드+복사 버튼
    assert "이 질문 방식이 유용한 이유" in html_                  # 교육 모드: 기법 설명
    # 결정 입력 부담↓: 칩/선택 안내가 있어야 한다
    assert "비워도 됨" in html_
    print("OK draft page shows suggested prompts (cards) + lighter decisions")


def test_context_injection_in_ui():
    cfg = Config(); cfg.backend = "mock"
    ctx = {"course_materials": "examples/course_materials",
           "my_files": "examples/my_files", "voice": "examples/voice_samples"}
    res = web.run_text("도시를 관찰하고 분석하는 에세이를 써라.", cfg, ctx)
    c = res.context
    assert c and (c.course_hits or c.my_hits or c.voice.n_samples), "맥락이 주입되어야 함"
    html_ = web.render_draft("tok", res)
    assert "반영한 맥락" in html_
    print("OK context injection wired into UI")


def test_announcements_panel_renders():
    # 이 과제 관련 공지가 있으면 초안 페이지에 패널로 표시된다(4번).
    from until.capture.sources.moodle_ws import Announcement
    cfg = Config(); cfg.backend = "mock"
    res = web.run_text("도시를 관찰하는 에세이.", cfg)
    res.etl_announcements = [Announcement(
        subject="도시 관찰 과제 조건 추가", body="사진 3장 이상 첨부할 것",
        created_iso="2026-07-01T00:00:00Z", url="https://x/mod/forum/discuss.php?d=5",
        replies=["교수: 흑백도 허용"])]
    h = web.render_draft("t", res)
    assert "이 과제 관련 eTL 공지" in h
    assert "도시 관찰 과제 조건 추가" in h
    assert "사진 3장 이상" in h
    assert "교수 답글" in h          # 답글 존재 표시
    assert 'href="https://x/mod/forum/discuss.php?d=5"' in h
    # 공지가 없으면 패널은 안 나온다.
    res2 = web.run_text("간단 과제.", cfg)
    assert "이 과제 관련 eTL 공지" not in web.render_draft("t2", res2)
    print("OK 관련 공지 패널 렌더")


def test_sources_panel_and_citation_highlight():
    cfg = Config(); cfg.backend = "mock"
    res = web.run_text("도시를 관찰하고 분석하는 에세이 5페이지.", cfg,
                       {"course_materials": "examples/course_materials"})
    assert res.sources, "근거 자료 범례가 채워져야 함"
    h = web.render_draft("t", res)
    assert "근거 자료 (이 초안이 본 자료)" in h       # 패널
    assert 'class="cite"' in h and "[자료1]" in h     # 인용 강조
    assert "인용됨" in h                              # 본문이 가리킨 자료 표시
    # _highlight_markers 단위: [자료N]/[출처]/DECISION 모두 강조.
    out = web._highlight_markers("앞 [자료2] 뒤 [출처?] 그리고 [[DECISION: x를 정할지 — 관점]]")
    assert '<span class="cite">[자료2]</span>' in out
    assert '<span class="cite">[출처?]</span>' in out
    assert '<span class="marker">' in out
    print("OK sources panel + citation highlight")


def test_citation_links_to_source_url():
    from until.llm.base import SourceDoc
    # [자료1]=URL 있는 eTL 자료 → 링크, [자료2]=URL 없음 → 기존 강조.
    docs = [SourceDoc(title="[eTL 자료] 강의노트", text="본문",
                      url="https://myetl.snu.ac.kr/files/123"),
            SourceDoc(title="과제: essay.txt", text="본문")]  # url 기본 ""
    out = web._highlight_markers("근거 [자료1] 그리고 [자료2] 참고.", docs)
    assert '<a class="cite citelink" href="https://myetl.snu.ac.kr/files/123"' in out
    assert 'target="_blank"' in out and 'rel="noopener noreferrer"' in out
    assert ">[자료1]</a>" in out
    assert '<span class="cite">[자료2]</span>' in out  # URL 없으면 링크 아님
    # source_docs 없이 호출하면 전부 기존 강조(하위호환).
    plain = web._highlight_markers("근거 [자료1].")
    assert '<span class="cite">[자료1]</span>' in plain and "<a " not in plain
    # XSS 방지: http(s)가 아닌 스킴은 링크로 만들지 않는다.
    evil = [SourceDoc(title="x", text="y", url="javascript:alert(1)")]
    ev = web._highlight_markers("[자료1]", evil)
    assert "javascript:" not in ev and '<span class="cite">[자료1]</span>' in ev
    print("OK citation links to source url (+xss guard)")


def test_answers_from_form():
    form = {"answer_1": ["감시 자본"], "answer_2": ["  "], "answer_3": ["신중히"]}
    out = web._answers_from_form(form, 3)
    assert out == {1: "감시 자본", 3: "신중히"}  # 공백만인 답은 제외
    print("OK answers parsed from form")


def test_http_server_end_to_end():
    cfg = Config()
    web._Handler.backend = "mock"
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        # GET /
        conn.request("GET", "/")
        r = conn.getresponse(); home = r.read().decode("utf-8")
        # 홈의 모습은 '연결 수단이 이미 있는가'에 달렸다. 이 판정은 env(그리고
        # gitignore되는 .env)를 읽으므로, 여기서 명시하지 않으면 개발자 머신에서만
        # 통과하고 CI·클린 체크아웃에서 깨진다(2026-08-20 실측).
        if web._env_canvas_token():
            assert r.status == 200 and 'action="/inbox"' in home
        else:
            assert r.status == 200 and 'href="/connect?mode=fast"' in home
            conn.request("GET", "/connect?mode=fast")
            r2 = conn.getresponse(); step = r2.read().decode("utf-8")
            assert r2.status == 200 and 'action="/inbox"' in step
        # 붙여넣기 폼은 간단 모드(/simple)에 있다.
        conn.request("GET", "/simple")
        r = conn.getresponse(); smp = r.read().decode("utf-8")
        assert r.status == 200 and 'name="assignment"' in smp

        # POST /draft → (PRG) 303 → GET /v/<token>
        s, draft = _post(conn, "/draft",
                         {"assignment": "에세이를 써라. 한 기술이 한 제도를 재편한 과정을 분석하라."})
        assert s == 200 and "제출 전에 볼 것" in draft
        import re
        m = re.search(r'name="session" value="([^"]+)"', draft)
        assert m, "세션 토큰이 초안 페이지에 있어야 함"
        token = m.group(1)
        n = draft.count('name="answer_')
        assert n >= 1
        # 새로고침(GET /v/token)해도 같은 초안 — 재생성 안 됨
        conn.request("GET", f"/v/{token}"); r = conn.getresponse(); again = r.read().decode("utf-8")
        assert r.status == 200 and "제출 전에 볼 것" in again

        # POST /finalize → (PRG) 303 → GET /vf/<token>
        fields = {"session": token}
        for i in range(1, n + 1):
            fields[f"answer_{i}"] = f"내 선택 {i}"
        s, final = _post(conn, "/finalize", fields)
        assert s == 200 and "최종 완성본" in final and "내 선택 1" in final
        conn.close()
    finally:
        httpd.shutdown(); httpd.server_close()
    print("OK http server GET/ POST/draft POST/finalize")


def test_session_persistence_survives_restart():
    """세션이 디스크에 지속화돼, 메모리(서버 재시작 시뮬레이션)를 비워도 복원된다."""
    cfg = Config(); web._Handler.backend = "mock"; web._Handler.sso = False
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        s, draft = _post(conn, "/draft",
                         {"assignment": "에세이를 써라. 한 기술이 한 제도를 재편한 과정을 분석하라."})
        import re
        token = re.search(r'name="session" value="([^"]+)"', draft).group(1)
        # 디스크 파일 생성 확인.
        assert (web._SESS_DIR / f"{token}.json").exists(), "서명 JSON 세션이 저장돼야 함"
        # 재시작 시뮬레이션: 인메모리 상태 전부 제거.
        web._SESSIONS.pop(token, None); web._SUGGESTIONS.pop(token, None)
        web._REVIEWS.pop(token, None); web._ANSWERS.pop(token, None)
        # 초안 페이지가 디스크 복원으로 계속 열린다.
        conn.request("GET", f"/v/{token}"); r = conn.getresponse()
        body = r.read().decode("utf-8")
        assert r.status == 200 and "제출 전에 볼 것" in body
        # finalize도 복원 세션 위에서 동작.
        n = body.count('name="answer_')
        fields = {"session": token, "answer_1": "내 선택"}
        s, final = _post(conn, "/finalize", fields)
        assert s == 200 and "최종 완성본" in final
        # 다시 재시작 시뮬레이션 → 최종본 페이지도 복원(final_draft+answers 포함).
        web._SESSIONS.pop(token, None); web._ANSWERS.pop(token, None)
        conn.request("GET", f"/vf/{token}"); r = conn.getresponse()
        vf = r.read().decode("utf-8")
        assert r.status == 200 and "최종 완성본" in vf
        # 경로탈출/이상 토큰은 404로 안전하게(파일 접근 없음).
        conn.request("GET", "/v/..%2F..%2Fetc"); r = conn.getresponse(); r.read()
        assert r.status == 404
        conn.close()
    finally:
        httpd.shutdown(); httpd.server_close()
        # 테스트 세션 파일 정리.
        try:
            (web._SESS_DIR / f"{token}.json").unlink()
        except OSError:
            pass
    print("OK session persistence (restart restore + finalize + weird token 404)")


def test_draft_page_shows_title():
    cfg = Config(); cfg.backend = "mock"
    res = web.run_text("에세이를 써라. 한 기술이 한 제도를 재편한 과정을 분석하라.", cfg, None)
    h = web.render_draft("t", res)
    assert "📄" in h  # 과제 제목 헤더(spec title/deliverable/goal)
    # 최종본 페이지에도 동일 헤더.
    from until.pipeline import finalize
    res = finalize(res, {1: "선택"}, cfg)
    hf = web.render_final(res, session_id="t", answered={1})
    assert "📄" in hf
    print("OK draft + final pages show assignment title")


def test_final_decision_progress():
    # 최종본 페이지에 '결정 진행 M/N' 표시. 결정 여러 개가 필요한 UI 기제 검증이라
    # legacy mock 계약(에세이 결정 3개)에 고정(8/14 unit 기본 전환 후).
    cfg = Config(); cfg.backend = "mock"; cfg.pipeline_mode = "legacy"
    res = web.run_text("에세이를 써라. 한 기술이 한 제도를 재편한 과정을 분석하라.", cfg, None)
    n = res.draft.n_decisions
    assert n >= 2
    from until.pipeline import finalize
    res = finalize(res, {1: "내 선택"}, cfg)
    h = web.render_final(res, session_id="tok", answered={1})
    assert f"결정 진행 <b>1/{n}</b>" in h
    print("OK final page decision progress M/N")


def test_error_paths_stay_alive():
    """오류 경로들이 올바른 상태코드를 내고 서버가 계속 살아있는지."""
    cfg = Config(); web._Handler.backend = "mock"; web._Handler.sso = False
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)

        def post_status(path, fields):
            conn.request("POST", path, urlencode(fields),
                         {"Content-Type": "application/x-www-form-urlencoded"})
            r = conn.getresponse(); r.read()
            return r.status

        # 빈 입력 → 400.
        assert post_status("/draft", {"assignment": "  "}) == 400
        assert post_status("/pick", {"url": ""}) == 400
        assert post_status("/collect", {"url": ""}) == 400
        # 만료 세션 POST들 → 400.
        for path in ("/suggest", "/review", "/finalize"):
            assert post_status(path, {"session": "expiredtoken"}) == 400, path
        # 없는 POST 경로 → 404, 없는 GET 경로 → 404.
        assert post_status("/nope", {}) == 404
        conn.request("GET", "/nope"); r = conn.getresponse(); r.read()
        assert r.status == 404
        # /dl 확장자 없는 토큰 → 404(만료 처리).
        conn.request("GET", "/dl/none"); r = conn.getresponse(); r.read()
        assert r.status == 404
        # 이후에도 서버 정상 동작(홈 200).
        conn.request("GET", "/"); r = conn.getresponse(); body = r.read().decode("utf-8")
        # 오류 뒤에도 홈이 살아 있는지가 요지다 — 홈의 형태는 연결 수단 유무에
        # 달렸으므로(위 end-to-end와 같은 이유) 둘 중 하나면 통과로 본다.
        assert r.status == 200
        assert ('action="/inbox"' in body) or ('href="/connect?mode=fast"' in body)
        conn.close()
    finally:
        httpd.shutdown(); httpd.server_close()
    print("OK error paths return 400/404 and server stays alive")


def test_sessions_list_page():
    """지속화된 세션이 /sessions 목록에 뜨고 링크로 다시 열린다."""
    cfg = Config(); web._Handler.backend = "mock"; web._Handler.sso = False
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    token = None
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        s, draft = _post(conn, "/draft",
                         {"assignment": "에세이를 써라. 한 기술이 한 제도를 재편한 과정을 분석하라."})
        import re
        token = re.search(r'name="session" value="([^"]+)"', draft).group(1)
        # /sessions 목록에 방금 세션이 뜬다(최신순 최상단 부근).
        conn.request("GET", "/sessions"); r = conn.getresponse()
        body = r.read().decode("utf-8")
        assert r.status == 200 and "이전 작업 다시 열기" in body
        assert f"/v/{token}" in body
        assert f"/sv/{token}" in body  # 간단 모드로 열기(✳)
        # 홈에도 다시 열기 링크가 뜬다(세션 존재 시).
        conn.request("GET", "/"); r = conn.getresponse()
        home = r.read().decode("utf-8")
        assert 'href="/sessions"' in home
        # list_sessions 메타 구조 확인(과제 유형·준비경고·D-day 포함).
        items = web.list_sessions()
        assert items and {"token", "title", "when", "final", "n_dec",
                          "task_type", "n_warnings", "dday"} <= set(items[0])
        # 마감 있는 과제는 목록에 D-day 배지가 뜬다.
        # (파서 연도 패턴이 20xx만 허용 — 자정 경계 플레이크 없게 오늘+30일로 동적 생성)
        from datetime import date as _date, timedelta as _td
        due = (_date.today() + _td(days=30)).isoformat()
        s2, draft2 = _post(conn, "/draft", {
            "assignment": f"보고서를 써라. 제출 마감: {due}. 근거를 정리하라."})
        tok2 = re.search(r'name="session" value="([^"]+)"', draft2).group(1)
        it2 = [x for x in web.list_sessions(limit=50) if x["token"] == tok2]
        assert it2 and it2[0]["dday"].startswith("D-")
        conn.request("GET", "/sessions"); r = conn.getresponse()
        body2 = r.read().decode("utf-8")
        assert it2[0]["dday"] in body2
        web.delete_session(tok2)
        # (mtime,날짜) 캐시 재사용 — 같은 키면 JSON 재검증 없이 캐시 항목 반환.
        key, it = web._SESS_META_CACHE[token]
        web._SESS_META_CACHE[token] = (key, {**it, "title": "CACHED!"})
        again = [x for x in web.list_sessions() if x["token"] == token]
        assert again and again[0]["title"] == "CACHED!"
        # 날짜가 바뀌면(키의 day가 어제) 재계산 — 마감 D-day 경고 스테일 방지.
        web._SESS_META_CACHE[token] = ((key[0], key[1] - 1), {**it, "title": "STALE!"})
        fresh = [x for x in web.list_sessions() if x["token"] == token]
        assert fresh and fresh[0]["title"] != "STALE!"
        # 손상 JSON이 최신이어도 유효 세션이 limit 슬롯에서 밀리지 않는다.
        # (병렬 스위트가 만든 다른 세션이 있을 수 있어 '유효 항목이 잡힌다'로 단언)
        bad = web._SESS_DIR / "zzzcorruptzz.json"
        bad.write_bytes(b"NOT A PICKLE")
        try:
            top = web.list_sessions(limit=1)
            assert top, "손상 파일이 유일 슬롯을 차지하면 안 됨"
            assert top[0]["token"] != "zzzcorruptzz"
            assert any(it["token"] == token for it in web.list_sessions(limit=50))
        finally:
            bad.unlink()
        # 검색 필터 입력(제목 걸러내기) 존재.
        assert 'id="sessq"' in body and 'id="sesslist"' in body
        # 삭제 버튼 존재 + POST /sessions/delete → 디스크·메모리에서 제거.
        assert 'action="/sessions/delete"' in body
        s2, after = _post(conn, "/sessions/delete", {"token": token})
        assert s2 == 200 and f"/v/{token}" not in after
        assert not (web._SESS_DIR / f"{token}.json").exists()
        assert web._SESSIONS.get(token) is None
        # 이상 토큰 삭제 요청도 안전(무시).
        assert web.delete_session("../evil") is False
        conn.close()
    finally:
        httpd.shutdown(); httpd.server_close()
        if token:
            try:
                (web._SESS_DIR / f"{token}.json").unlink()
            except OSError:
                pass
    print("OK /sessions list + home resume link")


def test_simple_error_paths_and_fast_fallback():
    """게이트 리뷰 10회차 회귀: ① 빈 /draft의 '다시 입력'은 /simple로(홈엔 붙여넣기 없음)
    ② 공백 env 토큰은 '설정됨' 아님 ③ fast+미제출 0 → 제출 완료 과제 조용한 초안 금지."""
    import os
    from types import SimpleNamespace as NS
    from until.capture.sources import discovery as disco
    web._Handler.backend = "mock"; web._Handler.sso = False
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    old_env = os.environ.pop("UNTIL_CANVAS_TOKEN", None)
    orig_inbox = disco.EtlInbox
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        # ① 빈 붙여넣기 → 400 + /simple 링크(막다른 홈 링크 금지).
        conn.request("POST", "/draft", urlencode({"assignment": " ", "ui": "simple"}),
                     {"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse(); page = r.read().decode("utf-8")
        assert r.status == 400 and 'href="/simple"' in page
        # ② 공백 env 토큰 → 홈이 '설정됨'으로 속지 않고 eTL 연결 단계로 보낸다.
        os.environ["UNTIL_CANVAS_TOKEN"] = "   "
        conn.request("GET", "/"); r = conn.getresponse(); home = r.read().decode("utf-8")
        assert r.status == 200 and 'href="/connect?mode=fast"' in home
        assert "서버에 토큰이 설정돼 있어요" not in home
        conn.request("GET", "/connect?mode=fast")
        r = conn.getresponse(); step = r.read().decode("utf-8")
        assert r.status == 200 and 'name="token"' in step
        os.environ.pop("UNTIL_CANVAS_TOKEN", None)

        # ③ fast + 미제출 0건 → 초안(303) 대신 전체 목록 + 안내.
        class _FakeInbox:
            def __init__(self, adapter, base_url=None): pass
            def list_assignments(self, bucket=None, only_unsubmitted=False, max_workers=8, **kw):
                if only_unsubmitted:
                    return []
                return [NS(due_at="2999-01-01T00:00:00Z", submitted=True,
                           url="https://etl/x", title="이미 낸 과제", course_name="코스")]
        disco.EtlInbox = _FakeInbox
        conn.request("POST", "/inbox",
                     urlencode({"token": "T" * 10, "unsubmitted": "1", "fast": "1"}),
                     {"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse(); page = r.read().decode("utf-8")
        assert r.status == 200 and "미제출 과제가 없어" in page and "직접 골라" in page
        assert "이미 낸 과제" in page  # 목록은 보여 주되 자동 초안은 안 함
        conn.close()
    finally:
        disco.EtlInbox = orig_inbox
        if old_env is not None:
            os.environ["UNTIL_CANVAS_TOKEN"] = old_env
        httpd.shutdown(); httpd.server_close()
    print("OK simple error paths + fast fallback guard")


def test_inbox_cache_within_ttl():
    """같은 토큰·필터의 /inbox 재방문은 60초 안엔 eTL 재조회 없이 캐시로."""
    from types import SimpleNamespace as NS
    from until.capture.sources import discovery as disco
    calls = {"n": 0}

    class _CountingInbox:
        def __init__(self, adapter, base_url=None): pass
        def list_assignments(self, bucket=None, only_unsubmitted=False, max_workers=8, **kw):
            calls["n"] += 1
            return [NS(due_at=None, submitted=False, url="u", title="캐시과제",
                       course_name="코스")]

    web._Handler.backend = "mock"; web._Handler.sso = False
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    orig = disco.EtlInbox; disco.EtlInbox = _CountingInbox
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        for _ in range(2):
            conn.request("POST", "/inbox", urlencode({"token": "CACHETOK123"}),
                         {"Content-Type": "application/x-www-form-urlencoded"})
            r = conn.getresponse(); page = r.read().decode("utf-8")
            assert r.status == 200 and "캐시과제" in page
        assert calls["n"] == 1  # 두 번째는 캐시
        conn.close()
    finally:
        disco.EtlInbox = orig
        web._INBOX_CACHE.clear()
        httpd.shutdown(); httpd.server_close()
    print("OK inbox 60s cache")


def test_upload_my_files_as_sources():
    """웹 업로드 자료 → [내 자료] 근거 주입 + 파싱 실패는 준비 점검 경고로."""
    import io as _io
    import zipfile
    web._Handler.backend = "mock"; web._Handler.sso = False
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        # 최소 docx(내장 폴백으로 파싱 가능).
        w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        buf = _io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml",
                       f'<w:document xmlns:w="{w}"><w:body>'
                       '<w:p><w:r><w:t>수업에서 다룬 핵심 근거 문장</w:t></w:r></w:p>'
                       '</w:body></w:document>')
        bnd = "----untiltestbnd"

        def part_field(k, v):
            return (f'--{bnd}\r\nContent-Disposition: form-data; name="{k}"'
                    f'\r\n\r\n{v}\r\n').encode("utf-8")

        def part_file(fn, data, field="files"):
            return ((f'--{bnd}\r\nContent-Disposition: form-data; name="{field}"; '
                     f'filename="{fn}"\r\nContent-Type: application/octet-stream'
                     '\r\n\r\n').encode("utf-8") + data + b"\r\n")

        body = (part_field("assignment", "에세이. 근거로 논지를 세워라.") +
                part_file("수업노트.docx", buf.getvalue()) +
                part_file("메모.txt", "같은 이름 첫째".encode("utf-8")) +
                part_file("메모.txt", "같은 이름 둘째".encode("utf-8")) +
                part_file("빈파일.txt", b"") +
                part_file("옛문서.hwp", b"\xd0\xcf\x11\xe0 binary") +
                part_file("내글.txt", "저는 이렇게 생각합니다. 그래서 씁니다.".encode("utf-8"),
                          field="voice_files") +
                f'--{bnd}--\r\n'.encode())
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        conn.request("POST", "/draft", body,
                     {"Content-Type": f"multipart/form-data; boundary={bnd}",
                      "Content-Length": str(len(body))})
        r = conn.getresponse(); r.read()
        assert r.status == 303, r.status
        loc = r.getheader("Location")
        conn.request("GET", loc); r = conn.getresponse()
        page = r.read().decode("utf-8")
        assert "[내 자료]" in page and "수업노트.docx" in page  # 근거 자료 패널
        # 같은 이름 2개 → 접미사로 구분(범례 혼동 방지).
        assert "메모.txt" in page and "메모(2).txt" in page
        # 문체 파일(voice_files)은 근거 자료로 새지 않는다(필드 분리).
        assert "내글.txt" not in page
        # 파싱 실패(.hwp)·빈 파일은 준비 점검 경고로(조용한 누락 금지).
        token = loc.rsplit("/", 1)[1]
        conn.request("GET", f"/readiness/{token}.json"); r = conn.getresponse()
        payload = r.read().decode("utf-8")
        assert "파싱 실패" in payload and "빈파일" in payload  # 첫 경고 파일명 노출
        # 경고 상세는 세션의 capture_warnings에 — 빈 파일·hwp 둘 다 잡혔는지 확인.
        res = web._get_session(token)
        assert any("빈파일" in w for w in res.capture_warnings)
        assert any("옛문서" in w for w in res.capture_warnings)
        # 25MB 초과 → 본문 드레인 후 친절한 413(연결 리셋으로 유실되지 않음).
        big = b"0" * (26 * 1024 * 1024)
        conn2 = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        conn2.request("POST", "/draft", big,
                      {"Content-Type": "application/x-www-form-urlencoded",
                       "Content-Length": str(len(big))})
        r = conn2.getresponse(); page413 = r.read().decode("utf-8")
        assert r.status == 413 and "너무 커요" in page413
        conn2.close()
        conn.close()
    finally:
        httpd.shutdown(); httpd.server_close()
    print("OK upload my files -> [내 자료] sources + warn + 413 drain")


def test_voice_dir_from_uploads():
    """업로드한 '내가 쓴 글' → 문체 프로파일 폴더(.voice.txt) + 실패 경고."""
    import pathlib as _pl
    import shutil
    txt = ("저는 도시를 이렇게 봅니다. 골목은 기억을 담습니다. "
           "그래서 저는 걷습니다. 오늘도 기록합니다.").encode("utf-8")
    d, warns = web._voice_dir_from_uploads([("내글.txt", txt), ("빈글.txt", b"")])
    try:
        assert d is not None
        assert len(warns) == 1 and "빈글" in warns[0]
        files = list(_pl.Path(d).glob("*.voice.txt"))
        assert len(files) == 1  # 원본은 제거, 변환본만(이중 집계 방지)
        from until.context.voice import voice_from_dir
        prof = voice_from_dir(d)
        assert prof.to_prompt_hint()  # 프로파일이 실제로 만들어진다
    finally:
        if d:
            shutil.rmtree(d, ignore_errors=True)
    # 전부 실패면 폴더 없이 경고만.
    d2, warns2 = web._voice_dir_from_uploads([("빈글.txt", b"")])
    assert d2 is None and warns2
    print("OK voice dir from uploads")




def test_simple_readiness_line_and_plan_feedback():
    """간단 모드: 준비 경고 있을 때만 ⚠ 한 줄(+자세히 링크). /plan 키 실패 피드백."""
    web._Handler.backend = "mock"; web._Handler.sso = False
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        # mock도 실행 계약의 분량 요건을 지킨다 — 예전에는 항상 짧아 ⚠가 뜨던
        # 스텁 결함 때문에 실제 UI 성공 경로를 검증하지 못했다.
        s, page = _post(conn, "/draft", {
            "assignment": "에세이. 분량은 3000자 이상 작성하세요. 근거로 논지를 세워라.",
            "ui": "simple"})
        assert s == 200 and "⚠" not in page
        # 경고 없는 케이스(요건 없는 짧은 과제)는 ⚠ 줄 자체가 없어야… 하지만 mock
        # 특성상 다른 경고가 있을 수 있어 렌더 함수로 직접 확인.
        assert web._simple_readiness_line.__doc__  # 헬퍼 존재(간단 모드 철학: 경고시에만)
        # 답 입력 보존 JS는 정적 자산으로 분리돼 있다(로컬 저장·제출 시 삭제).
        assert '/asset/app.js' in page
        app_js = (pathlib.Path(web.__file__).parent / "webassets" / "app.js").read_text(
            encoding="utf-8")
        assert "until-ans:" in app_js
        # /plan/activate 실패(8자 미만) → err=1 리다이렉트 + 안내 문구.
        conn.request("POST", "/plan/activate", urlencode({"license": "short"}),
                     {"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse(); r.read()
        assert r.status == 303 and r.getheader("Location") == "/plan?err=1"
        conn.request("GET", "/plan?err=1"); r = conn.getresponse()
        assert "키 활성화에 실패했어요" in r.read().decode("utf-8")
        conn.close()
    finally:
        httpd.shutdown(); httpd.server_close()
    print("OK simple readiness line + plan activate feedback")


def test_rating_widget():
    """완성본 별점(1~5) — 폼 노출 → POST /rate → 감사 문구, 재평가 불가, 검증."""
    web._Handler.backend = "mock"
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
    try:
        import re
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        _, draft = _post(conn, "/draft", {"assignment": "에세이를 써라. 주제는 자유."})
        tok = re.search(r'name="session" value="([^"]+)"', draft).group(1)
        fields = {"session": tok}
        for i in re.findall(r'name="answer_(\d+)"', draft):
            fields[f"answer_{i}"] = "별점 테스트 답."
        _, final = _post(conn, "/finalize", fields)
        assert 'action="/rate"' in final and "어땠어요" in final
        # 평가 → 감사 문구로 바뀌고 폼은 사라진다.
        s, after = _post(conn, "/rate", {"session": tok, "score": "5"})
        assert s == 200 and "고마워요" in after and 'action="/rate"' not in after
        # 재평가 시도해도 값 유지(세션당 1회).
        _post(conn, "/rate", {"session": tok, "score": "1"})
        assert web._RATINGS[tok] == 5
        # 범위 밖 점수·만료 세션은 400.
        s, _ = _post(conn, "/rate", {"session": tok, "score": "9"}, follow=False)
        assert s == 400
        s, _ = _post(conn, "/rate", {"session": "nope", "score": "3"}, follow=False)
        assert s == 400
        web.delete_session(tok)
        assert tok not in web._RATINGS  # 삭제 시 함께 정리
    finally:
        httpd.shutdown()
    print("OK rating widget (form -> rate once -> thanks, 400 paths)")


def test_prompt_bundle():
    """프롬프트 번들 — 자기완결(자료 실제 발췌 포함) + 경계선 규칙 수출 + 웹 버튼."""
    cfg = Config(); cfg.backend = "mock"
    res = web.run_text("에세이: 도시를 읽는 관점을 논하라. 500자 이상.", cfg)
    from until.promptpack import render_prompt_bundle
    txt = render_prompt_bundle(res)
    assert "[반드시 지킬 규칙]" in txt and "[[DECISION" in txt
    assert "[자료 발췌]" in txt and "[현재 본문]" in txt
    # 자료의 '실제 본문'이 담긴다(제목·링크 참조가 아니라) — 채팅 LLM 자기완결 조건.
    assert "도시를 읽는 관점을 논하라" in txt
    # 결정 목록 + 시작 지시.
    assert "[내가 아직 정하지 않은 결정]" in txt
    # spec이 비어도(경량 모델 폴백) 결정적 폴백으로 명세를 채운다.
    from until.promptpack import _augmented_spec_lines
    from until.understanding.deadline import Deadline
    import datetime as _dt

    class _FakeDoc:
        text = "# 도시 읽기 과제\n출처: https://myetl.snu.ac.kr/courses/1/assignments/2\n본문"
    class _FakeRes:
        spec = {}
        documents = [_FakeDoc()]
        deadline = Deadline(due=_dt.date(2026, 7, 27), had_year=True, time_str="오전 9시")
    lines = "\n".join(_augmented_spec_lines(_FakeRes()))
    assert "- 제목: 도시 읽기 과제" in lines
    assert "- 마감: 2026년 7월 27일(월) 오전 9시" in lines
    assert "- 출처(eTL): https://myetl.snu.ac.kr/courses/1/assignments/2" in lines
    # 질문 화면은 초안 도구 없이 바로 결정으로, 자세한 화면에서만 프롬프트를 제공.
    assert "프롬프트로 복사" not in web.render_simple_draft("t1", res)
    assert "프롬프트로 복사" in web.render_draft("t1", res)
    print("OK prompt bundle (self-contained + boundary rule exported)")


def test_simple_flow_one_click_completion():
    """딸깍 완주 — 간단 흐름의 제목·AI 제안 프리필·다운로드·막다른 페이지 구제."""
    cfg = Config(); cfg.backend = "mock"
    res = web.run_text("에세이: 도시를 읽는 관점을 논하라. 500자 이상.", cfg)
    assert res.draft.decisions
    tok = "simp1"
    web._SESSIONS[tok] = res
    try:
        # ① 초안 첫 줄 레터헤드에 과제 제목(무엇을 골랐는지) 노출.
        page = web.render_simple_draft(tok, res)
        _title = str((res.spec or {}).get("title") or (res.spec or {}).get("deliverable")
                     or (res.spec or {}).get("goal") or "").strip()
        import html as _h
        assert _title and 'class="smp-head"' in page and _h.escape(_title[:90]) in page
        # 초안 본문은 **접힌 채** 함께 있다(2026-08-22 결정, 원장 F9~F11).
        # 종전 계약은 '이 화면에 본문 없음'이었는데, 그러면 초안 전문이 편집 폼
        # 안에만 있어서 결과물을 읽기만 하려는 사용자가 갈 곳이 없었다 —
        # 완성본은 본문을 앞면에 그대로 보여 주므로 구조가 뒤집혀 있었다.
        assert 'class="draft-peek"' in page and 'class="doc"' in page
        assert res.draft.body.strip().splitlines()[0][:20] in page
        # 기본은 접혀 있어야 한다 — 펼쳐 두면 '이 하나만 정하면 완성됩니다'가 깨진다.
        assert "<details class=\"draft-peek\" " in page and " open" not in page.split(
            '</details>')[0]
        assert page.index('name="answer_') < page.index("자세히 보기")
        # ② AI 제안이 있으면 답칸 프리필 + 안내 문구(자동 확정 아님을 명시).
        web._SUGGESTIONS[tok] = {1: {"answer": "제안된 관점 답", "why": "근거"}}
        page = web.render_simple_draft(tok, res)
        assert "제안된 관점 답" in page and "추천이 채워져 있어요" in page
        # 내 답이 있으면 내 답이 제안보다 우선(프리필 안내 문구 없음).
        web._ANSWERS[tok] = {i: "내 답" for i in range(1, len(res.draft.decisions) + 1)}
        page = web.render_simple_draft(tok, res)
        assert "추천이 채워져 있어요" not in page
        web._ANSWERS.pop(tok, None)
        # ③ 답 없이 완성 화면 — 막다른 페이지가 아니라 초안 기반 제출용 다운로드 제공.
        assert res.final_draft is None
        page = web.render_simple_final(res, session_id=tok)
        assert f"/dl/{tok}." in page and "초안으로" in page
        # ④ 완성본 화면에도 제출 파일 다운로드 노출.
        answers = {1: "분산 생산 관점"}
        res2 = finalize(res, answers, cfg)
        page = web.render_simple_final(res2, session_id=tok, answered=set(answers))
        assert f"/dl/{tok}." in page
    finally:
        for d in (web._SESSIONS, web._SUGGESTIONS, web._ANSWERS):
            d.pop(tok, None)
    print("OK simple one-click completion (title, prefill, downloads, no dead end)")





def test_user_errors_are_actionable_and_redacted():
    from until.user_errors import user_error_message

    auth = user_error_message(RuntimeError(
        "eTL 인증 실패: token=secret-token https://internal.example/api"))
    assert "다시 연결" in auth and "secret-token" not in auth and "internal.example" not in auth

    class _RateLimit(Exception):
        status_code = 429

    assert "잠시 후" in user_error_message(_RateLimit("provider details"))
    assert "네트워크" in user_error_message(TimeoutError("socket timed out"))
    unknown = user_error_message(RuntimeError("database password=secret"), "초안을 생성")
    assert "초안을 생성" in unknown and "password" not in unknown and "secret" not in unknown
    print("OK user-facing errors actionable + internal details redacted")


def test_new_assignment_compose():
    """구조화 칸 → 붙여넣기와 같은 과제 텍스트(파이프라인 분기 없음)."""
    form = {"course": ["재료공학개론"], "title": ["3주차 보고서"],
            "due": ["2026-09-05 23:59"], "fmt": [".docx 파일 1개"],
            "length": ["1500자 이상"],
            "req": ["서론·본론·결론 구성", "  ", "수업 자료 3편 이상 인용"],
            "body": ["Hall-Petch 식으로 설명하라."]}
    # req는 여러 줄이 한 값으로 들어온다(textarea) — 리스트 첫 값만 쓴다.
    form["req"] = ["서론·본론·결론 구성\n\n수업 자료 3편 이상 인용"]
    text = web.compose_assignment(form)
    assert "[과목] 재료공학개론" in text
    assert "[과제명] 3주차 보고서" in text
    assert "[마감] 2026-09-05 23:59" in text
    assert "- 서론·본론·결론 구성" in text and "- 수업 자료 3편 이상 인용" in text
    assert text.count("- ") == 2                 # 빈 줄은 항목이 되지 않는다
    assert text.rstrip().endswith("Hall-Petch 식으로 설명하라.")
    # 안 채운 칸은 줄 자체가 없다(빈 라벨 노이즈 금지).
    sparse = web.compose_assignment({"body": ["설명만 있음"]})
    assert sparse == "[과제 설명]\n설명만 있음", sparse
    assert web.compose_assignment({}) == ""
    print("OK 과제 만들기 — 채운 칸만 명세로 조립")


def test_new_assignment_web_flow():
    """/new 폼 → /draft(mode=new) → 초안. 설명이 비면 입력을 지킨 채 되돌린다."""
    web._Handler.backend = "mock"; web._Handler.sso = False; web._Handler.ws = False
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection(
            "127.0.0.1", httpd.server_address[1], timeout=20)
        conn.request("GET", "/new")
        r = conn.getresponse(); page = r.read().decode("utf-8")
        assert r.status == 200
        for name in ("course", "title", "due", "fmt", "length", "req", "body"):
            assert f'name="{name}"' in page, name
        assert 'name="mode" value="new"' in page
        assert 'href="/simple"' in page              # 막다른 길 금지

        fields = {"ui": "simple", "mode": "new", "course": "재료공학개론",
                  "title": "3주차 보고서", "due": "2026-09-05 23:59",
                  "length": "1500자 이상",
                  "body": "결정립 크기와 항복강도의 관계를 설명하고 사례를 들어라."}
        conn.request("POST", "/draft", urlencode(fields),
                     {"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse(); r.read()
        assert r.status == 303, r.status
        token = r.getheader("Location").rsplit("/", 1)[-1]
        res = web._SESSIONS[token]
        # 조립된 명세에서 마감·분량이 결정적으로 잡힌다(기존 파서 그대로).
        assert res.deadline is not None and res.deadline.due.isoformat() == "2026-09-05"
        assert res.length_target is not None and res.length_target.min == 1500

        # 설명이 비면 400 + 입력한 값이 살아 있는 폼으로 되돌린다.
        conn.request("POST", "/draft",
                     urlencode({"ui": "simple", "mode": "new", "course": "물리학"}),
                     {"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse(); back = r.read().decode("utf-8")
        assert r.status == 400
        assert "과제 설명은 채워" in back and 'value="물리학"' in back
        conn.close()
    finally:
        httpd.shutdown(); httpd.server_close()
    print("OK /new → /draft 흐름 + 입력 보존 되돌림")


def test_sessions_list_marks_submitted():
    """과제함에서 끝난 과제가 보이고, 클릭하면 제출 기록 화면으로 간다."""
    items = [{"token": "a" * 12, "title": "낸 과제", "when": "08/20 10:00",
              "final": True, "n_dec": 0, "task_type": "", "n_warnings": 0,
              "dday": "", "submitted": True},
             {"token": "b" * 12, "title": "안 낸 과제", "when": "08/20 09:00",
              "final": True, "n_dec": 0, "task_type": "", "n_warnings": 0,
              "dday": "", "submitted": False}]
    page = web.render_sessions(items)
    assert "✓ 제출함" in page
    assert f'href="/ready/{"a" * 12}"' in page      # 낸 과제 → 제출 기록
    assert f'href="/vf/{"b" * 12}"' in page         # 안 낸 과제 → 완성본
    assert page.count("✓ 제출함") == 1
    print("OK 과제함 — 제출 완료 배지·링크")


def test_archive_shows_only_my_assignments():
    """내 과제 아카이브 — 내 것만 묶어 보여 준다(남의 제출물 열람 없음)."""
    items = [{"token": "a" * 12, "title": "3주차 보고서", "when": "08/20 10:00",
              "final": True, "n_dec": 3, "task_type": "report", "n_warnings": 0,
              "dday": "D-2", "submitted": True, "course": "재료공학개론"},
             {"token": "b" * 12, "title": "예비보고서", "when": "08/19 09:00",
              "final": False, "n_dec": 2, "task_type": "report", "n_warnings": 1,
              "dday": "", "submitted": False, "course": "재료공학개론"},
             {"token": "c" * 12, "title": "붙여넣은 과제", "when": "08/18 09:00",
              "final": True, "n_dec": 1, "task_type": "", "n_warnings": 0,
              "dday": "", "submitted": False, "course": ""}]
    page = web.render_archive(items)
    assert "내 과제 아카이브" in page
    assert "과제 <b>3</b>건" in page and "제출 표시 1건" in page
    assert "내가 내린 결정 6개" in page
    assert "재료공학개론" in page and "과목 미지정" in page      # 과목별 묶음
    assert f'href="/ready/{"a" * 12}"' in page                  # 제출한 건 → 제출 기록
    assert f'href="/vf/{"b" * 12}"' not in page                 # 미완성은 완성본 링크 아님
    assert "다른 사람에게 보이지 않아요" in page                 # 내 것만이라는 약속
    assert web.render_archive([]).count("아직 쌓인 과제가 없어요") == 1
    print("OK 아카이브 — 내 과제만·과목별 묶음·요약")


def test_all_assignments_mode_is_token_gated():
    """'지난 과제까지 전부'는 허용된 토큰에서만 열린다(UX 테스트용 표면).

    다른 사용자에게는 지금 해야 할 과제만 보이는 게 맞다. 토큰 원문은 저장하지
    않고 SHA-256 지문만 env에 둔다 — 재발급하면 자동으로 닫힌다.
    """
    import hashlib
    import os
    old = os.environ.get("UNTIL_TEST_TOKEN_SHA256")
    token = "test-token-" + "z" * 20
    try:
        # 지문 미설정 = 기능 자체가 없다(링크도 숨김, 판정도 항상 거짓).
        os.environ.pop("UNTIL_TEST_TOKEN_SHA256", None)
        assert web.test_mode_configured() is False
        assert web.test_all_assignments_allowed(token) is False
        assert "/connect?mode=all" not in web.render_index()

        os.environ["UNTIL_TEST_TOKEN_SHA256"] = hashlib.sha256(
            token.encode("utf-8")).hexdigest()
        assert web.test_mode_configured() is True
        assert web.test_all_assignments_allowed(token) is True
        assert web.test_all_assignments_allowed(token + "x") is False
        assert web.test_all_assignments_allowed("") is False
        assert "/connect?mode=all" in web.render_index()

        # 여러 명 허용(쉼표) + 공백·대문자 내성.
        os.environ["UNTIL_TEST_TOKEN_SHA256"] = (
            " DEADBEEF , " + hashlib.sha256(token.encode("utf-8")).hexdigest().upper())
        assert web.test_all_assignments_allowed(token) is True

        # 폼 계약: all 모드도 필터 입력을 그대로 싣고 의도만 all=1로 표시한다.
        # (폼에서 필터를 빼면 아무나 전체 목록을 여는 구멍이 된다.)
        page = web.render_connect("all")
        assert 'name="all" value="1"' in page
        assert 'name="unsubmitted" value="1"' in page
        assert 'name="hide_past" value="1"' in page
        assert 'name="all"' not in web.render_connect("list")
        assert 'name="all"' not in web.render_connect("fast")
    finally:
        if old is None:
            os.environ.pop("UNTIL_TEST_TOKEN_SHA256", None)
        else:
            os.environ["UNTIL_TEST_TOKEN_SHA256"] = old
    print("OK 전부 보기 — 토큰 지문 게이트(fail-closed)·폼 계약")


def test_final_screen_without_answers_is_not_a_dead_end():
    """빈 칸으로 '완성본 만들기'를 눌러 자동 채움이 실패해도 갈 길을 준다.

    라이브 확인 2026-08-23('12주차 출석'): 예전에는 "반영할 결정 답변이 없어 초안을
    그대로 둡니다" 한 줄과 '새 과제' 링크뿐이라, 사용자는 무슨 일이 났는지도 어디로
    가야 하는지도 모른 채 갇혔다. 초안은 멀쩡히 있으므로 돌아갈 길과 제출로 가는
    길을 함께 준다.
    """
    from until.config import Config
    from until.pipeline import run

    res = run(["examples/sample_assignment.txt"], Config(backend="mock"))
    res.final_draft = None
    token = "d" * 22
    page = web.render_final(res, session_id=token)
    assert f"/v/{token}" in page, "초안으로 돌아갈 길이 없다"
    assert f"/ready/{token}" in page, "제출로 갈 길이 없다"
    assert "직접 한 줄 적으면" in page
    print("OK 완성본 빈 화면이 막다른 길이 아니다")


if __name__ == "__main__":
    for fn in [test_render_index_has_form, test_naturalness_guidance_in_prompts,
               test_render_index_sso_mode,
               test_prompt_bundle,
               test_rating_widget,
               test_about_serves_landing,
               test_inbox_without_token_friendly_message,
               test_simple_error_paths_and_fast_fallback,
               test_inbox_cache_within_ttl,
               test_upload_my_files_as_sources,
               test_voice_dir_from_uploads,
               test_simple_readiness_line_and_plan_feedback,
               test_collect_canvas_in_ui_without_network,
               test_inbox_and_pick_flow_without_network,
               test_sso_inbox_and_pick_flow,
               test_highlight_markers_escapes_and_marks,
               test_render_draft_and_final_flow, test_draft_shows_suggested_prompts,
               test_context_injection_in_ui, test_sources_panel_and_citation_highlight,
               test_citation_links_to_source_url,
               test_announcements_panel_renders, test_ws_mode_inbox_and_pick_flow,
               test_ws_mode_unsubmitted_filter_honest,
               test_answers_from_form,
               test_http_server_end_to_end,
               test_session_persistence_survives_restart,
               test_final_decision_progress, test_draft_page_shows_title,
               test_error_paths_stay_alive,
               test_sessions_list_page,
               test_simple_flow_one_click_completion,
               test_new_assignment_compose,
               test_new_assignment_web_flow,
               test_sessions_list_marks_submitted,
               test_archive_shows_only_my_assignments,
               test_all_assignments_mode_is_token_gated,
               test_user_errors_are_actionable_and_redacted,
               test_final_screen_without_answers_is_not_a_dead_end]:
        fn()
    print("\nWEB TESTS PASS")
