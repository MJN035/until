"""Phase 2 — 공식 로컬 AI CLI 어댑터.

계약(`docs/ASSIGNMENT_RUNTIME_PLAN.md` §7 Phase 2)을 그대로 코드로 옮긴다:

  - **공식 설치·로그인 흐름만** 쓴다. 세션 파일·쿠키·OAuth 토큰을 읽지 않는다.
  - CLI가 공식으로 제공하는 **비대화형 실행 범위 안에서만** 자동화한다.
  - auto-approve 플래그를 쓰지 않는다 — 승인은 Until의 plan 승인 하나뿐이다.
  - 종료 코드·변경 파일·잘린 로그를 receipt로 바꾼다.
  - 구독 한도에 걸리면 결제를 우회하지 않고 ``usage_limited``로 끝낸다.

**CLI 인자를 코드에 못 박지 않는다.** 벤더마다 다르고 버전마다 바뀌므로,
어떤 명령을 어떻게 부를지는 사용자가 `CliSpec`(JSON)으로 준다. 추측한 플래그를
내장하면 "되는 척"하는 어댑터가 되고, 그건 이 계획서가 금지하는 것이다.

    UNTIL_AGENT_SPEC=~/.until/agent.json     # 아래 CliSpec 스키마
    # 또는 최소 설정
    UNTIL_AGENT_CMD=claude
    UNTIL_AGENT_RUN_ARGS=-p,{prompt}

이 모듈은 프로세스를 **직접 띄우지 않는다** — 실제 실행은 격리 경계
(`until.runtime.boundary`)가 주입한 러너가 한다. 격리 없이는 커널이 실행을
거부한다(`LocalAgentController._require_isolation`).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from .models import AgentAvailability, AgentFeedback, AgentJob, AgentPlan, AgentReceipt
from .security import RuntimeSecurityError, confined_path

# 종료 코드 관례 — 사용자가 Ctrl-C로 끊은 경우를 실패와 구분한다.
_CANCELLED_EXIT_CODES = frozenset({130, -2, 3221225786})

_DEFAULT_LOGIN_MARKERS = (
    "not logged in", "please log in", "please login", "login required",
    "unauthenticated", "not authenticated", "run `login`", "auth required",
    "로그인이 필요", "로그인 필요", "인증이 필요",
)
_DEFAULT_LIMIT_MARKERS = (
    "usage limit", "rate limit", "quota exceeded", "too many requests",
    "out of credits", "subscription limit", "limit reached",
    "사용량 한도", "한도 초과", "요청이 너무",
)


@dataclass(frozen=True)
class CommandResult:
    """러너가 돌려주는 한 번의 프로세스 실행 결과."""
    exit_code: Optional[int]
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    cancelled: bool = False
    launched: bool = True          # False = 실행 파일을 찾지 못함

    @property
    def text(self) -> str:
        return f"{self.stdout}\n{self.stderr}"


# 러너 계약: (argv, cwd, env, timeout, stdin_text) -> CommandResult
Runner = Callable[..., CommandResult]


class CliSpecError(ValueError):
    """CLI 설정이 없거나 형식이 잘못됐다 — 실행 대신 조용히 unavailable."""


@dataclass(frozen=True)
class CliSpec:
    """공식 CLI 하나를 부르는 방법(전부 사용자 제공, 추측 없음).

    run_args의 ``{prompt}``는 workspace 안 프롬프트 파일의 **절대 경로**로,
    ``{workspace}``는 작업공간 루트로 치환된다. prompt_via="stdin"이면 자리표시
    없이 프롬프트 본문을 표준입력으로 넘긴다.
    """
    name: str
    command: str
    version_args: tuple[str, ...] = ("--version",)
    status_args: tuple[str, ...] = ()
    run_args: tuple[str, ...] = ()
    prompt_via: str = "arg"                     # arg | stdin
    login_markers: tuple[str, ...] = _DEFAULT_LOGIN_MARKERS
    limit_markers: tuple[str, ...] = _DEFAULT_LIMIT_MARKERS
    probe_timeout_seconds: int = 20

    def __post_init__(self):
        if not self.command.strip():
            raise CliSpecError("agent command is required")
        if self.prompt_via not in ("arg", "stdin"):
            raise CliSpecError(f"unknown prompt_via: {self.prompt_via}")
        if self.prompt_via == "arg" and "{prompt}" not in " ".join(self.run_args):
            raise CliSpecError("run_args must contain {prompt} when prompt_via=arg")
        # 승인은 Until의 plan 승인 하나뿐이다(계획서 Phase 2 4번).
        # probe 인자(version/status)는 **승인 전에** 실행되므로 더 엄격히 봐야 한다.
        for label, args in (("run_args", self.run_args),
                            ("version_args", self.version_args),
                            ("status_args", self.status_args)):
            for arg in args:
                if _looks_like_auto_approve(arg):
                    raise CliSpecError(
                        f"auto-approve flags are not allowed in {label} "
                        f"({arg!r}) — Until plan approval is the only gate")

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "CliSpec":
        def seq(key: str, default: Sequence[str] = ()) -> tuple[str, ...]:
            value = data.get(key, default)
            if isinstance(value, str):
                value = [part for part in value.split(",") if part.strip()]
            return tuple(str(item).strip() for item in (value or ()) if str(item).strip())

        command = str(data.get("command") or "").strip()
        return cls(
            name=str(data.get("name") or command or "local-agent"),
            command=command,
            version_args=seq("version_args", ("--version",)),
            status_args=seq("status_args"),
            run_args=seq("run_args"),
            prompt_via=str(data.get("prompt_via") or "arg"),
            login_markers=seq("login_markers", _DEFAULT_LOGIN_MARKERS),
            limit_markers=seq("limit_markers", _DEFAULT_LIMIT_MARKERS),
            probe_timeout_seconds=int(data.get("probe_timeout_seconds") or 20),
        )


_AUTO_APPROVE_NAMES = frozenset({
    "yes", "y", "force", "auto-approve", "auto-accept", "dangerously-skip-permissions",
    "no-confirm", "assume-yes", "accept-all", "allow-all", "bypass-approvals",
    "skip-permissions", "no-interactive", "non-interactive-approve",
})


def _looks_like_auto_approve(arg: str) -> bool:
    """자동 승인 플래그 판정 — 표기 변형까지 잡는다.

    감사(2026-08-20)에서 세 가지 우회가 확인됐다:
      ① `--yes=true` 처럼 값이 결합된 형태 → `=`/`:` 앞의 이름만 떼어 본다.
      ② 전각 `--ｙｅｓ` 같은 유니코드 호환 문자 → NFKC 정규화로 접는다.
      ③ version_args·status_args는 아예 검사하지 않았다 → 호출부에서 전부 검사한다.
    """
    import unicodedata
    text = unicodedata.normalize("NFKC", (arg or "").strip()).casefold()
    name = text.split("=", 1)[0].split(":", 1)[0]
    name = name.lstrip("-/").replace("_", "-").strip()
    return name in _AUTO_APPROVE_NAMES


def load_cli_spec(environ: Mapping[str, str] | None = None) -> Optional[CliSpec]:
    """환경에서 CLI 설정을 읽는다. 설정이 없으면 None(어댑터 비활성)."""
    env = os.environ if environ is None else environ
    path = (env.get("UNTIL_AGENT_SPEC") or "").strip()
    if path:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CliSpecError(f"agent spec을 읽지 못했습니다: {exc}") from None
        if not isinstance(data, dict):
            raise CliSpecError("agent spec은 JSON 오브젝트여야 합니다")
        return CliSpec.from_mapping(data)
    command = (env.get("UNTIL_AGENT_CMD") or "").strip()
    if not command:
        return None
    return CliSpec.from_mapping({
        "command": command,
        "name": env.get("UNTIL_AGENT_NAME") or command,
        "version_args": env.get("UNTIL_AGENT_VERSION_ARGS") or "--version",
        "status_args": env.get("UNTIL_AGENT_STATUS_ARGS") or "",
        "run_args": env.get("UNTIL_AGENT_RUN_ARGS") or "",
        "prompt_via": env.get("UNTIL_AGENT_PROMPT_VIA") or "arg",
    })


@dataclass
class OfficialCliAgent:
    """`LocalAgent` 프로토콜 구현 — 공식 CLI 하나를 비대화형으로 부른다.

    workspace_provider: 현재 `RuntimeWorkspace`(또는 root Path)를 돌려주는 콜러블.
        런타임 플러그인이 작업공간을 만들면서 채운다.
    runner: 프로세스 실행자. **격리 경계가 주입한다** — 기본값은 없다(주입 전엔
        어떤 프로세스도 뜨지 않는다).
    """
    spec: CliSpec
    workspace_provider: Callable[[], object]
    runner: Optional[Runner] = None
    name: str = field(default="", init=False)
    _last: Optional[tuple] = field(default=None, init=False, repr=False)

    def __post_init__(self):
        self.name = self.spec.name

    # ── 내부 ────────────────────────────────────────────────────────
    def _root(self) -> Path:
        workspace = self.workspace_provider()
        root = getattr(workspace, "root", workspace)
        if root is None:
            raise RuntimeSecurityError("agent workspace is not prepared")
        return Path(root)

    def _run(self, argv: Sequence[str], *, timeout: int, stdin_text: str = "",
             env: Mapping[str, str] | None = None) -> CommandResult:
        if self.runner is None:
            # 격리 러너가 주입되지 않았다 — 프로세스를 띄우지 않는다.
            return CommandResult(None, launched=False,
                                 stderr="isolated runner is not attached")
        return self.runner(tuple(argv), cwd=self._root(), env=dict(env or {}),
                           timeout=timeout, stdin_text=stdin_text)

    def _classify(self, result: CommandResult) -> str:
        """CLI 출력에서 미로그인·한도를 결정적으로 가려낸다."""
        text = result.text.lower()
        if any(marker.lower() in text for marker in self.spec.login_markers):
            return "login_required"
        if any(marker.lower() in text for marker in self.spec.limit_markers):
            return "usage_limited"
        return ""

    # ── LocalAgent 프로토콜 ─────────────────────────────────────────
    def probe(self) -> AgentAvailability:
        """설치 여부(version) → 사용자가 직접 로그인했는지(status)만 확인한다."""
        if self.runner is None:
            # 격리 경계가 붙기 전 — 확인조차 실행이므로 하지 않는다.
            return AgentAvailability(
                "unavailable", self.name,
                reason="격리 실행 경계가 없습니다 — UNTIL_AGENT_SANDBOX를 설정해 주세요")
        # 여기서 shutil.which로 **호스트** 경로를 보지 않는다: 실제 실행은 샌드박스
        # 안에서 일어나고 그 안의 PATH는 다를 수 있다. 못 찾았는지는 러너가 말한다.
        version = self._run([self.spec.command, *self.spec.version_args],
                            timeout=self.spec.probe_timeout_seconds)
        if not version.launched:
            return AgentAvailability(
                "unavailable", self.name,
                reason=(f"{self.spec.command} 를 찾지 못했습니다 — 공식 설치 안내를 "
                        f"따라 주세요 ({version.stderr[:120]})".strip()))
        if version.timed_out:
            return AgentAvailability("unavailable", self.name,
                                     reason="version 확인이 시간 초과됐습니다")
        if version.exit_code not in (0, None):
            return AgentAvailability(
                "unavailable", self.name,
                reason=f"version 확인 실패(exit {version.exit_code})")
        label = (version.stdout or version.stderr).strip().splitlines()
        label = label[0][:80] if label else ""
        if not self.spec.status_args:
            # 상태 확인 명령이 없는 CLI — 로그인 여부는 실제 실행에서만 드러난다.
            return AgentAvailability("ready", self.name, label,
                                     reason="status 확인 명령이 없어 실행 시 판정합니다")
        status = self._run([self.spec.command, *self.spec.status_args],
                           timeout=self.spec.probe_timeout_seconds)
        kind = self._classify(status)
        if kind == "login_required" or (status.exit_code not in (0, None) and not kind):
            return AgentAvailability(
                "login_required", self.name, label,
                reason="공식 CLI에서 직접 로그인한 뒤 다시 시도해 주세요")
        if kind == "usage_limited":
            return AgentAvailability("busy", self.name, label,
                                     reason="구독 사용량 한도에 걸려 있습니다")
        return AgentAvailability("ready", self.name, label)

    def plan(self, job: AgentJob) -> AgentPlan:
        """계획은 **결정적으로** 만든다 — CLI를 부르지 않는다.

        미리보기 단계에서 프로세스를 띄우면 승인 전에 파일이 바뀔 수 있고,
        커널은 그걸 보안 위반으로 본다(preview 후 스냅샷 비교)."""
        return AgentPlan(
            job.fingerprint,
            f"{self.name}에게 {job.assignment_id} 작업을 맡기고 "
            f"편집 허용 범위 안의 파일만 고치게 합니다",
            tuple(job.editable_paths),
            tuple(job.allowed_tools) or ("editor",),
        )

    def execute(self, job: AgentJob, approval) -> AgentReceipt:
        return self._invoke(job, prompt_relpath=job.prompt_path)

    def continue_job(self, receipt: AgentReceipt, feedback: AgentFeedback) -> AgentReceipt:
        """검증 실패 1회 재시도 — 무엇이 왜 막혔는지를 수정 지시 파일로 넘긴다.

        원래 프롬프트(PROMPT.md)는 **읽기 전용**이라 여기에 덧붙이면 커널이
        workspace_escape로 막는다. 그래서 편집 허용 목록에 있는 별도 파일에 쓴다.
        변경 전 스냅샷은 이 쓰기보다 **먼저** 뜬다 — 그래야 receipt의 변경 목록이
        오케스트레이터가 관측한 변경과 정확히 일치한다(validate_receipt).
        """
        if self._last is None:
            return AgentReceipt("failed", reason="이어서 실행할 작업이 없습니다")
        job, _prompt_relpath = self._last
        root = self._root()
        before = _snapshot(root)                 # 우리 쓰기 이전 상태
        repair_relpath = _repair_relpath(job)
        if repair_relpath is None:
            return AgentReceipt(
                "failed", reason="재시도 지시를 쓸 편집 가능 파일이 없습니다")
        lines = ["# 수정 요청 (자동 검증 실패)", "",
                 "직전 결과가 아래 항목에서 막혔습니다. **그 항목만** 고치세요.",
                 "원래 지시는 `work/PROMPT.md`에 그대로 있습니다.", ""]
        for code, message in zip(feedback.codes, feedback.messages, strict=False):
            lines.append(f"- [{code}] {message}")
        lines += ["", "편집 허용 범위 밖의 파일은 건드리지 마세요."]
        try:
            target = confined_path(root, repair_relpath)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except (RuntimeSecurityError, OSError) as exc:
            return AgentReceipt("failed", reason=f"수정 지시를 쓰지 못했습니다: {exc}")
        return self._invoke(job, prompt_relpath=repair_relpath, before=before)

    # ── 실행 본체 ───────────────────────────────────────────────────
    def _invoke(self, job: AgentJob, *, prompt_relpath: str,
                before: dict | None = None) -> AgentReceipt:
        root = self._root()
        try:
            prompt_path = confined_path(root, prompt_relpath, must_exist=True)
        except (RuntimeSecurityError, OSError) as exc:
            # must_exist=True는 없는 파일에 FileNotFoundError(=OSError)를 던진다.
            # 보안 오류만 잡으면 프롬프트가 사라졌을 때 예외가 그대로 튀어나가
            # 오케스트레이터가 receipt 없이 죽는다 — 모든 실패는 receipt로 정규화한다.
            return AgentReceipt("failed", reason=str(exc))
        if before is None:
            before = _snapshot(root)
        stdin_text = ""
        if self.spec.prompt_via == "stdin":
            try:
                stdin_text = prompt_path.read_text(encoding="utf-8")
            except OSError as exc:
                return AgentReceipt("failed", reason=f"프롬프트를 읽지 못했습니다: {exc}")
            argv = [self.spec.command, *self.spec.run_args]
        else:
            argv = [self.spec.command]
            for arg in self.spec.run_args:
                argv.append(arg.replace("{prompt}", str(prompt_path))
                               .replace("{workspace}", str(root)))
        result = self._run(argv, timeout=job.timeout_seconds, stdin_text=stdin_text)
        self._last = (job, prompt_relpath)
        changed = _changed_paths(before, _snapshot(root))
        tool_kinds = tuple(job.allowed_tools) or ("editor",)
        if not result.launched:
            return AgentReceipt("failed", changed, tool_kinds, result.exit_code,
                                result.stdout, result.stderr,
                                reason=result.stderr or "실행 경계가 붙어 있지 않습니다")
        if result.timed_out:
            return AgentReceipt("timeout", changed, tool_kinds, result.exit_code,
                                result.stdout, result.stderr,
                                reason=f"{job.timeout_seconds}초 안에 끝나지 않았습니다")
        if result.cancelled or (result.exit_code in _CANCELLED_EXIT_CODES):
            return AgentReceipt("cancelled", changed, tool_kinds, result.exit_code,
                                result.stdout, result.stderr, reason="사용자가 중단했습니다")
        kind = self._classify(result)
        if kind == "login_required":
            return AgentReceipt("login_required", changed, tool_kinds, result.exit_code,
                                result.stdout, result.stderr,
                                reason="공식 CLI에서 직접 로그인한 뒤 다시 시도해 주세요")
        if kind == "usage_limited":
            # 결제 우회 없음 — 여기서 끝낸다(계획서 Phase 2 7번).
            return AgentReceipt("usage_limited", changed, tool_kinds, result.exit_code,
                                result.stdout, result.stderr,
                                reason="구독 사용량 한도에 도달했습니다")
        if result.exit_code not in (0, None):
            return AgentReceipt("failed", changed, tool_kinds, result.exit_code,
                                result.stdout, result.stderr,
                                reason=f"에이전트가 exit {result.exit_code}로 끝났습니다")
        return AgentReceipt("succeeded", changed, tool_kinds, result.exit_code,
                            result.stdout, result.stderr)


def _repair_relpath(job: AgentJob) -> Optional[str]:
    """수정 지시를 쓸 자리 — 편집 허용 목록의 파일 중 산출물이 아닌 것 하나."""
    artifacts = set(job.expected_artifacts)
    for path in job.editable_paths:
        if path not in artifacts and path.lower().endswith(".md"):
            return path
    return None


def _snapshot(root: Path) -> dict:
    from .security import snapshot_workspace
    return snapshot_workspace(root)


def _changed_paths(before: dict, after: dict) -> tuple[str, ...]:
    """변경·추가·삭제된 상대 경로(정렬) — receipt의 changed_files."""
    names = set(before) | set(after)
    return tuple(sorted(n for n in names if before.get(n) != after.get(n)))


def build_cli_agent(workspace_provider: Callable[[], object], *,
                    environ: Mapping[str, str] | None = None,
                    runner: Optional[Runner] = None) -> Optional[OfficialCliAgent]:
    """환경 설정이 있을 때만 어댑터를 만든다. 없으면 None(기능 자체가 꺼짐)."""
    spec = load_cli_spec(environ)
    if spec is None:
        return None
    return OfficialCliAgent(spec, workspace_provider, runner=runner)


def with_runner(agent, runner: Runner):
    """격리 경계가 실행 직전에 러너를 붙인다.

    러너는 경계가 소유한다 — 어댑터 혼자서는 어떤 프로세스도 띄우지 못한다.
    (`runner is None`이면 `_run`이 즉시 launched=False로 되돌아온다.)"""
    if hasattr(agent, "runner"):
        agent.runner = runner
    return agent
