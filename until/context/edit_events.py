"""
수정 diff 캡처 — (초안, 최종본, diff, 수정 유형)을 그대로 적립한다.

개인화 신호 중 가장 값싸고 정확한 것이다. 이게 없으면 스타일 카드도 레지스터도
전부 추측이 된다. 그래서 이 모듈만 기본값이 **켜짐**이다(`config.edit_capture_active`,
탈출구 `UNTIL_EDIT_CAPTURE=0`) — 프롬프트·출력을 전혀 바꾸지 않고 파일에만 쓰므로
결정성도 깨지 않는다.

⚠️ **지금 잡히는 것의 한계를 분명히 해 둔다.** 현재 Until에는 사용자가 본문을
직접 고치는 입력란이 없다(사용자는 다운로드해서 밖에서 고친다). 그래서 여기 쌓이는
것은 대부분 `llm_revise`(사용자 지시로 모델이 고친 것)와 `finalize`(사람의 결정
답변이 본문에 녹으며 바뀐 것)이다. **사람이 직접 고친 diff가 아니다.**
`edit_source`를 처음부터 세 값으로 나눠 둔 이유가 이것이다 — 나중에 편집란이
붙으면 `human`만 추가되고 스키마는 그대로다. 학습에 쓸 때는 반드시 source로
가중치를 달리해야 한다(`human` ≫ `finalize` > `llm_revise`).

diff는 원문 그대로 보관한다(요약만 남기면 나중에 다른 질문을 못 던진다).
`edit_ratio`/`edit_ops`는 텔레메트리 allowlist에 이미 예약돼 있던 필드다.
"""
from __future__ import annotations

import hashlib
import json
import threading as _threading
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

STORE_VERSION = 1
EDIT_EVENTS_PATH = Path("_until_work/edit_events.jsonl")

#: 수정 출처 — 신호 품질이 다르므로 절대 뭉개지 않는다.
EDIT_SOURCES = ("human", "finalize", "llm_revise")
#: 출처별 신호 가중치(배치 학습이 참조). 사람이 직접 고친 것만 온전한 신호다.
SOURCE_WEIGHT = {"human": 1.0, "finalize": 0.5, "llm_revise": 0.25}

MAX_KEEP = 300
MAX_BODY_CHARS = 8000
MAX_CHANGES = 40


@dataclass
class EditEvent:
    event_id: str
    edit_source: str                 # human | finalize | llm_revise
    before: str
    after: str
    ops: Dict[str, int] = field(default_factory=dict)   # {"수정":n,"추가":n,"삭제":n}
    edit_ratio: float = 0.0          # 바뀐 문단 / 전체 문단
    instruction: str = ""            # 사용자가 준 수정 지시(있으면)
    register_key: str = ""
    task_type: str = ""
    actor_id: str = "local"
    created_at: str = ""

    @property
    def weight(self) -> float:
        return SOURCE_WEIGHT.get(self.edit_source, 0.0)


_TL_PATH = _threading.local()


def set_edit_events_path_override(p: Optional[Path]) -> None:
    _TL_PATH.value = p


def _resolve_path(path: Optional[Path]) -> Path:
    if path is not None:
        return Path(path)
    o = getattr(_TL_PATH, "value", None)
    return Path(o) if o is not None else EDIT_EVENTS_PATH


def edit_events_path() -> Path:
    return _resolve_path(None)


def summarize_diff(before: str, after: str) -> tuple:
    """(ops, edit_ratio, changes) — `diffview.diff_drafts`를 그대로 재사용한다.

    표시용 diff와 학습용 diff가 다른 알고리즘을 쓰면, 사용자가 화면에서 본 변경과
    시스템이 배운 변경이 어긋난다. 같은 함수를 쓰는 것이 그 어긋남을 없앤다.
    """
    from ..diffview import diff_drafts
    changes = diff_drafts(before or "", after or "")
    ops: Dict[str, int] = {}
    for c in changes:
        ops[c.label] = ops.get(c.label, 0) + 1
    total_paras = len([p for p in (before or "").split("\n\n") if p.strip()])
    ratio = round(len(changes) / max(1, total_paras), 3) if changes else 0.0
    return ops, min(ratio, 9.999), changes[:MAX_CHANGES]


def record_edit_event(before: str, after: str, *, edit_source: str,
                      instruction: str = "", register_key: str = "",
                      task_type: str = "", actor_id: str = "local",
                      path: Optional[Path] = None) -> Optional[EditEvent]:
    """수정 1건 적립. 변화가 없거나 게이트가 꺼져 있으면 None(비치명적).

    호출부는 반환값을 무시해도 된다 — 이 함수는 어떤 이유로든 예외를 내지 않는다.
    """
    from ..config import edit_capture_active
    if not edit_capture_active():
        return None
    if edit_source not in EDIT_SOURCES:
        return None
    before_s, after_s = str(before or ""), str(after or "")
    if not before_s.strip() or not after_s.strip() or before_s == after_s:
        return None
    try:
        ops, ratio, _changes = summarize_diff(before_s, after_s)
        if not ops:
            return None
        event = EditEvent(
            event_id=hashlib.sha256(
                f"{before_s}\n=>\n{after_s}".encode("utf-8")).hexdigest()[:16],
            edit_source=edit_source,
            before=before_s[:MAX_BODY_CHARS], after=after_s[:MAX_BODY_CHARS],
            ops=ops, edit_ratio=ratio,
            instruction=" ".join(str(instruction or "").split())[:300],
            register_key=register_key, task_type=task_type, actor_id=actor_id,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        p = _resolve_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        row = dict(asdict(event))
        row["v"] = STORE_VERSION
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        _prune(p)
        return event
    except Exception:
        return None      # 로깅 실패가 생성 흐름을 절대 막지 않는다


def _prune(p: Path) -> None:
    try:
        lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if len(lines) > MAX_KEEP:
            p.write_text("\n".join(lines[-MAX_KEEP:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def load_edit_events(path: Optional[Path] = None) -> List[EditEvent]:
    """적립된 수정 이벤트. 깨진 줄·미래 버전·미지 출처는 건너뛴다."""
    p = _resolve_path(path)
    if not p.exists():
        return []
    allowed = {f.name for f in fields(EditEvent)}
    out: List[EditEvent] = []
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
        if kwargs.get("edit_source") not in EDIT_SOURCES:
            continue
        if not isinstance(kwargs.get("ops"), dict):
            kwargs["ops"] = {}
        try:
            out.append(EditEvent(**kwargs))
        except TypeError:
            continue
    return out


def clear_edit_events(path: Optional[Path] = None) -> None:
    try:
        _resolve_path(path).unlink()
    except OSError:
        pass


def describe(path: Optional[Path] = None) -> str:
    """CLI·설정 화면용 요약 — 출처별로 나눠 보여준다(뭉치면 신호 품질이 가려진다)."""
    events = load_edit_events(path)
    if not events:
        return "적립된 수정 기록 없음"
    from collections import Counter
    by_source = Counter(e.edit_source for e in events)
    detail = " · ".join(f"{k} {by_source[k]}" for k in EDIT_SOURCES if by_source[k])
    human = by_source.get("human", 0)
    note = "" if human else "  (⚠ 사람이 직접 고친 기록 0건 — 편집란이 아직 없음)"
    avg = round(sum(e.edit_ratio for e in events) / len(events), 3)
    return f"수정 기록 {len(events)}건({detail}) · 평균 변경률 {avg}{note}"
