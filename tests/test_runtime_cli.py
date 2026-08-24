# -*- coding: utf-8 -*-
"""`python -m until.runtime` — Local Agent Runtime 진입점.

전부 오프라인이다. 실제 CLI도 실제 샌드박스도 부르지 않고 러너를 주입한다.
**여기서 격리를 참으로 신고하는 건 주입한 가짜 경계에 한정된 실험 설정이다** —
운영에서는 OS 샌드박스가 실제로 막을 때만 신고해야 한다(`docs/LOCAL_AGENT_SETUP.md`).

이 스위트가 지키는 것:
  - 설정(에이전트·샌드박스)이 없으면 아무것도 실행하지 않고 무엇을 설정할지 말한다
  - 교수자가 AI를 금지하면 에이전트를 **띄우기 전에** 멈춘다
  - AI 정책이 불명이면 fail-closed로 막고 상속 방법을 알려 준다
  - 승인 없이는 작업 프로세스가 0회다
  - 통과하면 검증된 번들 경로를 주되 **제출은 하지 않는다**
"""
from __future__ import annotations

import io
import contextlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from until.runtime import boundary as boundary_mod
from until.runtime.boundary import SandboxSpec, SubprocessBoundary
from until.runtime.cli_agent import CommandResult
from until.runtime.report_runtime import DRAFT_RELPATH
from until.runtime.spec_builder import build_runtime_spec

GOOD_DRAFT = (
    "# 서론\n" + "기후 변화는 소비 구조를 바꾼다. " * 30 + "[자료1]\n\n"
    "[[DECISION: 결론의 관점을 어디로 세울지 — 본인 판단]]\n\n"
    "# 결론\n마무리한다.\n"
)
SHORT_DRAFT = "# 서론\n짧다. [자료1]\n\n[[DECISION: 관점 — 본인 판단]]\n\n# 결론\n끝.\n"

ASSIGNMENT = (
    "# 기말 보고서 — 기후변화와 소비\n\n"
    "과목: 환경과 사회 (2026-2)\n"
    "AI 사용 가능. 구성: 서론, 결론\n"
    "분량: 300자 이상. 신뢰할 수 있는 출처를 인용 표시할 것.\n"
)


class FakeCli:
    """프로세스 대신 호출을 기록하고 초안 파일을 써 주는 가짜 CLI."""

    def __init__(self, drafts=(GOOD_DRAFT,)):
        self.calls = []
        self.drafts = list(drafts)
        self.runs = 0

    def __call__(self, argv, *, cwd, env, timeout, stdin_text=""):
        self.calls.append(tuple(argv))
        if "-p" not in argv:                      # probe(version)
            return CommandResult(0, "fake-cli 1.0")
        body = self.drafts[min(self.runs, len(self.drafts) - 1)]
        self.runs += 1
        (Path(cwd) / DRAFT_RELPATH).write_text(body, encoding="utf-8")
        return CommandResult(0, "done")


@contextlib.contextmanager
def _cli_env(fake, *, agent=True, sandbox=True):
    """에이전트 설정과 (가짜) 격리 경계를 붙였다가 원상복구한다."""
    original_build = boundary_mod.build_boundary
    saved = {k: os.environ.get(k) for k in ("UNTIL_AGENT_CMD", "UNTIL_AGENT_RUN_ARGS")}
    if agent:
        os.environ["UNTIL_AGENT_CMD"] = "fake-agent"
        os.environ["UNTIL_AGENT_RUN_ARGS"] = "-p,{prompt}"
    else:
        os.environ.pop("UNTIL_AGENT_CMD", None)
        os.environ.pop("UNTIL_AGENT_RUN_ARGS", None)
    spec = SandboxSpec(("sbx", "{workspace}"), isolates_filesystem=True,
                       isolates_network=True) if sandbox else SandboxSpec()
    boundary_mod.build_boundary = lambda environ=None, **kw: SubprocessBoundary(
        spec, runner=fake)
    try:
        yield
    finally:
        boundary_mod.build_boundary = original_build
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _run(argv):
    """CLI를 돌리고 (종료코드, 출력)을 돌려준다."""
    from until.runtime.cli import main
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(argv)
    return code, buffer.getvalue()


def _task(root: Path, text: str = ASSIGNMENT, name: str = "기말보고서.md") -> Path:
    path = root / name
    path.write_text(text, encoding="utf-8")
    return path


