"""P9 — eTL 탐색(과목·과제 자동 목록) 테스트 (네트워크 불필요)."""
import io, json, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.capture.sources import canvas_api
from until.capture.sources.canvas_api import (
    parse_courses, parse_assignments, CanvasApiAdapter,
)
from until.capture.sources.models import CourseRef
from until.capture.sources.discovery import EtlInbox

BASE = "https://myetl.snu.ac.kr"
_COURSES = pathlib.Path("examples/canvas_fixture/courses_api.json")
_ASSIGNS = pathlib.Path("examples/canvas_fixture/assignments_api.json")


def test_parse_courses_skips_unnamed():
    courses = parse_courses(json.loads(_COURSES.read_text(encoding="utf-8")))
    # name=null(접근 제한) + 지난 과목 3종(학기 종료·완료 상태·종강일 경과) 제외 —
    # 지금 수강 중인 과목만 남는다.
    assert [c.id for c in courses] == ["302199", "305001"]
    assert courses[0].name == "도시와 사회"
    # 종료일 정보가 아예 없으면 현재 과목으로 간주(fail-open) — 302199가 그 케이스.
    print("OK parse courses (skips access-restricted + past-term)")


def test_parse_assignments_submission_flag():
    a = parse_assignments(json.loads(_ASSIGNS.read_text(encoding="utf-8")), BASE,
                          course=CourseRef("302199", "도시와 사회"))
    assert [x.id for x in a] == ["369118", "369200"]
    assert a[0].submitted is False and a[1].submitted is True   # 제출 여부 판별
    assert a[0].course_name == "도시와 사회"
    assert a[0].url.endswith("/courses/302199/assignments/369118")
    print("OK parse assignments (+submission flag)")


class _FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): self.close(); return False


def _fake_urlopen():
    courses = _COURSES.read_text(encoding="utf-8")
    assigns = _ASSIGNS.read_text(encoding="utf-8")
    def fake(req, timeout=None):
        url = req.full_url
        assert req.get_header("Authorization") == "Bearer TESTTOKEN"
        if "/assignments" in url:
            return _FakeResp(assigns.encode("utf-8"))
        if "/courses" in url:
            return _FakeResp(courses.encode("utf-8"))
        return _FakeResp(b"[]")
    return fake


def test_inbox_lists_and_sorts_without_network():
    orig = canvas_api.urllib.request.urlopen
    canvas_api.urllib.request.urlopen = _fake_urlopen()
    try:
        inbox = EtlInbox(CanvasApiAdapter(token="TESTTOKEN"), base_url=BASE)
        # 전체(bucket=None): 두 과목 × 2과제 = 4건, 마감순 정렬.
        allitems = inbox.list_assignments(bucket=None)
        assert len(allitems) == 4
        dues = [a.due_at for a in allitems]
        assert dues == sorted(dues), "마감 임박순 정렬"
        # 미제출만: 제출한 '기말 보고서'(369200)는 빠진다.
        todo = inbox.list_assignments(bucket=None, only_unsubmitted=True)
        ids = {a.id for a in todo}
        assert "369200" not in ids and "369118" in ids
    finally:
        canvas_api.urllib.request.urlopen = orig
    print("OK inbox lists assignments across courses, sorted, unsubmitted filter")


def test_include_past_and_term():
    data = json.loads(_COURSES.read_text(encoding="utf-8"))
    # 지난 학기 포함(include_past=True): 지난 과목이 ended=True로 함께 온다.
    all_courses = parse_courses(data, include_past=True)
    cur_courses = parse_courses(data)
    assert len(all_courses) > len(cur_courses)
    assert any(c.ended for c in all_courses)
    assert not any(c.ended for c in cur_courses)
    # 과제에 과목 학기 이름이 실린다(필터·표시용).
    a = parse_assignments(json.loads(_ASSIGNS.read_text(encoding="utf-8")), BASE,
                          course=CourseRef("302199", "도시와 사회", term="2026-1"))
    assert all(x.term == "2026-1" for x in a)
    # EtlInbox: include_past_courses가 어댑터로 전달, 미지원 어댑터는 폴백.
    class _Old:  # include_past 미지원(구 시그니처)
        def list_courses(self, base_url):
            return [CourseRef("1", "구식")]
        def list_assignments(self, course, base_url, bucket=None):
            return []
    EtlInbox(_Old(), base_url=BASE).list_assignments(include_past_courses=True)
    seen = {}
    class _New:
        def list_courses(self, base_url, include_past=False):
            seen["include_past"] = include_past
            return []
        def list_assignments(self, course, base_url, bucket=None):
            return []
    EtlInbox(_New(), base_url=BASE).list_assignments(include_past_courses=True)
    assert seen["include_past"] is True
    print("OK include_past + term threading")


