# -*- coding: utf-8 -*-
"""`python -m until.runtime --fast` — eTL 과제 하나를 끝까지.

이 스위트가 지키는 것: **eTL에서 가져오는 구간이 실제로 이어지는가.**
  - 마감 임박·미제출 과제를 골라 온다(이미 낸 과제를 조용히 다시 하지 않는다)
  - 과제 본문·첨부·관련 강의자료가 작업공간 `inputs/`에 들어간다
  - 통과하면 **제출하러 갈 eTL 페이지 링크**까지 찍는다
  - 토큰이 없거나 과제가 0건이면 빈손으로 끝내지 않고 무엇을 할지 말한다

전부 오프라인이다. 어댑터와 프로세스 러너를 주입해 네트워크·실제 CLI 호출이 0회다.
격리를 참으로 신고하는 것은 주입한 가짜 경계에 한정된 실험 설정이다 — 운영에서는
OS 샌드박스가 실제로 막을 때만 신고해야 한다(`docs/LOCAL_AGENT_SETUP.md`).
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from until.capture.sources.models import (
    Attachment,
    AssignmentRef,
    CourseRef,
    RawAssignment,
)
from until.runtime import boundary as boundary_mod
from until.runtime import etl_input
from until.runtime.boundary import SandboxSpec, SubprocessBoundary
from until.runtime.cli_agent import CommandResult
from until.runtime.report_runtime import DRAFT_RELPATH

BASE = "https://myetl.snu.ac.kr"
COURSE = CourseRef(id="101", name="환경과 사회 (2026-2)", term="2026 가을")
DESC = (
    "[기말 보고서] 기후 변화가 대학생의 일상 소비에 미치는 영향을 조사하고,\n"
    "신뢰할 수 있는 자료를 근거로 분석 보고서를 작성하시오.\n"
    "AI 사용 가능. 구성: 서론, 결론\n"
    "분량: 300자 이상. 신뢰할 수 있는 출처를 인용 표시할 것.\n"
)
GOOD_DRAFT = (
    "# 서론\n" + "기후 변화는 소비 구조를 바꾼다. " * 30 + "[자료1]\n\n"
    "[[DECISION: 결론의 관점을 어디로 세울지 — 본인 판단]]\n\n"
    "# 결론\n마무리한다.\n"
)


def _url(assignment_id: str) -> str:
    return f"{BASE}/courses/101/assignments/{assignment_id}"


class FakeEtl:
    """Canvas 어댑터 흉내 — 네트워크 0. 어떤 조회가 왔는지 기록한다."""

    def __init__(self, *, assignments=None, materials=True):
        self.base_url = BASE
        self.downloads = []
        self.assignments = assignments if assignments is not None else [
            # 이미 제출한 과제 — 골라서는 안 된다.
            AssignmentRef(id="4000", title="1주차 소감문", course_id="101",
                          course_name=COURSE.name, url=_url("4000"),
                          due_at="2026-08-25T14:59:00Z", submitted=True),
            # 마감이 더 먼 미제출.
            AssignmentRef(id="6000", title="기말 발표 준비", course_id="101",
                          course_name=COURSE.name, url=_url("6000"),
                          due_at="2026-10-01T14:59:00Z"),
            # 마감이 가장 가까운 미제출 — 이게 정답이다.
            AssignmentRef(id="5001", title="기말 보고서 — 기후변화와 소비",
                          course_id="101", course_name=COURSE.name,
                          url=_url("5001"), due_at="2026-09-01T14:59:00Z"),
        ]
        self._materials = materials

    # ── 탐색 ────────────────────────────────────────────────────────
    def list_courses(self, base_url, include_past=False):
        return [COURSE]

    def list_assignments(self, course, base_url, bucket=None):
        return list(self.assignments)

    # ── 수집 ────────────────────────────────────────────────────────
    def fetch_assignment(self, url):
        return RawAssignment(
            title="기말 보고서 — 기후변화와 소비", course=COURSE.name,
            description=DESC, url=url,
            attachments=[Attachment(name="과제안내.pdf",
                                    url=f"{BASE}/files/900/download")])

    def download(self, attachment, dest_dir):
        self.downloads.append(attachment.name)
        path = Path(dest_dir) / attachment.name
        path.write_bytes(b"%PDF-1.4 fake " + attachment.name.encode("utf-8"))
        return str(path)

    # ── 강의자료 ────────────────────────────────────────────────────
    def list_course_files(self, course_id, base_url):
        if not self._materials:
            return []
        return [Attachment(name="3주차_기후와소비.pdf",
                           url=f"{BASE}/files/901/download"),
                Attachment(name="수강생명단.xlsx",
                           url=f"{BASE}/files/902/download")]

    def list_modules(self, course_id, base_url):
        return []


class FakeCli:
    """프로세스 대신 초안을 써 주는 가짜 로컬 에이전트."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, *, cwd, env, timeout, stdin_text=""):
        self.calls.append(tuple(argv))
        if "-p" not in argv:
            return CommandResult(0, "fake-cli 1.0")
        (Path(cwd) / DRAFT_RELPATH).write_text(GOOD_DRAFT, encoding="utf-8")
        return CommandResult(0, "done")

    @property
    def work_runs(self):
        return [c for c in self.calls if "-p" in c]


