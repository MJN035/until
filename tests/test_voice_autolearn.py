"""문체 자동 학습(eTL 제출물 → VoiceProfile) 테스트 — 오프라인·결정적·LLM 0."""
import json
import sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.capture.sources.canvas_api import parse_my_submissions
from until.capture.sources.models import CourseRef
from until.context.voice import VoiceProfile
from until.context.voice_autolearn import (
    clear_stored_voice, collect_voice_texts, disable_stored_voice,
    learn_voice_profile, learn_voice_profile_with_stats, load_stored_voice,
    load_stored_voice_stats, save_stored_voice,
)

BASE = "https://myetl.snu.ac.kr"


def _sub(submitted_at="2026-06-01T00:00:00Z", body="", atts=(), group=None,
         stype="online_upload"):
    return {
        "submitted_at": submitted_at, "submission_type": stype, "body": body,
        "assignment": {"group_category_id": group},
        "attachments": [
            {"display_name": n, "url": f"{BASE}/files/{i}/download"}
            for i, n in enumerate(atts, 1)
        ],
    }


def test_parse_filters():
    data = [
        _sub(atts=("보고서.docx",)),                       # 정상 — 채택
        _sub(body="<p>온라인 텍스트 제출입니다.</p>",       # 온라인 텍스트 — 채택
             stype="online_text_entry"),
        _sub(atts=("팀플.docx",), group=7),                # 조별 과제 — 제외
        _sub(submitted_at="", atts=("미제출.docx",)),      # 미제출 — 제외
        _sub(atts=("발표.pptx", "사진.png")),              # 텍스트 포맷 아님 → 첨부 0 → 제외
        "not-a-dict", 42,                                  # 비-dict 방어
    ]
    out = parse_my_submissions(data, BASE)
    assert len(out) == 2
    assert out[0]["attachments"][0].name == "보고서.docx"
    assert "온라인 텍스트 제출입니다." in out[1]["body"]
    assert out[1]["attachments"] == []
    print("OK parse filters (조별·미제출·비텍스트 제외)")


class FakeAdapter:
    """list_my_submissions/download만 흉내 — 네트워크 없음."""

    def __init__(self, subs_by_course, texts_by_name):
        self.subs = subs_by_course
        self.texts = texts_by_name
        self.listed = []

    def list_my_submissions(self, course_id, base_url):
        self.listed.append(course_id)
        if self.subs.get(course_id) is Exception:
            raise RuntimeError("boom")
        return self.subs.get(course_id, [])

    def download(self, attachment, dest_dir):
        p = pathlib.Path(dest_dir) / attachment.name
        p.write_text(self.texts[attachment.name], encoding="utf-8")
        return str(p)


def _courses(*ids):
    return [CourseRef(id=i, name=f"과목{i}") for i in ids]


def test_collect_and_learn():
    subs = {
        "c1": parse_my_submissions([_sub(atts=("글1.txt",))], BASE),
        "c2": Exception,  # 한 과목 실패해도 나머지는 계속
        "c3": parse_my_submissions(
            [_sub(body="온라인 제출 본문입니다. 그렇게 생각합니다.",
                  stype="online_text_entry")], BASE),
    }
    texts = {"글1.txt": "이 보고서는 캠퍼스를 다룹니다. 저는 그렇게 판단했습니다."}
    ad = FakeAdapter(subs, texts)
    got = collect_voice_texts(ad, BASE, _courses("c1", "c2", "c3"))
    assert len(got) == 2 and any("캠퍼스" in t for t in got)
    profile, n = learn_voice_profile(ad, BASE, _courses("c1", "c2", "c3"))
    assert n == 2 and profile.n_samples == 2
    assert profile.ending_style == "합니다체"
    print("OK collect + learn (과목 실패 스킵, 합니다체 감지)")


