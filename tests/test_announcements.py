"""eTL 공지(4번) 수집·순위화·SourceDoc 변환 테스트 (네트워크 불필요)."""
import io
import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.capture.sources import moodle_ws
from until.capture.sources.moodle_ws import (
    MoodleWsAdapter, Announcement, parse_ws_forums, parse_ws_discussions, parse_ws_posts,
)
from until.capture.sources.models import CourseRef
from until.context.etl_announcements import (
    rank_announcements, collect_related_announcements, announcements_to_sources,
    announcements_summary,
)

_BASE = "https://myetl.snu.ac.kr"


def test_parse_forums_and_discussions_and_posts():
    forums = parse_ws_forums([
        {"id": 1, "name": "공지사항", "type": "news", "course": 42},
        {"id": 2, "name": "자유게시판", "type": "general", "course": 42},
        "bad",
    ])
    assert [f["id"] for f in forums] == ["1", "2"]
    assert forums[0]["type"] == "news"

    anns = parse_ws_discussions(
        {"discussions": [
            {"id": 5, "discussion": 5, "name": "1주차 안내",
             "message": "<p>도시 관찰 과제는 <b>3장 이상</b> 사진 첨부.</p>",
             "userfullname": "김교수", "created": 1780000000},
        ]},
        forum="공지사항", course_id="42", course_name="도시문화론", base_url=_BASE)
    assert len(anns) == 1
    a = anns[0]
    assert a.subject == "1주차 안내" and "3장 이상" in a.body and "<" not in a.body
    assert a.author == "unknown" and a.created_iso.endswith("Z")
    assert "김교수" not in repr(a)
    assert a.url.endswith("/mod/forum/discuss.php?d=5")

    posts = parse_ws_posts({"posts": [
        {"id": 1, "message": "<p>첫 글</p>"},
        {"id": 2, "message": "<p>교수 답글: 분량은 A4 2장</p>"},
    ]})
    assert posts == ["첫 글", "교수 답글: 분량은 A4 2장"]
    print("OK forum/discussion/post 파서")


def test_announcement_author_is_role_label():
    anns = parse_ws_discussions({"discussions": [{
        "id": 9, "name": "공지", "message": "본문",
        "userfullname": "노출되면안되는실명",
    }]})
    assert anns[0].author == "unknown"
    assert "노출되면안되는실명" not in repr(anns[0])

    # 역할 근거가 응답에 명시된 좁은 경우에만 열거형을 보존한다.
    labelled = parse_ws_discussions({"discussions": [{
        "id": 10, "name": "공지", "message": "본문",
        "userfullname": "또다른실명", "author_role": "instructor",
    }]})
    assert labelled[0].author == "instructor"
    assert "또다른실명" not in repr(labelled[0])


def test_rank_announcements_by_keywords():
    anns = [
        Announcement(subject="도시 관찰 과제 안내", body="사진 3장", created_iso="2026-07-01T00:00:00Z"),
        Announcement(subject="휴강 안내", body="다음 주 휴강", created_iso="2026-07-10T00:00:00Z"),
        Announcement(subject="도시 답사 장소", body="도시 답사는 종로", created_iso="2026-07-05T00:00:00Z"),
    ]
    hits = rank_announcements(anns, ["도시", "답사"], k=2)
    # '도시'·'답사' 매칭 상위 2건 — 무관한 '휴강 안내'는 제외.
    subs = [a.subject for a in hits]
    assert "휴강 안내" not in subs
    assert "도시 답사 장소" in subs
    print("OK 공지 키워드 순위화(무관 공지 제외)")


