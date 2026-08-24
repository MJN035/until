"""제출 확인 nonce 원장 — 1회용, content_hash 바인딩.

사람이 '이 정확한 본문'을 확인 화면에서 보고 '제출'을 눌렀다는 증거. 발급된
nonce는 그 plan의 content_hash에 묶이고(본문 변조 시 무효), 한 번만 소비된다
(리플레이·재전송 차단).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from .. import atomicio

_DEFAULT = Path("_until_work") / "submit_nonce.jsonl"
NONCE_TTL = 10 * 60


def _resolve(path) -> Path:
    return Path(path) if path is not None else _DEFAULT


def issue_nonce(content_hash: str, *, path=None, token: Optional[str] = None,
                binding: str = "", now: Optional[float] = None) -> str:
    """새 nonce 발급 후 원장에 append. token 주입은 테스트 결정성용.

    consume_nonce와 같은 경로락을 잡는다 — 잠금 없이 append하면, 마침 다른
    요청이 consume_nonce의 통째 재작성 중일 때 이 append가 그 재작성에
    덮여 사라질 수 있다(원장 유실)."""
    nonce = token or os.urandom(16).hex()
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    clock = time.time() if now is None else now
    row = {"nonce": nonce, "content_hash": content_hash, "binding": binding,
           "issued_at": clock, "consumed": False}
    with atomicio.path_lock(p):
        rows = _read_rows(p)
        rows = [r for r in rows
                if clock - float(r.get("issued_at", clock)) <= NONCE_TTL]
        rows.append(row)
        blob = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        atomicio.atomic_write_bytes(p, blob.encode("utf-8"))
    return nonce


def _read_rows(p: Path) -> list[dict]:
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except (ValueError, TypeError):
            continue  # 손상 행은 조용히 건너뛴다(원장 견고성)
    return rows


def consume_nonce(nonce: str, content_hash: str, *, path=None, binding: str = "",
                  now: Optional[float] = None) -> bool:
    """존재·해시 일치·미소비일 때만 True + 소비 마킹(원장 재기록).

    경로락(스레드 내부 + OS 파일락) 안에서 읽기→수정→원자적 재작성을 한 번에
    수행한다 — 동시에 같은 nonce를 소비하려는 두 요청 중 정확히 하나만
    성공해야 리플레이 방지 목적이 성립한다(잠금 없는 통째 덮어쓰기는 둘 다
    "미소비"를 읽고 둘 다 성공 처리할 수 있었다)."""
    p = _resolve(path)
    with atomicio.path_lock(p):
        clock = time.time() if now is None else now
        rows = _read_rows(p)
        ok = False
        for r in rows:
            if (r.get("nonce") == nonce and r.get("content_hash") == content_hash
                    and r.get("binding", "") == binding
                    and clock - float(r.get("issued_at", clock)) <= NONCE_TTL
                    and not r.get("consumed")):
                r["consumed"] = True
                ok = True
                break
        kept = [r for r in rows
                if clock - float(r.get("issued_at", clock)) <= NONCE_TTL]
        if ok or len(kept) != len(rows):
            blob = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept)
            atomicio.atomic_write_bytes(p, blob.encode("utf-8"))
    return ok
