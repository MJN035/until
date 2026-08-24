"""
Canvas REST API 어댑터 — 권장 제품 경로(브라우저 스크래핑 대안).

서울대 eTL은 Canvas LMS다. 브라우저로 페이지를 긁는 대신, 학생이 직접 발급한
액세스 토큰으로 Canvas 공식 REST API를 호출한다.
  GET /api/v1/courses/{cid}/assignments/{aid}  → {name, description(HTML), due_at, ...}
인증: 계정 > 설정 > '+ 새 액세스 토큰'에서 발급한 토큰을 Authorization: Bearer 로 전달.
(비밀번호·SSO 자동화 불필요 — 학생 토큰 접근이 Moodle보다 쉽다.)

- 순수 파서 parse_canvas_api_assignment(JSON dict → RawAssignment)는 네트워크 없이 테스트.
- CanvasApiAdapter 는 표준 라이브러리 urllib 만으로 호출/다운로드(의존성 0).
- BrowserAdapter 와 동일 인터페이스라 EtlSource 가 그대로 재사용한다.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from email.message import Message
import mimetypes
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from .models import Attachment, RawAssignment, CourseRef, AssignmentRef, safe_filename


def _download_name(display_name: str, headers) -> str:
    """Recover a real filename when Canvas link text has no extension."""
    original = safe_filename(display_name)
    if Path(original).suffix:
        return original
    disposition = ""
    content_type = ""
    try:
        disposition = headers.get("Content-Disposition", "")
        content_type = headers.get_content_type()
    except (AttributeError, TypeError):
        if headers:
            disposition = headers.get("Content-Disposition", "")
            content_type = str(headers.get("Content-Type", "")).split(";", 1)[0]
    if disposition:
        msg = Message()
        msg["Content-Disposition"] = disposition
        recovered = msg.get_filename()
        if recovered:
            recovered = safe_filename(recovered)
            if Path(recovered).suffix:
                return recovered
    ext = mimetypes.guess_extension(content_type or "") or ""
    if ext and ext not in {".bin", ".exe"}:
        return safe_filename(original + ext)
    return original

_ASSIGN_URL_RE = re.compile(r"^(https?://[^/]+).*?/courses/(\d+)/assignments/(\d+)")
_FILE_ID_RE = re.compile(r"/files/(\d+)")
_NEXT_LINK_RE = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


def _next_link(link_header: str) -> str:
    """Canvas Link 헤더에서 rel="next" URL을 추출(없으면 빈 문자열)."""
    m = _NEXT_LINK_RE.search(link_header or "")
    return m.group(1) if m else ""


class _StripAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """리다이렉트 시 Authorization 헤더를 떼어 서명 URL 호스트의 403을 피한다."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.headers = {k: v for k, v in new.headers.items() if k.lower() != "authorization"}
            try:
                new.remove_header("Authorization")
            except Exception:
                pass
        return new


def _file_id(url: str) -> str:
    """첨부 URL에서 Canvas 파일 id를 뽑는다(없으면 빈 문자열)."""
    m = _FILE_ID_RE.search(url or "")
    return m.group(1) if m else ""


def parse_assignment_url(url: str) -> Tuple[str, str, str]:
    """과제 URL에서 (base_url, course_id, assignment_id)를 뽑는다."""
    m = _ASSIGN_URL_RE.match(url)
    if not m:
        raise ValueError(f"Canvas 과제 URL 형식이 아닙니다: {url!r}")
    return m.group(1), m.group(2), m.group(3)


def api_assignment_url(base_url: str, course_id: str, assignment_id: str) -> str:
    return f"{base_url.rstrip('/')}/api/v1/courses/{course_id}/assignments/{assignment_id}"


class _DescriptionParser(HTMLParser):
    """Canvas description HTML 조각에서 본문 텍스트와 파일 첨부를 추출한다."""

    def __init__(self) -> None:
        super().__init__()
        self.text_parts: List[str] = []
        self.attachments: List[Attachment] = []
        self._cur_href: Optional[str] = None
        self._cur_title = ""
        self._cur_text: List[str] = []
        self._cur_is_file = False
        self._block = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in {"p", "div", "br", "li"}:
            self._block = True
        if tag == "a":
            href = a.get("href", "") or ""
            api_endpoint = a.get("data-api-endpoint", "") or ""
            classes = set((a.get("class", "") or "").split())
            self._cur_href = href
            self._cur_title = a.get("title", "") or a.get("download", "") or ""
            self._cur_text = []
            self._cur_is_file = (
                "instructure_file_link" in classes
                or ("/files/" in href and "/download" in href)
                or ("/api/v1/" in api_endpoint and "/files/" in api_endpoint)
            )

    def handle_endtag(self, tag):
        if tag == "a" and self._cur_href is not None:
            text = unescape("".join(self._cur_text)).strip()
            if self._cur_is_file:
                self.attachments.append(
                    Attachment(name=_file_name(self._cur_title, text, self._cur_href),
                               url=self._cur_href)
                )
            self._cur_href = None
            self._cur_title = ""
            self._cur_text = []
            self._cur_is_file = False

    def handle_data(self, data):
        if self._block and self.text_parts and self.text_parts[-1] != "\n":
            self.text_parts.append("\n")
            self._block = False
        self.text_parts.append(data)
        if self._cur_href is not None:
            self._cur_text.append(data)


