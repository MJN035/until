"""
채널 중립 이벤트 스키마 — 하나의 스키마로 모든 채널을 받는다.

지금 채널을 늘리는 게 아니다. **데이터가 채널에 종속되지 않게만** 만든다.
핵심 원칙 하나: **페르소나는 채널이 아니라 사용자(actor_id)에 귀속된다.**
`channel`은 태그일 뿐이라 나중에 이메일·메신저·문서·리뷰가 붙어도
페르소나를 복제하거나 스키마를 바꿀 필요가 없다.

채널별 고유 필드는 어댑터에서 이 스키마로 정규화해 넣고, **원본은 `raw_payload`에
그대로 보관**한다. 정규화가 틀렸을 때 되돌아갈 자리가 없으면 그 데이터는 죽는다.

이 로그는 CLAUDE.md가 규정한 **원문 파이프**(사용자 소유·학습 미사용)에 속한다.
비식별 신호 파이프(`telemetry/`)로 절대 나가지 않는다 — 여기엔 과제 원문·초안·
최종본이 그대로 들어 있다.
"""
from __future__ import annotations

import hashlib
import json
import threading as _threading
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

STORE_VERSION = 1
EVENTS_PATH = Path("_until_work/persona_events.jsonl")

#: 알려진 채널 태그. **강제하지 않는다** — 채널은 태그일 뿐이고, 새 채널이 붙을 때
#: 이 목록을 고치지 않아도 동작해야 한다는 것이 이 설계의 요점이다.
KNOWN_CHANNELS = ("web", "cli", "etl", "email", "messenger", "doc", "review")

MAX_KEEP = 500
MAX_BODY_CHARS = 8000
MAX_CONTEXT_CHARS = 2000
MAX_RAW_PAYLOAD_CHARS = 4000


@dataclass
class PersonaEvent:
    """한 번의 '생성 → 사람 확정' 사이클. 채널이 무엇이든 같은 모양이다."""
    event_id: str
    actor_id: str = "local"
    channel: str = "web"
    register_key: str = ""
    task_type: str = ""
    recipient_ref: str = ""       # 수신자 식별자(익명 가능) — 실명 저장 강제 아님
    input_context: str = ""       # 그때의 상황 요지
    generated_draft: str = ""
    final_output: str = ""
    edit_diff: str = ""           # 초안 → 최종본 변경 요약
    accepted: Optional[bool] = None
    latency_ms: int = 0
    model_version: str = ""
    prompt_version: str = ""
    created_at: str = ""
    #: 채널별 고유 필드의 원본. 정규화가 틀렸을 때 돌아갈 자리.
    raw_payload: Dict[str, Any] = field(default_factory=dict)


_TL_PATH = _threading.local()


def set_events_path_override(p: Optional[Path]) -> None:
    """이 스레드(요청)의 이벤트 로그 경로 — 클라우드 사용자별 격리."""
    _TL_PATH.value = p


def _resolve_path(path: Optional[Path]) -> Path:
    if path is not None:
        return Path(path)
    o = getattr(_TL_PATH, "value", None)
    return Path(o) if o is not None else EVENTS_PATH


def events_path() -> Path:
    return _resolve_path(None)


def _clip(value: Any, cap: int) -> str:
    return str(value or "")[:cap]


