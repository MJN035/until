# WARNING: 이 스크립트는 신뢰된 로컬 파일에만 쓴다. 서버에서 호출 금지.
"""One-time migration of trusted local v1 pickle sessions to signed v2 JSON."""
from __future__ import annotations

import argparse
import pickle
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from until.session_store import encode, to_jsonable


def _meta(result) -> dict:
    spec = getattr(result, "spec", None) or {}
    title = str(spec.get("title") or spec.get("deliverable")
                or spec.get("goal") or "과제").strip()[:70]
    try:
        from until.readiness import assess_readiness
        n_warnings = len(assess_readiness(result).warnings) if result is not None else 0
    except Exception:
        n_warnings = 0
    deadline = getattr(result, "deadline", None)
    return {
        "title": title or "과제",
        "task_type": spec.get("task_type") or "",
        "n_dec": result.draft.n_decisions if getattr(result, "draft", None) else 0,
        "final": getattr(result, "final_draft", None) is not None,
        "n_warnings": n_warnings,
        "deadline": deadline.due.isoformat() if deadline is not None else "",
    }


def migrate(root: Path, *, apply: bool, delete_old: bool) -> int:
    converted = []
    failed = []
    for source in sorted(root.rglob("*.pkl")) if root.exists() else []:
        try:
            legacy = pickle.loads(source.read_bytes())
            if type(legacy) is not dict or legacy.get("v") not in (None, 1):
                raise ValueError("v1 세션이 아님")
            payload = {key: legacy.get(key) for key in
                       ("result", "answers", "suggestions", "review")}
            to_jsonable(payload)  # dry-run에서도 전체 객체 그래프를 검증한다.
            target = source.with_suffix(".json")
            if apply:
                ts = float(legacy.get("ts") or source.stat().st_mtime or time.time())
                target.write_bytes(encode(payload, ts, _meta(payload["result"])))
                if delete_old:
                    source.unlink()
            converted.append((source, target))
        except Exception as exc:
            failed.append((source, str(exc)))

    mode = "APPLY" if apply else "DRY-RUN"
    for source, target in converted:
        print(f"[{mode}] {source} -> {target}")
    for source, error in failed:
        print(f"[SKIP] {source}: {error}")
    print(f"완료: 변환 가능/완료 {len(converted)}개, 실패 {len(failed)}개")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="신뢰된 로컬 v1 pickle 세션을 서명 JSON v2로 변환")
    parser.add_argument("root", nargs="?", type=Path,
                        default=Path("_until_work/web_sessions"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="검사만 수행(기본값)")
    mode.add_argument("--apply", action="store_true", help=".json 파일 작성")
    parser.add_argument("--delete-old", action="store_true",
                        help="성공한 원본 .pkl 삭제(--apply 필요)")
    args = parser.parse_args()
    if args.delete_old and not args.apply:
        parser.error("--delete-old는 --apply와 함께 사용해야 합니다")
    return migrate(args.root, apply=args.apply, delete_old=args.delete_old)


if __name__ == "__main__":
    raise SystemExit(main())
