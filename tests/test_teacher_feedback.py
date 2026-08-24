"""교수 피드백 학습(제출물 코멘트·루브릭 → 초안 참고) 테스트 — 오프라인·결정적."""
import sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.capture.sources.canvas_api import parse_my_feedback
from until.capture.sources.models import CourseRef
from until.context.teacher_feedback import (
    clear_feedback, collect_feedback_entries, disable_feedback,
    feedback_hint, feedback_summary, load_feedback, save_feedback,
)


def _sub(comments=(), rubric=None, crits=None, me=42, name="리포트 1", group=None):
    return {
        "user_id": me, "submitted_at": "2026-06-01T00:00:00Z", "grade": "A",
        "assignment": {"name": name, "rubric": crits or [],
                       "group_category_id": group},
        "submission_comments": [
            {"author_id": aid, "comment": text} for aid, text in comments
        ],
        "rubric_assessment": rubric,
    }


def test_parse_feedback():
    crits = [{"id": "c1", "description": "논증", "points": 10}]
    data = [
        _sub(comments=[(7, "인용이 부족합니다."), (42, "감사합니다!")],  # 42=본인
             rubric={"c1": {"points": 7, "comments": "근거 보강 필요"}}, crits=crits),
        _sub(comments=[]),                       # 피드백 없음 → 제외
        "not-a-dict",                            # 방어
    ]
    out = parse_my_feedback(data)
    assert len(out) == 1
    e = out[0]
    assert e["comments"] == ["인용이 부족합니다."]      # 내 코멘트 제외
    assert e["rubric"] == ["논증: 근거 보강 필요 (7/10점)"]
    assert e["assignment"] == "리포트 1" and e["grade"] == "A"
    print("OK parse feedback (본인 코멘트 제외·루브릭 해석)")


def test_parse_feedback_guards():
    # 조별 과제 — 팀원 코멘트가 '교수 피드백'으로 새지 않음(개인정보)
    assert parse_my_feedback([_sub(comments=[(7, "팀원 코멘트")], group=5)]) == []
    # user_id 미상 → 코멘트 수집 안 함(fail-closed) — 루브릭은 유지
    crits = [{"id": "c1", "description": "논증", "points": 10}]
    out = parse_my_feedback([_sub(comments=[(7, "누군가의 말")], me=None,
                                  rubric={"c1": {"points": 7}}, crits=crits)])
    assert len(out) == 1 and out[0]["comments"] == []
    assert out[0]["rubric"] == ["논증 (7/10점)"]
    assert parse_my_feedback([_sub(comments=[(7, "말")], me=None)]) == []
    # 기준 매칭 실패해도 실점수는 보존 + 정보 0인 빈 항목은 스킵
    out = parse_my_feedback([_sub(rubric={"cX": {"points": 7}})])
    assert len(out) == 1 and out[0]["rubric"] == ["기준 (7점)"]
    assert parse_my_feedback([_sub(rubric={"cX": {}})]) == []
    print("OK parse feedback guards (조별 제외·fail-closed·점수 보존)")


class FakeAdapter:
    def __init__(self, by_course):
        self.by_course = by_course

    def list_my_feedback(self, course_id, base_url):
        if self.by_course.get(course_id) is Exception:
            raise RuntimeError("boom")
        return self.by_course.get(course_id, [])


def test_collect_and_hint():
    fb1 = parse_my_feedback([_sub(comments=[(7, "출처 표기가 빠졌습니다.")])])
    ad = FakeAdapter({"c1": fb1, "c2": Exception})
    courses = [CourseRef(id="c1", name="글쓰기"), CourseRef(id="c2", name="실패과목")]
    entries = collect_feedback_entries(ad, "https://x", courses)
    assert len(entries) == 1 and entries[0]["course"] == "글쓰기"
    hint = feedback_hint(entries)
    assert "교수 피드백" in hint and "출처 표기가 빠졌습니다." in hint
    assert "이번 요구를 따른다" in hint            # 경계선 문구
    assert feedback_hint([]) == ""
    summ = feedback_summary(entries)
    assert "1건" in summ and "출처 표기" in summ
    print("OK collect + hint (과목 실패 스킵·경계선 문구)")


def test_collect_newest_courses_first():
    # Canvas /courses는 사실상 오래된 순 — id 내림차순(최신 개설)으로 정렬 후 상한 적용
    from until.context.teacher_feedback import MAX_COURSES
    newest = str(MAX_COURSES + 4)
    fb = parse_my_feedback([_sub(comments=[(7, "최근 과목 피드백")])])
    ad = FakeAdapter({newest: fb})
    courses = [CourseRef(id=str(i), name=f"과목{i}")           # 오래된 순(1..N)
               for i in range(1, MAX_COURSES + 5)]
    courses.append(CourseRef(id="abc", name="비숫자 id"))       # int 변환 실패 → 0 취급
    entries = collect_feedback_entries(ad, "https://x", courses)
    assert len(entries) == 1 and entries[0]["comments"] == ["최근 과목 피드백"]
    print("OK collect 최신 과목 우선 (id 내림차순 후 상한)")


