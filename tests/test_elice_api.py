"""Elice 코딩 과제 어댑터 테스트 — 읽기 전용 allowlist 강제(쓰기 차단) 핵심 검증.

네트워크·subprocess 불필요 — transport 콜백을 항상 fake로 주입한다.
실제 subprocess(curl) 호출은 0(가드가 subprocess.run 전에 예외를 던진다)."""
import json
import os
import sys
import pathlib
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.capture.sources.elice_api import (
    EliceClient, EliceAdapter, EliceReadOnlyError,
    parse_course, parse_lecture, parse_exercise, parse_exercise_file,
    course_url, lecture_url, exercise_url, exercise_file_url,
    ACCOUNT_ME_URL, _is_allowed, _curl_transport,
)
from until.capture.sources.models import RawAssignment, Attachment

# ─────────────────────────────────────────────────────────────────────────────
# 픽스처 — 명세에서 실측 확인된 필드만 사용(실제 라이브 호출 없음)
# ─────────────────────────────────────────────────────────────────────────────
COURSE_FIXTURE = {
    "course": {
        "title": "프로그래밍1 실습",
        "lectures": [
            {"id": 10, "parent_lecture_id": None, "depth": 0, "order_no": 1,
             "lecture_type": 0, "title": "1주차", "total_page_count": 0},  # 컨테이너
            {"id": 11, "parent_lecture_id": 10, "depth": 1, "order_no": 1,
             "lecture_type": 0, "title": "1주차 실습", "total_page_count": 3},
        ],
    },
    "course_sections": [{"id": 1, "name": "A반"}],
}

LECTURE_FIXTURE = {
    "lecture": {
        "main_lecture_pages": [
            {"id": 501, "material_id": 9001, "material_type": 4,
             "material_key": "exercise_9001", "title": "정수 합 구하기",
             "point": 10, "is_graded": True, "is_completed": False},
            {"id": 502, "material_id": 9002, "material_type": 1,
             "material_key": "video_9002", "title": "강의 영상",
             "point": 0, "is_graded": False, "is_completed": True},
        ],
        "sub_lecture_pages": [
            {"id": 503, "material_id": 9003, "material_type": 4,
             "material_key": "exercise_9003", "title": "배열 정렬",
             "point": 10, "is_graded": True, "is_completed": False},
        ],
        "close_schedule_datetime": None,
    }
}

EXERCISE_FIXTURE = {
    "material_exercise": {
        "description": "## 문제\n정수 N개를 입력받아 합을 출력하시오.",
        "description_rendered": "<h2>문제</h2><p>정수 N개...</p>",
        "instruction_content": "",
        "ready_exercise_image": {
            "id": 7001,
            "filelist": ["main.c", "README.md"],
            "task_filelist": ["main.c"],
            "read_only_filelist": ["README.md"],
            "base_image": {"title": "C / C++"},
        },
    }
}

EXERCISE_FILE_FIXTURE = {
    "exercise_file": {
        "filename": "main.c",
        "content": "#include <stdio.h>\nint main(){return 0;}\n",
    }
}

FAIL_RESULT_FIXTURE = {"_result": {"status": "fail", "message": "권한이 없습니다"}}
DETAIL_ONLY_FIXTURE = {"detail": "Not authenticated."}


def _dict_transport(url_map, calls=None):
    """url → fixture dict 매핑으로 (status, body) 를 돌려주는 fake transport."""
    def _t(method, url, headers):
        if calls is not None:
            calls.append((method, url))
        assert method == "GET", f"transport에 GET 아닌 요청이 들어옴: {method} {url}"
        if url not in url_map:
            raise AssertionError(f"예상 밖 URL 요청: {url}")
        return 200, json.dumps(url_map[url])
    return _t


def _boom_transport(method, url, headers):
    raise AssertionError(f"차단됐어야 할 요청이 transport까지 도달함: {method} {url}")


