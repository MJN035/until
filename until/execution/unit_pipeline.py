"""단위별 생성 경로(UNTIL_PIPELINE=unit) — 재설계 8단계의 통합 루프.

  단위 분할 → [단위마다] 근거 원장 → 계획 → 생성 → 검증(분량·구체성·커버리지)
  → 실패한 단위만 재생성 → 결정적 조립(기본정보·강의 표 + ①② 섹션)

원칙:
- absent 요소는 생성 금지 — 모델 지시 + **결정적 마커 삽입**의 이중 보장.
- 표(기본정보·강의)는 LLM이 아니라 코드가 채운다(프로필·단위 메타 — 환각 0).
- 실패 단위만 재생성(문서 전체 재생성 금지), 재생성 프롬프트에 구체적 델타.
- 최대 재시도 후 실패는 조용히 통과시키지 않고 GuardReport로 표면화.
기존 통짜 경로(drafter.draft_to_boundary)는 그대로 남는다(legacy — 롤아웃 비교).
"""
from __future__ import annotations

import json
from typing import List, Optional, Tuple

from ..boundary.models import Draft
from ..capture.formfill import detect_form
from ..context.evidence import build_ledger
from ..understanding.length_target import LengthTarget, measure_length
from ..understanding.skeleton import get_skeleton, merge_with_elements
from . import prompts
from .boundary_guard import BoundaryValidator, GuardReport
from .content_plan import build_unit_plan, enrich_plan_notes
from .coverage import check_unit_coverage, coverage_errors
from .specificity import SpecificityValidator
from .units import ResponseUnit, derive_units


def _unit_user_prompt(spec: dict, unit: ResponseUnit) -> str:
    plan = unit.plan
    if not plan or not plan.items:
        # 슬롯이 없는 일반 산문(단위 1개) — 명세 기반 생성(legacy와 동등 동작).
        return prompts.user_message(json.dumps(spec, ensure_ascii=False),
                                    "(첨부 자료 참조)")
    lines = []
    for it in plan.items if plan else []:
        if it.action == "decision":
            continue  # 쓸 것만 프롬프트에 — absent는 아예 요청하지 않는다
        mode = "" if it.action == "write" else \
            " (근거가 얇음 — 확신 표현 금지, 아래 발췌 범위 안에서만 서술)"
        ev = "\n".join(f"    · {x}" for x in it.excerpts) or "    · (일반 지식으로 충분)"
        note = f" | 계획: {it.note}" if it.note else ""
        tgt = f" | 목표 약 {it.target_chars}자" if it.target_chars else ""
        lines.append(f"- {it.label}{mode}{note}{tgt}\n  [근거 발췌]\n{ev}")
    meta = " · ".join(f"{k} {v}" for k, v in unit.meta.items())
    tgt_line = ""
    if plan and plan.target_chars:
        tgt_line = (f"\n[분량] 이 항목 본문 약 {plan.target_chars}자"
                    "(공백 제외) — 요소 목표를 합친 값. 억지 반복 금지.")
    return (
        f"[과제 명세(JSON)]\n{json.dumps(spec, ensure_ascii=False)}\n\n"
        f"[이 항목] {unit.mark} {unit.title or ''}" + (f" ({meta})" if meta else "")
        + "\n\n[써야 할 요소 — 아래 순서대로, 근거 발췌만 근거로]\n"
        + "\n".join(lines)
        + tgt_line
        + "\n\n[금지] 위 요소 외 내용(특히 본인 소감·경험류)을 지어내지 말 것 — "
          "그 자리는 시스템이 질문으로 남긴다. 강의 제목만 알면 쓸 수 있는 공허한 "
          "문장('~을 체험하였다', '많은 도움이 되었다') 금지.\n"
        "이 항목의 '본문 문단만' 출력하라(항목 번호·강의명 헤더·표·설명 문구 없이)."
    )


