"""Execution step — 맥락(수업자료·내 파일) 근거 + 내 말투로 초안 작성, 경계선은 BoundaryGuard로 강제."""
from __future__ import annotations
import json
from typing import List, Optional, Tuple

from ..capture.models import Document
from ..boundary.models import Draft
from ..config import measured_enforce_active
from ..llm.base import LLMClient, SourceDoc
from ..understanding.measured_check import find_ungrounded_measurements
from . import prompts
from .boundary_guard import BoundaryGuard, BoundaryValidator, OnFailAction, GuardReport


def draft_to_boundary(
    docs: List[Document],
    spec: dict,
    llm: LLMClient,
    *,
    context_sources: Optional[List[SourceDoc]] = None,
    voice_hint: str = "",
    max_reasks: int = 2,
    min_decisions: int = 1,
    on_fail: OnFailAction = OnFailAction.REASK,
    system_extra: str = "",
    extra_validators: Optional[list] = None,
) -> Tuple[Draft, GuardReport]:
    # 과제 자료 + (맥락) 수업자료·내 파일을 모두 citation 소스로.
    # 제목은 파일명만(범례와 동일 표기) — 서버 임시 경로가 인라인 [자료N] 라벨로
    # 새어 초안 본문에 그대로 인용되는 것을 막는다.
    from pathlib import Path as _P
    source_docs = [SourceDoc(title=f"과제: {_P(d.source).name}", text=d.text[:6000])
                   for d in docs]
    if context_sources:
        source_docs += context_sources
    spec_json = json.dumps(spec, ensure_ascii=False)

    # 시스템 프롬프트에 (유형 지침 + 말투 지침)을 덧붙임(있을 때만).
    system = prompts.SYSTEM
    if system_extra:
        system += "\n\n" + system_extra
    if voice_hint:
        system += "\n\n" + voice_hint
    base_user = prompts.user_message(spec_json, "(아래 첨부된 자료 문서 참조: [수업자료]/[내 파일] 포함)")

    def produce(errors: List[str], previous: str) -> str:
        user = base_user if not errors else (
            base_user + "\n\n" + prompts.reask_message(previous, errors)
        )
        return llm.complete(system, user, tag="execution", documents=source_docs).text

    guard = BoundaryGuard(
        validators=[BoundaryValidator(min_decisions=min_decisions)]
        + list(extra_validators or []),
        on_fail=on_fail, max_reasks=max_reasks,
    )
    return guard.run(produce)


def _norm_note(s: str) -> str:
    """노트 비교용 정규화 — 공백 접힘 + 소문자. 모델이 미답 마커를 살짝
    리워딩(공백/대소문자)해도 '있음'으로 보고 중복 복원하지 않도록."""
    return " ".join(s.split()).lower()


def _restore_missing_markers(draft: Draft, unanswered_notes: List[str]) -> Draft:
    """finalize가 누락한 '미답 결정' 마커를 복원한다(사람 미판단을 잃지 않도록).

    모델이 답 없는 [[DECISION]]을 가끔 떨어뜨리는 라이브 관측에 대한 결정적 안전장치.
    최종본에 없는 미답 노트만 '남은 결정' 블록으로 덧붙인다.
    정규화 비교로 가벼운 리워딩(공백/대소문자)에 의한 중복 복원을 피한다."""
    present = {_norm_note(d.note) for d in draft.decisions}
    missing = [n for n in unanswered_notes if _norm_note(n) not in present]
    if not missing:
        return draft
    block = "\n".join(f"[[DECISION: {n}]]" for n in missing)
    return Draft.from_text(draft.body.rstrip() + "\n\n## 남은 결정 (사람 판단 필요)\n" + block + "\n")