@contextlib.contextmanager
def _harness(fake_cli, fake_etl, *, token="TESTTOKEN"):
    """에이전트 설정·가짜 격리 경계·가짜 eTL 어댑터를 붙였다가 되돌린다."""
    saved_env = {k: os.environ.get(k) for k in
                 ("UNTIL_AGENT_CMD", "UNTIL_AGENT_RUN_ARGS", "UNTIL_CANVAS_TOKEN")}
    saved_build = boundary_mod.build_boundary
    saved_adapter = etl_input.build_adapter
    os.environ["UNTIL_AGENT_CMD"] = "fake-agent"
    os.environ["UNTIL_AGENT_RUN_ARGS"] = "-p,{prompt}"
    if token is None:
        os.environ.pop("UNTIL_CANVAS_TOKEN", None)
    else:
        os.environ["UNTIL_CANVAS_TOKEN"] = token
    boundary_mod.build_boundary = lambda environ=None, **kw: SubprocessBoundary(
        SandboxSpec(("sbx", "{workspace}"), isolates_filesystem=True,
                    isolates_network=True), runner=fake_cli)

    def _adapter(tok, *, ws=False):
        if not (tok or "").strip():          # 토큰 게이트는 진짜 코드를 그대로 쓴다
            return saved_adapter(tok, ws=ws)
        return fake_etl
    etl_input.build_adapter = _adapter
    try:
        yield
    finally:
        boundary_mod.build_boundary = saved_build
        etl_input.build_adapter = saved_adapter
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _run(argv):
    from until.runtime.cli import main
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(argv)
    return code, buffer.getvalue()


# ── 과제 선택 ───────────────────────────────────────────────────────
def test_fast_picks_the_nearest_unsubmitted_assignment():
    """이미 낸 과제를 조용히 다시 하지 않는다 — 웹 딸깍과 같은 정책."""
    etl = FakeEtl()
    items = etl.list_assignments(COURSE, BASE)
    best = etl_input.pick_nearest(items)
    assert best.id == "5001", best.id
    assert not best.submitted
    print("OK 과제 선택 — 마감 임박 미제출 우선")


def test_list_shows_assignments_without_starting_work():
    cli, etl = FakeCli(), FakeEtl()
    with _harness(cli, etl):
        code, out = _run(["--list"])
    assert code == 0
    assert "기말 보고서" in out and "미제출" in out and "제출함" in out
    assert cli.calls == []                    # 목록만 보는데 에이전트를 띄우지 않는다
    print("OK 목록 보기 — 에이전트 기동 0회")


def test_empty_inbox_does_not_end_empty_handed():
    cli, etl = FakeCli(), FakeEtl(assignments=[])
    with _harness(cli, etl):
        code, out = _run(["--fast", "--yes"])
    assert code == 1 and "과제를 하나도 찾지 못했" in out
    assert cli.work_runs == []
    print("OK 과제 0건 — 무엇을 확인할지 말하고 멈춘다")


def test_missing_token_says_how_to_get_one():
    cli, etl = FakeCli(), FakeEtl()
    with _harness(cli, etl, token=None):
        code, out = _run(["--fast", "--yes"])
    assert code == 2 and "액세스 토큰" in out
    assert cli.calls == []
    print("OK 토큰 없음 — 발급 경로 안내 + 실행 0회")


def test_etl_and_local_files_are_mutually_exclusive():
    cli, etl = FakeCli(), FakeEtl()
    with tempfile.TemporaryDirectory() as raw:
        task = Path(raw) / "a.md"
        task.write_text("# 과제\n", encoding="utf-8")
        with _harness(cli, etl):
            code, out = _run(["--fast", str(task)])
    assert code == 2 and "하나만 고르세요" in out
    print("OK eTL 경로와 로컬 파일 동시 지정 거부")


