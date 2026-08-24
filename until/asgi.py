"""Until의 점진 전환용 FastAPI/HTMX 표면.

기존 ``until.web`` URL을 한 번에 교체하지 않는다. 읽기 화면과 텍스트 초안 생성
경계를 먼저 ASGI로 제공하고, 검증된 라우트부터 이쪽으로 옮긴다.
"""
from __future__ import annotations

from pathlib import Path
from contextlib import contextmanager
import contextvars
import hashlib
import html
import hmac
import json
import logging
import os
import secrets
import urllib.parse
from typing import Dict, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .config import Config


class DraftRequest(BaseModel):
    assignment: str = Field(min_length=1, max_length=100_000)


class EtlDraftRequest(BaseModel):
    url: str = Field(min_length=12, max_length=1000)
    token: str = Field(min_length=1, max_length=500)


class InboxRequest(BaseModel):
    token: str = Field(min_length=1, max_length=500)
    only_unsubmitted: bool = False
    hide_past: bool = False


class FinalizeRequest(BaseModel):
    answers: Dict[int, str]


class ProfileRequest(BaseModel):
    values: Dict[str, str]


class RedeemRequest(BaseModel):
    code: str = Field(min_length=1, max_length=200)


def _cfg(default_backend: str, requested: Optional[str] = None) -> Config:
    cfg = Config()
    cfg.backend = requested or default_backend or cfg.backend
    return cfg


def _store_result(result, *, source: str = "manual", backend: str = "mock",
                  url: str = "", uid: str = "") -> str:
    from . import web
    token = web._new_token()
    web._SESSIONS[token] = result
    web._telemetry_begin(token, result, source=source, backend=backend, url=url)
    web._persist_session(token)
    web._telemetry_emit("draft", token, result, uid=uid)
    return token


# 요청 스코프의 로그인 사용자 — 미들웨어가 넣고, 작업 스레드의 _user_scope가 읽어
# web._REQ.auth로 옮긴다(상단 바 계정 슬롯 _account_html이 이 값을 본다).
# anyio.to_thread가 컨텍스트를 복사하므로 스레드풀 안에서도 그대로 보인다.
_AUTH_CTX: "contextvars.ContextVar[object]" = contextvars.ContextVar("until_auth", default=None)


def _scope_personal_stores(root) -> None:
    """개인 저장소 경로를 요청 스코프로 건다(root=None이면 해제).

    stdlib 서버의 `web._begin_request`와 **같은 목록**을 유지해야 한다. 새 저장소를
    더할 때 한쪽만 고치면 다른 서버에서 사용자 데이터가 섞인다."""
    from .context.course_profiles import set_course_profiles_path_override
    from .context.edit_events import set_edit_events_path_override
    from .context.episodes import set_episodes_path_override
    from .context.facts import set_facts_path_override
    from .context.tone import set_persona_path_override
    from .persona.events import set_events_path_override
    setters = (
        (set_episodes_path_override, "episodes.jsonl"),
        (set_facts_path_override, "facts.json"),
        (set_edit_events_path_override, "edit_events.jsonl"),
        (set_persona_path_override, "persona.json"),
        (set_events_path_override, "persona_events.jsonl"),
        (set_course_profiles_path_override, "course_profiles.json"),
    )
    for setter, name in setters:
        try:
            setter((root / name) if root is not None else None)
        except Exception:
            pass


@contextmanager
def _user_scope(uid: str, *, cloud: bool, mutate: bool = False):
    """레거시와 동일한 uid별 저장 경로·KV 수명주기를 ASGI 작업 스레드에 건다."""
    from . import billing, web
    from .context.answer_history import set_history_path_override
    from .profile import set_profile_path_override
    web._REQ.uid = uid if cloud else ""
    web._REQ.auth = _AUTH_CTX.get() if cloud else None
    hydrated = True
    if cloud:
        root = web._user_root(uid)
        set_history_path_override(root / "answer_history.jsonl")
        set_profile_path_override(root / "profile.json")
        # stdlib 서버(`web._begin_request`)가 격리하는 저장소와 **같은 목록**이어야
        # 한다. 여기에 빠지면 운영(ASGI)에서만 모든 사용자의 기억·편집 기록이
        # 전역 파일 하나에 섞인다 — 실제로 4개가 빠져 있었다(2026-08-20).
        _scope_personal_stores(root)
        billing.set_usage_path_override(root / "usage.json")
        billing.set_credits_path_override(root / "credits.json")
        hydrated = web._hydrate_user(uid)
        web._hydrate_global()
    # 텔레메트리 KV 미러 게이트가 읽는 플래그(_telemetry_emit) — 절단 방지 계보.
    web._REQ.hydrated_ok = bool(cloud and hydrated)
    try:
        yield
    finally:
        if cloud:
            if mutate and hydrated:
                web._mirror_user(uid)
            set_history_path_override(None)
            set_profile_path_override(None)
            _scope_personal_stores(None)
            billing.set_usage_path_override(None)
            billing.set_credits_path_override(None)
            web._REQ.uid = ""
        web._REQ.auth = None
        web._REQ.hydrated_ok = False


def _scoped(uid: str, cloud: bool, mutate: bool, fn, *args, **kwargs):
    with _user_scope(uid, cloud=cloud, mutate=mutate):
        return fn(*args, **kwargs)


def _charge_before(backend: str, cloud: bool) -> None:
    """생성 전 한도 검사. 막힌 **이유를 구분**해서 올린다.

    잔액 부족과 전역 일일 상한은 사용자가 할 수 있는 일이 정반대다 — 전자는
    충전하면 풀리고, 후자는 운영자만 풀 수 있어 충전해도 소용이 없다. 둘 다
    `credits_required`로 뭉뚱그리면 사용자가 돈을 내고도 못 쓴다.
    """
    if backend == "mock":
        return
    from . import billing
    if not billing.can_draft():
        raise HTTPException(status_code=402, detail="credits_required")
    if cloud and not billing.global_can_draft():
        raise HTTPException(status_code=402, detail="global_daily_limit")


def _admin_event(uid: str, event: str, *, token: str = "") -> None:
    """요청 흐름과 분리된 관리자 이벤트 적립(실패는 비치명)."""
    try:
        from . import adminboard, web
        from .profile import load_profile
        actual_uid = uid or "local"
        adminboard.record_event(web._user_root(actual_uid), actual_uid, event,
                                token=token, profile=load_profile())
    except Exception:
        pass


def _draft_result(uid: str, build):
    """파이프라인 실패를 적립하고 원래 예외는 호출자에게 보존한다."""
    try:
        return build()
    except Exception:
        _admin_event(uid, "draft_fail:pipe")
        raise


def _admin_export(uid: str, token: str, result) -> None:
    """초안 내보내기의 미응답 결정과 내보내기 1회를 함께 적립한다."""
    from . import web
    if result.final_draft is None:
        unanswered = result.draft.n_decisions - len(web._ANSWERS.get(token, {}))
        for _ in range(max(0, unanswered)):
            _admin_event(uid, "decision_skip")
    _admin_event(uid, "export")
    web._telemetry_emit("export", token, result, uid=uid)


def _charge_after(backend: str, cloud: bool, uid: str, token: str = "",
                  result=None) -> None:
    if backend != "mock":
        from . import billing
        billing.record_draft()
        if cloud:
            billing.record_global_draft()
    guard = getattr(result, "guard", None)
    event = "draft_fail:guard" if guard is not None and not guard.passed else "draft"
    _admin_event(uid, event, token=token)


def _result_json(token: str, result) -> dict:
    from .readiness import assess_readiness
    return {
        "session": token,
        "task_type": (result.spec or {}).get("task_type", ""),
        "draft": result.draft.body,
        "decisions": [d.note for d in result.draft.decisions],
        "readiness": assess_readiness(result).to_dict(),
        "html_url": f"/v/{token}",
    }


