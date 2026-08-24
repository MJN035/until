"""Until Runner — 신뢰할 수 없는 코드를 격리된 곳에서만 돌리는 별도 서비스.

웹 앱은 코드를 **절대 실행하지 않는다.** 세션·eTL 토큰·결제 원장이 같은 주소공간에
있는 프로세스에서 LLM이 쓴 코드를 돌릴 이유가 없다. 이 서비스가 대신 받는다.

세 가지가 이 파일의 전부다.

1. **격리를 증명하지 못하면 실행하지 않는다.** 기동 시 `runtime.sandbox_check`로
   자기 자신을 시험한다 — 네트워크가 실제로 막히는지, 작업공간 밖에 못 쓰는지.
   통과 못 하면 `/run`은 전부 503이다. 로컬 런타임의 fail-closed와 같은 기준이고,
   같은 판정기를 쓴다. `UNTIL_RUNNER_INSECURE=1`은 **개발용 탈출구**이며 응답에
   그 사실이 실려 나간다(조용히 안전한 척하지 않는다).
2. **명령은 요청이 정하지 않는다.** 요청은 `job` 종류를 고를 뿐이고, argv는 아래
   `_JOBS`에서 서버가 고른다. 커널 천장(`KERNEL_ALLOWED_COMMANDS`)으로 한 번 더 조인다.
3. **모든 한도는 코드로.** 서명·시각·크기·개수·타임아웃·출력 상한.

의존성은 표준 라이브러리뿐이다(`http.server`). 웹 앱과 같은 레포를 쓰지만 프로세스는
완전히 분리된다.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..runtime.models import RunStep
from . import protocol

#: 작업 종류 → 실행할 명령. **요청이 argv를 정하지 못한다**는 규칙의 구현이다.
#: `{workspace}` 치환도 없다 — 러너가 cwd를 작업공간으로 잡고 상대경로만 쓴다.
_JOBS = {
    "python_unittest": ("python3", "-m", "unittest", "discover", "-s", ".", "-t", "."),
    "python_pytest": ("python3", "-m", "pytest", "-q"),
    "python_syntax": ("python3", "-m", "compileall", "-q", "."),
}

_MAX_OUTPUT = 8 * 1024

#: 격리 시험에서 '작업공간 밖'으로 삼을 경로. 러너 코드가 사는 곳과 OS 설정 —
#: 학생 코드가 여기 쓸 수 있으면 러너 자신을 바꿔칠 수 있다는 뜻이다.
_ESCAPE_TARGETS = ("/app/.until-escape", "/etc/.until-escape")


def runner_key() -> str:
    return (os.getenv("UNTIL_RUNNER_KEY") or "").strip()


def insecure_mode() -> bool:
    """개발용 — 격리 증명 없이 실행을 허용한다. 운영에서 켜면 안 된다."""
    return (os.getenv("UNTIL_RUNNER_INSECURE") or "").strip() == "1"


def check_isolation(*, python: str = "python3") -> tuple[bool, str]:
    """자기 격리를 실제로 시험한다. (통과?, 사람이 읽는 사유).

    `--verify-sandbox`와 **같은 판정기**를 쓴다. 러너는 이미 격리된 컨테이너
    **안에서** 도는 것이 전제이므로 샌드박스 래퍼 없이(무포장) 시험한다 —
    "이 프로세스가 지금 갇혀 있는가"를 보는 것이다.
    """
    from ..runtime import sandbox_check

    with tempfile.TemporaryDirectory(prefix="until_runner_check_") as scratch:
        report = sandbox_check.verify(
            _NoWrap(), Path(scratch) / "ws", python=python,
            # 컨테이너에서 '밖'은 부모 디렉터리가 아니라 **시스템 경로**다.
            # 작업공간 부모는 대개 tmpfs라 써져도 무해하고, 그걸로 불합격을
            # 주면 멀쩡한 격리를 못 쓰게 된다. 진짜 확인할 것은 러너 코드와
            # OS를 건드릴 수 있는가다.
            escape_targets=_ESCAPE_TARGETS,
            # 러너는 **자기 자신이 샌드박스**다 — 비교할 '밖'이 없으므로 대조군을
            # 끈다. 여기서는 "지금 이 컨테이너에서 밖으로 못 나간다"가 곧 필요한
            # 보장이고, 그 상태는 컨테이너가 사는 동안 바뀌지 않는다.
            network_control=False)
    proven = set(report.safe_to_claim)
    if {"filesystem", "network"} <= proven:
        return True, "격리 확인됨(네트워크·작업공간 밖 쓰기 차단)"
    missing = sorted({"filesystem", "network"} - proven)
    details = "; ".join(f"{i.name}={i.status}" for i in report.results)
    return False, f"격리를 증명하지 못했습니다 — 미확인: {', '.join(missing)} ({details})"


class _NoWrap:
    """포장 없이 그대로 실행 — 러너는 자기가 이미 갇혀 있는지를 본다."""

    @staticmethod
    def wrap(argv, workspace):
        return tuple(argv)


def run_job(request: protocol.RunRequest, *, runner=None) -> dict:
    """요청을 임시 작업공간에 풀고 서버가 고른 명령을 돌린다.

    작업공간은 끝나면 반드시 지운다 — 남기면 다음 요청이 남의 파일을 본다.
    """
    from ..runtime.boundary import run_process
    from ..runtime.security import kernel_allowed, validate_step

    argv = _JOBS[request.job]
    step = RunStep(argv=argv, timeout_seconds=request.timeout_seconds,
                   network=False, stdout_limit_bytes=_MAX_OUTPUT,
                   stderr_limit_bytes=_MAX_OUTPUT)
    # 서버가 고른 명령이라도 천장을 통과해야 한다 — `_JOBS`에 실수로 셸을 적어도
    # 여기서 막힌다.
    validate_step(step, kernel_allowed((argv[0],)))

    execute = runner or run_process
    workspace = Path(tempfile.mkdtemp(prefix="until_runner_ws_"))
    try:
        for name, body in request.files.items():
            (workspace / name).write_text(body, encoding="utf-8")
        result = execute(argv, cwd=workspace, env=_child_env(),
                         timeout=request.timeout_seconds)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    if not getattr(result, "launched", True):
        return {"status": "tool_missing", "exit_code": None, "stdout": "",
                "stderr": "", "detail": f"명령을 찾지 못했습니다: {argv[0]}"}
    if getattr(result, "timed_out", False):
        return {"status": "timeout", "exit_code": None,
                "stdout": _clip(result.stdout), "stderr": _clip(result.stderr),
                "detail": f"{request.timeout_seconds}초 안에 끝나지 않았습니다"}
    ok = result.exit_code in (0, None)
    return {"status": "succeeded" if ok else "failed",
            "exit_code": result.exit_code,
            "stdout": _clip(result.stdout), "stderr": _clip(result.stderr),
            "detail": ""}


def _child_env() -> dict:
    """자식에게 넘길 환경 — 시크릿은 한 줄도 없다.

    러너가 어떤 비밀도 갖고 있지 않은 것이 원칙이지만, 원칙은 잊히므로 여기서
    화이트리스트로 다시 막는다.
    """
    return {"PATH": os.getenv("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": "/tmp", "LC_ALL": "C.UTF-8", "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1"}


def _clip(text) -> str:
    text = str(text or "")
    return text if len(text) <= _MAX_OUTPUT else text[:_MAX_OUTPUT] + "…(잘림)"


class Handler(BaseHTTPRequestHandler):
    server_version = "UntilRunner/1"
    isolation_ok = False
    isolation_reason = "미확인"

    def log_message(self, fmt, *args):    # 요청 본문·파일명을 로그에 남기지 않는다
        pass

    def _send(self, code: int, payload: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path != "/healthz":
            self._send(404, protocol.encode_result("error", detail="not found"))
            return
        self._send(200, protocol.encode_result(
            "ok" if self.isolation_ok else "degraded",
            detail=self.isolation_reason))

    def do_POST(self) -> None:
        if self.path != "/run":
            self._send(404, protocol.encode_result("error", detail="not found"))
            return
        if not self.isolation_ok and not insecure_mode():
            # 격리를 증명 못 했으면 **본문을 읽지도 않는다.**
            self._send(503, protocol.encode_result(
                "blocked", detail=self.isolation_reason))
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(400, protocol.encode_result("error", detail="bad length"))
            return
        if length <= 0 or length > protocol.MAX_TOTAL_BYTES:
            self._send(413, protocol.encode_result("error", detail="본문 크기 위반"))
            return
        body = self.rfile.read(length)
        try:
            protocol.verify(body, runner_key(),
                            self.headers.get("X-Until-Timestamp") or "",
                            self.headers.get("X-Until-Signature") or "",
                            now=time.time())
            request = protocol.parse_request(body)
        except protocol.ProtocolError as exc:
            self._send(400, protocol.encode_result("error", detail=str(exc)))
            return
        try:
            result = run_job(request)
        except Exception as exc:                    # 러너가 죽어도 웹은 계속 산다
            self._send(500, protocol.encode_result("error", detail=str(exc)[:200]))
            return
        if insecure_mode() and not self.isolation_ok:
            result["detail"] = ("⚠ 격리 미검증 상태로 실행됨(UNTIL_RUNNER_INSECURE=1) "
                                + result.get("detail", "")).strip()
        self._send(200, protocol.encode_result(**result))


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="until.runner", description="Until 코드 실행 러너(격리 필수)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=int(os.getenv("PORT") or 8900))
    ap.add_argument("--python", default="python3",
                    help="격리 시험에 쓸 파이썬 경로")
    args = ap.parse_args(argv)

    if not runner_key():
        print("UNTIL_RUNNER_KEY가 없습니다 — 서명 없는 요청을 받을 수는 없습니다.",
              flush=True)
        return 2

    ok, reason = check_isolation(python=args.python)
    Handler.isolation_ok, Handler.isolation_reason = ok, reason
    print(("격리 확인 — 실행을 엽니다: " if ok else "격리 미확인 — 실행을 막습니다: ")
          + reason, flush=True)
    if not ok and insecure_mode():
        print("⚠ UNTIL_RUNNER_INSECURE=1 — 격리 없이 실행합니다. 운영에서 쓰지 마세요.",
              flush=True)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"until-runner on {args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