def test_web_inbox_filter_sort():
    from until.capture.sources.models import AssignmentRef
    from until.web import _filter_sort_inbox
    items = [
        AssignmentRef("1", "지난 과제", "c1", "과목A", due_at="2020-01-01T00:00:00Z",
                      submitted=True, term="2020-1"),
        AssignmentRef("2", "임박 과제", "c1", "과목A", due_at="2999-01-01T00:00:00Z",
                      term="2026-1"),
        AssignmentRef("3", "마감 없음", "c2", "과목B", term="2026-1"),
        AssignmentRef("4", "여유 과제", "c2", "과목B", due_at="2999-06-01T00:00:00Z",
                      submitted=True, term="2026-1"),
    ]
    # 상태 필터: TODO / 완료.
    assert {a.id for a in _filter_sort_inbox(items, status="todo")} == {"2", "3"}
    assert {a.id for a in _filter_sort_inbox(items, status="done")} == {"1", "4"}
    # 기한 지난 숨기기 + 학기 필터.
    assert {a.id for a in _filter_sort_inbox(items, hide_past=True)} == {"2", "3", "4"}
    assert {a.id for a in _filter_sort_inbox(items, term="2020-1")} == {"1"}
    # 정렬: 임박순(기본, 마감 없음은 뒤) / 늦은순 / 과목명순.
    assert [a.id for a in _filter_sort_inbox(items)] == ["1", "2", "4", "3"]
    desc = [a.id for a in _filter_sort_inbox(items, sort="due_desc")]
    assert desc[0] == "4" and desc[1] == "2"  # 늦은 마감부터(빈 마감은 뒤)
    by_course = [a.course_name for a in _filter_sort_inbox(items, sort="course")]
    assert by_course == sorted(by_course)
    print("OK web inbox filter + sort")


def test_playwright_discovery_adapter_parses(monkeypatch=None):
    # P12: 브라우저 세션 어댑터도 같은 파서로 동작 — _get_json만 가짜로 주입.
    from until.capture.sources.playwright_discovery import PlaywrightDiscoveryAdapter
    courses = json.loads(_COURSES.read_text(encoding="utf-8"))
    assigns = json.loads(_ASSIGNS.read_text(encoding="utf-8"))

    ad = PlaywrightDiscoveryAdapter(base_url=BASE)
    # _fetch만 가짜로 주입 → 실제 _get_paginated(rel=next 따라가기) 경로까지 검증.
    def fake_fetch(url):
        data = assigns if "/assignments" in url else courses
        return data, ""  # 다음 페이지 없음
    ad._ensure_session = lambda: None   # 브라우저 launch 우회
    ad._fetch = fake_fetch

    # EtlInbox와 그대로 호환되는지(토큰 없이).
    inbox = EtlInbox(ad, base_url=BASE)
    items = inbox.list_assignments(bucket=None, only_unsubmitted=True)
    assert items and all(not a.submitted for a in items)
    assert any("369118" == a.id for a in items)
    print("OK playwright discovery adapter (token-less) parses + EtlInbox 호환")


def test_graphql_fast_path_and_fallback():
    """UNTIL_GRAPHQL=1이면 GraphQL 1콜 우선, 실패·빈 목록이면 REST 폴백."""
    import os
    from until.capture.sources.discovery import EtlInbox
    from until.capture.sources.models import AssignmentRef, CourseRef

    ref = AssignmentRef(id="9", title="GQL 과제", course_id="1",
                        course_name="과목", url="u", due_at="2099-01-01T00:00:00Z",
                        submitted=True)

    class GqlAdapter:
        def __init__(self, items):
            self.items = items
            self.rest_called = False

        def list_assignments_graphql(self, base_url):
            if self.items is Exception:
                raise RuntimeError("no graphql")
            return list(self.items)

        def list_courses(self, base_url, include_past=False):
            self.rest_called = True
            return [CourseRef(id="1", name="과목")]

        def list_assignments(self, course, base_url, bucket=None):
            return [AssignmentRef(id="1", title="REST 과제", course_id="1",
                                  course_name="과목", url="u")]

    old = os.environ.get("UNTIL_GRAPHQL")
    try:
        os.environ["UNTIL_GRAPHQL"] = "1"
        ok = GqlAdapter([ref])
        items = EtlInbox(ok).list_assignments(bucket=None)
        assert [a.title for a in items] == ["GQL 과제"] and not ok.rest_called
        # only_unsubmitted 필터가 GraphQL 경로에도 적용
        assert EtlInbox(GqlAdapter([ref])).list_assignments(
            bucket=None, only_unsubmitted=True) == []
        # GraphQL 실패 → REST 폴백
        bad = GqlAdapter(Exception)
        items2 = EtlInbox(bad).list_assignments(bucket=None)
        assert [a.title for a in items2] == ["REST 과제"] and bad.rest_called
        # 게이트 꺼짐 → GraphQL 안 씀
        os.environ["UNTIL_GRAPHQL"] = "0"
        off = GqlAdapter([ref])
        items3 = EtlInbox(off).list_assignments(bucket=None)
        assert [a.title for a in items3] == ["REST 과제"] and off.rest_called
    finally:
        if old is None:
            os.environ.pop("UNTIL_GRAPHQL", None)
        else:
            os.environ["UNTIL_GRAPHQL"] = old
    print("OK GraphQL 고속 경로(게이트·필터·REST 폴백)")