def _file_name(title: str, text: str, href: str) -> str:
    name = (title or text).strip()
    if name:
        return unescape(name)
    tail = urlparse(href).path.rstrip("/").rsplit("/", 1)[-1]
    return tail or "attachment"


def _description_to_text(html: str) -> Tuple[str, List[Attachment]]:
    p = _DescriptionParser()
    p.feed(html)
    text = unescape("".join(p.text_parts))
    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, p.attachments


def parse_canvas_api_assignment(data: dict, base_url: str,
                                course_name: str = "") -> RawAssignment:
    """Canvas API assignment JSON → RawAssignment (네트워크 없이 결정적).

    course_name은 호출부(어댑터)가 조회해 넘기는 사람이 읽는 과목명이다. 여기서
    'Canvas course 302199' 같은 **개발자용 폴백을 만들지 않는다** — 그 문자열은
    `to_files()`의 '과목:' 줄을 타고 LLM 입력과 제출 파일 본문에 그대로 실린다
    (실관측: 초안 본문에 'Canvas course 101'이 인용됨). due_at을 사람이 읽는
    표기로 바꾸는 것과 같은 이유로 소스 지점에서 막는다. 모르면 빈 문자열이고,
    `to_files()`가 '과목:' 줄 자체를 생략한다.
    """
    title = (data.get("name") or "(제목 없음)").strip()
    course = (course_name or "").strip()
    if not course:  # 응답에 course가 동봉된 경우(include[]=course)만 보조로 사용
        embedded = data.get("course")
        if isinstance(embedded, dict):
            course = (embedded.get("name") or "").strip()
    page_url = data.get("html_url") or base_url

    body, attachments = _description_to_text(data.get("description") or "")
    due = (data.get("due_at") or "").strip()
    description = (f"마감: {_readable_due(due)}\n\n{body}" if due else body) or "(과제 설명 없음)"

    # 첨부 URL은 절대경로로 정규화 + 파일 id 기준 중복 제거.
    # (같은 파일이 /files/123 미리보기 + /files/123/download 로 두 번 걸리는 흔한 케이스 합침)
    seen, atts = set(), []
    for att in attachments:
        url = urljoin(page_url, att.url)
        key = _file_id(url) or url
        if key in seen:
            continue
        seen.add(key)
        atts.append(Attachment(name=att.name, url=url))

    return RawAssignment(title=title, course=course, description=description,
                         attachments=atts, url=page_url)


def parse_canvas_files(data: list, base_url: str) -> List[Attachment]:
    """Canvas 파일 목록 API(JSON 배열) → Attachment 리스트(파일 id로 중복 제거)."""
    out: List[Attachment] = []
    seen = set()
    for f in data or []:
        if not isinstance(f, dict):
            continue
        url = urljoin(base_url, f.get("url") or "")
        if not url:
            continue
        fid = _file_id(url)
        key = fid or url
        if key in seen:
            continue
        seen.add(key)
        name = f.get("display_name") or f.get("filename") or _file_name("", "", url)
        out.append(Attachment(name=name, url=url))
    return out


def _course_ended(c: dict) -> bool:
    """지난 과목 판정 — 과목/학기 종료일이 지났거나 완료 상태면 True.

    eTL은 지난 학기 과목도 enrollment_state=active로 남겨 두는 경우가 많아
    목록이 과거 과목으로 뒤덮인다. 종료일 정보가 없으면 현재 과목으로 간주(fail-open).
    """
    if (c.get("workflow_state") or "").lower() == "completed":
        return True
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    for end in (c.get("end_at"), (c.get("term") or {}).get("end_at")):
        if not isinstance(end, str) or not end.strip():
            continue
        try:
            t = _dt.datetime.fromisoformat(end.strip().replace("Z", "+00:00"))
        except ValueError:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=_dt.timezone.utc)
        if t < now:
            return True
    return False


def parse_courses(data: list, include_past: bool = False) -> List[CourseRef]:
    """Canvas 과목 목록 API(JSON 배열) → CourseRef.

    이름 없는(접근 제한) 과목은 제외. **지난 과목**(종료일 경과·완료 상태)은 기본
    제외 — include_past=True면 ended 표시와 함께 포함('지난 학기' 필터용)."""
    out: List[CourseRef] = []
    seen = set()
    for c in data or []:
        if not isinstance(c, dict):  # 에러 객체의 키(문자열) 등 방어
            continue
        cid = str(c.get("id") or "").strip()
        name = (c.get("name") or "").strip()
        if not cid or not name or cid in seen:
            continue
        ended = _course_ended(c)
        if ended and not include_past:
            continue
        seen.add(cid)
        term = c.get("term") if isinstance(c.get("term"), dict) else {}
        out.append(CourseRef(id=cid, name=name,
                             term=(term.get("name") or "").strip(), ended=ended))
    return out


