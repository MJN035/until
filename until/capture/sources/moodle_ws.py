"""
Moodle Web Services(REST) 클라이언트 — **읽기 전용, allowlist 강제**.

서울대 eTL은 Moodle이다. 학생 토큰으로 Moodle WS REST 엔드포인트를 호출한다:
  {base}/webservice/rest/server.php?wstoken=TOKEN&moodlewsrestformat=json
     &wsfunction=<함수>&<파라미터...>

⚠️ 절대 원칙 — until은 읽기 전용이다(협상 대상 아님).
토큰 권한상 과제 최종 제출·퀴즈 응시·포럼 글쓰기·쪽지 발송 같은 **쓰기 함수가
호출 "가능"하지만 이 클라이언트는 그것을 코드 레벨에서 영구 차단한다.**
차단 방식 = **allowlist 강제**: `call()`은 READ_ALLOWLIST에 명시된 읽기 함수만
호출하고, 그 밖의 모든 함수(=쓰기 함수 포함)는 네트워크로 나가기 전에 예외로 막는다.
쓰기 함수를 새로 '허용'하려면 이 파일의 allowlist를 사람이 직접 고쳐야 하며,
WRITE_DENYLIST에 오른 함수는 allowlist에 넣어도 이중 방어로 거부된다.

- 순수 함수(check_site_info_error / activated_functions 등)는 네트워크 없이 테스트 가능.
- 클라이언트는 표준 라이브러리 urllib 만으로 호출(의존성 0).
- 파이프라인 코어는 이 클라이언트를 모른다(BrowserAdapter 패턴과 같은 소스 계층).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from dataclasses import dataclass, field

from .models import (
    Attachment, RawAssignment, CourseRef, AssignmentRef, safe_filename,
)


@dataclass
class Announcement:
    """eTL 공지/포럼 글 1건(4번). 본문 + 답글로 '숨은 명세'까지 담는다."""
    subject: str
    body: str
    author: str = "unknown"          # 역할 라벨만. 실명은 파서 경계에서 폐기.
    created_iso: str = ""          # ISO8601(정렬·표시용), 없으면 ""
    forum: str = ""                # 포럼 이름(공지사항/Q&A 등)
    course_id: str = ""
    course_name: str = ""
    url: str = ""
    replies: List[str] = field(default_factory=list)  # 답글 본문(교수 추가 조건 등)
    links: List[str] = field(default_factory=list)    # 본문 외부 링크(순번표 등)
    # 공지에 붙은 첨부(주차별 세미나 안내 PDF·한글 등). 예전에는 파서가 뽑아
    # 놓고 버렸다 — 그래서 "연사가 공지에 올라와 있는데 초안이 모른다"가 됐다.
    attachments: List[Any] = field(default_factory=list)


ANNOUNCEMENT_AUTHOR_ROLES = frozenset({"instructor", "ta", "student", "unknown"})


def announcement_author_role(value: Any) -> str:
    """명시된 작성자 역할 라벨만 보존하고 이름 등 자유 문자열은 폐기한다.

    Moodle/Canvas의 이름 필드는 역할의 근거가 아니다. 새 개인정보 조회 없이 응답에
    이미 정확한 역할 열거형이 있을 때만 보존하고, 그 밖에는 ``unknown``으로 닫는다.
    """
    candidates = []
    if isinstance(value, dict):
        candidates = [value.get("role_label"), value.get("role")]
    elif isinstance(value, str):
        candidates = [value]
    for candidate in candidates:
        role = str(candidate or "").strip().lower()
        if role in ANNOUNCEMENT_AUTHOR_ROLES:
            return role
    return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# 0번 — 읽기 함수 allowlist / 쓰기 함수 영구 차단 목록
# ─────────────────────────────────────────────────────────────────────────────
# until이 호출할 수 있는 **읽기 전용** Moodle WS 함수. 여기 없는 함수는 call()이
# 거부한다(쓰기 함수는 애초에 여기 못 들어온다). 새 읽기 함수를 쓰려면 여기 추가.
READ_ALLOWLIST: frozenset[str] = frozenset({
    # 사이트/함수 지형 조사(최초 1회 활성 함수 확인)
    "core_webservice_get_site_info",
    # 과목·강의 구조(주차별 섹션 + 모듈 + fileurl) — "가장 중요"
    "core_course_get_contents",
    "core_course_get_courses",
    "core_enrol_get_users_courses",
    "core_course_get_courses_by_field",
    # 과제 본문·마감·첨부·제출 상태
    "mod_assign_get_assignments",
    "mod_assign_get_submission_status",
    # 포럼(공지·Q&A) — "숨은 명세 소스"
    "mod_forum_get_forums_by_courses",
    "mod_forum_get_forum_discussions",
    "mod_forum_get_forum_discussions_paginated",
    "mod_forum_get_discussion_posts",
    # 섹션 설명·페이지 본문
    "mod_label_get_labels_by_courses",
    "mod_page_get_pages_by_courses",
    # 강의자료(파일/폴더) 목록
    "mod_resource_get_resources_by_courses",
    "mod_folder_get_folders_by_courses",
    # 마감 이벤트(정렬 기준)
    "core_calendar_get_action_events_by_courses",
    "core_calendar_get_calendar_events",
})

# 토큰상 호출 "가능"하지만 until에서 **영구 미사용**인 쓰기 함수.
# allowlist 강제만으로도 차단되지만, 실수로 allowlist에 추가되는 것을 막는 이중 방어 +
# '왜 막혔는지'를 사람이 읽는 에러로 설명하기 위한 명시 목록이다.
WRITE_DENYLIST: frozenset[str] = frozenset({
    "mod_assign_save_submission",        # 과제 초안 저장(서버)
    "mod_assign_submit_for_grading",     # 과제 최종 제출
    "mod_quiz_start_attempt",            # 퀴즈 응시 시작
    "mod_quiz_process_attempt",          # 퀴즈 응답 제출
    "mod_quiz_finish_attempt",           # 퀴즈 제출 완료
    "mod_forum_add_discussion",          # 포럼 새 글
    "mod_forum_add_discussion_post",     # 포럼 답글
    "core_message_send_instant_messages",  # 쪽지 발송
})


class WriteFunctionBlocked(RuntimeError):
    """쓰기(또는 allowlist 밖) 함수 호출 시도 — until 읽기 전용 원칙 위반 차단."""


def assert_read_only(wsfunction: str) -> None:
    """이 함수가 호출 가능한(=읽기 allowlist) 함수인지 검사. 아니면 예외.

    쓰기 함수/미등록 함수는 네트워크로 나가기 전에 여기서 막힌다(0번 강제 지점).
    """
    fn = (wsfunction or "").strip()
    if fn in WRITE_DENYLIST:
        raise WriteFunctionBlocked(
            f"'{fn}'은(는) 쓰기 함수입니다. until은 읽기 전용이라 이 함수를 영구 호출하지 "
            "않습니다(과제 제출·퀴즈 응시·글쓰기·쪽지 발송 금지 — 학사 사고 방지)."
        )
    if fn not in READ_ALLOWLIST:
        raise WriteFunctionBlocked(
            f"'{fn}'은(는) until 읽기 allowlist에 없어 호출할 수 없습니다. "
            "읽기 전용 원칙상 허용 함수는 moodle_ws.READ_ALLOWLIST에만 있습니다."
        )


# 개발/CI에서 allowlist와 denylist가 겹치면 안 된다(모순 설정 조기 발견).
assert not (READ_ALLOWLIST & WRITE_DENYLIST), (
    "설정 오류: 읽기 allowlist와 쓰기 denylist가 겹칩니다 → "
    f"{sorted(READ_ALLOWLIST & WRITE_DENYLIST)}"
)


# ─────────────────────────────────────────────────────────────────────────────
# 순수 헬퍼(네트워크 없이 테스트 가능)
# ─────────────────────────────────────────────────────────────────────────────
def ws_endpoint(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/webservice/rest/server.php"


def _flatten_params(params: Any, prefix: str = "") -> List[tuple[str, str]]:
    """Moodle WS는 배열/구조 파라미터를 PHP 스타일로 받는다.

      courseids=[5, 7]        → courseids[0]=5, courseids[1]=7
      options={"foo": "bar"}  → options[foo]=bar
    파이썬 dict/list/스칼라를 이 (key, value) 목록으로 평탄화한다."""
    out: List[tuple[str, str]] = []
    if isinstance(params, dict):
        for k, v in params.items():
            key = f"{prefix}[{k}]" if prefix else str(k)
            out.extend(_flatten_params(v, key))
    elif isinstance(params, (list, tuple)):
        for i, v in enumerate(params):
            key = f"{prefix}[{i}]" if prefix else str(i)
            out.extend(_flatten_params(v, key))
    elif isinstance(params, bool):
        out.append((prefix, "1" if params else "0"))
    elif params is not None:
        out.append((prefix, str(params)))
    return out


def check_ws_error(data: Any) -> None:
    """Moodle WS는 오류도 HTTP 200 + {exception, errorcode, message}로 준다.

    토큰 무효/함수 미활성/권한 없음을 사람이 읽는 RuntimeError로 변환한다.
    (정상 응답은 dict거나 list — 예외 키가 없으면 통과.)
    """
    if isinstance(data, dict) and ("exception" in data or "errorcode" in data):
        code = data.get("errorcode") or data.get("exception") or "unknown"
        msg = data.get("message") or "(메시지 없음)"
        if code in ("invalidtoken", "accessexception", "invalidsesskey"):
            raise RuntimeError(
                f"eTL 인증 실패({code}): 토큰이 무효/만료거나 권한이 없습니다. "
                "eTL 웹서비스 토큰을 다시 확인하세요."
            )
        if code in ("accessexception", "webservicerequirevalue",
                    "invalidfunction", "invalidparameter"):
            raise RuntimeError(f"eTL WS 요청 오류({code}): {msg}")
        raise RuntimeError(f"eTL WS 오류({code}): {msg}")


def activated_functions(site_info: dict) -> List[str]:
    """core_webservice_get_site_info 응답에서 활성 함수 이름 목록을 뽑는다.

    Moodle은 토큰에 허용된 함수를 `functions: [{name, version}, ...]`로 준다.
    """
    fns = site_info.get("functions") if isinstance(site_info, dict) else None
    out: List[str] = []
    for f in fns or []:
        if isinstance(f, dict) and f.get("name"):
            out.append(str(f["name"]))
    return out


def allowed_activated(site_info: dict) -> List[str]:
    """활성 함수 중 until이 실제로 쓸 수 있는(읽기 allowlist) 함수만."""
    return [f for f in activated_functions(site_info) if f in READ_ALLOWLIST]


def blocked_activated(site_info: dict) -> List[str]:
    """활성 함수 중 until이 **의도적으로 쓰지 않는** 쓰기 함수(투명성용 보고)."""
    return [f for f in activated_functions(site_info) if f in WRITE_DENYLIST]


# ─────────────────────────────────────────────────────────────────────────────
# 라이브 클라이언트(urllib만) — 모든 호출은 assert_read_only를 통과해야 나간다
# ─────────────────────────────────────────────────────────────────────────────
class MoodleWsClient:
    """Moodle WS REST 클라이언트. call()은 읽기 allowlist만 통과시킨다.

    쓰기 함수는 코드 레벨에서 호출 불가 — 네트워크 요청이 생성되지도 않는다.
    """

    def __init__(self, base_url: str, token: Optional[str] = None, timeout: float = 30.0):
        self.base_url = base_url
        self.token = token or os.getenv("UNTIL_ETL_WS_TOKEN") or os.getenv("UNTIL_CANVAS_TOKEN", "")
        self.timeout = timeout
        if not self.token:
            raise ValueError(
                "eTL 웹서비스 토큰이 필요합니다. UNTIL_ETL_WS_TOKEN 환경변수로 전달하거나 "
                "MoodleWsClient(token=...)로 주입하세요."
            )

    def _post_body(self, wsfunction: str, params: Dict[str, Any]) -> bytes:
        fields: List[tuple[str, str]] = [
            ("wstoken", self.token),
            ("moodlewsrestformat", "json"),
            ("wsfunction", wsfunction),
        ]
        fields.extend(_flatten_params(params))
        return urllib.parse.urlencode(fields).encode("utf-8")

    def call(self, wsfunction: str, **params: Any) -> Any:
        """읽기 함수 1개 호출 → 파싱된 JSON. allowlist 밖이면 WriteFunctionBlocked.

        토큰은 POST 바디로 보낸다(URL 쿼리에 토큰이 남아 로그·히스토리에 새는 것 방지).
        """
        assert_read_only(wsfunction)  # ← 0번 강제: 여기서 막히면 요청 자체가 없다
        body = self._post_body(wsfunction, params)
        req = urllib.request.Request(
            ws_endpoint(self.base_url), data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise RuntimeError(
                    "eTL 인증 실패(HTTP {}). 웹서비스 토큰을 다시 확인하세요.".format(e.code)
                ) from e
            raise
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise RuntimeError(
                "eTL가 JSON이 아닌 응답을 보냈습니다(로그인 만료·엔드포인트 오류 가능)."
            ) from e
        check_ws_error(data)
        return data

    def get_site_info(self) -> dict:
        """core_webservice_get_site_info — 사이트 정보 + 토큰 활성 함수 목록.

        최초 1회 호출해 '실제로 어떤 함수가 활성인지' 확인하는 지형 조사 진입점.
        """
        info = self.call("core_webservice_get_site_info")
        return info if isinstance(info, dict) else {}


# ─────────────────────────────────────────────────────────────────────────────
# 순수 파서(Moodle WS JSON → until 모델). 네트워크 없이 결정적·테스트 가능.
# ─────────────────────────────────────────────────────────────────────────────
def _iso_from_unix(ts: Any) -> str:
    """Moodle의 유닉스 초(0/None=없음) → ISO8601 UTC 문자열. 없으면 ''.

    AssignmentRef.due_at 등 기존 필드가 ISO 문자열 계약이므로 맞춰 준다."""
    try:
        n = int(ts or 0)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    try:
        return _dt.datetime.fromtimestamp(n, _dt.timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):  # 비정상적으로 큰/작은 값 방어
        return ""


def _readable_unix(ts: Any) -> str:
    """유닉스 초 → '2026년 7월 27일(월) 오전 9시' (KST). 없으면 ''.

    canvas_api._readable_due와 표기를 맞춰 초안에 ISO/유닉스가 새지 않게 한다."""
    try:
        n = int(ts or 0)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    try:
        kst = _dt.datetime.fromtimestamp(n, _dt.timezone(_dt.timedelta(hours=9)))
    except (OverflowError, OSError, ValueError):  # 비정상적으로 큰/작은 값 방어
        return ""
    wd = "월화수목금토일"[kst.weekday()]
    ampm = "오전" if kst.hour < 12 else "오후"
    h12 = kst.hour % 12 or 12
    mins = f" {kst.minute}분" if kst.minute else ""
    return f"{kst.year}년 {kst.month}월 {kst.day}일({wd}) {ampm} {h12}시{mins}"


class _HtmlText(HTMLParser):
    """Moodle intro/summary/message HTML 조각 → 평문 텍스트(블록 태그는 줄바꿈)."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"p", "div", "br", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(html: str) -> str:
    """HTML 조각을 평문으로. 태그 제거 + 공백 정리(결정적)."""
    if not html:
        return ""
    p = _HtmlText()
    p.feed(html)
    text = unescape("".join(p.parts))
    text = "\n".join(line.strip() for line in text.splitlines())
    import re as _re
    return _re.sub(r"\n{3,}", "\n\n", text).strip()


