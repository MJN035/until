"""Phase 3 — Report Runtime MVP.

일반 보고서 한 종류에서 Local Agent 제품 가치를 검증하는 첫 `RuntimePlugin`.

경계선은 그대로다: 가치판단·개인 경험은 에이전트가 확정하지 않고
``[[DECISION: ...]]`` 표식으로 남긴다. 검증기가 그걸 **코드로** 강제한다 —
표식이 전부 사라졌으면(=대신 결정해 버렸으면) block이다.

검증은 전부 결정적(LLM 0)이며 이미 있는 판정기를 재사용한다:
  - 필수 섹션 · 분량            → `until.execution.coverage` / `until.report`의 기준과 같은 규칙
  - 인용 커버리지               → `[자료N]` 표식 존재
  - 수치 날조                   → `until.understanding.measured_check`
  - 결정 표식 보존              → `until.boundary.models.Draft`

실패는 **1회만** 자동 수정(repair)하고 그다음엔 멈춘다(계획서 Phase 3 5번).
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

# 이 런타임이 맡는 라우팅 전략 — **산출물이 산문인 것만**. 나머지는 다른 플러그인 몫.
#
# 왜 이 목록만인가: 여기 검증기(필수 섹션·분량·인용·결정 표식)는 전부 산문을
# 전제한다. 코드·슬라이드·양식에 이걸 들이대면 "검증 통과"가 거짓말이 된다 —
# 마크다운 초안 하나 써 놓고 통과시켜 버린다. 산출물 모양이 다르면 플러그인도
# 달라야 한다(`code_runtime`·`presentation_runtime`·`form_runtime`).
SUPPORTED_STRATEGIES = frozenset({
    "evidence_report", "general_report", "report", "lab_report_cycle",
    # 아래는 모두 '글을 쓴다'가 산출물인 계열 — 같은 검증기가 그대로 맞는다.
    "staged_writing",       # 단계형 글쓰기
    "reflective_series",    # 반복형 강의 소감
    "weekly_inquiry",       # 주차별 사전 질의(짧은 글 — 분량 요건이 없으면 검사도 안 걸린다)
    "team_project",         # 공동 산출물 — 팀 합의·본인 담당은 결정 표식으로 남는다
    "problem_set",          # 번호형 문제 풀이(서술형 풀이)
    "textbook_problem_set", # 교재 문제 풀이
})
# 문제 풀이 계열에 대한 주의: 이 검증기는 **요건 충족**을 보지 **정답 여부**를
# 보지 않는다. 'report_ok'는 "필수 항목·분량·인용·결정 표식이 갖춰졌다"는 뜻이지
# "풀이가 맞다"가 아니다. 웹 경로도 같은 수준으로 초안을 주므로 동작이 어긋나지
# 않지만, 화면 문구가 정답을 보증하는 것처럼 읽히지 않게 유지할 것.
DRAFT_RELPATH = "work/draft.md"
PROMPT_RELPATH = "work/PROMPT.md"
SPEC_RELPATH = "work/SPEC.md"
# 검증 실패 재시도 지시를 쓰는 자리. **편집 허용 목록에 넣는다** —
# 커널은 편집 허용 밖의 파일이 바뀌면 workspace_escape로 막기 때문에,
# 읽기 전용인 PROMPT.md에 덧붙이면 재시도 자체가 보안 위반이 된다.
REPAIR_RELPATH = "work/REPAIR.md"

_DECISION_RE = re.compile(r"\[\[DECISION:\s*(.*?)\]\]", re.DOTALL)
_CITATION_RE = re.compile(r"\[자료\s*\d+\]")


@dataclass
class ReportRuntime:
    """보고서 과제용 런타임 플러그인."""

    name: str = "report"
    workspace: Optional[RuntimeWorkspace] = None
    _request: Optional[RuntimeRequest] = field(default=None, repr=False)
    _repairs: int = field(default=0, repr=False)

    # ── 선택 ────────────────────────────────────────────────────────
    def supports(self, request: RuntimeRequest) -> SupportDecision:
        strategy = str(getattr(request.route, "strategy", "") or "")
        if strategy not in SUPPORTED_STRATEGIES:
            return SupportDecision(
                "unsupported", f"report runtime handles {sorted(SUPPORTED_STRATEGIES)}, "
                               f"not {strategy or 'unknown'}")
        if not str(request.spec.get("title") or request.spec.get("goal") or "").strip():
            return SupportDecision("unsupported", "과제 명세에 제목·목표가 없습니다")
        return SupportDecision("supported", f"report runtime handles {strategy}", 50)

    # ── 준비 ────────────────────────────────────────────────────────
    def prepare(self, request: RuntimeRequest) -> WorkspacePlan:
        self._request = request
        self._repairs = 0
        return WorkspacePlan(
            directories=("inputs", "work", "artifacts", "logs"),
            files=(SPEC_RELPATH, PROMPT_RELPATH, DRAFT_RELPATH),
            runnable=False,          # 실행은 에이전트 몫 — 런타임이 명령을 돌리지 않는다
            reason="report runtime prepares files only",
        )

    def build_job(self, workspace: RuntimeWorkspace) -> AgentJob:
        self.workspace = workspace
        request = self._request
        if request is None:
            raise RuntimeSecurityError("report runtime was not prepared")
        self._write_spec(workspace, request)
        self._write_prompt(workspace, request)
        self._ensure_draft(workspace)
        return AgentJob(
            assignment_id=request.assignment_id,
            prompt_path=PROMPT_RELPATH,
            # 읽기 전용 목록에 초안을 넣지 않는다 — 커널은 읽기 전용과 편집 가능이
            # 겹치는 job을 보안 위반으로 거부한다(편집 가능은 읽기를 포함한다).
            readable_paths=("inputs", SPEC_RELPATH, PROMPT_RELPATH),
            editable_paths=(DRAFT_RELPATH, REPAIR_RELPATH),
            #   ^ 제출물은 초안 하나뿐이고, REPAIR.md는 재시도 지시 전용이다.
            allowed_tools=("editor",),
            intended_uses=("draft_report",),
            forbidden_actions=("network", "submit", "delete_inputs"),
            policy_requirements=_policy_requirements(request),
            expected_artifacts=(DRAFT_RELPATH,),
            environment_allowlist=(),
            timeout_seconds=int(request.spec.get("timeout_seconds") or 300),
            max_repair_attempts=1,
        )

    # ── 검증 ────────────────────────────────────────────────────────
    def validate(self, workspace: RuntimeWorkspace, receipt) -> ValidationResult:
        findings: list[ValidationFinding] = []
        if receipt.status != "succeeded":
            return ValidationResult((ValidationFinding(
                "block", f"agent_{receipt.status}",
                receipt.reason or f"에이전트가 {receipt.status} 상태로 끝났습니다"),))
        try:
            draft_path = confined_path(workspace.root, DRAFT_RELPATH, must_exist=True)
            body = draft_path.read_text(encoding="utf-8")
        except (RuntimeSecurityError, OSError) as exc:
            return ValidationResult((ValidationFinding(
                "block", "draft_missing", f"초안 파일을 읽지 못했습니다: {exc}",
                DRAFT_RELPATH),))
        spec = (self._request.spec if self._request else {}) or {}
        findings.extend(_check_sections(body, spec))
        findings.extend(_check_length(body, spec))
        findings.extend(_check_citations(body, spec))
        findings.extend(_check_decisions(body))
        strategy = str(getattr(getattr(self._request, "route", None), "strategy", "") or "")
        stage = str(spec.get("stage") or "")
        findings.extend(_check_measurements(body, strategy, stage))
        if not findings:
            findings.append(ValidationFinding("pass", "report_ok", "필수 항목을 모두 만족합니다",
                                              DRAFT_RELPATH))
        return ValidationResult(tuple(findings))

    def repair_feedback(self, validation: ValidationResult) -> AgentFeedback:
        self._repairs += 1
        blocking = [f for f in validation.findings if f.level == "block"]
        return AgentFeedback(tuple(f.code for f in blocking),
                             tuple(f.message for f in blocking))

    # ── 포장 ────────────────────────────────────────────────────────
    def package(self, workspace: RuntimeWorkspace,
                validation: ValidationResult) -> SubmissionBundle:
        assignment_id = self._request.assignment_id if self._request else workspace.plan_id
        if validation.blocked:
            return SubmissionBundle(assignment_id, (), ("draft.md",))
        files, missing = [], []
        for relpath in (DRAFT_RELPATH,) + _required_attachments(self._request):
            try:
                path = confined_path(workspace.root, relpath, must_exist=True)
            except (RuntimeSecurityError, OSError):
                # must_exist=True는 없는 파일에 FileNotFoundError(=OSError)를 던진다.
                # 보안 오류만 잡으면 '필수 첨부 누락'이 missing 기록 대신 크래시가 된다.
                missing.append(relpath)
                continue
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            files.append(SubmissionFile(relpath, mime, sha256_file(path),
                                        path.stat().st_size))
        return SubmissionBundle(assignment_id, tuple(files), tuple(missing))

    # ── 파일 쓰기 ───────────────────────────────────────────────────
    def _write_spec(self, workspace: RuntimeWorkspace, request: RuntimeRequest) -> None:
        lines = [f"# {request.spec.get('title') or request.assignment_id}", ""]
        for key in ("course", "goal", "deliverable", "submission_format"):
            value = str(request.spec.get(key) or "").strip()
            if value:
                lines.append(f"- **{key}**: {value}")
        required = request.spec.get("required") or ()
        if required:
            lines += ["", "## 필수 항목"] + [f"- {item}" for item in required]
        if request.decisions:
            lines += ["", "## 학생이 이미 정한 것(뒤집지 말 것)"]
            lines += [f"- {k}: {v}" for k, v in sorted(request.decisions.items())]
        _write(workspace, SPEC_RELPATH, "\n".join(lines) + "\n")

    def _write_prompt(self, workspace: RuntimeWorkspace, request: RuntimeRequest) -> None:
        editable = DRAFT_RELPATH
        text = f"""# 작업 지시

