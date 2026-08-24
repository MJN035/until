"""소셜 로그인(Google) — **코어 스텁**. 자체 호스팅에는 계정 계층이 없다.

Until 코어는 로그인 없이 동작한다. 계정·세션·도메인 제한은 운영 배포(비공개)의
관심사이고, 이 모듈은 코어가 기대하는 **인터페이스만** 유지한다.

`enabled()`가 False면 `until/web.py`·`until/asgi.py`의 로그인 표면이 통째로 닫히고
익명 uid 쿠키 경로로 떨어진다 — 이건 원래 코드가 **환경변수를 안 준 로컬 실행에서
이미 타던 경로**다(진짜 구현의 `enabled()`도 `UNTIL_GOOGLE_CLIENT_ID`/`_SECRET`이
둘 다 있을 때만 True). 스텁은 그 상태를 영구화할 뿐, 새 동작을 만들지 않는다.

운영 배포는 이 파일을 실제 구현으로 교체한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

STATE_TTL = 600.0
AUTH_COOKIE_TTL = 60 * 60 * 24 * 30


class AuthError(Exception):
    """인증 실패. 코어에서는 발생하지 않지만 예외 처리 경로가 참조한다."""


@dataclass(frozen=True)
class GoogleConfig:
    client_id: str
    client_secret: str
    redirect_uri: str


@dataclass(frozen=True)
class AuthUser:
    sub: str
    email: str = ""
    name: str = ""
    uid: str = ""
    provider: str = "google"

    @property
    def label(self) -> str:
        return (self.name or self.email.split("@", 1)[0] or "내 계정")[:24]


def enabled() -> bool:
    return False


def any_enabled() -> bool:
    return False


def require_login() -> bool:
    return False


def allowed_domain() -> str:
    return ""


def config(origin: str = "") -> Optional[GoogleConfig]:
    return None


def sign(payload: dict, ttl: float) -> str:
    return ""


def unsign(blob: str) -> Optional[dict]:
    return None


def new_pkce() -> Tuple[str, str]:
    return ("", "")


def authorize_url(cfg: GoogleConfig, *, state: str, challenge: str,
                  nonce: str, login_hint: str = "") -> str:
    return "/"


def exchange_code(cfg: GoogleConfig, code: str, verifier: str) -> dict:
    raise AuthError("이 배포에는 로그인 계층이 없습니다.")


def decode_id_token(id_token: str, *, client_id: str, nonce: str = "",
                    now: Optional[float] = None) -> dict:
    raise AuthError("이 배포에는 로그인 계층이 없습니다.")


def user_from_claims(claims: dict) -> AuthUser:
    raise AuthError("이 배포에는 로그인 계층이 없습니다.")


def pack_user(user: AuthUser) -> str:
    return ""


def unpack_user(blob: str) -> Optional[AuthUser]:
    return None


def safe_next(path: str) -> str:
    """열린 리다이렉트 방지 — 스텁도 이 성질은 지킨다(경로만 허용)."""
    if not path or not path.startswith("/") or path.startswith("//"):
        return "/"
    return path
