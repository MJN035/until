"""
Orchestrates the end-to-end slice:
  Capture(파싱) → Understanding → [Personalization/Context] → Execution → Boundary.

Personalization/Context 레이어: 수업자료·내 파일·내 말투를 모아 Execution에 주입한다.
(course_dir / my_files_dir / voice_dir 가 주어지면 활성화)
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .config import Config
from .capture.models import Document
from .understanding.task_spec import extract_task_spec
from .execution.drafter import draft_to_boundary, finalize_with_decisions
from .boundary.models import Draft
from .boundary.resolve import pair_resolved_decisions, render_resolved_block
from .execution.boundary_guard import GuardReport
from .prompts.suggest import suggest_prompts
from .llm.base import build_client
from .context.bundle import assemble_context, ContextBundle


@dataclass
class Result:
    documents: List[Document]
    spec: dict
    draft: Draft
    guard: GuardReport
    suggested_prompts: List[str] = field(default_factory=list)
    context: Optional[ContextBundle] = None
    # P6 — 결정 해소 후 2차 패스(finalize)로 만든 최종 완성본(있을 때만).
    final_draft: Optional[Draft] = None
    final_guard: Optional[GuardReport] = None
    # P10/P11 — eTL에서 자동수집해 주입한 관련자료(표시용; MaterialHit 목록).
    etl_materials: list = field(default_factory=list)
    # 4번 — 이 과제 관련 eTL 공지(표시용; Announcement 목록).
    etl_announcements: list = field(default_factory=list)
    # 근거 자료 범례 — Execution에 1-기반 순서로 넣은 자료 제목들([자료N] 인용과 매칭).
    sources: List[str] = field(default_factory=list)
    # Execution에 실제로 넣은 SourceDoc 목록(범례와 같은 순서) — finalize·suggest·review
    # 2차 패스가 동일 번호 체계를 쓰도록 재사용한다.
    source_docs: list = field(default_factory=list)
    # 분량 요건 감지 결과(LengthTarget | None). 표시·판정용, 결정적.
    length_target: object = None
    # 마감일 감지 결과(Deadline | None). D-day 표시용, 결정적.
    deadline: object = None
    # 요구사항 원자 분해(ContentElement 목록) — 커버리지·근거 판정의 기반.
    content_elements: list = field(default_factory=list)
    # 단위별 경로(UNTIL_PIPELINE=unit)의 ResponseUnit 목록(진단·eval용).
    units: list = field(default_factory=list)
    # Capture 단계에서 파싱 실패로 스킵된 첨부 경고("파일명: 사유").
    capture_warnings: List[str] = field(default_factory=list)
    # 주차별 질의순번표에서 프로필 학번으로 찾은 담당 교수·실제 마감(있을 때만).
    inquiry_assignment: object = None
    # 형식 검증기(execution/format_guard)가 찾은 어긋남 — 고친 것과 남은 것.
    # `pipeline.run`은 채우지 않는다(8월 결정성 동결 — 지문이 바뀌면 안 된다).
    # 화면으로 나가기 직전 `web._apply_format_pass`가 채우고, 저장은 하지 않는다
    # (조회할 때마다 결정적으로 다시 계산된다). **필드로 등록하는 것 자체가 목적**이다 —
    # 등록하지 않고 속성만 붙이면 session_store._result가 TypeError를 내서 세션 저장이
    # 통째로 조용히 멈춘다(test_submit_ready·test_cloud가 잡았다).
    format_issues: list = field(default_factory=list)
    # 전수 과제 라우터가 정한 처리 알고리즘(AssignmentRoute).
    assignment_route: object = None
    # 코드 실행 러너의 결과({status, exit_code, stdout, stderr, detail}) — 웹이
    # 붙일 때만 채워진다. 파이프라인 자신은 코드를 실행하지 않는다(러너가 별도
    # 서비스인 이유: 웹 프로세스에 세션·토큰이 함께 있다).
    run_check: object = None
    # LLM 사용량 합산({llm_calls, llm_tokens_in, llm_tokens_out}) — run()·2차
    # 패스(finalize/suggest/review)가 같은 dict에 누적. 텔레메트리 원가 원천.
    llm_usage: Optional[dict] = None
    # 최초 Execution 프롬프트에 VoiceProfile 지침이 실제 주입됐는지의 provenance.
    voice_applied: bool = False
    # 톤 레지스터(UNTIL_TONE_REGISTER=1)로 확정된 말투 규격. 2차 패스(finalize·
    # suggest·revise)가 같은 문자열을 재사용해야 초안과 최종본의 톤이 갈리지 않는다.
    # 플래그가 꺼져 있으면 빈 문자열 — 그때 동작은 기존과 완전히 동일하다.
    tone_block: str = ""
    tone_register: str = ""      # 확정된 register_key(표시·텔레메트리용)
    tone_source: str = ""        # explicit | inferred | default
    # 민감·고위험 상황(사과·거절·갈등) — 자동 확정 금지, 사람 승인 대기 플래그.
    # 초안 생성 자체는 막지 않는다. 막는 것은 자동 확정·자동 제출뿐이다.
    needs_approval: bool = False
    approval_kinds: List[str] = field(default_factory=list)
    approval_messages: List[str] = field(default_factory=list)
    # 출처 기록 — 이게 없으면 나중에 "톤이 바뀐 게 모델 때문인지 프롬프트 때문인지"를
    # 영원히 가릴 수 없다. prompt_version은 SemVer+실제 조립 지문, model_version은
    # 설정값이 아니라 **응답한 모델**(폴백 사슬이면 관여한 순서대로 이어 붙인다).
    prompt_version: str = ""
    model_version: str = ""
    # 생성 소요 시간(ms). 이벤트의 latency_ms 원천 — 0으로 두면 "즉시 나왔다"는
    # 거짓 신호가 되고, 나중에 채널·모델별 체감 속도를 비교할 수 없다.
    # 결정성 게이트는 필드 allowlist 기반이라(draft/spec/sources/…) 영향이 없다.
    elapsed_ms: int = 0
    # 과거 과제 연습은 실제 제출 흐름과 분리한다. 감사 결과는 화면·세션에 보존.
    practice_mode: bool = False
    practice_audit: Optional[dict] = None


def run(
    paths: List[str],
    config: Config | None = None,
    *,
    course_dir: Optional[str] = None,
    my_files_dir: Optional[str] = None,
    voice_dir: Optional[str] = None,
    enhance_voice: bool = False,
    voice_profile=None,
    feedback_hint: str = "",
    extra_context_sources: Optional[list] = None,
    # v0.2: 같은 experiment_id의 예비보고서를 결과보고서 맥락으로 잇기 위한
    # 본인 제출물 rows(Canvas submissions JSON). 웹 계층이 전달, 없으면 무시.
    my_submission_rows: Optional[list] = None,
    # eTL이 이미 아는 과목명. **라우팅 전에** 필요하다 — §3 course_profiles 폴백이
    # 이 값으로만 켜지는데, spec을 만드는 extract_task_spec의 스키마에는 course가
    # 없어서(additionalProperties:false) 여기로 받지 않으면 영영 빈 문자열이다.
    course_name: str = "",
) -> Result:
    cfg = config or Config()
    import time as _time
    # perf_counter는 monotonic보다 해상도가 높다. mock 백엔드로 도는 시험은 파이프라인
    # 전체가 1ms 안에 끝날 수 있는데, monotonic + int() 절삭이면 0ms가 나와 "시간이
    # 측정되지 않았다"와 구분되지 않았다 — CI에서 드물게 터지던 실패의 정체다
    # (2026-08-23 트레이스백 확보: `assert res.elapsed_ms > 0`).
    _started = _time.perf_counter()
    from .llm.meter import MeteredClient, new_usage
    usage = new_usage()
    llm = MeteredClient(build_client(cfg.backend, cfg.model), usage)

    # 1. Capture (문서 파싱) — deterministic, no tokens. 스킵된 첨부는 경고로 보존.
    from .capture.ingest import ingest_all_with_warnings
    docs, capture_warnings = ingest_all_with_warnings(paths, backend=cfg.parser_backend)

    # 교수자의 명시적 AI 금지는 어떤 편의 기능보다 우선한다. Understanding LLM조차
    # 호출하기 전에 멈춰 원문이나 과제 내용을 외부 모델로 전송하지 않는다.
    from .academic_policy import enforce_ai_use_policy
    enforce_ai_use_policy(docs)

    # 2. Understanding — structured task spec + 과제 유형 분류(결정적).
    spec = extract_task_spec(docs, llm)
    # 과목명을 **라우팅 전에** 심는다. 호출자들이 이 값을 run() 이후에 result.spec로
    # 넣고 있어서, §3 course_profiles 폴백은 판정 시점에 늘 빈 과목명을 보고
    # 아무것도 못 켰다 — 설계·테스트·기입 절차까지 갖춘 v0.2 기능이 라이브에서
    # 한 번도 동작하지 않았다(2026-08-21 실측). 호출자가 안 주면 종전과 같다.
    if course_name and isinstance(spec, dict) and not str(spec.get("course") or "").strip():
        spec["course"] = str(course_name).strip()
    from .understanding.task_type import classify_task_type, FACTUAL_TYPES
    task_type = classify_task_type(spec, docs)
    from .context.assignment_router import route_documents
    assignment_route = route_documents(spec, docs)
    # §7 측정 필드 route_source — 이 라우트를 '무엇이' 정했나. 네 갈래(rule ·
    # profile_hint · llm_inferred · clarify) 중 결정적 규칙이 잡았으면 여기서
    # 끝나므로 rule, 못 잡아 묻기로 남으면 clarify가 기본값이다. 아래 두 폴백은
    # 성공했을 때만 자기 값으로 덮는다(clarify 승격은 라우트를 확정하지 않으므로
    # 기본값 그대로 — 후보를 제시하든 일반 묻기든 둘 다 '묻기'다).
    spec["route_source"] = ("clarify"
                            if assignment_route.strategy == "spec_clarification"
                            else "rule")
    from .config import algo_version
    _v2 = algo_version() == "v0.2"
    # 2.0.0 과목 프로파일 폴백(v0.2, §5의 24번 위치) — 결정적 규칙이 아무것도 못
    #     잡았을 때만, 사용자가 학기 초 확정한 route_hint를 적용한다. 어휘 규칙을
    #     이기지 못하고 non_actionable을 뒤집지 못한다(§3 규칙). LLM 추정보다 앞
    #     — 사용자가 확정한 힌트가 모델 추정보다 강한 신호다.
    if _v2 and assignment_route.strategy == "spec_clarification":
        from .context.course_profiles import hint_applies, route_hint_for_course
        from .context.assignment_router import route_for_strategy
        if hint_applies(assignment_route):
            hint = route_hint_for_course(
                course_name=str(spec.get("course") or "") if isinstance(spec, dict) else "")
            hinted = route_for_strategy(hint) if hint else None
            # lab_report_cycle 힌트는 stage를 제목에서 못 정하면 적용하지 않는다
            # (단계를 모르면 어느 하드 금지를 걸지 정할 수 없다 — 팩토리가 None).
            if hinted is not None:
                assignment_route = hinted
                spec["route_source"] = "profile_hint"
    # 2.0.1 라우트 추정 폴백 — 결정적 라우터가 spec_clarification('묻기')만 남기면,
    #     수집 컨텍스트를 근거로 LLM 1회 추정(인용 검증 통과 시에만 교체, 실패·mock은
    #     묻기 유지). 이후의 strategy→task_type 보정이 추정 라우트에도 적용된다.
    if assignment_route.strategy == "spec_clarification":
        from .understanding.route_inference import infer_route
        try:
            route_llm = MeteredClient(
                build_client(cfg.backend, _light_model() or cfg.model), usage)
            inferred = infer_route(spec, docs, extra_context_sources or [], route_llm)
        except Exception:
            inferred = None
        if inferred is not None:
            assignment_route = inferred
            spec["route_inferred"] = inferred.strategy
            spec["route_source"] = "llm_inferred"
        else:
            # 2.0.2 가드 거절 → 능동형 묻기 승격 — 라우트를 확정하지 않되,
            #     후보 2개+필요 원료+선택 질문으로 '무엇을 제출하는지'의 조사를
            #     AI가 최대한 대신한다(질의 유형의 후보 제시 패턴 이식). 실패·
            #     mock은 기존 일반 묻기 그대로.
            from .understanding.route_inference import clarify_candidates
            try:
                clarified = clarify_candidates(
                    spec, docs, extra_context_sources or [], route_llm)
            except Exception:
                clarified = None
            if clarified is not None:
                assignment_route, _cands = clarified
                spec["route_candidates"] = [c["strategy"] for c in _cands]
    if assignment_route.strategy in {"rmd_notebook", "zip_project", "code_project"}:
        task_type = "code"
    elif assignment_route.strategy == "problem_set":
        task_type = "problemset"
    elif assignment_route.strategy == "weekly_inquiry":
        task_type = "inquiry"
    elif _v2 and assignment_route.strategy == "hdl_lab":
        # 계약 매핑: code로 흡수하면 FACTUAL_TYPES라 결정 0개가 허용되는데,
        # HDL 보고서의 '고찰'은 결정이 반드시 필요하다(§4.1).
        task_type = "hdl_lab"
    elif _v2 and assignment_route.strategy == "lab_report_cycle":
        task_type = "report"
    elif _v2 and assignment_route.strategy == "textbook_problem_set":
        task_type = "problemset"
    from .context.distributed_spec import distributed_task_type
    distributed_type = distributed_task_type(extra_context_sources or [])
    if distributed_type:
        task_type = distributed_type
    spec["task_type"] = task_type  # 실행 프롬프트(spec_json)와 mock이 함께 본다.

    # 2.1 대필 금지 신호 게이트(결정적) — 자필 규정 과제는 최종 답안 대신
    #     학습 보조 모드(기획 T4). 감지 근거는 spec에 실려 mock·readiness가 본다.
    from .understanding.integrity import detect_no_ghostwriting
    gate = detect_no_ghostwriting(spec, docs)
    if gate is not None:
        spec["integrity_gate"] = gate.reason

    # 2.2 요구사항 원자 분해 — "A, B, C 들을 기술"을 셀 수 있는 요소로(LLM 1회,
    #     보조 패스라 경량 모델 티어링, 실패 시 결정적 폴백). 커버리지·근거 판정의 기반.
    from .understanding.requirements import extract_content_elements
    try:
        req_llm = MeteredClient(build_client(cfg.backend, _light_model() or cfg.model),
                                usage)
        content_elements = extract_content_elements(spec, docs, req_llm)
    except Exception:
        content_elements = extract_content_elements(spec, docs, None)

    # 2.5 Personalization/Context — 수업자료·내 파일·내 말투 수집 (있을 때만, 토큰 0).
    ctx = assemble_context(
        spec,
        course_dir=course_dir,
        my_files_dir=my_files_dir,
        voice_dir=voice_dir,
        voice_llm=llm if enhance_voice else None,
        voice_profile=voice_profile,
    )

    # 3. Execution — 맥락 근거 + 내 말투로 초안, 경계선은 BoundaryGuard로 강제.
    #    eTL 자동수집 자료(extra_context_sources)가 있으면 폴더 맥락에 더해 함께 주입.
    #    유형별 지침을 시스템에 덧붙이고, 정형(문제풀이·코드)은 결정 0개도 허용(min=0).
    from .execution.prompts import length_directive, type_guidance
    context_sources = ctx.to_sources() + list(extra_context_sources or [])
    # 주차가 어긋나는 자료는 이 과제의 원료가 아니다 — 넣어 두면 모델이 그걸 근거로
    # **관찰한 적 없는 사실**을 쓴다(실사용 2026-08-23: 「12주차 출석」에 10주차 자료가
    # 붙어 "모든 대상 학생이 정상적으로 출석했음"이라는 기록을 지어냈다).
    # 둘 다 주차를 말하는데 서로 다를 때만 뺀다 — 주차가 없는 자료(강의계획서 등)는
    # 그대로 둔다. 모르면 버리지 않는다.
    if context_sources and docs:
        from .context.weekly_brief import drop_week_mismatched
        _head = (getattr(docs[0], "text", "") or "")[:200]
        _kept = drop_week_mismatched(_head, context_sources)
        if len(_kept) != len(context_sources):
            spec["week_mismatched_dropped"] = len(context_sources) - len(_kept)
            context_sources = _kept
    # v0.2 §4.2: 결과보고서(stage="result")는 같은 실험 번호의 예비보고서가
    # 1급 맥락이다("결과보고서는 예비보고서 바탕"). 표면형 시리즈(series_key)와
    # 반대 방향의 묶기라 experiment_id 경로를 따로 쓴다.
    if (_v2 and assignment_route.strategy == "lab_report_cycle"
            and assignment_route.stage == "result" and my_submission_rows):
        from .context.series import experiment_pre_sources
        try:
            context_sources += experiment_pre_sources(
                str(spec.get("title") or spec.get("goal") or "")
                if isinstance(spec, dict) else "", my_submission_rows)
        except Exception:
            pass  # 맥락 보강은 비치명 — 실패해도 초안 생성은 계속.
    # 학습 보조 모드(게이트)는 산출물이 답안이 아니라 결정 0개가 정상.
    min_dec = 0 if (task_type in FACTUAL_TYPES or gate is not None) \
        else cfg.min_decisions
    # 분량 요건은 초안 '생성 전'에 감지해 지침으로 주입 — 사후 판정만으로는
    # 짧은 초안이 반복된다(요건은 Result에도 실려 UI 판정에 재사용).
    from .understanding.length_target import detect_length_target
    # eTL 수집 자료·공지(extra_context_sources)는 '숨은 명세' — 분량 요건이 본문
    # 아닌 공지·첨부에 실리는 실측 패턴 대응(로컬 폴더 맥락 ctx는 명세가 아니라 제외).
    length_target = detect_length_target(spec, docs,
                                         extra_sources=extra_context_sources)
    # 양식(표·①② 항목) 첨부가 감지되면 '원본 구조 그대로 채워 출력' 지침 주입 —
    # 산문 통짜 출력으로 사용자가 칸마다 복붙하던 실사용 버그 대응.
    from .capture.formfill import form_directive
    # 저장된 프로필(이름·학번·소속 등)은 되묻지 않고 채우도록 힌트 주입 —
    # '개인 GPT였으면 안 물어봐도 될' 기본정보를 되묻던 실사용 불만 대응.
    from .profile import profile_hint
    # 답변 골격 — 유형별 논리 순서 슬롯 + 1단계 요소 병합(문장 템플릿 아님).
    from .understanding.skeleton import skeleton_directive, decision_directive
    from .execution.prompts import (study_mode_directive, material_gap_directive,
                                   missing_attachment_directive)
    from .context.presentation_conversion import conversion_directive
    from .context.distributed_spec import distributed_spec_directive
    from .context.structured_assignment import structured_assignment_directive
    from .context.assignment_router import assignment_route_directive
    # v0.2 §4.3: 자필 게이트 발동 시 '받아 적을 풀이' 형식 지시(단계 번호+한 줄씩)
    # 를 함께 준다 — v0.1은 기존 문구 그대로(handwritten 기본 False).
    gate_directive = study_mode_directive(gate.reason, handwritten=_v2) \
        if gate is not None else ""
    # 원료 없음 판정(결정적) — 과제 명세 문서 1개뿐이고 맥락 자료도 없으면,
    # 원료가 필요한 유형(반응형·실습레포트)은 지어내기 대신 자료 요청 결정(기획 §9-2).
    gap_directive = ""
    # 문서 개수만 보면 웹 붙여넣기(항상 1문서)가 상시 발동한다 — 사용자가 강의
    # 요지·실측을 본문에 붙여 넣었으면 그 문서 자체가 원료이므로, 명세 한 장
    # 수준(짧은 본문)일 때만 '원료 없음'으로 판정한다.
    doc_chars = sum(len(getattr(d, "text", "") or "") for d in docs)
    if len(docs) <= 1 and not context_sources and doc_chars < 1800:
        # 명세에 **실내용이 사실상 없으면** 유형과 무관하게 원료를 요청한다.
        #
        # 없으면 이런 일이 난다(2026-08-22 실측, 물리학1 HW#1): 명세가 과목·학기·
        # 마감 + "HW1.pdf" 한 줄뿐인데 그 PDF는 수집되지 않았다. 유형 신호가 하나도
        # 없으니 분류는 **기본값 essay**로 떨어지고, `_MATERIAL_GAP_ASKS`에 essay
        # 항목이 없어 게이트가 면제된다. 그래서 '숙제가 고전역학일까 양자역학일까'를
        # 1,355자 추측해 냈다 — 자료를 달라고 하는 대신.
        #
        # "뭔지 모르겠다 → essay → essay는 자료 없어도 된다"는 사슬을 여기서 끊는다.
        # 진짜 에세이 과제(논제가 본문에 있는 것)는 실내용이 충분해 종전대로 면제된다
        # (실측: HW#1 7자 vs 에세이 샘플 434자·보고서 샘플 226자).
        from .understanding.substance import substantive_chars
        thin = substantive_chars(getattr(docs[0], "text", "") or "") < 200 if docs else True
        gap_directive = material_gap_directive(task_type, fallback=thin)
    elif assignment_route.strategy not in {
            "distributed_spec", "rmd_notebook", "zip_project", "code_project",
            "weekly_inquiry"}:
        # 업로드 슬롯형 원료 없음 — 명세가 로지스틱스(마감·분반·제출 안내)뿐이고
        # 첨부·맥락에도 실내용이 없으면, 컨텍스트 번들이 문서로 붙어 있어도 쓸
        # 원료가 없다(실코퍼스: 실험 예비보고서·회의록 제출함 15건이 200자
        # 가드에 걸리던 회귀). 명세가 딴 곳(공지·ZIP·Rmd)에 사는 경로는 제외.
        from .understanding.substance import substantive_chars
        from .context.assignment_router import is_context_bundle_doc
        non_bundle = [d for d in docs if not is_context_bundle_doc(d)]
        substance = sum(
            substantive_chars(getattr(d, "text", "") or "") if i == 0
            else len(getattr(d, "text", "") or "")
            for i, d in enumerate(non_bundle))
        substance += sum(len(getattr(s, "text", "") or "")
                         for s in context_sources)
        if substance < 200:
            gap_directive = material_gap_directive(task_type, fallback=True)
    # v0.2 §4.1/§4.2 하드 금지 — hdl_lab·결과보고서는 실측(파형·합성 수치·측정값)이
    # 자료에 실재할 때만 쓸 수 있다. 일반 원료 판정과 무관하게 항상 주입한다:
    # 지어낸 수치는 그대로 제출돼 학문적 부정이 된다(타협 불가 항목).
    measured_ban = ""
    if _v2 and (assignment_route.strategy == "hdl_lab"
                or (assignment_route.strategy == "lab_report_cycle"
                    and assignment_route.stage == "result")):
        measured_ban = (
            "[실측 데이터 규칙 — 위반 금지]\n"
            "- 시뮬레이션 파형·합성 수치(LUT/FF·타이밍)·측정값·오차·그래프 수치는 "
            "제공된 자료·답변에 실재하는 것만 쓴다.\n"
            "- 실측 근거가 없으면 해당 자리를 추정값으로 채우지 말고 빈칸형 "
            "[[DECISION: (필요한 실측값) — 본인 실측 필요]]만 남긴다.")
        # 실측 근거가 사실상 없으면(명세뿐) 원료 요청 결정으로 전환 — 일반 판정이
        # 예비보고서 텍스트를 원료로 오인해 결과 수치까지 쓰게 두지 않는다.
        if not gap_directive and not any(
                getattr(s, "text", "") for s in context_sources):
            gap_directive = material_gap_directive(task_type, fallback=True)
    # 본문이 가리키는데 없는 첨부 — 원료 없음의 **구체형**이다. 어느 파일이
    # 없는지 알면 막연히 자료를 요청하는 대신 그 파일 하나를 집어 요청할 수 있다.
    from .understanding.substance import missing_attachments
    missing = missing_attachments(
        getattr(docs[0], "text", "") or "", docs) if docs else []
    attach_directive = missing_attachment_directive(missing)
    if attach_directive:
        spec["missing_attachments"] = list(missing)
        # 첨부가 비어 원료가 없는 것이므로 원료 없음 경로도 함께 켠다(유형 무관).
        if not gap_directive:
            gap_directive = material_gap_directive(task_type, fallback=True)
    if gap_directive:
        spec["material_gap"] = True
    conversion_hint = conversion_directive(context_sources) if task_type == "presentation" else ""
    if conversion_hint:
        spec["presentation_mode"] = "conversion"
    # v0.2 §4.4: 제공 코드에 TODO류 스켈레톤 마커가 보이면 계약 지시(시그니처·
    # 파일명 불변, TODO 안에서만 작성) 주입 — 스켈레톤 과제의 최대 실패 모드는
    # 명세 오해가 아니라 구조 변경(채점기 0점)이다.
    skeleton_contract = ""
    if _v2 and task_type in ("code", "hdl_lab"):
        import re as _re
        _todo = _re.compile(
            r"//\s*TODO|#\s*TODO|/\*\s*TODO|구현하(?:시오|세요|라)|"
            r"여기에\s*작성|your\s+code\s+here|fill\s+in", _re.I)
        all_text = "\n".join(
            [getattr(d, "text", "") or "" for d in docs]
            + [getattr(s, "text", "") or "" for s in context_sources])
        if _todo.search(all_text):
            from .execution.prompts import skeleton_contract_directive
            skeleton_contract = skeleton_contract_directive()
    # L3 사실 기억(기능 플래그 뒤) — **문체 계열과 다른 통에 넣는다.** voice_hint에
    # 섞으면 모델이 사실을 '이렇게 쓰라는 예시'로 오인하고, 반대로 예시 소재를
    # 사실로 착각한다. 그래서 안전 지시들과 같은 system_extra 쪽에 별도 섹션으로 둔다.
    facts_directive = ""
    from .config import context_depth_active
    if context_depth_active():
        try:
            from .context.facts import facts_block
            facts_directive = facts_block()
        except Exception:
            facts_directive = ""
    system_extra = "\n\n".join(
        b for b in (gate_directive,
                    attach_directive,
                    gap_directive,
                    facts_directive,
                    measured_ban,
                    distributed_spec_directive(context_sources),
                    structured_assignment_directive(docs),
                    assignment_route_directive(assignment_route),
                    type_guidance(task_type, bool(spec.get("material_gap"))),
                    skeleton_directive(task_type, content_elements,
                                       length_target=length_target),
                    skeleton_contract,
                    conversion_hint or decision_directive(task_type),
                    length_directive(length_target),
                    form_directive(docs), profile_hint()) if b)
    # 누적 '내 맥락' 힌트(과거 결정 답 기반, 결정적·LLM 0) — 입력 단계 없이
    # 사용할수록 초안이 이 학생의 소재로 개인화된다. 표본 부족이면 빈 문자열.
    from .context.answer_history import answers_context_hint
    profile_voice_hint = ctx.voice_hint
    voice_hint = profile_voice_hint
    # 톤 레지스터(기능 플래그 뒤) — 과제 유형·라우팅 전략·수신자에서 레지스터를
    # 확정해 결정적으로 직렬화한 규격을 문체 지침 **앞**에 둔다. 규격이 먼저,
    # 통계적 말투 모사가 뒤 — 충돌 시 규격이 읽히는 순서를 고정하기 위함.
    # 플래그 off이거나 해석 실패면 빈 문자열이라 아래 조립이 기존과 동일해진다.
    tone_block = tone_register = tone_source = ""
    from .config import tone_register_active
    if tone_register_active():
        try:
            from .context.tone import resolve_tone
            resolution = resolve_tone(spec, assignment_route,
                                      voice=getattr(ctx, "voice", None),
                                      explicit=str(spec.get("register_key") or ""))
            tone_block = resolution.block
            tone_register, tone_source = resolution.register_key, resolution.source
            spec["register_key"] = tone_register
            spec["register_source"] = tone_source
        except Exception:
            tone_block = tone_register = tone_source = ""
    if tone_block:
        voice_hint = (tone_block + "\n\n" + voice_hint).strip()
    # L2 에피소드(기능 플래그 뒤) — 통짜 요약이 아니라 **유사 사례 검색**.
    # 문체 계열이므로 voice_hint 쪽에 붙는다(사실 계열인 L3와 반대편).
    episode_hits: list = []
    if context_depth_active():
        try:
            from .context.episodes import (episodes_block, find_similar,
                                           query_from_spec)
            episode_hits = find_similar(
                query_from_spec(spec), register_key=tone_register,
                task_type=task_type)
            block = episodes_block(episode_hits)
            if block:
                voice_hint = (voice_hint + "\n\n" + block).strip()
        except Exception:
            episode_hits = []
    ctx_hint = answers_context_hint()
    if ctx_hint:
        voice_hint = (voice_hint + "\n\n" + ctx_hint).strip()
    # 지난 과제의 교수 피드백(결정적 수집분) — 같은 지적 반복 방지 참고.
    if feedback_hint:
        voice_hint = (voice_hint + "\n\n" + feedback_hint).strip()
    # 준수 강제 검증기 — 분량·양식 미달을 '사후 표시'가 아니라 생성 루프 안에서
    # reask로 되돌린다(항목별 델타가 재요청 프롬프트에 실림). mock은 결정적이라
    # 재생성 의미가 없어 기본 제외(불변 규칙 2 보호).
    units: list = []
    if getattr(cfg, "pipeline_mode", "legacy") == "unit":
        # 단위별 경로(UNTIL_PIPELINE=unit) — 근거 원장·계획·단위 검증·부분 재생성.
        # 게이트·원료없음 지시는 안전 규칙이라 경로와 무관하게 전달(없으면 대필
        # 금지 게이트가 unit 경로에서 통째로 우회된다).
        from .execution.unit_pipeline import run_unit_draft
        draft, guard, units = run_unit_draft(
            docs, spec, llm, cfg,
            content_elements=content_elements,
            context_sources=context_sources,
            # 유형·구조화 과제·분산 명세·프로필 지침도 legacy와 같은 경계로 전달.
            # 안전 지시만 잘라 넘기면 새 알고리즘이 unit 경로에서 우회된다.
            system_extra=system_extra,
            # 말투 지침도 legacy와 같은 경계로 — unit이 기본 경로(2026-08-14)라
            # 여기 안 넘기면 실사용자에게 개인화가 전혀 적용되지 않는다.
            # 단 **플래그 뒤에서만** 넘긴다: 지금까지 unit 경로는 voice_hint를 아예
            # 받지 않았으므로, 무조건 넘기면 톤 기능을 끈 사용자의 출력까지 바뀐다.
            voice_hint=voice_hint if tone_block else "")
    else:
        # 대필 금지 게이트 발동 시 산출물은 학습 보조(개념·예제)라 과제의 분량·
        # 양식 요건과 형태가 다르다 — 준수 검증기를 걸면 시스템이 금지한 형태를
        # 검증기가 요구하는 모순으로 reask만 소진한다. 원료 없음 지시도 의도적
        # 빈칸을 남기므로 분량 강제와 충돌(양식 구조 검증은 유지).
        if gate is not None:
            extra_validators = []
        else:
            extra_validators = (_build_enforcement_validators(
                                    cfg, length_target, docs,
                                    material_gap=bool(spec.get("material_gap")))
                                + _quality_validators(cfg, tone_register))
            if gap_directive:
                from .execution.boundary_guard import LengthValidator
                extra_validators = [v for v in extra_validators
                                    if not isinstance(v, LengthValidator)]
        draft, guard = draft_to_boundary(
            docs, spec, llm,
            context_sources=context_sources,
            voice_hint=voice_hint,
            max_reasks=cfg.max_reasks, min_decisions=min_dec,
            system_extra=system_extra,
            extra_validators=extra_validators,
        )
        # 🚫 수치 날조 금지 — legacy 경로 사후 차단(로드맵 Tier2-6). measured_ban은
        # 지침뿐이라 LLM이 무시하면 지어낸 수치가 그대로 남는다. 비활성 전략(hdl_lab도
        # lab_report_cycle(result)도 아님)이면 내부에서 즉시 무영향으로 통과한다.
        from .execution.drafter import enforce_measured_grounding
        draft, guard = enforce_measured_grounding(
            docs, spec, draft, guard, llm,
            context_sources=context_sources, voice_hint=voice_hint,
            system_extra=system_extra,
            strategy=assignment_route.strategy, stage=assignment_route.stage or "",
            min_decisions=min_dec, extra_validators=extra_validators)

    # 4. Boundary / Review — 사람에게 넘길 것.
    prompts = suggest_prompts(draft) if cfg.suggest_prompts else []

    # 근거 자료 범례 — drafter가 Execution에 넣는 source_docs와 같은 1-기반 순서
    # (과제 문서들 → 맥락 자료). 본문의 [자료N] 인용과 매칭된다. 경로는 파일명만.
    sources = [f"과제: {Path(d.source).name}" for d in docs] + \
              [sd.title for sd in context_sources]
    # drafter가 넣는 것과 동일한 목록·순서·제목(과제 docs → 맥락) — 2차 패스 재사용용.
    from .llm.base import SourceDoc
    source_docs = [SourceDoc(title=f"과제: {Path(d.source).name}", text=d.text[:6000])
                   for d in docs] + list(context_sources)

    # 마감일 감지(결정적) — 명세 deadline·원문에서. D-day 표시는 UI/리포트에서.
    from .understanding.deadline import detect_deadline
    deadline = detect_deadline(spec, docs)

    # 민감 상황 판정(결정적·LLM 0) — 플래그와 무관하게 항상 계산한다. 이건 개인화
    # 기능이 아니라 안전장치이고, 되돌릴 수 없는 글(사과·거절·갈등)이 자동으로
    # 확정되는 것을 막는 일이라 기능 플래그 뒤에 숨기지 않는다. 판정은 표시와
    # 제출 게이트에만 쓰이고 생성 결과를 바꾸지 않으므로 기존 출력은 불변이다.
    # 출처 기록 — 실제로 조립된 프롬프트 조각(시스템 지시 + 톤/문체 지침)의 지문을
    # 남긴다. 버전 문자열만 남기면 "버전을 안 올리고 고친" 변경을 놓친다.
    from .persona.versions import resolve_model_version, resolve_prompt_version
    prompt_version = resolve_prompt_version(system_extra, voice_hint)
    model_version = resolve_model_version(usage=usage, config=cfg)

    from .execution.sensitive import detect_sensitive
    try:
        sensitive = detect_sensitive(spec, docs, draft.body)
    except Exception:
        from .execution.sensitive import SensitiveReport
        sensitive = SensitiveReport()

    return Result(documents=docs, spec=spec, draft=draft, guard=guard,
                  suggested_prompts=prompts, context=ctx, sources=sources,
                  source_docs=source_docs,
                  length_target=length_target, deadline=deadline,
                  capture_warnings=capture_warnings,
                  content_elements=content_elements, units=units,
                  assignment_route=assignment_route, llm_usage=usage,
                  voice_applied=bool(profile_voice_hint),
                  tone_block=tone_block, tone_register=tone_register,
                  tone_source=tone_source,
                  needs_approval=sensitive.needs_approval,
                  approval_kinds=list(sensitive.kinds),
                  approval_messages=[f.message for f in sensitive.findings],
                  prompt_version=prompt_version, model_version=model_version,
                  # 올림 — 1ms 미만도 '잰 시간'이다. 절삭하면 0이 되어 미측정과
                  # 같은 값이 되고, 텔레메트리에서도 그 실행만 통째로 빠진다
                  # (telemetry/web.py는 elapsed_ms가 0이면 키를 아예 안 낸다).
                  elapsed_ms=math.ceil((_time.perf_counter() - _started) * 1000))


def _find_form_text(docs) -> "tuple[str, str] | None":
    """과제 문서 중 양식(표·항목 구조)이 감지되는 첫 문서의 (본문, 파일명)."""
    from .capture.formfill import detect_form
    for d in docs or []:
        text = getattr(d, "text", "") or ""
        if detect_form(text).is_form:
            return text, Path(getattr(d, "source", "") or "양식").name
    return None


def _citation_validators(cfg: Config, draft) -> list:
    """finalize 전용 — 초안의 `[자료N]`이 완성본에서 사라지면 reask시킨다.

    mock은 결정적이라 재생성해도 같다(다른 강제 검증기와 같은 이유로 제외).
    """
    if cfg.backend == "mock" and not cfg.enforce_on_mock:
        return []
    from .execution.boundary_guard import CitationPreservationValidator
    body = getattr(draft, "body", "") or ""
    if "[자료" not in body:
        return []
    return [CitationPreservationValidator(body)]


def _build_enforcement_validators(cfg: Config, length_target, docs,
                                  material_gap: bool = False) -> list:
    """감지된 분량 요건·양식으로 생성 루프용 검증기 목록을 만든다(없으면 [])."""
    if cfg.backend == "mock" and not cfg.enforce_on_mock:
        return []  # mock은 결정적 — 재생성해도 같아 강제 무의미(데모·테스트 보호)
    from .execution.boundary_guard import (AssignmentMetaValidator, FormValidator,
                                           LengthValidator)
    out: list = []
    form = _find_form_text(docs)
    # 산출물이 과제 자체를 서술하는 것(마감·과제ID·과목코드)은 프롬프트 규칙만으로
    # 안 잡힌다 — 실측에서 매번 어겼다. 양식 과제는 마감 칸이 정당할 수 있어 뺀다.
    if not form:
        out.append(AssignmentMetaValidator())
    if material_gap:
        # 원료가 없다고 판정한 상태에서 '구체적 후보'는 창작이다(원장 U-3).
        from .execution.boundary_guard import InventedCandidateValidator
        out.append(InventedCandidateValidator())
    if cfg.enforce_length and length_target is not None:
        expected = None
        if form and getattr(length_target, "per_item", ""):
            from .capture.formfill import expected_item_count
            expected = expected_item_count(form[0])
        out.append(LengthValidator(length_target, expected_items=expected))
    if cfg.enforce_form and form:
        out.append(FormValidator(form[0], form_name=form[1]))
    return out


def _quality_validators(cfg: Config, tone_register: str) -> list:
    """생성 품질 안전장치(n-gram 중복·금지 표현). 플래그가 꺼져 있으면 [].

    분량·양식 검증기와 같은 이유로 mock에서는 붙이지 않는다 — 결정적 백엔드라
    재생성해도 같은 출력이 나와 reask만 소진한다.
    """
    if not tone_register:
        return []
    if cfg.backend == "mock" and not cfg.enforce_on_mock:
        return []
    try:
        from .context.episodes import load_episodes
        from .context.tone import REGISTER_PRESETS, load_persona, resolve_tone_spec
        from .execution.quality_guards import build_quality_validators
        if tone_register not in REGISTER_PRESETS:
            return []
        store = load_persona()
        tone = resolve_tone_spec(tone_register,
                                 override=store.registers.get(tone_register))
        # 중복 비교 대상은 '내가 최근에 낸 글' — 에피소드의 최종본을 그대로 쓴다.
        recent = [e.example_body for e in load_episodes()[-5:]]
        return build_quality_validators(tone, recent)
    except Exception:
        return []      # 안전장치 조립 실패가 생성을 막지 않는다


def _all_source_docs(result: Result) -> list:
    """run()이 Execution에 넣은 것과 동일한 SourceDoc 목록(범례 번호와 일치).

    구버전 세션(source_docs 없음)은 과제 docs+맥락으로 재구성해 폴백한다
    (eTL 자동수집분은 복원 불가 — 당시에도 2차 패스엔 안 넘어가던 것).
    """
    sd = getattr(result, "source_docs", None)
    if sd:
        return sd
    from .llm.base import SourceDoc
    docs = [SourceDoc(title=f"과제: {Path(d.source).name}", text=d.text[:6000])
            for d in (result.documents or [])]
    ctx = result.context
    return docs + (ctx.to_sources() if ctx else [])


def _light_model() -> "str | None":
    """보조 패스(제안·점검) 전용 경량 모델 — UNTIL_MODEL_LIGHT 설정 시에만.

    무료 한도(70b TPD 100k)가 병목이라, 주 모델 예산을 초안·최종본에 몰아주는
    티어링. 미설정이면 주 모델 그대로(품질 우선 기본값)."""
    import os
    return os.getenv("UNTIL_MODEL_LIGHT", "").strip() or None


def _trimmed_source_docs(result: Result, cap: int = 1200) -> list:
    """보조 2차 패스(제안·점검)용 자료 목록 — 번호·제목 그대로, 본문만 절단.

    제안·점검은 자료의 요지만 필요한데 전문 재전송이 왕복 토큰을 지배한다(무료
    TPD에서 왕복당 소모의 주범). finalize(본문 생성)는 전문을 유지한다."""
    from .llm.base import SourceDoc
    out = []
    for sd in _all_source_docs(result):
        text = str(getattr(sd, "text", "") or "")
        out.append(SourceDoc(
            title=sd.title,
            text=text[:cap] + (" …(발췌 — 뒷부분 생략)" if len(text) > cap else "")))
    return out


def _metered_for(result: Result, client):
    """2차 패스 클라이언트를 run()과 같은 usage dict에 계측(구세션은 새로 시작)."""
    from .llm.meter import MeteredClient, new_usage
    usage = getattr(result, "llm_usage", None)
    if not isinstance(usage, dict):
        usage = new_usage()
        result.llm_usage = usage
    return MeteredClient(client, usage)


def suggest_decision_answers(
    result: Result,
    config: Config | None = None,
    *,
    my_answers: dict[int, str] | None = None,
    only: list[int] | None = None,
) -> dict[int, dict]:
    """각 결정 지점에 대한 AI 제안 {번호: {answer, why}}을 LLM 1회로 만든다. (모두 수락용)

    경계선 철학: 대신 확정이 아니라 '제안'. 반환값을 사람이 수락/수정한 뒤에야 finalize로 간다.
    결정이 없으면 빈 dict. run()과 동일한 맥락(수업자료·말투)을 제안에도 주입한다.

    my_answers/only: '일부만 답하고 나머지는 맡기기' 용도. 내가 이번 과제에서 채운
    답을 맥락으로 넣고(my_answers), 아직 빈 번호에만 제안한다(only). 둘 다 없으면
    지금까지와 동일하게 전체 결정에 제안한다.
    """
    cfg = config or Config()
    if not result.draft.decisions:
        return {}
    from .execution.suggest_answers import suggest_answers
    llm = _metered_for(result, build_client(cfg.backend, _light_model() or cfg.model))
    ctx = result.context
    # 과거 내 답(히스토리) — 비슷한 결정이 있으면 제안이 그 성향과 일관되게(비치명적).
    past: dict[int, str] = {}
    voice_hint = ctx.voice_hint if ctx else ""
    # 제안문도 초안과 같은 레지스터로 — 제안만 다른 말투면 그대로 붙여 쓸 수 없다.
    tone_block = str(getattr(result, "tone_block", "") or "")
    if tone_block:
        voice_hint = (tone_block + "\n\n" + voice_hint).strip()
    try:
        from .context.answer_history import suggest_from_history, answers_style_hint
        for i, d in enumerate(result.draft.decisions, 1):
            h = suggest_from_history(d.note)
            if h:
                past[i] = h.answer
        # 내 답 문체(종결어미) 힌트 — voice 프로파일과 연계.
        style = answers_style_hint()
        if style:
            voice_hint = (voice_hint + "\n" + style).strip()
    except Exception:
        past = {}
    return suggest_answers(
        result.draft, result.spec, llm,
        # 범례와 동일 번호 체계 + 토큰 다이어트(요지 발췌만 — 제안엔 충분).
        context_sources=_trimmed_source_docs(result) or None,
        voice_hint=voice_hint,
        past_answers=past or None,
        my_answers=my_answers or None,
        only=only,
    )


def review_result(result: Result, config: Config | None = None):
    """초안 완성도 점검 — AI가 자료 활용·빈 곳·결정 적정성을 점검한 ReviewReport 반환.

    생성이 아니라 점검(읽기)이므로 자동 수정은 없다. 무엇을 더 채울지·넘길지는 사람이 본다.
    최종본이 있으면 최종본을, 없으면 초안을 점검한다.
    """
    cfg = config or Config()
    from .execution.review import review_draft
    draft = result.final_draft or result.draft
    if draft is None:
        return None
    llm = _metered_for(result, build_client(cfg.backend, _light_model() or cfg.model))
    ctx = result.context
    # 결정적 사전 점검(마감·분량·인용·결정)을 AI 점검의 근거로 함께 전달.
    from .readiness import assess_readiness, render_readiness_lines
    readiness_lines = render_readiness_lines(assess_readiness(result)) or None
    return review_draft(
        draft, result.spec, llm,
        # 범례와 동일 번호 체계 + 토큰 다이어트(요지 발췌만 — 점검엔 충분).
        context_sources=_trimmed_source_docs(result) or None,
        sources_legend=getattr(result, "sources", None),
        readiness_lines=readiness_lines,
    )


def finalize(
    result: Result,
    answers: dict[int, str],
    config: Config | None = None,
    *,
    channel: str = "cli",
) -> Result:
    """P6 — 사람의 결정 답변을 받아 Execution 2차 패스로 최종 완성본을 만든다.

    `result`(run()의 산출물)와 결정 번호→답변 매핑을 받아, 답이 있는 결정만 본문에
    녹인 최종본을 생성해 `result.final_draft`/`result.final_guard`에 채워 반환한다.
    답변이 하나도 없으면 그대로 반환한다(2차 패스 생략).
    """
    cfg = config or Config()
    pairs = pair_resolved_decisions(result.draft, answers)
    if not pairs:
        return result

    llm = _metered_for(result, build_client(cfg.backend, cfg.model))
    ctx = result.context
    answered = {i for i, _, _ in pairs}
    unanswered = [dp.note for i, dp in enumerate(result.draft.decisions, 1) if i not in answered]
    # 내 답 문체 힌트 — 결정을 본문에 녹일 때 학생의 답변 문체와 일치시키게(비치명적).
    voice_hint = ctx.voice_hint if ctx else ""
    # run()이 확정한 톤 규격을 그대로 재사용한다(재계산 금지 — 초안과 최종본의
    # 레지스터가 갈리면 사용자는 '말투가 중간에 바뀌었다'로 체감한다).
    tone_block = str(getattr(result, "tone_block", "") or "")
    if tone_block:
        voice_hint = (tone_block + "\n\n" + voice_hint).strip()
    try:
        from .context.answer_history import answers_style_hint
        style = answers_style_hint()
        if style:
            voice_hint = (voice_hint + "\n" + style).strip()
    except Exception:
        pass
    final_draft, final_guard = finalize_with_decisions(
        result.draft,
        render_resolved_block(pairs),
        result.spec,
        llm,
        context_sources=_all_source_docs(result) or None,  # 범례와 동일 번호 체계
        voice_hint=voice_hint,
        max_reasks=cfg.max_reasks,
        unanswered_notes=unanswered,
        # 최종본도 같은 준수 강제 — 결정 반영 과정에서 분량·양식이 무너지지 않게.
        # 인용 보존은 **여기에만** 붙인다(run()의 초안은 인용의 출처 자체라
        # 지킬 대상이 없다). 프롬프트 지시만으로는 3회 중 1회밖에 안 살아남았다.
        extra_validators=_build_enforcement_validators(
            cfg, getattr(result, "length_target", None), result.documents)
        + _citation_validators(cfg, result.draft),
    )
    result.final_draft = final_draft
    result.final_guard = final_guard
    _record_memory(result, channel=channel)
    return result


def _record_memory(result: Result, *, channel: str = "cli") -> None:
    """L2 에피소드와 수정 diff를 적립한다(비치명적 — 실패해도 결과에 영향 없음).

    에피소드는 **최종본이 나왔을 때만** 남긴다. 초안만 있는 실행은 '발송본'이 아니라
    few-shot 예시로 쓰면 모델이 자기 출력을 다시 학습한다(에코 챔버).
    수정 이벤트는 edit_source='finalize' — 사람이 직접 고친 diff가 아니라 사람의
    결정 답변이 본문에 녹으며 생긴 변화다. 학습 가중치는 그쪽에서 구분한다.

    channel은 호출한 표면이 알려 준다(web/cli/…). 여기서 고정값을 쓰면 채널 중립
    스키마의 유일한 채널 정보가 처음부터 거짓이 된다 — 웹 사용자까지 전부 'cli'로
    기록되어 나중에 채널별 비교를 할 수 없다.
    """
    draft_body = getattr(result.draft, "body", "") or ""
    final_body = getattr(result.final_draft, "body", "") or ""
    if not final_body:
        return
    register = str(getattr(result, "tone_register", "") or "")
    task_type = str((result.spec or {}).get("task_type") or "")
    try:
        from .context.edit_events import record_edit_event
        record_edit_event(draft_body, final_body, edit_source="finalize",
                          register_key=register, task_type=task_type)
    except Exception:
        pass
    # 채널 중립 페르소나 이벤트(PHASE 3) — 채널이 무엇이든 같은 스키마로 남긴다.
    # 이 로그는 **원문 파이프**(사용자 소유)다. 비식별 텔레메트리로 나가지 않는다.
    try:
        from .persona.events import event_from_result, record_event
        # 모델 정보는 finalize 2차 패스에서 갱신될 수 있어 여기서 다시 해석한다.
        from .persona.versions import resolve_model_version
        fresh_model = resolve_model_version(result)
        if fresh_model:
            result.model_version = fresh_model
        record_event(event_from_result(result, channel=channel))
    except Exception:
        pass
    try:
        from .config import context_depth_active
        if not context_depth_active():
            return
        from .context.episodes import query_from_spec, record_episode
        record_episode(query_from_spec(result.spec), draft_body, final_body,
                       register_key=register, task_type=task_type)
    except Exception:
        pass
