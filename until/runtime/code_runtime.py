"""코드 과제 런타임 — 에이전트가 코드를 쓰고, Until이 **구조와 문법을** 검증한다.

정직하게 먼저 밝힌다: **이 런타임은 코드를 실행하지 않는다.** 커널에
`WorkspacePlan.steps`(실행 단계) 자리는 있지만 오케스트레이터가 아직 그걸
돌리지 않는다. 그래서 여기서 "검증 통과"는 *동작이 맞다*가 아니라
**"파일이 있고, 문법이 깨지지 않았고, 지어낸 실행 결과가 없다"** 까지다.
그 이상을 주장하면 학생이 돌아가지도 않는 코드를 믿고 낸다.

검증(전부 결정적, LLM 0):
  - 제출해야 할 파일이 실제로 있는가(`spec["expected_files"]`)
  - 파이썬 소스가 **파싱되는가**(`ast.parse` — 실행이 아니라 문법만)
  - 제공된 스켈레톤·테스트 파일을 **지우거나 비우지 않았는가**
  - 실행하지 않고 만든 **결과 수치**가 없는가(`measured_check` 재사용)
  - 사람 몫 판단을 대신 확정하지 않았는가(`[[DECISION]]` 보존)

`hdl_lab`·`rmd_notebook`은 **일부러 맡지 않는다**. 파형·합성 결과·통계 출력은
도구를 실제로 돌려야 나오는 값이고, 실행 엔진 없이 통과를 주면 CLAUDE.md가
금지한 '수치 날조'를 제품이 승인하는 꼴이 된다.
"""
from __future__ import annotations

import ast
import mimetypes
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from .models import (
    AgentFeedback,
    AgentJob,
    RunStep,
    RuntimeRequest,
    RuntimeWorkspace,
    SubmissionBundle,
    SubmissionFile,
    SupportDecision,
    ValidationFinding,
    ValidationResult,
    WorkspacePlan,
)
from .security import RuntimeSecurityError, confined_path
from .workspace import sha256_file

SUPPORTED_STRATEGIES = frozenset({"code_project", "zip_project"})

WORK_DIR = "work"
PROMPT_RELPATH = "work/PROMPT.md"
SPEC_RELPATH = "work/SPEC.md"
NOTES_RELPATH = "work/NOTES.md"       # 설계 근거·결정 표식이 사는 곳
REPAIR_RELPATH = "work/REPAIR.md"
#: 기본 산출물 — 과제가 파일명을 지정하지 않았을 때.
DEFAULT_ENTRY = "work/solution.py"

_DECISION_RE = re.compile(r"\[\[DECISION:\s*(.*?)\]\]", re.DOTALL)