def test_collect_newest_courses_first():
    # Canvas /courses는 사실상 오래된 순 — id 내림차순(최신 개설)으로 정렬 후 상한 적용.
    # (이전엔 [:MAX_COURSES] 후 정렬이라 과목이 많은 사용자는 가장 오래된 과목에서만 학습)
    from until.context.voice_autolearn import MAX_COURSES
    newest = str(MAX_COURSES + 4)
    subs = {newest: parse_my_submissions(
        [_sub(body="최신 과목 제출입니다.", stype="online_text_entry")], BASE)}
    ad = FakeAdapter(subs, {})
    courses = _courses(*[str(i) for i in range(1, MAX_COURSES + 5)])  # 오래된 순
    courses.append(CourseRef(id="abc", name="비숫자 id"))              # 방어: 0 취급
    got = collect_voice_texts(ad, BASE, courses)
    assert got == ["최신 과목 제출입니다."]
    assert ad.listed[0] == newest and "1" not in ad.listed
    assert len(ad.listed) == MAX_COURSES
    print("OK collect 최신 과목 우선 (id 내림차순 후 상한·비숫자 id 방어)")


def test_store_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / "voice_profile.json"
        # 없음 → (None, enabled)
        assert load_stored_voice(path) == (None, False, 0)
        prof = VoiceProfile(ending_style="한다체", avg_sentence_len=30,
                            frequent_terms=["캠퍼스"], n_samples=4)
        stats = {"courses_total": 25, "courses_scanned": 20,
                 "submitted_total": 84, "eligible_submissions": 51,
                 "samples_used": 30, "submitted_total_exact": True,
                 "sample_cap": 30}
        save_stored_voice(path, prof, n_docs=4, stats=stats)
        got, disabled, n = load_stored_voice(path)
        assert not disabled and n == 4
        assert got is not None and got.ending_style == "한다체"
        assert got.frequent_terms == ["캠퍼스"]
        # 원문은 저장 안 함 — 파일에 프로파일 필드만
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert set(raw) == {"v", "disabled", "n_docs", "learned_at", "profile", "stats"}
        assert load_stored_voice_stats(path) == stats
        # 끄기 → 프로파일 None + disabled
        disable_stored_voice(path)
        got2, disabled2, _ = load_stored_voice(path)
        assert got2 is None and disabled2
        # 다시 학습(삭제) → 파일 없음
        clear_stored_voice(path)
        assert not path.exists()
        print("OK store roundtrip (저장/끄기/삭제)")


def test_store_defensive():
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / "voice_profile.json"
        path.write_text("{broken json", encoding="utf-8")
        assert load_stored_voice(path) == (None, False, 0)  # 손상 → 무해
        path.write_text(json.dumps({"v": 99, "profile": {}}), encoding="utf-8")
        assert load_stored_voice(path)[0] is None            # 미래 버전 → 무시
        # 표본 0건 마커 — 프로파일 적용 없음(재스캔 방지 기록만)
        save_stored_voice(path, VoiceProfile(), n_docs=0)
        assert load_stored_voice(path)[0] is None
        print("OK store defensive (손상·버전·0건 마커)")


def test_bundle_priority():
    from until.context.bundle import assemble_context
    stored = VoiceProfile(ending_style="합니다체", n_samples=5)
    with tempfile.TemporaryDirectory() as td:
        pathlib.Path(td, "sample.txt").write_text(
            "나는 이렇게 생각한다. 그래서 그렇게 판단한다.", encoding="utf-8")
        # voice_dir(직접 올린 글)가 자동 학습 프로파일보다 우선
        ctx = assemble_context({}, voice_dir=td, voice_profile=stored)
        assert ctx.voice.ending_style == "한다체"
    # voice_dir 없으면 자동 학습 프로파일 사용
    ctx2 = assemble_context({}, voice_profile=stored)
    assert ctx2.voice.ending_style == "합니다체" and ctx2.voice_hint
    # 둘 다 없으면 빈 프로파일(힌트 없음)
    ctx3 = assemble_context({})
    assert ctx3.voice.n_samples == 0 and ctx3.voice_hint == ""
    print("OK bundle priority (업로드 > 자동 학습 > 없음)")


