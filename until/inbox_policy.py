"""eTL 인박스의 필터·정렬·마감·자동 선택 정책(결정적, 웹 독립)."""
from __future__ import annotations

import re
from datetime import datetime


def is_past_due(due_at) -> bool:
    """마감(ISO 문자열)이 이미 지났는지. 없음/형식 불명은 숨기지 않는다."""
    if not due_at:
        return False
    raw = str(due_at).strip().replace("Z", "+00:00")
    try:
        due = datetime.fromisoformat(raw)
    except ValueError:
        return False
    now = datetime.now(due.tzinfo) if due.tzinfo else datetime.now()
    return due < now


def dday_label(due_at) -> tuple[str, bool]:
    """사용자 로컬 달력 기준 (라벨, 임박 여부)."""
    if not due_at:
        return "", False
    raw = str(due_at).strip().replace("Z", "+00:00")
    try:
        due = datetime.fromisoformat(raw)
    except ValueError:
        return "", False
    if due.tzinfo:
        due = due.astimezone()
        now = datetime.now().astimezone()
    else:
        now = datetime.now()
    if due < now:
        return "지남", False
    days = (due.date() - now.date()).days
    if days == 0:
        return "D-DAY", True
    return f"D-{days}", days <= 3


def filter_sort_inbox(items: list, *, status: str = "all", hide_past: bool = False,
                      term: str = "", sort: str = "due") -> list:
    """상태·학기·기한 필터와 정렬을 적용한 새 목록을 반환한다."""
    out = list(items)
    if status == "todo":
        out = [item for item in out if not getattr(item, "submitted", False)]
    elif status == "done":
        out = [item for item in out if getattr(item, "submitted", False)]
    if hide_past:
        out = [item for item in out if not is_past_due(getattr(item, "due_at", None))]
    if term:
        out = [item for item in out if (getattr(item, "term", "") or "") == term]
    far = "9999-12-31T23:59:59Z"
    if sort == "due_desc":
        out.sort(key=lambda item: ((item.due_at or ""), item.course_name, item.title),
                 reverse=True)
    elif sort == "course":
        out.sort(key=lambda item: (item.course_name, (item.due_at or far), item.title))
    elif sort == "term":
        out.sort(key=lambda item: ((getattr(item, "term", "") or ""),
                                   (item.due_at or far), item.title))
    else:
        out.sort(key=lambda item: ((item.due_at or far), item.course_name, item.title))
    return out


_ATTENDANCE_TITLE = re.compile(r"출석|출결")
_TERM = re.compile(r"(20\d{2})\s*-\s*(\d)")  # 과목명 속 '2026-1' 학기 표기


def _term_recency(item) -> int:
    """과목명에서 학기를 읽어 최신일수록 작은 값(정렬용). 표기 없으면 중간값."""
    m = _TERM.search(getattr(item, "course_name", "") or "")
    return -(int(m.group(1)) * 10 + int(m.group(2))) if m else 0


def pick_practice(items):
    """연습 딸깍 대상: 이미 낸(또는 기한 지난) 과제 중 가장 최근 마감.

    새 과제가 없는 시기(방학·개강 전)에 흐름을 다시 돌려 보거나, 낸 과제를
    다른 각도로 재작성해 보는 명시적 사용자 행동. 출석·출결과 비실행 항목은
    여기서도 제외하고, 완료 과제가 하나도 없으면 전체에서 고른다."""
    if not items:
        return None
    doable = [x for x in items
              if getattr(x, "actionable", True)
              and not _ATTENDANCE_TITLE.search(getattr(x, "title", "") or "")]
    pool = doable or list(items)
    done = [x for x in pool if getattr(x, "submitted", False)
            or is_past_due(getattr(x, "due_at", None))]
    pool = done or pool
    return max(pool, key=lambda i: (bool(getattr(i, "due_at", None)),
                                    str(getattr(i, "due_at", "") or ""),
                                    -_term_recency(i)))


def pick_best(items):
    """바로 초안 대상: 실행 가능 우선, 미제출·기한 안 지남·마감 임박 순.

    출석·성적부 자리표시(actionable=False)와 '출석/출결' 제목 항목은 문서로
    해결할 수 없어 강등한다 — 딸깍이 헛과제를 고르던 실사용 회귀. 마감이 같거나
    없으면 최신 학기 과목을 우선한다(작년 과목의 잔여 항목이 첫 자리를 차지하던
    실측 문제). 실행 가능 항목이 하나도 없으면 전체에서 고른다(빈손 방지)."""
    if not items:
        return None
    doable = [x for x in items if getattr(x, "actionable", True)]

    def key(item):
        return (bool(getattr(item, "submitted", False)),
                is_past_due(getattr(item, "due_at", None)),
                bool(_ATTENDANCE_TITLE.search(getattr(item, "title", "") or "")),
                not getattr(item, "due_at", None),
                str(getattr(item, "due_at", None) or "9999"),
                _term_recency(item))

    return min(doable or items, key=key)
