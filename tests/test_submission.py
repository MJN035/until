"""제출용 내보내기(render_submission_*) 테스트 (오프라인·mock)."""
import sys, pathlib, tempfile, os
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.config import Config
from until.pipeline import run
from until.boundary.models import Draft
from until.pipeline import Result
from until.execution.boundary_guard import GuardReport
from until import report


def _mk_result(body: str, *, spec=None, sources=None) -> Result:
    draft = Draft.from_text(body)
    guard = GuardReport(passed=True, attempts=1, reasks=0)
    return Result(documents=[], spec=spec or {"title": "도시 에세이"},
                  draft=draft, guard=guard, sources=sources or [])


def test_submission_markdown_marks_decisions_and_checklist():
    body = ("서론 문단이다. " * 5 + "\n[[DECISION: 어느 도시를 고를지 — 본인 관점]]\n"
            + "본론 문단이다. " * 5 + "\n[[DECISION: 진로와 어떻게 연결할지]]\n")
    md = report.render_submission_markdown(_mk_result(body))
    # 제목 + 본문 내 자리표시(순번 유지) + 체크리스트.
    assert md.startswith("# 도시 에세이")
    assert "【직접 정할 것 1: 어느 도시를 고를지 — 본인 관점】" in md
    assert "【직접 정할 것 2: 진로와 어떻게 연결할지】" in md
    assert "## 직접 정할 것 (제출 전 채우세요)" in md
    assert "- [ ] **1.** 어느 도시를 고를지 — 본인 관점" in md
    assert "- [ ] **2.** 진로와 어떻게 연결할지" in md
    # 진단 내부정보(BoundaryGuard/spec)는 새지 않는다.
    assert "BoundaryGuard" not in md and "Task Spec" not in md
    print("OK submission markdown marks decisions + checklist")


def test_submission_no_decisions_omits_section():
    md = report.render_submission_markdown(_mk_result("완결된 본문. " * 20))
    assert "직접 정할 것" not in md
    print("OK submission omits empty decision section")


def test_submission_html_escapes_and_highlights():
    body = "위험 <script> 문단.\n[[DECISION: 관점을 <어디>로 둘지 — 판단]]\n"
    html = report.render_submission_html(_mk_result(body, sources=["수업자료 A"]))
    assert html.startswith("<!doctype html>")
    # 원문 꺾쇠는 escape, 결정 자리는 <mark>로 강조.
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert "<mark>【직접 정할 것 1:" in html
    # 인쇄 버튼(화면 전용, @media print에서 숨김).
    assert "printbtn" in html and "window.print()" in html
    # 노트 속 】는 유사 괄호로 치환돼 <mark> 강조가 중간에 끊기지 않는다.
    res2 = _mk_result("본문.\n[[DECISION: 괄호【테스트】포함 노트 — 판단]]\n")
    html2 = report.render_submission_html(res2)
    assert "괄호〔테스트〕포함" in html2
    m = report._re.search(r"<mark>【직접 정할 것 1:[^<]*】</mark>", html2)
    assert m, "mark가 자리표시 전체를 감싸야 함"
    assert "관점을 &lt;어디&gt;로 둘지" in html
    assert "참고 자료" in html and "[자료1] 수업자료 A" in html
    print("OK submission html escapes + highlights + sources")


def test_submission_prefers_final_draft():
    res = _mk_result("초안 본문.\n[[DECISION: 무엇을]]\n")
    res.final_draft = Draft.from_text("최종 본문이다. " * 10 + "\n")
    md = report.render_submission_markdown(res)
    assert "최종 본문이다." in md and "초안 본문" not in md
    assert "직접 정할 것" not in md  # 최종본엔 결정 없음
    print("OK submission prefers final_draft over draft")


def test_write_submission_infers_format():
    res = _mk_result("본문.\n[[DECISION: 무엇을 고를지 판단]]\n")
    with tempfile.TemporaryDirectory() as d:
        p_md = report.write_submission(res, os.path.join(d, "out.md"))
        p_html = report.write_submission(res, os.path.join(d, "out.html"))
        assert p_md.read_text(encoding="utf-8").startswith("# ")
        assert p_html.read_text(encoding="utf-8").startswith("<!doctype html>")
        # .docx — 유효한 zip/OOXML이고, 우리 ingest 폴백으로 왕복 파싱된다.
        p_docx = report.write_submission(res, os.path.join(d, "out.docx"))
        assert p_docx.read_bytes()[:2] == b"PK"
        from until.capture.ingest import ingest_file
        doc = ingest_file(p_docx, backend="basic")
        assert "본문." in doc.text and "직접 정할 것" in doc.text
        assert "☐" in doc.text  # 체크리스트가 문자로 살아 있다
        # C0 제어문자(\x07 등)가 본문에 있어도 XML 유효(Word가 여는 파일).
        res_ctl = _mk_result("경고음\x07이 섞인\x01 본문.\n[[DECISION: 판단]]\n")
        p_ctl = report.write_submission(res_ctl, os.path.join(d, "ctl.docx"))
        import zipfile
        from xml.etree import ElementTree as ET
        with zipfile.ZipFile(p_ctl) as z:
            ET.fromstring(z.read("word/document.xml"))  # ParseError 없이 유효
        doc2 = ingest_file(p_ctl, backend="basic")
        assert "경고음이 섞인 본문." in doc2.text  # 제어문자만 제거, 내용 보존
        # .pdf — 유효한 PDF 구조(헤더·페이지·EOF) + 한글이 UTF-16BE 헥스로 실린다.
        p_pdf = report.write_submission(res, os.path.join(d, "out.pdf"))
        blob = p_pdf.read_bytes()
        assert blob.startswith(b"%PDF-1.4") and blob.rstrip().endswith(b"%%EOF")
        assert b"/Type /Page" in blob and b"UniKS-UCS2-H" in blob
        assert "본문".encode("utf-16-be").hex().encode("ascii") in blob.lower()
        try:
            import fitz  # PyMuPDF 있으면 실제 파싱 왕복까지
        except Exception:
            pass
        else:
            pdoc = fitz.open(stream=blob, filetype="pdf")
            text = "".join(pg.get_text() for pg in pdoc)
            assert "본문." in text and "직접 정할 것" in text
    print("OK write_submission infers format by extension")


