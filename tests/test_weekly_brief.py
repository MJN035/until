"""주차별 세미나 안내(공지 첨부) → 그 주차 초안의 원료."""
import pathlib
import sys
import tempfile
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.capture.sources.canvas_api import parse_canvas_announcements
from until.context.etl_materials import _sniff_suffix
from until.context.weekly_brief import (readable_attachments, week_announcements,
                                        week_of, weekly_brief_sources)


class _Att:
    def __init__(self, name, url=""):
        self.name = name
        self.url = url


class _Ann:
    def __init__(self, subject, body="", atts=(), url="u"):
        self.subject = subject
        self.body = body
        self.attachments = list(atts)
        self.url = url


def test_week_matching_is_deterministic():
    """주차는 결정적으로 맞춘다 — 못 찾으면 아무것도 하지 않는다.

    최신 공지를 아무거나 집어 오면 **다른 주차의 연사**가 이 주차 소감문에
    들어간다. 그건 틀린 초안보다 나쁘다(사실을 지어낸 것이 된다).
    """
    assert week_of("3주차 소감문 제출") == 3
    assert week_of("10 주차 안내") == 10
    assert week_of("기말 보고서") is None
    assert week_of("99주차") is None          # 학기에 없는 수 — 오탐 방지

    anns = [_Ann("3주차 세미나 안내"), _Ann("4주차 세미나 안내"),
            _Ann("대체출석 공지", body="3주차에 결석한 학생은..."), _Ann("OT 공지")]
    got = [a.subject for a in week_announcements(anns, 3)]
    assert got == ["3주차 세미나 안내", "대체출석 공지"], got
    assert week_announcements(anns, 0) == []
    print("OK 주차 매칭 결정적 (없으면 아무것도 안 함)")


def test_korean_attachments_are_sniffed_and_read():
    """한글 파일이 흔하다 — hwp(CFB)·hwpx(zip)를 유형별로 갈라야 한다.

    예전 규칙은 PDF와 PK(zip)만 봤다. hwpx는 PK라서 `.docx`로 이름 붙어 잘못된
    파서를 탔고, 이진 hwp는 아무 규칙에도 안 걸려 텍스트 폴백으로 깨졌다.
    """
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "a").write_bytes(b"%PDF-1.7 xx")
    assert _sniff_suffix(d / "a") == ".pdf"
    (d / "b").write_bytes(bytes([0xD0, 0xCF, 0x11, 0xE0]) + b"abcd")
    assert _sniff_suffix(d / "b") == ".hwp"
    with zipfile.ZipFile(d / "c", "w") as z:
        z.writestr("Contents/section0.xml", "<x/>")
    assert _sniff_suffix(d / "c") == ".hwpx"
    with zipfile.ZipFile(d / "e", "w") as z:
        z.writestr("word/document.xml", "<x/>")
    assert _sniff_suffix(d / "e") == ".docx"

    # 이름 기준 필터: 읽을 수 있는 것과 확장자 없는 것만 남긴다(이미지 제외).
    ann = _Ann("3주차", atts=[_Att("poster.png"), _Att("연사소개.hwp"),
                             _Att("안내.hwpx"), _Att("확장자없음")])
    assert [a.name for a in readable_attachments(ann)] == [
        "연사소개.hwp", "안내.hwpx", "확장자없음"]
    print("OK 한글 첨부 유형 판정 + 이름 필터")


def test_announcement_attachments_are_not_discarded():
    """공지 파서가 첨부를 버리지 않는다 — 버리면 연사 안내가 영영 사라진다."""
    data = [{
        "title": "3주차 세미나 안내",
        "message": ('<p>이번 주 연사 안내입니다.</p>'
                    '<a class="instructure_file_link" '
                    'href="https://etl.example/files/77/download?x=1">연사소개.hwp</a>'),
        "posted_at": "2026-03-17T00:00:00Z",
        "html_url": "https://etl.example/courses/1/discussion_topics/9",
    }]
    anns = parse_canvas_announcements(data)
    assert len(anns) == 1
    names = [getattr(a, "name", "") for a in anns[0].attachments]
    assert names and "연사소개.hwp" in names[0], names
    print("OK 공지 첨부 보존")


def test_weekly_brief_becomes_material():
    """그 주차 첨부 텍스트가 SourceDoc(원료)이 된다 — 없으면 빈 목록."""
    anns = [_Ann("3주차 세미나 안내", atts=[_Att("연사소개.hwp")]),
            _Ann("4주차 세미나 안내", atts=[_Att("w4.pdf")])]
    body = "이번 주 연사는 김OO 교수이며 주제는 전력반도체 소자의 열 관리다. " * 6

    srcs = weekly_brief_sources(anns, "3주차 소감문 제출", lambda att: body)
    assert len(srcs) == 1
    assert srcs[0].title.startswith("[3주차 안내]")
    assert "전력반도체" in srcs[0].text
    assert "지어내지 마세요" in srcs[0].text, "근거 밖 창작 금지 지시가 있어야 한다"

    # 4주차 자료가 3주차 과제에 새어 들어가면 안 된다.
    assert "w4" not in srcs[0].title

    # 주차를 못 찾으면 아무것도 안 한다.
    assert weekly_brief_sources(anns, "기말 보고서", lambda att: body) == []
    # 표지 한 장짜리(짧은 추출)는 원료가 아니다.
    assert weekly_brief_sources(anns, "3주차 소감문", lambda att: "짧음") == []
    # 다운로드·파싱 실패는 흡수한다(되묻는 흐름으로 돌아간다).
    def boom(att):
        raise RuntimeError("네트워크 실패")
    assert weekly_brief_sources(anns, "3주차 소감문", boom) == []
    print("OK 주차 안내 → 원료 (누수·짧은 추출·실패 흡수)")


