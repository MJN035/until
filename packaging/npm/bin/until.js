#!/usr/bin/env node
/*
 * until-mcp CLI — Python 소스를 그대로 동봉해 실행만 중개한다(`dependencies = []`라
 * pip/uv 없이 시스템 python3만 있으면 된다). 로직은 python/until/mcp_server.py에
 * 있다 — 이 파일은 python3 탐지 + stdio 통과 + `setup` 위임만 한다.
 *
 * 명령:
 *   until-mcp            → stdio JSON-RPC 서버 실행(기본, Claude Code/Codex가 부르는 형태)
 *   until-mcp serve      → 위와 동일(명시형)
 *   until-mcp setup      → Claude Code/Codex 설정에 등록(토큰은 묻지도 쓰지도 않음)
 *   until-mcp --list-tools → 도구 목록 JSON 출력(사람이 붙이기 전 확인용)
 */
'use strict';

const { spawn, spawnSync } = require('child_process');
const path = require('path');

const PKG_ROOT = path.join(__dirname, '..');
const PY_PATH = path.join(PKG_ROOT, 'python');

function findPython() {
  // Windows는 보통 `python`만 PATH에 있고(`python3`는 Microsoft Store 별칭 스텁으로
  // 걸려 있는 경우가 흔함 — 실행하면 조용히 실패한다), macOS/Linux는 `python3`가
  // 표준이다. 플랫폼별로 우선순위를 바꾸고, 셋 다 실제로 실행해 확인한다
  // (PATH에 이름만 있고 동작 안 하는 스텁을 걸러내기 위해 `--version`을 직접 돌려본다).
  const candidates = process.platform === 'win32'
    ? ['python', 'py', 'python3']
    : ['python3', 'python'];
  for (const cmd of candidates) {
    const probe = spawnSync(cmd, ['--version'], { stdio: 'ignore' });
    if (!probe.error && probe.status === 0) return cmd;
  }
  return null;
}

function fail(python) {
  if (python === null) {
    console.error(
      'until-mcp: 이 시스템에서 python3(또는 python)를 찾지 못했습니다.\n' +
      'Python 3.10 이상을 설치한 뒤 다시 시도하세요.\n' +
      '  macOS:   brew install python3  (또는 https://www.python.org/downloads/)\n' +
      '  Linux:   패키지 매니저(apt/dnf 등)로 python3 설치\n' +
      '  Windows: https://www.python.org/downloads/ 에서 설치 시 "Add python.exe to PATH" 체크\n' +
      'until-mcp 자체는 pip 설치가 필요 없습니다(순수 표준 라이브러리).'
    );
    process.exit(1);
  }
}

function main() {
  const args = process.argv.slice(2);
  const cmd = args[0];

  if (cmd === 'setup') {
    const python = findPython();
    fail(python);
    require('../lib/setup.js').run(args.slice(1), { python });
    return;
  }

  const python = findPython();
  fail(python);

  const env = Object.assign({}, process.env, { PYTHONPATH: PY_PATH });

  if (cmd === '--list-tools' || cmd === 'list-tools') {
    const r = spawnSync(python, ['-m', 'until.mcp_server', '--list-tools'],
      { stdio: 'inherit', env });
    process.exit(r.status === null ? 1 : r.status);
    return;
  }

  // 기본 동작 및 'serve' — stdio를 그대로 파이썬 프로세스로 이어준다.
  const child = spawn(python, ['-m', 'until.mcp_server'], { stdio: 'inherit', env });
  child.on('error', (err) => {
    console.error(`until-mcp: ${python} 실행 실패 — ${err.message}`);
    process.exit(1);
  });
  child.on('exit', (code, signal) => {
    if (signal) process.kill(process.pid, signal);
    else process.exit(code === null ? 1 : code);
  });
}

main();
