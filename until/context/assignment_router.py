"""과제 목록을 명시적 처리 알고리즘으로 라우팅한다(결정적·LLM 0)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable

from ..config import algo_version


@dataclass(frozen=True)
class AssignmentRoute:
    strategy: str
    reason: str
    required_evidence: tuple[str, ...]
    questions: tuple[str, ...] = ()
    actionable: bool = True
    # lab_report_cycle(v0.2)에서만 비어 있지 않다: "pre" | "notebook" | "result".
    # 기본값 있는 마지막 필드 — 기존 위치 인자 호출과 하위호환.
    stage: str = ""


_GRADE_ONLY = re.compile(
    r"^(?:M\d+|F\d+|(?:중간|기말)\s*\d*\s*(?:총점|점수)|결석\s*횟수|출석\s*점수|"
    r"프로젝트\s*점수|(?:프로젝트\s*)?(?:최종\s*)?환산\s*점수|태도|출석|\d+주차\s*출석)$", re.I)
# 시험 표시·답안 슬롯 — 단독 '중간/기말'(실코퍼스: 물리2 점수 확인 창)과 영어
# 시험 문항 슬롯('midterm problem 5', 'Final problem 1')까지. 응시 산출물이라
# 초안 생성 대상이 아니다.
_EXAM_ONLY = re.compile(
    r"^(?:(?:중간|기말)?\s*(?:고사|시험)|중간|기말|"
    r"(?:midterm|final)(?:\s*exam)?(?:\s*problem)?)(?:\s*\d+)?$", re.I)
# 외부 시스템 응시 항목(실코퍼스: 수학연습 UNIMe 주차별) — 제출물이 eTL에 없다.
_EXTERNAL_SYSTEM = re.compile(r"^(?:unime|유니미)", re.I)
# 번호형 문제 풀이 세트(실코퍼스: 화학 'problem set_...', 신호및시스템
# 'Chapter 1,2,3 과제 안내' — 교재 챕터 문제 풀이).
_PROBLEM_SET = re.compile(r"problem\s*sets?|문제\s*(?:풀이\s*)?세트|"
                          r"^chapter[\d\s,.]+과제", re.I)
# 번호형 제출함의 괄호 접두어('[공통과제]HW1')·마감 괄호 접미어('숙제5 (12/15
# 까지)')·문항 슬롯('숙제5(3)')은 fullmatch를 막는다 — 벗겨서 판정.
_TITLE_PREFIX = re.compile(r"^\[[^\]]{1,15}\]\s*")
_TITLE_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")
# 현장·온라인 응시형 퀴즈 슬롯(실코퍼스: 수학연습 퀴즈N·기초회로 '실험 N 퀴즈'·
# 안전환경교육 Quiz) — '실험'이 evidence_report에 먼저 걸리므로 이 규칙이 앞서야
# 한다. 퀴즈를 '만드는' 과제는 응시가 아니라 산출물이므로 제외하지 않는다.
_QUIZ = re.compile(r"퀴즈|quiz", re.I)
_QUIZ_AUTHORING = re.compile(r"제작|만들|출제", re.I)
# 본인 서류·증빙 업로드 슬롯(구매 영수증·결석 증빙·수료증 등) — AI가 대신 쓸 수
# 있는 산출물이 아니라 학생 본인의 실물 서류다(실코퍼스: 기초회로 2건).
_PERSONAL_DOC = re.compile(
    r"증빙|증명서|수료증|(?:구매|결제|영수)\s*내역|영수증|진단서|결석계", re.I)
# 실물 인증 사진 제출(실코퍼스: 탁구 '조별 티타임 인증샷') — 본문에서만 나와도
# AI가 대신 만들 수 없는 실물 증빙이다. '인증샷'은 이 용도 외 쓰임이 드물다.
_PHOTO_PROOF = re.compile(r"인증샷|인증\s*사진", re.I)
_INQUIRY = re.compile(r"(?:\d+\s*주차.*질의|질문\s*제출)", re.I)
_PRESENT = re.compile(r"피피티|ppt|presentation|speech|발표|outline", re.I)
_FORM = re.compile(r"조별\s*활동.*보고|일지|회의록", re.I)
_TEAM = re.compile(r"팀\s*과제|팀\s*프로젝트|team\s*(?:project|assignment)", re.I)
_REFLECT = re.compile(
    r"소감문|성찰|reflection|강의\s*후기|self[-\s]?evaluation|자기\s*평가|"
    r"감상문|독후감", re.I)
_DISTRIBUTED = re.compile(
    r"^(?:(?:숙제|과제|assignment|homework|hw)\s*#?\s*\d+(?:\s*제출)?|"
    r"실습\s*\d+\s*(?:레포트|보고서)?)$", re.I)
_CODE = re.compile(r"코드|프로그램|project|assignment\s*#|구현|코딩|\bcode\b|"
                   r"(?<![a-z])lab\s*\d*\s*homework", re.I)
_REPORT = re.compile(r"레포트|보고서|실험|실습|\blab\b|laboratory", re.I)
_WRITING = re.compile(
    r"글쓰기|서론|개요|요약문|에세이|도시|개인과제|자기\s*소개서?|"
    r"\bessay\b|\bwrite\b|\bwriting\b", re.I)

# ── v0.2 신설 규칙(COURSE_ALGORITHMS_2026F §4) ─────────────────────────────
# 2026-2학기 6과목 실측(설계문서 §2)에서 4과목이 현행 라우팅을 벗어나던 것을
# 메운다. 전부 algo_version() == "v0.2"에서만 발동한다 — v0.1은 바이트 단위 동일.
# HDL 실습(논리설계실습·반도체): 툴체인 어휘를 과목명까지 보는 이유 — 첫 주차처럼
# 본문·첨부가 비면 본문 어휘 감지가 무력하다(§3 실측). 남는 구멍(축약 과목명 +
# 빈 본문)은 course_profiles 폴백(§3) 몫이다.
_HDL = re.compile(
    r"verilog|vhdl|systemverilog|\bfpga\b|vivado|quartus|테스트\s*벤치|"
    r"testbench|\bxdc\b|\brtl\b|논리\s*설계|\bhdl\b|합성\s*결과|넷리스트", re.I)
_HDL_SUFFIX = {".v", ".sv", ".vhd", ".vhdl", ".xdc", ".qsf", ".bit"}
_LAB_ROUND = re.compile(r"^(?:실습|실험|lab)\s*#?\s*\d+", re.I)
# 실험 3단 사이클(실험과목, §4.2): 단계명은 '제목'에서만 본다 — 결과보고서 본문이
# "예비보고서를 바탕으로"를 언급하는 게 정상이라, 본문까지 보면 단계가 뒤집힌다.
_LAB_PRE = re.compile(r"예비\s*(?:보고서|레포트)|pre-?lab|사전\s*보고서", re.I)
_LAB_NOTE = re.compile(r"랩\s*노트|실험\s*노트|lab\s*note(?:book)?|노트\s*제출", re.I)
_LAB_RESULT = re.compile(r"결과\s*(?:보고서|레포트)|result\s*report|본\s*보고서", re.I)
# 교재 문제 풀이(교재문제과목, §4.3): 문항이 교재에 있어 eTL이 못 가져온다 — 기존
# problem_set('문항 본문·데이터가 제출함 안에 있음' 가정)과 다른 경로가 필요하다.
# '중간과제 1'은 _EXAM_ONLY(단독 '중간'/'기말' fullmatch)에 걸리지 않는다 —
# _EXAM_ONLY는 '중간' 뒤에 숫자만 허용하므로 '과제'가 붙으면 매치 실패(충돌 없음).
_TEXTBOOK = re.compile(r"교재|textbook|\d+\s*장\s*(?:문제|연습)|chapter\s*\d+", re.I)
_MIDTERM_TASK = re.compile(r"^(?:중간|기말)\s*과제\s*\d*$")
# 기존 규칙 확장 — v0.2 전용 '별도 패턴'으로 둔다(v0.1 불변 보장).
# 프로그래밍과목 '프로그래밍 과제 5'가 _CODE에 없어 spec_clarification으로 새던 실측(§2).
_CODE_V2 = re.compile(r"프로그래밍\s*(?:과제|숙제)", re.I)
# 활동보고과목 '활동보고서 제출'이 _REPORT(보고서)에 먼저 걸리던 실측(§2). 단
# '실험 활동 보고서'류는 evidence_report로 남긴다(§4.5 회귀 방지, 실측 확인).
_FORM_V2 = re.compile(r"활동\s*보고서?", re.I)
_FORM_EXCLUDE = re.compile(r"실험|실습|\blab\b", re.I)
# HDL 신호가 제목에 있어도 산출물이 아닌 행정 항목(§4.1.5 체크리스트 대상)은
# 배제 — 적대적 회귀에서 'FPGA 보드 대여 신청'·'Verilog 설치 안내'가 hdl_lab으로
# 새던 실측. '안내'는 실과제 제목('Chapter 과제 안내')에도 흔해 넣지 않는다.
_HDL_ADMIN = re.compile(r"설치|대여|신청|배부", re.I)


def _hdl_lab_route() -> AssignmentRoute:
    # §4.1: evidence_report(사진 증빙·방법→결과→고찰 골격)도 code_project(보고서
    # 파트 소실)도 맞지 않는 혼합 산출물. 파형·합성 수치·보드 동작은 결정이 아니라
    # 사실이다 — 없으면 지어내지 말고 빈칸 DECISION으로 남긴다.
    return AssignmentRoute(
        "hdl_lab", "HDL 실습 — RTL·테스트벤치·파형 증빙·설계 근거 고찰의 혼합 산출물",
        ("실습 지시서(사전 설계 요구)", "진리표·상태도·K-map",
         "Verilog/VHDL 소스·테스트벤치", "시뮬레이션 파형·보드 동작 캡처",
         "합성 리포트(LUT/FF·타이밍)", "보고서 양식"),
        ("시뮬레이션 파형이나 보드 동작 캡처를 확보했나요?",
         "합성 결과 수치(LUT/FF 사용량, 최대 주파수)는 무엇인가요?",
         "이 설계를 택한 근거(인코딩·상태기계 방식)는 무엇인가요?"))


def _lab_cycle_route(stage: str) -> AssignmentRoute:
    # §4.2: 세 단계가 같은 strategy를 공유하되 stage로 구분한다 — 단계마다 가능한
    # 것과 금지된 것이 정반대다(예비=실측값 서술 금지 / 랩노트=기록 템플릿만,
    # 내용 대필 금지 / 결과=랩노트 실측 없이 수치·그래프 생성 금지).
    if stage == "pre":
        return AssignmentRoute(
            "lab_report_cycle", "실험 3단 사이클 — 예비 단계(아직 실험 전: 실측값 서술 금지)",
            ("실험 교재 해당 실험", "이론·원리", "시약·기구·안전(MSDS)", "절차 요약"),
            ("이번 실험의 교재 회차·실험 번호가 무엇인가요?",),
            stage="pre")
    if stage == "notebook":
        return AssignmentRoute(
            "lab_report_cycle", "실험 3단 사이클 — 랩노트 단계(현장 기록물: 기록 템플릿까지만 생성)",
            ("예비보고서", "측정 항목·단위·유효숫자", "실험 중 기록"),
            ("측정할 항목과 단위를 미리 확정할까요?(기록 템플릿만 생성)",),
            stage="notebook")
    return AssignmentRoute(
        "lab_report_cycle", "실험 3단 사이클 — 결과 단계(랩노트 실측 없이 수치 생성 금지)",
        ("예비보고서", "랩노트 실측값", "오차·불확도", "보고서 형식"),
        ("랩노트에 기록한 실측값과 관찰·오류가 무엇인가요?",),
        stage="result")


def _textbook_problem_set_route() -> AssignmentRoute:
    # §4.3: 문항 본문 없음(기본)이면 학습 보조 모드(교재·문항 확정 질문, 공식 정리,
    # 유사 예제 시연)까지만. 문항 본문(사진·스캔) 확보 시에만 problemset 골격 풀이.
    # "아마 이런 문제일 것"이라며 문항을 지어내지 않는다(하드 금지).
    return AssignmentRoute(
        "textbook_problem_set", "교재 문제 풀이 — 문항 본문이 제출함 밖(교재)에 있음",
        ("교재명·장·문항 번호", "문항 본문(사진·스캔)", "해당 장 공식·정의",
         "풀이 표기·수기 제출 규정"),
        ("어느 교재 몇 장 몇 번 문제인가요?",
         "문항 본문 사진·스캔을 올려줄 수 있나요? 없으면 공식 정리와 유사 예제 시연까지만 진행합니다."))


def route_assignment(*, title: str, description: str = "",
                     attachment_names: Iterable[str] = (), course_name: str = "") -> AssignmentRoute:
    """제목·본문·첨부명만으로 실행 경로와 부족 정보 질문을 정한다."""
    title = " ".join((title or "").split())
    description = " ".join((description or "").split())
    names = tuple(str(x) for x in attachment_names or ())
    suffixes = {Path(n).suffix.lower() for n in names}
    joined = f"{title}\n{description}\n{' '.join(names)}"
    # v0.2 게이트(설계문서 §8 동결 규율): 신설 규칙·패턴 확장은 이 플래그가 참일
    # 때만 검사한다. 기본(v0.1)은 아래 어느 분기도 추가로 타지 않는다.
    v2 = algo_version() == "v0.2"

    # 통계학실험 실코퍼스: 과제·중간·기말은 교수가 준 Rmd를 채워 HTML로 내는
    # 한 시리즈다. '제출 연습'처럼 현재 첨부가 비어도 과목 내 산출물 계열은 같다.
    course_context = f"{course_name}\n{description}"
    stats_rmd = ("통계학실험" in course_context
                 and re.search(r"^(?:과제\s*\d+|중간고사(?:\s*제출\s*연습)?|기말고사)$",
                               title, re.I))
    if stats_rmd:
        return AssignmentRoute(
            "rmd_notebook", "통계학실험의 반복 R Markdown/HTML 제출 계열",
            ("해당 회차 Rmd", "데이터 파일", "실행 환경·패키지"),
            ("현재 회차 Rmd가 보이지 않으면 강의자료·공지에서 템플릿을 확인해주세요.",))

    if _GRADE_ONLY.fullmatch(title):
        return AssignmentRoute("non_actionable", "성적·출석 표시용 항목",
                               (), actionable=False)
    if _EXAM_ONLY.fullmatch(title) and not suffixes.intersection({".rmd", ".pdf", ".docx"}):
        return AssignmentRoute("non_actionable", "별도 명세 없는 시험 표시용 항목",
                               (), actionable=False)
    if (_QUIZ.search(title) and not _QUIZ_AUTHORING.search(title)
            and not suffixes.intersection({".rmd", ".pdf", ".docx"})):
        return AssignmentRoute(
            "non_actionable", "현장·온라인 응시형 퀴즈 항목(초안 생성 대상 아님)",
            (), actionable=False)
    if _EXTERNAL_SYSTEM.search(title):
        return AssignmentRoute(
            "non_actionable", "외부 시스템 응시 항목(제출물이 eTL에 없음)",
            (), actionable=False)
    if _PERSONAL_DOC.search(title) or _PHOTO_PROOF.search(joined):
        return AssignmentRoute(
            "personal_upload", "본인 서류·증빙 업로드 항목(초안 생성 대상 아님)",
            (), actionable=False)
    # ── v0.2 신설 구간(설계문서 §5) — 왜 이 위치인가 ──────────────────────
    # 제외 판정(_GRADE_ONLY·_EXAM_ONLY·_QUIZ·_EXTERNAL_SYSTEM·_PERSONAL_DOC/
    # _PHOTO_PROOF) '뒤': 퀴즈·성적 항목이 실습·실험 어휘를 품고 있어서, 앞에
    # 두면 '실험 5 퀴즈'(응시물)가 hdl_lab으로 새어 응시물에 초안을 만들게 된다.
    # _INQUIRY '앞': 신설 3규칙의 신호(과목 툴체인·실험 단계명·교재 참조)는
    # _DISTRIBUTED(번호형)·_REPORT(보고서)·_CODE보다 강하므로 그보다 앞서야 한다.
    if v2:
        # §4.1 hdl_lab — 과목명/본문/첨부의 HDL 신호 + 회차형 제목(또는 본문·첨부
        # 자체 신호). '기초회로 실습 1 보고서'는 HDL 신호가 없어 삼키지 않는다(§6).
        if (not _HDL_ADMIN.search(title)
                and (_HDL.search(f"{course_name}\n{joined}") or suffixes & _HDL_SUFFIX)
                and (_LAB_ROUND.match(title) or _HDL.search(joined)
                     or suffixes & _HDL_SUFFIX)):
            return _hdl_lab_route()
        # §4.2 lab_report_cycle — 단계명(예비/랩노트/결과)에만 반응하므로
        # '생물학실험 실험 3 보고서' 같은 일반 실험 보고서를 뺏지 않는다(§6).
        for stage, pattern in (("pre", _LAB_PRE), ("notebook", _LAB_NOTE),
                               ("result", _LAB_RESULT)):
            if pattern.search(title):
                return _lab_cycle_route(stage)
        # §4.3 textbook_problem_set — 'problem set' 계열(_PROBLEM_SET)은 문항이
        # 제출함 안에 있는 기존 problem_set 경로에 남긴다.
        if _MIDTERM_TASK.match(title) or (
                _TEXTBOOK.search(joined) and not _PROBLEM_SET.search(joined)
                and re.search(r"과제|문제|숙제", title)):
            return _textbook_problem_set_route()
    if _INQUIRY.search(title):
        return AssignmentRoute(
            "weekly_inquiry", "주차별 사전 질의",
            ("질의 순번 공지", "담당 교수·강연 주제", "프로필 학번"),
            ("질의 순번표에서 본인 학번을 찾을 수 있나요?",))
    if _PRESENT.search(joined):
        return AssignmentRoute(
            "presentation_conversion", "발표·슬라이드·스피치 산출물",
            ("선행 글·개요", "발표 범위", "시간·형식 조건"),
            ("발표 범위와 시간을 확인할 수 없으면 알려주세요.",))
    if _TEAM.search(joined):
        return AssignmentRoute(
            "team_project", "팀 합의와 역할 분담이 필요한 공동 산출물",
            ("최종 산출물 형식", "팀 합의본·공유 파일", "본인 담당 범위", "마감·평가 기준"),
            ("팀이 합의한 방향과 본인 담당 부분은 무엇인가요?",
             "다른 팀원의 미완성 부분은 대신 작성하지 않고 빈칸으로 둘까요?"))
    # v0.2 확장(§4.5): '활동 보고서'류 추가(_FORM_V2) — 단 실험·실습·lab 문맥이면
    # evidence_report로 남긴다(_FORM_EXCLUDE, '실험 활동 보고서' 회귀 방지 실측).
    # 제외는 v2 '확장분'에만 건다 — 결합 전체에 걸면 v0.1이 잡던 '실습일지'가
    # v0.2에서 evidence_report로 역행한다(적대적 회귀 실측). v0.1 동작 그대로.
    if _FORM.search(joined) or (
            v2 and _FORM_V2.search(joined) and not _FORM_EXCLUDE.search(joined)):
        return AssignmentRoute(
            "activity_form", "실제 활동 사실을 양식에 기록",
            ("원본 양식", "참여자·활동·결과", "사진 요구 여부"),
            ("실제로 누가 무엇을 했고 결과가 어땠나요?",))
    if ".rmd" in suffixes:
        return AssignmentRoute(
            "rmd_notebook", "R Markdown 답안 슬롯형 과제",
            ("원본 Rmd", "데이터 파일", "실행 환경·패키지"),
            ("데이터 파일이나 실행 결과가 없으면 해당 값만 실행 후 채워주세요.",))
    if ".zip" in suffixes:
        return AssignmentRoute(
            "zip_project", "ZIP 안 명세·스켈레톤·코드 프로젝트",
            ("README/PDF 명세", "제공 코드", "테스트·입출력 계약"),
            ("실행 환경과 금지 라이브러리가 별도로 있나요?",))
    if _REFLECT.search(title):
        return AssignmentRoute(
            "reflective_series", "반복형 강의 소감",
            ("해당 주차 강의 메모·자료", "본인 인상·적용 계획"),
            ("인상 깊었던 대목과 본인 경험 연결점은 무엇인가요?",))
    bare_title = _TITLE_SUFFIX.sub("", _TITLE_PREFIX.sub("", title))
    if _DISTRIBUTED.fullmatch(bare_title) and len(description) < 900:
        return AssignmentRoute(
            "distributed_spec", "제출함 밖에 명세가 있는 번호형 과제",
            ("같은 번호 강의자료", "모듈", "코딩·토론 게시판"),
            ("연결된 명세가 여러 개면 어느 회차인지 확인해주세요.",))
    if _PROBLEM_SET.search(title):
        return AssignmentRoute(
            "problem_set", "번호형 문제 풀이 세트",
            ("문항 본문·데이터", "풀이 과정 표기 규정", "제출 형식"),
            ("자필·스캔 제출 규정이 있으면 알려주세요.",))
    # v0.2 확장(§4.4): '프로그래밍 과제/숙제'(_CODE_V2) — '프로그래밍'은
    # '프로그램'의 부분문자열이 아니라 _CODE에 안 걸려 묻기로 새던 실측(§2).
    if (_CODE.search(joined) or suffixes.intersection({".py", ".ino", ".c", ".cpp", ".java"})
            or (v2 and _CODE_V2.search(joined))):
        return AssignmentRoute(
            "code_project", "코드 구현 산출물",
            ("입출력·제약", "제공 코드", "테스트 기준"),
            ("실행 환경과 제출 파일 구조가 명세에 없으면 알려주세요.",))
    if _REPORT.search(joined):
        return AssignmentRoute(
            "evidence_report", "실험·실습·조사 보고서",
            ("실습 지시서", "실측 결과·사진", "보고서 형식"),
            ("직접 얻은 결과·관찰·오류가 무엇인가요?",))
    if _WRITING.search(joined):
        return AssignmentRoute(
            "staged_writing", "글쓰기 단계 또는 장문 과제",
            ("현재 단계 지시", "직전 단계 제출물", "피드백"),
            ("주제·관점이 정해지지 않았다면 후보 중 직접 선택해주세요.",))
    # 정보가 거의 없는 이름도 버리지 않는다. 초안을 지어내지 않고, 먼저 실제
    # 명세 위치와 산출물 형식을 묻는 것이 하나의 명시적인 안전 처리 경로다.
    return AssignmentRoute(
        "spec_clarification", "현재 메타데이터만으로 산출물 형식을 확정할 수 없음",
        ("과제 본문", "첨부", "공지·모듈", "제출 형식"),
        ("무엇을 제출해야 하는지 적힌 본문·첨부·공지 위치를 확인해주세요.",))


def is_context_bundle_doc(doc) -> bool:
    """eTL 컨텍스트 번들(참고자료 묶음) 문서인지 — 헤더·파일명 기준(결정적).

    번들에는 강의자료 발췌가 인용되므로 파서 표식([ZIP_PROJECT: ...] 등)이 그대로
    실려 온다. 이를 과제 자체의 첨부로 오인하면 안 된다."""
    text = str(getattr(doc, "text", "") or "")
    if text.lstrip().startswith("# eTL 과목 컨텍스트"):
        return True
    return Path(str(getattr(doc, "source", "") or "")).name == "context.md"


def route_documents(spec: dict, documents: Iterable[object]) -> AssignmentRoute:
    docs = list(documents or [])
    names = [Path(str(getattr(d, "source", ""))).name for d in docs]
    text = "\n".join(str(getattr(d, "text", "")) for d in docs)
    # 첫 문서는 과제 페이지(spec.md), 나머지는 첨부다. 번호형 제출함의 본문 길이
    # 판정에 첨부 PDF 전문까지 합치면 짧은 제출함이 갑자기 '명세 충분'으로 바뀐다.
    description = str(getattr(docs[0], "text", "")) if docs else ""
    # 구조화 추출 표식은 제목보다 강한 신호다.
    heading = next((line.lstrip("# ").strip() for line in text.splitlines()
                    if line.lstrip().startswith("#") and line.lstrip("# ").strip()), "")
    title = heading or str(spec.get("deliverable") or spec.get("goal") or "")
    # 컨텍스트 번들 속 발췌 표식은 라우팅 신호가 아니다 — 강의자료 zip 발췌의
    # ZIP_PROJECT 표식이 글쓰기 과제를 zip_project로 뒤집던 실코퍼스 회귀.
    marker_text = "\n".join(str(getattr(d, "text", "")) for d in docs
                            if not is_context_bundle_doc(d))
    if "[RMD_TEMPLATE:" in marker_text:
        names.append("assignment.Rmd")
    if "[ZIP_PROJECT:" in marker_text:
        names.append("starter.zip")
    return route_assignment(title=title, description=description, attachment_names=names)


def assignment_route_directive(route: AssignmentRoute) -> str:
    if not route.actionable:
        return f"[비과제 항목] 이 항목은 초안 생성 대상이 아니다 — {route.reason}."
    evidence = " · ".join(route.required_evidence)
    questions = "\n".join(f"  - {q}" for q in route.questions)
    return (
        f"[과제 처리 경로: {route.strategy}]\n"
        f"- 판정 근거: {route.reason}\n"
        # stage는 lab_report_cycle(v0.2)에서만 비어 있지 않다 — 빈 stage면
        # 아래 줄이 통째로 빠져 현행(v0.1) 출력과 바이트 단위로 동일하다.
        + (f"- 단계: {route.stage}\n" if route.stage else "")
        + f"- 먼저 확보할 근거: {evidence}\n"
        "- 확보되지 않은 근거의 사실·결과·팀 합의를 지어내지 말고 해당 질문을 DECISION으로 남긴다."
        + (f"\n- 필요한 확인 질문:\n{questions}" if questions else "")
    )


def route_for_strategy(strategy: str, stage: str = "") -> AssignmentRoute | None:
    """신설 3 strategy의 정본(canonical) AssignmentRoute를 돌려준다.

    course_profiles 폴백(설계문서 §3) 등 다른 모듈이 힌트 '문자열'을 라우트로
    바꿀 때 쓴다 — 감지 규칙과 같은 팩토리를 공유해 두 경로의 라우트 정의가
    어긋나지 않게 한다. lab_report_cycle은 유효한 stage("pre"|"notebook"|
    "result")가 있어야 한다 — 단계를 모르면 어느 하드 금지(실측값 서술·대필·
    수치 생성)를 걸지 정할 수 없으므로 None(호출자가 단계를 먼저 판정한다).
    모르는 strategy도 None."""
    if strategy == "hdl_lab":
        return _hdl_lab_route()
    if strategy == "lab_report_cycle":
        if stage in ("pre", "notebook", "result"):
            return _lab_cycle_route(stage)
        return None
    if strategy == "textbook_problem_set":
        return _textbook_problem_set_route()
    return None
