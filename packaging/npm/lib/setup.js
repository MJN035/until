/*
 * `until-mcp setup` — Claude Code·Codex CLI 설정에 until MCP 서버를 등록한다.
 *
 * 절대 규칙:
 *   - 토큰을 묻지도 쓰지도 않는다. 등록만 한다(eTL 토큰은 UNTIL_CANVAS_TOKEN
 *     환경변수로 각자 넘긴다 — 이 스크립트가 관여할 일이 아니다).
 *   - 기존 설정 파일을 통째로 덮어쓰지 않는다. 없는 키만 추가한다.
 *   - `until` 항목이 이미 있으면 손대지 않고 그 사실만 알린다.
 *
 * home 디렉터리는 옵션으로 주입 가능(테스트용) — 기본은 os.homedir().
 */
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

function claudeConfigPath(home) {
  return path.join(home, '.claude.json');
}

function codexConfigPath(home) {
  return path.join(home, '.codex', 'config.toml');
}

function tryClaudeCli() {
  const r = spawnSync('claude', ['mcp', 'add', 'until', '--scope', 'user', '--',
                                 'until-mcp', 'serve'], { stdio: 'pipe', encoding: 'utf-8' });
  if (r.error) return { ok: false, reason: `claude CLI 실행 실패: ${r.error.message}` };
  if (r.status !== 0) {
    const msg = (r.stderr || r.stdout || '').trim();
    // 이미 등록돼 있으면 claude CLI가 0이 아닌 코드로 알리는 경우가 있다 —
    // "덮어쓰지 않는다" 원칙과 맞으므로 실패가 아니라 '이미 있음'으로 본다.
    if (/already exists|이미|duplicate/i.test(msg)) {
      return { ok: true, alreadyExisted: true, detail: msg };
    }
    return { ok: false, reason: msg || `claude mcp add 종료 코드 ${r.status}` };
  }
  return { ok: true, alreadyExisted: false };
}

function mergeClaudeConfigFile(home) {
  const p = claudeConfigPath(home);
  let data = {};
  let existed = fs.existsSync(p);
  if (existed) {
    try {
      data = JSON.parse(fs.readFileSync(p, 'utf-8'));
    } catch (e) {
      return { ok: false, path: p, reason: `기존 파일 파싱 실패(JSON 아님) — 손대지 않음: ${e.message}` };
    }
  }
  if (data.mcpServers && data.mcpServers.until) {
    return { ok: true, path: p, alreadyExisted: true };
  }
  data.mcpServers = data.mcpServers || {};
  data.mcpServers.until = { command: 'until-mcp', args: ['serve'] };
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, JSON.stringify(data, null, 2) + '\n', 'utf-8');
  return { ok: true, path: p, alreadyExisted: false, wroteNewFile: !existed };
}

function mergeCodexConfig(home) {
  const p = codexConfigPath(home);
  let text = '';
  const existed = fs.existsSync(p);
  if (existed) text = fs.readFileSync(p, 'utf-8');
  if (/^\s*\[mcp_servers\.until\]/m.test(text)) {
    return { ok: true, path: p, alreadyExisted: true };
  }
  const sep = text.length && !text.endsWith('\n') ? '\n' : '';
  const block = `${sep}\n[mcp_servers.until]\ncommand = "until-mcp"\nargs = ["serve"]\n`;
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.appendFileSync(p, block, 'utf-8');
  return { ok: true, path: p, alreadyExisted: false, wroteNewFile: !existed };
}

function genericFragment() {
  return { mcpServers: { until: { command: 'until-mcp', args: ['serve'] } } };
}

function run(args, opts) {
  opts = opts || {};
  const home = opts.home || os.homedir();
  console.log('until-mcp setup — 토큰은 묻지도 저장하지도 않습니다. MCP 서버 등록만 합니다.\n');

  // 1) Claude Code — claude CLI가 있으면 그걸로(공식 경로), 없으면 파일 직접 병합.
  let claudeResult = tryClaudeCli();
  if (!claudeResult.ok) {
    claudeResult = mergeClaudeConfigFile(home);
  }
  if (claudeResult.alreadyExisted) {
    console.log(`Claude Code: 이미 'until'이 등록돼 있습니다 — 그대로 둡니다` +
               (claudeResult.path ? ` (${claudeResult.path})` : '') + '.');
  } else if (claudeResult.ok) {
    console.log(`Claude Code: 등록 완료` + (claudeResult.path ? ` (${claudeResult.path})` : ' (claude CLI)') + '.');
  } else {
    console.log(`Claude Code: 자동 등록 실패(${claudeResult.reason}) — 아래 조각을 수동으로 추가하세요.`);
  }

  // 2) Codex CLI — 항상 파일 병합(Codex는 CLI로 MCP를 등록하는 표준 명령이 없다).
  const codexResult = mergeCodexConfig(home);
  if (codexResult.alreadyExisted) {
    console.log(`Codex CLI: 이미 [mcp_servers.until]이 있습니다 — 그대로 둡니다 (${codexResult.path}).`);
  } else {
    console.log(`Codex CLI: 등록 완료 (${codexResult.path}).`);
  }

  // 3) 기타 도구용 — 표준 mcpServers 조각을 그대로 출력.
  console.log('\n다른 MCP 클라이언트용 표준 조각(직접 붙여넣으세요):');
  console.log(JSON.stringify(genericFragment(), null, 2));

  return { claude: claudeResult, codex: codexResult };
}

module.exports = { run, claudeConfigPath, codexConfigPath, mergeClaudeConfigFile, mergeCodexConfig };
