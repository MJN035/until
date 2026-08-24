"""P6 — 결정 루프 닫기: finalize(2차 패스) 테스트 (오프라인·mock)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.config import Config
from until.pipeline import run, finalize
from until.report import render_markdown_report
from until.boundary.models import Draft
from until.boundary.resolve import pair_resolved_decisions, render_resolved_block
from until.execution.boundary_guard import BoundaryValidator
from until.execution.drafter import finalize_with_decisions
from until.llm.mock_client import MockClient


def _two_decision_draft() -> Draft:
    return Draft.from_text(
        "서론." * 30 + "\n[[DECISION: 핵심 논지를 어디로 세울지 — 본인 관점 필요]]\n"
        "본론." * 30 + "\n[[DECISION: 결론 톤을 어떻게 할지 — 본인 취향]]\n"
    )


def test_pair_and_render_resolved():
    d = _two_decision_draft()
    pairs = pair_resolved_decisions(d, {1: "감시 자본 관점으로 간다", 2: "조심스럽게"})
    assert [p[0] for p in pairs] == [1, 2]
    assert pairs[0][2] == "감시 자본 관점으로 간다"
    block = render_resolved_block(pairs)
    assert "감시 자본 관점으로 간다" in block and "조심스럽게" in block
    # 결정 성격 태깅 + 성격별 반영 지침(0.5.0).
    assert "[관점·논지]" in block or "[취향·스타일]" in block
    assert "[ 성격별 반영 지침 ]" in block
    # 답 줄들이 지침 섹션보다 앞(mock 파서 계약: 첫 빈 줄+[ 전까지가 답 블록).
    assert block.index("→") < block.index("[ 성격별 반영 지침 ]")
    # 답이 일부만 있으면 그 결정만 짝지어진다.
    only_one = pair_resolved_decisions(d, {2: "조심스럽게"})
    assert [p[0] for p in only_one] == [2]
    print("OK pair/render resolved decisions (+category blend hints)")


def test_finalize_validator_relaxations():
    # finalize 가드: 1인칭 입장 단정 허용 + 결정 0개 허용.
    txt = ("나는 감시 자본 관점이 옳다고 본다. 따라서 이 글의 논지를 그 방향으로 세운다. "
           + "충분한 분량의 본문. " * 20)
    final_v = BoundaryValidator(min_decisions=0, forbid_stance=False)
    assert final_v.validate(Draft.from_text(txt)).passed
    # 기본 가드는 같은 글을 막아야 한다(대조).
    strict = BoundaryValidator(min_decisions=1).validate(Draft.from_text(txt))
    assert not strict.passed
    # 한자/가나 가드는 finalize에서도 유지된다.
    bad = Draft.from_text("資料 기반 최종본. " + "충분한 분량의 본문. " * 20)
    assert not final_v.validate(bad).passed
    print("OK finalize validator relaxations (stance/decisions ok, hanja still blocked)")


def test_finalize_with_decisions_mock():
    d = _two_decision_draft()
    pairs = pair_resolved_decisions(d, {1: "감시 자본 관점으로 간다", 2: "조심스럽게 마무리"})
    final, guard = finalize_with_decisions(
        d, render_resolved_block(pairs), {"deliverable": "에세이"}, MockClient(),
    )
    assert guard.passed
    assert "[[DECISION:" not in final.body and final.n_decisions == 0
    assert "감시 자본 관점으로 간다" in final.body
    assert "조심스럽게 마무리" in final.body
    print("OK finalize with decisions (mock) — answers woven, no markers")


def test_pipeline_finalize_end_to_end():
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    assert res.draft.n_decisions >= 1
    answers = {i + 1: f"내 선택 {i+1}번" for i in range(res.draft.n_decisions)}
    res = finalize(res, answers, cfg)
    assert res.final_draft is not None and res.final_guard.passed
    assert "내 선택 1번" in res.final_draft.body
    # 원래 경계선 초안은 보존된다(나란히 비교 가능).
    assert res.draft.n_decisions >= 1
    print("OK pipeline finalize end-to-end")


def test_finalize_restores_unanswered_markers():
    # mock finalize는 모든 마커를 떨어뜨림 → 미답 결정은 안전장치로 복원되어야 한다.
    d = _two_decision_draft()
    res = run(["examples/sample_assignment.txt"], Config())  # 형식상 Result 필요
    res.draft = d
    # 결정 1만 답하고 2는 미답으로 둔다.
    res = finalize(res, {1: "감시 자본 관점으로 간다"}, Config())
    body = res.final_draft.body
    assert "[[DECISION:" in body, "미답 결정 마커가 복원되어야 함"
    assert "결론 톤" in body                  # 미답 결정(2번)의 노트가 남아 있음
    assert res.final_draft.n_decisions >= 1
    assert "감시 자본 관점으로 간다" in body   # 답한 결정은 녹아 있음
    print("OK finalize restores unanswered decision markers")


def test_finalize_noop_without_answers():
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    res2 = finalize(res, {}, cfg)
    assert res2.final_draft is None  # 답이 없으면 2차 패스 생략
    print("OK finalize is no-op without answers")


def test_report_includes_final_draft():
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    answers = {i + 1: f"내 선택 {i+1}번" for i in range(res.draft.n_decisions)}
    res = finalize(res, answers, cfg)
    report = render_markdown_report(res, backend=cfg.backend)
    assert "## Final Draft" in report
    assert "내 선택 1번" in report
    print("OK report includes final draft")


if __name__ == "__main__":
    for fn in [test_pair_and_render_resolved, test_finalize_validator_relaxations,
               test_finalize_with_decisions_mock, test_pipeline_finalize_end_to_end,
               test_finalize_restores_unanswered_markers,
               test_finalize_noop_without_answers, test_report_includes_final_draft]:
        fn()
    print("\nRESOLVE TESTS PASS")