def test_term_project_alias_and_old_announcement_are_found():
    """영문 제출함·한글 공지 + 최신 20건 밖의 숨은 명세 회귀."""
    old = Announcement(
        subject="학기말 프로젝트 안내",
        body="팀별 주제와 발표 자료 제출 형식을 확인하세요.",
        created_iso="2026-03-10T00:00:00Z",
    )
    newer = [Announcement(subject=f"일반 공지 {i}", body="수업 안내",
                          created_iso=f"2026-05-{(i % 28) + 1:02d}T00:00:00Z")
             for i in range(25)]
    seen = {}

    class Adapter:
        def collect_announcements(self, course, *, limit=5, news_only=True,
                                  include_replies=False):
            seen["limit"] = limit
            return (newer + [old])[:limit]

    hits = collect_related_announcements(
        Adapter(), CourseRef(id="296074", name="산업공학개론"),
        {"goal": "Term Project", "deliverable": "과제", "requirements": []},
    )
    assert seen["limit"] == 100
    assert [a.subject for a in hits] == ["학기말 프로젝트 안내"]
    print("OK Term Project 한영 별칭·오래된 공지 연결")


def test_exact_notice_title_beats_later_incidental_mention():
    anns = [
        Announcement(subject="기말고사 성적 공지",
                     body="기말고사와 Term Project 채점이 완료되었습니다.",
                     created_iso="2026-06-14T00:00:00Z"),
        Announcement(subject="Term Project 공지",
                     body="일정 및 가이드라인을 안내합니다.",
                     created_iso="2026-05-12T00:00:00Z"),
    ]
    hits = rank_announcements(anns, ["Term", "Project"], k=2)
    assert [a.subject for a in hits] == ["Term Project 공지", "기말고사 성적 공지"]
    print("OK 공지 제목 직접 일치 우선")


def test_announcements_to_sources_includes_replies():
    a = Announcement(subject="과제 조건 추가", body="보고서 작성",
                     forum="공지사항", created_iso="2026-07-01T00:00:00Z",
                     url="https://x/d=9", replies=["교수: 표지 포함 5장 이상"])
    srcs = announcements_to_sources([a])
    assert len(srcs) == 1
    assert srcs[0].title == "[eTL 공지] 과제 조건 추가"
    assert "보고서 작성" in srcs[0].text
    assert "표지 포함 5장 이상" in srcs[0].text  # 답글(숨은 명세)까지
    print("OK 공지→SourceDoc(답글 포함)")


def test_announcements_summary():
    anns = [Announcement(subject="공지1", body="", course_name="도시문화론",
                         created_iso="2026-07-01T00:00:00Z", url="https://x/1")]
    s = announcements_summary(anns)
    assert s[0]["subject"] == "공지1" and s[0]["course"] == "도시문화론"
    print("OK 홈 공지 요약")


def _fake_forum_urlopen():
    site = {"userid": 7}
    forums = [{"id": 1, "name": "공지사항", "type": "news", "course": 42}]
    discs = {"discussions": [
        {"id": 5, "discussion": 5, "name": "도시 관찰 과제 조건",
         "message": "<p>사진 3장 이상</p>", "userfullname": "김교수", "created": 1780000000},
        {"id": 6, "discussion": 6, "name": "휴강",
         "message": "<p>다음 주 휴강</p>", "created": 1781000000},
    ]}
    posts_by_disc = {
        "5": {"posts": [
            {"id": 10, "message": "<p>사진 3장 이상</p>"},
            {"id": 11, "message": "<p>교수: 흑백 사진도 허용</p>"},
        ]},
        "6": {"posts": [{"id": 20, "message": "<p>보강일은 추후 공지</p>"}]},
    }

    class _Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): self.close(); return False

    def fake(req, timeout=None):
        body = (req.data or b"").decode("utf-8")
        if "get_site_info" in body:
            return _Resp(json.dumps(site).encode())
        if "get_forums_by_courses" in body:
            return _Resp(json.dumps(forums).encode())
        if "get_forum_discussions" in body:
            return _Resp(json.dumps(discs).encode())
        if "get_discussion_posts" in body:
            did = "6" if "discussionid=6" in body else "5"
            return _Resp(json.dumps(posts_by_disc[did]).encode())
        return _Resp(json.dumps({}).encode())
    return fake


