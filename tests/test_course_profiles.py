"""과목 프로파일 로더 + 실험 단위 연결 테스트 (오프라인·결정적).

COURSE_ALGORITHMS_2026F §3(course_profiles)·§4.2(experiment_id) 명세를 고정한다.
"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.context.assignment_router import AssignmentRoute, route_assignment
from until.context.course_profiles import (
    ALLOWED_ROUTE_HINTS, hint_applies, load_course_profiles,
    profile_for_course, route_hint_for_course, save_course_profiles,
    set_course_profiles_path_override,
)
from until.context.series import experiment_id, experiment_pre_sources, series_key


def _write_profiles(tmp: str, payload) -> pathlib.Path:
    p = pathlib.Path(tmp) / "course_profiles.json"
    if isinstance(payload, str):
        p.write_text(payload, encoding="utf-8")
    else:
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


_VALID = {
    "algo_version": "v0.2",
    "courses": [
        {"course_id": "123456", "alias": "논리설계실습", "route_hint": "hdl_lab",
         "toolchain": ["vivado"], "board": "Basys3", "series": ["실습", "lab"]},
        {"course_id": "123457", "alias": "실험과목",
         "route_hint": "lab_report_cycle", "cycle": ["pre", "notebook", "result"]},
        {"course_id": "123458", "alias": "교재문제과목",
         "route_hint": "textbook_problem_set"},
    ],
}


def test_load_valid():
    with tempfile.TemporaryDirectory() as tmp:
        p = _write_profiles(tmp, _VALID)
        profs = load_course_profiles(p)
        assert [c["route_hint"] for c in profs] == [
            "hdl_lab", "lab_report_cycle", "textbook_problem_set"]
        # 허용 힌트는 전부 공유 계약의 신설 strategy 3종.
        assert all(c["route_hint"] in ALLOWED_ROUTE_HINTS for c in profs)
        # 부가 키(toolchain 등)는 소비자용으로 보존된다.
        assert profs[0]["toolchain"] == ["vivado"]
    print("OK load valid (§3 스키마 3과목)")


def test_load_missing_file():
    # 파일 없음 — 예외 없이 빈 값/None(프로파일은 폴백이지 필수 입력이 아니다).
    ghost = pathlib.Path(tempfile.gettempdir()) / "until-no-such-profiles.json"
    assert load_course_profiles(ghost) == []
    assert profile_for_course("123456", path=ghost) is None
    assert route_hint_for_course(course_name="논리설계실습", path=ghost) == ""
    print("OK missing file (예외 없이 빈 값)")


def test_load_broken_json_and_bad_schema():
    with tempfile.TemporaryDirectory() as tmp:
        # JSON 자체가 깨짐.
        assert load_course_profiles(_write_profiles(tmp, "{깨진 json")) == []
        # 스키마 불일치 — 최상위가 리스트 / courses가 리스트 아님 / 항목이 비-dict
        # 또는 course_id·alias 둘 다 없음 → 전부 조용히 버린다.
        assert load_course_profiles(_write_profiles(tmp, [1, 2])) == []
        assert load_course_profiles(
            _write_profiles(tmp, {"courses": "논리설계실습"})) == []
        profs = load_course_profiles(_write_profiles(tmp, {"courses": [
            "쓰레기", {"route_hint": "hdl_lab"},                 # 조회 키 없음 → 제외
            {"course_id": "1", "alias": "논리설계실습", "route_hint": "hdl_lab"}]}))
        assert len(profs) == 1 and profs[0]["alias"] == "논리설계실습"
    print("OK broken json / bad schema (예외 없이 빈 값)")


def test_unknown_route_hint_ignored():
    # §3: route_hint가 허용 strategy 집합 밖이면 무시 — 오타가 경로를 켜지 않는다.
    with tempfile.TemporaryDirectory() as tmp:
        p = _write_profiles(tmp, {"courses": [
            {"course_id": "1", "alias": "논리설계실습", "route_hint": "essay_magic"},
            {"course_id": "2", "alias": "실험과목", "route_hint": ""},
            {"course_id": "3", "alias": "교재문제과목"}]})
        profs = load_course_profiles(p)
        assert [c["route_hint"] for c in profs] == ["", "", ""]
        assert route_hint_for_course("1", path=p) == ""
    print("OK unknown hint 무시 (허용 집합 밖 → \"\")")


def test_profile_lookup():
    with tempfile.TemporaryDirectory() as tmp:
        p = _write_profiles(tmp, _VALID)
        # course_id 정확 일치.
        assert profile_for_course("123457", path=p)["alias"] == "실험과목"
        # alias 부분일치 — eTL 과목명이 길어도('논리설계실습(디지털) 설계 및 실험') 잡힌다.
        got = profile_for_course(course_name="논리설계실습(디지털) 설계 및 실험", path=p)
        assert got and got["route_hint"] == "hdl_lab"
        # 일치 없음 → None. course_id는 부분일치를 허용하지 않는다(오연결 방지).
        assert profile_for_course("9999", course_name="기초회로", path=p) is None
        assert route_hint_for_course(course_name="교재문제과목", path=p) == \
            "textbook_problem_set"
    print("OK profile lookup (course_id 정확·alias 부분일치)")


def test_hint_never_beats_lexical_rules():
    # §3 (a): "route_hint는 결정적 규칙이 아무것도 못 잡았을 때만(= spec_clarification
    # 직전) 적용하는 폴백이다. 어휘 규칙을 이기지 못한다 — 사용자가 틀리게 적어도
    # 실제 명세가 이기게 한다."
    # 어휘 규칙이 아무것도 못 잡은 경우만 True. 제목은 v0.1·v0.2 어느 쪽에서도
    # 미분류로 남는 것을 쓴다 — '중간과제 1'은 v0.2에서 _MIDTERM_TASK가 잡으므로
    # 버전 민감(오케스트레이션 통합에서 실측된 이음새).
    fallback = route_assignment(title="알 수 없는 항목")
    assert fallback.strategy == "spec_clarification" and hint_applies(fallback)
    # 어휘 규칙이 잡은 라우트는 종류를 불문하고 힌트가 못 이긴다.
    for title in ("3주차 소감문", "실습 1 보고서", "문제 풀이 세트 2"):
        route = route_assignment(title=title)
        assert route.strategy != "spec_clarification"
        assert not hint_applies(route), title
    print("OK 제약(a) — 힌트는 spec_clarification 직전 폴백일 뿐, 어휘 규칙을 못 이김")


def test_hint_never_flips_non_actionable():
    # §3 (b): "non_actionable 판정은 힌트로 뒤집지 않는다(현행 route_inference와
    # 같은 안전 원칙)." — 퀴즈·시험·증빙 응시 슬롯에 초안을 만들면 안 된다.
    quiz = route_assignment(title="실험 5 퀴즈")   # 실코퍼스 보호 케이스(§5 순서 4)
    assert not quiz.actionable and not hint_applies(quiz)
    exam = route_assignment(title="중간고사")
    assert not exam.actionable and not hint_applies(exam)
    # 직접 구성한 non_actionable도 동일 — actionable=False면 무조건 False.
    assert not hint_applies(AssignmentRoute(
        "non_actionable", "테스트", (), actionable=False))
    assert not hint_applies(None)
    print("OK 제약(b) — non_actionable은 힌트로 절대 못 뒤집음")


def test_per_user_path_override_isolates_profiles():
    """§3 폴백은 사용자별이어야 한다 — 전역 파일 하나면 남의 힌트가 나에게 걸린다.

    2026-08-22까지 저장 경로가 서버 전역 `_until_work/course_profiles.json`
    하나였다. 클라우드에서는 한 사람이 적은 힌트가 전원에게 적용되고, 애초에
    값을 적을 화면도 없어 §3이 라이브에서 성립한 적이 없었다.
    """
    from until import web
    with tempfile.TemporaryDirectory() as tmp:
        a = pathlib.Path(tmp) / "a" / "course_profiles.json"
        b = pathlib.Path(tmp) / "b" / "course_profiles.json"
        try:
            set_course_profiles_path_override(a)
            save_course_profiles(web.course_rows_from_form({
                "alias0": ["논리설계실습"], "hint0": ["hdl_lab"],
            }))
            assert route_hint_for_course(
                course_name="논리설계실습(디지털) 설계 및 실험") == "hdl_lab"
            # 다른 사용자에게는 보이지 않는다.
            set_course_profiles_path_override(b)
            assert route_hint_for_course(
                course_name="논리설계실습(디지털) 설계 및 실험") == ""
            # 돌아오면 그대로 있다(요청 스코프이지 1회용이 아니다).
            set_course_profiles_path_override(a)
            assert route_hint_for_course(course_name="논리설계실습") == "hdl_lab"
        finally:
            set_course_profiles_path_override(None)
    print("OK 과목 프로파일 사용자별 격리")


def test_save_drops_unusable_rows():
    """저장은 로더와 같은 검증을 통과한 것만 남긴다.

    허용 밖 힌트·빈 줄·유형 미지정이 파일에 남으면 다음에 열었을 때 "적었는데
    안 먹는다"가 된다. 걸러 두면 저장된 것은 반드시 적용 가능한 것뿐이다.
    """
    from until import web
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp) / "course_profiles.json"
        rows = web.course_rows_from_form({
            "alias0": ["논리설계실습"], "hint0": ["hdl_lab"],
            "alias1": [""], "hint1": [""],                    # 안 쓴 줄
            "alias2": ["실험과목"], "hint2": [""],             # 유형 미지정
            "alias3": ["오타"], "hint3": ["made_up_strategy"],  # 허용 밖
            "alias4": [""], "hint4": ["hdl_lab"],             # 조회 불가(이름 없음)
            "alias5": ["논리설계실습"], "hint5": ["textbook_problem_set"],  # 중복
        })
        save_course_profiles(rows, p)
        saved = load_course_profiles(p)
        assert [(c["alias"], c["route_hint"]) for c in saved] == [
            ("논리설계실습", "hdl_lab")], saved
        # 저장 형식은 §3 스키마 그대로 — 로더가 다시 읽을 수 있어야 한다.
        raw = json.loads(p.read_text(encoding="utf-8"))
        assert raw["algo_version"] == "v0.2" and isinstance(raw["courses"], list)
        # 전체 지우기(빈 목록)도 성립한다 — 힌트를 끄는 길이 있어야 한다.
        save_course_profiles([], p)
        assert load_course_profiles(p) == []
    print("OK 저장 검증 (빈 줄·미지정·허용 밖·중복·전체 삭제)")


def test_saved_hint_drives_routing():
    """저장 → 조회 → 라우팅이 실제로 이어지는가(화면부터 판정까지 한 줄).

    로더 단위시험만으로는 '적었는데 라우팅이 안 바뀌는' 배선 누락을 못 잡는다 —
    실제로 `run(course_name=...)` 미전달로 폴백이 통째로 죽어 있었다(2026-08-21).
    """
    from until import web
    from until.context.assignment_router import route_for_strategy
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp) / "course_profiles.json"
        save_course_profiles(web.course_rows_from_form(
            {"alias0": ["논리설계실습"], "hint0": ["hdl_lab"]}), p)
        # 어휘 규칙이 아무것도 못 잡는 과제(§3이 겨냥한 첫 주차 실측 상황).
        route = route_assignment(title="알 수 없는 항목")
        assert route.strategy == "spec_clarification", route.strategy
        assert hint_applies(route)
        hint = route_hint_for_course(course_name="논리설계실습(디지털) 설계 및 실험",
                                     path=p)
        hinted = route_for_strategy(hint)
        assert hinted is not None and hinted.strategy == "hdl_lab"
    print("OK 저장한 힌트가 라우팅까지 이어짐")


def test_experiment_id():
    # §4.2: 예비·결과·랩노트 세 표면형이 같은 실험 id로 떨어져야 3단계가 연결된다.
    assert experiment_id("실험 4 결과보고서") == "exp-4"
    assert experiment_id("예비보고서 4주차") == "exp-4"
    assert experiment_id("랩노트 제출(4주차)") == "exp-4"
    # 다른 실험은 다른 id, 번호 없음·빈 제목·날짜뿐인 제목은 "".
    assert experiment_id("예비보고서 3주차") == "exp-3"
    assert experiment_id("기말 보고서") == ""
    assert experiment_id("") == ""
    assert experiment_id("예비보고서 (3/17)") == ""     # 날짜는 실험 번호가 아니다
    assert experiment_id("3장 문제 5") == ""            # 숫자 2개 → 확정 불가
    # series_key와의 역할 분리 — 표면형이 다르면 시리즈는 다르지만 실험은 같다.
    assert series_key("실험 4 결과보고서") != series_key("예비보고서 4주차")
    print("OK experiment_id (3표면형 → 같은 id·번호 없음 → \"\")")


def test_experiment_pre_sources():
    subs = [
        {"title": "예비보고서 4주차", "submitted_at": "2026-09-20T08:00:00Z",
         "body": "이번 실험의 이론과 절차 요약"},
        {"title": "예비보고서 3주차", "submitted_at": "2026-09-13T08:00:00Z",
         "body": "다른 실험의 예비"},                     # 다른 실험 번호 → 제외
        {"title": "랩노트 제출(4주차)", "submitted_at": "2026-09-21T08:00:00Z",
         "body": "현장 기록"},                            # 예비 단계 아님 → 제외
        {"title": "실험 4 결과보고서", "submitted_at": "2026-09-22T08:00:00Z",
         "body": "자기 자신"},                            # 자기 자신 → 제외
        "쓰레기",                                          # 비-dict 방어
    ]
    srcs = experiment_pre_sources("실험 4 결과보고서", subs)
    assert len(srcs) == 1
    s = srcs[0]
    assert s.title == "[예비보고서] 예비보고서 4주차"
    assert "이론과 절차" in s.text
    # 경계선 지침 — 예비보고서로 결과 수치를 만들지 말라는 문구가 본문에 명시.
    assert "실측" in s.text and "만들지 말" in s.text
    # 실험 번호를 못 찾는 제목·빈 입력은 빈 목록.
    assert experiment_pre_sources("기말 보고서", subs) == []
    assert experiment_pre_sources("실험 4 결과보고서", None) == []
    print("OK 예비→결과 맥락 (같은 실험만·예비만·자기 제외·경계선 지침)")


if __name__ == "__main__":
    test_load_valid()
    test_load_missing_file()
    test_load_broken_json_and_bad_schema()
    test_unknown_route_hint_ignored()
    test_profile_lookup()
    test_hint_never_beats_lexical_rules()
    test_hint_never_flips_non_actionable()
    test_per_user_path_override_isolates_profiles()
    test_save_drops_unusable_rows()
    test_saved_hint_drives_routing()
    test_experiment_id()
    test_experiment_pre_sources()
    print("\nCOURSE PROFILES TESTS PASS")
