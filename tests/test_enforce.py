"""준수 강제(생성 루프 내 분량·양식 검증) 테스트 (오프라인·결정적).

갭 1+2: check_length/check_form_fidelity가 '사후 표시'가 아니라 reask를
트리거하는지, 항목 수 불일치가 unknown이 아니라 실패로 잡히는지 검증.
"""
import sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.boundary.models import Draft
from until.execution.boundary_guard import (
    BoundaryGuard, BoundaryValidator, LengthValidator, FormValidator, OnFailAction,
)
from until.understanding.length_target import LengthTarget, check_length

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_formfill import _make_form_hwpx, _DRAFT_BODY

_T300 = LengthTarget(unit="자", min=270, max=330, per_item="강의")


def _item(mark: str, n: int) -> str:
    return f"{mark} 강의명: 테스트 / 수강일시: 2026-07-01\n" + ("내용. " * (n // 3)) + "\n"


def test_length_validator_per_item_delta():
    ok_body = _item("①", 300) + _item("②", 300) + _item("③", 300)
    v = LengthValidator(_T300, expected_items=3)
    assert v.validate(Draft.from_text(ok_body)).passed
    # 한 항목 부족 → 그 항목만 델타와 함께 개별 에러.
    bad = _item("①", 300) + _item("②", 120) + _item("③", 300)
    r = v.validate(Draft.from_text(bad))
    assert not r.passed and len(r.errors) == 1
    assert "②" in r.errors[0] and "더 쓸 것" in r.errors[0] and "약" in r.errors[0]
    print("OK LengthValidator per-item delta")


def test_length_validator_mismatch_not_unknown():
    # 양식 기준 3개 항목이 필요한데 통짜 산문 → unknown이 아니라 실패(mismatch).
    prose = "그냥 산문으로 쓴 결과 보고서 본문입니다. " * 30
    chk = check_length(_T300, prose, expected_items=3)
    assert chk.status == "mismatch"
    r = LengthValidator(_T300, expected_items=3).validate(Draft.from_text(prose))
    assert not r.passed and "항목 수 불일치" in r.errors[0]
    # 기대 수를 모르면 기존대로 unknown(전체 오판 방지) — 검증기는 통과,
    # readiness가 경고로 표면화한다.
    assert check_length(_T300, prose).status == "unknown"
    assert LengthValidator(_T300).validate(Draft.from_text(prose)).passed
    print("OK mismatch is failure, unknown surfaces as warning only")


def test_form_validator():
    from until.capture.ingest import ingest_file
    with tempfile.TemporaryDirectory() as d:
        form_text = ingest_file(_make_form_hwpx(pathlib.Path(d)), backend="basic").text
    v = FormValidator(form_text, form_name="CO-Week 양식")
    good = _DRAFT_BODY + "\n② 강의명: B\n③ 강의명: C\n"
    assert v.validate(Draft.from_text(good)).passed
    r = v.validate(Draft.from_text("산문으로만 쓴 출력입니다. " * 20))
    assert not r.passed
    joined = " ".join(r.errors)
    assert "라벨 누락" in joined and "항목 누락" in joined
    print("OK FormValidator missing labels/items")


def test_reask_loop_triggered_by_length():
    # 1차: ② 부족 → reask(항목별 델타가 에러로 전달) → 2차: 충족 → 통과.
    attempts = []

    def produce(errors, previous):
        attempts.append(list(errors))
        if not attempts[:-1]:  # 첫 시도
            return (_item("①", 300) + _item("②", 100) + _item("③", 300)
                    + "[[DECISION: 인상 깊었던 점 하나: ___]]")
        return (_item("①", 300) + _item("②", 300) + _item("③", 300)
                + "[[DECISION: 인상 깊었던 점 하나: ___]]")

    guard = BoundaryGuard(
        validators=[BoundaryValidator(min_decisions=1),
                    LengthValidator(_T300, expected_items=3)],
        on_fail=OnFailAction.REASK, max_reasks=2)
    draft, report = guard.run(produce)
    assert report.passed and report.reasks == 1
    # reask 사유가 항목별 델타로 기록됐다(수용 기준 1).
    assert any("②" in e and "더 쓸 것" in e for e in report.history[0])
    # 2차 호출의 produce가 그 에러를 받았다(reask 프롬프트에 실림).
    assert any("②" in e for e in attempts[1])
    print("OK reask loop triggered with per-item delta")


def test_pipeline_wires_validators():
    # legacy 기제(통짜 reask 루프·mock 실행 계약) 자체를 검증 — 기본 unit 전환(8/14) 후 명시 고정.
    import until.pipeline as pl
    from until.config import Config
    from until.llm.mock_client import MockClient

    with tempfile.TemporaryDirectory() as d:
        src = _make_form_hwpx(pathlib.Path(d))
        calls = {"n": 0}

        class Improving:
            def __init__(self, inner): self.inner = inner
            def complete(self, system, user, **kw):
                r = self.inner.complete(system, user, **kw)
                if kw.get("tag") != "execution":
                    return r
                calls["n"] += 1
                short = _DRAFT_BODY + "\n② 강의명: B / 수강일시: -\n▷ 강의 내용\n짧다.\n" \
                    + "③ 강의명: C / 수강일시: -\n▷ 강의 내용\n짧다.\n" \
                    + "[[DECISION: 인상 깊었던 점: ___]]"
                full = _DRAFT_BODY.replace("본문입니다.", "내용. " * 100) \
                    + "\n② 강의명: B / 수강일시: -\n▷ 강의 내용\n" + "내용. " * 100 \
                    + "\n③ 강의명: C / 수강일시: -\n▷ 강의 내용\n" + "내용. " * 100 \
                    + "\n[[DECISION: 인상 깊었던 점: ___]]"
                r.text = short if calls["n"] == 1 else full
                return r

        orig = pl.build_client
        pl.build_client = lambda backend, model=None: Improving(MockClient())
        try:
            cfg = Config()
            cfg.pipeline_mode = "legacy"
            cfg.backend = "local"  # mock이 아니어야 강제 활성(빌드는 패치됨)
            # 원료(강의 자료)를 함께 주입 — 없으면 §9-2 '원료 없음' 규칙이 분량
            # 강제를 해제해(지어내기 방지가 우선) 이 테스트의 대상이 사라진다.
            from until.llm.base import SourceDoc
            res = pl.run([str(src)], cfg, extra_context_sources=[
                SourceDoc(title="강의 자료",
                          text="강의 핵심 내용 요지와 실습 기록. " * 30)])
        finally:
            pl.build_client = orig
        # 1차 분량 미달 → reask → 2차 통과(수용 기준 1: 재생성 트리거).
        assert res.guard.reasks >= 1, res.guard.history
        assert res.guard.passed, res.guard.final_errors
        assert any("더 쓸 것" in e for e in res.guard.history[0])
        # mock 백엔드는 기본적으로 강제하지 않는다(데모·오프라인 보호).
        cfg2 = Config(); cfg2.backend = "mock"
        assert pl._build_enforcement_validators(
            cfg2, _T300, res.documents) == []
    print("OK pipeline wires enforcement validators (live only)")


def test_readiness_surfaces_mismatch():
    import until.pipeline as pl
    from until.config import Config
    from until.llm.mock_client import MockClient
    from until.readiness import assess_readiness

    with tempfile.TemporaryDirectory() as d:
        src = _make_form_hwpx(pathlib.Path(d))

        class Prose:
            def __init__(self, inner): self.inner = inner
            def complete(self, system, user, **kw):
                r = self.inner.complete(system, user, **kw)
                if kw.get("tag") == "execution":
                    r.text = ("양식을 무시한 통짜 산문. " * 40
                              + "\n[[DECISION: 확인: ___]]")
                return r

        orig = pl.build_client
        pl.build_client = lambda backend, model=None: Prose(MockClient())
        try:
            cfg = Config(); cfg.backend = "mock"  # 강제 없음 → 사후 점검이 잡아야
            cfg.pipeline_mode = "legacy"  # legacy mock 계약 검증(8/14 unit 기본 전환 후 고정)
            res = pl.run([str(src)], cfg)
        finally:
            pl.build_client = orig
        rd = assess_readiness(res)
        length_items = [i for i in rd.items if i.label == "분량"]
        # 양식 무시 산문이 '✅ 분량'으로 보이지 않는다(갭 2 수용 기준 2).
        assert length_items and length_items[0].status == "warn"
        assert "항목 수 불일치" in length_items[0].message
    print("OK readiness surfaces mismatch as warning")


def test_json_dump_body_rejected():
    """라이브 회귀: 초안 본문이 spec JSON 덤프로 나오면 reask — 산문만 통과.

    실측(2026-08-08, Cerebras): 빈 과제 본문에서 understanding이 자기 지시를
    요구사항으로 착각 → execution이 초안 대신 JSON을 출력 → 가드 통과 → 사용자
    화면에 JSON이 그대로 노출됐다."""
    v = BoundaryValidator(min_decisions=0, min_body_chars=10, forbid_stance=False)
    dumped = ('{\n  "course": {"name": "Canvas course 296405"},\n'
              '  "assignment": {"title": "2주차 질의",\n'
              '    "requirements": ["명세를 정확히 추출", "JSON 형식으로 응답"]},\n'
              '  "task_type": "inquiry"\n}\n'
              "[[DECISION: 2주차 순서에 해당하는지 선택해 주세요]]")
    res = v.validate(Draft(body=dumped))
    assert not res.passed and any("JSON" in e for e in res.errors)
    # 코드펜스로 감싼 JSON도 동일하게 거부.
    fenced = "```json\n" + '{"a": 1, "b": 2, "c": 3}' + "\n```"
    res = v.validate(Draft(body=fenced))
    assert not res.passed and any("JSON" in e for e in res.errors)
    # 정상 산문(중괄호 예시 포함)은 통과.
    prose = ("질문 후보를 정리했다. 예시 코드 {x: 1}를 참고하되, "
             "본문은 산문으로 서술한다. " * 3)
    assert v.validate(Draft(body=prose)).passed
    print("OK JSON dump body rejected")


def test_spec_echo_sanitized():
    """understanding이 자기 지시(명세 추출·JSON 응답)를 요구사항으로 착각한
    출력을 결정적으로 정화한다."""
    from until.understanding.task_spec import sanitize_task_spec
    spec = {
        "deliverable": "JSON 응답",
        "goal": "제공된 자료에서 과제 명세를 정확히 추출하여 제출",
        "requirements": [
            "제공된 자료('2주차 질의' 마크다운 파일)에서 과제 명세를 정확히 추출",
            "추출한 명세를 JSON 형식으로 응답",
            "질의 순번 확인 후 2주차 해당자는 필수 제출",
        ],
        "constraints": ["구조화하여 응답"],
        "open_questions": ["제출 기한이 명시되어 있지 않음"],
    }
    out = sanitize_task_spec(spec)
    assert out["requirements"] == ["질의 순번 확인 후 2주차 해당자는 필수 제출"]
    assert out["constraints"] == []
    assert out["goal"] == "" and out["deliverable"] == ""
    assert out["open_questions"]  # 무해한 항목은 유지
    # 정상 spec은 그대로.
    clean = {"deliverable": "질의서", "goal": "질문 2개 작성",
             "requirements": ["존댓말 문장"], "open_questions": []}
    assert sanitize_task_spec(dict(clean)) == clean
    print("OK spec echo sanitized")


if __name__ == "__main__":
    test_json_dump_body_rejected()
    test_spec_echo_sanitized()
    test_length_validator_per_item_delta()
    test_length_validator_mismatch_not_unknown()
    test_form_validator()
    test_reask_loop_triggered_by_length()
    test_pipeline_wires_validators()
    test_readiness_surfaces_mismatch()
    print("\nENFORCE TESTS PASS")
