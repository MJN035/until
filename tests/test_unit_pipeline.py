"""단위별 생성 경로(UNTIL_PIPELINE=unit) 테스트 (오프라인·결정적) — 8단계.

수용 기준: 1(근거 없으면 구체 질문) 2(한 줄 답 → 채움) 3(공허 문장 → 재생성)
5(강의 3개가 서로 다른 근거 인용) + 산문 회귀.
"""
import sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.config import Config
from until.execution.unit_pipeline import run_unit_draft
from until.understanding.requirements import ContentElement

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_formfill import _make_form_hwpx
from until.capture.ingest import ingest_all


_NOTE = """[수강 확인 내역]
1) 분야 AI · 강좌명 '생성형 인공지능과 산업의 재편' · 2026-07-01 10:00
요지: 제조 검사 공정에 비전 모델을 적용해 시간을 절반으로 줄인 사례와
도메인 데이터가 가치 사슬을 재편하는 구조를 다룸.
2) 분야 데이터 · 강좌명 '데이터 윤리와 프라이버시' · 2026-07-02 14:00
요지: 차등 프라이버시와 동의 설계, 재식별 위험 실험 결과.
3) 분야 창업 · 강좌명 '딥테크 창업' · 2026-07-03 16:00
요지: 연구 성과의 제품화에서 시장 검증 실패 사례 셋.
"""

_ELEMS = [
    ContentElement(id="core_concept", label="핵심 개념", required=True,
                   scope="per_unit", evidence_kind="lecture_material"),
    ContentElement(id="new_learning", label="새로 알게 된 점", required=True,
                   scope="per_unit", evidence_kind="user_experience"),
]


class _EchoLLM:
    """단위 프롬프트의 근거 발췌를 인용해 '구체적인' 본문을 쓰는 가짜 모델."""
    def __init__(self):
        self.calls = 0
    def complete(self, system, user, **kw):
        self.calls += 1
        class R: pass
        r = R()
        # 근거 발췌 줄(· ...)을 뽑아 문장으로 재구성 — 단위마다 내용이 달라진다.
        evs = [ln.strip("· ").strip() for ln in user.splitlines()
               if ln.strip().startswith("·") and len(ln.strip()) > 12]
        core = " ".join(evs)[:400] or "명세에 근거한 본문."
        r.text = (f"이 강의는 다음을 다뤘다: {core} 이 내용은 종합설계 과제의 "
                  "데이터 파이프라인 설계와 직접 연결된다.")
        return r


def _spec():
    return {"deliverable": "참가 결과 보고서", "task_type": "reflective_report",
            "goal": "CO-Week 참가 결과 보고서",
            "requirements": ["강의당 300자 내외"]}


def _build_inputs(d):
    form = _make_form_hwpx(d)
    note = d / "수강내역.txt"
    note.write_text(_NOTE, encoding="utf-8")
    return ingest_all([str(form), str(note)], backend="basic")


def test_units_distinct_and_absent_becomes_question():
    cfg = Config(); cfg.backend = "local"; cfg.pipeline_mode = "unit"
    with tempfile.TemporaryDirectory() as td:
        docs = _build_inputs(pathlib.Path(td))
        draft, report, units = run_unit_draft(
            docs, _spec(), _EchoLLM(), cfg, content_elements=_ELEMS)
    assert len(units) == 3
    # 수용 기준 5: 각 단위가 서로 다른 근거를 인용(복붙형 아님).
    bodies = [u.body for u in units]
    assert "비전 모델" in bodies[0] and "비전 모델" not in bodies[1]
    assert "차등 프라이버시" in bodies[1] and "차등 프라이버시" not in bodies[2]
    assert "시장 검증" in bodies[2]
    # 수용 기준 1: user_experience(새로 알게 된 점)는 자료가 있어도 안 지어냄 —
    # 단위마다 강의를 특정한 구체 질문이 남는다.
    assert draft.body.count("[[DECISION:") >= 3
    assert "생성형 인공지능과 산업의 재편" in draft.body and "한 줄이면 충분" in draft.body
    # 조립: 표는 코드가 채움(강의 표에 단위 제목), 항목 헤더 유지.
    assert "| AI | 생성형 인공지능과 산업의 재편 | 2026-07-01 10:00 |" in draft.body
    assert "① 강의명:" in draft.body and "③ 강의명:" in draft.body
    print("OK units distinct + absent → specific questions + tables by code")


