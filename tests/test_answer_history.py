"""결정 답변 히스토리(재제안) 테스트 (오프라인·결정적)."""
import sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.context import answer_history as ah


def test_record_and_load():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "hist.jsonl"
        n = ah.record_answers(
            ["핵심 논지를 어디로 세울지 — 본인 관점", "결론 톤을 어떻게 — 본인 취향"],
            {1: "형식 결정론으로 간다", 2: ""},  # 2번은 빈 답 → 미적립
            path=p)
        assert n == 1
        rows = ah.load_history(p)
        assert len(rows) == 1 and rows[0]["answer"] == "형식 결정론으로 간다"
        assert rows[0]["category"]  # rationale 분류 포함
    print("OK record + load (empty answers skipped)")


def test_suggest_similarity_threshold():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "hist.jsonl"
        ah.record_answers(["핵심 논지를 어디로 세울지 — 본인 관점 필요"],
                          {1: "형식 결정론"}, path=p)
        # 거의 같은 결정 → 재제안.
        h = ah.suggest_from_history("핵심 논지를 어디에 세울지 — 본인 관점", path=p)
        assert h and h.answer == "형식 결정론" and h.similarity >= 0.5
        # 전혀 다른 결정 → None.
        assert ah.suggest_from_history("실험 오차의 원인을 무엇으로 볼지", path=p) is None
        # 빈 노트 → None.
        assert ah.suggest_from_history("", path=p) is None
    print("OK suggest with similarity threshold")


def test_latest_answer_wins():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "hist.jsonl"
        note = "결론 톤을 어떻게 잡을지 — 본인 취향"
        ah.record_answers([note], {1: "차분하게"}, path=p)
        ah.record_answers([note], {1: "단호하게"}, path=p)
        h = ah.suggest_from_history(note, path=p)
        assert h and h.answer == "단호하게"  # 최신 답 우선
    print("OK latest answer wins")


def test_html_entities_dehtml():
    # 비브라우저 클라이언트가 이스케이프된 값을 제출해도 평문으로 적립·로드된다.
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "hist.jsonl"
        ah.record_answers(["&#x27;톤&#x27;을 어떻게 — 판단"],
                          {1: "&#x27;차분하게&#x27; 간다 &amp; 짧게"}, path=p)
        rows = ah.load_history(p)
        assert rows[0]["answer"] == "'차분하게' 간다 & 짧게"
        assert rows[0]["note"].startswith("'톤'을")
        # 레거시 오염 행(파일에 이미 엔티티)도 로드 시 무해화.
        p.write_text('{"note":"결정 — 판단","answer":"&#x27;답&#x27; 이다"}\n', encoding="utf-8")
        assert ah.load_history(p)[0]["answer"] == "'답' 이다"
        # 평문 '&'는 그대로(과잉 unescape 없음).
        ah.record_answers(["R&D 방향 — 판단"], {1: "R&D 위주로"}, path=p)
        assert ah.load_history(p)[-1]["answer"] == "R&D 위주로"
    print("OK html entities dehtml")


def test_dehtml_edge_cases():
    # 게이트/변환기 정합 — 완전한 엔티티만 치환, 원문은 저장 시 보존(load 1회 정화).
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "hist.jsonl"
        # ① 저장은 원문 그대로 → 이중 unescape 없음(사용자가 쓴 '&amp;#x27;'는
        #    load에서 딱 1회만 풀려 '&#x27;'가 아니라... 원문 '&amp;#x27;'→'&#x27;').
        ah.record_answers(["따옴표 표기를 어떻게 — 판단"],
                          {1: "아포스트로피는 &amp;#x27;로 쓴다"}, path=p)
        raw = p.read_text(encoding="utf-8")
        assert "&amp;#x27;" in raw            # 파일에는 원문 보존
        assert ah.load_history(p)[0]["answer"] == "아포스트로피는 &#x27;로 쓴다"  # 1회만
        # ② 세미콜론 없는 'R&amp'는 완전 엔티티와 공존해도 보존.
        p.write_text('{"note":"결정 — 판단","answer":"R&amp corp &#x27;x&#x27;"}\n',
                     encoding="utf-8")
        assert ah.load_history(p)[0]["answer"] == "R&amp corp 'x'"
        # ③ 잘린 부분 엔티티(&#x2)는 완전 엔티티 공존 시에도 삭제되지 않는다.
        p.write_text('{"note":"결정 — 판단","answer":"&#x27;q&#x27; 끝 &#x2"}\n',
                     encoding="utf-8")
        assert ah.load_history(p)[0]["answer"] == "'q' 끝 &#x2"
        # ③b 완전하지만 제어문자인 &#x2;도 조용히 삭제하지 않고 원문 유지.
        p.write_text('{"note":"결정 — 판단","answer":"&#x27;q&#x27; 와 &#x2;"}\n',
                     encoding="utf-8")
        assert ah.load_history(p)[0]["answer"] == "'q' 와 &#x2;"
        # ④ 대문자 &#X27;도 정화된다.
        p.write_text('{"note":"결정 — 판단","answer":"&#X27;톤&#X27;"}\n',
                     encoding="utf-8")
        assert ah.load_history(p)[0]["answer"] == "'톤'"
    print("OK dehtml edge cases")


