# -*- coding: utf-8 -*-
"""Until Runner — 웹이 코드를 실행할 수 있게 하는 별도 서비스.

웹 앱은 코드를 **절대 실행하지 않는다.** 신뢰할 수 없는 코드(LLM이 쓴 답 + 과제가
준 테스트)를 세션·eTL 토큰·결제 원장과 같은 주소공간에서 돌릴 이유가 없다.

이 스위트가 지키는 것 — 전부 오프라인(실제 프로세스·네트워크 0):
  - **요청자가 실행할 명령을 정하지 못한다.** argv는 서버가 고른다.
  - 격리를 증명 못 하면 실행하지 않는다(fail-closed).
  - 서명·시각·크기·개수·경로가 전부 코드로 막힌다.
  - 러너가 없거나 죽어도 웹은 그대로 굴러간다.
  - **못 돌린 것과 실패한 것을 구분한다.**

실제 컨테이너에서의 격리·타임아웃·네트워크 차단은 이 스위트가 아니라 손으로
확인했다(계획서 `docs/planning/2026-08-21-web-execution-runner.md` R6).
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.runner import assemble, client, protocol, service

KEY = "test-runner-key"


def _signed(body: bytes, *, key=KEY, when=None):
    stamp = f"{when if when is not None else time.time():.0f}"
    return stamp, protocol.sign(body, key, stamp)


# ── 프로토콜 ────────────────────────────────────────────────────────
def test_request_cannot_choose_a_command():
    """요청에 argv를 실을 자리가 아예 없다 — 있으면 그건 원격 셸이다."""
    raw = json.dumps({"job": "python_unittest", "files": {"a.py": "x=1"},
                      "argv": ["sh", "-c", "curl evil"]}).encode()
    request = protocol.parse_request(raw)
    assert not hasattr(request, "argv")
    # 서버가 고르는 명령에도 셸이 없다.
    for argv in service._JOBS.values():
        assert argv[0] in ("python3", "python"), argv
    print("OK 요청은 명령을 정하지 못한다")


def test_unknown_job_is_refused():
    raw = json.dumps({"job": "rm_rf", "files": {"a.py": "x=1"}}).encode()
    try:
        protocol.parse_request(raw)
        raise AssertionError("모르는 작업이 통과했다")
    except protocol.ProtocolError as exc:
        assert "모르는 작업" in str(exc)
    print("OK 모르는 작업 거절")


def test_path_escape_and_bad_extensions_are_refused():
    for name in ("../etc/passwd", "sub/dir.py", "a.sh", ".ssh", "x.py\x00"):
        raw = json.dumps({"job": "python_syntax", "files": {name: "x=1"}}).encode()
        try:
            protocol.parse_request(raw)
            raise AssertionError(f"{name!r}가 통과했다")
        except protocol.ProtocolError:
            pass
    print("OK 경로 탈출·허용 안 된 확장자 거절")


def test_size_and_count_limits():
    big = {"a.py": "x" * (protocol.MAX_FILE_BYTES + 1)}
    try:
        protocol.parse_request(json.dumps({"job": "python_syntax",
                                           "files": big}).encode())
        raise AssertionError("큰 파일이 통과했다")
    except protocol.ProtocolError:
        pass
    many = {f"f{i}.py": "x=1" for i in range(protocol.MAX_FILES + 1)}
    try:
        protocol.parse_request(json.dumps({"job": "python_syntax",
                                           "files": many}).encode())
        raise AssertionError("파일이 너무 많은데 통과했다")
    except protocol.ProtocolError:
        pass
    print("OK 크기·개수 상한")


def test_signature_and_replay_window():
    body = b'{"job":"python_syntax","files":{"a.py":"x=1"}}'
    stamp, signature = _signed(body)
    protocol.verify(body, KEY, stamp, signature, now=time.time())     # 정상

    # 마지막 한 글자를 **반드시 다른 값으로** 바꾼다. `+ "0"`으로 고정하면 유효
    # 서명이 이미 '0'으로 끝날 때(hex라 1/16) '잘못된 서명'이 진짜 서명과 같아져
    # 테스트가 무작위로 실패한다 — 실제로 병렬 실행 플레이크로 오인했던 원인이다.
    flipped = signature[:-1] + ("1" if signature[-1] == "0" else "0")
    for bad in ("", "deadbeef", flipped):
        try:
            protocol.verify(body, KEY, stamp, bad, now=time.time())
            raise AssertionError("잘못된 서명이 통과했다")
        except protocol.ProtocolError:
            pass

    # 본문이 한 글자만 바뀌어도 서명이 깨진다.
    try:
        protocol.verify(body + b" ", KEY, stamp, signature, now=time.time())
        raise AssertionError("변조된 본문이 통과했다")
    except protocol.ProtocolError:
        pass

    # 오래된 서명은 재사용할 수 없다.
    old_stamp, old_sig = _signed(body, when=time.time() - 3600)
    try:
        protocol.verify(body, KEY, old_stamp, old_sig, now=time.time())
        raise AssertionError("만료된 서명이 통과했다")
    except protocol.ProtocolError as exc:
        assert "만료" in str(exc)
    print("OK 서명·변조·재생 방지")


# ── 서비스 ──────────────────────────────────────────────────────────
def test_job_runs_the_server_chosen_command_and_cleans_up():
    calls = {}

    class _Result:
        launched, timed_out, exit_code = True, False, 0
        stdout, stderr = "OK", ""

    def fake_run(argv, *, cwd, env, timeout):
        calls["argv"] = tuple(argv)
        calls["files"] = sorted(p.name for p in pathlib.Path(cwd).iterdir())
        calls["env"] = dict(env)
        calls["cwd"] = pathlib.Path(cwd)
        return _Result()

    request = protocol.RunRequest("python_unittest",
                                  {"solution.py": "x=1", "test_a.py": "y=2"}, 30)
    out = service.run_job(request, runner=fake_run)
    assert out["status"] == "succeeded"
    assert calls["argv"] == service._JOBS["python_unittest"]
    assert calls["files"] == ["solution.py", "test_a.py"]
    # 시크릿은 한 줄도 넘어가지 않는다.
    assert set(calls["env"]) <= {"PATH", "HOME", "LC_ALL", "PYTHONIOENCODING",
                                 "PYTHONDONTWRITEBYTECODE"}
    # 작업공간은 반드시 지운다 — 남기면 다음 요청이 남의 파일을 본다.
    assert not calls["cwd"].exists()
    print("OK 실행 — 서버가 고른 명령 · 시크릿 없음 · 작업공간 정리")


def test_timeout_and_missing_tool_are_distinct():
    class _Timeout:
        launched, timed_out, exit_code = True, True, None
        stdout, stderr = "", ""

    class _Missing:
        launched, timed_out, exit_code = False, False, None
        stdout, stderr = "", "not found"

    request = protocol.RunRequest("python_syntax", {"a.py": "x=1"}, 5)
    assert service.run_job(request, runner=lambda *a, **k: _Timeout())["status"] == "timeout"
    assert service.run_job(request, runner=lambda *a, **k: _Missing())["status"] == "tool_missing"
    print("OK 타임아웃과 도구 없음을 구분")


def test_isolation_failure_is_reported_not_hidden():
    """격리를 증명 못 하면 사유가 그대로 올라온다 — 조용히 안전한 척하지 않는다."""
    from until.runtime import sandbox_check

    def all_open(argv, *, cwd, env, timeout):
        class _R:
            launched, timed_out, exit_code = True, False, 0
            stdout, stderr = "OPEN", ""
        return _R()

    import tempfile
    with tempfile.TemporaryDirectory() as raw:
        report = sandbox_check.verify(
            service._NoWrap(), pathlib.Path(raw) / "ws", runner=all_open,
            escape_targets=("/app/x",), network_control=False)
    assert report.safe_to_claim == (), report.safe_to_claim
    print("OK 격리 미증명은 그대로 보고")


# ── 클라이언트(웹 쪽) ───────────────────────────────────────────────
def test_client_is_off_by_default(monkey=None):
    import os
    saved = {k: os.environ.pop(k, None) for k in ("UNTIL_RUNNER_URL", "UNTIL_RUNNER_KEY")}
    try:
        assert not client.configured()
        assert client.run("python_syntax", {"a.py": "x=1"}) is None
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
    print("OK 러너 미설정이면 아무 일도 하지 않는다")


def test_client_failures_never_raise():
    """러너가 죽어도 웹은 초안·제출을 계속해야 한다."""
    import os

    os.environ["UNTIL_RUNNER_URL"] = "http://127.0.0.1:9"
    os.environ["UNTIL_RUNNER_KEY"] = KEY
    try:
        def boom(*a, **k):
            raise OSError("connection refused")
        out = client.run("python_syntax", {"a.py": "x=1"}, opener=boom)
        assert out["status"] == "error" and "연결" in out["detail"]
    finally:
        os.environ.pop("UNTIL_RUNNER_URL", None)
        os.environ.pop("UNTIL_RUNNER_KEY", None)
    print("OK 러너 장애는 예외가 아니라 상태로")


# ── 조립·요약 ───────────────────────────────────────────────────────
class _Doc:
    def __init__(self, source, text):
        self.source, self.text = source, text


def _result(body, docs, strategy="code_project"):
    from until.boundary.models import Draft
    from until.context.assignment_router import AssignmentRoute
    from until.execution.boundary_guard import GuardReport
    from until.pipeline import Result

    res = Result(documents=docs, spec={"title": "T"}, draft=Draft.from_text(body),
                 guard=GuardReport(passed=True, attempts=1, reasks=0), sources=[])
    res.assignment_route = AssignmentRoute(strategy, "fixture", ())
    return res


def test_assemble_sends_code_and_tests_only():
    docs = [_Doc("/x/test_solution.py", "import unittest\n"),
            _Doc("/x/과제안내.md", "과제 설명 원문")]
    res = _result("```python\ndef add(a,b): return a+b\n```\n", docs)
    job, files = assemble.files_for(res)
    assert job == "python_unittest"
    # 과제 원문·강의자료는 보내지 않는다 — 보낼 이유가 없는 것은 안 보낸다.
    assert sorted(files) == ["solution.py", "test_solution.py"]
    print("OK 조립 — 코드와 테스트만 보낸다")


def test_assemble_stays_silent_without_tests_or_code():
    only_code = _result("```python\nx=1\n```", [])
    assert assemble.files_for(only_code) is None      # 테스트 없음 → 안 보냄
    only_tests = _result("본문뿐", [_Doc("/x/test_a.py", "import unittest\n")])
    assert assemble.files_for(only_tests) is None     # 코드 없음
    prose = _result("```python\nx=1\n```",
                    [_Doc("/x/test_a.py", "import unittest\n")], "evidence_report")
    assert assemble.files_for(prose) is None          # 산문 과제엔 안 붙인다
    print("OK 조립 — 돌릴 게 없으면 보내지 않는다")


def test_summary_separates_failed_from_could_not_run():
    assert assemble.summarize({"status": "succeeded"})[0] == "ok"
    assert assemble.summarize({"status": "failed", "stdout": "FAILED (failures=1)"})[0] == "warn"
    assert assemble.summarize({"status": "timeout"})[0] == "warn"
    for status in ("blocked", "tool_missing", "error"):
        level, message = assemble.summarize({"status": status, "detail": "사유"})
        assert level == "info", (status, level)
        assert "코드가 틀렸다는 뜻이 아닙니다" in message
    assert assemble.summarize(None) is None
    print("OK 요약 — 실패와 '못 돌림'을 구분")


TESTS = [
    test_request_cannot_choose_a_command,
    test_unknown_job_is_refused,
    test_path_escape_and_bad_extensions_are_refused,
    test_size_and_count_limits,
    test_signature_and_replay_window,
    test_job_runs_the_server_chosen_command_and_cleans_up,
    test_timeout_and_missing_tool_are_distinct,
    test_isolation_failure_is_reported_not_hidden,
    test_client_is_off_by_default,
    test_client_failures_never_raise,
    test_assemble_sends_code_and_tests_only,
    test_assemble_stays_silent_without_tests_or_code,
    test_summary_separates_failed_from_could_not_run,
]

if __name__ == "__main__":
    for case in TESTS:
        case()
    print("\nRUNNER TESTS PASS")