def _course_ended(c: dict) -> bool:
    """지난 과목 판정 — 종료일(enddate)이 지났으면 True(없으면 fail-open=현재 과목)."""
    end = c.get("enddate")
    try:
        n = int(end or 0)
    except (TypeError, ValueError):
        return False
    if n <= 0:
        return False
    return _dt.datetime.fromtimestamp(n, _dt.timezone.utc) < _dt.datetime.now(_dt.timezone.utc)


def parse_ws_courses(data: Any) -> List[CourseRef]:
    """core_enrol_get_users_courses(JSON 배열) → CourseRef. 지난 과목·이름없음 제외."""
    out: List[CourseRef] = []
    seen = set()
    for c in data or []:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or "").strip()
        name = (c.get("fullname") or c.get("displayname") or c.get("shortname") or "").strip()
        if not cid or not name or cid in seen or _course_ended(c):
            continue
        seen.add(cid)
        out.append(CourseRef(id=cid, name=name))
    return out


def _attachments_from_files(files: Any, base_url: str) -> List[Attachment]:
    """Moodle 파일 배열([{filename, fileurl, ...}]) → Attachment(중복 URL 제거)."""
    out: List[Attachment] = []
    seen = set()
    for f in files or []:
        if not isinstance(f, dict):
            continue
        url = (f.get("fileurl") or "").strip()
        if not url:
            continue
        if url in seen:
            continue
        seen.add(url)
        name = (f.get("filename") or url.rstrip("/").rsplit("/", 1)[-1].split("?")[0]).strip()
        out.append(Attachment(name=name or "attachment", url=url))
    return out


