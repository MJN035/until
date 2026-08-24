"""활동 기록 양식 런타임 — **사실을 지어내지 않는 것**이 핵심 검증이다.

`activity_form`은 "실제 활동 사실을 양식에 기록"하는 과제다. 누가 무엇을 했고
결과가 어땠는지는 **일어난 일**이지 글솜씨가 아니다. 에이전트가 그럴듯하게
채우면 그건 초안이 아니라 허위 기록이고, 제출되면 학문적 부정이 된다.

그래서 이 런타임의 검증은 다른 것들과 방향이 반대다. 보고서 런타임은 "덜 썼다"를
막지만, 여기서는 **"모르는 걸 썼다"를 막는다.**
  - 활동 사실 칸은 자료나 학생 답변에 근거가 있어야 한다 — 없으면 `[[DECISION]]`
  - 날짜·인원·시간 같은 수치는 근거와 대조한다(`measured_check` 재사용)
  - 양식의 필수 칸이 남아 있는지(구조 보존)

양식 자체를 채운 원본 파일(hwpx/docx)을 만들지는 않는다. 그건 웹 경로의
`report.write_filled_form`이 하고, 그쪽은 원본 서식을 보존한다. 여기서는 어떤
칸에 무엇이 들어가는지를 마크다운으로 확정해 주고, 근거 없는 칸을 막는다.
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

SUPPORTED_STRATEGIES = frozenset({"activity_form"})

FILLED_RELPATH = "work/양식.md"
PROMPT_RELPATH = "work/PROMPT.md"
SPEC_RELPATH = "work/SPEC.md"
REPAIR_RELPATH = "work/REPAIR.md"

_DECISION_RE = re.compile(r"\[\[DECISION:\s*(.*?)\]\]", re.DOTALL)
#: 활동 기록에서 '사람만 아는 사실'을 요구하는 칸 — 지어내면 허위 기록이 된다.
_FACT_LABELS = ("참여자", "참가자", "인원", "일시", "날짜", "장소", "활동 내용",
                "결과", "소감", "역할", "담당")


@dataclass
class FormRuntime:
    name: str = "form"
    workspace: Optional[RuntimeWorkspace] = None
    _request: Optional[RuntimeRequest] = field(default=None, repr=False)

    def supports(self, request: RuntimeRequest) -> SupportDecision:
        strategy = str(getattr(request.route, "strategy", "") or "")
        if strategy not in SUPPORTED_STRATEGIES:
            return SupportDecision(
                "unsupported",
                f"form runtime handles {sorted(SUPPORTED_STRATEGIES)}, "
                f"not {strategy or 'unknown'}")
        return SupportDecision("supported", f"form runtime handles {strategy}", 50)

    def prepare(self, request: RuntimeRequest) -> WorkspacePlan:
        self._request = request
        return WorkspacePlan(
            directories=("inputs", "work", "artifacts", "logs"),
            files=(SPEC_RELPATH, PROMPT_RELPATH, FILLED_RELPATH),
            runnable=False,
            reason="form runtime prepares files only")

    def build_job(self, workspace: RuntimeWorkspace) -> AgentJob:
        self.workspace = workspace
        request = self._request
        if request is None:
            raise RuntimeSecurityError("form runtime was not prepared")
        _write(workspace, SPEC_RELPATH, _spec_text(request))
        _write(workspace, PROMPT_RELPATH, _prompt_text())
        # **비어 있을 때도** 깐다. 작업공간 생성기가 `plan.files`를 `touch`로 미리
        # 만들어 두기 때문에 `exists()`만 보면 스캐폴드가 영영 안 쓰인다(실측).
        path = confined_path(workspace.root, FILLED_RELPATH)
        if not path.exists() or not path.read_text(encoding="utf-8").strip():
            _write(workspace, FILLED_RELPATH, _scaffold(request))
        return AgentJob(
            assignment_id=request.assignment_id,
            prompt_path=PROMPT_RELPATH,
            readable_paths=("inputs", SPEC_RELPATH, PROMPT_RELPATH),
            editable_paths=(FILLED_RELPATH, REPAIR_RELPATH),
            allowed_tools=("editor",),
            intended_uses=("fill_form",),
            forbidden_actions=("network", "submit", "delete_inputs",
                               "invent_activity_facts"),
            policy_requirements=_policy_requirements(request),
            expected_artifacts=(FILLED_RELPATH,),
            environment_allowlist=(),
            timeout_seconds=int(request.spec.get("timeout_seconds") or 300),
            max_repair_attempts=1)

    def validate(self, workspace: RuntimeWorkspace, receipt) -> ValidationResult:
        if receipt.status != "succeeded":
            return ValidationResult((ValidationFinding(
                "block", f"agent_{receipt.status}",
                receipt.reason or f"에이전트가 {receipt.status} 상태로 끝났습니다"),))
        try:
            body = confined_path(workspace.root, FILLED_RELPATH,
                                 must_exist=True).read_text(encoding="utf-8")
        except (RuntimeSecurityError, OSError) as exc:
            return ValidationResult((ValidationFinding(
                "block", "form_missing", f"양식 파일을 읽지 못했습니다: {exc}",
                FILLED_RELPATH),))
        request = self._request
        findings = _check_no_invented_facts(body, request)
        findings += _check_decisions(body)
        if not findings:
            findings = [ValidationFinding(
                "pass", "form_ok",
                "양식 칸과 근거를 확인했습니다", FILLED_RELPATH)]
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
            return SubmissionBundle(assignment_id, (), (FILLED_RELPATH,))
        try:
            path = confined_path(workspace.root, FILLED_RELPATH, must_exist=True)
        except (RuntimeSecurityError, OSError):
            return SubmissionBundle(assignment_id, (), (FILLED_RELPATH,))
        mime = mimetypes.guess_type(path.name)[0] or "text/markdown"
        return SubmissionBundle(assignment_id, (SubmissionFile(
            FILLED_RELPATH, mime, sha256_file(path), path.stat().st_size),))


def _evidence_of(request: Optional[RuntimeRequest]) -> list:
    if request is None:
        return []
    texts = [str(x) for x in (request.spec.get("evidence_texts") or ())]
    texts += [str(v) for v in (request.decisions or {}).values()]
    return texts


def _check_no_invented_facts(body: str, request: Optional[RuntimeRequest]) -> list:
    """근거 없는 수치(일시·인원·시간)를 잡는다 — 활동 기록의 핵심 위험."""
    from . import grounding

    evidence = _evidence_of(request)
    # 인원·일시·수량은 활동 기록 고유의 위험이라 전용 패턴으로 본다.
    # (`measured_check`는 실험·HDL 단위 전용이라 "3명"·"5월 12일"을 못 잡는다.)
    hits = grounding.ungrounded_numbers(body, evidence, grounding.ACTIVITY_PATTERNS)
    if not hits:
        return []
    return [ValidationFinding(
        "block", "invented_activity_fact",
        "자료에 근거가 없는 수치를 양식에 적었습니다 — 활동 기록은 지어내면 "
        f"허위 기록이 됩니다. 그 칸은 [[DECISION]]으로 남기세요: {hits[0][:80]}",
        FILLED_RELPATH)]


def _check_decisions(body: str) -> list:
    """활동 사실 칸을 하나도 안 남겼으면 의심한다.

    양식 과제에서 결정 표식이 0개라는 건 에이전트가 '누가 무엇을 했는지'까지
    다 정해 버렸다는 뜻이다 — 그건 초안이 아니라 창작이다."""
    if [n for n in _DECISION_RE.findall(body) if len(n.strip()) >= 5]:
        return []
    return [ValidationFinding(
        "block", "boundary_crossed",
        "사람이 정할 자리를 하나도 남기지 않았습니다 — 실제 활동 사실"
        f"({', '.join(_FACT_LABELS[:5])} 등)은 학생만 압니다. "
        "[[DECISION: ...]]으로 남겨야 합니다", FILLED_RELPATH)]


def _scaffold(request: RuntimeRequest) -> str:
    """감지된 양식 구조가 있으면 칸을 미리 깔아 준다(없으면 빈 안내만)."""
    fields = [str(x).strip() for x in (request.spec.get("form_fields") or ())
              if str(x).strip()]
    if not fields:
        return ("<!-- 이 파일만 고치세요. 양식의 칸을 그대로 옮기고 채웁니다. -->\n")
    lines = ["<!-- 이 파일만 고치세요. -->", ""]
    for name in fields:
        lines.append(f"## {name}")
        lines.append(f"[[DECISION: {name} — 실제 사실을 학생이 채워야 함]]")
        lines.append("")
    return "\n".join(lines) + "\n"


def _spec_text(request: RuntimeRequest) -> str:
    lines = [f"# {request.spec.get('title') or request.assignment_id}", ""]
    for key in ("course", "goal", "deliverable", "submission_format"):
        value = str(request.spec.get(key) or "").strip()
        if value:
            lines.append(f"- **{key}**: {value}")
    fields = request.spec.get("form_fields") or ()
    if fields:
        lines += ["", "## 양식 칸"] + [f"- {name}" for name in fields]
    if request.decisions:
        lines += ["", "## 학생이 이미 알려 준 사실(그대로 쓸 것)"]
        lines += [f"- {k}: {v}" for k, v in sorted(request.decisions.items())]
    return "\n".join(lines) + "\n"


def _prompt_text() -> str:
    return f"""# 작업 지시

