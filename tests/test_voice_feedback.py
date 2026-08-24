"""VoiceProfile 적용 결과의 yes/no 평가 루프 — 원문 없는 열거형 신호."""
import json
import http.client
import os
import pathlib
import sys
import tempfile
import threading
from urllib.parse import urlencode

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from until import web
from until.config import Config
from until.context.voice import VoiceProfile
from until.feedback import FeedbackRecord, load_records
from until.telemetry.web import build_record


def _result(applied=True):
    result = web.run_text("근거를 요약하는 짧은 보고서를 작성하세요.", Config(backend="mock"))
    result.context.voice = VoiceProfile(n_samples=2 if applied else 0,
                                        ending_style="합니다체")
    result.voice_applied = applied
    return result


def test_widget_only_when_voice_profile_applied():
    assert "내 말투 같아요?" in web._voice_rating_html("tok", _result(True), False)
    assert web._voice_rating_html("tok", _result(False), False) == ""
    print("OK VoiceProfile 적용 결과에만 위젯 노출")


def test_asgi_record_dedup_and_backward_compatibility():
    old_cwd = os.getcwd()
    old_tel = os.environ.pop("UNTIL_TELEMETRY", None)
    with tempfile.TemporaryDirectory() as d:
        os.chdir(d)
        token = "voicefeedbacktoken1234"
        result = _result(True)
        web._SESSIONS[token] = result
        web._VOICE_RATINGS.pop(token, None)
        try:
            client = TestClient(__import__("until.asgi", fromlist=["create_app"])
                                .create_app("mock", cloud=False))
            csrf = web._voice_csrf(token)
            first = client.post("/rate/voice", data={"session": token, "match": "yes",
                                                       "csrf": csrf},
                                follow_redirects=False)
            second = client.post("/rate/voice", data={"session": token, "match": "no",
                                                        "csrf": csrf},
                                 follow_redirects=False)
            assert first.status_code == second.status_code == 303
            records = load_records()
            assert len(records) == 1 and records[0].voice_match is True
            assert records[0].sources == "" and records[0].spec == "{}"
            # 구버전 JSON에 새 필드가 없어도 dataclass 기본 None으로 복원된다.
            old = FeedbackRecord("과제", "{}", "", 0, 0, True)
            payload = old.__dict__.copy(); payload.pop("voice_match")
            pathlib.Path("old.jsonl").write_text(json.dumps(payload) + "\n", encoding="utf-8")
            assert load_records("old.jsonl")[0].voice_match is None
        finally:
            web._SESSIONS.pop(token, None)
            web._VOICE_RATINGS.pop(token, None)
            web.CLOUD = False
            os.chdir(old_cwd)
            if old_tel is not None: os.environ["UNTIL_TELEMETRY"] = old_tel
    print("OK ASGI voice rating 기록·세션 dedup·구버전 호환")


def test_telemetry_voice_match_is_enum_only():
    result = _result(True)
    old_salt = os.environ.get("UNTIL_TELEMETRY_SALT")
    os.environ["UNTIL_TELEMETRY_SALT"] = "voice-feedback-test"
    try:
        row = build_record("review", "browser-voice-1234", result, {}, {}, {
            "source": "manual", "backend": "mock", "voice_match": "yes"})
        assert row["voice_match"] == "yes"
        bad = False
        try:
            build_record("review", "browser-voice-1234", result, {}, {}, {
                "source": "manual", "backend": "mock", "voice_match": "내 문체 원문"})
        except Exception:
            bad = True
        assert bad, "자유 문자열 voice_match는 fail-closed여야 함"
    finally:
        if old_salt is None: os.environ.pop("UNTIL_TELEMETRY_SALT", None)
        else: os.environ["UNTIL_TELEMETRY_SALT"] = old_salt
    print("OK telemetry review voice_match yes/no enum only")