def test_adapter_collect_related_announcements():
    orig = moodle_ws.urllib.request.urlopen
    moodle_ws.urllib.request.urlopen = _fake_forum_urlopen()
    try:
        adapter = MoodleWsAdapter(_BASE, token="T")
        course = CourseRef(id="42", name="도시문화론")
        spec = {"goal": "도시 관찰 보고서", "requirements": ["사진 첨부"]}
        anns = collect_related_announcements(adapter, course, spec, k=2, include_replies=True)
        subs = [a.subject for a in anns]
        assert "도시 관찰 과제 조건" in subs   # 키워드 '도시' 매칭
        assert "휴강" not in subs             # 무관 공지 제외
        # 답글(숨은 명세)까지 흡수됐는지.
        target = next(a for a in anns if a.subject == "도시 관찰 과제 조건")
        assert any("흑백 사진도 허용" in r for r in target.replies)
    finally:
        moodle_ws.urllib.request.urlopen = orig
    print("OK 어댑터 관련 공지 수집(답글 포함·무관 제외)")


def test_canvas_rest_announcements():
    """Canvas REST 공지(토큰 모드) — 파서 + 어댑터 프로토콜 호환 + 순위화 연동."""
    from until.capture.sources.canvas_api import (CanvasApiAdapter,
                                                  parse_canvas_announcements)
    course = CourseRef(id="42", name="도시문화론")
    data = [
        {"title": "기말 보고서 분량 연장 안내", "message": "<p>도시 분석 보고서는 "
         "4000자까지 허용합니다.</p><a href=\"https://docs.google.com/x\">표</a>",
         "posted_at": "2026-07-20T09:00:00Z",
         "author": {"display_name": "김교수"}, "html_url": f"{_BASE}/ann/1"},
        {"title": "휴강 안내", "message": "<p>다음 주 휴강.</p>",
         "posted_at": "2026-07-25T09:00:00Z"},
        {"message": "<p>제목 없음</p>"}, "bad",
    ]
    anns = parse_canvas_announcements(data, course)
    assert len(anns) == 2
    assert anns[0].subject.startswith("기말 보고서") and "4000자" in anns[0].body
    assert anns[0].author == "unknown" and anns[0].course_name == "도시문화론"
    assert "김교수" not in repr(anns[0])
    assert anns[0].links == ["https://docs.google.com/x"]

    # 어댑터 — _get_paginated를 스텁해 collect_announcements 최신순·limit 검증.
    ad = CanvasApiAdapter(token="t")
    requested = {}
    def _page(url, cap_pages=12):
        requested["url"] = url
        return data
    ad._get_paginated = _page
    got = ad.collect_announcements(course, limit=1)
    assert len(got) == 1 and got[0].subject == "휴강 안내"  # 최신(7/25) 우선
    assert "start_date=" in requested["url"] and "end_date=" in requested["url"]
    assert "active_only=true" not in requested["url"]

    # 기존 순위화 경로 재사용 — 과제 키워드('보고서')와 매칭되는 공지만 주입.
    ad2 = CanvasApiAdapter(token="t")
    ad2._get_paginated = lambda url, cap_pages=12: data
    spec = {"deliverable": "보고서", "goal": "도시 분석 보고서",
            "requirements": ["분량 준수"]}
    top = collect_related_announcements(ad2, course, spec, k=3)
    assert [a.subject for a in top] == ["기말 보고서 분량 연장 안내"]
    srcs = announcements_to_sources(top)
    assert "4000자" in srcs[0].text
    print("OK Canvas REST 공지(파서·어댑터·순위화 연동)")