# ── 명세 조립(LLM 0) ────────────────────────────────────────────────
def test_spec_is_built_without_an_llm():
    """웹 경로의 Understanding(LLM 1회) 없이 결정적 판정기만으로 명세를 만든다."""
    class Doc:
        def __init__(self, text):
            self.text, self.source = text, "a.md"

    spec = build_runtime_spec([Doc(ASSIGNMENT)])
    assert spec["title"] == "기말 보고서 — 기후변화와 소비"
    assert spec["course"] == "환경과 사회 (2026-2)"
    assert spec["required"] == ["서론", "결론"]
    assert spec["min_chars"] == 300
    assert spec["requires_citation"] is True

    # 요건이 없으면 지어내지 않는다 — 없는 키는 검사도 걸리지 않는다.
    bare = build_runtime_spec([Doc("# 그냥 과제\n\n자유롭게 쓰세요.\n")])
    assert bare["title"] == "그냥 과제"
    for absent in ("required", "min_chars", "requires_citation"):
        assert absent not in bare, absent
    print("OK 명세 조립 — 결정적, 모르는 요건은 비워 둔다")


# ── 설정 게이트 ─────────────────────────────────────────────────────
def test_without_agent_config_nothing_runs():
    fake = FakeCli()
    with tempfile.TemporaryDirectory() as raw:
        with _cli_env(fake, agent=False):
            code, out = _run([str(_task(Path(raw)))])
    assert code == 2 and "UNTIL_AGENT_CMD" in out
    assert fake.calls == []          # 설정이 없으면 프로세스 0회
    print("OK 에이전트 미설정 — 실행 0회 + 설정 안내")


def test_without_sandbox_execution_is_refused():
    """격리를 신고할 수 없으면 probe조차 하지 않는다(fail-closed)."""
    fake = FakeCli()
    with tempfile.TemporaryDirectory() as raw:
        with _cli_env(fake, sandbox=False):
            code, out = _run([str(_task(Path(raw))), "--probe"])
    assert code == 2 and "샌드박스" in out
    assert fake.calls == []
    print("OK 샌드박스 미설정 — 격리를 거짓 신고하지 않고 거부")


def test_probe_reports_ready_without_touching_the_assignment():
    fake = FakeCli()
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = _task(root)
        with _cli_env(fake):
            code, out = _run(["--probe", str(task)])
        assert code == 0 and "ready" in out
        assert all("-p" not in call for call in fake.calls)   # 작업 실행은 없음
        assert task.read_text(encoding="utf-8") == ASSIGNMENT
    print("OK probe — 설치·로그인 확인만, 과제는 건드리지 않는다")


# ── 정책 게이트 ─────────────────────────────────────────────────────
def test_instructor_ai_ban_stops_before_the_agent():
    fake = FakeCli()
    with tempfile.TemporaryDirectory() as raw:
        task = _task(Path(raw), "# 과제\n\nAI 사용 금지. 직접 작성하세요.\n")
        with _cli_env(fake):
            code, out = _run([str(task), "--yes"])
    assert code == 1 and "AI 사용을 명시적으로 금지" in out
    assert fake.calls == []          # 에이전트를 띄우지도 않는다
    print("OK 교수자 AI 금지 — 에이전트 기동 전에 정지")


def test_unclear_policy_fails_closed_and_says_how_to_fix():
    fake = FakeCli()
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        task = _task(root, "# 조사 보고서\n\n구성: 서론, 결론\n분량: 300자 이상.\n")
        with _cli_env(fake):
            blocked, out = _run([str(task), "--work-root", str(root / "w1"), "--yes"])
        assert blocked == 1 and "--course-policy" in out
        assert all("-p" not in call for call in fake.calls)

        # 강의계획서를 상속시키면 같은 과제가 진행된다.
        policy = root / "syllabus.txt"
        policy.write_text("이 수업에서는 AI 사용 가능합니다.", encoding="utf-8")
        with _cli_env(fake):
            code, out = _run([str(task), "--work-root", str(root / "w2"),
                              "--course-policy", str(policy), "--yes"])
    assert code == 0, out
    print("OK 정책 불명 — fail-closed + 강의계획서 상속 경로 안내")


