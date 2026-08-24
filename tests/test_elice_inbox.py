"""Elice Discovery/inbox opt-in 배선 — fake 전용, subprocess·network 0."""
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until import web
from until.capture.sources.elice_api import EliceAdapter, is_exercise_url
from until.capture.sources.models import CourseRef, RawAssignment


class _Client:
    pass


def _adapter(rows=None, course_ids=None):
    adapter = EliceAdapter(client=_Client(), course_ids=course_ids or ["77"])
    adapter.list_coding_assignments = lambda course_id: list(rows or [])
    return adapter


def test_protocol_adapts_raw_assignments():
    raw = RawAssignment("배열 과제", "프로그래밍과목", "지문", [],
                        "https://api-rest.elice.io/org/snu/material_exercise/get/?material_exercise_id=901")
    adapter = _adapter([raw])
    courses = adapter.list_courses("https://snu.elice.io")
    assert courses == [CourseRef(id="77", name="Elice 77")]
    refs = adapter.list_assignments(courses[0], "https://snu.elice.io")
    assert len(refs) == 1 and refs[0].id == "901"
    assert refs[0].course_name == "Elice · 프로그래밍과목"
    assert "until_title=" in refs[0].url
    print("OK EliceAdapter → DiscoveryAdapter 변환")


def test_merge_sort_failure_isolation_and_opt_in_off():
    from until.capture.sources.models import AssignmentRef
    main = [AssignmentRef("1", "Canvas", "c", "본 과목", due_at="2026-09-01")]
    raw = RawAssignment("Elice", "코딩", "지문", [],
                        "https://api-rest.elice.io/org/snu/material_exercise/get/?material_exercise_id=2")
    merged = web.merge_elice_inbox(main, adapter=_adapter([raw]))
    assert [x.title for x in merged] == ["Canvas", "Elice"]  # due 없는 Elice는 뒤

    class Broken:
        def list_courses(self, base_url):
            raise RuntimeError("isolated")
    assert web.merge_elice_inbox(main, adapter=Broken()) == main
    failure_warnings = []
    assert web.merge_elice_inbox(main, adapter=Broken(),
                                 warnings=failure_warnings) == main
    assert failure_warnings == ["Elice 과제를 불러오지 못해 eTL 과제만 표시합니다."]

    warnings = []
    empty = _adapter([raw], course_ids=[])
    empty.course_ids = []
    assert web.merge_elice_inbox(main, adapter=empty, warnings=warnings) == main
    assert warnings and "과목 ID" in warnings[0]

    duplicated = web.merge_elice_inbox([], adapter=_adapter([raw, raw]))
    assert len(duplicated) == 1

    old_on, old_token = os.environ.get("UNTIL_ELICE"), os.environ.get("UNTIL_ELICE_TOKEN")
    try:
        os.environ.pop("UNTIL_ELICE", None)
        os.environ.pop("UNTIL_ELICE_TOKEN", None)
        assert web.merge_elice_inbox(main) is main
    finally:
        if old_on is not None: os.environ["UNTIL_ELICE"] = old_on
        if old_token is not None: os.environ["UNTIL_ELICE_TOKEN"] = old_token
    print("OK Elice 병합·정렬·실패 격리·opt-in off")


def test_production_opt_in_requires_courses_and_never_starts_process_when_off():
    import until.capture.sources.elice_api as module
    saved = {k: os.environ.get(k) for k in
             ("UNTIL_ELICE", "UNTIL_ELICE_TOKEN", "UNTIL_ELICE_COURSE_IDS")}
    original = module.subprocess.run
    module.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("subprocess/network must not start"))
    try:
        os.environ.pop("UNTIL_ELICE", None)
        os.environ["UNTIL_ELICE_TOKEN"] = "secret"
        assert web.merge_elice_inbox([]) == []

        os.environ["UNTIL_ELICE"] = "1"
        os.environ.pop("UNTIL_ELICE_COURSE_IDS", None)
        warnings = []
        assert web.merge_elice_inbox([], warnings=warnings) == []
        assert warnings and "UNTIL_ELICE_COURSE_IDS" in warnings[0]
    finally:
        module.subprocess.run = original
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_collect_elice_with_fake_adapter():
    from until.capture.sources.collect import collect_elice_to_files
    raw = RawAssignment("함수 과제", "프로그래밍", "함수를 구현하세요.", [])

    class Fake:
        def fetch_assignment(self, url): return raw
        def download(self, attachment, dest_dir): raise AssertionError("첨부 없음")

    with tempfile.TemporaryDirectory() as d:
        collected, files = collect_elice_to_files("elice://exercise", d, adapter=Fake())
        assert collected.title == "함수 과제" and pathlib.Path(files[0]).exists()
    print("OK elice: 수집 라우팅(fake adapter)")


def test_collect_with_materials_elice_end_to_end():
    from until.config import Config
    raw = RawAssignment("함수 과제", "프로그래밍", "함수를 구현하세요.", [],
                        "https://api-rest.elice.io/org/snu/material_exercise/get/?material_exercise_id=4")

    class Fake:
        def fetch_assignment(self, url): return raw
        def download(self, attachment, dest_dir): raise AssertionError("첨부 없음")
        def course_id_for_url(self, url): return None

    cfg = Config(); cfg.backend = "mock"
    result = web.collect_with_materials(raw.url, cfg, adapter=Fake())
    assert result.draft and result.draft.body


def test_exact_elice_url_routing():
    good = "https://api-rest.elice.io/org/snu/material_exercise/get/?material_exercise_id=4"
    assert is_exercise_url(good)
    assert not is_exercise_url("http://api-rest.elice.io/org/snu/material_exercise/get/?material_exercise_id=4")
    assert not is_exercise_url("https://evil.test/?next=api-rest.elice.io/org/snu/material_exercise/get/")
    assert not is_exercise_url("https://api-rest.elice.io/org/snu/material_exercise/submit/?material_exercise_id=4")


def test_cli_elice_failure_is_sanitized():
    import io
    import contextlib
    import until.capture.sources.collect as collect
    from until.cli import _collect_source
    original = collect.collect_elice_to_files
    collect.collect_elice_to_files = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("Bearer SECRET provider-body"))
    try:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert _collect_source("elice:https://example.invalid") is None
        text = out.getvalue()
        assert "SECRET" not in text and "provider-body" not in text
    finally:
        collect.collect_elice_to_files = original


if __name__ == "__main__":
    test_protocol_adapts_raw_assignments()
    test_merge_sort_failure_isolation_and_opt_in_off()
    test_production_opt_in_requires_courses_and_never_starts_process_when_off()
    test_collect_elice_with_fake_adapter()
    test_collect_with_materials_elice_end_to_end()
    test_exact_elice_url_routing()
    test_cli_elice_failure_is_sanitized()
    print("\nELICE INBOX TESTS PASS")