def test_pipeline_result_exports():
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    md = report.render_submission_markdown(res)
    html = report.render_submission_html(res)
    assert md.strip() and "# " in md
    assert html.startswith("<!doctype html>") and "</body></html>" in html
    print("OK pipeline result -> submission md + html")


def test_submission_requirements_checklist():
    spec = {"title": "T", "requirements": ["서론·본론·결론 구조", "출처 3개 이상",
                                           "서론·본론·결론 구조"],  # 중복 하나
            "constraints": ["표절 금지", ""]}
    res = _mk_result("본문. " * 20, spec=spec)
    md = report.render_submission_markdown(res)
    assert "## 과제 요건 점검 (제출 전 확인)" in md
    assert "- [ ] 서론·본론·결론 구조" in md
    assert "- [ ] 출처 3개 이상" in md and "- [ ] 표절 금지" in md
    assert md.count("- [ ] 서론·본론·결론 구조") == 1  # 중복 제거
    assert "- [ ] \n" not in md  # 빈 항목 제외
    html = report.render_submission_html(res)
    assert "과제 요건 점검" in html and "☐ 서론·본론·결론 구조" in html
    # 요건 없으면 섹션 자체가 없다.
    res2 = _mk_result("본문. " * 20, spec={"title": "T"})
    assert "과제 요건 점검" not in report.render_submission_markdown(res2)
    print("OK submission requirements checklist (dedup + empty filter)")


def test_type_submit_tip():
    # 유형이 감지되면 제출용에 유형별 팁 한 줄, 모르면 없음.
    body = "본문. " * 20 + "\n[[DECISION: 판단할 것 — 본인]]\n"
    res = _mk_result(body, spec={"title": "T", "task_type": "problemset"})
    md = report.render_submission_markdown(res)
    assert "풀이 과정을 단계별로" in md
    html = report.render_submission_html(res)
    assert "풀이 과정을 단계별로" in html
    res2 = _mk_result(body, spec={"title": "T"})  # 유형 미상
    assert "✍" not in report.render_submission_markdown(res2)
    print("OK type-specific submit tip")


def test_web_download_routes():
    import threading, http.client
    from urllib.parse import urlencode
    from until import web
    web._Handler.backend = "mock"; web._Handler.sso = False
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        conn.request("POST", "/draft",
                     urlencode({"assignment": "에세이를 써라. 한 기술이 한 제도를 재편한 과정을 분석하라."}),
                     {"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse(); body = r.read().decode("utf-8")
        if r.status == 303:  # PRG — 초안 페이지로 리다이렉트
            conn.request("GET", r.getheader("Location")); r = conn.getresponse()
            body = r.read().decode("utf-8")
        import re
        token = re.search(r'/dl/([^.]+)\.md', body).group(1)  # 다운로드 링크가 페이지에 있다
        # .md 다운로드
        conn.request("GET", f"/dl/{token}.md"); r = conn.getresponse()
        md = r.read().decode("utf-8")
        assert r.status == 200
        assert "attachment" in r.getheader("Content-Disposition", "")
        assert "text/markdown" in r.getheader("Content-Type", "")
        assert md.startswith("# ") and "BoundaryGuard" not in md
        # .html 다운로드
        conn.request("GET", f"/dl/{token}.html"); r = conn.getresponse()
        htmlout = r.read().decode("utf-8")
        assert r.status == 200 and htmlout.startswith("<!doctype html>")
        assert "text/html" in r.getheader("Content-Type", "")
        # .docx 다운로드(바이너리·PK 시그니처)
        conn.request("GET", f"/dl/{token}.docx"); r = conn.getresponse()
        blob = r.read()
        assert r.status == 200 and blob[:2] == b"PK"
        assert "wordprocessingml" in r.getheader("Content-Type", "")
        assert f'/dl/{token}.docx' in body  # 페이지에 버튼 링크
        # .pdf 다운로드(실제 제출 1위 포맷)
        conn.request("GET", f"/dl/{token}.pdf"); r = conn.getresponse()
        pdf = r.read()
        assert r.status == 200 and pdf.startswith(b"%PDF-")
        assert "application/pdf" in r.getheader("Content-Type", "")
        assert f'/dl/{token}.pdf' in body
        # 만료 세션 → 404
        conn.request("GET", "/dl/nope.md"); r = conn.getresponse(); r.read()
        assert r.status == 404
        conn.close()
    finally:
        httpd.shutdown(); httpd.server_close()
    print("OK web download routes (.md/.html/.docx + 404)")


if __name__ == "__main__":
    test_submission_markdown_marks_decisions_and_checklist()
    test_submission_no_decisions_omits_section()
    test_submission_html_escapes_and_highlights()
    test_submission_prefers_final_draft()
    test_write_submission_infers_format()
    test_pipeline_result_exports()
    test_submission_requirements_checklist()
    test_type_submit_tip()
    test_web_download_routes()
    print("\nSUBMISSION TESTS PASS")
