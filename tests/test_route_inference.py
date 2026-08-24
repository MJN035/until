"""spec_clarification LLM 폴백 추정 — 인용 검증 가드와 묻기 유지 폴백."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from until.llm.base import LLMResult
from until.understanding.route_inference import infer_route, is_inferred


class _Doc:
    def __init__(self, source, text):
        self.source, self.text = source, text


class _FakeLLM:
    """지정한 JSON을 돌려주는 가짜 클라이언트(호출 프롬프트 보관)."""

    def __init__(self, payload):
        self.payload, self.last_user = payload, ""

    def complete(self, system, user, *, tag="", json=False, schema=None,
                 documents=None, cache=True):
        self.last_user = user
        out = self.payload if isinstance(self.payload, str) \
            else __import__("json").dumps(self.payload, ensure_ascii=False)
        return LLMResult(text=out, backend="fake", tokens_in=0, tokens_out=0)


_DOCS = [
    _Doc("spec.md", "# 3월 4주차 제출\n\n마감: 3월 31일. 자세한 내용은 공지 참조."),
    _Doc("etl_context/context.md",
         "# eTL 과목 컨텍스트\n\n[공지] 이번 주 실험 결과를 정리하여 보고서로 "
         "제출하십시오. 측정값 표와 오차 분석을 반드시 포함해야 합니다."),
]


def test_verified_inference_replaces_route():
    llm = _FakeLLM({
        "strategy": "evidence_report", "confidence": "high",
        "evidence_quotes": ["실험 결과를 정리하여 보고서로 제출하십시오."],
        "deliverable": "실험 결과 보고서"})
    got = infer_route({"goal": "3월 4주차 제출"}, _DOCS, [], llm)
    assert got is not None and got.strategy == "evidence_report", got
    assert got.actionable and is_inferred(got)
    assert "보고서로 제출" in got.reason  # 채택 근거 인용이 사유에 남는다
    assert "실험 결과를 정리하여" in llm.last_user  # 발췌가 실제로 전달됐다
    print("OK 검증된 추정이 라우트를 교체")


def test_hallucinated_quote_rejected():
    llm = _FakeLLM({
        "strategy": "evidence_report", "confidence": "high",
        "evidence_quotes": ["기말 프로젝트 결과물을 PDF로 제출하시오."],
        "deliverable": "보고서"})
    assert infer_route({}, _DOCS, [], llm) is None
    print("OK 발췌에 없는 인용은 폐기(환각 가드)")


def test_low_confidence_and_unknown_keep_asking():
    for payload in (
            {"strategy": "evidence_report", "confidence": "medium",
             "evidence_quotes": ["실험 결과를 정리하여 보고서로 제출하십시오."],
             "deliverable": "보고서"},
            {"strategy": "unknown", "confidence": "high",
             "evidence_quotes": ["실험 결과를 정리하여 보고서로 제출하십시오."],
             "deliverable": ""}):
        assert infer_route({}, _DOCS, [], _FakeLLM(payload)) is None, payload
    print("OK 확신 부족·unknown은 묻기 유지")


def test_invalid_json_and_no_material_keep_asking():
    assert infer_route({}, _DOCS, [], _FakeLLM("(mock) 판정 불가")) is None
    ok = {"strategy": "evidence_report", "confidence": "high",
          "evidence_quotes": ["뭐든"], "deliverable": "보고서"}
    assert infer_route({}, [_Doc("spec.md", "# 짧음")], [], _FakeLLM(ok)) is None
    assert infer_route({}, _DOCS, [], None) is None
    print("OK 파싱 실패·원료 부족·클라이언트 없음은 묻기 유지")


def test_short_or_unlisted_strategy_rejected():
    # 8자 미만 인용은 우연 일치 위험 — 검증 실격. 표에 없는 strategy도 실격.
    llm = _FakeLLM({
        "strategy": "evidence_report", "confidence": "high",
        "evidence_quotes": ["보고서"], "deliverable": "보고서"})
    assert infer_route({}, _DOCS, [], llm) is None
    llm2 = _FakeLLM({
        "strategy": "non_actionable", "confidence": "high",
        "evidence_quotes": ["실험 결과를 정리하여 보고서로 제출하십시오."],
        "deliverable": ""})
    assert infer_route({}, _DOCS, [], llm2) is None
    print("OK 짧은 인용·제외 판정 시도 실격")


class _ScriptedLLM:
    """tag별로 다른 JSON을 돌려주는 가짜 클라이언트(2단 판정 검증용)."""

    def __init__(self, by_tag):
        self.by_tag = by_tag

    def complete(self, system, user, *, tag="", json=False, schema=None,
                 documents=None, cache=True):
        payload = self.by_tag[tag]
        out = payload if isinstance(payload, str) \
            else __import__("json").dumps(payload, ensure_ascii=False)
        return LLMResult(text=out, backend="scripted", tokens_in=0, tokens_out=0)


def test_stage2_flips_adjacent_pair_on_verified_quote():
    # 1단이 staged_writing으로 채택했지만 실제는 감상문 — 2단 이지선다가
    # reflective로 다운그레이드(인용 검증된 flip만 인정).
    docs = [_Doc("spec.md", "이번 특강을 듣고 느낀 점과 소감을 자유롭게 서술하시오. 강연에서 인상 깊었던 대목과 본인에게 어떤 생각의 변화가 있었는지 구체적으로 적어 제출한다.")]
    llm = _ScriptedLLM({
        "route-inference": {
            "strategy": "staged_writing", "confidence": "high",
            "evidence_quotes": ["느낀 점과 소감을 자유롭게 서술하시오"],
            "deliverable": "감상문"},
        "route-stage2": {
            "choice": "B", "quote": "느낀 점과 소감을 자유롭게 서술하시오"},
    })
    got = infer_route({"goal": "강연 감상"}, docs, [], llm)
    assert got is not None and got.strategy == "reflective_series", got
    print("OK 2단 인접쌍 flip(인용 검증)")


def test_stage2_keeps_stage1_when_quote_unverified():
    # 2단이 다른 후보를 골라도 인용이 발췌에 없으면 flip하지 않는다(1단 유지).
    docs = [_Doc("spec.md", "이번 특강을 듣고 느낀 점과 소감을 자유롭게 서술하시오. 강연에서 인상 깊었던 대목과 본인에게 어떤 생각의 변화가 있었는지 구체적으로 적어 제출한다.")]
    llm = _ScriptedLLM({
        "route-inference": {
            "strategy": "staged_writing", "confidence": "high",
            "evidence_quotes": ["느낀 점과 소감을 자유롭게 서술하시오"],
            "deliverable": "감상문"},
        "route-stage2": {"choice": "B", "quote": "발췌에 없는 지어낸 문장"},
    })
    got = infer_route({"goal": "강연 감상"}, docs, [], llm)
    assert got is not None and got.strategy == "staged_writing", got
    print("OK 2단 인용 미검증이면 1단 유지")


def test_stage2_skipped_for_non_adjacent_strategy():
    # code_project는 인접쌍이 없어 2단을 아예 호출하지 않는다(비용 0).
    docs = [_Doc("spec.md", "제공된 스켈레톤 코드를 완성해 제출하시오. 함수 시그니처와 파일명은 바꾸지 말고 TODO 자리만 구현한다. 표준 입력을 받아 처리하는 프로그램을 작성한다.")]
    called = {"stage2": False}

    class _Guard(_ScriptedLLM):
        def complete(self, system, user, *, tag="", **kw):
            if tag == "route-stage2":
                called["stage2"] = True
            return super().complete(system, user, tag=tag, **kw)

    llm = _Guard({
        "route-inference": {
            "strategy": "code_project", "confidence": "high",
            "evidence_quotes": ["제공된 스켈레톤 코드를 완성해 제출하시오"],
            "deliverable": "코드"},
    })
    got = infer_route({"goal": "구현"}, docs, [], llm)
    assert got is not None and got.strategy == "code_project"
    assert called["stage2"] is False, "인접쌍 아닌데 2단이 호출됨"
    print("OK 인접쌍 아니면 2단 미호출(비용 0)")


def test_clarify_candidates_builds_active_questions():
    # 가드 거절 후 2차 시도 — 라우트를 확정하지 않고 후보 2개+선택 질문으로
    # '묻기'를 능동형으로 바꾼다. strategy는 여전히 spec_clarification이다.
    from until.understanding.route_inference import clarify_candidates
    llm = _FakeLLM({
        "candidates": [
            {"strategy": "evidence_report", "rationale": "제출 안내가 보고서 형식 언급"},
            {"strategy": "staged_writing", "rationale": "서술형 제출 가능성"}],
        "needed_materials": ["과제 지시서 원문", "제출 양식"]})
    got = clarify_candidates({"goal": "3월 4주차 제출"}, _DOCS, [], llm)
    assert got is not None
    route, cands = got
    assert route.strategy == "spec_clarification" and route.actionable
    assert [c["strategy"] for c in cands] == ["evidence_report", "staged_writing"]
    # 선택 질문에 두 후보가 사람 언어로 들어간다.
    assert "보고서" in " ".join(route.questions), route.questions
    assert any("과제 지시서 원문" in q for q in route.questions)
    print("OK 후보 추정이 능동형 질문을 만든다")


def test_clarify_candidates_rejects_bad_payload():
    from until.understanding.route_inference import clarify_candidates
    # 허용 밖 strategy는 걸러지고, 유효 후보가 0이면 None(기존 묻기 유지).
    llm = _FakeLLM({"candidates": [
        {"strategy": "non_actionable", "rationale": "제외해버리기"},
        {"strategy": "직접 만든 유형", "rationale": "?"}],
        "needed_materials": []})
    assert clarify_candidates({}, _DOCS, [], llm) is None
    # 파싱 실패(mock)·클라이언트 없음도 None.
    assert clarify_candidates({}, _DOCS, [], _FakeLLM("(mock) 알 수 없음")) is None
    assert clarify_candidates({}, _DOCS, [], None) is None
    print("OK 후보 검증 — 제외 판정·미지 유형·파싱 실패는 기존 묻기 유지")


def test_pipeline_mock_keeps_spec_clarification():
    # mock 백엔드는 route-inference tag에 비-JSON을 돌려주므로 추정 없이
    # spec_clarification(묻기)이 유지돼야 한다(오프라인 불변식).
    import tempfile
    from until.config import Config
    from until.pipeline import run
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "assignment.txt"
        path.write_text("# 알 수 없는 항목\n\n마감은 다음 주입니다. 자세한 "
                        "내용은 수업 공지를 참고하세요. " * 3, encoding="utf-8")
        result = run([str(path)], Config(backend="mock"))
    assert result.assignment_route.strategy == "spec_clarification"
    assert "route_inferred" not in result.spec
    print("OK mock 파이프라인은 묻기 유지")


if __name__ == "__main__":
    test_verified_inference_replaces_route()
    test_hallucinated_quote_rejected()
    test_low_confidence_and_unknown_keep_asking()
    test_invalid_json_and_no_material_keep_asking()
    test_short_or_unlisted_strategy_rejected()
    test_stage2_flips_adjacent_pair_on_verified_quote()
    test_stage2_keeps_stage1_when_quote_unverified()
    test_stage2_skipped_for_non_adjacent_strategy()
    test_clarify_candidates_builds_active_questions()
    test_clarify_candidates_rejects_bad_payload()
    test_pipeline_mock_keeps_spec_clarification()
    print("\nROUTE INFERENCE TESTS PASS")
