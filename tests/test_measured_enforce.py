"""수치 날조 검증기 경고→차단 승격(로드맵 Tier2-6) 테스트 (오프라인·결정적).

범위:
  (a) readiness fail 승격 — find_ungrounded_measurements 발견 시 "실측" 항목이
      warn이 아니라 fail로 승격된다(발견 0이면 기존과 동일).
  (b) legacy 파이프라인 사후 차단 — execution.drafter.enforce_measured_grounding이
      1회 reask 후에도 근거 없는 수치가 남으면 결정적으로 [[DECISION: ...]]로 치환.
      reask 결과는 BoundaryValidator(+extra_validators)로 재검증한다 — 빈/퇴화
      응답이나 결정 마커 소실은 채택하지 않고 원본 초안의 치환 결과로 폴백한다
      (guard.passed를 그대로 베끼지 않고 실제 반환값을 정직하게 재계산).
  (c) 제출 게이트 regex 차단 — submission_gate.build_submission_plan(evidence_texts=...)
      가 본문 regex 발견을 measured_ban 하드 블록으로 잡는다(옵션 인자, 후방 호환).
  (d) UNTIL_MEASURED_ENFORCE=0 → 세 소비부 모두 기존(경고만) 동작으로 복귀.
  (e) 비활성 전략(essay 등)은 세 소비부 모두 무영향.
"""
import os
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.pipeline import Result
from until.boundary.models import Draft
from until.execution.boundary_guard import GuardReport
from until.readiness import assess_readiness
from until.context.assignment_router import AssignmentRoute
from until.llm.base import SourceDoc, LLMResult
from until.capture.models import Document
from until.execution.drafter import enforce_measured_grounding
from until.execution.submission_gate import build_submission_plan
from until.understanding.measured_check import find_ungrounded_measurements

_BODY = "합성 결과 최대 주파수 250 MHz, LUT 1200개 사용."

# BoundaryValidator 기본 min_body_chars=200을 실제로 넘기는 필러 — enforce_
# measured_grounding이 이제 reask/치환 후보를 진짜 BoundaryValidator로
# 재검증하므로, "이미 유효했던 원본"을 대표하는 fixture는 200자를 넘겨야
# min_body_chars 탈락과 의도한 실패(결정 마커 소실 등)가 섞이지 않는다.
_FILLER = "자료에서 도출되는 설계 근거와 검증 절차를 순서대로 정리했다. " * 8

# 이미 검증(BoundaryValidator: 분량 충분 + 결정 1개 이상)을 통과했다고 볼 수
# 있는 "원본" 초안 — 근거 없는 실측 수치 2건을 포함한다.
_VALID_BODY = (
    "## 설계 개요\n" + _FILLER +
    "\n## 합성 결과\n합성 결과 최대 주파수 250 MHz, LUT 1200개 사용.\n"
    "## 고찰\n[[DECISION: 설계 선택 근거를 어떻게 정리할지 — 본인 판단 필요]]\n"
)

# reask가 "제대로" 고친 경우 — 근거 없는 수치는 빠지고 결정 마커로 남았다.
_FIXED_LONG = (
    "## 설계 개요\n" + _FILLER +
    "\n## 합성 결과\n[[DECISION: 실측값 입력 — 최대 주파수·LUT 사용량]]\n"
)

# reask가 수치는 지웠지만(근거 문제는 해소) 결정 마커를 하나도 안 남긴 경우 —
# min_decisions>=1 위반으로 거부돼야 한다.
_NO_DECISION_LONG = (
    "## 설계 개요\n" + _FILLER +
    "\n## 합성 결과\n측정값은 아직 확정되지 않았습니다.\n"
)


def _clear_env():
    os.environ.pop("UNTIL_MEASURED_ENFORCE", None)


def _doc(text):
    return Document(source="과제.txt", kind="text", text=text)


# ── (a) readiness fail 승격 ──────────────────────────────────────────────

def test_readiness_fail_promotion_default():
    _clear_env()
    d = Draft.from_text(_BODY * 3)
    g = GuardReport(passed=True, attempts=1, reasks=0)
    route = AssignmentRoute(strategy="hdl_lab", reason="t", required_evidence=(), stage="result")
    res = Result(documents=[], spec={"title": "T"}, draft=d, guard=g,
                 source_docs=[], assignment_route=route)
    r = assess_readiness(res)
    items = {i.label: i for i in r.items}
    assert items["실측"].status == "fail", items
    assert any(i.status == "fail" for i in r.warnings), "fail도 경고 집계에 포함돼야 함"
    print("OK readiness: 실측 발견 시 fail로 승격(기본값)")