def _readable_due(iso: str) -> str:
    """Canvas due_at(UTC ISO) → '2026년 7월 27일(월) 오전 9시' (KST).

    ISO 원문을 설명문에 그대로 두면 초안 본문에 '2026-07-27T00:00:00Z'가 인용되는
    실관측이 있어, 소스 지점에서 사람이 읽는 표기로 바꾼다. 파싱 실패 시 원문 유지."""
    import datetime as _dt
    try:
        t = _dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    if t.tzinfo is None:
        t = t.replace(tzinfo=_dt.timezone.utc)
    kst = t.astimezone(_dt.timezone(_dt.timedelta(hours=9)))
    wd = "월화수목금토일"[kst.weekday()]
    ampm = "오전" if kst.hour < 12 else "오후"
    h12 = kst.hour % 12 or 12
    mins = f" {kst.minute}분" if kst.minute else ""
    return f"{kst.year}년 {kst.month}월 {kst.day}일({wd}) {ampm} {h12}시{mins}"


def _submitted(a: dict) -> bool:
    sub = a.get("submission") or {}
    state = (sub.get("workflow_state") or "").lower()
    return bool(sub.get("submitted_at")) or state in {"submitted", "graded", "complete"}


#: 온라인 제출물이 없는 submission_types — 성적부 자리표시(시험·총점·출석) 판정용.
_NON_ACTIONABLE_TYPES = {"none", "on_paper", "not_graded"}


def _actionable(types) -> bool:
    """submission_types로 '제출할 것 없는 성적부 행(기말고사·중간 총점·M1 등)' 판정.

    실코퍼스(148과제)에서 19%가 이런 자리표시였다 — 초안을 쓸 대상이 아니므로
    인박스·자동 선택에서 제외한다. 정보가 없으면 실행형으로 간주(fail-open)."""
    vals = {str(t).strip().lower() for t in (types or []) if str(t).strip()}
    return not vals or not vals <= _NON_ACTIONABLE_TYPES


def parse_assignments(data: list, base_url: str, course: "CourseRef | None" = None) -> List[AssignmentRef]:
    """Canvas 과제 목록 API(JSON 배열) → AssignmentRef."""
    out: List[AssignmentRef] = []
    for a in data or []:
        if not isinstance(a, dict):
            continue
        aid = str(a.get("id") or "").strip()
        title = (a.get("name") or "").strip() or "(제목 없음)"
        if not aid:
            continue
        cid = str(a.get("course_id") or (course.id if course else "")).strip()
        url = a.get("html_url") or (f"{base_url.rstrip('/')}/courses/{cid}/assignments/{aid}" if cid else "")
        out.append(AssignmentRef(
            id=aid, title=title, course_id=cid,
            course_name=(course.name if course else ""),
            url=url, due_at=(a.get("due_at") or "").strip(),
            submitted=_submitted(a),
            term=(getattr(course, "term", "") if course else ""),
            actionable=_actionable(a.get("submission_types")),
        ))
    return out


#: 문체 학습에 쓸 텍스트 첨부 확장자(발표 슬라이드·이미지 등은 문체 표본이 아님).
_VOICE_TEXT_EXTS = (".docx", ".hwpx", ".hwp", ".txt", ".md", ".pdf")


def parse_my_submissions(data: list, base_url: str) -> List[dict]:
    """Canvas 내 제출물 목록 API(JSON 배열, include[]=assignment) → 문체 표본 후보.

    반환: [{"submitted_at": ISO, "body": 온라인텍스트(HTML→텍스트, 없으면 ""),
           "attachments": [Attachment(텍스트 포맷만)]}] — 결정적, 네트워크 없음.
    전자동 수집의 안전 필터를 여기서 코드로 강제한다:
      · 미제출(submitted_at 없음) 제외
      · **조별 과제 제외**(assignment.group_category_id) — 팀 공동 작성물은 내 문체 아님
      · 첨부는 텍스트 포맷(.docx/.hwpx/.txt/.md/.pdf)만
    """
    out: List[dict] = []
    for s in data or []:
        if not isinstance(s, dict):
            continue
        if not (s.get("submitted_at") or "").strip():
            continue
        assign = s.get("assignment") if isinstance(s.get("assignment"), dict) else {}
        if assign.get("group_category_id"):
            continue
        body = ""
        if (s.get("submission_type") or "") == "online_text_entry" or s.get("body"):
            body, _ = _description_to_text(s.get("body") or "")
        atts: List[Attachment] = []
        for f in s.get("attachments") or []:
            if not isinstance(f, dict):
                continue
            name = (f.get("display_name") or f.get("filename") or "").strip()
            url = urljoin(base_url, f.get("url") or "")
            if not name or not url:
                continue
            if not name.lower().endswith(_VOICE_TEXT_EXTS):
                continue
            atts.append(Attachment(name=name, url=url))
        if body or atts:
            out.append({"submitted_at": (s.get("submitted_at") or "").strip(),
                        "body": body, "attachments": atts})
    return out