def finalize_with_decisions(
    draft: Draft,
    resolved_block: str,
    spec: dict,
    llm: LLMClient,
    *,
    context_sources: Optional[List[SourceDoc]] = None,
    voice_hint: str = "",
    max_reasks: int = 2,
    unanswered_notes: Optional[List[str]] = None,
    extra_validators: Optional[list] = None,
) -> Tuple[Draft, GuardReport]:
    """결정 해소 후 2차 패스 — 사람의 답을 본인 말투로 녹여 최종 완성본을 쓴다.

    경계선 초안(`draft`)과 사람의 결정 답변 블록(`resolved_block`)을 받아 Execution을
    한 번 더 돌린다. 사람이 이미 판단을 내렸으므로 가드는 완화한다:
    - min_decisions=len(미답)  : 답한 결정은 본문에 녹지만, 미답 결정 마커는 그 수만큼
      반드시 남아 있어야 통과(reask로 강제). 미답이 없으면 0.
    - forbid_stance=False : 사람이 고른 입장을 1인칭으로 단정 서술 허용.
    한자/가나 혼입·본문 과소 작성 가드는 그대로 유지한다.
    미답 마커는 ① 가드 하한(min_decisions)으로 reask 강제 + ② `_restore_missing_markers`로
    결정적 복원 — 이중 보존.
    """
    source_docs: List[SourceDoc] = []
    if context_sources:
        source_docs += context_sources
    spec_json = json.dumps(spec, ensure_ascii=False)

    system = prompts.FINALIZE_SYSTEM if not voice_hint else (
        prompts.FINALIZE_SYSTEM + "\n\n" + voice_hint
    )
    kept_block = "\n".join(f"[[DECISION: {n}]]" for n in (unanswered_notes or []))
    base_user = prompts.finalize_user_message(
        spec_json, draft.body, resolved_block,
        "(아래 첨부된 자료 문서 참조: [수업자료]/[내 파일] 포함)",
        kept_block=kept_block,
    )

    def produce(errors: List[str], previous: str) -> str:
        user = base_user if not errors else (
            base_user + "\n\n" + prompts.reask_message(previous, errors)
        )
        return llm.complete(system, user, tag="finalize", documents=source_docs).text

    n_unanswered = len(unanswered_notes or [])
    guard = BoundaryGuard(
        validators=[BoundaryValidator(min_decisions=n_unanswered, forbid_stance=False)]
        + list(extra_validators or []),
        on_fail=OnFailAction.REASK, max_reasks=max_reasks,
    )
    final, report = guard.run(produce)
    if unanswered_notes:
        final = _restore_missing_markers(final, unanswered_notes)
    return final, report


def _evidence_texts(docs: List[Document], context_sources: Optional[List[SourceDoc]],
                    spec: dict) -> List[str]:
    """근거 자료 텍스트 모음 — readiness.assess_readiness의 '실측' 판정과 같은
    구성(과제 문서 + 맥락 자료 + spec.requirements)이라야 두 소비부의 판정이 어긋나지 않는다."""
    texts = [d.text for d in docs]
    if context_sources:
        texts += [sd.text for sd in context_sources]
    spec_reqs = (spec or {}).get("requirements")
    if isinstance(spec_reqs, list):
        texts += [str(x) for x in spec_reqs]
    return texts


def _substitute_ungrounded_measurements(body: str, evidence_texts: List[str]) -> str:
    """근거 없는 실측 수치 표현을 결정적으로 [[DECISION: ...]] 마커로 치환(LLM 0).

    reask로도 지워지지 않은 근거 없는 수치가 그대로 제출되는 것을 막는 마지막
    안전망. measured_check의 탐지 정규식(비공개 심볼)을 그대로 재사용해 판정
    기준을 어긋나지 않게 한다 — measured_check.py 자체(탐지 로직)는 고치지 않고
    import만 한다.
    """
    from ..understanding.measured_check import _DECISION_RE, _NUM_RE, _PATTERNS

    decision_spans = [m.span() for m in _DECISION_RE.finditer(body)]

    def _in_decision(start: int, end: int) -> bool:
        return any(ds <= start and end <= de for ds, de in decision_spans)

    evidence_joined = "".join(t or "" for t in (evidence_texts or []))
    evidence_nums = set(_NUM_RE.findall(evidence_joined))

    raw_matches = []
    for pat in _PATTERNS:
        for m in pat.finditer(body):
            if _in_decision(*m.span()):
                continue
            raw_matches.append(m)
    raw_matches.sort(key=lambda m: (m.start(), -(m.end() - m.start())))
    accepted = []
    for m in raw_matches:
        if any(a.start() <= m.start() and m.end() <= a.end() for a in accepted):
            continue
        accepted.append(m)

    to_replace = []
    for m in accepted:
        num_m = _NUM_RE.search(m.group())
        if not num_m or num_m.group() in evidence_nums:
            continue
        to_replace.append(m)

    out = body
    for m in sorted(to_replace, key=lambda mm: mm.start(), reverse=True):
        token = m.group().strip()
        marker = f"[[DECISION: 실측값 필요 — {token} 근거 없음]]"
        out = out[:m.start()] + marker + out[m.end():]
    return out


def _run_boundary_validators(
    candidate: Draft, min_decisions: int, extra_validators: Optional[list],
) -> Tuple[bool, List[str]]:
    """draft_to_boundary가 쓰는 것과 같은 검증기 세트(BoundaryValidator +
    extra_validators)를 돌려 (통과 여부, 에러 목록)을 정직하게 계산한다.

    enforce_measured_grounding의 reask/치환 후보가 실제로 경계선 규칙(분량·
    결정 지점 수·입장 단정·한자·JSON 덤프 등)을 지키는지 재검증하기 위함 —
    측정값 근거만 보고 guard.passed를 그대로 베끼면 빈 응답 같은 퇴화 결과가
    통과로 둔갑한다."""
    validators = [BoundaryValidator(min_decisions=min_decisions)] + list(extra_validators or [])
    errors: List[str] = []
    for v in validators:
        errors.extend(v.validate(candidate).errors)
    return not errors, errors