def test_one_line_answer_fills_experience():
    cfg = Config(); cfg.backend = "local"; cfg.pipeline_mode = "unit"
    ans = {"'생성형 인공지능과 산업의 재편' 강의에서 새로 알게 된 점?":
           "검사 자동화가 비용이 아니라 재작업 감소로 정당화된다는 것"}
    with tempfile.TemporaryDirectory() as td:
        docs = _build_inputs(pathlib.Path(td))
        draft, report, units = run_unit_draft(
            docs, _spec(), _EchoLLM(), cfg, content_elements=_ELEMS,
            user_answers=ans)
    u1 = units[0]
    # 수용 기준 2: 한 줄 답이 근거(user_input)로 실려 그 단위의 질문이 사라진다.
    assert not any(it.action == "decision" and it.element_id == "new_learning"
                   for it in u1.plan.items)
    assert any(it.element_id == "new_learning" and it.action in ("write", "write_thin")
               for it in u1.plan.items)
    # 다른 단위(답 없음)는 여전히 질문.
    assert any(it.action == "decision" for it in units[1].plan.items)
    print("OK one-line answer converts absent → write for that unit")


def test_empty_sentence_triggers_unit_reask():
    cfg = Config(); cfg.backend = "local"; cfg.pipeline_mode = "unit"
    cfg.unit_parallel = 1

    class FluffyThenConcrete:
        def __init__(self): self.n = {}
        def complete(self, system, user, **kw):
            key = "u" + ("1" if "생성형" in user else "x")
            self.n[key] = self.n.get(key, 0) + 1
            class R: pass
            r = R()
            if self.n[key] == 1:
                r.text = ("다양한 기술을 배울 수 있었다. 사례를 소개받고 개념을 "
                          "실습을 통해 직접 체험하였다.")
            else:
                evs = [ln.strip("· ").strip() for ln in user.splitlines()
                       if ln.strip().startswith("·")]
                r.text = ("강의는 구체적으로 다음을 다뤘다: "
                          + " ".join(evs)[:300] + " 이를 과제 설계에 적용한다.")
            return r

    with tempfile.TemporaryDirectory() as td:
        docs = _build_inputs(pathlib.Path(td))
        draft, report, units = run_unit_draft(
            docs, _spec(), FluffyThenConcrete(), cfg, content_elements=_ELEMS)
    # 수용 기준 3: 공허 문장이 단위 reask를 트리거했고, 사유에 문장이 인용됐다.
    assert report.reasks >= 1
    joined = " ".join(e for h in report.history for e in h)
    assert "체험하였다" in joined or "공허" in joined
    print("OK empty sentences trigger per-unit reask")


def test_no_evidence_case_yields_questions_not_prose():
    # 자료가 제목뿐(요지 없음) — '그럴듯한 300자' 대신 질문+사실 칸이 정답.
    cfg = Config(); cfg.backend = "local"; cfg.pipeline_mode = "unit"

    class Watcher:
        def __init__(self): self.tags = []
        def complete(self, *a, **k):
            self.tags.append(k.get("tag") or "")
            class R: pass
            r = R(); r.text = "호출되면 안 되는 일반론."
            return r

    llm = Watcher()
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        form = _make_form_hwpx(d)
        docs = ingest_all([str(form)], backend="basic")
        draft, report, units = run_unit_draft(
            docs, _spec(), llm, cfg, content_elements=_ELEMS)
    # 근거 전무 → **본문 생성은 하지 않는다**(그게 이 계약의 핵심).
    assert "execution-unit" not in llm.tags, "근거가 없는데 본문을 생성했다"
    assert "일반론" not in draft.body
    assert draft.body.count("[[DECISION:") >= 3
    # 근거가 없으면 LLM을 아예 부르지 않는다. 질문을 LLM으로 다듬는 보강을
    # 시도했다가 되돌렸다(2026-08-23) — 명세가 얇으면 모델도 과제를 모르므로
    # 학생에게 과제를 되묻는 질문이 나왔다. 근거는 원장 U-3.
    assert llm.tags == [], llm.tags
    print("OK no-evidence case: questions, not plausible prose (LLM not called)")


def test_prose_single_unit_regression():
    # 양식 없는 산문 → 단위 1개, 명세 기반 생성(legacy 동등) — 회귀 방지.
    cfg = Config(); cfg.backend = "local"; cfg.pipeline_mode = "unit"

    class Simple:
        def complete(self, system, user, **kw):
            class R: pass
            r = R()
            r.text = ("도시 공공공간의 디지털 전환을 관찰 근거와 함께 분석한다. "
                      "구체 사례로 무인 대여 시스템의 이용 데이터 변화를 다룬다. "
                      "[[DECISION: 분석 대상 공간 하나: ___]]")
            return r

    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "과제.txt"
        p.write_text("디지털 전환이 공공공간에 미친 영향을 논하시오. 2000자 이상.",
                     encoding="utf-8")
        docs = ingest_all([str(p)], backend="basic")
        spec = {"deliverable": "에세이", "task_type": "essay",
                "goal": "공공공간 에세이", "requirements": []}
        draft, report, units = run_unit_draft(docs, spec, Simple(), cfg,
                                              content_elements=[])
    assert len(units) == 1
    assert "무인 대여 시스템" in draft.body and "[[DECISION:" in draft.body
    assert "| " not in draft.body  # 표 조립 없음(양식 없음)
    print("OK prose single-unit regression (legacy-equivalent)")