@dataclass
class CodeRuntime:
    """코드 과제용 런타임 플러그인."""

    name: str = "code"
    workspace: Optional[RuntimeWorkspace] = None
    #: 이 런타임이 쓰겠다고 선언하는 실행 명령. 커널이 자기 천장
    #: (`security.KERNEL_ALLOWED_COMMANDS`)으로 한 번 더 조이므로 여기에 셸을
    #: 적어도 열리지 않는다.
    allowed_commands: tuple = ("python", "python3", "pytest")
    _request: Optional[RuntimeRequest] = field(default=None, repr=False)
    _run: object = field(default=None, repr=False)

    # ── 선택 ────────────────────────────────────────────────────────
    def supports(self, request: RuntimeRequest) -> SupportDecision:
        strategy = str(getattr(request.route, "strategy", "") or "")
        if strategy not in SUPPORTED_STRATEGIES:
            return SupportDecision(
                "unsupported",
                f"code runtime handles {sorted(SUPPORTED_STRATEGIES)}, "
                f"not {strategy or 'unknown'}")
        return SupportDecision("supported", f"code runtime handles {strategy}", 50)

    # ── 준비 ────────────────────────────────────────────────────────
    def prepare(self, request: RuntimeRequest) -> WorkspacePlan:
        self._request = request
        self._run = None
        steps = _test_steps(request)
        return WorkspacePlan(
            directories=("inputs", WORK_DIR, "artifacts", "logs"),
            files=(SPEC_RELPATH, PROMPT_RELPATH, NOTES_RELPATH) + self._targets(request),
            steps=steps,
            runnable=bool(steps),
            reason=("code runtime runs the declared tests" if steps
                    else "code runtime prepares files only (no tests declared)"),
        )

    def observe_run(self, run_result) -> None:
        """커널이 검증 명령을 돌린 결과를 받아 둔다(검증에서 읽는다)."""
        self._run = run_result

    def build_job(self, workspace: RuntimeWorkspace) -> AgentJob:
        self.workspace = workspace
        request = self._request
        if request is None:
            raise RuntimeSecurityError("code runtime was not prepared")
        targets = self._targets(request)
        self._write(workspace, SPEC_RELPATH, self._spec_text(request, targets))
        self._write(workspace, PROMPT_RELPATH, self._prompt_text(targets))
        for relpath in targets:
            path = confined_path(workspace.root, relpath)
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
        notes = confined_path(workspace.root, NOTES_RELPATH)
        if not notes.exists():
            self._write(workspace, NOTES_RELPATH,
                        "<!-- 설계 근거와 사람이 정할 자리를 여기에 -->\n")
        return AgentJob(
            assignment_id=request.assignment_id,
            prompt_path=PROMPT_RELPATH,
            readable_paths=("inputs", SPEC_RELPATH, PROMPT_RELPATH),
            editable_paths=targets + (NOTES_RELPATH, REPAIR_RELPATH),
            allowed_tools=("editor",),
            intended_uses=("write_code",),
            forbidden_actions=("network", "submit", "delete_inputs"),
            policy_requirements=_policy_requirements(request),
            expected_artifacts=targets,
            environment_allowlist=(),
            timeout_seconds=int(request.spec.get("timeout_seconds") or 600),
            max_repair_attempts=1,
        )

    # ── 검증 ────────────────────────────────────────────────────────
    def validate(self, workspace: RuntimeWorkspace, receipt) -> ValidationResult:
        if receipt.status != "succeeded":
            return ValidationResult((ValidationFinding(
                "block", f"agent_{receipt.status}",
                receipt.reason or f"에이전트가 {receipt.status} 상태로 끝났습니다"),))
        request = self._request
        findings: list[ValidationFinding] = []
        targets = self._targets(request)

        bodies: dict[str, str] = {}
        for relpath in targets:
            try:
                path = confined_path(workspace.root, relpath, must_exist=True)
                bodies[relpath] = path.read_text(encoding="utf-8", errors="replace")
            except (RuntimeSecurityError, OSError):
                findings.append(ValidationFinding(
                    "block", "artifact_missing",
                    f"제출할 파일이 없습니다: {relpath}", relpath))
                continue
            if not bodies[relpath].strip():
                findings.append(ValidationFinding(
                    "block", "artifact_empty",
                    f"파일이 비어 있습니다: {relpath}", relpath))

        findings.extend(_check_syntax(bodies))
        findings.extend(_check_starter_preserved(workspace, request))
        notes = _read(workspace, NOTES_RELPATH)
        findings.extend(_check_measurements(bodies, notes, request))
        findings.extend(_check_decisions(notes, bodies))
        findings.extend(_check_tests(self._run))

        if not findings:
            findings.append(ValidationFinding(
                "pass", "code_ok", _pass_message(self._run),
                targets[0] if targets else WORK_DIR))
        return ValidationResult(tuple(findings))

    def repair_feedback(self, validation: ValidationResult) -> AgentFeedback:
        blocking = [f for f in validation.findings if f.level == "block"]
        return AgentFeedback(tuple(f.code for f in blocking),
                             tuple(f.message for f in blocking))

    # ── 포장 ────────────────────────────────────────────────────────
    def package(self, workspace: RuntimeWorkspace,
                validation: ValidationResult) -> SubmissionBundle:
        request = self._request
        assignment_id = request.assignment_id if request else workspace.plan_id
        targets = self._targets(request)
        if validation.blocked:
            return SubmissionBundle(assignment_id, (), targets)
        files, missing = [], []
        for relpath in targets:
            try:
                path = confined_path(workspace.root, relpath, must_exist=True)
            except (RuntimeSecurityError, OSError):
                missing.append(relpath)
                continue
            mime = mimetypes.guess_type(path.name)[0] or "text/plain"
            files.append(SubmissionFile(relpath, mime, sha256_file(path),
                                        path.stat().st_size))
        return SubmissionBundle(assignment_id, tuple(files), tuple(missing))

    # ── 내부 ────────────────────────────────────────────────────────
    @staticmethod
    def _targets(request: Optional[RuntimeRequest]) -> tuple[str, ...]:
        """제출할 파일들. 과제가 지정하지 않으면 기본 하나."""
        if request is None:
            return (DEFAULT_ENTRY,)
        raw = request.spec.get("expected_files") or ()
        out = []
        for item in raw:
            text = str(item).strip().replace("\\", "/").lstrip("/")
            if not text or ".." in text.split("/"):
                continue
            out.append(text if text.startswith(f"{WORK_DIR}/") else f"{WORK_DIR}/{text}")
        return tuple(dict.fromkeys(out)) or (DEFAULT_ENTRY,)

    @staticmethod
    def _write(workspace: RuntimeWorkspace, relpath: str, text: str) -> None:
        path = confined_path(workspace.root, relpath)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    @staticmethod
    def _spec_text(request: RuntimeRequest, targets: tuple[str, ...]) -> str:
        lines = [f"# {request.spec.get('title') or request.assignment_id}", ""]
        for key in ("course", "goal", "deliverable", "language", "submission_format"):
            value = str(request.spec.get(key) or "").strip()
            if value:
                lines.append(f"- **{key}**: {value}")
        lines += ["", "## 제출할 파일"] + [f"- `{t}`" for t in targets]
        required = request.spec.get("required") or ()
        if required:
            lines += ["", "## 요구 항목"] + [f"- {item}" for item in required]
        if request.decisions:
            lines += ["", "## 학생이 이미 정한 것(뒤집지 말 것)"]
            lines += [f"- {k}: {v}" for k, v in sorted(request.decisions.items())]
        return "\n".join(lines) + "\n"

    @staticmethod
    def _prompt_text(targets: tuple[str, ...]) -> str:
        listed = "\n".join(f"   - `{t}`" for t in targets)
        return f"""# 작업 지시

`{SPEC_RELPATH}`의 명세와 `inputs/`의 자료를 읽고 아래 파일을 완성하세요.

{listed}

## 반드시 지킬 것

1. 위 파일과 `{NOTES_RELPATH}` 밖의 파일을 만들거나 고치지 마세요.
   `inputs/`는 읽기 전용입니다. **제공된 스켈레톤·테스트 파일을 지우거나 비우지
   마세요** — 지우면 검증에서 되돌아옵니다.
2. 네트워크에 접속하지 말고, 주어진 자료 안에서만 작성하세요.
3. **실행하지 않은 결과를 적지 마세요.** 실행 시간, 정확도, 벤치마크 수치,
   출력 예시처럼 돌려 봐야 아는 값은 지어내면 안 됩니다. 필요하면 그 자리를
   빈칸 결정으로 남기세요:

       [[DECISION: 이 값은 실행해서 채워야 함 — 무엇을 어떻게 측정할지]]

4. 설계 근거와 **사람이 정할 자리**는 `{NOTES_RELPATH}`에 적으세요. 구현 방식
   선택 중 취향·트레이드오프가 걸린 것은 대신 확정하지 말고 표식으로 남깁니다.
5. Until은 이 코드를 **실행하지 않습니다.** 문법과 파일 존재만 확인하므로,
   동작 보증은 여러분이 직접 돌려 봐야 합니다. 그 점을 감안해 방어적으로 쓰세요.
"""


