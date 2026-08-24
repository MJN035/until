"""Canvas REST API 어댑터 테스트 (네트워크 불필요 — urlopen 대체)."""
import io
import json
import re
import sys, pathlib, tempfile
import urllib.error
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.capture.sources import canvas_api
from until.capture.sources.canvas_api import (
    parse_assignment_url, api_assignment_url, parse_canvas_api_assignment,
    parse_canvas_files, CanvasApiAdapter, _download_name,
)
from until.capture.sources.etl import EtlSource

_URL = "https://myetl.snu.ac.kr/courses/302199/assignments/369118"
_FIXTURE = pathlib.Path("examples/canvas_fixture/assignment_api.json")
_FILES_FIXTURE = pathlib.Path("examples/canvas_fixture/files_api.json")


def test_parse_assignment_url():
    base, cid, aid = parse_assignment_url(_URL)
    assert base == "https://myetl.snu.ac.kr" and cid == "302199" and aid == "369118"
    assert api_assignment_url(base, cid, aid) == \
        "https://myetl.snu.ac.kr/api/v1/courses/302199/assignments/369118"
    for bad in ["https://example.com/", "not a url", "https://x/courses/1"]:
        try:
            parse_assignment_url(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{bad!r} should raise")
    print("OK assignment URL parse")


def test_parse_canvas_api_assignment_fixture():
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    raw = parse_canvas_api_assignment(data, "https://myetl.snu.ac.kr")
    assert "나만의 시선으로 읽는 도시" in raw.title
    # 과목명은 어댑터가 조회해 넘긴다. 못 넘기면 내부 식별자로 자리를 채우지
    # 않는다 — 'Canvas course 302199'는 제출 문서 본문까지 새어 나갔었다.
    assert raw.course == ""
    assert "Canvas course" not in raw.course
    named = parse_canvas_api_assignment(data, "https://myetl.snu.ac.kr",
                                        course_name="  도시와 사회 (2026-1) ")
    assert named.course == "도시와 사회 (2026-1)"
    # due_at ISO는 사람이 읽는 표기로 변환된다(초안에 ISO 원문 인용 방지).
    assert "마감:" in raw.description
    assert "2026년 6월 24일(수) 오전 8시" in raw.description
    assert "T00:00" not in raw.description and "+09:00" not in raw.description
    assert "관찰 방법과 동선" in raw.description
    assert "<a" not in raw.description and "<p>" not in raw.description  # HTML 제거됨
    assert len(raw.attachments) == 1
    assert raw.attachments[0].name == "강의자료.pdf"
    assert raw.attachments[0].url.startswith(
        "https://myetl.snu.ac.kr/courses/302199/files/123456/download")
    print("OK parse canvas API assignment fixture")


def test_adapter_requires_token():
    raised = False
    try:
        CanvasApiAdapter(token="")
    except ValueError as e:
        raised = "토큰" in str(e)
    assert raised
    print("OK adapter requires access token")


class _FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): self.close(); return False


#: 과목 상세 조회(GET /api/v1/courses/<id>) — 뒤에 하위 경로가 없는 형태만.
_COURSE_URL_RE = re.compile(r"/api/v1/courses/\d+/?$")


def _fake_urlopen_factory(course_name: str = "도시와 사회 (2026-1)"):
    """assignment API / files API는 fixture JSON, /files 다운로드는 가짜 PDF 바이트.

    course_name=""이면 과목 상세를 404로 돌려 '이름을 못 구한' 경로를 재현한다."""
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    files = json.loads(_FILES_FIXTURE.read_text(encoding="utf-8"))
    def fake_urlopen(req, timeout=None):
        url = req.full_url
        assert req.get_header("Authorization") == "Bearer TESTTOKEN"  # 토큰 전달 확인
        if _COURSE_URL_RE.search(url):
            if not course_name:
                raise urllib.error.HTTPError(url, 404, "not found", None, None)
            return _FakeResp(json.dumps(
                {"id": 302199, "name": course_name}).encode("utf-8"))
        if "/api/v1/" in url and url.rstrip("/").endswith("/files"):
            return _FakeResp(json.dumps(files).encode("utf-8"))
        if "/api/v1/" in url:
            return _FakeResp(json.dumps(data).encode("utf-8"))
        return _FakeResp(b"%PDF-1.4 fake pdf bytes")
    return fake_urlopen