def test_readiness_no_item_when_grounded():
    _clear_env()
    d = Draft.from_text(_BODY * 3)
    g = GuardReport(passed=True, attempts=1, reasks=0)
    route = AssignmentRoute(strategy="hdl_lab", reason="t", required_evidence=(), stage="result")
    res = Result(documents=[], spec={"title": "T"}, draft=d, guard=g,
                 source_docs=[SourceDoc(title="합성 리포트",
                                        text="최대 주파수 250MHz, LUT 1200개 확인됨.")],
                 assignment_route=route)
    r = assess_readiness(res)
    assert not any(i.label == "실측" for i in r.items), "발견 0건이면 기존과 동일(항목 없음)"
    print("OK readiness: 근거 있으면 실측 항목 없음(발견 0건은 기존과 동일)")


# ── (b) legacy 파이프라인 사후 차단 ──────────────────────────────────────

class _FakeLLM:
    def __init__(self, response_text):
        self.response_text = response_text
        self.calls = 0

    def complete(self, system, user, *, tag="", json=False, schema=None,
                documents=None, cache=True):
        self.calls += 1
        return LLMResult(text=self.response_text, backend="fake")


def test_legacy_reask_still_ungrounded_falls_back_to_substituting_original():
    _clear_env()
    draft = Draft.from_text(_VALID_BODY)
    guard = GuardReport(passed=True, attempts=1, reasks=0)
    # reask 응답도 여전히 근거 없는 수치를 낸다(원본 그대로 에코) — 결정적
    # 치환 경로로 강제 진입. 원본이 이미 유효했으므로 치환 후 가드는 통과.
    fake = _FakeLLM(_VALID_BODY)
    new_draft, new_guard = enforce_measured_grounding(
        [_doc("과제 설명뿐, 실측 근거 없음")], {"title": "T"}, draft, guard, fake,
        strategy="hdl_lab", stage="", min_decisions=1)
    assert fake.calls == 1, "reask는 정확히 1회여야 한다"
    assert "[[DECISION: 실측값 필요 — 250 MHz 근거 없음]]" in new_draft.body
    assert "[[DECISION: 실측값 필요 — LUT 1200개 근거 없음]]" in new_draft.body
    assert "## 설계 개요" in new_draft.body, "비수치 문장은 보존돼야 함"
    assert find_ungrounded_measurements(
        new_draft.body, ["과제 설명뿐"], strategy="hdl_lab", stage="") == [], \
        "치환 후에는 재검사에서도 발견 0건이어야 함"
    assert new_guard.passed is True, "원본이 이미 유효했으므로 치환 후에도 가드는 통과해야 함"
    assert new_guard.attempts == guard.attempts + 1
    assert new_guard.reasks == guard.reasks + 1
    print("OK legacy: 1회 reask 후에도 남으면 결정적 치환(가드 정직하게 통과)")


def test_legacy_reask_success_uses_reasked_draft_without_substitution():
    _clear_env()
    draft = Draft.from_text(_VALID_BODY)
    guard = GuardReport(passed=True, attempts=1, reasks=0)
    fake = _FakeLLM(_FIXED_LONG)
    new_draft, new_guard = enforce_measured_grounding(
        [_doc("과제 설명뿐")], {"title": "T"}, draft, guard, fake,
        strategy="hdl_lab", stage="", min_decisions=1)
    assert fake.calls == 1
    assert new_draft.body == _FIXED_LONG, "reask로 근거·경계선 검증 모두 통과하면 치환 없이 그대로 쓴다"
    assert new_guard.passed is True
    print("OK legacy: reask로 해결되면(경계선 재검증도 통과) 그 결과를 그대로 쓴다(치환 없음)")


def test_legacy_empty_reask_response_does_not_get_accepted():
    # Important #1 회귀 테스트 — 공급자가 빈 문자열을 반환하면(퇴화 응답)
    # find_ungrounded_measurements("")가 빈 body에 단락(short-circuit)돼
    # "still=[]"이 되지만, BoundaryValidator 재검증이 빈 본문을 잡아내
    # 빈 초안이 채택되지 않고 원본 초안의 치환 결과로 폴백해야 한다.
    _clear_env()
    draft = Draft.from_text(_VALID_BODY)
    guard = GuardReport(passed=True, attempts=1, reasks=0)
    fake = _FakeLLM("")  # 퇴화된 빈 응답
    new_draft, new_guard = enforce_measured_grounding(
        [_doc("과제 설명뿐")], {"title": "T"}, draft, guard, fake,
        strategy="hdl_lab", stage="", min_decisions=1)
    assert fake.calls == 1
    assert new_draft.body != "", "빈 응답이 그대로 채택되면 안 된다"
    assert "[[DECISION: 실측값 필요 — 250 MHz 근거 없음]]" in new_draft.body
    assert "[[DECISION: 실측값 필요 — LUT 1200개 근거 없음]]" in new_draft.body
    assert new_guard.passed is True, \
        "원본을 치환한 결과는 실제로 유효하므로 가드는 이를 정직하게 반영해야 함"
    print("OK legacy: 빈/퇴화 reask 응답은 채택되지 않고 원본 치환으로 폴백(가드 계약 보존)")


