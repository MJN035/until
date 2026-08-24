"""
eTL 탐색(Discovery) — '내 과제'를 자동으로 찾아 목록으로 만든다. (P9)

어댑터(Canvas API / 브라우저 SSO)로부터 과목·과제를 받아 AssignmentRef 목록으로
조립한다. 파이프라인 코어와 접속 방식은 분리(BrowserAdapter 패턴) — Inbox는
'list_courses / list_assignments 능력을 가진 어댑터'만 알면 된다.
"""
from __future__ import annotations

from typing import List, Optional, Protocol

from .models import CourseRef, AssignmentRef

SNU_ETL_BASE = "https://myetl.snu.ac.kr"


class DiscoveryAdapter(Protocol):
    """탐색 능력. CanvasApiAdapter가 이 형태를 만족한다."""
    def list_courses(self, base_url: str) -> List[CourseRef]: ...
    def list_assignments(self, course: CourseRef, base_url: str,
                         bucket: Optional[str] = None) -> List[AssignmentRef]: ...


# due_at 없는 과제는 맨 뒤로(정렬 키).
_FAR_FUTURE = "9999-12-31T23:59:59Z"


class EtlInbox:
    """어댑터로 eTL을 훑어 과제 목록을 만든다."""

    def __init__(self, adapter: DiscoveryAdapter, base_url: str = SNU_ETL_BASE):
        self.adapter = adapter
        self.base_url = base_url

    def list_assignments(self, *, bucket: Optional[str] = "upcoming",
                         only_unsubmitted: bool = False,
                         max_workers: int = 8,
                         include_past_courses: bool = False) -> List[AssignmentRef]:
        """모든 과목의 과제를 모아 마감 임박순으로 정렬해 돌려준다.

        과목별 조회를 **병렬**로 수행해 과목이 많아도 빠르다(21과목 ≈ 순차 대비 수배).
        bucket: Canvas 서버측 필터(기본 'upcoming'). None이면 전체.
        only_unsubmitted: 이미 제출한 과제는 제외.
        한 과목 조회가 실패해도(접근 제한 등) 전체를 멈추지 않는다.
        max_workers<=1 이면 스레드 없이 **순차** 실행한다 — 브라우저 SSO 어댑터처럼
        호출 스레드가 고정돼야 하는(Playwright sync) 경우에 쓴다.
        include_past_courses: 지난 학기 과목까지 포함(어댑터가 지원할 때만 —
        미지원 어댑터는 기존 시그니처로 폴백).
        """
        # GraphQL 고속 경로(opt-in: UNTIL_GRAPHQL=1) — 과목 N+1 콜을 1콜로.
        # 라이브 미검증 인스턴스 방어: 예외·빈 목록이면 조용히 REST로 폴백.
        # 지난 학기 포함 요청은 REST 의미를 그대로 쓰기 위해 GraphQL을 건너뛴다.
        import os
        if (not include_past_courses
                and (os.getenv("UNTIL_GRAPHQL", "") or "").strip() == "1"
                and hasattr(self.adapter, "list_assignments_graphql")):
            try:
                items = self.adapter.list_assignments_graphql(self.base_url)
            except Exception:
                items = []
            if items:
                items = [a for a in items if a.actionable]
                if only_unsubmitted:
                    items = [a for a in items if not a.submitted]
                items.sort(key=lambda a: (a.due_at or _FAR_FUTURE,
                                          a.course_name, a.title))
                return items
        if include_past_courses:
            try:
                courses = self.adapter.list_courses(self.base_url, include_past=True)
            except TypeError:  # 어댑터가 include_past 미지원(WS·SSO 등)
                courses = self.adapter.list_courses(self.base_url)
        else:
            courses = self.adapter.list_courses(self.base_url)

        def _one(course) -> List[AssignmentRef]:
            try:
                return self.adapter.list_assignments(course, self.base_url, bucket=bucket)
            except Exception:
                return []  # 과목 하나 실패는 건너뛴다

        items: List[AssignmentRef] = []
        if courses and max_workers > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(max_workers, len(courses))) as ex:
                for got in ex.map(_one, courses):
                    items.extend(got)
        elif courses:
            for c in courses:  # 순차(SSO 등 스레드 고정 필요)
                items.extend(_one(c))
        # 성적부 자리표시(시험·총점·출석 — 제출할 것 없음)는 '할 일'이 아니다.
        items = [a for a in items if a.actionable]
        if only_unsubmitted:
            items = [a for a in items if not a.submitted]
        items.sort(key=lambda a: (a.due_at or _FAR_FUTURE, a.course_name, a.title))
        return items
