"""인용 커버리지 점검 테스트 (오프라인·결정적)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.context.citation_coverage import citation_coverage


def test_none_and_uncited():
    # 자료 없음 + 인용도 없음 → 점검 대상 아님.
    c = citation_coverage([], "인용 없는 본문")
    assert c.status == "none" and c.total == 0
    # 자료 없음 + [자료N] 인용 → 가짜 인용(invalid)로 표면화(2026-07 변경).
    c = citation_coverage([], "본문 [자료1]")
    assert c.status == "invalid" and c.invalid_ids == [1]
    # 자료는 있는데 인용 없음
    c = citation_coverage(["A", "B"], "인용 없는 본문이다")
    assert c.status == "uncited" and c.uncited_ids == [1, 2] and c.n_cited == 0
    print("OK none + uncited (+empty-sources fake cite = invalid)")


def test_partial_and_full():
    c = citation_coverage(["A", "B", "C"], "근거는 [자료1] 그리고 [자료3] 이다")
    assert c.status == "partial" and c.cited_ids == [1, 3] and c.uncited_ids == [2]
    assert "미인용" in c.message and "[자료2]" in c.message
    c = citation_coverage(["A", "B"], "[자료1] 과 [자료2] 모두 인용")
    assert c.status == "full" and c.cited_ids == [1, 2]
    print("OK partial + full")


def test_invalid_citation_flagged():
    # 존재하지 않는 번호 인용 = 가짜 인용 신호
    c = citation_coverage(["A"], "근거 [자료1] 과 [자료9] 라고 우김")
    assert c.status == "invalid" and c.invalid_ids == [9]
    assert "존재하지 않는" in c.message
    # 자료가 아예 없는데 [자료N] 인용 → 숨기지 않고 invalid.
    c = citation_coverage([], "근거는 [자료1] 이다")
    assert c.status == "invalid" and c.invalid_ids == [1]
    assert "자료가 없는데" in c.message
    # readiness도 이를 경고로 표면화.
    from until.pipeline import Result
    from until.boundary.models import Draft
    from until.execution.boundary_guard import GuardReport
    from until.readiness import assess_readiness
    res = Result(documents=[], spec={"title": "T"},
                 draft=Draft.from_text("[자료3] 라고 우김. " * 10),
                 guard=GuardReport(passed=True, attempts=1, reasks=0), sources=[])
    d = {i.label: i for i in assess_readiness(res).items}
    assert d.get("인용") and d["인용"].status == "warn"
    print("OK invalid citation flagged (+empty sources surfaced)")


def test_dedup_and_sort():
    c = citation_coverage(["A", "B", "C"], "[자료3] [자료1] [자료1] [자료3]")
    assert c.cited_ids == [1, 3]  # 중복 제거 + 정렬
    print("OK dedup + sort")


def test_second_pass_uses_same_source_numbering():
    """finalize·suggest·review가 run()과 동일한 자료 목록(범례 번호 체계)을 받는다.

    리뷰 9회차 버그: 2차 패스가 맥락만 넘겨 [자료N] 번호가 범례와 어긋났었다.
    """
    import until.pipeline as pl
    from until.config import Config
    from until.llm.mock_client import MockClient

    captured = {}
    orig = pl.build_client

    class Rec:
        def __init__(self, inner): self.inner = inner
        def complete(self, *a, **kw):
            captured[kw.get("tag")] = kw.get("documents")
            return self.inner.complete(*a, **kw)

    pl.build_client = lambda backend, model=None: Rec(MockClient())
    try:
        cfg = Config(); cfg.backend = "mock"
        res = pl.run(["examples/sample_assignment.txt"], cfg,
                     course_dir="examples/course_materials")
        # run이 저장한 source_docs가 범례와 개수·순서 일치.
        assert len(res.source_docs) == len(res.sources) >= 2
        # 과제 문서가 1번 — 제목은 경로가 아닌 파일명만(임시 경로 노출 방지).
        from pathlib import Path as _P
        want_title = f"과제: {_P(res.documents[0].source).name}"
        assert res.source_docs[0].title == want_title
        # 2차 패스 3종 모두 동일 목록을 받는다.
        pl.suggest_decision_answers(res, cfg)
        pl.review_result(res, cfg)
        pl.finalize(res, {1: "형식 결정론으로"}, cfg)
        for tag in ("suggest", "review", "finalize"):
            docs = captured.get(tag)
            assert docs is not None and len(docs) == len(res.sources), tag
            assert docs[0].title == want_title, tag
        # 구버전 세션 폴백 — source_docs 없어도 과제+맥락으로 재구성.
        res.source_docs = []
        fb = pl._all_source_docs(res)
        assert fb and fb[0].title == want_title
        # 2차 보조 패스 다이어트 — 제목·개수는 유지, 본문은 절단.
        res.source_docs = [type(fb[0])(title="t", text="가" * 5000)]
        tr = pl._trimmed_source_docs(res, cap=1200)
        assert len(tr) == 1 and tr[0].title == "t" and len(tr[0].text) < 1300
        # 모델 티어링 — UNTIL_MODEL_LIGHT 설정 시 제안·점검이 경량 모델로.
        import os as _os
        models = []
        pl.build_client = lambda backend, model=None: (models.append(model),
                                                       Rec(MockClient()))[1]
        _os.environ["UNTIL_MODEL_LIGHT"] = "light-model"
        try:
            res.source_docs = []
            pl.suggest_decision_answers(res, cfg)
            pl.review_result(res, cfg)
            assert models == ["light-model", "light-model"], models
        finally:
            _os.environ.pop("UNTIL_MODEL_LIGHT", None)
    finally:
        pl.build_client = orig
    print("OK second-pass shares legend numbering (finalize/suggest/review)")


def test_report_and_web_integration():
    from until.config import Config
    from until.pipeline import run
    from until import report, web
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    md = report.render_markdown_report(res)
    if getattr(res, "sources", None):
        assert "## Sources" in md
        # 커버리지 메시지 중 하나가 뜬다.
        assert any(k in md for k in ("인용 충실", "부분 인용", "근거 미인용", "인용 오류"))
    html = web._sources_html(res)
    assert (html == "") or ("근거 자료" in html)
    print("OK report + web integration")


def test_unsourced_claim_sentences():
    # 무근거 실명 사례 탐지 — 라이브 관측(MIT 확신조) 회귀.
    from until.context.citation_coverage import unsourced_claim_sentences as u
    # 실명 라틴 고유명 + [출처?] 없음 → 강한 신호로 잡힌다.
    body = "MIT는 AI 튜터를 도입했다. 이는 개인화 학습의 흐름을 보여준다."
    assert len(u(body)) == 1 and "MIT" in u(body)[0]
    # 같은 문장에 [출처?]가 있으면 통과(모델이 규칙을 지킨 경우).
    assert u("MIT는 AI 튜터를 도입했다 [출처?].") == []
    # [자료N] 인용 문장도 통과.
    assert u("MIT는 AI 튜터를 도입했다 [자료1].") == []
    # 결정 마커 안의 실명은 본문 주장이 아니다.
    assert u("[[DECISION: MIT 사례를 쓸지 — 본인 판단]] 본문은 일반 서술이다.") == []
    # 수치만 있는 문장은 약한 신호 — 1문장이면 미포함, 2문장부터 포함.
    assert u("취업률이 85%로 올랐다.") == []
    two = "취업률이 85%로 올랐다. 지원자는 3만 명이었다."
    assert len(u(two)) == 2
    # 일반 기술어(AI·GPT 등)는 실명 신호가 아니다.
    assert u("AI와 GPT의 활용이 늘고 있다.") == []
    print("OK unsourced claim sentences (strong latin / weak numeric / marker-safe)")


def test_unsourced_claims_in_readiness():
    # readiness 통합 — 참고 자료 없음 + 실명 확신조 → '근거' 경고.
    from until.config import Config
    from until.pipeline import run
    from until.readiness import assess_readiness
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    # mock 초안(자료 인용 있음)은 이 경고가 없어야 정상(오탐 방지).
    base = assess_readiness(res)
    assert not any(i.label == "근거" for i in base.items)
    # 실명 확신조 본문으로 바꾸면 경고가 뜬다(참고 자료 없음 전제).
    from until.boundary.models import Draft
    res.draft = Draft.from_text(
        "Stanford는 관련 제도를 도입했다. 이 흐름은 계속될 것이다.\n"
        "[[DECISION: 논지 선택 — 본인 판단]]\n")
    res.final_draft = None
    res.sources = [s for s in (res.sources or []) if str(s).startswith("과제:")]
    r = assess_readiness(res)
    got = [i for i in r.items if i.label == "근거"]
    assert got and got[0].status == "warn" and "실명" in got[0].message
    print("OK readiness unsourced-claims warning (no refs + confident naming)")


if __name__ == "__main__":
    test_none_and_uncited()
    test_partial_and_full()
    test_invalid_citation_flagged()
    test_dedup_and_sort()
    test_second_pass_uses_same_source_numbering()
    test_report_and_web_integration()
    test_unsourced_claim_sentences()
    test_unsourced_claims_in_readiness()
    print("\nCITATION TESTS PASS")
