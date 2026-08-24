"""Best-effort, per-browser web telemetry emission.

This store is deliberately independent from the person-oriented admin board.
Only :mod:`until.telemetry.schema` may turn session state into output fields.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import date
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from ..persona.versions import model_fingerprint, used_fallback
from .fields import evidence_missing, lab_stage, route_source
from .schema import (algo_gate, deadline_bucket, spec_chars_bucket, to_telemetry,
                     web_user_key)

MAX_BYTES = 1024 * 1024
_WRITE_LOCK = threading.Lock()
_ROOT_OVERRIDE: Path | None = None


def set_root_override(path: Path | None) -> None:
    """Override the user root in tests without changing process cwd."""
    global _ROOT_OVERRIDE
    _ROOT_OVERRIDE = path


def _algo_version() -> str:
    try:
        return version("until-mvp")
    except PackageNotFoundError:
        return "1.10.0"




def _warning_labels(result: Any) -> list[str]:
    from ..readiness import assess_readiness
    return sorted({item.label for item in assess_readiness(result).warnings})


def _sources(result: Any, answers: dict[int, str], meta: dict) -> dict:
    documents = list(getattr(result, "documents", None) or [])
    source_docs = list(getattr(result, "source_docs", None) or [])
    decisions = list(getattr(result.draft, "decisions", None) or [])
    return {
        "course_id": meta.get("course_id"),
        "assignment_id": meta.get("assignment_id"),
        "assignment_text": "\n".join(str(getattr(doc, "text", "")) for doc in documents),
        "attachment_text": "\n".join(str(getattr(doc, "text", "")) for doc in source_docs),
        "draft_body": str(getattr(result.draft, "body", "")),
        "final_body": str(getattr(getattr(result, "final_draft", None), "body", "")),
        "decision_questions": "\n".join(str(getattr(item, "note", "")) for item in decisions),
        "decision_answers": "\n".join(str(value) for value in answers.values()),
    }


def _edit_signal(result: Any) -> dict:
    """초안 → 최종본 변경량(비율·연산 수). 최종본이 없으면 빈 dict(키 자체를 안 낸다).

    `context/edit_events.summarize_diff`를 그대로 쓴다 — 화면·학습·텔레메트리가
    같은 diff 알고리즘을 보게 해야 세 숫자가 서로 어긋나지 않는다.
    """
    draft = str(getattr(getattr(result, "draft", None), "body", "") or "")
    final = str(getattr(getattr(result, "final_draft", None), "body", "") or "")
    if not draft or not final:
        return {}
    try:
        from ..context.edit_events import summarize_diff
        ops, ratio, _changes = summarize_diff(draft, final)
    except Exception:
        return {}
    return {"edit_ratio": round(float(ratio), 3), "edit_ops": sum(ops.values())}


def build_record(stage: str, uid: str, result: Any, answers: dict[int, str] | None,
                 suggestions: dict[int, dict] | None, meta: dict | None) -> dict:
    """Build one fail-closed record from explicit aggregate session facts."""
    answers = answers or {}
    suggestions = suggestions or {}
    meta = meta or {}
    total = len(getattr(result.draft, "decisions", None) or [])
    answered = sum(1 for index, value in answers.items()
                   if 1 <= int(index) <= total and str(value).strip())
    warnings = _warning_labels(result)
    shown = (list(meta["warning_shown"])
             if "warning_shown" in meta else list(warnings))
    started = meta.get("draft_started_at")
    user_seconds = (max(0, int(time.time() - float(started)))
                    if stage != "draft" and started is not None else None)
    spec = getattr(result, "spec", None) or {}
    guard = getattr(result, "final_guard", None) or getattr(result, "guard", None)
    route = getattr(result, "assignment_route", None)
    deadline = getattr(result, "deadline", None)
    record = {
        "salt_version": os.getenv("UNTIL_TELEMETRY_SALT_VERSION", "1"),
        "user_key": web_user_key(uid),
        "algo_version": _algo_version(),
        # 게이트 값은 schema.algo_gate() 하나로만 구한다(CLI 러너와 공용).
        "algo_gate": algo_gate(),
        "context_mode": "full",
        "pipeline_mode": "unit" if getattr(result, "units", None) else "legacy",
        "backend": str(meta.get("backend") or "mock"),
        "strategy": getattr(route, "strategy", None),
        "task_type": str(spec.get("task_type") or "general"),
        "actionable": getattr(route, "actionable", None),
        # ── COURSE_ALGORITHMS_2026F §7 측정 4축 ────────────────────────────
        # 웹이 실사용을 재는 유일한 경로다. 여기 안 실으면 allowlist에 등재만 돼
        # 있고 원장에는 영원히 안 나온다(2026-08-21 이전 상태). v0.2 기본 승격
        # 판단이 이 네 값 위에서 이뤄지므로 draft·review·final·export 전 stage에
        # 싣는다. 값은 전부 fields.py가 고정 어휘로 거른 뒤 넘어온다.
        # route_strategy는 strategy와 같은 원천이지만 축이 다르다 — strategy는
        # 실행 결과 집계용, route_strategy는 §7 라우팅 분포용이라 소비자가 스키마
        # 1.0 레코드와 섞어 집계할 때 두 이름이 모두 있어야 한다.
        "route_strategy": getattr(route, "strategy", None),
        "route_source": route_source(spec),
        "lab_stage": lab_stage(route),
        "evidence_missing": evidence_missing(result),
        "status": "passed" if guard is not None and guard.passed else "failed",
        "capture_warnings": len(getattr(result, "capture_warnings", None) or []),
        "readiness_warning_labels": warnings,
        "guard_passed": getattr(guard, "passed", None),
        "decisions": total,
        "unit_count": len(getattr(result, "units", None) or []),
        "spec_chars_bucket": spec_chars_bucket(sum(
            len(str(getattr(doc, "text", "")))
            for doc in (getattr(result, "documents", None) or []))),
        "draft_chars": len(str(getattr(result.draft, "body", ""))),
        "decision_total": total,
        "decision_answered": answered,
        "decision_partial": None,
        "decision_skipped": total - answered,
        "decision_median_seconds": None,
        "ai_suggestion_offered": len(suggestions),
        "ai_suggestion_accepted": None,
        "ai_suggestion_edited": None,
        "ai_suggestion_rejected": None,
        "warning_shown": shown,
        "warning_resolved": sorted(set(shown) - set(warnings)),
        "llm_calls": None,
        "llm_tokens_in": None,
        "llm_tokens_out": None,
        "stage": stage,
        "source": str(meta.get("source") or "manual"),
        "user_seconds": user_seconds,
        "revision_count": int(meta.get("revision_count") or 0),
        "voice_match": meta.get("voice_match"),
        # 출처 기록 — 원문이 아니라 버전·지문만 나간다(자유 문자열 금지 규칙).
        "prompt_version": str(getattr(result, "prompt_version", "") or "") or None,
        "model_fingerprint": model_fingerprint(
            str(getattr(result, "model_version", "") or "")) or None,
        "used_fallback": "yes" if used_fallback(result) else "no",
        # 감사에서 "allowlist에 등재만 돼 있고 생산 코드가 0건"이라 지적된 두 필드.
        # PHASE 2의 수정 diff 캡처가 값을 만들 수 있게 됐으므로 이제 실제로 채운다.
        # 원문은 나가지 않는다 — 변경 비율(실수)과 연산 횟수(정수)뿐이다.
        **_edit_signal(result),
        # 생성 소요 시간(정수 ms) — allowlist의 elapsed_ms를 채운다.
        "elapsed_ms": ({"generate": int(getattr(result, "elapsed_ms", 0) or 0)}
                       if getattr(result, "elapsed_ms", 0) else None),
    }
    if deadline is not None:
        record["deadline_bucket"] = deadline_bucket(deadline.days_from(date.today()))
    # LLM 사용량(파이프라인 계측 합산). 구세션 등 원천 없음은 null 유지.
    llm_usage = getattr(result, "llm_usage", None)
    if isinstance(llm_usage, dict):
        record["llm_calls"] = int(llm_usage.get("llm_calls") or 0)
        record["llm_tokens_in"] = int(llm_usage.get("llm_tokens_in") or 0)
        record["llm_tokens_out"] = int(llm_usage.get("llm_tokens_out") or 0)
    return to_telemetry(record, sources=_sources(result, answers, meta))


def _path(uid: str) -> Path:
    root = _ROOT_OVERRIDE or Path("_until_work/users")
    return root / (uid or "local") / "telemetry.jsonl"


def emit_sync(stage: str, uid: str, result: Any, answers: dict[int, str] | None = None,
              suggestions: dict[int, dict] | None = None, meta: dict | None = None,
              *, mirror: bool = False) -> dict:
    """Validate then append one record, rolling the bounded local file.

    mirror=True(클라우드·하이드레이션 확정 요청)면 append 후 파일 전체를
    `telem:<uid>`(+로테이션 `:1`)로 KV 미러 — 디스크 1차·KV 미러 관행.
    """
    record = build_record(stage, uid or "local", result, answers, suggestions, meta)
    encoded = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    path = _path(uid)
    with _WRITE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size + len(encoded) > MAX_BYTES:
            path.replace(path.with_suffix(".jsonl.1"))
        with path.open("ab") as handle:
            handle.write(encoded)
        if mirror:
            # 스냅샷과 enqueue 둘 다 잠금 안에서 — 잠금 밖 enqueue는 겹친 방출의
            # 순서 역전으로 구버전 스냅샷이 KV 최종값이 될 수 있다(FIFO는 적재
            # 순서만 보존, 리뷰 15회차 F2). put_async는 큐 적재뿐이라 잠금 안 무해.
            rot = path.with_suffix(".jsonl.1")
            _mirror_kv(uid or "local",
                       (path.read_bytes(),
                        rot.read_bytes() if rot.exists() else None))
    return record


def _mirror_kv(uid: str, blobs: tuple) -> None:
    """비차단 KV 미러(FIFO 워커 재사용). 실패는 조용히 — 응답을 막지 않는다."""
    try:
        from .. import cloudkv
        if cloudkv.kv() is None:
            return
        active, rotated = blobs
        cloudkv.put_async(f"telem:{uid}", active, cloudkv.TTL_TELEM)
        if rotated is not None:
            cloudkv.put_async(f"telem:{uid}:1", rotated, cloudkv.TTL_TELEM)
    except Exception:
        pass


def emit_best_effort(stage: str, uid: str, result: Any,
                     answers: dict[int, str] | None = None,
                     suggestions: dict[int, dict] | None = None,
                     meta: dict | None = None, *, mirror: bool = False) -> None:
    """Schedule emission only when opted in; never delay or fail a response."""
    if os.getenv("UNTIL_TELEMETRY") != "1":
        return

    def work() -> None:
        try:
            emit_sync(stage, uid, result, answers, suggestions, meta, mirror=mirror)
        except Exception:
            pass

    try:
        threading.Thread(target=work, name="until-telemetry", daemon=True).start()
    except Exception:
        pass
