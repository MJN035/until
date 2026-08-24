"""요구사항 원자 분해 테스트 (오프라인·결정적).

논리구조 재설계 1단계: "A, B, C 들을 기술" 문자열 → 셀 수 있는 ContentElement
목록. evidence_kind(특히 user_experience=자료로 불충족)가 축이다.
"""
import json, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.understanding.requirements import (
    ContentElement, extract_content_elements, render_elements,
)
from until.requirement_trace import trace_requirements


def test_requirement_trace_legacy_is_conservative():
    from types import SimpleNamespace
    result = SimpleNamespace(
        spec={"requirements": ["핵심 개념 설명", "본인의 진로 계획"]},
        draft=SimpleNamespace(body="핵심 개념을 설명한다.", decisions=[
            SimpleNamespace(note="본인의 진로 계획을 정해주세요")]),
        content_elements=[], units=[], sources=["[수업자료] 1주차"])
    rows = trace_requirements(result)
    assert [row.status for row in rows] == ["reflected", "decision"]
    assert rows[0].paragraph_index == 1

_COWEEK_REQ = {"requirements": [
    "수강한 강의별 핵심 개념, 새로 알게 된 점, 실습 내용 들을 자유롭게 기술",
    "분량 제한: 강의당 300자 내외",
]}


def test_fallback_splits_enumeration():
    # LLM 없이(폴백) 나열이 3개 요소로 쪼개진다.
    elems = extract_content_elements(_COWEEK_REQ, None, llm=None)
    labels = [e.label for e in elems]
    assert any("핵심 개념" in x for x in labels), labels
    assert any("새로 알게" in x for x in labels), labels
    assert any("실습" in x for x in labels), labels
    # evidence_kind: '새로 알게 된 점'은 본인 경험 — 자료로 채울 수 없다.
    by_label = {e.label: e for e in elems}
    exp = next(e for x, e in by_label.items() if "새로 알게" in x)
    assert exp.evidence_kind == "user_experience"
    concept = next(e for x, e in by_label.items() if "핵심 개념" in x)
    assert concept.evidence_kind in ("lecture_material", "source_document")
    # 분량 제한(형식 요건)은 내용 요소가 아니다.
    assert not any("300자" in x for x in labels)
    print("OK fallback splits enumeration + evidence kinds")


def test_llm_path_with_schema():
    class FakeLLM:
        def __init__(self):
            self.calls = []
        def complete(self, system, user, **kw):
            self.calls.append((kw.get("tag"), kw.get("schema") is not None))
            class R: pass
            r = R()
            r.text = json.dumps({"elements": [
                {"id": "core_concept", "label": "핵심 개념", "required": True,
                 "scope": "per_unit", "evidence_kind": "lecture_material",
                 "source_span": "강의별 핵심 개념"},
                {"id": "new_learning", "label": "새로 알게 된 점", "required": True,
                 "scope": "per_unit", "evidence_kind": "user_experience",
                 "source_span": "새로 알게 된 점"},
                {"id": "core_concept", "label": "중복", "required": True,
                 "scope": "per_unit", "evidence_kind": "lecture_material",
                 "source_span": "x"},  # id 중복 → 제거
                {"id": "bad_kind", "label": "이상 종류", "required": True,
                 "scope": "어딘가", "evidence_kind": "??",
                 "source_span": ""},   # 비정상 값 → 안전 기본값
            ]})
            return r

    llm = FakeLLM()
    elems = extract_content_elements(_COWEEK_REQ, None, llm=llm)
    assert llm.calls == [("requirements", True)]  # 별도 태그 + 스키마 강제
    ids = [e.id for e in elems]
    assert ids.count("core_concept") == 1 and "new_learning" in ids
    bad = next(e for e in elems if e.id == "bad_kind")
    assert bad.evidence_kind == "source_document" and bad.scope == "per_unit"
    print("OK LLM path (separate call + schema) with sanitization")


def test_llm_failure_falls_back():
    class Broken:
        def complete(self, *a, **k):
            raise RuntimeError("api down")
    elems = extract_content_elements(_COWEEK_REQ, None, llm=Broken())
    assert any("핵심 개념" in e.label for e in elems)  # 폴백이 살린다
    print("OK LLM failure falls back to deterministic split")


def test_pipeline_carries_elements():
    import until.pipeline as pl
    from until.config import Config
    cfg = Config(); cfg.backend = "mock"
    res = pl.run(["examples/sample_assignment.txt"], cfg)
    assert hasattr(res, "content_elements")  # 필드 존재(비어도 크래시 없음)
    out = render_elements([ContentElement(id="x", label="핵심 개념",
                                          evidence_kind="lecture_material")])
    assert "핵심 개념" in out and "강의자료" in out
    print("OK pipeline carries content_elements + render")


if __name__ == "__main__":
    test_requirement_trace_legacy_is_conservative()
    test_fallback_splits_enumeration()
    test_llm_path_with_schema()
    test_llm_failure_falls_back()
    test_pipeline_carries_elements()
    print("\nREQUIREMENTS TESTS PASS")
