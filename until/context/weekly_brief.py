"""주차별 세미나 안내 → 그 주차 초안의 원료(결정적 매칭 + 파일 파싱).

**왜 필요한가.** 전기·정보세미나처럼 매주 연사가 바뀌는 과목은 그 주차에 무엇을
다뤘는지가 **공지 첨부**(PDF·한글)에만 있다. 그런데 우리는 공지 본문만 읽고
첨부는 버려 왔다 — 그래서 「N주차 소감문」이 원료 없음(material_gap)으로 떨어져
"이 과제에서 본인의 '고찰' 한 가지: ___" 같은 빈 질문만 남았다(2026-08-23 실사용).
연사·주제를 알면 그 주차 소감문은 실제 내용을 근거로 쓸 수 있다.

**설계 원칙**
- 주차 매칭은 **결정적**이다. 과제 제목의 `N주차`와 공지의 `N주차`를 맞춘다.
  숫자가 없으면 아무것도 하지 않는다 — 최신 공지를 아무거나 집어 오면 다른
  주차의 연사를 그 주차 소감문에 넣는 사고가 난다.
- 첨부는 **그 주차 공지의 것만** 쓴다. 과목 전체 자료를 긁어 오지 않는다.
- 무엇이든 실패하면 빈 목록이다(예외를 밖으로 내지 않는다). 원료가 없으면
  지금까지처럼 되묻는 흐름으로 돌아가면 된다.
- 별도 '주제 추출기'를 만들지 않는다. 파일 텍스트를 원료로 넣어 주면 그다음은
  기존 파이프라인이 한다 — 추출기를 하나 더 두면 그게 또 틀린다.
"""
from __future__ import annotations

import re
from typing import Callable, List, Optional

from ..llm.base import SourceDoc

_WEEK_RE = re.compile(r"(?<!\d)(\d{1,2})\s*주\s*차")

#: 첨부 이름이 이 확장자면 본문을 읽어 볼 가치가 있다(ingest가 다 읽는다).
#: 한글 파일이 흔하다 — eTL 공지 첨부의 상당수가 hwp/hwpx다(사용자 확인).
_READABLE = (".pdf", ".hwp", ".hwpx", ".docx", ".pptx", ".txt", ".md")

#: 한 주차에 붙는 안내는 보통 한둘이다. 상한을 두어 토큰·지연을 막는다.
_MAX_FILES = 2
_MAX_CHARS = 3000


def week_of(text: str) -> Optional[int]:
    """텍스트에서 'N주차'의 N. 없으면 None(추측하지 않는다)."""
    m = _WEEK_RE.search(text or "")
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= 20 else None


def week_conflicts(assignment_text: str, source_title: str) -> bool:
    """과제와 자료가 **서로 다른 주차**를 가리키면 True. 한쪽이라도 없으면 False.

    한쪽에 주차가 없으면(강의계획서·일반 공지) 충돌이 아니다 — 모르면 버리지 않는다.
    이 판정으로 걸러지는 건 '12주차 과제에 붙은 10주차 자료'처럼 **둘 다 주차를
    말하는데 서로 다른** 경우뿐이다.

    왜 필요한가(실사용 2026-08-23, 통계학실험 「12주차 출석」): 과제 설명이 비어
    있는데 과목의 다른 주차 자료가 자동 주입돼 '원료 있음'으로 판정됐고, 모델은 그
    자료를 근거로 **관찰한 적 없는 사실**을 썼다 — "모든 대상 학생이 해당 주차에
    정상적으로 출석했음을 나타내는 기록으로 남았다". Until은 출석 데이터를 본 적이
    없다. 다른 주차 자료는 이 과제의 원료가 아니다.
    """
    a, s = week_of(assignment_text), week_of(source_title)
    return a is not None and s is not None and a != s


def drop_week_mismatched(assignment_text: str, sources: List) -> List:
    """과제와 주차가 어긋나는 자료를 뺀 목록(순서 유지). 제목 기준으로만 본다."""
    if week_of(assignment_text) is None:
        return list(sources)
    return [s for s in sources
            if not week_conflicts(assignment_text, str(getattr(s, "title", "") or ""))]


def _mentions_week(ann, week: int) -> bool:
    """공지가 그 주차 것인가 — 제목 우선, 없으면 본문."""
    subject = getattr(ann, "subject", "") or ""
    if week_of(subject) == week:
        return True
    # 제목에 주차가 없으면 본문 첫머리에서만 본다. 본문 전체를 뒤지면 지난
    # 주차를 언급한 공지가 전부 걸린다.
    return week_of((getattr(ann, "body", "") or "")[:400]) == week


def week_announcements(anns: List, week: int) -> List:
    """그 주차 공지만(최신순 입력 순서 유지)."""
    if not week:
        return []
    return [a for a in (anns or []) if _mentions_week(a, week)]


def readable_attachments(ann) -> List:
    """읽어 볼 만한 첨부만. 이름에 확장자가 없으면 그대로 둔다 —
    다운로드 뒤 매직 바이트로 유형을 붙이는 경로가 이미 있다."""
    out = []
    for att in (getattr(ann, "attachments", None) or []):
        name = str(getattr(att, "name", "") or "").lower()
        if not name or name.endswith(_READABLE) or "." not in name:
            out.append(att)
    return out


def weekly_brief_sources(anns: List, assignment_title: str,
                         fetch_text: Callable[[object], str]) -> List[SourceDoc]:
    """그 주차 공지 첨부를 읽어 SourceDoc으로. 못 찾으면 빈 목록.

    `fetch_text(attachment) -> str`은 호출부가 준다(다운로드·파싱은 네트워크
    계층의 일이고, 이 모듈은 순수·오프라인으로 남는다 — inquiry_assignment와
    같은 경계).
    """
    week = week_of(assignment_title)
    if week is None:
        return []
    out: List[SourceDoc] = []
    for ann in week_announcements(anns, week):
        for att in readable_attachments(ann):
            if len(out) >= _MAX_FILES:
                return out
            try:
                text = " ".join((fetch_text(att) or "").split())
            except Exception:
                continue
            if len(text) < 80:
                continue      # 표지 한 장짜리는 원료가 아니다
            name = str(getattr(att, "name", "") or "첨부")
            out.append(SourceDoc(
                title=f"[{week}주차 안내] {name}",
                text=(f"{week}주차 세미나 안내 첨부에서 읽은 내용입니다. "
                      "이 주차에 실제로 다룬 주제·연사를 여기서만 확인하고, "
                      "여기 없는 내용은 지어내지 마세요.\n\n"
                      + text[:_MAX_CHARS]),
                url=str(getattr(att, "url", "") or getattr(ann, "url", "") or "")))
    return out