def test_legacy_voice_rating_route():
    old_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as d:
        os.chdir(d)
        token = "legacyvoicefeedback12"
        web._SESSIONS[token] = _result(True)
        web._VOICE_RATINGS.pop(token, None)
        server = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            body = urlencode({"session": token, "match": "no",
                              "csrf": web._voice_csrf(token)})
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
            conn.request("POST", "/rate/voice", body,
                         {"Content-Type": "application/x-www-form-urlencoded"})
            response = conn.getresponse(); response.read()
            assert response.status == 303 and web._VOICE_RATINGS[token] is False
            assert load_records()[0].voice_match is False
        finally:
            server.shutdown()
            web._SESSIONS.pop(token, None); web._VOICE_RATINGS.pop(token, None)
            os.chdir(old_cwd)
    print("OK legacy POST /rate/voice")


def test_profile_present_without_execution_provenance_hides_widget():
    result = _result(True)
    result.voice_applied = False
    assert result.context.voice.n_samples > 0
    assert web._voice_rating_html("tok", result, False) == ""


def test_concurrent_claim_is_idempotent_and_delete_cleans_state():
    token = "voiceconcurrent12345"
    result = _result(True)
    old_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as d:
        os.chdir(d); web._VOICE_RATINGS.pop(token, None)
        barrier = threading.Barrier(8)
        threads = [threading.Thread(target=lambda value=(i % 2 == 0): (
            barrier.wait(), web.record_voice_rating(token, result, value,
                                                     backend="mock")))
                   for i in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        assert len(load_records()) == 1
        web._SESSIONS[token] = result
        assert web.delete_session(token)
        assert token not in web._VOICE_RATINGS
        os.chdir(old_cwd)


def test_csrf_and_cloud_uid_isolation():
    import until.asgi as asgi
    old_beta = os.environ.pop("UNTIL_BETA_CODE", None)
    token = "voicecloudisolated12"
    result = _result(True)
    app = asgi.create_app("mock", cloud=True)
    owner, stranger = TestClient(app), TestClient(app)
    owner.get("/"); stranger.get("/")
    uid = owner.cookies.get("uid")
    web._SESSIONS[token] = result; web._OWNER[token] = uid
    try:
        bad = owner.post("/rate/voice", data={"session": token, "match": "yes",
                                               "csrf": "bad"}, follow_redirects=False)
        foreign = stranger.post("/rate/voice", data={"session": token, "match": "yes",
                                                      "csrf": web._voice_csrf(token)},
                                follow_redirects=False)
        assert bad.status_code == foreign.status_code == 400, (
            bad.status_code, foreign.status_code)
    finally:
        web._SESSIONS.pop(token, None); web._OWNER.pop(token, None)
        web._VOICE_RATINGS.pop(token, None); web.CLOUD = False
        if old_beta is not None: os.environ["UNTIL_BETA_CODE"] = old_beta


def test_feedback_loader_strict_bool_and_forward_compatible():
    base = FeedbackRecord("과제", "{}", "", 0, 0, True).__dict__
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "rows.jsonl"
        rows = [{**base, "voice_match": False, "future_field": 1},
                {**base, "voice_match": None}, {**base, "voice_match": "yes"}]
        path.write_text("\n".join(json.dumps(x) for x in rows), encoding="utf-8")
        loaded = load_records(path)
        assert [x.voice_match for x in loaded] == [False, None]


def test_telemetry_docs_match_code_version():
    from until.telemetry.schema import SCHEMA_VERSION
    text = pathlib.Path("docs/TELEMETRY_SCHEMA.md").read_text(encoding="utf-8")
    assert f'`"{SCHEMA_VERSION}"`' in text
    assert f'"schema_version": "{SCHEMA_VERSION}"' in text


if __name__ == "__main__":
    test_widget_only_when_voice_profile_applied()
    test_asgi_record_dedup_and_backward_compatibility()
    test_telemetry_voice_match_is_enum_only()
    test_legacy_voice_rating_route()
    test_profile_present_without_execution_provenance_hides_widget()
    test_concurrent_claim_is_idempotent_and_delete_cleans_state()
    test_csrf_and_cloud_uid_isolation()
    test_feedback_loader_strict_bool_and_forward_compatible()
    test_telemetry_docs_match_code_version()
    print("\nVOICE FEEDBACK TESTS PASS")
