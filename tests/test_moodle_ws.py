"""Moodle WS 읽기 전용 클라이언트 테스트 — allowlist 강제(쓰기 함수 코드 차단).

핵심 검증: 쓰기 함수는 네트워크로 나가기 전에 막힌다(요청이 생성조차 안 됨).
네트워크 불필요 — urlopen을 가짜로 대체."""
import io
import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.capture.sources import moodle_ws
from until.capture.sources.moodle_ws import (
    MoodleWsClient, MoodleWsAdapter, WriteFunctionBlocked, assert_read_only,
    READ_ALLOWLIST, WRITE_DENYLIST, check_ws_error, activated_functions,
    allowed_activated, blocked_activated, ws_endpoint, _flatten_params,
    parse_ws_courses, parse_ws_assignments, parse_course_contents,
    assignment_from_ws, html_to_text, with_token,
)

_BASE = "https://myetl.snu.ac.kr"


def test_allowlist_denylist_disjoint():
    # 읽기 allowlist와 쓰기 denylist는 절대 겹치면 안 된다(모순 설정).
    assert not (READ_ALLOWLIST & WRITE_DENYLIST)
    # 팀원이 명시한 금지 쓰기 함수 8종이 전부 denylist에 있다.
    must_block = {
        "mod_assign_save_submission", "mod_assign_submit_for_grading",
        "mod_quiz_start_attempt", "mod_quiz_process_attempt", "mod_quiz_finish_attempt",
        "mod_forum_add_discussion", "mod_forum_add_discussion_post",
        "core_message_send_instant_messages",
    }
    assert must_block <= WRITE_DENYLIST
    print("OK allowlist/denylist disjoint + 금지 8종 포함")


def test_assert_read_only_blocks_writes():
    for fn in WRITE_DENYLIST:
        try:
            assert_read_only(fn)
        except WriteFunctionBlocked as e:
            assert "쓰기" in str(e) or "읽기 전용" in str(e)
        else:
            raise AssertionError(f"{fn} 은 쓰기 함수라 막혀야 함")
    print("OK assert_read_only가 쓰기 함수 전부 차단")


def test_assert_read_only_blocks_unknown():
    # allowlist에 없는 임의 함수도 거부(허용 목록 방식).
    try:
        assert_read_only("some_random_unlisted_function")
    except WriteFunctionBlocked as e:
        assert "allowlist" in str(e)
    else:
        raise AssertionError("미등록 함수는 막혀야 함")
    print("OK assert_read_only가 미등록 함수 차단")


def test_assert_read_only_allows_reads():
    for fn in READ_ALLOWLIST:
        assert_read_only(fn)  # 예외 없이 통과해야 함
    print(f"OK 읽기 allowlist {len(READ_ALLOWLIST)}종 통과")


def _fake_urlopen(payload, capture=None):
    """urlopen 대체 — 요청 바디를 capture 리스트에 적재하고 payload를 돌려준다."""
    class _Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): self.close(); return False

    def fake(req, timeout=None):
        if capture is not None:
            capture.append(req)
        return _Resp(json.dumps(payload).encode("utf-8"))
    return fake


def test_write_call_makes_no_request():
    # 가장 중요한 계약: 쓰기 함수 호출은 네트워크 요청을 만들지 않는다.
    calls = []
    orig = moodle_ws.urllib.request.urlopen
    moodle_ws.urllib.request.urlopen = _fake_urlopen({"ok": 1}, capture=calls)
    try:
        c = MoodleWsClient(_BASE, token="TESTTOKEN")
        for fn in ("mod_assign_submit_for_grading", "mod_quiz_finish_attempt",
                   "core_message_send_instant_messages"):
            try:
                c.call(fn, assignmentid=1)
            except WriteFunctionBlocked:
                pass
            else:
                raise AssertionError(f"{fn} 은 막혀야 함")
        assert calls == [], "쓰기 함수 시도로 요청이 나가면 안 됨"
    finally:
        moodle_ws.urllib.request.urlopen = orig
    print("OK 쓰기 함수 호출은 요청을 만들지 않음")