# ─────────────────────────────────────────────────────────────────────────────
# ① course.lectures 파싱
# ─────────────────────────────────────────────────────────────────────────────
def test_parse_course_lectures():
    parsed = parse_course(COURSE_FIXTURE)
    assert parsed["title"] == "프로그래밍1 실습"
    assert {lec["id"] for lec in parsed["lectures"]} == {10, 11}
    container = next(lec for lec in parsed["lectures"] if lec["id"] == 10)
    child = next(lec for lec in parsed["lectures"] if lec["id"] == 11)
    assert container["total_page_count"] == 0  # 컨테이너 lecture
    assert child["total_page_count"] == 3
    assert child["parent_lecture_id"] == 10
    print("OK parse_course가 lectures를 파싱")


# ─────────────────────────────────────────────────────────────────────────────
# ② material_type 4만 코딩 과제로 골라냄
# ─────────────────────────────────────────────────────────────────────────────
def test_list_exercise_pages_filters_material_type_4():
    calls = []
    transport = _dict_transport({lecture_url(11): LECTURE_FIXTURE}, calls=calls)
    adapter = EliceAdapter(token="t", transport=transport)
    pages = adapter.list_exercise_pages(11)
    assert {p["id"] for p in pages} == {501, 503}
    assert all(p["material_type"] == 4 for p in pages)
    # 502(video, material_type=1)는 제외됐어야 함
    assert 502 not in {p["id"] for p in pages}
    print("OK material_type==4(Exercise)만 코딩 과제로 필터링")


def test_parse_lecture_all_pages():
    parsed = parse_lecture(LECTURE_FIXTURE)
    assert len(parsed["pages"]) == 3  # main 2 + sub 1
    print("OK parse_lecture가 main+sub 페이지 전부 파싱")


# ─────────────────────────────────────────────────────────────────────────────
# ③ exercise 지문+스켈레톤 파일 목록 파싱
# ─────────────────────────────────────────────────────────────────────────────
def test_parse_exercise_problem_and_files():
    ex = parse_exercise(EXERCISE_FIXTURE)
    assert "정수 N개" in ex["problem"]
    assert ex["files"] == ["main.c", "README.md"]
    assert ex["task_files"] == ["main.c"]
    assert ex["readonly_files"] == ["README.md"]
    assert ex["runtime"] == "C / C++"
    assert ex["exercise_image_id"] == 7001
    print("OK parse_exercise가 지문+스켈레톤 파일 목록을 파싱")


def test_parse_exercise_falls_back_to_rendered_html():
    data = {
        "material_exercise": {
            "description": "",
            "description_rendered": "<p>렌더링된 지문</p>",
            "ready_exercise_image": {"id": 1, "filelist": [], "base_image": {}},
        }
    }
    ex = parse_exercise(data)
    assert "렌더링된 지문" in ex["problem"]
    print("OK description 없으면 description_rendered로 폴백")


def test_fetch_exercise_assignment_shape():
    transport = _dict_transport({exercise_url(501): EXERCISE_FIXTURE})
    adapter = EliceAdapter(token="t", transport=transport)
    page = {"id": 501, "title": "정수 합 구하기", "material_type": 4}
    raw = adapter.fetch_exercise_assignment(page, course_name="프로그래밍1 실습")
    assert isinstance(raw, RawAssignment)
    assert raw.title == "정수 합 구하기"
    assert raw.course == "프로그래밍1 실습"
    assert "정수 N개" in raw.description
    assert "C / C++" in raw.description
    names = {a.name for a in raw.attachments}
    assert names == {"main.c", "README.md"}
    for att in raw.attachments:
        assert _is_allowed(att.url), f"첨부 URL이 allowlist 밖: {att.url}"
    print("OK fetch_exercise_assignment이 RawAssignment(제목/본문/첨부) 형태로 반환")


# ─────────────────────────────────────────────────────────────────────────────
# ④ exercise_file content 파싱 + 다운로드
# ─────────────────────────────────────────────────────────────────────────────
def test_parse_exercise_file_content():
    f = parse_exercise_file(EXERCISE_FILE_FIXTURE)
    assert f["filename"] == "main.c"
    assert "stdio.h" in f["content"]
    print("OK parse_exercise_file이 filename/content를 파싱")