def test_corrupt_lines_and_prune():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "hist.jsonl"
        p.write_text('{broken\n{"note":"n","answer":"a"}\n', encoding="utf-8")
        rows = ah.load_history(p)
        assert len(rows) == 1  # 깨진 줄 스킵
        # prune: _MAX_KEEP 초과 시 최근 것만.
        old_keep = ah._MAX_KEEP
        ah._MAX_KEEP = 3
        try:
            for i in range(5):
                ah.record_answers([f"결정 노트 {i} — 판단"], {1: f"답{i}"}, path=p)
            lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
            assert len(lines) == 3
        finally:
            ah._MAX_KEEP = old_keep
    print("OK corrupt skip + prune")


def test_non_string_rows_skipped():
    # answer가 비문자열(손상)이어도 로드가 스킵 — 렌더 500 방지.
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "hist.jsonl"
        p.write_text('{"note":"결정 노트","answer":12345}\n'
                     '{"note":777,"answer":"답"}\n'
                     '{"note":"핵심 논지를 어디로 세울지","answer":"형식 결정론"}\n',
                     encoding="utf-8")
        rows = ah.load_history(p)
        assert len(rows) == 1 and rows[0]["answer"] == "형식 결정론"
        # suggest도 안전.
        h = ah.suggest_from_history("핵심 논지를 어디에 세울지", path=p)
        assert h and h.answer == "형식 결정론"
        # category 비문자열(list 등) 행 → ""로 강제(요약 Counter 크래시 방지).
        p.write_text('{"note":"결정 노트입니다","answer":"답입니다","category":["관점"]}\n',
                     encoding="utf-8")
        rows2 = ah.load_history(p)
        assert rows2 and rows2[0]["category"] == ""
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ah.print_history_summary(p)  # 크래시 없이 요약
        assert "적립 1건" in buf.getvalue()
    print("OK non-string rows skipped (no render crash)")


def test_no_overmatch_on_common_endings():
    # 상투 어미만 닮은 전혀 다른 결정엔 재제안하지 않는다(성격+내용어 게이트).
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "hist.jsonl"
        ah.record_answers(["어떤 도시를 다룰지 정하세요"], {1: "서울로 한다"}, path=p)
        assert ah.suggest_from_history("어떤 말투로 쓸지 정하세요", path=p) is None
        ah.record_answers(["제목을 정하세요"], {1: "도시의 재구성"}, path=p)
        assert ah.suggest_from_history("결론을 정하세요", path=p) is None
        # 진짜 비슷한 결정(내용어 공유+같은 성격)은 여전히 재제안.
        assert ah.suggest_from_history("어떤 도시를 다룰지 고르세요", path=p) is not None
    print("OK no overmatch on common endings (real matches still work)")


