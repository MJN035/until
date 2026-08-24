"""발표 자료 런타임 — 슬라이드 구조를 결정적으로 검증한다.

산출물이 산문이 아니라 **슬라이드 묶음**이라 보고서 검증기(분량·인용)를 그대로
쓸 수 없다. 여기서 보는 것은 슬라이드가 실제로 슬라이드 모양인가다:
  - 슬라이드가 최소 개수 이상 있는가(`## 슬라이드 N: 제목` 표기 — 웹 PPTX 변환과 동일)
  - 각 슬라이드에 제목과 내용이 있는가(빈 껍데기 금지)
  - 한 장에 너무 많이 밀어 넣지 않았는가(발표는 읽는 글이 아니다)
  - 사람 몫 판단(관점·주장·발표 순서)을 대신 확정하지 않았는가

파싱은 웹 경로가 PPTX를 만들 때 쓰는 것과 **같은 함수**
(`presentation_export.parse_slide_markdown`)를 쓴다 — 검증한 구조와 실제로
변환되는 구조가 다르면 검증이 의미가 없다.

PPTX 변환은 여기서 하지 않는다. 변환기는 `Result` 객체를 받는 웹 경로용이고,
제출본은 마크다운으로 낸 뒤 필요하면 웹에서 내려받는 편이 경로가 하나뿐이라
어긋날 여지가 없다.
"""
from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass, field
from typing import Optional