def test_web_autolearn_gate():
    from until import web
    with tempfile.TemporaryDirectory() as td:
        store = pathlib.Path(td) / "voice_profile.json"
        old = web._VOICE_STORE_LOCAL
        web._VOICE_STORE_LOCAL = store
        try:
            subs = {"c1": parse_my_submissions([_sub(atts=("글1.txt",))], BASE)}
            ad = FakeAdapter(subs, {"글1.txt": "저는 이렇게 생각합니다. 정리했습니다."})
            ad.list_courses = lambda base, include_past=True: _courses("c1")
            # 1) 파일 없음 → 학습·저장 (FakeAdapter는 피드백 미지원 → 0)
            assert web._maybe_autolearn_etl(ad, BASE) == (1, 0)
            assert store.exists()
            prof, _, n = load_stored_voice(store)
            assert prof is not None and n == 1
            # 2) 이미 있음 → 재실행 안 함
            assert web._maybe_autolearn_etl(ad, BASE) == (0, 0)
            # 3) 끔 → 파일이 존재하므로 재수집 안 함
            disable_stored_voice(store)
            assert web._maybe_autolearn_etl(ad, BASE) == (0, 0)
            assert web._stored_voice()[0] is None
            # 4) 미지원 어댑터(SSO/WS) → 아무 것도 안 함
            clear_stored_voice(store)
            assert web._maybe_autolearn_etl(object(), BASE) == (0, 0)
            assert not store.exists()
            # 5) 조회 실패 → 저장하지 않음(다음 인박스에서 재시도)
            bad = FakeAdapter({}, {})
            bad.list_courses = lambda base, include_past=True: (_ for _ in ()).throw(
                RuntimeError("network"))
            assert web._maybe_autolearn_etl(bad, BASE) == (0, 0)
            assert not store.exists()
        finally:
            web._VOICE_STORE_LOCAL = old
    print("OK web autolearn gate (1회 학습·끔·미지원·실패 무손상)")


def test_web_voice_note_and_routes_exist():
    from until import web
    with tempfile.TemporaryDirectory() as td:
        store = pathlib.Path(td) / "voice_profile.json"
        old = web._VOICE_STORE_LOCAL
        web._VOICE_STORE_LOCAL = store
        try:
            assert web._voice_note_html("tok") == ""  # 프로파일 없으면 표시 없음
            save_stored_voice(store, VoiceProfile(ending_style="합니다체",
                                                  n_samples=3), n_docs=3)
            note = web._voice_note_html("tok")
            assert "내 문체" in note and "표본 3개" in note
            assert "/voice/relearn" in note and "/voice/off" in note
        finally:
            web._VOICE_STORE_LOCAL = old
    print("OK web voice note (표시/통제 링크)")


def test_learning_stats_show_scale():
    class CountedAdapter(FakeAdapter):
        def list_my_submissions_with_counts(self, course_id, base_url):
            rows = self.list_my_submissions(course_id, base_url)
            return rows, 9

    rows = parse_my_submissions([
        _sub(body="첫 문체입니다.", stype="online_text_entry"),
        _sub(body="둘 문체입니다.", stype="online_text_entry"),
    ], BASE)
    ad = CountedAdapter({"c1": rows}, {})
    profile, n, stats = learn_voice_profile_with_stats(ad, BASE, _courses("c1"))
    assert profile.n_samples == n == 2
    assert stats["submitted_total"] == 9
    assert stats["eligible_submissions"] == 2
    assert stats["samples_used"] == 2
    assert stats["submitted_total_exact"] is True
    print("OK learning stats (전체 조회·적격·사용 표본 분리)")


if __name__ == "__main__":
    test_parse_filters()
    test_collect_and_learn()
    test_collect_newest_courses_first()
    test_store_roundtrip()
    test_store_defensive()
    test_bundle_priority()
    test_web_autolearn_gate()
    test_web_voice_note_and_routes_exist()
    test_learning_stats_show_scale()
    print("\nVOICE AUTOLEARN TESTS PASS")
