# -*- coding: utf-8 -*-
"""`until-mcp setup`(파이썬 경로) — 설정 병합 (오프라인·결정적, 격리된 임시 홈).

여기서 지키는 것:
  - 새 설정 파일을 만들 때 `until` MCP 서버 항목을 정확히 넣는다
  - 기존 설정(다른 MCP 서버·다른 키)을 지우거나 덮어쓰지 않는다
  - 이미 `until` 항목이 있으면 손대지 않고 그 사실만 알린다(멱등)
  - `packaging/npm/lib/setup.js`와 동작이 같다(둘 다 같은 산출물을 낸다)
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until import setup as s


def test_claude_config_created_fresh():
    with tempfile.TemporaryDirectory() as d:
        home = pathlib.Path(d)
        r = s.merge_claude_config_file(home)
        assert r["ok"] is True and r["already_existed"] is False and r["wrote_new_file"] is True
        data = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
        assert data["mcpServers"]["until"] == {"command": "until-mcp", "args": ["serve"]}
    print("OK .claude.json — 새 파일 생성 + until 항목 등록")


def test_claude_config_merge_preserves_existing_keys():
    with tempfile.TemporaryDirectory() as d:
        home = pathlib.Path(d)
        p = home / ".claude.json"
        p.write_text(json.dumps({"mcpServers": {"other": {"command": "foo"}},
                                 "someOtherKey": "preserve-me"}), encoding="utf-8")
        r = s.merge_claude_config_file(home)
        assert r["ok"] is True and r["wrote_new_file"] is False
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["mcpServers"]["other"] == {"command": "foo"}
        assert data["someOtherKey"] == "preserve-me"
        assert data["mcpServers"]["until"] == {"command": "until-mcp", "args": ["serve"]}
    print("OK .claude.json — 기존 키·다른 MCP 서버는 그대로, until만 추가")


def test_claude_config_is_idempotent():
    with tempfile.TemporaryDirectory() as d:
        home = pathlib.Path(d)
        s.merge_claude_config_file(home)
        r2 = s.merge_claude_config_file(home)
        assert r2["ok"] is True and r2["already_existed"] is True
    print("OK .claude.json — 두 번째 실행은 이미 있음으로 보고 아무것도 안 씀")


def test_claude_config_bad_json_is_not_touched():
    with tempfile.TemporaryDirectory() as d:
        home = pathlib.Path(d)
        p = home / ".claude.json"
        p.write_text("{not valid json", encoding="utf-8")
        r = s.merge_claude_config_file(home)
        assert r["ok"] is False
        assert p.read_text(encoding="utf-8") == "{not valid json"
    print("OK .claude.json — 파싱 안 되는 기존 파일은 손대지 않고 실패로 보고")


def test_codex_config_created_fresh():
    with tempfile.TemporaryDirectory() as d:
        home = pathlib.Path(d)
        r = s.merge_codex_config(home)
        assert r["ok"] is True and r["wrote_new_file"] is True
        text = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
        assert "[mcp_servers.until]" in text
        assert 'command = "until-mcp"' in text
    print("OK config.toml — 새 파일 생성 + [mcp_servers.until] 등록")


def test_codex_config_merge_preserves_existing_block():
    with tempfile.TemporaryDirectory() as d:
        home = pathlib.Path(d)
        codex_dir = home / ".codex"
        codex_dir.mkdir()
        p = codex_dir / "config.toml"
        p.write_text('[mcp_servers.other]\ncommand = "bar"\n', encoding="utf-8")
        r = s.merge_codex_config(home)
        assert r["ok"] is True and r["wrote_new_file"] is False
        text = p.read_text(encoding="utf-8")
        assert '[mcp_servers.other]' in text and 'command = "bar"' in text
        assert "[mcp_servers.until]" in text
    print("OK config.toml — 기존 [mcp_servers.other] 블록은 그대로, until만 추가")


def test_codex_config_is_idempotent():
    with tempfile.TemporaryDirectory() as d:
        home = pathlib.Path(d)
        s.merge_codex_config(home)
        r2 = s.merge_codex_config(home)
        assert r2["ok"] is True and r2["already_existed"] is True
    print("OK config.toml — 두 번째 실행은 이미 있음으로 보고 아무것도 안 씀")


def test_generic_fragment_matches_registered_entries():
    frag = s.generic_fragment()
    assert frag == {"mcpServers": {"until": {"command": "until-mcp", "args": ["serve"]}}}
    print("OK 기타 도구용 조각이 Claude/Codex에 실제로 등록하는 값과 동일")


def test_run_never_touches_real_home_when_isolated():
    """`run(home=...)`이 실제로 주어진 home만 쓰는지 — 회귀 방지.

    `try_claude_cli()`는 `home`과 무관하게 진짜 `claude` CLI를 부른다(전역
    등록이 그 명령의 정의라 home을 안 받는다) — 그래서 이 테스트는 그 경로를
    막고(실제 머신의 전역 설정을 이 테스트가 건드리면 안 된다) 파일 병합
    경로만 검증한다.
    """
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as d:
        home = pathlib.Path(d)
        with patch.object(s, "try_claude_cli", return_value={"ok": False, "reason": "test"}):
            result = s.run(home=home)
        assert (home / ".claude.json").exists()
        assert result["claude"]["path"] == str(home / ".claude.json")
        assert (home / ".codex" / "config.toml").exists()
    print("OK run(home=...)이 주입된 홈 디렉터리에만 씀(claude CLI 경로는 격리)")


if __name__ == "__main__":
    test_claude_config_created_fresh()
    test_claude_config_merge_preserves_existing_keys()
    test_claude_config_is_idempotent()
    test_claude_config_bad_json_is_not_touched()
    test_codex_config_created_fresh()
    test_codex_config_merge_preserves_existing_block()
    test_codex_config_is_idempotent()
    test_generic_fragment_matches_registered_entries()
    test_run_never_touches_real_home_when_isolated()
    print("\nSETUP TESTS PASS")