def parse_my_feedback(data: list) -> List[dict]:
    """Canvas 내 제출물 JSON(include[]=submission_comments·rubric_assessment·assignment)
    → 교수 피드백 항목. 결정적, 네트워크 없음.

    반환: [{"assignment": 과제명, "submitted_at": ISO, "grade": 표시 성적,
           "comments": [교수 코멘트…], "rubric": ["기준: 코멘트 (점수/만점)"…]}]
    · **조별 과제 제외**(assignment.group_category_id) — 팀원 코멘트가 '교수
      피드백'으로 섞이는 것 방지(parse_my_submissions와 같은 안전 필터).
    · 내 코멘트 제외(author_id == 제출자 user_id) — 교수·조교 피드백만.
      user_id를 알 수 없으면 코멘트는 수집하지 않는다(fail-closed; 루브릭은 유지).
    · 코멘트도 루브릭도 없는 제출물은 제외(피드백이 없으니 배울 것도 없음).
    """
    out: List[dict] = []
    for s in data or []:
        if not isinstance(s, dict):
            continue
        me = s.get("user_id")
        assign = s.get("assignment") if isinstance(s.get("assignment"), dict) else {}
        if assign.get("group_category_id"):
            continue  # 조별 과제 — 팀원 코멘트는 교수 피드백이 아님(개인정보 보호)
        comments: List[str] = []
        if me is not None:  # user_id 미상이면 내 코멘트를 거를 수 없음 → 수집 안 함
            for c in s.get("submission_comments") or []:
                if not isinstance(c, dict):
                    continue
                text = (c.get("comment") or "").strip()
                if not text:
                    continue
                if c.get("author_id") == me:
                    continue  # 내가 단 코멘트는 피드백이 아님
                comments.append(text)
        # 루브릭 평가 — 기준 id를 assignment.rubric의 이름·만점으로 해석.
        crit_by_id = {}
        for crit in assign.get("rubric") or []:
            if isinstance(crit, dict) and crit.get("id") is not None:
                crit_by_id[str(crit["id"])] = crit
        rubric_lines: List[str] = []
        ra = s.get("rubric_assessment")
        if isinstance(ra, dict):
            for cid, ev in ra.items():
                if not isinstance(ev, dict):
                    continue
                crit = crit_by_id.get(str(cid), {})
                name = (crit.get("description") or "기준").strip()
                note = (ev.get("comments") or "").strip()
                pts, full = ev.get("points"), crit.get("points")
                if isinstance(pts, (int, float)) and isinstance(full, (int, float)):
                    score = f" ({pts}/{full}점)"
                elif isinstance(pts, (int, float)):
                    score = f" ({pts}점)"  # 기준 매칭 실패해도 실점수는 살린다
                else:
                    score = ""
                if not crit and not note and not score:
                    continue  # 기준·코멘트·점수 전부 없음 — 정보 0인 항목은 스킵
                rubric_lines.append(f"{name}: {note}{score}" if note
                                    else f"{name}{score}")
        if not comments and not rubric_lines:
            continue
        out.append({
            "assignment": (assign.get("name") or "").strip() or "(과제 미상)",
            "submitted_at": (s.get("submitted_at") or "").strip(),
            "grade": str(s.get("grade") or "").strip(),
            "comments": comments,
            "rubric": rubric_lines,
        })
    return out


#: GraphQL 인박스 쿼리 — 과목→과제→내 제출 상태를 1콜에(REST N+1 대체).
GRAPHQL_INBOX_QUERY = """
query UntilInbox {
  allCourses {
    _id
    name
    term { name endAt }
    assignmentsConnection(first: 100) {
      nodes {
        _id
        name
        dueAt
        htmlUrl
        submissionTypes
        submissionsConnection(first: 1) { nodes { state submittedAt } }
      }
    }
  }
}"""