from .models import (
    AgentFeedback,
    AgentJob,
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

SUPPORTED_STRATEGIES = frozenset({"presentation_conversion"})

SLIDES_RELPATH = "work/slides.md"
PROMPT_RELPATH = "work/PROMPT.md"
SPEC_RELPATH = "work/SPEC.md"
REPAIR_RELPATH = "work/REPAIR.md"

#: 발표로 인정할 최소 슬라이드 수. 이보다 적으면 '발표 자료'가 아니라 메모다.
MIN_SLIDES = 3
#: 한 장에 넣을 수 있는 불릿 상한 — 넘으면 읽는 글이지 발표가 아니다.
MAX_BULLETS = 7

_DECISION_RE = re.compile(r"\[\[DECISION:\s*(.*?)\]\]", re.DOTALL)


@dataclass
class PresentationRuntime:
    name: str = "presentation"
    workspace: Optional[RuntimeWorkspace] = None
    _request: Optional[RuntimeRequest] = field(default=None, repr=False)

    def supports(self, request: RuntimeRequest) -> SupportDecision:
        strategy = str(getattr(request.route, "strategy", "") or "")
        if strategy not in SUPPORTED_STRATEGIES:
            return SupportDecision(
                "unsupported",
                f"presentation runtime handles {sorted(SUPPORTED_STRATEGIES)}, "
                f"not {strategy or 'unknown'}")
        return SupportDecision("supported", f"presentation runtime handles {strategy}", 50)

    def prepare(self, request: RuntimeRequest) -> WorkspacePlan:
        self._request = request
        return WorkspacePlan(
            directories=("inputs", "work", "artifacts", "logs"),
            files=(SPEC_RELPATH, PROMPT_RELPATH, SLIDES_RELPATH),
            runnable=False,
            reason="presentation runtime prepares files only")

    def build_job(self, workspace: RuntimeWorkspace) -> AgentJob:
        self.workspace = workspace
        request = self._request
        if request is None:
            raise RuntimeSecurityError("presentation runtime was not prepared")
        _write(workspace, SPEC_RELPATH, _spec_text(request))
        _write(workspace, PROMPT_RELPATH, _prompt_text(request))
        path = confined_path(workspace.root, SLIDES_RELPATH)
        if not path.exists():
            _write(workspace, SLIDES_RELPATH,
                   "<!-- 이 파일만 고치세요. `## 슬라이드 1: 제목` 한 줄이 한 장입니다. -->\n")
        return AgentJob(
            assignment_id=request.assignment_id,
            prompt_path=PROMPT_RELPATH,
            readable_paths=("inputs", SPEC_RELPATH, PROMPT_RELPATH),
            editable_paths=(SLIDES_RELPATH, REPAIR_RELPATH),
            allowed_tools=("editor",),
            intended_uses=("draft_presentation",),
            forbidden_actions=("network", "submit", "delete_inputs"),
            policy_requirements=_policy_requirements(request),
            expected_artifacts=(SLIDES_RELPATH,),
            environment_allowlist=(),
            timeout_seconds=int(request.spec.get("timeout_seconds") or 300),
            max_repair_attempts=1)

    def validate(self, workspace: RuntimeWorkspace, receipt) -> ValidationResult:
        if receipt.status != "succeeded":
            return ValidationResult((ValidationFinding(
                "block", f"agent_{receipt.status}",
                receipt.reason or f"에이전트가 {receipt.status} 상태로 끝났습니다"),))
        try:
            body = confined_path(workspace.root, SLIDES_RELPATH,
                                 must_exist=True).read_text(encoding="utf-8")
        except (RuntimeSecurityError, OSError) as exc:
            return ValidationResult((ValidationFinding(
                "block", "slides_missing", f"슬라이드 파일을 읽지 못했습니다: {exc}",
                SLIDES_RELPATH),))

        from ..presentation_export import parse_slide_markdown

        spec = (self._request.spec if self._request else {}) or {}
        findings = _check_slides(body, parse_slide_markdown(body), spec)
        if not findings:
            findings = [ValidationFinding("pass", "presentation_ok",
                                          "슬라이드 구조를 확인했습니다", SLIDES_RELPATH)]
        return ValidationResult(tuple(findings))

    def repair_feedback(self, validation: ValidationResult) -> AgentFeedback:
        blocking = [f for f in validation.findings if f.level == "block"]
        return AgentFeedback(tuple(f.code for f in blocking),
                             tuple(f.message for f in blocking))

    def package(self, workspace: RuntimeWorkspace,
                validation: ValidationResult) -> SubmissionBundle:
        assignment_id = (self._request.assignment_id if self._request
                         else workspace.plan_id)
        if validation.blocked:
            return SubmissionBundle(assignment_id, (), (SLIDES_RELPATH,))
        try:
            path = confined_path(workspace.root, SLIDES_RELPATH, must_exist=True)
        except (RuntimeSecurityError, OSError):
            return SubmissionBundle(assignment_id, (), (SLIDES_RELPATH,))
        mime = mimetypes.guess_type(path.name)[0] or "text/markdown"
        return SubmissionBundle(assignment_id, (SubmissionFile(
            SLIDES_RELPATH, mime, sha256_file(path), path.stat().st_size),))


def _check_slides(body: str, slides, spec) -> list:
    findings = []
    minimum = int(spec.get("min_slides") or MIN_SLIDES)
    real = [(title, bullets) for title, bullets in slides
            if title and title != "발표 자료"]
    if len(real) < minimum:
        findings.append(ValidationFinding(
            "block", "too_few_slides",
            f"슬라이드가 {len(real)}장입니다 — 최소 {minimum}장이 필요합니다",
            SLIDES_RELPATH))
    empty = [title for title, bullets in real if not bullets]
    if empty:
        findings.append(ValidationFinding(
            "block", "empty_slide",
            "내용이 없는 슬라이드가 있습니다: " + ", ".join(empty[:3]),
            SLIDES_RELPATH))
    crowded = [title for title, bullets in real if len(bullets) > MAX_BULLETS]
    if crowded:
        findings.append(ValidationFinding(
            "warn", "crowded_slide",
            f"한 장에 {MAX_BULLETS}줄을 넘는 슬라이드가 있습니다(발표는 읽는 글이 "
            "아닙니다): " + ", ".join(crowded[:3]), SLIDES_RELPATH))
    if not [n for n in _DECISION_RE.findall(body) if len(n.strip()) >= 5]:
        findings.append(ValidationFinding(
            "block", "boundary_crossed",
            "사람이 정할 자리를 남기지 않았습니다 — 발표의 관점·강조점·본인 경험은 "
            "[[DECISION: ...]]으로 남겨야 합니다", SLIDES_RELPATH))
    return findings


def _spec_text(request: RuntimeRequest) -> str:
    lines = [f"# {request.spec.get('title') or request.assignment_id}", ""]
    for key in ("course", "goal", "duration", "audience", "submission_format"):
        value = str(request.spec.get(key) or "").strip()
        if value:
            lines.append(f"- **{key}**: {value}")
    if request.decisions:
        lines += ["", "## 학생이 이미 정한 것(뒤집지 말 것)"]
        lines += [f"- {k}: {v}" for k, v in sorted(request.decisions.items())]
    return "\n".join(lines) + "\n"


def _prompt_text(request: RuntimeRequest) -> str:
    minimum = int((request.spec or {}).get("min_slides") or MIN_SLIDES)
    return f"""# 작업 지시

`{SPEC_RELPATH}`의 명세와 `inputs/`의 자료를 읽고 `{SLIDES_RELPATH}` **한 파일만**
고쳐 발표 자료를 만드세요.

## 형식 — 이대로 지켜야 변환됩니다

슬라이드 한 장은 **`## 슬라이드 N: 제목`** 한 줄로 시작합니다. 그 아래 불릿으로
내용을 씁니다. 이 표기는 Until이 PPTX로 바꿀 때 쓰는 것과 같은 형식이라,
다르게 쓰면 슬라이드로 인식되지 않습니다.

```
## 슬라이드 1: 배경
- 첫 줄
- 둘째 줄

## 슬라이드 2: 분석
- ...
```

최소 {minimum}장, 한 장에 {MAX_BULLETS}줄 이하로 유지하세요.

## 반드시 지킬 것

1. `{SLIDES_RELPATH}` 밖의 파일을 만들거나 고치지 마세요. `inputs/`는 읽기 전용입니다.
2. 네트워크에 접속하지 말고, 주어진 자료 안에서만 쓰세요.
3. **발표의 관점·강조점·본인 경험은 대신 정하지 마세요.** 그 자리는 표식으로 남깁니다:

       [[DECISION: 어떤 주장을 앞세울지 — 본인 판단]]

4. 자료에 없는 수치·사례를 지어내지 마세요.
"""


def _write(workspace: RuntimeWorkspace, relpath: str, text: str) -> None:
    path = confined_path(workspace.root, relpath)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _policy_requirements(request: RuntimeRequest) -> tuple[str, ...]:
    policy = request.policy
    out = []
    for name in ("ai_use", "citation_style", "collaboration"):
        value = getattr(policy, name, "")
        if value:
            out.append(f"{name}={value}")
    return tuple(out)


def workspace_provider_for(runtime: "PresentationRuntime"):
    def _provider():
        return runtime.workspace
    return _provider
