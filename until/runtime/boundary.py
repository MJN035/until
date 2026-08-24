"""Phase 2 — 실행 경계(프로세스를 실제로 띄우는 유일한 자리).

커널(`LocalAgentController`)은 파일시스템·환경·네트워크 **셋 다** 격리됐다고
경계가 스스로 밝힐 때만 실행을 허용한다. 이 모듈은 그 세 값을 **정직하게**
계산한다 — 진짜로 격리되지 않았으면 False를 돌려주고, 그러면 커널이 실행을
거부한다. 기본값은 여전히 '실행 불가'다.

왜 샌드박스를 사용자가 주는가
-----------------------------
파이썬만으로는 임의의 CLI가 작업공간 밖을 못 쓰게 막을 수 없다. 그래서 OS 샌드박스
래퍼(bubblewrap, sandbox-exec, firejail, 컨테이너 …)를 **운영자가 지정**하게 하고,
지정됐을 때만 filesystem/network 격리를 참으로 신고한다. `cwd=작업공간`과
환경변수 세탁만으로 "격리됐다"고 주장하지 않는다 — 그건 거짓말이 된다.

    UNTIL_AGENT_SANDBOX='bwrap,--ro-bind,/usr,/usr,--bind,{workspace},{workspace},--unshare-net'
    UNTIL_AGENT_SANDBOX_ISOLATES=filesystem,network      # 래퍼가 보장하는 것

`{workspace}`는 작업공간 루트로 치환된다. 환경 격리는 커널이 이미
`sanitize_environment`로 보장하므로 이 모듈이 항상 참으로 신고한다.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

from .cli_agent import CommandResult, with_runner

_MAX_CAPTURE_BYTES = 256 * 1024        # 로그는 receipt에서 다시 8KB로 잘린다


@dataclass(frozen=True)
class SandboxSpec:
    """운영자가 지정한 OS 샌드박스 래퍼."""
    argv_prefix: tuple[str, ...] = ()
    isolates_filesystem: bool = False
    isolates_network: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.argv_prefix)

    def wrap(self, argv: Sequence[str], workspace: Path) -> tuple[str, ...]:
        prefix = tuple(part.replace("{workspace}", str(workspace))
                       for part in self.argv_prefix)
        return prefix + tuple(argv)


def load_sandbox(environ: Mapping[str, str] | None = None) -> SandboxSpec:
    env = os.environ if environ is None else environ
    raw = (env.get("UNTIL_AGENT_SANDBOX") or "").strip()
    if not raw:
        return SandboxSpec()
    prefix = tuple(part for part in (p.strip() for p in raw.split(",")) if part)
    claims = {c.strip().lower() for c in
              (env.get("UNTIL_AGENT_SANDBOX_ISOLATES") or "").split(",") if c.strip()}
    return SandboxSpec(prefix,
                       isolates_filesystem="filesystem" in claims,
                       isolates_network="network" in claims)


def run_process(argv: Sequence[str], *, cwd: Path, env: Mapping[str, str],
                timeout: int, stdin_text: str = "") -> CommandResult:
    """프로세스 하나를 띄우고 결과를 정규화한다(출력은 상한까지만 읽는다)."""
    try:
        completed = subprocess.run(          # argv 리스트 + shell=False(문자열 셸 미사용)
            list(argv), cwd=str(cwd), env=dict(env), timeout=timeout,
            input=stdin_text, capture_output=True, text=True, encoding="utf-8",
            errors="replace", shell=False, check=False,
        )
    except FileNotFoundError as exc:
        return CommandResult(None, launched=False, stderr=str(exc))
    except subprocess.TimeoutExpired as exc:
        return CommandResult(None, _text(exc.stdout), _text(exc.stderr), timed_out=True)
    except KeyboardInterrupt:
        return CommandResult(None, cancelled=True, stderr="interrupted")
    except OSError as exc:
        return CommandResult(None, launched=False, stderr=str(exc))
    return CommandResult(completed.returncode,
                         _text(completed.stdout), _text(completed.stderr))


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return value[:_MAX_CAPTURE_BYTES]


@dataclass
class SubprocessBoundary:
    """공식 CLI를 격리 안에서 실행하는 경계.

    격리 신고는 실제 구성에서 계산한다 — 샌드박스가 없으면 filesystem/network가
    False라 커널이 실행 자체를 막는다(`_require_isolation`)."""
    sandbox: SandboxSpec
    workspace_provider: Optional[callable] = None
    runner: callable = run_process

    @property
    def filesystem_isolated(self) -> bool:
        return self.sandbox.configured and self.sandbox.isolates_filesystem

    @property
    def environment_isolated(self) -> bool:
        # 커널이 sanitize_environment로 시크릿을 걷어낸 dict만 넘겨준다.
        return True

    @property
    def network_isolated(self) -> bool:
        return self.sandbox.configured and self.sandbox.isolates_network

    # ── ExecutionBoundary 프로토콜 ─────────────────────────────────
    def preview(self, agent, job, environment):
        """probe + plan. plan은 결정적이라 여기서도 프로세스는 probe뿐이다."""
        bound = with_runner(agent, self._runner_for(environment))
        availability = bound.probe()
        plan = bound.plan(job) if availability.status == "ready" else None
        return availability, plan

    def execute(self, agent, job, approval, environment):
        return with_runner(agent, self._runner_for(environment)).execute(job, approval)

    def continue_job(self, agent, receipt, feedback, environment):
        return with_runner(agent, self._runner_for(environment)).continue_job(
            receipt, feedback)

    def run_step(self, step, workspace_root: Path, environment: Mapping[str, str]):
        """검증 명령 하나를 **에이전트와 똑같은 격리 안에서** 돌린다.

        에이전트만 가두고 검증기는 밖에서 돌리면 격리가 반쪽이 된다 — 테스트
        코드도 결국 학생 과제가 만든 코드를 부른다. 같은 래퍼, 같은 세탁된 환경.
        """
        root = Path(workspace_root)
        argv = self.sandbox.wrap(tuple(step.argv), root)
        return self.runner(argv, cwd=root, env=dict(environment),
                           timeout=step.timeout_seconds)

    # ── 내부 ───────────────────────────────────────────────────────
    def _runner_for(self, environment: Mapping[str, str]):
        sandbox, runner = self.sandbox, self.runner

        def _run(argv, *, cwd, env=None, timeout, stdin_text=""):
            root = Path(cwd)
            # 환경은 **커널이 세탁한 것만** 쓴다 — 어댑터가 넘긴 값은 무시한다.
            return runner(sandbox.wrap(argv, root), cwd=root,
                          env=dict(environment), timeout=timeout,
                          stdin_text=stdin_text)
        return _run


def build_boundary(environ: Mapping[str, str] | None = None,
                   *, runner=run_process) -> SubprocessBoundary:
    """환경에서 샌드박스 설정을 읽어 경계를 만든다(미설정이면 실행 불가 상태)."""
    return SubprocessBoundary(load_sandbox(environ), runner=runner)
