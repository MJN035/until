"""마감일 파싱·D-day 계산 테스트 (오프라인·결정적, 고정 today 주입)."""
import sys, pathlib
from datetime import date
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.understanding.deadline import parse_deadline, detect_deadline, Deadline


class _Doc:
    def __init__(self, text): self.text = text; self.source = "x"


TODAY = date(2026, 7, 3)


def test_parse_ymd_formats():
    for s in ["2026-07-10", "2026.07.10", "2026/7/10", "2026년 7월 10일"]:
        dl = parse_deadline(s, today=TODAY)
        assert dl and dl.due == date(2026, 7, 10) and dl.had_year, s
    # 잘못된 날짜(2월 30일)는 None.
    assert parse_deadline("2026-02-30", today=TODAY) is None
    print("OK parse YMD formats + invalid")


def test_parse_md_year_inference():
    # 아직 안 지난 월/일 → 올해.
    dl = parse_deadline("7월 10일까지", today=TODAY)
    assert dl and dl.due == date(2026, 7, 10) and not dl.had_year
    # 이미 지난 월/일 → 내년.
    dl = parse_deadline("마감 1/2", today=TODAY)
    assert dl and dl.due == date(2027, 1, 2)
    print("OK MD year inference (this year / next year)")


def test_decimal_version_not_date():
    # 소수·버전·문제번호·분수는 날짜가 아니다(마감 문맥 없으면 무시).
    assert parse_deadline("파이썬 3.11 이상을 사용하시오.", today=TODAY) is None
    assert parse_deadline("표 1/2를 참고하시오", today=TODAY) is None
    assert parse_deadline("버전 2.3 이상 사용", today=TODAY) is None
    # 앞의 오탐 후보가 있어도 뒤의 진짜 마감(월/일 형태)을 잡는다.
    dl = parse_deadline("파이썬 3.11 이상을 사용하시오. 마감: 7월 20일", today=TODAY)
    assert dl and dl.due == date(2026, 7, 20)
    dl = parse_deadline("문제 3.1을 풀고 제출하시오 (마감 7월 10일)", today=TODAY)
    assert dl and dl.due == date(2026, 7, 10)
    # 숫자 형태(7/15)는 마감 문맥이 붙으면 인정.
    dl = parse_deadline("제출 기한: 7/15", today=TODAY)
    assert dl and dl.due == date(2026, 7, 15)
    # (스모크) 번호 참조는 마감 문맥이 인접해도 배제 — 버전/연습문제/절.
    assert parse_deadline("버전 1.2 형식으로 저장하여 제출하시오", today=TODAY) is None
    assert parse_deadline("연습문제는 3.2까지 풀어서 제출", today=TODAY) is None
    assert parse_deadline("안내서 5.2절을 참고해 제출", today=TODAY) is None
    print("OK decimals/versions ignored, context-adjacent dates win (+번호 참조 배제)")


def test_relative_dates():
    # TODAY = 2026-07-03(금). 상대 날짜는 마감 문맥 필수.
    from datetime import timedelta
    dl = parse_deadline("내일까지 제출하시오", today=TODAY)
    assert dl and dl.due == TODAY + timedelta(days=1)
    dl = parse_deadline("제출 기한: 모레", today=TODAY)
    assert dl and dl.due == TODAY + timedelta(days=2)
    # 무수식 요일 = 다가오는 그 요일(금요일인 오늘 → 오늘).
    dl = parse_deadline("금요일까지 제출", today=TODAY)
    assert dl and dl.due == TODAY
    # 이번 주 일요일 = 7/5, 다음 주 월요일 = 7/6.
    dl = parse_deadline("이번 주 일요일까지", today=TODAY)
    assert dl and dl.due == date(2026, 7, 5)
    dl = parse_deadline("다음 주 월요일까지 제출", today=TODAY)
    assert dl and dl.due == date(2026, 7, 6)
    # 문맥 없으면 산문의 요일·내일은 무시.
    assert parse_deadline("내일의 도시를 상상해 보자", today=TODAY) is None
    assert parse_deadline("매주 월요일 수업이 있다", today=TODAY) is None
    # 절대 날짜가 있으면 상대보다 우선.
    dl = parse_deadline("마감 7월 20일, 늦어도 다음 주까지", today=TODAY)
    assert dl and dl.due == date(2026, 7, 20)
    # 관용어 '오늘날'은 마감이 아니다(가짜 D-DAY 방지).
    assert parse_deadline("고대부터 오늘날까지 이어진 도시 변화를 서술하시오.", today=TODAY) is None
    # '내일모레' = 모레(+2), 내일(+1)이 아니다.
    dl = parse_deadline("과제 제출은 내일모레까지입니다", today=TODAY)
    assert dl and dl.due == TODAY + timedelta(days=2)
    # 지난 날짜(참고 언급)가 문맥 있는 상대 마감을 가리지 않는다.
    dl = parse_deadline("6월 1일 강의 내용을 바탕으로 수요일까지 제출", today=TODAY)
    assert dl and dl.due == date(2026, 7, 8)  # 다가오는 수요일
    # 지난 날짜 + 마감 문맥 → 내년 범프는 유지.
    dl = parse_deadline("마감: 6월 1일", today=TODAY)
    assert dl and dl.due == date(2027, 6, 1)
    # 지난 날짜 + 문맥 없음 + 다른 단서 없음 → None(참고 언급).
    assert parse_deadline("6월 1일 강의를 정리해 두어라", today=TODAY) is None
    print("OK relative dates (오늘/내일/모레/요일/이번·다음 주 + 문맥 게이트 + 관용어 + 과거참조)")


