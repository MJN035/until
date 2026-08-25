#!/usr/bin/env node
/*
 * `dependencies = []`(파이썬 표준 라이브러리만 쓴다)라서 가능한 트릭 — 파이썬
 * 소스를 npm 패키지에 그대로 동봉한다. pip도 uv도 필요 없고 시스템 python3만
 * 있으면 된다. `npm pack`/`npm publish` 전에(prepack) 이 스크립트가 저장소
 * 루트의 `until/` 패키지를 `packaging/npm/python/until/`로 복사한다.
 */
'use strict';

const fs = require('fs');
const path = require('path');

const REPO_ROOT = path.join(__dirname, '..', '..', '..');
const SRC = path.join(REPO_ROOT, 'until');
const DEST_ROOT = path.join(__dirname, '..', 'python');
const DEST = path.join(DEST_ROOT, 'until');

const SKIP_DIRS = new Set(['__pycache__']);
const SKIP_EXT = new Set(['.pyc']);

/*
 * 9개 MCP 도구(읽기 전용, LLM 호출 0건)가 실제로 필요로 하지 않는 웹앱·결제·인증·
 * 관리자·텔레메트리·평가 하니스 코드는 npm 패키지에 넣지 않는다. 이 파일들은
 * 전부 web.py/asgi.py/cli.py 같은 '진입점'에서만 참조되고, mcp_server.py의 지연
 * import 그래프(레포 루트에서 직접 재현해 확인함)에는 등장하지 않는다 — 각 항목의
 * 모든 참조자를 grep으로 재귀 확인해서 이 목록 안에서 닫히는 것만 뺐다(예:
 * readiness.py가 lazy import하는 presentation_export.py·runner/는 여기 없다 —
 * 뺐으면 특정 과제 유형에서만 터지는 잠재 버그가 됐을 것).
 * 최종 검증은 packaging/npm/python을 PYTHONPATH로 잡고 전체 테스트 스위트를
 * 돌려서 한다(README 참고) — 이 목록만 보고 안전하다고 가정하지 않는다.
 */
const SKIP_FILES = new Set([
  'web.py', 'asgi.py', 'billing.py', 'pg_webhook.py', 'adminboard.py',
  'google_auth.py', 'kakao_auth.py', 'session_store.py', 'etltoken.py',
  'cli.py', '__main__.py', 'web_templates.py', 'promptpack.py', 'report.py',
  'profile.py', 'plan.py', 'feedback.py', 'diffview.py', 'demo_showcase.py',
  'requirement_trace.py', 'cloudkv.py', 'analytics.py',
  'personalization_board.py', 'betarequests.py', 'policy_profiles.py',
  // pipeline.py: TASK-019 회귀 테스트가 until_route/until_readiness 호출 시
  // until.pipeline이 모듈 그래프에 절대 안 딸려온다는 걸 이미 코드로 증명해 뒀다.
  'pipeline.py',
]);
const SKIP_RELDIRS = new Set([
  'telemetry', 'webassets', 'evals', 'optimize', 'persona',
]);
// 상대경로(POSIX 구분자)가 이 집합에 있으면 파일 하나만 제외한다(디렉터리 전체가 아님).
const SKIP_RELFILES = new Set([
  'runtime/cli.py', 'runtime/cli_agent.py', 'runtime/__main__.py',
]);

function copyDir(src, dest, relDir) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const relPath = relDir ? `${relDir}/${entry.name}` : entry.name;
    if (entry.isDirectory()) {
      // 점 디렉터리(.omc/.git/.pytest_cache/...)는 로컬 세션·캐시 상태다 —
      // filesystem을 그대로 긁으므로 .gitignore가 걸러주지 않는다. 방금 실제로
      // 이 세션의 .omc 상태 파일이 tarball에 실려 나갈 뻔한 걸 잡았다.
      if (entry.name.startsWith('.')) continue;
      if (SKIP_DIRS.has(entry.name) || SKIP_RELDIRS.has(relPath)) continue;
      copyDir(path.join(src, entry.name), path.join(dest, entry.name), relPath);
      continue;
    }
    if (SKIP_EXT.has(path.extname(entry.name))) continue;
    if (!relDir && SKIP_FILES.has(entry.name)) continue;
    if (SKIP_RELFILES.has(relPath)) continue;
    fs.copyFileSync(path.join(src, entry.name), path.join(dest, entry.name));
  }
}

function main() {
  if (!fs.existsSync(SRC)) {
    console.error(`bundle-python: 소스 패키지가 없습니다 — ${SRC}`);
    process.exit(1);
  }
  fs.rmSync(DEST_ROOT, { recursive: true, force: true });
  copyDir(SRC, DEST, '');
  let n = 0;
  const walk = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      if (e.isDirectory()) walk(path.join(d, e.name));
      else n++;
    }
  };
  walk(DEST);
  console.log(`bundle-python: ${SRC} → ${DEST} (${n}개 파일) 복사 완료`);
}

main();
