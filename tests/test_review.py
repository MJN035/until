"""완성도 점검(Self-Review) 테스트 (오프라인·mock)."""
import sys, pathlib, threading, http.client, re
from urllib.parse import urlencode
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.config import Config
from until.pipeline import run, review_result
from until.boundary.models import Draft
from until.execution.review import parse_review, review_draft, ReviewReport
from until.llm.mock_client import MockClient
from until.llm.base import LLMResult, SourceDoc
from until import web


def test_parse_review_robust():
    txt = ('잡소리 {"level":"보완 권장","coverage":"인용 부족",'
           '"gaps":["근거 더 연결","섹션 추가"],"decision_check":"적절","summary":"대체로 좋음"} 끝')
    r = parse_review(txt)
    assert r.level == "보완 권장" and r.coverage == "인용 부족"
    assert r.gaps == ["근거 더 연결", "섹션 추가"] and r.summary == "대체로 좋음"
    # 깨진 JSON → 안전한 기본값(예외 없음).
    bad = parse_review("not json")
    assert isinstance(bad, ReviewReport) and bad.gaps == []
    # level 정규화(공백 변형 허용).
    assert parse_review('{"level":"보완권장","summary":"x"}').level == "보완 권장"
    print("OK parse_review robust + level normalize")


def test_review_draft_mock_flags_missing_citation():
    # 인용 없는 초안 → 보완 권장 + gap에 자료 연결 제안.
    d = Draft.from_text("서론. " * 40 + "\n[[DECISION: 관점을 어디로 둘지 — 본인 판단]]\n")
    r = review_draft(d, {"deliverable": "에세이"}, MockClient(), sources_legend=["수업자료 A"])
    assert r.level in ("보완 권장", "부족")
    assert any("자료" in g or "인용" in g for g in r.gaps)
    # 인용 + 결정 + 충분한 구조 → 충분.
    d2 = Draft.from_text(
        "## 서론\n자료를 근거로 정리했다 [자료1]. " + "충분한 분량의 본문. " * 20
        + "\n## 본론\n근거를 더 제시했다 [자료1].\n"
        + "[[DECISION: 핵심 논지를 어디로 — 본인 관점]]\n## 결론\n요약한다.\n"
    )
    r2 = review_draft(d2, {"deliverable": "에세이"}, MockClient(), sources_legend=["A"])
    assert r2.level == "충분", (r2.level, r2.gaps)
    print("OK review_draft (mock) flags missing citation / passes good draft")


def test_readiness_injected_into_review_message():
    from until.execution.review import review_user_message
    # readiness_lines가 있으면 '결정적 사전 점검' 블록이 초안 앞에 들어간다.
    msg = review_user_message("{}", "초안 본문", ["자료A"],
                              ["⚠️ [분량] 분량 부족 — 1000자 더 필요", "✅ [인용] 충실"])
    assert "결정적 사전 점검" in msg
    assert msg.index("분량 부족") < msg.index("[ 점검할 초안 ]")  # 초안보다 앞
    # 없으면 블록도 없다(기존 동작 유지).
    assert "결정적 사전 점검" not in review_user_message("{}", "본문", ["A"])
    print("OK readiness injected into review message (before draft)")


def test_pipeline_review_result_uses_readiness():
    # 파이프라인 점검이 readiness를 근거로 넘겨도 정상 동작(mock).
    from until.understanding.length_target import LengthTarget
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    res.length_target = LengthTarget(unit="자", min=100000)  # 분량 부족 강제
    rev = review_result(res, cfg)
    assert isinstance(rev, ReviewReport) and rev.level and rev.summary
    print("OK pipeline review uses readiness without breaking")


def test_pipeline_review_result():
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    rev = review_result(res, cfg)
    assert isinstance(rev, ReviewReport) and rev.level and rev.summary
    print("OK pipeline review_result")


def test_focused_revision_preserves_boundary_and_excludes_source():
    from until.execution.revise import revise_draft
    seen = {}
    class Fake:
        def complete(self, system, user, **kwargs):
            seen["docs"] = kwargs["documents"]
            return LLMResult(("첫 문단을 더 자연스럽게 고쳤다. 자료에서 확인한 조건과 "
                              "그 조건이 결론에 미치는 영향을 구체적으로 설명한다. " * 8) + "\n\n"
                             "[[DECISION: 관점 선택 — 본인 판단]]", "fake")
    draft = Draft.from_text("첫 문단.\n\n[[DECISION: 관점 선택 — 본인 판단]]")
    revised, guard = revise_draft(
        draft, {"requirements": ["근거 사용"]}, "1번째 문단만 자연스럽게", Fake(),
        source_docs=[SourceDoc("A", "근거 A"), SourceDoc("B", "근거 B")], excluded={2})
    assert guard.passed and revised.n_decisions == 1
    assert "사용자가 이 자료를 제외" in seen["docs"][1].text
    assert seen["docs"][0].text == "근거 A"
    print("OK focused revision preserves boundary + excludes selected source")


def _post(conn, path, fields, follow=True):
    conn.request("POST", path, urlencode(fields),
                 {"Content-Type": "application/x-www-form-urlencoded"})
    r = conn.getresponse(); body = r.read().decode("utf-8")
    if follow and r.status == 303:
        conn.request("GET", r.getheader("Location")); r = conn.getresponse()
        body = r.read().decode("utf-8")
    return r.status, body


def test_web_review_flow():
    cfg = Config(); web._Handler.backend = "mock"; web._Handler.sso = False
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        s, draft = _post(conn, "/draft",
                         {"assignment": "에세이를 써라. 한 기술이 한 제도를 재편한 과정을 분석하라."})
        assert s == 200 and "완성도 점검 (자료 활용" in draft   # 점검 전 버튼
        token = re.search(r'name="session" value="([^"]+)"', draft).group(1)
        s, reviewed = _post(conn, "/review", {"session": token})
        assert s == 200 and "완성도 점검" in reviewed           # 점검 후 패널
        assert "완성도 점검 (자료 활용" not in reviewed          # 버튼 숨김
        conn.close()
    finally:
        httpd.shutdown(); httpd.server_close()
    print("OK web review flow (button -> panel)")


if __name__ == "__main__":
    test_parse_review_robust()
    test_review_draft_mock_flags_missing_citation()
    test_readiness_injected_into_review_message()
    test_pipeline_review_result_uses_readiness()
    test_pipeline_review_result()
    test_focused_revision_preserves_boundary_and_excludes_source()
    test_web_review_flow()
    print("\nREVIEW TESTS PASS")