def parse_graphql_inbox(payload, base_url: str) -> List[AssignmentRef]:
    """Canvas GraphQL 응답 → AssignmentRef 목록(결정적, 네트워크 없음).

    지난 과목(term.endAt 경과)은 REST 경로(_course_ended)와 같은 기준으로 제외
    (정보 없으면 현재 과목으로 간주 — fail-open). errors가 있으면 예외 —
    호출부(EtlInbox)가 REST로 폴백한다."""
    if not isinstance(payload, dict):
        raise RuntimeError("GraphQL 응답이 dict가 아님")
    if payload.get("errors"):
        raise RuntimeError(f"GraphQL 오류: {payload['errors']!r:.200}")
    courses = ((payload.get("data") or {}).get("allCourses")) or []
    out: List[AssignmentRef] = []
    for c in courses:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("_id") or "").strip()
        cname = (c.get("name") or "").strip()
        term = c.get("term") if isinstance(c.get("term"), dict) else {}
        if not cid or not cname:
            continue
        if _course_ended({"end_at": None, "term": {"end_at": term.get("endAt")}}):
            continue
        nodes = ((c.get("assignmentsConnection") or {}).get("nodes")) or []
        for a in nodes:
            if not isinstance(a, dict):
                continue
            aid = str(a.get("_id") or "").strip()
            if not aid:
                continue
            subs = ((a.get("submissionsConnection") or {}).get("nodes")) or []
            submitted = any(
                isinstance(s, dict)
                and (bool(s.get("submittedAt"))
                     or (s.get("state") or "").lower() in {"submitted", "graded",
                                                           "complete"})
                for s in subs)
            out.append(AssignmentRef(
                id=aid, title=(a.get("name") or "").strip() or "(제목 없음)",
                course_id=cid, course_name=cname,
                url=(a.get("htmlUrl")
                     or f"{base_url.rstrip('/')}/courses/{cid}/assignments/{aid}"),
                due_at=(a.get("dueAt") or "").strip(), submitted=submitted,
                term=(term.get("name") or "").strip(),
                actionable=_actionable(a.get("submissionTypes")),
            ))
    return out


def parse_planner_items(data: list) -> List[dict]:
    """Canvas Planner API(/api/v1/planner/items, JSON 배열) → '그 외 마감' 항목.

    과제(assignment)는 인박스가 이미 다루므로 제외하고, 퀴즈·토론·이벤트·페이지
    등 나머지 할 일만 추린다(제출 완료 표시된 항목 제외). 마감 오름차순.
    반환: [{"type", "title", "course", "due_at", "url"}] — 결정적, 네트워크 없음."""
    out: List[dict] = []
    for it in data or []:
        if not isinstance(it, dict):
            continue
        ptype = (it.get("plannable_type") or "").strip()
        if not ptype or ptype == "assignment":
            continue
        sub = it.get("submissions")
        if isinstance(sub, dict) and (sub.get("submitted") or sub.get("graded")):
            continue
        pl = it.get("plannable") if isinstance(it.get("plannable"), dict) else {}
        title = (pl.get("title") or "").strip()
        if not title:
            continue
        due = (pl.get("due_at") or pl.get("todo_date")
               or it.get("plannable_date") or "").strip()
        out.append({"type": ptype, "title": title,
                    "course": (it.get("context_name") or "").strip(),
                    "due_at": due, "url": (it.get("html_url") or "").strip()})
    out.sort(key=lambda e: e.get("due_at") or "9999")
    return out


def parse_canvas_announcements(data: list, course: "CourseRef | None" = None) -> list:
    """Canvas 공지 API(/api/v1/announcements, JSON 배열) → Announcement 목록.

    결정적, 네트워크 없음. 본문 HTML은 텍스트로 변환. Moodle WS 어댑터의
    Announcement와 같은 모델을 써서 기존 공지 순위화·주입 경로를 그대로 재사용."""
    from .moodle_ws import Announcement
    out = []
    for a in data or []:
        if not isinstance(a, dict):
            continue
        title = (a.get("title") or "").strip()
        if not title:
            continue
        raw_html = a.get("message") or ""
        # 첨부를 버리지 않는다 — 주차별 세미나 안내가 공지 첨부(PDF·한글)로만
        # 오는 과목이 있다(전기·정보세미나 실측). 버리면 초안이 그 주차에
        # 무엇을 다뤘는지 영영 모른다.
        body, atts = _description_to_text(raw_html)
        links = []
        for href in re.findall(r'''href\s*=\s*["']([^"']+)["']''', raw_html,
                               flags=re.IGNORECASE):
            absolute = urljoin(a.get("html_url") or "", unescape(href).strip())
            if absolute.startswith(("http://", "https://")) and absolute not in links:
                links.append(absolute)
        # display_name은 실명이며 역할 근거가 아니다. Announcement에 넣기 전에 폐기.
        from .moodle_ws import announcement_author_role
        author = announcement_author_role(a.get("author"))
        out.append(Announcement(
            subject=title, body=body, author=author,
            created_iso=(a.get("posted_at") or "").strip(),
            forum="공지사항",
            course_id=(course.id if course else ""),
            course_name=(course.name if course else ""),
            url=(a.get("html_url") or "").strip(),
            links=links,
            attachments=list(atts),
        ))
    return out