def test_cli_resolve_no_self_echo():
    # 같은 실행의 --resolve 답이 --suggest '지난 답'으로 자기 반향되지 않는다.
    import io, json, contextlib
    from until import cli
    with tempfile.TemporaryDirectory() as d:
        old = ah.HISTORY_PATH
        ah.HISTORY_PATH = pathlib.Path(d) / "hist.jsonl"
        try:
            ans = pathlib.Path(d) / "answers.json"
            ans.write_text(json.dumps({"1": "형식 결정론으로"}), encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(["examples/sample_assignment.txt", "--backend", "mock",
                               "--resolve", str(ans), "--suggest"])
            assert rc == 0
            assert "지난 답:" not in buf.getvalue()  # 자기 반향 없음
            # 실행이 끝난 뒤엔 적립돼 있다(다음 실행에서 재제안 가능).
            assert len(ah.load_history()) == 1
        finally:
            ah.HISTORY_PATH = old
    print("OK CLI resolve answers recorded after output (no self-echo)")


def test_answers_style_hint():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "hist.jsonl"
        # 표본 부족(2개) → 힌트 없음.
        ah.record_answers(["결정 하나 — 판단", "결정 둘 — 판단"],
                          {1: "형식 결정론으로 갑니다.", 2: "짧게 다룹니다."}, path=p)
        assert ah.answers_style_hint(path=p) == ""
        # 합니다체 3개 이상 → 힌트 생성.
        ah.record_answers(["결정 셋 — 판단"], {1: "단호하게 마무리합니다."}, path=p)
        hint = ah.answers_style_hint(path=p)
        assert "합니다체" in hint and "문체" in hint
        # 파이프라인 suggest가 힌트 주입 상태에서도 정상(mock).
        from until.config import Config
        from until.pipeline import run, suggest_decision_answers
        old = ah.HISTORY_PATH
        ah.HISTORY_PATH = p
        try:
            cfg = Config(); cfg.backend = "mock"
            res = run(["examples/sample_assignment.txt"], cfg)
            sugg = suggest_decision_answers(res, cfg)
            assert sugg
        finally:
            ah.HISTORY_PATH = old
    print("OK answers style hint (min samples + 합니다체 + pipeline)")


def test_answers_context_hint():
    # 누적 '내 맥락' 힌트 — 표본 부족이면 빈 문자열, 쌓이면 반복 소재+개인 답 노출.
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "hist.jsonl"
        assert ah.answers_context_hint(path=p) == ""      # 기록 없음
        ah.record_answers(["예로 들 본인 경험 하나 — 본인 판단"],
                          {1: "편의점 아르바이트 경험"}, path=p)
        ah.record_answers(["핵심 논지를 어느 쪽으로 — 관점"],
                          {1: "도시재생 찬성 입장으로"}, path=p)
        assert ah.answers_context_hint(path=p) == ""      # 표본 2 < 3
        ah.record_answers(["다룰 사례 선택 — 무엇을"],
                          {1: "편의점 아르바이트 사례로"}, path=p)
        hint = ah.answers_context_hint(path=p)
        assert "내 맥락" in hint
        assert "아르바이트" in hint                        # 2회 반복 소재 추출
        assert "지어내지" in hint                          # 사실 창작 금지(경계선) 문구
        assert "찬성" in hint or "경험" in hint            # 진로·경험/관점·논지 답 원문
        # 파이프라인 run이 힌트 주입 상태에서도 정상(mock).
        from until.config import Config
        from until.pipeline import run
        old = ah.HISTORY_PATH
        ah.HISTORY_PATH = p
        try:
            cfg = Config(); cfg.backend = "mock"
            res = run(["examples/sample_assignment.txt"], cfg)
            assert res.draft.body
        finally:
            ah.HISTORY_PATH = old
    print("OK answers context hint (min samples + repeated topics + no-fabrication)")


def test_print_history_summary():
    import io, contextlib
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "hist.jsonl"
        ah.record_answers(["핵심 논지를 어디로 — 관점", "결론 톤 — 취향", "진로 연결 — 본인"],
                          {1: "형식 결정론입니다.", 2: "차분하게 갑니다.", 3: "반도체 진로와 잇습니다."},
                          path=p)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ah.print_history_summary(p)
        out = buf.getvalue()
        assert "적립 3건" in out and "성격 분포" in out and "최근 답" in out
        assert "합니다체" in out  # 문체 힌트 표시
        # 빈 경로 → 기록 없음.
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            ah.print_history_summary(pathlib.Path(d) / "none.jsonl")
        assert "기록 없음" in buf2.getvalue()
    print("OK print_history_summary")


def test_web_history_chip_and_finalize_records():
    import threading, http.client, re
    from urllib.parse import urlencode
    from until import web
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "hist.jsonl"
        old = ah.HISTORY_PATH
        ah.HISTORY_PATH = p
        web._Handler.backend = "mock"; web._Handler.sso = False
        httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        token = None
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)

            def post(path, fields):
                conn.request("POST", path, urlencode(fields),
                             {"Content-Type": "application/x-www-form-urlencoded"})
                r = conn.getresponse(); body = r.read().decode("utf-8")
                if r.status == 303:
                    conn.request("GET", r.getheader("Location")); r = conn.getresponse()
                    body = r.read().decode("utf-8")
                return r.status, body

            assign = "에세이를 써라. 한 기술이 한 제도를 재편한 과정을 분석하라."
            s, draft = post("/draft", {"assignment": assign})
            token = re.search(r'name="session" value="([^"]+)"', draft).group(1)
            # 결정 폼은 하나뿐이지만(2026-08-23) 고유 인덱스를 세는 편이
            # 화면 구조 변화에 안 흔들린다.
            n = len(set(re.findall(r'name="answer_(\d+)"', draft)))
            # finalize → 답이 히스토리에 적립된다.
            fields = {"session": token}
            for i in range(1, n + 1):
                fields[f"answer_{i}"] = f"과거의 내 선택 {i}"
            post("/finalize", fields)
            assert len(ah.load_history(p)) == n
            # 같은 답으로 다시 finalize → delta 없음 → 중복 적립 없음.
            post("/finalize", fields)
            assert len(ah.load_history(p)) == n
            # 같은 과제 다시 → 결정 필드에 '지난 답' 칩(data-val=원문 답).
            s, draft2 = post("/draft", {"assignment": assign})
            assert "지난 답:" in draft2
            assert 'data-val="과거의 내 선택 1"' in draft2
            # suggest 버튼 문구도 성향 반영으로 바뀐다.
            assert "내 지난 답 성향 반영" in draft2
            # 간단 모드에도 지난 답 칩(하나만·자동 채움 아님)이 뜬다.
            s, sdraft = post("/draft", {"assignment": assign, "ui": "simple"})
            assert s == 200 and "지난 답 ·" in sdraft
            assert 'data-val="과거의 내 선택 1"' in sdraft
            conn.close()
        finally:
            httpd.shutdown(); httpd.server_close()
            ah.HISTORY_PATH = old
            if token:
                try:
                    (web._SESS_DIR / f"{token}.pkl").unlink()
                except OSError:
                    pass
    print("OK web finalize records + history chip on next draft")


