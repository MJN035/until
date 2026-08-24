"""제출 게이트 — 결정적 안전 코어(네트워크·LLM 0).

작성까지 끝낸 최종본을 사람 확인 후 Canvas에 제출하기 전, 지어낸 수치·미완성
텍스트·마감 지남 등을 하드 블록으로 걸러 낸다. nonce 발급의 원장 쓰기만
submit_nonce에 위임하고, 판정 자체는 순수 함수다.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import List, Optional

from ..config import measured_enforce_active
from .submit_nonce import issue_nonce


@dataclass(frozen=True)
class GateFinding:
    code: str
    message: str


@dataclass(frozen=True)
class SubmitTarget:
    course_id: str
    assignment_id: str
    submission_type: str
    base_url: str


@dataclass(frozen=True)
class SubmissionPlan:
    allowed: bool
    blocks: List[GateFinding]
    warnings: List[GateFinding]
    content: str
    target: SubmitTarget
    content_hash: str
    confirm_nonce: str = ""


def submission_content(result) -> str:
    """제출될 본문 — 최종본(final_draft) 우선, 없으면 초안(draft)."""
    final = getattr(result, "final_draft", None)
    if final is not None and getattr(final, "body", None):
        return final.body
    return getattr(getattr(result, "draft", None), "body", "") or ""


def content_hash(content: str, target: SubmitTarget) -> str:
    """본문+대상 바인딩 해시(nonce·감사용). 본문 1바이트만 바뀌어도 달라진다."""
    key = (f"{content}|{target.course_id}|{target.assignment_id}"
           f"|{target.submission_type}")
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _readiness_warn(result, label: str) -> bool:
    """readiness 항목 label이 warn이면 True(마감·분량·양식·인용 게이트용)."""
    try:
        from ..readiness import assess_readiness
        for it in assess_readiness(result).items:
            if it.label == label and it.status == "warn":
                return True
    except Exception:
        pass
    return False


def build_submission_plan(result, assignment, *,
                          submission_type: str = "online_text_entry",
                          base_url: str = "", allowed_submission_types=None,
                          today=None, nonce: Optional[str] = None,
                          nonce_path=None, issue: bool = True,
                          evidence_texts: Optional[List[str]] = None,
                          control_report=None) -> SubmissionPlan:
    """제출 전 게이트 — 하드 블록/경고 판정 + 본문·해시·nonce 산출(결정적).

    evidence_texts: 로드맵 Tier2-6 — 실측 사후 검증기(measured_check)를 본문에
    직접 돌려 근거 없는 수치를 하드 블록으로 잡는다. 옵션 인자(기본 None)로
    후방 호환 — 호출부가 넘기지 않으면 이 추가 판정만 건너뛰고 기존 동작 그대로."""
    import datetime
    today = today or datetime.date.today()
    content = submission_content(result)
    target = SubmitTarget(
        course_id=str(getattr(assignment, "course_id", "") or ""),
        assignment_id=str(getattr(assignment, "id", "") or ""),
        submission_type=submission_type, base_url=base_url)
    spec = getattr(result, "spec", {}) or {}
    route = getattr(result, "assignment_route", None)
    strategy = getattr(route, "strategy", "") or ""
    stage = getattr(route, "stage", "") or ""
    guard = getattr(result, "final_guard", None) or getattr(result, "guard", None)

    blocks: List[GateFinding] = []
    if control_report is not None:
        for finding in getattr(control_report, "findings", ()):
            if getattr(finding, "severity", "") == "block":
                blocks.append(GateFinding(
                    f"control:{getattr(finding, 'code', 'blocked')}",
                    str(getattr(finding, "message", "관제실 점검 미통과"))))
    # 🚫 수치 날조 금지 — 실측 근거 없는 hdl_lab·결과보고서
    measured_active = (strategy == "hdl_lab"
                       or (strategy == "lab_report_cycle" and stage == "result"))
    if measured_active and spec.get("material_gap"):
        blocks.append(GateFinding(
            "measured_ban", "실측 근거가 없어 수치·파형이 지어내진 상태 — 제출 불가"))
    if measured_active and evidence_texts is not None and measured_enforce_active():
        # 로드맵 Tier2-6 — material_gap과 별개로, 본문에 근거 없는 수치 표현이
        # 남아 있으면(measured_check 결정적 검사) 그 자체로 하드 블록.
        from ..understanding.measured_check import find_ungrounded_measurements
        ungrounded = find_ungrounded_measurements(
            content, evidence_texts, strategy=strategy, stage=stage)
        if ungrounded:
            blocks.append(GateFinding(
                "measured_ban",
                f"근거 없는 실측 수치 {len(ungrounded)}건이 본문에 남아 있어 제출 불가 — "
                "지우고 [[DECISION: 실측값 입력]]으로 남기세요"))
    if spec.get("integrity_gate"):
        blocks.append(GateFinding(
            "integrity_gate", "자필·손글씨 규정 과제 — 자동 제출 대상 아님(직접 제출)"))
    # 민감·고위험(사과·거절·갈등) — 되돌릴 수 없는 종류의 글은 자동 제출하지 않는다.
    # 초안 생성은 이미 끝나 있고, 여기서 막는 것은 '사람 눈을 거치지 않은 발송'뿐이다.
    if getattr(result, "needs_approval", False):
        kinds = " · ".join(getattr(result, "approval_kinds", None) or []) or "민감"
        blocks.append(GateFinding(
            "needs_human_approval",
            f"{kinds} 성격의 글입니다 — 되돌리기 어려우니 직접 읽고 확인한 뒤 제출하세요"))
    if guard is not None and not getattr(guard, "passed", True):
        blocks.append(GateFinding("guard_failed", "경계선 가드 미통과 — 제출 불가"))
    dl = getattr(result, "deadline", None)
    if dl is not None and dl.days_from(today) < 0:
        blocks.append(GateFinding("deadline_passed", "마감이 지났습니다 — 제출 전 확인 필요"))
    if _readiness_warn(result, "분량"):
        blocks.append(GateFinding("length_unmet", "분량 요건 미달/초과 — 제출 불가"))
    if _readiness_warn(result, "양식"):
        blocks.append(GateFinding("length_unmet", "양식 구조 불일치 — 제출 불가"))
    if "[[DECISION" in content:
        blocks.append(GateFinding(
            "raw_decision_marker", "본문에 미완성 결정 마커가 남아 있습니다 — 제출 불가"))
    if not target.course_id or not target.assignment_id:
        blocks.append(GateFinding("assignment_mismatch", "제출 대상 과제를 확정할 수 없습니다"))
    if allowed_submission_types is not None and submission_type not in allowed_submission_types:
        blocks.append(GateFinding(
            "type_unsupported", f"이 과제는 {submission_type} 제출을 받지 않습니다"))

    warnings: List[GateFinding] = []
    orig_draft = getattr(result, "draft", None)
    if orig_draft is not None and getattr(orig_draft, "n_decisions", 0) > 0:
        warnings.append(GateFinding(
            "unresolved_decisions",
            "이 과제에는 당신의 판단(결정)이 필요했습니다 — 최종본에 제대로 반영됐는지 확인 후 제출하세요"))
    if _readiness_warn(result, "인용") or _readiness_warn(result, "근거"):
        warnings.append(GateFinding("citation_missing", "자료를 줬는데 본문 인용이 없습니다"))
    if getattr(assignment, "submitted", False):
        warnings.append(GateFinding("already_submitted", "이미 제출된 과제입니다 — 재제출 확인"))

    chash = content_hash(content, target)
    allowed = not blocks
    # 허용되고 issue=True일 때만 확인 nonce를 발급한다(차단 상태·순수 미리보기
    # 렌더에서는 발급 자체를 안 함 — issue=False는 원장에 아무것도 안 씀).
    token = issue_nonce(chash, path=nonce_path, token=nonce) if (allowed and issue) else ""
    # length_unmet 중복 코드 정리(분량·양식 둘 다 걸리면 1건으로).
    seen, dedup = set(), []
    for b in blocks:
        if b.code in seen:
            continue
        seen.add(b.code)
        dedup.append(b)
    return SubmissionPlan(allowed, dedup, warnings, content, target, chash, token)
