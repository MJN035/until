"""`until-mcp setup` — Claude Code·Codex CLI 설정에 until MCP 서버를 등록한다.

`packaging/npm/lib/setup.js`와 같은 동작을 파이썬으로 낸다 — `pip install .`로
설치한 사용자는 Node.js가 없어도 `until-mcp setup`이 그대로 되어야 한다
(설계서 §3.3 "파이썬 사용자 경로"). 로직은 두 구현이 같아야 하므로 이 파일을
고치면 `packaging/npm/lib/setup.js`도 같이 봐야 한다.

절대 규칙:
  - 토큰을 묻지도 쓰지도 않는다. 등록만 한다(eTL 토큰은 UNTIL_CANVAS_TOKEN
    환경변수로 각자 넘긴다 — 이 스크립트가 관여할 일이 아니다).
  - 기존 설정 파일을 통째로 덮어쓰지 않는다. 없는 키만 추가한다.
  - `until` 항목이 이미 있으면 손대지 않고 그 사실만 알린다.
  - 표준 라이브러리만 쓴다(`dependencies = []` 불변) — TOML은 파싱하지 않고
    `[mcp_servers.until]` 존재 여부만 정규식으로 보고, 없으면 텍스트로 덧붙인다.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Optional


def claude_config_path(home: Path) -> Path:
    return home / ".claude.json"


def codex_config_path(home: Path) -> Path:
    return home / ".codex" / "config.toml"


def try_claude_cli() -> dict:
    """`claude mcp add`로 등록 시도. claude CLI가 없거나 실패하면 ok=False."""
    try:
        r = subprocess.run(
            ["claude", "mcp", "add", "until", "--scope", "user", "--",
             "until-mcp", "serve"],
            capture_output=True, text=True, encoding="utf-8", timeout=30)
    except (FileNotFoundError, OSError) as exc:
        return {"ok": False, "reason": f"claude CLI 실행 실패: {exc}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "claude CLI 응답 없음(타임아웃)"}
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip()
        if re.search(r"already exists|이미|duplicate", msg, re.I):
            return {"ok": True, "already_existed": True, "detail": msg}
        return {"ok": False, "reason": msg or f"claude mcp add 종료 코드 {r.returncode}"}
    return {"ok": True, "already_existed": False}


def merge_claude_config_file(home: Path) -> dict:
    p = claude_config_path(home)
    data: dict = {}
    existed = p.exists()
    if existed:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except ValueError as exc:
            return {"ok": False, "path": str(p),
                    "reason": f"기존 파일 파싱 실패(JSON 아님) — 손대지 않음: {exc}"}
    if isinstance(data.get("mcpServers"), dict) and "until" in data["mcpServers"]:
        return {"ok": True, "path": str(p), "already_existed": True}
    data.setdefault("mcpServers", {})
    data["mcpServers"]["until"] = {"command": "until-mcp", "args": ["serve"]}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"ok": True, "path": str(p), "already_existed": False, "wrote_new_file": not existed}


def merge_codex_config(home: Path) -> dict:
    p = codex_config_path(home)
    existed = p.exists()
    text = p.read_text(encoding="utf-8") if existed else ""
    if re.search(r"(?m)^\s*\[mcp_servers\.until\]", text):
        return {"ok": True, "path": str(p), "already_existed": True}
    sep = "\n" if text and not text.endswith("\n") else ""
    block = f'{sep}\n[mcp_servers.until]\ncommand = "until-mcp"\nargs = ["serve"]\n'
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(block)
    return {"ok": True, "path": str(p), "already_existed": False, "wrote_new_file": not existed}


def generic_fragment() -> dict:
    return {"mcpServers": {"until": {"command": "until-mcp", "args": ["serve"]}}}


def run(home: Optional[Path] = None) -> dict:
    """설정 등록을 실행하고 결과를 사람이 읽을 메시지와 함께 출력한다."""
    home = home or Path.home()
    print("until-mcp setup — 토큰은 묻지도 저장하지도 않습니다. MCP 서버 등록만 합니다.\n")

    claude_result = try_claude_cli()
    if not claude_result["ok"]:
        claude_result = merge_claude_config_file(home)
    if claude_result.get("already_existed"):
        loc = f" ({claude_result['path']})" if claude_result.get("path") else ""
        print(f"Claude Code: 이미 'until'이 등록돼 있습니다 — 그대로 둡니다{loc}.")
    elif claude_result["ok"]:
        loc = f" ({claude_result['path']})" if claude_result.get("path") else " (claude CLI)"
        print(f"Claude Code: 등록 완료{loc}.")
    else:
        print(f"Claude Code: 자동 등록 실패({claude_result['reason']}) — "
              "아래 조각을 수동으로 추가하세요.")

    codex_result = merge_codex_config(home)
    if codex_result.get("already_existed"):
        print(f"Codex CLI: 이미 [mcp_servers.until]이 있습니다 — "
              f"그대로 둡니다 ({codex_result['path']}).")
    else:
        print(f"Codex CLI: 등록 완료 ({codex_result['path']}).")

    print("\n다른 MCP 클라이언트용 표준 조각(직접 붙여넣으세요):")
    print(json.dumps(generic_fragment(), indent=2, ensure_ascii=False))

    return {"claude": claude_result, "codex": codex_result}