def assignment_view_url(base_url: str, cmid: Any, course_id: Any = None) -> str:
    """과제 보기 URL. Moodle은 cmid(id)로 과제를 연다.

    course_id를 함께 실어(&courseid=) 무상태 재조회를 가능하게 한다 — Moodle WS는
    과제를 과목 단위로만 조회하므로, URL만으로 과목을 알아야 새 어댑터도 본문을
    가져올 수 있다(웹 /pick 흐름). Moodle은 이 여분 파라미터를 무시하므로 링크는 정상."""
    if not cmid:
        return base_url
    url = f"{base_url.rstrip('/')}/mod/assign/view.php?id={cmid}"
    cid = str(course_id or "").strip()
    return f"{url}&courseid={cid}" if cid else url


def assignment_from_ws(assign: dict, base_url: str) -> RawAssignment:
    """mod_assign_get_assignments의 개별 assignment dict → RawAssignment.

    본문(intro) + 마감(duedate, 사람이 읽는 표기) + introattachments를 담는다."""
    title = (assign.get("name") or "(제목 없음)").strip()
    course_id = assign.get("course")
    course = f"eTL 과목 {course_id}" if course_id else "(과목 미상)"
    body = html_to_text(assign.get("intro") or "")
    due = _readable_unix(assign.get("duedate"))
    cutoff = _readable_unix(assign.get("cutoffdate"))
    lines: List[str] = []
    if due:
        lines.append(f"마감: {due}")
    if cutoff and cutoff != due:
        lines.append(f"최종 제출 기한: {cutoff}")
    header = "\n".join(lines)
    description = (f"{header}\n\n{body}" if header else body) or "(과제 설명 없음)"
    atts = _attachments_from_files(assign.get("introattachments"), base_url)
    url = assignment_view_url(base_url, assign.get("cmid"), course_id)
    return RawAssignment(title=title, course=course, description=description,
                         attachments=atts, url=url)


