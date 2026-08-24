"""로컬 eTL 코퍼스 전수 처리경로 감사(원문·개인정보 출력 없음)."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from until.context.assignment_router import route_assignment


def audit(root: Path) -> tuple[int, Counter, list[str]]:
    manifest = root / "manifest.jsonl"
    rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    counts: Counter = Counter()
    failures = []
    for row in rows:
        directory = root / row["dir"]
        spec_path = directory / "spec.md"
        description = spec_path.read_text(encoding="utf-8") if spec_path.exists() else ""
        # 정답셋인 과거 제출물은 라우팅 입력이 아니다. 신규 과제 시점에 실제로
        # 존재하는 과제 첨부만 사용해야 미래 정보 누수가 없다.
        names = [p.name for p in (directory / "intro_files").glob("*") if p.is_file()]
        route = route_assignment(title=row.get("title", ""), description=description,
                                 attachment_names=names,
                                 course_name=row.get("course_name", ""))
        counts[route.strategy] += 1
        if not route.strategy or (route.actionable and not route.required_evidence):
            failures.append(str(row.get("assignment_id", "?")))
    return len(rows), counts, failures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="_until_work/corpus/minjun")
    ap.add_argument("--minimum", type=int, default=109,
                    help="검증해야 할 최소 과제 수(기본: 대화에서 확인한 109개)")
    args = ap.parse_args()
    total, counts, failures = audit(Path(args.root))
    print(f"coverage={total - len(failures)}/{total} (requested minimum={args.minimum})")
    for name, count in counts.most_common():
        print(f"  {name}: {count}")
    if total < args.minimum or failures:
        print("uncovered ids:", ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