def _read(workspace: RuntimeWorkspace, relpath: str) -> str:
    try:
        return confined_path(workspace.root, relpath,
                             must_exist=True).read_text(encoding="utf-8",
                                                        errors="replace")
    except (RuntimeSecurityError, OSError):
        return ""


def _policy_requirements(request: RuntimeRequest) -> tuple[str, ...]:
    policy = request.policy
    out = []
    for name in ("ai_use", "citation_style", "collaboration"):
        value = getattr(policy, name, "")
        if value:
            out.append(f"{name}={value}")
    return tuple(out)


# ── 결정적 검사기 ───────────────────────────────────────────────────
def _check_syntax(bodies: dict) -> list:
    """파이썬 소스는 **파싱**해 본다. 실행이 아니라 문법 확인이다."""
    findings = []
    for relpath, body in bodies.items():
        if not relpath.endswith(".py") or not body.strip():
            continue
        try:
            ast.parse(body)
        except SyntaxError as exc:
            findings.append(ValidationFinding(
                "block", "syntax_error",
                f"파이썬 문법 오류 {relpath}:{exc.lineno} — {exc.msg}", relpath))
    return findings


def _check_starter_preserved(workspace: RuntimeWorkspace,
                             request: Optional[RuntimeRequest]) -> list:
    """제공된 스켈레톤·테스트를 지우거나 비우지 않았는가.

    코드 과제에서 가장 흔한 사고는 "테스트가 통과하지 않으니 테스트를 지운다"다.
    `inputs/`는 읽기 전용이라 지울 수 없지만, 작업 파일로 **복사해 온 뒤** 비우는
    경로는 열려 있다. 명세가 보존을 요구한 파일만 본다."""
    if request is None:
        return []
    keep = [str(x).strip() for x in (request.spec.get("preserve_files") or ())
            if str(x).strip()]
    findings = []
    for relpath in keep:
        safe = relpath.replace("\\", "/").lstrip("/")
        target = safe if safe.startswith(f"{WORK_DIR}/") else f"{WORK_DIR}/{safe}"
        try:
            body = confined_path(workspace.root, target,
                                 must_exist=True).read_text(encoding="utf-8",
                                                            errors="replace")
        except (RuntimeSecurityError, OSError):
            findings.append(ValidationFinding(
                "block", "starter_removed",
                f"보존해야 할 파일이 없어졌습니다: {target}", target))
            continue
        if not body.strip():
            findings.append(ValidationFinding(
                "block", "starter_emptied",
                f"보존해야 할 파일이 비워졌습니다: {target}", target))
    return findings


