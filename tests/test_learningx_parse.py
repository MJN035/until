"""LearningX/Canvas 과제 페이지 파서 단위 테스트 (브라우저/네트워크 불필요)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.capture.sources.learningx_adapter import is_learningx_url, parse_learningx_assignment


def test_parse_learningx_fixture():
    html = pathlib.Path("examples/canvas_fixture/assignment_page.html").read_text(encoding="utf-8")
    raw = parse_learningx_assignment(
        html,
        "https://myetl.snu.ac.kr/courses/302199/assignments/98765",
    )
    assert "나만의 시선으로 읽는 도시" in raw.title
    assert raw.course == "LearningX course 302199"
    assert "6.24(수) 오전 8시" in raw.description
    assert "관찰 방법과 동선" in raw.description
    assert len(raw.attachments) == 1
    assert raw.attachments[0].name == "강의자료.pdf"
    assert raw.attachments[0].url.startswith("https://myetl.snu.ac.kr/courses/302199/files/123456/download")
    print("OK LearningX parse — title/course/description/attachments")


def test_learningx_url_detection():
    assert is_learningx_url("https://myetl.snu.ac.kr/courses/302199/assignments/98765")
    assert not is_learningx_url("https://etl.snu.ac.kr/mod/assign/view.php?id=9")
    print("OK LearningX URL detection")


if __name__ == "__main__":
    test_parse_learningx_fixture()
    test_learningx_url_detection()
    print("\nLEARNINGX PARSE TEST PASS")