def test_legacy_reask_missing_decision_markers_rejected():
    # reask가 근거 없는 수치는 없앴지만(측정 문제는 해소) 결정 마커를 하나도
    # 남기지 않았다 — min_decisions=1 위반이라 채택되면 안 되고, 원본 초안의
    # 결정적 치환 결과로 폴백해야 한다.
    _clear_env()
    draft = Draft.from_text(_VALID_BODY)
    guard = GuardReport(passed=True, attempts=1, reasks=0)
    fake = _FakeLLM(_NO_DECISION_LONG)
    new_draft, new_guard = enforce_measured_grounding(
        [_doc("과제 설명뿐")], {"title": "T"}, draft, guard, fake,
        strategy="hdl_lab", stage="", min_decisions=1)
    assert fake.calls == 1
    assert new_draft.body != _NO_DECISION_LONG, \
        "결정 마커가 없는 reask 결과는 채택되면 안 된다(min_decisions 위반)"
    assert "[[DECISION: 실측값 필요 — 250 MHz 근거 없음]]" in new_draft.body
    assert "[[DECISION: 실측값 필요 — LUT 1200개 근거 없음]]" in new_draft.body
    assert new_guard.passed is True
    print("OK legacy: reask가 결정 마커를 없애면(min_dec 위반) 원본 치환으로 폴백")


def test_legacy_no_llm_call_when_already_grounded():
    _clear_env()
    body = "설계 개요만 있고 근거 없는 수치 표현은 없다."
    draft = Draft.from_text(body)
    guard = GuardReport(passed=True, attempts=1, reasks=0)
    fake = _FakeLLM(body)
    new_draft, new_guard = enforce_measured_grounding(
        [_doc("근거 텍스트")], {"title": "T"}, draft, guard, fake,
        strategy="hdl_lab", stage="")
    assert fake.calls == 0, "발견 0건이면 LLM을 호출하지 않는다(mock 백엔드 계약)"
    assert new_draft is draft and new_guard is guard
    print("OK legacy: 발견 0건이면 LLM 미호출·무변경(mock 백엔드 계약)")


# ── (c) 제출 게이트 regex 차단 ───────────────────────────────────────────

class _GDraft:
    def __init__(self, body):
        self.body = body

    @property
    def n_decisions(self):
        import re
        return len(re.findall(r"\[\[DECISION:", self.body))


class _GGuard:
    def __init__(self, passed=True):
        self.passed = passed


class _GRoute:
    def __init__(self, strategy, stage=""):
        self.strategy, self.stage = strategy, stage


class _GRef:
    def __init__(self, aid="202", cid="101"):
        self.id, self.course_id, self.submitted = aid, cid, False


class _GResult:
    def __init__(self, body, strategy="hdl_lab", stage="", material_gap=False):
        self.draft = _GDraft(body)
        self.final_draft = _GDraft(body)
        self.spec = {"material_gap": True} if material_gap else {}
        self.guard = _GGuard(True)
        self.final_guard = _GGuard(True)
        self.assignment_route = _GRoute(strategy, stage)
        self.deadline = None
        self.length_target = None


def _plan(result, evidence_texts):
    import tempfile
    import datetime as _dt
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        return build_submission_plan(
            result, _GRef(), base_url="https://e", today=_dt.date(2026, 8, 16),
            nonce="t", nonce_path=Path(d) / "n.jsonl", evidence_texts=evidence_texts)


_GATE_BODY = _BODY + " 결정 없이도 충분한 길이의 본문."


def test_gate_blocks_on_ungrounded_regex_findings():
    _clear_env()
    plan = _plan(_GResult(_GATE_BODY), evidence_texts=["과제 설명뿐, 실측 근거 없음"])
    assert plan.allowed is False
    assert any(b.code == "measured_ban" for b in plan.blocks)
    print("OK gate: evidence_texts 대비 근거 없는 수치 발견 시 차단")