def test_read_call_sends_token_in_body_not_url():
    # 토큰은 POST 바디로만 나간다(URL 쿼리에 노출 금지).
    calls = []
    orig = moodle_ws.urllib.request.urlopen
    moodle_ws.urllib.request.urlopen = _fake_urlopen([{"id": 1}], capture=calls)
    try:
        c = MoodleWsClient(_BASE, token="SECRET123")
        out = c.call("core_course_get_contents", courseid=42)
        assert out == [{"id": 1}]
        assert len(calls) == 1
        req = calls[0]
        assert req.full_url == ws_endpoint(_BASE)
        assert "SECRET123" not in req.full_url  # URL에 토큰 없음
        body = req.data.decode("utf-8")
        assert "wstoken=SECRET123" in body
        assert "wsfunction=core_course_get_contents" in body
        assert "courseid=42" in body
    finally:
        moodle_ws.urllib.request.urlopen = orig
    print("OK 읽기 호출 — 토큰은 바디로, URL엔 없음")


def test_ws_error_becomes_clear_message():
    # Moodle은 오류도 HTTP 200 + {exception,...}로 준다 → 사람 메시지.
    try:
        check_ws_error({"exception": "webservice_access_exception",
                        "errorcode": "invalidtoken", "message": "Invalid token"})
    except RuntimeError as e:
        assert "인증 실패" in str(e)
    else:
        raise AssertionError("invalidtoken은 인증 실패 메시지여야 함")
    # 정상 응답(list/예외키 없는 dict)은 통과.
    check_ws_error([{"id": 1}])
    check_ws_error({"sitename": "SNU"})
    print("OK WS 오류 → 사람이 읽는 메시지")


def test_flatten_params_php_style():
    # Moodle 배열/구조 파라미터의 PHP 스타일 인코딩.
    got = dict(_flatten_params({"courseids": [5, 7]}))
    assert got == {"courseids[0]": "5", "courseids[1]": "7"}
    got2 = dict(_flatten_params({"options": {"userid": 3}}))
    assert got2 == {"options[userid]": "3"}
    assert dict(_flatten_params({"flag": True})) == {"flag": "1"}
    print("OK PHP 스타일 배열 파라미터 인코딩")


def test_site_info_function_inventory():
    # get_site_info로 활성 함수 지형을 조사하고, until이 쓸/안 쓸 함수를 분리한다.
    payload = {"sitename": "SNU eTL", "functions": [
        {"name": "core_course_get_contents", "version": "1"},
        {"name": "mod_assign_get_assignments", "version": "1"},
        {"name": "mod_assign_submit_for_grading", "version": "1"},  # 활성이지만 안 씀
        {"name": "some_other_function", "version": "1"},            # allowlist 밖
    ]}
    orig = moodle_ws.urllib.request.urlopen
    moodle_ws.urllib.request.urlopen = _fake_urlopen(payload)
    try:
        info = MoodleWsClient(_BASE, token="T").get_site_info()
        assert info.get("sitename") == "SNU eTL"
        assert set(activated_functions(info)) == {
            "core_course_get_contents", "mod_assign_get_assignments",
            "mod_assign_submit_for_grading", "some_other_function"}
        assert set(allowed_activated(info)) == {
            "core_course_get_contents", "mod_assign_get_assignments"}
        assert blocked_activated(info) == ["mod_assign_submit_for_grading"]
    finally:
        moodle_ws.urllib.request.urlopen = orig
    print("OK site_info 함수 지형 조사 + 사용/미사용 분리")


def test_print_site_inventory(capsys=None):
    # 함수 지형 조사 CLI — 활성/사용/미사용 분리 출력(팀 요청 '최초 1회 확인').
    import io as _io
    from contextlib import redirect_stdout
    payload = {"sitename": "SNU eTL", "userid": 7, "functions": [
        {"name": "core_course_get_contents"},
        {"name": "mod_assign_submit_for_grading"},  # 활성 쓰기(미사용)
        {"name": "some_other_fn"},
    ]}
    orig = moodle_ws.urllib.request.urlopen
    moodle_ws.urllib.request.urlopen = _fake_urlopen(payload)
    buf = _io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = moodle_ws.print_site_inventory(_BASE, token="T")
    finally:
        moodle_ws.urllib.request.urlopen = orig
    out = buf.getvalue()
    assert rc == 0
    assert "core_course_get_contents" in out          # 사용 가능 읽기
    assert "mod_assign_submit_for_grading" in out      # 미사용 쓰기로 분류
    assert "영구 미사용" in out
    print("OK 함수 지형 조사 출력")