def _safe_payload(raw: Any) -> Dict[str, Any]:
    """raw_payload는 JSON 직렬화 가능한 것만 담는다(로드 불가 파일 방지)."""
    if not isinstance(raw, dict):
        return {}
    try:
        text = json.dumps(raw, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {}
    if len(text) > MAX_RAW_PAYLOAD_CHARS:
        return {"_truncated": True, "_preview": text[:MAX_RAW_PAYLOAD_CHARS]}
    return json.loads(text)


def make_event(*, actor_id: str = "local", channel: str = "web",
               register_key: str = "", task_type: str = "",
               recipient_ref: str = "", input_context: str = "",
               generated_draft: str = "", final_output: str = "",
               edit_diff: str = "", accepted: Optional[bool] = None,
               latency_ms: int = 0, model_version: str = "",
               prompt_version: str = "",
               raw_payload: Optional[dict] = None) -> Optional[PersonaEvent]:
    """검증된 이벤트 하나. 본문이 전부 비면 None(빈 이벤트는 적립하지 않는다)."""
    draft = _clip(generated_draft, MAX_BODY_CHARS)
    final = _clip(final_output, MAX_BODY_CHARS)
    context = _clip(input_context, MAX_CONTEXT_CHARS)
    if not (draft or final):
        return None
    raw_id = f"{actor_id}|{channel}|{context}|{final or draft}".encode("utf-8")
    return PersonaEvent(
        event_id=hashlib.sha256(raw_id).hexdigest()[:16],
        actor_id=_clip(actor_id, 64) or "local",
        channel=_clip(channel, 32) or "web",
        register_key=_clip(register_key, 64),
        task_type=_clip(task_type, 64),
        recipient_ref=_clip(recipient_ref, 120),
        input_context=context, generated_draft=draft, final_output=final,
        edit_diff=_clip(edit_diff, 1000),
        accepted=accepted if isinstance(accepted, bool) else None,
        latency_ms=max(0, int(latency_ms or 0)),
        model_version=_clip(model_version, 200),
        prompt_version=_clip(prompt_version, 64),
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        raw_payload=_safe_payload(raw_payload))


def recipient_ref_for(result: Any) -> str:
    """수신자 참조 — **이름이 아니라 지문**. 수신자가 없으면 빈 문자열.

    페르소나가 "이 사람에게는 이렇게 쓴다"를 배우려면 수신자를 **구분**할 수 있어야
    한다. 하지만 구분에 필요한 것은 안정적인 참조이지 실명이 아니다. 교수 실명은
    제3자의 개인정보이고, 이 로그에는 과제 원문까지 함께 들어 있다.
    그래서 역할 + 지문(`professor:3f2a…`)만 남긴다 — 같은 교수면 같은 참조가 되어
    학습에는 충분하고, 파일이 새어도 누구인지는 드러나지 않는다.
    (PHASE 3 프라이버시 원칙 "개인 식별 정보는 본문과 분리"의 적용.)
    """
    inquiry = getattr(result, "inquiry_assignment", None)
    professor = str(getattr(inquiry, "professor", "") or "").strip()
    if professor:
        digest = hashlib.sha256(professor.encode("utf-8")).hexdigest()[:12]
        return f"professor:{digest}"
    strategy = str(getattr(getattr(result, "assignment_route", None),
                           "strategy", "") or "")
    if strategy == "team_project":
        return "team"          # 특정 개인이 아니라 '팀'이 수신자다
    if strategy in ("activity_form", "personal_upload"):
        return "admin"
    return ""                  # 수신자 없음(채점자가 읽는 산문) — 지어내지 않는다


def event_from_result(result: Any, *, channel: str = "web", actor_id: str = "local",
                      recipient_ref: str = "", latency_ms: int = 0,
                      accepted: Optional[bool] = None,
                      raw_payload: Optional[dict] = None,
                      config: Any = None) -> Optional[PersonaEvent]:
    """웹·CLI 채널 어댑터 — 파이프라인 `Result`를 채널 중립 이벤트로 정규화한다.

    Result를 import하지 않고 getattr로만 읽는다(덕 타이핑) — 이 모듈이 파이프라인에
    의존하면 나중에 붙는 다른 채널 어댑터도 파이프라인을 끌고 와야 한다.
    """
    from .versions import resolve_model_version, resolve_prompt_version

    draft = getattr(getattr(result, "draft", None), "body", "") or ""
    final = getattr(getattr(result, "final_draft", None), "body", "") or ""
    spec = getattr(result, "spec", None) or {}

    diff_summary = ""
    if draft and final:
        try:
            from ..diffview import diff_drafts, summarize_changes
            diff_summary = summarize_changes(diff_drafts(draft, final))
        except Exception:
            diff_summary = ""

    context_parts = [str(spec.get("goal") or ""), str(spec.get("deliverable") or "")]
    reqs = spec.get("requirements")
    if isinstance(reqs, list):
        context_parts += [str(r) for r in reqs]

    prompt_version = str(getattr(result, "prompt_version", "") or "") \
        or resolve_prompt_version()
    model_version = str(getattr(result, "model_version", "") or "") \
        or resolve_model_version(result, config=config)

    return make_event(
        actor_id=actor_id, channel=channel,
        register_key=str(getattr(result, "tone_register", "") or ""),
        task_type=str(spec.get("task_type") or ""),
        recipient_ref=recipient_ref or recipient_ref_for(result),
        input_context=" ".join(p for p in context_parts if p),
        generated_draft=draft, final_output=final, edit_diff=diff_summary,
        # **최종본이 있다는 것은 수락이 아니다.** 사람이 결정에 답했을 뿐이고, 그 글을
        # 실제로 냈는지·마음에 들었는지는 아직 모른다. 여기서 True로 적으면 수락률이
        # 항상 100%가 되어 지표가 통째로 무의미해진다 — 증거가 생길 때
        # `update_acceptance_for_result`가 나중에 채운다.
        accepted=accepted,
        latency_ms=latency_ms or int(getattr(result, "elapsed_ms", 0) or 0),
        model_version=model_version,
        prompt_version=prompt_version,
        raw_payload=raw_payload if raw_payload is not None else {
            "route_strategy": str(getattr(getattr(result, "assignment_route", None),
                                          "strategy", "") or ""),
            "tone_source": str(getattr(result, "tone_source", "") or ""),
            "needs_approval": bool(getattr(result, "needs_approval", False)),
            "llm_usage": getattr(result, "llm_usage", None),
        })


def record_event(event: Optional[PersonaEvent],
                 path: Optional[Path] = None) -> Optional[PersonaEvent]:
    """이벤트 1건 적립. 실패는 조용히 None — 로깅이 본 흐름을 막지 않는다."""
    if event is None:
        return None
    try:
        p = _resolve_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        row = dict(asdict(event))
        row["v"] = STORE_VERSION
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        _prune(p)
        return event
    except Exception:
        return None


def _prune(p: Path) -> None:
    try:
        lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if len(lines) > MAX_KEEP:
            p.write_text("\n".join(lines[-MAX_KEEP:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def load_events(path: Optional[Path] = None) -> List[PersonaEvent]:
    """적립된 이벤트. 깨진 줄·미래 버전·타입 불일치는 조용히 건너뛴다."""
    p = _resolve_path(path)
    if not p.exists():
        return []
    allowed = {f.name for f in fields(PersonaEvent)}
    out: List[PersonaEvent] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("v") != STORE_VERSION:
            continue
        kwargs = {k: v for k, v in row.items() if k in allowed}
        if not isinstance(kwargs.get("event_id"), str) or not kwargs["event_id"]:
            continue
        if not isinstance(kwargs.get("raw_payload"), dict):
            kwargs["raw_payload"] = {}
        try:
            out.append(PersonaEvent(**kwargs))
        except TypeError:
            continue
    return out


def update_acceptance(event_id: str, accepted: Optional[bool],
                      path: Optional[Path] = None) -> bool:
    """이미 적립된 이벤트의 `accepted`를 나중에 채운다. 바뀐 게 없으면 False.

    수락 여부는 생성 시점에 알 수 없다(그때는 아직 내지도 않았다). 증거가 생기는
    시점 — 실제 제출, 사용자 평가 — 에 되돌아와 채우는 것이 유일하게 정직한 방법이다.
    JSONL을 줄 단위로 다시 쓴다(상한 500줄이라 비용이 무의미하다).
    """
    if not event_id:
        return False
    p = _resolve_path(path)
    try:
        lines = [ln for ln in p.read_text(encoding="utf-8",
                                          errors="replace").splitlines() if ln.strip()]
    except OSError:
        return False
    changed = False
    out: List[str] = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            out.append(line)          # 못 읽는 줄은 보존
            continue
        if isinstance(row, dict) and row.get("event_id") == event_id \
                and row.get("accepted") != accepted:
            row["accepted"] = accepted
            out.append(json.dumps(row, ensure_ascii=False))
            changed = True
        else:
            out.append(line)
    if not changed:
        return False
    try:
        p.write_text("\n".join(out) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def update_acceptance_for_result(result: Any, accepted: Optional[bool], *,
                                 channel: str = "web", actor_id: str = "local",
                                 path: Optional[Path] = None) -> bool:
    """Result로부터 event_id를 재계산해 수락 여부를 채운다(비치명적).

    event_id는 (actor, channel, 상황, 최종본)의 해시라 같은 입력이면 같은 값이
    나온다 — 그래서 이벤트를 들고 다니지 않아도 나중에 찾아갈 수 있다.
    """
    try:
        candidate = event_from_result(result, channel=channel, actor_id=actor_id)
        if candidate is None:
            return False
        return update_acceptance(candidate.event_id, accepted, path)
    except Exception:
        return False


def clear_events(path: Optional[Path] = None) -> None:
    try:
        _resolve_path(path).unlink()
    except OSError:
        pass


def describe(path: Optional[Path] = None) -> str:
    """CLI용 요약 — 채널별 분포와 버전 기록 여부를 함께 보여준다."""
    events = load_events(path)
    if not events:
        return "적립된 페르소나 이벤트 없음"
    from collections import Counter
    channels = Counter(e.channel for e in events)
    detail = " · ".join(f"{k} {v}" for k, v in channels.most_common())
    missing = sum(1 for e in events if not e.model_version)
    note = f" · ⚠ 모델 미기록 {missing}건" if missing else ""
    return f"이벤트 {len(events)}건({detail}){note}"
