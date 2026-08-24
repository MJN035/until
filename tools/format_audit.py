"""전체 과제 대상 형식 감사 — 형식 검증기를 3인 코퍼스 539건에 통째로 돌린다.

무엇을 재는가:

  1. **규칙 검출** — 과제마다 어떤 제출 형식을 요구하는지, Until이 그걸 자동으로
     맞춰 주는지(`fixed`) 사람에게 넘기는지(`남음`).
  2. **오탐 대조** — 학생이 **실제로 제출한 글**을 본문 자리에 넣고 검증기를 돌린다.
     사람이 쓴 글에는 우리 마커도 자료 번호도 슬롯 라벨도 없으므로, 산출물 형식
     층(B)이 그 글을 건드리면 그건 **전부 오탐**이다. 규칙 기반 검증기에서 제일
     위험한 건 미탐이 아니라 멀쩡한 글을 고치는 것이라 이걸 따로 센다.

실행: `PYTHONIOENCODING=utf-8 python tools/format_audit.py`
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from until.execution.format_guard import check_and_fix
from until.understanding.format_spec import detect_format_rules

CORPUS = ROOT / "_until_work" / "corpus"
PEOPLE = ("minjun", "jihu", "jaewon")


class _Doc:
    def __init__(self, text: str):
        self.text, self.source = text, "spec.md"


class _Draft:
    def __init__(self, body: str):
        self.body = body


class _Result:
    """감사용 최소 Result — 검증기가 보는 필드만 채운다."""

    def __init__(self, assignment: str, body: str, sources):
        self.documents = [_Doc(assignment)]
        self.draft, self.final_draft = _Draft(body), None
        self.sources, self.spec = list(sources), {}


def _assignment_text(base: Path, row: dict) -> str:
    parts = []
    spec = base / row["dir"] / "spec.md"
    if spec.exists():
        parts.append(spec.read_text(encoding="utf-8", errors="ignore"))
    ctx = base / row["dir"] / "etl_context" / "context.md"
    if ctx.exists():
        parts.append(ctx.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


def _submission_text(base: Path, row: dict) -> str:
    sub = base / row["dir"] / "submission.md"
    return sub.read_text(encoding="utf-8", errors="ignore") if sub.exists() else ""


def audit(limit: int = 0) -> dict:
    seen = 0
    with_rules = 0
    kinds = collections.Counter()
    applied = collections.Counter()      # 자동으로 맞춰 준 것
    left = collections.Counter()         # 사람에게 남긴 것
    unmet = []                           # 요구가 있는데 채울 수 없는 것
    fp_bodies = 0                        # 실제 제출본을 건드린 건수(= 오탐)
    fp_detail = collections.Counter()
    checked_bodies = 0
    per_person = collections.Counter()

    for who in PEOPLE:
        base = CORPUS / who
        manifest = base / "manifest.jsonl"
        if not manifest.exists():
            continue
        rows = [json.loads(x) for x in manifest.read_text(encoding="utf-8").splitlines() if x.strip()]
        if limit:
            rows = rows[:limit]
        for row in rows:
            seen += 1
            text = _assignment_text(base, row)
            if not text.strip():
                continue
            rules = detect_format_rules(text)
            if rules:
                with_rules += 1
                per_person[who] += 1
            for r in rules:
                kinds[("금지 " if r.forbidden else "") + r.kind] += 1

            # 요구가 있는 과제는 전부 검증기를 태워 '맞춰 줌 / 남김'을 센다.
            # 본문은 실제 제출본이 있으면 그것, 없으면 중립 자리표시.
            body = _submission_text(base, row)
            real = bool(body.strip())
            if not rules and not real:
                continue
            if not real:
                body = "본문 자리."
            out, issues = check_and_fix(
                _Result(text, body, sources=["자료 A", "자료 B"]),
                profile={"name": "홍길동", "student_id": "2020-00000"})
            for i in issues:
                (applied if i.fixed else left)[i.kind] += 1
                if not i.fixed and i.kind == "references":
                    unmet.append(f"{who}/{row['title'][:40]}")
            if not real:
                continue
            checked_bodies += 1
            # (B)층이 사람 글을 고쳤는가? 표지·참고문헌은 과제가 시킨 추가라 제외.
            structural = [i for i in issues
                          if i.fixed and i.kind in ("decision_marker", "citation_range",
                                                    "slot_label")]
            if structural:
                fp_bodies += 1
                for i in structural:
                    fp_detail[i.kind] += 1
    return {
        "과제": seen, "형식요구있음": with_rules, "제출본검사": checked_bodies,
        "kinds": kinds, "applied": applied, "left": left,
        "오탐본문": fp_bodies, "오탐상세": fp_detail,
        "사람별": per_person, "충족불가_참고문헌": unmet,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="사람당 상한(0=전부)")
    args = ap.parse_args()
    got = audit(args.limit)

    print(f"과제 {got['과제']}건 · 형식 요구가 있는 과제 "
          f"{got['형식요구있음']}건 ({got['형식요구있음'] * 100 // max(got['과제'], 1)}%)")
    print("  사람별: " + ", ".join(f"{k} {v}" for k, v in got["사람별"].items()))

    print("\n[요구 종류]")
    for k, v in got["kinds"].most_common():
        print(f"  {v:5d}  {k}")

    print(f"\n[실제 제출본 {got['제출본검사']}건에 검증기 적용]")
    print("  자동으로 맞춤:")
    for k, v in got["applied"].most_common():
        print(f"    {v:5d}  {k}")
    print("  사람에게 남김:")
    for k, v in got["left"].most_common():
        print(f"    {v:5d}  {k}")

    print(f"\n[오탐 — 사람이 쓴 글을 산출물 형식 층이 고친 건수] {got['오탐본문']}건")
    for k, v in got["오탐상세"].most_common():
        print(f"    {v:5d}  {k}")
    if got["충족불가_참고문헌"]:
        print(f"\n[참고문헌을 요구하는데 자료 0건] {len(got['충족불가_참고문헌'])}건")
        for name in got["충족불가_참고문헌"][:5]:
            print("    ·", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
