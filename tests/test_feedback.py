"""P7 — 베타 피드백 로그 테스트 (오프라인·mock, 키 불필요)."""
import json
import tempfile
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.config import Config
from until.pipeline import run, finalize
from until.feedback import (
    FeedbackRecord, record_from_result, append_record, load_records,
    feedback_examples, summarize,
)


def test_record_from_result_and_roundtrip():
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    rec = record_from_result(res, satisfaction=4, backend="mock")
    assert rec.assignment and rec.spec and rec.sources
    assert rec.n_decisions == res.draft.n_decisions
    assert rec.reasks == res.guard.reasks and rec.passed == res.guard.passed
    assert rec.satisfaction == 4 and rec.timestamp  # 타임스탬프 자동 채움
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "fb.jsonl"
        append_record(rec, p)
        append_record(record_from_result(res, backend="mock"), p)  # 만족도 없는 기록
        recs = load_records(p)
        assert len(recs) == 2
        assert recs[0].assignment == rec.assignment
        assert recs[1].satisfaction is None
        # JSONL: 정확히 2줄.
        assert len([ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]) == 2
    print("OK feedback record + JSONL roundtrip")


def test_extraction_signals_recorded_and_summarized():
    # eTL 추출 지표(결정수=추출실패 지표) — 자료 수·추출 글자수·자료당 결정.
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    rec = record_from_result(res, backend="mock")
    assert rec.n_sources is not None and rec.n_sources >= 1
    assert rec.chars_extracted is not None and rec.chars_extracted > 0
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "fb.jsonl"
        append_record(rec, p)
        s = summarize(p)
        assert s["avg_sources"] == rec.n_sources
        assert s["avg_chars_extracted"] == rec.chars_extracted
        # 자료당 결정 = n_decisions / n_sources.
        assert s["decisions_per_source"] == round(rec.n_decisions / rec.n_sources, 2)
    print("OK 추출 지표 기록·집계(자료수·글자수·자료당 결정)")


def test_satisfaction_validation():
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    raised = False
    try:
        record_from_result(res, satisfaction=9)
    except ValueError:
        raised = True
    assert raised, "범위 밖 만족도는 ValueError"
    print("OK satisfaction range validated")


def test_records_capture_finalize_decisions():
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    answers = {i + 1: f"선택 {i+1}" for i in range(res.draft.n_decisions)}
    res = finalize(res, answers, cfg)
    rec = record_from_result(res, backend="mock")
    assert rec.n_final_decisions == res.final_draft.n_decisions == 0
    print("OK record captures finalize remaining decisions")


def test_feedback_examples_shape():
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "fb.jsonl"
        append_record(record_from_result(res, backend="mock"), p)
        ex = feedback_examples(p)
        assert ex and set(ex[0].keys()) == {"spec", "sources"}
        # GEPA 입력으로 바로 쓸 수 있는 형식인지 — spec은 유효 JSON.
        json.loads(ex[0]["spec"])
    print("OK feedback_examples -> GEPA {spec, sources} shape")


def test_summarize_and_empty():
    assert summarize("does_not_exist_____.jsonl") == {"runs": 0}
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "fb.jsonl"
        append_record(record_from_result(res, satisfaction=5, backend="mock"), p)
        append_record(record_from_result(res, satisfaction=3, backend="mock"), p)
        s = summarize(p)
        assert s["runs"] == 2 and s["rated_runs"] == 2
        assert s["avg_satisfaction"] == 4.0 and 0.0 <= s["pass_rate"] <= 1.0
    print("OK summarize aggregates")


