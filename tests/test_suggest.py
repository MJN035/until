"""결정 AI 제안 + '모두 수락' 테스트 (오프라인·mock)."""
import sys, pathlib, threading, http.client, re
from urllib.parse import urlencode
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.config import Config
from until.pipeline import run, suggest_decision_answers, finalize
from until.boundary.models import Draft
from until.execution.suggest_answers import parse_suggestions, suggest_answers
from until.llm.mock_client import MockClient
from until import web


def test_parse_suggestions_robust():
    # 코드펜스/잡텍스트가 섞여도 첫 JSON 오브젝트를 뽑아 번호별로 정렬.
    txt = ('설명 ```json {"suggestions":[{"index":2,"answer":"B답","why":"이유B"},'
           '{"index":1,"answer":"A답","why":"이유A"}]} ``` 끝')
    out = parse_suggestions(txt, 2)
    assert set(out) == {1, 2}
    assert out[1]["answer"] == "A답" and out[2]["why"] == "이유B"
    # 범위 밖 index/빈 answer는 무시.
    bad = parse_suggestions('{"suggestions":[{"index":5,"answer":"x"},{"index":1,"answer":""}]}', 2)
    assert bad == {}
    # 깨진 JSON → 빈 dict(예외 없음).
    assert parse_suggestions("not json", 3) == {}
    print("OK parse_suggestions robust")


def test_suggest_answers_mock_aligns():
    d = Draft.from_text(
        "서론. " * 30 + "\n[[DECISION: 핵심 논지를 어디로 세울지 — 본인 관점]]\n"
        + "본론. " * 30 + "\n[[DECISION: 결론 톤을 어떻게 할지 — 본인 취향]]\n"
    )
    sugg = suggest_answers(d, {"deliverable": "에세이"}, MockClient())
    assert set(sugg) == {1, 2}, sugg
    for i in (1, 2):
        assert sugg[i]["answer"] and sugg[i]["why"]
    print("OK suggest_answers aligns to each decision (mock)")


def test_pipeline_suggest_then_accept_all():
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    n = res.draft.n_decisions
    assert n >= 1
    sugg = suggest_decision_answers(res, cfg)
    assert len(sugg) == n and all(1 <= k <= n for k in sugg)
    # '모두 수락' = 제안 answer를 그대로 사람 답으로 넘겨 최종본 생성.
    answers = {i: s["answer"] for i, s in sugg.items()}
    res = finalize(res, answers, cfg)
    assert res.final_draft is not None and res.final_guard is not None
    print("OK pipeline suggest -> accept-all -> finalize")


def test_suggest_noop_without_decisions():
    # 결정이 없으면 제안도 빈 dict(LLM 호출 생략).
    cfg = Config(); cfg.backend = "mock"

    class _NoDec:
        draft = Draft.from_text("결정 없는 본문." * 20)
        spec = {}
        context = None
    assert suggest_decision_answers(_NoDec(), cfg) == {}
    print("OK suggest no-op without decisions")


def _post(conn, path, fields, follow=True):
    conn.request("POST", path, urlencode(fields),
                 {"Content-Type": "application/x-www-form-urlencoded"})
    r = conn.getresponse(); body = r.read().decode("utf-8")
    if follow and r.status == 303:
        conn.request("GET", r.getheader("Location")); r = conn.getresponse()
        body = r.read().decode("utf-8")
    return r.status, body


def test_web_suggest_flow():
    cfg = Config(); web._Handler.backend = "mock"; web._Handler.sso = False
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        s, draft = _post(conn, "/draft",
                         {"assignment": "에세이를 써라. 한 기술이 한 제도를 재편한 과정을 분석하라."})
        assert s == 200 and "참고할 답 채우기" in draft     # 제안 전: 버튼 노출
        token = re.search(r'name="session" value="([^"]+)"', draft).group(1)
        # POST /suggest → (PRG) 제안 채워진 초안
        s, withsug = _post(conn, "/suggest", {"session": token})
        assert s == 200
        assert "전부 제안대로 수락" in withsug              # 수락 버튼으로 바뀜
        assert "제안 근거" in withsug                       # 근거 노출
        assert "참고할 답 채우기" not in withsug            # 버튼 숨김
        # 제안 그대로 수락(폼 프리필 값) → 최종본
        n = withsug.count('name="answer_')
        m = re.findall(r'name="answer_(\d+)"[^>]*>([^<]*)</textarea>', withsug)
        fields = {"session": token}
        for idx, val in m:
            fields[f"answer_{idx}"] = val
        s, final = _post(conn, "/finalize", fields)
        assert s == 200 and "최종 완성본" in final
        conn.close()
    finally:
        httpd.shutdown(); httpd.server_close()
    print("OK web suggest -> accept-all -> final flow")


