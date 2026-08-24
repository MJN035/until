"""주차별 질의순번표 → 프로필 학번 → 담당 교수 연결 테스트(오프라인)."""
import sys, pathlib
from types import SimpleNamespace
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.capture.sources.moodle_ws import Announcement
from until.context.inquiry_assignment import (
    normalize_student_id, official_professor_profile, parse_assignment_csv,
    resolve_inquiry_assignment, sheet_csv_url, week_from_title,
)

SHEET = "https://docs.google.com/spreadsheets/d/abc_123/edit?gid=7#gid=7"
CSV = '''"","2주차","권성훈 교수님","심형보 교수님","이진규 교수님","","3주차","다음 교수님"
"","(3/10)","2099-10001","2099-12345","2099-10003","","(3/17)","2099-10004"
"","","2099-10005","2099-10006","2099-10007","","","2099-10008"
"","6주차","문태섭 교수님","윤성로 교수님"
'''


def test_parse_week_assignment_and_deadline():
    got = parse_assignment_csv(CSV, 2, "2099 12345", year=2026,
                               due_previous_day=True, due_time="오후 5시")
    assert got and got.professor == "심형보"
    assert got.class_date.isoformat() == "2026-03-10"
    assert got.due_date.isoformat() == "2026-03-09" and got.due_time == "오후 5시"
    assert parse_assignment_csv(CSV, 2, "2099-99999", year=2026) is None
    assert week_from_title("2주차 질의") == 2
    assert normalize_student_id("학번 209912345") == "2099-12345"
    print("OK 질의순번표 주차·학번 매칭 + 수업 전날 17시 마감")


def test_sheet_url_allowlist():
    got = sheet_csv_url(SHEET)
    assert "gviz/tq" in got and "gid=7" in got and "out%3Acsv" in got
    try:
        sheet_csv_url("https://evil.example/sheets/d/abc")
        raise AssertionError("외부 호스트가 허용됨")
    except ValueError:
        pass
    print("OK Google Sheets 공개 CSV URL allowlist")


def test_official_profile_and_end_to_end():
    index = '''<a href="/research-faculty/faculty/full-time?md=view&amp;profid=p1">심형보 교수</a>'''
    detail = '''<h1>심형보</h1><p>분야 : 제어이론, 분산 제어, 시스템 생물학</p><h2>연구실:</h2>'''
    def fetch(url):
        if "docs.google.com" in url:
            return CSV
        if "profid=p1" in url:
            return detail
        return index

    field, url = official_professor_profile("심형보", fetch)
    assert field == "제어이론, 분산 제어, 시스템 생물학"
    assert "profid=p1" in url
    ann = Announcement(subject="질의 순번 업데이트",
                       body="질의는 매주 수업 전날 오후 5시까지 제출", links=[SHEET])
    got = resolve_inquiry_assignment(title="2주차 질의", student_id="2099-12345",
                                     announcements=[ann], fetch_text=fetch, year=2026)
    assert got and got.professor == "심형보" and "제어이론" in got.professor_field
    src = got.to_source()
    assert "2099-12345" not in src.text  # 학번은 LLM 근거에 절대 노출하지 않음
    assert "아직 듣지 않은" in src.text and "2026-03-09 오후 5시" in src.text
    from until.web import _inquiry_assignment_html
    panel = _inquiry_assignment_html(SimpleNamespace(inquiry_assignment=got))
    assert "심형보 교수" in panel and "2026-03-09" in panel
    assert "학번은 AI에 전달하지 않았어요" in panel and "2099-12345" not in panel
    print("OK 공지→시트→담당 교수→공식 연구 분야 연결(학번 비노출)")


if __name__ == "__main__":
    test_parse_week_assignment_and_deadline()
    test_sheet_url_allowlist()
    test_official_profile_and_end_to_end()
    print("\nINQUIRY ASSIGNMENT TESTS PASS")
