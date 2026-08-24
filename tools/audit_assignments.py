"""Exported eTL assignment JSON을 개인정보 없이 일괄 점검한다.

입력: [{"title": str, "description": str, "attachment_count": int}, ...]
URL, 토큰, 제출물은 읽거나 출력하지 않는다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from until.practice_audit import audit_assignment


def audit_rows(rows: list[dict]) -> list[dict]:
    output = []
    for index, row in enumerate(rows, 1):
        title = str(row.get("title") or f"과제 {index}").strip()[:120]
        audit = audit_assignment(
            str(row.get("description") or ""),
            attachment_count=int(row.get("attachment_count") or 0),
        )
        output.append({"index": index, "title": title, **audit.to_dict()})
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="eTL 과제 수집 정확도·사전 중단 일괄 점검")
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    rows = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        parser.error("입력 최상위는 JSON 배열이어야 합니다")
    report = audit_rows(rows)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 1 if any(row["blockers"] for row in report) else 0


if __name__ == "__main__":
    raise SystemExit(main())
