"""톤 레지스터 회귀 골든셋 — 대표 과제 18건(결정적 생성, 이진 픽스처 없음).

각 케이스는 실제 eTL 과제 제목 패턴을 본뜬 텍스트 하나다. 라우팅은 본문 첫
마크다운 헤딩을 제목으로 읽으므로(`assignment_router.route_documents`), 케이스마다
`# 제목`으로 시작해 어느 전략·레지스터로 떨어지는지를 통제한다.

`expect_register`는 단언이 아니라 **관측 기준선**이다 — 실제 확정값이 다르면
리포트가 불일치로 표시한다. 그게 톤 버그일 수도, 라우팅 버그일 수도 있어서
스크립트가 조용히 한쪽으로 판정하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass(frozen=True)
class ToneCase:
    key: str
    title: str
    text: str
    expect_register: str

    def write(self, folder: Path) -> Path:
        p = Path(folder) / f"{self.key}.txt"
        p.write_text(self.text, encoding="utf-8")
        return p


def _case(key: str, title: str, body: str, expect: str) -> ToneCase:
    return ToneCase(key=key, title=title, text=f"# {title}\n\n{body}\n",
                    expect_register=expect)


_CASES: List[ToneCase] = [
    # ── 수신자 없는 문어체 산문 ──────────────────────────────────────
    _case("essay_media", "매체 이론 에세이",
          "강의에서 다룬 두 매체 이론을 비교하고, 어느 쪽이 오늘날의 플랫폼 환경을 "
          "더 잘 설명하는지 자신의 견해를 논하시오. 1500자 이상 서술하시오.",
          "academic_prose"),
    _case("essay_city", "도시 공간 분석 에세이",
          "관찰한 도시 공간 한 곳을 골라 그 공간이 사용자의 행동을 어떻게 규정하는지 "
          "분석하시오. 근거를 들어 논하고 예상 반론도 다루시오. 2000자 내외.",
          "academic_prose"),
    _case("writing_stage2", "글쓰기 2단계 — 초고 작성",
          "1단계 개요를 바탕으로 초고를 작성하여 제출한다. 지난 단계 피드백을 반영할 것. "
          "분량 3000자 이상.",
          "academic_prose"),
    _case("submission_slot", "제출함",
          "여기에 파일을 올리세요.",
          "academic_prose"),

    # ── 실험·실습 보고서 ────────────────────────────────────────────
    _case("lab_physics", "일반물리학실험 3주차 결과 보고서",
          "측정한 데이터를 정리해 목적·방법·결과·고찰 순으로 결과 보고서를 작성한다. "
          "측정값과 오차 분석을 반드시 포함할 것.",
          "lab_report"),
    _case("lab_circuit", "전자회로 실습2 레포트",
          "실습에서 구성한 증폭 회로의 동작을 기술하고 측정 결과를 논의하시오. "
          "실험 방법과 결과를 표로 정리할 것.",
          "lab_report"),

    # ── 참가·활동 보고서, 소감 ──────────────────────────────────────
    _case("reflect_week3", "3주차 강의 소감문",
          "이번 주 강의를 듣고 새로 알게 된 점과 느낀 점을 자유롭게 작성하시오. "
          "분량 800자 내외.",
          "reflective"),
    _case("reflect_coweek", "제5회 CO-Week Academy 참가 결과 보고서",
          "수강한 강의별로 핵심 개념, 새로 알게 된 점, 실습 내용을 기술한다. "
          "분량 제한: 강의당 300자 내외.",
          "reflective"),
    _case("reflect_week4", "4주차 강의 감상문",
          "강의 내용 중 인상 깊었던 대목을 골라 감상을 적으시오.",
          "reflective"),

    # ── 교수에게 직접 가는 질의 ─────────────────────────────────────
    _case("inquiry_w5", "5주차 질의 제출",
          "다음 수업 연사에게 드릴 질문을 미리 제출한다. 질문은 2개 이상 작성할 것. "
          "질의 순번표에서 본인 담당 교수를 확인하시오.",
          "inquiry_to_professor"),
    _case("inquiry_w7", "7주차 사전 질의",
          "강연 전 궁금한 점을 제출한다. 강연자의 연구 분야와 연결되는 질문을 쓸 것.",
          "inquiry_to_professor"),

    # ── 행정 양식 ───────────────────────────────────────────────────
    _case("form_group", "조별 활동 보고서",
          "조별 활동 내용을 아래 양식에 맞춰 기록해 제출한다. "
          "| 항목 | 내용 |\n|---|---|\n| 활동일 |  |\n| 참여자 |  |\n| 활동 내용 |  |",
          "form_admin"),
    _case("form_minutes", "3주차 회의록",
          "회의 일시·참석자·논의 내용·결정 사항을 양식에 채워 제출한다.",
          "form_admin"),

    # ── 팀 커뮤니케이션 ─────────────────────────────────────────────
    _case("team_final", "팀 프로젝트 최종 산출물",
          "팀별로 합의한 최종 산출물을 제출한다. 본인 담당 범위를 명시할 것.",
          "team_coordination"),
    _case("team_roles", "team project 역할 분담서",
          "팀 과제의 역할 분담과 일정 계획을 정리해 제출한다.",
          "team_coordination"),

    # ── 발표 ────────────────────────────────────────────────────────
    _case("present_ppt", "기말 발표 자료(PPT) 초안",
          "발표 자료 초안을 작성한다. 발표 시간은 10분이며 슬라이드 8장 내외로 구성할 것.",
          "presentation_script"),
    _case("present_outline", "발표 개요 outline",
          "발표 순서와 각 파트에서 말할 요지를 정리한 개요를 제출한다.",
          "presentation_script"),

    # ── 기계 채점(수신자 없음) ──────────────────────────────────────
    _case("code_sort", "프로그래밍 과제 #3 — 정렬 알고리즘 구현",
          "제공된 스켈레톤의 TODO 자리에 정렬 함수를 구현하시오. "
          "함수 시그니처와 파일명을 변경하지 말 것.",
          "technical_neutral"),
    _case("pset4", "Problem Set 4",
          "교재 5장 연습문제 1~6번을 풀어 제출하시오. 풀이 과정을 모두 쓸 것.",
          "technical_neutral"),
]


def tone_cases(keys: List[str] | None = None) -> List[ToneCase]:
    """골든셋 반환. keys를 주면 그 케이스만(순서는 정의 순서 유지)."""
    if not keys:
        return list(_CASES)
    wanted = set(keys)
    return [c for c in _CASES if c.key in wanted]


def case_keys() -> List[str]:
    return [c.key for c in _CASES]
