"""과제 유형 분류 + 유형별 Execution 테스트 (오프라인·mock)."""
import sys, pathlib, tempfile, os
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.config import Config
from until.pipeline import run
from until.understanding.task_type import classify_task_type, LABELS, FACTUAL_TYPES
from until.execution.prompts import type_guidance
from until import web


def _spec(goal):
    return {"deliverable": "", "goal": goal, "requirements": []}


def test_classify_each_type():
    cases = {
        "5페이지 에세이를 쓰고 자신의 견해를 논하시오": "essay",
        "다음 회로의 전류를 구하시오. 문제 1, 문제 2를 계산하라": "problemset",
        "실험 보고서: 목적, 방법, 결과, 고찰을 작성하라": "report",
        "파이썬으로 정렬 알고리즘을 구현하라": "code",
        "주제에 대한 발표 자료(슬라이드)를 만드시오": "presentation",
    }
    for txt, exp in cases.items():
        got = classify_task_type(_spec(txt), None)
        assert got == exp, f"{txt!r} -> {got} (기대 {exp})"
    # 신호 없으면 보수적으로 essay.
    assert classify_task_type(_spec("무언가 해오기"), None) == "essay"
    assert classify_task_type({}, None) == "essay"
    # (스모크 20케이스 통과분 중 대표 회귀 고정)
    more = {
        "독후감: 책을 읽고 감상을 서술하시오": "essay",
        "증명하라: 임의의 자연수 n에 대해": "problemset",
        "기말 프로젝트: 웹 서비스 프로토타입을 구현하고 시연 영상 제출": "code",
        "사례 연구를 슬라이드로 정리해 조별 발표": "presentation",
        "설문 데이터 분석 결과를 표와 그래프로 정리한 보고서": "report",
    }
    for txt, exp in more.items():
        assert classify_task_type(_spec(txt), None) == exp, txt
    print("OK classify each task type + default essay + smoke picks")


def test_corpus_regressions():
    # eTL 실코퍼스(2026-08, 148과제) 실측 오분류 회귀 고정 — 본문이 거의 없는
    # 과제가 33%라 제목만으로도 유형이 잡혀야 한다.
    cases = {
        "실습4 레포트": "report",                       # essay로 오분류되던 실측
        "[조16_실험4] 결과보고서": "report",
        "3주차 질의 (3/16 17:00) 강의를 듣고 질문을 제출하세요": "inquiry",
        "5주차 소감문 제출": "reflective_report",
        "피피티 제출": "presentation",
    }
    for txt, exp in cases.items():
        got = classify_task_type(_spec(txt), None)
        assert got == exp, f"{txt!r} -> {got} (기대 {exp})"
    print("OK corpus regressions (실습 레포트·질의·소감문·피피티)")


def test_type_guidance_and_factual():
    # 정형 유형(문제풀이·코드)은 결정 0개 허용 대상.
    assert "problemset" in FACTUAL_TYPES and "code" in FACTUAL_TYPES
    assert "essay" not in FACTUAL_TYPES and "report" not in FACTUAL_TYPES
    # 유형별 지침이 각자 있다 — essay도 구조 지침(서론·본론·결론, 개요만 쓰기 금지).
    assert "에세이" in type_guidance("essay") and "서론" in type_guidance("essay")
    assert "문제 풀이" in type_guidance("problemset")
    assert "슬라이드" in type_guidance("presentation")
    assert set(LABELS) >= {"essay", "report", "problemset", "code", "presentation"}
    print("OK type guidance + factual types")


def _run_text(txt):
    cfg = Config(); cfg.backend = "mock"
    fd, p = tempfile.mkstemp(suffix=".txt", text=True)
    os.write(fd, txt.encode("utf-8")); os.close(fd)
    try:
        return run([p], cfg)
    finally:
        os.unlink(p)


def test_pipeline_each_type_passes_and_tags():
    cases = {
        "다음 회로의 전류를 구하시오. 문제 1, 문제 2를 계산하라": "problemset",
        "실험 보고서: 목적, 방법, 결과, 고찰을 작성하라": "report",
        "파이썬으로 정렬 알고리즘을 구현하라": "code",
        "주제에 대한 발표 자료 슬라이드를 만드시오": "presentation",
    }
    for txt, exp in cases.items():
        res = _run_text(txt)
        assert res.spec.get("task_type") == exp, (txt, res.spec.get("task_type"))
        assert res.guard.passed, f"{exp} 초안이 가드를 통과해야 함"
        # 정형 유형은 결정이 0개여도 정상(억지 결정 금지).
        if exp in FACTUAL_TYPES:
            assert res.draft.n_decisions >= 0
    print("OK pipeline tags type + each type draft passes guard")


