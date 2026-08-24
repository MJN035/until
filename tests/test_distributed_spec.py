"""강의자료 중간/끝 + 코딩 게시판 분산 명세 연결 테스트."""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.capture.sources.canvas_api import parse_discussion_topics
from until.capture.sources.models import Attachment
from until.config import Config
from until.context.distributed_spec import (
    assignment_identity,
    collect_distributed_spec,
    discussion_candidates,
    distributed_task_type,
    distributed_spec_directive,
    extract_spec_windows,
    needs_distributed_spec,
    rank_reference_names,
)
from until.pipeline import run


def test_identity_and_gate():
    assert assignment_identity("숙제3 제출") == ("3", False)
    assert assignment_identity("Assignment #2") == ("2", False)
    assert assignment_identity("코딩 과제 제출") == ("", True)
    assert needs_distributed_spec("과제 3", "제출하세요")
    assert assignment_identity("실습 4 레포트") == ("4", False)
    assert needs_distributed_spec("실습 4 레포트", "제출하세요")
    assert not needs_distributed_spec("도시 관찰 에세이", "")
    print("OK 모호한 제출함 제목·번호·코딩 신호 감지")


def test_distributed_type_can_be_report_or_code():
    from until.llm.base import SourceDoc
    report = SourceDoc("[분산 과제 명세] 실습 2", "실험 결과와 고찰을 보고서로 제출")
    code = SourceDoc("[분산 과제 명세] 실습 4", "Arduino 소스 코드를 구현하고 zip 제출")
    assert distributed_task_type([report]) == "report"
    assert distributed_task_type([code]) == "code"
    print("OK 실습 회차별 보고서·코드 산출물 판별")


def test_middle_and_end_windows():
    text = ("강의 설명 " * 800 +
            " Homework 3: 다음 네 문제를 풀고 풀이 과정을 PDF로 제출하시오. " +
            "문제 1 뉴턴 법칙을 적용하시오. " + "부록 " * 900)
    got = extract_spec_windows(text, number="3")
    assert "Homework 3" in got and "문제 1" in got
    assert len(got) < len(text) / 2
    assert extract_spec_windows(text, number="2") == ""
    print("OK PDF 중간/끝 명세 주변 발췌 + 다른 회차 차단")


def test_reference_ranking():
    refs = [
        Attachment("1주차 강의자료.pdf", "https://x/files/1"),
        Attachment("3주차 강의자료.pdf", "https://x/files/3"),
        Attachment("13주차 강의자료.pdf", "https://x/files/13"),
        Attachment("외부 링크", "https://x/page"),
    ]
    got = rank_reference_names(refs, "숙제3")
    assert [x.name for x in got] == ["3주차 강의자료.pdf"]
    assert rank_reference_names(refs, "숙제1")[0].name == "1주차 강의자료.pdf"
    assert all("13주차" not in x.name for x in rank_reference_names(refs, "숙제1"))
    print("OK 과제 번호와 같은 주차 자료만 선별")


def test_discussion_parser_and_code_candidate():
    raw = [{"title": "Coding Assignment 안내",
            "message": "<p>Python으로 정렬 함수를 구현하고 source.zip을 제출하세요.</p>",
            "html_url": "/courses/1/discussion_topics/7"}]
    rows = parse_discussion_topics(raw, "https://myetl.snu.ac.kr")
    assert "정렬 함수" in rows[0]["body"] and rows[0]["url"].startswith("https://")
    got = discussion_candidates(rows, "코딩 과제 제출")
    assert len(got) == 1 and got[0].location == "코딩/토론 게시판"
    print("OK 별도 코딩 게시판 명세 수집")


class _FakeAdapter:
    def list_course_files(self, course_id, base_url):
        return [Attachment("3주차 강의자료.pdf", "https://x/files/3")]

    def list_modules(self, course_id, base_url):
        return []

    def list_discussion_topics(self, course_id, base_url):
        return []

    def download(self, attachment, dest_dir):
        path = pathlib.Path(dest_dir) / "lecture3.txt"
        path.write_text("도입 " * 1200 +
                        "Homework 3: 문제 A와 문제 B를 풀고 풀이 PDF를 제출하세요. " +
                        "마무리 " * 500, encoding="utf-8")
        return str(path)


def test_collect_and_pipeline_route():
    srcs = collect_distributed_spec(_FakeAdapter(), "1", "https://x", "숙제3", "제출")
    assert len(srcs) == 1 and srcs[0].title.startswith("[분산 과제 명세]")
    assert "문제 A" in srcs[0].text
    assert "단위별로" in distributed_spec_directive(srcs)
    with tempfile.TemporaryDirectory() as d:
        task = pathlib.Path(d) / "task.txt"
        task.write_text("숙제3 제출", encoding="utf-8")
        cfg = Config(); cfg.backend = "mock"
        result = run([str(task)], cfg, extra_context_sources=srcs)
    assert result.spec["task_type"] == "problemset"
    print("OK 분산 명세 SourceDoc + essay 오분류를 problemset으로 교정")


def test_web_wiring():
    import inspect
    from until import web
    source = inspect.getsource(web.collect_with_materials)
    assert "collect_distributed_spec" in source
    print("OK 웹 eTL 수집 경로 배선")


if __name__ == "__main__":
    test_identity_and_gate()
    test_distributed_type_can_be_report_or_code()
    test_middle_and_end_windows()
    test_reference_ranking()
    test_discussion_parser_and_code_candidate()
    test_collect_and_pipeline_route()
    test_web_wiring()
    print("\nDISTRIBUTED SPEC TESTS PASS")