def _url_ids(url: str) -> Dict[str, str]:
    """과제 URL에서 cmid(id)·courseid를 뽑는다(무상태 fetch용)."""
    q = dict(parse_qsl(urlsplit(url or "").query))
    return {"cmid": q.get("id", ""), "courseid": q.get("courseid", "")}


def parse_ws_assignments(data: Any, base_url: str,
                         course: "CourseRef | None" = None) -> List[AssignmentRef]:
    """mod_assign_get_assignments({courses:[{assignments:[...]}]}) → AssignmentRef 목록."""
    out: List[AssignmentRef] = []
    courses = data.get("courses") if isinstance(data, dict) else None
    for co in courses or []:
        if not isinstance(co, dict):
            continue
        cid = str(co.get("id") or (course.id if course else "")).strip()
        cname = (co.get("fullname") or (course.name if course else "")).strip()
        for a in co.get("assignments") or []:
            if not isinstance(a, dict):
                continue
            aid = str(a.get("id") or "").strip()
            if not aid:
                continue
            url = assignment_view_url(base_url, a.get("cmid"), cid)
            out.append(AssignmentRef(
                id=aid, title=(a.get("name") or "(제목 없음)").strip(),
                course_id=cid, course_name=cname, url=url,
                due_at=_iso_from_unix(a.get("duedate")),
            ))
    return out