`{SPEC_RELPATH}`의 양식과 `inputs/`의 자료를 읽고 `{FILLED_RELPATH}` **한 파일만**
고쳐 양식을 채우세요.

## 이 과제의 특수성 — 가장 중요합니다

이건 글쓰기가 아니라 **실제로 일어난 일의 기록**입니다. 누가 참여했는지, 언제
어디서 했는지, 결과가 어땠는지는 **학생만 아는 사실**입니다.

**모르는 사실을 그럴듯하게 채우지 마세요. 그건 초안이 아니라 허위 기록입니다.**
자료(`inputs/`)나 명세의 '학생이 이미 알려 준 사실'에 근거가 없는 칸은 반드시
그대로 남기세요:

    [[DECISION: 참여자 — 실제로 누가 했는지 학생이 채워야 함]]

날짜·인원·소요 시간 같은 수치는 근거와 대조합니다. 지어낸 값은 검증에서
되돌아옵니다.

## 그 밖에

1. `{FILLED_RELPATH}` 밖의 파일을 만들거나 고치지 마세요. `inputs/`는 읽기 전용입니다.
2. 네트워크에 접속하지 마세요.
3. 양식의 칸 구조(제목·순서)는 원본 그대로 유지하세요.
4. 자료로 확인되는 부분(활동 목적, 배경 설명, 형식 문구)은 끝까지 채워도 됩니다.
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


def workspace_provider_for(runtime: "FormRuntime"):
    def _provider():
        return runtime.workspace
    return _provider