def enforce_measured_grounding(
    docs: List[Document],
    spec: dict,
    draft: Draft,
    guard: GuardReport,
    llm: LLMClient,
    *,
    context_sources: Optional[List[SourceDoc]] = None,
    voice_hint: str = "",
    system_extra: str = "",
    strategy: str = "",
    stage: str = "",
    min_decisions: int = 1,
    extra_validators: Optional[list] = None,
) -> Tuple[Draft, GuardReport]:
    """legacy 경로 사후 차단 — 근거 없는 실측 수치를 1회 reask, 그래도 남으면 결정적 치환.

    로드맵 Tier2-6: legacy 경로는 지금까지 measured_ban 프롬프트 지침뿐이라 모델이
    무시하면 지어낸 수치가 그대로 초안에 남았다. draft_to_boundary가 이미 통과시킨
    초안을 measured_check(결정적)로 다시 검사해, 발견되면 (1) 발견 목록을 넣어 딱
    1회만 reask — 기존 BoundaryGuard의 다회 reask 루프와 별개인 단발 보정이다.
    (2) reask 응답은 **다시 BoundaryValidator(+extra_validators)로 검증**한다 —
    reask가 경계선 규칙을 어기면(빈 응답·결정 마커 소실 등) 채택하지 않는다.
    측정값 근거가 해소됐고 경계선 검증도 통과해야만 reask 결과를 쓴다. 둘 중
    하나라도 실패하면 LLM을 더 부르지 않고, **이미 검증을 통과한 원본 초안**에
    기계적으로 [[DECISION: ...]] 마커를 치환해 돌려준다(경계선 철학 — 근거
    없으면 빈칸). 최종 GuardReport.passed는 실제로 반환하는 초안이 검증기를
    통과했는지를 그대로 반영한다(guard.passed를 그대로 베끼지 않는다) — 빈
    응답이 조용히 "통과"로 둔갑하는 것을 막는다.
    strategy/stage가 비활성(hdl_lab도 lab_report_cycle(result)도 아님)이면
    find_ungrounded_measurements가 항상 빈 리스트라 아무 일도 하지 않는다.
    UNTIL_MEASURED_ENFORCE=0이면 기존(경고만) 동작으로 되돌아가 아무 것도 하지 않는다.
    """
    if not measured_enforce_active():
        return draft, guard

    evidence = _evidence_texts(docs, context_sources, spec)
    ungrounded = find_ungrounded_measurements(
        draft.body, evidence, strategy=strategy, stage=stage)
    if not ungrounded:
        return draft, guard

    from pathlib import Path as _P
    errors = [f'근거 없는 실측 수치: "{s}" — 제공 자료 어디에도 없다. 지우고 '
              "[[DECISION: 실측값 입력]]으로 남겨라." for s in ungrounded]
    source_docs = [SourceDoc(title=f"과제: {_P(d.source).name}", text=d.text[:6000])
                   for d in docs]
    if context_sources:
        source_docs += context_sources
    spec_json = json.dumps(spec, ensure_ascii=False)
    system = prompts.SYSTEM
    if system_extra:
        system += "\n\n" + system_extra
    if voice_hint:
        system += "\n\n" + voice_hint
    base_user = prompts.user_message(
        spec_json, "(아래 첨부된 자료 문서 참조: [수업자료]/[내 파일] 포함)")
    user = base_user + "\n\n" + prompts.reask_message(draft.body, errors)
    text = llm.complete(system, user, tag="execution", documents=source_docs).text
    reasked = Draft.from_text(text)

    history = guard.history + [list(errors)]
    still = find_ungrounded_measurements(
        reasked.body, evidence, strategy=strategy, stage=stage)
    if not still:
        ok, verr = _run_boundary_validators(reasked, min_decisions, extra_validators)
        if ok:
            return reasked, GuardReport(
                passed=True, attempts=guard.attempts + 1, reasks=guard.reasks + 1,
                final_errors=[], history=history)
        # reask가 근거 문제는 해소했지만 경계선 규칙을 어겼다(예: 결정 마커
        # 소실) — 이 결과는 채택하지 않고 원본에 결정적 치환으로 폴백한다.

    # reask가 여전히 근거 없는 수치를 내거나 경계선 검증에 실패하면(빈 응답
    # 등 퇴화 포함) 그 결과를 버리고, 이미 검증을 통과한 **원본** 초안에만
    # 결정적 치환을 적용한다 — 퇴화된 reask 응답이 초안을 대체하지 않는다.
    fixed_body = _substitute_ungrounded_measurements(draft.body, evidence)
    fixed = Draft.from_text(fixed_body)
    ok, verr = _run_boundary_validators(fixed, min_decisions, extra_validators)
    return fixed, GuardReport(
        passed=ok, attempts=guard.attempts + 1, reasks=guard.reasks + 1,
        final_errors=[] if ok else verr, history=history)