def test_coverage_decision_pending_ok():
    # 루프 중 커버리지가 '의도된 공백(decision)' 요소를 누락으로 세던 자기모순
    # 회귀(리뷰 발견) — 마커는 조립 때 결정적으로 붙으므로 항상 by_decision.
    from until.execution.coverage import check_unit_coverage

    class It:
        excerpts: list = []

        def __init__(self, action, eid, label):
            self.action, self.element_id, self.label = action, eid, label

    class Plan:
        pass

    class U:
        pass

    u = U()
    u.index = 0
    u.body = "실측 결과를 표와 함께 정리한 본문."  # 결정 마커 없음(지시 준수)
    u.plan = Plan()
    u.plan.items = [It("decision", "d1", "본인 소감"),
                    It("write", "w1", "실측 결과")]
    rep = check_unit_coverage(u)
    assert rep.ok, rep.missing
    assert "d1" in rep.by_decision
    print("OK 루프 커버리지 — decision 요소는 마커 없어도 누락 아님")


def test_mock_generic_and_safety_units_pass():
    import pathlib
    import tempfile
    from until.config import Config
    from until.pipeline import run
    with tempfile.TemporaryDirectory() as td:
        code = pathlib.Path(td) / "code.txt"
        code.write_text("Python으로 정렬 함수를 구현하고 테스트를 제출하시오.", encoding="utf-8")
        cfg = Config(backend="mock"); cfg.pipeline_mode = "unit"
        result = run([str(code)], cfg)
        assert result.guard.passed, result.guard.final_errors

        paper = pathlib.Path(td) / "paper.txt"
        paper.write_text("답안은 손글씨를 그대로 남긴 파일만 인정합니다. 풀이를 설명하시오.",
                         encoding="utf-8")
        safe = run([str(paper)], cfg)
        assert safe.guard.passed and safe.spec.get("integrity_gate")
    print("OK mock 일반 단위 충분 분량 + 안전모드 검증기 모순 없음")


def test_gate_directive_reaches_unit_prompt():
    # 대필 금지 게이트·원료 없음 지시(system_extra)가 unit 경로에 전달되는지 —
    # 누락 시 게이트가 unit 경로에서 통째로 우회되던 회귀(리뷰 발견).
    import pathlib
    import tempfile

    from until.capture.ingest import ingest_all
    from until.config import Config
    from until.execution.unit_pipeline import run_unit_draft

    seen = {}

    class Cap:
        def complete(self, system, user, **kw):
            seen["system"] = system

            class R:
                text = ("도시 공공공간의 변화를 관찰 근거와 함께 분석한다. "
                        "[[DECISION: 분석 대상 하나: ___]]")
            return R()

    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "과제.txt"
        # 근거가 아주 없으면 생성 자체를 생략하는 설계라, LLM 호출이 일어날
        # 만큼의 본문을 준다(기존 prose 회귀 테스트와 같은 수준).
        p.write_text("디지털 전환이 공공공간에 미친 영향을 논하시오. 2000자 이상.",
                     encoding="utf-8")
        docs = ingest_all([str(p)], backend="basic")
        spec = {"deliverable": "에세이", "task_type": "essay",
                "goal": "공공공간 에세이", "requirements": []}
        run_unit_draft(docs, spec, Cap(), Config(), content_elements=[],
                       system_extra="[게이트-테스트-지시]")
    assert "[게이트-테스트-지시]" in seen.get("system", "")
    print("OK system_extra(게이트 지시)가 unit 프롬프트에 도달")


