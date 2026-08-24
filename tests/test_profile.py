"""사용자 프로필 저장·자동 채움 테스트 (오프라인·결정적).

실사용 피드백: 학교·소속·이메일을 매번 되물으면 GPT 대비 강점이 사라진다 —
1회 저장 → 프롬프트 힌트·양식 셀 매핑·LMS 자동 보충으로 되묻지 않는다.
"""
import sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until import profile as prof


def test_save_load_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "profile.json"
        assert prof.load_profile(p) == {}
        prof.save_profile({"name": "김민준", "student_id": "2020-12345",
                           "email": "hong@example.com", "junk": "무시",
                           "phone": "  "}, p)
        got = prof.load_profile(p)
        assert got["name"] == "김민준" and "junk" not in got and "phone" not in got
        # 손상 파일은 빈 dict(비치명적).
        p.write_text("{broken", encoding="utf-8")
        assert prof.load_profile(p) == {}
    print("OK save/load roundtrip")


def test_hint_and_mapping():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "profile.json"
        assert prof.profile_hint(p) == ""      # 저장 전엔 힌트 없음(주입 안 함)
        prof.save_profile({"name": "김민준", "university": "서울대학교",
                           "department": "자유전공학부", "student_id": "2020-12345"}, p)
        hint = prof.profile_hint(p)
        assert "내 프로필" in hint and "김민준" in hint and "되묻지" in hint
        m = prof.profile_mapping(p)
        assert m["이름"] == "김민준" and m["성명"] == "김민준"  # 별칭 매핑
        assert m["소속 대학·학과"] == "서울대학교 자유전공학부"  # 합친 라벨
    print("OK hint + mapping")


def test_merge_from_lms_fills_only_empty():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "profile.json"
        prof.save_profile({"name": "직접 저장한 이름"}, p)
        got = prof.merge_from_lms({"name": "LMS이름", "email": "lms@snu.ac.kr"}, p)
        # 직접 저장한 값은 유지, 빈 필드만 보충.
        assert got["name"] == "직접 저장한 이름" and got["email"] == "lms@snu.ac.kr"
    print("OK LMS merge fills only empty fields")


def test_student_id_from_lms_profile():
    assert prof.student_id_from_lms_profile({"sis_user_id": "2099-13111"}) == "2099-13111"
    assert prof.student_id_from_lms_profile({"login_id": "209913111"}) == "2099-13111"
    assert prof.student_id_from_lms_profile({"primary_email": "209912345@example.com"}) == ""
    assert prof.student_id_from_lms_profile({"login_id": "nickname"}) == ""
    print("OK LMS 명시적 식별자에서만 학번 추출")


def test_thread_local_override():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "u1" / "profile.json"
        prof.set_profile_path_override(p)
        try:
            prof.save_profile({"name": "요청 스코프"})
            assert prof.load_profile()["name"] == "요청 스코프"
            assert prof.profile_path() == p
        finally:
            prof.set_profile_path_override(None)
    print("OK thread-local path override")


def test_pipeline_injects_profile_hint():
    import until.pipeline as pl
    from until.config import Config
    from until.llm.mock_client import MockClient
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "profile.json"
        prof.save_profile({"name": "김민준", "student_id": "2020-12345"}, p)
        prof.set_profile_path_override(p)
        captured = {}
        orig = pl.build_client

        class Rec:
            def __init__(self, inner): self.inner = inner
            def complete(self, system, user, **kw):
                if kw.get("tag") in ("execution", "execution-unit"):
                    captured.setdefault("sys", system)
                return self.inner.complete(system, user, **kw)

        pl.build_client = lambda backend, model=None: Rec(MockClient())
        try:
            cfg = Config(); cfg.backend = "mock"
            pl.run(["examples/sample_assignment.txt"], cfg)
        finally:
            pl.build_client = orig
            prof.set_profile_path_override(None)
        assert "내 프로필" in captured["sys"] and "김민준" in captured["sys"]
    print("OK pipeline injects profile hint")


def test_filled_form_uses_profile_as_base():
    # 초안 표에 값이 없어도 프로필 값이 양식 셀에 들어간다(초안 값이 있으면 우선).
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from test_formfill import _make_form_hwpx
    from until.capture.ingest import ingest_file
    from until.capture.formfill import fill_form_file
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        p = d / "profile.json"
        prof.save_profile({"name": "김민준", "student_id": "2020-12345"}, p)
        src = _make_form_hwpx(d)
        out = d / "filled.hwpx"
        stats = fill_form_file(src, out, prof.profile_mapping(p))
        assert stats.cells >= 2
        text = ingest_file(out, backend="basic").text
        assert "| 이름 | 김민준 |" in text and "2020-12345" in text
    print("OK filled form uses profile mapping")


if __name__ == "__main__":
    test_save_load_roundtrip()
    test_hint_and_mapping()
    test_merge_from_lms_fills_only_empty()
    test_student_id_from_lms_profile()
    test_thread_local_override()
    test_pipeline_injects_profile_hint()
    test_filled_form_uses_profile_as_base()
    print("\nPROFILE TESTS PASS")
