"""분량 요건 감지·측정·판정 테스트 (오프라인·결정적)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.understanding.length_target import (
    detect_length_target, measure_length, check_length, LengthTarget,
)


class _Doc:
    def __init__(self, text): self.text = text; self.source = "x"


def test_detect_min_max_range():
    # 하한 "이상"
    t = detect_length_target({"requirements": ["2000자 이상 작성"]})
    assert t and t.unit == "자" and t.min == 2000 and t.max is None
    # 상한 "이하"
    t = detect_length_target({"constraints": ["1500자 이하로"]})
    assert t and t.max == 1500 and t.min is None
    # 범위
    t = detect_length_target({"requirements": ["분량은 500~800자"]})
    assert t and t.min == 500 and t.max == 800
    # 콤마 숫자
    t = detect_length_target({"requirements": ["최소 1,200자"]})
    assert t and t.min == 1200
    print("OK detect min/max/range/comma")


def test_detect_units_and_fallback():
    # 단어
    t = detect_length_target({"requirements": ["at least 500 words"]})
    assert t and t.unit == "단어" and t.min == 500
    # 페이지
    t = detect_length_target({"constraints": ["A4 3페이지 내외"]})
    assert t and t.unit == "페이지" and t.min and t.max  # 내외 → 범위
    # 명세에 없으면 원문에서
    t = detect_length_target({}, [_Doc("과제 안내: 800자 이상 제출할 것.")])
    assert t and t.min == 800
    # 아무데도 없으면 None
    assert detect_length_target({"requirements": ["출처를 밝힐 것"]}, [_Doc("자유 주제")]) is None
    print("OK detect units + doc fallback + none")


def test_jang_only_with_length_context():
    # 챕터·사진 '장'은 분량이 아니다.
    assert detect_length_target({"requirements": ["교재 5장을 읽고 감상문을 쓰시오"]}) is None
    assert detect_length_target({"requirements": ["사진 3장을 첨부하시오"]}) is None
    # 분량 문맥이 붙은 '장'만 페이지로.
    t = detect_length_target({"requirements": ["A4 2장 이상 작성"]})
    assert t and t.unit == "페이지" and t.min == 2
    t = detect_length_target({"requirements": ["3장 분량으로 정리"]})
    assert t and t.unit == "페이지" and t.min == 3
    print("OK 장 only with length context")


def test_a4_mae_is_page_not_manuscript():
    """'A4 5매'는 원고지 5매(1,000자)가 아니라 A4 5쪽이다.

    2026-08-22 실사용(대학 글쓰기 1 기말리포트 '분량은 A4 5매 내외')에서 잡혔다.
    같은 요건을 '장'으로 적으면 페이지, '매'로 적으면 원고지로 갈려 목표 분량이
    세 배 넘게 달라지던 비대칭을 없앤다. 용지 표지가 없는 '매'는 원고지 그대로.
    """
    for text in ("분량은 A4 5매 내외입니다.", "A4용지 5매 내외"):
        t = detect_length_target({"requirements": [text]})
        assert t and t.unit == "페이지" and t.min == 4 and t.max == 5, text
    t = detect_length_target({"requirements": ["A4 용지 3매 이상 작성하시오"]})
    assert t and t.unit == "페이지" and t.min == 3
    # '장'으로 쓴 같은 요건과 판정이 같아야 한다(raw 인용문만 다르다).
    def _judged(text):
        t = detect_length_target({"requirements": [text]})
        return (t.unit, t.min, t.max, t.mode)
    assert _judged("A4 5매 내외") == _judged("A4 5장 내외")
    # 용지 표지가 없으면 원고지 매수 그대로.
    t = detect_length_target({"requirements": ["원고지 5매 내외로 작성"]})
    assert t and t.unit == "매" and t.min == 4 and t.max == 5
    print("OK A4 매 = page, 원고지 매 = manuscript")


def test_page_reference_not_length():
    # (스모크) 페이지 '참조'는 분량 요건이 아니다.
    assert detect_length_target({"requirements": ["교재 20~35쪽을 읽고 오세요"]}) is None
    assert detect_length_target({"requirements": ["교재 120페이지를 참고하여 정리"]}) is None
    # 진짜 페이지 분량은 유지.
    t = detect_length_target({"requirements": ["5페이지 내외로 작성"]})
    assert t and t.unit == "페이지" and t.min == 4 and t.max == 5
    print("OK page reference excluded (real page targets kept)")


def test_leading_modifier_and_merge():
    # 앞에 오는 수식어: "최대 3000자" → 상한.
    t = detect_length_target({"requirements": ["최대 3000자로 작성하시오."]})
    assert t and t.max == 3000 and t.min is None
    over = check_length(t, "글자수. " * 900)  # 3600자 이상
    assert over.status == "over"
    # min/max 병합: "최소 1000자, 최대 3000자".
    t = detect_length_target({"requirements": ["최소 1000자, 최대 3000자"]})
    assert t and t.min == 1000 and t.max == 3000
    # '이내'도 상한.
    t = detect_length_target({"requirements": ["2000자 이내"]})
    assert t and t.max == 2000 and t.min is None
    print("OK leading modifier + min/max merge")


def test_prefers_char_unit_over_page():
    # 자와 페이지가 함께 나오면 '자'를 우선.
    t = detect_length_target({"requirements": ["3페이지, 1500자 이상"]})
    assert t and t.unit == "자" and t.min == 1500
    print("OK prefers 자 over 페이지")


def test_measure_excludes_markers():
    body = "가나다 라마바\n[[DECISION: 이건 세면 안 됨 — 매우 긴 결정 노트]]\n사아자"
    no_space, with_space, words = measure_length(body)
    # 결정 마커 내용은 제외 → "가나다라마바사아자" 9자.
    assert no_space == len("가나다라마바사아자")
    assert words == 3
    print("OK measure excludes decision markers")


def test_check_status():
    t = LengthTarget(unit="자", min=100)
    short = check_length(t, "짧다. " * 5)
    assert short.status == "short" and "더 필요" in short.message
    ok = check_length(t, "충분한 본문. " * 40)
    assert ok.status == "ok" and "충족" in ok.message
    over = check_length(LengthTarget(unit="자", max=10), "이건 열 글자를 훨씬 넘는 본문이다")
    assert over.status == "over" and "초과" in over.message
    # 요건 없음 → unknown, 측정치는 채움
    none = check_length(None, "본문 몇 자")
    assert none.status == "unknown" and none.chars > 0
    print("OK check status short/ok/over/unknown")


def test_pipeline_and_report_integration():
    from until.config import Config
    from until.pipeline import run
    from until import report
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    # length_target 필드 존재(감지 여부는 자료에 따라 다름 — 크래시 없이 동작).
    md = report.render_markdown_report(res)
    sub = report.render_submission_markdown(res)
    assert isinstance(md, str) and isinstance(sub, str)
    # 강제로 요건을 주입해 리포트 '제출 준비 점검'에 분량 경고가 뜨는지 확인.
    res.length_target = LengthTarget(unit="자", min=100000)
    md2 = report.render_markdown_report(res)
    assert "제출 준비 점검" in md2 and "분량 부족" in md2
    # 제출용은 분량을 '제출 준비 점검' 블록으로 통합해 보여준다.
    sub2 = report.render_submission_markdown(res)
    assert "제출 준비 점검" in sub2 and "분량 부족" in sub2
    print("OK pipeline + report integration")


def test_length_directive_injected():
    # 감지된 분량 요건이 초안 '생성 지침'으로 주입된다(사후 판정만이 아니라).
    from until.execution.prompts import length_directive
    from until.understanding.length_target import LengthTarget
    d = length_directive(LengthTarget(unit="자", min=500, max=800))
    assert "500~800자" in d and "분량 요건" in d
    assert length_directive(None) == ""
    # 파이프라인 끝단: 요건 있는 과제를 돌리면 Result에 감지 결과가 실린다(주입 경로 공유).
    import until.pipeline as pl
    from until.config import Config
    captured = {}
    orig = pl.build_client
    class Rec:
        def __init__(self, inner): self.inner = inner
        def complete(self, system, user, **kw):
            if kw.get("tag") in ("execution", "execution-unit"):   # 초안 생성 호출의 시스템만
                captured.setdefault("sys", system)
            return self.inner.complete(system, user, **kw)
    from until.llm.mock_client import MockClient
    pl.build_client = lambda backend, model=None: Rec(MockClient())
    try:
        cfg = Config(); cfg.backend = "mock"
        res = pl.run(["examples/sample_assignment.txt"], cfg)
        if res.length_target is not None:  # 샘플에 분량 요건이 있으면 시스템에도 주입돼야
            assert "분량 요건" in captured["sys"]
    finally:
        pl.build_client = orig
    print("OK length directive injected into execution system prompt")


def test_per_item_scope_detected():
    # "강의당 300자 내외" — 전체가 아니라 '강의' 항목당 요건이다(실사용 버그: CO-Week 보고서).
    t = detect_length_target({"requirements": ["분량 제한: 강의당 300자 내외"]})
    assert t and t.per_item == "강의" and t.min == 270 and t.max == 330
    assert "강의당" in t.describe()
    # 다른 표면형: 문항당 / 각 강좌 / 강의별 / 강의마다.
    assert detect_length_target({"requirements": ["문항당 500자 이상"]}).per_item == "문항"
    assert detect_length_target({"requirements": ["각 강좌 300자 내외"]}).per_item == "강좌"
    assert detect_length_target({"requirements": ["강의별 300자 내외 작성"]}).per_item == "강의"
    assert detect_length_target({"requirements": ["강의마다 300자 내외"]}).per_item == "강의"
    # 오탐 방지: '해당'의 '당'은 항목 단위가 아니다.
    t = detect_length_target({"requirements": ["해당 300자 분량을 지키시오"]})
    assert t is None or t.per_item == ""
    # per_item 없는 기존 동작 보존.
    t = detect_length_target({"requirements": ["2000자 이상 작성"]})
    assert t and t.per_item == ""
    print("OK per-item scope detected")


def test_per_item_check_each_item():
    from until.understanding.length_target import split_items
    t = detect_length_target({"requirements": ["강의당 300자 내외"]})
    ok_item = "내용. " * 100          # 공백 제외 300자
    short_item = "짧다. " * 10        # 공백 제외 ~50자
    body = (f"① AI 융합 (7/1)\n{ok_item}\n"
            f"② 데이터 윤리 (7/2)\n{ok_item}\n"
            f"③ 창업 특강 (7/3)\n{ok_item}")
    # 항목 분할이 ①②③을 인식한다.
    assert len(split_items(body)) == 3
    chk = check_length(t, body)
    # 강의 3개 각각이 270~330 범위 → 전체(~900자)를 '초과'로 오판하지 않는다.
    assert chk.status == "ok", chk.message
    assert "①" in chk.message and "③" in chk.message
    # 한 항목이 부족하면 그 항목이 지목된다.
    bad = (f"① AI 융합\n{ok_item}\n② 데이터 윤리\n{short_item}\n③ 창업 특강\n{ok_item}")
    chk2 = check_length(t, bad)
    assert chk2.status == "short" and "②" in chk2.message
    # 항목 구분을 못 찾으면 전체를 단일 항목 요건으로 오판하지 않는다(unknown).
    chk3 = check_length(t, "구분 없는 통짜 본문. " * 100)
    assert chk3.status == "unknown"
    print("OK per-item check judges each item")


def test_per_item_directive():
    # 생성 지침이 '항목 각각' 적용임을 명시한다(전체 300자로 잘리는 실사용 버그 방지).
    from until.execution.prompts import length_directive
    t = detect_length_target({"requirements": ["강의당 300자 내외"]})
    d = length_directive(t)
    assert "각각" in d and "강의" in d
    assert "전체" in d  # '전체를 이 분량으로 줄이면 실패' 경고
    print("OK per-item length directive")


def test_extra_sources_hidden_spec():
    # 실코퍼스: 분량 요건이 과제 본문(12%)이 아니라 공지·첨부(숨은 명세)에 실린다.
    # extra_sources(eTL 공지·관련자료 SourceDoc)는 마지막 순위로 스캔.
    ann = _Doc("eTL 공지 '보고서 안내'\n본문: 보고서는 1500자 이내로 제출하세요.")
    t = detect_length_target({"requirements": ["주제를 논하시오"]}, None,
                             extra_sources=[ann])
    assert t and t.max == 1500 and t.unit == "자"
    # 명세·원문이 있으면 그쪽이 이긴다(보조 소스는 폴백).
    t2 = detect_length_target({"requirements": ["2000자 이상"]}, None,
                              extra_sources=[ann])
    assert t2 and t2.min == 2000 and t2.max is None
    doc = _Doc("과제: 800자 이상 서술")
    t3 = detect_length_target({}, [doc], extra_sources=[ann])
    assert t3 and t3.min == 800
    # 보조 소스의 페이지 '참조'(읽기 안내)는 여전히 오탐 아님.
    ref = _Doc("교재 120페이지를 참고하여 읽어 오세요.")
    assert detect_length_target({}, None, extra_sources=[ref]) is None
    print("OK extra sources (공지/첨부 숨은 명세) fallback order")


def test_split_items_md_prose_sections():
    # 마크다운 산문 구획(서론/본론/결론)은 '항목'이 아니다 — 항목당 분량을
    # 구획에 들이대 허위 델타로 reask를 돌리던 회귀(리뷰 발견). 번호 있는
    # 헤딩(1주차 등)만 항목으로 분할한다.
    from until.understanding.length_target import split_items
    prose = "# 제목\n## 서론\n글.\n## 본론\n글.\n## 결론\n글."
    assert split_items(prose) == []
    numbered = "## 1주차 강의\n내용.\n## 2주차 강의\n내용."
    assert len(split_items(numbered)) == 2
    print("OK MD 산문 구획은 항목 분할 안 함(번호 헤딩만)")


def test_expected_single_item_passes():
    # 단일 항목 양식(expected_items=1) — split_items가 1개를 못 만들어(설계상
    # 0 또는 2+) 어떤 출력도 mismatch로 통과 불가였던 회귀(리뷰 발견).
    from until.understanding.length_target import LengthTarget, check_length
    t = LengthTarget(unit="자", min=50, per_item="항목")
    body = "요건을 정확히 채운 단일 항목 본문. " * 10
    chk = check_length(t, body, expected_items=1)
    assert chk.status == "ok", (chk.status, chk.message)
    # 항목이 여럿이어야 하는데 없는 경우는 여전히 실패로 잡는다.
    chk2 = check_length(t, body, expected_items=3)
    assert chk2.status == "mismatch"
    print("OK 단일 항목 양식 통과(다항목 불일치는 유지)")


def test_target_in_chars_converts_units():
    """분량 요건을 글자 수로 환산한다 — 페이지 5는 5자가 아니다.

    2026-08-22 실측: unit 경로가 `.max`를 그대로 글자 수로 써서 'A4 5매' 요건의
    `plan.target_chars`가 **5**로 잡혔고, 하한 게이트(60자)에 걸려 분량 검증기가
    아예 안 붙었다. 페이지 단위 과제는 unit 경로에서 분량 강제가 통째로 없었다.
    """
    from until.understanding.length_target import LengthTarget, target_in_chars

    assert target_in_chars(LengthTarget(unit="페이지", min=4, max=5)) == 4500
    assert target_in_chars(LengthTarget(unit="매", min=4, max=5)) == 1000
    assert target_in_chars(LengthTarget(unit="자", min=800, max=1000)) == 1000
    # max가 없으면 min으로 떨어진다(하한만 있는 요건이 다수).
    assert target_in_chars(LengthTarget(unit="페이지", min=3)) == 2700
    # 단어는 환산하지 않는다 — 한·영 혼합에서 안정적인 계수가 없다.
    # 틀린 계수로 강제하느니 강제를 걸지 않는 편이 낫다.
    assert target_in_chars(LengthTarget(unit="단어", min=500)) == 0
    assert target_in_chars(None) == 0
    print("OK 분량 요건 → 글자 수 환산 (단어는 환산 안 함)")


def test_slide_count_is_not_a_length_requirement():
    """'슬라이드 8~12장'은 산문 12페이지가 아니다.

    발표 자료의 크기는 장수로 세는 것이지 글자로 세는 것이 아니다 — 같은 과제가
    대개 "슬라이드당 글자 수는 최소화"라고 못박는다. 이걸 페이지로 읽으면 목표가
    10,800자가 되고, 생성 루프에 걸리면 통과 불가능한 요구가 된다(실제로
    examples/sample_presentation.txt가 이것으로 가드 실패했다).
    """
    for text in ("슬라이드 8~12장 분량의 프레젠테이션을 구성한다",
                 "PPT 10장 이내",
                 "발표 자료 15매 이내",
                 "장표 20장 내외로 만드시오"):
        assert detect_length_target({"requirements": [text]}) is None, text
    # 산문 요건은 그대로 살아 있어야 한다(슬라이드 가드가 과잉 적용되면 안 된다).
    t = detect_length_target({"requirements": ["A4 5매 내외로 제출"]})
    assert t and t.unit == "페이지" and t.max == 5
    t = detect_length_target({"requirements": ["A4 2장 이상 작성"]})
    assert t and t.unit == "페이지" and t.min == 2
    print("OK 슬라이드 장수는 분량 요건이 아니다 (산문 요건은 유지)")


def test_page_target_reaches_unit_plan():
    """페이지 요건이 단위 계획의 글자 목표로 실제 전달된다.

    골격이 **없는** 유형(problemset·code·presentation)에는 목표를 주지 않는다 —
    코드 산출물·문제 풀이·발표 자료에 산문 글자 수를 요구하는 것 자체가 틀렸고,
    실제로 그렇게 걸었더니 3인 코퍼스 9건이 깨졌다(2026-08-22).
    """
    from until.execution.content_plan import build_unit_plan
    from until.execution.units import ResponseUnit
    from until.understanding.length_target import LengthTarget
    from until.understanding.skeleton import SkeletonSlot

    slots = [SkeletonSlot(id="s%d" % i, label="슬롯%d" % i,
                          evidence_kind="general_knowledge") for i in range(4)]
    lt = LengthTarget(unit="페이지", min=4, max=5)

    u = ResponseUnit(index=1, title="", meta={}, elements=list(slots),
                     length_target=lt)
    plan = build_unit_plan(u)
    assert plan.target_chars == 4500, plan.target_chars
    assert [i.target_chars for i in plan.items] == [1125] * 4
    assert plan.target_chars >= 60, "60자 게이트를 넘어야 분량 검증기가 붙는다"

    # 골격 없는 유형(슬롯 0) — 분량 강제 대상이 아니다.
    bare = ResponseUnit(index=1, title="", meta={}, elements=[], length_target=lt)
    assert build_unit_plan(bare).target_chars == 0
    print("OK 페이지 요건이 단위 목표로 전달됨 (골격 없는 유형은 제외)")


if __name__ == "__main__":
    test_detect_min_max_range()
    test_extra_sources_hidden_spec()
    test_detect_units_and_fallback()
    test_jang_only_with_length_context()
    test_a4_mae_is_page_not_manuscript()
    test_target_in_chars_converts_units()
    test_slide_count_is_not_a_length_requirement()
    test_page_target_reaches_unit_plan()
    test_page_reference_not_length()
    test_leading_modifier_and_merge()
    test_prefers_char_unit_over_page()
    test_measure_excludes_markers()
    test_check_status()
    test_length_directive_injected()
    test_per_item_scope_detected()
    test_per_item_check_each_item()
    test_per_item_directive()
    test_pipeline_and_report_integration()
    test_split_items_md_prose_sections()
    test_expected_single_item_passes()
    print("\nLENGTH TESTS PASS")
