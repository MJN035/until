"""준수율 eval 하니스 스모크 (오프라인·mock — 수치가 아니라 구조를 검증)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.config import Config
from until.evals.goldens import golden_cases
from until.evals.runner import run_all, render_table
from until.evals.metrics import score_output


def test_golden_cases_cover_required():
    cases = {c.key: c for c in golden_cases()}
    # 지시된 필수 케이스: 강의 1/3/8, 혼합 요건, '1.' 변형, 행 불일치 함정,
    # 프로필 결측 칸, (대조) 산문.
    for key in ("coweek_1", "coweek_3", "coweek_8", "mixed_req", "numbered",
                "trap_rows", "missing_profile", "prose_essay", "no_evidence",
                "evidence_report", "reflective_report", "problemset", "hdl_lab"):
        assert key in cases, key
    assert len(cases) >= 13
    assert cases["trap_rows"].n_items_expected == 3  # 표 4행이어도 서술 자리 기준
    assert not cases["prose_essay"].has_form
    print("OK golden cases cover required set")


def test_score_output_deterministic():
    form = "| 이름 |  |\n|---|---|\n① 강의명:\n▷ 강의 내용\n② 강의명:\n▷ 강의 내용\n"
    good = ("| 이름 | 김민준 |\n|---|---|\n"
            "① 강의명: A\n" + "내용. " * 100 + "\n② 강의명: B\n" + "내용. " * 100)
    s = score_output("t", "until", good, per_item_range=(270, 330),
                     n_items_expected=2, whole_min=None, form_text=form,
                     profile={"name": "김민준"}, source_text="")
    assert s.item_compliance == 1.0 and s.hallucinated_cells == 0
    # 프로필·자료에 없는 이름을 지어냄 → 환각 1건.
    fake = good.replace("김민준", "홍아무개")
    s2 = score_output("t", "until", fake, per_item_range=(270, 330),
                      n_items_expected=2, whole_min=None, form_text=form,
                      profile={"name": "김민준"}, source_text="")
    assert s2.hallucinated_cells == 1
    # 항목 하나가 분량 미달 → 준수율 0.5.
    s3 = score_output("t", "until",
                      "① 강의명: A\n" + "내용. " * 100 + "\n② 강의명: B\n짧다.",
                      per_item_range=(270, 330), n_items_expected=2,
                      whole_min=None, form_text=form,
                      profile={}, source_text="")
    assert s3.item_compliance == 0.5
    # 항목 구조 자체가 없음 → 0 + 비고에 개수 기록.
    s4 = score_output("t", "until", "통짜 산문. " * 60,
                      per_item_range=(270, 330), n_items_expected=2,
                      whole_min=None, form_text=form,
                      profile={}, source_text="")
    assert s4.item_compliance == 0.0 and any("항목" in n for n in s4.notes)
    # 단일 항목 양식(coweek_1형) — 헤드 1개여도 채점된다(split 폴백).
    s5 = score_output("t", "until", "① 강의명: A\n" + "내용. " * 100,
                      per_item_range=(270, 330), n_items_expected=1,
                      whole_min=None, form_text=form,
                      profile={}, source_text="")
    assert s5.item_compliance == 1.0
    print("OK score_output deterministic metrics")


def test_run_all_mock_smoke():
    cfg = Config(); cfg.backend = "mock"
    rows, elapsed, cases = run_all(cfg, keys=["coweek_3", "prose_essay"])
    assert len(rows) == 6  # 2케이스 × (legacy, unit, raw)
    variants = {(r.key, r.variant) for r in rows}
    for v in ("legacy", "unit", "raw"):
        assert ("coweek_3", v) in variants, v
    table = render_table(rows, elapsed, cases)
    assert "평균(legacy)" in table and "평균(unit)" in table and "평균(raw)" in table
    # raw 비교군은 원본 주입이 불가(None → '-') — 존재 증명 축.
    raw = next(r for r in rows if r.key == "coweek_3" and r.variant == "raw")
    assert raw.injection is None
    # 품질 지표(9단계)가 채점된다.
    assert raw.specificity is not None
    # 회귀: build_client가 여러 번 불려도(메인+요건추출) 호출 수가 덮어써지지
    # 않고 전 클라이언트에 걸쳐 누적된다(초안 1 + 요건추출 1 ≥ 2, reask 포함).
    for r in rows:
        if r.variant in ("legacy", "unit"):
            assert r.llm_calls >= 2, (r.key, r.variant, r.llm_calls)
            assert r.llm_calls >= r.reasks + 1, (r.llm_calls, r.reasks)
    print("OK run_all mock smoke (legacy/unit/raw + quality metrics)")


def test_llm_call_counter_accumulates_across_clients():
    # 회귀(카운터 덮어쓰기): _Counting 래퍼 2개가 공유 카운터에 함께 누적.
    from until.evals.runner import _Counting

    class _Fake:
        def complete(self, system, user, **kw):
            class R:
                text = "x"
            return R()

    shared = {"n": 0}
    a = _Counting(_Fake(), shared=shared)
    b = _Counting(_Fake(), shared=shared)  # 두 번째 build_client 상당(req_llm)
    a.complete("s", "u"); a.complete("s", "u"); b.complete("s", "u")
    assert shared["n"] == 3 and a.calls == 2 and b.calls == 1
    print("OK llm call counter accumulates across clients")


def test_no_evidence_golden_rewards_questions():
    # 근거 없는 케이스: '그럴듯한 300자'(raw류)는 무근거로 감점, DECISION 전환은 만점.
    from until.understanding.requirements import ContentElement
    elems = [ContentElement(id="new_learning", label="새로 알게 된 점",
                            evidence_kind="user_experience")]
    prose = "많은 것을 배웠고 유익했다. " * 20
    s_bad = score_output("t", "raw", prose, per_item_range=None,
                         n_items_expected=None, whole_min=None,
                         form_text="| 이름 |  |", profile={}, source_text="",
                         elements=elems)
    assert s_bad.ungrounded >= 1 and s_bad.decisions_ok == 0.0
    good = prose + "\n[[DECISION: '강의'에서 새로 알게 된 점 하나: ___]]"
    s_good = score_output("t", "unit", good, per_item_range=None,
                          n_items_expected=None, whole_min=None,
                          form_text="| 이름 |  |", profile={}, source_text="",
                          elements=elems)
    assert s_good.ungrounded == 0 and s_good.decisions_ok == 1.0
    print("OK no-evidence scoring rewards questions over plausible prose")


def test_coverage_per_element_not_blanket_decision():
    # 회귀: 무관한 DECISION 마커 1개가 모든 요소를 '커버됨'으로 만들지 않는다.
    from until.understanding.requirements import ContentElement
    elems = [ContentElement(id="a", label="강의 요약"),
             ContentElement(id="b", label="진로 계획")]
    body = "그럴듯한 본문이다. " * 30 + "\n[[DECISION: 어느 도시를 다룰지: ___]]"
    s = score_output("t", "unit", body, per_item_range=None,
                     n_items_expected=None, whole_min=None, form_text="",
                     profile={}, source_text="", elements=elems)
    assert s.coverage == 0.0, s.coverage
    # 마커가 '그 요소'를 물을 때만 해당 요소가 결정으로 커버된다(요소별 판정).
    body2 = body + "\n[[DECISION: '강의 요약'에 담을 핵심 사건: ___]]"
    s2 = score_output("t", "unit", body2, per_item_range=None,
                      n_items_expected=None, whole_min=None, form_text="",
                      profile={}, source_text="", elements=elems)
    assert s2.coverage == 0.5, s2.coverage
    # 1글자 토큰("점"·"된")은 내용어로 안 친다 — 과대 판정 방지.
    elems1 = [ContentElement(id="c", label="된 점")]
    s3 = score_output("t", "unit", "장점과 단점이 있다.", per_item_range=None,
                      n_items_expected=None, whole_min=None, form_text="",
                      profile={}, source_text="", elements=elems1)
    assert s3.coverage == 0.0, s3.coverage
    print("OK coverage judged per element (no blanket DECISION credit)")


def test_hallucination_rejects_combos_and_truncation():
    # 회귀: 자료 본문 단어 짜깁기·절단 조작은 환각으로 잡고, 프로필 값 결합은 정상.
    form = "| 이름 |  |\n| 학번 |  |\n| 지도교수 |  |\n| 소속 |  |"
    src = "이 과제는 데이터 분석과 연구 윤리를 다룬다. 담당 조교 학번 2020-12345."
    bad = "| 지도교수 | 데이터 윤리 |\n|---|---|\n| 학번 | 2020-1234 |"
    s = score_output("t", "unit", bad, per_item_range=None,
                     n_items_expected=None, whole_min=None, form_text=form,
                     profile={"name": "김민준"}, source_text=src)
    assert s.hallucinated_cells == 2, s.notes
    # 자료에 온전한 값으로 있는 학번·프로필 두 값의 결합("서울대학교 자유전공학부")은 정상.
    good = ("| 학번 | 2020-12345 |\n|---|---|\n"
            "| 소속 | 서울대학교 자유전공학부 |")
    s2 = score_output("t", "unit", good, per_item_range=None,
                      n_items_expected=None, whole_min=None, form_text=form,
                      profile={"univ": "서울대학교", "dept": "자유전공학부"},
                      source_text=src)
    assert s2.hallucinated_cells == 0, s2.notes
    print("OK hallucination catches combos/truncation, keeps merged profile values")


def test_metric_weighted_compliance():
    from until.optimize.metric import score_and_feedback
    from until.understanding.length_target import LengthTarget
    body = ("본문. " * 80) + "\n[[DECISION: 관점 선택: ___]]"
    base, _ = score_and_feedback(body)
    t = LengthTarget(unit="자", min=5000)  # 명백히 미달인 요건
    with_len, fb = score_and_feedback(body, length_target=t)
    assert with_len < base and "분량" in fb  # 준수 미달이 점수에 반영
    # 입력이 없으면 기존 점수와 동일(하위호환).
    again, _ = score_and_feedback(body)
    assert again == base
    print("OK GEPA metric weighted compliance (opt-in)")


if __name__ == "__main__":
    test_golden_cases_cover_required()
    test_score_output_deterministic()
    test_run_all_mock_smoke()
    test_llm_call_counter_accumulates_across_clients()
    test_no_evidence_golden_rewards_questions()
    test_coverage_per_element_not_blanket_decision()
    test_hallucination_rejects_combos_and_truncation()
    test_metric_weighted_compliance()
    print("\nEVALS TESTS PASS")
