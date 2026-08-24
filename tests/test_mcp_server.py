# -*- coding: utf-8 -*-
"""MCP 서버(stdio JSON-RPC) — 프로토콜 왕복과 읽기 전용 경계 (오프라인·결정적).

여기서 지키는 것:
  - `initialize` → `tools/list` → `tools/call` 왕복이 줄바꿈 구분 JSON으로 성립한다
  - 알림(id 없음)에는 응답하지 않는다
  - **생성 도구가 하나도 없다** — MCP 표면에 초안·문장 생성이 새어 나가면 실패
  - 토큰이 없을 때 크래시가 아니라 사람이 읽는 오류(isError)가 나온다
  - 도구 출력에 토큰이 실리지 않는다
  - 오프라인 도구(until_route·until_readiness)는 토큰 없이 실제로 동작한다
"""
from __future__ import annotations

import io
import json
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until import mcp_server as mcp

_TOKEN_ENVS = ("UNTIL_CANVAS_TOKEN", "UNTIL_ETL_WS_TOKEN", "UNTIL_ETL_WS")


class _NoToken:
    """토큰 env를 잠시 걷어낸다 — .env가 있는 개발 머신에서도 결정적이도록."""

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in _TOKEN_ENVS}
        for k in _TOKEN_ENVS:
            os.environ.pop(k, None)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