def test_web_history_page_and_clear():
    import threading, http.client
    from urllib.parse import urlencode
    from until import web
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "hist.jsonl"
        old = ah.HISTORY_PATH
        ah.HISTORY_PATH = p
        web._Handler.backend = "mock"; web._Handler.sso = False
        httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
            # 빈 상태.
            conn.request("GET", "/history"); r = conn.getresponse()
            body = r.read().decode("utf-8")
            assert r.status == 200 and "아직 기록이 없습니다" in body
            # 기록 후 목록·성향·삭제 버튼.
            ah.record_answers(["핵심 논지 — 관점", "톤 — 취향", "진로 연결"],
                              {1: "형식 결정론입니다.", 2: "차분하게 갑니다.", 3: "반도체와 잇습니다."})
            conn.request("GET", "/history"); r = conn.getresponse()
            body = r.read().decode("utf-8")
            assert "내 답 히스토리" in body and "형식 결정론입니다." in body
            assert "합니다체" in body and 'action="/history/clear"' in body
            assert 'id="histq"' in body and 'id="histlist"' in body  # 검색 필터
            # 진입점 — 홈 과밀을 줄이며 '이전 작업' 페이지로 이동(개인 데이터
            # 통제 접근성은 유지돼야 한다).
            conn.request("GET", "/sessions"); r = conn.getresponse()
            assert 'href="/history"' in r.read().decode("utf-8")
            # XSS — 답에 스크립트가 있어도 escape된다.
            ah.record_answers(["결정 <script>x</script> — 판단"],
                              {1: "답 <img src=x onerror=1>"})
            conn.request("GET", "/history"); r = conn.getresponse()
            page = r.read().decode("utf-8")
            assert "<script>x</script>" not in page and "<img src=x" not in page
            assert "&lt;script&gt;" in page
            # 전체 삭제 → 파일 제거 + 빈 상태.
            conn.request("POST", "/history/clear", urlencode({}),
                         {"Content-Type": "application/x-www-form-urlencoded"})
            r = conn.getresponse(); r.read()
            assert r.status == 303 and not p.exists()
            conn.request("GET", "/history"); r = conn.getresponse()
            assert "아직 기록이 없습니다" in r.read().decode("utf-8")
            conn.close()
        finally:
            httpd.shutdown(); httpd.server_close()
            ah.HISTORY_PATH = old
    print("OK web /history page + clear")


if __name__ == "__main__":
    test_record_and_load()
    test_suggest_similarity_threshold()
    test_latest_answer_wins()
    test_html_entities_dehtml()
    test_dehtml_edge_cases()
    test_corrupt_lines_and_prune()
    test_non_string_rows_skipped()
    test_no_overmatch_on_common_endings()
    test_cli_resolve_no_self_echo()
    test_answers_style_hint()
    test_answers_context_hint()
    test_print_history_summary()
    test_web_history_page_and_clear()
    test_web_history_chip_and_finalize_records()
    print("\nANSWER HISTORY TESTS PASS")