def test_gate_allows_when_grounded():
    _clear_env()
    plan = _plan(_GResult(_GATE_BODY),
                evidence_texts=["최대 주파수 250MHz, LUT 1200개 확인됨."])
    assert not any(b.code == "measured_ban" for b in plan.blocks)
    print("OK gate: 근거 있으면 measured_ban 없음")


def test_gate_backward_compatible_without_evidence_texts():
    _clear_env()
    plan = _plan(_GResult(_GATE_BODY), evidence_texts=None)
    assert not any(b.code == "measured_ban" for b in plan.blocks), \
        "evidence_texts 미전달(기본 None)이면 regex 차단은 건너뛴다(후방 호환)"
    print("OK gate: evidence_texts 생략 시 기존 동작(regex 차단 없음)")


def test_gate_material_gap_still_blocks():
    _clear_env()
    plan = _plan(_GResult("짧은 본문", material_gap=True), evidence_texts=None)
    assert any(b.code == "measured_ban" for b in plan.blocks)
    print("OK gate: material_gap 차단은 그대로 유지")


# ── (d) UNTIL_MEASURED_ENFORCE=0 → 기존(경고만) 동작 ────────────────────

def test_env_escape_hatch_reverts_all_consumers():
    os.environ["UNTIL_MEASURED_ENFORCE"] = "0"
    try:
        d = Draft.from_text(_BODY * 3)
        g = GuardReport(passed=True, attempts=1, reasks=0)
        route = AssignmentRoute(strategy="hdl_lab", reason="t", required_evidence=(), stage="result")
        res = Result(documents=[], spec={"title": "T"}, draft=d, guard=g,
                     source_docs=[], assignment_route=route)
        r = assess_readiness(res)
        assert {i.label: i for i in r.items}["실측"].status == "warn"

        draft2 = Draft.from_text(_BODY)
        guard2 = GuardReport(passed=True, attempts=1, reasks=0)
        fake = _FakeLLM(_BODY)
        new_draft, new_guard = enforce_measured_grounding(
            [_doc("근거 없음")], {"title": "T"}, draft2, guard2, fake,
            strategy="hdl_lab", stage="")
        assert fake.calls == 0
        assert new_draft is draft2 and new_guard is guard2

        plan = _plan(_GResult(_GATE_BODY), evidence_texts=["근거 없음"])
        assert not any(b.code == "measured_ban" for b in plan.blocks)
    finally:
        os.environ.pop("UNTIL_MEASURED_ENFORCE", None)
    print("OK env=0: readiness/legacy reask/gate 세 소비부 모두 기존(경고만) 동작으로 복귀")


# ── (e) 비활성 전략(essay 등) 무영향 ─────────────────────────────────────

def test_inactive_strategy_no_effect():
    _clear_env()
    d = Draft.from_text(_BODY * 3)
    g = GuardReport(passed=True, attempts=1, reasks=0)
    route = AssignmentRoute(strategy="essay", reason="t", required_evidence=(), stage="")
    res = Result(documents=[], spec={"title": "T"}, draft=d, guard=g,
                 source_docs=[], assignment_route=route)
    r = assess_readiness(res)
    assert not any(i.label == "실측" for i in r.items)

    draft2 = Draft.from_text(_BODY)
    guard2 = GuardReport(passed=True, attempts=1, reasks=0)
    fake = _FakeLLM(_BODY)
    new_draft, new_guard = enforce_measured_grounding(
        [_doc("근거 없음")], {"title": "T"}, draft2, guard2, fake,
        strategy="essay", stage="")
    assert fake.calls == 0
    assert new_draft is draft2 and new_guard is guard2

    plan = _plan(_GResult(_GATE_BODY, strategy="essay"), evidence_texts=["근거 없음"])
    assert not any(b.code == "measured_ban" for b in plan.blocks)
    print("OK 비활성 전략(essay 등)은 세 소비부 모두 무영향")


if __name__ == "__main__":
    test_readiness_fail_promotion_default()
    test_readiness_no_item_when_grounded()
    test_legacy_reask_still_ungrounded_falls_back_to_substituting_original()
    test_legacy_reask_success_uses_reasked_draft_without_substitution()
    test_legacy_empty_reask_response_does_not_get_accepted()
    test_legacy_reask_missing_decision_markers_rejected()
    test_legacy_no_llm_call_when_already_grounded()
    test_gate_blocks_on_ungrounded_regex_findings()
    test_gate_allows_when_grounded()
    test_gate_backward_compatible_without_evidence_texts()
    test_gate_material_gap_still_blocks()
    test_env_escape_hatch_reverts_all_consumers()
    test_inactive_strategy_no_effect()
    print("\nMEASURED ENFORCE TESTS PASS")