def test_parse_canvas_files_fixture():
    files = json.loads(_FILES_FIXTURE.read_text(encoding="utf-8"))
    atts = parse_canvas_files(files, "https://myetl.snu.ac.kr")
    assert [a.name for a in atts] == ["강의자료.pdf", "관찰_체크리스트.docx"]
    assert atts[0].url.startswith("https://myetl.snu.ac.kr/files/123456/download")
    print("OK parse canvas files list")


def test_fetch_merges_course_files_dedup():
    orig = canvas_api.urllib.request.urlopen
    canvas_api.urllib.request.urlopen = _fake_urlopen_factory()
    try:
        # 기본(off): description 첨부 1개만.
        a_off = CanvasApiAdapter(token="TESTTOKEN").fetch_assignment(_URL)
        assert len(a_off.attachments) == 1

        # on: 코스 파일 병합 — 이미 description에 있던 강의자료.pdf(123456)는 중복 제외,
        # 새 파일 관찰_체크리스트.docx(222333)만 추가 → 총 2개.
        a_on = CanvasApiAdapter(token="TESTTOKEN", include_course_files=True).fetch_assignment(_URL)
        names = sorted(a.name for a in a_on.attachments)
        assert names == ["강의자료.pdf", "관찰_체크리스트.docx"], names
        ids = sorted(canvas_api._file_id(a.url) for a in a_on.attachments)
        assert ids == ["123456", "222333"]  # 파일 id 중복 없음
    finally:
        canvas_api.urllib.request.urlopen = orig
    print("OK fetch merges course files with dedup by file id")


def test_adapter_fetch_and_collect_without_network(monkeypatch_target=None):
    orig = canvas_api.urllib.request.urlopen
    canvas_api.urllib.request.urlopen = _fake_urlopen_factory()
    try:
        adapter = CanvasApiAdapter(token="TESTTOKEN")
        raw = adapter.fetch_assignment(_URL)
        assert "나만의 시선으로 읽는 도시" in raw.title and len(raw.attachments) == 1

        # EtlSource 와 그대로 통합되는지 — 첨부 다운로드까지.
        with tempfile.TemporaryDirectory() as d:
            collected = EtlSource(_URL, adapter).collect(d)
            assert collected.attachments[0].local_path
            files = collected.to_files(d)
            # 설명 파일명 = 과제 제목 기반(.md) — 라벨에 사람이 읽는 이름이 보이도록.
            assert any(f.endswith(".md") and "나만의 시선" in f for f in files)
            assert any("강의자료.pdf" in f for f in files)
            pdf = pathlib.Path(collected.attachments[0].local_path)
            assert pdf.read_bytes().startswith(b"%PDF")
    finally:
        canvas_api.urllib.request.urlopen = orig
    print("OK adapter fetch + EtlSource collect (no network)")


def test_collect_tolerates_download_failure():
    # 첨부 다운로드 실패(403/만료)가 전체 수집을 막지 않아야 한다(실관측 버그).
    from until.capture.sources.models import RawAssignment, Attachment

    class _BadDownloadAdapter:
        def fetch_assignment(self, url):
            return RawAssignment(title="T", course="C", description="설명",
                                 attachments=[Attachment(name="x.pdf", url="https://x/y")], url=url)
        def download(self, att, dest):
            raise RuntimeError("403 Forbidden")

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        collected = EtlSource(_URL, _BadDownloadAdapter()).collect(d)
        assert collected.title == "T"                       # 수집은 성공
        assert collected.attachments[0].local_path is None  # 실패 첨부는 None
        files = collected.to_files(d)
        # 본문은 남음 — 설명 파일명은 과제 제목 기반("T.md").
        assert any(f.endswith("T.md") for f in files)
        # 긴 공지형 제목도 파일명 한계로 크래시하지 않는다(60자 절단 — 리뷰 발견).
        collected.title = "매우 긴 과제 제목 " * 30
        long_files = collected.to_files(d)
        assert any(f.endswith(".md") for f in long_files)
    print("OK collect tolerates download failure (+long title)")