def test_time_annotation():
    # 마감 시각 표기를 D-day 라벨에 병기(판정엔 미사용).
    dl = parse_deadline("마감: 2026-07-10 23:59", today=TODAY)
    assert dl and dl.time_str == "23:59" and dl.dday_label(TODAY).endswith("23:59")
    dl = parse_deadline("7월 10일 오후 6시까지 제출", today=TODAY)
    assert dl and dl.time_str == "오후 6시"
    dl = parse_deadline("금요일 자정까지 제출", today=TODAY)
    assert dl and dl.time_str == "자정"
    dl = parse_deadline("제출 기한: 7/15 18시 30분", today=TODAY)
    assert dl and dl.time_str == "18시 30분"
    dl = parse_deadline("제출 기한: 7/15 13시30분", today=TODAY)
    assert dl and dl.time_str == "13시30분"
    # '9시간'(기간)은 시각이 아니다 · 시각 없으면 라벨 불변.
    dl = parse_deadline("마감 7월 10일 (9시간 내 채점)", today=TODAY)
    assert dl and dl.time_str == ""
    dl = parse_deadline("마감: 2026-07-10", today=TODAY)
    assert dl and dl.dday_label(TODAY) == "D-7 · 마감 2026-07-10"
    # (게이트 8회차) 윈도우 절단 조작 방지 — 옛 마감 '18시'가 '8시'로 붙지 않고 새 마감 시각.
    dl = parse_deadline("마감이 7월 1일 18시에서 7월 10일 23:59로 연장되었습니다", today=TODAY)
    assert dl and dl.time_str == "23:59"
    # 같은 표기 재언급 — 문맥 매치 위치·재언급 위치 모두에서 시각 탐지.
    dl = parse_deadline("오늘 3시 회의였습니다. 리포트 제출 기한은 오늘 23:59입니다.", today=TODAY)
    assert dl and dl.time_str == "23:59"
    dl = parse_deadline("7월 10일 과제 안내입니다. 반드시 제출: 7월 10일 23:59", today=TODAY)
    assert dl and dl.time_str == "23:59"
    # 무효 시각(25시/99:99)은 표기하지 않는다.
    dl = parse_deadline("7월 10일 25시까지 제출", today=TODAY)
    assert dl and dl.time_str == ""
    print("OK time annotation (23:59/오후 N시/자정/붙여쓰기/기간 배제 + 게이트8 회귀)")


def test_extension_picks_latest():
    # '연장' 공지 → 여러 후보 중 가장 늦은 날짜.
    dl = parse_deadline("금요일까지였으나 다음 주 월요일까지로 연장합니다", today=TODAY)
    assert dl and dl.due == date(2026, 7, 6)  # 다음 주 월요일
    dl = parse_deadline("제출 기한이 7월 10일에서 7월 17일로 연장되었습니다", today=TODAY)
    assert dl and dl.due == date(2026, 7, 17)
    dl = parse_deadline("마감 연장: 2026-07-10 → 2026-07-24", today=TODAY)
    assert dl and dl.due == date(2026, 7, 24)
    # '연장' 없으면 기존 우선순위(첫 매치) 유지.
    dl = parse_deadline("7월 10일과 7월 17일 중 앞 날짜가 마감", today=TODAY)
    assert dl and dl.due == date(2026, 7, 10)
    # (게이트 7회차) YMD 부분문자열 재매칭 방지 — 연도 있는 옛/새 마감.
    dl = parse_deadline("과제 마감이 2025년 12월 20일에서 2026년 1월 10일로 연장되었습니다.", today=TODAY)
    assert dl and dl.due == date(2026, 1, 10)
    # 숫자형 과거 마감이 내년 범프로 새 마감을 이기지 않는다.
    dl = parse_deadline("과제 마감이 6/20에서 7/10로 연장되었습니다.", today=TODAY)
    assert dl and dl.due == date(2026, 7, 10)
    # 연말 걸침 — 새 마감이 내년 초.
    dl = parse_deadline("과제 마감이 12월 20일에서 1월 10일로 연장되었습니다.", today=date(2026, 12, 25))
    assert dl and dl.due == date(2027, 1, 10)
    # '연장전' 같은 무관 어휘는 연장 모드를 켜지 않는다.
    dl = parse_deadline("제출 2026-07-10. 연장전이 있던 2026-08-01 경기 분석.", today=TODAY)
    assert dl and dl.due == date(2026, 7, 10) and not dl.extended
    # 연장 채택 마감은 라벨에 '연장됨' 병기.
    dl = parse_deadline("제출 기한이 7월 10일에서 7월 17일로 연장되었습니다", today=TODAY)
    assert dl and dl.extended and "연장됨" in dl.dday_label(TODAY)
    # 일반 마감엔 표시 없음.
    dl = parse_deadline("마감: 2026-07-10", today=TODAY)
    assert dl and not dl.extended and "연장" not in dl.dday_label(TODAY)
    print("OK extension picks latest deadline (+gate-7 cases +연장됨 라벨)")