# ── 수집 ────────────────────────────────────────────────────────────
def test_collect_brings_body_attachment_and_materials():
    etl = FakeEtl()
    with tempfile.TemporaryDirectory() as raw:
        got = etl_input.collect(etl, _url("5001"), Path(raw), materials=1,
                                base_url=BASE)
        names = sorted(p.name for p in got.files)
    assert got.assignment_id == "5001" and got.course_id == "101"
    assert any(n.endswith(".md") for n in names), names          # 과제 본문
    assert "과제안내.pdf" in names, names                          # 첨부
    # 관련 강의자료는 제목 키워드로 순위화된 것만 — 명단 같은 무관 파일은 안 온다.
    assert "3주차_기후와소비.pdf" in names, names
    assert "수강생명단.xlsx" not in names, names
    print("OK 수집 — 본문·첨부·관련 자료만 골라 온다")


def test_submit_page_url_is_rebuilt_from_ids_only():
    assert etl_input.submit_page_url(BASE, "101", "5001") == _url("5001")
    # id가 숫자가 아니면 링크를 지어내지 않는다(WS·SSO 경로).
    assert etl_input.submit_page_url(BASE, "", "5001") == ""
    assert etl_input.submit_page_url(BASE, "101", "abc") == ""
    print("OK 제출 링크 — 과목·과제 id로만 재구성")


# ── 전체 경로 ───────────────────────────────────────────────────────
def test_fast_runs_etl_to_submission_ready():
    """eTL 과제 하나 → 에이전트 작업 → 검증 → 제출 파일 + 제출 페이지 링크."""
    cli, etl = FakeCli(), FakeEtl()
    with tempfile.TemporaryDirectory() as raw:
        with _harness(cli, etl):
            code, out = _run(["--fast", "--yes", "--work-root", str(Path(raw) / "w")])
        assert code == 0, out
        assert "고른 과제" in out and "기말 보고서" in out
        assert "환경과 사회" in out                       # 과목명이 화면에 뜬다
        assert "검증 통과" in out
        assert _url("5001") in out                        # 제출하러 갈 곳
        assert "전송은 사람이 합니다" in out               # 제출은 안 한다

        # 에이전트는 정확히 한 번 작업했고, 샌드박스 밖으로 나가지 않았다.
        assert len(cli.work_runs) == 1, cli.calls
        assert all(c[0] == "sbx" for c in cli.calls)

        # 수집한 자료가 작업공간 inputs/ 에 실제로 들어갔다.
        inputs = sorted(p.name for p in (Path(raw) / "w").rglob("inputs/*"))
        assert any(n.endswith(".md") for n in inputs), inputs
        assert "과제안내.pdf" in inputs, inputs
        draft = next((Path(raw) / "w").rglob("draft.md"))
        assert "[[DECISION:" in draft.read_text(encoding="utf-8")
    print("OK 전체 경로 — eTL 과제 → 검증된 제출 파일 → 제출 링크")


def test_etl_scratch_is_cleaned_up():
    """내려받은 원본은 작업공간에 복사된 뒤 임시 폴더에 남지 않는다."""
    import tempfile as _t
    before = set(Path(_t.gettempdir()).glob("until_etl_*"))
    cli, etl = FakeCli(), FakeEtl()
    with tempfile.TemporaryDirectory() as raw:
        with _harness(cli, etl):
            code, _out = _run(["--fast", "--yes", "--work-root", str(Path(raw) / "w")])
    assert code == 0
    assert not (set(Path(_t.gettempdir()).glob("until_etl_*")) - before)
    print("OK 임시 다운로드 폴더 정리")


TESTS = [
    test_fast_picks_the_nearest_unsubmitted_assignment,
    test_list_shows_assignments_without_starting_work,
    test_empty_inbox_does_not_end_empty_handed,
    test_missing_token_says_how_to_get_one,
    test_etl_and_local_files_are_mutually_exclusive,
    test_collect_brings_body_attachment_and_materials,
    test_submit_page_url_is_rebuilt_from_ids_only,
    test_fast_runs_etl_to_submission_ready,
    test_etl_scratch_is_cleaned_up,
]

if __name__ == "__main__":
    for case in TESTS:
        case()
    print("\nRUNTIME ETL TESTS PASS")
