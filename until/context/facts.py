"""
L3 사실 기억 — 진행 중 사안, 인물 관계, 과거 결정, 약속한 일정.

"무엇을 말할지"를 아는 층이다. **문체와 완전히 분리해서 저장하고, 주입할 때도
별도 섹션으로 넣는다.** 이유는 단순하다: 문체 예시(L2)와 같은 자리에 섞이면
모델이 사실을 '이렇게 쓰라는 예시'로 오인하고, 반대로 예시 속 소재를 사실로
착각한다. 그래서 이 모듈은 `voice_hint` 계열이 아니라 **`system_extra` 계열**로
주입된다(파이프라인 배선 참조).

경계선 철학은 여기서도 같다. 사실 기억은 **아는 것만** 적는다 — 추론·예측을
사실로 저장하지 않는다. 만료된 사실(`valid_until` 경과)은 주입하지 않는다.
지난 학기 마감일이 이번 학기 초안에 살아 나오는 것이 이 층의 대표 실패다.
"""
from __future__ import annotations

import hashlib
import json
import threading as _threading
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional

STORE_VERSION = 1
FACTS_PATH = Path("_until_work/facts.json")

#: 사실의 종류 — 자유 문자열을 허용하지 않는다(주입 블록이 종류별로 묶이므로).
FACT_KINDS = ("사안", "인물", "결정", "일정")
#: 보관 상한과 주입 상한을 나눈다. 저장은 넉넉히, 프롬프트는 짧게.
MAX_KEEP = 200
MAX_INJECT = 12
MAX_STATEMENT_CHARS = 300


@dataclass
class Fact:
    fact_id: str
    kind: str                 # FACT_KINDS 중 하나
    subject: str              # 무엇/누구에 대한 사실인가
    statement: str            # 사실 그 자체(한 문장)
    source: str = ""          # 어디서 알게 됐나(공지·메일·본인 입력 등)
    valid_until: str = ""     # ISO 날짜. 비면 만료 없음
    actor_id: str = "local"
    created_at: str = ""

    def is_expired(self, today: Optional[date] = None) -> bool:
        if not self.valid_until:
            return False
        try:
            until = date.fromisoformat(self.valid_until)
        except ValueError:
            return False      # 형식이 깨진 값은 만료로 취급하지 않는다(조용한 삭제 방지)
        return until < (today or date.today())


_TL_PATH = _threading.local()


def set_facts_path_override(p: Optional[Path]) -> None:
    _TL_PATH.value = p


def _resolve_path(path: Optional[Path]) -> Path:
    if path is not None:
        return Path(path)
    o = getattr(_TL_PATH, "value", None)
    return Path(o) if o is not None else FACTS_PATH


def facts_path() -> Path:
    return _resolve_path(None)


def _fact_id(kind: str, subject: str, statement: str) -> str:
    raw = f"{kind}|{subject}|{statement}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def make_fact(kind: str, subject: str, statement: str, *, source: str = "",
              valid_until: str = "", actor_id: str = "local") -> Optional[Fact]:
    """검증된 Fact 하나. 종류가 어휘 밖이거나 내용이 비면 None."""
    kind = str(kind or "").strip()
    subject = " ".join(str(subject or "").split())[:120]
    statement = " ".join(str(statement or "").split())[:MAX_STATEMENT_CHARS]
    if kind not in FACT_KINDS or not statement:
        return None
    if valid_until:
        try:
            date.fromisoformat(str(valid_until))
        except (TypeError, ValueError):
            valid_until = ""     # 깨진 날짜는 '만료 없음'으로(사실 자체는 살린다)
    return Fact(
        fact_id=_fact_id(kind, subject, statement), kind=kind, subject=subject,
        statement=statement, source=" ".join(str(source or "").split())[:120],
        valid_until=str(valid_until or ""), actor_id=actor_id,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))


def load_facts(path: Optional[Path] = None) -> List[Fact]:
    """저장된 사실. 파일 없음·손상·미래 버전은 빈 목록(비치명적)."""
    p = _resolve_path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict) or raw.get("v") != STORE_VERSION:
        return []
    allowed = {f.name for f in fields(Fact)}
    out: List[Fact] = []
    for row in raw.get("facts") or []:
        if not isinstance(row, dict):
            continue
        kwargs = {k: v for k, v in row.items() if k in allowed}
        if kwargs.get("kind") not in FACT_KINDS:
            continue
        if not isinstance(kwargs.get("statement"), str) or not kwargs["statement"]:
            continue
        try:
            out.append(Fact(**kwargs))
        except TypeError:
            continue
    return out