def test_unit_floor_defers_to_plan_length():
    # 이중 규제 모순 회귀(실코퍼스 18건): plan 목표 100자 단위는 LengthValidator가
    # 상한 135자(×1.35, 공백 제외)를 걸어 두는데, BoundaryValidator의 고정 200자
    # 하한이 함께 걸리면 어떤 본문도 양쪽을 동시에 만족할 수 없다.
    # 목표가 걸린 단위의 분량 판정은 LengthValidator가 단독으로 맡아야 한다.
    from until.execution.unit_pipeline import _unit_validators
    from until.execution.boundary_guard import BoundaryValidator, LengthValidator
    from until.execution.content_plan import UnitPlan, PlanItem
    from until.execution.units import ResponseUnit

    plan = UnitPlan(unit_index=1, items=[
        PlanItem(element_id="e1", label="핵심 개념", action="write",
                 sufficiency="sufficient", excerpts=["근거 발췌"], target_chars=100),
    ], target_chars=100)
    unit = ResponseUnit(index=1, title="단위", plan=plan)
    vs = _unit_validators(unit, Config(backend="mock"), task_type="report")
    lvs = [v for v in vs if isinstance(v, LengthValidator)]
    bvs = [v for v in vs if isinstance(v, BoundaryValidator)]
    assert lvs, "plan 목표가 있으면 LengthValidator가 걸려야 한다"
    assert bvs, "경계선 기본 검사는 유지돼야 한다"
    hi = int(plan.target_chars * 1.35)
    assert bvs[0].min_body_chars <= hi, (
        f"고정 하한 {bvs[0].min_body_chars}자가 분량 상한 {hi}자(공백 제외)와 모순")
    # 목표가 없는 단위는 기존 200자 하한 유지(과소 작업 방어선).
    bare = ResponseUnit(index=1, title="단위")
    vs2 = _unit_validators(bare, Config(backend="mock"), task_type="report")
    bv2 = [v for v in vs2 if isinstance(v, BoundaryValidator)][0]
    assert bv2.min_body_chars == 200
    print("OK 단위 분량 이중 규제 해소")


def test_empty_draft_never_ends_without_a_question():
    """본문도 없고 질문도 없는 초안은 막다른 페이지다 — 최소한 물을 것은 남긴다.

    실측(2026-08-22, 실 LLM): 골격 없는 task_type(problemset)에 material_gap이
    겹치자 unit 경로가 `# 과제 / 글쓰기과제` 12자를 내놓고 결정 지점도 0개였다.
    그런데 readiness는 material_gap에 "결정 칸에 원료를 답하면 마저 채울 수
    있어요"라고 안내한다 — 가리키는 칸이 없으니 안내가 거짓말이 된다.

    이 상태는 fail-open 셋이 겹쳐 생긴다: ①골격 없는 유형(problemset·code·
    presentation)은 슬롯이 0이라 계획이 비고, ②계획이 비면 계획 기반 검증기가
    사라지며, ③safety_mode(integrity_gate·material_gap)는 본문 하한을 1자로
    낮춘다. 셋 다 각각은 근거가 있으므로 조립 끝에서 결과를 보고 막는다.
    """
    from until.boundary.models import Draft
    from until.execution.unit_pipeline import _ensure_answerable

    nl = "\n"
    # 관측된 그대로 — 제목 + 다섯 글자.
    out = _ensure_answerable("# 과제" + nl * 2 + "글쓰기과제" + nl, "과제")
    decs = Draft.from_text(out).decisions
    assert len(decs) == 1, out
    assert "원료" in decs[0].note and "?" in decs[0].note, decs[0].note

    # 자리표시 마커만 있는 경우도 막다른 길이다 — Draft가 그 마커를 걸러 내므로
    # 문자열 존재만 보고 판정하면 답할 수 없는 마커 하나에 속아 통과시킨다.
    out = _ensure_answerable("# 과제" + nl * 2 + "짧음 [[DECISION: ___]]" + nl, "과제")
    assert len(Draft.from_text(out).decisions) == 1

    # 답할 수 있는 질문이 이미 있으면 건드리지 않는다.
    body = "# 과제" + nl * 2 + "짧음 [[DECISION: 어떤 관점을 택하시겠어요 세 후보 중]]" + nl
    assert _ensure_answerable(body, "과제") == body

    # 본문을 쓸 만큼 썼으면 질문을 덧붙이지 않는다(정상 초안 불변).
    body = "# 보고서" + nl * 2 + "가" * 200 + nl
    assert _ensure_answerable(body, "보고서") == body

    # 짧은 산출물이 정당한 경우(대필 금지 게이트의 학습 보조)에도 내용은 그대로
    # 두고 질문 한 줄만 붙는다 — 본문을 만들어 채우지 않는다.
    out = _ensure_answerable("# 과제" + nl * 2 + "개념 정리만." + nl, "과제")
    assert "개념 정리만." in out
    print("OK 빈 초안은 질문 없이 끝나지 않는다")


if __name__ == "__main__":
    test_units_distinct_and_absent_becomes_question()
    test_one_line_answer_fills_experience()
    test_empty_sentence_triggers_unit_reask()
    test_no_evidence_case_yields_questions_not_prose()
    test_prose_single_unit_regression()
    test_coverage_decision_pending_ok()
    test_mock_generic_and_safety_units_pass()
    test_gate_directive_reaches_unit_prompt()
    test_unit_floor_defers_to_plan_length()
    test_empty_draft_never_ends_without_a_question()
    print("\nUNIT PIPELINE TESTS PASS")
