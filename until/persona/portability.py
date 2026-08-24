"""
페르소나 export / import — 크로스채널의 실질적 전제조건.

"나중에 어디서든 같은 페르소나를 쓴다"는 말은 결국 **페르소나를 통째로 들고 나갈 수
있다**는 뜻이다. 그게 안 되면 채널을 늘려도 페르소나는 이 앱에 갇혀 있다.
동시에 이것이 사용자 데이터 이동권 대응이기도 하다 — 같은 함수 하나가 두 요구를 만족한다.

프라이버시 설계:
  · **개인 식별 정보는 본문과 분리**해 최상위 `identity` 절에만 담는다.
    본문 절(persona/facts/episodes/events)에는 이름·학번·연락처가 섞이지 않는다.
    `include_identity=False`로 빼고 내보낼 수 있어야, 문체 프로파일만 다른 기기·
    다른 사람에게 공유하는 일이 안전해진다.
  · 무거운 원문(에피소드·이벤트)은 **기본적으로 뺀다**. 문체를 옮기는 데는 필요 없고,
    실수로 과제 원문 전체를 파일 하나로 유출시키는 것이 가장 흔한 사고다.

import는 **예외를 던지지 않는다.** 남의 파일·구버전·손상 파일이 들어오는 경로라
거부 이유를 담은 dict를 돌려주는 편이 호출자(웹 핸들러·CLI)에게 쓸모 있다.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1
#: 최상위에 허용하는 절. 이 밖의 키가 있으면 import를 거부한다(조용한 무시 금지).
SECTIONS = ("schema_version", "exported_at", "app_version", "identity",
            "persona", "facts", "episodes", "edit_events", "persona_events")


def _app_version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version("until-mcp")
        except PackageNotFoundError:
            return ""
    except Exception:
        return ""


# ── export ──────────────────────────────────────────────────────────

def export_persona(*, include_identity: bool = True,
                   include_episodes: bool = False,
                   include_events: bool = False,
                   include_edits: bool = False) -> Dict[str, Any]:
    """페르소나 전체를 하나의 dict로. 어떤 스토어가 없어도 예외 없이 빈 값이다.

    기본값이 '문체만'인 이유는 모듈 docstring 참조(원문 유출 사고 방지).
    """
    from ..context.tone import load_persona

    payload: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app_version": _app_version(),
    }

    if include_identity:
        try:
            from ..profile import load_profile
            payload["identity"] = dict(load_profile())
        except Exception:
            payload["identity"] = {}

    try:
        store = load_persona()
        card = store.style_card
        payload["persona"] = {
            "base": {"actor_id": store.base.actor_id,
                     "defaults": dict(store.base.defaults),
                     "source": store.base.source},
            "registers": {k: {"delta": dict(v.delta), "pinned": bool(v.pinned)}
                          for k, v in sorted(store.registers.items())},
            "pinned_register": store.pinned_register,
            "style_card": card.to_dict() if card is not None else None,
        }
    except Exception:
        payload["persona"] = {}

    try:
        from ..context.facts import load_facts
        payload["facts"] = [asdict(f) for f in load_facts()]
    except Exception:
        payload["facts"] = []

    if include_episodes:
        try:
            from ..context.episodes import load_episodes
            payload["episodes"] = [asdict(e) for e in load_episodes()]
        except Exception:
            payload["episodes"] = []
    if include_edits:
        try:
            from ..context.edit_events import load_edit_events
            payload["edit_events"] = [asdict(e) for e in load_edit_events()]
        except Exception:
            payload["edit_events"] = []
    if include_events:
        try:
            from .events import load_events
            payload["persona_events"] = [asdict(e) for e in load_events()]
        except Exception:
            payload["persona_events"] = []
    return payload


# ── import ──────────────────────────────────────────────────────────

def _reject(reason: str, **extra) -> Dict[str, Any]:
    out = {"ok": False, "reason": reason, "imported": {}}
    out.update(extra)
    return out


def import_persona(payload: Any, *, replace: bool = False,
                   include_identity: bool = True) -> Dict[str, Any]:
    """export 결과를 되돌려 넣는다. 성공/거부 모두 dict로 보고(예외 없음).

    `replace=False`(기본)면 기존 값을 지우지 않고 **없는 것만 채운다** — 남의 파일을
    잘못 열었을 때 내 페르소나가 통째로 날아가는 사고를 막는다.
    """
    if not isinstance(payload, dict):
        return _reject("payload가 JSON 객체가 아닙니다")
    version = payload.get("schema_version")
    if version != SCHEMA_VERSION:
        return _reject(f"스키마 버전 불일치(기대 {SCHEMA_VERSION}, 실제 {version!r})")
    unknown = sorted(set(payload) - set(SECTIONS))
    if unknown:
        return _reject(f"알 수 없는 최상위 키: {', '.join(unknown)}")

    imported: Dict[str, int] = {}
    warnings: List[str] = []

    # 1) 페르소나(문체 규격) — 검증은 tone.sanitize_delta가 전담한다.
    persona = payload.get("persona")
    if isinstance(persona, dict) and persona:
        try:
            from ..context.style_card import StyleCard, merge_card
            from ..context.tone import (PersonaBase, PersonaStore, REGISTER_PRESETS,
                                        RegisterOverride, load_persona,
                                        sanitize_delta, save_persona)
            current = PersonaStore() if replace else load_persona()
            base_raw = persona.get("base") or {}
            incoming_defaults = sanitize_delta(base_raw.get("defaults"))
            if incoming_defaults and (replace or not current.base.defaults):
                current.base = PersonaBase(
                    actor_id=str(base_raw.get("actor_id") or "local")[:64],
                    defaults=incoming_defaults,
                    source=str(base_raw.get("source") or "import")[:32])
            n_reg = 0
            for key, item in (persona.get("registers") or {}).items():
                if key not in REGISTER_PRESETS or not isinstance(item, dict):
                    warnings.append(f"알 수 없는 레지스터 건너뜀: {key}")
                    continue
                if not replace and key in current.registers:
                    continue
                delta = sanitize_delta(item.get("delta"))
                if delta or item.get("pinned"):
                    current.registers[key] = RegisterOverride(
                        delta=delta, pinned=bool(item.get("pinned")))
                    n_reg += 1
            pinned = persona.get("pinned_register")
            if pinned in REGISTER_PRESETS and (replace or not current.pinned_register):
                current.pinned_register = pinned
            card_raw = persona.get("style_card")
            if isinstance(card_raw, dict):
                incoming = StyleCard.from_dict(card_raw)
                existing = current.style_card if current.style_card is not None \
                    else StyleCard()
                current.style_card = incoming if replace else merge_card(existing, incoming)
            save_persona(current)
            imported["persona"] = 1
            imported["registers"] = n_reg
        except Exception as exc:
            return _reject(f"페르소나 반영 실패: {type(exc).__name__}: {exc}",
                           imported=imported)

    # 2) 사실 기억 — 종류 어휘 검증은 facts.make_fact가 전담한다.
    facts_raw = payload.get("facts")
    if isinstance(facts_raw, list) and facts_raw:
        try:
            from ..context.facts import load_facts, make_fact, save_facts
            existing = [] if replace else load_facts()
            seen = {f.fact_id for f in existing}
            added = 0
            for row in facts_raw:
                if not isinstance(row, dict):
                    continue
                fact = make_fact(row.get("kind"), row.get("subject"),
                                 row.get("statement"), source=row.get("source", ""),
                                 valid_until=row.get("valid_until", ""))
                if fact is None or fact.fact_id in seen:
                    continue
                existing.append(fact)
                seen.add(fact.fact_id)
                added += 1
            save_facts(existing)
            imported["facts"] = added
        except Exception as exc:
            warnings.append(f"사실 기억 반영 실패: {type(exc).__name__}")

    # 3) 신상 정보 — 본문과 분리된 절이라 따로 끌 수 있다.
    identity = payload.get("identity")
    if include_identity and isinstance(identity, dict) and identity:
        try:
            from ..profile import load_profile, merge_from_lms, save_profile
            if replace:
                save_profile({str(k): str(v) for k, v in identity.items()})
            else:
                merge_from_lms({str(k): str(v) for k, v in identity.items()})
            imported["identity"] = len(load_profile())
        except Exception:
            warnings.append("신상 정보 반영 실패")

    return {"ok": True, "reason": "", "imported": imported, "warnings": warnings,
            "replace": bool(replace)}


# ── 파일 입출력 + CLI ────────────────────────────────────────────────

def export_to_file(path: Path, **kwargs) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(export_persona(**kwargs), ensure_ascii=False, indent=1),
                 encoding="utf-8")
    return p


def import_from_file(path: Path, **kwargs) -> Dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _reject(f"파일을 읽을 수 없습니다: {type(exc).__name__}: {exc}")
    return import_persona(payload, **kwargs)


def main(argv: Optional[list] = None) -> int:
    """`python -m until.persona.portability --export out.json | --import in.json`.

    옵션: `--with-episodes` `--with-events` `--with-edits`(원문 포함 — 기본 제외),
    `--no-identity`(신상 제외), `--replace`(import 시 기존 값 교체).
    """
    import sys
    args = list(argv if argv is not None else sys.argv[1:])
    flags = {a for a in args if a.startswith("--")}
    identity = "--no-identity" not in flags

    if "--export" in args:
        idx = args.index("--export")
        out = args[idx + 1] if len(args) > idx + 1 else "persona-export.json"
        path = export_to_file(
            Path(out), include_identity=identity,
            include_episodes="--with-episodes" in flags,
            include_events="--with-events" in flags,
            include_edits="--with-edits" in flags)
        size = path.stat().st_size
        print(f"내보냄: {path} ({size:,}B)")
        if not identity:
            print("  (신상 정보 제외됨)")
        if not (flags & {"--with-episodes", "--with-events", "--with-edits"}):
            print("  (원문 기록 제외 — 문체·사실만. 포함하려면 --with-episodes 등)")
        return 0

    if "--import" in args:
        idx = args.index("--import")
        if len(args) <= idx + 1:
            print("가져올 파일 경로가 필요합니다: --import <파일>")
            return 2
        result = import_from_file(Path(args[idx + 1]), replace="--replace" in flags,
                                  include_identity=identity)
        if not result.get("ok"):
            print(f"거부됨: {result.get('reason')}")
            return 1
        print(f"가져옴: {result.get('imported')}")
        for w in result.get("warnings") or []:
            print(f"  경고: {w}")
        return 0

    print("사용법: python -m until.persona.portability --export <파일> | --import <파일>")
    print("  옵션: --with-episodes --with-events --with-edits --no-identity --replace")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