def _unit_validators(unit: ResponseUnit, cfg, task_type: str,
                     safety_mode: bool = False,
                     is_form: bool = False) -> list:
    vs: list = []
    # 과제 자체를 서술하는 본문 차단(legacy와 같은 계약). 양식 과제는
    # 마감 칸이 정당할 수 있어 뺀다.
    if not is_form and not (cfg.backend == "mock" and not cfg.enforce_on_mock):
        from .boundary_guard import AssignmentMetaValidator
        vs.append(AssignmentMetaValidator())
    if safety_mode and not (cfg.backend == "mock" and not cfg.enforce_on_mock):
        # 원료가 없다고 판정한 상태에서 '구체적 후보'는 창작이다(원장 U-3).
        from .boundary_guard import InventedCandidateValidator
        vs.append(InventedCandidateValidator())
    plan = unit.plan
    has_plan_length = False
    if not safety_mode and plan and plan.target_chars and plan.target_chars >= 60:
        # absent 몫이 빠진 실효 목표 — 단위 본문 전체로 판정(내외 ±25% 허용).
        # 목표가 60자 미만이면 하한 바닥(50)이 상한(×1.35)을 넘어 어떤 본문도
        # 통과 불가가 되므로 분량 검증을 걸지 않는다(다른 검증은 유지).
        lo = max(50, int(plan.target_chars * 0.75))
        hi = int(plan.target_chars * 1.35)
        from .boundary_guard import LengthValidator
        vs.append(LengthValidator(LengthTarget(unit="자", min=lo, max=hi)))
        has_plan_length = True
    src_texts = [x for it in (plan.items if plan else []) for x in it.excerpts]
    if not safety_mode:
        vs.append(SpecificityValidator(
            source_texts=src_texts, title=unit.title,
            min_score=getattr(cfg, "specificity_min", 0.55)))
    # 경계선 기본 검사(외국 문자·깨진 마커). 결정 수는 단위 단위로 강제하지 않는다
    # (absent 마커는 조립 때 결정적으로 붙는다).
    # 분량 하한: plan 목표가 있는 단위는 그 목표가 분량 의도를 지배한다(60자
    # 이상이면 LengthValidator가 판정, 미만이면 목표 자체가 작다는 뜻). 고정
    # 200자 하한을 함께 걸면 목표 60~150자 단위는 상한(×1.35)과 모순되어 어떤
    # 본문도 통과 불가였다(실코퍼스 18건 회귀). 최소 문장 방어선만 남긴다.
    has_plan_length = has_plan_length or bool(
        not safety_mode and plan and plan.target_chars)
    floor = 1 if safety_mode else (50 if has_plan_length else 200)
    vs.append(BoundaryValidator(min_decisions=0, min_body_chars=floor,
                                forbid_stance=(task_type == "essay")))
    return vs


