"""until의 **읽기 전용** 기능을 MCP(stdio JSON-RPC 2.0)로 노출한다 — 의존성 0, LLM 0.

왜 이 모듈이 있나. until을 완제품으로 경쟁시키면 진다 — 브라우저를 그대로 모는
에이전트는 토큰조차 필요 없다. 하지만 **eTL 접근과 과목별 라우팅 규칙은 until만
갖고 있다.** 그러니 에이전트와 싸우지 말고 에이전트가 until을 타게 한다.
이 모듈은 그 부품 표면이다.

노출 원칙 — 타협 불가:
  - **생성 도구를 넣지 않는다.** 초안·문장 생성은 MCP에 없다. LLM 호출 0건.
    `enforce_ai_use_policy` 게이트는 생성 경로에만 있으므로 여기서는 건드리지 않는다.
  - 판정 로직은 전부 **결정적**이다. 같은 입력(같은 eTL 상태) → 같은 출력.
    난수·시각 의존은 D-day 계산(오늘 날짜) 하나뿐이고 그건 값의 정의다.
  - **토큰을 저장하지 않는다.** eTL 토큰은 호출 때마다 환경변수에서 읽고 버린다.
    디스크에 쓰지 않고, 도구 출력에도 절대 싣지 않는다.
  - 기존 모듈을 **호출만** 한다. 판정은 전부 원래 있던 코드가 한다:
      until_inbox      → `runtime.etl_input`, `inbox_policy`
      until_assignment → `runtime.spec_builder`, `understanding.{deadline,length_target,requirements}`
      until_materials  → `context.etl_materials`
      until_route      → `context.assignment_router`
      until_readiness  → `readiness`
      until_series     → `context.series`
      until_control_tower → `practice_audit`
      until_semester   → `inbox_policy`
      until_brief      → `context.{etl_announcements,weekly_brief}`

전송은 MCP stdio 규약 그대로 **줄바꿈으로 구분된 JSON-RPC**다(Content-Length 헤더가
아니다). 표준 라이브러리만으로 충분해서 `mcp` SDK를 넣지 않았다 — `pyproject.toml`의
`dependencies = []`는 불변이다.

진입점: `python -m until.mcp_server` (stdio)

⚠ stdout은 프로토콜 전용이다. 어떤 하위 모듈이든 print를 하면 스트림이 깨지므로
도구 실행 구간의 stdout을 stderr로 돌려 둔다(`_run_tool`).
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from typing import Any, Callable

SERVER_NAME = "until"
SERVER_VERSION = "0.1"

#: 우리가 아는 MCP 프로토콜 버전. 클라이언트가 이 중 하나를 요구하면 그대로 돌려주고
#: (도구만 쓰는 서버라 이 범위에서 의미 차이가 없다), 모르는 값이면 우리 기본값을 준다.
KNOWN_PROTOCOL_VERSIONS = (
    "2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "2026-07-28",
)
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

#: 도구 출력에 실어 보낼 발췌 상한. 에이전트 컨텍스트를 통째로 먹지 않게 한다.
EXCERPT_CHARS = 1200


class ToolError(RuntimeError):
    """사람이 읽을 수 있는 도구 실패 — 크래시가 아니라 isError 응답으로 나간다."""


# ── eTL 접속 (토큰은 읽고 버린다) ────────────────────────────────────────
def _etl_token() -> str:
    """WS 토큰 → Canvas 토큰 순. `web.etl_token()`과 같은 규칙, web은 임포트하지 않는다."""
    return (os.getenv("UNTIL_ETL_WS_TOKEN") or os.getenv("UNTIL_CANVAS_TOKEN") or "").strip()


def _ws_mode() -> bool:
    """Moodle WS 어댑터를 쓸지. WS 토큰이 있거나 UNTIL_ETL_WS=1이면 WS."""
    return bool((os.getenv("UNTIL_ETL_WS_TOKEN") or "").strip()) or \
        (os.getenv("UNTIL_ETL_WS", "") or "").strip() == "1"


def _adapter():
    """eTL 어댑터. 토큰이 없으면 크래시가 아니라 무엇을 하면 되는지 말한다."""
    token = _etl_token()
    if not token:
        raise ToolError(
            "eTL 액세스 토큰이 없습니다. eTL › 계정 › 설정 › '+ 새 액세스 토큰'에서 "
            "발급한 뒤 UNTIL_CANVAS_TOKEN 환경변수로 넘기세요(WS 모드는 "
            "UNTIL_ETL_WS_TOKEN). MCP 서버는 토큰을 저장하지 않습니다.")
    from .runtime.etl_input import build_adapter
    try:
        return build_adapter(token, ws=_ws_mode())
    except Exception as exc:
        raise ToolError(str(exc)) from exc


def _base_url(adapter=None) -> str:
    from .runtime.etl_input import etl_base_url
    return etl_base_url(adapter)


def _collect(url: str, dest, materials: int = 0):
    """과제 1건 수집 → (EtlAssignment, documents). 실패는 읽을 수 있는 사유로."""
    from .runtime.etl_input import EtlInputError, collect
    from .capture.ingest import ingest_all_with_warnings
    adapter = _adapter()
    try:
        got = collect(adapter, url, dest, materials=materials,
                      base_url=_base_url(adapter))
    except EtlInputError as exc:
        raise ToolError(str(exc)) from exc
    docs, warnings = ingest_all_with_warnings([str(p) for p in got.files])
    return adapter, got, docs, warnings


def _excerpt(text: str, limit: int = EXCERPT_CHARS) -> str:
    flat = " ".join(str(text or "").split())
    return flat[:limit] + (" …(발췌)" if len(flat) > limit else "")


# ── 도구 구현 ────────────────────────────────────────────────────────────
def tool_inbox(args: dict) -> dict:
    """eTL 과제 목록 + D-day. 선택·정렬 정책은 `inbox_policy`가 그대로 한다."""
    from .inbox_policy import (
        dday_label, filter_sort_inbox, is_past_due, item_kind,
    )
    from .runtime.etl_input import EtlInputError, list_assignments

    status = str(args.get("status") or "todo")
    if status not in ("all", "todo", "done"):
        raise ToolError("status는 all | todo | done 중 하나여야 합니다.")
    kind = str(args.get("kind") or "assignment")
    if kind not in ("assignment", "gradebook", "all"):
        raise ToolError("kind는 assignment | gradebook | all 중 하나여야 합니다.")
    hide_past = bool(args.get("hide_past", True))
    limit = max(1, min(int(args.get("limit") or 50), 200))

    adapter = _adapter()
    try:
        items = list_assignments(adapter, base_url=_base_url(adapter))
    except EtlInputError as exc:
        raise ToolError(str(exc)) from exc

    # 실측 148항목 중 46건이 성적부 열(`M3`·`출석 점수`·`중간고사`)이다. 이것들이
    # 과제로 섞여 나가면 이 도구를 부르는 에이전트가 그대로 믿는다 — 인박스의
    # 신뢰는 첫 응답에서 결정된다. 기본은 과제만, `kind`로 꺼내 볼 수 있다.
    picked = filter_sort_inbox(items, status=status, hide_past=hide_past,
                               term=str(args.get("term") or ""), sort="due",
                               hide_gradebook=(kind == "assignment"))
    if kind == "gradebook":
        picked = [i for i in picked if item_kind(i) == "gradebook"]
    out = []
    for item in picked[:limit]:
        label, urgent = dday_label(getattr(item, "due_at", ""))
        out.append({
            "assignment_id": str(getattr(item, "id", "")),
            "course_id": str(getattr(item, "course_id", "")),
            "course_name": getattr(item, "course_name", ""),
            "title": getattr(item, "title", ""),
            "due_at": getattr(item, "due_at", "") or "",
            "dday": label,
            "urgent": urgent,
            "past_due": is_past_due(getattr(item, "due_at", "")),
            "submitted": bool(getattr(item, "submitted", False)),
            "actionable": bool(getattr(item, "actionable", True)),
            "kind": item_kind(item),
            "url": getattr(item, "url", "") or "",
        })
    n_gradebook = sum(1 for i in items if item_kind(i) == "gradebook")
    return {"count": len(out), "total_found": len(items),
            "gradebook_rows": n_gradebook,
            "filtered_out": max(0, len(picked) - len(out)), "items": out}


def tool_assignment(args: dict) -> dict:
    """과제 1건의 명세·요구사항·분량 요건·마감·첨부 수. 전부 결정적 판정기."""
    from .context.assignment_router import route_documents
    from .runtime.spec_builder import build_runtime_spec
    from .understanding.deadline import detect_deadline
    from .understanding.length_target import detect_length_target
    from .understanding.requirements import extract_content_elements

    url = str(args.get("url") or "").strip()
    if not url:
        raise ToolError("url(eTL 과제 페이지 주소)이 필요합니다.")

    with tempfile.TemporaryDirectory(prefix="until_mcp_") as tmp:
        _, got, docs, warnings = _collect(url, tmp, materials=0)
        if not docs:
            raise ToolError("과제 문서를 하나도 읽지 못했습니다(첨부 권한·형식 확인).")
        spec = build_runtime_spec(docs, title=got.title)
        route = route_documents(spec, docs)
        length = detect_length_target(spec, docs)
        deadline = detect_deadline(spec, docs)
        # llm=None → 결정적 폴백 경로만 탄다(LLM 호출 0).
        elements = extract_content_elements(spec, docs, llm=None)
        body = str(getattr(docs[0], "text", "") or "")

        return {
            "assignment_id": got.assignment_id,
            "course_id": got.course_id,
            "course_name": got.course_name,
            "title": got.title,
            "due_at": got.due_at or "",
            "page_url": got.page_url,
            "spec": {
                "goal": spec.get("goal", ""),
                "course": spec.get("course", ""),
                "required_sections": list(spec.get("required") or []),
                "citation_style": spec.get("citation_style", ""),
                "min_chars": spec.get("min_chars") or 0,
                "requires_citation": bool(spec.get("requires_citation")),
            },
            "content_elements": [e.to_dict() for e in elements],
            "length_target": (
                {"unit": length.unit, "min": length.min, "max": length.max,
                 "mode": length.mode, "per_item": length.per_item,
                 "raw": length.raw, "describe": length.describe()}
                if length is not None else None),
            "deadline": (
                {"due": deadline.due.isoformat(), "had_year": deadline.had_year,
                 "time_str": deadline.time_str, "extended": deadline.extended,
                 "raw": deadline.raw}
                if deadline is not None else None),
            "route": _route_dict(route),
            "attachment_count": len(got.files),
            "skipped": list(got.skipped),
            "capture_warnings": list(warnings),
            "body_excerpt": _excerpt(body),
        }


def tool_materials(args: dict) -> dict:
    """그 과제와 키워드가 겹치는 과목 강의자료 상위 N건(+선택적 본문 발췌)."""
    from .context.etl_materials import (
        collect_material_refs, fetch_material_texts, rank_materials,
    )
    from .context.retrieval import keywords_from_spec

    url = str(args.get("url") or "").strip()
    if not url:
        raise ToolError("url(eTL 과제 페이지 주소)이 필요합니다.")
    top = max(1, min(int(args.get("top") or 5), 20))
    with_text = bool(args.get("with_text", False))

    with tempfile.TemporaryDirectory(prefix="until_mcp_") as tmp:
        adapter, got, docs, _ = _collect(url, tmp, materials=0)
        if not got.course_id:
            raise ToolError("과목 id를 해석하지 못해 강의자료를 찾을 수 없습니다.")
        # 웹 경로(`web._pick`)와 같은 키워드 형태 — 제목만 쓰면 매칭이 너무 얇다.
        body = str(getattr(docs[0], "text", "") or "") if docs else ""
        spec_like = {"deliverable": "과제", "goal": got.title,
                     "requirements": [body[:800]]}
        refs = collect_material_refs(adapter, got.course_id, _base_url(adapter))
        hits = rank_materials(refs, keywords_from_spec(spec_like), k=top)
        texts = fetch_material_texts(adapter, hits, top=min(top, 3)) if with_text else {}
        return {
            "course_id": got.course_id,
            "count": len(hits),
            "total_materials": len(refs),
            "items": [
                {"name": h.name, "score": h.score, "matched": list(h.matched),
                 "url": h.url, "excerpt": _excerpt(texts.get(h.name, ""))}
                for h in hits
            ],
        }


def tool_route(args: dict) -> dict:
    """과제 유형·전략 분류 + 왜 그렇게 분류했는지. 네트워크·토큰 불필요."""
    from .context.assignment_router import route_assignment
    title = str(args.get("title") or "").strip()
    if not title:
        raise ToolError("title(과제 제목)이 필요합니다.")
    names = args.get("attachment_names") or []
    if not isinstance(names, list):
        raise ToolError("attachment_names는 문자열 배열이어야 합니다.")
    route = route_assignment(
        title=title,
        description=str(args.get("description") or ""),
        attachment_names=[str(n) for n in names],
        course_name=str(args.get("course_name") or ""))
    return _route_dict(route)


def tool_readiness(args: dict) -> dict:
    """초안 텍스트를 받아 마감·분량·인용·남은 결정을 점검. 네트워크·토큰 불필요."""
    from .boundary.models import Draft
    from .context.assignment_router import route_documents
    from .execution.boundary_guard import GuardReport
    from .types import Result  # NOT .pipeline — pipeline.py pulls in llm.base
    from .readiness import assess_readiness
    from .runtime.spec_builder import build_runtime_spec
    from .understanding.deadline import detect_deadline
    from .understanding.length_target import detect_length_target

    body = str(args.get("draft") or "")
    if not body.strip():
        raise ToolError("draft(점검할 초안 본문)가 필요합니다.")
    assignment_text = str(args.get("assignment_text") or "")

    docs = []
    if assignment_text.strip():
        from .capture.models import Document
        docs = [Document(source="assignment", kind="text", text=assignment_text)]
    spec = build_runtime_spec(docs, title=str(args.get("title") or "")) if docs else {}
    draft = Draft.from_text(body)
    result = Result(
        documents=docs, spec=spec, draft=draft,
        guard=GuardReport(passed=True, attempts=1, reasks=0),
        source_docs=[],
        length_target=detect_length_target(spec, docs) if spec else None,
        deadline=detect_deadline(spec, docs) if spec else None,
        assignment_route=route_documents(spec, docs) if docs else None,
    )
    out = assess_readiness(result).to_dict()
    out["n_decisions"] = draft.n_decisions
    out["decisions"] = [d.note for d in draft.decisions]
    out["crossed_boundary"] = draft.crossed_boundary
    return out


def tool_series(args: dict) -> dict:
    """같은 시리즈(회차·단계)의 내 지난 제출물 교차참조."""
    from .context.series import (
        find_predecessors, find_stage_predecessors, rows_from_canvas_submissions,
        series_key, stage_stem,
    )
    title = str(args.get("title") or "").strip()
    course_id = str(args.get("course_id") or "").strip()
    if not title or not course_id:
        raise ToolError("title(과제 제목)과 course_id가 모두 필요합니다.")
    k = max(1, min(int(args.get("k") or 2), 5))

    adapter = _adapter()
    if not hasattr(adapter, "my_submissions_json"):
        raise ToolError("이 eTL 어댑터는 내 제출물 조회를 지원하지 않습니다"
                        "(Canvas 토큰 모드에서만 동작).")
    try:
        raw = adapter.my_submissions_json(course_id, _base_url(adapter))
    except Exception as exc:
        from .user_errors import user_error_message
        raise ToolError(user_error_message(exc, "지난 제출물을 조회")) from exc

    rows = rows_from_canvas_submissions(raw)
    hits = find_predecessors(title, rows, k=k)
    matched_by = "series"
    if not hits:
        hits = find_stage_predecessors(title, rows, k=k)
        matched_by = "stage" if hits else "none"
    return {
        "series_key": series_key(title),
        "stage_stem": stage_stem(title),
        "matched_by": matched_by,
        "count": len(hits),
        "scanned": len(rows),
        "items": [
            {"title": h.get("title", ""), "submitted_at": h.get("submitted_at", ""),
             "chars": len(str(h.get("body") or "")),
             "excerpt": _excerpt(h.get("body", ""))}
            for h in hits
        ],
    }


def tool_control_tower(args: dict) -> dict:
    """과제 1건의 제출 가능 상태 — 필수 첨부·AI 사용 규정·분량 신호를 findings로.

    `control_tower.inspect_assignment`가 아니라 `practice_audit.audit_assignment`를
    쓴다 — inspect_assignment는 `AssignmentPolicy`(policy_compiler로 강의계획서를
    컴파일한 결과)·`AcademicGraph`(과목 전체 이력 그래프)·`student_memory` 세 가지를
    미리 만들어 받아야 하는데, 이 셋을 만드는 코드는 제품 전체에서 **어디에도 없다**
    (테스트에서만 손으로 만들어 넣는다) — 재사용이 아니라 새 파이프라인을 짜는
    일이 된다. `audit_assignment`는 반대로 `web.py`가 이미 실제로 쓰는 검증된
    경로라 그대로 재사용했다. eTL 토큰 필요.
    """
    from .practice_audit import audit_assignment

    url = str(args.get("url") or "").strip()
    if not url:
        raise ToolError("url(eTL 과제 페이지 주소)이 필요합니다.")

    with tempfile.TemporaryDirectory(prefix="until_mcp_") as tmp:
        _, got, docs, _ = _collect(url, tmp, materials=0)
        body = str(getattr(docs[0], "text", "") or "") if docs else ""
        audit = audit_assignment(body, attachment_count=len(got.files))
        findings = ([{"severity": "block", "message": m} for m in audit.blockers]
                   + [{"severity": "warn", "message": m} for m in audit.warnings])
        return {
            "assignment_id": got.assignment_id,
            "submit_state": "blocked" if audit.blockers else "review",
            "policy": audit.policy,
            "body_present": audit.body_present,
            "deadline_present": audit.deadline_present,
            "formats": list(audit.formats),
            "attachment_count": audit.attachment_count,
            "findings": findings,
        }


def tool_semester(args: dict) -> dict:
    """학기 전체 상태 한 응답 — 과목별 과제 수·임박 건수·성적부 열 수·다음 마감.

    `until_inbox`가 쓰는 것과 같은 판정기(`inbox_policy`)로 과목별 집계만 새로
    만든다 — 새 판정 로직 없음. eTL 토큰 필요.
    """
    from .inbox_policy import dday_label, is_past_due, item_kind
    from .runtime.etl_input import EtlInputError, list_assignments

    adapter = _adapter()
    try:
        items = list_assignments(adapter, base_url=_base_url(adapter))
    except EtlInputError as exc:
        raise ToolError(str(exc)) from exc

    by_course: dict = {}
    for item in items:
        cid = str(getattr(item, "course_id", ""))
        rec = by_course.setdefault(cid, {
            "course_id": cid, "course_name": getattr(item, "course_name", "") or cid,
            "assignment_count": 0, "gradebook_count": 0,
            "urgent_count": 0, "next_due": "",
        })
        if item_kind(item) == "gradebook":
            rec["gradebook_count"] += 1
            continue
        rec["assignment_count"] += 1
        if getattr(item, "submitted", False) or is_past_due(getattr(item, "due_at", None)):
            continue
        _, urgent = dday_label(getattr(item, "due_at", ""))
        if urgent:
            rec["urgent_count"] += 1
        due = getattr(item, "due_at", "") or ""
        if due and (not rec["next_due"] or due < rec["next_due"]):
            rec["next_due"] = due
    courses = sorted(by_course.values(),
                     key=lambda c: (c["next_due"] or "9999", c["course_name"]))
    return {
        "course_count": len(courses),
        "total_assignments": sum(c["assignment_count"] for c in courses),
        "total_gradebook_rows": sum(c["gradebook_count"] for c in courses),
        "total_urgent": sum(c["urgent_count"] for c in courses),
        "courses": courses,
    }


def tool_brief(args: dict) -> dict:
    """과목 주차 브리프 — 그 과제와 같은 주차 공지의 제목·첨부 목록(결정적 매칭).

    주차 매칭은 `context.weekly_brief`(제목의 'N주차' 패턴만 결정적으로 본다).
    eTL 토큰 필요(공지 조회 지원 어댑터에서만).
    """
    from .capture.sources.models import CourseRef
    from .context.etl_announcements import collect_related_announcements
    from .context.weekly_brief import readable_attachments, week_announcements, week_of

    url = str(args.get("url") or "").strip()
    if not url:
        raise ToolError("url(eTL 과제 페이지 주소)이 필요합니다.")

    with tempfile.TemporaryDirectory(prefix="until_mcp_") as tmp:
        adapter, got, docs, _ = _collect(url, tmp, materials=0)
        week = week_of(got.title or "")
        if week is None:
            return {"week": None, "course_id": got.course_id, "count": 0, "items": [],
                    "message": "과제 제목에서 주차를 못 찾았습니다(예: 'N주차 소감문')."}
        if not got.course_id or not hasattr(adapter, "collect_announcements"):
            raise ToolError("이 eTL 어댑터는 공지 조회를 지원하지 않습니다.")
        body = str(getattr(docs[0], "text", "") or "") if docs else ""
        spec_like = {"deliverable": "과제", "goal": got.title, "requirements": [body[:800]]}
        course = CourseRef(id=got.course_id, name=got.course_name or "")
        anns = collect_related_announcements(adapter, course, spec_like, k=5)
        weekly = week_announcements(anns, week)
        items = [
            {"subject": getattr(a, "subject", "") or "",
             "created_iso": getattr(a, "created_iso", "") or "",
             "url": getattr(a, "url", "") or "",
             "excerpt": _excerpt(getattr(a, "body", "") or ""),
             "attachments": [str(getattr(x, "name", "") or "")
                            for x in readable_attachments(a)]}
            for a in weekly
        ]
        return {"week": week, "course_id": got.course_id, "count": len(items),
                "items": items}


def _route_dict(route) -> dict:
    if route is None:
        return {}
    return {
        "strategy": route.strategy,
        "reason": route.reason,
        "required_evidence": list(route.required_evidence),
        "questions": list(route.questions),
        "actionable": route.actionable,
        "stage": route.stage,
    }


# ── 도구 목록 (MCP tools/list 스키마) ────────────────────────────────────
def _schema(props: dict, required: tuple = ()) -> dict:
    return {"type": "object", "properties": props, "required": list(required)}


_STR = {"type": "string"}
_BOOL = {"type": "boolean"}
_INT = {"type": "integer"}

TOOLS: tuple[tuple[str, str, dict, Callable[[dict], dict]], ...] = (
    (
        "until_inbox",
        "eTL 과제 목록을 마감 임박순으로. 각 건에 D-day·임박 여부·제출 여부·"
        "종류(kind)·'제출할 것이 있는 과제인지(actionable)'가 붙는다. "
        "성적부 열(중간고사·출석 점수·M1 같은 성적 표시 항목)은 기본으로 빠진다 — "
        "eTL 항목의 약 3분의 1이 그것이다. eTL 토큰 필요.",
        _schema({
            "status": {**_STR, "enum": ["all", "todo", "done"],
                       "description": "기본 todo(미제출만)"},
            "kind": {**_STR, "enum": ["assignment", "gradebook", "all"],
                     "description": "기본 assignment(성적부 열 제외)"},
            "hide_past": {**_BOOL, "description": "마감 지난 과제 숨김(기본 true)"},
            "term": {**_STR, "description": "학기 이름으로 거르기(예: 2026-2)"},
            "limit": {**_INT, "description": "최대 건수(1~200, 기본 50)"},
        }),
        tool_inbox,
    ),
    (
        "until_assignment",
        "과제 1건의 명세·요구사항·제약·분량 요건·마감·첨부 수와 본문 발췌. "
        "판정은 전부 결정적(LLM 0). eTL 토큰 필요.",
        _schema({"url": {**_STR, "description": "eTL 과제 페이지 주소"}}, ("url",)),
        tool_assignment,
    ),
    (
        "until_materials",
        "그 과제와 키워드가 겹치는 과목 강의자료 상위 N건. with_text=true면 "
        "상위 자료의 본문 발췌까지 내려받아 담는다. eTL 토큰 필요.",
        _schema({
            "url": {**_STR, "description": "eTL 과제 페이지 주소"},
            "top": {**_INT, "description": "상위 몇 건(1~20, 기본 5)"},
            "with_text": {**_BOOL, "description": "본문 발췌까지(기본 false — 제목만)"},
        }, ("url",)),
        tool_materials,
    ),
    (
        "until_route",
        "과제 제목·본문·첨부명만으로 처리 전략을 분류하고 그 근거와 "
        "부족 정보 질문을 돌려준다. 네트워크·토큰 불필요, 완전 결정적.",
        _schema({
            "title": {**_STR, "description": "과제 제목"},
            "description": {**_STR, "description": "과제 본문(있으면 정확도가 오른다)"},
            "attachment_names": {"type": "array", "items": _STR,
                                 "description": "첨부 파일명 목록"},
            "course_name": {**_STR, "description": "과목명"},
        }, ("title",)),
        tool_route,
    ),
    (
        "until_readiness",
        "초안 본문을 받아 제출 전 점검(마감·분량·인용·남은 결정·경계선)을 돌려준다. "
        "초안을 생성하지 않는다 — 받은 텍스트를 검사만 한다. 네트워크·토큰 불필요.",
        _schema({
            "draft": {**_STR, "description": "점검할 초안 본문"},
            "assignment_text": {**_STR,
                                "description": "과제 지시문(주면 분량·마감까지 대조)"},
            "title": {**_STR, "description": "과제 제목"},
        }, ("draft",)),
        tool_readiness,
    ),
    (
        "until_series",
        "같은 시리즈('N주차 소감문')나 같은 단계 줄기('서론 작성'↔'서론 수정')의 "
        "내 지난 제출물을 찾아 교차참조한다. eTL 토큰 필요(Canvas 모드).",
        _schema({
            "title": {**_STR, "description": "지금 과제의 제목"},
            "course_id": {**_STR, "description": "eTL 과목 id"},
            "k": {**_INT, "description": "최대 건수(1~5, 기본 2)"},
        }, ("title", "course_id")),
        tool_series,
    ),
    (
        "until_control_tower",
        "과제 1건의 제출 가능 상태 — 필수 첨부·AI 사용 규정 판정·분량/마감 신호를 "
        "severity(block/warn)별 findings로 돌려준다. 판정만 하고 제출하지 않는다. "
        "eTL 토큰 필요.",
        _schema({"url": {**_STR, "description": "eTL 과제 페이지 주소"}}, ("url",)),
        tool_control_tower,
    ),
    (
        "until_semester",
        "학기 전체 상태를 한 응답으로 — 과목별 과제 수·성적부 열 수·마감 임박 건수·"
        "다음 마감을 집계한다. `until_inbox`와 같은 판정기를 과목 단위로 묶는다. "
        "eTL 토큰 필요.",
        _schema({}),
        tool_semester,
    ),
    (
        "until_brief",
        "그 과제와 같은 주차('N주차') 공지의 제목·발췌·첨부 목록을 결정적으로 "
        "매칭해 돌려준다. 주차마다 연사·주제가 바뀌는 과목에서 '이번 주 뭘 다뤘는지'가 "
        "여기에만 있는 경우가 많다. eTL 토큰 필요(공지 조회 지원 어댑터에서만).",
        _schema({"url": {**_STR, "description": "eTL 과제 페이지 주소"}}, ("url",)),
        tool_brief,
    ),
)

_HANDLERS = {name: fn for name, _d, _s, fn in TOOLS}


def tool_definitions() -> list:
    return [{"name": name, "description": desc, "inputSchema": schema}
            for name, desc, schema, _fn in TOOLS]


# ── JSON-RPC ────────────────────────────────────────────────────────────
def _negotiate(requested: Any) -> str:
    want = str(requested or "").strip()
    return want if want in KNOWN_PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION


def _run_tool(name: str, arguments: dict) -> dict:
    """도구 1회 실행 → MCP CallToolResult. stdout 오염을 막고 실패를 isError로.

    하위 모듈이 print를 하면 JSON-RPC 스트림이 깨진다 — stdout을 stderr로 돌린다.
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"content": [{"type": "text", "text": f"알 수 없는 도구: {name}"}],
                "isError": True}
    try:
        with contextlib.redirect_stdout(sys.stderr):
            payload = handler(arguments if isinstance(arguments, dict) else {})
    except ToolError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    except Exception as exc:  # 크래시 대신 읽을 수 있는 실패로 — 서버는 살아 있어야 한다
        from .user_errors import user_error_message
        return {"content": [{"type": "text",
                             "text": user_error_message(exc, f"{name}을 실행")}],
                "isError": True}
    text = json.dumps(payload, ensure_ascii=False, indent=1, default=str)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def handle(message: dict) -> dict | None:
    """요청 1건 → 응답 1건. 알림(id 없음)이면 None(응답하지 않는다)."""
    method = str(message.get("method") or "")
    mid = message.get("id")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}

    if mid is None:                      # 알림 — notifications/initialized 등
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, msg):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": msg}}

    if method == "initialize":
        return ok({
            "protocolVersion": _negotiate(params.get("protocolVersion")),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "until은 서울대 eTL 과제·마감·강의자료·과목별 라우팅을 읽어 오는 "
                "읽기 전용 부품입니다. 초안·문장을 생성하지 않습니다 — 생성은 "
                "호출하는 쪽이 합니다. 과제 목록은 until_inbox로 시작하세요."),
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": tool_definitions()})
    if method == "tools/call":
        name = str(params.get("name") or "")
        return ok(_run_tool(name, params.get("arguments") or {}))
    return err(-32601, f"Method not found: {method}")


def serve(stdin=None, stdout=None) -> int:
    """줄바꿈 구분 JSON-RPC 루프. EOF면 정상 종료."""
    src = stdin if stdin is not None else sys.stdin
    dst = stdout if stdout is not None else sys.stdout
    for line in src:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            _write(dst, {"jsonrpc": "2.0", "id": None,
                         "error": {"code": -32700, "message": "Parse error"}})
            continue
        if not isinstance(message, dict):
            _write(dst, {"jsonrpc": "2.0", "id": None,
                         "error": {"code": -32600, "message": "Invalid Request"}})
            continue
        response = handle(message)
        if response is not None:
            _write(dst, response)
    return 0


def _write(dst, payload: dict) -> None:
    dst.write(json.dumps(payload, ensure_ascii=False) + "\n")
    dst.flush()


def main(argv=None) -> int:
    from .config import load_dotenv
    load_dotenv()
    argv = list(sys.argv[1:] if argv is None else argv)
    # 다른 분기보다 먼저 재설정한다 — Windows 콘솔(cp949 등 비-UTF8 코드페이지)에서
    # 한글 섞인 출력(도구 설명·setup 경로·JSON-RPC 응답)이 UnicodeEncodeError로 죽는
    # 걸 실제로 npm 패키지 로컬 설치 후 재현했다. --list-tools·setup만 골라 뒤에서
    # 처리하면 그 경로들이 이 보호를 못 받는다.
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if argv and argv[0] == "setup":     # Claude Code·Codex 설정에 등록만(토큰 없음)
        from .setup import run as setup_run
        setup_run()
        return 0
    if "--list-tools" in argv:          # 사람이 붙이기 전에 확인하는 용도
        print(json.dumps(tool_definitions(), ensure_ascii=False, indent=1))
        return 0
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