def parse_course_contents(data: Any, base_url: str) -> Dict[str, List[Attachment]]:
    """core_course_get_contents(섹션 배열) → {'files':[...], 'modules':[...]}.

    - files: 모듈에 딸린 실제 파일(fileurl 있는 것) — 자동 다운로드 대상.
    - modules: 파일/페이지/링크 등 제목 있는 모듈 항목(자료 후보 이름·URL).
    """
    files: List[Attachment] = []
    modules: List[Attachment] = []
    fseen, mseen = set(), set()
    for sec in data or []:
        if not isinstance(sec, dict):
            continue
        for mod in sec.get("modules") or []:
            if not isinstance(mod, dict):
                continue
            title = (mod.get("name") or "").strip()
            murl = (mod.get("url") or "").strip()
            if title and (title, murl) not in mseen:
                mseen.add((title, murl))
                modules.append(Attachment(name=title, url=murl))
            for f in mod.get("contents") or []:
                if not isinstance(f, dict) or f.get("type") != "file":
                    continue
                furl = (f.get("fileurl") or "").strip()
                if not furl or furl in fseen:
                    continue
                fseen.add(furl)
                fname = (f.get("filename") or "").strip() or title or "attachment"
                files.append(Attachment(name=fname, url=furl))
    return {"files": files, "modules": modules}


def with_token(fileurl: str, token: str) -> str:
    """Moodle pluginfile URL에 WS 토큰을 붙인다({url}?token= 또는 &token=).

    이미 token 파라미터가 있으면 교체(중복 방지). 토큰이 URL 쿼리에 남지만 이는
    Moodle 파일 다운로드의 정규 방식이다(pluginfile은 세션이 아닌 토큰 인증)."""
    if not token:
        return fileurl
    parts = urlsplit(fileurl)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "token"]
    q.append(("token", token))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


# ─────────────────────────────────────────────────────────────────────────────
# 포럼(4번 공지·Q&A) 파서
# ─────────────────────────────────────────────────────────────────────────────
def parse_ws_forums(data: Any) -> List[dict]:
    """mod_forum_get_forums_by_courses(JSON 배열) → [{id, name, type, course}].

    type='news'는 공지사항 포럼(Moodle 관례). 정렬·필터는 상위에서."""
    out: List[dict] = []
    for f in data or []:
        if not isinstance(f, dict):
            continue
        fid = str(f.get("id") or "").strip()
        if not fid:
            continue
        out.append({
            "id": fid,
            "name": (f.get("name") or "").strip(),
            "type": (f.get("type") or "").strip(),
            "course": str(f.get("course") or "").strip(),
        })
    return out


def parse_ws_discussions(data: Any, *, forum: str = "", course_id: str = "",
                         course_name: str = "", base_url: str = "") -> List[Announcement]:
    """mod_forum_get_forum_discussions({discussions:[...]}) → Announcement 목록.

    각 토론의 첫 글(name/subject + message)이 공지 본문이다."""
    discs = data.get("discussions") if isinstance(data, dict) else data
    out: List[Announcement] = []
    for d in discs or []:
        if not isinstance(d, dict):
            continue
        subject = (d.get("name") or d.get("subject") or "(제목 없음)").strip()
        body = html_to_text(d.get("message") or "")
        created = _iso_from_unix(d.get("created") or d.get("timemodified"))
        # 답글 조회에 쓰는 토론 id(신형 Moodle은 discussion 필드).
        did = str(d.get("discussion") or d.get("id") or "").strip()
        url = (f"{base_url.rstrip('/')}/mod/forum/discuss.php?d={did}"
               if did and base_url else "")
        out.append(Announcement(
            subject=subject, body=body,
            # userfullname은 실명이며 역할 근거가 아니다. 객체 생성 전에 폐기한다.
            author=announcement_author_role(d.get("author_role")),
            created_iso=created, forum=forum,
            course_id=course_id, course_name=course_name, url=url,
        ))
    return out


