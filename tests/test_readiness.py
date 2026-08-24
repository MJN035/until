"""제출 준비 점검(readiness) 테스트 (오프라인·결정적)."""
import sys, pathlib
from datetime import date, timedelta
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.pipeline import Result
from until.boundary.models import Draft
from until.execution.boundary_guard import GuardReport
from until.readiness import assess_readiness, render_readiness_lines
from until.understanding.length_target import LengthTarget
from until.understanding.deadline import Deadline


def _res(body, *, sources=None, length_target=None, deadline=None, passed=True,
         task_type=None):
    d = Draft.from_text(body)
    g = GuardReport(passed=passed, attempts=1, reasks=0)
    spec = {"title": "T"}
    if task_type:
        spec["task_type"] = task_type
    return Result(documents=[], spec=spec, draft=d, guard=g,
                  sources=sources or [], length_target=length_target, deadline=deadline)


def test_empty_when_nothing_detected():
    r = assess_readiness(_res("본문. " * 20))
    # 감지된 것 없음(마감·분량·자료·결정 없음) → 항목 없음.
    assert r.items == [] and "양호" in r.headline
    print("OK empty readiness")


def test_warns_short_length_and_uncited():
    body = "짧다.\n[[DECISION: 관점을 어디로 둘지 — 본인 판단 필요]]\n"
    r = assess_readiness(_res(body, sources=["자료A", "자료B"],
                              length_target=LengthTarget(unit="자", min=5000)))
    labels = {i.label: i for i in r.items}
    assert labels["분량"].status == "warn"     # 5000자 미달
    assert labels["인용"].status == "warn"     # 자료 줬는데 미인용
    assert labels["결정"].status == "info"     # 남은 결정은 안내(경고 아님)
    assert len(r.warnings) == 2
    assert any("분량" in l for l in render_readiness_lines(r))
    print("OK warns short length + uncited, decision is info")


def test_deadline_urgency():
    soon = _res("본문. " * 30, deadline=Deadline(due=date.today() + timedelta(days=2), had_year=True))
    d = {i.label: i for i in assess_readiness(soon).items}
    assert d["마감"].status == "warn"   # 3일 이내 → 경고
    far = _res("본문. " * 30, deadline=Deadline(due=date.today() + timedelta(days=30), had_year=True))
    d2 = {i.label: i for i in assess_readiness(far).items}
    assert d2["마감"].status == "info"
    print("OK deadline urgency warn/info")


def test_boundary_crossed_warns_when_no_decisions():
    # 결정 0개 + 가드 미통과 → 경계선 경고.
    r = assess_readiness(_res("결정 없는 본문. " * 20, passed=False))
    d = {i.label: i for i in r.items}
    assert d.get("경계선") and d["경계선"].status == "warn"
    print("OK boundary-crossed warning")


def test_factual_type_relaxes_citation_warning():
    # 에세이: 자료 미인용 → 경고.
    body = "결정 없는 완결 본문. " * 20
    essay = assess_readiness(_res(body, sources=["자료A"], task_type="essay"))
    assert {i.label: i.status for i in essay.items}.get("인용") == "warn"
    # 코드/문제풀이: 자료 미인용 → 안내(info)로 완화.
    code = assess_readiness(_res(body, sources=["자료A"], task_type="code"))
    assert {i.label: i.status for i in code.items}.get("인용") == "info"
    pset = assess_readiness(_res(body, sources=["자료A"], task_type="problemset"))
    assert {i.label: i.status for i in pset.items}.get("인용") == "info"
    # 가짜(범위 밖) 인용은 유형 무관하게 경고 유지.
    bad = assess_readiness(_res("[자료9] 라고 우김. " * 20, sources=["자료A"], task_type="code"))
    assert {i.label: i.status for i in bad.items}.get("인용") == "warn"
    print("OK factual type relaxes citation warning (invalid stays warn)")


def test_factual_type_no_boundary_warning():
    # 정형 유형은 결정 0개 + 가드 미통과여도 경계선 경고 없음.
    r = assess_readiness(_res("결정 없는 본문. " * 20, passed=False, task_type="code"))
    assert not any(i.label == "경계선" for i in r.items)
    print("OK factual type no boundary warning")


def test_capture_warnings_surfaced():
    # 파싱 실패 첨부가 있으면 '자료' 경고 항목.
    res = _res("본문. " * 20)
    res.capture_warnings = ["강의노트.pdf: PDF엔 docling 또는 pymupdf 필요", "부록.hwp: 미지원"]
    d = {i.label: i for i in assess_readiness(res).items}
    assert d.get("자료") and d["자료"].status == "warn"
    assert "강의노트" in d["자료"].message and "외 1건" in d["자료"].message
    # 경고 없으면 항목도 없음.
    res2 = _res("본문. " * 20)
    assert not any(i.label == "자료" for i in assess_readiness(res2).items)
    print("OK capture warnings surfaced in readiness")


