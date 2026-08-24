"""Moodle 과제 페이지 파서 단위 테스트 (브라우저/네트워크 불필요)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from until.capture.sources.moodle import parse_moodle_assignment

SAMPLE = '''
<html><body>
<div class="breadcrumb"><a href="/course/view.php?id=42">미디어와 사회</a></div>
<h2>중간 보고서: 기술과 제도</h2>
<div id="intro" class="box generalbox">
  주어진 자료를 바탕으로 5쪽 보고서를 작성하시오. 최소 2개 자료 인용. 마감 금요일.
  <a href="https://etl.snu.ac.kr/pluginfile.php/123/mod_assign/intro/0/reading1.pdf">reading1.pdf</a>
  <a href="/pluginfile.php/123/mod_assign/intro/0/rubric.docx">평가기준.docx</a>
</div>
<a href="https://other.site/not-a-file.html">관련 링크</a>
</body></html>
'''

def test_parse():
    raw = parse_moodle_assignment(SAMPLE, "https://etl.snu.ac.kr/mod/assign/view.php?id=9")
    assert "중간 보고서" in raw.title
    assert raw.course == "미디어와 사회"
    assert "5쪽 보고서" in raw.description
    # pluginfile 링크 2개만 첨부로(상대경로는 절대경로화), 외부 html 링크는 제외
    assert len(raw.attachments) == 2
    urls = [a.url for a in raw.attachments]
    assert any("reading1.pdf" in u for u in urls)
    assert any(u.startswith("https://etl.snu.ac.kr/pluginfile.php") and "rubric.docx" in u for u in urls)
    print("OK moodle parse — title/course/intro/attachments")

if __name__ == "__main__":
    test_parse(); print("\nMOODLE PARSE TEST PASS")
