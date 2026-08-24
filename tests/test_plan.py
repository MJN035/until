# 체크포인트 플랜 — 볼륨 게이트·기간별 단계 수·날짜 단조·유형 문구·렌더(결정적·LLM 0).
import sys
import pathlib
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.plan import (
    build_checkpoint_plan, render_plan_markdown, plan_for_result, _is_volume)
from until.understanding.length_target import LengthTarget

TODAY = date(2026, 7, 24)


def test_volume_gate():
    # 분량 정보 없음 → 마감이 멀어도 플랜 없음(사용자 피드백: 간단한 에세이는
    # 체크포인트 대상이 아님 — 마감 여유는 게이트가 아니라 단계 수에만 쓴다).
    assert build_checkpoint_plan(date(2026, 7, 26), "essay", None, today=TODAY) is None
    assert build_checkpoint_plan(date(2026, 8, 14), "essay", None, today=TODAY) is None
    # 분량 작음(800자) → 마감 멀어도 미달.
    small = LengthTarget(unit="자", max=800)
    assert build_checkpoint_plan(date(2026, 8, 14), "essay", small, today=TODAY) is None
    assert not _is_volume(21, small)
    # 분량 크면(3000자) 마감 임박이어도 볼륨.
    lt = LengthTarget(unit="자", min=3000)
    assert build_checkpoint_plan(date(2026, 7, 27), "essay", lt, today=TODAY) is not None
    # 페이지·매 단위 하한.
    assert _is_volume(None, LengthTarget(unit="페이지", min=5))
    assert not _is_volume(None, LengthTarget(unit="페이지", min=2))
    # 지난 마감은 플랜 무의미.
    assert build_checkpoint_plan(date(2026, 7, 1), "essay", lt, today=TODAY) is None
    print("OK volume gate (big length only / small stays clean / past deadline)")


def test_cp_counts_and_dates():
    # 21일 → 4단계, 날짜 단조 증가, 마지막=마감일. (볼륨: 3000자)
    due = date(2026, 8, 14)
    BIG = LengthTarget(unit="자", min=3000)
    p = build_checkpoint_plan(due, "essay", BIG, today=TODAY)
    assert len(p.checkpoints) == 4
    ds = [c.due for c in p.checkpoints]
    assert all(a < b for a, b in zip(ds, ds[1:], strict=False)) and ds[-1] == due
    # 10일 → 3단계(앞 두 단계 병합).
    p3 = build_checkpoint_plan(date(2026, 8, 3), "essay", BIG, today=TODAY)
    assert len(p3.checkpoints) == 3 and p3.checkpoints[-1].due == date(2026, 8, 3)
    # 임박(3일)+큰 분량 → 2단계.
    p2 = build_checkpoint_plan(date(2026, 7, 27), "essay",
                               LengthTarget(unit="자", min=3000), today=TODAY)
    assert len(p2.checkpoints) == 2
    # 마감 없음+큰 분량 → 날짜 없는 3단계.
    pn = build_checkpoint_plan(None, "essay", LengthTarget(unit="자", min=3000), today=TODAY)
    assert len(pn.checkpoints) == 3 and all(c.due is None for c in pn.checkpoints)
    assert pn.checkpoints[0].date_label() == "날짜 자유"
    print("OK checkpoint counts (4/3/2/dateless) + monotonic dates + last=deadline")


def test_type_specific_steps():
    due = date(2026, 8, 14)
    BIG = LengthTarget(unit="자", min=3000)
    essay = build_checkpoint_plan(due, "essay", BIG, today=TODAY)
    pres = build_checkpoint_plan(due, "presentation", BIG, today=TODAY)
    code = build_checkpoint_plan(due, "code", BIG, today=TODAY)
    assert "자료" in essay.checkpoints[0].title
    assert any("리허설" in c.title for c in pres.checkpoints)
    assert any("검산" in c.title for c in code.checkpoints)
    # 모든 단계에 3요소(until 몫·내 몫·통과 조건)가 있다 — 경계선의 시간축 확장.
    for c in essay.checkpoints:
        assert c.until_does and c.you_do and c.done_when
    print("OK type-specific steps + until/me/pass triplet")


def test_render_and_result_integration():
    due = date(2026, 8, 14)
    p = build_checkpoint_plan(due, "essay", LengthTarget(unit="자", min=3000), today=TODAY)
    md = render_plan_markdown(p)
    assert "## 체크포인트 플랜" in md and "CP1" in md and "통과:" in md
    # Result 통합 — mock 파이프라인 결과에 마감이 없으면 None(오탐 없음).
    from until.config import Config
    from until.pipeline import run
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    plan = plan_for_result(res, today=TODAY)
    # sample_assignment 마감·분량에 따라 플랜 유무가 갈리므로 타입만 검증.
    assert plan is None or plan.checkpoints
    # 웹 패널·리포트가 예외 없이 렌더된다.
    from until import report, web
    md_report = report.render_markdown_report(res)
    assert "## Draft" in md_report
    html_panel = web._plan_html(res)
    assert isinstance(html_panel, str)
    # 데모 과제는 항상 볼륨 게이트를 통과하는 입력(3000자+3주 마감)이다.
    demo = web.demo_assignment_text()
    assert "3000자" in demo and "마감" in demo
    print("OK render + Result/web/report integration + demo text")


if __name__ == "__main__":
    test_volume_gate()
    test_cp_counts_and_dates()
    test_type_specific_steps()
    test_render_and_result_integration()
    print("\nPLAN TESTS PASS")