def test_to_dict_serialization():
    import json
    body = "짧다.\n[[DECISION: 관점을 어디로 둘지 — 본인 판단 필요]]\n"
    r = assess_readiness(_res(body, sources=["자료A"],
                              length_target=LengthTarget(unit="자", min=5000)))
    d = r.to_dict()
    assert set(d.keys()) == {"headline", "n_warnings", "items"}
    assert d["n_warnings"] == len(r.warnings)
    assert all(set(it.keys()) == {"label", "status", "message"} for it in d["items"])
    # JSON items는 심각도순(warn 먼저) — 툴이 경고를 먼저 본다.
    statuses = [it["status"] for it in d["items"]]
    rank = {"warn": 0, "info": 1, "ok": 2}
    assert statuses == sorted(statuses, key=lambda s: rank.get(s, 3))
    assert statuses[0] == "warn"  # 이 케이스엔 경고가 있음
    # JSON 직렬화 가능(툴 연동).
    json.loads(json.dumps(d, ensure_ascii=False))
    print("OK readiness to_dict serialization")


def test_submission_and_cli_report_integration():
    from until.config import Config
    from until.pipeline import run
    from until import report
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    md = report.render_submission_markdown(res)
    html = report.render_submission_html(res)
    # mock 초안엔 결정이 있으므로 준비 점검 섹션이 뜬다.
    assert "제출 준비 점검" in md
    assert "제출 준비 점검" in html
    print("OK submission md/html integration")


def test_web_readiness_json_endpoint():
    import threading, http.client, json
    from urllib.parse import urlencode
    from until import web
    web._Handler.backend = "mock"; web._Handler.sso = False
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        conn.request("POST", "/draft",
                     urlencode({"assignment": "실험 보고서: 목적·방법·결과·고찰을 4페이지 내외로. 마감 2026-07-20."}),
                     {"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse(); body = r.read().decode("utf-8")
        if r.status == 303:
            conn.request("GET", r.getheader("Location")); r = conn.getresponse()
            body = r.read().decode("utf-8")
        import re
        token = re.search(r'/dl/([^.]+)\.md', body).group(1)
        # JSON 엔드포인트
        conn.request("GET", f"/readiness/{token}.json"); r = conn.getresponse()
        payload = r.read().decode("utf-8")
        assert r.status == 200 and "application/json" in r.getheader("Content-Type", "")
        data = json.loads(payload)
        assert set(data.keys()) == {"headline", "n_warnings", "items"}
        # 만료 세션 → 404 JSON
        conn.request("GET", "/readiness/nope.json"); r = conn.getresponse()
        err = json.loads(r.read().decode("utf-8"))
        assert r.status == 404 and err.get("error") == "session_not_found"
        conn.close()
    finally:
        httpd.shutdown(); httpd.server_close()
    print("OK web /readiness/<token>.json endpoint + 404")


def test_material_gap_flag_and_readiness():
    """원료 없음(첨부·맥락 0) — spec 플래그 + readiness '자료' 안내(기획 §9-2)."""
    import tempfile, os
    from until.config import Config
    from until.pipeline import run
    cfg = Config(); cfg.backend = "mock"
    fd, p = tempfile.mkstemp(suffix=".txt", text=True)
    os.write(fd, ("실습2 레포트\n\n이번 주 실습 내용을 정리하여 레포트로 "
                  "제출하세요.").encode("utf-8"))
    os.close(fd)
    try:
        res = run([p], cfg)
    finally:
        os.unlink(p)
    assert res.spec.get("task_type") == "report", res.spec.get("task_type")
    assert res.spec.get("material_gap") is True
    r = assess_readiness(res)
    msgs = " ".join(i.message for i in r.items if i.label == "자료")
    assert "원료" in msgs, msgs
    print("OK material gap — 플래그 + 자료 안내")


if __name__ == "__main__":
    test_empty_when_nothing_detected()
    test_warns_short_length_and_uncited()
    test_deadline_urgency()
    test_boundary_crossed_warns_when_no_decisions()
    test_factual_type_relaxes_citation_warning()
    test_factual_type_no_boundary_warning()
    test_capture_warnings_surfaced()
    test_to_dict_serialization()
    test_web_readiness_json_endpoint()
    test_submission_and_cli_report_integration()
    test_material_gap_flag_and_readiness()
    print("\nREADINESS TESTS PASS")
