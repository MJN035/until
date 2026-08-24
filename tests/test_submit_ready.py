# -*- coding: utf-8 -*-
"""제출 직전 마지막 한 칸(`/ready/<token>`) — 점검·올릴 파일·eTL 링크·완료 표시.

경계선: Until은 파일까지만 만든다. 이 화면은 '올렸다'는 사람의 표시만 기록하고
실제 전송은 절대 하지 않는다(네트워크 0). 전부 오프라인·mock.
"""
import http.client
import os
import pathlib
import sys
import tempfile
import threading
from urllib.parse import urlencode

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until import billing, web
from until.boundary.models import Draft
from until.config import Config
from until.pipeline import run

# 고정 포트는 병렬 실행에서 충돌한다 — 0을 주고 커널이 고른 포트를 쓴다.
PORT = 0


def _tmp():
    d = pathlib.Path(tempfile.mkdtemp(prefix="until_ready_"))
    web._SESS_DIR = web._Path(d / "s")
    web._USERS_DIR = web._Path(d / "u")
    web._SESS_META_CACHE.clear()
    web._WORKSPACES.clear()
    web._TELEMETRY_META.clear()
    billing.USAGE_PATH = d / "usage.json"
    billing.CREDITS_PATH = d / "credits.json"
    return d


def _result():
    return run(["examples/sample_assignment.txt"], Config(backend="mock"))


# ── 순수 함수 ───────────────────────────────────────────────────────
def test_assignment_link_from_ids_only():
    """원문 URL을 저장하지 않고 과목·과제 id로 링크를 재구성한다."""
    _tmp()
    web._TELEMETRY_META["t1"] = {"course_id": "123", "assignment_id": "456"}
    link = web._assignment_link("t1")
    assert link.endswith("/courses/123/assignments/456"), link
    assert link.startswith("http"), link
    # 붙여넣기 세션(이 정보가 없음)·비정상 값은 링크를 만들지 않는다.
    assert web._assignment_link("nope") == ""
    web._TELEMETRY_META["t2"] = {"course_id": "../etc", "assignment_id": "456"}
    assert web._assignment_link("t2") == ""
    print("OK eTL 과제 링크 — id로만 재구성·주입 불가")


def test_submitted_marker_roundtrip():
    _tmp()
    assert web._submit_state("tok") == {}
    web._SESSIONS["tok"] = None          # _persist_session은 토큰 형식만 보고 조용히 무시
    web.mark_submitted("tok")
    assert web._submit_state("tok").get("submitted_at")
    web.mark_submitted("tok", done=False)
    assert web._submit_state("tok") == {}
    print("OK 제출 표시 — 켜고 끄기")


def test_required_formats_detected():
    res = _result()
    res.spec = dict(res.spec or {})
    res.spec["submission_format"] = "제출은 .docx 파일로, 발표자료는 pptx 형식으로"
    got = web._required_formats(res)
    assert "docx" in got and "pptx" in got, got
    res.spec["submission_format"] = ""
    res.spec["deliverable"] = "보고서"
    assert web._required_formats(res) == []
    print("OK 요구 형식 추출 — 결정적, 없으면 빈 목록")


def test_checklist_splits_pass_and_todo():
    res = _result()
    html_ = web._ready_checklist_html(res, "tok")
    assert "제출 전 점검" in html_
    assert "✓" in html_ or "⚠" in html_ or "✗" in html_
    assert "돌아가 고치기" in html_        # 막다른 화면 금지
    print("OK 점검 체크리스트 — 통과/확인 필요 분리")


def test_practice_mode_has_no_submit_surface():
    res = _result()
    res.practice_mode = True
    page = web.render_submit_ready("tok", res)
    assert "연습 모드" in page and "완료로 표시" not in page
    assert web._submit_ready_link("tok", res) == ""
    print("OK 연습 모드 — 제출 표면 없음")