def _roundtrip(messages: list) -> list:
    """줄바꿈 구분 JSON을 서버에 흘려 넣고 나온 응답들을 파싱한다."""
    src = io.StringIO("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages))
    dst = io.StringIO()
    assert mcp.serve(stdin=src, stdout=dst) == 0
    return [json.loads(line) for line in dst.getvalue().splitlines() if line.strip()]


def _call(name: str, arguments: dict) -> dict:
    out = _roundtrip([{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": arguments}}])
    assert len(out) == 1, out
    return out[0]["result"]


def _payload(result: dict) -> dict:
    assert not result["isError"], result["content"][0]["text"]
    return json.loads(result["content"][0]["text"])


# ── 프로토콜 ─────────────────────────────────────────────────────────────
def test_initialize_negotiates_and_lists_tools():
    out = _roundtrip([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},   # 알림 — 무응답
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
    ])
    assert len(out) == 3, "알림에는 응답하면 안 된다"
    init = out[0]["result"]
    assert init["protocolVersion"] == "2025-06-18"
    assert init["capabilities"] == {"tools": {}}
    assert init["serverInfo"]["name"] == "until"
    names = [t["name"] for t in out[1]["result"]["tools"]]
    assert names == ["until_inbox", "until_assignment", "until_materials",
                     "until_route", "until_readiness", "until_series",
                     "until_control_tower", "until_semester", "until_brief"], names
    for tool in out[1]["result"]["tools"]:
        schema = tool["inputSchema"]
        assert schema["type"] == "object" and isinstance(schema["properties"], dict)
        assert isinstance(schema["required"], list)
    assert out[2]["result"] == {}
    print("OK initialize·tools/list·ping 왕복, 알림 무응답")


def test_unknown_protocol_version_falls_back_to_ours():
    out = _roundtrip([{"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "1999-01-01"}}])
    assert out[0]["result"]["protocolVersion"] == mcp.DEFAULT_PROTOCOL_VERSION
    print("OK 모르는 프로토콜 버전은 우리 기본값으로 응답")


def test_bad_input_does_not_kill_the_server():
    src = io.StringIO('not json\n{"jsonrpc":"2.0","id":1,"method":"nope"}\n'
                      '[1,2,3]\n{"jsonrpc":"2.0","id":2,"method":"ping"}\n')
    dst = io.StringIO()
    assert mcp.serve(stdin=src, stdout=dst) == 0
    out = [json.loads(x) for x in dst.getvalue().splitlines() if x.strip()]
    assert out[0]["error"]["code"] == -32700          # parse error
    assert out[1]["error"]["code"] == -32601          # method not found
    assert out[2]["error"]["code"] == -32600          # invalid request
    assert out[3]["result"] == {}                      # 그래도 살아 있다
    print("OK 깨진 입력·모르는 메서드에도 서버가 죽지 않는다")


# ── 읽기 전용 경계 ───────────────────────────────────────────────────────
def test_no_generation_tool_is_exposed():
    """초안·문장 생성이 MCP 표면에 새면 안 된다(TASK-008 절대 조건)."""
    banned = ("draft", "generate", "write", "finalize", "compose", "answer",
              "suggest", "submit", "초안", "생성")
    for tool in mcp.tool_definitions():
        blob = f"{tool['name']} {tool['description']}".lower()
        for word in banned:
            if word in ("draft", "초안", "생성"):
                continue          # 설명에 "초안을 받아 점검"처럼 등장할 수 있다
            assert word not in tool["name"].lower(), (tool["name"], word)
        assert "생성하지" in blob or "생성" not in tool["name"]
    names = {t["name"] for t in mcp.tool_definitions()}
    assert not (names & {"until_draft", "until_generate", "until_finalize",
                         "until_submit"})
    print("OK 생성 도구가 노출돼 있지 않다")


def test_offline_tools_work_without_a_token():
    with _NoToken():
        route = _payload(_call("until_route", {
            "title": "3주차 실험 예비보고서",
            "description": "실험 목적과 이론적 배경을 정리해 제출하세요.",
            "course_name": "기초전자실험",
        }))
        assert route["strategy"] and route["reason"]

        ready = _payload(_call("until_readiness", {
            "draft": ("# 서론\n" + "기후 변화는 소비 구조를 바꾼다. " * 20
                      + "[자료1]\n\n[[DECISION: 결론의 관점 — 본인 판단]]\n"),
            "assignment_text": "보고서를 800자 이상으로 작성하고 자료를 인용하세요.",
            "title": "기말 보고서",
        }))
        assert ready["n_decisions"] == 1
        assert ready["crossed_boundary"] is False
        assert isinstance(ready["items"], list) and ready["items"]
    print("OK 오프라인 도구(until_route·until_readiness)는 토큰 없이 동작")


def test_missing_token_is_a_friendly_error_not_a_crash():
    with _NoToken():
        for name, args in (("until_inbox", {}),
                           ("until_assignment", {"url": "https://e.edu/a/1"}),
                           ("until_materials", {"url": "https://e.edu/a/1"}),
                           ("until_series", {"title": "T", "course_id": "1"}),
                           ("until_control_tower", {"url": "https://e.edu/a/1"}),
                           ("until_semester", {}),
                           ("until_brief", {"url": "https://e.edu/a/1"})):
            result = _call(name, args)
            assert result["isError"] is True, name
            text = result["content"][0]["text"]
            assert "토큰" in text and "UNTIL_CANVAS_TOKEN" in text, (name, text)
    print("OK 토큰 없음 → 크래시가 아니라 무엇을 하면 되는지 말한다")


def test_required_arguments_are_checked_before_the_network():
    with _NoToken():
        for name, args, word in (("until_route", {}, "title"),
                                 ("until_readiness", {"draft": "  "}, "draft"),
                                 ("until_assignment", {}, "url"),
                                 ("until_materials", {}, "url"),
                                 ("until_series", {"title": "T"}, "course_id"),
                                 ("until_control_tower", {}, "url"),
                                 ("until_brief", {}, "url")):
            result = _call(name, args)
            assert result["isError"] is True, name
            assert word in result["content"][0]["text"], (name, word)
    print("OK 필수 인자 누락은 네트워크 전에 막힌다")


def test_token_never_appears_in_tool_output():
    os.environ["UNTIL_CANVAS_TOKEN"] = "SECRET-TOKEN-DO-NOT-LEAK"
    try:
        for name, args in (("until_route", {"title": "보고서"}),
                           ("until_readiness", {"draft": "본문 [[DECISION: x]]"}),
                           ("until_inbox", {}),
                           ("until_series", {"title": "T", "course_id": "1"}),
                           ("until_semester", {}),
                           ("until_control_tower", {"url": "https://e.edu/a/1"}),
                           ("until_brief", {"url": "https://e.edu/a/1"})):
            result = _call(name, args)
            assert "SECRET-TOKEN-DO-NOT-LEAK" not in json.dumps(
                result, ensure_ascii=False), name
    finally:
        os.environ.pop("UNTIL_CANVAS_TOKEN", None)
    print("OK 토큰이 도구 출력에 실리지 않는다")


def test_unknown_tool_is_an_error_result_not_an_exception():
    result = _call("until_nope", {})
    assert result["isError"] is True and "until_nope" in result["content"][0]["text"]
    print("OK 없는 도구는 예외가 아니라 isError 결과")


def test_route_tool_is_deterministic():
    args = {"title": "과제 2", "description": "Rmd를 채워 HTML로 제출",
            "course_name": "통계학실험"}
    first = _payload(_call("until_route", args))
    second = _payload(_call("until_route", args))
    assert first == second
    print("OK until_route는 같은 입력에 같은 출력")


if __name__ == "__main__":
    test_initialize_negotiates_and_lists_tools()
    test_unknown_protocol_version_falls_back_to_ours()
    test_bad_input_does_not_kill_the_server()
    test_no_generation_tool_is_exposed()
    test_offline_tools_work_without_a_token()
    test_missing_token_is_a_friendly_error_not_a_crash()
    test_required_arguments_are_checked_before_the_network()
    test_token_never_appears_in_tool_output()
    test_unknown_tool_is_an_error_result_not_an_exception()
    test_route_tool_is_deterministic()
    print("\nMCP SERVER TESTS PASS")
