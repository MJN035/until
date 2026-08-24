# -*- coding: utf-8 -*-
"""사람이 직접 고친 편집 — `edit_source="human"` 신호를 만드는 유일한 경로.

배경: 지금까지 쌓이던 수정 신호는 `finalize`와 `llm_revise`뿐이고 둘 다
"AI가 무엇을 바꿨나"였다. 개인화가 필요한 건 "사람이 무엇을 고쳤나"인데
그 값이 0이면 나머지가 전부 추측 위에 선다.

여기서 보는 것: ① human 이벤트가 실제로 적립되는가 ② 변화가 없으면 적립하지
않는가 ③ 고친 본문이 이후 흐름(/ready·제출 파일)에 진짜 반영되는가.
전부 오프라인·mock. 이벤트 스키마는 개인화 창 것을 그대로 쓴다.
"""
from __future__ import annotations

import http.client
import os
import pathlib
import sys
import tempfile
import threading
from urllib.parse import urlencode

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until import billing, web
from until.config import Config
from until.context import edit_events
from until.pipeline import run

EDITED = ("사람이 직접 고친 본문입니다. " * 8
          + "\n\n[[DECISION: 핵심 논지를 어디로 세울지 - 본인 관점]]\n")


def _tmp():
    d = pathlib.Path(tempfile.mkdtemp(prefix="until_humanedit_"))
    web._SESS_DIR = web._Path(d / "s")
    web._USERS_DIR = web._Path(d / "u")
    web._SESS_META_CACHE.clear()
    web._WORKSPACES.clear()
    web._SESSIONS.clear()
    web._OWNER.clear()
    billing.USAGE_PATH = d / "usage.json"
    billing.CREDITS_PATH = d / "credits.json"
    # 편집 이벤트는 개인화 창이 만든 저장소를 그대로 쓴다 — 경로만 임시로 돌린다.
    edit_events.set_edit_events_path_override(d / "edits.jsonl")
    edit_events.clear_edit_events()
    os.environ["UNTIL_EDIT_CAPTURE"] = "1"
    return d


def _human_events():
    return [e for e in edit_events.load_edit_events() if e.edit_source == "human"]


def _session():
    result = run(["examples/sample_assignment.txt"], Config(backend="mock"))
    token = "e" * 22
    web._SESSIONS[token] = result
    web._persist_session(token)
    return token, result


# ── ① human 이벤트 적립 ─────────────────────────────────────────────
def test_human_edit_records_event_and_applies_body():
    _tmp()
    token, result = _session()
    original = result.draft.body

    updated = web.edit_session(token, EDITED)
    assert updated is not None
    assert updated.draft.body.strip() == EDITED.strip(), "본문이 반영되지 않았다"
    assert web._SESSIONS[token].draft.body.strip() == EDITED.strip()

    human = _human_events()
    everything = [e.edit_source for e in edit_events.load_edit_events()]
    assert len(human) == 1, everything
    assert human[0].after.strip() == EDITED.strip()
    assert human[0].before.strip() == original.strip()
    # 되돌리기용 이전 본문이 남는다(AI 수정과 같은 workspace 이력).
    assert original in (web._WORKSPACES.get(token) or {}).get("versions", [])
    print("OK 사람 편집 → edit_source=human 1건 + 본문 반영")


# ── ② 변화 없으면 적립 안 함 ────────────────────────────────────────
def test_no_change_records_nothing():
    _tmp()
    token, result = _session()
    same = result.draft.body

    web.edit_session(token, same)              # 같은 본문
    web.edit_session(token, "   ")             # 빈 입력
    web.edit_session(token, same + "\n")       # 끝 공백만 다름

    assert edit_events.load_edit_events() == [], "변화가 없는데 이벤트가 쌓였다"
    assert not (web._WORKSPACES.get(token) or {}).get("versions")
    print("OK 변화 없음·빈 입력 → 적립 0, 이력도 안 남김")