`{SPEC_RELPATH}`의 과제 명세와 `inputs/`의 자료를 읽고 `{editable}` **한 파일만**
고쳐 보고서 초안을 완성하세요.

## 반드시 지킬 것

1. `{editable}` 밖의 파일을 만들거나 고치지 마세요. `inputs/`는 읽기 전용입니다.
2. 네트워크에 접속하지 말고, 주어진 자료 안에서만 쓰세요.
3. **사람의 고유 판단은 대신 정하지 마세요.** 관점·논지 선택, 가치판단, 본인 경험·
   진로처럼 그 학생만 정할 수 있는 자리는 확정하지 말고 그대로 표식으로 남기세요:

       [[DECISION: 무엇을 정해야 하는지 한 문장 + 후보가 있으면 후보]]

   이 표식이 하나도 없으면 검증에서 되돌아옵니다(대신 결정한 것으로 봅니다).
4. 자료를 근거로 쓴 문장에는 `[자료1]` 처럼 자료 번호를 답니다.
5. **측정하지 않은 수치를 지어내지 마세요.** 실측값·파형·합성 결과가 필요한데
   자료에 없으면 값을 채우지 말고 빈칸형 결정으로 남기세요.

## 자료

`inputs/` 아래 파일들이 이번 과제의 근거입니다.
"""
        _write(workspace, PROMPT_RELPATH, text)

    def _ensure_draft(self, workspace: RuntimeWorkspace) -> None:
        path = confined_path(workspace.root, DRAFT_RELPATH)
        if not path.exists():
            _write(workspace, DRAFT_RELPATH,
                   "<!-- 이 파일만 고치세요. 완성한 초안을 여기에 씁니다. -->\n")


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


def _required_attachments(request: Optional[RuntimeRequest]) -> tuple[str, ...]:
    if request is None:
        return ()
    items = request.spec.get("required_attachments") or ()
    return tuple(str(item) for item in items)


# ── 결정적 검사기들 ─────────────────────────────────────────────────
def _check_sections(body: str, spec) -> list:
    required = [str(item).strip() for item in (spec.get("required") or ()) if str(item).strip()]
    missing = [item for item in required if item not in body]
    if not missing:
        return []
    return [ValidationFinding("block", "missing_section",
                              "필수 항목이 초안에 없습니다: " + ", ".join(missing[:5]),
                              DRAFT_RELPATH)]


def _check_length(body: str, spec) -> list:
    minimum = spec.get("min_chars")
    try:
        minimum = int(minimum)
    except (TypeError, ValueError):
        return []
    if minimum <= 0:
        return []
    # 결정 표식과 공백은 분량에서 뺀다(웹·CLI 판정과 같은 기준).
    counted = re.sub(r"\s+", "", _DECISION_RE.sub("", body))
    if len(counted) >= minimum:
        return []
    return [ValidationFinding(
        "block", "too_short",
        f"분량 부족 — 요건 {minimum}자, 현재 {len(counted)}자", DRAFT_RELPATH)]


def _check_citations(body: str, spec) -> list:
    if not spec.get("requires_citation"):
        return []
    if _CITATION_RE.search(body):
        return []
    return [ValidationFinding("block", "no_citation",
                              "자료 인용 표식([자료N])이 하나도 없습니다", DRAFT_RELPATH)]


def _check_decisions(body: str) -> list:
    """경계선 강제 — 사람 몫 판단을 에이전트가 확정해 버리지 않았는지.

    마커 **존재만** 보면 뚫린다: 진로를 대신 확정해 놓고 "표지 색상을 고르세요"
    같은 사소한 마커 하나만 남겨도 통과했다(감사 2026-08-20에서 재현).
    그래서 두 가지를 함께 본다.
      ① 사람 몫 자리를 하나도 안 남겼는가(기존 검사)
      ② 1인칭으로 **입장을 확정한 문장**이 본문에 있는가 —
         판정은 BoundaryValidator가 쓰는 것과 같은 결정적 패턴을 재사용한다.
         (마커 안쪽 텍스트는 제외한다. 거기 적힌 건 '정하라'는 질문이지 확정이 아니다.)
    """
    findings = []
    notes = [note.strip() for note in _DECISION_RE.findall(body)]
    if not [note for note in notes if len(note) >= 5]:
        findings.append(ValidationFinding(
            "block", "boundary_crossed",
            "사람이 정할 자리를 남기지 않았습니다 — 관점·가치판단·본인 경험은 "
            "[[DECISION: ...]]으로 남겨야 합니다", DRAFT_RELPATH))
    outside = _DECISION_RE.sub(" ", body)
    for pattern in _stance_patterns():
        hit = pattern.search(outside)
        if hit:
            fragment = outside[max(0, hit.start() - 20):hit.end() + 20].strip()
            findings.append(ValidationFinding(
                "block", "stance_decided_for_user",
                "사람 고유의 입장을 대신 확정한 문장이 있습니다 — 그 자리는 "
                f"[[DECISION: ...]]으로 남겨야 합니다: …{fragment[:80]}…",
                DRAFT_RELPATH))
            break
    return findings


# BoundaryValidator 패턴이 못 잡는 확정 표현 보강 — 동결된 boundary_guard를
# 건드리면 알고리즘 결정성이 깨지므로(8월 동결) 런타임 쪽에서만 더한다.
# 감사(2026-08-20) 재현 문장 "나는 진로를 의사로 확정한다"가 기존 패턴을 빠져나갔다.
_NOT_SENTENCE_END = r"[^.\n]"
_EXTRA_STANCE_RE = tuple(re.compile(p) for p in (
    r"나(는|의)\s*" + _NOT_SENTENCE_END + r"{0,40}(확정|결정)(한|했|하겠|합니다|했습니다)",
    r"저(는|의)\s*" + _NOT_SENTENCE_END + r"{0,40}(확정|결정)(한|했|하겠|합니다|했습니다)",
    r"내\s*(진로|전공|장래|목표|가치관)" + _NOT_SENTENCE_END + r"{0,30}"
    r"(로|으로)\s*" + _NOT_SENTENCE_END + r"{0,20}(정한|정했|확정|결정)",
))


def _stance_patterns():
    """1인칭 '입장 확정' 판정 패턴 — BoundaryValidator 것 + 런타임 보강분.

    boundary_guard를 못 불러오면 보강분만으로라도 검사한다(검사 생략 금지)."""
    try:
        from ..execution.boundary_guard import _STANCE_RE
    except Exception:
        return _EXTRA_STANCE_RE
    return tuple(_STANCE_RE) + _EXTRA_STANCE_RE


def _check_measurements(body: str, strategy: str = "", stage: str = "") -> list:
    """수치 날조 금지 — hdl_lab / lab_report_cycle(result)에서만 동작하는 결정적 검사."""
    try:
        from ..understanding.measured_check import find_ungrounded_measurements
    except Exception as exc:
        # 검사기를 못 부르면 '통과'가 아니라 '판정 불가'다. 조용히 넘기면 수치 날조
        # 금지(CLAUDE.md 타협 불가 규칙)가 import 실패 한 번으로 무력화된다.
        return [ValidationFinding(
            "block", "measurement_check_unavailable",
            f"수치 근거 검사기를 실행하지 못했습니다: {exc}", DRAFT_RELPATH)]
    try:
        found = find_ungrounded_measurements(body, [], strategy=strategy, stage=stage)
    except Exception as exc:
        return [ValidationFinding(
            "block", "measurement_check_unavailable",
            f"수치 근거 검사가 실패했습니다: {exc}", DRAFT_RELPATH)]
    if not found:
        return []
    sample = "; ".join(str(item)[:60] for item in list(found)[:3])
    return [ValidationFinding(
        "block", "ungrounded_measurement",
        f"근거 없는 수치가 있습니다(날조 금지): {sample}", DRAFT_RELPATH)]


def build_report_runtime() -> ReportRuntime:
    return ReportRuntime()


def workspace_provider_for(runtime: ReportRuntime):
    """CLI 어댑터가 현재 작업공간을 알 수 있게 해 주는 콜러블."""
    def _provider():
        return runtime.workspace
    return _provider