def test_dday_label():
    dl = Deadline(due=date(2026, 7, 10), had_year=True)
    assert dl.days_from(TODAY) == 7 and dl.dday_label(TODAY).startswith("D-7")
    assert Deadline(due=TODAY, had_year=True).dday_label(TODAY).startswith("D-DAY")
    past = Deadline(due=date(2026, 7, 1), had_year=True)
    assert past.dday_label(TODAY).startswith("D+2 (지남)")
    print("OK dday labels D-/D-DAY/D+")


def test_detect_prefers_spec_then_doc():
    dl = detect_deadline({"deadline": "2026-08-01"}, today=TODAY)
    assert dl and dl.due == date(2026, 8, 1)
    # 명세에 없으면 원문에서.
    dl = detect_deadline({"deadline": "미정"}, [_Doc("제출 마감: 2026-07-20")], today=TODAY)
    assert dl and dl.due == date(2026, 7, 20)
    # 아무데도 없으면 None.
    assert detect_deadline({}, [_Doc("자유 제출")], today=TODAY) is None
    print("OK detect spec-then-doc + none")


def test_detect_spec_hallucinated_year_defers_to_doc():
    """라이브 실버그(2026-08-05): 원문 '마감: 8월 20일'(무연도)인데 LLM이 spec에
    '2023-08-20'(지어낸 과거 연도)을 쓰면 원문의 연도 추론이 이겨야 한다."""
    doc = _Doc("과제: 살고 싶은 도시를 골라 서술하시오. 마감: 8월 20일")
    dl = detect_deadline({"deadline": "2023-08-20"}, [doc], today=TODAY)
    assert dl and dl.due == date(2026, 8, 20) and not dl.had_year
    # 미래 연도를 지어내도 월·일이 원문 무연도와 같으면 원문 추론 우선(같은 결과).
    dl = detect_deadline({"deadline": "2027-08-20"}, [doc], today=TODAY)
    assert dl and dl.due == date(2026, 8, 20)
    # 원문에도 '진짜 연도'가 있으면 기존대로 spec 신뢰(정당한 과거 마감 보존).
    dl = detect_deadline({"deadline": "2023-08-20"},
                         [_Doc("마감: 2023년 8월 20일")], today=TODAY)
    assert dl and dl.due == date(2023, 8, 20)
    # 원문에서 마감을 못 찾으면 검증 불가 → spec 유지.
    dl = detect_deadline({"deadline": "2023-08-20"}, [_Doc("자유 제출")], today=TODAY)
    assert dl and dl.due == date(2023, 8, 20)
    # 월·일이 다르면(spec이 다른 근거에서 온 날짜) spec 우선 유지.
    dl = detect_deadline({"deadline": "2026-09-01"}, [doc], today=TODAY)
    assert dl and dl.due == date(2026, 9, 1)
    print("OK spec 지어낸 연도 → 원문 추론 우선")


def test_extension_sample_e2e():
    # 저장소 예제(연장 공지+시각)가 ingest→detect로 정확히 잡힌다.
    from until.capture.ingest import ingest_all
    docs = ingest_all(["examples/sample_extension.txt"])
    dl = detect_deadline({}, docs, today=TODAY)
    assert dl and dl.due == date(2026, 7, 17) and dl.time_str == "23:59" and dl.extended
    assert dl.dday_label(TODAY) == "D-14 · 마감 2026-07-17 23:59 · 연장됨"
    print("OK extension sample e2e (연장+시각+라벨)")


def test_pipeline_and_report_integration():
    from until.config import Config
    from until.pipeline import run
    from until import report
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    md = report.render_markdown_report(res)
    assert isinstance(md, str)
    # 강제로 마감 주입 → 리포트 '제출 준비 점검'에 마감 항목.
    res.deadline = Deadline(due=date(2099, 1, 1), had_year=True)
    md2 = report.render_markdown_report(res)
    assert "제출 준비 점검" in md2 and "마감 2099-01-01" in md2
    print("OK pipeline + report integration")


if __name__ == "__main__":
    test_parse_ymd_formats()
    test_parse_md_year_inference()
    test_decimal_version_not_date()
    test_relative_dates()
    test_time_annotation()
    test_extension_picks_latest()
    test_detect_spec_hallucinated_year_defers_to_doc()
    test_extension_sample_e2e()
    test_dday_label()
    test_detect_prefers_spec_then_doc()
    test_pipeline_and_report_integration()
    print("\nDEADLINE TESTS PASS")