def _generate_unit(unit: ResponseUnit, spec: dict, llm, cfg,
                   task_type: str, system_extra: str = "",
                   voice_hint: str = "", is_form: bool = False,
                   ) -> Tuple[ResponseUnit, List[List[str]]]:
    """단위 하나 생성+검증 루프(실패 시 그 단위만 reask). 이력 반환."""
    plan = unit.plan
    if plan is not None and plan.items and not plan.writable:
        # 쓸 수 있는 요소가 하나도 없다(근거 전무) — 생성 자체를 생략.
        # '그럴듯한 300자'가 아니라 질문만 남기는 것이 정답인 케이스.
        unit.body = ""
        return unit, [[]]
    system = prompts.SYSTEM
    # 원료가 없으면 유형 지침을 끈다 — "끝까지 써라"와 "지어내지 마라"가
    # 부딪히면 모델은 구체적인 쪽(유형 지침)을 따른다(prompts.type_guidance).
    tg = prompts.type_guidance(task_type, bool(spec.get("material_gap")))
    if tg:
        system += "\n\n" + tg
    if system_extra:
        # 대필 금지 게이트·원료 없음 지시 등 안전 지시 — legacy와 동일하게
        # unit 경로에도 반드시 전달돼야 한다(누락 시 게이트 무력화).
        system += "\n\n" + system_extra
    if voice_hint:
        # 톤 규격·문체 지침 — legacy(drafter.draft_to_boundary)와 같은 위치(맨 뒤).
        system += "\n\n" + voice_hint
    base_user = _unit_user_prompt(spec, unit)
    safety_mode = bool(spec.get("integrity_gate") or spec.get("material_gap"))
    validators = _unit_validators(unit, cfg, task_type, safety_mode=safety_mode,
                                  is_form=is_form)
    history: List[List[str]] = []
    body, errors = "", []
    for _attempt in range(cfg.max_reasks + 1):
        user = base_user if not errors else (
            base_user + "\n\n=== 재요청 ===\n직전 출력의 문제:\n"
            + "\n".join(f"- {e}" for e in errors)
            + "\n문제를 고쳐 이 항목 본문만 다시 출력하라.")
        body = llm.complete(system, user, tag="execution-unit").text.strip()
        unit.body = body
        errors = []
        d = Draft.from_text(body)
        for v in validators:
            errors.extend(v.validate(d).errors)
        if not safety_mode:
            errors.extend(coverage_errors(check_unit_coverage(unit)))
        history.append(list(errors))
        if not errors:
            break
    unit.body = body
    return unit, history


def _append_absent_decisions(unit: ResponseUnit) -> None:
    """absent 요소의 질문 마커를 결정적으로 붙인다(모델 누락과 무관한 보장)."""
    plan = unit.plan
    if not plan:
        return
    for it in plan.decisions:
        q = it.decision_question or f"{it.label}: ___"
        if q[:20] not in unit.body:
            unit.body = unit.body.rstrip() + f"\n[[DECISION: {q}]]"


def _meta_when(meta: dict) -> str:
    """단위 메타에서 일시/날짜 값 — 원본 헤더 표기('수강일시'·'수강 일시'·'일시'
    등)로 키가 저장되므로 공백 제거 정규화로 대조한다(고정 키 2개만 보면 유실)."""
    for k in ("수강 일시", "일시"):
        if meta.get(k):
            return meta[k]
    for k, v in meta.items():
        kn = (k or "").replace(" ", "")
        if v and ("일시" in kn or "날짜" in kn):
            return v
    return ""


def _assemble_tables(form_text: str, units: List[ResponseUnit]) -> str:
    """양식의 표를 코드로 채운 마크다운 — 기본정보(프로필)·강의 표(단위 메타).

    LLM에게 표를 맡기지 않는다(환각 0·formfill 주입과 같은 값 계보).
    """
    from ..profile import profile_mapping
    fs = detect_form(form_text)
    if not fs.tables:
        return ""
    pm = profile_mapping()
    out: List[str] = []
    for t in fs.tables:
        head = [c.strip() for c in t[0]]
        is_list = any("강좌" in h or "강의" in h for h in head if h)
        if is_list:
            out.append("| " + " | ".join(h for h in head) + " |")
            out.append("|" + "---|" * len(head))
            for u in units:
                row = []
                for h in head:
                    if "강좌" in h or "강의" in h:
                        row.append(u.title or "")
                    elif "분야" in h:
                        row.append(u.meta.get("분야", ""))
                    elif "일시" in h or "날짜" in h:
                        row.append(_meta_when(u.meta))
                    else:
                        row.append(u.meta.get(h, ""))
                out.append("| " + " | ".join(row) + " |")
        else:
            # 라벨/값 쌍 표 — 프로필에서. 모르는 칸은 비워 둔다(지어내지 않음).
            for row in t:
                cells = []
                for i, c in enumerate(row):
                    c = c.strip()
                    if i % 2 == 0:
                        cells.append(c)
                    else:
                        label = row[i - 1].strip()
                        cells.append(c or pm.get(label, ""))
                out.append("| " + " | ".join(cells) + " |")
            n = max(len(r) for r in t)
            out.insert(len(out) - len(t) + 1, "|" + "---|" * n)
        out.append("")
    return "\n".join(out).strip()


