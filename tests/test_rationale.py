"""결정 지점 '왜 당신 몫인지' 분류 테스트 (오프라인·결정적)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.boundary.rationale import classify_decision, DecisionRationale


def test_categories_detected():
    cases = {
        "세 논점 중 어느 것을 핵심 논지로 세울지 — 본인의 관점 필요": "관점·논지",
        "이 사안에서 무엇이 더 중요한지 가치 판단": "가치판단",
        "이 주제를 나의 진로·경험과 어떻게 연결할지": "진로·경험",
        "글의 제목과 어조(스타일)를 어떻게 잡을지": "취향·스타일",
        "어떤 사례를 범위에 포함하고 무엇을 뺄지 선택": "범위·선택",
    }
    for note, exp in cases.items():
        r = classify_decision(note)
        assert isinstance(r, DecisionRationale)
        assert r.category == exp, (note, r.category, exp)
        assert r.why  # 근거 한 줄 존재
    print("OK categories detected")


def test_default_when_no_signal():
    r = classify_decision("여기는 알 수 없는 무언가")
    assert r.category == "고유 판단" and r.why
    r2 = classify_decision("")
    assert r2.category == "고유 판단"
    print("OK default (고유 판단) fallback")


def test_web_shows_rationale():
    from until.config import Config
    from until.pipeline import run
    from until import web
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    # 결정이 있는 mock 초안 → 결정 필드에 왜 사용자 판단인지 근거가 뜬다.
    if res.draft.decisions:
        h = web.render_draft("tok", res)
    assert 'class="mine"' in h and "당신" in h
    print("OK web shows decision rationale")


def test_submission_and_report_show_rationale():
    from until.config import Config
    from until.pipeline import run
    from until import report
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    if res.draft.decisions:
        md = report.render_submission_markdown(res)
        assert "🔒" in md  # 제출용 '직접 정할 것'에 근거
        html = report.render_submission_html(res)
        assert "🔒" in html
        rep = report.render_markdown_report(res)
        # 진단 리포트 Decision Points에 카테고리 태그.
        assert "Decision Points" in rep and "_[" in rep
    print("OK submission + report show rationale")


if __name__ == "__main__":
    test_categories_detected()
    test_default_when_no_signal()
    test_web_shows_rationale()
    test_submission_and_report_show_rationale()
    print("\nRATIONALE TESTS PASS")