def test_pagination_follows_next_link():
    # 100개 초과 시 조용히 잘리지 않도록 Link 헤더 rel=next를 따라가야 한다.
    class _Resp(io.BytesIO):
        def __init__(self, b, link=""):
            super().__init__(b); self.headers = {"Link": link} if link else {}
        def __enter__(self): return self
        def __exit__(self, *a): self.close(); return False

    page2 = "https://myetl.snu.ac.kr/api/v1/courses?page=2"
    def fake(req, timeout=None):
        if "page=2" in req.full_url:
            return _Resp(json.dumps([{"id": 2, "name": "B"}]).encode())          # 마지막
        return _Resp(json.dumps([{"id": 1, "name": "A"}]).encode(),
                     link=f'<{page2}>; rel="next"')                              # 다음 있음

    orig = canvas_api.urllib.request.urlopen
    canvas_api.urllib.request.urlopen = fake
    try:
        courses = CanvasApiAdapter(token="T").list_courses("https://myetl.snu.ac.kr")
        assert [c.id for c in courses] == ["1", "2"], "두 페이지가 합쳐져야 함"
    finally:
        canvas_api.urllib.request.urlopen = orig
    print("OK pagination follows Link rel=next")


def test_non_json_response_gives_clear_error():
    # SSO/토큰 만료 시 Canvas가 로그인 HTML(200)을 준다 → JSONDecodeError 말고 사람 메시지.
    class _Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): self.close(); return False
    def fake(req, timeout=None):
        return _Resp(b"<!doctype html><html><body>login</body></html>")
    orig = canvas_api.urllib.request.urlopen
    canvas_api.urllib.request.urlopen = fake
    try:
        try:
            CanvasApiAdapter(token="T").fetch_assignment(_URL)
        except RuntimeError as e:
            assert "JSON" in str(e) or "로그인" in str(e), str(e)
        else:
            raise AssertionError("비-JSON 응답은 RuntimeError를 내야 함")
    finally:
        canvas_api.urllib.request.urlopen = orig
    print("OK non-JSON response → clear error")


def test_auth_error_gives_clear_message():
    import urllib.error
    def fake(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)
    orig = canvas_api.urllib.request.urlopen
    canvas_api.urllib.request.urlopen = fake
    try:
        try:
            CanvasApiAdapter(token="T").list_courses("https://myetl.snu.ac.kr")
        except RuntimeError as e:
            assert "인증" in str(e), str(e)
        else:
            raise AssertionError("401은 인증 실패 메시지를 내야 함")
    finally:
        canvas_api.urllib.request.urlopen = orig
    print("OK 401 → '인증 실패' message")


def test_safe_filename_sanitizes():
    from until.capture.sources.models import safe_filename
    assert safe_filename("../../etc/passwd") == "passwd"        # 경로 탈출 차단
    assert safe_filename(r"a\b\c.pdf") == "c.pdf"               # 백슬래시도 basename
    assert safe_filename('q?:*"<>|.txt') == "q_______.txt"      # Windows 금지문자
    assert safe_filename("") == "attachment"                    # 빈 값 폴백
    assert safe_filename("   ...   ") == "attachment"           # 점/공백만 → 폴백
    assert safe_filename("강의자료.pdf") == "강의자료.pdf"       # 정상 한국어 유지
    print("OK safe_filename blocks traversal/illegal chars")


def test_download_name_recovers_extension_safely():
    assert _download_name("다운로드", {
        "Content-Disposition": 'attachment; filename="week-3.pdf"'}) == "week-3.pdf"
    assert _download_name("자료", {"Content-Type": "application/pdf"}) == "자료.pdf"
    assert _download_name("given.docx", {
        "Content-Disposition": 'attachment; filename="other.pdf"'}) == "given.docx"
    assert _download_name("자료", {
        "Content-Disposition": 'attachment; filename="../../unsafe.pdf"'}) == "unsafe.pdf"
    print("OK extensionless Canvas attachment recovers safe response filename")


def test_description_attachment_dedup_by_file_id():
    # 같은 파일이 미리보기(/files/123) + 다운로드(/files/123/download)로 두 번 걸려도 1개.
    page = "https://myetl.snu.ac.kr/courses/1/assignments/2"
    data = {"name": "T", "course_id": "1", "html_url": page, "description":
            '<p>보기 '
            '<a class="instructure_file_link" href="https://myetl.snu.ac.kr/files/123">강의안</a>'
            ' / 받기 '
            '<a class="instructure_file_link" href="https://myetl.snu.ac.kr/files/123/download">강의안</a>'
            '</p>'}
    raw = parse_canvas_api_assignment(data, "https://myetl.snu.ac.kr")
    assert len(raw.attachments) == 1, [a.url for a in raw.attachments]
    assert canvas_api._file_id(raw.attachments[0].url) == "123"
    print("OK description attachments dedup by file id")