# ── ③ 편집이 이후 흐름에 반영 ───────────────────────────────────────
def test_edit_flows_into_ready_and_downloads():
    _tmp()
    web.CLOUD = False
    web._Handler.backend = "mock"
    web._Handler.sso = False
    web._Handler.ws = False
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1],
                                          timeout=20)

        def post(path, fields):
            conn.request("POST", path, urlencode(fields),
                         {"Content-Type": "application/x-www-form-urlencoded"})
            r = conn.getresponse()
            body = r.read().decode("utf-8")
            return r.status, r.getheader("Location") or "", body

        def get(path):
            conn.request("GET", path)
            r = conn.getresponse()
            return r.status, r.read().decode("utf-8")

        text = pathlib.Path("examples/sample_assignment.txt").read_text(encoding="utf-8")
        _s, loc, _b = post("/draft", {"assignment": text, "ui": "simple"})
        token = loc.rsplit("/", 1)[-1]

        status, draft_page = get(f"/sv/{token}")
        assert status == 200
        assert "내가 직접 고치기" not in draft_page, "질문보다 편집란이 먼저 나왔다"
        status, detail_page = get(f"/v/{token}")
        assert status == 200
        assert "내가 직접 고치기" in detail_page, "자세한 화면에 편집란이 없다"
        assert 'action="/edit"' in detail_page

        marker = "내가 직접 넣은 결정적 문장입니다."
        status, loc, _b = post("/edit", {"session": token, "ui": "simple",
                                         "body": marker + "\n\n" + EDITED})
        assert status == 303, status

        status, after = get(f"/v/{token}")
        assert status == 200 and marker in after, "편집 결과가 화면에 없다"

        status, ready = get(f"/ready/{token}")
        assert status == 200
        status, download = get(f"/dl/{token}.md")
        assert status == 200 and marker in download, "제출 파일에 편집이 안 실렸다"

        # 로컬 서버 스레드는 요청 스코프 오버라이드(thread-local)를 공유하지 않으므로
        # 이벤트는 기본 경로에 쌓인다 — 적립 사실은 위 단위 테스트가 이미 고정했고,
        # 여기서는 '편집이 흐름에 반영되는가'만 본다.
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
    print("OK 편집 → 화면·/ready·제출 파일까지 반영")


# ── AI 수정과 섞이지 않는다 ─────────────────────────────────────────
def test_human_path_is_separate_from_ai_revise():
    """폼도 라우트도 분리돼 있어야 신호가 오염되지 않는다."""
    _tmp()
    _token, result = _session()
    page = web.render_draft("t" * 22, result)
    assert 'action="/edit"' in page          # 사람 편집 전용 라우트
    assert "내가 직접 고치기" in page
    # 문단을 골라 AI에게 다시 시키는 패널은 없앴다(사용자 지시 2026-08-23) —
    # 고르고 지시하는 조작이 거추장스럽고, 고칠 거면 직접 고치는 편이 빠르다.
    # /revise 라우트 자체는 남는다(버전 복원이 쓴다).
    assert "이 부분만 고치기" not in page
    assert 'name="paragraph"' not in page
    assert "안 고쳐도 됩니다" in page          # 선택임을 명시(강제 금지)
    # 연습 모드에는 편집란을 열지 않는다(실제 제출 흐름이 아니다).
    result.practice_mode = True
    assert web._edit_form_html("t" * 22, result, simple=False) == ""
    print("OK 사람 편집 폼 = 별개 라우트·선택 표기·연습 모드 제외")


def test_missing_session_is_rejected():
    _tmp()
    assert web.edit_session("nosuchtoken", "무언가") is None
    print("OK 없는 세션 편집 → None")


def test_editing_final_draft_updates_final():
    """최종본을 보고 있었다면 최종본을 고친다(보고 있던 문서가 바뀌어야 한다)."""
    _tmp()
    token, result = _session()
    from until.boundary.models import Draft
    result.final_draft = Draft.from_text("최종본 원본입니다. " * 10)
    web._SESSIONS[token] = result

    updated = web.edit_session(token, EDITED)
    assert updated.final_draft.body.strip() == EDITED.strip()
    assert updated.draft.body != EDITED, "초안까지 덮어쓰면 안 된다"
    assert len(_human_events()) == 1
    print("OK 최종본 편집 → 최종본만 갱신")


TESTS = [
    test_human_edit_records_event_and_applies_body,
    test_no_change_records_nothing,
    test_edit_flows_into_ready_and_downloads,
    test_human_path_is_separate_from_ai_revise,
    test_missing_session_is_rejected,
    test_editing_final_draft_updates_final,
]

if __name__ == "__main__":
    for fn in TESTS:
        fn()
    print("HUMAN EDIT TESTS PASS")