def test_requires_token():
    import os
    saved = {k: os.environ.pop(k, None) for k in ("UNTIL_ETL_WS_TOKEN", "UNTIL_CANVAS_TOKEN")}
    try:
        MoodleWsClient(_BASE, token="")
    except ValueError as e:
        assert "토큰" in str(e)
    else:
        raise AssertionError("토큰 없으면 ValueError")
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
    print("OK 토큰 필수")


# ─────────────────────────────────────────────────────────────────────────────
# 파서(2번 추출) — 네트워크 없이 결정적
# ─────────────────────────────────────────────────────────────────────────────
def test_parse_ws_courses_filters_ended():
    import time
    now = int(time.time())
    data = [
        {"id": 1, "fullname": "도시문화론", "enddate": 0},                    # 진행(종료일 없음)
        {"id": 2, "fullname": "지난과목", "enddate": now - 86400},            # 지난(종료일 경과)
        {"id": 3, "shortname": "SHORT", "enddate": now + 86400 * 30},         # 진행(미래 종료)
        {"id": 4, "fullname": "", "enddate": 0},                             # 이름 없음 → 제외
        "not a dict",                                                        # 방어
    ]
    courses = parse_ws_courses(data)
    assert [c.id for c in courses] == ["1", "3"]
    assert courses[0].name == "도시문화론"
    print("OK parse_ws_courses (지난/이름없음 제외)")


def test_unix_time_guards():
    # 비정상 값(거대·문자열·None)이 와도 크래시 없이 ''로(라이브 데이터 방어).
    from until.capture.sources.moodle_ws import _iso_from_unix, _readable_unix
    assert _iso_from_unix(None) == "" and _iso_from_unix("bad") == ""
    assert _iso_from_unix(0) == "" and _iso_from_unix(-5) == ""
    assert _iso_from_unix(10**18) == ""       # OverflowError/OSError 방어
    assert _readable_unix(10**18) == ""
    assert _iso_from_unix(1782000000).endswith("Z")  # 정상값은 통과
    print("OK 유닉스 시각 가드(비정상값 → '')")


def test_html_to_text_strips_tags():
    html = "<p>첫 문단</p><p>둘째 <a href='x'>링크</a></p><br>셋째"
    txt = html_to_text(html)
    assert "<" not in txt and ">" not in txt
    assert "첫 문단" in txt and "둘째 링크" in txt and "셋째" in txt
    print("OK html_to_text")


def test_assignment_from_ws():
    assign = {
        "id": 9, "cmid": 100, "course": 42, "name": "도시 관찰 보고서",
        "intro": "<p>도시를 관찰하고 <b>보고서</b>를 작성하시오.</p>",
        "duedate": 1782000000,   # 미래 유닉스 초
        "introattachments": [
            {"filename": "관찰가이드.pdf", "fileurl": "https://myetl.snu.ac.kr/webservice/pluginfile.php/1/mod_assign/intro/0/관찰가이드.pdf"},
            {"filename": "dup", "fileurl": "https://x/a"},
            {"filename": "dup2", "fileurl": "https://x/a"},  # 같은 URL 중복 제거
        ],
    }
    raw = assignment_from_ws(assign, "https://myetl.snu.ac.kr")
    assert raw.title == "도시 관찰 보고서"
    assert "보고서를 작성" in raw.description
    assert "마감:" in raw.description and "년" in raw.description  # 사람이 읽는 표기
    assert "1782000000" not in raw.description  # 유닉스 원문 노출 안 함
    assert len(raw.attachments) == 2  # URL 중복 제거
    # cmid(id)로 열고, 무상태 재조회용 courseid를 함께 싣는다.
    assert "/mod/assign/view.php?id=100" in raw.url and "courseid=42" in raw.url
    print("OK assignment_from_ws (본문·마감표기·첨부dedup)")