def test_graphql_parse():
    from until.capture.sources.canvas_api import parse_graphql_inbox
    payload = {"data": {"allCourses": [
        {"_id": "10", "name": "도시문화론", "term": {"name": "2026-1", "endAt": None},
         "assignmentsConnection": {"nodes": [
             {"_id": "77", "name": "기말 보고서", "dueAt": "2099-12-01T00:00:00Z",
              "htmlUrl": "https://x/courses/10/assignments/77",
              "submissionsConnection": {"nodes": [{"state": "submitted",
                                                   "submittedAt": "2026-07-01"}]}},
             {"_id": "78", "name": "미제출 과제", "dueAt": "",
              "submissionsConnection": {"nodes": [{"state": "unsubmitted",
                                                   "submittedAt": None}]}},
         ]}},
        {"_id": "11", "name": "지난 과목",
         "term": {"name": "2020-1", "endAt": "2020-06-30T00:00:00Z"},
         "assignmentsConnection": {"nodes": [{"_id": "1", "name": "옛 과제"}]}},
        "bad",
    ]}}
    refs = parse_graphql_inbox(payload, "https://x")
    assert [r.id for r in refs] == ["77", "78"]        # 지난 과목 제외
    assert refs[0].submitted and not refs[1].submitted
    assert refs[1].url.endswith("/courses/10/assignments/78")  # URL 폴백 생성
    assert refs[0].term == "2026-1"
    # errors → 예외(호출부 폴백 신호)
    try:
        parse_graphql_inbox({"errors": [{"message": "no schema"}]}, "https://x")
        raise AssertionError("errors인데 예외가 없음")
    except RuntimeError:
        pass
    print("OK GraphQL 파서(지난 과목 제외·제출 상태·errors)")


def test_placeholder_assignments_filtered():
    # 성적부 자리표시(기말고사·중간 총점·M1·출석 — submission_types가 none/on_paper뿐)는
    # '할 일'이 아니다: actionable=False 태깅 + 인박스 제외. 실코퍼스에서 19%였다.
    data = [
        {"id": 1, "name": "기말고사", "submission_types": ["none"]},
        {"id": 2, "name": "중간 총점", "submission_types": ["on_paper"]},
        {"id": 3, "name": "실습4 레포트", "submission_types": ["online_upload"]},
        {"id": 4, "name": "정보 없음"},                       # 정보 없음 → fail-open
        {"id": 5, "name": "지필+업로드",                      # 혼합이면 실행형
         "submission_types": ["on_paper", "online_upload"]},
    ]
    a = parse_assignments(data, BASE, course=CourseRef("1", "c"))
    flags = {x.title: x.actionable for x in a}
    assert flags == {"기말고사": False, "중간 총점": False, "실습4 레포트": True,
                     "정보 없음": True, "지필+업로드": True}
    class _Ad:
        def list_courses(self, base_url):
            return [CourseRef("1", "c")]
        def list_assignments(self, course, base_url, bucket=None):
            return parse_assignments(data, base_url, course=course)
    items = EtlInbox(_Ad(), base_url=BASE).list_assignments(bucket=None)
    assert {x.title for x in items} == {"실습4 레포트", "정보 없음", "지필+업로드"}
    # GraphQL 경로도 같은 판정(submissionTypes) — 미지원 인스턴스는 None → fail-open.
    payload = {"data": {"allCourses": [{
        "_id": "9", "name": "c", "term": {"name": "t", "endAt": None},
        "assignmentsConnection": {"nodes": [
            {"_id": "11", "name": "기말고사", "submissionTypes": ["none"],
             "submissionsConnection": {"nodes": []}},
            {"_id": "12", "name": "과제", "submissionsConnection": {"nodes": []}},
        ]}}]}}
    refs = canvas_api.parse_graphql_inbox(payload, BASE)
    assert {r.id: r.actionable for r in refs} == {"11": False, "12": True}
    print("OK placeholder assignments filtered (REST+GraphQL+inbox)")


if __name__ == "__main__":
    test_parse_courses_skips_unnamed()
    test_parse_assignments_submission_flag()
    test_placeholder_assignments_filtered()
    test_inbox_lists_and_sorts_without_network()
    test_include_past_and_term()
    test_web_inbox_filter_sort()
    test_playwright_discovery_adapter_parses()
    test_graphql_fast_path_and_fallback()
    test_graphql_parse()
    print("\nDISCOVERY TEST PASS")
