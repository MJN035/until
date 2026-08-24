from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from until.academic_graph import build_assignment_graph
from until.control_tower import inspect_assignment
from until.policy_compiler import compile_policy
from until.student_memory import Outcome, derive_memory


def test_graph_is_deterministic_and_provenance_linked():
    rows = [{"id": "a1", "title": "보고서", "course_id": "c1", "course_name": "연구"}]
    materials = [{"id": "m1", "assignment_id": "a1", "title": "강의자료", "evidence": "3주차"}]
    a = build_assignment_graph(rows, materials=materials).to_dict()
    b = build_assignment_graph(rows, materials=materials).to_dict()
    assert a == b
    assert build_assignment_graph(rows, materials=materials).fingerprint() == build_assignment_graph(
        rows, materials=materials).fingerprint()
    assert any(e["relation"] == "supported_by" and e["evidence"] == "3주차" for e in a["edges"])
    print("OK 학업 그래프 결정성·근거 연결")


def test_policy_compiles_with_source_evidence():
    p = compile_policy("AI 사용은 아이디어 구상에만 허용. 구성: 서론, 분석, 결론. 첨부 파일 2개, APA 방식.")
    assert p.ai_use == "limited"
    assert p.required_sections == ["서론", "분석", "결론"]
    assert p.required_file_count == 2 and p.citation_style == "APA"
    assert all(len(e.source_hash) == 64 and e.excerpt for e in p.evidence)
    print("OK 자연어 정책 컴파일·원문 근거")


def test_memory_requires_repetition_and_is_course_scoped():
    outcomes = [
        Outcome("a1", "c1", comments=("출처가 부족함",)),
        Outcome("a2", "c1", comments=("참고문헌을 보강",)),
        Outcome("a3", "c2", comments=("출처가 부족함",)),
    ]
    rules = derive_memory(outcomes, "c1")
    assert len(rules) == 1 and rules[0].code == "citation"
    assert rules[0].assignment_ids == ("a1", "a2")
    print("OK 반복 결과만 과목별 메모리로 승격")


def test_control_tower_explains_policy_and_memory_effects():
    graph = build_assignment_graph([{"id": "a1", "title": "a1", "course_id": "c1"}])
    policy = compile_policy("AI 사용 가능. 구성: 서론, 결론. 첨부 파일 2개")
    memory = derive_memory([
        Outcome("old1", "c1", comments=("출처 부족",)),
        Outcome("old2", "c1", comments=("참고문헌 부족",)),
    ], "c1")
    report = inspect_assignment(
        "a1", policy=policy, graph=graph, memory=memory,
        draft="서론만 작성", attachment_count=1)
    codes = {f.code for f in report.findings}
    assert report.submit_state == "blocked"
    assert {"required_files_missing", "required_section_missing", "memory:citation"} <= codes
    assert any(f.basis for f in report.findings if f.code == "required_files_missing")
    print("OK 관제실이 현재 규정+과거 결과의 영향을 설명")


def test_local_corpus_builder_has_reproducible_fingerprint():
    import json
    import tempfile
    from tools.build_academic_os import build
    with tempfile.TemporaryDirectory() as d:
        manifest = Path(d) / "manifest.jsonl"
        row = {"assignment_id": "a1", "course_id": "c1", "title": "보고서",
               "description": "AI 사용 가능. 첨부 파일 1개", "attachments": []}
        manifest.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
        first, second = build(manifest), build(manifest)
        assert first == second
        assert first["states"]["blocked"] == 1
        assert len(first["graph_fingerprint"]) == 64
    print("OK 로컬 코퍼스 감사·재현 가능한 그래프 지문")


def test_local_builder_inherits_course_policy():
    import json
    import tempfile
    from tools.build_academic_os import build
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        manifest = root / "manifest.jsonl"
        policies = root / "course-policies.jsonl"
        manifest.write_text(json.dumps({
            "assignment_id": "a1", "course_id": "c1", "title": "보고서",
            "description": "서론과 결론을 작성하세요."}, ensure_ascii=False) + "\n", encoding="utf-8")
        policies.write_text(json.dumps({
            "course_id": "c1", "text": "AI 사용은 아이디어 구상에만 허용합니다."},
            ensure_ascii=False) + "\n", encoding="utf-8")
        report = build(manifest, course_policies_path=policies)["reports"][0]
        codes = {finding["code"] for finding in report["findings"]}
        assert "ai_policy_unclear" not in codes
        assert "required_action:disclose_ai_use" in codes
    print("OK 로컬 감사가 과제 침묵 시 과목 정책 상속")


if __name__ == "__main__":
    test_graph_is_deterministic_and_provenance_linked()
    test_policy_compiles_with_source_evidence()
    test_memory_requires_repetition_and_is_course_scoped()
    test_control_tower_explains_policy_and_memory_effects()
    test_local_corpus_builder_has_reproducible_fingerprint()
    test_local_builder_inherits_course_policy()