def test_parse_ws_assignments():
    data = {"courses": [{"id": 42, "fullname": "도시문화론", "assignments": [
        {"id": 9, "cmid": 100, "name": "1차 과제", "duedate": 1782000000},
        {"id": 10, "cmid": 101, "name": "2차 과제", "duedate": 0},
    ]}]}
    refs = parse_ws_assignments(data, "https://myetl.snu.ac.kr")
    assert [r.id for r in refs] == ["9", "10"]
    assert refs[0].course_name == "도시문화론" and refs[0].course_id == "42"
    assert refs[0].due_at.endswith("Z") and refs[1].due_at == ""  # ISO 계약
    assert "view.php?id=100" in refs[0].url and "courseid=42" in refs[0].url
    print("OK parse_ws_assignments")


def test_parse_course_contents():
    data = [
        {"id": 1, "name": "1주차", "modules": [
            {"id": 100, "name": "강의노트.pdf", "modname": "resource",
             "url": "https://x/mod/resource/view.php?id=100",
             "contents": [{"type": "file", "filename": "강의노트.pdf",
                           "fileurl": "https://x/pluginfile.php/1/f/강의노트.pdf"}]},
            {"id": 101, "name": "공지 라벨", "modname": "label", "contents": []},
        ]},
    ]
    got = parse_course_contents(data, "https://x")
    fnames = [a.name for a in got["files"]]
    mnames = [a.name for a in got["modules"]]
    assert "강의노트.pdf" in fnames
    assert "강의노트.pdf" in mnames and "공지 라벨" in mnames
    print("OK parse_course_contents (files+modules)")


def test_with_token():
    # ? 없는 URL → ?token= ; 이미 쿼리 있으면 &token= ; 기존 token은 교체.
    assert with_token("https://x/f.pdf", "T") == "https://x/f.pdf?token=T"
    assert "forcedownload=1&token=T" in with_token("https://x/f.pdf?forcedownload=1", "T")
    once = with_token("https://x/f.pdf?token=OLD", "NEW")
    assert once.count("token=") == 1 and "token=NEW" in once and "OLD" not in once
    assert with_token("https://x/f.pdf", "") == "https://x/f.pdf"  # 토큰 없으면 그대로
    print("OK with_token")


# ─────────────────────────────────────────────────────────────────────────────
# 어댑터 통합 — 탐색→수집→다운로드(네트워크 대체)
# ─────────────────────────────────────────────────────────────────────────────
def _adapter_fake_urlopen(capture=None):
    """MoodleWsAdapter의 WS 호출과 파일 다운로드를 함수별로 라우팅."""
    site = {"userid": 7, "sitename": "SNU eTL", "functions": []}
    courses = [{"id": 42, "fullname": "도시문화론", "enddate": 0}]
    assigns = {"courses": [{"id": 42, "fullname": "도시문화론", "assignments": [
        {"id": 9, "cmid": 100, "course": 42, "name": "도시 관찰 보고서",
         "intro": "<p>도시를 관찰하시오.</p>", "duedate": 1782000000,
         "introattachments": [{"filename": "가이드.pdf",
                               "fileurl": "https://myetl.snu.ac.kr/webservice/pluginfile.php/1/x/가이드.pdf"}]},
    ]}]}
    contents = [{"id": 1, "name": "1주차", "modules": [
        {"id": 100, "name": "강의노트.pdf", "modname": "resource", "url": "https://x/r/100",
         "contents": [{"type": "file", "filename": "강의노트.pdf",
                       "fileurl": "https://myetl.snu.ac.kr/webservice/pluginfile.php/1/f/강의노트.pdf"}]},
    ]}]

    class _Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): self.close(); return False

    def fake(req, timeout=None):
        if capture is not None:
            capture.append(req)
        url = req.full_url
        # 파일 다운로드(pluginfile)는 토큰이 붙어 있어야 한다.
        if "pluginfile.php" in url:
            assert "token=" in url, "파일 다운로드에 토큰 필요"
            return _Resp(b"%PDF-1.4 fake")
        body = (req.data or b"").decode("utf-8")
        if "core_webservice_get_site_info" in body:
            return _Resp(json.dumps(site).encode("utf-8"))
        if "core_enrol_get_users_courses" in body:
            return _Resp(json.dumps(courses).encode("utf-8"))
        if "mod_assign_get_assignments" in body:
            return _Resp(json.dumps(assigns).encode("utf-8"))
        if "core_course_get_contents" in body:
            return _Resp(json.dumps(contents).encode("utf-8"))
        return _Resp(json.dumps({}).encode("utf-8"))
    return fake