def parse_modules(data: list, base_url: str) -> List[Attachment]:
    """Canvas 모듈 API(include[]=items, JSON 배열) → 모듈 항목을 Attachment(name,url)로.

    파일/페이지/링크 등 제목 있는 항목을 모은다(과제 자료 후보). 본문은 받지 않는다."""
    out: List[Attachment] = []
    seen = set()
    for mod in data or []:
        if not isinstance(mod, dict):
            continue
        mod_name = (mod.get("name") or "").strip()
        for it in mod.get("items") or []:
            title = (it.get("title") or "").strip()
            if not title:
                continue
            url = urljoin(base_url, it.get("html_url") or it.get("url") or "")
            key = (title, url)
            if key in seen:
                continue
            seen.add(key)
            label = f"{title}" + (f" [{mod_name}]" if mod_name else "")
            out.append(Attachment(name=label, url=url))
    return out


def parse_discussion_topics(data: list, base_url: str) -> List[dict]:
    """Canvas 일반 토론/코딩 게시판 목록 → 분산 명세 후보 평문."""
    out = []
    for row in data or []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        body, _ = _description_to_text(row.get("message") or "")
        out.append({"title": title, "body": body,
                    "url": urljoin(base_url, row.get("html_url") or ""),
                    "posted_at": str(row.get("posted_at") or "")})
    return out


