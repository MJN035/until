"""
4번 — eTL 공지/포럼(숨은 명세 소스) → 표시 + Execution 맥락 주입.

공지는 두 가지로 쓴다:
  ① 홈에 최신 공지 목록(저비용) — announcements_summary().
  ② 과제와 엮어 '이 과제 관련 공지'(고가치) — 교수가 Q&A/공지에서 추가한 조건이
     여기 있다(복붙으론 절대 못 얻는 정보). rank_announcements()로 과제 키워드와
     매칭해 상위만 SourceDoc으로 주입 → 초안·결정 질문에 반영(빈칸 축소).

접속은 어댑터 뒤에(BrowserAdapter 패턴). 순위화는 결정적(토큰 0).
"""
from __future__ import annotations

import re
from typing import List, Optional, Protocol

from ..capture.sources.moodle_ws import Announcement
from ..capture.sources.models import CourseRef
from ..llm.base import SourceDoc
from .retrieval import keywords_from_spec

# 공지 매칭에서 제외할 일반어(과제 자료 순위화와 동일 취지).
_GENERIC_WORDS = frozenset({
    "과제", "제출", "수업", "강의", "자료", "마감", "확인", "작성", "안내",
    "공지", "참고", "안녕", "여러분", "학생",
})

# 행정(출결·좌석·성적) 공지 — 과제와 키워드가 겹쳐 순위에 오르지만 과제 '내용'
# 명세가 아니다. 숨은 명세로 주입되면 모델이 출결 인증(스크린샷 류) 요구를
# 초안에 지어내는 실사용 회귀가 있었다. 제목 기준 결정적 판정: 실측된 세미나
# 출결 공지 제목("중간 출결 현황", "전자출결 관련", "출결 및 질의 내역")을 덮되
# "질의 순번 변경" 같은 실질 정보는 통과시킨다.
_LOGISTICS_TITLE = re.compile(
    r"출결|전자\s*출결|출석\s*(?:현황|점검|확인|인정|처리)"
    r"|지정\s*좌석|지정석|좌석\s*(?:배정|조정)"
    r"|성적\s*(?:공지|열람|이의|확인|입력)")

# 같은 과제를 한글 공지와 영문 제출함에서 다르게 부르는 실데이터 대응.
# 범용 번역기가 아니라, 제목이 비어 있는 제출함에서 실제로 관측된 좁은 별칭만 둔다.
_PHRASE_ALIASES = {
    "term project": ("텀 프로젝트", "텀프로젝트", "학기말 프로젝트", "기말 프로젝트"),
}


class AnnouncementAdapter(Protocol):
    def collect_announcements(self, course: CourseRef, *, limit: int = 5,
                              news_only: bool = True,
                              include_replies: bool = False) -> List[Announcement]: ...


def _text_of(a: Announcement) -> str:
    """공지 매칭 대상 텍스트(제목 + 본문 + 답글)."""
    return " ".join([a.subject, a.body, *a.replies])


def _expanded_keywords(keywords: List[str]) -> List[str]:
    """과제명 언어가 공지와 달라도 좁은 과제명 별칭으로 연결한다."""
    joined = " ".join(keywords).lower()
    out = list(keywords)
    for phrase, aliases in _PHRASE_ALIASES.items():
        if phrase in joined:
            out.extend(aliases)
    return list(dict.fromkeys(out))


def rank_announcements(anns: List[Announcement], keywords: List[str],
                       k: int = 3) -> List[Announcement]:
    """공지를 과제 키워드와 매칭해 관련 상위 k건. 매칭 0은 제외.

    일반어(과제·제출 등)는 매칭에서 빼고 내용어(주제어)로만 순위화한다."""
    kw = [w for w in _expanded_keywords(keywords)
          if len(w) >= 2 and w not in _GENERIC_WORDS]
    scored = []
    for a in anns:
        title_low = (a.subject or "").lower()
        low = _text_of(a).lower()
        # 제목 일치는 본문에서 과제를 곁다리로 언급한 성적 공지보다 강한 신호다.
        n = sum(3 if w.lower() in title_low else 1
                for w in kw if w.lower() in low)
        if n > 0:
            scored.append((n, a))
    # 점수 내림차순 → 같은 점수는 최신(created_iso 큰 것) 우선. 안정 정렬 + reverse.
    scored.sort(key=lambda t: (t[0], t[1].created_iso or ""), reverse=True)
    return [a for _, a in scored[:k]]