def test_adapter_discovery_collect_download():
    import tempfile
    from until.capture.sources.discovery import EtlInbox
    from until.capture.sources.etl import EtlSource
    orig = moodle_ws.urllib.request.urlopen
    moodle_ws.urllib.request.urlopen = _adapter_fake_urlopen()
    try:
        adapter = MoodleWsAdapter("https://myetl.snu.ac.kr", token="TESTTOKEN")
        # 인박스(EtlInbox 재사용) — 순차 모드(스레드 캐시 공유).
        inbox = EtlInbox(adapter, base_url=adapter.base_url)
        items = inbox.list_assignments(bucket=None, max_workers=1)
        assert len(items) == 1 and items[0].title == "도시 관찰 보고서"
        url = items[0].url
        # course 매핑이 캐시됐다(자료 순위화용).
        assert adapter.course_id_for_url(url) == "42"
        # 수집(EtlSource 재사용) — fetch_assignment는 캐시에서, download는 토큰 URL로.
        with tempfile.TemporaryDirectory() as d:
            collected = EtlSource(url, adapter).collect(d)
            assert "도시 관찰" in collected.title
            assert collected.attachments[0].local_path  # 다운로드 성공
            assert pathlib.Path(collected.attachments[0].local_path).read_bytes().startswith(b"%PDF")
        # 자료 목록(etl_materials 재사용) — 파일/모듈.
        files = adapter.list_course_files("42", adapter.base_url)
        mods = adapter.list_modules("42", adapter.base_url)
        assert any(a.name == "강의노트.pdf" for a in files)
        assert any(a.name == "강의노트.pdf" for a in mods)
    finally:
        moodle_ws.urllib.request.urlopen = orig
    print("OK 어댑터 탐색→수집→다운로드(토큰) + 자료 목록")


def test_submission_status_and_enrich():
    # mod_assign_get_submission_status로 submitted를 채운다(미제출 필터·과금 보호).
    import io as _io
    from until.capture.sources.models import AssignmentRef

    def _resp(status=None, graded=False):
        d = {"lastattempt": {"submission": {"status": status} if status else {}}}
        if graded:
            d["feedback"] = {"gradeddate": 1700000000}
        return d

    submitted_payload = _resp(status="submitted")
    new_payload = _resp(status="new")
    graded_payload = _resp(graded=True)

    class _Resp(_io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): self.close(); return False

    # assignid로 응답 분기.
    def fake(req, timeout=None):
        b = (req.data or b"").decode("utf-8")
        if "assignid=1" in b:
            return _Resp(json.dumps(submitted_payload).encode())
        if "assignid=2" in b:
            return _Resp(json.dumps(new_payload).encode())
        if "assignid=3" in b:
            return _Resp(json.dumps(graded_payload).encode())
        return _Resp(json.dumps({}).encode())

    orig = moodle_ws.urllib.request.urlopen
    moodle_ws.urllib.request.urlopen = fake
    try:
        ad = MoodleWsAdapter(_BASE, token="T")
        assert ad.submission_submitted("1") is True    # 제출됨
        assert ad.submission_submitted("2") is False   # new=미제출
        assert ad.submission_submitted("3") is True    # 채점됨=제출로 간주
        items = [AssignmentRef(id="1", title="A", course_id="9"),
                 AssignmentRef(id="2", title="B", course_id="9")]
        ad.enrich_submitted(items, max_workers=1)
        assert items[0].submitted is True and items[1].submitted is False
    finally:
        moodle_ws.urllib.request.urlopen = orig
    print("OK 제출 상태 조회 + enrich(미제출 필터 정직화)")