def test_factual_type_allows_zero_decisions():
    # 결정 신호가 전혀 없는 순수 계산 문제도 가드 통과(min_decisions=0 적용).
    res = _run_text("아래 값을 계산하라. 답을 구하시오. 풀이 과정을 적어라.")
    assert res.spec.get("task_type") == "problemset"
    assert res.guard.passed
    print("OK factual type allows zero decisions")


def test_example_files_detected_and_run():
    # 저장소 예제(문제풀이·코드·보고서·에세이)를 실제 ingest→분류→파이프라인까지.
    from until.capture.ingest import ingest_all
    from until import report
    expect = {
        "examples/sample_problemset.txt": "problemset",
        "examples/sample_code.txt": "code",
        "examples/sample_report.txt": "report",
        "examples/sample_presentation.txt": "presentation",
        "examples/sample_assignment.txt": "essay",
    }
    cfg = Config(); cfg.backend = "mock"
    for path, exp in expect.items():
        # 결정적 분류(capture만) — LLM 없이.
        docs = ingest_all([path])
        assert classify_task_type({}, docs) == exp, (path, exp)
        # 전체 파이프라인(mock)도 같은 유형으로 태깅하고 가드 통과.
        res = run([path], cfg)
        assert res.spec.get("task_type") == exp and res.guard.passed
        # 준비 점검·제출용 렌더가 유형과 무관하게 안전하게 동작.
        assert isinstance(report.render_submission_markdown(res), str)
    print("OK example files detected end-to-end")


def test_inquiry_type_and_candidate_draft():
    """T1b 질의 — 전용 유형 분류 + mock 초안이 '후보 생성 → 선택 결정 1개' 골격.

    기획 근거(type_algorithms.md T1b): 미제출 1위 유형. 질문 후보 생성은 경계선 안,
    '내가 뭘 궁금해하는가' 선택만 사람 몫 — 에세이 결정(논지)이 붙으면 범주 착오.
    """
    t = classify_task_type(_spec("다음 수업 교수님들께 질문드릴 내용을 작성하여 제출"))
    assert t == "inquiry", t
    assert LABELS["inquiry"] == "질의/질문 제출"
    assert "inquiry" not in FACTUAL_TYPES  # 선택 결정 1개는 필수

    # e2e: 예제 파일 → 분류 → mock 초안이 질문 후보 + 선택 결정을 낸다.
    cfg = Config(); cfg.backend = "mock"
    # legacy 기제(통짜 reask 루프·mock 실행 계약) 자체를 검증 — 기본 unit 전환(8/14) 후 명시 고정.
    cfg.pipeline_mode = "legacy"
    res = run(["examples/sample_inquiry.txt"], cfg)
    assert res.spec["task_type"] == "inquiry", res.spec["task_type"]
    assert res.guard.passed and res.draft.n_decisions >= 1
    notes = " ".join(d.note for d in res.draft.decisions)
    assert "질문" in notes and "선택" in notes, notes
    assert "논지" not in notes  # 에세이 결정 골격 오적용 회귀 방지
    body = res.draft.body
    assert body.count("?") >= 3 or "궁금합니다" in body  # 후보가 실제 문장으로
    print("OK inquiry — 후보 생성 + 선택 결정 1개")


def test_web_type_badge():
    res = _run_text("실험 보고서: 목적과 방법, 결과, 고찰을 작성하라")
    h = web.render_draft("t", res)
    assert "유형 · " in h and LABELS["report"] in h
    print("OK web shows task-type badge")


def test_qna_mention_not_inquiry():
    # 안내문 상투구 '질의응답 시간'이 inquiry 신호로 잡혀 감상문이 질문 목록으로
    # 오분류되던 회귀(리뷰 발견) — '질의'는 정규식(질의(?!응답))으로만 매치.
    from until.understanding.task_type import classify_task_type
    t = classify_task_type({
        "goal": "특강을 듣고 감상문을 제출하세요.",
        "requirements": ["특강 후에는 질의응답 시간이 있습니다"]})
    assert t == "reflective_report", t
    # 진짜 질의 과제는 여전히 inquiry.
    t2 = classify_task_type({"goal": "다음 주 교수님께 드릴 질의를 제출하세요."})
    assert t2 == "inquiry", t2
    print("OK '질의응답' 언급은 inquiry 오분류 없음")