def collect_related_announcements(adapter: AnnouncementAdapter, course: CourseRef,
                                  spec: dict, *, k: int = 3,
                                  include_replies: bool = True,
                                  extra_keywords: Optional[List[str]] = None) -> List[Announcement]:
    """과목 공지를 모아 과제(spec) 키워드로 순위화한 상위 k건.

    답글은 **순위화로 추린 상위 k건에만** 나중에 채운다 — 공지 20건 전부의 답글을
    받아 3건만 쓰는 N+1 지연을 피한다(리뷰 발견). 제목·본문만으로 먼저 순위화하고,
    include_replies=True면 상위 k건의 답글(교수 추가 조건=숨은 명세)만 조회한다."""
    try:
        # 학기 말 과제 안내는 학기 초에 먼저 올라오기도 한다. 최신 20건 절단은
        # 공지가 많은 과목에서 실제 명세를 영구히 숨기므로, 답글 없는 목록은
        # 학기 전체 수준으로 받고 순위화한 소수에만 답글을 조회한다.
        anns = adapter.collect_announcements(
            course, limit=100, news_only=True, include_replies=False)
    except Exception:
        return []
    keywords = keywords_from_spec(spec) + list(extra_keywords or [])
    # 2단 랭킹 — 행정(출결 류) 공지가 약한 키워드 과제(주차별 소감문·질의)에서
    # 단일 top-k를 점유해 진짜 명세 공지를 밀어내는 실측 회귀 대응. 명세 공지를
    # 먼저 k건 채우고, 행정 공지는 뒤에 붙인다(숨은 명세 주입에서는
    # spec_announcements()로 걸러지지만, 질의 resolver가 출결 공지 속 순번표
    # 링크를 계속 쓸 수 있어야 하므로 반환 목록에는 남긴다).
    top = rank_announcements(spec_announcements(anns), keywords, k=k)
    top += rank_announcements(
        [a for a in anns if is_logistics_announcement(a)], keywords, k=k)
    if include_replies and top and hasattr(adapter, "fill_replies"):
        try:
            # 답글(교수 추가 조건)은 명세 공지에만 의미가 있다 — 행정 공지 답글
            # 조회는 지연만 늘린다.
            adapter.fill_replies(spec_announcements(top))
        except Exception:
            pass  # 답글 실패는 본문만으로 진행(치명적 아님)
    return top


def is_logistics_announcement(a: Announcement) -> bool:
    """출결·좌석·성적 등 행정 공지인지(제목 기준, 결정적).

    순번표 시트 링크가 출결 공지 본문에 실리는 실데이터 때문에 순위화·질의
    resolver 입력에서는 빼지 않는다 — 숨은 명세(SourceDoc) 주입 직전에만 거른다."""
    return bool(_LOGISTICS_TITLE.search(a.subject or ""))


def spec_announcements(anns: List[Announcement]) -> List[Announcement]:
    """숨은 명세로 주입해도 되는 공지만 — 행정(출결 류) 공지 제외."""
    return [a for a in anns if not is_logistics_announcement(a)]


def announcements_to_sources(anns: List[Announcement]) -> List[SourceDoc]:
    """관련 공지를 Execution 맥락(SourceDoc)으로 — 교수 공지/답글을 근거로 인용 가능.

    본문 + 답글을 담되, 제목이 그대로 범례([자료N])에 보이게 한다."""
    out: List[SourceDoc] = []
    for a in anns:
        where = f"eTL 공지 위치: {a.url}" if a.url else "eTL 공지"
        head = f"eTL 공지 '{a.subject}'"
        if a.forum:
            head += f" ({a.forum})"
        if a.created_iso:
            head += f" — {a.created_iso}"
        parts = [head, where, "본문:", a.body or "(본문 없음)"]
        if a.replies:
            parts.append("답글(교수 추가 조건 등):")
            parts.extend(f"- {r}" for r in a.replies)
        if getattr(a, "links", None):
            parts.append("본문 링크:")
            parts.extend(f"- {u}" for u in a.links)
        out.append(SourceDoc(title=f"[eTL 공지] {a.subject}", text="\n".join(parts),
                             url=a.url or ""))
    return out


def announcements_summary(anns: List[Announcement], limit: int = 5) -> List[dict]:
    """홈 '최신 공지' 표시용 가벼운 목록(제목·과목·날짜·URL)."""
    out = []
    for a in anns[:limit]:
        out.append({
            "subject": a.subject,
            "course": a.course_name or a.course_id,
            "date": a.created_iso,
            "url": a.url,
            "forum": a.forum,
        })
    return out
