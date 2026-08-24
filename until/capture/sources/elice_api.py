"""
Elice(snu.elice.io) 코딩 과제 어댑터 — **읽기 전용, allowlist 강제**.

서울대 일부 과목은 코딩 과제를 Elice(snu.elice.io)로 낸다. eTL(Canvas/Moodle)과는
별개 소스라 이 파일이 따로 존재한다. moodle_ws.py의 읽기 전용 철학과 대칭:
학생 토큰으로 제출·저장·채점·실행 같은 쓰기 엔드포인트도 "호출 가능"하지만, 이
클라이언트는 그것을 코드 레벨에서 영구 차단한다.

⚠️ 절대 원칙 — until은 읽기 전용이다(협상 대상 아님).
차단 방식 = **allowlist 강제**: `EliceClient.call()`은 (1) method가 GET인 것,
(2) URL의 (호스트, 경로)가 `_ALLOWED_PATHS`에 명시된 것만 통과시키고, 그 밖은
네트워크로 나가기 전에 `EliceReadOnlyError`로 막는다. 새 엔드포인트를 쓰려면
이 파일의 allowlist를 사람이 직접 고쳐야 한다.

전송 계층은 **주입식(transport 콜백)**이다. Cloudflare(error 1010)가 python
urllib을 차단하므로, 기본 전송은 시스템 curl 서브프로세스(`_curl_transport`,
브라우저 User-Agent + Bearer 헤더)로 구현한다. 테스트는 항상 fake transport를
주입하므로 subprocess·네트워크 호출이 0이다.

- 순수 함수(parse_course/parse_lecture/parse_exercise/parse_exercise_file 등)는
  네트워크 없이 테스트 가능.
- 개인정보(fullname 등)는 이 모듈이 수집·저장·로그하지 않는다 — `whoami()`가
  돌려주는 원본에도 그대로 남지만, 호출부가 저장·로그하지 말아야 한다(계정 소유자
  확인용 1회성 조회로만 쓴다).
- 신규 파이썬 의존성 0(표준 라이브러리 + 프로젝트 내부 import만).
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import AssignmentRef, Attachment, CourseRef, RawAssignment, safe_filename
from .moodle_ws import html_to_text  # 순수 함수, 네트워크 없음(HTML 조각 → 평문)

# ─────────────────────────────────────────────────────────────────────────────
# 0번 — 허용 GET 경로 allowlist (호스트, 경로) 쌍으로 정확 매칭. 쿼리스트링 무시.
# ─────────────────────────────────────────────────────────────────────────────
ACCOUNT_ME_URL = "https://api-account.elice.io/account/me"
_COURSE_GET_PATH = "/org/snu/course/get/"
_LECTURE_GET_PATH = "/org/snu/lecture/get/"
_EXERCISE_GET_PATH = "/org/snu/material_exercise/get/"
_EXERCISE_FILE_GET_PATH = "/org/snu/material_exercise/exercise_image/exercise_file/get/"

_ALLOWED_PATHS: frozenset[Tuple[str, str]] = frozenset({
    ("api-account.elice.io", "/account/me"),
    ("api-rest.elice.io", _COURSE_GET_PATH),
    ("api-rest.elice.io", _LECTURE_GET_PATH),
    ("api-rest.elice.io", _EXERCISE_GET_PATH),
    ("api-rest.elice.io", _EXERCISE_FILE_GET_PATH),
})

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)


class EliceReadOnlyError(RuntimeError):
    """쓰기(또는 allowlist 밖) 요청 시도 — until 읽기 전용 원칙 위반 차단."""


def _is_allowed(url: str) -> bool:
    """URL이 읽기 allowlist(호스트+경로 정확 일치)에 있는지. 쿼리스트링은 무시."""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return p.scheme == "https" and (p.netloc, p.path) in _ALLOWED_PATHS


def is_exercise_url(url: str) -> bool:
    """Elice exercise 조회 URL만 정확히 판정한다(부분문자열 라우팅 금지)."""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return (p.scheme == "https" and p.netloc == "api-rest.elice.io"
            and p.path == _EXERCISE_GET_PATH
            and bool(urllib.parse.parse_qs(p.query).get("material_exercise_id")))


def assert_read_only(method: str, url: str) -> None:
    """0번 강제 지점: GET이 아니거나 allowlist 밖이면 네트워크로 나가기 전에 예외."""
    m = (method or "").strip().upper()
    if m != "GET":
        raise EliceReadOnlyError(
            f"'{m}'은(는) 쓰기 메서드입니다. until은 읽기 전용이라 Elice에 GET만 "
            "호출합니다(제출·저장·채점·실행 요청 금지)."
        )
    if not _is_allowed(url):
        raise EliceReadOnlyError(
            f"허용되지 않은 경로입니다(읽기 allowlist 밖): {url}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# URL 빌더(순수 함수)
# ─────────────────────────────────────────────────────────────────────────────
def course_url(course_id: Any) -> str:
    return f"https://api-rest.elice.io/org/snu/course/get/?course_id={course_id}"


def lecture_url(lecture_id: Any) -> str:
    return f"https://api-rest.elice.io/org/snu/lecture/get/?lecture_id={lecture_id}"


def exercise_url(material_exercise_id: Any) -> str:
    return (
        "https://api-rest.elice.io/org/snu/material_exercise/get/"
        f"?material_exercise_id={material_exercise_id}"
    )


def exercise_file_url(exercise_image_id: Any, filename: str) -> str:
    return (
        "https://api-rest.elice.io/org/snu/material_exercise/exercise_image/"
        f"exercise_file/get/?exercise_image_id={exercise_image_id}"
        f"&filename={urllib.parse.quote(filename or '')}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 순수 파서(Elice JSON → until 표면). 네트워크 없이 결정적·테스트 가능.
# ─────────────────────────────────────────────────────────────────────────────
def _check_fail(data: Any) -> None:
    """HTTP 200 바디로 오는 실패 표시를 사람이 읽는 RuntimeError로 변환.

    관측된 실패 형태: `{_result:{status:"fail"}}` 또는 `{detail:"..."}`.
    `detail`만 오는 경우(course/lecture/material_exercise/exercise_file/id 같은
    정상 응답 키가 전혀 없을 때)만 실패로 간주한다(정상 응답에 우연히 detail
    필드가 섞인 경우를 오탐하지 않기 위한 보수적 판정)."""
    if not isinstance(data, dict):
        return
    result = data.get("_result")
    if isinstance(result, dict) and str(result.get("status", "")).strip().lower() == "fail":
        raise RuntimeError(f"Elice 요청 실패: {result.get('message') or result}")
    success_keys = ("course", "lecture", "material_exercise", "exercise_file", "id",
                    "fullname")
    if "detail" in data and not any(k in data for k in success_keys):
        raise RuntimeError(f"Elice 오류 응답: {data.get('detail')}")


def parse_course(data: Any) -> Dict[str, Any]:
    """course/get 응답 → {title, lectures:[{id,title,total_page_count,parent_lecture_id,...}]}.

    실제 강의 트리는 course.lectures[]. 컨테이너 lecture는 total_page_count=0."""
    course = data.get("course") if isinstance(data, dict) else None
    course = course if isinstance(course, dict) else {}
    title = (course.get("title") or course.get("name") or "").strip()
    lectures: List[Dict[str, Any]] = []
    for lec in course.get("lectures") or []:
        if not isinstance(lec, dict) or lec.get("id") is None:
            continue
        try:
            total = int(lec.get("total_page_count") or 0)
        except (TypeError, ValueError):
            total = 0
        lectures.append({
            "id": lec.get("id"),
            "title": (lec.get("title") or "").strip(),
            "total_page_count": total,
            "parent_lecture_id": lec.get("parent_lecture_id"),
            "depth": lec.get("depth"),
            "order_no": lec.get("order_no"),
            "lecture_type": lec.get("lecture_type"),
        })
    return {"title": title, "lectures": lectures}


def parse_lecture(data: Any) -> Dict[str, Any]:
    """lecture/get 응답 → {pages:[{id,material_type,title,is_graded,...}]}.

    main_lecture_pages + sub_lecture_pages를 순서대로 합친다."""
    lecture = data.get("lecture") if isinstance(data, dict) else None
    lecture = lecture if isinstance(lecture, dict) else {}
    pages: List[Dict[str, Any]] = []
    for key in ("main_lecture_pages", "sub_lecture_pages"):
        for p in lecture.get(key) or []:
            if not isinstance(p, dict) or p.get("id") is None:
                continue
            pages.append({
                "id": p.get("id"),
                "material_id": p.get("material_id"),
                "material_type": p.get("material_type"),
                "material_key": p.get("material_key"),
                "title": (p.get("title") or "").strip(),
                "point": p.get("point"),
                "is_graded": bool(p.get("is_graded")),
                "is_completed": bool(p.get("is_completed")),
            })
    return {"pages": pages}


#: material_type 4 = Exercise(코딩 과제). 실측 확인된 값.
EXERCISE_MATERIAL_TYPE = 4


def _filenames(items: Any) -> List[str]:
    """filelist/task_filelist/read_only_filelist → 파일명 문자열 목록.

    항목이 문자열이거나 {"filename": ...} 형태인 두 경우를 모두 받는다."""
    out: List[str] = []
    for it in items or []:
        if isinstance(it, str):
            name = it.strip()
        elif isinstance(it, dict):
            name = str(it.get("filename") or it.get("name") or "").strip()
        else:
            name = ""
        if name:
            out.append(name)
    return out


def parse_exercise(data: Any) -> Dict[str, Any]:
    """material_exercise/get 응답 → {problem, files, task_files, readonly_files,
    runtime, exercise_image_id}.

    problem은 description(Markdown)을 우선 쓰고, 없으면 description_rendered(HTML)를
    평문으로 변환해 폴백한다."""
    me = data.get("material_exercise") if isinstance(data, dict) else None
    me = me if isinstance(me, dict) else {}
    problem = (me.get("description") or "").strip()
    if not problem:
        problem = html_to_text(me.get("description_rendered") or "")
    img = me.get("ready_exercise_image")
    img = img if isinstance(img, dict) else {}
    base_image = img.get("base_image") if isinstance(img.get("base_image"), dict) else {}
    return {
        "problem": problem or "(과제 설명 없음)",
        "files": _filenames(img.get("filelist")),
        "task_files": _filenames(img.get("task_filelist")),
        "readonly_files": _filenames(img.get("read_only_filelist")),
        "runtime": (base_image.get("title") or "").strip(),
        "exercise_image_id": img.get("id"),
    }


def parse_exercise_file(data: Any) -> Dict[str, str]:
    """exercise_file/get 응답 → {filename, content}."""
    ef = data.get("exercise_file") if isinstance(data, dict) else None
    ef = ef if isinstance(ef, dict) else {}
    return {
        "filename": (ef.get("filename") or "").strip(),
        "content": ef.get("content") if isinstance(ef.get("content"), str) else "",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 기본 전송(시스템 curl 서브프로세스) — urllib은 Cloudflare(error 1010)에 막힌다.
# 테스트는 항상 fake transport를 주입하므로 이 함수는 실제 호출부(운영)에서만 쓰인다.
# ─────────────────────────────────────────────────────────────────────────────
_STATUS_MARKER = "\n<<<UNTIL_ELICE_HTTP_STATUS:"


def _curl_transport(method: str, url: str, headers: Dict[str, str],
                    *, timeout: float = 20) -> Tuple[int, str]:
    """기본 transport: 시스템 curl 서브프로세스로 GET만 실행한다.

    호출부(EliceClient.call)가 이미 assert_read_only를 통과시켰다고 가정하지만,
    이 함수 단독으로도(직접 주입돼 재사용될 가능성 대비) 이중으로 GET만 허용한다."""
    m = (method or "").strip().upper()
    if m != "GET":
        raise EliceReadOnlyError(
            f"'{m}'은(는) 쓰기 메서드입니다. until은 읽기 전용이라 curl로도 GET만 실행합니다."
        )
    # 비밀 Bearer 토큰을 argv에 넣지 않는다. curl은 `-H @-`로 stdin의 헤더를
    # 읽을 수 있어 프로세스 목록·진단 덤프에 Authorization 값이 남지 않는다.
    cmd = ["curl", "-s", "-S", "--max-time", str(timeout),
           "-w", f"{_STATUS_MARKER}%{{http_code}}", "-H", "@-"]
    header_input = "".join(f"{k}: {v}\n" for k, v in headers.items())
    cmd.append(url)
    try:
        proc = subprocess.run(cmd, input=header_input, capture_output=True, text=True,
                              timeout=timeout + 5)
    except (subprocess.SubprocessError, OSError) as e:
        raise RuntimeError(f"curl 실행 실패: {e}") from e
    out = proc.stdout
    idx = out.rfind(_STATUS_MARKER)
    if idx == -1:
        raise RuntimeError(f"curl 응답 형식을 해석할 수 없습니다: {(proc.stderr or '')[:200]}")
    body = out[:idx]
    try:
        status = int(out[idx + len(_STATUS_MARKER):].strip())
    except ValueError:
        status = 0
    return status, body


# ─────────────────────────────────────────────────────────────────────────────
# 라이브 클라이언트 — 모든 호출은 assert_read_only를 통과해야 나간다
# ─────────────────────────────────────────────────────────────────────────────
class EliceClient:
    """Elice REST 클라이언트. call()은 GET + 읽기 allowlist만 통과시킨다.

    쓰기 메서드/allowlist 밖 경로는 코드 레벨에서 호출 불가 — transport가
    호출되지도 않는다(0번 강제가 요청 생성 전에 막는다).
    """

    def __init__(self, token: Optional[str] = None,
                transport: Optional[Any] = None, timeout: float = 20):
        self.token = token or os.getenv("UNTIL_ELICE_TOKEN", "")
        if not self.token:
            raise ValueError(
                "Elice 토큰이 필요합니다. UNTIL_ELICE_TOKEN 환경변수로 전달하거나 "
                "EliceClient(token=...)로 주입하세요."
            )
        self.timeout = timeout
        self.transport = transport or (
            lambda m, u, h: _curl_transport(m, u, h, timeout=self.timeout)
        )

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "User-Agent": _USER_AGENT,
            "Origin": "https://snu.elice.io",
            "Referer": "https://snu.elice.io/",
            "Accept": "application/json",
        }

    def call(self, method: str, url: str) -> Any:
        """읽기 요청 1개 호출 → 파싱된 JSON. GET이 아니거나 allowlist 밖이면
        EliceReadOnlyError(0번 강제 — transport가 호출되지 않는다)."""
        assert_read_only(method, url)
        status, body = self.transport("GET", url, self._headers())
        if status != 200:
            raise RuntimeError(f"Elice 요청 실패(HTTP {status}): {url}")
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, TypeError) as e:
            raise RuntimeError(
                "Elice가 JSON이 아닌 응답을 보냈습니다(로그인 만료·Cloudflare 차단 가능)."
            ) from e
        _check_fail(data)
        return data

    def get_json(self, url: str) -> Any:
        """GET 전용 진입점(call의 얇은 별칭 — 어댑터가 쓰는 주 인터페이스)."""
        return self.call("GET", url)


# ─────────────────────────────────────────────────────────────────────────────
# 어댑터 표면 — canvas_api.CanvasApiAdapter와 같은 형태(RawAssignment/Attachment)로
# 반환해 until의 code_project 경로(assignment_router가 코드 파일 확장자·키워드로
# 판정)에 그대로 넘길 수 있게 한다.
# ─────────────────────────────────────────────────────────────────────────────
class EliceAdapter:
    """Elice 코딩 과제 읽기 전용 어댑터. 쓰기는 EliceClient가 코드 레벨에서 차단."""

    def __init__(self, token: Optional[str] = None, transport: Optional[Any] = None,
                timeout: float = 20, client: Optional[EliceClient] = None,
                course_ids: Optional[List[str]] = None):
        self.client = client or EliceClient(token=token, transport=transport, timeout=timeout)
        raw_ids = course_ids if course_ids is not None else (
            os.getenv("UNTIL_ELICE_COURSE_IDS", "").split(","))
        self.course_ids = [str(x).strip() for x in raw_ids if str(x).strip()]
        self.warnings: List[str] = []

    def whoami(self) -> dict:
        """계정 소유자 확인용 1회성 조회. fullname 등 개인정보는 호출부가 저장·로그 금지."""
        data = self.client.get_json(ACCOUNT_ME_URL)
        return data if isinstance(data, dict) else {}

    def list_course_lectures(self, course_id: Any) -> List[Dict[str, Any]]:
        """과목의 강의 트리(course.lectures[])."""
        data = self.client.get_json(course_url(course_id))
        return parse_course(data)["lectures"]

    def list_exercise_pages(self, lecture_id: Any) -> List[Dict[str, Any]]:
        """강의 내 코딩 과제(Exercise, material_type==4) 페이지만 골라낸다."""
        data = self.client.get_json(lecture_url(lecture_id))
        pages = parse_lecture(data)["pages"]
        return [p for p in pages if p.get("material_type") == EXERCISE_MATERIAL_TYPE]

    def fetch_exercise_assignment(self, page: Dict[str, Any], *,
                                  course_name: str = "") -> RawAssignment:
        """코딩 과제 페이지 1건 → RawAssignment(제목·본문(지문)·스켈레톤 첨부).

        첨부는 실제 바이트를 여기서 받지 않고(과금·지연 방지), 각 스켈레톤 파일의
        조회 URL만 Attachment.url에 실어 둔다 — 실제 텍스트는 download()가
        필요할 때만(수집 단계에서) 받는다. Canvas 어댑터의 지연 다운로드와 동일 패턴."""
        page_id = page.get("id")
        data = self.client.get_json(exercise_url(page_id))
        ex = parse_exercise(data)
        title = (page.get("title") or "(제목 없음)").strip()
        header = f"실행 환경: {ex['runtime']}\n\n" if ex.get("runtime") else ""
        description = f"{header}{ex['problem']}"
        atts: List[Attachment] = []
        img_id = ex.get("exercise_image_id")
        if img_id:
            for fname in ex.get("files") or []:
                atts.append(Attachment(name=fname, url=exercise_file_url(img_id, fname)))
        return RawAssignment(
            title=title,
            course=course_name or "(과목 미상)",
            description=description,
            attachments=atts,
            url=exercise_url(page_id),
        )

    def list_coding_assignments(self, course_id: Any) -> List[RawAssignment]:
        """과목의 모든 코딩 과제(Exercise)를 RawAssignment 목록으로.

        course → lectures(컨테이너 제외) → 각 lecture의 Exercise 페이지 → 과제 본문
        순으로 조회한다. 개별 lecture/exercise 조회 실패는 건너뛴다(부분 실패 허용)."""
        course_data = self.client.get_json(course_url(course_id))
        parsed = parse_course(course_data)
        course_title = parsed["title"]
        out: List[RawAssignment] = []
        failures = 0
        for lec in parsed["lectures"]:
            if not lec.get("total_page_count"):
                continue  # 컨테이너 lecture(total_page_count=0) — 실제 페이지 없음
            try:
                lecture_data = self.client.get_json(lecture_url(lec["id"]))
            except Exception:
                failures += 1
                continue
            pages = [p for p in parse_lecture(lecture_data)["pages"]
                    if p.get("material_type") == EXERCISE_MATERIAL_TYPE]
            for page in pages:
                try:
                    out.append(self.fetch_exercise_assignment(page, course_name=course_title))
                except Exception:
                    failures += 1
                    continue
        if failures:
            self.warnings.append(
                f"Elice 일부 항목 {failures}건을 불러오지 못해 나머지만 표시합니다.")
        return out

    def list_courses(self, base_url: str = "") -> List[CourseRef]:
        """DiscoveryAdapter 표면. 접근할 Elice 과목 ID는 env/생성자에서 명시한다."""
        if not self.course_ids:
            self.warnings.append(
                "Elice 과목 ID가 설정되지 않아 코딩 과제를 합치지 않았습니다. "
                "UNTIL_ELICE_COURSE_IDS에 과목 ID를 쉼표로 구분해 설정하세요.")
        return [CourseRef(id=cid, name=f"Elice {cid}") for cid in self.course_ids]

    def list_assignments(self, course: CourseRef, base_url: str = "",
                         bucket: Optional[str] = None) -> List[AssignmentRef]:
        """RawAssignment를 공용 인박스 AssignmentRef로 변환한다."""
        rows = self.list_coding_assignments(course.id)
        out: List[AssignmentRef] = []
        for row in rows:
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(row.url).query)
            aid = str((query.get("material_exercise_id") or [""])[0])
            pick_url = (row.url + "&until_title=" + urllib.parse.quote(row.title)
                        + "&until_course=" + urllib.parse.quote(row.course or course.name))
            out.append(AssignmentRef(
                id=aid, title=row.title, course_id=str(course.id),
                course_name=f"Elice · {row.course or course.name}", url=pick_url,
                due_at="", submitted=False, actionable=True))
        return out

    def course_id_for_url(self, url: str) -> None:
        """Elice 단건 URL에는 과목 ID가 없어 추가 강의자료 탐색을 생략한다."""
        return None

    def fetch_assignment(self, url: str) -> RawAssignment:
        """EtlSource 수집 표면: exercise URL 1건을 지문+스켈레톤으로 변환."""
        if not is_exercise_url(url):
            raise EliceReadOnlyError("허용되지 않은 Elice 과제 URL입니다.")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        exercise_id = str((query.get("material_exercise_id") or [""])[0])
        if not exercise_id:
            raise ValueError("Elice 과제 URL에 material_exercise_id가 없습니다.")
        title = str((query.get("until_title") or ["Elice 코딩 과제"])[0])
        course = str((query.get("until_course") or ["Elice"])[0])
        return self.fetch_exercise_assignment({"id": exercise_id, "title": title},
                                              course_name=course)

    def download(self, attachment: Attachment, dest_dir: str) -> str:
        """스켈레톤 코드 파일 1건을 받아 dest_dir에 저장(수집 단계에서만 호출)."""
        data = self.client.get_json(attachment.url)
        f = parse_exercise_file(data)
        name = f["filename"] or attachment.name
        dest = Path(dest_dir) / safe_filename(name)
        dest.write_text(f["content"] or "", encoding="utf-8")
        return str(dest)