#: 조립된 본문에서 제목을 뺀 '실내용'이 이보다 짧으면 사실상 빈 초안으로 본다.
#: 정상 초안은 이 값의 열 배를 훌쩍 넘는다 — 오탐보다 미탐을 걱정할 자리다.
_EMPTY_DRAFT_CHARS = 120

#: 빈 초안일 때 되묻는 질문. 원료가 무엇인지는 과제마다 다르지만, '무엇을 주면
#: 이어서 쓸 수 있는가'는 항상 같은 형태로 물을 수 있다.
_MATERIAL_QUESTION = (
    "이 과제를 채울 원료가 자료에서 확인되지 않았어요. 수업에서 다룬 내용"
    "(강의 자료·필기·실습 기록)이나 본인이 조사한 자료 중 쓸 수 있는 것을 "
    "알려 주시면 이어서 씁니다 — 어떤 자료를 쓸 수 있나요?"
)


def _ensure_answerable(assembled: str, title: str) -> str:
    """본문도 없고 질문도 없는 초안은 막다른 페이지다 — 최소한 물을 것은 남긴다.

    실측(2026-08-22, 실 LLM): 원료가 빈 과제에서 unit 경로가 `# 과제 / 글쓰기과제`
    12자를 내놓고 결정 지점도 0개였다. 그런데 readiness는 `material_gap`을 잡아
    *"결정 칸에 원료를 답하면 마저 채울 수 있어요"*라고 안내한다 — **가리키는 칸이
    없으니 안내가 거짓말이 된다.**

    왜 여기서 막는가: 이 상태는 여러 fail-open이 겹쳐 만들어진다. ① 골격이 없는
    task_type(`problemset`·`code`·`presentation`)은 슬롯이 0이라 계획이 비고,
    ② 계획이 비면 계획 기반 검증기가 사라지며, ③ `safety_mode`(integrity_gate ·
    material_gap)는 본문 하한을 1자로 낮춘다(`_unit_validators`). 셋 다 각각은
    근거가 있는 선택이라 어느 하나를 뒤집기보다, **조립 끝에서 결과를 보고** 막는
    편이 안전하다. 짧은 산출물이 정당한 경우(대필 금지 게이트의 학습 보조)에도
    질문 한 줄이 더 붙을 뿐 내용은 건드리지 않는다.

    본문을 만들어 채우지 않는다 — 근거가 없으니 쓸 게 없는 것이 맞다. 사용자가
    답할 수 있는 질문으로 바꿀 뿐이다(경계선 철학 그대로).
    """
    head = f"# {title}"
    content = assembled.replace(head, "", 1).strip()
    if len(content) >= _EMPTY_DRAFT_CHARS:
        return assembled                      # 쓸 만큼 썼다
    # 마커 문자열이 아니라 **파싱된 결정**으로 판정한다 — Draft.from_text는
    # 자리표시 마커("___" 같은 것)를 걸러 내므로, 문자열만 보면 사용자가 답할 수
    # 없는 마커 하나 때문에 막다른 페이지를 그대로 통과시킨다.
    if Draft.from_text(assembled).decisions:
        return assembled                      # 물을 것이 이미 있다
    return assembled.rstrip() + f"\n\n[[DECISION: {_MATERIAL_QUESTION}]]\n"