def save_facts(facts: List[Fact], path: Optional[Path] = None) -> Path:
    """전체 교체 저장(원자적). 최신 MAX_KEEP건만 유지."""
    from .. import atomicio
    p = _resolve_path(path)
    payload = {"v": STORE_VERSION,
               "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "facts": [asdict(f) for f in list(facts)[-MAX_KEEP:]]}
    p.parent.mkdir(parents=True, exist_ok=True)
    with atomicio.path_lock(p):
        atomicio.atomic_write_json(p, payload, indent=1)
    return p


def add_fact(fact: Fact, path: Optional[Path] = None) -> bool:
    """같은 fact_id가 있으면 교체, 없으면 추가. 저장 실패는 False."""
    if fact is None:
        return False
    current = [f for f in load_facts(path) if f.fact_id != fact.fact_id]
    current.append(fact)
    try:
        save_facts(current, path)
    except OSError:
        return False
    return True


def remove_fact(fact_id: str, path: Optional[Path] = None) -> bool:
    current = load_facts(path)
    kept = [f for f in current if f.fact_id != fact_id]
    if len(kept) == len(current):
        return False
    try:
        save_facts(kept, path)
    except OSError:
        return False
    return True


def clear_facts(path: Optional[Path] = None) -> None:
    try:
        _resolve_path(path).unlink()
    except OSError:
        pass


def active_facts(today: Optional[date] = None,
                 path: Optional[Path] = None) -> List[Fact]:
    """만료되지 않은 사실만. 종류 순서(FACT_KINDS) → 최신 순으로 정렬."""
    order = {k: i for i, k in enumerate(FACT_KINDS)}
    live = [f for f in load_facts(path) if not f.is_expired(today)]
    live.sort(key=lambda f: (order.get(f.kind, 9), f.created_at), reverse=False)
    return live


# ── 프롬프트 주입 (문체 블록과 절대 섞지 않는다) ─────────────────────

FACTS_HEADER = "【진행 중 사안 — 확인된 사실 (문체 예시가 아니다)】"


def facts_block(facts: Optional[List[Fact]] = None, *,
                today: Optional[date] = None, limit: int = MAX_INJECT,
                path: Optional[Path] = None) -> str:
    """사실 주입 블록. 사실이 없으면 빈 문자열.

    블록 머리에 '문체 예시가 아니다'를 못박는다 — L2 few-shot과 프롬프트 안에서
    가까이 놓일 수 있고, 그때 모델이 사실을 예시 문장으로 흉내 내는 실패가 난다.
    """
    live = facts if facts is not None else active_facts(today, path)
    live = [f for f in live if f.statement.strip()][:max(0, limit)]
    if not live:
        return ""
    lines = [FACTS_HEADER]
    for kind in FACT_KINDS:
        group = [f for f in live if f.kind == kind]
        if not group:
            continue
        lines.append(f"- [{kind}]")
        for f in group:
            subject = f"{f.subject}: " if f.subject else ""
            src = f" (출처: {f.source})" if f.source else ""
            lines.append(f"  · {subject}{f.statement}{src}")
    lines.append(
        "- 위는 **사실**이다. 관련 있는 것만 본문에 자연스럽게 반영하고, 억지로 전부 "
        "끼워 넣지 마라. 여기 없는 사실을 지어내지 말 것 — 없으면 [[DECISION]]으로 남긴다.")
    return "\n".join(lines)


def describe(path: Optional[Path] = None) -> str:
    """CLI·설정 화면용 한 줄 요약 — 무엇이 기억되고 있는지 투명하게."""
    all_facts = load_facts(path)
    if not all_facts:
        return "저장된 사실 없음"
    live = [f for f in all_facts if not f.is_expired()]
    from collections import Counter
    kinds = Counter(f.kind for f in live)
    detail = " · ".join(f"{k} {v}" for k, v in kinds.most_common()) or "없음"
    expired = len(all_facts) - len(live)
    return (f"사실 {len(live)}건({detail})"
            + (f" · 만료 {expired}건" if expired else ""))