class CanvasApiAdapter:
    """Canvas REST API 라이브 어댑터(urllib만 사용). BrowserAdapter 인터페이스 호환."""

    def __init__(self, token: Optional[str] = None, timeout: float = 30.0,
                 include_course_files: bool = False):
        self.token = token or os.getenv("UNTIL_CANVAS_TOKEN", "")
        self.timeout = timeout
        # description에 안 걸린 첨부를 코스 파일 목록에서 보강(opt-in; 파일 id로 중복 제거).
        self.include_course_files = include_course_files
        if not self.token:
            raise ValueError(
                "Canvas 액세스 토큰이 필요합니다. 계정 > 설정 > '새 액세스 토큰'에서 발급 후 "
                "UNTIL_CANVAS_TOKEN 환경변수로 전달하거나 CanvasApiAdapter(token=...)로 주입하세요."
            )

    def _request(self, url: str) -> urllib.request.Request:
        return urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self.token}",
                          "Accept": "application/json+canvas-string-ids, application/json"},
        )

    def _open(self, url: str):
        """urlopen + 인증 실패(401/403)를 사람이 읽을 수 있는 메시지로 변환."""
        try:
            return urllib.request.urlopen(self._request(url), timeout=self.timeout)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise RuntimeError(
                    "eTL 인증 실패(토큰 무효/만료). UNTIL_CANVAS_TOKEN을 다시 발급해 확인하세요."
                ) from e
            raise

    @staticmethod
    def _decode_json(body: bytes, url: str):
        """JSON 파싱 + 비-JSON(로그인 HTML 등) 응답을 명확한 에러로 변환."""
        try:
            return json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise RuntimeError(
                "eTL가 JSON이 아닌 응답을 보냈습니다(로그인 만료·토큰 무효 가능). "
                f"URL: {url}"
            ) from e

    def _get_json(self, url: str):
        with self._open(url) as r:
            body = r.read()
        return self._decode_json(body, url)

    def _get_with_next(self, url: str):
        """JSON과 다음 페이지 URL(Canvas Link 헤더의 rel=next)을 함께 반환."""
        with self._open(url) as r:
            body = r.read()
            hdrs = getattr(r, "headers", None)
            link = hdrs.get("Link", "") if hdrs is not None else ""
        return self._decode_json(body, url), _next_link(link)

    def _get_paginated(self, url: str, cap_pages: int = 12) -> list:
        """list 엔드포인트를 끝까지(또는 cap_pages까지) 따라가 합친다. 100개 초과 누락 방지."""
        out: list = []
        pages = 0
        while url and pages < cap_pages:
            data, url = self._get_with_next(url)
            if not isinstance(data, list):
                return data  # 예상 밖(단일 객체) — 그대로 반환
            out.extend(data)
            pages += 1
        return out

    def list_courses(self, base_url: str,
                     include_past: bool = False) -> List[CourseRef]:
        """수강 과목 목록 — 기본은 지난 과목 제외, include_past=True면 지난 학기 포함."""
        api = (f"{base_url.rstrip('/')}/api/v1/courses"
               "?enrollment_state=active&include[]=term&per_page=100")
        return parse_courses(self._get_paginated(api), include_past=include_past)

    def get_course_name(self, course_id: str, base_url: str) -> str:
        """과목 하나의 사람이 읽는 이름 — 실패하면 빈 문자열(비치명적).

        과제 상세 API는 course_id만 주고 과목명을 주지 않는다. 이름을 못 구하면
        과제 문서에 '과목:' 줄이 아예 안 붙을 뿐, 수집은 계속된다."""
        if not str(course_id or "").strip():
            return ""
        try:
            data = self._get_json(
                f"{base_url.rstrip('/')}/api/v1/courses/{course_id}")
        except Exception:
            return ""
        return (data.get("name") or "").strip() if isinstance(data, dict) else ""

    def get_self_profile(self, base_url: str) -> dict:
        """내 프로필(/users/self/profile — name·primary_email 등).

        LMS가 이미 아는 값을 사용자에게 되묻지 않기 위한 조회. 실패는 호출부에서
        비치명적으로 다룬다(프로필 자동 보충은 편의 기능).
        """
        data = self._get_json(f"{base_url.rstrip('/')}/api/v1/users/self/profile")
        return data if isinstance(data, dict) else {}

    def fetch_public_text(self, url: str) -> str:
        """질의 배정에 필요한 공개 문서만 무인증·읽기 전용으로 조회한다."""
        p = urlparse(url)
        allowed = {"docs.google.com", "ece.snu.ac.kr"}
        if p.scheme != "https" or p.hostname not in allowed:
            raise ValueError("허용되지 않은 공개 자료 호스트")
        req = urllib.request.Request(url, headers={"User-Agent": "Until/1.9"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            if urlparse(r.geturl()).hostname not in allowed:
                raise ValueError("공개 자료가 허용되지 않은 호스트로 이동했습니다")
            raw = r.read(2_000_001)
            charset = r.headers.get_content_charset()
        if len(raw) > 2_000_000:
            raise ValueError("공개 자료가 2MB 상한을 초과했습니다")
        return raw.decode(charset or "utf-8", errors="replace")

    def list_assignments(self, course: CourseRef, base_url: str,
                         bucket: str | None = None) -> List[AssignmentRef]:
        """과목별 과제 목록. bucket='upcoming'|'unsubmitted'|'past' 등(Canvas 지원)."""
        api = (f"{base_url.rstrip('/')}/api/v1/courses/{course.id}/assignments"
               "?per_page=100&include[]=submission")
        if bucket:
            api += f"&bucket={bucket}"
        return parse_assignments(self._get_paginated(api), base_url, course=course)

    def graphql(self, base_url: str, query: str, variables: Optional[dict] = None):
        """POST /api/graphql — 응답 JSON(dict). 인증 실패는 _open과 같은 메시지."""
        body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        url = f"{base_url.rstrip('/')}/api/graphql"
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json",
                     "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise RuntimeError(
                    "eTL 인증 실패(토큰 무효/만료). UNTIL_CANVAS_TOKEN을 다시 발급해 확인하세요."
                ) from e
            raise
        return self._decode_json(raw, url)

    def list_assignments_graphql(self, base_url: str) -> List[AssignmentRef]:
        """인박스 1콜 — GraphQL로 과목·과제·내 제출 상태를 한 번에.

        스키마 미지원 인스턴스에선 예외/빈 목록 → 호출부(EtlInbox)가 REST 폴백."""
        return parse_graphql_inbox(self.graphql(base_url, GRAPHQL_INBOX_QUERY),
                                   base_url)

    def my_submissions_json(self, course_id: str, base_url: str, *,
                            include_feedback: bool = True) -> list:
        """내 제출물 원본 JSON. 개인 자동학습만 피드백 필드를 명시 요청한다.

        팀원 코퍼스 수집은 ``include_feedback=False``로 교수 코멘트·루브릭을
        응답에 포함시키지 않는다. 기본 True는 기존 웹 개인 자동학습 계약을 유지한다.
        """
        api = (f"{base_url.rstrip('/')}/api/v1/courses/{course_id}/students/submissions"
               "?student_ids[]=self&include[]=assignment")
        if include_feedback:
            api += "&include[]=submission_comments&include[]=rubric_assessment"
        api += "&per_page=100"
        return self._get_paginated(api)

    def list_my_submissions(self, course_id: str, base_url: str) -> List[dict]:
        """내가 이 과목에 제출한 과제들(문체 학습 표본 후보) — parse_my_submissions 참고."""
        return parse_my_submissions(self.my_submissions_json(course_id, base_url), base_url)

    def list_my_submissions_with_counts(self, course_id: str,
                                        base_url: str) -> tuple[List[dict], int]:
        """문체 후보와 Canvas가 돌려준 실제 제출 완료 건수를 함께 반환한다.

        UI에서 학습 상한(표본 30개)을 전체 제출물 수처럼 보이지 않게 하기 위한
        계측용 경로다. 원문은 기존과 마찬가지로 저장하지 않는다.
        """
        raw = self.my_submissions_json(course_id, base_url)
        submitted = sum(
            1 for row in raw
            if isinstance(row, dict) and str(row.get("submitted_at") or "").strip()
        )
        return parse_my_submissions(raw, base_url), submitted

    def list_my_feedback(self, course_id: str, base_url: str) -> List[dict]:
        """내 제출물에 교수가 남긴 코멘트·루브릭 평가 — parse_my_feedback 참고."""
        return parse_my_feedback(self.my_submissions_json(course_id, base_url))

    def collect_announcements(self, course: CourseRef, *, limit: int = 5,
                              news_only: bool = True,
                              include_replies: bool = False) -> list:
        """과목 공지 최신순 limit건 — Moodle WS 어댑터와 동일 프로토콜.

        Canvas /api/v1/announcements는 공지 전용이라 news_only는 항상 참이고,
        답글은 별도 API라 include_replies는 이 어댑터에선 무시한다(빈 replies).
        프로토콜(Moodle 호환) 시그니처에 base_url이 없어 인스턴스 기본
        (UNTIL_ETL_BASE env → SNU eTL)을 쓴다."""
        import datetime as _dt
        from .discovery import SNU_ETL_BASE
        base = (os.getenv("UNTIL_ETL_BASE") or SNU_ETL_BASE).strip()
        # Canvas announcements API의 기본 날짜 창은 과거 과목 공지를 0건으로 만든다.
        # 과목 하나로 이미 범위가 좁으므로 최근 5년~향후 1년을 명시해 종강 뒤에도
        # 과제 안내를 회수한다. 페이지·최종 limit 상한은 그대로 유지한다.
        today = _dt.date.today()
        start = today.replace(year=today.year - 5).isoformat()
        end = today.replace(year=today.year + 1).isoformat()
        api = (f"{base.rstrip('/')}/api/v1/announcements"
               f"?context_codes[]=course_{course.id}&start_date={start}"
               f"&end_date={end}&per_page=100")
        anns = parse_canvas_announcements(self._get_paginated(api, cap_pages=2), course)
        anns.sort(key=lambda a: a.created_iso or "", reverse=True)
        return anns[:limit]

    def list_planner_items(self, base_url: str,
                           start_date: Optional[str] = None) -> List[dict]:
        """플래너 할 일(과제 외 퀴즈·토론·이벤트) — 오늘부터, 학생 통합 뷰 API."""
        import datetime as _dt
        start = (start_date or _dt.date.today().isoformat()).strip()
        api = (f"{base_url.rstrip('/')}/api/v1/planner/items"
               f"?start_date={start}&per_page=50")
        return parse_planner_items(self._get_paginated(api, cap_pages=2))

    def list_modules(self, course_id: str, base_url: str) -> List[Attachment]:
        """과목 모듈 항목 목록(자료 후보)."""
        api = (f"{base_url.rstrip('/')}/api/v1/courses/{course_id}/modules"
               "?include[]=items&per_page=100")
        return parse_modules(self._get_paginated(api), base_url)

    def list_course_files(self, course_id: str, base_url: str) -> List[Attachment]:
        """GET /api/v1/courses/{cid}/files → 코스 파일 목록(Attachment)."""
        api = f"{base_url.rstrip('/')}/api/v1/courses/{course_id}/files"
        return parse_canvas_files(self._get_paginated(api), base_url)

    def list_discussion_topics(self, course_id: str, base_url: str) -> List[dict]:
        """과목 일반 토론/코딩 게시글(공지 제외 포함) — 읽기 전용 GET."""
        api = (f"{base_url.rstrip('/')}/api/v1/courses/{course_id}/discussion_topics"
               "?per_page=100&only_announcements=false")
        return parse_discussion_topics(self._get_paginated(api), base_url)

    def fetch_assignment(self, url: str) -> RawAssignment:
        base, cid, aid = parse_assignment_url(url)
        raw = parse_canvas_api_assignment(
            self._get_json(api_assignment_url(base, cid, aid)), base,
            course_name=self.get_course_name(cid, base))
        if self.include_course_files:
            seen = {_file_id(a.url) or a.url for a in raw.attachments}
            try:
                for att in self.list_course_files(cid, base):
                    key = _file_id(att.url) or att.url
                    if key not in seen:
                        seen.add(key)
                        raw.attachments.append(att)
            except Exception:
                pass  # 파일 목록 조회 실패는 치명적 아님 — description 첨부만으로 진행.
        return raw

    def download(self, attachment: Attachment, dest_dir: str) -> str:
        # 1차: 토큰으로 직접. Canvas 파일은 서명 URL로 302되며, 그때 Bearer를 같이 보내면
        # 서명 호스트가 403을 준다 → 403이면 인증을 빼고(리다이렉트에도 미부착) 재시도.
        try:
            with urllib.request.urlopen(self._request(attachment.url), timeout=self.timeout) as r:
                body = r.read()
                headers = getattr(r, "headers", None)
        except urllib.error.HTTPError:
            opener = urllib.request.build_opener(_StripAuthOnRedirect())
            with opener.open(urllib.request.Request(attachment.url), timeout=self.timeout) as r:
                body = r.read()
                headers = getattr(r, "headers", None)
        dest = Path(dest_dir) / _download_name(attachment.name, headers)
        dest.write_bytes(body)
        return str(dest)
