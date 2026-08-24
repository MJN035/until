"""단위별 내용 계획(UnitPlan) — 생성 전에 '무엇을 어떤 근거로 몇 자' 정한다(5단계).

plan.py(체크포인트 일정 계획)와 다르다 — 이것은 내용 계획이다.

핵심 규칙(코드로 강제):
- element별 행동은 근거 충분성(evidence.sufficiency)이 정한다:
  sufficient=write / thin=write_thin(확신 금지·근거 범위 내) / absent=decision(생성 금지).
- 분량 배분: 항목 목표를 write 가능한 element에 나누고, **absent의 몫은 다른
  element로 넘기지 않는다**(넘기면 물타기) — 그 단위의 목표를 줄이고 DECISION.
결정적 코어(LLM 0). '무엇을 쓸지 한 줄'은 선택적 LLM 보강(실패 무해)이지만
행동·배분·근거 선택은 항상 결정적이다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from ..context.evidence import absent_decision_question, sufficiency
from ..understanding.length_target import target_in_chars


@dataclass
class PlanItem:
    element_id: str
    label: str
    action: str                 # write | write_thin | decision
    sufficiency: str            # sufficient | thin | absent
    evidence_titles: List[str] = field(default_factory=list)
    excerpts: List[str] = field(default_factory=list)   # 생성 프롬프트에 실릴 발췌
    target_chars: int = 0       # 이 element에 배분된 분량(자)
    note: str = ""              # 한 줄 계획(선택적 LLM 보강 자리)
    decision_question: str = "" # action=decision일 때의 구체 질문


@dataclass
class UnitPlan:
    unit_index: int
    items: List[PlanItem] = field(default_factory=list)
    target_chars: int = 0       # absent 몫을 제외한 이 단위의 실효 목표

    @property
    def writable(self) -> List[PlanItem]:
        return [i for i in self.items if i.action != "decision"]

    @property
    def decisions(self) -> List[PlanItem]:
        return [i for i in self.items if i.action == "decision"]


_KIND_MAP = {
    "user_experience": ("user_input",),
    "lecture_material": ("etl_material", "assignment_doc", "my_file",
                         "etl_announcement"),
    "source_document": ("assignment_doc", "etl_material", "my_file",
                        "etl_announcement"),
    "general_knowledge": (),
}


def build_unit_plan(unit, *, min_chars: int = 100, min_tokens: int = 2) -> UnitPlan:
    """단위 하나의 결정적 내용 계획. unit.evidence(원장)와 elements(슬롯) 필요."""
    ledger = unit.evidence
    plan = UnitPlan(unit_index=unit.index)
    slots = list(getattr(unit, "elements", []) or [])
    if not slots:
        # 슬롯이 없는 건 '근거가 없다'가 아니라 **이 유형에 골격이 없다**는 뜻이다
        # (`problemset`·`code`·`presentation`은 get_skeleton()이 None).
        #
        # 여기서 분량 목표를 주고 싶은 유혹이 있다 — 'A4 5매' 요건이 이 경로에서
        # 증발하기 때문이다. 실제로 그렇게 해 봤고 **3인 코퍼스 9건이 깨졌다**
        # (2026-08-22, 전부 problemset·presentation·code). 당연한 결과다: 코드
        # 산출물·문제 풀이·발표 자료에 산문 글자 수를 요구하는 것 자체가 틀렸다.
        # 골격이 없는 유형은 분량 강제 대상이 아니다 — 목표 0으로 둔다.
        #
        # 진짜 문제는 여기가 아니라 **라우팅**이다. 산문 과제가 problemset으로
        # 오추정돼 이 경로로 오는 것(원장 U-5). 그건 여기서 못 고친다.
        return plan

    # 1) element별 충분성 → 행동.
    for s in slots:
        kind = getattr(s, "evidence_kind", "source_document")
        suff = sufficiency(ledger, kind, min_chars=min_chars,
                           min_tokens=min_tokens) if ledger else (
            "sufficient" if kind == "general_knowledge" else "absent")
        required = getattr(s, "required", True)
        if suff == "absent":
            if not required:
                continue  # 선택 요소는 근거 없으면 그냥 생략(질문도 안 만든다)
            action = "decision"
        elif suff == "thin":
            action = "write_thin"
        else:
            action = "write"
        item = PlanItem(element_id=getattr(s, "id", ""), label=getattr(s, "label", ""),
                        action=action, sufficiency=suff)
        if ledger is not None and action != "decision":
            wanted = _KIND_MAP.get(kind, ())
            evs = ledger.of_kinds(set(wanted)) if wanted else []
            item.evidence_titles = [e.title for e in evs[:2]]
            item.excerpts = [e.excerpt for e in evs[:2]]
        if action == "decision":
            item.decision_question = absent_decision_question(unit, s)
        plan.items.append(item)

    # 2) 분량 배분 — 항목 목표를 write 가능한 element에 균등 배분.
    #    absent 몫은 이월 금지(물타기 방지): 실효 목표 = 목표 × (write 수 / 전체 수).
    # 글자 수로 환산해서 나눈다 — `.max`를 그대로 쓰면 '페이지 5'가 5자가 된다.
    base = target_in_chars(getattr(unit, "length_target", None))
    n_all = len(plan.items)
    writable = plan.writable
    if base and n_all:
        per = base // n_all
        for i in writable:
            i.target_chars = per
        plan.target_chars = per * len(writable)
    return plan


def enrich_plan_notes(units: List, llm, spec: dict) -> None:
    """선택적 LLM 보강 — 전 단위를 한 번에 계획(무엇을 쓸지 한 줄). 실패 무해.

    행동·배분·근거는 이미 결정적으로 정해져 있고, note만 채운다(토큰 절약을 위해
    단위 전체 1회 호출 — 생성은 단위별 분리 유지).
    """
    import json
    todo = []
    for u in units:
        for it in (u.plan.items if u.plan else []):
            if it.action != "decision":
                todo.append((u, it))
    if not todo or llm is None:
        return
    lines = [f"{u.index}.{it.element_id}: [{u.title or u.mark}] {it.label} "
             f"(근거: {'; '.join(x[:80] for x in it.excerpts) or '일반 지식'})"
             for u, it in todo]
    schema = {"type": "object",
              "properties": {"notes": {"type": "object",
                                       "additionalProperties": {"type": "string"}}},
              "required": ["notes"], "additionalProperties": False}
    try:
        res = llm.complete(
            "각 항목에 '무엇을 쓸지' 계획 한 줄(25자 내)을 만든다. 근거에 있는 "
            "내용만, 문장 틀이 아니라 소재를 정하라.",
            "항목 목록:\n" + "\n".join(lines) + "\n\nnotes의 키는 '단위.요소' 그대로.",
            tag="content-plan", json=True, schema=schema)
        notes = json.loads(res.text).get("notes", {})
        for u, it in todo:
            note = notes.get(f"{u.index}.{it.element_id}")
            if isinstance(note, str) and note.strip():
                it.note = note.strip()[:60]
    except Exception:
        pass  # note는 장식 — 실패해도 계획은 유효


# 되묻는 질문을 LLM으로 다시 쓰는 보강을 시도했다가 **되돌렸다**(2026-08-23).
# 실측에서 더 나빠졌다 — 명세가 얇으면 모델도 과제가 뭘 요구하는지 모르므로
# "과제에서 요구하는 핵심 분석 항목은 무엇인가요?", "제출 파일명·형식은?"처럼
# **학생에게 과제를 되묻는** 질문이 나왔고, 강의 내용을 모른 채 "(1) 전자 회로
# 기본 (2) 디지털 논리 설계" 같은 후보를 지어냈다.
#
# 교훈: 명세가 얇을 때 정직한 질문은 "무엇을 쓸까요?"가 아니라 "원료를 주세요"다.
# 그건 이미 material_gap·missing_attachments가 한다. 질문 문구를 다듬는 것으로
# 풀 문제가 아니었다(원장 U-3).


def render_plan(unit) -> str:
    """진단용 — 단위 계획 요약."""
    if not getattr(unit, "plan", None):
        return ""
    rows = []
    for it in unit.plan.items:
        act = {"write": "작성", "write_thin": "제한적 작성",
               "decision": "질문(생성 금지)"}[it.action]
        tail = f" · {it.target_chars}자" if it.target_chars else ""
        rows.append(f"  - {it.label}: {act}{tail}"
                    + (f" · 근거 {len(it.excerpts)}건" if it.excerpts else ""))
    return "\n".join(rows)