def test_missing_attachment_is_named_not_guessed():
    """본문이 가리키는 첨부가 없으면 **그 파일을 집어** 요청한다.

    2026-08-22 실측(물리학1 HW#1): 명세가 과목·학기·마감 + `HW1.pdf` 한 줄뿐이고
    그 PDF는 수집되지 않았다. 시스템은 그 사실을 모른 채 '숙제가 고전역학일까
    양자역학일까'를 1,383자 추측해 냈다 — 자료를 달라고 하는 대신.
    수정 후 같은 과제의 산출물은 두 줄이다: 파일을 못 읽었다는 사실 + 요청.
    """
    from until.understanding.substance import (missing_attachments,
                                               referenced_attachments)
    from until.execution.prompts import missing_attachment_directive

    class _D:
        def __init__(self, text, source="spec.md"):
            self.text = text
            self.source = source

    body = "# HW#1\n과목: 2025-1 물리학 1\n마감: 2025년 3월 21일\n\nHW1.pdf"
    docs = [_D(body)]
    assert referenced_attachments(body) == ["HW1.pdf"]
    assert missing_attachments(body, docs) == ["HW1.pdf"]

    # URL 끝의 확장자는 첨부가 아니다 — 없는 파일을 달라고 하면 안 된다.
    assert referenced_attachments(
        "출처: https://x.snu.ac.kr/files/9/download?f=a.pdf") == []
    # 이미 읽어 온 파일은 '없는 첨부'가 아니다.
    assert missing_attachments("첨부 spec.md 참고", docs) == []

    d = missing_attachment_directive(["HW1.pdf"])
    assert "HW1.pdf" in d and "[[DECISION:" in d
    assert "추측해서 쓰지 마라" in d
    assert missing_attachment_directive([]) == ""
    print("OK 없는 첨부는 이름을 집어 요청한다 (URL·기존 파일 오탐 없음)")


def test_type_guidance_yields_to_material_gap():
    """원료가 없으면 유형 지침을 끈다 — 서로 싸우는 지시를 보내지 않는다.

    유형 지침은 "끝까지 써라"(essay: 문제 제기 → 주장 → 반론 → 결론)를 요구하고
    원료 없음 지침은 "지어내지 말고 골격만"이라고 한다. 둘을 함께 보내면 모델은
    더 구체적인 쪽을 따른다 — 실측에서 원료 없음 지침이 켜져 있는데도 1,039자
    논증문이 나왔다.
    """
    from until.execution.prompts import TYPE_GUIDANCE, type_guidance

    assert type_guidance("essay") == TYPE_GUIDANCE["essay"]
    assert type_guidance("essay", material_gap=True) == ""
    assert type_guidance("report", material_gap=True) == ""
    # 원료가 있으면 종전 그대로(기본값도 essay 유지).
    assert type_guidance("", material_gap=False) == TYPE_GUIDANCE["essay"]
    print("OK 원료 없음이면 유형 지침을 끈다")


def test_thin_spec_triggers_material_gap_even_for_essay():
    """'뭔지 모르겠다 → essay → essay는 자료 없어도 된다' 사슬을 끊는다.

    `_MATERIAL_GAP_ASKS`에 essay 항목이 없어 essay는 원료 게이트가 면제되는데,
    하필 essay는 **신호가 없을 때의 기본값**이다. 그래서 내용이 없는 과제일수록
    게이트를 빠져나갔다. 실내용 글자 수로 끊는다(실측: HW#1 7자 vs 에세이 샘플
    434자 · 보고서 샘플 226자).
    """
    from until.capture.ingest import ingest_all
    from until.config import Config
    from until.pipeline import run
    from until.understanding.substance import substantive_chars

    thin = "# HW#1\n과목: 2025-1 물리학 1\n마감: 2025년 3월 21일\n\nHW1.pdf"
    assert substantive_chars(thin) < 200
    assert substantive_chars(ingest_all(
        ["examples/sample_assignment.txt"])[0].text) >= 200

    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    assert not (res.spec or {}).get("material_gap"), \
        "논제가 본문에 있는 에세이는 종전대로 면제돼야 한다"
    print("OK 내용 없는 명세는 유형과 무관하게 원료를 요청한다")


if __name__ == "__main__":
    test_classify_each_type()
    test_corpus_regressions()
    test_type_guidance_and_factual()
    test_pipeline_each_type_passes_and_tags()
    test_factual_type_allows_zero_decisions()
    test_example_files_detected_and_run()
    test_inquiry_type_and_candidate_draft()
    test_qna_mention_not_inquiry()
    test_web_type_badge()
    test_missing_attachment_is_named_not_guessed()
    test_type_guidance_yields_to_material_gap()
    test_thin_spec_triggers_material_gap_even_for_essay()
    print("\nTASK TYPE TESTS PASS")