def parse_ws_posts(data: Any) -> List[str]:
    """mod_forum_get_discussion_posts({posts:[...]}) → 글 본문 목록(첫 글 포함 전부).

    교수가 Q&A 답글에서 추가한 과제 조건 등 '숨은 명세'를 담는다."""
    posts = data.get("posts") if isinstance(data, dict) else data
    out: List[str] = []
    for p in posts or []:
        if not isinstance(p, dict):
            continue
        msg = html_to_text(p.get("message") or "")
        if msg:
            out.append(msg)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 라이브 어댑터 — CanvasApiAdapter와 같은 표면(Discovery/Material/Browser 프로토콜).
#   list_courses / list_assignments  → EtlInbox(discovery.py) 재사용
#   list_course_files / list_modules → etl_materials.py 순위화 재사용
#   fetch_assignment / download      → EtlSource(etl.py) 재사용
# 모든 호출은 MoodleWsClient.call(=allowlist)만 거친다 → 0번 원칙 유지.
# ─────────────────────────────────────────────────────────────────────────────
class MoodleWsAdapter(MoodleWsClient):
    """Moodle WS 기반 eTL 어댑터. 읽기 전용(쓰기 함수는 상위 클래스가 차단)."""

    def __init__(self, base_url: str, token: Optional[str] = None, timeout: float = 30.0):
        super().__init__(base_url, token=token, timeout=timeout)
        self._userid: Optional[int] = None
        # list_assignments가 채운 캐시: 과제 URL → 전체 assignment dict.
        # fetch_assignment(url)가 재조회 없이 본문을 만들 수 있게 한다(탐색→수집 흐름).
        self._assign_by_url: Dict[str, dict] = {}
        self._assign_by_cmid: Dict[str, dict] = {}
        # 과제 URL → course_id (collect가 자료 순위화용 course_id를 얻도록).
        self._course_of: Dict[str, str] = {}
        self._contents_cache: Dict[str, dict] = {}

    def _uid(self) -> int:
        if self._userid is None:
            self._userid = int(self.get_site_info().get("userid") or 0)
        return self._userid

    # ── Discovery ────────────────────────────────────────────────
    def list_courses(self, base_url: str) -> List[CourseRef]:
        data = self.call("core_enrol_get_users_courses", userid=self._uid())
        return parse_ws_courses(data)

    def _cache_course_assignments(self, course_id: str) -> Any:
        """과목의 과제를 조회해 캐시(URL·cmid·course 매핑)하고 원본 data를 반환."""
        data = self.call("mod_assign_get_assignments", courseids=[course_id])
        for co in (data.get("courses") if isinstance(data, dict) else []) or []:
            if not isinstance(co, dict):
                continue
            cid = str(co.get("id") or course_id)
            for a in co.get("assignments") or []:
                if not isinstance(a, dict):
                    continue
                cmid = str(a.get("cmid") or "")
                url = assignment_view_url(self.base_url, a.get("cmid"), cid)
                if a.get("cmid"):
                    self._assign_by_url[url] = a
                    self._course_of[url] = cid
                    self._assign_by_cmid[cmid] = a
        return data

    def list_assignments(self, course: CourseRef, base_url: str,
                         bucket: Optional[str] = None) -> List[AssignmentRef]:
        data = self._cache_course_assignments(course.id)
        return parse_ws_assignments(data, self.base_url, course=course)

    # ── Materials ────────────────────────────────────────────────
    def _contents(self, course_id: str) -> dict:
        if course_id not in self._contents_cache:
            data = self.call("core_course_get_contents", courseid=course_id)
            self._contents_cache[course_id] = parse_course_contents(data, self.base_url)
        return self._contents_cache[course_id]

    def list_course_files(self, course_id: str, base_url: str) -> List[Attachment]:
        return list(self._contents(course_id)["files"])

    def list_modules(self, course_id: str, base_url: str) -> List[Attachment]:
        return list(self._contents(course_id)["modules"])

    def course_id_for_url(self, url: str) -> str:
        """과제 URL → course_id. 캐시 우선, 없으면 URL의 courseid 파라미터."""
        return self._course_of.get(url) or _url_ids(url).get("courseid", "")

    def assignment_target(self, url: str) -> tuple:
        """과제 URL → (course_id, **assign 인스턴스 id**). 못 찾으면 ("", "").

        ⚠ URL에 실린 `id=`는 **course module id(cmid)**인데, 제출 함수
        (`mod_assign_save_submission`)가 받는 것은 **assign 인스턴스 id**다. 둘은
        다른 번호이고, 혼동하면 **엉뚱한 과제에 제출된다**. 그래서 URL에서 뽑아
        쓰지 않고 WS가 준 과제 레코드의 `id`만 쓴다 — 못 찾으면 빈 값을 돌려주고,
        제출 게이트가 `assignment_mismatch`로 막는 것이 올바른 동작이다.
        """
        a = self._assign_by_url.get(url)
        if a is None:
            ids = _url_ids(url)
            cid, cmid = ids.get("courseid"), ids.get("cmid")
            if cid:
                try:
                    self._cache_course_assignments(cid)
                except Exception:
                    return "", ""
                a = self._assign_by_url.get(url) or self._assign_by_cmid.get(cmid or "")
        if not isinstance(a, dict):
            return "", ""
        return self.course_id_for_url(url), str(a.get("id") or "")

    # ── Browser(EtlSource) ───────────────────────────────────────
    def fetch_assignment(self, url: str) -> RawAssignment:
        """과제 본문을 RawAssignment로. 캐시 미스 시 URL의 courseid로 자체 조회.

        Moodle WS는 과제를 과목 단위로만 조회하므로, 인박스를 거치지 않은 새 어댑터
        (웹 /pick)도 URL에 실린 courseid로 해당 과목 과제를 한 번 당겨 캐시를 채운다."""
        a = self._assign_by_url.get(url)
        if a is None:
            ids = _url_ids(url)
            cid, cmid = ids.get("courseid"), ids.get("cmid")
            if cid:
                self._cache_course_assignments(cid)
                a = self._assign_by_url.get(url) or self._assign_by_cmid.get(cmid or "")
        if a is None:
            raise RuntimeError(
                "이 과제 본문을 가져오지 못했습니다. 인박스에서 과제를 다시 선택해 주세요"
                "(Moodle WS는 과제를 과목 단위로 조회합니다)."
            )
        return assignment_from_ws(a, self.base_url)

    def download(self, attachment: Attachment, dest_dir: str) -> str:
        """fileurl에 토큰을 붙여 파일 바이트를 받는다(3번 자동 다운로드의 핵심)."""
        url = with_token(attachment.url, self.token)
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            body = r.read()
        dest = Path(dest_dir) / safe_filename(attachment.name)
        dest.write_bytes(body)
        return str(dest)

    # ── Forum(4번 공지·Q&A) ──────────────────────────────────────
    def list_forums(self, course_id: str) -> List[dict]:
        data = self.call("mod_forum_get_forums_by_courses", courseids=[course_id])
        return parse_ws_forums(data)

    def get_discussions(self, forum_id: str, *, forum_name: str = "",
                        course_id: str = "", course_name: str = "") -> List["Announcement"]:
        data = self.call("mod_forum_get_forum_discussions", forumid=forum_id)
        return parse_ws_discussions(
            data, forum=forum_name, course_id=course_id,
            course_name=course_name, base_url=self.base_url)

    def get_posts(self, discussion_id: str) -> List[str]:
        data = self.call("mod_forum_get_discussion_posts", discussionid=discussion_id)
        return parse_ws_posts(data)

    def collect_announcements(self, course: CourseRef, *, limit: int = 5,
                              news_only: bool = True,
                              include_replies: bool = False) -> List["Announcement"]:
        """과목의 공지(뉴스 포럼) 최신 limit건을 모은다. 개별 실패는 스킵.

        news_only=True면 type='news' 포럼만(공지사항). include_replies=True면
        각 공지의 답글 본문까지 채워 '숨은 명세'(교수 추가 조건)를 흡수한다."""
        try:
            forums = self.list_forums(course.id)
        except Exception:
            return []
        if news_only:
            news = [f for f in forums if f.get("type") == "news"]
            forums = news or forums  # 뉴스 포럼이 없으면 전체 폴백
        anns: List[Announcement] = []
        for f in forums:
            try:
                got = self.get_discussions(
                    f["id"], forum_name=f.get("name", ""),
                    course_id=course.id, course_name=course.name)
            except Exception:
                continue
            anns.extend(got)
        # 최신순(created_iso 내림차순; 없는 건 뒤로).
        anns.sort(key=lambda a: a.created_iso or "", reverse=True)
        anns = anns[:limit]
        if include_replies:
            self.fill_replies(anns)
        return anns

    def fill_replies(self, anns: List["Announcement"]) -> None:
        """주어진 공지들의 답글 본문을 채운다(교수 추가 조건=숨은 명세).

        답글 조회는 공지 1건당 API 1콜이라, **순위화로 추린 소수에만** 호출해야
        지연이 폭발하지 않는다(리뷰 발견 — 20건 받아 3건 쓰는 N+1 방지)."""
        import re as _re
        for a in anns:
            # discuss.php?d=<숫자> 에서 토론 id만 안전하게 추출(뒤 파라미터 무시).
            m = _re.search(r"[?&]d=(\d+)", a.url or "")
            did = m.group(1) if m else ""
            if not did:
                continue
            try:
                posts = self.get_posts(did)
                a.replies = [p for p in posts if p and p != a.body]  # 본문 중복 제외
            except Exception:
                continue

    # ── 제출 상태(미제출 필터·과금 보호) ─────────────────────────
    def submission_submitted(self, assignment_id: str) -> bool:
        """mod_assign_get_submission_status → 제출 완료 여부.

        mod_assign_get_assignments는 제출 상태를 주지 않아 AssignmentRef.submitted가
        항상 False로 남는다(리뷰 발견) — '미제출만' 필터가 무력화되고 /quick이 이미
        제출한 과제를 자동 초안(+과금)할 수 있다. 이 함수로 정확히 채운다."""
        data = self.call("mod_assign_get_submission_status", assignid=assignment_id)
        if not isinstance(data, dict):
            return False
        last = data.get("lastattempt") or {}
        for key in ("submission", "teamsubmission"):
            sub = last.get(key) or {}
            if (sub.get("status") or "").lower() in ("submitted", "complete"):
                return True
            if sub.get("timemodified") and (sub.get("status") or "") != "new":
                # 일부 Moodle은 status 없이 timemodified로만 표시.
                if (sub.get("status") or "").lower() != "reopened":
                    return bool(sub.get("submitted") or sub.get("timemodified"))
        # 채점 완료도 제출된 것으로 본다.
        fb = data.get("feedback") or {}
        return bool(fb.get("gradeddate") or fb.get("grade"))

    def enrich_submitted(self, items: List[AssignmentRef], *, max_workers: int = 8) -> None:
        """AssignmentRef 목록의 submitted를 제출 상태 조회로 채운다(병렬·실패 무시).

        비용(과제당 1콜)이 있으므로 '미제출만' 필터/quick처럼 정말 필요할 때만 호출."""
        def _one(a: AssignmentRef) -> None:
            try:
                a.submitted = self.submission_submitted(a.id)
            except Exception:
                pass  # 조회 실패는 False 유지(안전측 — 필터가 과제를 숨기지 않음)
        if not items:
            return
        if max_workers > 1 and len(items) > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as ex:
                list(ex.map(_one, items))
        else:
            for a in items:
                _one(a)