def test_suggest_message_tags_categories():
    # 결정 목록에 결정적 분류 카테고리가 태깅되고, 성격별 지침 블록이 들어간다.
    from until.execution.suggest_answers import suggest_user_message
    msg = suggest_user_message(
        "{}",
        ["핵심 논지를 어디로 세울지 — 본인 관점", "나의 진로와 어떻게 연결할지"],
        "(자료)")
    assert "[관점·논지]" in msg and "[진로·경험]" in msg
    assert "[ 성격별 제안 지침 ]" in msg
    assert "관점·논지:" in msg and "진로·경험:" in msg
    # 같은 카테고리가 중복돼도 지침은 1회만.
    msg2 = suggest_user_message("{}", ["관점 A", "관점 B"], "(자료)")
    assert msg2.count("관점·논지:") == 1
    print("OK suggest message tags categories + hints")


def test_past_answers_injected_into_message():
    from until.execution.suggest_answers import suggest_user_message
    msg = suggest_user_message("{}", ["관점을 어디로", "톤을 어떻게"], "(자료)",
                               past={1: "형식 결정론으로"})
    assert "내 과거 결정 답" in msg and "1. 형식 결정론으로" in msg
    assert msg.index("내 과거 결정 답") < msg.index("[ 결정 목록")  # 결정 목록보다 앞
    # 없으면 블록도 없음(기존 동작 유지).
    assert "내 과거 결정 답" not in suggest_user_message("{}", ["관점"], "(자료)")
    print("OK past answers injected before decision list")


def test_pipeline_suggest_uses_history():
    # 히스토리에 mock 결정 노트와 같은 결정을 넣으면 suggest가 깨지지 않고 동작.
    import tempfile
    from until.context import answer_history as ah
    from until.pipeline import suggest_decision_answers
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    assert res.draft.decisions
    with tempfile.TemporaryDirectory() as d:
        old = ah.HISTORY_PATH
        ah.HISTORY_PATH = pathlib.Path(d) / "hist.jsonl"
        try:
            ah.record_answers([res.draft.decisions[0].note], {1: "지난번 내 선택"})
            sugg = suggest_decision_answers(res, cfg)
            assert sugg and 1 in sugg  # 히스토리 주입 상태에서도 정상 제안
        finally:
            ah.HISTORY_PATH = old
    print("OK pipeline suggest works with history injected")


def test_cli_suggest_writes_resolve_template():
    # CLI --suggest <경로> → 제안 출력 + --resolve용 answers JSON 템플릿 저장.
    import io, json, tempfile, os, contextlib
    from until import cli
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "answers.json")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["examples/sample_assignment.txt", "--backend", "mock",
                           "--suggest", p])
        assert rc == 0
        out = buf.getvalue()
        assert "결정 AI 제안" in out and "확정은 당신 몫" in out
        data = json.loads(open(p, encoding="utf-8").read())
        assert data and all(k.isdigit() for k in data)
        # 태그([관점·논지])가 답에 새지 않는다(mock 태그 벗김).
        assert not any(v.startswith("'[") for v in data.values())
        # 저장된 템플릿이 --resolve로 바로 먹힌다(왕복).
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            rc2 = cli.main(["examples/sample_assignment.txt", "--backend", "mock",
                            "--resolve", p])
        assert rc2 == 0 and "최종 완성본" in buf2.getvalue()
    print("OK CLI --suggest -> template -> --resolve roundtrip")


def test_prompt_education_patterns():
    # 결정마다 다른 프롬프트 기법을 번갈아 적용 + '왜 좋은지' 설명 + 하위호환 List[str].
    from until.prompts.suggest import suggest_prompts_detailed, suggest_prompts
    d = Draft.from_text(
        "본문. " * 20
        + "\n[[DECISION: 핵심 논지를 어디로 세울지 — 본인 관점]]\n"
        + "본문. " * 20 + "\n[[DECISION: 결론 톤을 어떻게 — 본인 취향]]\n"
        + "본문. " * 20 + "\n[[DECISION: 인용 범위를 어디까지 — 정직성]]\n"
    )
    det = suggest_prompts_detailed(d)
    assert len(det) == 3
    # 서로 다른 기법이 번갈아 적용된다(첫 3개는 모두 다름).
    assert len({s.pattern for s in det}) == 3, [s.pattern for s in det]
    for s in det:
        assert s.text and s.pattern and s.why
        assert "내가" in s.text or "내" in s.text  # 선택권은 사람에게
    # 하위호환: 문장 리스트.
    flat = suggest_prompts(d)
    assert flat == [s.text for s in det]
    print("OK prompt education patterns rotate + backward-compatible")


# ── 일부만 답하고 나머지는 맡기기(부분 자동 채움) ─────────────────────
_THREE = "\n".join([
    "서론. " * 30,
    "[[DECISION: 핵심 논지를 어디로 세울지 - 본인 관점]]",
    "본론. " * 30,
    "[[DECISION: 다룰 사례 범위를 어디까지 할지]]",
    "결론. " * 30,
    "[[DECISION: 결론 톤을 어떻게 할지 - 본인 취향]]",
])