def run_unit_draft(docs, spec, llm, cfg, *, content_elements=None,
                   context_sources=None, user_answers=None,
                   n_units_override: Optional[int] = None,
                   system_extra: str = "",
                   voice_hint: str = "",
                   ) -> Tuple[Draft, GuardReport, List[ResponseUnit]]:
    """단위별 경로의 본체 — pipeline.run(UNTIL_PIPELINE=unit)이 호출한다."""
    from pathlib import Path as _P
    task_type = str(spec.get("task_type") or "essay")
    form_text = ""
    for d in docs or []:
        text = getattr(d, "text", "") or ""
        if detect_form(text).is_form:
            form_text = text
            break

    # 분량 요건(항목당) — legacy와 같은 감지기 재사용.
    from ..understanding.length_target import detect_length_target
    length_target = detect_length_target(spec, docs)

    slots = merge_with_elements(get_skeleton(task_type), content_elements or [])
    units = derive_units(docs, form_text, slots, length_target,
                         n_override=n_units_override)

    # 근거 원장은 과제 문서+맥락 자료 전체에서 단위별로 검색.
    from ..llm.base import SourceDoc
    source_docs = [SourceDoc(title=f"과제: {_P(d.source).name}", text=d.text[:6000])
                   for d in (docs or [])] + list(context_sources or [])
    for u in units:
        u.evidence = build_ledger(u, source_docs, user_answers=user_answers,
                                  generic_ok=(len(units) == 1))
        u.plan = build_unit_plan(
            u, min_chars=getattr(cfg, "evidence_sufficient_chars", 100),
            min_tokens=getattr(cfg, "evidence_min_tokens", 2))
    enrich_plan_notes(units, llm if cfg.backend != "mock" else None, spec)

    # 단위별 생성(병렬) — 실패한 단위만 자체 reask.
    histories: dict = {}
    workers = max(1, min(getattr(cfg, "unit_parallel", 3), len(units)))
    if workers > 1 and len(units) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_generate_unit, u, spec, llm, cfg, task_type,
                              system_extra, voice_hint, bool(form_text)): u
                    for u in units}
            for f in futs:
                u, hist = f.result()
                histories[u.index] = hist
    else:
        for u in units:
            _, hist = _generate_unit(u, spec, llm, cfg, task_type, system_extra,
                                     voice_hint, bool(form_text))
            histories[u.index] = hist
    for u in units:
        _append_absent_decisions(u)

    # 조립 — 표는 코드로, 서술은 단위 본문으로.
    title = spec.get("goal") or spec.get("deliverable") or "과제"
    parts: List[str] = [f"# {title}", ""]
    tables = _assemble_tables(form_text, units) if form_text else ""
    if tables:
        parts += [tables, ""]
    if form_text or len(units) > 1:
        for u in units:
            when = _meta_when(u.meta)
            head = f"{u.mark} 강의명: {u.title or ''}" \
                + (f" / 수강일시: {when}" if when else "")
            parts += [head, "▷ 강의 내용", u.body, ""]
    else:
        parts += [units[0].body if units else ""]
    assembled = _ensure_answerable("\n".join(parts).strip() + "\n", title)
    draft = Draft.from_text(assembled)

    # 종합 리포트 — 단위별 이력을 합산(실패는 조용히 통과시키지 않는다).
    attempts = sum(len(h) for h in histories.values()) or 1
    reasks = sum(max(0, len(h) - 1) for h in histories.values())
    final_errors: List[str] = []
    history: List[List[str]] = []
    for idx, h in sorted(histories.items()):
        history.extend(h)  # 단위별 '전체' 시도 이력(1차 reask 사유 포함)
        if h and h[-1]:
            final_errors += [f"[{idx}] {e}" for e in h[-1]]
    report = GuardReport(passed=not final_errors, attempts=attempts,
                         reasks=reasks, final_errors=final_errors,
                         history=history)
    return draft, report, units


def render_units_diagnostics(units: List[ResponseUnit]) -> str:
    """CLI/리포트용 — 단위·계획·근거 상태 한눈에."""
    from .content_plan import render_plan
    out = []
    for u in units:
        n_ev = len(u.evidence.items) if u.evidence else 0
        chars = measure_length(u.body)[0] if u.body else 0
        out.append(f"{u.mark} {u.title or '(제목 미상)'} — 근거 {n_ev}건 · "
                   f"본문 {chars}자")
        p = render_plan(u)
        if p:
            out.append(p)
    return "\n".join(out)
