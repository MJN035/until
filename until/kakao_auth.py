"""소셜 로그인(Kakao) — **코어 스텁**. 자체 호스팅에는 계정 계층이 없다.

공통 타입(`AuthError`·`AuthUser`)은 진짜 구현과 마찬가지로 `google_auth`에서
가져온다 — 두 제공자가 같은 사용자 표현을 공유하는 구조를 유지하기 위해서다.
자세한 배경은 `google_auth.py` 참조.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import google_auth as common

AuthError = common.AuthError


@dataclass(frozen=True)
class KakaoConfig:
    client_id: str
    client_secret: str
    redirect_uri: str


def enabled() -> bool:
    return False


def config(origin: str = "") -> Optional[KakaoConfig]:
    return None


def authorize_url(cfg: KakaoConfig, *, state: str, challenge: str) -> str:
    return "/"


def exchange_code(cfg: KakaoConfig, code: str, verifier: str) -> dict:
    raise AuthError("이 배포에는 로그인 계층이 없습니다.")


def fetch_user(access_token: str) -> dict:
    raise AuthError("이 배포에는 로그인 계층이 없습니다.")


def user_from_profile(profile: dict) -> common.AuthUser:
    raise AuthError("이 배포에는 로그인 계층이 없습니다.")