def test_message_carries_my_answers_and_only_block():
    """내가 이번에 정한 답이 과거 히스토리보다 앞서고, 채울 번호가 명시된다."""
    from until.execution.suggest_answers import suggest_user_message
    msg = suggest_user_message("{}", ["관점을 어디로", "범위를 어디까지", "톤을 어떻게"],
                               "(자료)", past={2: "예전엔 좁게"},
                               mine={1: "기술 격차를 논지로"}, only=[2, 3])
    assert "내가 이미 정한 답(이번 과제)" in msg
    assert "기술 격차를 논지로" in msg
    assert "1. 관점을 어디로" in msg          # 내 답이 어느 질문의 것인지 함께 보인다
    # 이번 과제의 내 답 > 과거 히스토리 > 결정 목록 순서.
    assert msg.index("내가 이미 정한 답") < msg.index("내 과거 결정 답")
    assert msg.index("내 과거 결정 답") < msg.index("[ 결정 목록")
    assert "[ 제안할 번호 ] 2, 3" in msg
    assert "위 번호에 대해서만" in msg
    # 아무것도 안 주면 예전 문안 그대로(기존 동작 불변).
    plain = suggest_user_message("{}", ["관점을 어디로"], "(자료)")
    assert "내가 이미 정한 답" not in plain and "[ 제안할 번호 ]" not in plain
    assert "각 결정에 대해" in plain
    print("OK 제안 메시지 — 내 답 우선 + 채울 번호 한정")


def test_suggest_answers_fills_only_blanks():
    d = Draft.from_text(_THREE)
    assert d.n_decisions == 3
    got = suggest_answers(d, {"deliverable": "에세이"}, MockClient(),
                          my_answers={1: "기술 격차를 논지로"}, only=[2, 3])
    assert set(got) == {2, 3}, got          # 내가 정한 1번은 절대 덮지 않는다
    for i in (2, 3):
        assert got[i]["answer"]
    # 빈칸이 없으면 LLM을 부르지 않고 즉시 빈 dict.
    class _Boom(MockClient):
        def complete(self, *a, **kw):
            raise AssertionError("빈칸이 없는데 LLM을 불렀다")
    assert suggest_answers(d, {}, _Boom(), only=[]) == {}
    print("OK 빈칸만 채움 — 내 답 보존·빈칸 0이면 호출 생략")


def test_web_partial_autofill_keeps_my_answer():
    """간단 모드에서 1번만 채우고 '나머지는 나에 맞춰 채워줘' → 빈칸만 제안."""
    web._Handler.backend = "mock"; web._Handler.sso = False
    httpd = web.ThreadingHTTPServer(("127.0.0.1", 0), web._Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        s, draft = _post(conn, "/draft",
                         {"assignment": "에세이를 써라. 한 기술이 한 제도를 재편한 과정을 분석하라.",
                          "ui": "simple"})
        assert s == 200
        token = re.search(r'name="session" value="([^"]+)"', draft).group(1)
        # 결정 3개짜리 초안으로 바꿔 부분 답변 상황을 만든다(모델 비결정성 제거).
        web._SESSIONS[token].draft = Draft.from_text(_THREE)
        page = web.render_simple_draft(token, web._SESSIONS[token])
        assert "나머지는 나에 맞춰 채워줘" in page
        assert 'formaction="/suggest"' in page

        mine = "기술 격차를 핵심 논지로 세운다"
        s, filled = _post(conn, "/suggest",
                          {"session": token, "ui": "simple", "answer_1": mine,
                           "answer_2": "", "answer_3": "  "})
        assert s == 200
        # ① 타이핑한 답은 '내 답'으로 확정돼 왕복에서 유실되지 않는다.
        assert web._ANSWERS[token][1] == mine
        assert mine in filled
        # ② 제안은 빈칸(2·3)에만 붙는다 — 내가 정한 1번은 그대로.
        assert set(web._SUGGESTIONS[token]) == {2, 3}, web._SUGGESTIONS[token]
        # ③ 화면은 '추천이 채워져 있어요'로 상태를 밝힌다(자동 확정 아님).
        assert "추천이 채워져 있어요" in filled
        # ④ 그대로 완성하기 → 최종본까지 간다.
        vals = {"session": token, "ui": "simple", "answer_1": mine}
        for i in (2, 3):
            vals[f"answer_{i}"] = web._SUGGESTIONS[token][i]["answer"]
        s, final = _post(conn, "/finalize", vals)
        assert s == 200
        conn.close()
    finally:
        httpd.shutdown(); httpd.server_close()
    print("OK 부분 답변 → 빈칸 자동 채움 → 완성")


if __name__ == "__main__":
    test_parse_suggestions_robust()
    test_suggest_message_tags_categories()
    test_past_answers_injected_into_message()
    test_pipeline_suggest_uses_history()
    test_cli_suggest_writes_resolve_template()
    test_prompt_education_patterns()
    test_suggest_answers_mock_aligns()
    test_pipeline_suggest_then_accept_all()
    test_suggest_noop_without_decisions()
    test_web_suggest_flow()
    test_message_carries_my_answers_and_only_block()
    test_suggest_answers_fills_only_blanks()
    test_web_partial_autofill_keeps_my_answer()
    print("\nSUGGEST TESTS PASS")
