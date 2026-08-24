"""로그인 계정의 eTL 토큰 보관 — 암호화 저장, 계정에만, 언제든 해제(결정적·LLM 0).

**왜 만드나.** 콜드 스타트의 세 번째 벽이 eTL 토큰 발급 왕복이다(2026-08-22 실사용
원장 F2). 한 번 연결한 사람에게 매번 다시 시키는 건 이 제품에서 가장 비싼 요구다.

**왜 조심하나.** eTL 액세스 토큰은 **그 사람의 LMS 계정 전체를 여는 열쇠**다.
성적·제출물·개인정보가 전부 그 뒤에 있다. 그래서 범위를 좁혀 뒀다:

- **로그인 계정에만.** 익명 uid 쿠키는 저장 대상이 아니다 — 쿠키 하나가 새면
  남의 LMS가 열린다. 계정은 최소한 구글·카카오 인증 뒤에 있다.
- **암호화해서 저장한다.** 평문이면 KV 자격증명 하나만 새도 전원의 eTL이 열린다.
  키는 `UNTIL_SESSION_KEY`(Render env), 암호문은 디스크·Cloudflare KV — **서로 다른
  시스템**이라 한쪽 유출로는 못 읽는다. 세션 키가 없으면 **저장하지 않는다**
  (fail-closed — 암호화 못 할 바엔 안 하는 게 낫다).
- **사용자가 켠 경우에만.** 기본은 꺼짐. `/connect`의 체크박스로 켜고,
  `/profile`에서 지운다. 켜져 있다는 사실을 화면에 계속 밝힌다.
- **만료가 있다.** eTL 토큰은 만료일을 비우면 무기한이라 우리가 상한을 둔다.

**암호는 직접 만들지 않았다.** stdlib만으로 되는 표준 구성이다 —
HMAC-SHA256 카운터 모드 키스트림(PRF) + encrypt-then-MAC, 키는 용도별로 분리
유도(`_derive`), 논스는 매번 새로. 새 알고리즘이 아니라 조립이다.
(`cryptography` 의존성을 넣지 않는 이유: 이 저장소는 `dependencies = []`이고
불변규칙 2가 "키·인터넷 없이 모든 테스트 통과"를 요구한다.)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from typing import Optional

from . import atomicio

#: 보관 상한(초). eTL 토큰은 발급 시 만료일을 비우면 무기한이므로 우리가 끊는다.
#: 한 학기보다 조금 긴 값 — 학기가 바뀌면 어차피 다시 연결하는 게 맞다.
TTL_SECONDS = 150 * 24 * 3600

_NONCE_BYTES = 16
_TAG_BYTES = 32


def _derive(purpose: str, nonce: bytes) -> bytes:
    from .session_store import session_key
    return hmac.new(session_key(), purpose.encode("utf-8") + nonce,
                    hashlib.sha256).digest()


def _keystream(key: bytes, n: int) -> bytes:
    """HMAC-SHA256 카운터 모드 — 블록마다 다른 카운터로 PRF를 돌려 이어 붙인다."""
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:n])


def _xor(data: bytes, stream: bytes) -> bytes:
    # strict=True — 키스트림 길이가 본문과 다르면 조용히 잘려 평문이 남는다.
    return bytes(a ^ b for a, b in zip(data, stream, strict=True))


def encrypt(plain: str) -> Optional[dict]:
    """토큰 → 봉투(dict). 세션 키가 없으면 None(저장하지 않는다)."""
    try:
        from .session_store import session_key
        if not session_key():
            return None
    except Exception:
        return None
    nonce = secrets.token_bytes(_NONCE_BYTES)
    raw = plain.encode("utf-8")
    body = _xor(raw, _keystream(_derive("etl-token-enc", nonce), len(raw)))
    # encrypt-then-MAC — 논스와 암호문을 함께 인증한다(논스 바꿔치기 방지).
    tag = hmac.new(_derive("etl-token-mac", nonce), nonce + body,
                   hashlib.sha256).digest()
    return {"v": 1,
            "n": base64.b64encode(nonce).decode(),
            "c": base64.b64encode(body).decode(),
            "t": base64.b64encode(tag).decode()}


def decrypt(env: dict) -> str:
    """봉투 → 토큰. 위조·키 불일치·형식 오류는 전부 ""(예외를 내지 않는다)."""
    try:
        if not isinstance(env, dict) or env.get("v") != 1:
            return ""
        nonce = base64.b64decode(env["n"])
        body = base64.b64decode(env["c"])
        tag = base64.b64decode(env["t"])
        if len(nonce) != _NONCE_BYTES or len(tag) != _TAG_BYTES:
            return ""
        want = hmac.new(_derive("etl-token-mac", nonce), nonce + body,
                        hashlib.sha256).digest()
        if not hmac.compare_digest(tag, want):
            return ""   # 위조되었거나 세션 키가 바뀌었다 — 조용히 없는 셈 친다
        return _xor(body, _keystream(_derive("etl-token-enc", nonce),
                                     len(body))).decode("utf-8")
    except Exception:
        return ""


def save(path: Path, token: str, *, now: float | None = None) -> bool:
    """토큰 보관. 저장했으면 True. 세션 키가 없으면 저장하지 않고 False."""
    token = (token or "").strip()
    if not token:
        return False
    env = encrypt(token)
    if env is None:
        return False
    env["exp"] = (time.time() if now is None else now) + TTL_SECONDS
    p = Path(path)
    with atomicio.path_lock(p):
        p.parent.mkdir(parents=True, exist_ok=True)
        atomicio.atomic_write_json(p, env)
    return True


def load(path: Path, *, now: float | None = None) -> str:
    """보관된 토큰. 없음·만료·복호 실패는 전부 ""(호출부는 평소처럼 물어보면 된다)."""
    p = Path(path)
    try:
        env = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(env, dict):
        return ""
    exp = env.get("exp")
    clock = time.time() if now is None else now
    if isinstance(exp, (int, float)) and exp <= clock:
        clear(p)          # 만료본을 남겨 두지 않는다
        return ""
    return decrypt(env)


def clear(path: Path) -> None:
    """보관 해제 — 파일을 지운다(실패는 비치명)."""
    try:
        Path(path).unlink()
    except OSError:
        pass


def has_token(path: Path, *, now: float | None = None) -> bool:
    """화면 표시용 — 보관 중인지만. 토큰 값을 반환하지 않는다."""
    return bool(load(path, now=now))