# ── 승인 게이트 ─────────────────────────────────────────────────────
def test_no_approval_means_no_work_process():
    """--yes 없이 비대화형이면 승인을 받을 수 없으므로 실행하지 않는다."""
    fake = FakeCli()
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        with _cli_env(fake):
            code, out = _run([str(_task(root)), "--work-root", str(root / "w")])
    assert code == 1 and "승인" in out
    assert all("-p" not in call for call in fake.calls)   # probe만, 작업 0회
    print("OK 승인 없음 — 작업 프로세스 0회")


def test_policy_gate_blocks_behaviour_not_bookkeeping():
    """정책 검사는 '금지된 일을 하려는가'만 본다(사용자 지시 2026-08-20).

    예전에는 job이 정책의 금지·필수 항목을 문자열로 **복창**하고 있는지도 봤다.
    같은 문자열을 베껴 넣으면 통과하니 행동을 막는 게 아니었고, 기관 정책
    기준선을 층에 넣는 순간 유일한 플러그인이 100% 차단됐다. 지금은 통과한다.
    그러나 진짜 제한(`ai_use=limited`)은 그대로 막아야 한다.
    """
    from until.policy_hierarchy import PolicyLayer, resolve_policy
    from until.policy_profiles import snu_2026_baseline
    from until.runtime.report_runtime import ReportRuntime
    from until.runtime.security import RuntimeSecurityError, validate_job_policy
    from until.runtime.models import RuntimeRequest
    from until.context.assignment_router import AssignmentRoute

    def _job(ai_use):
        runtime = ReportRuntime()
        policy = resolve_policy((snu_2026_baseline(),
                                 PolicyLayer("assignment", "a1", ai_use=ai_use)))
        request = RuntimeRequest("a1", {"title": "보고서"},
                                 AssignmentRoute("general_report", "fixture", ()),
                                 policy)
        runtime.prepare(request)
        with tempfile.TemporaryDirectory() as raw:
            from until.runtime.workspace import WorkspaceManager
            workspace = WorkspaceManager(Path(raw)).materialize(
                runtime.name, request, runtime.prepare(request))
            return runtime.build_job(workspace), policy

    # 기관 기준선(하드 제약·필수 행동 다수)이 있어도 통과한다.
    job, policy = _job("allowed")
    assert policy.hard_constraints and policy.required_actions
    validate_job_policy(job, policy)

    # 'limited'(아이디어·교정만 허용)는 초안 작성을 여전히 막는다.
    job, policy = _job("limited")
    try:
        validate_job_policy(job, policy)
        raise AssertionError("limited 정책이 초안 작성을 통과시켰다")
    except RuntimeSecurityError as exc:
        assert "limited" in str(exc)
    print("OK 정책 검사 — 복창 요구는 없애고, 실제 제한은 그대로")


# ── 샌드박스 자체 검증 ──────────────────────────────────────────────
class _FakeSandbox:
    def __init__(self, isolates_filesystem=False, isolates_network=False):
        self.argv_prefix = ("sbx",)
        self.isolates_filesystem = isolates_filesystem
        self.isolates_network = isolates_network

    def wrap(self, argv, workspace):
        return ("sbx",) + tuple(argv)


def _verdict_runner(verdicts):
    """프로브 이름 → 'OPEN'/'BLOCKED'. 샌드박스 밖 실행(대조군)은 argv[0]로 구분."""
    from until.runtime.cli_agent import CommandResult

    def run(argv, *, cwd, env, timeout, stdin_text=""):
        sandboxed = argv[0] == "sbx"
        script = next((a for a in argv if str(a).endswith(".py")), "")
        name = Path(script).stem.replace(".until-probe-", "")
        key = name if sandboxed else f"{name}-outside"
        return CommandResult(0, verdicts.get(key, "OPEN"))
    return run


def test_sandbox_check_refuses_to_certify_without_a_control():
    """밖에서도 연결이 안 되면 안에서 막힌 것은 격리의 증거가 아니다."""
    from until.runtime import sandbox_check

    with tempfile.TemporaryDirectory() as raw:
        report = sandbox_check.verify(
            _FakeSandbox(), Path(raw) / "ws",
            runner=_verdict_runner({"network": "BLOCKED",
                                    "network-baseline-outside": "BLOCKED"}))
    assert report.status_of("network") == sandbox_check.UNKNOWN
    assert "network" not in report.safe_to_claim
    print("OK 샌드박스 검증 — 대조군 없으면 네트워크 격리를 인정하지 않는다")


