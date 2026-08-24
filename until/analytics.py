"""웹 분석 태그 — **코어 스텁**. 자체 호스팅은 추적 태그를 넣지 않는다.

빈 문자열을 돌려주면 `web_templates`가 `<script>`도 CSP 출처도 만들지 않는다.
추적을 켜는 것은 그 배포를 운영하는 사람의 선택이지 코어의 기본값이 아니다.
"""
from __future__ import annotations


def configured_ids() -> tuple[str, str]:
    return ("", "")


def browser_loader() -> str:
    return ""


def csp_sources() -> tuple[str, str]:
    return ("", "")