def test_download_writes_skeleton_file():
    url = exercise_file_url(7001, "main.c")
    transport = _dict_transport({url: EXERCISE_FILE_FIXTURE})
    adapter = EliceAdapter(token="t", transport=transport)
    att = Attachment(name="main.c", url=url)
    with tempfile.TemporaryDirectory() as tmp:
        path = adapter.download(att, tmp)
        content = pathlib.Path(path).read_text(encoding="utf-8")
        assert "stdio.h" in content
    print("OK download()가 exercise_file content를 파일로 저장")


def test_list_coding_assignments_end_to_end():
    calls = []
    url_map = {
        course_url(123): COURSE_FIXTURE,
        lecture_url(11): LECTURE_FIXTURE,
        exercise_url(501): EXERCISE_FIXTURE,
        exercise_url(503): EXERCISE_FIXTURE,
    }
    transport = _dict_transport(url_map, calls=calls)
    adapter = EliceAdapter(token="t", transport=transport)
    out = adapter.list_coding_assignments(123)
    assert {a.title for a in out} == {"정수 합 구하기", "배열 정렬"}
    # 컨테이너 lecture(10, total_page_count=0)는 조회하지 않아야 함
    requested = {u for _, u in calls}
    assert lecture_url(10) not in requested
    print("OK list_coding_assignments가 course→lecture→exercise를 엮어 반환(컨테이너 스킵)")


# ─────────────────────────────────────────────────────────────────────────────
# ⑤ 쓰기/비허용 경로 차단
# ─────────────────────────────────────────────────────────────────────────────
def test_write_method_blocked_before_transport():
    client = EliceClient(token="t", transport=_boom_transport)
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        try:
            client.call(method, course_url(1))
        except EliceReadOnlyError as e:
            assert "쓰기" in str(e) or "GET" in str(e)
        else:
            raise AssertionError(f"{method}는 차단돼야 함")
    print("OK GET 아닌 메서드는 transport 호출 전에 차단")


def test_disallowed_path_blocked_before_transport():
    client = EliceClient(token="t", transport=_boom_transport)
    bad_urls = [
        "https://api-rest.elice.io/org/snu/material_exercise/submit/?id=1",
        "https://api-rest.elice.io/org/snu/material_exercise/exercise_image/exercise_file/run/?id=1",
        "https://evil.example.com/org/snu/course/get/?course_id=1",
        "http://api-rest.elice.io/org/snu/course/get/?course_id=1",  # http(비-https)
    ]
    for url in bad_urls:
        try:
            client.get_json(url)
        except EliceReadOnlyError as e:
            assert "allowlist" in str(e)
        else:
            raise AssertionError(f"allowlist 밖 URL은 차단돼야 함: {url}")
    print("OK allowlist 밖 경로(쓰기 엔드포인트·다른 호스트·비-https)는 transport 호출 전에 차단")


def test_curl_transport_blocks_write_before_subprocess():
    # 서브프로세스가 실제로 뜨지 않는지는 이 가드가 subprocess.run보다 먼저 실행되는지로 검증.
    try:
        _curl_transport("POST", course_url(1), {})
    except EliceReadOnlyError:
        pass
    else:
        raise AssertionError("_curl_transport도 POST를 막아야 함")
    print("OK 기본 transport(curl)도 GET 아니면 subprocess 실행 전에 차단")