# ── 웹 왕복 ─────────────────────────────────────────────────────────
def _serve():
    web.CLOUD = False
    web._Handler.backend = "mock"
    web._Handler.sso = False
    web._Handler.ws = False
    httpd = web.ThreadingHTTPServer(("127.0.0.1", PORT), web._Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def test_web_final_to_ready_to_submitted():
    _tmp()
    httpd = _serve()
    try:
        conn = http.client.HTTPConnection(
            "127.0.0.1", httpd.server_address[1], timeout=20)

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
        _s, loc, _b = post("/finalize", {"session": token, "ui": "simple",
                                         "answer_1": "내가 고른 논지"})
        status, final = get(loc)
        assert status == 200 and "제출하러 가기" in final

        status, ready = get(f"/ready/{token}")
        assert status == 200
        for must in ("제출 전 점검", "올릴 파일", "어디에 올리나", "완료로 표시"):
            assert must in ready, must
        assert "/dl/" in ready                     # 파일을 이 화면에서 바로 받는다
        # 붙여넣기 세션이라 eTL 주소를 모른다 — 링크 대신 안내가 떠야 한다.
        assert "eTL에서 해당 과제를 직접 열어" in ready

        status, _loc, _b = post("/submitted", {"session": token})
        assert status == 303
        status, done = get(f"/ready/{token}")
        assert "제출 완료로 표시해 뒀어요" in done
        assert "다음 과제 하나 더" in done
        # 완성 화면 버튼도 완료 상태를 반영한다.
        _s, final2 = get(f"/svf/{token}")
        assert "제출 완료로 표시됨" in final2

        status, _loc, _b = post("/submitted", {"session": token, "undo": "1"})
        status, undone = get(f"/ready/{token}")
        assert "제출 완료로 표시해 뒀어요" not in undone and "완료로 표시" in undone

        # 없는 세션은 404, 표시도 거부.
        assert get("/ready/nosuchtoken")[0] == 404
        assert post("/submitted", {"session": "nosuchtoken"})[0] == 404
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
    print("OK 웹 — 완성 → 제출 화면 → 완료 표시 → 취소")


def test_ready_shows_etl_link_when_known():
    """eTL로 시작한 작업은 그 과제 페이지로 바로 보낸다."""
    _tmp()
    res = _result()
    res.draft = Draft.from_text("본문. " * 40 + "\n[[DECISION: 논지를 어디로 세울지]]\n")
    web._SESSIONS["etltoken"] = res
    web._TELEMETRY_META["etltoken"] = {"course_id": "77", "assignment_id": "88"}
    page = web.render_submit_ready("etltoken", res)
    assert "/courses/77/assignments/88" in page
    assert "eTL 제출 페이지 열기" in page
    print("OK eTL 세션 — 제출 페이지 딥링크")


def test_blank_decisions_are_autofilled_and_disclosed():
    """질문에 답 안 하고 그냥 완성하면 AI가 채운다(사용자 지시 2026-08-20).

    예전에는 빈 칸이 제출 문서에 `【직접 정할 것 N: ...】` 자리표시로 남아, 그대로
    올리면 교수가 빈칸이 박힌 파일을 받았다. 이제는 채우되 **채운 사실을 밝힌다** —
    조용히 정해 주면 학생은 자기가 정한 줄 알고 낸다.
    """
    _tmp()
    web._AUTOFILLED.clear()
    web._SUGGESTIONS.clear()
    web._ANSWERS.clear()
    httpd = _serve()
    try:
        conn = http.client.HTTPConnection(
            "127.0.0.1", httpd.server_address[1], timeout=30)

        def post(path, fields):
            conn.request("POST", path, urlencode(fields),
                         {"Content-Type": "application/x-www-form-urlencoded"})
            r = conn.getresponse()
            return r.status, r.getheader("Location") or "", r.read().decode("utf-8")

        def get(path):
            conn.request("GET", path)
            r = conn.getresponse()
            return r.status, r.read().decode("utf-8")

        text = pathlib.Path("examples/sample_assignment.txt").read_text(encoding="utf-8")
        _s, loc, _b = post("/draft", {"assignment": text, "ui": "simple"})
        token = loc.rsplit("/", 1)[-1]
        n = web._SESSIONS[token].draft.n_decisions
        assert n, "이 표본은 결정 지점이 있어야 의미가 있다"

        # 답을 하나도 쓰지 않고 '완성하기'.
        blanks = {f"answer_{i}": "" for i in range(1, n + 1)}
        _s, loc, _b = post("/finalize", {"session": token, "ui": "simple", **blanks})

        filled = web._ANSWERS.get(token) or {}
        assert len(filled) == n, (len(filled), n)          # 빈 칸이 없다
        assert sorted(web._AUTOFILLED.get(token) or []) == list(range(1, n + 1))

        # 제출 파일에 자리표시가 남지 않는다.
        status, md = get(f"/dl/{token}.md")
        assert status == 200 and "직접 정할 것" not in md

        # 그러나 대신 정했다는 사실은 화면에 그대로 뜬다(완성·제출 두 화면 모두).
        for path in (loc, f"/ready/{token}"):
            status, page = get(path)
            assert status == 200 and "AI가 대신 정한 곳" in page, path

        # 재시작해도 남는다 — 서명 세션 스키마에 실려야 하고, 안 실리면
        # `to_jsonable`이 통째로 거부해 세션 저장 자체가 조용히 멈춘다.
        assert (web._SESS_DIR / f"{token}.json").exists()
        web._SESSIONS.pop(token, None)
        web._ANSWERS.pop(token, None)
        web._AUTOFILLED.pop(token, None)
        status, page = get(f"/ready/{token}")
        assert status == 200 and "AI가 대신 정한 곳" in page
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
    print("OK 빈 결정 칸 — AI가 채우고, 채운 사실을 밝힌다")


def test_my_answers_are_never_overwritten_by_autofill():
    """내가 쓴 칸은 자동 채움이 건드리지 않는다."""
    _tmp()
    web._AUTOFILLED.clear()
    web._SUGGESTIONS.clear()
    web._ANSWERS.clear()
    res = _result()
    res.draft = Draft.from_text(
        "본문. " * 40
        + "\n[[DECISION: 논지를 어디로]]\n[[DECISION: 사례를 무엇으로]]\n")
    web._SESSIONS["mine"] = res
    mine = "내가 직접 고른 논지"
    filled, done = web._fill_blank_decisions("mine", res, {1: mine}, Config(backend="mock"))
    assert filled[1] == mine          # 덮어쓰지 않는다
    assert done == [2] and filled.get(2)
    print("OK 자동 채움 — 내가 쓴 답은 덮지 않는다")


def test_autofill_can_be_turned_off():
    """`UNTIL_AUTOFILL_DECISIONS=0`이면 예전처럼 빈 칸으로 둔다."""
    _tmp()
    web._AUTOFILLED.clear()
    res = _result()
    res.draft = Draft.from_text("본문. " * 40 + "\n[[DECISION: 논지를 어디로]]\n")
    web._SESSIONS["off"] = res
    saved = os.environ.get("UNTIL_AUTOFILL_DECISIONS")
    os.environ["UNTIL_AUTOFILL_DECISIONS"] = "0"
    try:
        filled, done = web._fill_blank_decisions("off", res, {}, Config(backend="mock"))
    finally:
        if saved is None:
            os.environ.pop("UNTIL_AUTOFILL_DECISIONS", None)
        else:
            os.environ["UNTIL_AUTOFILL_DECISIONS"] = saved
    assert filled == {} and done == []
    assert not web._AUTOFILLED.get("off")
    print("OK 자동 채움 끄기 — UNTIL_AUTOFILL_DECISIONS=0")


def test_autofill_retries_until_every_blank_is_filled():
    """자동 채움은 한 번 묻고 끝내지 않는다 — 모델이 빠뜨린 칸을 다시 묻는다.

    실사용(산업공학개론 Term Project, 결정 9개, 2026-08-23): "알아서 정해 줘"를
    눌렀는데 **9개 중 1개만** 채워졌다. 원인은 한 호출에 아홉 문항을 얹고 돌아온
    것만 쓴 것 — 모델이 조용히 몇 개를 빠뜨리면 그대로 빈칸으로 남았다.

    고친 방식: 작게 나눠 묻고(3개씩), 남은 것은 **한 개씩** 다시 묻는다. 한 문항만
    물으면 빠뜨릴 여지가 없다. 진전이 없으면 즉시 멈추고(못 채우는 칸을 무한히
    조르지 않는다) 총 호출 예산으로 폭주를 막는다.
    """
    from until.boundary.models import DecisionPoint, Draft
    from until.config import Config

    class _Res:
        pass

    def _res(n=9):
        r = _Res()
        r.draft = Draft.from_text("본문")
        r.draft.decisions = [DecisionPoint(note=f"결정 {i}") for i in range(1, n + 1)]
        r.spec = {}
        return r

    original = web.suggest_decision_answers
    try:
        def _install(behaviour):
            calls = {"n": 0}

            def fake(result, cfg, *, my_answers=None, only=None):
                calls["n"] += 1
                return behaviour(list(only or []))
            web.suggest_decision_answers = fake
            web._SUGGESTIONS.clear()
            web._AUTOFILLED.clear()
            return calls

        # 요청분을 다 주는 모델 — 묶음 호출만으로 전부 채운다.
        calls = _install(lambda only: {i: {"answer": f"a{i}", "why": ""} for i in only})
        filled, done = web._fill_blank_decisions("ok", _res(), {}, Config(backend="mock"))
        assert len(done) == 9, done
        assert calls["n"] <= 4, f"정상 모델에 호출이 과하다: {calls['n']}"

        # 호출당 하나만 주는 최악 모델 — 그래도 대부분 회수한다(이전엔 1개였다).
        calls = _install(
            lambda only: {only[0]: {"answer": f"a{only[0]}", "why": ""}} if only else {})
        filled, done = web._fill_blank_decisions("bad", _res(), {}, Config(backend="mock"))
        assert len(done) >= 8, f"재시도가 동작하지 않는다: {len(done)}/9"
        assert calls["n"] <= web._AUTOFILL_MAX_CALLS

        # 아무것도 못 주는 모델 — 무한히 조르지 않고 멈춘다.
        calls = _install(lambda only: {})
        filled, done = web._fill_blank_decisions("none", _res(), {}, Config(backend="mock"))
        assert done == []
        assert calls["n"] <= 4, f"진전이 없는데 계속 물었다: {calls['n']}"

        # 내가 쓴 답은 절대 덮지 않는다(재시도가 늘어도 그대로).
        calls = _install(lambda only: {i: {"answer": f"a{i}", "why": ""} for i in only})
        filled, done = web._fill_blank_decisions(
            "mine", _res(), {3: "내가 고른 주제"}, Config(backend="mock"))
        assert filled[3] == "내가 고른 주제"
        assert 3 not in done and len(done) == 8
    finally:
        web.suggest_decision_answers = original
        web._SUGGESTIONS.clear()
        web._AUTOFILLED.clear()
    print("OK 자동 채움 재시도 (정상 9/9 · 최악 ≥8/9 · 무진전 즉시 정지 · 내 답 보존)")


def test_ready_screen_has_the_submit_entry_point():
    """제출을 켜 놓고도 **누를 곳이 없던** 구멍(라이브 확인 2026-08-23).

    초안 화면에는 확인 패널이 없고, 결정이 0개인 과제는 최종본 화면이 비어 있어
    그 화면에 있던 '마지막 한 칸' 버튼도 함께 사라졌다. 어느 경로로도 제출에
    도달하지 못했다. 진입점은 '마지막 한 칸'(/ready)에 있어야 한다.
    """
    from until.boundary.models import Draft
    from until.config import Config
    from until.pipeline import run

    res = run(["examples/sample_assignment.txt"], Config(backend="mock"))
    res.spec.update(assignment_id="777", course_id="101", title="2주차 질의")
    res.draft = Draft.from_text("완성된 본문입니다. " * 60)   # 마커 없음·분량 충분
    res.final_draft = None
    res.deadline = None
    res.length_target = None
    res.needs_approval = False
    token = "r" * 22
    web._SESSIONS[token] = res
    try:
        ready = web.render_submit_ready(token, res)
        assert 'action="/submit/prepare"' in ready, "제출 진입점이 없다"
        assert "제출 미리보기" in ready
        # 결정이 0개여도 초안 화면에서 마지막 한 칸으로 갈 길이 있어야 한다.
        res.draft.decisions = []
        assert f"/ready/{token}" in web.render_draft(token, res)
    finally:
        web._SESSIONS.pop(token, None)
    print("OK 제출 진입점 — 마지막 한 칸 · 결정 0개 경로")


TESTS = [
    test_assignment_link_from_ids_only,
    test_submitted_marker_roundtrip,
    test_required_formats_detected,
    test_checklist_splits_pass_and_todo,
    test_practice_mode_has_no_submit_surface,
    test_web_final_to_ready_to_submitted,
    test_ready_shows_etl_link_when_known,
    test_blank_decisions_are_autofilled_and_disclosed,
    test_autofill_retries_until_every_blank_is_filled,
    test_my_answers_are_never_overwritten_by_autofill,
    test_autofill_can_be_turned_off,
    test_ready_screen_has_the_submit_entry_point,
]


if __name__ == "__main__":
    for fn in TESTS:
        fn()
    print("SUBMIT READY TESTS PASS")