def test_readiness_warnings_recorded_and_summarized():
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    rec = record_from_result(res, backend="mock")
    # 준비 점검 경고 수가 기록된다(정수, 0 이상).
    assert isinstance(rec.n_readiness_warnings, int) and rec.n_readiness_warnings >= 0
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "fb.jsonl"
        append_record(rec, p)
        # 구버전 레코드(필드 없음)도 하위호환으로 로드된다.
        with p.open("a", encoding="utf-8") as f:
            f.write('{"assignment":"old","spec":"{}","sources":"s",'
                    '"n_decisions":0,"reasks":0,"passed":true}\n')
        recs = load_records(p)
        assert len(recs) == 2 and recs[1].n_readiness_warnings is None
        s = summarize(p)
        # 평균 준비경고는 값이 있는 레코드만 집계(구버전 None 제외).
        assert s["avg_readiness_warnings"] == float(rec.n_readiness_warnings)
    print("OK readiness warnings recorded + summarized + backward-compat")


def test_decision_categories_recorded():
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    rec = record_from_result(res, backend="mock")
    # 결정이 있으면 성격 목록이 결정 수와 일치.
    if res.draft.n_decisions:
        assert rec.decision_categories and len(rec.decision_categories) == res.draft.n_decisions
        assert all(isinstance(c, str) and c for c in rec.decision_categories)
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "fb.jsonl"
        append_record(rec, p)
        # 구버전 레코드(필드 없음) 하위호환.
        with p.open("a", encoding="utf-8") as f:
            f.write('{"assignment":"old","spec":"{}","sources":"s",'
                    '"n_decisions":0,"reasks":0,"passed":true}\n')
        recs = load_records(p)
        assert recs[1].decision_categories is None
        s = summarize(p)
        cats = s.get("decision_category_counts")
        if rec.decision_categories:
            assert cats and sum(cats.values()) == len(rec.decision_categories)
    print("OK decision categories recorded + summarized + backward-compat")


def test_print_summary_output():
    import io, contextlib
    from until.feedback import print_summary
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "fb.jsonl"
        append_record(record_from_result(res, satisfaction=4, backend="mock"), p)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            print_summary(p)
        out = buf.getvalue()
        assert "베타 피드백 요약" in out and "실행 1회" in out and "만족도 평균 4" in out
        # 빈 로그 → 기록 없음.
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            print_summary(pathlib.Path(d) / "none.jsonl")
        assert "기록 없음" in buf2.getvalue()
    print("OK print_summary output + empty")


def test_quality_sorted_examples():
    from until.feedback import quality_sorted_examples
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "fb.jsonl"
        for spec, warn in [("s2", 2), ("s0", 0), ("sN", None), ("s1", 1)]:
            append_record(FeedbackRecord(assignment="a", spec=spec, sources="x",
                                         n_decisions=1, reasks=0, passed=True,
                                         n_readiness_warnings=warn), p)
        ex = quality_sorted_examples(p)
        # 경고 적은 순, None(신호 없음)은 맨 뒤.
        assert [e["spec"] for e in ex] == ["s0", "s1", "s2", "sN"]
        assert [e["spec"] for e in quality_sorted_examples(p, limit=2)] == ["s0", "s1"]
        assert quality_sorted_examples(p, limit=0) == []  # 0은 '전체'가 아니라 0개
    print("OK quality-sorted examples (warnings asc, None last, limit)")


def test_load_skips_corrupt_lines():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "fb.jsonl"
        good = FeedbackRecord(assignment="a", spec="{}", sources="s",
                              n_decisions=1, reasks=0, passed=True)
        append_record(good, p)
        with p.open("a", encoding="utf-8") as f:
            f.write("{broken json\n")
            f.write('{"unexpected_field": 1}\n')  # TypeError -> skip
        recs = load_records(p)
        assert len(recs) == 1 and recs[0].assignment == "a"
    print("OK corrupt lines skipped")


if __name__ == "__main__":
    for fn in [test_record_from_result_and_roundtrip,
               test_extraction_signals_recorded_and_summarized,
               test_satisfaction_validation,
               test_records_capture_finalize_decisions, test_feedback_examples_shape,
               test_summarize_and_empty, test_readiness_warnings_recorded_and_summarized,
               test_decision_categories_recorded,
               test_print_summary_output, test_quality_sorted_examples,
               test_load_skips_corrupt_lines]:
        fn()
    print("\nFEEDBACK TESTS PASS")
