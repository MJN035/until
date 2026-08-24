"""웹 페이지 셸 렌더링 — Jinja2 경계, 미설치 시 mock용 결정적 폴백."""
from __future__ import annotations

from functools import lru_cache
from html import escape
from pathlib import Path

_ROOT = Path(__file__).parent / "templates"


@lru_cache(maxsize=1)
def _template():
    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
    except ImportError:
        return None
    env = Environment(loader=FileSystemLoader(str(_ROOT)),
                      autoescape=select_autoescape(("html", "xml")))
    return env.get_template("base.html")


@lru_cache(maxsize=1)
def _asset_version() -> str:
    """정적 자산 내용 해시 — 배포마다 URL이 바뀌어 브라우저 캐시가 즉시 갱신된다.

    (배포 후에도 옛 app.css가 캐시로 남아 새 디자인이 안 보이던 실사용 문제.)"""
    import hashlib
    root = Path(__file__).parent / "webassets"
    h = hashlib.sha1()
    for name in ("app.css", "app.js"):
        try:
            h.update((root / name).read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:8]


def render_page(body: str, backend: str, title: str, account: str = "") -> str:
    """신뢰된 서버 렌더 fragment를 공통 셸에 넣는다.

    fragment 내부 사용자 값은 기존 render_* 함수에서 escape한다. Jinja2에는
    body·account만 명시적으로 safe로 전달하고 title/backend는 자동 escape한다.
    (account = 상단 바 계정 슬롯. 서버가 만든 fragment이며 이메일·이름은
     _account_html에서 escape한다.)
    """
    from .analytics import browser_loader
    analytics = browser_loader()
    # 상단 '작업 환경' 칩은 값이 있을 때만 그린다 — 빈 문자열이면 칩 자체가 없다.
    # 조건 분기를 템플릿이 아니라 여기서 처리하는 이유: 아래 Jinja2 미설치 폴백은
    # 단순 치환이라 {% if %}를 해석하지 못한다. 두 렌더러가 갈라지면 의존성 0
    # 계약(불변규칙 2)에서만 마크업이 새어 나온다.
    chip = f'<div class="m">작업 환경 · {escape(backend)}</div>' if backend else ""
    template = _template()
    if template is not None:
        return template.render(body=body, backend_chip=chip, title=title,
                               account=account, analytics=analytics,
                               asset_v=_asset_version())
    # 의존성 0 mock 계약: Jinja2가 없어도 동일 템플릿으로 테스트·CLI가 산다.
    source = (_ROOT / "base.html").read_text(encoding="utf-8")
    return (source.replace("{{ title }}", escape(title))
                  .replace("{{ backend_chip | safe }}", chip)
                  .replace("{{ body | safe }}", body)
                  .replace("{{ account | safe }}", account)
                  .replace("{{ analytics | safe }}", analytics)
                  .replace("{{ asset_v }}", _asset_version()))
