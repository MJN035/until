"""발표 변환형 과제 + 실제 PPTX 출력 테스트(오프라인)."""
import io
import pathlib
import sys
import tempfile
from types import SimpleNamespace
from xml.etree import ElementTree
from zipfile import ZipFile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.boundary.models import Draft
from until.capture.ingest import ingest_file
from until.context.presentation_conversion import (
    conversion_directive,
    find_presentation_predecessors,
    is_generic_presentation_assignment,
    presentation_predecessors_to_sources,
)
from until.presentation_export import parse_slide_markdown, render_presentation_pptx


def _row(aid, title, submitted, body, *, due="", group=None):
    return {"submitted_at": submitted, "body": f"<p>{body}</p>",
            "assignment": {"id": aid, "name": title, "due_at": due,
                           "group_category_id": group}}


def test_predecessor_chain_and_future_filter():
    rows = [
        _row(1, "주제_주제문_개요", "2025-03-20T10:00:00Z", "주제문과 개요"),
        _row(2, "서론 작성", "2025-04-02T10:00:00Z", "첫 서론"),
        _row(3, "서론 수정", "2025-04-18T10:00:00Z", "수정 서론"),
        _row(4, "조별 보고서", "2025-04-20T10:00:00Z", "팀 글", group=9),
        _row(5, "피피티 제출", "2025-06-04T10:00:00Z", "현재", due="2025-06-04T13:00:00Z"),
        _row(6, "기말 과제 글쓰기", "2025-06-20T10:00:00Z", "미래 완성본"),
    ]
    got = find_presentation_predecessors(
        "피피티 제출", rows, current_assignment_id="5",
        current_due_at="2025-06-04T13:00:00Z")
    assert [x["title"] for x in got] == ["주제_주제문_개요", "서론 작성", "서론 수정"]
    assert all("미래" not in x["body"] for x in got)
    no_due = find_presentation_predecessors(
        "피피티 제출", rows, current_assignment_id="5", current_due_at="")
    assert all(x["title"] != "기말 과제 글쓰기" for x in no_due)
    assert is_generic_presentation_assignment("PPT 업로드")
    assert not is_generic_presentation_assignment("인공지능 윤리 발표 자료")
    print("OK 선행 과제 체인 + 미래/조별/현재 과제 제외")


def test_residual_questions_only():
    hits = [{"title": "서론 수정", "stage": "수정 원고",
             "submitted_at": "2025-04-18T10:00:00Z", "body": "수정 원고"}]
    sources = presentation_predecessors_to_sources(hits)
    hint = conversion_directive(sources)
    assert "다시 묻지 말고" in hint
    assert "일부 단락 선택" in hint and "발표 시간 확인" in hint
    assert "디자인" in hint and "묻지 말고" in hint
    assert conversion_directive([]) == ""
    print("OK 이미 아는 정보 유지 + 범위/시간만 질문")


def test_pptx_export_roundtrip():
    body = """# 발표 자료
## 슬라이드 1: 건강한 수면 자세
- 수면 자세가 건강에 미치는 영향을 살펴봅니다.
- 핵심 주장과 발표 순서를 안내합니다.
## 슬라이드 2: 발표 범위
- [[DECISION: 발표할 일부 단락 선택 — 서론 또는 원인 분석]]
- 선택한 문단의 주장과 근거를 압축합니다.
"""
    assert len(parse_slide_markdown(body)) == 2
    result = SimpleNamespace(draft=Draft.from_text(body), final_draft=None)
    data = render_presentation_pptx(result)
    assert data.startswith(b"PK") and len(data) > 3000
    with ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        assert "ppt/slides/slide1.xml" in names and "ppt/slides/slide2.xml" in names
        for name in names:
            if name.endswith(".xml"):
                ElementTree.fromstring(z.read(name))
        assert "직접 정할 것" in z.read("ppt/slides/slide2.xml").decode("utf-8")
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "until.pptx"
        path.write_bytes(data)
        parsed = ingest_file(path, backend="basic")
        assert "건강한 수면 자세" in parsed.text and "발표 범위" in parsed.text
    print("OK 실제 PPTX 패키지·XML·텍스트 왕복")


def test_web_wiring():
    import inspect
    from until import web
    source = inspect.getsource(web.collect_with_materials)
    assert "find_presentation_predecessors" in source
    links = web._submission_links("abc", SimpleNamespace(spec={"task_type": "presentation"}))
    assert "/dl/abc.pptx" in links
    print("OK eTL 수집 + PPTX 다운로드 배선")


if __name__ == "__main__":
    test_predecessor_chain_and_future_filter()
    test_residual_questions_only()
    test_pptx_export_roundtrip()
    test_web_wiring()
    print("\nPRESENTATION CONVERSION TESTS PASS")