def test_inquiry_turn_detection_is_conservative():
    """'내 차례 아님'과 '표를 못 읽음'을 가른다 — 틀리는 방향이 대칭이 아니다.

    잘못 '내 차례'라고 하면 안 해도 될 걸 하지만, 잘못 '내 차례 아님'이라고 하면
    **진짜 과제를 놓친다.** 그래서 False(=내 차례 아님)는 표가 실제로 채워져
    있다는 증거(다른 학생 학번)가 있을 때만 낸다(사용자 지시 2026-08-23).
    """
    from until.context.inquiry_assignment import student_in_week
    from until.readiness import assess_readiness

    sheet = chr(10).join([
        "1주차,김교수,이교수",
        "(3/5),2025-11111,2025-22222",
        ",2025-33333,",
        "2주차,박교수,최교수",
        "(3/12),2025-44444,2025-17868",
        "3주차,정교수,",
        "(3/19),,",
    ])
    assert student_in_week(sheet, 2, "2025-17868") is True     # 내 차례
    assert student_in_week(sheet, 1, "2025-17868") is False    # 표가 찼는데 내가 없다
    assert student_in_week(sheet, 3, "2025-17868") is None     # 아직 안 채워짐
    assert student_in_week(sheet, 9, "2025-17868") is None     # 없는 주차
    assert student_in_week(sheet, 2, "") is None               # 학번 모름
    assert student_in_week("", 2, "2025-17868") is None

    # 화면에도 '안 해도 되는 주차'로 뜬다(진짜 Result로 확인 — 얇은 스텁은
    # readiness가 실제로 보는 필드를 놓친다).
    from until.config import Config
    from until.pipeline import run
    cfg = Config()
    cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    assert "차례" not in [i.label for i in assess_readiness(res).items]
    res.spec["inquiry_not_my_turn"] = True
    labels = [i.label for i in assess_readiness(res).items]
    assert "차례" in labels, labels
    print("OK 질의 차례 판정 (보수적) + 화면 표기")


def test_other_weeks_material_is_not_this_assignment_material():
    """다른 주차 자료를 원료로 세면 모델이 **관찰한 적 없는 사실**을 쓴다.

    실사용 2026-08-23(통계학실험 「12주차 출석」): 과제 설명이 비어 있는데 과목의
    10주차 자료가 자동 주입돼 '원료 있음'으로 판정됐고, 초안은 이렇게 나왔다 —
    "출석 체크 결과, 시스템에 입력된 데이터는 모든 대상 학생이 해당 주차에
    정상적으로 출석했음을 나타내는 기록으로 남았다". Until은 출석 데이터를 본 적이
    없다. 코퍼스에서 주차가 붙은 과제 90건 전부가 다른 주차 자료를 달고 있었다
    (평균 8건 중 7건).

    **모르면 버리지 않는다** — 한쪽에 주차가 없으면 충돌이 아니다.
    """
    from until.context.weekly_brief import drop_week_mismatched, week_conflicts

    assert week_conflicts("# 12주차 출석", "10주차 출석 [10주차 모듈]")
    assert not week_conflicts("# 12주차 출석", "12주차 출석")
    assert not week_conflicts("# 12주차 출석", "강의계획서")   # 자료에 주차 없음
    assert not week_conflicts("# 출석", "10주차 출석")          # 과제에 주차 없음

    class _S:
        def __init__(self, title):
            self.title, self.text = title, "내용 " * 100

    kept = drop_week_mismatched(
        "# 12주차 출석", [_S("10주차 출석"), _S("12주차 출석"), _S("강의계획서")])
    assert [s.title for s in kept] == ["12주차 출석", "강의계획서"]


def test_week_mismatch_turns_material_gap_back_on():
    """엉뚱한 주차 자료만 붙어 있으면 원료 없음으로 떨어져 자료를 요청한다."""
    import tempfile

    from until.config import Config
    from until.llm.base import SourceDoc
    from until.pipeline import run

    with tempfile.TemporaryDirectory() as d:
        spec = pathlib.Path(d) / "a.md"
        spec.write_text("# 12주차 출석\n\n(과제 설명 없음)\n", encoding="utf-8")
        other = [SourceDoc(title="[수업자료] 10주차 출석", text="출석 안내 " * 60),
                 SourceDoc(title="[수업자료] 9주차 출석", text="출석 안내 " * 60)]
        res = run([str(spec)], Config(backend="mock"), extra_context_sources=other)
        assert res.spec.get("material_gap") is True, res.spec
        assert res.spec.get("week_mismatched_dropped") == 2

        # 그 주차 자료가 있으면 원료로 인정한다 — 소감문·질의가 여기 걸리면 안 된다.
        same = [SourceDoc(title="[수업자료] 12주차 출석 자료", text="12주차 수업 " * 60)]
        res2 = run([str(spec)], Config(backend="mock"), extra_context_sources=same)
        assert not res2.spec.get("material_gap"), res2.spec
        assert not res2.spec.get("week_mismatched_dropped")


if __name__ == "__main__":
    test_week_matching_is_deterministic()
    test_korean_attachments_are_sniffed_and_read()
    test_announcement_attachments_are_not_discarded()
    test_weekly_brief_becomes_material()
    test_inquiry_turn_detection_is_conservative()
    test_other_weeks_material_is_not_this_assignment_material()
    test_week_mismatch_turns_material_gap_back_on()
    print("\nWEEKLY BRIEF TESTS PASS")
