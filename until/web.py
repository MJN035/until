"""
P8 — 최소 UI (학생이 쓸 표면).

CLI 대신, 과제를 붙여넣으면 → 경계선 초안 + 결정 체크리스트를 보여주고 →
사람이 결정에 답하면 → 최종 완성본을 돌려준다. (P5~P7 파이프라인 재사용)

설계:
- 의존성 0. 파이썬 표준 라이브러리 http.server 만 사용(Flask 등 불필요).
- 렌더링은 순수 함수(서버 없이 단위 테스트 가능). 서버는 얇은 라우터.
- 단일 사용자 localhost 데모. 결과는 인메모리 세션에 보관(토큰 키).

실행: python -m until.web   (기본 http://127.0.0.1:8000, 백엔드 mock)
      UNTIL_BACKEND=local python -m until.web  로 Groq 등 라이브 백엔드 사용.
"""
from __future__ import annotations

import html
import json
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from typing import Dict, Optional
from urllib.parse import parse_qs, quote as _urlquote, urlsplit

from .config import Config
from .pipeline import run, finalize, suggest_decision_answers, review_result, Result
from .inbox_policy import (
    dday_label as _dday_label,
    filter_sort_inbox as _filter_sort_inbox,
    is_past_due as _is_past_due,  # noqa: F401 - 기존 web._is_past_due 계약
    pick_best as _pick_best,
)
from .user_errors import is_auth_error, user_error_message

# 인메모리 세션: 토큰 -> Result. (localhost 단일 사용자 데모용)
_SESSIONS: Dict[str, Result] = {}
# Canvas 토큰을 페이지 HTML에 노출하지 않도록 서버측에 잠시 보관.
# key=(uid, sid), value=(만료 monotonic 시각, token).
_TOKENS: Dict[tuple[str, str], tuple[float, str]] = {}
_TOKEN_TTL = 15 * 60.0
_TOKEN_MAX = 256
_TOKEN_LOCK = threading.Lock()
# 세션별 누적 결정 답변(원본 초안 인덱스 -> 답). 재답변 루프용.
_ANSWERS: Dict[str, Dict[int, str]] = {}
# 세션별 AI 결정 제안(번호 -> {answer, why}). '모두 수락' 흐름에서 칸을 미리 채우는 용도.
_SUGGESTIONS: Dict[str, Dict[int, dict]] = {}
# 사용자가 비워 둔 채 완성해서 **AI가 대신 채운** 결정 번호. 화면에 그대로 밝힌다 —
# 대신 정해 준 것을 조용히 넘기면 학생은 자기가 정한 줄 알고 제출한다.
_AUTOFILLED: Dict[str, list] = {}
# 세션별 완성도 점검 리포트(ReviewReport). '완성도 점검' 버튼으로 생성.
_REVIEWS: Dict[str, object] = {}
# Session-local editing workspace: excluded source indices and previous draft bodies.
_WORKSPACES: Dict[str, dict] = {}
# Non-content lifecycle facts used only to build privacy-filtered telemetry.
_TELEMETRY_META: Dict[str, dict] = {}
# 인박스 60초 캐시 — 라이브 eTL 재방문(뒤로가기·모드 전환)마다 수 초 재조회 방지.
# key=(토큰 식별, 미제출 필터) → (ts, items, note, fell_back). 60초면 제출 상태 갱신도 수용.
_INBOX_CACHE: Dict[tuple, tuple] = {}
_INBOX_TTL = 60.0


def _token_uid(uid: str = "") -> str:
    return uid or _uid() or "local"


def _store_canvas_token(sid: str, token: str, *, uid: str = "",
                        now: float | None = None) -> None:
    if sid and token:
        clock = time.monotonic() if now is None else now
        with _TOKEN_LOCK:
            _sweep_canvas_tokens(clock)
            _TOKENS[(_token_uid(uid), sid)] = (clock + _TOKEN_TTL, token)
            while len(_TOKENS) > _TOKEN_MAX:
                oldest = min(_TOKENS, key=lambda key: _TOKENS[key][0])
                _TOKENS.pop(oldest, None)


def _get_canvas_token(sid: str, *, uid: str = "",
                      now: float | None = None) -> str:
    key = (_token_uid(uid), sid)
    clock = time.monotonic() if now is None else now
    with _TOKEN_LOCK:
        _sweep_canvas_tokens(clock)
        item = _TOKENS.get(key)
        return item[1] if item is not None else ""


def _sweep_canvas_tokens(now: float) -> None:
    """잠금 안에서 만료 비밀을 지우며 저장소가 TTL을 실제 보존기간으로 지키게 한다."""
    for key, (expires, _token) in list(_TOKENS.items()):
        if expires <= now:
            _TOKENS.pop(key, None)


def is_etl_auth_error(exc: BaseException) -> bool:
    """래핑된 예외까지 따라가 Canvas 401/403을 일관되게 분류한다."""
    return is_auth_error(exc)


def check_canvas_token(token: str) -> dict:
    """토큰을 저장하지 않고 프로필·과목 접근만 확인하는 공용 계약."""
    from .capture.sources.canvas_api import CanvasApiAdapter
    from .capture.sources.discovery import SNU_ETL_BASE
    try:
        adapter = CanvasApiAdapter(token=token)
        profile = adapter.get_self_profile(SNU_ETL_BASE)
        result = {"ok": True, "name": str(profile.get("name") or "").strip()}
        try:
            result["course_count"] = len(adapter.list_courses(SNU_ETL_BASE))
        except Exception:
            result["course_count"] = None
        return result
    except Exception as exc:
        return {"ok": False, "reason": "auth" if is_etl_auth_error(exc) else "net"}


def render_etl_auth_error() -> str:
    return ('<div class="sec"><h2>eTL 연결이 만료됐어요</h2>'
            '<p>토큰이 만료됐거나 무효화됐을 수 있어요. 새 토큰을 발급해 다시 연결해 주세요.</p>'
            '<p><a class="btn" target="_blank" rel="noopener" '
            'href="https://myetl.snu.ac.kr/profile/settings">eTL에서 토큰 재발급 ↗</a> '
            '<a class="btn ghost" href="/">← 홈으로</a></p></div>')


def _telemetry_begin(token: str, result: Result, *, source: str, backend: str,
                     url: str = "") -> None:
    """Capture non-content facts at draft completion and emit the draft stage."""
    import time
    meta = {"source": source, "backend": backend, "draft_started_at": time.time(),
            "revision_count": 0}
    if source == "etl" and url:
        try:
            from .capture.sources.canvas_api import parse_assignment_url
            _base, course_id, assignment_id = parse_assignment_url(url)
            meta.update(course_id=course_id, assignment_id=assignment_id)
        except (TypeError, ValueError):
            pass
    try:
        from .readiness import assess_readiness
        meta["warning_shown"] = sorted({item.label for item in assess_readiness(result).warnings})
    except Exception:
        meta["warning_shown"] = []
    _TELEMETRY_META[token] = meta


def _telemetry_emit(stage: str, token: str, result: Result | None = None,
                    uid: str = "") -> None:
    """Best-effort bridge shared by the stdlib and ASGI servers."""
    try:
        from .telemetry.consent import get_consent
        from .telemetry.web import emit_best_effort
        effective = uid or _uid() or "local"
        consent = get_consent(effective, root=_USERS_DIR)
        # 멀티유저(실제 uid)는 사용자 opt-in이 확인된 경우에만 방출(fail-closed).
        # 로컬 단일사용자("local")는 env 게이트가 기본이되, /consent에서 명시적으로
        # 거부한 기록(False)은 존중한다(설정 UI의 약속과 코드 일치 — 리뷰 15회차).
        if effective != "local":
            if consent is not True:
                return
        elif consent is False:
            return
        current = result or _get_session(token)
        if current is not None:
            hydrated = getattr(_REQ, "hydrated_ok", False)
            # 하이드레이션 미확정 클라우드 요청은 로컬 적립도 하지 않는다 — 이때
            # 만들어진 파일이 이후 하이드레이션의 '파일 있음 스킵'을 유발해 다음
            # mirror가 KV 이력을 절단하는 경로를 원천 차단(리뷰 15회차 F3).
            if CLOUD and not hydrated:
                return
            mirror = CLOUD and hydrated  # KV 미러는 확정 요청만(절단 방지)
            emit_best_effort(stage, effective, current,
                             _ANSWERS.get(token), _SUGGESTIONS.get(token),
                             _TELEMETRY_META.get(token), mirror=mirror)
    except Exception:
        pass

# ── 세션 지속화(디스크) ──────────────────────────────────────────────
# 서버 재시작 시 학생 작업(초안·답변·제안·점검)이 날아가지 않도록 서명 JSON으로 저장.
# _until_work/는 gitignore(개인정보 커밋 방지). 실패해도 응답을 막지 않는다(베스트에포트).
import re as _re_tok
from pathlib import Path as _Path

_SESS_DIR = _Path("_until_work/web_sessions")
_SESS_KEEP = 100                                   # 최근 N개만 보관(무한 증식 방지)
_TOKEN_RE = _re_tok.compile(r"[A-Za-z0-9_-]{1,64}\Z")  # 파일명 안전(경로 탈출 방지)

# ── 클라우드(멀티유저) 모드 ──────────────────────────────────────────
# `--cloud`(또는 UNTIL_CLOUD=1)면 익명 uid 쿠키로 사용자를 구분하고, 세션·히스토리·
# 사용량을 전부 uid 하위 경로로 격리한다. 로컬 모드 동작은 완전히 불변.
import threading as _threading

CLOUD = False                     # serve(cloud=True)에서 설정
_REQ = _threading.local()         # 요청 스코프(스레드=요청): .uid
_UID_RE = _re_tok.compile(r"[A-Za-z0-9_-]{16,64}\Z")  # 쿠키 uid 검증(경로 안전+엔트로피 하한)
_SESS_KEEP_CLOUD = 20             # uid당 세션 보관 수
# UNTIL_REQUIRE_LOGIN=1일 때도 로그인 없이 열려 있는 표면 — 소개·예시·게이트.
# (LLM·크레딧·개인 데이터를 쓰지 않는 마케팅/보조 경로만 넣는다.)
_LOGIN_OPEN_PATHS = frozenset({
    "/healthz", "/beta", "/about", "/about/", "/demo", "/demo/",
    "/beta-request", "/beta-request/",
    "/login", "/logout", "/consent", "/consent/",
})


def _uid() -> str:
    """현재 요청의 uid(클라우드 모드에서만 비어 있지 않음)."""
    return getattr(_REQ, "uid", "") if CLOUD else ""


# CSRF — 상태를 바꾸는 POST는 **우리 페이지에서 시작한 것**이어야 한다.
# 쿠키의 SameSite=Lax는 서로 다른 site에서 온 POST만 막는다. 같은 등록 도메인의
# 형제 오리진(attacker.example.com ↔ app.example.com)은 same-site라 쿠키가 실려
# 가고 Lax가 막지 못한다(감사 2026-08-20). Origin은 그 경우에도 다르므로,
# Origin(없으면 Referer)을 요청 호스트와 대조하는 중앙 검사 하나로 전부 막는다.
#
# 남는 구멍은 정직하게 적어 둔다: 브라우저가 Origin·Referer를 **둘 다** 안 보내는
# 요청은 통과한다. 실제 브라우저는 크로스 오리진 폼 POST에 Origin을 항상 싣기
# 때문에 공격 경로는 닫히지만, 이건 토큰 기반 검증과 달리 '헤더를 믿는' 방식이다.
_CSRF_EXEMPT_PATHS = frozenset({
    "/billing/webhook",      # 서버-서버 호출. 자체 HMAC 서명으로 검증한다.
    "/billing/refund",       # 환불 정산 — 위와 같은 서명 경로.
    "/api/v1/token/check",   # 무저장 검증 — 상태를 바꾸지 않는다.
    # 베타 접수는 **설계상 교차 출처**다 — 폼은 랜딩(workers.dev)에 있고 받는
    # 곳은 앱(onrender.com)이라 브라우저가 Origin을 다른 호스트로 싣는다.
    # 면제해도 잃는 게 없다: 로그인하지 않은 공개 접수라 위조할 세션이 없고,
    # 이 요청이 하는 일(신청 1건 적립)은 누구나 폼을 열어서 할 수 있는 일이다.
    # 남용은 CSRF가 아니라 허니팟·하루 상한이 막는다(betarequests.py).
    "/beta-request", "/beta-request/",
})


def csrf_trusted_origins() -> set:
    """추가로 신뢰할 호스트(UNTIL_CSRF_ORIGINS, 쉼표 구분).

    프록시가 Host를 바꿔 쓰는 배포에서 정상 요청이 막히는 걸 푸는 탈출구다.
    값은 호스트만 적는다(`app.example.com`) — scheme은 무시한다."""
    raw = os.getenv("UNTIL_CSRF_ORIGINS") or ""
    out = set()
    for item in raw.split(","):
        item = item.strip().lower()
        if not item:
            continue
        out.add(urlsplit(item).netloc or item)
    return out


def csrf_enforced() -> bool:
    """UNTIL_CSRF_ENFORCE=0이면 차단 대신 경고만 남긴다.

    이 검사는 모든 상태 변경 POST를 지나므로, 오탐이 나면 서비스가 통째로 멈춘다.
    운영에서 그 상황을 즉시 되돌릴 수 있는 스위치를 남겨 둔다 — 끄더라도
    무슨 요청이 막힐 뻔했는지는 로그로 계속 보인다."""
    return os.getenv("UNTIL_CSRF_ENFORCE") != "0"


def csrf_origin_ok(origin: str, referer: str, host: str) -> bool:
    """POST의 출처가 우리 호스트인지 — 헤더가 아예 없으면 통과(위 주석의 한계)."""
    host = (host or "").split(",")[0].strip().lower()
    if not host:
        return True                     # 호스트를 모르면 판단 근거가 없다
    allowed = {host} | csrf_trusted_origins()
    for value in (origin, referer):
        value = (value or "").strip()
        if not value or value.lower() == "null":
            continue
        try:
            parsed = urlsplit(value)
        except ValueError:
            return False
        if not parsed.netloc:
            continue                    # 상대 Referer — 같은 출처로 본다
        return parsed.netloc.lower() in allowed
    return True                         # Origin·Referer 둘 다 없음


def is_https(headers, scheme: str = "") -> bool:
    """HTTPS 판정 **단일 정책** — stdlib 서버와 ASGI가 같은 답을 내야 한다.

    두 서버가 서로 다른 근거로 판단하면(ASGI가 scope의 scheme만 보던 시절처럼)
    프록시 뒤에서 한쪽만 Secure 쿠키를 빠뜨린다(감사 2026-08-20 지적).
    `headers.get`은 stdlib·Starlette 둘 다 대소문자 무시로 동작한다."""
    if (scheme or "").lower() == "https":
        return True
    try:
        forwarded = (headers.get("X-Forwarded-Proto") or "").lower()
        visitor = headers.get("CF-Visitor") or ""
    except Exception:
        return False
    # X-Forwarded-Proto는 프록시 체인에서 "https, http"처럼 여러 값이 올 수 있다.
    if forwarded.split(",")[0].strip() == "https":
        return True
    return '"scheme":"https"' in visitor


def secure_cookies(headers, scheme: str = "") -> bool:
    """쿠키에 Secure를 붙일지. 운영에서는 강제할 수 있다.

    `UNTIL_FORCE_SECURE_COOKIES=1`이면 헤더와 무관하게 항상 붙인다 — 프록시가
    forwarded 헤더를 안 넘기는 배포에서도 평문 발급을 막는 안전장치다.
    (평문 HTTP로 도는 로컬·테스트에서는 기본값 off를 유지한다. Secure 쿠키는
    http:// 응답에서 클라이언트가 저장하지 않아 흐름 자체가 끊기기 때문이다.)"""
    if os.getenv("UNTIL_FORCE_SECURE_COOKIES") == "1":
        return True
    return is_https(headers, scheme)


def _auth_user():
    """현재 요청의 로그인 사용자(AuthUser) — 미로그인이면 None.

    로그인하면 uid가 익명 쿠키 대신 외부 계정에서 유도된 값으로 고정된다.
    → 기기·브라우저가 바뀌어도 내 과제·명세서가 그대로 따라온다."""
    return getattr(_REQ, "auth", None) if CLOUD else None


def _sess_dir() -> _Path:
    """현재 요청 스코프의 세션 디렉터리(클라우드=uid 하위, 로컬=기존 평면)."""
    u = _uid()
    return (_SESS_DIR / u) if u else _SESS_DIR


def _sess_keep() -> int:
    return _SESS_KEEP_CLOUD if _uid() else _SESS_KEEP


_USERS_DIR = _Path("_until_work/users")  # 테스트에서 임시 경로로 치환 가능


def _user_root(uid: str) -> _Path:
    """uid별 개인 데이터 루트(히스토리·사용량)."""
    return _USERS_DIR / uid


def _adopt_anon_data(anon_uid: str, uid: str) -> int:
    """익명으로 만든 세션·개인 파일을 로그인 계정으로 넘긴다(로그인 순간 1회).

    퍼널상 사용자는 **먼저 과제를 붙여넣고 나중에 로그인**한다. 그때 방금 만든
    초안이 사라지면 로그인이 손해가 되므로, 익명 uid의 산출물을 계정 uid로
    옮긴다. 계정 쪽에 같은 이름이 이미 있으면 건드리지 않는다(덮어쓰기 금지 —
    다른 기기에서 쌓인 진짜 데이터가 우선).
    반환값: 넘어온 세션 수."""
    if not anon_uid or not uid or anon_uid == uid:
        return 0

    def _move_dir(src_dir: _Path, dst_dir: _Path) -> list:
        names = []
        try:
            if not src_dir.is_dir():
                return names
            dst_dir.mkdir(parents=True, exist_ok=True)
            for src in sorted(src_dir.iterdir()):
                if not src.is_file():
                    continue
                dst = dst_dir / src.name
                if dst.exists():
                    continue
                try:
                    src.replace(dst)
                    names.append(src.name)
                except OSError:
                    pass
        except OSError:
            pass
        return names

    sessions = _move_dir(_SESS_DIR / anon_uid, _SESS_DIR / uid)
    _move_dir(_USERS_DIR / anon_uid, _USERS_DIR / uid)
    # 메모리 소유자 표도 함께 넘긴다 — 파일만 옮기면 _get_session의 소유자
    # 검사(_OWNER)가 방금 옮겨온 세션을 '남의 것'으로 보고 404를 낸다.
    for name in sessions:
        tok = name[:-5] if name.endswith(".json") else name
        if _OWNER.get(tok) == anon_uid:
            _OWNER[tok] = uid
    # KV 미러도 새 uid 키로 다시 쓴다 — 디스크만 옮기면 Render 재시작(무료 티어는
    # 디스크가 휘발)에서 방금 '저장한' 과제가 사라진다.
    if sessions:
        try:
            from . import cloudkv
            if cloudkv.kv() is not None:
                for name in sessions:
                    if not name.endswith(".json"):
                        continue
                    tok = name[:-5]
                    if not _TOKEN_RE.match(tok):
                        continue
                    try:
                        blob = (_SESS_DIR / uid / name).read_bytes()
                    except OSError:
                        continue
                    cloudkv.put_async(f"sess:{uid}:{tok}", blob, cloudkv.TTL_SESS)
                    cloudkv.delete_async(f"sess:{anon_uid}:{tok}")
        except Exception:
            pass                       # 미러는 베스트에포트 — 디스크 이동이 정본
        _SESS_META_CACHE.clear()
    return len(sessions)


def _new_token() -> str:
    """세션 토큰 — 클라우드는 128bit(능력 URL 추측 불가), 로컬은 기존 64bit."""
    return secrets.token_urlsafe(16 if CLOUD else 8)


# ── 문체 자동 학습(eTL 제출물 → VoiceProfile) ────────────────────────
# 첫 eTL 인박스 성공 시 내 제출물에서 문체를 배워 프로파일만 저장한다(원문 미보관).
# 설계: docs/superpowers/specs/2026-07-28-voice-autolearn-design.md
_VOICE_STORE_LOCAL = _Path("_until_work/voice_profile.json")


def _voice_store_path() -> _Path:
    """현재 요청 스코프의 문체 프로파일 경로(클라우드=uid 하위, 로컬=평면)."""
    u = _uid()
    return (_user_root(u) / "voice_profile.json") if u else _VOICE_STORE_LOCAL


def _stored_voice():
    """(적용할 VoiceProfile 또는 None, disabled, n_docs) — 실패는 빈 값(비치명적)."""
    from .context.voice_autolearn import load_stored_voice
    try:
        return load_stored_voice(_voice_store_path())
    except Exception:
        return None, False, 0


_FEEDBACK_STORE_LOCAL = _Path("_until_work/teacher_feedback.json")


def _feedback_store_path() -> _Path:
    """현재 요청 스코프의 교수 피드백 저장 경로(클라우드=uid 하위, 로컬=평면)."""
    u = _uid()
    return (_user_root(u) / "teacher_feedback.json") if u else _FEEDBACK_STORE_LOCAL


def _stored_feedback():
    """(피드백 entries, disabled) — 실패는 빈 값(비치명적)."""
    from .context.teacher_feedback import load_feedback
    try:
        return load_feedback(_feedback_store_path())
    except Exception:
        return [], False


def _stored_feedback_hint() -> str:
    """Execution 주입용 '지난 피드백' 블록(없으면 빈 문자열)."""
    from .context.teacher_feedback import feedback_hint
    return feedback_hint(_stored_feedback()[0])


def _maybe_autolearn_etl(adapter, base_url: str) -> tuple:
    """첫 eTL 연결 시 제출물로 문체+교수 피드백 자동 학습. 반환=(문체 표본, 피드백 건).

    저장 파일이 이미 있으면(성공·0건 마커·disabled 전부) 그 항목은 재실행하지
    않는다. 조회 실패는 저장하지 않고 조용히 스킵 → 다음 인박스에서 재시도.
    어댑터가 해당 조회를 지원하지 않으면(SSO/WS) 아무 것도 하지 않는다.
    (둘 다 필요하면 제출물 API가 과목당 2회 호출되지만 — 첫 1회뿐이라 허용.)"""
    need_voice = hasattr(adapter, "list_my_submissions") and not _voice_store_path().exists()
    need_fb = hasattr(adapter, "list_my_feedback") and not _feedback_store_path().exists()
    if not (need_voice or need_fb):
        return 0, 0
    try:
        courses = adapter.list_courses(base_url, include_past=True)
    except Exception:
        return 0, 0
    nv = nf = 0
    if need_voice:
        try:
            from .context.voice_autolearn import (learn_voice_profile_with_stats,
                                                  save_stored_voice)
            profile, n, stats = learn_voice_profile_with_stats(
                adapter, base_url, courses)
            save_stored_voice(_voice_store_path(), profile, n, stats=stats)
            nv = n if profile.n_samples else 0
        except Exception:
            pass
    if need_fb:
        try:
            from .context.teacher_feedback import (collect_feedback_entries,
                                                   save_feedback)
            entries = collect_feedback_entries(adapter, base_url, courses)
            save_feedback(_feedback_store_path(), entries)  # 0건도 저장(재스캔 방지)
            nf = len(entries)
        except Exception:
            pass
    return nv, nf


# KV 하이드레이션 — 프로세스 시작 후 uid 첫 방문에 세션 목록·히스토리·사용량을
# 미러에서 디스크로 복원(콜드스타트 후 '이전 작업'·'지난 답'이 살아 있게).
#
# 감사 14회차 교훈: '완료' 마킹은 반드시 **성공 후에만**. 일시 KV 장애를 '데이터
# 없음'으로 오인해 미러를 삭제/절단하면 사용자 히스토리가 영구 소실된다.
# 규칙: ① 성공해야 _HYDRATED, ② 동시 첫 요청은 주도 스레드를 기다림,
# ③ 하이드레이션이 확정되지 않은 요청은 미러 쓰기/삭제 금지(_REQ.hydrated_ok).
_HYDRATED: set = set()                       # 성공적으로 복원된 uid
_HYDRATION_EVENTS: Dict[str, "_threading.Event"] = {}  # 진행 중 uid → 완료 이벤트
_HYDRATE_LOCK = _threading.Lock()
_GLOBAL_HYDRATED = False                     # 전역 사용량 카운터 복원 여부


def _hydrate_user(uid: str) -> bool:
    """uid의 KV 미러를 디스크로 복원. 반환 = 이 요청에서 미러 쓰기가 안전한가.

    True: 복원 완료(또는 KV 비활성 — 미러 자체가 없음).
    False: 복원 실패/미확정 — 이번 요청은 KV 미러를 건드리면 안 됨.
    """
    from . import cloudkv
    c = cloudkv.kv()
    if c is None:
        return True
    with _HYDRATE_LOCK:
        if uid in _HYDRATED:
            return True
        ev = _HYDRATION_EVENTS.get(uid)
        if ev is None:
            ev = _threading.Event()
            _HYDRATION_EVENTS[uid] = ev
            leader = True
        else:
            leader = False
    if not leader:
        # 같은 uid의 동시 첫 요청 — 주도 스레드의 복원을 기다린다(한도 우회·
        # 절단 미러 방지). 타임아웃 시에도 미러 금지로 안전.
        ev.wait(timeout=20)
        with _HYDRATE_LOCK:
            return uid in _HYDRATED
    ok = True
    try:
        from datetime import date as _date
        sdir = _SESS_DIR / uid
        try:
            sdir.mkdir(parents=True, exist_ok=True)
        except OSError:
            ok = False
        if ok:
            keys, definitive = c.list_keys_checked(f"sess:{uid}:",
                                                   limit=_SESS_KEEP_CLOUD * 2)
            if not definitive:
                ok = False
            for key in keys:
                tok = key.rsplit(":", 1)[-1]
                if not _TOKEN_RE.match(tok):
                    continue
                p = sdir / f"{tok}.json"
                if p.exists():
                    continue
                blob, definitive = c.get_checked(key)
                if not definitive:
                    ok = False
                if blob:
                    try:
                        p.write_bytes(blob)
                        _set_session_mtime(p, blob)  # 원래 작업 시각 복원(목록 순서)
                    except OSError:
                        pass
            root = _user_root(uid)
            # hist·usage는 로컬 파일이 있으면 그대로(append/일일). credits는 잔액=금전이라
            # KV가 정본이다 — 로컬에 (직전 실패로 생긴 starter) 파일이 있어도 KV에 값이
            # 있으면 **항상 덮어써** 복원한다. 이 always-restore가 없으면 과금 리뷰 Finding
            # #1(일시적 KV 실패로 만들어진 starter 파일이 정본으로 굳어 결제 잔액 유실)이 난다.
            for key, rel, force in (
                (f"hist:{uid}", "answer_history.jsonl", False),
                (f"prof:{uid}", "profile.json", False),
                (f"vprof:{uid}", "voice_profile.json", False),
                (f"tfb:{uid}", "teacher_feedback.json", False),
                (f"cprof:{uid}", "course_profiles.json", False),
                (f"etltok:{uid}", "etl_token.json", False),
                (f"pers:{uid}", "persona.json", False),
                (f"epi:{uid}", "episodes.jsonl", False),
                (f"fact:{uid}", "facts.json", False),
                (f"edit:{uid}", "edit_events.jsonl", False),
                (f"pevt:{uid}", "persona_events.jsonl", False),
                (f"adm:{uid}", "admin.json", False),
                (f"consent:{uid}", "consent.json", False),
                (f"telem:{uid}", "telemetry.jsonl", False),
                (f"telem:{uid}:1", "telemetry.jsonl.1", False),
                (f"usage:{uid}:{_date.today().isoformat()}", "usage.json", False),
                (f"credits:{uid}", "credits.json", True),
            ):
                fp = root / rel
                if fp.exists() and not force:
                    continue
                blob, definitive = c.get_checked(key)
                if not definitive:
                    ok = False
                    continue  # 미확정이면 로컬 starter를 정본으로 굳히지 않는다
                if blob:
                    try:
                        fp.parent.mkdir(parents=True, exist_ok=True)
                        fp.write_bytes(blob)
                    except OSError:
                        pass
    except Exception:
        ok = False
    finally:
        with _HYDRATE_LOCK:
            if ok:
                _HYDRATED.add(uid)
            _HYDRATION_EVENTS.pop(uid, None)
        ev.set()
    return ok


def _set_session_mtime(p: _Path, blob: bytes) -> None:
    """복원된 세션 파일의 mtime을 저장 시점(payload ts)으로 — /sessions 시각·순서 보존."""
    try:
        from .session_store import decode
        payload = decode(blob)
        ts = payload.get("ts") if payload else None
        if isinstance(ts, (int, float)) and ts > 0:
            import os as _os
            _os.utime(p, (ts, ts))
    except Exception:
        pass


def _hydrate_global() -> None:
    """전역 일일 상한 카운터를 KV에서 복원(프로세스당 1회) — 재시작 리셋 방지."""
    global _GLOBAL_HYDRATED
    from . import cloudkv
    c = cloudkv.kv()
    if c is None:
        return
    with _HYDRATE_LOCK:
        if _GLOBAL_HYDRATED:
            return
    from datetime import date as _date
    from . import billing as _billing
    ok = True
    if not _billing.USAGE_PATH.exists():
        blob, definitive = c.get_checked(f"usage:__global__:{_date.today().isoformat()}")
        if not definitive:
            ok = False
        if blob:
            try:
                _billing.USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
                _billing.USAGE_PATH.write_bytes(blob)
            except OSError:
                pass
    if ok:
        with _HYDRATE_LOCK:
            _GLOBAL_HYDRATED = True


def _mirror_user(uid: str) -> None:
    """요청 처리 후 사용자 파일(히스토리·사용량)을 KV로 미러(비차단·베스트에포트).

    하이드레이션이 확정된 요청에서만 동작(_end_request가 게이트) — 미확정 상태의
    '파일 없음'을 삭제 신호로 오인해 KV의 유일 사본을 지우는 사고 방지.
    """
    from . import cloudkv
    if cloudkv.kv() is None:
        return
    from datetime import date as _date
    root = _user_root(uid)
    hp = root / "answer_history.jsonl"
    try:
        if hp.exists():
            cloudkv.put_async(f"hist:{uid}", hp.read_bytes(), cloudkv.TTL_HIST)
        else:
            cloudkv.delete_async(f"hist:{uid}")  # '전체 기록 삭제'가 미러에도 반영
    except OSError:
        pass
    up = root / "usage.json"
    try:
        if up.exists():
            cloudkv.put_async(f"usage:{uid}:{_date.today().isoformat()}",
                              up.read_bytes(), cloudkv.TTL_USAGE)
    except OSError:
        pass
    cp = root / "credits.json"
    try:
        if cp.exists():  # 잔액은 영속 키(1년 TTL) — 컨테이너 재시작에도 유지.
            cloudkv.put_async(f"credits:{uid}", cp.read_bytes(), cloudkv.TTL_HIST)
    except OSError:
        pass
    pp = root / "profile.json"
    try:
        if pp.exists():  # 프로필도 영속(히스토리와 동일 TTL) — '1회 저장' 약속 유지.
            cloudkv.put_async(f"prof:{uid}", pp.read_bytes(), cloudkv.TTL_HIST)
        else:
            cloudkv.delete_async(f"prof:{uid}")  # 프로필 삭제가 미러에도 반영
    except OSError:
        pass
    vp = root / "voice_profile.json"
    try:
        if vp.exists():  # 문체 프로파일도 영속 — 콜드스타트 후 재학습 방지.
            cloudkv.put_async(f"vprof:{uid}", vp.read_bytes(), cloudkv.TTL_HIST)
        else:
            cloudkv.delete_async(f"vprof:{uid}")  # '다시 학습'(삭제)이 미러에도 반영
    except OSError:
        pass
    fp = root / "teacher_feedback.json"
    try:
        if fp.exists():  # 교수 피드백도 동일 수명 정책.
            cloudkv.put_async(f"tfb:{uid}", fp.read_bytes(), cloudkv.TTL_HIST)
        else:
            cloudkv.delete_async(f"tfb:{uid}")
    except OSError:
        pass
    etltok = root / "etl_token.json"
    try:
        # 보관된 eTL 연결(암호문). 미러하지 않으면 Render 무료 티어가 잠들 때마다
        # 사라져 '자동 연결'이 사실상 동작하지 않는다. 평문이 아니라 봉투를 보내며,
        # 복호 키(UNTIL_SESSION_KEY)는 KV에 없다 — 미러 유출만으로는 못 연다.
        if etltok.exists():
            cloudkv.put_async(f"etltok:{uid}", etltok.read_bytes(), cloudkv.TTL_HIST)
        else:
            cloudkv.delete_async(f"etltok:{uid}")   # 보관 해제가 미러에도 반영
    except OSError:
        pass
    cprof = root / "course_profiles.json"
    try:
        # 과목 프로파일은 학기 초 1회 적는 값이다 — 콜드스타트에 날아가면
        # 그 학기 내내 §3 폴백이 꺼진 채로 돌고, 사용자는 이유를 알 수 없다.
        if cprof.exists():
            cloudkv.put_async(f"cprof:{uid}", cprof.read_bytes(), cloudkv.TTL_HIST)
        else:
            cloudkv.delete_async(f"cprof:{uid}")  # 전체 삭제가 미러에도 반영
    except OSError:
        pass
    sp = root / "persona.json"
    try:
        if sp.exists():  # 톤 레지스터 페르소나 — 프로필과 같은 영속 정책.
            cloudkv.put_async(f"pers:{uid}", sp.read_bytes(), cloudkv.TTL_HIST)
        else:
            cloudkv.delete_async(f"pers:{uid}")  # 페르소나 삭제가 미러에도 반영
    except OSError:
        pass
    # 기억 3계층·수정 기록 — 히스토리와 같은 수명 정책(원문 파이프, 사용자 소유).
    for rel, key in (("episodes.jsonl", f"epi:{uid}"),
                     ("facts.json", f"fact:{uid}"),
                     ("edit_events.jsonl", f"edit:{uid}"),
                     ("persona_events.jsonl", f"pevt:{uid}")):
        fp2 = root / rel
        try:
            if fp2.exists():
                cloudkv.put_async(key, fp2.read_bytes(), cloudkv.TTL_HIST)
            else:
                cloudkv.delete_async(key)   # '전체 삭제'가 미러에도 반영
        except OSError:
            pass
    ap = root / "admin.json"
    try:
        if ap.exists():  # 관리자 보드 기록(토큰 지문·사용량) — 분석용, 영속.
            cloudkv.put_async(f"adm:{uid}", ap.read_bytes(), cloudkv.TTL_HIST)
    except OSError:
        pass
    np = root / "consent.json"
    try:
        if np.exists():  # 텔레메트리 동의 상태 — '한 번 고지' 약속이라 영속.
            cloudkv.put_async(f"consent:{uid}", np.read_bytes(), cloudkv.TTL_HIST)
    except OSError:
        pass
    # 전역 상한 카운터도 지속화(복원이 확정된 프로세스에서만).
    try:
        from . import billing as _billing
        if _GLOBAL_HYDRATED and _billing.USAGE_PATH.exists():
            cloudkv.put_async(f"usage:__global__:{_date.today().isoformat()}",
                              _billing.USAGE_PATH.read_bytes(), cloudkv.TTL_USAGE)
    except OSError:
        pass


#: 보관된 연결을 '채워진 것처럼' 보이게 하는 가림표. **실토큰이 아니다** —
#: 실값을 HTML에 실으면 페이지 소스·캐시·스크린샷에 자격증명이 남는다.
#: 폼이 이 값을 그대로 돌려주면 "보관본을 쓰라"는 뜻으로 읽는다.
SAVED_TOKEN_MASK = "•" * 16


def uses_saved_token(submitted: str) -> bool:
    """폼이 보낸 토큰이 가림표뿐인가 — 사용자가 덮어쓰지 않았다는 뜻."""
    v = (submitted or "").strip()
    return bool(v) and set(v) <= {"•"}


def _remembered_token_path(uid: str = "") -> "_Path | None":
    """로그인 계정의 eTL 토큰 보관 경로. **로그인 안 했으면 None.**

    익명 uid에는 보관하지 않는다 — 쿠키 하나가 새면 남의 LMS 계정이 통째로
    열린다. 계정은 최소한 구글·카카오 인증 뒤에 있다.
    """
    if not CLOUD or _auth_user() is None:
        return None
    u = uid or _uid()
    return (_user_root(u) / "etl_token.json") if u else None


def _remembered_token(uid: str = "") -> str:
    """보관된 eTL 토큰(없음·만료·복호 실패는 "")."""
    p = _remembered_token_path(uid)
    if p is None:
        return ""
    from . import etltoken
    return etltoken.load(p)


def _remember_token(token: str, *, uid: str = "") -> bool:
    """토큰 보관(사용자가 켠 경우에만 호출). 저장했으면 True."""
    p = _remembered_token_path(uid)
    if p is None or not (token or "").strip():
        return False
    from . import etltoken
    return etltoken.save(p, token)


def _forget_token(uid: str = "") -> None:
    p = _remembered_token_path(uid)
    if p is not None:
        from . import etltoken
        etltoken.clear(p)


def _inquiry_not_my_turn(adapter, anns, title: str, student_id: str) -> bool:
    """이번 주차 질의가 **내 차례가 아님**이 확실한가. 불확실하면 False.

    False로 기울여 둔다 — 잘못 '내 차례 아님'으로 분류하면 학생이 진짜 과제를
    놓친다. 표를 못 읽거나 아직 안 채워졌으면 지금까지처럼 그냥 과제로 둔다.
    """
    try:
        from .context.inquiry_assignment import (spreadsheet_links, sheet_csv_url,
                                                 student_in_week, week_from_title)
        week = week_from_title(title)
        if week is None or not student_id:
            return False
        texts = []
        for a in anns or []:
            texts += [getattr(a, "body", ""), *(getattr(a, "links", []) or [])]
        for link in spreadsheet_links(texts):
            got = student_in_week(adapter.fetch_public_text(sheet_csv_url(link)),
                                  week, student_id)
            if got is True:
                return False       # 내 차례다
            if got is False:
                return True        # 표가 채워져 있고 내 학번이 없다
        return False               # 판단 불가 — 평소대로 과제로 둔다
    except Exception:
        return False


def _attachment_text(adapter, attachment) -> str:
    """공지 첨부 1건을 내려받아 본문 텍스트로. 실패는 ""(예외를 내지 않는다).

    자료 수집(fetch_material_texts)과 같은 규율을 따른다 — 용량 상한, 확장자가
    없으면 매직 바이트로 유형 판정(한글 hwp/hwpx 포함), 깨진 추출은 버린다.
    """
    if adapter is None or not hasattr(adapter, "download"):
        return ""
    import shutil
    import tempfile
    from pathlib import Path as _P

    from .capture.ingest import ingest_file
    from .context.etl_materials import (_looks_garbled, _max_file_bytes,
                                        _sniff_suffix)
    tmp = tempfile.mkdtemp(prefix="until_ann_")
    try:
        path = _P(adapter.download(attachment, tmp))
        if path.stat().st_size > _max_file_bytes():
            return ""
        if not path.suffix:
            path = path.rename(path.with_suffix(_sniff_suffix(path)))
        text = " ".join((ingest_file(str(path)).text or "").split())
        return "" if (not text or _looks_garbled(text)) else text
    except Exception:
        return ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _env_canvas_token() -> str:
    """운영 env의 eTL 토큰 — 클라우드(멀티유저)에서는 절대 공용 폴백으로 쓰지 않음
    (운영자 eTL 계정이 전 사용자에게 노출되는 사고 방지). 로컬 편의 기능 전용.

    WS 모드/Canvas 모드 공통으로 UNTIL_ETL_WS_TOKEN → UNTIL_CANVAS_TOKEN 순."""
    if CLOUD:
        return ""
    import os as _os
    return (_os.getenv("UNTIL_ETL_WS_TOKEN") or _os.getenv("UNTIL_CANVAS_TOKEN") or "").strip()


def _persist_session(token: str) -> None:
    """세션 상태(Result+답변+제안+점검)를 디스크에 저장. 실패는 조용히 무시."""
    if not _TOKEN_RE.match(token or ""):
        return
    _claim_session(token)  # 생성·변경 주체 = 소유자(메모리 격리 심층방어)
    try:
        sdir = _sess_dir()
        sdir.mkdir(parents=True, exist_ok=True)
        import time as _time
        ts = _time.time()
        payload = {
            "result": _SESSIONS.get(token),
            "answers": _ANSWERS.get(token),
            "autofilled": _AUTOFILLED.get(token),
            "suggestions": _SUGGESTIONS.get(token),
            "review": _REVIEWS.get(token),
            "telemetry_meta": _TELEMETRY_META.get(token),
            "workspace": _WORKSPACES.get(token),
            "voice_match": _VOICE_RATINGS.get(token),
        }
        res = payload["result"]
        spec = getattr(res, "spec", None) or {}
        title = str(spec.get("title") or spec.get("deliverable")
                    or spec.get("goal") or "과제").strip()[:70]
        try:
            from .readiness import assess_readiness
            n_warn = len(assess_readiness(res).warnings) if res is not None else 0
        except Exception:
            n_warn = 0
        dl = getattr(res, "deadline", None)
        meta = {"title": title or "과제", "task_type": spec.get("task_type") or "",
                "n_dec": res.draft.n_decisions if getattr(res, "draft", None) else 0,
                "final": getattr(res, "final_draft", None) is not None,
                "n_warnings": n_warn,
                "deadline": dl.due.isoformat() if dl is not None else "",
                # 과제함에서 '끝난 과제'를 한눈에 — 세션 메타에 실어야 재시작 후에도
                # 목록이 알 수 있다(workspace 본문은 목록이 읽지 않는다).
                "submitted": bool(_submit_state(token).get("submitted_at")),
                # 아카이브(/archive)가 과목별로 묶으려면 메타에 있어야 한다 —
                # 없으면 목록이 전체 Result를 복원해야 해서 화면이 느려진다.
                "course": str(spec.get("course") or "").strip()[:60]}
        from .session_store import encode
        from . import atomicio
        blob = encode(payload, ts, meta)
        # 원자적 쓰기(tmp+os.replace) — 크래시/동시 저장 중 파일이 절반만
        # 쓰인 상태로 남아 다음 복원(_restore_session)이 깨지는 것을 방지.
        atomicio.atomic_write_bytes(sdir / f"{token}.json", blob)
        # 클라우드: KV 미러(컨테이너 재시작·슬립 후에도 세션 유지, 비차단).
        u = _uid()
        if u:
            from . import cloudkv
            cloudkv.put_async(f"sess:{u}:{token}", blob, cloudkv.TTL_SESS)
        # 메타 캐시 축출 — mtime 해상도(같은 tick 재저장) 스테일 가능성 제거.
        _SESS_META_CACHE.pop(token, None)
        # 오래된 세션 정리(최근 _sess_keep()개만 — 클라우드는 uid당).
        # KV 미러도 함께 삭제 — 안 지우면 재시작 하이드레이션 때 부활한다.
        files = sorted(sdir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[_sess_keep():]:
            if u:
                from . import cloudkv
                cloudkv.delete_async(f"sess:{u}:{old.stem}")
            try:
                old.unlink()
            except OSError:
                pass
    except Exception:
        pass


def _restore_session(token: str) -> Optional[Result]:
    """디스크에서 세션 상태를 복원(있으면). 손상 파일은 무시."""
    if not _TOKEN_RE.match(token or ""):
        return None
    p = _sess_dir() / f"{token}.json"
    blob = None
    if not p.exists():
        # 클라우드: 디스크 미스 → KV 미러에서 복원(재시작·다른 인스턴스).
        u = _uid()
        if u:
            from . import cloudkv
            c = cloudkv.kv()
            blob = c.get(f"sess:{u}:{token}") if c else None
            if blob:
                try:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(blob)  # 다음 조회는 디스크에서
                except OSError:
                    pass
        if blob is None:
            return None
    try:
        from .session_store import decode
        payload = decode(blob if blob is not None else p.read_bytes())
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    res = payload.get("result")
    if res is None:
        return None
    _SESSIONS[token] = res
    if payload.get("answers"):
        _ANSWERS[token] = payload["answers"]
    if payload.get("autofilled"):
        _AUTOFILLED[token] = list(payload["autofilled"])
    if payload.get("suggestions"):
        _SUGGESTIONS[token] = payload["suggestions"]
    if payload.get("review") is not None:
        _REVIEWS[token] = payload["review"]
    if payload.get("telemetry_meta") is not None:
        _TELEMETRY_META[token] = payload["telemetry_meta"]
    if payload.get("workspace") is not None:
        _WORKSPACES[token] = payload["workspace"]
    if payload.get("voice_match") is not None:
        _VOICE_RATINGS[token] = bool(payload["voice_match"])
    return res


# 클라우드 심층방어 — 인메모리 세션의 소유자(uid) 추적. 격리가 토큰 엔트로피에만
# 의존하지 않게, 토큰이 유출돼도(링크 공유·스크린샷) 타 uid의 접근을 막는다.
_OWNER: Dict[str, str] = {}


def _claim_session(token: str) -> None:
    """현재 요청의 uid를 토큰 소유자로 기록(클라우드에서만 의미)."""
    u = _uid()
    if u:
        _OWNER[token] = u


def _get_session(token: str) -> Optional[Result]:
    """메모리 → 디스크 순으로 세션 조회(재시작 복원).

    클라우드: 메모리 히트여도 소유자 uid가 다르면 없는 세션으로 취급 —
    디스크/KV 경로와 동일한 uid 네임스페이스 규칙을 메모리에도 적용.
    """
    res = _SESSIONS.get(token)
    if res is not None and CLOUD:
        # fail-closed: 소유자 표가 **없어도** 메모리 히트를 그냥 내주지 않는다.
        # 예전에는 owner가 None이면 통과시켰는데, 그건 심층방어가 fail-open이라는
        # 뜻이다(감사 2026-08-20). 소유자가 확정되지 않았으면 메모리를 무시하고
        # 내 네임스페이스의 디스크/KV에서 복원해 소유권을 확정한다.
        if _OWNER.get(token) != _uid():
            res = None
    if res is None:
        res = _restore_session(token)
        if res is not None:
            _claim_session(token)  # 자기 네임스페이스에서 복원됐으므로 소유 확정
    return _apply_format_pass(res)


def _apply_format_pass(res):
    """제출 형식 검증 — 화면·다운로드로 나가기 **직전** 한 번. 결정적, LLM 0.

    파이프라인 안이 아니라 여기서 도는 이유가 있다. 8월은 `algo_version`을 동결하고
    측정하는 달이라 `pipeline.run`의 출력(결정성 지문)을 건드리면 그 달 백테스트가
    통째로 무의미해진다. 형식은 알고리즘이 아니라 **산출물의 겉모습**이므로 표면에서
    고치는 것이 맞고, 동결도 지킨다.

    `check_and_fix`는 멱등이라 조회할 때마다 돌아도 결과가 같다(시험이 고정한다).
    실패는 삼킨다 — 형식 검증이 과제 화면 자체를 막으면 안 된다.
    """
    if res is None:
        return None
    try:
        from .execution.format_guard import check_and_fix
        try:
            from .profile import load_profile
            profile = load_profile()
        except Exception:
            profile = {}
        draft = getattr(res, "final_draft", None) or getattr(res, "draft", None)
        body, issues = check_and_fix(res, profile=profile)
        if draft is not None and body and body != getattr(draft, "body", ""):
            draft.body = body
        res.format_issues = issues
    except Exception:
        res.format_issues = []
    return res


# list_sessions 메타 캐시 — token -> (mtime, item). 홈/목록 로드마다 전체 Result 복원을
# 피한다(파일이 바뀌면 mtime이 달라져 자연 무효화).
_SESS_META_CACHE: Dict[str, tuple] = {}


def list_sessions(limit: int = 30) -> list:
    """지속화된 세션 목록(최신순) — 이전 작업 다시 열기용 메타데이터.

    유효성 검증(손상 JSON·서명·버전·비정상 토큰 제외)을 통과한 항목이 limit개가
    될 때까지 전체 파일을 훑는다 — 손상 파일 하나가 정상 세션 슬롯을 가리지 않게.
    """
    from datetime import datetime, date
    try:
        # mtime을 한 번만 읽어 재사용(경합으로 파일이 사라져도 stat 재호출 없음).
        files = sorted(((p, p.stat().st_mtime) for p in _sess_dir().glob("*.json")),
                       key=lambda t: t[1], reverse=True)
    except OSError:
        return []
    # 캐시 키 = (mtime, 오늘) — n_warnings의 마감 D-day 판정이 날짜에 의존하므로
    # 날이 바뀌면 재계산한다(하루 1회, cold ~수십 ms 수준).
    day = date.today().toordinal()
    items = []
    for p, mtime in files:
        if len(items) >= limit:
            break
        token = p.stem
        if not _TOKEN_RE.match(token):
            continue
        cached = _SESS_META_CACHE.get(token)
        if cached and cached[0] == (mtime, day):
            items.append(cached[1])
            continue
        try:
            from .session_store import read_meta
            meta = read_meta(p.read_bytes())
        except Exception:
            continue  # 손상 파일은 목록에서 제외
        if meta is None:
            continue
        # 마감 D-day — 어떤 이전 작업이 급한지 목록에서 보이게(캐시 키에 날짜 포함).
        dday = ""
        due = meta.get("deadline") or ""
        if due:
            try:
                d = (date.fromisoformat(due) - date.today()).days
                if d >= 0:
                    dday = "D-DAY" if d == 0 else f"D-{d}"
            except Exception:
                dday = ""
        item = {
            "token": token,
            "title": meta.get("title") or "과제",
            "when": datetime.fromtimestamp(mtime).strftime("%m/%d %H:%M"),
            "final": bool(meta.get("final")),
            "n_dec": int(meta.get("n_dec") or 0),
            "task_type": meta.get("task_type") or "",
            "n_warnings": int(meta.get("n_warnings") or 0),
            "dday": dday,
            "submitted": bool(meta.get("submitted")),
            "course": str(meta.get("course") or ""),
        }
        _SESS_META_CACHE[token] = ((mtime, day), item)
        items.append(item)
    return items


def delete_session(token: str) -> bool:
    """세션을 메모리·디스크에서 삭제(개인정보 통제). 성공 여부 반환."""
    if not _TOKEN_RE.match(token or ""):
        return False
    # 클라우드: 소유자가 아닌 uid의 삭제 시도는 무시(메모리 축출도 불가).
    if CLOUD:
        owner = _OWNER.get(token)
        if owner is not None and owner != _uid():
            return False
    for d in (_SESSIONS, _ANSWERS, _AUTOFILLED, _SUGGESTIONS, _REVIEWS,
              _TELEMETRY_META, _WORKSPACES,
              _SESS_META_CACHE,
              _OWNER, _RATINGS, _VOICE_RATINGS):
        d.pop(token, None)
    u = _uid()
    if u:
        from . import cloudkv
        cloudkv.delete_async(f"sess:{u}:{token}")  # KV 미러도 함께(비차단)
    p = _sess_dir() / f"{token}.json"
    try:
        if p.exists():
            p.unlink()
            return True
    except OSError:
        pass
    return False

# ── SSO(토큰리스) 탐색 세션 ───────────────────────────────────────────
# 로그인된 브라우저 세션을 재사용하는 단일 어댑터. Playwright sync는 스레드 안전이
# 아니므로 SSO 모드에선 서버를 단일 스레드로 띄우고 이 어댑터를 그 스레드에서만 쓴다.
_SSO_ADAPTER = None


def _sso_adapter():
    """로그인된 eTL 브라우저 세션 어댑터를 lazy 생성·재사용. (단일 스레드 서버 전제)

    첫 호출 시 브라우저 창이 열리고 사용자가 한 번 MySNU 로그인을 한다(최대 5분 대기).
    이후 요청은 같은 세션 쿠키로 토큰 없이 eTL API를 호출한다."""
    global _SSO_ADAPTER
    if _SSO_ADAPTER is None:
        import os
        from .capture.sources.playwright_discovery import PlaywrightDiscoveryAdapter
        base = os.getenv("UNTIL_ETL_BASE", "https://myetl.snu.ac.kr")
        _SSO_ADAPTER = PlaywrightDiscoveryAdapter(base_url=base)
    return _SSO_ADAPTER


def _close_sso_adapter() -> None:
    global _SSO_ADAPTER
    if _SSO_ADAPTER is not None:
        try:
            _SSO_ADAPTER.close()
        except Exception:
            pass
        _SSO_ADAPTER = None

def _wrap(body: str, backend: str, title: str = "UNTIL — 경계선까지") -> str:
    from .web_templates import render_page
    # '작업 환경 · <backend>'는 개발용 표시다. 클라우드에서 이 값은 제공자 어댑터
    # 이름("local")이라 학생에게 뜻이 없을 뿐 아니라 "내 컴퓨터에서 도나?"로 읽힌다
    # (2026-08-22 실사용 관찰 — 라이브 전 화면 상단에 노출돼 있었다).
    # 로컬·개발에서는 그대로 두어 mock/실제 구분이 보이게 한다.
    return render_page(body, "" if CLOUD else backend, title,
                       account=_account_html())


_INPUT = "width:100%;box-sizing:border-box;padding:.5rem;border:1px solid #ccc;border-radius:8px"


# eTL 토큰 '연결 확인' — /api/v1/token/check 왕복(무저장 검증). 연결 단계 화면 전용.
_TOKEN_CHECK_JS = """
 <script>
 async function checkTok(btn) {
   const input=document.getElementById('tok'), out=document.getElementById('tok-status');
   const token=(input&&input.value||'').trim();
   if(!token) { out.textContent='토큰을 먼저 붙여넣어 주세요.'; return; }
   btn.disabled=true; out.textContent='연결을 확인하는 중…';
   try {
     const r=await fetch('/api/v1/token/check', {method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({token})}); const data=await r.json();
     if(data.ok) { const count=data.course_count==null?'':`, 과목 ${data.course_count}개`;
       out.textContent=`✅ ${data.name||'eTL 사용자'}님${count} 확인됐어요`; }
     else if(data.reason==='auth') out.textContent='❌ 토큰이 유효하지 않아요 — 아래 재발급 안내를 확인해 주세요.';
     else out.textContent='❌ eTL에 연결하지 못했어요 — 네트워크를 확인하고 다시 시도해 주세요.';
   } catch(e) { out.textContent='❌ 연결을 확인하지 못했어요. 잠시 후 다시 시도해 주세요.'; }
   finally { btn.disabled=false; }
 }
 </script>"""


def test_all_assignments_allowed(token: str) -> bool:
    """'지난 과제까지 전부' 목록을 열 수 있는 토큰인가.

    UX 테스트용 표면이라 아무에게나 열면 안 된다 — 다른 사용자에게는 지금 해야
    할 과제(미제출·기한 전)만 보이는 게 맞다.

    **토큰을 저장하지 않는다.** `UNTIL_TEST_TOKEN_SHA256`에 SHA-256 지문만 둔다
    (쉼표로 여러 명). 코드·저장소·로그 어디에도 원문이 남지 않고, 토큰을
    재발급하면 지문이 달라져 자동으로 닫힌다.
    """
    import hashlib
    import hmac as _hmac
    allowed = {h.strip().lower()
               for h in (os.getenv("UNTIL_TEST_TOKEN_SHA256") or "").split(",")
               if h.strip()}
    token = (token or "").strip()
    if not (allowed and token):
        return False
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return any(_hmac.compare_digest(digest, item) for item in allowed)


def test_mode_configured() -> bool:
    """테스트 표면 자체를 노출할지 — 지문이 하나도 없으면 링크도 숨긴다."""
    return bool((os.getenv("UNTIL_TEST_TOKEN_SHA256") or "").strip())


# eTL 연결 단계에서 쓰는 모드 — 홈에서 무엇을 누르고 왔는지 그대로 이어 붙인다.
# (라벨, 제출 버튼에 실을 플래그, 미제출·기한 필터를 걸 것인가)
#
# "all"은 **지난 과제까지 전부** 목록으로 본다. 나머지 모드는 미제출·기한 전만
# 보여 주는데(실사용자는 지금 해야 할 것만 보면 되니까), 제품을 훑어보며 여러
# 과제로 시험해 볼 때는 그 필터가 오히려 방해가 된다. practice 모드도 필터를
# 끄지만 그쪽은 하나를 자동 선택해 버려서 '골라 가며' 테스트할 수가 없다.
_CONNECT_MODES = {
    "fast":     ("가장 가까운 과제 하나 해결하기", "fast", True),
    "list":     ("과제 목록 불러오기", "", True),
    "practice": ("이미 한 과제로 다시 해보기", "practice", True),
    "all":      ("지난 과제까지 전부 보기", "", False),
}


def render_connect(mode: str = "fast", sso: bool = False) -> str:
    """eTL 연결 단계 — 홈에서 '과제 하나 해결'을 누른 **다음** 화면.

    왜 별도 화면인가(사용자 지시, 3회차 멘토링 후속): 홈에 토큰 입력칸을 먼저
    놓으면 처음 온 사람은 '이게 뭔데 비밀번호를 달라는 거지'에서 멈춘다.
    무엇을 할지 먼저 고르고(클릭) → 그러려면 eTL 연결이 필요하다는 순서가
    이해하기 쉽다. 토큰이 없는 사람을 위한 탈출구(붙여넣기)도 이 화면에 둔다.
    """
    label, flag, filtered = _CONNECT_MODES.get(mode, _CONNECT_MODES["fast"])
    submit_attr = f' name="{flag}" value="1"' if flag else ""
    onclick = ' onclick="fastmsg(this.form)"' if flag else ""
    if sso:
        body = ('<p class="meta">버튼을 누르면 브라우저 창이 한 번 열려요 — '
                'MySNU 로그인만 마치면 자동으로 이어집니다.</p>')
        attrs = (' data-loadmsg="Waiting for login"'
                 ' data-submsg="열린 브라우저 창에서 MySNU 로그인을 마치면 자동 진행 (최대 5분)"')
        script = ""
    else:
        # 보관된 연결이 있으면 **입력칸이 이미 채워져 보이게** 한다. 화면 모양을
        # 바꾸지 않는 게 낫다(사용자 지시 2026-08-23) — "저장돼 있어요" 안내로
        # 갈아 끼우면 같은 화면을 두 벌 기억해야 한다.
        #
        # ⚠ 채워 보이는 값은 **가림표일 뿐 진짜 토큰이 아니다.** 실토큰을 HTML에
        # 넣으면 페이지 소스·브라우저 캐시·스크린샷에 자격증명이 남는다. 서버는
        # 값이 가림표 그대로면 보관본을 쓰고, 사용자가 덮어써서 보내면 그쪽을 쓴다.
        remembered = bool(_remembered_token())
        if remembered:
            val = f' value="{SAVED_TOKEN_MASK}"'
            hint = ('<p class="meta" style="margin:.45rem 0 0">저장된 eTL 연결을 '
                    '씁니다 — 바꾸려면 새 토큰을 붙여넣으세요. '
                    '<a href="/profile">보관 해제</a></p>')
            keep = ""
        else:
            val = ""
            hint = ""
            # 로그인한 사람에게만 보관을 제안한다(익명 쿠키에는 저장하지 않는다).
            # 기본은 꺼짐 — 남의 LMS를 여는 열쇠를 묻지 않고 맡아 두지 않는다.
            keep = ('<label class="meta" style="display:block;margin:.55rem 0 0">'
                    '<input type="checkbox" name="remember" value="1"> '
                    '다음에도 자동으로 연결 — 이 토큰을 <b>내 계정에 암호화해 보관</b>합니다'
                    '(언제든 <a href="/profile">설정</a>에서 해제).</label>'
                    if _auth_user() is not None else
                    '<p class="meta" style="margin:.45rem 0 0">'
                    '<a href="/login">로그인</a>하면 다음부터 토큰을 다시 넣지 '
                    '않아도 됩니다.</p>')
        body = ('<p class="meta">eTL에서 과제를 읽어오려면 <b>액세스 토큰</b>이 한 번 필요해요. '
                '보관을 켜지 않으면 과제를 불러오는 동안만 메모리에 머뭅니다.</p>'
                '<div class="tokrow" style="margin-top:1rem">'
                f'<input id="tok" name="token" type="password"{val} '
                'placeholder="eTL ACCESS TOKEN">'
                '<button type="button" class="btn ghost" onclick="pasteTok(this)"'
                ' style="white-space:nowrap">붙여넣기</button>'
                '<button type="button" class="btn ghost" onclick="checkTok(this)"'
                ' style="white-space:nowrap">연결 확인</button></div>'
                '<p id="tok-status" class="meta" role="status" aria-live="polite"'
                ' style="margin:.45rem 0 0"></p>' + hint + keep)
        attrs = ""
        script = _TOKEN_CHECK_JS
    # 필터 입력은 **항상** 싣는다. "전부 보기"는 서버가 토큰 지문을 확인한 뒤에만
    # 필터를 푸는 구조라(fail-closed), 폼에서 빼 버리면 아무나 전체 목록을 여는
    # 구멍이 된다. 여기서는 의도만 `all=1`로 표시한다.
    filters = "\n  ".join(
        ['<input type="hidden" name="unsubmitted" value="1">',
         '<input type="hidden" name="hide_past" value="1">']
        + ([] if filtered else ['<input type="hidden" name="all" value="1">']))
    help_box = "" if sso else """
 <details class="tgsec" style="margin-top:1.6rem">
  <summary><span class="meta">토큰이 없다면? — 1분 안내 펼치기</span></summary>
  <ol style="margin:.7rem 0 .3rem;padding-left:1.2rem;line-height:1.85;font-size:.9rem">
   <li><a class="btn ghost" style="padding:.35rem .7rem" target="_blank" rel="noopener"
       href="https://myetl.snu.ac.kr/profile/settings">eTL 토큰 페이지 열기 ↗</a></li>
   <li>아래로 내려 <b>+ 새 액세스 토큰</b>을 누르세요.</li>
   <li>목적 칸에 <b>until</b>을 쓰고, 만료일은 비운 채 <b>토큰 생성</b>을 누르세요.</li>
   <li>나온 토큰을 복사해 여기로 돌아와 <b>붙여넣기</b> —
       그 화면을 벗어나면 다시 볼 수 없어요</li>
  </ol>
  <p class="meta" style="margin:.4rem 0 0">보관을 켜지 않으면 토큰은 과제를 불러오는
   동안만 메모리에 머물러요. 어느 쪽이든 eTL 설정에서 언제든 무효화할 수 있어요.</p>
 </details>"""
    return f"""
<div class="smp">
 <p class="smp-step">2단계 · eTL 연결</p>
 <h1 class="smp-t">{html.escape(label)}</h1>
 <form method="post" action="/inbox"{attrs}>
  <input type="hidden" name="ui" value="simple">
  {filters}
  {body}
  <p style="margin-top:1.4rem"><button class="btn block big" type="submit"{submit_attr}{onclick}>
   연결하고 과제 가져오기</button></p>
 </form>{script}{help_box}
  <div class="matbox" style="margin-top:1.6rem">
   <p class="meta" style="margin:0">Until은 eTL 토큰으로 과제와 자료를 안전하게 불러온 뒤 시작합니다.
    보관을 켠 경우에만 내 계정에 암호화해 저장하고, 그 외에는 연결하는 동안에만
    메모리에 머뭅니다.</p>
  </div>
 <p class="smp-x"><span><a href="/">← 처음으로</a></span>
  <span><a href="/about">Until 소개</a></span></p>
</div>
"""


def render_index(has_env_token: bool = False, sso: bool = False) -> str:
    """홈 — 기본 동작은 가장 가까운 미제출 과제 하나를 바로 해결한다.

    화면 순서(사용자 지시): **무엇을 할지 클릭 → 그다음 eTL 토큰**. 토큰이 필요한
    경우 홈에는 입력칸을 두지 않고 `/connect`(render_connect)로 한 칸 미룬다.
    이미 연결 수단이 있으면(SSO·운영 토큰) 홈에서 바로 제출한다.
    """
    # 연결 수단이 이미 있으면(브라우저 SSO·운영 토큰) 홈에서 바로 제출한다.
    # 없으면 토큰을 묻지 않고 /connect로 한 칸 미룬다 — 클릭이 먼저, 토큰이 나중.
    direct = bool(sso or has_env_token)
    if sso:
        attrs = (' data-loadmsg="Waiting for login"'
                 ' data-submsg="열린 브라우저 창에서 MySNU 로그인을 마치면 자동 진행 (최대 5분)"')
        hint = "버튼을 누르면 브라우저 창이 한 번 열려요 — MySNU 로그인만 하면 됩니다."
    else:
        attrs = ""
        hint = ""
    _sub = ("font:inherit;font-size:.8rem;color:var(--muted);text-decoration:underline;"
            "text-underline-offset:3px")
    # UX 테스트용 '전부 보기' 진입점 — 지문이 설정된 배포에서만 노출한다.
    # 실제 개방 여부는 서버가 토큰 지문으로 다시 판정한다(링크는 편의일 뿐).
    test_link = ('<span class="meta" style="font-size:.8rem">·</span>'
                 f'<a href="/connect?mode=all" style="{_sub}"'
                 ' title="테스트 계정 전용 — 제출·기한 지난 과제까지 전부 목록으로">'
                 '지난 과제까지 전부 보기</a>') if test_mode_configured() else ""
    if direct:
        # 필터는 기본값(미제출·기한 전)으로 내장 — 조절은 목록 화면에서.
        form = f"""
 <form method="post" action="/inbox"{attrs}>
  <input type="hidden" name="ui" value="simple">
  <input type="hidden" name="unsubmitted" value="1">
  <input type="hidden" name="hide_past" value="1">
  <p style="margin-top:1.2rem"><button class="btn block big" type="submit" name="fast" value="1"
     onclick="fastmsg(this.form)">가장 가까운 과제 하나 해결하기</button></p>
  <p style="margin-top:.9rem;display:flex;gap:.5rem;align-items:baseline;flex-wrap:wrap">
   <button type="submit" style="background:none;border:0;padding:0;cursor:pointer;{_sub}"
     >과제 목록에서 직접 고르기</button>
   <span class="meta" style="font-size:.8rem">·</span>
   <button type="submit" name="practice" value="1" onclick="fastmsg(this.form)"
     style="background:none;border:0;padding:0;cursor:pointer;{_sub}"
     title="새 과제가 없을 때 — 이미 낸 과제 중 최근 것으로 흐름을 다시 돌려봅니다">이미 한 과제로 다시 해보기</button>
  </p>
 </form>
 {f'<p class="meta" style="margin-top:.6rem">{hint}</p>' if hint else ""}"""
    else:
        form = f"""
 <p style="margin-top:1.2rem"><a class="btn block big" href="/connect?mode=fast"
    >가장 가까운 과제 하나 해결하기</a></p>
  <p class="meta" style="margin:.6rem 0 0;font-size:.8rem">누르면 eTL 연결(토큰) 한 칸만 거칩니다.
   보관을 켜지 않으면 토큰은 과제를 불러오는 동안에만 사용합니다.</p>
 <p style="margin-top:.9rem;display:flex;gap:.5rem;align-items:baseline;flex-wrap:wrap">
  <a href="/connect?mode=list" style="{_sub}">과제 목록에서 직접 고르기</a>
  <span class="meta" style="font-size:.8rem">·</span>
  <a href="/connect?mode=practice" style="{_sub}"
     title="새 과제가 없을 때 — 이미 낸 과제 중 최근 것으로 흐름을 다시 돌려봅니다"
     >이미 한 과제로 다시 해보기</a>{test_link}
 </p>"""
    # 재방문 신호는 도구 메뉴 위에 짧게 분리한다. 새 작업과 이전 작업을 한 줄에
    # 섞으면 목적이 다른 링크가 모두 같은 무게로 보여 첫 선택이 어려워진다.
    resume = ""
    if list_sessions(limit=1):
        resume = ('<aside class="home-resume" aria-label="이전 작업">'
                  '<span>이어서 하기</span><a href="/sessions">이전 작업</a>'
                  '<a href="/archive">내 과제 아카이브</a></aside>')
    # 보조 링크는 세 개뿐이라 분류명·세로선 없이 한 줄로 나열한다. 묶음을 만들면
    # 항목보다 분류명이 먼저 읽혀 첫 선택이 늦어진다.
    # `/profile`은 어디에서도 링크된 적이 없었다 — 신상 자동 채움도 과목 프로파일도
    # 주소를 직접 쳐야 닿는 화면이라 사실상 없는 기능이었다(2026-08-22 실사용 원장).
    tool_links = (
        ("/about", "Until 소개"),
        ("/profile", "내 정보·과목 설정"),
        ("/plan", "플랜·데이터 설정"),
    )
    tools = ''.join(f'<a href="{href}"><span>{html.escape(text)}</span></a>'
                    for href, text in tool_links)
    return f"""
<div class="smp">
 <h1 class="smp-t">마감이 가장 가까운 과제 하나,<br><span class="hl">제출 직전</span>까지.</h1>
 <p class="meta">미제출·마감 전 과제를 우선합니다. 관점·취향·진로 같은 판단은 대신 정하지 않습니다.</p>
{form}
 {resume}
 <nav class="home-tools" aria-label="다른 시작 방법">{tools}</nav>
</div>
"""


def render_sessions(items: list) -> str:
    """이전 작업(지속화된 세션) 목록 페이지 — 클릭으로 이어서 열기."""
    head = ('<div class="sec" style="border:0">'
            f'<div class="lab"><span class="n">↺</span> / SESSIONS · {len(items)} <span class="ln"></span></div>'
            '<h2>이전 작업 다시 열기</h2>'
            '<p class="meta">서버를 껐다 켜도 여기 남아 있어요. 최신순.</p>')
    if not items:
        return head + ('<p class="meta">저장된 작업이 없습니다. 새 과제로 시작하세요.</p>'
                       '<p><a class="btn ghost back" href="/">← Home</a></p></div>')
    from .understanding.task_type import LABELS as _TYPE_LABELS
    rows = []
    for it in items:
        tok = html.escape(it["token"])
        href = f"/vf/{tok}" if it["final"] else f"/v/{tok}"
        badge = ('<span class="pill ok">최종본</span>' if it["final"]
                 else f'<span class="pill">결정 {it["n_dec"]}</span>')
        if it.get("submitted"):
            href = f"/ready/{tok}"          # 끝난 과제는 제출 화면으로(기록 확인)
            badge = '<span class="pill ok">✓ 제출함</span> ' + badge
        tlabel = _TYPE_LABELS.get(it.get("task_type") or "", "")
        if tlabel and it.get("task_type") != "general":
            badge = f'<span class="pill">{html.escape(tlabel)}</span> ' + badge
        if it.get("dday"):
            urgent = it["dday"] == "D-DAY" or (
                it["dday"].startswith("D-") and it["dday"][2:].isdigit()
                and int(it["dday"][2:]) <= 3)
            style = (' style="color:var(--warn);border-color:var(--warn)"'
                     if urgent else "")
            badge += f' <span class="pill"{style}>{html.escape(it["dday"])}</span>'
        if it.get("n_warnings"):
            badge += (f' <span class="pill" style="color:var(--warn);border-color:var(--warn)">'
                      f'⚠ {it["n_warnings"]}</span>')
        del_form = (f'<form method="post" action="/sessions/delete" style="display:inline;margin-left:.4rem">'
                    f'<input type="hidden" name="token" value="{tok}">'
                    f'<button class="btn ghost" type="submit" style="padding:.1rem .5rem;font-size:.75rem">삭제</button></form>')
        # 간단 모드로도 열기(✳) — 홈→간단 흐름 사용자가 목록에서 모드 이탈하지 않게.
        shref = f"/svf/{tok}" if it["final"] else f"/sv/{tok}"
        simple_link = f' <a href="{shref}" title="간단 모드로 열기" class="meta">✳</a>'
        rows.append(f'<li style="margin:.45rem 0"><a href="{href}">{html.escape(it["title"])}</a>'
                    f'{simple_link} '
                    f'<span class="meta">· {html.escape(it["when"])}</span> {badge}{del_form}</li>')
    search = ('<input id="sessq" placeholder="제목으로 걸러내기…" style="margin:.2rem 0 .6rem" '
              'oninput="var q=this.value.toLowerCase();'
              "document.querySelectorAll('#sesslist>li').forEach(function(li){"
              "li.style.display=li.textContent.toLowerCase().indexOf(q)>=0?'':'none';});\">")
    return head + (search +
                   f'<ul id="sesslist" style="list-style:none;padding-left:0">{"".join(rows)}</ul>'
                   + ('<p class="meta">삭제하면 내 계정에 저장된 해당 작업(초안·답변)이 지워집니다.</p>'
                      if CLOUD else
                      '<p class="meta">삭제하면 이 컴퓨터에 저장된 해당 작업(초안·답변)이 지워집니다.</p>')
                   # 내 답 히스토리(재제안 원천·전체 삭제 통제) 진입점 — 홈 과밀을
                   # 줄이며 홈에서 이리로 옮김(개인 데이터 통제 접근성 유지).
                   + '<p class="meta"><a href="/archive">내 과제 아카이브</a> · '
                     '<a href="/history">내 답 히스토리 보기·삭제</a></p>'
                   + '<p><a class="btn ghost back" href="/">← Home</a></p></div>')


# ── 내 과제 아카이브 ────────────────────────────────────────────────
# **내 것만** 모은다. 남의 제출물을 보여주는 아카이브는 만들지 않기로 했다
# (2026-08-20 결정): 옆에 남의 완성본이 있으면 학생은 [[DECISION]]을 자기 판단으로
# 채우는 대신 베껴서 채우고, 그러면 boundary_guard를 통과한 산출물이 표절이 된다.
# 경계선을 코드로 지키는 제품이 그 경계를 무력화하는 화면을 함께 둘 수는 없다.
#
# 새 저장소를 만들지 않는다 — 세션 메타(list_sessions)에 이미 있는 것만 묶는다.

def _archive_stats(items: list) -> dict:
    """아카이브 요약 — 총계·제출·완성·유형 분포(전부 이미 있는 메타에서)."""
    from collections import Counter
    types = Counter(it.get("task_type") or "" for it in items if it.get("task_type"))
    courses = Counter(it.get("course") or "" for it in items if it.get("course"))
    return {
        "total": len(items),
        "submitted": sum(1 for it in items if it.get("submitted")),
        "final": sum(1 for it in items if it.get("final")),
        "decisions": sum(int(it.get("n_dec") or 0) for it in items),
        "types": types.most_common(5),
        "courses": courses.most_common(8),
    }


def render_archive(items: list) -> str:
    """내가 지금까지 한 과제를 과목별로 묶어 보여 준다.

    `/history`(내가 낸 답)와 층이 다르다 — 이쪽은 **과제 단위**다.
    쌓일수록 초안이 나에게 맞춰진다는 걸 눈으로 보여 주는 화면이기도 하다."""
    from .understanding.task_type import LABELS as _TYPE_LABELS
    head = ('<div class="sec" style="border:0">'
            f'<div class="lab"><span class="n">📚</span> / ARCHIVE · {len(items)} '
            '<span class="ln"></span></div>'
            '<h2>내 과제 아카이브</h2>')
    if not items:
        return head + ('<p class="meta">아직 쌓인 과제가 없어요. 과제를 하나 끝내면 '
                       '여기에 남고, 쌓일수록 초안이 내 방식에 맞춰집니다.</p>'
                       '<p><a class="btn ghost back" href="/">← Home</a></p></div>')
    st = _archive_stats(items)
    type_line = " · ".join(
        f"{html.escape(_TYPE_LABELS.get(k, k))} {v}" for k, v in st["types"]) or "—"
    summary = (
        '<div class="matbox"><b>지금까지</b>'
        f'<p class="meta" style="margin:.3rem 0">과제 <b>{st["total"]}</b>건 · '
        f'완성 {st["final"]}건 · 제출 표시 {st["submitted"]}건 · '
        f'내가 내린 결정 {st["decisions"]}개</p>'
        f'<p class="meta" style="margin:0">유형: {type_line}</p></div>')

    # 과목별로 묶는다 — 과목이 없는 건(붙여넣기 등) 맨 뒤 '기타'로.
    groups: dict = {}
    for it in items:
        groups.setdefault(it.get("course") or "", []).append(it)
    ordered = sorted(groups.items(), key=lambda kv: (kv[0] == "", kv[0]))

    blocks = []
    for course, rows in ordered:
        label = html.escape(course) if course else "과목 미지정"
        lines = []
        for it in rows:
            tok = html.escape(it["token"])
            if it.get("submitted"):
                href, state = f"/ready/{tok}", '<span class="pill ok">✓ 제출함</span>'
            elif it.get("final"):
                href, state = f"/vf/{tok}", '<span class="pill ok">완성본</span>'
            else:
                href = f"/v/{tok}"
                state = f'<span class="pill">결정 {int(it.get("n_dec") or 0)}</span>'
            tlabel = _TYPE_LABELS.get(it.get("task_type") or "", "")
            type_pill = (f'<span class="pill">{html.escape(tlabel)}</span> '
                         if tlabel and it.get("task_type") != "general" else "")
            due = html.escape(it.get("dday") or "")
            due_html = f'<span class="tag">{due}</span>' if due else ""
            lines.append(
                f'<div class="arow"><div><div class="t">'
                f'<a href="{href}">{html.escape(it["title"])}</a></div>'
                f'<div class="c">{html.escape(it["when"])} {due_html}</div></div>'
                f'<div>{type_pill}{state}</div></div>')
        blocks.append(f'<div class="matbox"><b>{label}</b>'
                      f'<div class="alist">{"".join(lines)}</div></div>')
    return head + summary + "".join(blocks) + (
        '<p class="meta" style="margin-top:1.2rem">여기 쌓인 결정은 다음 과제의 '
        '<a href="/history">답 제안</a>에 쓰입니다. 내 데이터만 모여 있고 '
        '다른 사람에게 보이지 않아요.</p>'
        '<p><a class="btn ghost back" href="/">← Home</a></p></div>')


def _beta_codes() -> list:
    """UNTIL_BETA_CODE — 쉼표로 복수 코드 지원(채널별 초대 코드: SNU-ETA 등).

    런칭 플랜의 '어느 채널이 실사용자를 데려오는지' 측정용. 코드 하나를 목록에서
    빼면 그 코드의 쿠키만 무효(해시가 코드별이라)."""
    import os as _os
    return [c.strip() for c in (_os.getenv("UNTIL_BETA_CODE") or "").split(",")
            if c.strip()]


def _beta_hashes(codes: list) -> set:
    import hashlib
    return {hashlib.sha256(c.encode("utf-8")).hexdigest()[:32] for c in codes}


def render_beta_gate(err: bool = False) -> str:
    """클로즈드 베타 초대 코드 입력 페이지(UNTIL_BETA_CODE 설정 시)."""
    msg = ('<p class="meta" style="color:var(--warn)">코드가 맞지 않아요. 다시 확인해 주세요.</p>'
           if err else
           '<p class="meta">Until은 지금 초대 베타예요. 받은 초대 코드를 입력하면 시작됩니다.</p>')
    return ('<div class="sec" style="border:0">'
            '<div class="lab"><span class="n">🔑</span> / BETA <span class="ln"></span></div>'
            '<h2>초대 코드</h2>' + msg +
            '<form method="post" action="/beta" style="margin-top:1rem">'
            '<input name="code" autofocus placeholder="초대 코드" '
            'style="max-width:16rem" autocomplete="off">'
            '<button class="btn" type="submit" style="margin-left:.5rem">입장</button>'
            '</form>'
            '<div class="matbox" style="margin-top:1.2rem">'
            '<p class="meta" style="margin:0">초대 코드가 없어도 기능이 어떻게 작동하는지 볼 수 있어요.</p>'
            '<p style="margin:.7rem 0 0"><a class="btn ghost" href="/about">'
            'Until 소개 보기 →</a></p></div></div>')


def render_login(next_path: str = "/", err: str = "") -> str:
    """설정된 계정 제공자를 모두 보여 주는 로그인 페이지."""
    from . import google_auth as _ga
    from . import kakao_auth as _ka
    nxt = html.escape(_ga.safe_next(next_path))
    head = ('<section class="auth-shell">'
            '<p class="auth-kicker">계정 연결</p>'
            '<h1>이어서 작업하기</h1>')
    if not _ga.any_enabled():
        return (head + '<p class="auth-lead">계정 로그인이 아직 켜져 있지 않습니다.</p>'
                '<p class="meta">관리자가 Kakao 또는 Google 로그인을 설정하면 '
                '다른 기기에서도 작업을 이어볼 수 있어요.</p>'
                '<p class="auth-back"><a href="/">홈으로 돌아가기</a></p>'
                '</section>')
    warn = (f'<p class="meta" style="color:var(--warn)">{html.escape(err)}</p>'
            if err else '')
    hd = _ga.allowed_domain() if _ga.enabled() else ""
    domain_note = (f'<p class="meta" style="margin:.4rem 0 0">{html.escape(hd)} 계정으로만 '
                   f'로그인할 수 있어요.</p>' if hd else '')
    buttons = []
    if _ka.enabled():
        buttons.append(
            f'<a class="auth-provider auth-kakao" aria-label="카카오 로그인" '
            f'href="/auth/kakao/start?next={nxt}">'
            '<img src="/asset/kakao-login.png" width="300" height="45" '
            'alt="카카오 로그인"></a>')
    if _ga.enabled():
        buttons.append(
            f'<a class="auth-provider auth-google" aria-label="Google로 계속하기" '
            f'href="/auth/google/start?next={nxt}">'
            '<img src="/asset/google-g.png" width="40" height="40" alt="" '
            'aria-hidden="true"><span>Google로 계속하기</span></a>')
    return (head +
            '<p class="auth-lead">지금 만든 초안과 과제 명세서를 계정에 저장합니다. '
            '다른 기기에서도 그대로 이어볼 수 있어요.</p>'
            + warn +
            '<div class="auth-actions">'
            + ''.join(buttons) + '</div>'
            + domain_note +
            '<p class="auth-privacy">로그인에는 계정 식별 정보만 사용합니다. '
            '카카오톡 대화, Gmail, Google Drive 권한은 요청하지 않습니다.</p>'
            '<p class="auth-back"><a href="/">지금은 로그인하지 않기</a></p>'
            '</section>')


def _account_html() -> str:
    """상단 바의 계정 슬롯 — 로그인 상태면 이름+로그아웃, 아니면 로그인 링크."""
    from . import google_auth as _ga
    if not (CLOUD and _ga.any_enabled()):
        return ""
    user = _auth_user()
    if user is None:
        return '<a class="acct" href="/login">로그인</a>'
    return ('<form class="acct-on" method="post" action="/logout">'
            f'<span class="who" title="{html.escape(user.email)}">'
            f'{html.escape(user.label)}</span>'
            '<button class="tg" type="submit">로그아웃</button></form>')


def render_consent_notice() -> str:
    """텔레메트리 opt-in 고지(클라우드·수집 활성 시 1회). 두 선택 모두 동등 —
    어느 쪽이든 모든 기능을 그대로 쓴다(다크패턴 금지)."""
    return ('<div class="utility-page utility-setting">'
            '<header class="page-head"><p class="page-kicker">데이터 설정</p>'
            '<h1>사용 통계를 선택해 주세요</h1>'
            '<p class="page-lead">동의하지 않아도 모든 기능을 똑같이 쓸 수 있습니다.</p></header>'
            '<section class="setting-status"><span>현재 상태</span><b>선택 전</b></section>'
            '<section class="utility-section"><h2>기록 범위</h2>'
            '<ul class="plain-list">'
            '<li><b>기록하는 것</b> — 과제 유형·결정 응답 수·준비 경고 해소·소요 시간·'
            'LLM 호출/토큰량 같은 숫자·분류값</li>'
            '<li><b>기록하지 않는 것</b> — 과제 원문·초안·결정 질문과 답변·첨부 파일. '
            '원문 조각이 섞이면 기록 자체를 차단하는 검사가 코드로 강제돼요.</li>'
            '<li><b>외부 분석</b> — 설정된 경우 공개 소개 화면의 방문(PageView)만 '
            'Google Analytics와 Meta Pixel에 보냅니다. 과제·세션 화면에서는 두 도구를 '
            '불러오지 않으며, Google·Meta가 쿠키 등 기기 정보를 처리할 수 있습니다.</li>'
            '<li><b>언제든 변경</b> — 홈의 데이터 설정에서 켜거나 끌 수 있습니다.</li>'
            '</ul></section>'
            '<form class="setting-actions" method="post" action="/consent">'
            '<button class="btn block" type="submit" name="choice" value="yes">동의하고 시작</button>'
            '<button class="btn ghost block" type="submit" name="choice" value="no">동의하지 않고 시작</button>'
            '</form></div>')


def render_consent_settings(current) -> str:
    """데이터 설정 페이지 — 현재 동의 상태 확인·변경(철회 포함)."""
    if current is True:
        state = '<span class="pill ok">수집 중</span>'
    elif current is False:
        state = '<span class="pill">수집 안 함</span>'
    else:
        state = '<span class="pill">선택 전</span>'
    return ('<div class="utility-page utility-setting">'
            '<header class="page-head"><p class="page-kicker">데이터 설정</p>'
            '<h1>어떤 정보도 과제 내용보다 앞서지 않습니다</h1>'
            '<p class="page-lead">현재 선택을 확인하고 언제든 바꿀 수 있습니다.</p></header>'
            f'<section class="setting-status"><span>비식별 사용 통계</span>{state}</section>'
            '<section class="utility-section"><h2>기록하는 것과 기록하지 않는 것</h2>'
            '<dl class="setting-facts"><div><dt>기록</dt><dd>과제 유형, 결정 응답 수, 경고 해소, '
            '소요 시간, LLM 토큰량 같은 집계 신호</dd></div>'
            '<div><dt>기록하지 않음</dt><dd>과제 원문, 초안, 답변, 첨부 파일. 원문 조각이 '
            '섞이면 기록 자체를 차단합니다.</dd></div>'
            '<div><dt>외부 분석</dt><dd>설정된 경우 Google Analytics와 Meta Pixel이 공개 소개 화면의 방문만 측정합니다. '
            '과제·세션 화면에서는 실행하지 않습니다.</dd></div></dl></section>'
            '<form class="setting-actions" method="post" action="/consent">'
            '<input type="hidden" name="back" value="settings">'
            '<button class="btn block" type="submit" name="choice" value="yes">사용 통계 켜기</button>'
            '<button class="btn ghost block" type="submit" name="choice" value="no">사용 통계 끄기</button>'
            '</form>'
            '<p class="page-back"><a href="/">← 홈으로</a></p></div>')


def render_history() -> str:
    """내 답 히스토리 페이지 — 무엇이 기억되는지 보기·전체 삭제(개인정보 통제)."""
    from .context.answer_history import load_history, answers_style_hint, history_path
    rows = load_history()
    where = ("내 계정에만" if CLOUD else "이 컴퓨터에만")
    head = ('<div class="sec" style="border:0">'
            f'<div class="lab"><span class="n">01</span> / 내 답 기록 · {len(rows)} <span class="ln"></span></div>'
            '<h2>내 답 히스토리</h2>'
            f'<p class="meta">결정에 답할 때마다 {where} 저장돼, 비슷한 결정이 다시 나오면 '
            "'지난 답'으로 재제안됩니다. AI 제안의 문체도 여기에 맞춰져요.</p>")
    if not rows:
        return head + ('<p class="meta">아직 기록이 없습니다. 결정에 답하면 쌓여요.</p>'
                       '<p><a class="btn ghost back" href="/">← Home</a></p></div>')
    from collections import Counter
    cats = Counter((r.get("category") or "고유 판단") for r in rows)
    stats = " · ".join(f"{k} {v}" for k, v in cats.most_common(4))
    style = answers_style_hint()
    style_html = (f'<p class="meta">{html.escape(style.lstrip("- "))}</p>' if style else "")
    items = []
    for r in rows[-15:][::-1]:  # 최근 15개, 최신부터
        note = r["note"][:44] + ("…" if len(r["note"]) > 44 else "")
        ans = r["answer"][:52] + ("…" if len(r["answer"]) > 52 else "")
        items.append(f'<li style="margin:.5rem 0"><span class="meta">{html.escape(note)}</span>'
                     f'<br>→ {html.escape(ans)}</li>')
    search = ('<input id="histq" placeholder="결정·답으로 걸러내기…" style="margin:.2rem 0 .6rem" '
              'oninput="var q=this.value.toLowerCase();'
              "document.querySelectorAll('#histlist>li').forEach(function(li){"
              "li.style.display=li.textContent.toLowerCase().indexOf(q)>=0?'':'none';});\">")
    return head + (
        f'<p class="meta">성격 분포: {html.escape(stats)}</p>{style_html}'
        f'{search}'
        f'<ul id="histlist" style="list-style:none;padding-left:0">{"".join(items)}</ul>'
        '<form method="post" action="/history/clear" style="margin:.8rem 0">'
        '<button class="btn ghost" type="submit">전체 기록 삭제</button></form>'
        + ("" if CLOUD else  # 서버 내부 경로는 사용자에게 무의미(노출 안 함)
           f'<p class="meta">파일 위치: {html.escape(str(history_path()))} (직접 지워도 됩니다)</p>')
        + '<p><a class="btn ghost back" href="/">← Home</a></p></div>')


def render_profile(saved: bool = False, courses_saved: bool = False) -> str:
    """내 프로필 페이지 — 기본정보 1회 저장 → 양식·초안에 자동 채움(되묻지 않음)."""
    from .profile import load_profile, FIELDS
    prof = load_profile()
    where = "내 계정에만" if CLOUD else "이 컴퓨터에만"
    note = ('<p class="meta" style="color:var(--ok,#2a7)">저장했어요. 다음 초안부터 자동으로 채워집니다.</p>'
            if saved else "")
    ph = {"name": "홍길동", "university": "서울대학교", "department": "자유전공학부",
          "student_id": "2020-12345", "phone": "010-1234-5678", "email": "me@snu.ac.kr"}
    inputs = []
    for key, disp, _aliases in FIELDS:
        v = html.escape(prof.get(key, ""))
        inputs.append(
            f'<label style="display:block;margin:.45rem 0">{disp}<br>'
            f'<input name="{key}" value="{v}" placeholder="{ph.get(key, "")}" '
            'style="max-width:22rem" autocomplete="off"></label>')
    lms_note = ("<p class=\"meta\">eTL을 연결하면 LMS가 이미 아는 값(이름·이메일)은 "
                "빈 칸에 자동으로 채워 둡니다 — 직접 저장한 값은 덮어쓰지 않아요.</p>")
    return ('<div class="sec" style="border:0">'
            '<div class="lab"><span class="n">👤</span> / PROFILE <span class="ln"></span></div>'
            '<h2>내 프로필</h2>' + note +
            f'<p class="meta">이름·학번·소속 같은 기본정보를 한 번만 저장하면, 보고서 양식의 '
            f'기본정보 칸을 되묻지 않고 자동으로 채웁니다. {where} 저장됩니다.</p>'
            '<form method="post" action="/profile" style="margin-top:.6rem">'
            + "".join(inputs) +
            '<button class="btn" type="submit" style="margin-top:.5rem">저장</button>'
            '</form>' + lms_note
            + _render_etl_panel()
            + _render_course_panel(courses_saved)
            + _render_tone_panel() + _render_data_panel()
            + '<p><a class="btn ghost back" href="/">← Home</a></p></div>')


def _render_tone_panel() -> str:
    """말투 레지스터 명시 지정 — 자동 추론과 분리된 **사용자 경로**의 화면.

    기능 플래그(UNTIL_TONE_REGISTER)가 꺼져 있으면 패널 자체를 그리지 않는다.
    꺼진 기능의 설정을 보여주면 "정했는데 왜 안 바뀌지"가 된다.
    """
    from .config import tone_register_active
    if not tone_register_active():
        return ""
    try:
        from .context.tone import (REGISTER_PRESETS, load_persona,
                                   render_tone_spec, resolve_tone_spec)
        store = load_persona()
    except Exception:
        return ""
    labels = {
        "academic_prose": "학술 산문 (에세이·논술) — 한다체 문어체",
        "lab_report": "실험·실습 보고서 — 한다체, 더 건조하게",
        "reflective": "소감·참가 보고서 — 하십시오체, 온기 있게",
        "inquiry_to_professor": "교수님께 드릴 질의 — 겸양·완충어 많이",
        "form_admin": "행정 양식 칸 — 짧고 사실 위주",
        "team_coordination": "팀 공유 문서 — 해요체, 동료에게",
        "presentation_script": "발표 대본 — 짧은 문장, 청중에게",
        "technical_neutral": "코드·문제 풀이 — 수신자 없음",
    }
    options = ['<option value="">과제에 맞춰 자동으로 (권장)</option>']
    for key in REGISTER_PRESETS:
        sel = " selected" if store.pinned_register == key else ""
        options.append(f'<option value="{key}"{sel}>{html.escape(labels.get(key, key))}'
                       '</option>')
    current = store.pinned_register or "academic_prose"
    try:
        preview = render_tone_spec(resolve_tone_spec(
            current, override=store.registers.get(current)))
    except Exception:
        preview = ""
    pinned_note = (
        '<p class="meta">지금은 <b>항상 이 말투</b>로 씁니다 — 과제 종류와 무관합니다.</p>'
        if store.pinned_register else
        '<p class="meta">지금은 과제 종류·수신자를 보고 <b>자동으로</b> 고릅니다. '
        '자동 추론이 마음에 안 들 때만 아래에서 못박으세요.</p>')
    return (
        '<div class="sec" style="margin-top:1.2rem">'
        '<h3 style="margin-bottom:.2rem">말투</h3>' + pinned_note +
        '<form method="post" action="/profile/tone">'
        '<label style="display:block;margin:.45rem 0">기본 말투<br>'
        '<select name="register" style="max-width:26rem">' + "".join(options) +
        '</select></label>'
        '<button class="btn" type="submit" style="margin-top:.4rem">말투 저장</button>'
        '</form>'
        '<details style="margin-top:.5rem"><summary class="meta">'
        '지금 적용되는 말투 규격 보기</summary>'
        f'<pre style="white-space:pre-wrap;font-size:.85em">{html.escape(preview)}</pre>'
        '</details></div>')


#: 과목 프로파일 입력 줄 수 — 저장된 것 + 빈 줄 몇 개. 한 학기 수강이 보통
#: 5~7과목이라 처음 열었을 때 대부분이 한 화면에서 끝난다.
_COURSE_ROWS_BLANK = 3
_COURSE_ROWS_MIN = 6


def course_rows_from_form(form: dict) -> list:
    """`aliasN`/`hintN` 폼 → save_course_profiles가 받는 리스트.

    두 서버(stdlib·ASGI)가 같은 파싱을 써야 한다 — 한쪽만 고치면 운영에서만
    설정이 안 먹는다. 폼 값은 `{키: [값]}`(stdlib parse_qs)와 `{키: 값}`(ASGI
    FormData) 둘 다 들어오므로 여기서 흡수한다.
    """
    def _one(key: str) -> str:
        v = form.get(key)
        if isinstance(v, (list, tuple)):
            v = v[0] if v else ""
        return str(v or "").strip()

    rows = []
    for i in range(_COURSE_ROWS_MIN + _COURSE_ROWS_BLANK + 24):
        alias = _one(f"alias{i}")
        hint = _one(f"hint{i}")
        # 둘 다 비면 안 쓴 줄이다. 한쪽만 있으면 저장 쪽 검증이 버린다
        # (유형 없는 과목은 §3에서 하는 일이 없고, 이름 없는 유형은 조회 불가).
        if alias or hint:
            rows.append({"alias": alias, "route_hint": hint})
    return rows


def _render_course_panel(saved: bool = False) -> str:
    """과목 프로파일(§3 route_hint 폴백)을 사용자가 학기 초 1회 적는 화면.

    이 폴백은 설계·구현·시험이 다 있는데도 라이브에서 성립한 적이 없었다 — 저장
    경로가 서버 전역 파일 하나였고(사용자별이 아님), 값을 적을 화면도 없었다.
    여기가 그 화면이다.

    적용 조건을 화면에 그대로 밝힌다. "정했는데 왜 안 바뀌지"를 막는 유일한 방법은
    **언제 안 쓰이는지**를 먼저 말하는 것이다 — 이 힌트는 과제 본문에서 유형이
    읽히면 지고, 퀴즈·시험 같은 비작성 과제는 뒤집지 못한다(§3 (a)(b)).
    """
    from .config import algo_version
    from .context.course_profiles import ROUTE_HINT_LABELS, load_course_profiles
    saved_rows = load_course_profiles()
    rows = list(saved_rows) + [{}] * _COURSE_ROWS_BLANK
    if len(rows) < _COURSE_ROWS_MIN:
        rows += [{}] * (_COURSE_ROWS_MIN - len(rows))

    # 과목명 자동완성 — 이미 해 본 과제에서 과목명을 안다. 사용자가 eTL 표기를
    # 외워서 다시 타이핑할 이유가 없다(별칭은 부분일치라 축약형도 통한다).
    known = []
    for s in list_sessions(limit=30):
        c = (s.get("course") or "").strip()
        if c and c not in known:
            known.append(c)
    datalist = ""
    if known:
        opts = "".join(f'<option value="{html.escape(c)}">' for c in known[:20])
        datalist = f'<datalist id="known-courses">{opts}</datalist>'
    list_attr = ' list="known-courses"' if known else ""

    inputs = []
    for i, row in enumerate(rows):
        alias = html.escape(str(row.get("alias") or ""))
        hint = str(row.get("route_hint") or "")
        opts = ['<option value="">— 지정 안 함 —</option>']
        for key, label in ROUTE_HINT_LABELS:
            sel = " selected" if hint == key else ""
            opts.append(f'<option value="{key}"{sel}>{html.escape(label)}</option>')
        inputs.append(
            '<div class="course-row" style="display:flex;gap:.4rem;flex-wrap:wrap;'
            'margin:.35rem 0">'
            f'<input name="alias{i}" value="{alias}" placeholder="과목명 또는 줄임말"'
            f'{list_attr} style="flex:1 1 12rem;min-width:0" autocomplete="off">'
            f'<select name="hint{i}" style="flex:1 1 18rem;min-width:0">'
            + "".join(opts) + '</select></div>')

    note = ('<p class="meta" style="color:var(--ok,#2a7)">저장했어요. '
            '다음 과제부터 적용됩니다.</p>' if saved else "")
    # v0.1에서는 이 힌트를 파이프라인이 아예 보지 않는다. 안 쓰이는 설정을
    # 말없이 받아 두면 "적었는데 왜 안 먹지"가 된다.
    off = ("" if algo_version() == "v0.2" else
           '<p class="meta">⚠ 지금 이 배포는 <code>algo_version=v0.1</code>이라 '
           '적어 두어도 아직 적용되지 않습니다. 적어 두면 v0.2로 켜는 순간 '
           '그대로 쓰입니다.</p>')
    return (
        '<div class="sec" style="margin-top:1.2rem">'
        '<h3 style="margin-bottom:.2rem">과목 유형</h3>'
        '<p class="meta">과목명이 줄임말이고 과제 본문이 짧으면 어떤 종류의 과제인지 '
        '읽어 낼 단서가 없습니다. 학기 초에 한 번만 적어 두면 그럴 때 이걸 씁니다.</p>'
        + note + off +
        '<form method="post" action="/profile/courses">' + datalist
        + "".join(inputs) +
        '<button class="btn" type="submit" style="margin-top:.5rem">과목 저장</button>'
        '</form>'
        '<p class="meta" style="margin-top:.5rem">과제 본문에서 종류가 읽히면 '
        '<b>본문이 이깁니다</b> — 여기 적은 것과 달라도 실제 명세를 따릅니다. '
        '퀴즈·시험처럼 초안을 만들지 않는 과제도 이 설정으로 바뀌지 않습니다.</p>'
        '</div>')


def _render_etl_panel() -> str:
    """보관된 eTL 연결 — 켜져 있다는 사실을 **계속 밝히고**, 끄는 길을 같은 자리에.

    남의 LMS 계정을 여는 열쇠를 맡아 두는 기능이다. 어딘가 깊은 설정에 숨겨 두면
    사용자는 자기가 무엇을 맡겼는지 잊는다.
    """
    if _remembered_token_path() is None:
        return ""            # 미로그인·로컬 — 보관 자체가 없는 경로
    on = bool(_remembered_token())
    if on:
        state = ('<p class="meta" style="color:var(--ok,#2a7)">이 계정에 eTL 연결이 '
                 '<b>암호화되어 보관 중</b>입니다 — 과제를 가져올 때 토큰을 다시 '
                 '묻지 않아요.</p>'
                 '<form method="post" action="/profile/etl-forget">'
                 '<button class="btn ghost" type="submit">보관 해제하고 지우기</button>'
                 '</form>')
    else:
        state = ('<p class="meta">지금은 보관하지 않습니다 — 과제를 가져올 때마다 '
                 'eTL 토큰을 넣어야 해요. eTL 연결 화면에서 '
                 '<b>다음에도 자동으로 연결</b>을 켜면 보관합니다.</p>')
    return ('<div class="sec" style="margin-top:1.2rem">'
            '<h3 style="margin-bottom:.2rem">eTL 연결</h3>' + state +
            '<p class="meta" style="margin-top:.5rem">보관된 토큰은 이 서버의 키로 '
            '암호화해 저장하며, 저장본만으로는 열 수 없습니다. eTL 설정에서 토큰을 '
            '무효화하면 보관본도 즉시 쓸모없어집니다.</p></div>')


def _render_data_panel() -> str:
    """내 데이터 — 무엇이 저장돼 있는지 보여주고, 통째로 내보내거나 지우게 한다.

    삭제는 되돌릴 수 없으므로 확인 문구를 직접 입력하게 한다(버튼 하나로 지워지면
    실수로 지운 사용자에게 돌려줄 것이 없다).
    """
    from .persona.retention import RETENTION_DAYS, USER_DATA_FILES
    uid = _uid()
    root = _user_root(uid) if uid else _Path("_until_work")
    rows = []
    for name, desc in USER_DATA_FILES:
        target = root / name
        if not target.exists():
            continue
        keep = RETENTION_DAYS.get(name, 0)
        policy = f"{keep}일 뒤 자동 삭제" if keep else "자동 삭제 없음"
        try:
            size = target.stat().st_size
        except OSError:
            size = 0
        rows.append(f'<li>{html.escape(desc)} <span class="meta">· {policy} '
                    f'· {size:,}B</span></li>')
    listing = ("<ul style=\"margin:.3rem 0 .6rem 1rem\">" + "".join(rows) + "</ul>"
               if rows else '<p class="meta">저장된 개인 데이터가 없습니다.</p>')
    return (
        '<div class="sec" style="margin-top:1.2rem">'
        '<h3 style="margin-bottom:.2rem">내 데이터</h3>'
        '<p class="meta">여기에 저장된 것 전부입니다. 언제든 통째로 가져가거나 '
        '지울 수 있어요.</p>' + listing +
        '<p><a class="btn ghost" href="/data/export.json" download>페르소나 내보내기 '
        '(.json)</a> <span class="meta">문체·사실만 — 과제 원문은 빠집니다</span></p>'
        '<form method="post" action="/data/delete" style="margin-top:.6rem">'
        '<label style="display:block">전부 삭제하려면 <b>삭제</b>라고 입력하세요<br>'
        '<input name="confirm" placeholder="삭제" style="max-width:12rem" '
        'autocomplete="off"></label>'
        '<button class="btn ghost" type="submit" style="margin-top:.4rem;'
        'color:var(--warn,#c33)">내 데이터 전부 삭제</button>'
        '</form>'
        '<p class="meta">삭제하면 저장본·클라우드 미러까지 함께 지워집니다. '
        '되돌릴 수 없어요.</p></div>')


def render_plan(full=False, backend: str = "mock", err: str = "",
                msg: str = "") -> str:
    """플랜·크레딧·충전 페이지 — 선불 충전 크레딧 모델(billing.py)."""
    from . import billing
    p = billing.plan()
    pay = billing.pay_url()
    cost = billing.credit_cost()
    if p == "pro":
        status = '<span class="pill ok">PRO</span> <span class="meta">무제한 초안</span>'
    else:
        bal = billing.balance()
        cls = "ok" if bal >= cost else ""
        status = (f'<span class="pill {cls}">잔액 {bal} 크레딧</span> '
                  f'<span class="meta">과제 1건 = {cost}크레딧 · 재시도(수정·완성)는 무료</span>')
    # full="limit" = 전역 일일 상한(운영자만 풀 수 있다), 그 외 참값 = 내 잔액 부족.
    # 둘을 같은 문구로 뭉뚱그리면 사용자가 충전하고도 못 쓴다.
    if str(full) == "limit":
        warn = ('<p class="page-alert">'
                '오늘 전체 사용량이 한도에 닿았어요 — <b>충전으로는 풀리지 않습니다.</b> '
                '내일 다시 시도하거나 운영자에게 문의해 주세요.</p>')
    elif full:
        warn = ('<p class="page-alert">크레딧이 부족해요 — '
                '아래에서 충전하거나 Pro로 올리면 이어서 만들 수 있어요.</p>')
    else:
        warn = ""
    if err == "redeem":
        warn += ('<p class="page-alert">'
                 '충전 코드를 확인해 주세요(유효하지 않거나 이미 사용한 코드).</p>')
    elif err:
        warn += ('<p class="page-alert">'
                 + ('플랜 업그레이드는 결제 링크로만 진행돼요 — 키 입력은 로컬 버전 전용입니다.'
                    if CLOUD else
                    '키 활성화에 실패했어요 — 결제 후 받은 키(8자 이상)를 그대로 붙여넣어 주세요.')
                 + '</p>')
    if msg:
        warn += (f'<p class="page-alert ok">{html.escape(msg)}</p>')
    mock_note = ('<p class="meta">지금은 <b>mock 데모 백엔드</b>라 크레딧 차감 '
                 '없이 체험할 수 있어요. 크레딧은 라이브 백엔드(Groq 등)에서만 차감됩니다.</p>'
                 if backend == "mock" else "")
    if pay:
        paybox = (f'<p><a class="btn block" href="{html.escape(pay)}" '
                  'target="_blank" rel="noopener">크레딧 충전하기</a>'
                  '<br><span class="meta">결제 후 받은 충전 코드를 아래에 입력해 주세요.</span></p>')
    else:
        paybox = ('<p class="meta">결제 링크가 아직 설정되지 않았어요 — 운영자가 '
                  '<code>UNTIL_PAY_URL</code>(스트라이프/토스 결제 링크)을 지정하면 여기에 버튼이 생깁니다.</p>')
    # 충전 코드 입력 폼(크레딧 모델의 핵심) — 결제 후 받은 코드를 잔액으로.
    redeem_form = ("" if p == "pro" else """
 <form method="post" action="/plan/redeem" style="margin-top:1.2rem">
  <label>충전 코드 <span class="hint">결제 후 받은 코드</span></label>
  <input name="code" placeholder="UNTIL-CREDIT-XXXX" autocomplete="off">
  <p><button class="btn block" type="submit">크레딧 충전</button></p>
 </form>""")
    # 클라우드: 라이선스 파일은 서버 공유라 키 활성화 폼은 숨긴다(무제한 패스는 운영자용).
    license_form = ("" if CLOUD else """
 <form method="post" action="/plan/activate" style="margin-top:.6rem">
  <label>무제한 라이선스 키 <span class="hint">기관/무제한 패스</span></label>
  <input name="license" placeholder="UNTIL-XXXX-XXXX-XXXX">
  <p><button class="btn ghost block" type="submit">키 활성화</button></p>
 </form>""")
    return f"""
<div class="utility-page utility-plan">
 <header class="page-head"><p class="page-kicker">플랜</p>
  <h1>플랜·데이터 설정</h1>
  <p class="page-lead">초안을 처음 만들 때만 차감되고, 같은 과제의 수정과 완성은 무료입니다.</p>
 </header>
 <nav class="settings-subnav" aria-label="설정 항목"><b>플랜</b>
  <a href="/consent">데이터 설정</a></nav>
 <section class="setting-status plan-status"><span>현재 플랜</span><div>{status}</div></section>
 {warn}{mock_note}
 <section class="utility-section plan-purchase"><h2>충전하기</h2>{paybox}{redeem_form}{license_form}</section>
 {_ledger_html()}
 <p class="page-back"><a href="/">← 홈으로</a></p>
</div>"""


# 원장에 적히는 사유 → 사람이 읽는 말. 자유 문자열을 그대로 뿌리지 않는다.
_LEDGER_WHY = {
    "draft": "과제 1건 생성",
    "redeem": "충전 코드",
    "refund": "환불 회수",
    "chargeback": "지불 취소 회수",
    "purchase": "결제 충전",
    "starter": "가입 지급",
    "grant": "지급",
}


def _ledger_html(limit: int = 8) -> str:
    """최근 크레딧 내역 — 잔액만 보이면 환불이 났을 때 이유를 알 수 없다.

    표시용이라 실패는 조용히 넘긴다(원장이 없다고 플랜 화면이 죽으면 안 된다).
    회수 부족분(shortfall)은 사용자에게도 그대로 보여 준다 — 잔액이 환불액만큼
    줄지 않은 이유가 그것이기 때문이다."""
    try:
        from . import billing
        rows = billing.ledger(limit=limit)
    except Exception:
        return ""
    if not rows:
        return ""
    items = []
    for row in rows:
        try:
            delta = int(row.get("d") or 0)
        except (TypeError, ValueError):
            delta = 0
        when = str(row.get("t") or "")[:16].replace("T", " ")
        why = _LEDGER_WHY.get(str(row.get("why") or ""), str(row.get("why") or "기타"))
        sign = "+" if delta > 0 else ""
        cls = "ok" if delta > 0 else ""
        short = ""
        try:
            missing = int(row.get("shortfall") or 0)
        except (TypeError, ValueError):
            missing = 0
        if missing:
            short = (f' <span class="tag" style="color:var(--warn)">'
                     f'{missing} 회수 못 함(이미 사용)</span>')
        items.append(
            f'<div class="arow"><div><div class="t">{html.escape(why)}'
            f'<span class="pill {cls}" style="margin-left:.5rem">{sign}{delta}</span>'
            f'{short}</div>'
            f'<div class="c">{html.escape(when)}</div></div></div>')
    return ('<div class="matbox" style="margin-top:1.4rem"><b>최근 내역</b>'
            '<p class="meta" style="margin:.25rem 0">충전·사용·환불이 시간순으로 남습니다.</p>'
            f'<div class="alist">{"".join(items)}</div></div>')


def _inbox_announcements_html(anns: list) -> str:
    """인박스 상단 '📢 최신 공지' 섹션(WS 모드에서 수집됐을 때만)."""
    if not anns:
        return ""
    rows = []
    for a in anns:
        date = (getattr(a, "created_iso", "") or "")[:10]
        date_html = f' <span class="tag">{html.escape(date)}</span>' if date else ""
        course = html.escape(getattr(a, "course_name", "") or "")
        link = getattr(a, "url", "") or ""
        subj = html.escape(getattr(a, "subject", "") or "(제목 없음)")
        subj_html = (f'<a href="{html.escape(link)}" target="_blank" rel="noopener">{subj}</a>'
                     if link else subj)
        rows.append(f'<div class="arow"><div><div class="t">{subj_html}</div>'
                    f'<div class="c">{course}{date_html}</div></div></div>')
    return ('<div class="matbox" style="margin-bottom:1rem">'
            '<b>최신 공지</b>'
            '<p class="meta" style="margin:.25rem 0">eTL 공지사항에서 자동으로 가져왔어요.</p>'
            f'<div class="alist">{"".join(rows)}</div></div>')


_PLANNER_ICON = {"quiz": "📝", "discussion_topic": "💬",
                 "calendar_event": "📅", "wiki_page": "📄"}


def _inbox_extras_html(extras: list | None) -> str:
    """플래너 '그 외 마감'(퀴즈·토론·이벤트) 접이식 목록 — 과제 목록의 보조 정보.

    초안 버튼은 없다 — until이 대신 할 수 없는 종류의 할 일이라 eTL 링크만 건다."""
    if not extras:
        return ""
    rows = []
    for e in extras[:8]:
        icon = _PLANNER_ICON.get(e.get("type", ""), "📌")
        due = (e.get("due_at") or "")[:16].replace("T", " ")
        due_html = f' <span class="tag">{html.escape(due)}</span>' if due else ""
        title = html.escape(e.get("title", ""))
        if e.get("url"):
            title = (f'<a href="{html.escape(e["url"])}" target="_blank" '
                     f'rel="noopener">{title}</a>')
        course = html.escape(e.get("course", ""))
        rows.append(f'<li style="margin:.3rem 0">{icon} {title} '
                    f'<span class="meta">{course}</span>{due_html}</li>')
    return ('<details class="tgsec" style="margin:.6rem 0">'
            f'<summary>📅 그 외 마감 {len(extras)}건 (퀴즈·토론·이벤트)</summary>'
            f'<ul style="list-style:none;padding-left:.2rem;margin:.4rem 0">'
            f'{"".join(rows)}</ul></details>')


def render_inbox(items: list, sid: str = "", note: str = "", simple: bool = False,
                 announcements: list | None = None,
                 extras: list | None = None) -> str:
    """탐색된 과제 목록 → 각 과제에 '이 과제로 초안 만들기' 버튼. (토큰은 sid로만 전달)

    필터 UI는 홈의 체크박스 2개(미제출만·기한 지난 숨기기)가 전부 — 인박스 자체
    필터 바는 선택지 과잉이라 제거(사용자 피드백). 정렬은 마감 임박순 고정.
    """
    note_html = f'<p class="meta" style="color:var(--warn)">{html.escape(note)}</p>' if note else ""
    head = ('<div class="sec" style="border:0">'
            f'<div class="lab"><span class="n">01</span> / INBOX · {len(items)} <span class="ln"></span></div>'
            '<h2>내 과제</h2>'
            '<p class="meta">마감 임박순. 과제를 고르면 본문+관련자료를 모아 초안을 만듭니다.</p>'
            f'{note_html}'
            f'{_inbox_announcements_html(announcements)}'
            f'{_inbox_extras_html(extras)}')
    if not items:
        return head + '<p class="meta">표시할 과제가 없습니다(필터를 풀거나 토큰을 확인하세요).</p>' \
                      '<p><a class="btn ghost back" href="/">← Home</a></p></div>'
    ui_hidden = '<input type="hidden" name="ui" value="simple">' if simple else ''
    rows = ['<div class="alist">']
    for a in items:
        due = (a.due_at or "")[:16].replace("T", " ")
        dd, urgent = _dday_label(a.due_at)
        dd_html = ""
        if dd:
            style = ' style="color:var(--warn);border-color:var(--warn)"' if urgent else ""
            dd_html = f' <span class="tag"{style}>{html.escape(dd)}</span>'
        due_tag = (f'<span class="tag">{html.escape(due)}</span>{dd_html}' if a.due_at
                   else '<span class="tag">NO DUE</span>')
        sub = ('<span class="tag">SUBMITTED</span>' if a.submitted
               else '<span class="tag todo">TODO</span>')
        rows.append(
            '<div class="arow"><div>'
            f'<div class="t">{html.escape(a.title)}</div>'
            f'<div class="c">{html.escape(a.course_name)} {due_tag} {sub}</div>'
            '</div>'
            '<form method="post" action="/pick">'
            f'<input type="hidden" name="url" value="{html.escape(a.url)}">'
            f'<input type="hidden" name="sid" value="{html.escape(sid)}">'
            f'{ui_hidden}'
            '<button class="btn" type="submit">초안 →</button>'
            '</form></div>'
        )
    rows.append('</div><p><a class="btn ghost back" href="/">← Home</a></p></div>')
    return head + "\n".join(rows)


def _doc_tools(text: str, *, doc_id: str, filename: str, extra: str = "",
               minimal: bool = False, telemetry_token: str = "") -> str:
    """문서 텍스트 복사/다운로드 도구 — 원문을 hidden textarea에 담고 버튼 제공.

    복사가 1순위 행동(일반 사용자는 .md를 모른다 — 복사해서 쓰던 곳에 붙여넣는 게
    가장 자연스러운 다음 단계). minimal=True(간단 모드)면 .md 버튼을 숨긴다."""
    md_btn = ("" if minimal else
              f'<button type="button" class="btn ghost" '
              f'onclick="downloadDoc(\'{doc_id}\',\'{filename}\')">Download .md</button>')
    token_arg = f",'{html.escape(telemetry_token)}'" if telemetry_token else ""
    return (
        f'<textarea id="{doc_id}" hidden>{html.escape(text)}</textarea>'
        '<div class="row" style="margin:.7rem 0 .2rem">'
        f'<button type="button" class="btn" '
        f'onclick="copyDoc(\'{doc_id}\',this{token_arg})">전체 복사</button>'
        f'{extra}'
        f'{md_btn}'
        '</div>'
    )


def _prompt_button(result: Result) -> str:
    """'프롬프트로 복사' — 채팅 LLM(ChatGPT 등)에 그대로 붙여넣는 자기완결 번들.

    링크·자료명만으로는 채팅 LLM이 아무것도 못 연다 — 자료 실제 발췌까지 담는다."""
    from .promptpack import render_prompt_bundle
    try:
        txt = render_prompt_bundle(result)
    except Exception:  # 프롬프트 번들 실패가 페이지를 막지 않게
        return ""
    return (f'<textarea id="promptsrc" hidden>{html.escape(txt)}</textarea>'
            '<button type="button" class="btn ghost" '
            'onclick="copyDoc(\'promptsrc\',this)" '
            'title="과제·자료 발췌·본문·남은 결정을 전부 담은 프롬프트 — 쓰는 AI 채팅에 붙여넣기">'
            '프롬프트로 복사</button>')


def _report_button(result: Result) -> str:
    """전체 리포트(.md: 초안+결정+자료+제안) 다운로드 버튼 — 본문 hidden에 담는다."""
    from .report import render_markdown_report
    rep = render_markdown_report(result)
    return ('<textarea id="reportsrc" hidden>' + html.escape(rep) + '</textarea>'
            '<button type="button" class="btn ghost" '
            'onclick="downloadDoc(\'reportsrc\',\'until-report.md\')">Full report .md</button>')


def _readiness_html(result: Result) -> str:
    """제출 준비 점검 패널 — 마감·분량·인용·결정을 한 상자에. 경고는 강조색."""
    from .readiness import assess_readiness
    rd = assess_readiness(result)
    if not rd.items:
        return ""
    icon = {"ok": "완료", "warn": "확인", "fail": "차단", "info": "안내"}
    rows = []
    for it in rd.items:
        color = "var(--warn)" if it.status in ("warn", "fail") else "var(--ink)"
        rows.append(f'<li style="color:{color}">{icon.get(it.status, "•")} '
                    f'<b>{html.escape(it.label)}</b> · {html.escape(it.message)}</li>')
    return (f'<div class="matbox"><b>제출 준비 점검 — {html.escape(rd.headline)}</b>'
            f'<ul style="margin:.4rem 0 .1rem;list-style:none;padding-left:0">{"".join(rows)}</ul></div>')


def _outcome_summary_html(result: Result, answered=None) -> str:
    """Show four verifiable completion signals on draft and final views."""
    from .readiness import assess_readiness

    spec = getattr(result, "spec", None) or {}
    requirements = [item for item in (spec.get("requirements") or []) if str(item).strip()]
    sources = list(getattr(result, "source_docs", None) or [])
    total = len(getattr(result.draft, "decisions", None) or [])
    done = min(len(answered or set()), total)
    warnings = len(assess_readiness(result).warnings)
    cards = (
        ("요구사항", f"{len(requirements)}개 분석"),
        ("근거 자료", f"{len(sources)}개 연결"),
        ("내 결정", f"{done}/{total} 완료"),
        ("현재 경고", f"{warnings}개"),
    )
    items = "".join(
        f'<div style="min-width:7rem;flex:1"><span class="meta">{label}</span>'
        f'<br><b>{value}</b></div>' for label, value in cards
    )
    return (f'<div class="matbox" aria-label="과제 완성 현황">'
            f'<b>한눈에 보는 완성 현황</b>'
            f'<div style="display:flex;gap:.8rem;flex-wrap:wrap;margin-top:.55rem">{items}</div>'
            f'</div>')


def _requirement_trace_html(result: Result) -> str:
    """Visible requirement→evidence→draft status table; all values are escaped."""
    from .requirement_trace import trace_requirements

    rows = trace_requirements(result)
    if not rows:
        return ""
    labels = {"reflected": ("반영됨", "ok"), "partial": ("부분 반영", ""),
              "missing": ("미반영", "warn"), "decision": ("내 판단 필요", "warn")}
    body = []
    for row in rows:
        label, cls = labels[row.status]
        evidence = " · ".join(row.evidence_titles[:2]) or "연결 근거 없음"
        units = f" · 적용: {', '.join(row.unit_titles[:2])}" if row.unit_titles else ""
        draft_href = f'#draft-p{row.paragraph_index}' if row.paragraph_index else '#draft-body'
        body.append(
            '<li style="margin:.55rem 0">'
            f'<span class="pill {cls}">{label}</span> <b>{html.escape(row.label)}</b><br>'
            f'<span class="meta">근거: {html.escape(evidence + units)}</span> '
            f'<a class="meta" href="{draft_href}">관련 문단 보기</a> · '
            '<a class="meta" href="#source-control">근거 보기</a></li>')
    return ('<details class="tgsec"><summary><span class="tglab">＊ Trace</span>'
            '<h2>요구사항–근거–초안 연결</h2></summary><div class="tgbody">'
            '<p class="meta">각 요구사항이 어떤 근거와 초안 작업으로 이어졌는지 보여줘요.</p>'
            f'<ul style="list-style:none;padding:0">{"".join(body)}</ul></div></details>')


def _plan_html(result: Result) -> str:
    """체크포인트 플랜 패널 — 볼륨 과제(마감 여유·큰 분량)에만 뜬다(결정적·LLM 0).

    각 체크포인트 = until이 준비해주는 것 + 학생이 정할 것 + 통과 조건.
    팀 피드백(2026-07-24) '볼륨 있는 과제에 체크포인트' 반영."""
    from .plan import plan_for_result
    return _plan_panel(plan_for_result(result))


def _plan_panel(plan) -> str:
    """CheckpointPlan → 패널 HTML(플랜이 None이면 "")."""
    if plan is None:
        return ""
    rows = []
    for c in plan.checkpoints:
        rows.append(
            f'<li style="margin:.35rem 0"><b>CP{c.no} · {html.escape(c.date_label())} — '
            f'{html.escape(c.title)}</b><br>'
            f'<span class="meta">until: {html.escape(c.until_does)}</span><br>'
            f'<span class="meta">나: {html.escape(c.you_do)} · 통과: {html.escape(c.done_when)}</span></li>')
    return ('<details class="tgsec" open><summary><span class="tglab">🗓 Plan</span>'
            f'<h2>체크포인트 플랜 <span class="meta">· {html.escape(plan.basis)}</span></h2></summary>'
            '<div class="tgbody"><p class="meta">볼륨 있는 과제라 단계로 나눴어요. '
            '각 날짜까지 통과 조건만 맞추면 마감에 몰리지 않습니다.</p>'
            f'<ul style="margin:.2rem 0 .1rem;list-style:none;padding-left:0">{"".join(rows)}</ul>'
            '</div></details>')


def _trim(s: str, n: int = 220) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _diff_html(result: Result) -> str:
    """초안→최종본 변경 요약 패널 — finalize가 결정을 어떻게 녹였는지 투명하게."""
    fd = result.final_draft
    if fd is None:
        return ""
    from .diffview import diff_drafts, summarize_changes
    changes = diff_drafts(result.draft.body, fd.body)
    if not changes:
        return ""
    rows = []
    for c in changes[:12]:  # 표시는 12개까지(과다 방지)
        if c.kind == "changed":
            rows.append(f'<li><b>수정</b> · <span class="muted">{html.escape(_trim(c.before))}</span>'
                        f'<br>→ {html.escape(_trim(c.after))}</li>')
        elif c.kind == "added":
            rows.append(f'<li><b>추가</b> · {html.escape(_trim(c.after))}</li>')
        else:
            rows.append(f'<li><b>삭제</b> · <span class="muted">{html.escape(_trim(c.before))}</span></li>')
    more = (f'<p class="meta">…외 {len(changes) - 12}곳</p>' if len(changes) > 12 else "")
    return ('<details class="tgsec"><summary><span class="tglab">＊ Changes</span>'
            f'<h2>초안에서 달라진 부분 <span class="meta">· {html.escape(summarize_changes(changes))}</span></h2></summary>'
            '<div class="tgbody"><p class="meta">당신의 결정이 본문에 어떻게 반영됐는지 확인하세요. '
            '이상하면 아래에서 답을 고쳐 다시 만들 수 있어요.</p>'
            f'<ul style="margin:.2rem 0 .1rem">{"".join(rows)}</ul>{more}</div></details>')


def _submission_links(session_id: str, result=None) -> str:
    """제출용 문서(본문+결정 체크리스트만) 다운로드 링크 — .md / 인쇄용 .html."""
    if not session_id:
        return ""
    if result is not None and getattr(result, "practice_mode", False):
        return ""
    tok = html.escape(session_id)
    # 양식 첨부(hwpx/docx/hwp)가 있으면 '채워진 양식'이 1순위 — 셀 단위 복붙 제거.
    # .hwp(이진)는 셀 주입이 불가능해(C안) 값 표 .docx로 대체되므로 라벨·안내가 다르다.
    form_btn = ""
    if result is not None:
        try:
            from pathlib import Path as _P
            from .capture.formfill import find_form_document
            src = find_form_document(result)
            if src:
                is_hwp = _P(src).suffix.lower() == ".hwp"
                label = ("채운 값 표 (.hwp → .docx)" if is_hwp
                         else "채워진 양식 (원본 서식)")
                try:  # 무엇이 채워지는지 수치로(표 N칸 · 서술 M항목) — 드라이런.
                    import tempfile
                    from .report import write_filled_form
                    with tempfile.TemporaryDirectory() as d:
                        got = write_filled_form(result, _P(d) / "probe.bin")
                    if got:
                        prefix = "채운 값 표" if is_hwp else "채워진 양식"
                        label = f"{prefix} · {html.escape(got[1].describe())}"
                except Exception:
                    pass
                hint = ("<p class=\"meta\">.hwp 양식은 셀에 바로 채우지 못해 대신 "
                       "채운 값 .docx 표로 드려요 — 한글에서 열어 원본 .hwp 양식에 "
                       "붙여넣고 .hwpx로 저장해 다시 올리면 다음부터는 원본 서식 그대로 "
                       "채워 드립니다.</p>") if is_hwp else ""
                form_btn = (f'<a class="btn ghost" href="/dl/{tok}.form" download>'
                            f'{label}</a>{hint}')
        except Exception:
            form_btn = ""
    pptx_btn = ""
    if result is not None and (getattr(result, "spec", None) or {}).get("task_type") == "presentation":
        pptx_btn = (f'<a class="btn ghost" href="/dl/{tok}.pptx" download>'
                    '발표자료 (.pptx)</a>')
    # 발표는 PPTX, 문서는 워드가 1순위 — .md/.html은 아는 사람용으로 뒤에.
    docx = f'<a class="btn ghost" href="/dl/{tok}.docx" download>제출용 워드 (.docx)</a>'
    pdf = f'<a class="btn ghost" href="/dl/{tok}.pdf" download>.pdf</a>'
    tail = (f'<a class="btn ghost" href="/dl/{tok}.md" download>.md</a>'
            f'<a class="btn ghost" href="/dl/{tok}.html" download>.html</a>')
    # 과제가 형식을 정했으면 그 형식을 앞에 놓고 금지된 형식은 뒤로 뺀다 — 기본 순서를
    # 그대로 두면 "pdf로 내라"는 과제에서 학생이 맨 앞의 .docx를 집어 간다.
    note = ""
    try:
        from .execution.format_guard import assignment_text
        from .understanding.format_spec import (
            detect_format_rules, forbidden_extensions, required_extension)
        rules = detect_format_rules(assignment_text(result), getattr(result, "spec", None) or {})
        want, banned = required_extension(rules), forbidden_extensions(rules)
        if want == ".pdf":
            docx, pdf = pdf.replace(">.pdf<", ">제출용 PDF (.pdf)<"), docx.replace(
                ">제출용 워드 (.docx)<", ">.docx<")
        if want or banned:
            said = []
            if want:
                said.append(f"이 과제는 {want} 제출이에요")
            for ext in sorted(banned):
                said.append(f"{ext}는 받지 않는다고 했어요")
            note = f'<p class="meta">{html.escape(" · ".join(said))}</p>'
        if ".docx" in banned:
            docx, tail = "", tail + docx
        if ".pdf" in banned:
            pdf, tail = "", tail + pdf
    except Exception:
        pass
    return form_btn + pptx_btn + docx + pdf + tail + note


# ── 제출 직전 마무리(마지막 한 칸) ──────────────────────────────────
# Until은 파일까지 만들고 멈춘다. 남는 일은 "어디에 · 무엇을 · 다 됐는지 확인하고
# 올리기"인데, 지금까지 그게 여러 화면에 흩어져 있었다. `/ready/<token>`은 그
# 마지막 한 칸을 한 화면으로 모은다 — 점검 · 올릴 파일 · eTL 링크 · 완료 표시.
#
# 업로드도 Until이 할 수 있다(2026-08-23) — `UNTIL_SUBMIT_ARMED=1`이면 확인 화면의
# 클릭 한 번으로 eTL에 저장하고 채점 확정까지 간다(`capture/sources/moodle_submit`).
# 경계선은 그대로다: **누를지 말지는 사람이 정한다.** 스위치가 켜져 있어도 자동으로
# 나가는 경로는 없고, 한 건마다 1회용 nonce·제출 게이트·신뢰 호스트를 통과해야 한다.
# 스위치가 꺼져 있으면 예전처럼 파일을 내려받아 사람이 올린다.

def _assignment_link(session_id: str) -> str:
    """세션의 eTL 과제 페이지 URL — 텔레메트리 메타의 과목·과제 id로 재구성.

    원문 URL을 저장하지 않는 기존 방침(비식별 id만 보관)을 그대로 두고, 표시에
    필요한 링크만 베이스 URL과 합쳐 만든다. 붙여넣기로 만든 세션은 빈 문자열."""
    meta = _TELEMETRY_META.get(session_id) or {}
    course, assignment = str(meta.get("course_id") or ""), str(meta.get("assignment_id") or "")
    if not (course.isdigit() and assignment.isdigit()):
        return ""
    return f"{etl_ws_base().rstrip('/')}/courses/{course}/assignments/{assignment}"


def _submit_state(session_id: str) -> dict:
    """세션의 제출 상태({submitted_at, note}) — 세션 workspace에 함께 저장된다."""
    ws = _WORKSPACES.get(session_id) or {}
    state = ws.get("submitted")
    return dict(state) if isinstance(state, dict) else {}


def mark_submitted(session_id: str, *, done: bool = True) -> None:
    """'제출했어요' 표시(사람이 누른 사실의 기록 — 실제 전송이 아니다)."""
    ws = _WORKSPACES.setdefault(session_id, {"excluded_sources": [], "versions": []})
    if done:
        ws["submitted"] = {"submitted_at": time.time()}
    else:
        ws.pop("submitted", None)
    _persist_session(session_id)


def _ready_checklist_html(result: Result, session_id: str) -> str:
    """제출 전 점검을 '통과 / 확인 필요'로 갈라 한 눈에. 결정적(LLM 0)."""
    try:
        from .readiness import assess_readiness
        items = assess_readiness(result).items
    except Exception:
        return ""
    if not items:
        return ""
    bad = [it for it in items if it.status in ("warn", "fail")]
    good = [it for it in items if it.status not in ("warn", "fail")]
    rows = []
    for it in bad:
        mark = "✗" if it.status == "fail" else "⚠"
        rows.append(f'<li style="color:var(--warn)">{mark} <b>{html.escape(it.label)}</b> · '
                    f'{html.escape(it.message)}</li>')
    for it in good:
        rows.append(f'<li>✓ <b>{html.escape(it.label)}</b> · {html.escape(it.message)}</li>')
    head = ("모두 통과했어요 — 올려도 됩니다." if not bad else
            f"{len(bad)}가지만 확인하면 됩니다. 그대로 올려도 막지는 않아요.")
    tok = html.escape(session_id)
    return ('<div class="matbox"><b>제출 전 점검</b>'
            f'<p class="meta">{html.escape(head)}</p>'
            '<ul style="list-style:none;padding-left:0;margin:.5rem 0 .2rem;'
            'font-size:.86rem;line-height:1.9">' + "".join(rows) + '</ul>'
            f'<p class="meta"><a href="/sv/{tok}">← 돌아가 고치기</a></p></div>')


def _required_formats(result: Result) -> list:
    """과제 명세가 요구하는 제출 파일 확장자(결정적 추출). 없으면 빈 목록."""
    spec = result.spec or {}
    raw = " ".join(str(spec.get(k) or "") for k in
                   ("submission_format", "deliverable", "format", "requirements", "goal"))
    found = []
    for ext in ("hwpx", "hwp", "docx", "pptx", "pdf", "zip", "ipynb", "py", "md"):
        if re.search(rf"\.{ext}\b|\b{ext.upper()}\s*(파일|형식|로)", raw, re.I):
            if ext not in found:
                found.append(ext)
    return found[:4]


def render_submit_ready(session_id: str, result: Result) -> str:
    """제출 직전 한 화면 — 점검 · 올릴 파일 · 어디에 올리나 · 완료 표시."""
    tok = html.escape(session_id)
    if getattr(result, "practice_mode", False):
        return ('<div class="smp"><p class="smp-step">연습 모드</p>'
                '<p class="meta">과거 과제 연습에는 제출 흐름이 없습니다.</p>'
                f'<p class="smp-x"><a href="/svf/{tok}">← 돌아가기</a></p></div>')
    state = _submit_state(session_id)
    link = _assignment_link(session_id)
    fmts = _required_formats(result)
    fmt_line = ("" if not fmts else
                '<p class="meta">과제가 요구하는 형식: '
                + " · ".join(f"<b>.{html.escape(f)}</b>" for f in fmts)
                + ' — 아래에서 같은 형식을 골라 내려받으세요.</p>')
    where = (f'<p style="margin:.8rem 0 0"><a class="btn" href="{html.escape(link)}" '
             f'target="_blank" rel="noopener">eTL 제출 페이지 열기 ↗</a></p>'
             if link else
             '<p class="meta">이 작업은 붙여넣기로 시작해서 eTL 과제 주소를 모릅니다 — '
             'eTL에서 해당 과제를 직접 열어 올려 주세요.</p>')
    if state.get("submitted_at"):
        done_box = ('<div class="matbox" style="border-color:var(--ok)">'
                    '<b>✓ 제출 완료로 표시해 뒀어요</b>'
                    '<p class="meta">실제 전송은 eTL에서 이뤄집니다 — Until은 사용자가 '
                    '눌렀다는 사실만 기록합니다.</p>'
                    '<form method="post" action="/submitted" style="margin:.6rem 0 0">'
                    f'<input type="hidden" name="session" value="{tok}">'
                    '<input type="hidden" name="undo" value="1">'
                    '<button class="btn ghost" type="submit">표시 취소</button></form></div>'
                    '<p style="margin:1rem 0 0"><a class="btn block big" href="/">'
                    '다음 과제 하나 더 →</a></p>')
    else:
        done_box = ('<form method="post" action="/submitted" style="margin:1.2rem 0 0">'
                    f'<input type="hidden" name="session" value="{tok}">'
                    '<button class="btn block big" type="submit">'
                    'eTL에 올렸어요 — 완료로 표시</button></form>'
                    '<p class="meta" style="margin:.45rem 0 0;font-size:.78rem">'
                    '표시해 두면 다음에 열었을 때 끝난 과제인지 바로 보입니다.</p>')
    return ('<div class="smp">'
            + _simple_head(result, right_fallback="제출")
            + '<p class="smp-step">마지막 한 칸 · 제출</p>'
            + _ready_checklist_html(result, session_id)
            + _autofilled_notice_html(session_id, result)
            + '<div class="matbox"><b>올릴 파일</b>' + fmt_line
            + f'<div class="row">{_submission_links(session_id, result)}</div></div>'
            # Until이 직접 내는 길 — '마지막 한 칸'이 바로 그 자리다. 여기에 없으면
            # 제출 기능을 켜 놓고도 **누를 곳이 없다**(라이브 확인 2026-08-23:
            # 초안 화면엔 확인 패널이 없고, 결정이 0개인 과제는 최종본 화면이
            # 비어 있어 어느 경로로도 제출에 도달하지 못했다).
            + _submission_preview_html(session_id, result)
            + '<div class="matbox"><b>어디에 올리나</b>' + where + '</div>'
            + done_box
            + f'<p class="smp-x"><span><a href="/svf/{tok}">← 완성본으로</a></span>'
            + '<span><a href="/">새 과제</a></span></p></div>')


def _submit_ready_link(session_id: str, result: Result) -> str:
    """완성 화면에서 마지막 칸으로 넘어가는 버튼(연습 모드는 숨김)."""
    if not session_id or getattr(result, "practice_mode", False):
        return ""
    if _submit_state(session_id).get("submitted_at"):
        return (f'<p style="margin:1rem 0 0"><a class="btn block ghost" '
                f'href="/ready/{html.escape(session_id)}">✓ 제출 완료로 표시됨 — 다시 보기</a></p>')
    return (f'<p style="margin:1rem 0 0"><a class="btn block big" '
            f'href="/ready/{html.escape(session_id)}">제출하러 가기 →</a></p>')


# ── 사람이 직접 고치는 편집란 ───────────────────────────────────────
# 왜 별개 경로인가: 지금 쌓이는 수정 신호는 finalize와 llm_revise뿐이고, 둘 다
# "AI가 무엇을 바꿨나"다. 개인화가 필요한 건 "사람이 무엇을 고쳤나"인데 그 값이
# 0이면 나머지가 전부 추측 위에 선다. AI 수정 지시(/revise)와 같은 폼에 섞으면
# 두 신호가 오염되므로(web.py의 llm_revise 주석과 같은 이유) 폼도 라우트도 나눈다.
#
# 강제하지 않는다 — 안 고치고 넘어가는 게 기본이고 편집은 선택이다.
# ("AI 딸깍하러 들어온 사람"에게 편집을 요구하면 그 자리에서 이탈한다.)

def edit_session(token: str, body: str) -> "Result | None":
    """사람이 고친 본문을 세션에 반영하고 human 편집 이벤트를 남긴다.

    반영이 먼저다 — 기록만 하고 본문이 안 바뀌면 사용자를 속이는 것이다.
    최종본이 있으면 최종본을, 없으면 초안을 고친다(화면에서 보고 있던 그 문서).
    """
    res = _get_session(token)
    if res is None:
        return None
    after = (body or "").strip()
    if not after:
        return res
    target_final = res.final_draft is not None
    current = res.final_draft if target_final else res.draft
    before = current.body
    if before.strip() == after:
        return res                      # 바뀐 게 없으면 아무것도 적립하지 않는다

    # 되돌리기용 이전 본문 보관(AI 수정과 같은 workspace 이력 사용).
    workspace = _WORKSPACES.setdefault(token, {"excluded_sources": [], "versions": []})
    versions = workspace.setdefault("versions", [])
    versions.append(before)
    del versions[:-5]

    from .boundary.models import Draft
    edited = Draft.from_text(after)
    if target_final:
        res.final_draft = edited
    else:
        res.draft = edited
        # 초안을 직접 고쳤으면 그 위에 만든 최종본은 더 이상 그 초안의 결과가
        # 아니다 — 남겨 두면 화면이 옛 최종본을 계속 보여 준다.
        res.final_draft = None
        res.final_guard = None
    # 사람 편집 신호 — edit_source="human". 이 값을 넣는 코드가 여기 하나뿐이다.
    try:
        from .context.edit_events import record_edit_event
        record_edit_event(before, after, edit_source="human",
                          register_key=str(getattr(res, "tone_register", "") or ""),
                          task_type=str((res.spec or {}).get("task_type") or ""))
    except Exception:
        pass
    _SESSIONS[token] = res
    _persist_session(token)
    return res


def _edit_form_html(session_id: str, result: Result, *, simple: bool) -> str:
    """본문 직접 고치기 — 기본은 접혀 있다(선택 기능이라는 걸 형태로 말한다)."""
    if not session_id or getattr(result, "practice_mode", False):
        return ""
    current = result.final_draft or result.draft
    if current is None:
        return ""
    tok = html.escape(session_id)
    rows = max(8, min(24, len(current.body.splitlines()) + 2))
    back = "simple" if simple else "full"
    return (
        '<details class="more-sd" style="margin-top:1.2rem"><summary>'
        '내가 직접 고치기 — 안 고쳐도 됩니다</summary>'
        '<p class="meta" style="margin:.5rem 0">AI에게 수정을 시키는 것과 다릅니다. '
        '여기서 고친 내용은 그대로 반영되고, 제출 파일에도 그대로 들어갑니다.</p>'
        f'<form method="post" action="/edit">'
        f'<input type="hidden" name="session" value="{tok}">'
        f'<input type="hidden" name="ui" value="{back}">'
        f'<textarea name="body" rows="{rows}" spellcheck="false">'
        f'{html.escape(current.body.strip())}</textarea>'
        '<button class="btn block" type="submit">고친 내용 저장</button>'
        '</form></details>')


def _submission_status_html(session_id: str, result: Result) -> str:
    """Explain the primary deliverable and keep the final human-submit boundary explicit."""
    if not session_id:
        return ""
    if getattr(result, "practice_mode", False):
        return ('<div class="matbox" style="border-color:var(--warn)"><b>연습 모드</b>'
                '<p class="meta">과거 과제를 재현하는 별도 작업 공간입니다. 실제 제출 '
                '기능과 제출용 파일 내보내기는 꺼져 있습니다.</p></div>')
    primary = "제출용 워드(.docx)"
    try:
        from pathlib import Path as _P
        from .capture.formfill import find_form_document
        src = find_form_document(result)
        if src and _P(src).suffix.lower() == ".hwp":
            primary = "교수가 제공한 .hwp 양식에 채울 값 표(.docx)"
        elif src:
            primary = "교수가 제공한 원본 양식에 채운 파일"
        elif (result.spec or {}).get("task_type") == "presentation":
            primary = "16:9 발표자료(.pptx)"
    except Exception:
        pass
    return ('<div class="matbox"><b>제출 파일 준비</b>'
            f'<p class="meta">우선 추천: <b>{html.escape(primary)}</b>. 분량·인용·남은 결정은 '
            '위 제출 준비 점검과 함께 확인하세요.</p>'
            + (('<p class="meta">제출 대상이 확정되면 <b>여기서 바로 eTL에 제출</b>할 수 '
                '있어요 — 확인 화면에서 내용을 다시 보고 누르면 됩니다.</p>')
               if submit_armed(CLOUD) else
               ('<p class="meta">Until은 파일까지만 준비합니다. '
                '최종 확인과 eTL 제출은 직접 진행해요.</p>'))
            +
            f'<div class="row">{_submission_links(session_id, result)}</div></div>')


def _submission_preview_from_plan(plan, session_id: str = "") -> str:
    """SubmissionPlan → 제출 미리보기 HTML(순수 함수, dry-run 전용).

    이 함수는 submit()을 전혀 호출하지 않는다 — 감사 로그·nonce 소비가 페이지
    렌더(새로고침 포함)로 발생하지 않도록 preview_request()만 쓴다. 하드 블록이
    있으면 '제출' 버튼을 아예 렌더하지 않는다. 허용 상태여도 버튼은 항상
    확인 POST가 nonce를 발급·소비하며, 이 GET 렌더 자체는 계속 부작용 0이다."""
    preview_request = _submit_backend().preview_request

    blocks_html = "".join(
        f'<li style="color:var(--warn)">✗ <b>{html.escape(b.code)}</b> · '
        f'{html.escape(b.message)}</li>' for b in plan.blocks)
    warns_html = "".join(
        f'<li style="color:var(--warn)">⚠ <b>{html.escape(w.code)}</b> · '
        f'{html.escape(w.message)}</li>' for w in plan.warnings)
    list_html = ""
    if blocks_html or warns_html:
        list_html = (f'<ul style="margin:.4rem 0 .1rem;list-style:none;padding-left:0">'
                     f'{blocks_html}{warns_html}</ul>')
    if plan.allowed:
        # 순수 렌더 — 감사 로그·nonce 소비·네트워크 0.
        req = preview_request(plan)
        status_html = '<p class="meta ok">하드 블록 없음 — 제출 가능 상태입니다.</p>'
        req_html = (f'<p class="meta">보낼 요청(미리보기) · '
                    f'<code>{html.escape(str(req.get("method", "")))} '
                    f'{html.escape(str(req.get("url", "")))}</code></p>')
        tok = html.escape(session_id)
        btn_html = (f'<form method="post" action="/submit/prepare" style="margin:0">'
                    f'<input type="hidden" name="session" value="{tok}">'
                    '<button type="submit" class="btn">확인하고 제출</button></form>'
                    if session_id else '<button type="button" class="btn">확인하고 제출</button>')
    else:
        status_html = '<p class="meta" style="color:var(--warn)">하드 블록이 있어 제출할 수 없습니다.</p>'
        req_html = ""
        btn_html = ""
    return (
        '<div class="matbox"><b>제출 미리보기 <span class="meta">(확인 전에는 전송 없음)</span></b>'
        f'{status_html}{list_html}{req_html}'
        + ('<p class="meta">클라우드에서는 실제 전송이 열리지 않아요. 확인 후에도 dry-run으로만 보여 드립니다.</p>'
           if CLOUD and plan.allowed else '')
        + (f'<p class="meta" style="margin-top:.4rem">{btn_html}</p>' if btn_html else '')
        + '</div>'
    )


def _submission_preview_html(session_id: str, result: Result) -> str:
    """세션 Result에서 SubmissionPlan을 만들어 제출 미리보기 패널을 렌더(dry-run 전용).

    대상 과제(assignment_id/course_id)는 세션 spec에서만 가져온다 — 없으면
    게이트가 assignment_mismatch로 차단하는 것이 정상 동작이다."""
    if not session_id:
        return ""
    try:
        # 렌더는 순수해야 한다 — 새로고침마다 확인 nonce를 발급하면 원장이
        # 무한 증가한다. issue=False로 nonce 발급 자체를 건너뛴다.
        plan = _submission_plan(result, issue=False)
    except Exception:
        return ""
    return _submission_preview_from_plan(plan, session_id)


def _submission_plan(result: Result, *, issue: bool):
    """세션 Result로 제출 plan 구성. GET은 issue=False, 확인 POST만 True."""
    from .capture.sources.discovery import SNU_ETL_BASE
    from .capture.sources.models import AssignmentRef
    from .execution.submission_gate import build_submission_plan
    spec = getattr(result, "spec", None) or {}
    ref = AssignmentRef(id=str(spec.get("assignment_id", "") or ""),
                        title=str(spec.get("title", "") or ""),
                        course_id=str(spec.get("course_id", "") or ""))
    evidence = [getattr(sd, "text", "") or ""
                for sd in (getattr(result, "source_docs", None) or [])]
    if isinstance(spec.get("requirements"), list):
        evidence.extend(str(x) for x in spec["requirements"])
    return build_submission_plan(
        result, ref, base_url=SNU_ETL_BASE,
        issue=issue, evidence_texts=evidence)


def _submission_binding(uid: str, session_id: str) -> str:
    return f"{uid or 'local'}:{session_id}"


def prepare_submission(result: Result, *, uid: str, session_id: str):
    """정확한 현재 plan에 사용자·세션 결합 nonce를 발급한다(네트워크 0)."""
    from dataclasses import replace
    from .execution.submit_nonce import issue_nonce
    plan = _submission_plan(result, issue=False)
    if not plan.allowed:
        return plan
    nonce = issue_nonce(plan.content_hash,
                        binding=_submission_binding(uid, session_id))
    return replace(plan, confirm_nonce=nonce)


def render_submission_confirmation(plan, session_id: str, *, result=None) -> str:
    """최종 전송 직전, nonce에 결합된 대상과 본문을 다시 보여 준다.

    **이 버튼이 진짜 제출인지 아닌지를 화면이 분명히 말해야 한다.** 실제 전송이 열린
    상태에서 "정말 제출할까요?"만 띄우고 원시 id(`101 / 777`)를 보여 주면, 누르는
    사람은 자기가 무엇을 어디에 확정하는지 모른다. 되돌리기가 과목 설정에 막혀 있을
    수 있으므로 그 사실도 함께 밝힌다.
    """
    if not plan.allowed:
        return _submission_preview_from_plan(plan, session_id)
    target = plan.target
    spec = (getattr(result, "spec", None) or {}) if result is not None else {}
    course = str(spec.get("course") or "").strip()
    title = str(spec.get("title") or "").strip()
    where = " · ".join(x for x in (course, title) if x)
    where_html = (f'<p><b>{html.escape(where)}</b></p>' if where else "")
    live = submit_armed(CLOUD)
    if live:
        lead = ("이 버튼을 누르면 <b>eTL에 실제로 제출</b>되고 채점 대기로 확정됩니다. "
                "과목 설정에 따라 되돌리지 못할 수 있어요.")
        btn = "eTL에 지금 제출하기"
    else:
        lead = ("실제 전송은 아직 열려 있지 않아요 — 누르면 어떤 요청이 나갈지만 "
                "확인합니다(dry-run). 파일을 내려받아 직접 올려도 됩니다.")
        btn = "요청 확인하기 (dry-run)"
    warn = "".join(
        f'<li>{html.escape(w.message)}</li>' for w in (plan.warnings or []))
    warn_html = (f'<div class="matbox"><b>확인하고 누르세요</b><ul>{warn}</ul></div>'
                 if warn else "")
    return ('<div class="sec"><h2>정말 제출할까요?</h2>'
            + where_html
            + f'<p class="meta">{lead}</p>'
            + f'<p class="meta">대상 <code>{html.escape(target.course_id)} / '
            f'{html.escape(target.assignment_id)}</code></p>'
            + warn_html
            + f'<div class="doc"><pre>{html.escape(plan.content)}</pre></div>'
            '<form method="post" action="/submit/confirm">'
            f'<input type="hidden" name="session" value="{html.escape(session_id)}">'
            f'<input type="hidden" name="confirm_nonce" value="{html.escape(plan.confirm_nonce)}">'
            f'<button class="btn" type="submit">{btn}</button>'
            '</form>'
            '<p><a class="btn ghost" href="javascript:history.back()">← 돌아가기</a></p>'
            '</div>')


def _submit_backend():
    """제출 실행 모듈 — eTL은 Moodle 웹서비스다. Canvas는 레거시 보존 경로.

    `canvas_submit`은 Canvas REST API(`/api/v1/.../submissions`)로 쏘는데 현재 eTL은
    Moodle이라 그 경로로는 **아무것도 나가지 않았다**. "제출까지 된다"고 적혀 있었지만
    실제로는 되지 않던 이유가 이것이다(사용자 보고 2026-08-23).
    """
    if os.getenv("UNTIL_SUBMIT_BACKEND", "moodle").lower() == "canvas":
        from .capture.sources import canvas_submit
        return canvas_submit
    from .capture.sources import moodle_submit
    return moodle_submit


def submit_armed(cloud: bool) -> bool:
    """실제 전송을 열지 말지 — 운영자가 켜는 단일 스위치.

    예전에는 `not cloud`가 조건에 있어 **라이브 앱에서는 절대 실전송이 안 됐다**.
    사용자가 쓰는 곳이 라이브인데 거기서만 꺼져 있으면 기능이 없는 것과 같다
    (사용자 지시 2026-08-23: 최종 제출까지 가능하게).

    켜져 있어도 한 건의 제출은 여전히 4겹을 통과해야 나간다 — 사람이 확인 화면에서
    누른 클릭, 그 클릭에 결합된 1회용 nonce, 제출 게이트 통과(plan.allowed), 신뢰
    호스트. 이 스위치는 '기능을 켠다'이지 '자동으로 낸다'가 아니다.
    """
    del cloud   # 더는 보지 않는다 — 남겨 둔 인자는 호출부 호환용
    return os.getenv("UNTIL_SUBMIT_ARMED") == "1"


def confirm_submission(result: Result, *, cloud: bool, confirm_nonce: str,
                       uid: str, session_id: str):
    """미리 발급된 nonce만 소비한다. 최종 POST에서는 절대 새 nonce를 만들지 않는다."""
    backend = _submit_backend()
    plan = _submission_plan(result, issue=False)
    armed = submit_armed(cloud)
    token = _get_canvas_token(session_id, uid=uid)
    if not token:
        token = _remembered_token() or _env_canvas_token()
    receipt = backend.submit(plan, confirm_nonce, armed=armed, token=token,
                             binding=_submission_binding(uid, session_id))
    # 가장 강한 수락 증거 — 실제로 냈다. dry-run(클라우드)은 제출이 아니므로 제외한다.
    if plan.allowed and not getattr(receipt, "dry_run", False):
        try:
            from .persona.events import update_acceptance_for_result
            update_acceptance_for_result(result, True, channel="web")
        except Exception:
            pass
    return plan, receipt


def render_submission_receipt(plan, receipt, *, cloud: bool) -> str:
    req = receipt.request or {}
    form = req.get("form") or {}
    rows = "".join(f'<li><code>{html.escape(str(k))}</code>: {html.escape(str(v))}</li>'
                   for k, v in form.items())
    if not plan.allowed:
        blocks = "".join(f'<li>{html.escape(x.message)}</li>' for x in plan.blocks)
        return ('<div class="sec"><h2>제출할 수 없어요</h2><ul>' + blocks
                + '</ul><p><a href="javascript:history.back()">← 돌아가 보완하기</a></p></div>')
    if receipt.dry_run:
        note = ("실제 전송이 아직 열려 있지 않아요(UNTIL_SUBMIT_ARMED). "
                "아래 파일을 내려받아 eTL에 직접 올리면 됩니다.")
        title = "제출 요청을 확인했어요 (dry-run)"
    else:
        note = (receipt.detail or f"eTL 응답 상태: {receipt.status}") \
            if receipt.sent else receipt.detail
        title = "제출을 완료했어요" if receipt.sent else "제출하지 못했어요"
    return (f'<div class="sec"><h2>{html.escape(title)}</h2>'
            f'<p class="meta">{html.escape(note)}</p>'
            f'<p><code>{html.escape(str(req.get("method", "")))} '
            f'{html.escape(str(req.get("url", "")))}</code></p><ul>{rows}</ul>'
            '<p><a class="btn ghost" href="/">홈으로</a></p></div>')


def _teacher_rules_html(result: Result) -> str:
    """Show how prior instructor feedback changes the current assignment checks."""
    entries = getattr(result, "teacher_feedback", None) or []
    if not entries:
        return ""
    from .context.teacher_feedback import feedback_summary
    summary = feedback_summary(entries)
    return ('<div class="matbox"><b>이전 교수 피드백에서 만든 점검 규칙</b>'
            f'<p class="meta">{html.escape(summary)}</p>'
            '<p class="meta">이번 초안의 프롬프트와 제출 전 점검에 반영됐어요. 과거 피드백은 '
            '새 사실이나 관점을 대신 정하는 데 사용하지 않습니다.</p></div>')


def _decision_choices(note: str) -> list:
    """결정 노트에서 후보 선택지를 추출(후보:/예:/괄호 a / b / c). 최대 5개, 짧은 것만."""
    import re
    seg = None
    m = re.search(r"(?:후보|선택지|예시|예)\s*[:：]\s*([^—\-]+)", note)
    if m:
        seg = m.group(1)
    else:
        m2 = re.search(r"[\(（]([^)）]+)[\)）]", note)  # 괄호 안 a / b / c
        if m2 and ("/" in m2.group(1) or "," in m2.group(1)):
            seg = m2.group(1)
    if not seg:
        return []
    seg = re.sub(r"\s*등\.?\s*$", "", seg.strip())
    parts = re.split(r"\s*[/,·]\s*", seg)
    out, seen = [], set()
    for p in parts:
        c = p.strip(" .'\"")
        if 1 < len(c) <= 22 and c not in seen:
            seen.add(c); out.append(c)
    return out[:5]


def _decision_field(i: int, note: str, idp: str = "a", value: str = "", why: str = "") -> str:
    """결정 1개 입력 필드 — 선택지 칩(클릭으로 채움) + 짧은 입력칸. (선택 입력)

    value/why가 있으면 AI 제안으로 칸을 미리 채우고 근거를 보여준다(수정 가능)."""
    fid = f"{idp}{i}"
    chips = "".join(
        f'<button type="button" class="chip" onclick="pick(\'{fid}\',this)">{html.escape(c)}</button>'
        for c in _decision_choices(note)
    )
    # 과거 내 답 재제안(결정적·로컬 히스토리) — AI 제안이 없을 때만, 클릭으로 채움.
    if not value:
        try:
            from .context.answer_history import suggest_from_history
            h = suggest_from_history(note)
        except Exception:
            h = None
        if h:
            label = h.answer if len(h.answer) <= 42 else h.answer[:41] + "…"
            chips = (f'<button type="button" class="chip" data-val="{html.escape(h.answer)}" '
                     f'onclick="pick(\'{fid}\',this)" title="비슷한 결정에 답했던 내용(유사도 {h.similarity})">'
                     f'지난 답: {html.escape(label)}</button>') + chips
    chipbox = f'<div class="chips">{chips}</div>' if chips else ""
    # 왜 이게 당신 몫인지 — 결정적 분류로 경계선 개념 강화(떠넘김 아님을 밝힘).
    from .boundary.rationale import classify_decision
    rat = classify_decision(note)
    mine_html = (f'<p class="mine"><b>{html.escape(rat.category)}</b> · '
                 f'{html.escape(rat.why)}</p>')
    # AI 제안이 있으면 칸을 미리 채우고(수정 가능) 한 줄 근거를 보여준다.
    val = html.escape(value or "")
    why_html = (f'<p class="why">제안 근거: {html.escape(why)}</p>' if (value and why) else "")
    rows = 2 if value else 1
    return (f'<div class="decision"><label for="{fid}">[{i}] {html.escape(note)}</label>'
            f'{mine_html}'
            f'{chipbox}'
            f'<textarea id="{fid}" name="answer_{i}" rows="{rows}" '
            f'placeholder="(선택) 칩을 누르거나 직접 적기 · 비워도 됨">{val}</textarea>'
            f'{why_html}</div>')


def _source_url_map(source_docs: "list | None") -> "dict":
    """source_docs(1-기반 [자료N] 순서) → {N: url}. http(s) URL만 링크 대상으로 삼는다.

    javascript:·data: 등 스킴은 링크로 만들지 않는다(XSS 방지). 옛 세션 데이터의
    url 없는 SourceDoc은 getattr로 안전 처리."""
    out: dict = {}
    for i, sd in enumerate(source_docs or [], 1):
        url = (getattr(sd, "url", "") or "").strip()
        if url.startswith("http://") or url.startswith("https://"):
            out[i] = url
    return out


def _highlight_markers(body: str, source_docs: "list | None" = None) -> str:
    """초안을 HTML escape 하고 [[DECISION: ...]] 마커와 [자료N]/[출처] 인용을 강조한다.

    source_docs가 주어지면 URL이 있는 [자료N]은 그 출처로 가는 새 탭 링크로 만든다
    (범례와 동일한 1-기반 번호). URL이 없거나 목록에 없으면 기존 강조만 한다."""
    import re
    escaped = html.escape(body)
    out = re.sub(
        r"(\[\[DECISION:.*?\]\])",
        r'<span class="marker">\1</span>',
        escaped,
        flags=re.DOTALL,
    )
    # 근거 인용 강조 — [자료N]은 URL 있으면 링크, 없으면 강조. [출처...]는 강조만.
    urls = _source_url_map(source_docs)

    def _cite(m: "re.Match") -> str:
        n = int(m.group(1))
        url = urls.get(n, "")
        if url:
            safe = html.escape(url, quote=True)
            return (f'<a class="cite citelink" href="{safe}" target="_blank" '
                    f'rel="noopener noreferrer" title="출처 열기 (새 탭)">[자료{n}]</a>')
        return f'<span class="cite">[자료{n}]</span>'

    out = re.sub(r"\[자료(\d+)\]", _cite, out)
    out = re.sub(r"(\[출처[^\]]*\])", r'<span class="cite">\1</span>', out)
    return out


def _sources_html(result: Result, session_id: str = "") -> str:
    """근거 자료 범례 — Execution에 넣은 자료 목록. 본문의 [자료N]가 인용한 것은 표시."""
    import re
    srcs = getattr(result, "sources", None) or []
    if not srcs:
        return ""
    draft = result.final_draft or result.draft
    body = draft.body if draft else ""
    cited = {int(n) for n in re.findall(r"\[자료(\d+)\]", body)}
    urls = _source_url_map(getattr(result, "source_docs", None))
    items = []
    workspace = _WORKSPACES.get(session_id, {}) if session_id else {}
    excluded = {int(x) for x in workspace.get("excluded_sources", [])}
    for i, title in enumerate(srcs, 1):
        tag = ' <span class="pill ok">인용됨</span>' if i in cited else ''
        if i in excluded:
            tag += ' <span class="pill warn">제외됨</span>'
        label = html.escape(title)
        url = urls.get(i, "")
        if url:  # 범례 제목도 본문 [자료N]과 같은 출처로 링크(새 탭).
            safe = html.escape(url, quote=True)
            label = (f'<a class="matlink" href="{safe}" target="_blank" '
                     f'rel="noopener noreferrer">{label}</a>')
        why = "본문에서 직접 인용" if i in cited else "과제와 관련 있어 참고 후보로 연결"
        # 자료를 체크박스로 빼고 재작성시키는 조작은 없앴다(사용자 지시
        # 2026-08-23) — 어느 자료를 뺄지 고르는 일은 학생이 하러 온 일이 아니고,
        # 목록마다 체크박스가 붙으면 '읽어 보는 근거 목록'이 '조작해야 하는 폼'이
        # 된다. 이미 제외된 자료가 있으면 상태만 계속 보여 준다(되돌릴 길은
        # 세션을 새로 여는 것).
        items.append(f'<li><b>[자료{i}]</b> {label}{tag}<br>'
                     f'<span class="meta">선택 이유: {why}</span></li>')
    from .context.citation_coverage import citation_coverage
    cov = citation_coverage(srcs, body)
    cov_color = {"uncited": "var(--warn)", "invalid": "var(--warn)",
                 "partial": "var(--muted)", "full": "var(--ok)"}.get(cov.status, "var(--muted)")
    cov_line = (f'<p class="meta" style="border-left:3px solid {cov_color};padding-left:.6rem;'
                f'margin:.4rem 0">{html.escape(cov.message)}</p>')
    return ('<div class="matbox" id="source-control"><b>근거 자료 (이 초안이 본 자료)</b>'
            '<p class="meta" style="margin:.25rem 0">초안 속 '
            '<span class="cite">[자료N]</span> 표시가 아래 목록을 가리켜요 — 어떤 자료에 근거했는지 보여줍니다.</p>'
            f'{cov_line}'
            f'<ul style="margin-bottom:.1rem">{"".join(items)}</ul></div>')


def _version_compare_html(session_id: str, result: Result) -> str:
    """업데이트 전후 비교 + 이전 버전 복원. 버전이 없으면 아무것도 그리지 않는다.

    예전에는 여기 **문단을 골라 AI에게 다시 시키는** 패널이 같이 있었다(라디오
    20개 + 지시 입력). 없앴다 — 고르고 지시하는 조작이 거추장스럽고(사용자 지시
    2026-08-23), 고치고 싶으면 바로 아래 '내가 직접 고치기'에서 직접 고치는 편이
    빠르다. 되돌릴 길(복원)은 조작 위젯이 아니라 안전장치라 남긴다.
    """
    if not session_id:
        return ""
    workspace = _WORKSPACES.get(session_id, {})
    if not workspace.get("versions"):
        return ""
    from .diffview import diff_drafts, summarize_changes
    previous = workspace["versions"][-1]
    current = (result.final_draft or result.draft).body
    summary = summarize_changes(diff_drafts(previous, current))
    return (
        '<div class="matbox"><b>업데이트 전후 비교</b>'
        f'<p class="meta">이전 {len(previous)}자 → 현재 {len(current)}자 · '
        f'{html.escape(summary)}</p>'
        f'<details><summary>이전 결과 보기</summary><div class="doc">'
        f'{_highlight_markers(previous)}</div></details>'
        f'<form method="post" action="/revise">'
        f'<input type="hidden" name="session" value="{html.escape(session_id)}">'
        '<input type="hidden" name="mode" value="restore">'
        '<button class="btn ghost" type="submit">이전 버전으로 복원</button></form></div>')


def _anchored_draft_html(text: str, source_docs=None, *, final: bool = False) -> str:
    """Render stable paragraph anchors used by requirement trace links."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    rendered = "".join(
        f'<div id="draft-p{i}" class="draft-paragraph">{_highlight_markers(p, source_docs)}</div>'
        for i, p in enumerate(paragraphs, 1))
    cls = "doc final" if final else "doc"
    return f'<div class="{cls}" id="draft-body">{rendered}</div>'


def _context_summary_html(result: Result) -> str:
    """주입된 맥락(수업자료·내 파일·말투)이 있으면 한 줄 요약 HTML로."""
    c = result.context
    if not c or not (c.course_hits or c.my_hits or c.voice.n_samples):
        return ""
    return f'<p class="meta">반영한 맥락: {html.escape(c.summary())}</p>'


def _etl_materials_html(result: Result) -> str:
    """eTL에서 자동수집한 관련 자료 목록(있을 때만)."""
    mats = getattr(result, "etl_materials", None)
    if not mats:
        return ""
    items = "\n".join(
        f"<li>{html.escape(m.name)} <span class=\"meta\">(관련도 {m.score:g})</span></li>"
        for m in mats
    )
    return ('<div class="matbox"><b>eTL에서 모은 관련 자료</b>'
            '<p class="meta" style="margin:.25rem 0">과제와 관련도가 높은 순서. 초안 작성에 근거로 함께 넣었어요.</p>'
            f'<ul style="margin-bottom:.1rem">{items}</ul></div>')


def _etl_announcements_html(result: Result) -> str:
    """이 과제 관련 eTL 공지(있을 때만) — 교수 Q&A 추가 조건까지 초안에 반영됐음을 알림."""
    anns = getattr(result, "etl_announcements", None)
    if not anns:
        return ""
    rows = []
    for a in anns:
        date = ""
        if getattr(a, "created_iso", ""):
            date = f' <span class="meta">({html.escape(a.created_iso[:10])})</span>'
        reply = ""
        if getattr(a, "replies", None):
            reply = ('<div class="meta" style="margin:.1rem 0 .1rem .6rem">'
                     '↳ 교수 답글 등 추가 조건 포함</div>')
        link = (f'<a href="{html.escape(a.url)}" target="_blank" rel="noopener">원문</a>'
                if getattr(a, "url", "") else "")
        rows.append(
            f'<li><b>{html.escape(a.subject)}</b>{date} {link}'
            f'<div class="meta" style="margin:.1rem 0">{html.escape((a.body or "")[:160])}'
            f'{"…" if len(a.body or "") > 160 else ""}</div>{reply}</li>')
    return ('<div class="matbox"><b>이 과제 관련 eTL 공지</b>'
            '<p class="meta" style="margin:.25rem 0">공지·Q&A에서 이 과제와 관련된 내용을 '
            '찾아 초안에 함께 반영했어요(복붙으론 못 얻는 정보).</p>'
            f'<ul style="margin-bottom:.1rem">{"".join(rows)}</ul></div>')


def _inquiry_assignment_html(result: Result) -> str:
    """프로필 학번으로 찾은 이번 주 질의 대상과 실제 마감을 투명하게 표시."""
    a = getattr(result, "inquiry_assignment", None)
    if not a:
        return ""
    due = (f" · 실제 마감 {a.due_date.isoformat()} {html.escape(a.due_time)}"
           if a.due_date else "")
    field = (f'<p class="meta" style="margin:.25rem 0">공식 연구 분야: '
             f'{html.escape(a.professor_field)}</p>' if a.professor_field else
             '<p class="meta" style="margin:.25rem 0">공식 연구 분야를 찾지 못해 '
             '세부 내용을 추측하지 않았어요.</p>')
    return ('<div class="matbox"><b>이번 질의 대상</b>'
            f'<p style="margin:.25rem 0">{a.week}주차 · '
            f'{html.escape(a.professor)} 교수{due}</p>{field}'
            '<p class="meta" style="margin:.25rem 0">Until 프로필 학번과 공개 '
            '질의순번표를 서버에서 대조했으며, 학번은 AI에 전달하지 않았어요.</p></div>')


def _suggested_prompts_html(result: Result) -> str:
    if not result.suggested_prompts:
        return ""
    # 교육 모드: 결정마다 다른 프롬프트 기법 + '왜 좋은지'를 함께 보여준다(AI 공부 병행).
    from .prompts.suggest import suggest_prompts_detailed
    draft = result.final_draft or result.draft
    sugs = suggest_prompts_detailed(draft) if draft else []
    if not sugs:
        return ""
    cards = []
    for i, s in enumerate(sugs, 1):
        cards.append(
            '<div class="pcard edu">'
            '<div class="pi">'
            f'<div class="pk">PROMPT {i:02d} · <span class="ptag">{html.escape(s.pattern)}</span></div>'
            f'<div class="pt">{html.escape(s.text)}</div>'
            f'<p class="why">이 질문 방식이 유용한 이유: {html.escape(s.why)}</p>'
            '</div>'
            f'<textarea id="pp{i}" hidden>{html.escape(s.text)}</textarea>'
            f'<button type="button" class="btn ghost" onclick="copyDoc(\'pp{i}\',this)">프롬프트 복사</button>'
            '</div>'
        )
    # ③ 막히면 이렇게 물어보세요 — 토글(기본 접힘: 막혔을 때 펼쳐 보는 도움말 + 프롬프트 학습)
    return ('<details class="tgsec">'
            '<summary><span class="tglab">＊ Prompts</span>'
            '<h2>막히면, 이렇게 물어보세요 <span class="hint">+ 프롬프트 공부</span></h2></summary>'
            '<div class="tgbody">'
            '<p class="meta">결정을 스스로 정할 때 AI에게 던질 질문이에요. 결정마다 <b>서로 다른 '
            '프롬프트 기법</b>을 보여주니, 복사해 ChatGPT·Claude에 붙여넣으며 기법도 익혀 보세요.</p>'
            + "".join(cards) + '</div></details>')


_REVIEW_PILL = {"충분": "ok", "보완 권장": "", "부족": "warn"}


def _review_html(review) -> str:
    """완성도 점검 리포트 패널 — 등급·자료활용·빈 곳·결정 점검."""
    if review is None:
        return ""
    lvl = getattr(review, "level", "") or ""
    cls = _REVIEW_PILL.get(lvl, "")
    gaps = getattr(review, "gaps", None) or []
    gaps_html = ""
    if gaps:
        items = "".join(f"<li>{html.escape(g)}</li>" for g in gaps)
        gaps_html = f'<p class="meta" style="margin:.4rem 0 .1rem">더 채울 수 있는 곳:</p><ul>{items}</ul>'
    else:
        gaps_html = '<p class="meta">게으르게 빈 곳은 발견되지 않았어요.</p>'
    return (
        '<div class="matbox">'
        f'<b>완성도 점검</b> <span class="pill {cls}">{html.escape(lvl)}</span>'
        f'<p class="meta" style="margin:.4rem 0 .1rem">{html.escape(getattr(review, "summary", "") or "")}</p>'
        f'<p class="meta" style="margin:.2rem 0">자료 활용: {html.escape(getattr(review, "coverage", "") or "")}</p>'
        f'<p class="meta" style="margin:.2rem 0">◇ 결정 점검: {html.escape(getattr(review, "decision_check", "") or "")}</p>'
        f'{gaps_html}'
        '</div>'
    )


def _type_badge(result: Result) -> str:
    """감지된 과제 유형 배지(에세이/문제풀이/보고서/코드/발표)."""
    t = (getattr(result, "spec", None) or {}).get("task_type")
    if not t:
        return ""
    from .understanding.task_type import LABELS
    return f'<div class="statusbar"><span class="pill">유형 · {html.escape(LABELS.get(t, t))}</span></div>'


def _guard_pills(g) -> str:
    cls = "ok" if getattr(g, "passed", False) else "warn"
    label = "경계선 통과" if getattr(g, "passed", False) else "검토 필요"
    return (f'<div class="statusbar"><span class="pill {cls}">{label}</span>'
            f'<span class="pill">시도 {g.attempts}회 · 재요청 {g.reasks}</span></div>')


def _voice_note_html(session_id: str) -> str:
    """자동 학습(문체·교수 피드백) 상태 한 줄(초안 페이지) — 투명성 + 통제.

    전자동 수집(동의 UI 없음)의 균형추: 무엇을 배웠고 어떻게 끄는지 항상 보인다."""
    prof, disabled, n_docs = _stored_voice()
    fb, fb_disabled = _stored_feedback()
    btn0 = ('style="font:inherit;background:none;border:none;padding:0;'
            'cursor:pointer;text-decoration:underline;color:inherit"')
    if prof is None and not fb:
        if disabled or fb_disabled:
            # '끄기' 뒤에도 되돌릴 손잡이를 남긴다 — 없으면 저장 파일을 수동
            # 삭제하는 것 말고 재학습 경로가 없는 막다른 길(리뷰 발견).
            sess0 = f'<input type="hidden" name="session" value="{html.escape(session_id)}">'
            return ('<p class="meta" style="margin:.1rem 0 .6rem">'
                    '문체·피드백 자동 학습이 꺼져 있어요. '
                    f'<form method="post" action="/voice/relearn" style="display:inline">{sess0}'
                    f'<button {btn0}>다시 켜기</button></form>'
                    ' <span class="meta">(다음 과제 목록 조회 때 다시 학습돼요)</span></p>')
        return ""
    bits = []
    if prof is not None:
        from .context.voice_autolearn import load_stored_voice_stats
        vs = load_stored_voice_stats(_voice_store_path())
        if vs:
            coverage = (f"최근 {vs.get('courses_scanned', 0)}/"
                        f"{vs.get('courses_total', 0)}과목에서 ")
            total = vs.get("submitted_total", 0)
            qualifier = "" if vs.get("submitted_total_exact") else "최소 "
            bits.append(
                f"내 문체({coverage}제출 완료 {qualifier}{total}건 조회 · "
                f"학습 가능 {vs.get('eligible_submissions', 0)}건 · "
                f"표본 {n_docs}개 사용 · {html.escape(prof.ending_style)})")
        else:
            bits.append(f'내 문체(표본 {n_docs}개·{html.escape(prof.ending_style)})')
    if fb:
        bits.append(f'교수 피드백 {len(fb)}건')
    btn = ('style="font:inherit;background:none;border:none;padding:0;'
           'cursor:pointer;text-decoration:underline;color:inherit"')
    sess = f'<input type="hidden" name="session" value="{html.escape(session_id)}">'
    return (
        '<p class="meta" style="margin:.1rem 0 .6rem">'
        f'eTL에서 학습됨 — {" · ".join(bits)}. '
        '초안·완성본에 반영돼요(문체는 직접 올린 글이 우선). '
        f'<form method="post" action="/voice/relearn" style="display:inline">{sess}'
        f'<button {btn}>다시 학습</button></form> · '
        f'<form method="post" action="/voice/off" style="display:inline">{sess}'
        f'<button {btn}>끄기</button></form></p>')


def _spec_text(value, limit: int = 110) -> str:
    """spec 값 하나를 사람이 읽는 한 줄로. dict·list가 와도 원문을 그대로 뱉지 않는다.

    spec은 LLM이 만든 JSON이라 문자열을 기대한 자리에 구조가 오는 일이 있다. 실제로
    화면 상단에 `{'name': '2025-1 대학 글쓰기 1', 'code': '049'}`가 그대로 떴다
    (라이브 확인 2026-08-23). 사용자가 제일 먼저 보는 줄이라 여기서 막는다.
    """
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("name", "title", "course_name", "label"):
            got = value.get(key)
            if isinstance(got, str) and got.strip():
                return got.strip()[:limit]
        return ""
    if isinstance(value, (list, tuple)):
        parts = [_spec_text(x, limit) for x in value]
        return " · ".join(p for p in parts if p)[:limit]
    return str(value).strip()[:limit]


def _assignment_heading(result: Result, limit: int = 110) -> str:
    """화면 상단 표기 — **과목명 + 과제명**.

    과제명만 띄우면 '과제1'·'HW#1'처럼 어느 과목 것인지 알 수 없는 제목이 많다
    (사용자 지시 2026-08-23). 과목명을 아는 경우에만 앞에 붙인다.
    """
    spec = result.spec or {}
    title = _spec_text(spec.get("title") or spec.get("deliverable")
                       or spec.get("goal"))
    course = _spec_text(spec.get("course"))
    if course and title:
        return f"{course} · {title}"[:limit]
    return (course or title)[:limit]


def _detail_group(label: str, title: str, panels: list) -> str:
    """세부 정보 한 묶음 — 내용이 하나도 없으면 **접힘 자체를 그리지 않는다**.

    빈 아코디언은 열어 봐야 아무것도 없어서 화면만 늘린다. 예전에는 패널 15개가
    한 겹 안에 그대로 나열돼 있었는데, 없는 것까지 자리를 차지했다.
    """
    body = "\n".join(x for x in panels if x and str(x).strip())
    if not body.strip():
        return ""
    return (f'<details class="tgsec"><summary><span class="tglab">＊ {html.escape(label)}</span>'
            f'<h2>{html.escape(title)}</h2></summary>'
            f'<div class="tgbody">{body}</div></details>')


def render_draft(session_id: str, result: Result, suggestions: dict | None = None,
                 review=None, answers: dict | None = None,
                 voice_note: str = "") -> str:
    g = result.guard
    sugg = suggestions or {}
    my_answers = answers or {}
    # 과제 제목 — /sessions에서 돌아왔을 때 무엇을 보고 있는지 바로 알 수 있게.
    _title = _assignment_heading(result)
    title_html = (f'<p class="meta" style="margin:.1rem 0 .7rem">📄 {html.escape(_title)}</p>'
                  if _title else "")
    # 결과물이 맨 위다(사용자 지시 2026-08-23). 근거·세부는 아래로 내린다 —
    # 사용자가 보러 온 것은 산출물이지 우리 작업 내역이 아니다.
    body_top = ""
    _body = (result.draft.body or "").strip()
    if _body:
        # 앵커 렌더러를 그대로 쓴다 — 요구사항 추적의 '관련 문단 보기'가
        # `#draft-p{n}`을 가리키므로, 여기서 평범한 본문으로 바꾸면 그 링크가
        # 전부 깨진다(옮기면서 한 번 깨뜨렸다).
        body_top = (
            '<div class="sec" style="border:0;padding-top:0">'
            + _anchored_draft_html(_body, result.source_docs)
            + _doc_tools(_body, doc_id="topsrc", filename="until-draft.md",
                         minimal=True, telemetry_token=session_id)
            + '</div>')
    parts = [title_html, body_top, voice_note]
    # 세부는 **한 겹으로 접고 세 묶음**으로만 나눈다(사용자 지시 2026-08-23:
    # 화면을 단순명료하게). 예전에는 이 안에 패널이 15개 나열돼 있었다 — 학생이
    # 보러 온 것은 산출물이지 우리 작업 내역이 아니다. 지우지 않고 묶은 이유는
    # 각 패널이 "왜 이렇게 썼나"의 근거라, 지우면 검증할 길이 함께 사라지기 때문이다.
    draft_details = [
        _detail_group(
            "점검", "제출 전에 볼 것",
            [f'<p class="meta">결정 지점 {result.draft.n_decisions}개 — 채울 수 있는 건 '
             '다 썼고, 당신 판단이 필요한 곳만 위에서 물었습니다.</p>',
             _outcome_summary_html(result, set(my_answers)),
             _readiness_html(result),
             _submission_status_html(session_id, result),
             (_review_html(review) if review is not None else
              ('<form method="post" action="/review" style="margin:.6rem 0 .1rem">'
               f'<input type="hidden" name="session" value="{html.escape(session_id)}">'
               '<button class="btn ghost" type="submit">완성도 점검 '
               '(자료 활용·빈 곳·결정)</button></form>'))]),
        _detail_group(
            "근거", "무엇을 읽고 썼나",
            [_requirement_trace_html(result),
             _sources_html(result, session_id),
             _etl_materials_html(result),
             _etl_announcements_html(result),
             _inquiry_assignment_html(result),
             _teacher_rules_html(result)]),
        _detail_group(
            "진단", "어떻게 판단했나",
            [_type_badge(result), _guard_pills(g),
             _plan_html(result), _context_summary_html(result),
             _suggested_prompts_html(result),
             # 본문은 맨 위에 이미 있다 — 여기서는 도구만(중복 게재 금지).
             _doc_tools(result.draft.body.strip(), doc_id="draftsrc",
                        filename="until-draft.md",
                        extra=_prompt_button(result) + _report_button(result))]),
        # 문단을 골라 AI에게 다시 시키는 패널은 없앴다(사용자 지시
        # 2026-08-23) — 고르고 지시하는 조작이 거추장스럽다. 고치고
        # 싶으면 아래 '내가 직접 고치기'에서 직접 고치는 편이 빠르다.
        _version_compare_html(session_id, result),
        _edit_form_html(session_id, result, simple=False),
    ]
    if result.draft.decisions:
        # 결정은 **한 곳에서만** 묻는다(사용자 지시 2026-08-23). 예전에는 같은 화면에
        # '하나씩 결정하기'(첫 결정 1개)와 '당신이 정할 것만'(전부)이 나란히 있어서
        # 같은 질문이 두 번 떴다 — 한 화면에 같은 걸 두 번 묻는 건 단순함의 반대다.
        # 부분만 답해도 되는 성질은 그대로라 '하나만 답하기'의 값어치는 유지된다.
        has_sugg = bool(sugg)
        cls = "tgbody suggested" if has_sugg else "tgbody"
        parts.append('<details class="tgsec" open>')
        parts.append('<summary><span class="tglab">＊ Decisions</span>'
                     '<h2>당신이 정할 것 <span class="hint">전부 안 채워도 됨</span></h2></summary>')
        parts.append(f'<div class="{cls}">')
        if has_sugg:
            parts.append('<div class="sugnote"><b>참고할 제안을 채워 뒀어요.</b> 한눈에 보고 '
                         '<b>그대로 수락</b>하거나 고치면 됩니다. 결정은 끝까지 당신 몫이에요.</div>')
        else:
            parts.append('<p class="meta">관심 가는 것만 칩을 누르거나 짧게 적으세요. 비워 둔 결정은 초안에 그대로 남고, '
                         '최종본에서 이어서 채울 수 있어요.</p>')
            # AI 제안 받기(별도 폼 — 같은 세션으로 /suggest 호출).
            # 지난 답 히스토리가 있으면 제안이 내 성향을 반영함을 버튼에 밝힌다.
            has_hist = False
            try:
                from .context.answer_history import suggest_from_history
                has_hist = any(suggest_from_history(d.note) for d in result.draft.decisions)
            except Exception:
                pass
            btn_label = ('참고할 답 채우기 (내 지난 답 성향 반영)' if has_hist
                         else '참고할 답 채우기')
            parts.append('<form method="post" action="/suggest" style="margin:.2rem 0 .9rem">'
                         f'<input type="hidden" name="session" value="{html.escape(session_id)}">'
                         f'<button class="btn ghost" type="submit">{btn_label}</button>'
                         '</form>')
        parts.append('<form method="post" action="/finalize">')
        parts.append(f'<input type="hidden" name="session" value="{html.escape(session_id)}">')
        for i, d in enumerate(result.draft.decisions, 1):
            s = sugg.get(i) or {}
            # 프리필 우선순위: **내가 확정한 답** > AI 제안. 재방문 시 제안이 내 답을
            # 덮으면 '전부 수락' 재제출에서 사용자가 고친 답이 조용히 되돌아간다.
            mine = (my_answers.get(i) or "").strip()
            if mine:
                parts.append(_decision_field(i, d.note, idp="a", value=mine,
                                             why="이미 반영한 내 답(수정 가능)"))
            else:
                parts.append(_decision_field(i, d.note, idp="a",
                                             value=s.get("answer", ""), why=s.get("why", "")))
        btn = ('전부 제안대로 수락 → 최종본 만들기' if has_sugg
               else '입력한 것만 반영해 최종본 만들기 →')
        parts.append(f'<p style="margin-top:.9rem"><button class="btn block" type="submit">{btn}</button></p>')
        parts.append('</form></div></details>')
    else:
        # 결정이 0개면 최종본 화면(/vf)이 비어 있다 — 거기 있던 '마지막 한 칸'
        # 버튼도 함께 사라져서, 이 과제들은 어느 경로로도 제출에 도달하지 못했다
        # (라이브 확인 2026-08-23). 초안이 곧 완성본이면 여기서 바로 넘어간다.
        parts.append('<div class="sec"><p class="meta">결정 지점이 없습니다. '
                     '초안이 곧 완성본입니다.</p>'
                     + _submit_ready_link(session_id, result) + '</div>')
    parts.extend(draft_details)
    parts.append('<p><a class="btn ghost back" href="/">← 새 과제</a></p>')
    return "\n".join(parts)


def render_final(result: Result, *, session_id: str = "", answered=None) -> str:
    fd = result.final_draft
    fg = result.final_guard
    if fd is None:
        # **막다른 화면을 만들지 않는다.** 빈 칸으로 '완성본 만들기'를 눌렀는데 AI
        # 자동 채움이 아무것도 못 만들면(제안 실패·모델 오류) 여기로 떨어지는데,
        # 예전에는 "반영할 결정 답변이 없어 초안을 그대로 둡니다" 한 줄과 '새 과제'
        # 링크뿐이었다 — 사용자는 무슨 일이 났는지도, 어디로 가야 하는지도 모른다
        # (라이브 확인 2026-08-23: '12주차 출석'에서 이 화면에 갇혔다).
        # 초안은 멀쩡히 있으므로 돌아갈 길과 제출로 가는 길을 함께 준다.
        back = (f'<a class="btn" href="/v/{html.escape(session_id)}">← 초안으로 돌아가 답하기</a>'
                if session_id else "")
        ready = (f'<a class="btn ghost" href="/ready/{html.escape(session_id)}">'
                 '초안 그대로 제출 준비하기</a>' if session_id else "")
        return ('<div class="sec"><h2>최종 완성본</h2>'
                '<p class="meta">아직 반영할 답이 없어 초안을 그대로 뒀어요. '
                '한 곳만 답하면 완성본이 만들어집니다 — 자동 채움이 제안을 못 만든 '
                '경우에도 직접 한 줄 적으면 됩니다.</p>'
                f'<p class="row">{back}{ready}</p>'
                '<p><a class="btn ghost back" href="/">← 새 과제</a></p></div>')
    answered = answered or set()
    # 결정 진행률 — 원본 초안의 결정 중 몇 개를 답했는지(경계선 여정의 진행 표시).
    total = len(result.draft.decisions)
    done = min(len(answered), total)
    if total:
        pct = int(done / total * 100)
        progress = (f'<div class="meta" style="margin:.3rem 0 .55rem">결정 진행 <b>{done}/{total}</b>'
                    f'<span style="display:inline-block;width:9rem;height:3px;background:var(--line);'
                    f'margin-left:.6rem;vertical-align:middle">'
                    f'<span style="display:block;width:{pct}%;height:100%;background:var(--accent)"></span>'
                    f'</span></div>')
    else:
        progress = ""
    # 과제 제목 — 초안 페이지와 동일한 헤더(오리엔테이션 일관성).
    _title = _assignment_heading(result)
    title_html = (f'<p class="meta" style="margin:.1rem 0 .7rem">📄 {html.escape(_title)}</p>'
                  if _title else "")
    # 초안 화면과 같은 순서 규칙(사용자 지시 2026-08-23): **결과물이 맨 위**,
    # 그다음 제출에 필요한 것, 나머지 근거·진단은 한 겹 접어 세 묶음으로.
    # 예전에는 요약·점검·규칙·자료가 본문보다 위에 있어서, 완성본을 보러 온
    # 사람이 우리 작업 내역을 네 번 지나야 자기 글을 만났다.
    #
    # 자동 채움 고지와 제출 점검은 **접지 않는다** — 접으면 안 보고 낸다.
    # (불변규칙 1: 대신 채운 사실을 화면에 반드시 밝힌다.)
    parts = [
        title_html,
        '<div class="sec">',
        '<div class="lab"><span class="n">＊</span> / FINAL <span class="ln"></span></div>',
        '<h2>최종 완성본 <span class="meta">· 결정 반영</span></h2>',
        _anchored_draft_html(fd.body.strip(), result.source_docs, final=True),
        _doc_tools(fd.body.strip(), doc_id="finalsrc", filename="until-final.md",
                   extra=_prompt_button(result),
                   telemetry_token=session_id),
        _autofilled_notice_html(session_id, result),
        _readiness_html(result),
        _submit_ready_link(session_id, result),
        _submission_preview_html(session_id, result),
        _submission_status_html(session_id, result),
        '</div>',
        _detail_group(
            "근거", "무엇을 읽고 썼나",
            [_sources_html(result, session_id), _teacher_rules_html(result),
             _outcome_summary_html(result, answered)]),
        _detail_group(
            "변경", "초안에서 무엇이 달라졌나",
            [_diff_html(result), _version_compare_html(session_id, result)]),
        _detail_group(
            "진단", "어떻게 판단했나",
            [_guard_pills(fg) if fg else "", progress,
             f'<p class="meta">남은(미답) 결정 {fd.n_decisions}개</p>']),
        # 문단을 골라 AI에게 다시 시키는 패널은 없앴다(사용자 지시
        # 2026-08-23) — 고르고 지시하는 조작이 거추장스럽다. 고치고
        # 싶으면 아래 '내가 직접 고치기'에서 직접 고치는 편이 빠르다.
        _edit_form_html(session_id, result, simple=False),
    ]
    # 재답변 루프: 원본 초안에서 아직 답 안 한 결정을 폼으로 띄워 이어서 채우게 한다.
    remaining = [(i, d.note) for i, d in enumerate(result.draft.decisions, 1) if i not in answered]
    if remaining and session_id:
        parts.append('<div class="sec"><div class="lab"><span class="n">＊</span> / DECISIONS <span class="ln"></span></div>')
        parts.append('<h2>남은 결정 이어서 <span class="hint">원하는 것만</span></h2>')
        parts.append('<p class="meta">채운 것만 반영해 최종본을 다시 만듭니다(이전 답변은 유지).</p>')
        parts.append('<form method="post" action="/finalize">')
        parts.append(f'<input type="hidden" name="session" value="{html.escape(session_id)}">')
        i, note = remaining[0]
        parts.append(_decision_field(i, note, idp="r"))
        parts.append('<p class="meta">나머지는 한 번에 하나씩 이어서 보여드려요.</p>')
        parts.append('<p style="margin-top:.9rem"><button class="btn block" type="submit">반영하고 다음 결정 →</button></p>')
        parts.append('</form></div>')
        parts.append('<p><a class="btn ghost back" href="/">← 새 과제</a></p>')
    return "\n".join(parts)


def revise_session(token: str, cfg: Config, *, mode: str, paragraph: int = 0,
                   instruction: str = "", excluded_sources=None) -> Result:
    """Apply a recoverable focused revision to one persisted web session."""
    res = _get_session(token)
    if res is None:
        raise ValueError("session_not_found")
    workspace = _WORKSPACES.setdefault(token, {"excluded_sources": [], "versions": []})
    versions = workspace.setdefault("versions", [])
    if mode == "restore":
        if versions:
            from .boundary.models import Draft
            res.draft = Draft.from_text(versions.pop())
            res.final_draft = None; res.final_guard = None
            _SESSIONS[token] = res; _persist_session(token)
        return res
    if mode == "sources":
        workspace["excluded_sources"] = sorted({int(x) for x in (excluded_sources or [])})
        instruction = ("사용자가 제외한 자료를 근거와 인용에서 제거하고, 영향받는 문단만 "
                       "남은 자료로 다시 작성하라. 근거가 부족해지면 DECISION으로 남겨라.")
    elif mode == "paragraph":
        if paragraph < 1 or not instruction.strip():
            raise ValueError("revision_input_required")
        instruction = f"{paragraph}번째 문단만 다음 지시대로 수정: {instruction.strip()}"
    else:
        raise ValueError("invalid_revision_mode")
    current = res.final_draft or res.draft
    versions.append(current.body)
    del versions[:-5]
    from .execution.revise import revise_draft
    from .llm.base import build_client
    from .llm.meter import MeteredClient, new_usage
    usage = res.llm_usage if isinstance(res.llm_usage, dict) else new_usage()
    llm = MeteredClient(build_client(cfg.backend, cfg.model), usage)
    revised, guard = revise_draft(
        current, res.spec, instruction, llm, source_docs=res.source_docs,
        excluded=set(workspace.get("excluded_sources", [])), max_reasks=cfg.max_reasks,
        # run()이 확정한 톤 규격 재사용(재계산 금지) — 없으면 기존 동작 그대로.
        voice_hint=str(getattr(res, "tone_block", "") or ""))
    # 수정 diff 캡처 — before/after가 이미 손에 있고, 지금까지 버려지던 사용자
    # 수정 지시(instruction)도 함께 남긴다. edit_source='llm_revise'로 표시해
    # 사람이 직접 고친 신호와 섞이지 않게 한다(가중치는 학습 쪽에서 구분).
    try:
        from .context.edit_events import record_edit_event
        record_edit_event(current.body, revised.body, edit_source="llm_revise",
                          instruction=instruction,
                          register_key=str(getattr(res, "tone_register", "") or ""),
                          task_type=str((res.spec or {}).get("task_type") or ""))
    except Exception:
        pass
    res.draft = revised; res.guard = guard
    res.final_draft = None; res.final_guard = None; res.llm_usage = usage
    _SESSIONS[token] = res
    meta = _TELEMETRY_META.get(token)
    if meta is not None:
        meta["revision_count"] = int(meta.get("revision_count") or 0) + 1
    _persist_session(token)
    return res


def demo_assignment_text() -> str:
    """볼륨형 샘플 과제 — `/simple?demo=1` 프리필용(`until/demo_showcase.py` 픽스처)."""
    from .demo_showcase import demo_assignment_text as _t
    return _t()


#: 과제 직접 등록(`/new`) 폼의 칸 — (키, 라벨, 예시).
_NEW_FIELDS = (
    ("course",  "과목",      "예: 재료공학개론"),
    ("title",   "과제명",    "예: 3주차 보고서"),
    ("due",     "마감",      "예: 2026-09-05 23:59"),
    ("fmt",     "제출 형식", "예: .docx 파일 1개"),
    ("length",  "분량",      "예: 1500자 이상 / A4 3장"),
)


def compose_assignment(form: Dict[str, list]) -> str:
    """과제 만들기 폼(구조화 칸) → 과제 텍스트. 채운 칸만 줄로 남긴다.

    붙여넣기와 완전히 같은 입력 형태로 떨어뜨리는 게 핵심이다 — 새 경로가
    파이프라인에 특수 분기를 만들지 않는다."""
    def val(name: str) -> str:
        return (form.get(name, [""])[0] or "").strip()

    lines = []
    for key, label, _ph in _NEW_FIELDS:
        v = val(key)
        if v:
            lines.append(f"[{label}] {v}")
    req = val("req")
    if req:
        lines.append("")
        lines.append("[요구사항]")
        lines.extend(f"- {ln.strip()}" for ln in req.splitlines() if ln.strip())
    body = val("body")
    if body:
        lines.append("")
        lines.append("[과제 설명]")
        lines.append(body)
    return "\n".join(lines).strip()


def render_new_assignment(err: str = "", form: Dict[str, list] | None = None) -> str:
    """과제 만들기 화면 — 칸을 채우면 과제 명세가 된다(eTL 불필요)."""
    form = form or {}

    def keep(name: str) -> str:
        return html.escape((form.get(name, [""])[0] or "").strip())

    rows = []
    for key, label, ph in _NEW_FIELDS:
        rows.append(
            f'<label class="nf" for="nf-{key}">{html.escape(label)}'
            f'<input id="nf-{key}" name="{key}" placeholder="{html.escape(ph)}" '
            f'value="{keep(key)}"></label>')
    warn = (f'<p class="meta" style="color:var(--warn)">{html.escape(err)}</p>'
            if err else "")
    return f"""
<div class="utility-page utility-form">
 <header class="page-head"><p class="page-kicker">과제 직접 등록</p>
  <h1>과제 정보를 알려주세요</h1>
  <p class="page-lead">eTL에 없는 구두 공지·팀플·스터디도 시작할 수 있습니다.
   과제 설명만 필수이고, 모르는 칸은 비워 두면 됩니다.</p>
 </header>
 {warn}
 <form class="task-form" method="post" action="/draft" enctype="multipart/form-data">
  <input type="hidden" name="ui" value="simple">
  <input type="hidden" name="mode" value="new">
  <section class="task-form-section task-form-primary"><div class="form-section-head">
   <h2>과제 설명</h2><span>필수</span></div>
   <label class="sr-only" for="nf-body">과제 설명</label>
   <textarea id="nf-body" name="body" rows="9"
    placeholder="교수님이 공지한 내용을 그대로 옮겨 적거나 붙여넣으세요">{keep("body")}</textarea>
  </section>
  <section class="task-form-section"><div class="form-section-head">
   <h2>알고 있는 정보</h2><span>선택</span></div>
   <div class="nfgrid">{"".join(rows)}</div>
   <label class="nf" for="nf-req">요구사항 <span class="hint">한 줄에 하나</span>
    <textarea id="nf-req" name="req" rows="3"
     placeholder="예: 서론·본론·결론 구성&#10;예: 수업 자료 3편 이상 인용">{keep("req")}</textarea></label>
  </section>
  <section class="task-form-section"><div class="form-section-head">
   <h2>자료와 내 문체</h2><span>선택</span></div>
   <div class="upload-options"><label class="upload-row"><span><b>과제 자료</b>
    <small>PDF·DOCX·HWPX·PPTX·TXT, 최대 5개</small></span>
    <input type="file" name="files" multiple accept=".pdf,.docx,.pptx,.hwpx,.html,.htm,.txt,.md"></label>
   <label class="upload-row"><span><b>내가 쓴 글</b>
    <small>초안의 문체를 맞추는 데만 사용합니다</small></span>
    <input type="file" name="voice_files" multiple accept=".txt,.md,.docx,.hwpx,.pdf"></label></div>
  </section>
  <button class="btn block big" type="submit">이 과제로 초안 만들기</button>
 </form>
 <p class="page-links"><a href="/simple">통째로 붙여넣기</a><a href="/">eTL에서 가져오기</a></p>
</div>"""


# 간단 모드에서 처음 화면에 펴 두는 결정 수. 나머지는 접어 둔다(같은 폼 안).
# 근거: 3회차 멘토링 — "UI 단순화: 질문 4개만 답변하는 것부터 시작".
SIMPLE_FIRST_N = 4


def render_simple_index(prefill: str = "") -> str:
    """간단 모드 홈 — 붙여넣기 한 칸, 버튼 하나. 글자 최소.

    prefill: 데모 과제 체험(/simple?demo=1) 등에서 textarea를 미리 채운다."""
    demo_link = ('' if prefill else
                 '<p class="page-shortcuts">'
                 '<a href="/simple?demo=1">샘플 과제로 체험하기</a></p>')
    return f"""
<div class="utility-page utility-form">
 <header class="page-head"><p class="page-kicker">직접 붙여넣기</p>
  <h1>과제 내용을 그대로 붙여넣으세요</h1>
  <p class="page-lead">과제 공지 전체를 넣으면 요구사항·분량·마감을 읽고 초안을 만듭니다.</p>
 </header>
 {demo_link}
 <form class="task-form" method="post" action="/draft" enctype="multipart/form-data">
  <input type="hidden" name="ui" value="simple">
  <section class="task-form-section task-form-primary"><div class="form-section-head">
   <h2>과제 공지</h2><span>필수</span></div>
   <label class="sr-only" for="simple-assignment">과제 공지</label>
   <textarea id="simple-assignment" name="assignment" rows="12"
    placeholder="과제 공지나 지시사항을 여기에 붙여넣으세요">{html.escape(prefill)}</textarea>
  </section>
  <section class="task-form-section"><div class="form-section-head">
   <h2>자료와 내 문체</h2><span>선택</span></div>
   <div class="upload-options"><label class="upload-row"><span><b>내 자료 첨부</b>
    <small>PDF·DOCX·HWPX·PPTX·TXT, 최대 5개</small></span>
    <input type="file" name="files" multiple accept=".pdf,.docx,.pptx,.hwpx,.html,.htm,.txt,.md"></label>
   <label class="upload-row"><span><b>내가 쓴 글</b>
    <small>올리면 초안이 더 자연스럽고 내 글처럼 나옵니다</small></span>
    <input type="file" name="voice_files" multiple accept=".txt,.md,.docx,.hwpx,.pdf"></label></div>
  </section>
  <button class="btn block big" type="submit">초안 만들기</button>
 </form>
 <p class="page-links"><a href="/new">칸 채워서 등록하기</a><a href="/">eTL에서 가져오기</a></p>
</div>"""


def _simple_decisions_form(session_id: str, decisions, *, action_label: str,
                           prefill: dict | None = None) -> str:
    """간단 모드 결정 폼 — 질문+한 칸. 최소 유지(칩은 '내 지난 답' 하나만, 있을 때).

    처음 화면에는 **앞의 SIMPLE_FIRST_N개만** 편다. 결정이 그보다 많으면 나머지는
    접어 둔다 — 질문이 여섯 개씩 쏟아지면 '딸깍하러 온 사람'이 그 자리에서
    이탈한다(3회차 멘토링: 질문 4개만 답하는 데서 시작). 접힌 항목도 같은 폼
    안에 있어 펴서 채우면 그대로 함께 제출된다.

    prefill: {번호: 값} — 자세히 모드에서 확정한 내 답/수락한 제안이 모드 전환으로
    사라져 보이지 않으면 '완성하기' 헛제출이 된다."""
    prefill = prefill or {}
    head = ['<form method="post" action="/finalize">',
            f'<input type="hidden" name="session" value="{html.escape(session_id)}">',
            '<input type="hidden" name="ui" value="simple">']
    rows: list = []
    for i, note in decisions:
        # 지난 답 재사용(결정적·로컬) — 돌아온 사용자는 한 번 눌러 답을 채운다.
        # 자동 채움 아님(경계선: 확정은 사람 클릭).
        chip = ""
        try:
            from .context.answer_history import suggest_from_history
            h = suggest_from_history(note)
        except Exception:
            h = None
        if h:
            label = h.answer if len(h.answer) <= 32 else h.answer[:31] + "…"
            chip = (f'<div class="chips"><button type="button" class="chip" '
                    f'data-val="{html.escape(h.answer)}" onclick="pick(\'s{i}\',this)">'
                    f'지난 답 · {html.escape(label)}</button></div>')
        val = html.escape((prefill.get(i) or "").strip())
        rows.append(f'<div class="sd"><label for="s{i}">{html.escape(note)}</label>'
                    f'{chip}'
                    f'<textarea id="s{i}" name="answer_{i}" rows="1" '
                    f'placeholder="비워도 됩니다">{val}</textarea></div>')
    first, rest = rows[:SIMPLE_FIRST_N], rows[SIMPLE_FIRST_N:]
    body = list(first)
    if rest:
        body.append('<details class="more-sd"><summary>'
                    f'나머지 {len(rest)}개 더 정하기 — 비워도 완성됩니다</summary>'
                    + "\n".join(rest) + '</details>')
    body.append(f'<button class="btn block big" type="submit">{action_label}</button>')
    # 전부 답하지 않아도 막히지 않게 — 같은 폼을 formaction으로 /suggest에 보내
    # 지금 타이핑한 답 + 내 지난 내역·수업 자료·말투로 **빈칸만** 채워 돌아온다.
    # 채워진 값은 '수락 대기 제안'이라 그대로 고쳐 쓸 수 있고, 확정은 여전히
    # '완성하기' 클릭이다(경계선: 대신 확정하지 않는다).
    body.append('<button class="btn block ghost" type="submit" formaction="/suggest"'
                ' onclick="fillmsg(this.form)" style="margin-top:.5rem">'
                '나머지는 나에 맞춰 채워줘</button>'
                '<p class="meta" style="margin:.45rem 0 0;font-size:.78rem">'
                '지금 채운 답과 내 지난 답·수업 자료·내 말투를 참고해 빈칸만 채웁니다. '
                '채운 뒤에도 고쳐 쓸 수 있어요.</p></form>')
    return "\n".join(head + body)


def _simple_readiness_line(result: Result, detail_href: str) -> str:
    """간단 모드용 준비 점검 한 줄 — 경고가 있을 때만(글자 최소 철학 유지)."""
    try:
        from .readiness import assess_readiness
        warns = [it for it in assess_readiness(result).items if it.status in ("warn", "fail")]
    except Exception:
        return ""
    if not warns:
        return ""
    more = f" 외 {len(warns) - 1}건" if len(warns) > 1 else ""
    return (f'<p class="meta" style="color:var(--warn);text-align:center">'
            f'⚠ {html.escape(warns[0].label)} · {html.escape(warns[0].message)}{more} — '
            f'<a href="{detail_href}">자세히</a></p>')


def _simple_prefill(session_id: str, n: int) -> dict:
    """간단 모드 프리필 — 내 답 > 수락 대기 AI 제안(자세히 모드와 동일 우선순위)."""
    out = {}
    sugg = _SUGGESTIONS.get(session_id) or {}
    mine = _ANSWERS.get(session_id) or {}
    for i in range(1, n + 1):
        v = (mine.get(i) or "").strip() or ((sugg.get(i) or {}).get("answer") or "").strip()
        if v:
            out[i] = v
    return out


def _simple_head(result: Result, right_fallback: str = "") -> str:
    """간단 화면 레터헤드 — 과목(왼쪽) · D-day(오른쪽) + 과제명 한 줄.

    딸깍(fast)이 과제를 자동 선택하므로, 무엇을 골라 해결하는지가 화면의
    첫 정보여야 한다(사용자 지시)."""
    _spec = result.spec or {}
    _title = str(_spec.get("title") or _spec.get("deliverable")
                 or _spec.get("goal") or "").strip()
    _course = str(_spec.get("course") or "").strip()
    dday = ""
    dl = getattr(result, "deadline", None)
    if dl is not None:
        from datetime import date as _date
        try:
            dday = dl.dday_label(_date.today())
        except Exception:
            dday = ""
    if not (_title or _course or dday):
        return ""
    cls = "d" if dday else "d n"
    right = html.escape(dday or right_fallback)
    head = (f'<div class="smp-head"><span class="t">'
            f'{html.escape(_course[:60] or _title[:60] or "UNTIL")}</span>'
            f'<span class="{cls}">{right}</span></div>')
    name = (f'<p class="smp-name">{html.escape(_title[:90])}</p>' if _title else "")
    return head + name


def _remain_line(n: int, prefilled: bool) -> str:
    """남은 결정 안내 — 개수·끝나는 시점·프리필 상태를 한 문장으로.

    결정이 SIMPLE_FIRST_N개를 넘으면 '먼저 4개'라고 말한다 — 화면에 펴 놓은
    개수와 문구가 어긋나면 '네 가지만 정하면 된다더니'가 된다."""
    shown = min(n, SIMPLE_FIRST_N)
    kor = {1: "하나", 2: "두 가지", 3: "세 가지", 4: "네 가지",
           5: "다섯 가지"}.get(shown, f"{shown}가지")
    t = (f"이 {kor}만 정하면 완성됩니다" if n <= SIMPLE_FIRST_N
         else f"먼저 이 {kor}만 정하면 됩니다")
    s = "추천이 채워져 있어요 — 고쳐 써도 됩니다" if prefilled else "비워둔 항목은 표시로 남습니다"
    return f'<div class="remain"><span class="t">{t}</span><span class="s">{s}</span></div>'


def _simple_draft_peek(result: Result) -> str:
    """지금까지 쓴 초안을 **접힌 채** 얹는다 — 읽기 전용.

    초안 본문이 편집 폼(`＊ Revise`·'내가 직접 고치기') 안에만 있어서, 결과물을
    읽기만 하려는 사용자가 갈 곳이 없었다. 완성본은 본문을 그대로 앞면에 보여
    주는데 초안은 숨기니 정보 구조가 뒤집혀 있었다 — 신뢰 판정이 필요한 쪽이
    초안이다(2026-08-22 실사용 원장 F9~F11).

    기본은 접어 둔다. 이 화면의 목적은 '이 하나만 정하면 완성됩니다'이고,
    본문을 펼쳐 두면 그 단순함이 사라진다. 펼침은 사용자가 고른다.
    """
    body = (result.draft.body or "").strip()
    if not body:
        return ""
    return ('<details class="draft-peek" style="margin:.6rem 0">'
            '<summary class="meta">지금까지 쓴 초안 보기 — 아래 질문은 '
            '본문에 자리표시로 남아 있어요</summary>'
            f'<div class="doc">{_highlight_markers(body, result.source_docs)}</div>'
            '</details>')


def render_simple_draft(session_id: str, result: Result) -> str:
    """간단 모드 결정 화면 — 질문과 완성 버튼이 먼저, 초안 본문은 접어서 함께."""
    tok = html.escape(session_id)
    parts = ['<div class="smp">',
             _simple_head(result, right_fallback="질문"),
             _simple_draft_peek(result)]
    if result.draft.decisions:
        prefill = _simple_prefill(session_id, len(result.draft.decisions))
        mine = _ANSWERS.get(session_id) or {}
        # AI 제안이 미리 채워진 상태임을 밝힌다 — 자동 확정이 아니라 검토 대상.
        parts.append(_remain_line(len(result.draft.decisions),
                                  any(i not in mine for i in prefill)))
        parts.append(_simple_decisions_form(
            session_id, [(i, d.note) for i, d in enumerate(result.draft.decisions, 1)],
            action_label='완성하기', prefill=prefill))
    else:
        parts.append('<div class="remain"><span class="t">직접 정할 질문이 없습니다</span>'
                     '<span class="s">초안이 바로 준비됐습니다</span></div>')
    parts.append(f'<p class="smp-x"><span><a href="/simple">새 과제</a></span>'
                 f'<span><a href="/v/{tok}">초안·근거 자세히 보기</a></span></p></div>')
    return "\n".join(parts)


def _saved_state_html(session_id: str) -> str:
    """완성 화면 하단의 '저장' 줄 — 퍼널의 다음 칸을 한 줄로 가리킨다.

      로그인 전: 이 과제를 잃지 않으려면 계정 로그인 — 지금 만든 초안은 로그인
                 순간 계정으로 넘어간다(_adopt_anon_data).
      로그인 후: 저장 완료를 확인시키고 다음 칸(eTL 연결)으로 보낸다.
    """
    from . import google_auth as _ga
    if not (CLOUD and _ga.any_enabled()):
        return ""
    tok = html.escape(session_id or "")
    if _auth_user() is None:
        nxt = _urlquote(f"/svf/{session_id}" if session_id else "/", safe="")
        return ('<div class="saved"><span>이 과제를 <b>저장</b>할까요? 로그인하면 '
                '다음에 접속해도 그대로 열려요.</span>'
                f'<a class="btn ghost" href="/login?next={nxt}">로그인 →</a></div>')
    nxt_step = ('<a class="btn ghost" href="/">eTL 연결하기 →</a>'
                if not _get_canvas_token(tok, uid=_uid()) and not _env_canvas_token()
                else '<a href="/sessions">이전 작업 보기</a>')
    return ('<div class="saved"><span><b>✓ 내 계정에 저장됐어요.</b> '
            '노트북을 바꿔도 그대로 열립니다.</span>' + nxt_step + '</div>')


def render_simple_final(result: Result, *, session_id: str = "", answered=None) -> str:
    """간단 모드 완성본 — 문서 + 복사/저장 + (남은 답만) 폼."""
    tok = html.escape(session_id)
    fd = result.final_draft
    if fd is None:
        # 막다른 페이지 방지 — 답이 없어도 초안 기반 제출용 파일은 받을 수 있다
        # (결정 자리는 【직접 정할 것】 자리표시로 보존, 대신 채우지 않음).
        dl = _submission_links(session_id, result)
        return ('<div class="smp"><p class="smp-step">완성</p>'
                '<p class="meta" style="text-align:center">아직 반영한 답이 없어 초안 '
                '그대로예요. 이대로도 제출용 파일을 받을 수 있고, 답을 채우면 '
                '완성본이 돼요.</p>'
                + (f'<div class="row" style="justify-content:center;margin:.7rem 0">{dl}</div>'
                   if dl else '')
                + _saved_state_html(session_id)
                + f'<p class="smp-x"><a href="/sv/{tok}">← 초안으로 (답 채우기)</a></p></div>')
    answered = answered or set()
    dl = _submission_links(session_id, result)
    parts = ['<div class="smp">',
             _simple_head(result, right_fallback="완성"),
             f'<div class="doc final">{_highlight_markers(fd.body.strip(), result.source_docs)}</div>',
             _doc_tools(fd.body.strip(), doc_id="finalsrc", filename="until-final.md",
                        minimal=True, extra=_prompt_button(result),
                        telemetry_token=session_id),
             # 제출 파일까지가 딸깍의 끝 — 간단 완성본에서 바로 받는다.
             (f'<div class="row" style="justify-content:center;margin:.4rem 0 0">{dl}</div>'
              if dl else ''),
             '<p class="meta" style="text-align:center;margin:.3rem 0 0">'
             '복사해서 쓰던 문서에 붙여넣거나, 쓰는 AI 채팅에 이어서 물어보려면 '
             '프롬프트로 복사를 쓰세요.</p>',
             _simple_readiness_line(result, f"/vf/{tok}"),
             _autofilled_notice_html(session_id, result),
             # 마지막 한 칸 — 점검·올릴 파일·eTL 링크·완료 표시를 한 화면에.
             _submit_ready_link(session_id, result),
             _saved_state_html(session_id)]
    remaining = [(i, d.note) for i, d in enumerate(result.draft.decisions, 1)
                 if i not in answered]
    if remaining and session_id:
        parts.append('<div class="remain"><span class="t">아직 정하지 않은 항목</span>'
                     '<span class="s">답하면 다시 반영해요</span></div>')
        parts.append(_simple_decisions_form(
            session_id, remaining, action_label='다시 만들기 →',
            prefill=_simple_prefill(session_id, len(result.draft.decisions))))
    parts.append(f'<p class="smp-x"><span><a href="/simple">새 과제</a></span>'
                 f'<span><a href="/vf/{tok}">자세히 보기</a></span></p></div>')
    return "\n".join(parts)


# 완성본 만족도(1~5) — 세션당 1회, 피드백 로그(P7)에 적립(베타 학습 루프의 핵심 지표).
_RATINGS: Dict[str, int] = {}
_VOICE_RATINGS: Dict[str, bool] = {}
_VOICE_RATING_LOCK = threading.Lock()


def _rating_html(session_id: str, rated: bool, simple: bool = False) -> str:
    """완성본 하단 별점 행 — 이미 평가했으면 감사 문구만."""
    if not session_id:
        return ""
    if rated:
        return ('<p class="meta" style="text-align:center;margin:1.1rem 0">'
                '피드백 고마워요 — 다음 초안을 더 낫게 만드는 데 씁니다 ✓</p>')
    tok = html.escape(session_id)
    ui = '<input type="hidden" name="ui" value="simple">' if simple else ""
    btns = "".join(
        f'<button class="btn ghost" name="score" value="{i}" '
        f'style="min-width:2.6rem;padding:.3rem .45rem;margin:0 .12rem">★{i}</button>'
        for i in range(1, 6))
    return (f'<form method="post" action="/rate" '
            f'style="text-align:center;margin:1.1rem 0">'
            f'<input type="hidden" name="session" value="{tok}">{ui}'
            f'<span class="meta" style="margin-right:.5rem">이 결과, 어땠어요?</span>'
            f'{btns}</form>')


def _voice_applied(result: Result) -> bool:
    return getattr(result, "voice_applied", False) is True


def _voice_csrf(token: str) -> str:
    """세션 서명 키 기반 CSRF 토큰 — 원문/uid를 포함하지 않고 재시작에도 안정적."""
    import hashlib, hmac
    from .session_store import session_key
    return hmac.new(session_key(), ("voice-rating:" + token).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def _voice_rating_html(session_id: str, result: Result, rated: bool,
                       simple: bool = False) -> str:
    """실제로 VoiceProfile이 Execution에 적용된 완성본에만 표시."""
    if not session_id or not _voice_applied(result):
        return ""
    if rated:
        return '<p class="meta" style="text-align:center">말투 피드백 고마워요 ✓</p>'
    tok = html.escape(session_id)
    csrf = html.escape(_voice_csrf(session_id))
    ui = '<input type="hidden" name="ui" value="simple">' if simple else ""
    return (f'<form method="post" action="/rate/voice" style="text-align:center;margin:.5rem 0">'
            f'<input type="hidden" name="session" value="{tok}">'
            f'<input type="hidden" name="csrf" value="{csrf}">{ui}'
            '<span class="meta" style="margin-right:.5rem">내 말투 같아요?</span>'
            '<button class="btn ghost" name="match" value="yes">예</button> '
            '<button class="btn ghost" name="match" value="no">아니오</button></form>')


def record_voice_rating(token: str, result: Result, match: bool, *, backend: str,
                        uid: str = "") -> None:
    """세션당 1회, bool/yes-no 외 값과 문체·자료 원문은 기록하지 않는다."""
    with _VOICE_RATING_LOCK:
        if token in _VOICE_RATINGS:
            return
        _VOICE_RATINGS[token] = match
        try:
            from .feedback import append_record, record_from_result
            record = record_from_result(result, voice_match=match,
                                        backend=f"{backend}+voice-rated")
            # 이 평가는 품질 신호만 필요하다. 과제·자료·문체 표본은 별도 저장하지 않는다.
            record.assignment, record.spec, record.sources = "과제", "{}", ""
            append_record(record)
        except OSError:
            pass
        meta = _TELEMETRY_META.setdefault(token, {})
        meta["voice_match"] = "yes" if match else "no"
        _telemetry_emit("review", token, result, uid=uid)
        _persist_session(token)


def _answers_from_form(form: Dict[str, list], n: int) -> Dict[int, str]:
    out: Dict[int, str] = {}
    for i in range(1, n + 1):
        vals = form.get(f"answer_{i}")
        if vals and vals[0].strip():
            out[i] = vals[0].strip()
    return out


def _maybe_run_code_check(res: Result) -> None:
    """코드 과제면 별도 러너에 테스트 실행을 맡기고 결과를 세션에 붙인다.

    **웹 프로세스는 코드를 실행하지 않는다.** 여기서는 HTTP로 부르기만 한다 —
    세션·eTL 토큰·결제 원장이 같은 주소공간에 있는 곳에서 LLM이 쓴 코드를 돌릴
    이유가 없다.

    러너가 없으면(기본) 아무 일도 하지 않는다. 실패도 비치명 — 사용자는 초안을
    받고 제출할 수 있어야 하고, 점검 항목 하나가 '못 돌림'으로 뜰 뿐이다.
    """
    try:
        from .runner import assemble, client
        if not client.configured():
            return
        prepared = assemble.files_for(res)
        if prepared is None:
            return
        job, files = prepared
        res.run_check = client.run(job, files)
    except Exception:
        pass


def autofill_on() -> bool:
    """빈 결정 칸을 AI가 대신 채울지. 기본 켬, `UNTIL_AUTOFILL_DECISIONS=0`으로 끔."""
    return (os.getenv("UNTIL_AUTOFILL_DECISIONS", "1") or "1").strip() != "0"


#: 자동 채움 — 한 호출에 물을 문항 수와 총 호출 예산.
#: 한 번에 아홉 문항을 얹으면 모델이 조용히 몇 개를 빠뜨린다(실사용 9개 중 1개).
#: 첫 라운드는 3개씩 묶어 싸게 훑고, 그래도 남으면 **한 개씩** 다시 묻는다 —
#: 한 문항만 물으면 빠뜨릴 여지가 없다. 예산은 폭주 방지용 상한이다.
_AUTOFILL_CHUNK = 3
_AUTOFILL_MAX_CALLS = 8


def _fill_blank_decisions(token: str, res: Result, answers: Dict[int, str],
                          cfg) -> tuple[Dict[int, str], list]:
    """비워 둔 결정 칸을 AI 제안으로 채운다. (채운 답변, 채운 번호 목록) 반환.

    사용자 지시(2026-08-20): "질문에 답 안 하고 그냥 제출하면 AI가 알아서 채우게."
    그 전까지는 빈 칸이 제출 문서에 `【직접 정할 것 N: ...】` 자리표시로 남았다.

    이미 만들어 둔 제안(`_SUGGESTIONS`)을 먼저 쓰고, 그래도 빈 번호만 LLM을
    1회 더 부른다 — 딸깍 경로는 초안 단계에서 이미 제안을 만들어 두므로 대개
    추가 호출이 없다. 내가 직접 쓴 칸은 **절대 덮지 않는다.**

    실패는 비치명적이다. 제안을 못 만들면 예전처럼 빈 칸으로 두고 진행한다 —
    여기서 멈추면 사용자는 '완성하기'가 그냥 안 되는 것으로 겪는다.
    """
    n = res.draft.n_decisions
    blanks = [i for i in range(1, n + 1) if not (answers.get(i) or "").strip()]
    if not blanks or not autofill_on():
        return answers, []
    filled = dict(answers)
    ready = _SUGGESTIONS.get(token) or {}
    remaining = []
    for i in blanks:
        text = str((ready.get(i) or {}).get("answer") or "").strip()
        if text:
            filled[i] = text
        else:
            remaining.append(i)
    # 한 번만 물으면 모자란다. 실사용(산업공학개론 Term Project, 결정 9개)에서
    # 모델이 **9개 중 1개만** 돌려줘 여덟 칸이 빈 채로 "1/9 진행"이 떴다.
    # 한 호출에 아홉 문항을 얹으면 모델이 조용히 몇 개를 빠뜨린다.
    #
    # 그래서 ①작게 나눠 묻고(_AUTOFILL_CHUNK) ②남은 것만 다시 묻는다. 진전이
    # 없으면 즉시 멈춘다 — 못 채우는 칸을 무한히 조르지 않는다(지시가 아니라
    # 루프로 잡는다: 인용 보존·메타 차단과 같은 계보).
    merged_sugg = dict(ready)
    calls = 0
    size = _AUTOFILL_CHUNK
    while remaining and calls < _AUTOFILL_MAX_CALLS:
        progressed = False
        for start in range(0, len(remaining), size):
            if calls >= _AUTOFILL_MAX_CALLS:
                break
            chunk = remaining[start:start + size]
            calls += 1
            try:
                fresh = suggest_decision_answers(res, cfg,
                                                 my_answers=filled or None,
                                                 only=chunk)
            except Exception:
                fresh = {}
            merged_sugg.update(fresh)
            for i in chunk:
                text = str((fresh.get(i) or {}).get("answer") or "").strip()
                if text:
                    filled[i] = text
                    progressed = True
        remaining = [i for i in remaining if not (filled.get(i) or "").strip()]
        if not progressed:
            break      # 이번 바퀴에 하나도 못 채웠다 — 더 물어도 같다
        size = 1       # 남은 건 한 개씩 — 한 문항만 물으면 빠뜨릴 여지가 없다
    _SUGGESTIONS[token] = merged_sugg
    done = sorted(i for i in blanks if (filled.get(i) or "").strip())
    if done:
        _AUTOFILLED[token] = sorted(set(_AUTOFILLED.get(token) or []) | set(done))
    return filled, done


def _autofilled_notice_html(session_id: str, result: Result) -> str:
    """AI가 대신 정한 칸을 화면에 그대로 밝힌다(무엇을 정했는지까지).

    자동으로 채워 주는 것과 채운 사실을 숨기는 것은 다르다. 학생이 제출 전에
    '이건 내가 안 정했다'를 알아야 고칠 기회가 생긴다."""
    indices = _AUTOFILLED.get(session_id) or []
    if not indices:
        return ""
    answers = _ANSWERS.get(session_id) or {}
    decisions = list(getattr(result.draft, "decisions", ()) or ())
    rows = []
    for i in indices:
        if not 1 <= i <= len(decisions):
            continue
        note = str(getattr(decisions[i - 1], "note", "") or "").strip()
        rows.append(f'<li><b>{html.escape(note[:90])}</b><br>'
                    f'<span class="meta">→ {html.escape((answers.get(i) or "")[:160])}</span></li>')
    if not rows:
        return ""
    return ('<div class="matbox"><b>AI가 대신 정한 곳 '
            f'{len(rows)}군데</b>'
            '<p class="meta">비워 두셔서 자료로 방어 가능한 쪽으로 채웠습니다. '
            '관점·경험이 걸린 곳이면 아래를 확인하고 고쳐 주세요.</p>'
            f'<ul style="margin:.3rem 0 0;padding-left:1.1rem">{"".join(rows)}</ul>'
            '</div>')


def render_about_page() -> str | None:
    """소개 단일 원본을 앱 내부 라우트와 자산 경로에 맞춘다."""
    from pathlib import Path as _P
    here = _P(__file__).parent
    candidates = (here / "webassets" / "landing.html",
                  here.parent / "deploy" / "landing" / "public" / "index.html")
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        # `/about`은 이미 앱 안이므로 외부 운영 URL로 왕복시키지 않는다.
        text = text.replace('var APP_URL = "https://until-app.onrender.com";',
                            'var APP_URL = "/";')
        # 앱 CSP(img-src 'self')와 /asset 라우트에 맞춘다.
        return text.replace('src="img/', 'src="/asset/')
    return None


class _Handler(BaseHTTPRequestHandler):
    backend = "mock"
    context_dirs: Dict[str, str] = {}  # course_materials / my_files / voice (launch 시 설정)
    sso = False  # True면 토큰 대신 브라우저 SSO 세션으로 eTL 조회(단일 스레드 서버에서만)
    ws = False   # True면 Moodle Web Services(읽기 전용) 어댑터로 조회·수집(Canvas 대신)
    _allow_manual_start_for_tests = False

    # ── 클라우드 모드 공통(모든 응답 경로에 중앙 적용) ──────────────
    def send_response(self, code: int, message: str | None = None) -> None:
        """모든 응답에 보안 헤더 + 대기 중 Set-Cookie를 중앙에서 붙인다."""
        super().send_response(code, message)
        for ck in getattr(self, "_set_cookies", ()) or ():
            self.send_header("Set-Cookie", ck)
        self._set_cookies = []
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if CLOUD:
            # 인라인 style/script + 페이지가 실제로 쓰는 폰트 CDN만 허용.
            # (_PAGE가 fonts.googleapis.com·cdn.jsdelivr.net 스타일시트를 링크함 —
            #  막으면 세리프/프리텐다드가 시스템 폰트로 조용히 강등된다. 감사 14회차)
            from .analytics import csp_sources
            analytics_scripts, analytics_connects = csp_sources()
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data:; "
                             "style-src 'self' 'unsafe-inline' "
                             "https://fonts.googleapis.com https://cdn.jsdelivr.net; "
                             "font-src 'self' https://fonts.gstatic.com "
                             "https://cdn.jsdelivr.net; "
                             f"script-src 'self' 'unsafe-inline' {analytics_scripts}; "
                             f"connect-src 'self' {analytics_connects}; "
                             "frame-ancestors 'none'")

    def _https(self) -> bool:
        """Cloudflare/프록시 뒤 HTTPS 판정(Secure 쿠키용) — 공유 정책 사용."""
        return secure_cookies(self.headers)

    def _cookies(self) -> Dict[str, str]:
        from http.cookies import SimpleCookie
        try:
            c = SimpleCookie(self.headers.get("Cookie", "") or "")
            return {k: m.value for k, m in c.items()}
        except Exception:
            return {}

    def _set_cookie(self, name: str, value: str, *, max_age: int) -> None:
        """응답에 실을 쿠키 하나(HttpOnly·SameSite=Lax, HTTPS면 Secure)."""
        sec = "; Secure" if self._https() else ""
        self._set_cookies.append(
            f"{name}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax{sec}")

    def _clear_cookie(self, name: str) -> None:
        self._set_cookie(name, "", max_age=0)

    def _origin(self) -> str:
        """브라우저가 실제로 접속한 origin — 구글 redirect_uri 유도용.

        프록시(Cloudflare/Render) 뒤에서도 맞게 나오도록 X-Forwarded-*를 본다."""
        host = (self.headers.get("X-Forwarded-Host")
                or self.headers.get("Host") or "").split(",")[0].strip()
        if not host or any(c in host for c in " \t/\\"):
            return ""
        return ("https" if self._https() else "http") + "://" + host

    def _drain_body(self) -> None:
        """게이트 응답 전 POST 본문 드레인 — 안 읽고 보내면 클라이언트가 RST를
        받아 안내가 유실된다(413/베타 게이트와 동일 계보)."""
        if self.command != "POST":
            return
        try:
            n = min(int(self.headers.get("Content-Length", 0) or 0), 25 * 1024 * 1024)
            while n > 0:
                n -= len(self.rfile.read(min(n, 65536)) or b"\x00")
        except (TypeError, ValueError, OSError):
            pass

    def _begin_request(self) -> bool:
        """요청 스코프 초기화(uid·사용자별 경로·베타 게이트). False면 응답 완료됨."""
        self._set_cookies = []
        if not CLOUD:
            _REQ.uid = ""
            return True
        cookies = self._cookies()
        uid = cookies.get("uid", "")
        if not _UID_RE.match(uid):
            uid = secrets.token_urlsafe(24)
            sec = "; Secure" if self._https() else ""
            self._set_cookies.append(
                f"uid={uid}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax{sec}")
        _REQ.anon_uid = uid
        # 계정 로그인 상태면 uid를 계정 uid로 승격한다. 익명 쿠키는 지우지 않는다 —
        # 로그아웃하면 로그인 전 작업으로 그대로 되돌아간다.
        _REQ.auth = None
        from . import google_auth as _ga
        blob = cookies.get("auth", "")
        if blob and _ga.any_enabled():
            user = _ga.unpack_user(blob)
            if user is not None:
                _REQ.auth = user
                uid = user.uid
            else:
                self._clear_cookie("auth")   # 만료·위조 → 조용히 정리
        _REQ.uid = uid
        # 사용자별 히스토리·사용량 경로를 요청 스코프에 건다(깊은 호출부까지 전파).
        from .context.answer_history import set_history_path_override
        from .context.tone import set_persona_path_override
        from .profile import set_profile_path_override
        from . import billing as _billing
        root = _user_root(uid)
        set_history_path_override(root / "answer_history.jsonl")
        set_profile_path_override(root / "profile.json")
        set_persona_path_override(root / "persona.json")
        # 기억 3계층·수정 기록도 같은 요청 스코프로 격리한다(클라우드 멀티유저).
        from .context.edit_events import set_edit_events_path_override
        from .context.episodes import set_episodes_path_override
        from .context.facts import set_facts_path_override
        set_episodes_path_override(root / "episodes.jsonl")
        set_facts_path_override(root / "facts.json")
        set_edit_events_path_override(root / "edit_events.jsonl")
        from .persona.events import set_events_path_override
        set_events_path_override(root / "persona_events.jsonl")
        # 과목 프로파일(§3 route_hint 폴백)도 사용자별이다 — 전역 파일 하나면
        # 한 사람이 적은 힌트가 전원에게 걸린다.
        from .context.course_profiles import set_course_profiles_path_override
        set_course_profiles_path_override(root / "course_profiles.json")
        _billing.set_usage_path_override(root / "usage.json")
        _billing.set_credits_path_override(root / "credits.json")
        # KV 미러 → 디스크(성공한 uid만 1회). 실패/미확정이면 이 요청은 미러 금지.
        _REQ.hydrated_ok = _hydrate_user(uid)
        _hydrate_global()
        # 베타 게이트(선택) — UNTIL_BETA_CODE 설정 시 초대 코드 통과 쿠키 필요.
        codes = _beta_codes()
        if codes:
            # 소개와 정적 쇼케이스는 LLM·크레딧·개인화를 쓰지 않는 마케팅 표면.
            open_paths = ("/healthz", "/beta", "/about", "/about/", "/demo", "/demo/",
                          "/beta-request", "/beta-request/")
            if (cookies.get("beta") not in _beta_hashes(codes)
                    and self.path not in open_paths
                    and not self.path.startswith("/asset/")
                    # /admin은 자체 키 게이트(UNTIL_ADMIN_KEY) — 베타 쿠키 불요.
                    and not self.path.startswith("/admin")):
                # POST 본문을 드레인하고 응답(안 읽고 보내면 클라이언트가 RST를 받아
                # 안내가 유실된다 — 413/게이트와 동일한 수정 계보).
                if self.command == "POST":
                    try:
                        n = min(int(self.headers.get("Content-Length", 0) or 0),
                                25 * 1024 * 1024)
                        while n > 0:
                            n -= len(self.rfile.read(min(n, 65536)) or b"\x00")
                    except (TypeError, ValueError, OSError):
                        pass
                self._send(render_beta_gate(err=False), code=403, title="베타 · UNTIL")
                return False
        # CSRF — 상태를 바꾸는 POST는 우리 페이지에서 시작한 것이어야 한다.
        # 게이트들보다 **앞**에 둔다: 남의 사이트가 시킨 요청은 아무 일도 하기 전에
        # 끊는 게 맞고, 여기서 통과시키면 뒤의 게이트가 상태를 건드릴 수 있다.
        if self.command == "POST" and self.path.split("?", 1)[0] not in _CSRF_EXEMPT_PATHS:
            if not csrf_origin_ok(self.headers.get("Origin"),
                                  self.headers.get("Referer"),
                                  self.headers.get("X-Forwarded-Host")
                                  or self.headers.get("Host") or ""):
                import logging as _logging
                _logging.warning(
                    "CSRF 출처 불일치 path=%s origin=%s host=%s enforce=%s",
                    self.path.split("?", 1)[0], self.headers.get("Origin"),
                    self.headers.get("X-Forwarded-Host")
                    or self.headers.get("Host"), csrf_enforced())
                if not csrf_enforced():
                    return True                # 경고만 — 요청은 그대로 진행
                self._drain_body()
                self._send('<div class="sec"><h2>요청 출처를 확인할 수 없어요</h2>'
                           '<p class="meta">다른 사이트에서 시작된 요청으로 보입니다. '
                           'Until 화면에서 다시 시도해 주세요.</p>'
                           '<p><a class="btn ghost" href="/">← 처음으로</a></p></div>',
                           code=403, title="차단됨 · UNTIL")
                return False
        # 로그인 게이트(선택) — UNTIL_REQUIRE_LOGIN=1이면 작업 경로에 로그인 필요.
        # 기본은 off: 붙여넣고 바로 초안까지 가 본 뒤 저장하려고 로그인하는 흐름이
        # 전환율에 낫다. 로그인 없이 열어 두는 표면은 _LOGIN_OPEN_PATHS.
        if _ga.require_login() and _REQ.auth is None:
            bare = self.path.split("?", 1)[0]
            if (bare not in _LOGIN_OPEN_PATHS
                    and not bare.startswith("/asset/")
                    and not bare.startswith("/admin")
                    and not bare.startswith("/auth/")):
                self._drain_body()
                nxt = _urlquote(_ga.safe_next(self.path), safe="")
                self._redirect(f"/login?next={nxt}")
                return False
        # 텔레메트리 opt-in 고지 — 수집이 켜진 클라우드에서, 선택 기록이 생기기
        # 전까지 1회 화면. 어느 선택이든 이후 모든 경로가 그대로 열린다.
        import os as _os
        if (_os.getenv("UNTIL_TELEMETRY") == "1"
                and self.path not in ("/healthz", "/beta", "/about", "/about/",
                                      "/demo", "/demo/", "/login", "/logout",
                                      "/beta-request", "/beta-request/",
                                      "/consent", "/consent/")
                and not self.path.startswith("/auth/")
                and not self.path.startswith("/asset/")
                and not self.path.startswith("/admin")):
            from .telemetry.consent import get_consent
            if get_consent(uid, root=_USERS_DIR) is None:
                if self.command == "POST":  # 게이트 응답 전 본문 드레인(RST 방지)
                    try:
                        n = min(int(self.headers.get("Content-Length", 0) or 0),
                                25 * 1024 * 1024)
                        while n > 0:
                            n -= len(self.rfile.read(min(n, 65536)) or b"\x00")
                    except (TypeError, ValueError, OSError):
                        pass
                self._send(render_consent_notice(), title="데이터 안내 · UNTIL")
                return False
        return True

    def _end_request(self) -> None:
        """요청 스코프 해제 — 스레드 재사용 시 사용자 간 경로 누수 방지."""
        if not CLOUD:
            return
        try:
            # 변이 가능성이 있는 요청(POST) 뒤엔 히스토리·사용량을 KV로 미러.
            # 단, 하이드레이션이 확정된 요청만 — 미확정 상태의 미러는 KV의
            # 유일 사본을 절단/삭제할 수 있다(감사 14회차 HIGH).
            u = getattr(_REQ, "uid", "")
            if u and self.command == "POST" and getattr(_REQ, "hydrated_ok", False):
                _mirror_user(u)
            from .context.answer_history import set_history_path_override
            from .context.tone import set_persona_path_override
            from .profile import set_profile_path_override
            from . import billing as _billing
            set_history_path_override(None)
            set_profile_path_override(None)
            set_persona_path_override(None)
            from .context.edit_events import set_edit_events_path_override
            from .context.episodes import set_episodes_path_override
            from .context.facts import set_facts_path_override
            set_episodes_path_override(None)
            set_facts_path_override(None)
            set_edit_events_path_override(None)
            from .persona.events import set_events_path_override
            set_events_path_override(None)
            from .context.course_profiles import set_course_profiles_path_override
            set_course_profiles_path_override(None)
            _billing.set_usage_path_override(None)
            _billing.set_credits_path_override(None)
            _REQ.uid = ""
        except Exception:
            pass

    def _send_admin(self) -> None:
        """관리자 보드(GET /admin) — HMAC 쿠키 인증.

        env UNTIL_ADMIN_KEY 미설정은 404. 키는 POST /admin/login으로만 받는다.
        """
        import os as _os
        from urllib.parse import parse_qs as _pq
        from urllib.parse import urlparse as _up
        want = (_os.getenv("UNTIL_ADMIN_KEY") or "").strip()
        query = _pq(_up(self.path).query)
        if not want:
            self._send("<p>Not Found</p>", code=404)
            return
        from . import adminboard
        if not adminboard.verify_admin_token(
                self._cookies().get(adminboard.ADMIN_COOKIE, ""), want):
            self._send(adminboard.render_admin_login(), title="관리자 로그인 · UNTIL")
            return
        local_recs = adminboard.load_all(_USERS_DIR)
        kv_recs: list = []
        if CLOUD:  # 콜드스타트 후에도 전체 사용자 — adm:* 키를 KV에서 수집.
            try:
                from . import cloudkv
                c = cloudkv.kv()
                if c is not None:
                    for k in c.list_keys("adm:", limit=200):
                        blob = c.get(k)
                        rec = adminboard.parse_record(blob) if blob else None
                        if rec:
                            rec.setdefault("uid", k.split(":", 1)[1])
                            kv_recs.append(rec)
            except Exception:
                pass
        recs = adminboard.merge_records(local_recs, kv_recs)
        # 코퍼스 CLI 텔레메트리 + 웹 uid별 텔레메트리(클라우드는 KV 미러 병합).
        telemetry_records = (adminboard.load_telemetry()
                             + adminboard.load_web_telemetry(_USERS_DIR, use_kv=CLOUD))
        # 개인화 패널 — "Until이 이 사용자에 대해 무엇을 알고 있나"(파생 지표만).
        from . import personalization_board as _pboard
        panel = _pboard.render_html(_pboard.collect_rows(_USERS_DIR), me=_uid())
        # 베타 초대 요청 — 아직 사용자가 아닌 사람들이라 위 표(uid 단위)에 없다.
        from . import betarequests as _breq
        panel += _breq.render_admin_section(_breq.load_all(use_kv=CLOUD))
        self._send(adminboard.render_admin_html(
            recs, include_internal=query.get("internal", [""])[0] == "1",
            telemetry_records=telemetry_records, me=_uid()) + panel,
            title="관리자 보드 · UNTIL")

    def _send(self, html_body: str, code: int = 200, title: str = "UNTIL — 경계선까지") -> None:
        from . import billing as _billing
        data = _wrap(html_body, f"{self.backend} · {_billing.plan()}",
                     title=title).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            # 브라우저가 기다리다 끊은 경우 — 무해하므로 조용히 무시.
            pass

    def _send_download(self, data, filename: str, content_type: str) -> None:
        """파일 다운로드 응답(_wrap 없이 원본 그대로). data는 str 또는 bytes."""
        raw = data if isinstance(data, bytes) else data.encode("utf-8")
        ctype = content_type if isinstance(data, bytes) else f"{content_type}; charset=utf-8"
        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _download_submission(self, token: str, fmt: str) -> None:
        """제출용 문서(.md/.html)를 다운로드로 내려준다."""
        res = _get_session(token)
        if res is None:
            self._send('<div class="sec"><h2>세션이 만료됐습니다</h2>'
                       '<p><a class="btn ghost" href="/">← 처음으로</a></p></div>', code=404)
            return
        from .report import (render_submission_docx, render_submission_html,
                             render_submission_markdown, render_submission_pdf)
        if fmt == "html":
            self._send_download(render_submission_html(res), "until-submission.html", "text/html")
        elif fmt == "docx":
            self._send_download(
                render_submission_docx(res), "until-submission.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        elif fmt == "pdf":
            self._send_download(render_submission_pdf(res), "until-submission.pdf",
                                "application/pdf")
        elif fmt == "pptx":
            from .presentation_export import render_presentation_pptx
            self._send_download(
                render_presentation_pptx(res), "until-presentation.pptx",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation")
        else:
            self._send_download(render_submission_markdown(res), "until-submission.md", "text/markdown")
        if res.final_draft is None:
            self._admin_touch_many(
                "decision_skip", res.draft.n_decisions - len(_ANSWERS.get(token, {})))
        self._admin_touch("export")
        _telemetry_emit("export", token, res)

    def _download_filled_form(self, token: str) -> None:
        """원본 양식(hwpx/docx)에 초안 값을 셀 주입한 파일을 내려준다(원본 서식 유지)."""
        res = _get_session(token)
        if res is None:
            self._send('<div class="sec"><h2>세션이 만료됐습니다</h2>'
                       '<p><a class="btn ghost" href="/">← 처음으로</a></p></div>', code=404)
            return
        import tempfile
        from pathlib import Path as _P
        from .report import write_filled_form
        from .capture.formfill import find_form_document
        src = find_form_document(res)
        if not src:
            self._send('<div class="sec"><h2>채울 양식이 없어요</h2>'
                       '<p class="meta">양식 첨부(hwpx/docx/hwp)가 없는 세션이거나 원본 파일이 '
                       '정리됐습니다.</p></div>', code=404)
            return
        src_suffix = _P(src).suffix.lower()
        try:
            with tempfile.TemporaryDirectory() as d:
                got = write_filled_form(res, _P(d) / f"filled{src_suffix}")
                if not got:
                    self._send('<div class="sec"><h2>옮길 표 값이 아직 없어요</h2>'
                               '<p class="meta">초안·완성본의 표에 값이 채워진 뒤 다시 시도해 '
                               '주세요.</p></div>', code=404)
                    return
                data = got[0].read_bytes()
                out_suffix = got[0].suffix.lower()  # .hwp 원본은 .docx로 강제 대체(C안)
                self._last_fill_stats = got[1]  # 진단용(테스트에서 검사)
        except Exception:
            self._send('<div class="sec"><h2>양식을 만들지 못했어요</h2>'
                       '<p class="meta">원본 파일을 읽는 중 문제가 생겼습니다. 잠시 후 '
                       '다시 시도해 주세요.</p></div>', code=500)
            return
        ctype = ("application/vnd.hancom.hwpx" if out_suffix == ".hwpx" else
                 "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        # 헤더 파일명은 ASCII(비-latin1 헤더 인코딩 오류 회피 — 기존 다운로드와 동일 규칙).
        self._send_download(data, f"until-form{out_suffix}", ctype)
        if res.final_draft is None:
            self._admin_touch_many(
                "decision_skip", res.draft.n_decisions - len(_ANSWERS.get(token, {})))
        self._admin_touch("export")
        _telemetry_emit("export", token, res)

    def _readiness_json(self, token: str) -> None:
        """세션의 제출 준비 점검을 JSON으로(툴 연동). 만료 세션은 404 JSON."""
        import json as _json
        res = _get_session(token)
        if res is None:
            body = _json.dumps({"error": "session_not_found"})
            raw = body.encode("utf-8")
            try:
                self.send_response(404)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass
            return
        from .readiness import assess_readiness
        payload = _json.dumps(assess_readiness(res).to_dict(), ensure_ascii=False)
        raw = payload.encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _send_about(self) -> None:
        """소개(랜딩) 단일 원본을 앱 내부 링크로 바꿔 서빙한다."""
        text = render_about_page()
        if text is None:
            self._redirect("https://until-landing.minjun05.workers.dev")
            return
        raw = text.encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            for c in getattr(self, "_set_cookies", []):
                self.send_header("Set-Cookie", c)
            self.end_headers()
            self.wfile.write(raw)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _send_asset(self, name: str) -> None:
        """번들 정적 에셋(until/webassets/) — 안전한 파일명만, 캐시 1일."""
        from pathlib import Path as _P
        ok = bool(name) and len(name) <= 64 and all(
            c.isalnum() or c in "._-" for c in name) and ".." not in name
        p = (_P(__file__).parent / "webassets" / name) if ok else None
        if p is not None and not p.is_file():
            # 로컬 개발 폴백 — 랜딩 스크린샷은 리포지토리에만 있다(클라우드는
            # Dockerfile이 webassets로 번들). 파일명은 위에서 이미 검증됨.
            repo_img = (_P(__file__).resolve().parents[1]
                        / "deploy" / "landing" / "public" / "img" / name)
            if repo_img.is_file():
                p = repo_img
        if p is None or not p.is_file():
            try:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass
            return
        ctype = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                 ".svg": "image/svg+xml", ".webp": "image/webp",
                 ".css": "text/css; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8"}.get(
            p.suffix.lower(), "application/octet-stream")
        raw = p.read_bytes()
        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(raw)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _billing_gate(self) -> bool:
        """무료 한도 검사(라이브 백엔드만). True면 /plan으로 보냈음(처리 종료).

        클라우드에서는 사용자별 한도(요청 스코프 오버라이드)에 더해 전역 일일
        상한(UNTIL_GLOBAL_DAILY_DRAFTS — 운영 비용·Groq TPD 방어)도 함께 걷는다.
        """
        from . import billing
        if self.backend == "mock":
            return False
        if not billing.can_draft():
            self._redirect("/plan?full=1")
            return True
        if CLOUD and not billing.global_can_draft():
            # 전역 상한은 사용자가 충전해도 안 풀린다 — 다른 문구로 보낸다.
            self._redirect("/plan?full=limit")
            return True
        return False

    def _billing_record(self, result: Result | None = None) -> None:
        """초안 1회 적립(라이브 백엔드만 — mock 데모·테스트는 무제한)."""
        from . import billing
        if self.backend != "mock":
            billing.record_draft()
            if CLOUD:
                billing.record_global_draft()
        guard = getattr(result, "guard", None)
        event = "draft_fail:guard" if guard is not None and not guard.passed else "draft"
        self._admin_touch(event)

    def _admin_touch(self, event: str, token: str = "") -> None:
        """관리자 보드 기록(비치명·베스트에포트) — 토큰은 SHA-256 지문만.

        프로필은 요청 스코프(load_profile — 클라우드는 uid 파일)의 저장분 스냅샷.
        실패는 조용히(분석 기록이 사용자 흐름을 막으면 안 된다).
        """
        try:
            from . import adminboard
            uid = _uid() or "local"
            prof = {}
            try:
                from .profile import load_profile
                prof = load_profile() or {}
            except Exception:
                prof = {}
            adminboard.record_event(_user_root(uid), uid, event,
                                    token=token, profile=prof)
        except Exception:
            pass

    def _admin_touch_many(self, event: str, count: int) -> None:
        """결정 단위 카운트를 비치명적으로 여러 건 적립한다."""
        for _ in range(max(0, int(count))):
            self._admin_touch(event)

    def _read_form(self) -> Dict[str, list]:
        # 잘못된 Content-Length·비-UTF8 본문이 와도 핸들러가 죽지 않도록 방어.
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            length = 0
        if length > 25 * 1024 * 1024:  # 업로드 경로와 동일 상한(OOM 방지)
            return {}
        raw = self.rfile.read(length).decode("utf-8", "replace") if length > 0 else ""
        return parse_qs(raw, keep_blank_values=True)

    def _read_form_and_files(self):
        """폼 파싱(+multipart 파일) — (fields dict, [(파일명, bytes)], too_large) 반환.

        urlencoded면 파일 없이 기존과 동일. multipart는 email 파서(표준 라이브러리)로.
        """
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = 0
        if length > 25 * 1024 * 1024:  # 업로드 합계 25MB 상한(OOM 방지)
            # 본문을 청크로 비워 버린다 — 안 읽고 응답하면 클라이언트가 아직
            # 보내는 중이라 RST로 413 페이지가 유실된다(메모리 미사용 드레인).
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
            return {}, [], True
        body = self.rfile.read(length) if length > 0 else b""
        ctype = self.headers.get("Content-Type", "") or ""
        if not ctype.lower().startswith("multipart/form-data"):
            return (parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True),
                    [], False)
        from email.parser import BytesParser
        from email.policy import default as _policy
        try:
            msg = BytesParser(policy=_policy).parsebytes(
                b"Content-Type: " + ctype.encode("utf-8", "replace") + b"\r\n\r\n" + body)
        except Exception:
            return {}, [], False
        fields: Dict[str, list] = {}
        files = []
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            payload = part.get_payload(decode=True) or b""
            fn = part.get_filename()
            if fn:
                # 0바이트 파일도 넘긴다 — 조용한 누락 대신 '빈 자료' 경고 표면화.
                # (파일 미선택의 filename="" 빈 파트는 get_filename()이 falsy라 제외.)
                # 필드명을 함께 보존 — 자료(files)와 문체(voice_files)를 구분.
                files.append((str(name), fn, payload))
            else:
                fields.setdefault(str(name), []).append(
                    payload.decode("utf-8", "replace"))
        return fields, files, False

    def log_message(self, *args) -> None:  # 콘솔 소음 제거
        pass

    def _redirect(self, location: str) -> None:
        """POST 처리 후 303 리다이렉트(PRG) — 새로고침 재제출/재생성 방지."""
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ── 계정 로그인 ─────────────────────────────────────────────────
    def _query(self) -> Dict[str, list]:
        return parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}

    def _send_login(self, err: str = "", next_path: str = "", code: int = 200) -> None:
        nxt = next_path or (self._query().get("next", ["/"])[0] or "/")
        self._send(render_login(nxt, err=err), code=code, title="로그인 · UNTIL")

    def _auth_start(self) -> None:
        """구글 인가 화면으로 보낸다(state·PKCE·nonce는 서명 쿠키에 담아 왕복)."""
        from . import google_auth as _ga
        cfg = _ga.config(self._origin())
        if cfg is None:
            self._send_login(err="구글 로그인 설정이 완료되지 않았습니다.")
            return
        nxt = _ga.safe_next(self._query().get("next", ["/"])[0])
        verifier, challenge = _ga.new_pkce()
        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(16)
        self._set_cookie("gauth", _ga.sign(
            {"v": verifier, "s": state, "n": nonce, "next": nxt}, _ga.STATE_TTL),
            max_age=int(_ga.STATE_TTL))
        self._redirect(_ga.authorize_url(cfg, state=state, challenge=challenge,
                                         nonce=nonce))

    def _auth_callback(self) -> None:
        """구글이 돌려준 code를 토큰으로 바꾸고 uid를 계정에 고정한다."""
        from . import google_auth as _ga
        q = self._query()
        saved = _ga.unsign(self._cookies().get("gauth", ""))
        self._clear_cookie("gauth")     # 1회용 — 성공/실패 무관하게 폐기
        nxt = _ga.safe_next(str((saved or {}).get("next") or "/"))
        if q.get("error"):
            self._send_login(err="구글에서 로그인이 취소됐습니다.", next_path=nxt)
            return
        code = (q.get("code", [""])[0] or "").strip()
        state = (q.get("state", [""])[0] or "")
        if not saved or not code or not secrets.compare_digest(
                state, str(saved.get("s", ""))):
            self._send_login(err="로그인 요청이 만료됐어요. 다시 시도해 주세요.",
                             next_path=nxt, code=400)
            return
        cfg = _ga.config(self._origin())
        if cfg is None:
            self._send_login(err="구글 로그인 설정이 완료되지 않았습니다.", code=400)
            return
        try:
            tokens = _ga.exchange_code(cfg, code, str(saved.get("v", "")))
            claims = _ga.decode_id_token(tokens.get("id_token", ""),
                                         client_id=cfg.client_id,
                                         nonce=str(saved.get("n", "")))
        except _ga.AuthError as e:
            self._send_login(err=str(e), next_path=nxt, code=400)
            return
        user = _ga.user_from_claims(claims)
        # 로그인 직전까지 익명으로 만든 초안·명세서를 계정으로 넘긴다.
        moved = _adopt_anon_data(getattr(_REQ, "anon_uid", ""), user.uid)
        self._set_cookie("auth", _ga.pack_user(user), max_age=_ga.AUTH_COOKIE_TTL)
        _REQ.auth, _REQ.uid = user, user.uid   # 이 응답의 상단 바부터 로그인 상태
        self._admin_touch("login")
        if moved:
            _hydrate_user(user.uid)
        self._redirect(nxt)

    def _kakao_auth_start(self) -> None:
        """카카오 인가 화면으로 보낸다(state·PKCE는 1회용 서명 쿠키로 왕복)."""
        from . import google_auth as _ga
        from . import kakao_auth as _ka
        cfg = _ka.config(self._origin())
        if cfg is None:
            self._send_login(err="카카오 로그인 설정이 완료되지 않았습니다.")
            return
        nxt = _ga.safe_next(self._query().get("next", ["/"])[0])
        verifier, challenge = _ga.new_pkce()
        state = secrets.token_urlsafe(24)
        self._set_cookie("kauth", _ga.sign(
            {"v": verifier, "s": state, "next": nxt}, _ga.STATE_TTL),
            max_age=int(_ga.STATE_TTL))
        self._redirect(_ka.authorize_url(cfg, state=state, challenge=challenge))

    def _kakao_auth_callback(self) -> None:
        """카카오 code를 사용자 id로 바꾸고 기존 계정 세션 계약에 연결한다."""
        from . import google_auth as _ga
        from . import kakao_auth as _ka
        q = self._query()
        saved = _ga.unsign(self._cookies().get("kauth", ""))
        self._clear_cookie("kauth")
        nxt = _ga.safe_next(str((saved or {}).get("next") or "/"))
        if q.get("error"):
            self._send_login(err="카카오에서 로그인이 취소됐습니다.", next_path=nxt)
            return
        code = (q.get("code", [""])[0] or "").strip()
        state = (q.get("state", [""])[0] or "")
        if not saved or not code or not secrets.compare_digest(
                state, str(saved.get("s", ""))):
            self._send_login(err="로그인 요청이 만료됐어요. 다시 시도해 주세요.",
                             next_path=nxt, code=400)
            return
        cfg = _ka.config(self._origin())
        if cfg is None:
            self._send_login(err="카카오 로그인 설정이 완료되지 않았습니다.", code=400)
            return
        try:
            tokens = _ka.exchange_code(cfg, code, str(saved.get("v", "")))
            profile = _ka.fetch_user(str(tokens.get("access_token", "")))
            user = _ka.user_from_profile(profile)
        except _ka.AuthError as exc:
            self._send_login(err=str(exc), next_path=nxt, code=400)
            return
        moved = _adopt_anon_data(getattr(_REQ, "anon_uid", ""), user.uid)
        self._set_cookie("auth", _ga.pack_user(user), max_age=_ga.AUTH_COOKIE_TTL)
        _REQ.auth, _REQ.uid = user, user.uid
        self._admin_touch("login")
        if moved:
            _hydrate_user(user.uid)
        self._redirect(nxt)

    def _logout(self) -> None:
        self._clear_cookie("auth")
        _REQ.auth = None
        _REQ.uid = getattr(_REQ, "anon_uid", "")
        self._redirect("/")

    def _fast_draft(self, items, note, cfg, *, token=None, sid="",
                    picker=None) -> None:
        """'바로 초안' — 가장 급한 과제를 골라 수집→초안까지 한 번에(간단 뷰로).

        picker로 선택 정책을 바꾼다(기본 _pick_best, 연습 모드는 pick_practice)."""
        best = (picker or _pick_best)(items)
        if best is None:
            self._send(render_inbox(items, sid=sid,
                                    note=(note + " " if note else "") + "바로 초안을 만들 과제가 없어요.",
                                    simple=True), title="내 과제 · UNTIL")
            return
        if self._billing_gate():
            return
        try:
            self._admin_touch("assign_open")
            adapter = _sso_adapter() if self.sso else None
            # ws 플래그 전달 필수 — 누락 시 Moodle URL을 Canvas 어댑터로 파싱해
            # --ws 서버의 '바로 초안'이 항상 502였다(리뷰 발견).
            is_practice = getattr(picker, "__name__", "") == "pick_practice"
            res = collect_with_materials(best.url, cfg, token=token, adapter=adapter,
                                         ws=self.ws, context_dirs=self.context_dirs,
                                         practice=is_practice)
        except Exception as e:
            self._admin_touch("draft_fail:pipe")
            self._send(f'<p>{html.escape(user_error_message(e, "바로 초안을 생성"))}</p>'
                       '<p><a href="/">처음으로</a></p>', code=502)
            return
        # 인박스가 아는 원본 과제명·과목명이 표기 정답(수집기의 course 해상 실패
        # 시 'Canvas course NNN' 폴백이 화면에 새지 않게).
        if isinstance(res.spec, dict):
            if (getattr(best, "title", "") or "").strip():
                res.spec["title"] = best.title.strip()
            if (getattr(best, "course_name", "") or "").strip():
                res.spec["course"] = best.course_name.strip()
        tok = _new_token()
        _SESSIONS[tok] = res
        if token:
            _store_canvas_token(tok, token)
        _telemetry_begin(tok, res, source="etl", backend=cfg.backend, url=best.url)
        _persist_session(tok)
        _telemetry_emit("draft", tok, res)
        # 딸깍 완주 — fast 경로는 AI 제안을 미리 만들어 답칸을 채워 둔다.
        # 수락/수정은 여전히 사람 몫(자동 확정 아님, 경계선 유지). 제안 실패는
        # 비치명 — 빈 답칸 흐름으로 계속.
        if res.draft.decisions:
            try:
                _SUGGESTIONS[tok] = suggest_decision_answers(res, cfg)
                _persist_session(tok)
            except Exception:
                pass
        _log_feedback(res, cfg.backend)
        self._billing_record(res)
        self._redirect(f"/sv/{tok}")

    def _view_draft(self, token: str) -> None:
        res = _get_session(token)
        if res is None:
            self._send('<div class="sec"><h2>세션이 만료됐습니다</h2>'
                       '<p><a class="btn ghost" href="/">← 처음으로</a></p></div>', code=404)
            return
        self._send(render_draft(token, res, suggestions=_SUGGESTIONS.get(token),
                                review=_REVIEWS.get(token),
                                answers=_ANSWERS.get(token),
                                voice_note=_voice_note_html(token)),
                   title="초안 · UNTIL")

    def _view_final(self, token: str) -> None:
        res = _get_session(token)
        if res is None:
            self._send('<div class="sec"><h2>세션이 만료됐습니다</h2>'
                       '<p><a class="btn ghost" href="/">← 처음으로</a></p></div>', code=404)
            return
        self._send(render_final(res, session_id=token, answered=set(_ANSWERS.get(token, {})))
                   + _rating_html(token, token in _RATINGS)
                   + _voice_rating_html(token, res, token in _VOICE_RATINGS),
                   title="최종본 · UNTIL")

    def do_GET(self) -> None:
        if self.path == "/healthz":
            # 헬스체크 — 쿠키·게이트·래핑 없이 즉답(Worker/모니터링용).
            # 배포 커밋을 병기해 "지금 떠 있는 게 어느 빌드인지" 즉시 확인
            # (Render가 RENDER_GIT_COMMIT 주입 — 없으면 그냥 "ok").
            import os as _os
            sha = (_os.getenv("RENDER_GIT_COMMIT") or "").strip()[:7]
            raw = f"ok {sha}".strip().encode("utf-8")
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass
            return
        if not self._begin_request():
            return
        try:
            self._route_get()
        finally:
            self._end_request()

    def _route_get(self) -> None:
        try:
            if self.path in ("/", "/index", "/index.html"):
                self._admin_touch("visit")
                # /inbox 가드와 동일 기준(strip) — 공백 env 토큰이 '설정됨'으로 보이면
                # 홈은 입력칸을 숨기는데 /inbox는 입력하라는 모순(복구 불가)이 된다.
                self._send(render_index(
                    has_env_token=bool(_env_canvas_token()),
                    sso=self.sso))
            elif self.path.startswith("/v/"):
                self._view_draft(self.path[len("/v/"):])
            elif self.path.startswith("/vf/"):
                self._view_final(self.path[len("/vf/"):])
            elif self.path in ("/beta-request", "/beta-request/"):
                from . import betarequests
                self._send(betarequests.render_form(),
                           title="베타 초대 요청 · UNTIL")
            elif self.path in ("/demo", "/demo/"):
                # 작동 예시 페이지는 없앴다(2026-08-21) — 소개 페이지가 같은 5단계를
                # 이미 보여 준다. 404 대신 리다이렉트인 이유: 초대 메일과 따로
                # 배포된 랜딩에 /demo 링크가 남아 있어 죽은 링크가 된다.
                self._redirect("/about")
            elif self.path in ("/simple", "/simple/") or self.path.startswith("/simple?"):
                if CLOUD and not self._allow_manual_start_for_tests:
                    self._redirect("/connect?mode=fast")
                    return
                self._admin_touch("visit")
                # ?demo=1 — 볼륨형 샘플 과제 프리필(팀 demo 합의). LLM 호출 없음.
                demo = "demo=1" in (self.path.split("?", 1)[1] if "?" in self.path else "")
                self._send(render_simple_index(
                    prefill=demo_assignment_text() if demo else ""),
                    title="UNTIL — 간단히")
            elif self.path.startswith("/sv/"):
                tok = self.path[len("/sv/"):]
                res = _get_session(tok)
                if res is None:
                    self._send('<div class="smp"><p class="smp-step">만료</p>'
                               '<p class="smp-x"><a href="/simple">← 처음으로</a></p></div>', code=404)
                else:
                    self._send(render_simple_draft(tok, res), title="초안 · UNTIL")
            elif self.path.startswith("/svf/"):
                tok = self.path[len("/svf/"):]
                res = _get_session(tok)
                if res is None:
                    self._send('<div class="smp"><p class="smp-step">만료</p>'
                               '<p class="smp-x"><a href="/simple">← 처음으로</a></p></div>', code=404)
                else:
                    self._send(render_simple_final(res, session_id=tok,
                                                   answered=set(_ANSWERS.get(tok, {})))
                               + _rating_html(tok, tok in _RATINGS, simple=True)
                               + _voice_rating_html(tok, res, tok in _VOICE_RATINGS,
                                                    simple=True),
                               title="완성본 · UNTIL")
            elif self.path in ("/new", "/new/"):
                if CLOUD and not self._allow_manual_start_for_tests:
                    self._redirect("/connect?mode=fast")
                    return
                self._admin_touch("visit")
                self._send(render_new_assignment(), title="과제 만들기 · UNTIL")
            elif self.path.startswith("/ready/"):
                # 제출 직전 마지막 한 칸(점검·올릴 파일·eTL 링크·완료 표시).
                tok = self.path[len("/ready/"):].split("?", 1)[0]
                res = _get_session(tok)
                if res is None:
                    self._send('<div class="smp"><p class="smp-step">만료</p>'
                               '<p class="smp-x"><a href="/">← 처음으로</a></p></div>',
                               code=404)
                else:
                    self._send(render_submit_ready(tok, res), title="제출 · UNTIL")
            elif self.path in ("/data/export.json", "/data/export"):
                # 페르소나 이동성 — 문체·사실만 담고 과제 원문은 뺀다(실수 유출 방지).
                # 원문까지 필요하면 CLI(`python -m until.persona.portability
                # --export out.json --with-episodes`)를 쓴다.
                from .persona.portability import export_persona
                blob = json.dumps(export_persona(), ensure_ascii=False,
                                  indent=1).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Disposition",
                                 'attachment; filename="until-persona.json"')
                self.send_header("Content-Length", str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)
            elif self.path.startswith("/dl/"):
                # /dl/<token>.md | .html | .docx | .form(hwpx/docx/hwp) — 문서 다운로드.
                name = self.path[len("/dl/"):]
                token, _, ext = name.rpartition(".")
                ext = ext.lower()
                if ext == "form":  # /dl/<token>.form — 채워진 원본 양식(hwpx/docx)
                    self._download_filled_form(token)
                else:
                    fmt = ("html" if ext in ("html", "htm")
                           else ext if ext in ("docx", "pdf", "pptx") else "md")
                    self._download_submission(token, fmt)
            elif self.path.startswith("/readiness/"):
                # /readiness/<token>.json — 제출 준비 점검 JSON(툴 연동).
                name = self.path[len("/readiness/"):]
                token = name[:-5] if name.endswith(".json") else name
                self._readiness_json(token)
            elif self.path.startswith("/asset/"):
                # 캐시 버스터 쿼리(?v=해시)는 파일명이 아니다 — 잘라내고 서빙.
                self._send_asset(self.path[len("/asset/"):].split("?", 1)[0])
            elif self.path.startswith("/admin"):
                self._send_admin()
            elif self.path in ("/sessions", "/sessions/"):
                self._send(render_sessions(list_sessions()), title="이전 작업 · UNTIL")
            elif self.path in ("/archive", "/archive/"):
                self._send(render_archive(list_sessions(limit=60)),
                           title="내 과제 아카이브 · UNTIL")
            elif self.path in ("/history", "/history/"):
                self._send(render_history(), title="내 답 히스토리 · UNTIL")
            elif self.path in ("/consent", "/consent/"):
                from .telemetry.consent import get_consent
                self._send(render_consent_settings(
                    get_consent(_uid() or "local", root=_USERS_DIR)),
                    title="데이터 설정 · UNTIL")
            elif self.path in ("/about", "/about/"):
                self._send_about()
            elif self.path in ("/profile", "/profile/"):
                self._send(render_profile(), title="내 프로필 · UNTIL")
            elif self.path == "/profile?saved=1":
                self._send(render_profile(saved=True), title="내 프로필 · UNTIL")
            elif self.path == "/profile?courses=1":
                self._send(render_profile(courses_saved=True),
                           title="내 프로필 · UNTIL")
            elif self.path == "/connect" or self.path.startswith("/connect?"):
                # 홈 버튼 → 이 화면(eTL 연결) → /inbox. 클릭이 먼저, 토큰이 나중.
                if self.sso or _env_canvas_token():
                    self._redirect("/")   # 연결 수단이 이미 있으면 홈에서 바로 제출
                else:
                    self._admin_touch("connect")
                    self._send(render_connect(
                        mode=(self._query().get("mode", ["fast"])[0] or "fast"),
                        sso=self.sso), title="eTL 연결 · UNTIL")
            elif self.path == "/login" or self.path.startswith("/login?"):
                from . import google_auth as _ga
                if _auth_user() is not None:      # 이미 로그인 — 되돌아갈 곳으로
                    self._redirect(_ga.safe_next(
                        self._query().get("next", ["/"])[0]))
                else:
                    self._send_login()
            elif self.path == "/auth/google/start" or self.path.startswith(
                    "/auth/google/start?"):
                self._auth_start()
            elif self.path == "/auth/google/callback" or self.path.startswith(
                    "/auth/google/callback?"):
                self._auth_callback()
            elif self.path == "/auth/kakao/start" or self.path.startswith(
                    "/auth/kakao/start?"):
                self._kakao_auth_start()
            elif self.path == "/auth/kakao/callback" or self.path.startswith(
                    "/auth/kakao/callback?"):
                self._kakao_auth_callback()
            elif self.path == "/beta":
                # 게이트 open_paths와 짝 — 초대 흐름이 404 막다른 길이 되지 않게.
                codes = _beta_codes()
                if CLOUD and codes:
                    if self._cookies().get("beta") in _beta_hashes(codes):
                        self._redirect("/")  # 이미 통과 — 홈으로
                    else:
                        self._send(render_beta_gate(), code=403, title="베타 · UNTIL")
                else:
                    self._redirect("/")
            elif self.path.startswith("/plan"):
                err = ("redeem" if "err=redeem" in self.path
                       else "1" if "err=1" in self.path else "")
                msg = "충전됐어요! 이어서 과제를 만들 수 있어요." if "ok=1" in self.path else ""
                # full 계약: "limit"=전역 상한(충전 무의미) / "1"=내 잔액 부족.
                full = ("limit" if "full=limit" in self.path
                        else "1" if "full=1" in self.path else "")
                self._send(render_plan(full=full, backend=self.backend,
                                       err=err, msg=msg),
                           title="플랜 · UNTIL")
            else:
                self._send('<div class="sec"><h2>없는 페이지</h2>'
                           '<p><a class="btn ghost" href="/">← 처음으로</a></p></div>', code=404)
        except Exception as e:  # 렌더 버그가 스레드를 죽이지 않도록 깔끔한 500 페이지로.
            self._send(f'<div class="sec"><h2>페이지를 그리는 중 오류</h2>'
                       f'<p class="muted">{html.escape(str(e))}</p>'
                       f'<p><a class="btn ghost" href="/">← 처음으로</a></p></div>', code=500)

    def _beta_request(self) -> None:
        """베타 초대 요청 접수(POST /beta-request) — 인증 없는 공개 엔드포인트.

        랜딩(다른 오리진)에서 오는 일반 폼 POST다. CORS 프리플라이트가 없는
        `application/x-www-form-urlencoded`라서 브라우저가 그대로 보내 주고,
        응답은 이 앱의 감사 화면으로 넘어온다 — JS 없이도 동작한다.
        """
        from . import betarequests
        form = {k: (v[0] if v else "") for k, v in self._read_form().items()}
        record, error = betarequests.normalize(form)
        if record is None:
            if not error:      # 허니팟 — 봇에게 걸린 걸 알려 주지 않는다.
                self._send(betarequests.render_thanks(), title="요청 완료 · UNTIL")
                return
            self._send(betarequests.render_form(error=error, values=form),
                       code=400, title="베타 초대 요청 · UNTIL")
            return
        if betarequests.today_count() >= betarequests.MAX_PER_DAY:
            # 상한은 남용 차단용이다. 사람에게는 실패가 아니라 대체 경로를 준다.
            self._send(betarequests.render_form(
                error="오늘 접수가 많아 잠시 후에 다시 시도해 주세요. "
                      "급하시면 minjun05@snu.ac.kr로 메일 주세요.", values=form),
                code=429, title="베타 초대 요청 · UNTIL")
            return
        if not betarequests.save(record):
            self._send(betarequests.render_form(
                error="저장에 실패했어요. minjun05@snu.ac.kr로 메일 주시면 "
                      "직접 등록해 드릴게요.", values=form),
                code=500, title="베타 초대 요청 · UNTIL")
            return
        self._send(betarequests.render_thanks(), title="요청 완료 · UNTIL")

    def do_POST(self) -> None:
        if not self._begin_request():
            return
        try:
            self._route_post()
        finally:
            self._end_request()

    def _route_post(self) -> None:
        if self.path in ("/beta-request", "/beta-request/"):
            self._beta_request()
            return
        if self.path == "/submit/prepare":
            form = self._read_form()
            token = (form.get("session", [""])[0] or "").strip()
            result = _get_session(token)
            if result is None:
                self._send('<div class="sec"><h2>세션이 만료됐습니다</h2></div>', code=404)
                return
            plan = prepare_submission(result, uid=_uid(), session_id=token)
            self._send(render_submission_confirmation(plan, token),
                       code=200 if plan.allowed else 409,
                       title="제출 최종 확인 · UNTIL")
            return
        if self.path == "/submit/confirm":
            form = self._read_form()
            token = (form.get("session", [""])[0] or "").strip()
            nonce = (form.get("confirm_nonce", [""])[0] or "").strip()
            if not nonce:
                self._send('<div class="sec"><h2>확인 정보가 없거나 만료됐습니다</h2></div>',
                           code=400)
                return
            result = _get_session(token)
            if result is None:
                self._send('<div class="sec"><h2>세션이 만료됐습니다</h2></div>', code=404)
                return
            plan, receipt = confirm_submission(
                result, cloud=CLOUD, confirm_nonce=nonce,
                uid=_uid(), session_id=token)
            self._send(render_submission_receipt(plan, receipt, cloud=CLOUD),
                       code=200 if plan.allowed else 409, title="제출 확인 · UNTIL")
            return
        if self.path == "/api/v1/token/check":
            length = min(int(self.headers.get("Content-Length", "0") or 0), 4096)
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                token = (str(payload.get("token") or "").strip()
                         if isinstance(payload, dict) else "")
            except (TypeError, ValueError, UnicodeDecodeError):
                token = ""
            valid = bool(token) and len(token) <= 500
            result = check_canvas_token(token) if valid else {"ok": False, "reason": "auth"}
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(200 if valid else 400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/telemetry/export":
            form = self._read_form()
            token = (form.get("session", [""])[0] or "").strip()
            res = _get_session(token)
            if res is not None:
                self._admin_touch("export")
                _telemetry_emit("export", token, res)
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/consent":
            # 텔레메트리 opt-in 선택/변경. 잘못된 값은 무기록(고지 다시 뜸).
            form = self._read_form()
            choice = (form.get("choice", [""])[0] or "").strip()
            if choice in ("yes", "no"):
                from .telemetry.consent import set_consent
                try:
                    set_consent(_uid() or "local", choice == "yes", root=_USERS_DIR)
                except OSError:
                    pass
                sec = "; Secure" if self._https() else ""
                self._set_cookies.append(
                    "until_analytics=" + ("yes" if choice == "yes" else "no")
                    + f"; Path=/; Max-Age=31536000; SameSite=Lax{sec}")
            back = (form.get("back", [""])[0] or "").strip()
            self._redirect("/consent" if back == "settings" else "/")
            return
        if CLOUD and self.path == "/beta":
            # 베타 초대 코드 확인 → 통과 쿠키(입력 코드별 해시 — 채널 코드 하나만
            # 교체/삭제해도 그 채널 쿠키만 무효).
            import hashlib
            form = self._read_form()
            codes = _beta_codes()
            got = (form.get("code", [""])[0] or "").strip()
            if codes and got in codes:
                # 채널별 유입 측정 — 호스팅 로그(Render 등)에서 코드별 집계.
                print(f"[beta] pass: {got}", flush=True)
                sec = "; Secure" if self._https() else ""
                want = hashlib.sha256(got.encode("utf-8")).hexdigest()[:32]
                self._set_cookies.append(
                    f"beta={want}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax{sec}")
                self._redirect("/")
            else:
                self._send(render_beta_gate(err=True), code=403, title="베타 · UNTIL")
            return
        if self.path == "/edit":
            # 사람이 직접 고친 본문 — AI 수정(/revise)과 별개 경로.
            form = self._read_form()
            tok = (form.get("session", [""])[0] or "").strip()
            simple = (form.get("ui", [""])[0] == "simple")
            if _get_session(tok) is None:
                self._send('<p>세션이 만료됐습니다. <a href="/">새로 시작</a></p>', code=404)
                return
            res = edit_session(tok, form.get("body", [""])[0] or "")
            self._admin_touch("edit")
            final = res is not None and res.final_draft is not None
            prefix = ("/svf/" if final else "/sv/") if simple else ("/vf/" if final else "/v/")
            self._redirect(f"{prefix}{tok}")
            return
        if self.path == "/submitted":
            # '올렸어요' 표시 — 사람이 눌렀다는 사실만 기록한다(전송 아님).
            form = self._read_form()
            tok = (form.get("session", [""])[0] or "").strip()
            if _get_session(tok) is None:
                self._send('<p>세션이 만료됐습니다. <a href="/">새로 시작</a></p>', code=404)
                return
            mark_submitted(tok, done=not form.get("undo"))
            self._admin_touch("submitted")
            self._redirect(f"/ready/{tok}")
            return
        if self.path == "/logout":
            self._read_form()           # 본문 드레인(폼 POST) 후 쿠키 정리
            self._logout()
            return
        if self.path == "/admin/login":
            import logging
            import os as _os
            from . import adminboard
            want = (_os.getenv("UNTIL_ADMIN_KEY") or "").strip()
            if not want:
                self._send("<p>Not Found</p>", code=404)
                return
            form = self._read_form()
            got = (form.get("key", [""])[0] or "").strip()
            if not secrets.compare_digest(got, want):
                logging.warning("admin login failed uid=%s at=%s",
                                _uid() or "local", adminboard._now_iso())
                self._send(adminboard.render_admin_login(), code=403,
                           title="관리자 로그인 · UNTIL")
                return
            sec = "; Secure" if self._https() else ""
            self._set_cookies.append(
                f"{adminboard.ADMIN_COOKIE}={adminboard.issue_admin_token(want)}; Path=/; "
                f"Max-Age={adminboard.ADMIN_TOKEN_TTL}; HttpOnly; SameSite=Strict{sec}")
            self._redirect("/admin")
            return
        cfg = Config(); cfg.backend = self.backend
        if self.path == "/draft":
            if CLOUD and not self._allow_manual_start_for_tests:
                self._drain_body()
                self._redirect("/connect?mode=fast")
                return
            # 본문을 먼저 읽는다(폼 파싱 겸 드레인) — 게이트가 안 읽고 303을 보내면
            # 업로드 중인 클라이언트가 RST를 받아 /plan 안내가 유실된다(413과 동일 수정).
            form, upload_files, too_large = self._read_form_and_files()
            if self._billing_gate():
                return
            if too_large:
                self._send('<p>첨부가 너무 커요(합계 25MB까지). '
                           '<a href="/simple">다시 시도</a></p>', code=413)
                return
            text = (form.get("assignment", [""])[0] or "").strip()
            simple = (form.get("ui", [""])[0] == "simple")
            new_mode = (form.get("mode", [""])[0] == "new")
            if new_mode and not text:
                # 과제 만들기(구조화 칸) → 붙여넣기와 똑같은 과제 텍스트로 조립.
                text = compose_assignment(form)
                if not (form.get("body", [""])[0] or "").strip():
                    self._send(render_new_assignment(
                        err="과제 설명은 채워 주세요 — 나머지 칸은 비워도 됩니다.",
                        form=form), code=400, title="과제 만들기 · UNTIL")
                    return
            if not text:
                # 붙여넣기 폼은 /simple에 있다(홈 초미니멀화로 홈에는 없음) —
                # 에러에서 홈으로 보내면 입력칸 없는 막다른 길.
                self._send('<p>과제 내용이 비었습니다. <a href="/simple">다시 입력</a></p>', code=400)
                return
            doc_files = [(fn, pl) for n, fn, pl in upload_files if n != "voice_files"]
            voice_files = [(fn, pl) for n, fn, pl in upload_files if n == "voice_files"]
            extra_sources, upload_warns = _sources_from_uploads(doc_files)
            voice_dir, voice_warns = _voice_dir_from_uploads(voice_files)
            upload_warns += voice_warns
            try:
                res = run_text(text, cfg, self.context_dirs,
                               extra_sources=extra_sources or None,
                               voice_dir=voice_dir)
            except Exception as e:  # 데모 안정성: 오류를 페이지로
                self._admin_touch("draft_fail:pipe")
                back = "/simple" if simple else "/"
                self._send(f'<p>{html.escape(user_error_message(e))}</p>'
                           f'<p><a href="{back}">처음으로</a></p>', code=500)
                return
            finally:
                if voice_dir:
                    import shutil as _sh
                    _sh.rmtree(voice_dir, ignore_errors=True)
            if upload_warns:  # 업로드 파싱 실패도 준비 점검·CLI와 같은 경고 채널로.
                res.capture_warnings = list(res.capture_warnings or []) + upload_warns
            token = _new_token()
            _SESSIONS[token] = res
            _telemetry_begin(token, res, source="manual", backend=cfg.backend)
            _persist_session(token)
            _telemetry_emit("draft", token, res)
            _log_feedback(res, cfg.backend)
            self._billing_record(res)
            simple = (form.get("ui", [""])[0] == "simple")
            self._redirect(f"/sv/{token}" if simple else f"/v/{token}")
        elif self.path == "/inbox":
            form = self._read_form()
            token = (form.get("token", [""])[0] or "").strip() or None
            if token:
                self._admin_touch("token_try", token=token)
            # 토큰 없음은 어댑터 예외(개발자용 문구)에 맡기지 않고 여기서 친절하게 안내.
            if not self.sso and not token and not _env_canvas_token():
                self._send(
                    '<div style="max-width:34rem;margin:2rem auto">'
                    '<p><b>eTL 액세스 토큰을 먼저 입력해 주세요.</b></p>'
                    '<p class="meta">eTL › 계정 › 설정 › <b>+ 새 액세스 토큰</b>에서 발급한 뒤, '
                    '홈 화면 맨 위 입력칸에 붙여넣고 다시 눌러 주세요. '
                    + ('토큰은 저장·기록하지 않고 이 세션의 메모리에만 잠시 머물러요.</p>'
                       if CLOUD else
                       '토큰은 이 컴퓨터의 서버에만 머물고 밖으로 나가지 않아요.</p>')
                    + '<p><a class="btn" href="/">← 홈에서 토큰 입력하기</a></p></div>',
                    code=400)
                return
            # 필터는 홈 체크박스 2개(미제출만·기한 지난 숨기기)가 전부 — 인박스
            # 자체 필터 바는 선택지 과잉이라 제거(사용자 피드백). 정렬=임박순 고정.
            # 연습 모드(practice) — 이미 낸 과제로 딸깍을 재현하는 명시적 행동이라
            # 미제출·기한 필터를 끄고 pick_practice로 고른다.
            practice = bool(form.get("practice"))
            # UX 테스트용 '전부 보기' — 폼이 의도를 표시해도 **토큰 지문이 허용
            # 목록에 있을 때만** 연다(fail-closed). 아니면 평소 필터 그대로.
            want_all = bool(form.get("all")) and test_all_assignments_allowed(token)
            only_unsub = (bool(form.get("unsubmitted"))
                          and not practice and not want_all)
            fast = bool(form.get("fast")) or practice
            # SSO 모드는 스레드 고정이 필요(Playwright) → max_workers=1 순차 조회.
            workers = 1 if self.sso else 8
            import time as _time
            # WS 제출상태 보강(enrich) 여부까지 키에 — 빠지면 보강 안 된 캐시를
            # fast(⚡ 바로 초안)가 히트해 이미 제출한 과제를 자동 초안+과금하는
            # 회귀(3011행 주석이 막은 바로 그 결함)가 캐시 경로로 재유입된다.
            ws_enrich_req = self.ws and (only_unsub or fast)
            cache_key = ("sso" if self.sso else (token or _env_canvas_token()),
                         only_unsub, ws_enrich_req)
            cached = _INBOX_CACHE.get(cache_key)
            learned = (0, 0)  # (문체 표본, 피드백 건) — 캐시 히트면 (0,0)
            if cached and _time.time() - cached[0] < _INBOX_TTL:
                items, note, fell_back = list(cached[1]), cached[2], cached[3]
                inbox_anns = list(cached[4]) if len(cached) > 4 else []
                inbox_extras = list(cached[5]) if len(cached) > 5 else []
            else:
                inbox_anns = []
                inbox_extras = []
                try:
                    from .capture.sources.discovery import EtlInbox
                    if self.sso:
                        adapter = _sso_adapter()
                        inbox = EtlInbox(adapter, base_url=adapter.base_url)
                    elif self.ws:
                        from .capture.sources.moodle_ws import MoodleWsAdapter
                        adapter = MoodleWsAdapter(etl_ws_base(),
                                                  token=token or _env_canvas_token())
                        inbox = EtlInbox(adapter, base_url=adapter.base_url)
                    else:
                        from .capture.sources.canvas_api import CanvasApiAdapter
                        adapter = CanvasApiAdapter(token=token)
                        inbox = EtlInbox(adapter)
                    note = ""
                    fell_back = False
                    # WS는 mod_assign_get_assignments가 제출 상태를 주지 않아 submitted가
                    # 항상 False → '미제출만' 필터가 무력화되고 /quick이 이미 제출한 과제를
                    # 자동 초안(+과금)할 수 있다(리뷰 발견). 정말 필요할 때만(필터·quick)
                    # 제출 상태를 조회로 채운다(과제당 1콜, 병렬·60초 캐시).
                    ws_enrich = ws_enrich_req
                    items = inbox.list_assignments(
                        bucket=None, only_unsubmitted=(only_unsub and not ws_enrich),
                        max_workers=workers)
                    if ws_enrich:
                        adapter.enrich_submitted(items, max_workers=workers)
                    if only_unsub:
                        filtered = [a for a in items if not a.submitted] if ws_enrich else items
                        if ws_enrich and not filtered:  # 미제출이 없으면 전체로 폴백
                            note = "미제출 과제가 없어 전체 과제를 표시합니다."
                            fell_back = True
                        else:
                            items = filtered
                    if only_unsub and not ws_enrich and not items:  # 서버측 필터가 빈 경우
                        items = inbox.list_assignments(bucket=None, only_unsubmitted=False,
                                                       max_workers=workers)
                        note = "미제출 과제가 없어 전체 과제를 표시합니다."
                        fell_back = True
                    # 최신 공지 동봉(과목 앞쪽 몇 개만 — 지연 방어). 어댑터가
                    # 공지를 지원할 때만(WS + Canvas REST. SSO는 미지원→빈 목록).
                    inbox_anns = collect_inbox_announcements(adapter, items)
                    # 플래너 '그 외 마감'(퀴즈·토론·이벤트) — 과제 외 할 일 보조
                    # 표시. 지원 어댑터(Canvas 토큰)만, 실패는 조용히 빈 목록.
                    if hasattr(adapter, "list_planner_items"):
                        try:
                            from .capture.sources.discovery import SNU_ETL_BASE
                            inbox_extras = adapter.list_planner_items(
                                getattr(inbox, "base_url", "") or SNU_ETL_BASE)
                        except Exception:
                            inbox_extras = []
                    # LMS가 이미 아는 값(이름·이메일)으로 프로필 빈 필드 자동 보충 —
                    # 되묻지 않기 위한 편의 기능, 실패는 비치명적. 직접 저장 값은 유지.
                    if not self.sso and not self.ws:
                        try:
                            from .profile import merge_from_lms
                            from .capture.sources.discovery import SNU_ETL_BASE
                            me = adapter.get_self_profile(SNU_ETL_BASE)
                            from .profile import student_id_from_lms_profile
                            merge_from_lms({"name": me.get("name", ""),
                                            "email": me.get("primary_email", ""),
                                            "student_id": student_id_from_lms_profile(me)})
                        except Exception:
                            pass
                    # 첫 연결 시 문체+교수 피드백 자동 학습 — 제출했던 과제에서
                    # 추출·저장(원문 미보관). 이미 학습됨/끔/미지원이면 즉시 통과.
                    from .capture.sources.discovery import SNU_ETL_BASE as _BASE
                    learned = _maybe_autolearn_etl(
                        adapter, getattr(inbox, "base_url", "") or _BASE)
                    # 관리자 보드 — eTL 연결 성공 1건(토큰은 지문만 기록).
                    self._admin_touch("inbox", token=token or "")
                except ValueError as e:
                    from . import adminboard
                    self._admin_touch(adminboard.inbox_failure_event(e), token=token or "")
                    self._send(f'<p>{html.escape(str(e))}</p><p><a href="/">처음으로</a></p>', code=400)
                    return
                except Exception as e:
                    from . import adminboard
                    self._admin_touch(adminboard.inbox_failure_event(e), token=token or "")
                    if is_etl_auth_error(e):
                        self._send(render_etl_auth_error(), code=401,
                                   title="eTL 재연결 · UNTIL")
                        return
                    self._send(f'<p>{html.escape(user_error_message(e, "eTL 과제 목록을 불러오"))}</p>'
                               '<p><a href="/">처음으로</a></p>', code=502)
                    return
                if len(_INBOX_CACHE) > 8:  # 무한 증식 방지(단일 사용자 로컬)
                    _INBOX_CACHE.clear()
                _INBOX_CACHE[cache_key] = (_time.time(), list(items), note, fell_back,
                                           list(inbox_anns), list(inbox_extras))
            if any(learned):  # 캐시에 안 넣음 — 학습 완료 안내는 이번 응답 1회만
                nv, nf = learned
                bits = []
                if nv:
                    from .context.voice_autolearn import load_stored_voice_stats
                    vs = load_stored_voice_stats(_voice_store_path())
                    if vs:
                        qualifier = "" if vs.get("submitted_total_exact") else "최소 "
                        bits.append(
                            f"최근 {vs.get('courses_scanned', 0)}/"
                            f"{vs.get('courses_total', 0)}과목에서 제출 완료 "
                            f"{qualifier}{vs.get('submitted_total', 0)}건 조회, "
                            f"학습 가능 {vs.get('eligible_submissions', 0)}건 중 "
                            f"표본 {nv}개로 내 문체")
                    else:
                        bits.append(f"표본 {nv}개로 내 문체")
                if nf:
                    bits.append(f"교수 피드백 {nf}건")
                note = ((note + " ") if note else "") + (
                    f"{'와 '.join(bits)}을 배웠어요 — 다음 초안부터 반영됩니다.")
            hide_past = (bool(form.get("hide_past"))
                         and not practice and not want_all)
            elice_warnings = []
            items = merge_elice_inbox(items, bucket=None, only_unsubmitted=only_unsub,
                                      max_workers=workers, warnings=elice_warnings)
            if elice_warnings:
                note = ((note + " ") if note else "") + " ".join(elice_warnings)
            n_before = len(items)
            # 폴백된 목록(미제출 0건 → 전체 표시)은 상태 필터를 다시 적용하면
            # 도로 비어 버린다 — 폴백 취지대로 전체를 보여준다.
            eff_status = "all" if (fell_back or not only_unsub) else "todo"
            items = _filter_sort_inbox(items, status=eff_status, hide_past=hide_past)
            if n_before and not items:
                note = (note + " " if note else "") + "필터에 걸리는 과제가 없어요 — 필터를 풀어 보세요."
            # 토큰은 HTML에 노출하지 않고 sid로만 전달(서버에 보관). SSO는 토큰 자체가 없음.
            sid = ""
            if token and not self.sso:
                sid = _new_token(); _store_canvas_token(sid, token)
            if practice:
                from .inbox_policy import pick_practice
                self._fast_draft(items, note, cfg, token=token, sid=sid,
                                 picker=pick_practice)
                return
            if form.get("fast"):
                if fell_back:
                    # '미제출만'에서 폴백된 목록은 전부 제출 완료 — 이미 낸 과제를
                    # 조용히 초안 생성(+한도 차감)하지 않고 목록에서 직접 고르게 한다.
                    self._send(render_inbox(items, sid=sid,
                                            note=note + " 바로 초안 대신 아래에서 직접 골라 주세요.",
                                            simple=True),
                               title="내 과제 · UNTIL")
                    return
                self._fast_draft(items, note, cfg, token=token, sid=sid)
                return
            simple = (form.get("ui", [""])[0] == "simple")
            self._send(render_inbox(items, sid=sid, note=note, simple=simple,
                                    announcements=inbox_anns,
                                    extras=inbox_extras),
                       title="내 과제 · UNTIL")
        elif self.path == "/pick":
            form = self._read_form()  # 게이트 전에 본문 소비(303 유실 방지 일관)
            self._admin_touch("assign_open")
            if self._billing_gate():
                return
            url = (form.get("url", [""])[0] or "").strip()
            token = (form.get("token", [""])[0] or "").strip() or None
            if not token:  # 인박스에서 온 경우 sid로 서버 보관 토큰 조회
                token = _get_canvas_token((form.get("sid", [""])[0] or "").strip()) or None
            if not url:
                self._send('<p>과제를 선택하세요. <a href="/">처음으로</a></p>', code=400)
                return
            if CLOUD and not self.sso and not token:
                # 어댑터 계층의 env 토큰 폴백까지 도달 금지(운영자 계정 노출 방지).
                # sid는 메모리에만 있어 컨테이너 재시작 시 사라진다 — 재입력 안내.
                self._send('<p><b>토큰 세션이 만료됐어요.</b> 홈에서 eTL 토큰을 '
                           '다시 입력하고 과제를 불러와 주세요.</p>'
                           '<p><a class="btn" href="/">← 처음으로</a></p>', code=400)
                return
            try:
                adapter = _sso_adapter() if self.sso else None
                res = collect_with_materials(url, cfg, token=token, adapter=adapter,
                                             context_dirs=self.context_dirs, ws=self.ws)
            except ValueError as e:
                self._admin_touch("draft_fail:pipe")
                self._send(f'<p>{html.escape(str(e))}</p><p><a href="/">처음으로</a></p>', code=400)
                return
            except Exception as e:
                self._admin_touch("draft_fail:pipe")
                self._send(f'<p>{html.escape(user_error_message(e, "과제를 수집"))}</p>'
                           '<p><a href="/">처음으로</a></p>', code=502)
                return
            tok = _new_token()
            _SESSIONS[tok] = res
            if token:
                _store_canvas_token(tok, token)
            _telemetry_begin(tok, res, source="etl", backend=cfg.backend, url=url)
            _persist_session(tok)
            _telemetry_emit("draft", tok, res)
            _log_feedback(res, cfg.backend)
            self._billing_record(res)
            simple = (form.get("ui", [""])[0] == "simple")
            self._redirect(f"/sv/{tok}" if simple else f"/v/{tok}")
        elif self.path == "/collect":
            form = self._read_form()  # 게이트 전에 본문 소비(303 유실 방지 일관)
            self._admin_touch("assign_open")
            if self._billing_gate():
                return
            url = (form.get("url", [""])[0] or "").strip()
            token = (form.get("token", [""])[0] or "").strip() or None
            files_merge = bool(form.get("files"))
            if not url:
                self._send('<p>과제 URL이 비었습니다. <a href="/">다시 입력</a></p>', code=400)
                return
            if CLOUD and not self.sso and not token:
                # /pick과 동일 — env 토큰 폴백 도달 금지.
                self._send('<p><b>eTL 토큰이 필요해요.</b> 홈에서 토큰과 함께 '
                           '다시 시도해 주세요.</p>'
                           '<p><a class="btn" href="/">← 처음으로</a></p>', code=400)
                return
            try:
                if self.sso:  # SSO 세션으로 수집(관련자료까지 함께).
                    res = collect_with_materials(url, cfg, adapter=_sso_adapter(),
                                                 context_dirs=self.context_dirs)
                elif self.ws:  # Moodle WS로 수집(관련자료·공지까지 함께).
                    res = collect_with_materials(url, cfg, token=token,
                                                 context_dirs=self.context_dirs, ws=True)
                else:
                    res = collect_canvas(url, cfg, token=token, include_course_files=files_merge,
                                         context_dirs=self.context_dirs)
            except ValueError as e:  # 토큰 없음/URL 형식 오류
                self._admin_touch("draft_fail:pipe")
                self._send(f'<p>{html.escape(str(e))}</p><p><a href="/">처음으로</a></p>', code=400)
                return
            except Exception as e:  # 네트워크/HTTP 오류
                self._admin_touch("draft_fail:pipe")
                self._send(f'<p>{html.escape(user_error_message(e, "eTL 과제를 수집"))}</p>'
                           '<p><a href="/">처음으로</a></p>', code=502)
                return
            token2 = _new_token()
            _SESSIONS[token2] = res
            if token:
                _store_canvas_token(token2, token)
            _telemetry_begin(token2, res, source="etl", backend=cfg.backend, url=url)
            _persist_session(token2)
            _telemetry_emit("draft", token2, res)
            _log_feedback(res, cfg.backend)
            self._billing_record(res)
            self._redirect(f"/v/{token2}")
        elif self.path == "/rate":
            # 완성본 만족도(1~5) — 세션당 1회, 피드백 로그에 적립(베타 핵심 지표).
            form = self._read_form()
            token = form.get("session", [""])[0]
            simple = form.get("ui", [""])[0] == "simple"
            try:
                score = int(form.get("score", ["0"])[0])
            except (TypeError, ValueError):
                score = 0
            res = _get_session(token)
            if res is None or not (1 <= score <= 5):
                self._send('<p>세션이 만료됐거나 잘못된 요청이에요. '
                           '<a href="/">처음으로</a></p>', code=400)
                return
            if token not in _RATINGS:
                _RATINGS[token] = score
                try:
                    from .feedback import append_record, record_from_result
                    append_record(record_from_result(
                        res, satisfaction=score, backend=f"{self.backend}+rated"))
                except Exception:
                    pass  # 로그 실패가 사용자 흐름을 막지 않는다
                # 수락 여부의 증거 — 5점 만점 중 4~5는 수락, 1~2는 거절로 본다.
                # 3점은 판단하지 않는다(모호한 신호를 양쪽 어디로도 밀지 않는다).
                if score >= 4 or score <= 2:
                    try:
                        from .persona.events import update_acceptance_for_result
                        update_acceptance_for_result(res, score >= 4, channel="web")
                    except Exception:
                        pass
            self._redirect(f"/svf/{token}" if simple else f"/vf/{token}")
        elif self.path == "/rate/voice":
            form = self._read_form()
            token = form.get("session", [""])[0]
            value = form.get("match", [""])[0]
            csrf = form.get("csrf", [""])[0]
            simple = form.get("ui", [""])[0] == "simple"
            res = _get_session(token)
            import hmac
            if (res is None or value not in ("yes", "no") or not _voice_applied(res)
                    or not hmac.compare_digest(csrf, _voice_csrf(token))):
                self._send('<p>세션이 만료됐거나 잘못된 요청이에요.</p>', code=400)
                return
            record_voice_rating(token, res, value == "yes", backend=self.backend)
            self._redirect(f"/svf/{token}" if simple else f"/vf/{token}")
        elif self.path == "/sessions/delete":
            form = self._read_form()
            delete_session(form.get("token", [""])[0])
            self._redirect("/sessions")
        elif self.path in ("/voice/off", "/voice/relearn"):
            # 자동 학습(문체+피드백) 통제 — off=disabled 저장(재수집 안 함),
            # relearn=파일 삭제(다음 eTL 인박스에서 다시 학습).
            form = self._read_form()
            try:
                from .context.teacher_feedback import (clear_feedback,
                                                       disable_feedback)
                from .context.voice_autolearn import (clear_stored_voice,
                                                      disable_stored_voice)
                if self.path == "/voice/off":
                    disable_stored_voice(_voice_store_path())
                    disable_feedback(_feedback_store_path())
                else:
                    clear_stored_voice(_voice_store_path())
                    clear_feedback(_feedback_store_path())
            except OSError:
                pass
            back = (form.get("session", [""])[0] or "").strip()
            self._redirect(f"/v/{back}" if _TOKEN_RE.match(back) else "/")
        elif self.path == "/history/clear":
            # 내 답 히스토리 전체 삭제(개인정보 통제, 비치명적).
            # history_path() = 요청 스코프 경로(클라우드에선 그 사용자 파일만).
            try:
                from .context.answer_history import history_path
                hp = history_path()
                if hp.exists():
                    hp.unlink()
            except OSError:
                pass
            self._redirect("/history")
        elif self.path == "/data/delete":
            # 사용자별 **전체** 삭제 — 지금까지 삭제가 3개 라우트에 흩어져 있고
            # profile·feedback·telemetry·credits는 어느 경로로도 지워지지 않았다.
            # 대상 목록은 persona/retention.py 한 곳에만 있다(빠뜨림 방지).
            form = self._read_form()
            if (form.get("confirm", [""])[0] or "").strip() != "삭제":
                self._send('<p>확인 문구가 일치하지 않아 아무것도 지우지 않았습니다.</p>'
                           '<p><a href="/">처음으로</a></p>', code=400)
                return
            from .persona.retention import delete_all_user_data, kv_keys_for
            uid = _uid()
            root = _user_root(uid) if uid else _Path("_until_work")
            report = delete_all_user_data(root)
            # 세션(디스크)과 KV 미러도 함께 — 파일만 지우면 다음 하이드레이션이
            # 미러에서 전부 되살린다("지웠다"는 약속을 조용히 어기는 경로).
            try:
                for f in sorted(_sess_dir().glob("*.json")):
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
                        # 세션 미러는 sess:<uid>:<token> 형태라 접두사로 훑어 지운다.
                        keys, _definitive = client.list_keys_checked(
                            f"sess:{uid}:", limit=200)
                        for name in keys:
                            cloudkv.delete_async(name)
                except Exception:
                    pass
            _SESSIONS.clear(); _ANSWERS.clear(); _AUTOFILLED.clear()
            _SUGGESTIONS.clear()
            _REVIEWS.clear(); _WORKSPACES.clear(); _TELEMETRY_META.clear()
            body = ("<p>개인 데이터를 삭제했습니다 — " + html.escape(report.headline)
                    + ".</p>")
            if not report.ok:
                body += ("<p>지우지 못한 파일이 있습니다: "
                         + html.escape(", ".join(sorted(report.failed))) + "</p>")
            self._send(body + '<p><a href="/">처음으로</a></p>',
                       code=200 if report.ok else 500)
        elif self.path == "/plan/activate":
            form = self._read_form()
            if CLOUD:
                # 라이선스 파일은 서버 전체 공유 — 한 사용자의 키가 전원 pro가 되는
                # 사고 방지. 클라우드 플랜 업그레이드는 결제 링크 흐름으로만.
                self._redirect("/plan?err=1")
                return
            from . import billing
            ok = billing.activate_license(form.get("license", [""])[0])
            # 실패(짧은 키·저장 오류)를 조용히 삼키지 않고 페이지에 알린다.
            self._redirect("/plan" if ok else "/plan?err=1")
        elif self.path == "/profile":
            form = self._read_form()
            from .profile import save_profile, FIELDS
            save_profile({k: form.get(k, [""])[0] for k, _, _ in FIELDS})
            self._redirect("/profile?saved=1")
        elif self.path == "/profile/etl-forget":
            _forget_token()
            self._redirect("/profile?courses=1")
        elif self.path == "/profile/courses":
            form = self._read_form()
            from .context.course_profiles import save_course_profiles
            try:
                save_course_profiles(course_rows_from_form(form))
            except OSError:
                pass  # 저장 실패는 비치명 — 프로파일은 있으면 좋은 폴백이다.
            self._redirect("/profile?courses=1")
        elif self.path == "/profile/tone":
            # 말투 **명시 지정** — 자동 추론과 분리된 사용자 경로. 빈 값이면 해제해
            # 자동 추론으로 돌아간다(끄는 길이 없으면 못박기가 함정이 된다).
            form = self._read_form()
            want = (form.get("register", [""])[0] or "").strip()
            try:
                from .context.tone import REGISTER_PRESETS, load_persona, save_persona
                store = load_persona()
                store.pinned_register = want if want in REGISTER_PRESETS else ""
                save_persona(store)
            except Exception:
                pass
            self._redirect("/profile?saved=1")
        elif self.path == "/plan/redeem":
            form = self._read_form()
            from . import billing
            ok, _bal, _m = billing.redeem(form.get("code", [""])[0])
            # 성공하면 잔액이 바뀌었다 — _end_request의 _mirror_user가 credits.json을
            # KV로 지속화한다(클라우드, 컨테이너 재시작에도 잔액 유지).
            self._redirect("/plan?ok=1" if ok else "/plan?err=redeem")
        elif self.path == "/suggest":
            form = self._read_form()
            token = form.get("session", [""])[0]
            simple = (form.get("ui", [""])[0] == "simple")
            back = f"/sv/{token}" if simple else f"/v/{token}"
            res = _get_session(token)
            if res is None:
                self._send('<p>세션이 만료됐습니다. <a href="/">새로 시작</a></p>', code=400)
                return
            # 폼에 이미 타이핑해 둔 답은 '내 답'으로 확정해 둔다 — 제안 왕복에서
            # 날아가면 사용자는 같은 걸 두 번 쓰게 된다.
            n = len(res.draft.decisions)
            typed = _answers_from_form(form, n)
            mine = dict(_ANSWERS.get(token) or {})
            mine.update({i: v for i, v in typed.items() if v.strip()})
            if mine:
                _ANSWERS[token] = mine
            # 아직 비어 있는 번호에만 제안한다. 내가 정한 답은 맥락으로 넘겨
            # 남은 칸이 그 논지·범위·톤과 어긋나지 않게 이어지도록 한다.
            blanks = [i for i in range(1, n + 1) if not (mine.get(i) or "").strip()]
            try:
                fresh = suggest_decision_answers(res, cfg, my_answers=mine or None,
                                                 only=blanks if mine else None)
            except Exception as e:
                self._send(f'<p>{html.escape(user_error_message(e, "AI 제안을 생성"))}</p>'
                           f'<p><a href="{back}">돌아가기</a></p>', code=500)
                return
            merged = dict(_SUGGESTIONS.get(token) or {})
            merged.update(fresh)
            # 내가 직접 쓴 칸의 묵은 제안은 지운다(프리필이 내 답을 가리지 않게).
            for i in list(merged):
                if (mine.get(i) or "").strip():
                    merged.pop(i, None)
            _SUGGESTIONS[token] = merged
            _persist_session(token)
            self._redirect(back)  # PRG → 제안이 채워진 화면
        elif self.path == "/review":
            form = self._read_form()
            token = form.get("session", [""])[0]
            res = _get_session(token)
            if res is None:
                self._send('<p>세션이 만료됐습니다. <a href="/">새로 시작</a></p>', code=400)
                return
            try:
                _REVIEWS[token] = review_result(res, cfg)
                _persist_session(token)
            except Exception as e:
                self._send(f'<p>{html.escape(user_error_message(e, "완성도를 점검"))}</p>'
                           '<p><a href="/">처음으로</a></p>', code=500)
                return
            _telemetry_emit("review", token, res)
            self._redirect(f"/v/{token}")  # PRG → 점검 결과 붙은 초안 페이지
        elif self.path == "/revise":
            form = self._read_form()
            token = form.get("session", [""])[0]
            mode = form.get("mode", [""])[0]
            try:
                paragraph = int(form.get("paragraph", ["0"])[0] or 0)
                excluded = [int(key.split("_", 1)[1]) for key in form
                            if key.startswith("exclude_") and form.get(key, [""])[0]]
                revise_session(token, cfg, mode=mode, paragraph=paragraph,
                               instruction=form.get("instruction", [""])[0],
                               excluded_sources=excluded)
            except (ValueError, TypeError) as e:
                self._send(f'<p>{html.escape(user_error_message(e, "초안을 수정"))}</p>'
                           '<p><a href="/">처음으로</a></p>', code=400)
                return
            self._redirect(f"/v/{token}")
        elif self.path == "/finalize":
            form = self._read_form()
            token = form.get("session", [""])[0]
            res = _get_session(token)
            if res is None:
                self._send('<p>세션이 만료됐습니다. <a href="/">새로 시작</a></p>', code=400)
                return
            # 누적 답변(이전에 답한 것) + 이번 폼 답변을 원본 초안 인덱스 기준으로 병합.
            prior = _ANSWERS.get(token, {})
            form_answers = _answers_from_form(form, res.draft.n_decisions)
            merged = {**prior, **form_answers}
            self._admin_touch_many(
                "decision_ans", sum(index not in prior for index in form_answers))
            self._admin_touch_many("decision_skip", res.draft.n_decisions - len(merged))
            # 비워 둔 칸은 AI가 채운다(사용자 지시) — 채운 사실은 화면에 밝힌다.
            merged, autofilled = _fill_blank_decisions(token, res, merged, cfg)
            self._admin_touch_many("decision_autofill", len(autofilled))
            _ANSWERS[token] = merged
            # 이번에 새로/다르게 답한 것(delta)만 히스토리 적립 — 라운드마다 전체를
            # 재적립하면 중복이 prune 지평을 잠식한다(비치명적).
            # **사람이 직접 쓴 답만** 적립한다: AI가 채운 값을 '내 지난 답'으로
            # 학습하면 다음 과제의 제안이 자기 출력을 근거로 삼는다(에코 챔버).
            try:
                from .context.answer_history import record_answers
                delta = {i: a for i, a in form_answers.items() if prior.get(i) != a}
                record_answers([d.note for d in res.draft.decisions], delta)
            except Exception:
                pass
            try:
                was_final = res.final_draft is not None
                res = finalize(res, merged, cfg, channel="web")
                if res.final_draft is not None:
                    from .prompts.suggest import suggest_prompts
                    res.suggested_prompts = suggest_prompts(res.final_draft)
            except Exception as e:
                self._send(f'<p>{html.escape(user_error_message(e, "최종본을 생성"))}</p><p><a href="/">처음으로</a></p>', code=500)
                return
            _maybe_run_code_check(res)
            _SESSIONS[token] = res
            meta = _TELEMETRY_META.get(token)
            if meta is not None and was_final:
                meta["revision_count"] = int(meta.get("revision_count") or 0) + 1
            _persist_session(token)
            _telemetry_emit("final", token, res)
            self._admin_touch("final")
            simple = (form.get("ui", [""])[0] == "simple")
            self._redirect(f"/svf/{token}" if simple else f"/vf/{token}")
        else:
            self._send('<p>없는 경로입니다. <a href="/">처음으로</a></p>', code=404)


def run_text(text: str, cfg: Config, context_dirs: Optional[dict] = None,
             extra_sources: Optional[list] = None,
             voice_dir: Optional[str] = None) -> Result:
    """붙여넣은 과제 텍스트를 임시 파일로 파이프라인에 흘려보낸다(맥락 주입 옵션).

    extra_sources: 업로드한 '내 자료'(SourceDoc 목록) — 근거 자료로 함께 주입.
    voice_dir: 업로드한 '내가 쓴 글' 임시 폴더 — 서버 기본 voice보다 우선."""
    import tempfile, os
    c = context_dirs or {}
    fd, path = tempfile.mkstemp(suffix=".txt", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        res = run([path], cfg,
                  course_dir=c.get("course_materials"),
                  my_files_dir=c.get("my_files"),
                  voice_dir=voice_dir or c.get("voice"),
                  voice_profile=_stored_voice()[0],  # 자동 학습분(voice_dir가 우선)
                  feedback_hint=_stored_feedback_hint(),
                  extra_context_sources=extra_sources)
        res.teacher_feedback = _stored_feedback()[0]  # 준비 점검 '피드백' 항목용
        return res
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


_UPLOAD_DOC_CHARS = 8000  # 자료 1건당 주입 상한(프롬프트 폭주 방지)
_UPLOAD_MAX_FILES = 5


def _voice_dir_from_uploads(files) -> tuple:
    """업로드한 '내가 쓴 글' → 문체 프로파일용 임시 폴더(.txt 변환 저장).

    반환 (폴더 경로 또는 None, 경고 목록). 호출자가 run 후 폴더를 정리한다.
    docx/hwpx 등도 ingest로 텍스트를 뽑아 .txt로 저장(voice_from_dir는 txt/md만 읽음)."""
    import tempfile
    from .capture.ingest import ingest_file
    from .capture.sources.models import safe_filename
    warns = []
    if not files:
        return None, warns
    workdir = tempfile.mkdtemp(prefix="until_voice_")
    n_ok = 0
    for fn, payload in files[:_UPLOAD_MAX_FILES]:
        name = safe_filename(fn or "내글")
        src = _Path(workdir) / name
        try:
            src.write_bytes(payload)
            body = ingest_file(src).text.strip()
            if not body:
                raise ValueError("본문이 비어 있음")
            (_Path(workdir) / f"{name}.voice.txt").write_text(body, encoding="utf-8")
            n_ok += 1
        except Exception as e:
            warns.append(f"{name}: 문체 참고용으로 읽지 못함({e})")
        finally:
            try:
                src.unlink()  # 원본 제거 — .voice.txt만 남겨 이중 집계 방지
            except OSError:
                pass
    if n_ok == 0:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)
        return None, warns
    return workdir, warns


def _sources_from_uploads(files) -> tuple:
    """업로드 파일들 → (SourceDoc 목록, 파싱 실패 경고 목록). 결정적·LLM 0.

    임시 폴더에 안전한 이름으로 저장 후 ingest(내장 폴백: docx/pptx/html/hwpx/pdf/txt).
    실패 파일은 초안이 그 자료 없이 작성됨을 알리는 경고로 표면화."""
    import shutil
    import tempfile
    from .capture.ingest import ingest_file
    from .capture.sources.models import safe_filename
    from .llm.base import SourceDoc
    sources, warns = [], []
    if not files:
        return sources, warns
    workdir = tempfile.mkdtemp(prefix="until_upload_")
    used = set()
    try:
        for fn, payload in files[:_UPLOAD_MAX_FILES]:
            name = safe_filename(fn or "자료")
            if name in used:  # 같은 이름 2개 → 접미사로 구분(범례 혼동 방지)
                stem, dot, ext = name.rpartition(".")
                base = stem if dot else name
                suffix = f".{ext}" if dot else ""
                k = 2
                while f"{base}({k}){suffix}" in used:
                    k += 1
                name = f"{base}({k}){suffix}"
            used.add(name)
            p = _Path(workdir) / name
            try:
                p.write_bytes(payload)
                doc = ingest_file(p)
                body = doc.text.strip()
                if not body:
                    raise ValueError("본문이 비어 있음")
                if len(body) > _UPLOAD_DOC_CHARS:
                    body = body[:_UPLOAD_DOC_CHARS] + "\n…(뒷부분 생략)"
                sources.append(SourceDoc(title=f"[내 자료] {name}", text=body))
            except Exception as e:
                warns.append(f"{name}: 파싱하지 못해 이 자료 없이 작성됨({e})")
        if len(files) > _UPLOAD_MAX_FILES:
            warns.append(f"자료는 한 번에 {_UPLOAD_MAX_FILES}개까지 — "
                         f"{len(files) - _UPLOAD_MAX_FILES}개는 건너뜀")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return sources, warns


def collect_canvas(url: str, cfg: Config, *, token: Optional[str] = None,
                   include_course_files: bool = False,
                   context_dirs: Optional[dict] = None) -> Result:
    """Canvas REST API로 과제를 수집해 파이프라인을 돌린다(브라우저 불필요)."""
    import tempfile, shutil
    from .capture.sources.collect import collect_canvas_api_to_files
    workdir = tempfile.mkdtemp(prefix="until_web_")
    try:
        collected, files = collect_canvas_api_to_files(
            url, workdir, token=token, include_course_files=include_course_files)
        # 과목명은 라우팅 전에 필요하다(§3 폴백) — 여기서 버리면 못 켠다.
        res = run(files, cfg, course_name=(getattr(collected, "course", "") or "").strip(),
                  **_ctx_kwargs(context_dirs))
        res.teacher_feedback = _stored_feedback()[0]
        return res
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _log_feedback(result: Result, backend: str) -> None:
    """웹에서 만든 초안도 P7 피드백 로그에 적립(비치명적). GEPA 입력으로 재사용."""
    try:
        from .feedback import record_from_result, append_record
        append_record(record_from_result(result, backend=backend))
    except Exception:
        pass


def _ctx_kwargs(context_dirs: Optional[dict]) -> dict:
    c = context_dirs or {}
    return {"course_dir": c.get("course_materials"),
            "my_files_dir": c.get("my_files"), "voice_dir": c.get("voice"),
            "voice_profile": _stored_voice()[0],  # eTL 자동 학습분(voice_dir가 우선)
            "feedback_hint": _stored_feedback_hint()}


def etl_ws_base() -> str:
    """Moodle WS eTL 베이스 URL(env UNTIL_ETL_BASE, 기본 SNU eTL)."""
    import os
    from .capture.sources.discovery import SNU_ETL_BASE
    return (os.getenv("UNTIL_ETL_BASE") or SNU_ETL_BASE).strip()


def _resolve_course(adapter, url: str):
    """(base_url, course_id)를 어댑터 종류와 무관하게 해석.

    Moodle WS 어댑터는 course_id_for_url(캐시/URL courseid)로, Canvas 어댑터는
    URL 파싱(parse_assignment_url)으로 course_id를 얻는다."""
    if hasattr(adapter, "course_id_for_url"):
        cid = adapter.course_id_for_url(url)
        return getattr(adapter, "base_url", ""), cid
    from .capture.sources.canvas_api import parse_assignment_url
    base, cid, _aid = parse_assignment_url(url)
    return base, cid


def _auto_download_on() -> bool:
    """강의자료 자동 다운로드(본문 발췌) on/off. 기본 on, UNTIL_ETL_AUTODOWNLOAD=0으로 끔."""
    import os
    return (os.getenv("UNTIL_ETL_AUTODOWNLOAD", "1") or "1").strip() != "0"


def collect_inbox_announcements(adapter, items: list, *, cap_courses: int = 6,
                                limit: int = 6) -> list:
    """인박스에 보여줄 최신 공지 — 인박스 과목 중 앞쪽 몇 개만(지연·비용 방어).

    adapter가 collect_announcements를 지원할 때만(=Moodle WS). 실패는 조용히 무시."""
    if not items or not hasattr(adapter, "collect_announcements"):
        return []
    from .capture.sources.models import CourseRef
    seen, courses = set(), []
    for a in items:  # 인박스는 마감 임박순 — 앞쪽 과목 우선
        cid = getattr(a, "course_id", "")
        if cid and cid not in seen:
            seen.add(cid)
            courses.append(CourseRef(id=cid, name=getattr(a, "course_name", "") or ""))
        if len(courses) >= cap_courses:
            break
    anns = []
    for c in courses:
        try:
            anns.extend(adapter.collect_announcements(c, limit=3, news_only=True))
        except Exception:
            continue
    anns.sort(key=lambda x: getattr(x, "created_iso", "") or "", reverse=True)
    return anns[:limit]


def merge_elice_inbox(items: list, *, bucket=None, only_unsubmitted: bool = False,
                       max_workers: int = 8, adapter=None,
                       warnings: Optional[list] = None) -> list:
    """옵트인 Elice 과제를 주 인박스에 병합. Elice 실패는 주 결과에 영향 없음."""
    if adapter is None:
        if os.getenv("UNTIL_ELICE") != "1" or not os.getenv("UNTIL_ELICE_TOKEN", "").strip():
            return items
        try:
            from .capture.sources.elice_api import EliceAdapter
            adapter = EliceAdapter()
        except Exception:
            if warnings is not None:
                warnings.append("Elice 연결을 준비하지 못해 eTL 과제만 표시합니다.")
            return items
    try:
        from .capture.sources.discovery import EtlInbox
        extra = EtlInbox(adapter, base_url="https://snu.elice.io").list_assignments(
            bucket=bucket, only_unsubmitted=only_unsubmitted, max_workers=max_workers)
    except Exception:
        if warnings is not None:
            warnings.append("Elice 과제를 불러오지 못해 eTL 과제만 표시합니다.")
        return items
    if warnings is not None:
        warnings.extend(str(x) for x in getattr(adapter, "warnings", []) if x)
    merged = list(items) + list(extra)
    unique = {}
    for item in merged:
        try:
            p = urlsplit(item.url or "")
            q = parse_qs(p.query)
            if p.netloc == "api-rest.elice.io":
                key = ("elice", (q.get("material_exercise_id") or [item.id])[0])
            else:
                key = (p.netloc or "main", item.id, item.url)
        except Exception:
            key = ("item", item.course_id, item.id, item.title)
        unique.setdefault(key, item)
    merged = list(unique.values())
    merged.sort(key=lambda a: (a.due_at or "9999-12-31T23:59:59Z", a.course_name, a.title))
    return merged


def _set_submit_target(res, adapter, url: str) -> None:
    """세션에 **제출 대상**(과목 id · 과제 id)을 실어 둔다. 실패는 조용히 비워 둔다.

    이게 없으면 제출 게이트가 `assignment_mismatch`로 전부 막는다 — 실제로 지금까지
    아무도 이 값을 채우지 않아서(시험만 채웠다) 제출은 **한 건도 나갈 수 없었다**.
    "제출까지 된다"고 적혀 있었지만 게이트 앞에서 멈춰 있었다(2026-08-23).

    확정하지 못하면 **빈 값 그대로 둔다.** 추측한 번호로 채우면 남의 과제에 낸다 —
    막히는 쪽이 옳다.
    """
    if not isinstance(getattr(res, "spec", None), dict):
        return
    course_id = assignment_id = ""
    try:
        if hasattr(adapter, "assignment_target"):
            course_id, assignment_id = adapter.assignment_target(url)
        else:
            from .capture.sources.canvas_api import parse_assignment_url
            _base, course_id, assignment_id = parse_assignment_url(url)
    except Exception:
        course_id = assignment_id = ""
    if course_id and assignment_id:
        res.spec["course_id"] = str(course_id)
        res.spec["assignment_id"] = str(assignment_id)


def collect_with_materials(url: str, cfg: Config, *, token: Optional[str] = None,
                           adapter=None, context_dirs: Optional[dict] = None,
                           ws: bool = False, practice: bool = False) -> Result:
    """과제 본문 + 첨부(PDF 본문 포함) + 관련자료 + 관련 공지를 모아 파이프라인을 돌린다.

    adapter를 주면 그대로 사용(SSO 세션 등). 없으면 ws=True면 MoodleWsAdapter,
    아니면 token으로 CanvasApiAdapter를 만든다(Canvas/Moodle 공통 경로)."""
    import tempfile, shutil
    from .capture.sources.etl import EtlSource
    from .context.etl_materials import (
        collect_material_refs, collect_related_materials, fetch_material_texts,
        materials_to_sources,
    )
    from .context.etl_announcements import (
        collect_related_announcements, announcements_to_sources,
        spec_announcements,
    )

    if adapter is None:
        from .capture.sources.elice_api import is_exercise_url
        candidate = url[len("elice:"):] if url.startswith("elice:") else url
        if url.startswith("elice:") or is_exercise_url(candidate):
            from .capture.sources.elice_api import EliceAdapter
            url = candidate
            if not is_exercise_url(url):
                raise ValueError("허용되지 않은 Elice 과제 URL입니다.")
            adapter = EliceAdapter()
        elif ws:
            from .capture.sources.moodle_ws import MoodleWsAdapter
            adapter = MoodleWsAdapter(etl_ws_base(), token=token)
        else:
            from .capture.sources.canvas_api import CanvasApiAdapter
            adapter = CanvasApiAdapter(token=token)
    workdir = tempfile.mkdtemp(prefix="until_pick_")
    try:
        collected = EtlSource(url, adapter).collect(workdir)  # Moodle는 여기서 캐시 채움
        files = collected.to_files(workdir)
        practice_audit = None
        if practice:
            from .practice_audit import audit_assignment, enforce_practice_preflight
            practice_audit = audit_assignment(
                collected.description or "", attachment_count=len(collected.attachments))
            enforce_practice_preflight(practice_audit)
        base, cid = _resolve_course(adapter, url)
        # 과제 제목/설명을 키워드로 관련 자료 순위화 → Execution 맥락으로 주입.
        spec_like = {"deliverable": "과제", "goal": collected.title,
                     "requirements": [collected.description[:800]]}
        extra_sources = []
        mats = []
        if cid:
            material_refs = collect_material_refs(adapter, cid, base)
            mats = collect_related_materials(
                adapter, cid, spec_like, base, k=5, refs=material_refs)
            # 상위 자료는 실제 본문까지 — 과제 원문이 강의자료 PDF에 있는 경우 대응.
            mat_texts = fetch_material_texts(adapter, mats) if _auto_download_on() else {}
            extra_sources += materials_to_sources(mats, mat_texts)
            if _auto_download_on():
                from .context.distributed_spec import collect_distributed_spec
                extra_sources += collect_distributed_spec(
                    adapter, cid, base, collected.title, collected.description,
                    refs=material_refs)
        # 4번 — 이 과제 관련 공지(교수 Q&A 추가 조건 등 숨은 명세)를 맥락으로 주입.
        anns = []
        if cid and hasattr(adapter, "collect_announcements"):
            from .capture.sources.models import CourseRef
            course = CourseRef(id=cid, name=collected.course or "")
            anns = collect_related_announcements(adapter, course, spec_like, k=3)
            # 출결·좌석 등 행정 공지는 숨은 명세로 주입하지 않는다(초안이 출결
            # 인증 요구를 지어내던 회귀). anns 자체는 유지 — 질의 resolver가
            # 출결 공지 속 순번표 링크를 계속 쓸 수 있어야 한다.
            extra_sources += announcements_to_sources(spec_announcements(anns))
            # 주차별 세미나 안내 — 그 주차 공지의 첨부(PDF·한글)가 곧 원료다.
            # 매주 연사가 바뀌는 과목은 무엇을 다뤘는지가 여기에만 있어서,
            # 없으면 「N주차 소감문」이 원료 없음으로 떨어진다(실사용 2026-08-23).
            # 주차 매칭은 결정적이다 — 제목에 'N주차'가 없으면 아무것도 안 한다.
            if _auto_download_on():
                try:
                    from .context.weekly_brief import weekly_brief_sources
                    extra_sources += weekly_brief_sources(
                        anns, collected.title or "",
                        lambda att: _attachment_text(adapter, att))
                except Exception:
                    pass      # 원료 보강 실패는 비치명 — 되묻는 흐름으로 돌아간다
        # 질의 과제 — 공지의 공개 순번표를 프로필 학번과 대조해 이번 주 담당
        # 교수·실제 마감(수업 전날 17시)을 결정적으로 확정한다. 학번은 LLM에
        # 보내지 않으며, 표/공식 프로필 조회 실패나 중복 매칭은 조용히 폴백.
        inquiry_assignment = None
        not_my_turn = False
        if cid and hasattr(adapter, "fetch_public_text"):
            try:
                import re as _re
                from .context.inquiry_assignment import resolve_inquiry_assignment
                from .profile import load_profile
                year_m = _re.search(r"(?<!\d)(20\d{2})(?!\d)", collected.course or "")
                if not year_m:
                    for _ann in anns:
                        year_m = _re.match(r"(20\d{2})", getattr(_ann, "created_iso", "") or "")
                        if year_m:
                            break
                inquiry_assignment = resolve_inquiry_assignment(
                    title=collected.title,
                    student_id=load_profile().get("student_id", ""),
                    announcements=anns,
                    fetch_text=adapter.fetch_public_text,
                    year=int(year_m.group(1)) if year_m else None,
                )
                if inquiry_assignment:
                    extra_sources.append(inquiry_assignment.to_source())
                else:
                    # 매칭이 안 된 이유를 가른다: '내 차례가 아님'과 '표를 못 읽음'.
                    # 내 차례가 아닌 주의 질의는 할 일이 아니다(사용자 지시
                    # 2026-08-23) — 따로 분류해 할 일 목록에서 뺀다. 판단이
                    # 불확실하면(None) 아무것도 하지 않는다: 잘못 '내 차례 아님'
                    # 으로 치우면 진짜 과제를 놓친다.
                    not_my_turn = _inquiry_not_my_turn(
                        adapter, anns, collected.title,
                        load_profile().get("student_id", ""))
            except Exception:
                inquiry_assignment = None
        # 반복 시리즈('N주차 소감문' 등) — 같은 시리즈의 내 지난 제출물을 참고
        # 맥락으로(문체·구조 참고, 복사 금지 지침 포함). 조회 실패는 비치명.
        if cid and hasattr(adapter, "my_submissions_json"):
            from .context.series import (
                rows_from_canvas_submissions, find_predecessors,
                find_stage_predecessors, predecessors_to_sources,
            )
            try:
                raw_submissions = adapter.my_submissions_json(cid, base)
                rows = rows_from_canvas_submissions(raw_submissions)
                prev = find_predecessors(collected.title, rows)
                if not prev:
                    # 회차 시리즈가 아니면 **단계**로 이어진 것을 찾는다 —
                    # '서론 작성'→'서론 수정', '초고'→'최종본'. 대학 글쓰기처럼
                    # 한 산출물을 여러 과제로 쪼개는 과목은 이쪽이 본류인데
                    # 예전엔 하나도 못 잡았다(사용자 지적 2026-08-23).
                    prev = find_stage_predecessors(collected.title, rows)
                extra_sources += predecessors_to_sources(prev)
                # '피피티 제출'처럼 현재 페이지에 내용이 없는 변환 과제는 같은
                # 과목의 주제·개요·원고 제출물을 연결한다. 현재 과제 뒤 제출물은
                # 미래 정보 누수를 막기 위해 제외한다.
                from .capture.sources.canvas_api import parse_assignment_url
                from .context.presentation_conversion import (
                    find_presentation_predecessors,
                    hydrate_predecessor_attachments,
                    presentation_predecessors_to_sources,
                )
                try:
                    _, _, current_aid = parse_assignment_url(url)
                except ValueError:
                    current_aid = ""
                current_due = ""
                for _row in raw_submissions:
                    _assignment = (_row.get("assignment") or {}) if isinstance(_row, dict) else {}
                    if str(_assignment.get("id") or "") == current_aid:
                        current_due = str(_assignment.get("due_at") or "")
                        break
                _hits = find_presentation_predecessors(
                    collected.title, raw_submissions,
                    current_assignment_id=current_aid, current_due_at=current_due)
                hydrate_predecessor_attachments(_hits, adapter.download, workdir)
                extra_sources += presentation_predecessors_to_sources(_hits)
            except Exception:
                pass
        # 과목명을 run()에 **미리** 넘긴다 — 아래에서 res.spec에 넣는 것은 화면
        # 표기용이라 이미 늦다. §3 course_profiles 폴백은 라우팅 시점에 과목명을
        # 봐야 켜진다(그게 없어서 그 기능이 라이브에서 동작한 적이 없었다).
        res = run(files, cfg, extra_context_sources=extra_sources or None,
                  course_name=(collected.course or "").strip(),
                  **_ctx_kwargs(context_dirs))
        if practice:
            res.practice_mode = True
            res.practice_audit = practice_audit.to_dict() if practice_audit else None
        # 어떤 과제를 골랐는지 — 화면 표기는 LLM 추출 제목이 아니라 eTL 원본
        # 과제명·과목명이 정답(딸깍 직후 사용자가 가장 먼저 확인하는 정보).
        if isinstance(res.spec, dict):
            if (collected.title or "").strip():
                res.spec["title"] = collected.title.strip()
            if (collected.course or "").strip():
                res.spec.setdefault("course", collected.course.strip())
        res.etl_materials = mats
        res.etl_announcements = anns
        res.inquiry_assignment = inquiry_assignment
        # 내 차례가 아닌 주차의 질의 — 할 일이 아니라고 따로 분류한다. 초안은
        # 이미 만들어졌지만 화면·제출 게이트가 '안 해도 되는 과제'로 다룬다.
        # (확실할 때만 켜진다 — _inquiry_not_my_turn이 불확실하면 False다.)
        if not_my_turn and isinstance(res.spec, dict):
            res.spec["inquiry_not_my_turn"] = True
        _set_submit_target(res, adapter, url)
        if inquiry_assignment and inquiry_assignment.due_date:
            from .understanding.deadline import Deadline
            res.deadline = Deadline(due=inquiry_assignment.due_date, had_year=True,
                                    time_str=inquiry_assignment.due_time)
        res.teacher_feedback = _stored_feedback()[0]
        return res
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def serve(host: str = "127.0.0.1", port: int = 8000, backend: str | None = None,
          context_dirs: Optional[dict] = None, sso: bool = False,
          cloud: bool = False, ws: bool = False) -> None:
    global CLOUD
    CLOUD = bool(cloud)
    import os as _os
    if ws is False and (_os.getenv("UNTIL_ETL_WS", "") or "").strip() not in ("", "0"):
        ws = True  # env로도 WS 모드 켤 수 있음
    if CLOUD and sso:
        raise SystemExit("--cloud와 --sso는 함께 쓸 수 없습니다(SSO는 로컬 전용).")
    if sso and ws:
        raise SystemExit("--sso와 --ws는 함께 쓸 수 없습니다(둘 다 조회 방식).")
    if CLOUD:
        # 운영자 eTL 토큰이 어댑터 계층의 env 폴백으로 전 사용자에게 노출되는 경로를
        # 원천 차단 — 웹 레이어 가드에 더해 프로세스 env에서 제거(WS 토큰도 함께).
        for _k in ("UNTIL_CANVAS_TOKEN", "UNTIL_ETL_WS_TOKEN"):
            if (_os.environ.pop(_k, None) or "").strip():
                print(f"⚠ {_k}은 클라우드 모드에서 무시·제거됩니다(계정 노출 방지).")
    _Handler.backend = backend or Config().backend
    _Handler.context_dirs = {k: v for k, v in (context_dirs or {}).items() if v}
    _Handler.sso = sso
    _Handler.ws = ws
    _Handler._allow_manual_start_for_tests = False
    # SSO 모드는 Playwright sync 세션을 한 스레드에서만 써야 하므로 단일 스레드 서버.
    server_cls = HTTPServer if sso else ThreadingHTTPServer
    httpd = server_cls((host, port), _Handler)
    ctx = ", ".join(_Handler.context_dirs) or "없음"
    mode = ("클라우드(멀티유저)" if CLOUD else
            "SSO(브라우저 로그인)" if sso else
            "Moodle WS(읽기 전용)" if ws else "토큰")
    print(f"Until UI: http://{host}:{port}  (backend={_Handler.backend}, 맥락={ctx}, 인증={mode})  — Ctrl+C로 종료")
    if sso:
        print("  · /inbox 첫 요청 때 브라우저 창이 열립니다 — MySNU 로그인 후 자동으로 진행됩니다.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
        httpd.server_close()
        if sso:
            _close_sso_adapter()


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="until.web", description="Until 최소 UI")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--backend", default=None, help="mock(기본) | local | anthropic")
    ap.add_argument("--course-materials", default=None, help="수업자료 폴더(관련 자료 검색)")
    ap.add_argument("--my-files", default=None, help="내 파일 폴더(관련 파일 검색)")
    ap.add_argument("--voice", default=None, help="내 기존 글 폴더(말투 프로파일)")
    ap.add_argument("--sso", action="store_true",
                    help="토큰 대신 브라우저 SSO 로그인으로 eTL 조회(단일 스레드 서버). "
                         "playwright 필요.")
    ap.add_argument("--cloud", action="store_true",
                    help="클라우드(멀티유저) 모드 — 0.0.0.0:$PORT 바인딩, 익명 uid 쿠키로 "
                         "세션·히스토리·사용량 격리. UNTIL_CLOUD=1과 동일.")
    ap.add_argument("--ws", action="store_true",
                    help="Moodle Web Services(읽기 전용) 어댑터로 조회·수집(Canvas 대신). "
                         "eTL은 Moodle이라 강의자료·공지 자동수집이 가능. UNTIL_ETL_WS=1과 동일.")
    args = ap.parse_args(argv)
    import os as _os
    cloud = args.cloud or (_os.getenv("UNTIL_CLOUD") or "").strip() in ("1", "true", "yes")
    host, port = args.host, args.port
    if cloud:
        # 컨테이너 관행: 전체 인터페이스 바인딩 + $PORT 존중(명시 인자가 우선).
        if host == "127.0.0.1":
            host = "0.0.0.0"
        if port == 8000 and (_os.getenv("PORT") or "").strip().isdigit():
            port = int(_os.getenv("PORT"))
    serve(host, port, args.backend, context_dirs={
        "course_materials": args.course_materials,
        "my_files": args.my_files,
        "voice": args.voice,
    }, sso=args.sso, cloud=cloud, ws=args.ws)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
