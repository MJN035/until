"""근거 없는 실측 수치 사후 검출(measured_check) 테스트 (오프라인·결정적)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.understanding.measured_check import find_ungrounded_measurements


def test_hdl_lab_ungrounded_detected():
    body = "합성 결과 최대 주파수 250 MHz, LUT 1200개 사용."
    result = find_ungrounded_measurements(body, [], strategy="hdl_lab")
    assert len(result) >= 2, result
    joined = " ".join(result)
    assert "250" in joined and "1200" in joined
    print("OK hdl_lab ungrounded measurements detected")


def test_hdl_lab_grounded_passes():
    body = "합성 결과 최대 주파수 250 MHz, LUT 1200개 사용."
    evidence = ["합성 리포트: 최대 주파수는 250MHz이며 LUT 사용량은 1200개였다."]
    result = find_ungrounded_measurements(body, evidence, strategy="hdl_lab")
    assert result == [], result
    print("OK hdl_lab grounded values pass")


def test_decision_blank_excluded():
    body = "[[DECISION: 합성 결과 250 MHz, LUT 1200개를 실제 값으로 채우세요]]"
    result = find_ungrounded_measurements(body, [], strategy="hdl_lab")
    assert result == [], result
    print("OK values inside [[DECISION: ...]] are excluded")


def test_non_target_strategy_returns_empty():
    body = "합성 결과 최대 주파수 250 MHz, LUT 1200개 사용."
    result = find_ungrounded_measurements(body, [], strategy="staged_writing")
    assert result == []
    result2 = find_ungrounded_measurements(body, [], strategy="lab_report_cycle", stage="plan")
    assert result2 == []
    result3 = find_ungrounded_measurements(body, [])  # strategy/stage 미지정
    assert result3 == []
    print("OK non-target strategies return empty")


def test_lab_report_cycle_result_ungrounded_detected():
    body = "측정값 3.3V, 오차 5%."
    evidence = ["이 실험은 저항값과 전압을 측정하는 것이 목적이다."]
    result = find_ungrounded_measurements(
        body, evidence, strategy="lab_report_cycle", stage="result")
    assert len(result) >= 1, result
    print("OK lab_report_cycle(result) ungrounded measure/error detected")


def test_lab_report_cycle_result_grounded_passes():
    body = "측정값 3.3V, 오차 5%."
    evidence = ["실측 데이터: 3.3V가 측정됐고 오차는 5%였다."]
    result = find_ungrounded_measurements(
        body, evidence, strategy="lab_report_cycle", stage="result")
    assert result == [], result
    print("OK lab_report_cycle(result) grounded values pass")


def test_substring_false_negative_lut_fixed():
    # High 결함 재현: "1200"이 evidence 속 "12000"의 부분 문자열로 우연히 포함돼
    # 과거 구현은 "근거 있음"으로 오판했다. 정확 토큰 매칭이면 반드시 검출돼야 한다.
    body = "합성 결과 LUT 1200개 사용."
    evidence = ["총 게이트 수는 12000이었다."]
    result = find_ungrounded_measurements(body, evidence, strategy="hdl_lab")
    assert result, "1200 != 12000 should be detected as ungrounded"
    print("OK substring false-negative (LUT 1200 vs 12000) now detected")


def test_substring_false_negative_mhz_fixed():
    body = "최대 주파수 250 MHz."
    evidence = ["클럭은 1250MHz였다."]
    result = find_ungrounded_measurements(body, evidence, strategy="hdl_lab")
    assert result, "250 != 1250 should be detected as ungrounded"
    print("OK substring false-negative (250MHz vs 1250MHz) now detected")


def test_substring_false_negative_error_pct_fixed():
    body = "오차 5%."
    evidence = ["측정 결과 저항은 215옴이었다."]
    result = find_ungrounded_measurements(
        body, evidence, strategy="lab_report_cycle", stage="result")
    assert result, "5 != 215 should be detected as ungrounded"
    print("OK substring false-negative (오차 5% vs 215옴) now detected")


def test_readiness_integration_warns_and_passes():
    from until.pipeline import Result
    from until.boundary.models import Draft
    from until.execution.boundary_guard import GuardReport
    from until.readiness import assess_readiness
    from until.context.assignment_router import AssignmentRoute
    from until.llm.base import SourceDoc

    def _res(body, *, source_docs=None):
        d = Draft.from_text(body)
        g = GuardReport(passed=True, attempts=1, reasks=0)
        route = AssignmentRoute(strategy="hdl_lab", reason="test",
                                 required_evidence=(), stage="result")
        return Result(documents=[], spec={"title": "T"}, draft=d, guard=g,
                      source_docs=source_docs or [], assignment_route=route)

    ungrounded = _res("합성 결과 최대 주파수 250 MHz, LUT 1200개 사용. " * 3)
    r = assess_readiness(ungrounded)
    d = {i.label: i for i in r.items}
    # 로드맵 Tier2-6 — 경고→차단 승격(기본값). UNTIL_MEASURED_ENFORCE=0 동작은
    # tests/test_measured_enforce.py에서 별도로 검증한다.
    assert d.get("실측") and d["실측"].status == "fail", d

    grounded = _res(
        "합성 결과 최대 주파수 250 MHz, LUT 1200개 사용. " * 3,
        source_docs=[SourceDoc(title="합성 리포트",
                                text="최대 주파수 250MHz, LUT 1200개 확인됨.")])
    r2 = assess_readiness(grounded)
    assert not any(i.label == "실측" for i in r2.items)
    print("OK readiness integration: warns when ungrounded, silent when grounded")


if __name__ == "__main__":
    test_hdl_lab_ungrounded_detected()
    test_hdl_lab_grounded_passes()
    test_decision_blank_excluded()
    test_non_target_strategy_returns_empty()
    test_lab_report_cycle_result_ungrounded_detected()
    test_lab_report_cycle_result_grounded_passes()
    test_substring_false_negative_lut_fixed()
    test_substring_false_negative_mhz_fixed()
    test_substring_false_negative_error_pct_fixed()
    test_readiness_integration_warns_and_passes()
    print("\nMEASURED CHECK TESTS PASS")
