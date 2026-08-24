"""전수 과제 처리경로 라우터 테스트."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from until.context.assignment_router import (
    assignment_route_directive, route_assignment, route_for_strategy)


def test_explicit_routes_and_safe_fallback():
    cases = [
        ({"title": "5주차 질의"}, "weekly_inquiry"),
        ({"title": "피피티 제출"}, "presentation_conversion"),
        ({"title": "서비스디자인 팀과제 제출"}, "team_project"),
        ({"title": "3/17 조별활동 보고서"}, "activity_form"),
        ({"title": "과제 1", "attachment_names": ["assignment1.Rmd"]}, "rmd_notebook"),
        ({"title": "중간고사 제출 연습", "course_name": "통계학실험"}, "rmd_notebook"),
        ({"title": "Project", "attachment_names": ["starter.zip"]}, "zip_project"),
        ({"title": "3주차 소감문"}, "reflective_series"),
        ({"title": "숙제3"}, "distributed_spec"),
        ({"title": "실습4 레포트"}, "distributed_spec"),
        ({"title": "관찰 실험 보고서"}, "evidence_report"),
        ({"title": "서론 수정"}, "staged_writing"),
        ({"title": "알 수 없는 항목"}, "spec_clarification"),
    ]
    for kwargs, expected in cases:
        got = route_assignment(**kwargs)
        assert got.strategy == expected, (kwargs, got)
        assert got.required_evidence
        if expected == "spec_clarification":
            assert got.questions and "확인" in got.questions[0]
    print("OK 명시적 알고리즘 10종 + 안전 정보보완 경로")


def test_non_actionable_grade_rows():
    for title in ("M1", "F8", "중간 총점", "출석 점수", "13주차 출석"):
        got = route_assignment(title=title)
        assert got.strategy == "non_actionable" and not got.actionable
    print("OK 성적부 자리표시 제외")


def test_grade_notice_and_personal_upload_excluded():
    # 실코퍼스(기초회로): 성적 공지가 과제로 등록되거나('프로젝트 최종 환산점수'),
    # 본인 증빙 서류 업로드 슬롯('소자 구매 내역 제출')이 evidence_report로 라우팅돼
    # 200자 가드에 걸리던 회귀 — 둘 다 초안 생성 대상이 아니다.
    for title in ("프로젝트 최종 환산점수", "환산 점수", "최종 환산점수"):
        got = route_assignment(title=title)
        assert got.strategy == "non_actionable" and not got.actionable, (title, got)
    for title in ("소자 구매 내역 제출", "프로젝트 결석 증빙자료", "수료 증명서 제출"):
        got = route_assignment(title=title)
        assert got.strategy == "personal_upload" and not got.actionable, (title, got)
    # 기존 라우트를 빼앗으면 안 된다.
    assert route_assignment(title="관찰 실험 보고서").strategy == "evidence_report"
    print("OK 성적 공지·증빙 업로드 제외")


def test_quiz_slots_excluded():
    # 실코퍼스(기여자 B, 21건): LMS 퀴즈 슬롯은 현장·온라인 응시형이라 초안 생성 대상이
    # 아닌데, '실험 N 퀴즈'는 제목의 '실험'이 evidence_report로 걸려 보고서 초안이
    # 나오고, '퀴즈N'·'Quiz'는 spec_clarification으로 빠지던 회귀.
    for title in ("퀴즈1", "퀴즈4", "Quiz, 연구실책임자용",
                  "03.23. 월 분반 실험 1 퀴즈", "06.02 화 분반 실험 6 퀴즈"):
        got = route_assignment(title=title)
        assert got.strategy == "non_actionable" and not got.actionable, (title, got)
    # 퀴즈를 '만드는' 과제나 제출 템플릿 첨부가 있는 퀴즈는 제외 대상이 아니다.
    assert route_assignment(title="퀴즈 문항 제작 과제").actionable
    assert route_assignment(
        title="퀴즈1", attachment_names=["quiz1.pdf"]).actionable
    # 기존 라우트를 빼앗으면 안 된다.
    assert route_assignment(title="관찰 실험 보고서").strategy == "evidence_report"
    print("OK 퀴즈 응시 슬롯 제외")


def test_induced_routes_from_jihu_corpus():
    # 실코퍼스(기여자 B) spec_clarification 70건 귀납 — 다수가 새 유형이 아니라
    # '영어 제목·접두어·변형 미지원'이었다. 각 클러스터의 실제 제목으로 고정.
    cases = [
        # 화학 예비대학 problem set 6건 — 문제 풀이 세트(신규 strategy)
        ({"title": "2025 예비대학 problem set_Elements, Compounds, Nomenclature"},
         "problem_set"),
        # 물리학1 [공통과제]HW1~5 — 괄호 접두어가 번호형 fullmatch를 막던 회귀
        ({"title": "[공통과제]HW1"}, "distributed_spec"),
        # Creative Engineer Lab N Report/Code 21건 — 영어 키워드 미지원
        ({"title": "Lab 1 Report"}, "evidence_report"),
        ({"title": "Lab 2 Code"}, "code_project"),
        # 체력단련 운동일지 3건 — '활동 일지'만 매칭되던 규칙
        ({"title": "첫번째 운동일지",
          "description": "지난 2주간 체험한 운동들에 대한 일지를 남깁시다."},
         "activity_form"),
        # 자기소개서·영어 에세이 프롬프트 — 글쓰기
        ({"title": "자기소개서"}, "staged_writing"),
        ({"title": "AI_ the good and the bad (your thoughts)",
          "description": "For Thursday (before class), write about: the pros "
                         "and cons of AI."}, "staged_writing"),
        # College English Self-Evaluation — 성찰 계열
        ({"title": "Self-Evaluation"}, "reflective_series"),
        # CO-Week 수료증 제출 — 본인 서류 업로드
        ({"title": "제5회 CO-Week Academy 수료증 제출"}, "personal_upload"),
    ]
    for kwargs, expected in cases:
        got = route_assignment(**kwargs)
        assert got.strategy == expected, (kwargs, got)
    # 시험 계열(영어 문항 슬롯·단독 중간/기말)과 외부 시스템(UNIMe)은 비실행.
    # '시험 1'(기여자 A 창의공학설계)은 확장 전부터 제외였다 — 확장이 빼앗으면 안 된다.
    for title in ("midterm problem 5", "Final problem 1", "중간", "기말",
                  "시험 1", "시험 2", "고사",
                  "UNIMe 1~3주차", "UNIMe 13주차", "유니미"):
        got = route_assignment(title=title)
        assert got.strategy == "non_actionable" and not got.actionable, (title, got)
    # 기존 라우트·정당한 묻기는 그대로다.
    assert route_assignment(title="중간 보고서").strategy == "evidence_report"
    assert route_assignment(title="기말 레포트").strategy == "evidence_report"
    assert route_assignment(title="Day-1").strategy == "spec_clarification"
    assert route_assignment(
        title="중간고사", attachment_names=["hw.pdf"]).actionable
    print("OK 기여자 B 코퍼스 귀납 규칙 9종")


def test_induced_routes_from_jaewon_corpus():
    # 실코퍼스(기여자 C) spec_clarification 26건 귀납 — 괄호 접미어·성적 행 변형·
    # 교재 문제풀이·감상문·인증샷. 잔여 2건(제목 신호 전무)은 LLM 폴백 몫.
    cases = [
        # 물리2: 마감 괄호 접미어·문항별 슬롯이 번호형 fullmatch를 막던 회귀
        ({"title": "숙제5 (12/15 까지)"}, "distributed_spec"),
        ({"title": "과제3 (11/6 까지)"}, "distributed_spec"),
        ({"title": "숙제5(3)"}, "distributed_spec"),
        # 신호및시스템: 교재 챕터 문제 풀이
        ({"title": "Chapter 1,2,3 과제 안내"}, "problem_set"),
        ({"title": "Chapter 7,8,9,10 과제 안내"}, "problem_set"),
        # 프로그래밍방법론: skeleton code 완성 제출
        ({"title": "Lab2 Homework 제출함"}, "code_project"),
        # 감상문(농구·한국사) — 성찰·반응 계열
        ({"title": "농구 감상문"}, "reflective_series"),
        ({"title": "감상문 과제"}, "reflective_series"),
    ]
    for kwargs, expected in cases:
        got = route_assignment(**kwargs)
        assert got.strategy == expected, (kwargs, got)
    # 성적 행 변형('중간 점수'·'중간2 점수')과 인증샷 제출은 비실행.
    for title, strategy in (("중간 점수", "non_actionable"),
                            ("중간2 점수", "non_actionable"),
                            ("기말 점수", "non_actionable")):
        got = route_assignment(title=title)
        assert got.strategy == strategy and not got.actionable, (title, got)
    # 인증샷 제출(실물 증빙, 본문에서 감지)은 초안 대상이 아니다.
    got = route_assignment(
        title="조별 티타임 과제",
        description="조별 티타임 인증샷 제출(대표 1명, 제출시 조원 명단 기입)")
    assert got.strategy == "personal_upload" and not got.actionable, got
    # 기존 라우트 보존 + 제목 신호가 전무한 건 여전히 묻는다(LLM 폴백 몫).
    assert route_assignment(title="숙제3").strategy == "distributed_spec"
    assert route_assignment(title="중간 총점").strategy == "non_actionable"
    assert route_assignment(
        title="지역방어 공격과 수비, 그리고 나의 역할").strategy == "spec_clarification"
    print("OK 기여자 C 코퍼스 귀납 규칙 6종")


def test_v02_adversarial_fixes():
    # 적대적 회귀(8/13 인라인)에서 찾은 오분류 3건의 수정 고정.
    prev = os.environ.get("UNTIL_ALGO_VERSION")
    try:
        os.environ["UNTIL_ALGO_VERSION"] = "v0.2"
        # ① '실습일지'는 v0.1이 잡던 activity_form을 v0.2에서도 유지해야 한다
        #    (_FORM_EXCLUDE가 결합 전체를 깎아 evidence_report로 역행하던 회귀).
        assert route_assignment(title="간호학 실습일지").strategy == "activity_form"
        # ② HDL 어휘가 있어도 행정 항목(설치·대여·신청)은 초안 대상이 아니다.
        for title in ("FPGA 보드 대여 신청", "Verilog 설치 안내"):
            got = route_assignment(title=title)
            assert got.strategy != "hdl_lab", (title, got)
        # 실과제는 그대로: '활동 보고서'→양식, '실험 활동 보고서'→보고서 유지,
        # HDL 회차형 실과제는 여전히 hdl_lab.
        assert route_assignment(title="활동 보고서").strategy == "activity_form"
        assert route_assignment(
            title="실험 활동 보고서").strategy == "evidence_report"
        assert route_assignment(
            title="실습 3", course_name="논리설계실습 FPGA 설계").strategy == "hdl_lab"
    finally:
        if prev is None:
            os.environ.pop("UNTIL_ALGO_VERSION", None)
        else:
            os.environ["UNTIL_ALGO_VERSION"] = prev
    # v0.1 불변 재확인.
    assert route_assignment(title="간호학 실습일지").strategy == "activity_form"
    print("OK v0.2 적대적 회귀 수정 3건")


def test_context_bundle_zip_marker_does_not_flip_route():
    # 실코퍼스(대학 글쓰기 1 '요약문 쓰기'): 컨텍스트 번들에 인용된 강의자료
    # zip 발췌의 [ZIP_PROJECT:] 표식이 과제 첨부처럼 오인돼 staged_writing이
    # zip_project로 뒤집히던 회귀. 번들 속 표식은 라우팅 신호가 아니다.
    from until.context.assignment_router import route_documents

    class _Doc:
        def __init__(self, source, text):
            self.source, self.text = source, text

    spec_doc = _Doc("spec.md", "# 요약문 쓰기\n\n제시문을 읽고 요약문을 작성해 제출하세요.")
    bundle = _Doc(
        "etl_context/context.md",
        "# eTL 과목 컨텍스트 번들\n\n## 컨텍스트 1: 결시사유서 양식.zip\n본문 발췌:\n"
        "[ZIP_PROJECT: 파일을 실행하지 않고 구조만 읽음] ## FILE: FAQ.pdf")
    got = route_documents({}, [spec_doc, bundle])
    assert got.strategy != "zip_project", got.strategy
    # 과제 자체의 첨부에 실린 표식은 여전히 라우팅 신호다.
    intro = _Doc("starter_notice.md", "[ZIP_PROJECT: 파일을 실행하지 않고 구조만 읽음]")
    got2 = route_documents({}, [_Doc("spec.md", "# Project\n\n구현 과제"), intro])
    assert got2.strategy == "zip_project", got2.strategy
    print("OK 컨텍스트 번들 ZIP 마커 격리")


def test_pipeline_exposes_route():
    import tempfile
    from until.config import Config
    from until.pipeline import run
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "assignment.Rmd"
        path.write_text("## 문제 1\n```{r}\n### Todo ###\n```", encoding="utf-8")
        result = run([str(path)], Config(backend="mock"))
    assert result.assignment_route.strategy == "rmd_notebook"
    assert result.spec["task_type"] == "code"
    print("OK pipeline Rmd 알고리즘 라우팅")


def _with_algo_version(version, fn):
    """UNTIL_ALGO_VERSION을 설정(None=해제)하고 fn 실행 — try/finally로 환경 복원."""
    prev = os.environ.get("UNTIL_ALGO_VERSION")
    try:
        if version is None:
            os.environ.pop("UNTIL_ALGO_VERSION", None)
        else:
            os.environ["UNTIL_ALGO_VERSION"] = version
        fn()
    finally:
        if prev is None:
            os.environ.pop("UNTIL_ALGO_VERSION", None)
        else:
            os.environ["UNTIL_ALGO_VERSION"] = prev


# 설계문서(COURSE_ALGORITHMS_2026F) §6 목표 17케이스.
# (kwargs, v0.1 기대, v0.2 기대, v0.2 stage). v0.1 기대값은 설계문서 §2가 아니라
# '현재 코드를 v0.1로 실행해 얻은 실측 판정'이다(§2는 코드보다 낡았다).
_V02_TARGET_CASES = [
    # 교재문제과목 — 교재 문제 풀이(문항이 제출함 밖). v0.1은 묻기/번호형으로 샌다.
    ({"title": "중간과제 1", "course_name": "교재문제과목"},
     "spec_clarification", "textbook_problem_set", ""),
    ({"title": "중간과제 2", "course_name": "교재문제과목"},
     "spec_clarification", "textbook_problem_set", ""),
    ({"title": "과제 3", "course_name": "교재문제과목",
      "description": "교재 12장 연습문제를 풀어 제출하세요."},
     "distributed_spec", "textbook_problem_set", ""),
    # 논리설계실습 — HDL 실습. 과목명·본문·첨부의 툴체인 신호 + 회차형.
    ({"title": "실습 3 보고서", "course_name": "논리설계실습",
      "description": "Verilog 코드와 테스트벤치를 포함해 제출"},
     "distributed_spec", "hdl_lab", ""),
    ({"title": "실습 3", "course_name": "논리설계실습",
      "attachment_names": ["lab3.v"]},
     "distributed_spec", "hdl_lab", ""),
    ({"title": "FPGA & Implementation", "course_name": "논리설계실습"},
     "spec_clarification", "hdl_lab", ""),
    ({"title": "Lab 4 - Sequential Logic", "course_name": "논리설계실습"},
     "evidence_report", "hdl_lab", ""),
    ({"title": "실습 2 레포트", "course_name": "논리설계실습",
      "description": "Verilog 소스 zip과 보고서를 제출",
      "attachment_names": ["lab2_starter.zip"]},
     "zip_project", "hdl_lab", ""),
    # 실습 퀴즈는 응시물 — v0.2에서도 hdl_lab으로 새면 안 된다(§5 보호 확인).
    ({"title": "06.09 화 분반 실험 5 퀴즈", "course_name": "논리설계실습"},
     "non_actionable", "non_actionable", ""),
    # 프로그래밍과목 — '프로그래밍 과제'가 _CODE에 없어 묻기로 새던 실측.
    ({"title": "프로그래밍 과제 5", "course_name": "프로그래밍과목"},
     "spec_clarification", "code_project", ""),
    ({"title": "Assignment #3", "course_name": "프로그래밍과목",
      "attachment_names": ["skeleton.zip"]},
     "zip_project", "zip_project", ""),
    # 활동보고과목 — '활동보고서'가 _REPORT(보고서)에 먼저 걸리던 실측.
    ({"title": "활동보고서 제출", "course_name": "활동보고과목"},
     "evidence_report", "activity_form", ""),
    ({"title": "활동 보고서", "course_name": "활동보고과목"},
     "evidence_report", "activity_form", ""),
    # 실험과목 — 3단 사이클, stage로 단계 구분.
    ({"title": "예비보고서 3주차", "course_name": "실험과목"},
     "evidence_report", "lab_report_cycle", "pre"),
    ({"title": "랩노트 제출", "course_name": "실험과목"},
     "spec_clarification", "lab_report_cycle", "notebook"),
    ({"title": "실험 4 결과보고서", "course_name": "실험과목"},
     "evidence_report", "lab_report_cycle", "result"),
    # 세미나과목 — 이미 정확한 판정, 양쪽 버전 유지.
    ({"title": "3주차 소감문", "course_name": "세미나과목"},
     "reflective_series", "reflective_series", ""),
]

# 설계문서 §6 회귀 28케이스 — 기존 코드 주석의 실코퍼스 유형 전수. 기대값은
# 현재 코드를 v0.1로 실행한 실측 판정이며, v0.1·v0.2 양쪽에서 동일해야 한다.
_V02_REGRESSION_CASES = [
    # 성적 항목 3
    ({"title": "M1"}, "non_actionable"),
    ({"title": "중간 총점"}, "non_actionable"),
    ({"title": "출석"}, "non_actionable"),
    # 시험 슬롯 3(단독 중간/기말·영어 문항 슬롯)
    ({"title": "중간고사"}, "non_actionable"),
    ({"title": "기말"}, "non_actionable"),
    ({"title": "Final problem 1"}, "non_actionable"),
    # 외부 시스템 1
    ({"title": "UNIMe 13주차"}, "non_actionable"),
    # 응시형 퀴즈 2 — '실험 N 퀴즈'가 hdl_lab·evidence_report로 새면 안 된다.
    ({"title": "06.02 화 분반 실험 6 퀴즈"}, "non_actionable"),
    ({"title": "Quiz, 연구실책임자용"}, "non_actionable"),
    # 본인 서류·증빙 2
    ({"title": "소자 구매 내역 제출"}, "personal_upload"),
    ({"title": "프로젝트 결석 증빙자료"}, "personal_upload"),
    # 주차별 질의·발표·팀·양식 4
    ({"title": "5주차 질의"}, "weekly_inquiry"),
    ({"title": "피피티 제출"}, "presentation_conversion"),
    ({"title": "서비스디자인 팀과제 제출"}, "team_project"),
    ({"title": "3/17 조별활동 보고서"}, "activity_form"),
    ({"title": "회의록 제출"}, "activity_form"),
    # 통계학실험 Rmd 시리즈 3 + Rmd 첨부 1
    ({"title": "과제 1", "course_name": "통계학실험"}, "rmd_notebook"),
    ({"title": "중간고사 제출 연습", "course_name": "통계학실험"}, "rmd_notebook"),
    ({"title": "기말고사", "course_name": "통계학실험"}, "rmd_notebook"),
    ({"title": "과제 1", "attachment_names": ["assignment1.Rmd"]}, "rmd_notebook"),
    # zip 프로젝트·problem set — 'problem set' 계열은 v0.1부터 problem_set이며
    # textbook_problem_set이 뺏으면 안 된다(_PROBLEM_SET 제외 조건).
    ({"title": "Project", "attachment_names": ["starter.zip"]}, "zip_project"),
    ({"title": "2025 예비대학 problem set_Elements, Compounds, Nomenclature"},
     "problem_set"),
    # 감상문(소감·성찰 계열)·개인과제 서론·번호형 HW
    ({"title": "농구 감상문"}, "reflective_series"),
    ({"title": "개인과제 서론 제출"}, "staged_writing"),
    ({"title": "HW4"}, "distributed_spec"),
    # 다른 실습 과목 보호 — HDL 신호가 없으면 hdl_lab이 삼키지 않는다.
    ({"title": "실습 1 보고서", "course_name": "기초회로"}, "distributed_spec"),
    # 일반 실험 보고서 보호 — lab_report_cycle은 단계명에만 반응한다.
    ({"title": "실험 3 보고서", "course_name": "생물학실험"}, "evidence_report"),
    ({"title": "자기소개서"}, "staged_writing"),
]


def test_v02_target_cases_both_versions():
    # §6 목표 17케이스: v0.2에서만 신설 판정이 켜지고, 기본(미설정)·명시 v0.1은
    # 현행 판정을 바이트 단위로 유지한다(stage도 v0.1에서는 항상 빈 문자열).
    def check(column, expect_stage):
        for kwargs, v01, v02, stage in _V02_TARGET_CASES:
            expected = v01 if column == "v01" else v02
            got = route_assignment(**kwargs)
            assert got.strategy == expected, (column, kwargs, got)
            assert got.stage == (stage if expect_stage else ""), (column, kwargs, got)

    _with_algo_version(None, lambda: check("v01", False))
    _with_algo_version("v0.1", lambda: check("v01", False))
    _with_algo_version("v0.2", lambda: check("v02", True))
    print("OK v0.2 목표 17케이스 — v0.2 신설 판정 + 기본/v0.1 현행 유지")


def test_v02_regression_cases_both_versions():
    # §6 회귀 28케이스: 기본(미설정)·v0.1·v0.2 세 환경 전부에서 현재 v0.1 실측
    # 판정과 동일해야 한다(신설 규칙·패턴 확장이 기존 판정을 뺏으면 안 된다).
    def check():
        for kwargs, expected in _V02_REGRESSION_CASES:
            got = route_assignment(**kwargs)
            assert got.strategy == expected, (kwargs, got)
            assert got.stage == "", (kwargs, got)
            if expected in ("non_actionable", "personal_upload"):
                assert not got.actionable, (kwargs, got)
            else:
                assert got.actionable and got.required_evidence, (kwargs, got)

    for version in (None, "v0.1", "v0.2"):
        _with_algo_version(version, check)
    print("OK v0.2 회귀 28케이스 — 세 환경(기본·v0.1·v0.2) 전부 현행 판정 유지")


def test_midterm_task_does_not_collide_with_exam_only():
    # _EXAM_ONLY는 단독 '중간'/'기말'(+숫자)을 fullmatch하지만 '중간과제 1'은
    # '과제'가 붙어 매치되지 않는다 — 신설 _MIDTERM_TASK와 충돌 없음을 못 박는다.
    def check_exams():
        for t in ("중간", "기말", "중간 2"):
            got = route_assignment(title=t)
            assert got.strategy == "non_actionable" and not got.actionable, (t, got)

    def check_v01():
        check_exams()
        assert route_assignment(title="중간과제 1").strategy == "spec_clarification"

    def check_v02():
        check_exams()  # 시험 표시용 항목은 v0.2에서도 그대로 제외된다.
        assert route_assignment(title="중간과제 1").strategy == "textbook_problem_set"

    _with_algo_version("v0.1", check_v01)
    _with_algo_version("v0.2", check_v02)
    print("OK '중간과제 N' — _EXAM_ONLY 충돌 없음(양쪽 버전)")


def test_route_for_strategy_factory():
    # 정본 팩토리: course_profiles 폴백이 힌트 문자열을 라우트로 바꿀 때 쓴다.
    hdl = route_for_strategy("hdl_lab")
    assert hdl is not None and hdl.strategy == "hdl_lab" and hdl.stage == ""
    assert hdl.required_evidence and hdl.questions and hdl.actionable
    for stage in ("pre", "notebook", "result"):
        got = route_for_strategy("lab_report_cycle", stage)
        assert got is not None and got.strategy == "lab_report_cycle", (stage, got)
        assert got.stage == stage and got.required_evidence, (stage, got)
    tb = route_for_strategy("textbook_problem_set")
    assert tb is not None and tb.strategy == "textbook_problem_set" and tb.stage == ""
    # 단계 미상 lab_report_cycle·모르는 strategy는 None — 호출자가 먼저 판정한다.
    assert route_for_strategy("lab_report_cycle") is None
    assert route_for_strategy("lab_report_cycle", "final") is None
    assert route_for_strategy("evidence_report") is None
    assert route_for_strategy("") is None

    # 감지 규칙이 돌려주는 라우트와 정본이 완전히 같아야 한다(정의 이원화 방지).
    def check_same():
        assert route_assignment(title="예비보고서 3주차") == route_for_strategy(
            "lab_report_cycle", "pre")
        assert route_assignment(title="중간과제 1") == route_for_strategy(
            "textbook_problem_set")

    _with_algo_version("v0.2", check_same)
    print("OK route_for_strategy 정본 팩토리")


def test_directive_stage_display():
    # 빈 stage면 현행 출력과 바이트 단위로 동일해야 한다('단계' 줄 자체가 없음).
    text = assignment_route_directive(route_assignment(title="관찰 실험 보고서"))
    assert "- 단계:" not in text
    expected = (
        "[과제 처리 경로: evidence_report]\n"
        "- 판정 근거: 실험·실습·조사 보고서\n"
        "- 먼저 확보할 근거: 실습 지시서 · 실측 결과·사진 · 보고서 형식\n"
        "- 확보되지 않은 근거의 사실·결과·팀 합의를 지어내지 말고 해당 질문을 DECISION으로 남긴다."
        "\n- 필요한 확인 질문:\n  - 직접 얻은 결과·관찰·오류가 무엇인가요?")
    assert text == expected, text
    # stage가 있으면(=lab_report_cycle) 판정 근거 다음 줄에 표시된다.
    staged = assignment_route_directive(route_for_strategy("lab_report_cycle", "result"))
    assert "- 판정 근거: " in staged and "\n- 단계: result\n" in staged
    print("OK directive stage 표시(빈 stage는 현행 출력 동일)")


if __name__ == "__main__":
    test_explicit_routes_and_safe_fallback()
    test_non_actionable_grade_rows()
    test_grade_notice_and_personal_upload_excluded()
    test_quiz_slots_excluded()
    test_induced_routes_from_jihu_corpus()
    test_induced_routes_from_jaewon_corpus()
    test_v02_adversarial_fixes()
    test_context_bundle_zip_marker_does_not_flip_route()
    test_pipeline_exposes_route()
    test_v02_target_cases_both_versions()
    test_v02_regression_cases_both_versions()
    test_midterm_task_does_not_collide_with_exam_only()
    test_route_for_strategy_factory()
    test_directive_stage_display()
    print("\nASSIGNMENT ROUTER TESTS PASS")
