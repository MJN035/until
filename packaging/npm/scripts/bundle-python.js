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

function copyDir(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name)) continue;
      copyDir(path.join(src, entry.name), path.join(dest, entry.name));
      continue;
    }
    if (SKIP_EXT.has(path.extname(entry.name))) continue;
    fs.copyFileSync(path.join(src, entry.name), path.join(dest, entry.name));
  }
}

function main() {
  if (!fs.existsSync(SRC)) {
    console.error(`bundle-python: 소스 패키지가 없습니다 — ${SRC}`);
    process.exit(1);
  }
  fs.rmSync(DEST_ROOT, { recursive: true, force: true });
  copyDir(SRC, DEST);
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
