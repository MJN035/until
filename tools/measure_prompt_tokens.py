"""프롬프트 토큰 실측 — 초안 1건이 실제로 몇 토큰을 태우는지 네트워크 없이 센다.

  python tools/measure_prompt_tokens.py                       # unit 모드, 60건
  python tools/measure_prompt_tokens.py --mode legacy
  python tools/measure_prompt_tokens.py --root _until_work/corpus/jaewon --limit 20
  python tools/measure_prompt_tokens.py --out _until_work/prompt_tokens.json

왜 필요한가
-----------
`docs/LLM_COST_STRATEGY.md` §2는 동의된 실사용 텔레메트리로 A_in·A_out을 구하라고 한다.
그 경로는 옳지만 **텔레메트리가 켜져 있어야** 하고 2주가 걸린다. 유료 제공자로 넘어갈지
말지는 그 전에 판단해야 하므로, 입력 토큰만이라도 오늘 확정한다.

입력 토큰은 우리가 만드는 값이라 모델 없이도 정확히 셀 수 있다. `pipeline.build_client`를
가로채 **실제로 전송될 system·user·documents 문자열을 그대로** 붙잡아 세면 된다.
백엔드는 mock이라 네트워크·비용이 0이다.

한계(전부 과소추정 방향, `--mode` 무관)
--------------------------------------
1. mock 출력이 실제 모델 출력보다 짧다. 뒷단(execution)은 앞단 출력을 프롬프트에 싣기
   때문에 실제 입력은 여기 수치보다 크다.
2. eTL 자료 본문 수집(`collect_with_materials`)이 붙는 실사용 경로는 발췌가 더 들어간다.
3. 경계선 재질문(reask)·429 폴백 재시도는 세지 않는다.
4. 반대로 프롬프트 캐싱은 반영하지 않았다 — 이건 실제를 더 싸게 만든다.

출력 토큰은 mock으로 알 수 없다. `UNTIL_MAX_TOKENS`(기본 2048)가 호출당 상한이고,
understanding·requirements는 짧은 JSON이라 실질 출력은 초안 본문이 지배한다.
정확한 A_out은 라이브 usage 응답으로만 확정된다 — 이 스크립트는 A_in 전용이다.

토크나이저는 tiktoken(o200k_base)을 쓰고, 없으면 문자수 기반 근사로 떨어진다(경고 출력).
근사는 제공자별 토크나이저와 다르므로 자릿수 감각용이지 계약 수치가 아니다.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from until.config import Config
from until.context.assignment_router import route_assignment
import until.pipeline as pipeline_module


def _encoder():
    """(세는 함수, 토크나이저 이름) — tiktoken이 없으면 문자수 근사로 폴백."""
    try:
        import tiktoken
    except ImportError:
        # 한국어는 o200k_base에서 대략 1토큰 ≈ 1.4자. 자릿수 감각용 근사.
        print("[warn] tiktoken 없음 → 문자수 근사(1토큰≈1.4자). pip install tiktoken 권장")
        return (lambda s: int(len(s or "") / 1.4)), "approx-chars"
    enc = tiktoken.get_encoding("o200k_base")
    return (lambda s: len(enc.encode(s or "", disallowed_special=()))), "o200k_base"


class _PromptRecorder:
    """complete()를 위임하면서 전송될 프롬프트의 토큰 수만 기록하는 투명 프록시."""

    def __init__(self, inner, calls: list, lock: threading.Lock, ntok) -> None:
        self.inner = inner
        self.calls = calls
        self.lock = lock
        self.ntok = ntok

    def complete(self, system, user, *, tag="", json=False, schema=None,
                 documents=None, cache=True):
        docs = sum(self.ntok(getattr(d, "title", "")) + self.ntok(getattr(d, "text", ""))
                   for d in (documents or []))
        record = {"tag": tag or "-", "system": self.ntok(system),
                  "user": self.ntok(user), "documents": docs}
        record["in"] = record["system"] + record["user"] + record["documents"]
        with self.lock:
            self.calls.append(record)
        return self.inner.complete(system, user, tag=tag, json=json, schema=schema,
                                   documents=documents, cache=cache)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def measure_one(paths: list, mode: str, ntok) -> list:
    """한 과제를 파이프라인에 통과시키고 호출별 입력 토큰 기록을 돌려준다."""
    calls: list = []
    lock = threading.Lock()
    original = pipeline_module.build_client

    def factory(backend: str, model: str):
        return _PromptRecorder(original(backend, model), calls, lock, ntok)

    cfg = Config(backend="mock", parser_backend="basic")
    cfg.pipeline_mode = mode
    pipeline_module.build_client = factory
    try:
        pipeline_module.run(paths, cfg)
    finally:
        pipeline_module.build_client = original
    return calls


def _files(directory: Path, folder: str) -> list:
    root = directory / folder
    return sorted((p for p in root.glob("*") if p.is_file()), key=lambda p: p.name.lower())


def _paths_for(corpus: Path, row: dict) -> list:
    """파이프라인 입력 = 과제 명세 + 첨부 + eTL 컨텍스트(있을 때)."""
    directory = corpus / row["dir"]
    spec = directory / "spec.md"
    if not spec.exists():
        return []
    context = (corpus / row["context_path"] if row.get("context_path")
               else directory / "etl_context" / "context.md")
    paths = [str(spec), *map(str, _files(directory, "intro_files"))]
    if context.is_file():
        paths.append(str(context))
    return paths


def _percentile(values: list, q: float) -> int:
    """정렬된 리스트의 최근접 순위 백분위 — 표본이 작아 보간은 과한 정밀도다."""
    if not values:
        return 0
    index = max(0, min(len(values) - 1, round(q * (len(values) - 1))))
    return values[index]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="_until_work/corpus/minjun")
    parser.add_argument("--mode", choices=("unit", "legacy"), default="unit")
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    ntok, tokenizer = _encoder()
    corpus = Path(args.root)
    manifest = corpus / "manifest.jsonl"
    if not manifest.is_file():
        print(f"manifest 없음: {manifest}")
        return 2
    rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()
            if line.strip()]

    measured: list = []
    skipped: Counter = Counter()
    for row in rows:
        if len(measured) >= args.limit:
            break
        paths = _paths_for(corpus, row)
        if not paths:
            skipped["missing_spec"] += 1
            continue
        description = Path(paths[0]).read_text(encoding="utf-8")
        route = route_assignment(
            title=row.get("title", ""), description=description,
            attachment_names=[Path(p).name for p in paths[1:]],
            course_name=row.get("course_name", ""))
        if not route.actionable:
            skipped["non_actionable"] += 1
            continue
        try:
            calls = measure_one(paths, args.mode, ntok)
        except Exception as exc:
            # AI 사용 금지 과제(AiUseProhibitedError) 등은 정상적인 제외 사유다.
            skipped[type(exc).__name__] += 1
            continue
        measured.append({
            "title": row.get("title", "")[:40], "strategy": route.strategy,
            "calls": len(calls), "tokens_in": sum(c["in"] for c in calls),
            "by_tag": {tag: sum(c["in"] for c in calls if c["tag"] == tag)
                       for tag in sorted({c["tag"] for c in calls})},
        })

    if not measured:
        print(f"측정된 과제 0건 (skipped={dict(skipped)})")
        return 1

    tokens = sorted(r["tokens_in"] for r in measured)
    calls = sorted(r["calls"] for r in measured)
    n = len(measured)
    tag_total: Counter = Counter()
    for record in measured:
        tag_total.update(record["by_tag"])

    print(f"\n=== mode={args.mode} tokenizer={tokenizer} n={n} 과제 "
          f"(skipped={dict(skipped)}) ===")
    print(f"calls/draft      min={calls[0]} p50={_percentile(calls, 0.5)} max={calls[-1]}")
    print(f"tokens_in/draft  min={tokens[0]:,} p50={_percentile(tokens, 0.5):,} "
          f"p90={_percentile(tokens, 0.9):,} p95={_percentile(tokens, 0.95):,} "
          f"max={tokens[-1]:,} mean={sum(tokens) // n:,}")
    print("tokens_in by tag (과제당 평균):")
    for tag, total in tag_total.most_common():
        print(f"   {tag:24s} {total // n:>8,d}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"mode": args.mode, "tokenizer": tokenizer,
                                   "n": n, "skipped": dict(skipped),
                                   "rows": measured}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        print(f"out={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
