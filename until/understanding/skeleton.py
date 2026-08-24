"""답변 골격(AnswerSkeleton) — 유형별 응답 단위의 '논리 순서'를 슬롯 목록으로.

TYPE_GUIDANCE(산문 지침)와 달리 골격은 구조다: 각 응답 단위(강의·문항·문서)가
어떤 순서로 무엇을 다뤄야 하는지의 슬롯 나열. **문장 템플릿이 아니다** — 템플릿을
박으면 모든 학생 답이 똑같아진다. 슬롯은 '무엇이 와야 하는가'만 정하고 문장은
모델·학생 몫.

우선순위: 양식(formfill.detect_form)이 감지되면 양식의 항목 구조가 골격보다
우선하고, 골격은 각 항목 '안'의 논리 순서로만 쓴다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..config import algo_version
from .requirements import ContentElement


@dataclass
class SkeletonSlot:
    """응답 단위가 갖춰야 할 논리 슬롯 하나(문장 템플릿 아님)."""
    id: str
    label: str            # 사람이 읽는 이름
    hint: str = ""        # 무엇이 오는 자리인지(지침, 문장 아님)
    evidence_kind: str = "source_document"   # 기본 근거 종류(요소 병합 시 갱신)
    required: bool = True


@dataclass
class AnswerSkeleton:
    task_type: str
    unit_name: str                      # 응답 단위 이름(강의/문항/문서)
    slots: List[SkeletonSlot] = field(default_factory=list)


_SKELETONS = {
    # 참가/활동 보고서 — 사실 → 지식 → 개인 관점 → 구체 행위 → 남은 질문.
    "reflective_report": AnswerSkeleton(
        task_type="reflective_report", unit_name="강의",
        slots=[
            SkeletonSlot("covered_facts", "무엇을 다뤘나",
                         "강의·활동이 실제로 다룬 주제·사례(사실)", "lecture_material"),
            SkeletonSlot("core_concept", "핵심 개념",
                         "다룬 지식 중 중심 개념·이론", "lecture_material"),
            SkeletonSlot("new_learning", "내가 새로 알게 된 것",
                         "본인 관점 — 자료로 채울 수 없음(없으면 DECISION)",
                         "user_experience"),
            SkeletonSlot("application", "실습·적용",
                         "직접 해 본 것 또는 적용 계획(구체 행위)",
                         "user_experience"),
            SkeletonSlot("open_question", "남은 질문",
                         "더 알고 싶은 것(선택)", "user_experience", required=False),
        ]),
    # 에세이 — 기존 논증 골격 유지(prompts.TYPE_GUIDANCE와 일치).
    "essay": AnswerSkeleton(
        task_type="essay", unit_name="문서",
        slots=[
            SkeletonSlot("problem", "문제 제기", "이 글이 답할 질문", "source_document"),
            SkeletonSlot("claims", "주장 후보와 근거",
                         "후보별 전개 — 채택은 DECISION", "source_document"),
            SkeletonSlot("counter", "예상 반론과 재반론", "", "source_document"),
            SkeletonSlot("conclusion", "결론 후보", "선택은 사람 몫", "user_experience"),
        ]),
    # 실험/조사 보고서 — 방법 → 결과 → 고찰.
    "report": AnswerSkeleton(
        task_type="report", unit_name="문서",
        slots=[
            SkeletonSlot("method", "방법", "재료·절차·조건(객관)", "source_document"),
            SkeletonSlot("results", "결과", "측정·수집된 사실", "source_document"),
            SkeletonSlot("discussion", "고찰",
                         "해석 방향은 본인 판단(DECISION 가능)", "user_experience"),
        ]),
    # 질의 — 각 질문(응답 단위)이 갖출 논리: 강의 연결 → 구체 질문(기획 T1b).
    "inquiry": AnswerSkeleton(
        task_type="inquiry", unit_name="질문",
        slots=[
            SkeletonSlot("lecture_link", "강의 주제와의 연결",
                         "질문이 이번 강의·연사와 닿는 지점", "source_document"),
            SkeletonSlot("question_body", "구체 질문 문장",
                         "완성된 존댓말 한 문장, 열린 질문", "source_document"),
        ]),
    # HDL 실습(v0.2, COURSE_ALGORITHMS_2026F §4.1) — 단위는 실습 회차.
    # 슬롯 순서 근거: 사전 설계(진리표·상태도·K-map)가 **구현보다 먼저**다 —
    # 실습 세션 2시간 안에 못 끝내는 원인이 사전 설계 누락이라(실측 메모),
    # 코드부터 쓰고 사전 설계를 역산하지 않도록 골격이 순서를 강제한다.
    # 5~8번(실측 증빙·합성 결과·고찰·디버깅)은 툴 실행·본인 경험 없이는 채울 수
    # 없는 user_experience — 근거 없으면 빈칸형 DECISION만 남긴다(§4.1 하드 금지).
    "hdl_lab": AnswerSkeleton(
        task_type="hdl_lab", unit_name="실습 회차",
        slots=[
            SkeletonSlot("design_goal", "설계 목표·사양",
                         "지시서에서", "source_document"),
            SkeletonSlot("pre_design", "사전 설계(진리표·상태도·K-map)",
                         "구현보다 먼저 — 여기서부터 시작", "source_document"),
            SkeletonSlot("rtl_impl", "RTL 구현", "코드 블록", "source_document"),
            SkeletonSlot("verification", "검증(테스트벤치·시나리오)",
                         "커버할 입력 조합 나열", "source_document"),
            SkeletonSlot("measured_evidence", "실측 증빙(파형·보드 동작)",
                         "없으면 지어내지 말고 빈칸 DECISION", "user_experience"),
            SkeletonSlot("synthesis_result", "합성 결과(LUT/FF·타이밍)",
                         "수치는 툴이 준 것만", "user_experience"),
            SkeletonSlot("discussion", "고찰 — 설계 선택의 근거",
                         "'왜 Moore로 했는지', '왜 LUT 그만큼인지'",
                         "user_experience"),
            SkeletonSlot("debug_log", "오류·디버깅 기록",
                         "에러 메시지 원문 → 해석 → 조치", "user_experience"),
        ]),
}

# 단문 소감문 골격(v0.2, §4.6(b)) — 200자 상한 과제(세미나과목, due=당일)에
# 현행 5슬롯을 넣으면 슬롯당 40자로 전부 공허해진다. 3슬롯으로 줄이고 배분을
# 힌트에 명시한다. 3번(내 관점)은 자료로 채울 수 없다 — 200자라고 관점을
# 지어내면 안 되고, 근거 없으면 빈칸형 DECISION 하나만 남긴다(현행 정책 그대로).
_REFLECTIVE_SHORT = AnswerSkeleton(
    task_type="reflective_report", unit_name="강의",
    slots=[
        SkeletonSlot("covered_facts", "이 강의가 실제로 다룬 것",
                     "~70자 — 사실만", "lecture_material"),
        SkeletonSlot("core_concept", "그중 핵심 개념 하나",
                     "~60자 — 하나만 고른다", "lecture_material"),
        SkeletonSlot("my_view", "내 관점·적용",
                     "~70자 — 본인 관점, 자료로 채울 수 없음(없으면 DECISION)",
                     "user_experience"),
    ])

# 단문 골격 발동 상한(자) — §4.6(b): length_target.max <= 400이면 단문.
_SHORT_REFLECTIVE_MAX = 400


def get_skeleton(task_type: str,
                 length_target=None) -> Optional[AnswerSkeleton]:
    """유형별 골격 선택. length_target(LengthTarget, .min/.max)은 v0.2 신설 옵션 —
    reflective_report이고 상한 400자 이하면 단문 3슬롯(§4.6(b))을 준다.

    기본값(None)이면 현행 동작 그대로(v0.1 바이트 동일). 발동 조건만 여기 두고
    배선(호출부에서 length_target을 넘기는 일)은 오케스트레이터 몫이다.
    """
    if (length_target is not None
            and (task_type or "") == "reflective_report"
            and algo_version() == "v0.2"
            and getattr(length_target, "max", None) is not None
            and length_target.max <= _SHORT_REFLECTIVE_MAX):
        return _REFLECTIVE_SHORT
    return _SKELETONS.get(task_type or "")


def merge_with_elements(skeleton: Optional[AnswerSkeleton],
                        elements: List[ContentElement]) -> List[SkeletonSlot]:
    """골격 슬롯과 요구사항 요소를 병합한 '이 과제의 슬롯 목록'.

    지시문에서 뽑은 요소(1단계)가 우선 — 골격은 지시문이 말하지 않은 기본 순서를
    보충한다. 라벨이 겹치면 요소의 evidence_kind/필수 여부를 쓴다.
    """
    slots: List[SkeletonSlot] = []
    used = set()
    elems = list(elements or [])

    from difflib import SequenceMatcher

    def _sim(a: str, b: str) -> float:
        a, b = a.replace(" ", ""), b.replace(" ", "")
        return SequenceMatcher(None, a, b).ratio()

    def _match(slot: SkeletonSlot) -> Optional[ContentElement]:
        # 유사 라벨 병합("내가 새로 알게 된 것"↔"새로 알게 된 점") — 안 하면
        # 같은 요소가 슬롯 두 개로 중복된다.
        best, best_r = None, 0.0
        for e in elems:
            if id(e) in used:
                continue
            if slot.id == e.id or slot.label in e.label or e.label in slot.label:
                r = 1.0
            else:
                r = _sim(slot.label, e.label)
            if r > best_r:
                best, best_r = e, r
        if best is not None and best_r >= 0.5:
            used.add(id(best))
            return best
        return None

    for s in (skeleton.slots if skeleton else []):
        e = _match(s)
        if e is not None:
            slots.append(SkeletonSlot(e.id, e.label, s.hint,
                                      e.evidence_kind, e.required))
        else:
            slots.append(s)
    # 골격에 없는 지시문 요소는 뒤에 덧붙인다(지시문이 우선).
    for e in elems:
        if id(e) not in used:
            slots.append(SkeletonSlot(e.id, e.label, e.source_span[:60],
                                      e.evidence_kind, e.required))
    return slots


def skeleton_directive(task_type: str,
                       elements: Optional[List[ContentElement]] = None,
                       length_target=None) -> str:
    """실행 프롬프트용 골격 지침 — 슬롯 순서만 제시(문장 템플릿 금지).

    length_target은 get_skeleton으로 그대로 전달(v0.2 단문 골격 선택용) —
    기본값 None이면 현행 동작 불변.
    """
    sk = get_skeleton(task_type, length_target)
    slots = merge_with_elements(sk, elements or [])
    if not slots:
        return ""
    unit = sk.unit_name if sk else "항목"
    lines = []
    for i, s in enumerate(slots, 1):
        opt = "" if s.required else " (선택)"
        human = " — 본인만 아는 내용: 자료·답 없이 지어내지 말고 빈칸형 DECISION" \
            if s.evidence_kind == "user_experience" else ""
        hint = f": {s.hint}" if s.hint else ""
        lines.append(f"  {i}) {s.label}{opt}{hint}{human}")
    return (
        f"[ 답변 골격 — {unit} 단위 논리 순서 ]\n"
        f"- 각 {unit}의 서술은 아래 순서를 갖춰라(제목 나열이 아니라 내용 흐름으로,\n"
        "  문장 틀을 복제하지 말 것 — 순서만 지키고 문장은 자유):\n"
        + "\n".join(lines))


# ── 결정 골격 — 유형별로 '어떤 판단이 사람 몫인가' ─────────────────────
# Phase 3 검증(docs/planning/type_algorithms.md §9-1): 결정의 '개수'는 가드가
# 지키지만 '종류'는 아무도 안 지켜, 반응형(소감문·질의)이 에세이 결정 3종
# (논지/반론 톤/주장 강도)을 받는 범주 착오가 확인됐다. 유형별 결정의 종류를
# 지침으로 명시한다. 문장 강제가 아니라 종류 안내(경계선 철학 유지).
DECISION_SKELETONS = {
    "essay": "핵심 논지·관점의 선택, 반론 수용 톤, 마무리 강도",
    "reflective_report": (
        "본인 '경험'의 선택뿐 — 인상 깊었던 대목(키워드), 적용·수강 계획. "
        "논지 선택·반론 톤·주장 강도 같은 에세이형 결정을 만들지 말 것"),
    "inquiry": "질문 후보 중 선택(2~3개) 단 하나 — 그 외 결정을 만들지 말 것",
    "report": "결과 해석의 방향, 개선·후속 실험 방향",
    "presentation": "가장 앞세울 메시지·스토리의 선택",
    "problemset": "억지 결정 금지 — 문제에 없는 가정을 둬야 할 때만",
    "code": "억지 결정 금지 — 설계 선택지(자료구조 등)가 실제로 갈릴 때만",
    # v0.2 신설(§4.1) — code와 달리 고찰 결정이 필수인 유형. 인용문 그대로:
    # 파형·합성 수치는 '사실'이라 결정 후보조차 아니다(추정값 환각 = 학문적 부정).
    "hdl_lab": (
        "설계 선택의 근거(인코딩 방식·상태기계 형태·파이프라인 여부), "
        "예상과 실측이 갈린 지점의 해석. 파형·합성 수치·보드 동작은 결정이 "
        "아니라 사실이다 — 없으면 빈칸으로 남기고 절대 추정값을 쓰지 않는다"),
}


def decision_directive(task_type: str) -> str:
    """유형별 '결정의 종류' 지침 — 실행 시스템 프롬프트에 주입. 없으면 빈 문자열."""
    kinds = DECISION_SKELETONS.get(task_type or "")
    if not kinds:
        return ""
    return (
        "[ 이 유형의 결정 골격 — 어떤 판단이 사람 몫인가 ]\n"
        f"- 이 과제 유형에서 [[DECISION]]으로 남길 판단의 종류: {kinds}.\n"
        "- 위 종류에 해당하지 않는 결정을 억지로 만들거나, 다른 유형의 결정"
        "(예: 소감문에 '반론 수용 톤')을 복제하지 말 것."
    )