def create_app(backend: str | None = None, *, cloud: bool = False) -> FastAPI:
    from . import web
    web.CLOUD = bool(cloud)
    default_backend = backend or Config().backend
    app = FastAPI(title="Until", version="0.1", docs_url="/api/docs")
    assets = Path(__file__).parent / "webassets"
    app.mount("/asset", StaticFiles(directory=str(assets)), name="asset")

    from .academic_policy import AiUseProhibitedError

    @app.exception_handler(AiUseProhibitedError)
    async def ai_use_prohibited_handler(request: Request, exc: AiUseProhibitedError):
        if not request.url.path.startswith("/api/"):
            body = ('<div class="sec"><h1>이 과제는 AI 사용 금지입니다</h1>'
                    '<p>Until은 초안이나 답안을 생성하지 않았습니다. 과제 지시를 '
                    '직접 따라 작성하세요.</p><p><a class="btn ghost" href="/simple">'
                    '← 돌아가기</a></p></div>')
            return HTMLResponse(web._wrap(body, default_backend, "AI 사용 금지 · UNTIL"),
                                status_code=422)
        return JSONResponse({"detail": str(exc), "code": "ai_use_prohibited"},
                            status_code=422)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        """처리 못 한 예외를 맨 "Internal Server Error"로 내보내지 않는다.

        stdlib 서버(`web._Handler`)는 /finalize·/suggest의 2차 패스 실패를
        `user_error_message()` 안내로 바꿔 보내는데, **운영 엔트리포인트인 ASGI에는
        그 그물이 아예 없었다** — 제공자 오류 한 번이 다섯 단어 영문으로 그대로
        사용자에게 갔다(실사용 보고 2026-08-21: 연습 모드 완성하기 → Internal
        Server Error). 로컬 stdlib으로 아무리 재현해도 안 잡히던 이유이기도 하다.

        Starlette는 이 핸들러의 응답을 보낸 뒤 예외를 다시 올리므로 서버 로그의
        traceback은 그대로 남는다 — 운영 진단을 잃지 않는다.
        """
        from .user_errors import user_error_message
        logging.exception("처리되지 않은 오류 path=%s", request.url.path)
        # HTML로 바꾸는 건 브라우저에게만. 기계가 읽는 표면은 JSON을 유지한다
        # (HTTPException 핸들러와 같은 분기 규칙 — 어긋나면 호출자가 깨진다).
        wants_html = "text/html" in (request.headers.get("accept") or "").lower()
        if request.url.path.startswith("/api/") or not wants_html:
            return JSONResponse({"detail": "internal_error"}, status_code=500)
        body = ('<div class="sec"><h2>지금은 끝내지 못했어요</h2>'
                f'<p>{html.escape(user_error_message(exc, "요청을 처리"))}</p>'
                '<p class="meta">방금 만든 초안은 그대로 남아 있어요 — '
                '뒤로 가서 다시 시도해 주세요.</p>'
                '<p><a class="btn ghost" href="/">← 처음으로</a></p></div>')
        return HTMLResponse(web._wrap(body, default_backend, "오류 · UNTIL"),
                            status_code=500)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """브라우저에게 원시 JSON을 던지지 않는다.

        한도에 걸렸을 때 `{"detail":"credits_required"}`만 뜨고 끝나던 실사용
        보고(2026-08-20)를 고친다. stdlib 서버는 같은 상황에서 `/plan?full=1`로
        보내 무엇을 하면 되는지 알려 주는데, ASGI만 JSON을 뱉고 있었다 —
        운영 엔트리포인트가 ASGI라 실제 사용자는 전부 이 화면을 봤다.
        API 경로(`/api/`)는 기계가 읽으므로 JSON을 유지한다.
        """
        from fastapi.exception_handlers import http_exception_handler as _default
        # HTML로 바꾸는 건 **브라우저에게만**이다. 경로 접두어로 가르면
        # /billing/webhook·/dl/*.json처럼 API가 아니면서 JSON을 쓰는 표면까지
        # HTML로 덮어써 호출자를 깨뜨린다(2026-08-20 test_formfill_hwp 회귀).
        wants_html = "text/html" in (request.headers.get("accept") or "").lower()
        # Accept만 보면 놓친다 — 실사용에서 과제를 눌렀는데 원시 JSON이 떴다
        # (2026-08-23). 브라우저 **폼 전송**(application/x-www-form-urlencoded ·
        # multipart)은 Accept가 무엇이든 사람이 보는 화면으로 돌아가야 한다.
        ctype = (request.headers.get("content-type") or "").lower()
        is_form_post = ctype.startswith(("application/x-www-form-urlencoded",
                                         "multipart/form-data"))
        if request.url.path.startswith("/api/") or not (wants_html or is_form_post):
            return await _default(request, exc)
        if exc.status_code == 402:
            reason = "limit" if exc.detail == "global_daily_limit" else "1"
            return RedirectResponse(f"/plan?full={reason}", status_code=303)
        if exc.status_code == 400:
            # 브라우저 폼 제출에 원시 JSON을 돌려주면 사용자는 무슨 일인지 알 수
            # 없다(실사용: 과제를 눌렀더니 `{"detail": "assignment_or_token_missing"}`).
            # API 경로는 기존대로 JSON을 유지한다.
            hint = ("eTL 연결이 만료됐어요 — 다시 연결하면 이어서 진행됩니다."
                    if "token" in str(exc.detail or "") else
                    "요청에 빠진 값이 있어요. 앞 화면에서 다시 시도해 주세요.")
            return HTMLResponse(web._wrap(
                '<div class="sec"><h2>이어서 진행할 수 없었어요</h2>'
                f'<p class="meta">{web.html.escape(hint)}</p>'
                '<p><a class="btn" href="/connect?mode=fast">eTL 다시 연결하기</a></p>'
                '<p><a class="btn ghost" href="/">← 처음으로</a></p></div>',
                default_backend, "이어서 진행할 수 없었어요 · UNTIL"), status_code=400)
        if exc.status_code in (404, 405, 409, 503):
            from .user_errors import user_error_message   # noqa: F401  (계보 유지)
            messages = {
                404: ("찾을 수 없어요",
                      "주소가 바뀌었거나 작업이 만료됐을 수 있어요."),
                # 405: 폼으로만 여는 화면(/inbox 등)을 주소창·북마크·새로고침으로
                # 직접 열면 난다. 실사용에서 `{"detail":"Method Not Allowed"}`가
                # 그대로 떴다(라이브 확인 2026-08-23) — 사용자는 앱이 깨진 줄 안다.
                405: ("이 주소는 바로 열 수 없어요",
                      "과제 목록은 eTL에 연결한 뒤에 열립니다. 아래로 다시 시작하세요."),
                409: ("지금은 처리할 수 없어요",
                      "같은 요청이 이미 처리됐거나 조건이 맞지 않아요."),
                503: ("잠시 뒤에 다시 시도해 주세요",
                      "저장소에 일시적으로 접근하지 못했어요. 작업은 사라지지 않았어요."),
            }
            title, hint = messages[exc.status_code]
            # 405는 '되돌아갈 곳'이 분명하다 — 연결 화면에서 다시 목록을 연다.
            extra = ('<p><a class="btn" href="/connect?mode=list">과제 목록 다시 불러오기</a></p>'
                     if exc.status_code == 405 else "")
            return HTMLResponse(web._wrap(
                f'<div class="sec"><h2>{title}</h2>'
                f'<p class="meta">{hint}</p>'
                + extra
                + '<p><a class="btn ghost" href="/">← 처음으로</a></p></div>',
                default_backend, f"{title} · UNTIL"), status_code=exc.status_code)
        return await _default(request, exc)

    # 라우터가 던지는 404·405는 **starlette**의 HTTPException이다. fastapi의
    # 서브클래스에만 핸들러를 걸어 두면 그것들은 위 분기를 통째로 지나쳐 원시
    # JSON으로 나간다 — 실사용에서 `/inbox`를 주소창으로 열었더니
    # `{"detail":"Method Not Allowed"}`가 그대로 떴다(2026-08-23).
    # 같은 함수를 상위 클래스에도 등록해 두 경로가 같은 화면을 주게 한다.
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)

    @app.middleware("http")
    async def operational_boundary(request: Request, call_next):
        from . import google_auth as ga
        uid = request.cookies.get("uid", "") if cloud else ""
        new_uid = False
        if cloud and not web._UID_RE.match(uid):
            uid = secrets.token_urlsafe(24); new_uid = True
        request.state.anon_uid = uid
        # 계정 로그인 상태면 uid를 계정 uid로 승격(익명 쿠키는 유지 — 로그아웃 복귀용).
        auth = None
        drop_auth = False
        if cloud and ga.any_enabled():
            blob = request.cookies.get("auth", "")
            if blob:
                auth = ga.unpack_user(blob)
                if auth is not None:
                    uid = auth.uid
                else:
                    drop_auth = True        # 만료·위조 → 조용히 정리
        request.state.uid = uid
        request.state.auth = auth
        _AUTH_CTX.set(auth)
        # CSRF — stdlib 서버와 같은 중앙 검사(형제 서브도메인은 same-site라
        # SameSite=Lax가 못 막는다). 게이트들보다 앞에서 끊는다.
        if (request.method == "POST"
                and request.url.path not in web._CSRF_EXEMPT_PATHS
                and not web.csrf_origin_ok(
                    request.headers.get("origin"), request.headers.get("referer"),
                    request.headers.get("x-forwarded-host")
                    or request.headers.get("host") or "")):
            logging.warning(
                "CSRF 출처 불일치 path=%s origin=%s host=%s enforce=%s",
                request.url.path, request.headers.get("origin"),
                request.headers.get("x-forwarded-host")
                or request.headers.get("host"), web.csrf_enforced())
        if (request.method == "POST"
                and request.url.path not in web._CSRF_EXEMPT_PATHS
                and web.csrf_enforced()
                and not web.csrf_origin_ok(
                    request.headers.get("origin"), request.headers.get("referer"),
                    request.headers.get("x-forwarded-host")
                    or request.headers.get("host") or "")):
            try:
                async for _ in request.stream():
                    pass
            except Exception:
                pass
            return HTMLResponse(web._wrap(
                '<div class="sec"><h2>요청 출처를 확인할 수 없어요</h2>'
                '<p class="meta">다른 사이트에서 시작된 요청으로 보입니다. '
                'Until 화면에서 다시 시도해 주세요.</p>'
                '<p><a class="btn ghost" href="/">← 처음으로</a></p></div>',
                default_backend, "차단됨 · UNTIL"), status_code=403)
        if cloud:
            response = None
            codes = web._beta_codes()
            allowed = (request.url.path in ("/healthz", "/beta", "/about", "/about/",
                                            "/demo", "/demo/", "/billing/webhook",
                                            "/billing/refund",
                                            "/beta-request", "/beta-request/")
                       or request.url.path.startswith(("/asset/", "/admin")))
            login_open = (allowed or request.url.path in web._LOGIN_OPEN_PATHS
                          or request.url.path.startswith("/auth/"))
            if codes and not allowed and request.cookies.get("beta") not in web._beta_hashes(codes):
                response = HTMLResponse(web._wrap(web.render_beta_gate(False), default_backend,
                                                   "베타 · UNTIL"), status_code=403)
            elif ga.require_login() and auth is None and not login_open:
                # 로그인 게이트(UNTIL_REQUIRE_LOGIN=1) — stdlib 서버와 같은 규칙.
                requested = request.url.path
                if request.url.query:
                    requested += "?" + request.url.query
                nxt = urllib.parse.quote(ga.safe_next(requested), safe="")
                response = Response(status_code=303,
                                    headers={"Location": f"/login?next={nxt}"})
            elif (os.getenv("UNTIL_TELEMETRY") == "1" and not allowed
                  and request.url.path not in ("/login", "/logout")
                  and not request.url.path.startswith("/auth/")
                  and request.url.path not in ("/consent", "/consent/")):
                # 텔레메트리 opt-in 고지 — 선택 기록이 생기기 전 1회(stdlib 서버와 동일).
                # 동의는 KV에 미러되므로 재시작 후 로컬 파일만 보면 전원이 재고지된다
                # (게이트 리뷰 15회차 HIGH) — 읽기 전에 하이드레이션(프로세스당 uid 1회).
                from .telemetry.consent import get_consent
                await run_in_threadpool(web._hydrate_user, uid)
                if get_consent(uid, root=web._USERS_DIR) is None:
                    response = HTMLResponse(web._wrap(web.render_consent_notice(),
                                                      default_backend, "데이터 안내 · UNTIL"))
            if response is not None and request.method == "POST":
                # 게이트 응답 전 본문 드레인 — 안 읽고 응답하면 업로드 중인 클라이언트가
                # RST를 받아 안내가 유실된다(stdlib 베타 게이트 수정 계보와 동일).
                try:
                    async for _ in request.stream():
                        pass
                except Exception:
                    pass
            if response is None:
                response = await call_next(request)
        else:
            response = await call_next(request)
        if new_uid:
            response.set_cookie("uid", request.state.anon_uid, max_age=31536000,
                                httponly=True, samesite="lax",
                                secure=web.secure_cookies(request.headers, request.url.scheme))
        if drop_auth:
            response.delete_cookie("auth", path="/")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if cloud:
            from .analytics import csp_sources
            analytics_scripts, analytics_connects = csp_sources()
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline' "
                "https://fonts.googleapis.com https://cdn.jsdelivr.net; font-src 'self' "
                "https://fonts.gstatic.com https://cdn.jsdelivr.net; script-src 'self' "
                f"'unsafe-inline' {analytics_scripts}; connect-src 'self' "
                f"{analytics_connects}; frame-ancestors 'none'")
        return response

    @app.get("/healthz")
    def healthz():
        """배포 커밋을 병기한다 — "지금 떠 있는 게 어느 빌드인지"를 즉시 본다.

        stdlib `/healthz`는 이걸 내놓는데 **운영 엔트리포인트인 ASGI만 빠져
        있었다**(2026-08-21). 그래서 푸시 후 재배포가 실제로 반영됐는지 밖에서
        확인할 방법이 없었다 — 고친 게 안 올라갔는데 올라간 줄 알기 딱 좋다.
        Render가 `RENDER_GIT_COMMIT`을 주입한다(로컬은 키가 없어 생략된다).
        """
        sha = (os.getenv("RENDER_GIT_COMMIT") or "").strip()[:7]
        body = {"ok": True, "runtime": "asgi"}
        if sha:
            body["sha"] = sha
        return body

    @app.get("/beta", response_class=HTMLResponse)
    def beta_page():
        return web._wrap(web.render_beta_gate(False), default_backend, "베타 · UNTIL")

    @app.post("/beta")
    async def beta_submit(request: Request, code: str = Form("")):
        codes = web._beta_codes()
        if not cloud or not codes or code not in codes:
            return HTMLResponse(web._wrap(web.render_beta_gate(True), default_backend,
                                          "베타 · UNTIL"), status_code=403)
        value = hashlib.sha256(code.encode("utf-8")).hexdigest()[:32]
        response = Response(status_code=303, headers={"Location": "/"})
        response.set_cookie("beta", value, max_age=31536000, httponly=True,
                            samesite="lax", secure=web.secure_cookies(request.headers, request.url.scheme))
        return response

    @app.get("/admin", response_class=HTMLResponse)
    def admin(request: Request, internal: int = 0):
        want = (os.getenv("UNTIL_ADMIN_KEY") or "").strip()
        if not want:
            raise HTTPException(status_code=404, detail="not_found")
        from . import adminboard
        if not adminboard.verify_admin_token(
                request.cookies.get(adminboard.ADMIN_COOKIE, ""), want):
            return web._wrap(adminboard.render_admin_login(), default_backend,
                             "관리자 로그인 · UNTIL")
        local = adminboard.load_all(web._USERS_DIR)
        remote = []
        if cloud:
            try:
                from . import cloudkv
                client = cloudkv.kv()
                if client is not None:
                    for name in client.list_keys("adm:", limit=200):
                        blob = client.get(name)
                        record = adminboard.parse_record(blob) if blob else None
                        if record:
                            record.setdefault("uid", name.split(":", 1)[1])
                            remote.append(record)
            except Exception:
                pass
        body = adminboard.render_admin_html(
            adminboard.merge_records(local, remote), include_internal=internal == 1,
            telemetry_records=(adminboard.load_telemetry()
                               + adminboard.load_web_telemetry(web._USERS_DIR,
                                                               use_kv=cloud)),
            me=request.state.uid)
        # 개인화 패널 — stdlib 보드와 같은 내용(둘이 갈리면 한쪽만 보고 판단하게 된다).
        from . import personalization_board as pboard
        body += pboard.render_html(pboard.collect_rows(web._USERS_DIR),
                                   me=request.state.uid)
        from . import betarequests
        body += betarequests.render_admin_section(betarequests.load_all(use_kv=cloud))
        return web._wrap(body, default_backend, "관리자 보드 · UNTIL")

    @app.post("/admin/login")
    async def admin_login(request: Request, key: str = Form("")):
        want = (os.getenv("UNTIL_ADMIN_KEY") or "").strip()
        if not want:
            raise HTTPException(status_code=404, detail="not_found")
        from . import adminboard
        if not secrets.compare_digest(key.strip(), want):
            logging.warning("admin login failed uid=%s at=%s",
                            request.state.uid or "local", adminboard._now_iso())
            return HTMLResponse(
                web._wrap(adminboard.render_admin_login(), default_backend,
                          "관리자 로그인 · UNTIL"), status_code=403)
        response = RedirectResponse("/admin", status_code=303)
        response.set_cookie(
            adminboard.ADMIN_COOKIE, adminboard.issue_admin_token(want),
            max_age=adminboard.ADMIN_TOKEN_TTL, httponly=True, samesite="strict",
            secure=web.secure_cookies(request.headers, request.url.scheme))
        return response

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        def work():
            _admin_event(request.state.uid, "visit")
            return web._wrap(web.render_index(), default_backend)
        return await run_in_threadpool(
            _scoped, request.state.uid, cloud, True, work)

    @app.get("/simple", response_class=HTMLResponse)
    async def simple(request: Request):
        if cloud:
            return RedirectResponse("/connect?mode=fast", status_code=303)
        def work():
            _admin_event(request.state.uid, "visit")
            return web._wrap(web.render_simple_index(), default_backend,
                             "바로 초안 · UNTIL")
        return await run_in_threadpool(
            _scoped, request.state.uid, cloud, True, work)

    # ── eTL 연결 단계 (홈에서 '과제 하나 해결' 클릭 → 여기 → /inbox) ────
    @app.get("/connect", response_class=HTMLResponse)
    async def connect(request: Request, mode: str = "fast"):
        if web._Handler.sso or web._env_canvas_token():
            return RedirectResponse("/", status_code=303)   # 이미 연결 수단 있음

        def work():
            _admin_event(request.state.uid, "connect")
            return web._wrap(web.render_connect(mode=mode, sso=web._Handler.sso),
                             default_backend, "eTL 연결 · UNTIL")
        return HTMLResponse(await run_in_threadpool(
            _scoped, request.state.uid, cloud, True, work))

    # ── 계정 로그인 ────────────────────────────────────────────────
    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/"):
        from . import google_auth as ga
        if request.state.auth is not None:
            return RedirectResponse(ga.safe_next(next), status_code=303)
        return HTMLResponse(web._wrap(web.render_login(next), default_backend,
                                      "로그인 · UNTIL"))

    @app.get("/auth/google/start")
    def google_start(request: Request, next: str = "/"):
        from . import google_auth as ga
        origin = str(request.base_url).rstrip("/")
        cfg = ga.config(origin)
        if cfg is None:
            return HTMLResponse(web._wrap(web.render_login(next), default_backend,
                                          "로그인 · UNTIL"))
        verifier, challenge = ga.new_pkce()
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(16)
        blob = ga.sign({"v": verifier, "s": state, "n": nonce,
                        "next": ga.safe_next(next)}, ga.STATE_TTL)
        response = RedirectResponse(
            ga.authorize_url(cfg, state=state, challenge=challenge, nonce=nonce),
            status_code=303)
        response.set_cookie("gauth", blob, max_age=int(ga.STATE_TTL), httponly=True,
                            samesite="lax", secure=web.secure_cookies(request.headers, request.url.scheme))
        return response

    @app.get("/auth/google/callback")
    async def google_callback(request: Request, code: str = "", state: str = "",
                              error: str = ""):
        from . import google_auth as ga

        def page(err: str, nxt: str, status: int = 200):
            r = HTMLResponse(web._wrap(web.render_login(nxt, err=err), default_backend,
                                       "로그인 · UNTIL"), status_code=status)
            r.delete_cookie("gauth", path="/")
            return r

        saved = ga.unsign(request.cookies.get("gauth", ""))
        nxt = ga.safe_next(str((saved or {}).get("next") or "/"))
        if error:
            return page("구글에서 로그인이 취소됐습니다.", nxt)
        if not saved or not code or not secrets.compare_digest(
                state, str(saved.get("s", ""))):
            return page("로그인 요청이 만료됐어요. 다시 시도해 주세요.", nxt, 400)
        cfg = ga.config(str(request.base_url).rstrip("/"))
        if cfg is None:
            return page("구글 로그인 설정이 완료되지 않았습니다.", "/", 400)
        try:
            tokens = await run_in_threadpool(ga.exchange_code, cfg, code,
                                             str(saved.get("v", "")))
            claims = ga.decode_id_token(tokens.get("id_token", ""),
                                        client_id=cfg.client_id,
                                        nonce=str(saved.get("n", "")))
        except ga.AuthError as e:
            return page(str(e), nxt, 400)
        user = ga.user_from_claims(claims)
        anon = getattr(request.state, "anon_uid", "")
        moved = await run_in_threadpool(web._adopt_anon_data, anon, user.uid)
        if moved:
            await run_in_threadpool(web._hydrate_user, user.uid)
        _admin_event(user.uid, "login")
        response = RedirectResponse(nxt, status_code=303)
        response.set_cookie("auth", ga.pack_user(user), max_age=ga.AUTH_COOKIE_TTL,
                            httponly=True, samesite="lax",
                            secure=web.secure_cookies(request.headers, request.url.scheme))
        response.delete_cookie("gauth", path="/")
        return response

    @app.get("/auth/kakao/start")
    def kakao_start(request: Request, next: str = "/"):
        from . import google_auth as ga
        from . import kakao_auth as ka
        cfg = ka.config(str(request.base_url).rstrip("/"))
        if cfg is None:
            return HTMLResponse(web._wrap(web.render_login(next), default_backend,
                                          "로그인 · UNTIL"))
        verifier, challenge = ga.new_pkce()
        state = secrets.token_urlsafe(24)
        blob = ga.sign({"v": verifier, "s": state,
                        "next": ga.safe_next(next)}, ga.STATE_TTL)
        response = RedirectResponse(
            ka.authorize_url(cfg, state=state, challenge=challenge), status_code=303)
        response.set_cookie(
            "kauth", blob, max_age=int(ga.STATE_TTL), httponly=True,
            samesite="lax",
            secure=web.secure_cookies(request.headers, request.url.scheme))
        return response

    @app.get("/auth/kakao/callback")
    async def kakao_callback(request: Request, code: str = "", state: str = "",
                             error: str = ""):
        from . import google_auth as ga
        from . import kakao_auth as ka

        def page(err: str, nxt: str, status: int = 200):
            response = HTMLResponse(
                web._wrap(web.render_login(nxt, err=err), default_backend,
                          "로그인 · UNTIL"), status_code=status)
            response.delete_cookie("kauth", path="/")
            return response

        saved = ga.unsign(request.cookies.get("kauth", ""))
        nxt = ga.safe_next(str((saved or {}).get("next") or "/"))
        if error:
            return page("카카오에서 로그인이 취소됐습니다.", nxt)
        if not saved or not code or not secrets.compare_digest(
                state, str(saved.get("s", ""))):
            return page("로그인 요청이 만료됐어요. 다시 시도해 주세요.", nxt, 400)
        cfg = ka.config(str(request.base_url).rstrip("/"))
        if cfg is None:
            return page("카카오 로그인 설정이 완료되지 않았습니다.", "/", 400)
        try:
            tokens = await run_in_threadpool(
                ka.exchange_code, cfg, code, str(saved.get("v", "")))
            profile = await run_in_threadpool(
                ka.fetch_user, str(tokens.get("access_token", "")))
            user = ka.user_from_profile(profile)
        except ka.AuthError as exc:
            return page(str(exc), nxt, 400)
        anon = getattr(request.state, "anon_uid", "")
        moved = await run_in_threadpool(web._adopt_anon_data, anon, user.uid)
        if moved:
            await run_in_threadpool(web._hydrate_user, user.uid)
        _admin_event(user.uid, "login")
        response = RedirectResponse(nxt, status_code=303)
        response.set_cookie(
            "auth", ga.pack_user(user), max_age=ga.AUTH_COOKIE_TTL,
            httponly=True, samesite="lax",
            secure=web.secure_cookies(request.headers, request.url.scheme))
        response.delete_cookie("kauth", path="/")
        return response

    @app.get("/new", response_class=HTMLResponse)
    async def new_assignment(request: Request):
        """과제 직접 등록 — eTL에 없는 과제도 여기서 시작한다."""
        if cloud:
            return RedirectResponse("/connect?mode=fast", status_code=303)
        def work():
            _admin_event(request.state.uid, "visit")
            return web._wrap(web.render_new_assignment(), default_backend,
                             "과제 만들기 · UNTIL")
        return HTMLResponse(await run_in_threadpool(
            _scoped, request.state.uid, cloud, True, work))

    # ── 제출 직전 마지막 한 칸 ─────────────────────────────────────
    @app.get("/ready/{token}", response_class=HTMLResponse)
    async def submit_ready(token: str, request: Request):
        def work():
            result = web._get_session(token)
            if result is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            return web._wrap(web.render_submit_ready(token, result), default_backend,
                             "제출 · UNTIL")
        return HTMLResponse(await run_in_threadpool(
            _scoped, request.state.uid, cloud, False, work))

    @app.post("/edit")
    async def edit_draft(request: Request):
        """사람이 직접 고친 본문 저장 — AI 수정(/revise)과 별개 경로.

        stdlib 서버에만 붙이면 운영(ASGI)에서 편집란이 눌려도 404가 난다."""
        form = await request.form()
        token = str(form.get("session") or "")
        simple_ui = str(form.get("ui") or "") == "simple"
        body = str(form.get("body") or "")

        def work():
            if web._get_session(token) is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            result = web.edit_session(token, body)
            _admin_event(request.state.uid, "edit")
            return result is not None and result.final_draft is not None

        final = await run_in_threadpool(_scoped, request.state.uid, cloud, True, work)
        prefix = ("/svf/" if final else "/sv/") if simple_ui else ("/vf/" if final else "/v/")
        return RedirectResponse(f"{prefix}{token}", 303)

    @app.post("/submitted")
    async def mark_submitted(request: Request):
        """'올렸어요' 표시 — 사람이 눌렀다는 사실만 기록한다(전송 아님)."""
        form = await request.form()
        token = str(form.get("session") or "")
        undo = bool(form.get("undo"))

        def work():
            if web._get_session(token) is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            web.mark_submitted(token, done=not undo)
            _admin_event(request.state.uid, "submitted")
        await run_in_threadpool(_scoped, request.state.uid, cloud, True, work)
        return RedirectResponse(f"/ready/{token}", 303)

    @app.post("/logout")
    def logout():
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie("auth", path="/")
        return response

    @app.get("/beta-request", response_class=HTMLResponse)
    @app.get("/beta-request/", response_class=HTMLResponse)
    def beta_request_form():
        from . import betarequests
        return web._wrap(betarequests.render_form(), default_backend,
                         "베타 초대 요청 · UNTIL")

    @app.post("/beta-request", response_class=HTMLResponse)
    @app.post("/beta-request/", response_class=HTMLResponse)
    async def beta_request_submit(request: Request):
        """인증 없는 공개 접수. 랜딩(다른 오리진)의 일반 폼 POST가 그대로 도착한다 —
        `x-www-form-urlencoded`는 CORS 프리플라이트가 없어 JS 없이도 동작한다."""
        from . import betarequests
        raw = await request.form()
        form = {k: str(v) for k, v in raw.items() if isinstance(v, str)}
        record, error = betarequests.normalize(form)
        if record is None:
            if not error:          # 허니팟 — 봇에게 걸린 걸 알려 주지 않는다.
                return web._wrap(betarequests.render_thanks(), default_backend,
                                 "요청 완료 · UNTIL")
            return HTMLResponse(web._wrap(
                betarequests.render_form(error=error, values=form),
                default_backend, "베타 초대 요청 · UNTIL"), status_code=400)
        if betarequests.today_count() >= betarequests.MAX_PER_DAY:
            return HTMLResponse(web._wrap(betarequests.render_form(
                error="오늘 접수가 많아 잠시 후에 다시 시도해 주세요. "
                      "급하시면 minjun05@snu.ac.kr로 메일 주세요.", values=form),
                default_backend, "베타 초대 요청 · UNTIL"), status_code=429)
        if not betarequests.save(record):
            return HTMLResponse(web._wrap(betarequests.render_form(
                error="저장에 실패했어요. minjun05@snu.ac.kr로 메일 주시면 "
                      "직접 등록해 드릴게요.", values=form),
                default_backend, "베타 초대 요청 · UNTIL"), status_code=500)
        return web._wrap(betarequests.render_thanks(), default_backend,
                         "요청 완료 · UNTIL")

    @app.get("/demo", response_class=HTMLResponse)
    @app.get("/demo/", response_class=HTMLResponse)
    def demo():
        # 작동 예시 페이지는 없앴다(2026-08-21) — 소개가 같은 5단계를 보여 준다.
        # 404 대신 리다이렉트: 초대 메일·따로 배포된 랜딩에 링크가 남아 있다.
        return RedirectResponse("/about", status_code=308)

    @app.get("/sessions", response_class=HTMLResponse)
    async def sessions_page(request: Request):
        body = await run_in_threadpool(
            _scoped, request.state.uid, cloud, False,
            lambda: web.render_sessions(web.list_sessions()))
        return web._wrap(body, default_backend, "이전 작업 · UNTIL")

    @app.get("/about", response_class=HTMLResponse)
    @app.get("/about/", response_class=HTMLResponse)
    def about():
        landing = web.render_about_page()
        if landing is not None:
            return landing
        # 랜딩 자산을 못 읽는 경우(배포 사고)에도 죽지 않게 최소 안내만 준다.
        return web._wrap(
            '<div class="sec"><h2>Until 소개</h2>'
            '<p class="meta">소개 자료를 불러오지 못했습니다. '
            '<a href="/">처음으로</a></p></div>',
            default_backend, "Until")

    @app.get("/archive", response_class=HTMLResponse)
    async def archive(request: Request):
        """내 과제 아카이브 — 내 것만. 남의 제출물은 보여주지 않는다."""
        def work():
            return web._wrap(web.render_archive(web.list_sessions(limit=60)),
                             default_backend, "내 과제 아카이브 · UNTIL")
        return HTMLResponse(await run_in_threadpool(
            _scoped, request.state.uid, cloud, False, work))

    @app.get("/history", response_class=HTMLResponse)
    async def history_page(request: Request):
        body = await run_in_threadpool(_scoped, request.state.uid, cloud, False,
                                       web.render_history)
        return web._wrap(body, default_backend, "답변 히스토리 · UNTIL")

    @app.get("/consent", response_class=HTMLResponse)
    async def consent_page(request: Request):
        from .telemetry.consent import get_consent
        # _scoped 경유 — 재시작 후에도 KV의 동의 상태를 복원해 정확히 표시.
        current = await run_in_threadpool(
            _scoped, request.state.uid, cloud, False,
            lambda: get_consent(request.state.uid or "local", root=web._USERS_DIR))
        return web._wrap(web.render_consent_settings(current), default_backend,
                         "데이터 설정 · UNTIL")

    @app.post("/consent")
    async def consent_submit(request: Request, choice: str = Form(""),
                             back: str = Form("")):
        if choice in ("yes", "no"):
            uid = request.state.uid

            def work():
                from .telemetry.consent import set_consent
                set_consent(uid or "local", choice == "yes", root=web._USERS_DIR)
            # mutate=True — consent.json이 _mirror_user 경유로 KV까지 미러된다.
            await run_in_threadpool(_scoped, uid, cloud, True, work)
        response = RedirectResponse("/consent" if back == "settings" else "/", 303)
        response.set_cookie("until_analytics", "yes" if choice == "yes" else "no",
                            max_age=31536000, httponly=False, samesite="lax",
                            secure=web.secure_cookies(request.headers, request.url.scheme))
        return response

    @app.post("/submit/prepare", response_class=HTMLResponse)
    async def submit_prepare(request: Request, session: str = Form("")):
        def work():
            result = web._get_session(session)
            if result is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            plan = web.prepare_submission(
                result, uid=request.state.uid, session_id=session)
            return web.render_submission_confirmation(plan, session, result=result), \
                200 if plan.allowed else 409
        body, status = await run_in_threadpool(
            _scoped, request.state.uid, cloud, True, work)
        return HTMLResponse(web._wrap(body, default_backend, "제출 최종 확인 · UNTIL"), status)

    @app.post("/submit/confirm", response_class=HTMLResponse)
    async def submit_confirm(request: Request, session: str = Form(""),
                             confirm_nonce: str = Form("")):
        if not confirm_nonce:
            raise HTTPException(status_code=400, detail="confirm_nonce_required")
        def work():
            result = web._get_session(session)
            if result is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            plan, receipt = web.confirm_submission(
                result, cloud=cloud, confirm_nonce=confirm_nonce,
                uid=request.state.uid, session_id=session)
            body = web.render_submission_receipt(plan, receipt, cloud=cloud)
            return body, 200 if plan.allowed else 409
        body, status = await run_in_threadpool(
            _scoped, request.state.uid, cloud, True, work)
        return HTMLResponse(web._wrap(body, default_backend, "제출 확인 · UNTIL"), status)

    @app.get("/profile", response_class=HTMLResponse)
    async def profile_page(request: Request, saved: str = "", courses: str = ""):
        body = await run_in_threadpool(
            _scoped, request.state.uid, cloud, False,
            lambda: web.render_profile(saved=saved == "1",
                                       courses_saved=courses == "1"))
        return web._wrap(body, default_backend, "내 프로필 · UNTIL")

    @app.get("/plan", response_class=HTMLResponse)
    async def plan_page(request: Request, full: str = "", ok: str = "", err: str = ""):
        # full은 stdlib과 같은 계약이다: "limit"=전역 상한(충전 무의미), 그 외 참값=잔액 부족.
        msg = "충전됐어요! 이어서 과제를 만들 수 있어요." if ok == "1" else ""
        body = await run_in_threadpool(
            _scoped, request.state.uid, cloud, False,
            lambda: web.render_plan(full=full, backend=default_backend,
                                    err=err, msg=msg))
        return web._wrap(body, default_backend, "플랜 · UNTIL")

    @app.get("/v/{token}", response_class=HTMLResponse)
    async def draft_page(token: str, request: Request):
        result = await run_in_threadpool(_scoped, request.state.uid, cloud, False,
                                         web._get_session, token)
        if result is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        return web._wrap(web.render_draft(token, result), default_backend,
                         "경계선 초안 · UNTIL")

    @app.get("/vf/{token}", response_class=HTMLResponse)
    async def final_page(token: str, request: Request):
        def work():
            result = web._get_session(token)
            if result is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            body = web.render_final(
                result, session_id=token, answered=set(web._ANSWERS.get(token, {})))
            body += web._voice_rating_html(token, result, token in web._VOICE_RATINGS)
            return web._wrap(body, default_backend, "완성본 · UNTIL")
        return await run_in_threadpool(_scoped, request.state.uid, cloud, False, work)

    @app.get("/sv/{token}", response_class=HTMLResponse)
    async def simple_draft_page(token: str, request: Request):
        def work():
            result = web._get_session(token)
            if result is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            return web._wrap(web.render_simple_draft(token, result), default_backend,
                             "초안 · UNTIL")
        return await run_in_threadpool(_scoped, request.state.uid, cloud, False, work)

    @app.get("/svf/{token}", response_class=HTMLResponse)
    async def simple_final_page(token: str, request: Request):
        def work():
            result = web._get_session(token)
            if result is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            body = web.render_simple_final(
                result, session_id=token, answered=set(web._ANSWERS.get(token, {})))
            body += web._voice_rating_html(
                token, result, token in web._VOICE_RATINGS, simple=True)
            return web._wrap(body, default_backend, "완성본 · UNTIL")
        return await run_in_threadpool(_scoped, request.state.uid, cloud, False, work)

    @app.get("/api/v1/sessions/{token}/readiness")
    async def readiness(token: str, request: Request):
        result = await run_in_threadpool(_scoped, request.state.uid, cloud, False,
                                         web._get_session, token)
        if result is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        from .readiness import assess_readiness
        return assess_readiness(result).to_dict()

    @app.get("/readiness/{token}.json")
    async def readiness_compat(token: str, request: Request):
        return await readiness(token, request)

    @app.post("/api/v1/drafts", response_class=JSONResponse)
    async def create_draft(payload: DraftRequest, request: Request):
        # 제공자는 서버 설정만 따른다. 클라이언트가 임의 백엔드·키 경로를 고르지 못한다.
        cfg = _cfg(default_backend)
        def work():
            _charge_before(cfg.backend, cloud)
            result = _draft_result(
                request.state.uid, lambda: web.run_text(payload.assignment, cfg))
            token = _store_result(result, backend=cfg.backend, uid=request.state.uid)
            _charge_after(cfg.backend, cloud, request.state.uid, result=result)
            return token, result
        token, result = await run_in_threadpool(
            _scoped, request.state.uid, cloud, True, work)
        return _result_json(token, result)

    @app.post("/api/v1/upload-drafts")
    async def create_upload_draft(
        request: Request,
        assignment: str = Form(min_length=1, max_length=100_000),
        files: list[UploadFile] = File(default=[]),
        voice_files: list[UploadFile] = File(default=[]),
    ):
        uploads, voices, total = [], [], 0
        for item, target in [(x, uploads) for x in files] + [(x, voices) for x in voice_files]:
            data = await item.read(25 * 1024 * 1024 + 1)
            total += len(data)
            if total > 25 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="uploads_too_large")
            target.append((item.filename or "upload", data))
        cfg = _cfg(default_backend)
        def work():
            import shutil
            _charge_before(cfg.backend, cloud)
            sources, warnings = web._sources_from_uploads(uploads)
            voice_dir, voice_warnings = web._voice_dir_from_uploads(voices)
            try:
                result = _draft_result(
                    request.state.uid,
                    lambda: web.run_text(assignment, cfg, extra_sources=sources,
                                         voice_dir=voice_dir))
            finally:
                if voice_dir:
                    shutil.rmtree(voice_dir, ignore_errors=True)
            result.capture_warnings += warnings + voice_warnings
            token = _store_result(result, backend=cfg.backend, uid=request.state.uid)
            _charge_after(cfg.backend, cloud, request.state.uid, result=result)
            return token, result
        token, result = await run_in_threadpool(
            _scoped, request.state.uid, cloud, True, work)
        return _result_json(token, result)

    @app.post("/draft")
    async def form_draft(request: Request):
        if cloud:
            return RedirectResponse("/connect?mode=fast", status_code=303)
        form = await request.form()
        assignment = str(form.get("assignment") or "").strip()
        if not assignment and str(form.get("mode") or "") == "new":
            # 과제 만들기(구조화 칸) → 붙여넣기와 동일한 과제 텍스트로 조립.
            raw = {k: [str(v)] for k, v in form.multi_items()
                   if not isinstance(v, UploadFile)}
            if not str(form.get("body") or "").strip():
                return HTMLResponse(
                    web._wrap(web.render_new_assignment(
                        err="과제 설명은 채워 주세요 — 나머지 칸은 비워도 됩니다.",
                        form=raw), default_backend, "과제 만들기 · UNTIL"),
                    status_code=400)
            assignment = web.compose_assignment(raw)
        if not assignment:
            raise HTTPException(status_code=400, detail="assignment_required")
        if len(assignment) > 100_000:
            raise HTTPException(status_code=413, detail="assignment_too_large")
        uploads, voices, total = [], [], 0
        for key, target in (("files", uploads), ("voice_files", voices)):
            for item in form.getlist(key):
                if not isinstance(item, UploadFile):
                    continue
                data = await item.read(25 * 1024 * 1024 + 1)
                total += len(data)
                if total > 25 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="uploads_too_large")
                target.append((item.filename or "upload", data))
        cfg = _cfg(default_backend)
        def work():
            import shutil
            _charge_before(cfg.backend, cloud)
            sources, warnings = web._sources_from_uploads(uploads)
            voice_dir, voice_warnings = web._voice_dir_from_uploads(voices)
            try:
                result = _draft_result(
                    request.state.uid,
                    lambda: web.run_text(assignment, cfg, extra_sources=sources,
                                         voice_dir=voice_dir))
            finally:
                if voice_dir:
                    shutil.rmtree(voice_dir, ignore_errors=True)
            result.capture_warnings += warnings + voice_warnings
            token = _store_result(result, backend=cfg.backend, uid=request.state.uid)
            _charge_after(cfg.backend, cloud, request.state.uid, result=result)
            return token
        token = await run_in_threadpool(_scoped, request.state.uid, cloud, True, work)
        simple_ui = str(form.get("ui") or "") == "simple"
        return RedirectResponse(f"/sv/{token}" if simple_ui else f"/v/{token}", 303)

    @app.post("/hx/draft", response_class=HTMLResponse)
    async def hx_draft(request: Request,
                       assignment: str = Form(min_length=1, max_length=100_000)):
        """HTMX가 페이지 셸 교체 없이 초안 fragment만 받을 수 있는 경계."""
        if cloud:
            return RedirectResponse("/connect?mode=fast", status_code=303)
        cfg = _cfg(default_backend)
        def work():
            _charge_before(cfg.backend, cloud)
            result = _draft_result(
                request.state.uid, lambda: web.run_text(assignment, cfg))
            token = _store_result(result, backend=cfg.backend, uid=request.state.uid)
            _charge_after(cfg.backend, cloud, request.state.uid, result=result)
            return token, result
        token, result = await run_in_threadpool(
            _scoped, request.state.uid, cloud, True, work)
        return web.render_draft(token, result)

    @app.post("/api/v1/inbox")
    async def inbox(payload: InboxRequest, request: Request):
        def work():
            from . import adminboard
            from .capture.sources.canvas_api import CanvasApiAdapter
            from .capture.sources.discovery import EtlInbox
            _admin_event(request.state.uid, "token_try", token=payload.token)
            try:
                adapter = CanvasApiAdapter(token=payload.token)
                items = EtlInbox(adapter).list_assignments(
                    bucket=None, only_unsubmitted=payload.only_unsubmitted)
                items = web.merge_elice_inbox(
                    items, bucket=None, only_unsubmitted=payload.only_unsubmitted)
            except Exception as exc:
                _admin_event(request.state.uid, adminboard.inbox_failure_event(exc),
                             token=payload.token)
                raise
            if payload.hide_past:
                items = web._filter_sort_inbox(items, status="all", hide_past=True)
            _admin_event(request.state.uid, "inbox", token=payload.token)
            return [{"id": x.id, "title": x.title, "course_id": x.course_id,
                     "course": x.course_name, "url": x.url, "due_at": x.due_at,
                     "submitted": x.submitted, "actionable": x.actionable}
                    for x in items]
        return await run_in_threadpool(_scoped, request.state.uid, cloud, True, work)

    @app.post("/api/v1/token/check")
    async def token_check(request: Request):
        # 저장·관리자 로그 없이 검증만 한다. 입력 토큰은 응답에 넣지 않는다.
        try:
            payload = await request.json()
            token = payload.get("token") if isinstance(payload, dict) else None
        except (ValueError, UnicodeDecodeError):
            token = None
        if not isinstance(token, str) or not 1 <= len(token.strip()) <= 500:
            return JSONResponse({"ok": False, "reason": "auth"}, status_code=400)
        return await run_in_threadpool(web.check_canvas_token, token.strip())

    @app.post("/inbox", response_class=HTMLResponse)
    async def form_inbox(request: Request):
        form = await request.form()
        token = str(form.get("token") or "").strip()
        # 보관된 연결이 있으면 토큰을 다시 묻지 않는다(로그인 계정 한정).
        # 폼에 토큰이 함께 오면 그쪽이 이긴다 — '다른 토큰으로 연결하기'.
        remember = str(form.get("remember") or "") == "1"
        # 화면이 보관본을 '채워진 것처럼' 보여 주므로, 가림표 그대로 돌아오면
        # 사용자가 덮어쓰지 않았다는 뜻이다 — 그때 보관본을 쓴다.
        if not token or web.uses_saved_token(token):
            token = await run_in_threadpool(
                _scoped, request.state.uid, cloud, False, web._remembered_token)
        if not token:
            raise HTTPException(status_code=400, detail="token_required")
        # 연습 모드 — 이미 낸 과제로 딸깍 재현(명시적 행동이라 필터 해제).
        practice = str(form.get("practice") or "") == "1"
        # UX 테스트용 '전부 보기' — stdlib 서버와 같은 규칙. 폼이 의도를 표시해도
        # 토큰 지문이 허용 목록에 있을 때만 필터를 푼다(fail-closed).
        want_all = bool(form.get("all")) and web.test_all_assignments_allowed(token)
        only = bool(form.get("unsubmitted")) and not practice and not want_all
        hide = bool(form.get("hide_past")) and not practice and not want_all
        fast = str(form.get("fast") or "") == "1" or practice
        simple = str(form.get("ui") or "") == "simple"
        cfg = _cfg(default_backend)
        def work():
            from . import adminboard
            from .capture.sources.canvas_api import CanvasApiAdapter
            from .capture.sources.discovery import EtlInbox
            _admin_event(request.state.uid, "token_try", token=token)
            try:
                adapter = CanvasApiAdapter(token=token)
                items = EtlInbox(adapter).list_assignments(
                    bucket=None, only_unsubmitted=only)
                items = web.merge_elice_inbox(
                    items, bucket=None, only_unsubmitted=only)
            except Exception as exc:
                _admin_event(request.state.uid, adminboard.inbox_failure_event(exc),
                             token=token)
                raise
            items = web._filter_sort_inbox(items, status="all", hide_past=hide)
            _admin_event(request.state.uid, "inbox", token=token)
            # '가장 가까운 과제 하나 해결하기' — 홈 primary 버튼(fast=1)은 목록이
            # 아니라 초안까지 간다(stdlib _fast_draft와 동일 동작). ASGI가 이
            # 플래그를 무시해 라이브에서 딸깍이 목록에서 끊기던 실사용 회귀.
            if fast:
                from .inbox_policy import pick_practice
                best = (pick_practice if practice else web._pick_best)(items)
                if best is not None:
                    _admin_event(request.state.uid, "assign_open")
                    _charge_before(cfg.backend, cloud)
                    result = _draft_result(
                        request.state.uid,
                        lambda: web.collect_with_materials(
                            best.url, cfg, token=token, practice=practice))
                    # 인박스의 원본 과제명·과목명을 표기에 사용(stdlib 경로와 동일).
                    if isinstance(result.spec, dict):
                        if (getattr(best, "title", "") or "").strip():
                            result.spec["title"] = best.title.strip()
                        if (getattr(best, "course_name", "") or "").strip():
                            result.spec["course"] = best.course_name.strip()
                    session = _store_result(result, source="etl", backend=cfg.backend,
                                            url=best.url, uid=request.state.uid)
                    web._store_canvas_token(session, token, uid=request.state.uid)
                    if remember:
                        web._remember_token(token, uid=request.state.uid)
                    _charge_after(cfg.backend, cloud, request.state.uid, token, result)
                    # 딸깍 완주 — AI 제안을 미리 만들어 간단 화면 답칸 프리필
                    # (수락/수정은 사람 몫). 실패는 비치명.
                    if result.draft.decisions:
                        try:
                            web._SUGGESTIONS[session] = \
                                web.suggest_decision_answers(result, cfg)
                            web._persist_session(session)
                        except Exception:
                            pass
                    return ("redirect", f"/sv/{session}")
            sid = web._new_token(); web._store_canvas_token(sid, token, uid=request.state.uid)
            if remember:
                web._remember_token(token, uid=request.state.uid)
            note = "바로 초안을 만들 과제가 없어요. 아래에서 직접 골라 주세요." if fast else ""
            return ("html", web._wrap(web.render_inbox(items, sid=sid, note=note,
                                                       simple=simple),
                                      default_backend, "내 과제 · UNTIL"))
        try:
            kind, value = await run_in_threadpool(
                _scoped, request.state.uid, cloud, True, work)
        except Exception as exc:
            from .practice_audit import PracticePreflightError
            if isinstance(exc, PracticePreflightError):
                reasons = "".join(f"<li>{web.html.escape(x)}</li>" for x in exc.reasons)
                body = ('<div class="sec"><h2>이 과제는 아직 연습을 시작할 수 없어요</h2>'
                        f'<ul>{reasons}</ul><p>빠진 자료나 담당 범위를 확인한 뒤 다시 '
                        '시도하세요.</p><p><a class="btn ghost" href="/">← 홈으로</a></p></div>')
                return HTMLResponse(web._wrap(body, default_backend,
                                               "연습 사전 점검 · UNTIL"), 422)
            if web.is_etl_auth_error(exc):
                body = web.render_etl_auth_error()
                return HTMLResponse(web._wrap(body, default_backend, "eTL 재연결 · UNTIL"), 401)
            body = ('<div class="sec"><h2>eTL에 연결하지 못했어요</h2>'
                    '<p>네트워크 연결을 확인하고 잠시 후 다시 시도해 주세요.</p>'
                    '<p><a class="btn ghost" href="/">← 홈으로</a></p></div>')
            return HTMLResponse(web._wrap(body, default_backend, "eTL 연결 오류 · UNTIL"), 502)
        if kind == "redirect":
            return RedirectResponse(value, 303)
        return HTMLResponse(value)

    @app.post("/api/v1/etl-drafts")
    async def create_etl_draft(payload: EtlDraftRequest, request: Request):
        cfg = _cfg(default_backend)
        def work():
            _admin_event(request.state.uid, "assign_open")
            _charge_before(cfg.backend, cloud)
            result = _draft_result(
                request.state.uid,
                lambda: web.collect_with_materials(payload.url, cfg, token=payload.token))
            token = _store_result(result, source="etl", backend=cfg.backend,
                                  url=payload.url, uid=request.state.uid)
            web._store_canvas_token(token, payload.token, uid=request.state.uid)
            _charge_after(cfg.backend, cloud, request.state.uid, payload.token, result)
            return token, result
        token, result = await run_in_threadpool(
            _scoped, request.state.uid, cloud, True, work)
        return _result_json(token, result)

    @app.post("/pick")
    async def form_pick(request: Request):
        form = await request.form()
        url = str(form.get("url") or "").strip()
        sid = str(form.get("sid") or "").strip()
        token = (str(form.get("token") or "").strip()
                 or web._get_canvas_token(sid, uid=request.state.uid))
        if not token:
            # 세션 토큰 저장소는 **메모리 + TTL**이라 Render 무료 티어가 잠들면
            # 사라진다. 그러면 목록에서 과제를 누르는 순간 400이 났다(실사용
            # 2026-08-23, 물리학1). 보관된 연결이 있으면 그걸 쓴다 —
            # "저장해 뒀으니 다시 안 묻는다"가 목록→초안에도 적용돼야 한다.
            token = await run_in_threadpool(
                _scoped, request.state.uid, cloud, False, web._remembered_token)
        if not url or not token:
            raise HTTPException(status_code=400, detail="assignment_or_token_missing")
        cfg = _cfg(default_backend)
        def work():
            _admin_event(request.state.uid, "assign_open")
            _charge_before(cfg.backend, cloud)
            result = _draft_result(
                request.state.uid,
                lambda: web.collect_with_materials(url, cfg, token=token))
            session = _store_result(result, source="etl", backend=cfg.backend,
                                    url=url, uid=request.state.uid)
            web._store_canvas_token(session, token, uid=request.state.uid)
            _charge_after(cfg.backend, cloud, request.state.uid, token, result)
            return session
        session = await run_in_threadpool(_scoped, request.state.uid, cloud, True, work)
        return RedirectResponse(f"/v/{session}", 303)

    @app.post("/api/v1/sessions/{token}/finalize")
    async def finalize_session(token: str, payload: FinalizeRequest, request: Request):
        cfg = _cfg(default_backend)
        def work():
            result = web._get_session(token)
            if result is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            prior = web._ANSWERS.get(token, {})
            merged = {**prior, **payload.answers}
            web._ANSWERS[token] = merged
            for _ in (index for index in payload.answers if index not in prior):
                _admin_event(request.state.uid, "decision_ans")
            for _ in range(max(0, result.draft.n_decisions - len(merged))):
                _admin_event(request.state.uid, "decision_skip")
            was_final = result.final_draft is not None
            result = web.finalize(result, merged, cfg, channel="web")
            web._SESSIONS[token] = result; web._persist_session(token)
            meta = web._TELEMETRY_META.get(token)
            if meta is not None and was_final:
                meta["revision_count"] = int(meta.get("revision_count") or 0) + 1
                web._persist_session(token)
            web._telemetry_emit("final", token, result, uid=request.state.uid)
            _admin_event(request.state.uid, "final")
            return result
        result = await run_in_threadpool(
            _scoped, request.state.uid, cloud, True, work)
        return _result_json(token, result)

    @app.post("/finalize")
    async def form_finalize(request: Request):
        form = await request.form()
        token = str(form.get("session") or "")
        simple_ui = str(form.get("ui") or "") == "simple"
        def work():
            result = web._get_session(token)
            if result is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            answers = {}
            for key, value in form.multi_items():
                if key.startswith("answer_") and str(value).strip():
                    try: answers[int(key.split("_", 1)[1])] = str(value).strip()
                    except ValueError: pass
            prior = web._ANSWERS.get(token, {})
            merged = {**prior, **answers}
            for _ in (index for index in answers if index not in prior):
                _admin_event(request.state.uid, "decision_ans")
            for _ in range(max(0, result.draft.n_decisions - len(merged))):
                _admin_event(request.state.uid, "decision_skip")
            # 비워 둔 칸은 AI가 채운다(사용자 지시 2026-08-20) — 채운 사실은
            # `/svf`가 화면에 밝힌다. bf912e4가 stdlib 서버만 고쳐서 **운영
            # 엔트리포인트인 ASGI에는 이 기능이 통째로 없었다**(2026-08-21 발견).
            merged, autofilled = web._fill_blank_decisions(
                token, result, merged, _cfg(default_backend))
            for _ in autofilled:
                _admin_event(request.state.uid, "decision_autofill")
            web._ANSWERS[token] = merged
            # **사람이 직접 쓴 답만** 히스토리에 적립한다 — AI가 채운 값을 '내 지난
            # 답'으로 학습하면 다음 과제 제안이 자기 출력을 근거로 삼는다(에코 챔버).
            try:
                from .context.answer_history import record_answers
                delta = {i: a for i, a in answers.items() if prior.get(i) != a}
                record_answers([d.note for d in result.draft.decisions], delta)
            except Exception:
                pass
            was_final = result.final_draft is not None
            result = web.finalize(result, merged, _cfg(default_backend),
                                  channel="web")
            if result.final_draft is not None:
                from .prompts.suggest import suggest_prompts
                result.suggested_prompts = suggest_prompts(result.final_draft)
            web._maybe_run_code_check(result)
            web._SESSIONS[token] = result; web._persist_session(token)
            meta = web._TELEMETRY_META.get(token)
            if meta is not None and was_final:
                meta["revision_count"] = int(meta.get("revision_count") or 0) + 1
                web._persist_session(token)
            web._telemetry_emit("final", token, result, uid=request.state.uid)
            _admin_event(request.state.uid, "final")
        await run_in_threadpool(_scoped, request.state.uid, cloud, True, work)
        return RedirectResponse(f"/svf/{token}" if simple_ui else f"/vf/{token}", 303)

    @app.post("/api/v1/sessions/{token}/suggest")
    async def suggest(token: str, request: Request):
        cfg = _cfg(default_backend)
        def work():
            result = web._get_session(token)
            if result is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            got = web.suggest_decision_answers(result, cfg)
            web._SUGGESTIONS[token] = got; web._persist_session(token)
            return got
        return await run_in_threadpool(_scoped, request.state.uid, cloud, True, work)

    @app.post("/suggest")
    async def form_suggest(request: Request):
        """폼 경로 — 전부 답하지 않아도 막히지 않게 **빈칸만** 채워 같은 화면으로.

        지금 타이핑한 답은 '내 답'으로 확정해 두고(왕복에서 유실 방지), 아직 빈
        번호에만 제안을 만든다. 내가 정한 답은 맥락으로 넘겨 남은 칸이 그 논지·
        범위·톤과 어긋나지 않게 한다. 확정은 여전히 사람의 '완성하기' 클릭이다.
        """
        form = await request.form()
        token = str(form.get("session") or "")
        simple_ui = str(form.get("ui") or "") == "simple"
        back = f"/sv/{token}" if simple_ui else f"/v/{token}"
        cfg = _cfg(default_backend)
        raw = {k: [str(v)] for k, v in form.multi_items()}

        def work():
            result = web._get_session(token)
            if result is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            n = len(result.draft.decisions)
            typed = web._answers_from_form(raw, n)
            mine = dict(web._ANSWERS.get(token) or {})
            mine.update({i: v for i, v in typed.items() if v.strip()})
            if mine:
                web._ANSWERS[token] = mine
            blanks = [i for i in range(1, n + 1) if not (mine.get(i) or "").strip()]
            fresh = web.suggest_decision_answers(
                result, cfg, my_answers=mine or None,
                only=blanks if mine else None)
            merged = dict(web._SUGGESTIONS.get(token) or {})
            merged.update(fresh)
            for i in list(merged):      # 내가 쓴 칸의 묵은 제안은 걷어낸다
                if (mine.get(i) or "").strip():
                    merged.pop(i, None)
            web._SUGGESTIONS[token] = merged
            web._persist_session(token)
        await run_in_threadpool(_scoped, request.state.uid, cloud, True, work)
        return RedirectResponse(back, 303)

    @app.post("/api/v1/sessions/{token}/review")
    async def review(token: str, request: Request):
        cfg = _cfg(default_backend)
        def work():
            result = web._get_session(token)
            if result is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            got = web.review_result(result, cfg)
            web._REVIEWS[token] = got; web._persist_session(token)
            web._telemetry_emit("review", token, result, uid=request.state.uid)
            return got.to_dict() if hasattr(got, "to_dict") else got.__dict__
        return await run_in_threadpool(_scoped, request.state.uid, cloud, True, work)

    @app.post("/review")
    async def form_review(request: Request):
        form = await request.form(); token = str(form.get("session") or "")
        await review(token, request)
        return RedirectResponse(f"/v/{token}", 303)

    @app.post("/revise")
    async def form_revise(request: Request):
        form = await request.form()
        token = str(form.get("session") or "")
        mode = str(form.get("mode") or "")
        try:
            paragraph = int(form.get("paragraph") or 0)
        except (TypeError, ValueError):
            paragraph = 0
        excluded = []
        for key, value in form.multi_items():
            if str(key).startswith("exclude_") and value:
                try:
                    excluded.append(int(str(key).split("_", 1)[1]))
                except ValueError:
                    pass
        cfg = _cfg(default_backend)
        def work():
            return web.revise_session(
                token, cfg, mode=mode, paragraph=paragraph,
                instruction=str(form.get("instruction") or ""),
                excluded_sources=excluded)
        await run_in_threadpool(_scoped, request.state.uid, cloud, True, work)
        return RedirectResponse(f"/v/{token}", 303)

    @app.post("/telemetry/export", status_code=204)
    async def copy_export(request: Request):
        form = await request.form()
        token = str(form.get("session") or "")
        def work():
            result = web._get_session(token)
            if result is not None:
                _admin_event(request.state.uid, "export")
                # _scoped 안에서 방출 — hydrated_ok가 설정돼야 KV 미러가 걸린다.
                web._telemetry_emit("export", token, result, uid=request.state.uid)
        await run_in_threadpool(_scoped, request.state.uid, cloud, False, work)
        return None

    @app.get("/dl/{token}.{fmt}")
    async def download(token: str, fmt: str, request: Request):
        result = await run_in_threadpool(_scoped, request.state.uid, cloud, False,
                                         web._get_session, token)
        if result is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        filename = f"until-submission.{fmt}"
        # 과제가 파일명 규칙을 정했으면 그대로 짓는다("학번_이름"). 칸을 다 못 채우면
        # 빈 문자열이 와서 기본 이름이 그대로 쓰인다(format_guard 주석 참조).
        try:
            from .execution.format_guard import submission_filename
            from .profile import load_profile
            named = await run_in_threadpool(
                _scoped, request.state.uid, cloud, False,
                lambda: submission_filename(result, fmt, profile=load_profile()))
            if named:
                filename = named
        except Exception:
            pass
        if fmt == "pptx":
            from .presentation_export import render_presentation_pptx
            data, ctype = render_presentation_pptx(result), (
                "application/vnd.openxmlformats-officedocument.presentationml.presentation")
        elif fmt == "form":
            # 채워진 원본 양식(hwpx/docx 셀 주입) 또는 .hwp 값 표 대체(C안) —
            # 레거시(web.py `_download_filled_form`)와 파일명·Content-Type 동일 규칙.
            from .capture.formfill import find_form_document
            from .report import write_filled_form
            src = find_form_document(result)
            if not src:
                raise HTTPException(status_code=404, detail="form_not_found")
            import tempfile
            from pathlib import Path as _P
            try:
                with tempfile.TemporaryDirectory() as d:
                    got = write_filled_form(result, _P(d) / f"filled{_P(src).suffix.lower()}")
                    if not got:
                        raise HTTPException(status_code=404, detail="form_fill_empty")
                    out_path, _stats = got
                    data = out_path.read_bytes()
                    out_suffix = out_path.suffix.lower()  # .hwp 원본은 .docx로 강제 대체
            except HTTPException:
                raise
            except Exception as exc:
                # 사용자 대면 에러는 스택 노출 없이 친절하게 — 원본 손상 등도 여기로 수렴.
                raise HTTPException(status_code=500, detail="form_fill_failed") from exc
            ctype = ("application/vnd.hancom.hwpx" if out_suffix == ".hwpx" else
                     "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            filename = f"until-form{out_suffix}"
        else:
            from .report import (render_submission_docx, render_submission_html,
                                 render_submission_markdown, render_submission_pdf)
            renderers = {"md": (render_submission_markdown, "text/markdown"),
                         "html": (render_submission_html, "text/html"),
                         "docx": (render_submission_docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                         "pdf": (render_submission_pdf, "application/pdf")}
            if fmt not in renderers:
                raise HTTPException(status_code=404, detail="format_not_found")
            renderer, ctype = renderers[fmt]; data = renderer(result)
        await run_in_threadpool(
            _scoped, request.state.uid, cloud, True,
            _admin_export, request.state.uid, token, result)
        return Response(data, media_type=ctype,
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.put("/api/v1/profile")
    async def save_user_profile(payload: ProfileRequest, request: Request):
        def work():
            from .profile import FIELDS, load_profile, save_profile
            allowed = {name for name, _, _ in FIELDS}
            save_profile({k: v for k, v in payload.values.items() if k in allowed})
            return load_profile()
        return await run_in_threadpool(_scoped, request.state.uid, cloud, True, work)

    @app.get("/api/v1/account")
    async def account(request: Request):
        def work():
            from . import billing
            from .profile import load_profile
            return {"profile": load_profile(), "plan": billing.plan(),
                    "credits": billing.remaining_credits(),
                    "can_draft": billing.can_draft()}
        return await run_in_threadpool(_scoped, request.state.uid, cloud, False, work)

    @app.post("/api/v1/account/redeem")
    async def redeem(payload: RedeemRequest, request: Request):
        def work():
            from . import billing
            ok, balance, message = billing.redeem(payload.code)
            if not ok:
                raise HTTPException(status_code=400, detail=message or "redeem_failed")
            return {"ok": True, "credits": balance}
        return await run_in_threadpool(_scoped, request.state.uid, cloud, True, work)

    def billing_webhook_event(event: str) -> None:
        """결제 이벤트는 원 uid·금액 없이 전용 집계 파일에 열거형만 기록."""
        try:
            from . import adminboard, web
            adminboard.record_event(web._USERS_DIR / "_billing_webhook",
                                    "billing-webhook", event)
        except Exception:
            pass

    @app.post("/billing/webhook")
    async def billing_webhook(request: Request):
        secret = os.getenv("UNTIL_PG_WEBHOOK_SECRET", "")
        if len(secret.encode("utf-8")) < 32:
            raise HTTPException(status_code=404, detail="not_found")
        try:
            declared = int(request.headers.get("Content-Length", "0") or 0)
        except ValueError:
            declared = 0
        from .pg_webhook import MAX_BODY, MAX_SKEW
        if declared < 0 or declared > MAX_BODY:
            raise HTTPException(status_code=413, detail="payload_too_large")
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BODY:
                raise HTTPException(status_code=413, detail="payload_too_large")
            chunks.append(chunk)
        body = b"".join(chunks)
        timestamp = request.headers.get("X-Until-Timestamp", "").strip()
        try:
            stamp = int(timestamp) if timestamp.isascii() and timestamp.isdigit() else 0
        except ValueError:
            stamp = 0
        import time
        if stamp <= 0 or abs(time.time() - stamp) > MAX_SKEW:
            billing_webhook_event("billing_webhook:auth")
            raise HTTPException(status_code=401, detail="stale_signature")
        signature = request.headers.get("X-Until-Signature", "").strip().lower()
        signed = timestamp.encode("ascii") + b"." + body
        expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
        if len(signature) != 64 or not hmac.compare_digest(signature, expected):
            billing_webhook_event("billing_webhook:auth")
            raise HTTPException(status_code=401, detail="invalid_signature")
        try:
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict) or set(payload) != {
                    "event_id", "order_id", "uid", "credits"}:
                raise ValueError
            event_id = payload["event_id"]
            order_id, uid, credits = payload["order_id"], payload["uid"], payload["credits"]
            if (not isinstance(event_id, str) or not 1 <= len(event_id) <= 100
                    or not all(c.isalnum() or c in "-_." for c in event_id)
                    or not isinstance(order_id, str) or not 1 <= len(order_id) <= 100
                    or not all(c.isalnum() or c in "-_." for c in order_id)
                    or not isinstance(uid, str) or not web._UID_RE.match(uid)
                    or isinstance(credits, bool) or not isinstance(credits, int)
                    or not 1 <= credits <= 10_000):
                raise ValueError
        except (UnicodeDecodeError, ValueError, KeyError, TypeError):
            billing_webhook_event("billing_webhook:schema")
            raise HTTPException(status_code=400, detail="invalid_payload") from None

        from .pg_webhook import validate_registered_order
        if not validate_registered_order(web._USERS_DIR, order_id, uid, credits):
            raise HTTPException(status_code=409, detail="order_not_authorized")

        def work():
            from . import billing, web
            from .pg_webhook import settle_registered_order
            if not web._REQ.hydrated_ok:
                raise HTTPException(status_code=503, detail="credit_store_unavailable")
            try:
                return settle_registered_order(
                    web._USERS_DIR, event_id, order_id, uid, credits,
                    billing.add_credits_checked)
            except PermissionError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from None
            except OSError:
                raise HTTPException(status_code=503,
                                    detail="credit_store_unavailable") from None

        balance = await run_in_threadpool(_scoped, uid, True, True, work)
        billing_webhook_event("billing_webhook:ok")
        return {"ok": True, "balance": balance}

    async def _read_signed_body(request: Request, secret: str) -> bytes:
        """서명·시각·크기 검증을 마친 본문 — 충전/환불 웹훅이 그대로 공유한다.

        환불에만 다른 검증 규칙을 쓰면 둘 중 하나가 조용히 약해진다. 충전 경로에서
        쓰던 절차를 그대로 뽑아 두 경로가 같은 방어를 지나게 한다."""
        try:
            declared = int(request.headers.get("Content-Length", "0") or 0)
        except ValueError:
            declared = 0
        from .pg_webhook import MAX_BODY, MAX_SKEW
        if declared < 0 or declared > MAX_BODY:
            raise HTTPException(status_code=413, detail="payload_too_large")
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BODY:
                raise HTTPException(status_code=413, detail="payload_too_large")
            chunks.append(chunk)
        body = b"".join(chunks)
        timestamp = request.headers.get("X-Until-Timestamp", "").strip()
        try:
            stamp = int(timestamp) if timestamp.isascii() and timestamp.isdigit() else 0
        except ValueError:
            stamp = 0
        import time
        if stamp <= 0 or abs(time.time() - stamp) > MAX_SKEW:
            billing_webhook_event("billing_refund:auth")
            raise HTTPException(status_code=401, detail="invalid_signature")
        signature = request.headers.get("X-Until-Signature", "").strip().lower()
        signed = timestamp.encode("ascii") + b"." + body
        expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
        if len(signature) != 64 or not hmac.compare_digest(signature, expected):
            billing_webhook_event("billing_refund:auth")
            raise HTTPException(status_code=401, detail="invalid_signature")
        return body

    @app.post("/billing/refund")
    async def billing_refund(request: Request):
        """환불·부분취소·차지백 정산 — 충전(`/billing/webhook`)과 대칭 구조.

        정산 로직은 `pg_webhook.settle_registered_refund`가 이미 갖고 있다.
        여기서는 서명 검증·스키마 검증·스코핑·오류 매핑만 충전과 똑같이 한다."""
        secret = os.getenv("UNTIL_PG_WEBHOOK_SECRET", "")
        if len(secret.encode("utf-8")) < 32:
            raise HTTPException(status_code=404, detail="not_found")
        body = await _read_signed_body(request, secret)
        try:
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict) or set(payload) != {
                    "event_id", "order_id", "uid", "amount"}:
                raise ValueError
            event_id = payload["event_id"]
            order_id, uid, amount = payload["order_id"], payload["uid"], payload["amount"]
            if (not isinstance(event_id, str) or not 1 <= len(event_id) <= 100
                    or not all(c.isalnum() or c in "-_." for c in event_id)
                    or not isinstance(order_id, str) or not 1 <= len(order_id) <= 100
                    or not all(c.isalnum() or c in "-_." for c in order_id)
                    or not isinstance(uid, str) or not web._UID_RE.match(uid)
                    or isinstance(amount, bool) or not isinstance(amount, int)
                    or not 1 <= amount <= 10_000):
                raise ValueError
        except (UnicodeDecodeError, ValueError, KeyError, TypeError):
            billing_webhook_event("billing_refund:schema")
            raise HTTPException(status_code=400, detail="invalid_payload") from None

        from .pg_webhook import validate_refundable_order
        if not validate_refundable_order(web._USERS_DIR, order_id, uid, amount):
            raise HTTPException(status_code=409, detail="refund_not_authorized")

        def work():
            from . import billing, web
            from .pg_webhook import settle_registered_refund
            if not web._REQ.hydrated_ok:
                raise HTTPException(status_code=503, detail="credit_store_unavailable")
            try:
                return settle_registered_refund(
                    web._USERS_DIR, event_id, order_id, uid, amount,
                    billing.revoke_credits_checked)
            except PermissionError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from None
            except OSError:
                raise HTTPException(status_code=503,
                                    detail="credit_store_unavailable") from None

        balance, shortfall = await run_in_threadpool(_scoped, uid, True, True, work)
        billing_webhook_event("billing_refund:ok")
        if shortfall:
            # 회수 부족분을 조용히 삼키지 않는다. "환불은 났는데 회수는 부분만 됐다"가
            # 원장에만 남고 아무도 모르면, 분쟁이 났을 때 운영자가 뒤늦게 발견한다.
            billing_webhook_event("billing_refund:shortfall")
            logging.warning(
                "환불 회수 부족 — order=%s event=%s 부족분=%d 잔액=%d "
                "(사용자가 이미 쓴 몫. 원장의 shortfall이 분쟁 근거)",
                order_id, event_id, shortfall, balance)
        return {"ok": True, "balance": balance, "shortfall": shortfall}

    @app.delete("/api/v1/sessions/{token}", status_code=204)
    async def delete_user_session(token: str, request: Request):
        deleted = await run_in_threadpool(_scoped, request.state.uid, cloud, True,
                                          web.delete_session, token)
        if not deleted:
            raise HTTPException(status_code=404, detail="session_not_found")
        return Response(status_code=204)

    @app.post("/rate")
    async def rate(request: Request):
        form = await request.form()
        token = str(form.get("session") or "")
        try:
            score = int(str(form.get("score") or "0"))
        except ValueError:
            score = 0
        simple_ui = str(form.get("ui") or "") == "simple"
        def work():
            result = web._get_session(token)
            if result is None or not 1 <= score <= 5:
                raise HTTPException(status_code=400, detail="invalid_rating")
            if token not in web._RATINGS:
                web._RATINGS[token] = score
                try:
                    from .feedback import append_record, record_from_result
                    append_record(record_from_result(
                        result, satisfaction=score, backend=f"{default_backend}+rated"))
                except OSError:
                    pass
        await run_in_threadpool(_scoped, request.state.uid, cloud, True, work)
        target = f"/svf/{token}" if simple_ui else f"/vf/{token}"
        return RedirectResponse(target, 303)

    @app.post("/rate/voice")
    async def rate_voice(request: Request):
        form = await request.form()
        token = str(form.get("session") or "")
        value = str(form.get("match") or "")
        csrf = str(form.get("csrf") or "")
        simple_ui = str(form.get("ui") or "") == "simple"
        def work():
            result = web._get_session(token)
            if (result is None or value not in ("yes", "no")
                    or not web._voice_applied(result)
                    or not hmac.compare_digest(csrf, web._voice_csrf(token))):
                raise HTTPException(status_code=400, detail="invalid_voice_rating")
            web.record_voice_rating(token, result, value == "yes",
                                    backend=default_backend, uid=request.state.uid)
        await run_in_threadpool(_scoped, request.state.uid, cloud, True, work)
        return RedirectResponse(f"/svf/{token}" if simple_ui else f"/vf/{token}", 303)

    @app.post("/profile")
    async def save_profile_form(request: Request):
        form = await request.form()
        def work():
            from .profile import FIELDS, save_profile
            save_profile({name: str(form.get(name) or "") for name, _, _ in FIELDS})
        await run_in_threadpool(_scoped, request.state.uid, cloud, True, work)
        return RedirectResponse("/profile?saved=1", 303)

    @app.post("/profile/etl-forget")
    async def forget_etl_token(request: Request):
        """보관된 eTL 토큰 삭제 — 맡긴 것을 되찾는 길은 언제나 열려 있어야 한다."""
        await run_in_threadpool(_scoped, request.state.uid, cloud, True,
                                web._forget_token)
        return RedirectResponse("/profile?courses=1", 303)

    @app.post("/profile/courses")
    async def save_courses_form(request: Request):
        """과목 프로파일(§3 route_hint 폴백) 저장 — 파싱은 web과 공유한다.

        stdlib에만 붙이면 운영(ASGI)에서 화면은 보이는데 저장이 404가 난다 —
        `/profile/tone`이 남긴 교훈 그대로다.
        """
        form = await request.form()
        rows = web.course_rows_from_form(dict(form))

        def work():
            from .context.course_profiles import save_course_profiles
            try:
                save_course_profiles(rows)
            except OSError:
                pass  # 저장 실패는 비치명 — 있으면 좋은 폴백이다.

        await run_in_threadpool(_scoped, request.state.uid, cloud, True, work)
        return RedirectResponse("/profile?courses=1", 303)

    @app.post("/profile/tone")
    async def save_tone_form(request: Request):
        """말투 명시 지정 — 운영 엔트리포인트(ASGI)에도 같은 경로를 연다.

        stdlib 서버(`until/web.py`)에만 붙이면 `/profile` 화면의 패널은 보이는데
        누르면 404가 난다. 설정 화면이 '보이지만 작동하지 않는' 상태가 가장 나쁘다.
        """
        form = await request.form()
        want = str(form.get("register") or "").strip()

        def work():
            from .context.tone import REGISTER_PRESETS, load_persona, save_persona
            store = load_persona()
            store.pinned_register = want if want in REGISTER_PRESETS else ""
            save_persona(store)

        await run_in_threadpool(_scoped, request.state.uid, cloud, True, work)
        return RedirectResponse("/profile?saved=1", 303)

    @app.get("/data/export.json")
    async def export_persona_json(request: Request):
        """페르소나 내보내기 — 문체·사실만(과제 원문 제외). 이동권 대응 표면."""
        from .persona.portability import export_persona
        payload = await run_in_threadpool(_scoped, request.state.uid, cloud, False,
                                          export_persona)
        return JSONResponse(payload, headers={
            "Content-Disposition": 'attachment; filename="until-persona.json"'})

    @app.post("/data/delete")
    async def delete_all_data(request: Request):
        """사용자별 전체 삭제. 확인 문구가 틀리면 **아무것도 지우지 않는다.**"""
        form = await request.form()
        if str(form.get("confirm") or "").strip() != "삭제":
            return HTMLResponse(
                "<p>확인 문구가 일치하지 않아 아무것도 지우지 않았습니다.</p>"
                '<p><a href="/profile">← 내 프로필</a></p>', status_code=400)
        uid = request.state.uid

        def work():
            from .persona.retention import delete_all_user_data, kv_keys_for
            root = web._user_root(uid) if uid else web._Path("_until_work")
            report = delete_all_user_data(root)
            # 파일만 지우면 다음 하이드레이션이 KV 미러에서 되살린다 — 세션·미러까지.
            try:
                for f in sorted(web._sess_dir().glob("*.json")):
                    f.unlink()
            except OSError:
                pass
            if uid:
                try:
                    from . import cloudkv
                    client = cloudkv.kv()
                    if client is not None:
                        for key in kv_keys_for(uid):
                            cloudkv.delete_async(key)
                        keys, _definitive = client.list_keys_checked(
                            f"sess:{uid}:", limit=200)
                        for name in keys:
                            cloudkv.delete_async(name)
                except Exception:
                    pass
            for store in (web._SESSIONS, web._ANSWERS, web._SUGGESTIONS,
                          web._REVIEWS, web._WORKSPACES, web._TELEMETRY_META):
                store.clear()
            return report

        report = await run_in_threadpool(_scoped, uid, cloud, True, work)
        body = f"<p>개인 데이터를 삭제했습니다 — {html.escape(report.headline)}.</p>"
        if not report.ok:
            body += ("<p>지우지 못한 파일이 있습니다: "
                     + html.escape(", ".join(sorted(report.failed))) + "</p>")
        return HTMLResponse(body + '<p><a href="/">처음으로</a></p>',
                            status_code=200 if report.ok else 500)

    @app.post("/plan/redeem")
    async def redeem_form(request: Request):
        form = await request.form()
        try:
            await redeem(RedeemRequest(code=str(form.get("code") or "")), request)
            target = "/plan?ok=1"
        except (HTTPException, ValueError):
            target = "/plan?err=redeem"
        return RedirectResponse(target, 303)

    @app.post("/plan/activate")
    async def activate_form(request: Request):
        form = await request.form()
        if cloud:
            return RedirectResponse("/plan?err=1", 303)
        def work():
            from . import billing
            return billing.activate_license(str(form.get("license") or ""))
        ok = await run_in_threadpool(_scoped, request.state.uid, cloud, True, work)
        return RedirectResponse("/plan" if ok else "/plan?err=1", 303)

    @app.post("/sessions/delete")
    async def delete_session_form(request: Request):
        form = await request.form()
        await run_in_threadpool(_scoped, request.state.uid, cloud, True,
                                web.delete_session, str(form.get("token") or ""))
        return RedirectResponse("/sessions", 303)

    @app.post("/history/clear")
    async def clear_history(request: Request):
        def work():
            from .context.answer_history import history_path
            path = history_path()
            if path.exists():
                path.unlink()
        await run_in_threadpool(_scoped, request.state.uid, cloud, True, work)
        return RedirectResponse("/history", 303)

    async def voice_action(request: Request, *, disable: bool):
        form = await request.form()
        def work():
            from .context.teacher_feedback import clear_feedback, disable_feedback
            from .context.voice_autolearn import clear_stored_voice, disable_stored_voice
            if disable:
                disable_stored_voice(web._voice_store_path())
                disable_feedback(web._feedback_store_path())
            else:
                clear_stored_voice(web._voice_store_path())
                clear_feedback(web._feedback_store_path())
        await run_in_threadpool(_scoped, request.state.uid, cloud, True, work)
        token = str(form.get("session") or "")
        return RedirectResponse(f"/v/{token}" if web._TOKEN_RE.match(token) else "/", 303)

    @app.post("/voice/off")
    async def voice_off(request: Request):
        return await voice_action(request, disable=True)

    @app.post("/voice/relearn")
    async def voice_relearn(request: Request):
        return await voice_action(request, disable=False)

    return app


app = create_app(cloud=(os.getenv("UNTIL_CLOUD", "").strip().lower()
                        in {"1", "true", "yes", "on"}))