def test_fill_replies_only_targets():
    # fill_replies는 주어진 공지에만 답글을 채운다(순위화 상위만 → N+1 방지).
    import io as _io
    from until.capture.sources.moodle_ws import Announcement

    class _Resp(_io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): self.close(); return False

    def fake(req, timeout=None):
        return _Resp(json.dumps({"posts": [
            {"id": 1, "message": "<p>본문</p>"},
            {"id": 2, "message": "<p>교수 답글</p>"}]}).encode())

    orig = moodle_ws.urllib.request.urlopen
    moodle_ws.urllib.request.urlopen = fake
    try:
        ad = MoodleWsAdapter(_BASE, token="T")
        anns = [Announcement(subject="s", body="본문",
                             url=f"{_BASE}/mod/forum/discuss.php?d=5&parent=9")]  # 뒤 파라미터
        ad.fill_replies(anns)
        assert anns[0].replies == ["교수 답글"]  # 본문 중복 제외 + d=5 안전 추출
    finally:
        moodle_ws.urllib.request.urlopen = orig
    print("OK fill_replies(대상만·d= 안전추출)")


def test_fetch_stateless_via_courseid():
    # 인박스를 거치지 않은 새 어댑터도 URL의 courseid로 자체 조회해 본문을 만든다
    # (웹 /pick 흐름 — /pick은 매 요청 새 어댑터라 캐시가 없다).
    orig = moodle_ws.urllib.request.urlopen
    moodle_ws.urllib.request.urlopen = _adapter_fake_urlopen()
    try:
        adapter = MoodleWsAdapter("https://myetl.snu.ac.kr", token="T")
        # 캐시 비어 있음 — URL만으로 fetch(내부에서 과목 조회).
        url = "https://myetl.snu.ac.kr/mod/assign/view.php?id=100&courseid=42"
        raw = adapter.fetch_assignment(url)
        assert "도시 관찰" in raw.title
        assert adapter.course_id_for_url(url) == "42"
    finally:
        moodle_ws.urllib.request.urlopen = orig
    print("OK 무상태 fetch_assignment(courseid 자체조회)")


def test_fetch_no_courseid_raises():
    # courseid도 캐시도 없으면 명확한 에러(과목 단위 조회 특성).
    orig = moodle_ws.urllib.request.urlopen
    moodle_ws.urllib.request.urlopen = _adapter_fake_urlopen()
    try:
        adapter = MoodleWsAdapter("https://myetl.snu.ac.kr", token="T")
        try:
            adapter.fetch_assignment("https://myetl.snu.ac.kr/mod/assign/view.php?id=999")
        except RuntimeError as e:
            assert "인박스" in str(e) or "과목 단위" in str(e)
        else:
            raise AssertionError("courseid 없는 캐시 미스는 에러여야 함")
    finally:
        moodle_ws.urllib.request.urlopen = orig
    print("OK courseid 없는 fetch 에러")


if __name__ == "__main__":
    test_allowlist_denylist_disjoint()
    test_assert_read_only_blocks_writes()
    test_assert_read_only_blocks_unknown()
    test_assert_read_only_allows_reads()
    test_write_call_makes_no_request()
    test_read_call_sends_token_in_body_not_url()
    test_ws_error_becomes_clear_message()
    test_flatten_params_php_style()
    test_site_info_function_inventory()
    test_print_site_inventory()
    test_requires_token()
    test_parse_ws_courses_filters_ended()
    test_unix_time_guards()
    test_html_to_text_strips_tags()
    test_assignment_from_ws()
    test_parse_ws_assignments()
    test_parse_course_contents()
    test_with_token()
    test_adapter_discovery_collect_download()
    test_submission_status_and_enrich()
    test_fill_replies_only_targets()
    test_fetch_stateless_via_courseid()
    test_fetch_no_courseid_raises()
    print("\nMOODLE WS TEST PASS")