def test_sandbox_check_certifies_only_what_it_proved():
    from until.runtime import sandbox_check

    with tempfile.TemporaryDirectory() as raw:
        report = sandbox_check.verify(
            _FakeSandbox(), Path(raw) / "ws",
            runner=_verdict_runner({
                "network-baseline-outside": "OPEN",   # 대조군: 밖에선 나간다
                "network": "BLOCKED",                 # 안에선 막힌다 → 증명됨
                "filesystem": "OPEN",                 # 밖으로 썼다 → 안 막힘
                "workspace_writable": "OPEN",
            }))
    assert report.safe_to_claim == ("network",), report.safe_to_claim
    assert report.status_of("filesystem") == sandbox_check.FAIL
    assert report.status_of("workspace_writable") == sandbox_check.PASS
    print("OK 샌드박스 검증 — 증명된 것만 신고 가능으로 표시")


def test_sandbox_check_flags_overclaimed_isolation():
    """가장 위험한 상태: 신고했는데 증명되지 않음."""
    from until.runtime import sandbox_check

    with tempfile.TemporaryDirectory() as raw:
        report = sandbox_check.verify(
            _FakeSandbox(isolates_filesystem=True, isolates_network=True),
            Path(raw) / "ws",
            runner=_verdict_runner({
                "network-baseline-outside": "OPEN",
                "network": "BLOCKED",
                "filesystem": "OPEN",          # 신고했지만 실제로는 뚫림
                "workspace_writable": "OPEN",
            }))
    assert report.overclaimed == ("filesystem",), report.overclaimed
    print("OK 샌드박스 검증 — 거짓 신고를 잡아낸다")


def test_verify_sandbox_needs_no_agent_config():
    """격리부터 갖추고 CLI를 붙이는 순서 — 에이전트를 먼저 요구하면 순환이다."""
    fake = FakeCli()
    with _cli_env(fake, agent=False, sandbox=False):
        code, out = _run(["--verify-sandbox"])
    assert code == 2 and "UNTIL_AGENT_SANDBOX" in out
    assert "UNTIL_AGENT_CMD" not in out          # 에이전트 얘기를 꺼내지 않는다
    print("OK --verify-sandbox — 에이전트 설정 없이도 돌아간다")


# ── 전체 경로 ───────────────────────────────────────────────────────
def test_full_path_produces_a_verified_bundle_but_never_submits():
    fake = FakeCli()
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        with _cli_env(fake):
            code, out = _run([str(_task(root)), "--work-root", str(root / "w"), "--yes"])
        assert code == 0, out
        assert "검증 통과" in out and "제출은 사람이 합니다" in out
        # 번들 경로가 실제로 존재하고 에이전트가 쓴 본문이 들어 있다.
        drafts = list((root / "w").rglob("draft.md"))
        assert drafts and "[[DECISION:" in drafts[0].read_text(encoding="utf-8")
    # 샌드박스 래퍼가 실제 argv 앞에 붙었다(격리 밖으로 새지 않는다).
    assert all(call[0] == "sbx" for call in fake.calls), fake.calls
    print("OK 전체 경로 — 검증된 번들 생성, 제출은 하지 않음")


def test_submission_file_never_carries_raw_decision_markers():
    """검증된 초안에는 `[[DECISION:]]`이 살아 있다 — 그대로 올리면 안 된다.

    검증기가 경계선 표식을 **남기라고** 강제하므로 `work/draft.md`에는 항상
    원문 마커가 있다. 올릴 파일은 따로 만들어야 한다(실측으로 확인한 구멍).
    """
    fake = FakeCli()
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        with _cli_env(fake):
            code, out = _run([str(_task(root)), "--work-root", str(root / "w"), "--yes"])
        assert code == 0, out

        draft = next((root / "w").rglob("draft.md")).read_text(encoding="utf-8")
        assert "[[DECISION:" in draft          # 검증한 원본은 표식을 그대로 보존

        submission = next((root / "w").rglob("제출본.md")).read_text(encoding="utf-8")
        assert "[[DECISION:" not in submission          # 올릴 파일엔 내부 표기 없음
        assert "직접 정할 것 1:" in submission           # 사람이 채울 자리로 바뀜
        assert "아직 정하지 않은 곳" in out              # 화면이 그걸 경고
    print("OK 제출본 — 원문 마커 없음, 빈칸은 자리표시 + 경고")