def print_site_inventory(base_url: str, token: Optional[str] = None) -> int:
    """eTL 함수 지형 조사 — 팀 요청 '최초 1회 활성 함수 확인'.

    `python -m until.capture.sources.moodle_ws <base_url>` (토큰은 UNTIL_ETL_WS_TOKEN).
    활성 함수 전체 / until이 쓸 읽기 함수 / until이 의도적으로 안 쓰는 쓰기 함수를 분리 출력.
    """
    try:
        client = MoodleWsClient(base_url, token=token)
        info = client.get_site_info()
    except Exception as e:  # 토큰 없음/네트워크/인증
        print(f"조회 실패: {e}")
        return 1
    acts = activated_functions(info)
    allowed = allowed_activated(info)
    blocked = blocked_activated(info)
    print(f"=== eTL 함수 지형 ({info.get('sitename') or base_url}) ===")
    print(f"  사용자: {info.get('fullname') or info.get('username') or info.get('userid')}")
    print(f"  활성 함수 {len(acts)}개")
    print(f"\n  ✅ until이 쓸 수 있는 읽기 함수 {len(allowed)}개:")
    for f in sorted(allowed):
        print(f"     · {f}")
    missing = sorted(f for f in READ_ALLOWLIST if f not in acts)
    if missing:
        print(f"\n  ⚠ allowlist에 있으나 이 토큰에 비활성 {len(missing)}개(해당 기능 스킵):")
        for f in missing:
            print(f"     · {f}")
    print(f"\n  ⛔ 활성이지만 until이 영구 미사용(쓰기) {len(blocked)}개:")
    for f in sorted(blocked):
        print(f"     · {f}")
    other = sorted(f for f in acts if f not in READ_ALLOWLIST and f not in WRITE_DENYLIST)
    if other:
        print(f"\n  (참고) allowlist 밖 기타 활성 함수 {len(other)}개 — until은 호출하지 않음.")
    return 0


if __name__ == "__main__":  # python -m until.capture.sources.moodle_ws <base_url>
    import sys
    if len(sys.argv) < 2:
        print("사용법: python -m until.capture.sources.moodle_ws <eTL_base_url>")
        print("  토큰은 UNTIL_ETL_WS_TOKEN 환경변수로 전달.")
        raise SystemExit(2)
    raise SystemExit(print_site_inventory(sys.argv[1]))