def test_parse_planner_items():
    """플래너 '그 외 마감' — 과제 제외·완료 제외·마감 오름차순."""
    from until.capture.sources.canvas_api import parse_planner_items
    data = [
        {"plannable_type": "quiz", "context_name": "통계학",
         "plannable": {"title": "퀴즈 3", "due_at": "2026-08-02T15:00:00Z"},
         "html_url": "https://x/q3"},
        {"plannable_type": "assignment",                       # 과제 → 인박스 담당
         "plannable": {"title": "리포트"}},
        {"plannable_type": "discussion_topic", "context_name": "글쓰기",
         "plannable": {"title": "토론 참여", "todo_date": "2026-08-01T00:00:00Z"},
         "submissions": {"submitted": False}},
        {"plannable_type": "quiz",                             # 완료 → 제외
         "plannable": {"title": "끝난 퀴즈", "due_at": "2026-08-03T00:00:00Z"},
         "submissions": {"submitted": True}},
        {"plannable_type": "calendar_event", "plannable": {}},  # 제목 없음 → 제외
        "bad",
    ]
    out = parse_planner_items(data)
    assert [e["title"] for e in out] == ["토론 참여", "퀴즈 3"]  # 마감 오름차순
    assert out[1]["type"] == "quiz" and out[1]["course"] == "통계학"
    assert out[1]["url"] == "https://x/q3"
    print("OK planner items (과제·완료 제외, 마감순)")


def test_course_name_resolved_and_never_internal_id():
    """과제 문서('과목:' 줄)에 사람이 읽는 과목명만 들어간다.

    이 줄은 LLM 입력과 제출 문서 본문까지 그대로 흘러간다 — 내부 식별자로
    자리를 채우면 학생이 'Canvas course 302199'가 박힌 파일을 제출하게 된다.
    """
    orig = canvas_api.urllib.request.urlopen
    try:
        # (1) 과목명 조회 성공 → 문서에 사람이 읽는 이름.
        canvas_api.urllib.request.urlopen = _fake_urlopen_factory()
        adapter = CanvasApiAdapter(token="TESTTOKEN")
        raw = adapter.fetch_assignment(_URL)
        assert raw.course == "도시와 사회 (2026-1)", raw.course
        with tempfile.TemporaryDirectory() as d:
            md = pathlib.Path(
                [f for f in EtlSource(_URL, adapter).collect(d).to_files(d)
                 if f.endswith(".md")][0]).read_text(encoding="utf-8")
        assert "과목: 도시와 사회 (2026-1)" in md
        assert "Canvas course" not in md

        # (2) 과목명 조회 실패(404) → 수집은 계속되고 '과목:' 줄만 빠진다.
        canvas_api.urllib.request.urlopen = _fake_urlopen_factory(course_name="")
        adapter = CanvasApiAdapter(token="TESTTOKEN")
        raw = adapter.fetch_assignment(_URL)
        assert raw.course == "" and "나만의 시선으로 읽는 도시" in raw.title
        with tempfile.TemporaryDirectory() as d:
            md = pathlib.Path(
                [f for f in EtlSource(_URL, adapter).collect(d).to_files(d)
                 if f.endswith(".md")][0]).read_text(encoding="utf-8")
        assert "과목:" not in md and "Canvas course" not in md
        assert "출처: " in md and "관찰 방법과 동선" in md
    finally:
        canvas_api.urllib.request.urlopen = orig
    print("OK course name resolved; internal id never reaches the document")


if __name__ == "__main__":
    test_parse_assignment_url()
    test_parse_canvas_api_assignment_fixture()
    test_course_name_resolved_and_never_internal_id()
    test_adapter_requires_token()
    test_parse_canvas_files_fixture()
    test_fetch_merges_course_files_dedup()
    test_collect_tolerates_download_failure()
    test_pagination_follows_next_link()
    test_non_json_response_gives_clear_error()
    test_auth_error_gives_clear_message()
    test_safe_filename_sanitizes()
    test_download_name_recovers_extension_safely()
    test_description_attachment_dedup_by_file_id()
    test_adapter_fetch_and_collect_without_network()
    test_parse_planner_items()
    print("\nCANVAS API TEST PASS")