def test_answers_file_fills_the_decisions():
    """미리 답을 주면 그 문장이 들어가고 자리표시가 남지 않는다."""
    import json

    fake = FakeCli()
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        answers = root / "answers.json"
        answers.write_text(json.dumps({"1": "제도가 개인 선택보다 크게 작용한다고 본다."},
                                      ensure_ascii=False), encoding="utf-8")
        with _cli_env(fake):
            code, out = _run([str(_task(root)), "--work-root", str(root / "w"),
                              "--yes", "--answers", str(answers)])
        assert code == 0, out
        submission = next((root / "w").rglob("제출본.md")).read_text(encoding="utf-8")
        assert "제도가 개인 선택보다" in submission
        assert "[[DECISION:" not in submission and "직접 정할 것" not in submission
        assert "아직 정하지 않은 곳" not in out
    print("OK 답변 파일 — 결정이 문장으로 들어가고 빈칸 0")


def test_finish_reports_requirements_broken_by_substitution():
    """치환 뒤 요건이 무너지면 조용히 넘기지 않는다.

    마커를 지우면 분량이 줄 수 있다. '검증은 통과했는데 올릴 파일은 요건 미달'을
    막으려면 치환 **후** 한 번 더 봐야 한다."""
    from until.runtime import finish

    class _WS:
        def __init__(self, root):
            self.root = root

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "work").mkdir()
        (root / "work" / "draft.md").write_text(
            "# 서론\n짧다. [자료1]\n[[DECISION: 관점을 어디로 — 본인 판단]]\n"
            "# 결론\n끝.\n",
            encoding="utf-8")
        done = finish.finish(_WS(root), {"min_chars": 5000, "required": ["서론", "결론"]})
    assert done.open_notes and not done.ready
    assert any("분량" in w for w in done.warnings), done.warnings
    print("OK 제출본 재검사 — 치환 뒤 요건 미달을 잡는다")


def test_validation_failure_repairs_once_then_stops():
    """분량 미달 → 1회 자동 수정으로 통과. 계속 미달이면 막고 그대로 남긴다."""
    repaired = FakeCli(drafts=(SHORT_DRAFT, GOOD_DRAFT))
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        with _cli_env(repaired):
            code, out = _run([str(_task(root)), "--work-root", str(root / "w"), "--yes"])
    assert code == 0, out
    assert repaired.runs == 2, repaired.runs      # 정확히 1회만 수정

    stuck = FakeCli(drafts=(SHORT_DRAFT,))
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        with _cli_env(stuck):
            code, out = _run([str(_task(root)), "--work-root", str(root / "w"), "--yes"])
    assert code == 1 and "too_short" in out
    assert stuck.runs == 2                        # 2회 시도 후 멈춘다(무한 재시도 없음)
    assert "작업 결과는" in out                    # 결과를 지우지 않고 어디 있는지 알려준다
    print("OK 검증 실패 — 1회 수정 후 통과 · 계속 실패하면 정지")


TESTS = [
    test_spec_is_built_without_an_llm,
    test_without_agent_config_nothing_runs,
    test_without_sandbox_execution_is_refused,
    test_probe_reports_ready_without_touching_the_assignment,
    test_instructor_ai_ban_stops_before_the_agent,
    test_unclear_policy_fails_closed_and_says_how_to_fix,
    test_no_approval_means_no_work_process,
    test_policy_gate_blocks_behaviour_not_bookkeeping,
    test_sandbox_check_refuses_to_certify_without_a_control,
    test_sandbox_check_certifies_only_what_it_proved,
    test_sandbox_check_flags_overclaimed_isolation,
    test_verify_sandbox_needs_no_agent_config,
    test_full_path_produces_a_verified_bundle_but_never_submits,
    test_submission_file_never_carries_raw_decision_markers,
    test_answers_file_fills_the_decisions,
    test_finish_reports_requirements_broken_by_substitution,
    test_validation_failure_repairs_once_then_stops,
]

if __name__ == "__main__":
    for case in TESTS:
        case()
    print("\nRUNTIME CLI TESTS PASS")
