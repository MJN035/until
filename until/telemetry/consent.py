"""사용자별 텔레메트리 opt-in 동의 상태(결정적·LLM 0).

- 기록 없음(None) = 아직 고지를 못 봄 → fail-closed로 미방출.
- 파일: `<users root>/<uid>/consent.json` — 클라우드에선 `consent:<uid>`로
  KV 미러(웹 `_mirror_user`/`_hydrate_user`가 담당, hist와 같은 계보).
- 어느 선택이든 앱 사용에 제약이 없다(다크패턴 금지 — 설계 문서 참조).
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

NOTICE_VERSION = 1
_DEFAULT_ROOT = Path("_until_work/users")


def consent_path(uid: str, root: Path | None = None) -> Path:
    return (root or _DEFAULT_ROOT) / (uid or "local") / "consent.json"


def get_consent(uid: str, root: Path | None = None) -> bool | None:
    """True/False = 선택 완료, None = 기록 없음(미고지·손상 포함)."""
    try:
        data = json.loads(consent_path(uid, root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("telemetry") if isinstance(data, dict) else None
    return value if isinstance(value, bool) else None


def set_consent(uid: str, granted: bool, root: Path | None = None) -> None:
    """원자 교체 저장(빌링 계보) — 동시 요청·중단에도 반쪽 파일을 남기지 않는다."""
    path = consent_path(uid, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({
        "telemetry": bool(granted),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "notice_version": NOTICE_VERSION,
    }, ensure_ascii=False)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    for _ in range(20):  # Windows: 동시 읽기 중 replace 실패 재시도
        try:
            os.replace(tmp, path)
            return
        except OSError:
            time.sleep(0.02)
    os.replace(tmp, path)
