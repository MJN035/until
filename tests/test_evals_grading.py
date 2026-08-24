"""사람 채점 eval 하네스 계약(오프라인·mock)."""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.config import Config
from until.evals.goldens import golden_cases
from until.evals.grading import (aggregate_grades, load_grades,
                                 render_grade_table, write_grading_sheet)
from until.evals.runner import main, run_all
from until.evals.metrics import CaseScore, score_output


def test_expanded_goldens_run_mock():
    keys = {c.key for c in golden_cases()}
    expected = {"evidence_report", "reflective_report", "problemset", "hdl_lab"}
    assert expected <= keys
    cfg = Config()
    cfg.backend = "mock"
    rows, _, _ = run_all(cfg, keys=sorted(expected))
    assert len(rows) == 12
    assert {r.assignment_type for r in rows} == expected
    assert all(r.generated_body or r.notes for r in rows)
    assert all(r.type_compliance is not None for r in rows)


def test_grading_sheet_is_self_contained():
    cfg = Config()
    cfg.backend = "mock"
    rows, _, _ = run_all(cfg, keys=["reflective_report"])
    with tempfile.TemporaryDirectory() as d:
        path = write_grading_sheet(rows, pathlib.Path(d))
        text = path.read_text(encoding="utf-8")
        assert "<!doctype html>" in text
        assert "과제 지문" in text and "생성 본문" in text
        assert "until-grades.json" in text and "제출 가능 수준인가?" in text
        assert "http://" not in text and "https://" not in text
        assert "아직 채점하지 않은 결과" in text
        # assignment is shown once per variant; it must not also be duplicated into notes.
        assert all(r.assignment_text.count("[성찰문]") == 1 for r in rows)


def test_grade_in_aggregation_exact(capsys=None):
    specs = [("p1", "problemset", "yes"), ("p2", "problemset", "partial"),
             ("p3", "problemset", "no"), ("h1", "hdl_lab", "yes"),
             ("h2", "hdl_lab", "yes")]
    payload = {
        "manifest": [{"id": i, "assignment_type": kind} for i, kind, _ in specs],
        "grades": [{"id": i, "assignment_type": kind, "grade": grade}
                   for i, kind, grade in specs],
    }
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "grades.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        summary = aggregate_grades(load_grades(path))
        assert summary["problemset"]["submission_ready_rate"] == 1 / 3
        assert summary["hdl_lab"]["submission_ready_rate"] == 1.0
        table = render_grade_table(summary)
        assert "33.3%" in table and "100.0%" in table
        assert main(["--grade-in", str(path)]) == 0


def test_grade_json_rejects_incomplete_duplicate_and_tampered():
    base = {"manifest": [{"id": "x", "assignment_type": "essay"}],
            "grades": [{"id": "x", "assignment_type": "essay", "grade": "yes"}]}
    bad_payloads = [
        {**base, "grades": []},
        {**base, "grades": base["grades"] * 2},
        {**base, "grades": [{**base["grades"][0], "assignment_type": "hdl_lab"}]},
        {"grades": base["grades"]},
    ]
    with tempfile.TemporaryDirectory() as d:
        for n, payload in enumerate(bad_payloads):
            path = pathlib.Path(d) / f"bad-{n}.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            try:
                load_grades(path)
            except ValueError:
                pass
            else:
                raise AssertionError(f"invalid payload accepted: {payload}")


def test_failed_runs_are_visible_but_not_gradeable():
    failed = CaseScore("broken", "unit", notes=["실행 실패: provider down"],
                       title="실패 케이스", assignment_type="essay")
    with tempfile.TemporaryDirectory() as d:
        text = write_grading_sheet([failed], pathlib.Path(d)).read_text(encoding="utf-8")
    assert "품질 채점에서 제외" in text and "provider down" in text
    assert 'data-type="essay"' not in text
    assert "const manifest=[]" in text


def test_type_specific_metrics_positive_and_negative():
    def scored(key, body, source_text=""):
        return score_output(
            key, "unit", body, per_item_range=None, n_items_expected=None,
            whole_min=None, form_text="", profile={}, source_text=source_text)

    evidence_source = "[자료 A] 사실 하나\n[자료 B] 사실 둘"
    assert scored("evidence_report", "자료 A와 자료 B", evidence_source).type_compliance == 1.0
    assert scored("evidence_report", "자료 A만", evidence_source).type_compliance == 0.5
    assert scored("reflective_report", "[[DECISION: 실제 경험]]").type_compliance == 1.0
    assert scored("reflective_report", "내가 실제로 갈등을 해결했다.").type_compliance == 0.0
    assert scored("problemset", "1. 답\n2. 답\n3. 답").type_compliance == 1.0
    assert scored("problemset", "1. 답\n3. 답").type_compliance == 2 / 3
    assert scored("hdl_lab", "측정값은 [[DECISION: 합성 결과]]").type_compliance == 1.0
    fabricated = scored("hdl_lab", "최대 지연은 12.4 ns로 측정되었다.")
    assert fabricated.type_compliance == 0.0


def test_unknown_case_key_is_an_error():
    try:
        main(["definitely-not-a-case"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("unknown key must fail")


if __name__ == "__main__":
    test_expanded_goldens_run_mock()
    test_grading_sheet_is_self_contained()
    test_grade_in_aggregation_exact()
    test_grade_json_rejects_incomplete_duplicate_and_tampered()
    test_failed_runs_are_visible_but_not_gradeable()
    test_type_specific_metrics_positive_and_negative()
    test_unknown_case_key_is_an_error()
    print("EVAL GRADING TESTS PASS")