def _check_measurements(bodies: dict, notes: str,
                        request: Optional[RuntimeRequest]) -> list:
    """실행해야 알 수 있는 수치를 지어냈는지 — 근거 자료와 대조한다.

    "정확도 97.3%", "실행 시간 0.42초"처럼 돌려 보지 않으면 알 수 없는 값을
    주석·NOTES에 적어 두는 실사용 패턴을 잡는다. 판정 방식은 보고서 경로와 같다:
    수치 안의 숫자가 근거(자료·학생 답변)에 **그대로** 있으면 통과, 없으면 차단.
    """
    if request is None:
        return []
    from ..understanding.measured_check import find_ungrounded_measurements
    from . import grounding

    evidence = [str(x) for x in (request.spec.get("evidence_texts") or ())]
    evidence += [str(v) for v in (request.decisions or {}).values()]
    text = "\n".join([notes] + list(bodies.values()))

    # ① 실험·HDL 단위는 기존 판정기 그대로(전략 게이트만 우회) — 지어낸 실행
    #    결과는 코드 과제에서도 똑같이 학문적 부정이다.
    hits = list(find_ungrounded_measurements(text, evidence, strategy="hdl_lab"))
    # ② 코드 과제 고유 단위는 여기서 같은 규칙으로 본다.
    hits += grounding.ungrounded_numbers(text, evidence, grounding.CODE_PATTERNS)
    if not hits:
        return []
    return [ValidationFinding(
        "block", "ungrounded_measurement",
        "실행하지 않고 적은 결과 수치가 있습니다 — 값을 지우고 "
        f"[[DECISION]]으로 남기세요: {hits[0][:80]}", NOTES_RELPATH)]