def test_curl_token_is_not_in_process_argv():
    import until.capture.sources.elice_api as module
    calls = []
    original = module.subprocess.run

    def fake(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return type("Proc", (), {"stdout": "{}\n<<<UNTIL_ELICE_HTTP_STATUS:200",
                                  "stderr": ""})()

    module.subprocess.run = fake
    try:
        status, _ = _curl_transport("GET", course_url(1),
                                    {"Authorization": "Bearer TOPSECRET"})
    finally:
        module.subprocess.run = original
    assert status == 200
    cmd, kwargs = calls[0]
    assert "TOPSECRET" not in " ".join(cmd)
    assert "TOPSECRET" in kwargs["input"] and "-H" in cmd and "@-" in cmd


def test_allowed_paths_pass():
    for url in (ACCOUNT_ME_URL, course_url(1), lecture_url(1), exercise_url(1),
                exercise_file_url(1, "a.c")):
        assert _is_allowed(url), f"허용돼야 할 URL이 막힘: {url}"
    print("OK 명시된 5개 읽기 경로는 allowlist 통과")


# ─────────────────────────────────────────────────────────────────────────────
# ⑥ 토큰 없을 때 명확한 에러
# ─────────────────────────────────────────────────────────────────────────────
def test_requires_token():
    saved = os.environ.pop("UNTIL_ELICE_TOKEN", None)
    try:
        try:
            EliceClient(token=None, transport=_boom_transport)
        except ValueError as e:
            assert "토큰" in str(e)
        else:
            raise AssertionError("토큰 없으면 ValueError여야 함")
    finally:
        if saved is not None:
            os.environ["UNTIL_ELICE_TOKEN"] = saved
    print("OK 토큰 미설정 시 명확한 ValueError")


def test_token_from_env():
    saved = os.environ.get("UNTIL_ELICE_TOKEN")
    os.environ["UNTIL_ELICE_TOKEN"] = "env-token"
    try:
        client = EliceClient(transport=_boom_transport)
        assert client.token == "env-token"
    finally:
        if saved is None:
            os.environ.pop("UNTIL_ELICE_TOKEN", None)
        else:
            os.environ["UNTIL_ELICE_TOKEN"] = saved
    print("OK UNTIL_ELICE_TOKEN 환경변수로 토큰 주입")


# ─────────────────────────────────────────────────────────────────────────────
# 실패 응답(HTTP 200 바디 실패 표시) 처리
# ─────────────────────────────────────────────────────────────────────────────
def test_fail_result_status_raises():
    transport = _dict_transport({course_url(1): FAIL_RESULT_FIXTURE})
    client = EliceClient(token="t", transport=transport)
    try:
        client.get_json(course_url(1))
    except RuntimeError as e:
        assert "실패" in str(e)
    else:
        raise AssertionError("_result.status=fail 은 RuntimeError여야 함")
    print("OK {_result:{status:fail}} 응답을 RuntimeError로 변환")


def test_detail_only_error_raises():
    transport = _dict_transport({course_url(1): DETAIL_ONLY_FIXTURE})
    client = EliceClient(token="t", transport=transport)
    try:
        client.get_json(course_url(1))
    except RuntimeError as e:
        assert "Not authenticated" in str(e)
    else:
        raise AssertionError("detail만 있는 응답은 RuntimeError여야 함")
    print("OK {detail:...}만 있는 응답을 RuntimeError로 변환")


def test_success_payload_with_id_key_not_treated_as_failure():
    # account/me는 detail 없이 id/fullname을 주므로 실패로 오탐하면 안 된다.
    payload = {"id": 42, "fullname": "학생"}
    transport = _dict_transport({ACCOUNT_ME_URL: payload})
    client = EliceClient(token="t", transport=transport)
    data = client.get_json(ACCOUNT_ME_URL)
    assert data["id"] == 42
    print("OK 정상 account/me 응답은 실패로 오탐하지 않음")


if __name__ == "__main__":
    test_parse_course_lectures()
    test_list_exercise_pages_filters_material_type_4()
    test_parse_lecture_all_pages()
    test_parse_exercise_problem_and_files()
    test_parse_exercise_falls_back_to_rendered_html()
    test_fetch_exercise_assignment_shape()
    test_parse_exercise_file_content()
    test_download_writes_skeleton_file()
    test_list_coding_assignments_end_to_end()
    test_write_method_blocked_before_transport()
    test_disallowed_path_blocked_before_transport()
    test_curl_transport_blocks_write_before_subprocess()
    test_curl_token_is_not_in_process_argv()
    test_allowed_paths_pass()
    test_requires_token()
    test_token_from_env()
    test_fail_result_status_raises()
    test_detail_only_error_raises()
    test_success_payload_with_id_key_not_treated_as_failure()
    print("\nELICE API TEST PASS")