def test_logistics_announcements_filtered_from_spec_injection():
    """출결·좌석·성적 행정 공지는 '숨은 명세'로 주입하지 않는다(실사용 회귀).

    실측: 세미나 소감문·질의 과제의 top-3 공지가 전부 출결 공지였고, 이를
    숨은 명세로 받은 모델이 출결 인증(스크린샷 류) 요구를 초안에 지어냈다.
    순번표 시트 링크가 출결 공지에 실리는 실데이터 때문에 순위화에서 빼지 않고
    (질의 resolver가 계속 받도록) 주입 직전에만 거른다."""
    from until.context.etl_announcements import (
        is_logistics_announcement, spec_announcements,
    )
    logistics = [
        Announcement(subject="중간 출결 현황 공지 (~7주차) 및 지정좌석 관련 공지",
                     body="전자출결, 지정좌석, 소감문 제출을 모두 만족해야 완전한 "
                          "출석이 인정되기 때문에 모두 출결에 주의해주시면 감사하겠습니다.",
                     created_iso="2026-04-17T00:00:00Z"),
        Announcement(subject="2주차 수업 안내 및 전자출결 관련 공지",
                     body="2주차 수업부터는 전자출결, 지정석 착석, 소감문 제출 세 내용 "
                          "모두 확인할 예정입니다.",
                     created_iso="2026-03-12T00:00:00Z"),
        Announcement(subject="5월 7일 수업 전자출결 관련 공지",
                     body="금일 출결은 지정좌석과 소감문으로만 확인할 예정입니다.",
                     created_iso="2026-05-07T00:00:00Z"),
        Announcement(subject="13주차 전체 출결 및 질의 내역 공지",
                     body="질의 제출 내역과 출결 현황을 올려드립니다.",
                     created_iso="2026-05-27T00:00:00Z"),
    ]
    real = Announcement(subject="소감문 작성 안내",
                        body="소감문은 강의 내용을 포함해 400자 이상 작성해 제출하세요.",
                        created_iso="2026-03-10T00:00:00Z")
    for a in logistics:
        assert is_logistics_announcement(a), a.subject
    assert not is_logistics_announcement(real)
    # 질의 순번 변경은 행정이 아니라 과제 실질 정보 — 걸러지면 안 된다.
    assert not is_logistics_announcement(
        Announcement(subject="5주차 질의 순번 변경", body="순번이 변경되었습니다."))

    # 출결 공지가 단일 랭킹 top-3을 점유해 진짜 명세 공지를 밀어내는 실측 상황 —
    # 사후 필터만으로는 주입이 0건이 된다(회귀의 두 번째 축).
    naive = rank_announcements(logistics + [real], ["주차", "소감문"], k=3)
    assert real not in naive  # 점유(crowd-out) 재현

    # collect_related_announcements는 2단 랭킹으로 명세 공지를 먼저 채우고,
    # 행정 공지는 뒤에 붙여 질의 resolver(순번표 링크)가 계속 받게 한다.
    class _Stub:
        def collect_announcements(self, course, *, limit=5, news_only=True,
                                  include_replies=False):
            return logistics + [real]

    course = CourseRef(id="296406", name="세미나3")
    spec = {"deliverable": "과제", "goal": "3주차 소감문 제출", "requirements": [""]}
    top = collect_related_announcements(_Stub(), course, spec, k=3)
    kept = spec_announcements(top)
    assert [a.subject for a in kept] == ["소감문 작성 안내"]  # 주입용
    assert any(is_logistics_announcement(a) for a in top)     # resolver용 유지
    print("OK 행정(출결) 공지 숨은 명세 주입 차단")


if __name__ == "__main__":
    test_parse_forums_and_discussions_and_posts()
    test_announcement_author_is_role_label()
    test_rank_announcements_by_keywords()
    test_term_project_alias_and_old_announcement_are_found()
    test_exact_notice_title_beats_later_incidental_mention()
    test_announcements_to_sources_includes_replies()
    test_announcements_summary()
    test_adapter_collect_related_announcements()
    test_canvas_rest_announcements()
    test_logistics_announcements_filtered_from_spec_injection()
    print("\nANNOUNCEMENTS TEST PASS")
