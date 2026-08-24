"""`python -m until.runtime` — Local Agent Runtime의 사용자 진입점.

이 파일이 하는 일은 **조립뿐**이다. 판정·격리·검증은 전부 이미 있는 모듈이
하고(`orchestrator`·`report_runtime`·`boundary`·`security`), 여기서는 순서대로
불러 사람에게 보여 준다:

    수집(LLM 0) → 정책 층 결정 → 작업공간 생성 → 에이전트 계획 미리보기
      → **사람 승인** → 실행 → 결정적 검증(+1회 자동 수정) → 제출 번들

경계선은 그대로다. 승인 없이는 프로세스가 0회 뜨고, 검증을 통과하지 않은
결과는 번들이 되지 않으며, **실제 제출은 이 명령이 하지 않는다** — 검증된
파일 목록을 알려 줄 뿐 eTL에 올리는 것은 여전히 사람 몫이다.

설정은 `docs/LOCAL_AGENT_SETUP.md` 참조. CLI 설정(`UNTIL_AGENT_CMD` 또는
`UNTIL_AGENT_SPEC`)과 OS 샌드박스(`UNTIL_AGENT_SANDBOX` +
`UNTIL_AGENT_SANDBOX_ISOLATES`)가 **둘 다** 있어야 실행이 열린다. 하나라도
없으면 격리를 거짓으로 신고하지 않고 실행을 거부한다(fail-closed).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from . import etl_input

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_CONFIG = 2

_SETUP_DOC = "docs/LOCAL_AGENT_SETUP.md"


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z가-힣._-]+", "-", value).strip("-")
    return cleaned[:64] or "assignment"


def _out(line: str = "") -> None:
    print(line, flush=True)


def _findings(validation) -> list:
    return list(getattr(validation, "findings", ()) or ())


def _print_prepared(report) -> None:
    workspace, plan = report.workspace, report.agent_plan
    availability = report.availability
    _out(f"작업공간   {workspace.root}")
    _out(f"에이전트   {availability.name} {availability.version}".rstrip())
    _out(f"계획       {plan.summary}")
    if plan.expected_changes:
        _out("바뀔 파일  " + ", ".join(plan.expected_changes))
    if plan.tool_kinds:
        _out("쓸 도구    " + ", ".join(plan.tool_kinds))


def _print_report(report) -> None:
    for finding in _findings(report.validation):
        mark = {"block": "✗", "warn": "⚠"}.get(finding.level, "✓")
        where = f" ({finding.artifact})" if finding.artifact else ""
        _out(f"  {mark} {finding.code} · {finding.message}{where}")
    receipt = report.receipt
    if receipt is not None and receipt.reason:
        _out(f"  에이전트 상태 {receipt.status} — {receipt.reason}")


def _confirm(prompt: str) -> bool:
    """대화형 승인. 파이프·리다이렉트로 입력이 없으면 승인하지 않는다."""
    if not sys.stdin or not sys.stdin.isatty():
        _out("입력이 터미널이 아니라 승인을 받을 수 없습니다 — --yes 를 쓰세요.")
        return False
    try:
        return input(prompt).strip().lower() in {"y", "yes", "ㅇ"}
    except (EOFError, KeyboardInterrupt):
        _out()
        return False


def _ask_decisions(notes, *, interactive: bool) -> dict:
    """사람이 정할 곳을 하나씩 물어본다. 빈 답은 '아직 안 정함'으로 남긴다.

    비대화형이면 묻지 않고 전부 비워 둔다 — 여기서 대신 정해 버리면 경계선을
    넘는다. 대신 제출본에 `【직접 정할 것 N】`으로 남고 화면이 그걸 경고한다.
    """
    if not notes:
        return {}
    _out()
    _out(f"사람이 정할 곳 {len(notes)}군데 — 자료로 못 채우는 판단입니다.")
    if not interactive:
        for index, note in enumerate(notes, 1):
            _out(f"  [{index}] {note}")
        _out("  (비대화형이라 묻지 않았습니다. --ask 로 물어보게 하거나 "
             "--answers 파일로 미리 주세요.)")
        return {}
    answers = {}
    for index, note in enumerate(notes, 1):
        _out(f"  [{index}] {note}")
        try:
            text = input("      > ").strip()
        except (EOFError, KeyboardInterrupt):
            _out()
            break
        if text:
            answers[note] = text
    return answers


def _finish_submission(args, report, spec):
    """검증된 초안에서 제출본을 만든다. 실패는 비치명적(원본은 그대로 남는다)."""
    from . import finish

    workspace = report.workspace
    notes = finish.read_decision_notes(workspace)
    answers = {}
    if args.answers is not None:
        try:
            answers = finish.load_answers(args.answers, notes)
        except OSError as exc:
            _out(f"⚠ 답변 파일을 읽지 못했습니다: {exc}")
    interactive = bool(sys.stdin and sys.stdin.isatty()) and (args.ask or not args.yes)
    remaining = tuple(n for n in notes if n not in answers)
    if remaining:
        answers.update(_ask_decisions(remaining, interactive=interactive))
    try:
        return finish.finish(workspace, spec, answers)
    except (OSError, ValueError) as exc:
        _out(f"⚠ 제출본을 만들지 못했습니다: {exc}")
        return None


def _build_policy(head_text: str, assignment_id: str, course_policy: str):
    """기관 → (있으면) 과목 → 과제 순으로 정책 층을 쌓는다.

    `tools/build_academic_os.py`가 코퍼스 감사에서 쓰는 것과 같은 순서다 —
    감사와 실행이 다른 정책을 보면 감사 결과가 실행을 설명하지 못한다."""
    from ..policy_compiler import compile_policy_layer
    from ..policy_hierarchy import resolve_policy
    from ..policy_profiles import snu_2026_baseline

    layers = [snu_2026_baseline()]
    if course_policy.strip():
        layers.append(compile_policy_layer(
            course_policy, scope="course", scope_id="course",
            source_id="course:local", title="강의계획서"))
    layers.append(compile_policy_layer(
        head_text, scope="assignment", scope_id=assignment_id,
        source_id=f"assignment:{assignment_id}", title="과제 지시문"))
    return resolve_policy(layers)


def _parse_args(argv):
    ap = argparse.ArgumentParser(
        prog="until.runtime",
        description="로컬 AI 에이전트에게 과제 작업을 맡기고 결과를 검증한다.")
    ap.add_argument("files", nargs="*", type=Path,
                    help="과제 지시문·첨부 파일 (첫 파일을 과제 원문으로 본다)")
    ap.add_argument("--assignment-id", default="",
                    help="과제 식별자 (기본: 첫 파일 이름)")
    ap.add_argument("--course-policy", type=Path, default=None,
                    help="강의계획서 텍스트 파일 — 과제가 AI 정책에 침묵할 때 상속한다")
    ap.add_argument("--title", default="", help="과제 제목을 직접 지정")
    ap.add_argument("--work-root", type=Path, default=Path("_until_work"),
                    help="작업공간 루트 (기본: _until_work)")
    ap.add_argument("--yes", action="store_true",
                    help="계획을 보여준 뒤 되묻지 않고 승인한다")
    ap.add_argument("--probe", action="store_true",
                    help="에이전트 설치·로그인 상태만 확인하고 끝낸다")
    ap.add_argument("--json", action="store_true", help="결과를 JSON으로 출력")
    etl = ap.add_argument_group("eTL에서 바로 가져오기")
    etl.add_argument("--fast", action="store_true",
                     help="마감이 가장 가까운 미제출 과제를 골라 그대로 진행한다")
    etl.add_argument("--etl-url", default="",
                     help="특정 eTL 과제 URL을 지정한다")
    etl.add_argument("--list", action="store_true",
                     help="eTL 과제 목록만 보여주고 끝낸다")
    etl.add_argument("--token", default="",
                     help="eTL 액세스 토큰 (기본: UNTIL_CANVAS_TOKEN)")
    etl.add_argument("--ws", action="store_true",
                     help="Moodle WS 어댑터로 접속 (기본: Canvas API)")
    etl.add_argument("--materials", type=int, default=etl_input.DEFAULT_MATERIALS,
                     metavar="N", help="함께 넣을 관련 강의자료 수 (0이면 안 넣음)")
    fin = ap.add_argument_group("제출본 마무리")
    fin.add_argument("--answers", type=Path, default=None, metavar="파일",
                     help="결정 답변을 미리 담은 파일(JSON 또는 줄당 하나) — 비대화형용")
    fin.add_argument("--ask", action="store_true",
                     help="--yes 로 돌려도 결정만은 물어본다")
    ap.add_argument("--verify-sandbox", action="store_true",
                    help="샌드박스가 실제로 네트워크·작업공간 밖 쓰기를 막는지 시험한다")
    ap.add_argument("--python", default="python", metavar="경로",
                    help="샌드박스 안의 파이썬 실행 파일 (--verify-sandbox 용)")
    return ap.parse_args(argv)


def _load_agent_spec():
    """CLI 설정을 읽는다. 없거나 잘못됐으면 (None, 안내문)."""
    from .cli_agent import CliSpecError, load_cli_spec
    try:
        spec = load_cli_spec(os.environ)
    except CliSpecError as exc:
        return None, f"에이전트 설정이 올바르지 않습니다: {exc}"
    if spec is None:
        return None, ("로컬 에이전트가 설정돼 있지 않습니다. 벤더 플래그를 추측하지 "
                      "않으므로 직접 알려 주셔야 합니다:\n"
                      "  UNTIL_AGENT_CMD=<공식 CLI> UNTIL_AGENT_RUN_ARGS=<실행플래그>,{prompt}\n"
                      f"자세한 설정은 {_SETUP_DOC} 를 보세요.")
    return spec, ""


def build_plugins() -> list:
    """이 런타임이 맡을 수 있는 과제 유형들.

    산출물 모양이 다르면 검증기도 달라야 한다 — 코드에 산문 검증기를 들이대면
    "검증 통과"가 거짓말이 된다. 여기 없는 전략(`hdl_lab`·`rmd_notebook` 등)은
    **일부러 비워 둔다**: 파형·통계 출력은 도구를 실제로 돌려야 나오는 값인데
    커널에 실행 엔진이 아직 없어서, 통과를 주면 수치 날조를 승인하는 꼴이 된다.
    맡지 못하는 과제는 라우팅 단계에서 unsupported로 정직하게 멈춘다.
    """
    from .code_runtime import CodeRuntime
    from .form_runtime import FormRuntime
    from .presentation_runtime import PresentationRuntime
    from .report_runtime import ReportRuntime

    return [ReportRuntime(), CodeRuntime(), PresentationRuntime(), FormRuntime()]


def _active_workspace(plugins):
    """플러그인 중 작업공간이 채워진 것을 돌려준다(선택된 하나만 채워진다)."""
    def _provider():
        for plugin in plugins:
            workspace = getattr(plugin, "workspace", None)
            if workspace is not None:
                return workspace
        return None
    return _provider


def _verify_sandbox(args) -> int:
    """설정한 샌드박스가 실제로 막는지 시험하고, 무엇을 신고해도 되는지 알려준다."""
    import tempfile

    from .boundary import load_sandbox
    from . import sandbox_check

    sandbox = load_sandbox(os.environ)
    if not sandbox.configured:
        _out("UNTIL_AGENT_SANDBOX 가 설정돼 있지 않습니다 — 시험할 대상이 없습니다.")
        _out(f"설정 방법은 {_SETUP_DOC} 2단계를 보세요.")
        return EXIT_CONFIG

    _out("샌드박스: " + " ".join(sandbox.argv_prefix))
    with tempfile.TemporaryDirectory(prefix="until_sbxcheck_") as scratch:
        report = sandbox_check.verify(sandbox, Path(scratch) / "ws",
                                      python=args.python)
    # 라벨은 시험마다 뜻이 다르다. '작업공간 안 쓰기'는 **되어야** 통과인데
    # 차단 계열과 같은 문구를 쓰면 "막힘 ✓ 작업공간 안 쓰기 가능"처럼 읽혀
    # 정반대로 이해된다(실측으로 잡음).
    blocking = {sandbox_check.PASS: "막힘   ✓", sandbox_check.FAIL: "뚫림   ✗",
                sandbox_check.UNKNOWN: "모름   ?"}
    allowing = {sandbox_check.PASS: "가능   ✓", sandbox_check.FAIL: "불가   ✗",
                sandbox_check.UNKNOWN: "모름   ?"}
    names = {"network": "네트워크 차단", "filesystem": "작업공간 밖 쓰기 차단",
             "workspace_writable": "작업공간 안 쓰기"}
    _out()
    for item in report.results:
        label = allowing if item.name == "workspace_writable" else blocking
        _out(f"  {label[item.status]}  {names.get(item.name, item.name)}")
        if item.detail:
            _out(f"      {item.detail}")
    _out()

    if report.overclaimed:
        _out("⚠ 신고했지만 증명되지 않은 격리: " + ", ".join(report.overclaimed))
        _out("  UNTIL_AGENT_SANDBOX_ISOLATES 에서 빼세요 — 확인 못 한 격리를 "
             "신고하면 커널이 거짓 신뢰로 프로세스를 띄웁니다.")
        return EXIT_BLOCKED
    safe = report.safe_to_claim
    if len(safe) == 2:
        _out("두 격리가 모두 확인됐습니다. 이렇게 신고하면 됩니다:")
        _out("  export UNTIL_AGENT_SANDBOX_ISOLATES='filesystem,network'")
        return EXIT_OK
    _out("확인된 격리: " + (", ".join(safe) if safe else "없음"))
    _out("둘 다 확인돼야 실행이 열립니다 — 샌드박스 설정을 고친 뒤 다시 시험하세요.")
    return EXIT_BLOCKED


def _probe(agent_spec, controller) -> int:
    """설치·로그인 상태만 확인.

    probe도 샌드박스 **안에서** `--version`/`status`를 돌리므로 작업 디렉터리가
    필요하다. 진짜 과제 작업공간을 만들지 않고 빈 임시 폴더를 쓴다 — 이 확인이
    과제 파일을 건드릴 이유가 없다."""
    import tempfile

    from .cli_agent import OfficialCliAgent
    from .models import AgentJob

    job = AgentJob(assignment_id="probe", prompt_path="work/PROMPT.md",
                   readable_paths=(), editable_paths=("work/draft.md",))
    with tempfile.TemporaryDirectory(prefix="until_probe_") as scratch:
        agent = OfficialCliAgent(agent_spec, lambda: Path(scratch))
        return _probe_with(agent, controller, job)


def _probe_with(agent, controller, job) -> int:
    from .local_agent import AgentContractError
    try:
        availability, _plan = controller.preview(agent, job)
    except AgentContractError as exc:
        _out(f"probe 불가 — {exc}")
        _out(f"OS 샌드박스 설정이 필요합니다({_SETUP_DOC} 2단계).")
        return EXIT_CONFIG
    except (OSError, ValueError) as exc:
        _out(f"probe 실패 — {exc}")
        return EXIT_CONFIG
    _out(f"{availability.name} · {availability.status} "
         f"{availability.version}".rstrip())
    if availability.reason:
        _out(f"  {availability.reason}")
    return EXIT_OK if availability.status == "ready" else EXIT_BLOCKED


def main(argv=None) -> int:
    args = _parse_args(argv)

    # 샌드박스 확인은 에이전트 설정보다 **먼저** 할 수 있어야 한다 — 순서상
    # 격리부터 갖춘 뒤 CLI를 붙이는 게 자연스럽고, 여기서 에이전트를 요구하면
    # "샌드박스를 확인하려면 먼저 에이전트를 설정하라"는 순환이 된다.
    if args.verify_sandbox:
        return _verify_sandbox(args)

    agent_spec, problem = _load_agent_spec()
    if agent_spec is None:
        _out(problem)
        return EXIT_CONFIG

    from .boundary import build_boundary
    from .local_agent import LocalAgentController
    from .cli_agent import OfficialCliAgent

    plugins = build_plugins()
    # 어느 플러그인이 뽑힐지는 라우팅이 정하므로, 에이전트에게는 **선택된
    # 플러그인의 작업공간**을 알려 줘야 한다. 하나만 보게 하면 다른 플러그인이
    # 뽑혔을 때 에이전트가 엉뚱한(또는 없는) 디렉터리에서 돌게 된다.
    agent = OfficialCliAgent(agent_spec, _active_workspace(plugins))
    controller = LocalAgentController(build_boundary(os.environ))

    if args.probe:
        return _probe(agent_spec, controller)

    # 0. 입력 — eTL에서 가져오거나(--fast/--etl-url/--list) 로컬 파일을 받는다.
    #    작업공간에 복사되기 전까지 다운로드는 이 임시 폴더에만 머문다.
    from_etl = None
    scratch = None
    if args.list or args.fast or args.etl_url:
        if args.files:
            _out("eTL 경로와 로컬 파일을 같이 줄 수 없습니다 — 하나만 고르세요.")
            return EXIT_CONFIG
        import tempfile
        scratch = tempfile.mkdtemp(prefix="until_etl_")
        try:
            outcome = _resolve_from_etl(args, Path(scratch))
        except etl_input.EtlInputError as exc:
            _out(str(exc))
            return EXIT_CONFIG
        if isinstance(outcome, int):            # --list 등 여기서 끝나는 경로
            _cleanup(scratch)
            return outcome
        from_etl = outcome
        args.files = list(from_etl.files)
        for skipped in from_etl.skipped:
            _out(f"⚠ {skipped}")

    try:
        return _run(args, plugins, agent, controller, from_etl)
    finally:
        _cleanup(scratch)


def _cleanup(scratch) -> None:
    if not scratch:
        return
    import shutil
    shutil.rmtree(scratch, ignore_errors=True)


def _resolve_from_etl(args, scratch: Path):
    """eTL에서 과제를 골라 파일로 내려받는다. `--list`는 목록만 찍고 종료코드를 반환."""
    token = (args.token or os.getenv("UNTIL_CANVAS_TOKEN") or "").strip()
    adapter = etl_input.build_adapter(token, ws=args.ws)
    base = etl_input.etl_base_url(adapter)

    url = (args.etl_url or "").strip()
    if not url or args.list:
        items = etl_input.list_assignments(adapter, base_url=base)
        if not items:
            _out("eTL에서 과제를 하나도 찾지 못했습니다. "
                 "토큰이 맞는지, 수강 과목이 있는지 확인해 주세요.")
            return EXIT_BLOCKED
        if args.list:
            _out(f"과제 {len(items)}건 (마감 임박순)")
            for item in items[:30]:
                mark = "제출함" if getattr(item, "submitted", False) else "미제출"
                due = (getattr(item, "due_at", "") or "").replace("T", " ")[:16] or "마감 미상"
                _out(f"  [{mark}] {due}  {getattr(item, 'title', '')[:48]}")
                _out(f"           {getattr(item, 'url', '')}")
            return EXIT_OK
        best = etl_input.pick_nearest(items)
        if best is None:
            _out("바로 시작할 과제를 고르지 못했습니다 — --list 로 보고 "
                 "--etl-url 로 직접 지정해 주세요.")
            return EXIT_BLOCKED
        url = best.url
        _out(f"고른 과제   {getattr(best, 'title', '')}")

    return etl_input.collect(adapter, url, scratch,
                             materials=max(0, args.materials), base_url=base)


def _run(args, plugins, agent, controller, from_etl):
    if not args.files:
        _out("과제 파일을 하나 이상 주세요 (또는 --fast / --list / --probe).")
        return EXIT_CONFIG
    missing = [str(path) for path in args.files if not Path(path).is_file()]
    if missing:
        _out("파일을 찾지 못했습니다: " + ", ".join(missing))
        return EXIT_CONFIG

    # 1. 수집 — 결정적, 토큰 0.
    from ..capture.ingest import ingest_all_with_warnings
    docs, warnings = ingest_all_with_warnings([str(p) for p in args.files])
    for warning in warnings:
        _out(f"⚠ 첨부 건너뜀 — {warning}")
    if not docs:
        _out("읽을 수 있는 과제 문서가 없습니다.")
        return EXIT_CONFIG

    # 2. 교수자의 명시적 AI 금지 — 어떤 편의보다 먼저, 에이전트를 띄우기 전에.
    from ..academic_policy import AiUseProhibitedError, enforce_ai_use_policy
    try:
        enforce_ai_use_policy(docs)
    except AiUseProhibitedError as exc:
        _out(str(exc))
        return EXIT_BLOCKED

    # 3. 명세·라우팅·정책(전부 결정적).
    from .spec_builder import build_runtime_spec
    from ..context.assignment_router import route_documents
    from .models import RuntimeRequest

    spec = build_runtime_spec(
        docs, title=args.title or (from_etl.title if from_etl else ""))
    route = route_documents(spec, docs)
    # eTL에서 왔으면 **eTL의 과제 id**를 쓴다 — 번들·제출 대조가 같은 id를 봐야
    # '어느 과제의 제출물인지'가 코드로 확인된다(파일 이름 슬러그는 그걸 못 한다).
    assignment_id = _slug(
        args.assignment_id
        or (from_etl.assignment_id if from_etl else "")
        or Path(args.files[0]).stem)
    if from_etl and from_etl.course_name and not spec.get("course"):
        spec["course"] = from_etl.course_name
    course_policy = ""
    if args.course_policy is not None:
        try:
            course_policy = args.course_policy.read_text(encoding="utf-8")
        except OSError as exc:
            _out(f"강의계획서를 읽지 못했습니다: {exc}")
            return EXIT_CONFIG
    policy = _build_policy(str(getattr(docs[0], "text", "") or ""),
                           assignment_id, course_policy)

    _out(f"과제       {spec.get('title') or assignment_id}")
    if from_etl and from_etl.course_name:
        _out(f"과목       {from_etl.course_name}")
    _out(f"라우팅     {route.strategy} — {route.reason}")
    _out(f"AI 정책    {policy.ai_use}"
         + (f" (근거 범위: {policy.controlling_scope})" if policy.controlling_scope else ""))
    requirements = [f"필수 항목 {len(spec['required'])}개"] if spec.get("required") else []
    if spec.get("min_chars"):
        requirements.append(f"{spec['min_chars']}자 이상")
    if spec.get("requires_citation"):
        requirements.append("자료 인용")
    _out("검증 기준  " + (" · ".join(requirements) or "본문 검사만(요건 미검출)"))
    _out()

    request = RuntimeRequest(assignment_id, spec, route, policy,
                             tuple(str(p) for p in args.files))

    from .orchestrator import RuntimeOrchestrator
    from .registry import RuntimeRegistry
    orchestrator = RuntimeOrchestrator(
        RuntimeRegistry(tuple(plugins)), agent, args.work_root, controller=controller)

    # 4. 준비 — 여기까지 프로세스는 probe 말고 뜨지 않는다.
    prepared = orchestrator.execute(request)
    if prepared.status != "prepared":
        _out(f"진행 불가 — {prepared.reason or prepared.status}")
        if policy.ai_use == "unclear":
            _out("과제 지시문에 AI 사용 가능 여부가 없습니다. 강의계획서 문구를 "
                 "파일로 저장해 --course-policy 로 넘기면 그 정책을 상속합니다.")
        if args.json:
            _out(json.dumps(prepared.to_dict(), ensure_ascii=False, indent=1))
        return EXIT_BLOCKED
    _print_prepared(prepared)
    _out()

    # 5. 승인 — 이 클릭이 없으면 에이전트는 한 글자도 쓰지 않는다.
    if not (args.yes or _confirm("이 계획대로 로컬 에이전트를 실행할까요? [y/N] ")):
        _out("승인하지 않았습니다 — 실행하지 않았습니다.")
        return EXIT_BLOCKED

    # 6. 실행 → 검증 → (실패 시 1회 수정) → 번들.
    from .models import Approval
    report = orchestrator.execute(request, approval=Approval(
        prepared.agent_plan.fingerprint, True, prepared.workspace.run_id, "cli"))

    if args.json:
        _out(json.dumps(report.to_dict(), ensure_ascii=False, indent=1))
        return EXIT_OK if report.status == "ready" else EXIT_BLOCKED

    if report.status != "ready":
        _out(f"검증 실패 — {report.reason or '아래 항목을 통과하지 못했습니다'}")
        _print_report(report)
        if report.workspace is not None:
            _out(f"작업 결과는 {report.workspace.root} 에 그대로 있습니다.")
        return EXIT_BLOCKED

    _out("검증 통과.")
    _print_report(report)

    # 마지막 한 칸 — 검증된 초안에는 경계선 표식이 살아 있다. 그대로 올리면
    # 교수가 `[[DECISION: ...]]`을 본다. 사람의 답을 받아 제출본을 따로 만든다.
    finished = _finish_submission(args, report, spec)
    root = report.workspace.root
    _out()
    _out("올릴 파일")
    if finished is not None:
        _out(f"  · {finished.path}")
        for warning in finished.warnings:
            _out(f"    ⚠ {warning}")
        if finished.open_notes:
            _out(f"    ⚠ 아직 정하지 않은 곳 {len(finished.open_notes)}군데가 "
                 "【직접 정할 것】으로 남아 있습니다 — 올리기 전에 채워 주세요.")
    _out(f"  (검증한 원본: {root / report.bundle.files[0].path})"
         if report.bundle.files else "")
    # 마지막 한 칸 — 어디에 올리는지까지 알려 줘야 "제출 클릭만 하면 되는" 상태다.
    # 링크는 과목·과제 id로 재구성한다(원문 URL 미보관 방침, `web._assignment_link`와 동일).
    link = ""
    if from_etl:
        link = (etl_input.submit_page_url(
            etl_input.etl_base_url(), from_etl.course_id, from_etl.assignment_id)
            or from_etl.page_url)
    if link:
        _out(f"제출하러 가기  {link}")
        _out("위 파일을 그 페이지에서 올리면 끝입니다 — 전송은 사람이 합니다.")
    else:
        _out("제출은 사람이 합니다 — 위 파일을 eTL 과제 페이지에서 직접 올려 주세요.")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