def test_save_feedback_retry():
    # Windows 원자 교체가 일시적으로 막혀도(PermissionError) 재시도 후 저장 성공
    entries = [{"assignment": "리포트", "comments": ["코멘트"], "rubric": [],
                "submitted_at": "2026-06-01", "grade": ""}]
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / "teacher_feedback.json"
        orig = pathlib.Path.replace
        calls = {"n": 0}

        def flaky(self, target):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("locked")
            return orig(self, target)

        pathlib.Path.replace = flaky
        try:
            save_feedback(path, entries)
        finally:
            pathlib.Path.replace = orig
        assert calls["n"] == 3
        got, _ = load_feedback(path)
        assert got and got[0]["comments"] == ["코멘트"]
        # 5회 전부 실패 → 직접 쓰기 폴백(조용한 저장 실패 방지)
        def always_fail(self, target):
            raise PermissionError("locked")

        pathlib.Path.replace = always_fail
        try:
            save_feedback(path, [{"assignment": "폴백", "comments": ["직접 쓰기"],
                                  "rubric": [], "submitted_at": "", "grade": ""}])
        finally:
            pathlib.Path.replace = orig
        got2, _ = load_feedback(path)
        assert got2 and got2[0]["assignment"] == "폴백"
    print("OK save retry (재시도·직접 쓰기 폴백)")


def test_store_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / "teacher_feedback.json"
        assert load_feedback(path) == ([], False)
        entries = [{"assignment": "리포트", "comments": ["더 구체적으로"],
                    "rubric": [], "submitted_at": "2026-06-01", "grade": ""}]
        save_feedback(path, entries)
        got, disabled = load_feedback(path)
        assert not disabled and got[0]["comments"] == ["더 구체적으로"]
        disable_feedback(path)
        assert load_feedback(path) == ([], True)
        clear_feedback(path)
        assert not path.exists()
        # 손상 파일 방어
        path.write_text("{bad", encoding="utf-8")
        assert load_feedback(path) == ([], False)
        print("OK store roundtrip (저장/끔/삭제/손상)")


def test_readiness_shows_feedback():
    from until.config import Config
    from until.pipeline import run
    from until.readiness import assess_readiness
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    base = {i.label for i in assess_readiness(res).items}
    assert "피드백" not in base                     # 피드백 없으면 항목도 없음
    res.teacher_feedback = [{"assignment": "지난 리포트",
                             "comments": ["인용을 늘리세요"], "rubric": []}]
    items = {i.label: i for i in assess_readiness(res).items}
    assert "피드백" in items and items["피드백"].status == "info"
    assert "인용을 늘리세요" in items["피드백"].message
    from until import web
    rendered = web.render_draft("feedback-session", res)
    assert "이전 교수 피드백에서 만든 점검 규칙" in rendered
    assert "이번 초안의 프롬프트와 제출 전 점검에 반영" in rendered
    print("OK readiness 피드백 항목 (info)")


def test_web_combined_autolearn():
    from until import web
    from until.capture.sources.canvas_api import parse_my_submissions
    with tempfile.TemporaryDirectory() as td:
        vstore = pathlib.Path(td) / "voice_profile.json"
        fstore = pathlib.Path(td) / "teacher_feedback.json"
        oldv, oldf = web._VOICE_STORE_LOCAL, web._FEEDBACK_STORE_LOCAL
        web._VOICE_STORE_LOCAL, web._FEEDBACK_STORE_LOCAL = vstore, fstore

        class Combined:
            def list_courses(self, base, include_past=True):
                return [CourseRef(id="c1", name="글쓰기")]

            def list_my_submissions(self, cid, base):
                raw = [{"submitted_at": "2026-06-01T00:00:00Z",
                        "submission_type": "online_text_entry",
                        "body": "저는 이렇게 생각합니다. 정리했습니다.",
                        "assignment": {"group_category_id": None},
                        "attachments": []}]
                return parse_my_submissions(raw, "https://x")

            def list_my_feedback(self, cid, base):
                return parse_my_feedback([_sub(comments=[(7, "좋은 시도입니다.")])])

        try:
            nv, nf = web._maybe_autolearn_etl(Combined(), "https://x")
            assert (nv, nf) == (1, 1) and vstore.exists() and fstore.exists()
            # 힌트·표시줄에 피드백 반영
            assert "좋은 시도입니다." in web._stored_feedback_hint()
            note = web._voice_note_html("tok")
            assert "교수 피드백 1건" in note
            # 재실행 안 함
            assert web._maybe_autolearn_etl(Combined(), "https://x") == (0, 0)
            # 끄기 → 둘 다 비활성(빈 힌트). 표시줄은 사라지지 않고 '다시 켜기'
            # 손잡이를 남긴다(막다른 길 방지 — 리뷰 발견 수정).
            from until.context.teacher_feedback import disable_feedback as _df
            from until.context.voice_autolearn import disable_stored_voice as _dv
            _dv(vstore); _df(fstore)
            assert web._stored_feedback_hint() == ""
            off_note = web._voice_note_html("tok")
            assert "다시 켜기" in off_note and "/voice/relearn" in off_note
            assert "교수 피드백 1건" not in off_note  # 학습 내용은 표시 안 함
        finally:
            web._VOICE_STORE_LOCAL, web._FEEDBACK_STORE_LOCAL = oldv, oldf
    print("OK web combined autolearn (문체+피드백 1패스·끄기)")


if __name__ == "__main__":
    test_parse_feedback()
    test_parse_feedback_guards()
    test_collect_and_hint()
    test_collect_newest_courses_first()
    test_save_feedback_retry()
    test_store_roundtrip()
    test_readiness_shows_feedback()
    test_web_combined_autolearn()
    print("\nTEACHER FEEDBACK TESTS PASS")