def _check_decisions(notes: str, bodies: dict) -> list:
    """사람 몫 판단을 남겼는가. 코드 경로는 NOTES와 소스 주석 어디든 인정한다."""
    text = "\n".join([notes] + list(bodies.values()))
    kept = [n.strip() for n in _DECISION_RE.findall(text) if len(n.strip()) >= 5]
    if kept:
        return []
    return [ValidationFinding(
        "block", "boundary_crossed",
        "사람이 정할 자리를 남기지 않았습니다 — 구현 방식의 트레이드오프·"
        "실행해서 채울 값은 [[DECISION: ...]]으로 남겨야 합니다", NOTES_RELPATH)]


def workspace_provider_for(runtime: "CodeRuntime"):
    def _provider():
        return runtime.workspace
    return _provider


# ── 검증 명령(테스트) ───────────────────────────────────────────────
_TEST_NAME_RE = re.compile(r"(?:^|/)(?:test_[^/]+|[^/]+_test)\.py$")


def _test_steps(request: RuntimeRequest) -> tuple:
    """이 과제에서 돌릴 검증 명령. 돌릴 게 없으면 빈 튜플.

    **명령은 에이전트가 돌기 전에 여기서 정해진다.** 에이전트가 쓴 파일이
    명령줄이 되는 경로는 없다 — 그게 실행을 열어 주는 전제다.

    기본은 pytest다. 테스트 파일이 있다고 판단될 때만 켠다(`spec["run_tests"]`로
    강제하거나 끌 수 있다). 없는데 돌리면 "테스트 0개 통과"라는 무의미한 초록불을
    주게 된다.
    """
    spec = request.spec or {}
    # 과제가 자기 검증 명령을 정해 둘 수 있다. 기본(pytest)이 그 환경에 없거나
    # 프로젝트가 unittest·다른 러너를 쓰는 경우가 흔하다. 여기 적힌 명령도
    # 커널 천장과 `validate_step`을 그대로 통과해야 한다.
    override = spec.get("test_command")
    if override:
        argv = tuple(str(x) for x in override if str(x).strip())
        if argv:
            return (RunStep(
                argv=argv, inputs=(WORK_DIR,),
                timeout_seconds=int(spec.get("test_timeout_seconds") or 120),
                network=False),)
    declared = spec.get("run_tests")
    names = [str(x) for x in (spec.get("preserve_files") or ())]
    names += [str(x) for x in (spec.get("expected_files") or ())]
    names += [Path(str(p)).name for p in (request.inputs or ())]
    has_tests = any(_TEST_NAME_RE.search(n.replace("\\", "/")) for n in names)
    if declared is False or (declared is None and not has_tests):
        return ()
    timeout = int(spec.get("test_timeout_seconds") or 120)
    return (RunStep(
        argv=("python", "-m", "pytest", "-q", WORK_DIR),
        inputs=(WORK_DIR,),
        timeout_seconds=timeout,
        network=False,
    ),)


def _check_tests(run) -> list:
    """테스트 결과를 판정한다. **못 돌린 것과 실패한 것을 구분한다.**

    - 실패      → 차단(고칠 수 있는 정보이고, repair 1회가 붙는다)
    - 못 돌림   → 경고. pytest가 샌드박스에 없거나 격리가 없어서 못 돌린 것을
                  '실패'로 적으면 학생은 멀쩡한 코드를 고치려 든다.
    """
    if run is None:
        return []
    status = getattr(run, "status", "")
    if status == "succeeded":
        return []
    tail = (getattr(run, "stdout_summary", "") or
            getattr(run, "stderr_summary", "") or "").strip()[-400:]
    if status == "failed":
        reason = getattr(run, "skipped_reason", "") or "테스트가 실패했습니다"
        return [ValidationFinding(
            "block", "tests_failed", f"{reason}\n{tail}".strip(), WORK_DIR)]
    return [ValidationFinding(
        "warn", "tests_not_run",
        "테스트를 돌리지 못했습니다 — 코드가 틀렸다는 뜻이 아닙니다: "
        + (getattr(run, "skipped_reason", "") or status), WORK_DIR)]


def _pass_message(run) -> str:
    if getattr(run, "status", "") == "succeeded":
        return "파일·문법·스켈레톤 보존을 확인했고 선언된 테스트가 통과했습니다"
    return ("파일·문법·스켈레톤 보존을 확인했습니다 "
            "(테스트는 돌리지 않았습니다 — 동작은 직접 확인하세요)")
