"""Signed JSON persistence for web sessions.

The conversion below is intentionally explicit.  Adding a field or a new
runtime type to :class:`Result` must also update this module; unsupported
objects fail loudly instead of being silently omitted.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import date
from pathlib import Path
from typing import Any, Optional

from .boundary.models import DecisionPoint, Draft, Resolution
from .capture.models import Document, Section
from .capture.sources.moodle_ws import Announcement
from .context.assignment_router import AssignmentRoute
from .context.bundle import ContextBundle
from .context.evidence import EvidenceItem, EvidenceLedger
from .context.etl_materials import MaterialHit
from .context.inquiry_assignment import InquiryAssignment
from .context.retrieval import Hit
from .context.voice import VoiceProfile
from .execution.boundary_guard import GuardReport
from .execution.content_plan import PlanItem, UnitPlan
from .execution.review import ReviewReport
from .execution.units import ResponseUnit
from .llm.base import SourceDoc
from .pipeline import Result
from .understanding.deadline import Deadline
from .understanding.length_target import LengthTarget
from .understanding.requirements import ContentElement
from .understanding.skeleton import SkeletonSlot

VERSION = 2
_KEY_PATH = Path("_until_work/.session_key")


def _plain(value: Any) -> Any:
    """Copy JSON-native data while rejecting every unknown value type."""
    if value is None or type(value) in (bool, int, float, str):
        return value
    if type(value) is list:
        return [_plain(item) for item in value]
    if type(value) is dict:
        if not all(type(key) is str for key in value):
            raise TypeError("JSON object keys must be strings")
        return {key: _plain(item) for key, item in value.items()}
    raise TypeError(f"unsupported session value: {type(value).__name__}")


def _section(value: Section) -> dict:
    return {"heading": value.heading, "text": value.text}


def _document(value: Document) -> dict:
    return {"source": value.source, "kind": value.kind, "text": value.text,
            "sections": [_section(item) for item in value.sections],
            "n_chars": value.n_chars, "n_tokens_est": value.n_tokens_est}


def _decision(value: DecisionPoint) -> dict:
    resolution = value.resolution.value if value.resolution is not None else None
    return {"note": value.note, "context": value.context, "resolution": resolution,
            "human_input": value.human_input}


def _draft(value: Draft) -> dict:
    return {"body": value.body, "decisions": [_decision(item) for item in value.decisions]}


def _guard(value: GuardReport) -> dict:
    return {"passed": value.passed, "attempts": value.attempts, "reasks": value.reasks,
            "final_errors": list(value.final_errors),
            "history": [list(item) for item in value.history]}


def _voice(value: VoiceProfile) -> dict:
    return {"ending_style": value.ending_style, "avg_sentence_len": value.avg_sentence_len,
            "frequent_terms": list(value.frequent_terms), "uses_emoji": value.uses_emoji,
            "exclaim_ratio": value.exclaim_ratio, "n_samples": value.n_samples,
            "llm_summary": value.llm_summary}


def _hit(value: Hit) -> dict:
    return {"document": _document(value.document), "score": value.score,
            "matched": list(value.matched)}


def _context(value: ContextBundle) -> dict:
    return {"course_hits": [_hit(item) for item in value.course_hits],
            "my_hits": [_hit(item) for item in value.my_hits],
            "voice": _voice(value.voice), "keywords": list(value.keywords)}


def _source(value: SourceDoc) -> dict:
    return {"title": value.title, "text": value.text, "url": value.url}


def _length(value: LengthTarget) -> dict:
    # mode("min"|"max"|"range")를 빠뜨리면 v0.2의 상한 전용 판정
    # (boundary_guard §4.6(a))이 복원한 세션에서만 조용히 "min"으로 되돌아간다 —
    # 같은 과제인데 새 세션과 복원 세션의 분량 판정이 달라지는, 재현하기 어려운
    # 버그다. 새 필드를 추가하면 여기와 _length_from를 함께 늘린다.
    return {"unit": value.unit, "min": value.min, "max": value.max,
            "raw": value.raw, "per_item": value.per_item, "mode": value.mode}


def _deadline(value: Deadline) -> dict:
    return {"due": value.due.isoformat(), "had_year": value.had_year, "raw": value.raw,
            "time_str": value.time_str, "extended": value.extended, "raw_pos": value.raw_pos}


def _element(value: ContentElement) -> dict:
    return {"id": value.id, "label": value.label, "required": value.required,
            "scope": value.scope, "evidence_kind": value.evidence_kind,
            "source_span": value.source_span}


def _slot(value: SkeletonSlot) -> dict:
    return {"id": value.id, "label": value.label, "hint": value.hint,
            "evidence_kind": value.evidence_kind, "required": value.required}


def _evidence_item(value: EvidenceItem) -> dict:
    return {"kind": value.kind, "title": value.title, "excerpt": value.excerpt,
            "relevance": value.relevance}


def _evidence(value: EvidenceLedger) -> dict:
    return {"unit_title": value.unit_title,
            "items": [_evidence_item(item) for item in value.items]}


def _plan_item(value: PlanItem) -> dict:
    return {"element_id": value.element_id, "label": value.label, "action": value.action,
            "sufficiency": value.sufficiency, "evidence_titles": list(value.evidence_titles),
            "excerpts": list(value.excerpts), "target_chars": value.target_chars,
            "note": value.note, "decision_question": value.decision_question}


def _plan(value: UnitPlan) -> dict:
    return {"unit_index": value.unit_index, "items": [_plan_item(item) for item in value.items],
            "target_chars": value.target_chars}


def _unit(value: ResponseUnit) -> dict:
    return {"index": value.index, "title": value.title, "meta": _plain(value.meta),
            "elements": [_slot(item) for item in value.elements],
            "length_target": _length(value.length_target) if value.length_target else None,
            "evidence": _evidence(value.evidence) if value.evidence else None,
            "plan": _plan(value.plan) if value.plan else None, "body": value.body}


def _material(value: MaterialHit) -> dict:
    return {"name": value.name, "url": value.url, "score": value.score,
            "matched": list(value.matched)}


def _announcement(value: Announcement) -> dict:
    return {"subject": value.subject, "body": value.body, "author": value.author,
            "created_iso": value.created_iso, "forum": value.forum,
            "course_id": value.course_id, "course_name": value.course_name,
            "url": value.url, "replies": list(value.replies), "links": list(value.links)}


def _inquiry(value: InquiryAssignment) -> dict:
    return {"week": value.week, "professor": value.professor, "sheet_url": value.sheet_url,
            "class_date": value.class_date.isoformat() if value.class_date else None,
            "due_date": value.due_date.isoformat() if value.due_date else None,
            "due_time": value.due_time, "professor_field": value.professor_field,
            "professor_url": value.professor_url}


def _route(value: AssignmentRoute) -> dict:
    # stage를 빠뜨리면 복원된 세션의 lab_report_cycle이 단계를 잃는다 — 화면의
    # 단계별 하드 금지(예비=실측값 서술 금지 / 결과=랩노트 없이 수치 생성 금지)와
    # 텔레메트리 lab_stage가 재시작·클라우드 하이드레이션 뒤에만 조용히 비는,
    # 재현하기 어려운 버그다. 구세션은 키가 없어 기본값 ""로 복원된다.
    return {"strategy": value.strategy, "reason": value.reason,
            "required_evidence": list(value.required_evidence),
            "questions": list(value.questions), "actionable": value.actionable,
            "stage": value.stage}


def _result(value: Result) -> dict:
    known = set(Result.__dataclass_fields__)
    extras = set(vars(value)) - known
    if extras - {"teacher_feedback"}:
        raise TypeError(f"unsupported Result fields: {sorted(extras - {'teacher_feedback'})}")
    return {
        "documents": [_document(item) for item in value.documents],
        "spec": _plain(value.spec), "draft": _draft(value.draft), "guard": _guard(value.guard),
        "suggested_prompts": list(value.suggested_prompts),
        "context": _context(value.context) if value.context else None,
        "final_draft": _draft(value.final_draft) if value.final_draft else None,
        "final_guard": _guard(value.final_guard) if value.final_guard else None,
        "etl_materials": [_material(item) for item in value.etl_materials],
        "etl_announcements": [_announcement(item) for item in value.etl_announcements],
        "sources": list(value.sources), "source_docs": [_source(item) for item in value.source_docs],
        "length_target": _length(value.length_target) if value.length_target else None,
        "deadline": _deadline(value.deadline) if value.deadline else None,
        "content_elements": [_element(item) for item in value.content_elements],
        "units": [_unit(item) for item in value.units],
        "capture_warnings": list(value.capture_warnings),
        "inquiry_assignment": _inquiry(value.inquiry_assignment) if value.inquiry_assignment else None,
        "assignment_route": _route(value.assignment_route) if value.assignment_route else None,
        "teacher_feedback": _plain(getattr(value, "teacher_feedback", [])),
        "llm_usage": _plain(value.llm_usage) if value.llm_usage is not None else None,
        "voice_applied": bool(value.voice_applied),
        "tone_block": str(value.tone_block or ""),
        "tone_register": str(value.tone_register or ""),
        "tone_source": str(value.tone_source or ""),
        "needs_approval": bool(value.needs_approval),
        "approval_kinds": [str(item) for item in value.approval_kinds],
        "approval_messages": [str(item) for item in value.approval_messages],
        "prompt_version": str(value.prompt_version or ""),
        "model_version": str(value.model_version or ""),
        "elapsed_ms": int(value.elapsed_ms or 0),
        "practice_mode": bool(value.practice_mode),
        "practice_audit": _plain(value.practice_audit) if value.practice_audit else None,
        # 러너 결과 — Result에 있는데 여기 없으면 복원한 세션에서만 조용히 사라져
        # 제출 전 점검의 '실행' 항목이 재시작 후 빈다(extras 검사는 dataclass
        # 필드 기준이라 예외도 안 난다 — 그래서 눈에 안 띄었다).
        "run_check": _plain(value.run_check) if value.run_check is not None else None,
    }


def to_jsonable(payload: dict) -> dict:
    """Convert the fixed web-session payload to JSON-native values."""
    if type(payload) is not dict:
        raise TypeError("session payload must be a dict")
    allowed = {"result", "answers", "autofilled", "suggestions", "review",
               "telemetry_meta", "workspace", "voice_match"}
    unknown = set(payload) - allowed
    if unknown:
        raise TypeError(f"unsupported payload fields: {sorted(unknown)}")
    answers = payload.get("answers")
    suggestions = payload.get("suggestions")
    if answers is not None and (type(answers) is not dict or
                                not all(type(k) is int and type(v) is str for k, v in answers.items())):
        raise TypeError("answers must be dict[int, str]")
    if suggestions is not None and type(suggestions) is not dict:
        raise TypeError("suggestions must be a dict")
    # AI가 대신 채운 결정 번호 — 재시작 후에도 "이건 내가 안 정했다"를 보여야 한다.
    autofilled = payload.get("autofilled")
    if autofilled is not None and (type(autofilled) is not list
                                   or not all(type(i) is int for i in autofilled)):
        raise TypeError("autofilled must be list[int] or None")
    suggestion_data = None
    if suggestions is not None:
        suggestion_data = {}
        for key, item in suggestions.items():
            if type(key) is not int or type(item) is not dict or set(item) != {"answer", "why"}:
                raise TypeError("suggestions must be dict[int, {answer, why}]")
            if type(item["answer"]) is not str or type(item["why"]) is not str:
                raise TypeError("suggestion values must be strings")
            suggestion_data[str(key)] = {"answer": item["answer"], "why": item["why"]}
    review = payload.get("review")
    if review is not None and type(review) is not ReviewReport:
        raise TypeError("review must be ReviewReport or None")
    result = payload.get("result")
    if result is not None and type(result) is not Result:
        raise TypeError("result must be Result or None")
    telemetry_meta = payload.get("telemetry_meta")
    if telemetry_meta is not None and type(telemetry_meta) is not dict:
        raise TypeError("telemetry_meta must be a dict or None")
    workspace = payload.get("workspace")
    if workspace is not None and type(workspace) is not dict:
        raise TypeError("workspace must be a dict or None")
    return {
        "result": _result(result) if result is not None else None,
        "answers": ({str(k): v for k, v in answers.items()} if answers is not None else None),
        "autofilled": (list(autofilled) if autofilled is not None else None),
        "suggestions": suggestion_data,
        "review": ({"level": review.level, "coverage": review.coverage,
                    "gaps": list(review.gaps), "decision_check": review.decision_check,
                    "summary": review.summary} if review is not None else None),
        "telemetry_meta": _plain(telemetry_meta),
        "workspace": _plain(workspace),
        "voice_match": (bool(payload["voice_match"])
                        if payload.get("voice_match") is not None else None),
    }


def _document_from(data: dict) -> Document:
    return Document(source=data["source"], kind=data["kind"], text=data["text"],
                    sections=[Section(**item) for item in data["sections"]],
                    n_chars=data["n_chars"], n_tokens_est=data["n_tokens_est"])


def _draft_from(data: dict) -> Draft:
    decisions = [DecisionPoint(note=item["note"], context=item["context"],
                               resolution=Resolution(item["resolution"]) if item["resolution"] else None,
                               human_input=item["human_input"]) for item in data["decisions"]]
    return Draft(body=data["body"], decisions=decisions)


def _guard_from(data: dict) -> GuardReport:
    return GuardReport(passed=data["passed"], attempts=data["attempts"], reasks=data["reasks"],
                       final_errors=list(data["final_errors"]),
                       history=[list(item) for item in data["history"]])


def _voice_from(data: dict) -> VoiceProfile:
    return VoiceProfile(**data)


def _context_from(data: dict) -> ContextBundle:
    def hit(item: dict) -> Hit:
        return Hit(document=_document_from(item["document"]), score=item["score"],
                   matched=list(item["matched"]))
    return ContextBundle(course_hits=[hit(item) for item in data["course_hits"]],
                         my_hits=[hit(item) for item in data["my_hits"]],
                         voice=_voice_from(data["voice"]), keywords=list(data["keywords"]))


def _length_from(data: Optional[dict]) -> Optional[LengthTarget]:
    # mode 도입 이전에 구운 세션에는 키가 없다 — dataclass 기본값("min")이 곧
    # 하위 호환 폴백이라 별도 분기를 두지 않는다(test_old_session_without_length_mode).
    return LengthTarget(**data) if data is not None else None


def _deadline_from(data: Optional[dict]) -> Optional[Deadline]:
    if data is None:
        return None
    copy = dict(data)
    copy["due"] = date.fromisoformat(copy["due"])
    return Deadline(**copy)


def _unit_from(data: dict) -> ResponseUnit:
    evidence = data["evidence"]
    plan = data["plan"]
    return ResponseUnit(
        index=data["index"], title=data["title"], meta=dict(data["meta"]),
        elements=[SkeletonSlot(**item) for item in data["elements"]],
        length_target=_length_from(data["length_target"]),
        evidence=(EvidenceLedger(unit_title=evidence["unit_title"],
                                 items=[EvidenceItem(**item) for item in evidence["items"]])
                  if evidence else None),
        plan=(UnitPlan(unit_index=plan["unit_index"],
                       items=[PlanItem(**item) for item in plan["items"]],
                       target_chars=plan["target_chars"]) if plan else None),
        body=data["body"])


def _result_from(data: dict) -> Result:
    inquiry = data["inquiry_assignment"]
    if inquiry:
        inquiry = dict(inquiry)
        inquiry["class_date"] = date.fromisoformat(inquiry["class_date"]) if inquiry["class_date"] else None
        inquiry["due_date"] = date.fromisoformat(inquiry["due_date"]) if inquiry["due_date"] else None
    route = data["assignment_route"]
    if route:
        route = dict(route)
        route["required_evidence"] = tuple(route["required_evidence"])
        route["questions"] = tuple(route["questions"])
    result = Result(
        documents=[_document_from(item) for item in data["documents"]], spec=dict(data["spec"]),
        draft=_draft_from(data["draft"]), guard=_guard_from(data["guard"]),
        suggested_prompts=list(data["suggested_prompts"]),
        context=_context_from(data["context"]) if data["context"] else None,
        final_draft=_draft_from(data["final_draft"]) if data["final_draft"] else None,
        final_guard=_guard_from(data["final_guard"]) if data["final_guard"] else None,
        etl_materials=[MaterialHit(**item) for item in data["etl_materials"]],
        etl_announcements=[Announcement(**item) for item in data["etl_announcements"]],
        sources=list(data["sources"]), source_docs=[SourceDoc(**item) for item in data["source_docs"]],
        length_target=_length_from(data["length_target"]), deadline=_deadline_from(data["deadline"]),
        content_elements=[ContentElement(**item) for item in data["content_elements"]],
        units=[_unit_from(item) for item in data["units"]],
        capture_warnings=list(data["capture_warnings"]),
        inquiry_assignment=InquiryAssignment(**inquiry) if inquiry else None,
        assignment_route=AssignmentRoute(**route) if route else None,
        # 구세션(v2 초기)은 키 자체가 없다 — None 폴백(텔레메트리도 null 유지).
        llm_usage=(dict(data["llm_usage"]) if data.get("llm_usage") else None),
        voice_applied=(data.get("voice_applied") is True),
        # 구세션(톤 레지스터 도입 전)은 키가 없다 — 빈 문자열 폴백(기존 동작 유지).
        tone_block=str(data.get("tone_block") or ""),
        tone_register=str(data.get("tone_register") or ""),
        tone_source=str(data.get("tone_source") or ""),
        needs_approval=(data.get("needs_approval") is True),
        approval_kinds=[str(item) for item in (data.get("approval_kinds") or [])],
        approval_messages=[str(item) for item in (data.get("approval_messages") or [])],
        prompt_version=str(data.get("prompt_version") or ""),
        model_version=str(data.get("model_version") or ""),
        elapsed_ms=int(data.get("elapsed_ms") or 0),
        practice_mode=(data.get("practice_mode") is True),
        practice_audit=(dict(data["practice_audit"]) if data.get("practice_audit") else None))
    result.run_check = _plain(data.get("run_check"))
    result.teacher_feedback = list(data.get("teacher_feedback") or [])
    return result


def from_jsonable(data: dict) -> dict:
    """Rebuild the fixed web-session payload from validated JSON data."""
    required = {"result", "answers", "suggestions", "review"}
    if (type(data) is not dict or not required <= set(data)
            or not set(data) - required <= {"autofilled", "telemetry_meta", "workspace",
                                            "voice_match"}):
        raise ValueError("invalid session payload shape")
    review = data["review"]
    return {
        "result": _result_from(data["result"]) if data["result"] is not None else None,
        "answers": ({int(k): v for k, v in data["answers"].items()} if data["answers"] is not None else None),
        "autofilled": ([int(i) for i in data["autofilled"]]
                       if data.get("autofilled") is not None else None),
        "suggestions": ({int(k): dict(v) for k, v in data["suggestions"].items()}
                        if data["suggestions"] is not None else None),
        "review": ReviewReport(**review) if review is not None else None,
        "telemetry_meta": _plain(data.get("telemetry_meta")),
        "workspace": _plain(data.get("workspace")),
        "voice_match": (bool(data["voice_match"])
                        if data.get("voice_match") is not None else None),
    }


def session_key() -> bytes:
    """Return the configured key, creating a private local key when absent."""
    configured = os.getenv("UNTIL_SESSION_KEY")
    if configured:
        return configured.encode("utf-8")
    try:
        key = _KEY_PATH.read_bytes()
        if key:
            return key
    except OSError:
        pass
    key = secrets.token_bytes(32)
    _KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _KEY_PATH.write_bytes(key)
    if os.name == "posix":
        os.chmod(_KEY_PATH, 0o600)
    return key


def encode(payload: dict, ts: float, meta: Optional[dict] = None) -> bytes:
    """Encode and sign one v2 session envelope."""
    body = to_jsonable(payload)
    signed_meta = _plain(meta) if meta is not None else None
    if signed_meta is not None:
        body["_meta"] = signed_meta
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":")).encode("utf-8")
    sig = hmac.new(session_key(), canonical, hashlib.sha256).hexdigest()
    envelope = {"v": VERSION, "ts": float(ts), "sig": sig, "payload": body}
    if signed_meta is not None:
        envelope["meta"] = signed_meta
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def decode(blob: bytes) -> Optional[dict]:
    """Verify and decode a v2 envelope; malformed or untrusted data is skipped."""
    try:
        envelope = json.loads(blob.decode("utf-8"))
        if type(envelope) is not dict or envelope.get("v") != VERSION:
            return None
        body = envelope["payload"]
        canonical = json.dumps(body, sort_keys=True, ensure_ascii=False,
                               separators=(",", ":")).encode("utf-8")
        expected = hmac.new(session_key(), canonical, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(str(envelope.get("sig", "")), expected):
            return None
        session_body = dict(body)
        signed_meta = session_body.pop("_meta", None)
        if envelope.get("meta") != signed_meta:
            return None
        payload = from_jsonable(session_body)
        payload["ts"] = float(envelope["ts"])
        payload["meta"] = _plain(signed_meta) if signed_meta is not None else None
        return payload
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def read_meta(blob: bytes) -> Optional[dict]:
    """Verify an envelope and return listing metadata without rebuilding Result."""
    try:
        envelope = json.loads(blob.decode("utf-8"))
        if type(envelope) is not dict or envelope.get("v") != VERSION:
            return None
        body = envelope["payload"]
        canonical = json.dumps(body, sort_keys=True, ensure_ascii=False,
                               separators=(",", ":")).encode("utf-8")
        expected = hmac.new(session_key(), canonical, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(str(envelope.get("sig", "")), expected):
            return None
        meta = body.get("_meta")
        if envelope.get("meta") != meta:
            return None
        return _plain(meta) if type(meta) is dict else None
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
