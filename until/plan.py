"""체크포인트 플랜 — 볼륨 있는 과제를 마감 역산 단계로 나눈다(결정적·LLM 0).

팀 피드백(2026-07-24): "딸깍 보고서 말고, 볼륨 있는 과제에 체크포인트를 확실하게."
경계선 철학의 시간축 확장 — 각 체크포인트는 「until이 준비해주는 것 + 학생이
정할 것 + 통과 조건」의 묶음이다. 날짜만 뽑는 도서관 Assignment Calculator류와
달리 각 단계가 until의 실제 기능(자료 수집·초안·finalize·준비 점검)에 연결된다.

볼륨 게이트: '분량이 큰 과제'(자≥2000 등)에만 플랜 생성 — 에세이 같은 간단한
과제는 마감이 멀어도 None을 돌려 UI가 아무것도 띄우지 않는다(간단함 유지).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import List, Optional


@dataclass
class Checkpoint:
    no: int
    due: Optional[date]     # None이면 날짜 없는 단계형(마감 미상)
    title: str
    until_does: str         # until이 준비해주는 것
    you_do: str             # 학생이 정할 것(경계선 위 몫)
    done_when: str          # 통과 조건

    def date_label(self) -> str:
        return self.due.isoformat() if self.due else "날짜 자유"


@dataclass
class CheckpointPlan:
    checkpoints: List[Checkpoint] = field(default_factory=list)
    basis: str = ""         # 산출 근거 한 줄(마감·분량·유형)


# 분량이 '볼륨 과제'로 간주되는 하한(단위별) — length_target.unit 기준.
_VOLUME_MIN = {"자": 2000, "단어": 1000, "페이지": 3, "매": 10}


def _is_volume(days: Optional[int], length_target) -> bool:
    """볼륨 게이트 — '분량이 큰 과제'만(자≥2000·단어≥1000·페이지≥3·매≥10).

    사용자 피드백(2026-07-24): 에세이 같은 간단한 과제는 마감이 멀어도
    체크포인트가 필요 없다 → 마감 여유는 게이트 조건에서 제외(단계 수 계산에만
    쓴다). 분량 요건이 감지되지 않은 과제는 보수적으로 플랜을 띄우지 않는다."""
    if length_target is not None:
        need = _VOLUME_MIN.get(getattr(length_target, "unit", ""), None)
        size = getattr(length_target, "min", None) or getattr(length_target, "max", None)
        if need is not None and size is not None and size >= need:
            return True
    return False


# 유형별 단계 문구 — (제목, until이 하는 것, 학생 몫, 통과 조건) 4단계 기준.
# 3단계는 앞 두 개를 합치고, 2단계는 초안까지/제출로 접는다.
_STEPS_DEFAULT = [
    ("자료·방향 잡기",
     "eTL·첨부 자료 수집과 순위화, 쟁점 정리",
     "주제·방향 확정([[DECISION]] 답하기)",
     "다룰 주제와 핵심 자료가 정해져 있다"),
    ("개요 + 경계선 초안",
     "논증 구조로 경계선까지 초안 작성",
     "논지·관점 선택([[DECISION]] 답하기)",
     "초안이 있고 남은 결정이 무엇인지 안다"),
    ("결정 반영 완성본",
     "내 답을 내 말투로 녹인 완성본(finalize)",
     "남은 결정 답 채우기·본문 검토",
     "제출 가능한 완성본이 있다"),
    ("최종 점검·제출",
     "준비 점검(마감·분량·인용·근거) 요약",
     "점검 경고 확인 후 제출",
     "경고 0건 또는 확인 완료"),
]
_STEPS_PRESENTATION = [
    ("자료·스토리라인",
     "자료 수집·순위화, 메시지 후보 정리",
     "핵심 메시지 선택([[DECISION]] 답하기)",
     "발표의 한 줄 메시지가 정해져 있다"),
    ("슬라이드 초안",
     "슬라이드 단위 초안(경계선까지)",
     "강조점·순서 선택([[DECISION]] 답하기)",
     "슬라이드 골격과 내용 초안이 있다"),
    ("완성 + 리허설",
     "결정 반영 완성본(finalize)",
     "소리 내어 리허설, 시간 재기",
     "시간 안에 발표가 한 번 돌아간다"),
    ("최종 점검·제출",
     "준비 점검 요약",
     "파일 형식·제출 방법 확인 후 제출",
     "제출 완료"),
]
_STEPS_FACTUAL = [
    ("문항 파악·자료 잡기",
     "문항 분해와 관련 자료·정의 정리",
     "접근법이 갈리는 문항 선택([[DECISION]] 답하기)",
     "모든 문항의 요구를 파악했다"),
    ("풀이(구현) 초안",
     "풀 수 있는 문항의 풀이·코드 초안 작성",
     "가정·설계 선택([[DECISION]] 답하기)",
     "문항별 초안이 있다"),
    ("검산·정리",
     "준비 점검 요약, 형식 정리",
     "답 검산(테스트)·빠진 문항 확인",
     "전 문항 검토 완료"),
    ("제출",
     "제출용 문서 내보내기",
     "제출",
     "제출 완료"),
]


def _steps_for(task_type: str) -> list:
    if task_type == "presentation":
        return _STEPS_PRESENTATION
    if task_type in ("problemset", "code"):
        return _STEPS_FACTUAL
    return _STEPS_DEFAULT


def build_checkpoint_plan(due: Optional[date], task_type: str,
                          length_target=None, today: Optional[date] = None,
                          ) -> Optional[CheckpointPlan]:
    """마감 역산 체크포인트 플랜. 볼륨 게이트 미달이면 None(패널 미노출).

    - 마감 ≥14일: 4단계(비율 0.25/0.6/0.9/1.0) · 7~13일: 3단계(0.4/0.8/1.0)
    - 마감 <7일이지만 분량이 크면: 2단계(0.5/1.0)
    - 마감 없음 + 분량 큼: 날짜 없는 3단계
    날짜는 단조 증가 보정, 마지막 체크포인트=마감일. 지난 마감(D+)은 None.
    """
    today = today or date.today()
    days = (due - today).days if due else None
    if days is not None and days < 0:
        return None                      # 이미 지난 마감 — 플랜 무의미
    if not _is_volume(days, length_target):
        return None
    steps = _steps_for(task_type or "essay")

    if days is None:
        # 마감 미상 — 날짜 없는 3단계(앞 두 단계 병합).
        merged = _merge_first_two(steps)
        cps = [Checkpoint(i + 1, None, *s) for i, s in enumerate(merged[:3])]
        basis = "마감 미상 — 분량 요건 기준 단계형 플랜"
        return CheckpointPlan(cps, basis)

    if days >= 14:
        fracs, use = [0.25, 0.6, 0.9, 1.0], steps
    elif days >= 7:
        fracs, use = [0.4, 0.8, 1.0], _merge_first_two(steps)
    else:
        fracs, use = [0.5, 1.0], [_collapse_to_draft(steps), steps[-1]]

    cps: List[Checkpoint] = []
    prev: Optional[date] = None
    for i, (frac, s) in enumerate(zip(fracs, use, strict=True)):
        d = today + timedelta(days=round(frac * days))
        if prev is not None and d <= prev:
            d = prev + timedelta(days=1)  # 단조 증가 보정(짧은 기간 중복 방지)
        d = min(d, due)
        cps.append(Checkpoint(i + 1, d, *s))
        prev = d
    cps[-1].due = due                     # 마지막은 반드시 마감일
    basis = f"마감까지 {days}일 · 유형 {task_type or 'essay'} 기준 역산"
    return CheckpointPlan(cps, basis)


def _merge_first_two(steps: list) -> list:
    a, b = steps[0], steps[1]
    merged = (f"{a[0]} + {b[0].split(' + ')[-1]}",
              f"{a[1]} → {b[1]}",
              b[2], b[3])
    return [merged] + list(steps[2:])


def _collapse_to_draft(steps: list) -> tuple:
    return ("초안까지 한 번에",
            f"{steps[0][1]} → {steps[1][1]}",
            f"{steps[0][2]} · {steps[1][2]}",
            steps[2][3] if len(steps) > 3 else steps[1][3])


def render_plan_markdown(plan: CheckpointPlan) -> str:
    """리포트/CLI 공용 마크다운 블록."""
    lines = ["## 체크포인트 플랜", f"_{plan.basis}_", ""]
    for c in plan.checkpoints:
        lines.append(f"- **CP{c.no} ({c.date_label()}) {c.title}**")
        lines.append(f"  - until: {c.until_does}")
        lines.append(f"  - 나: {c.you_do}")
        lines.append(f"  - 통과: {c.done_when}")
    return "\n".join(lines)


def plan_for_result(result, today: Optional[date] = None) -> Optional[CheckpointPlan]:
    """Result에서 플랜 산출(렌더 시점 계산 — 저장 없음·LLM 0)."""
    dl = getattr(result, "deadline", None)
    due = dl.due if dl is not None else None
    task_type = (getattr(result, "spec", None) or {}).get("task_type") or "essay"
    lt = getattr(result, "length_target", None)
    return build_checkpoint_plan(due, task_type, lt, today=today)
