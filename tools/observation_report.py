"""Until 관찰 세션의 관리자 이벤트·비식별 텔레메트리 요약을 출력한다.

사용법:
    python tools/observation_report.py --users-root _until_work/users
    python tools/observation_report.py --users-root _until_work/users --since 2026-08-20

이 스크립트는 읽기 전용이다. 사용자 파일을 만들거나 수정하지 않으며, 과제 제목·본문과
uid 원문을 출력하지 않는다.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from until.adminboard import load_all, parse_record


FUNNEL = (
    ("홈", "visit"),
    ("연결", "connect"),
    ("초안", "draft"),
    ("완성", "final"),
    ("제출표시", "submitted"),
)


def _positive(value: object) -> int:
    """손상된 카운트 하나 때문에 전체 관찰 보고서가 사라지지 않게 0으로 닫는다."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _on_or_after(value: object, since: date | None) -> bool:
    """서로 다른 ISO 날짜·시각을 날짜 단위 필터 하나로 안전하게 맞춘다."""
    if since is None:
        return True
    text = str(value or "").strip()
    if not text:
        return False
    try:
        return date.fromisoformat(text[:10]) >= since
    except ValueError:
        return False


def _load_telemetry(users_root: Path, since: date | None) -> list[dict]:
    """uid별 JSONL을 기존 parse_record로 읽고 손상 행·중복 run을 건너뛴다."""
    rows: list[dict] = []
    seen_runs: set[str] = set()
    try:
        user_dirs = sorted(path for path in users_root.iterdir() if path.is_dir())
    except OSError:
        return rows

    for user_dir in user_dirs:
        for filename in ("telemetry.jsonl.1", "telemetry.jsonl"):
            try:
                lines = (user_dir / filename).read_bytes().splitlines()
            except OSError:
                continue
            for line in lines:
                row = parse_record(line)
                if row is None or not _on_or_after(row.get("date"), since):
                    continue
                run_id = row.get("run_id")
                if isinstance(run_id, str) and run_id:
                    if run_id in seen_runs:
                        continue
                    seen_runs.add(run_id)
                # 디렉터리 소유자는 집계 연결에만 쓰며 보고서에는 8자 마스킹만 내보낸다.
                row = dict(row)
                row["_owner_uid"] = user_dir.name
                rows.append(row)
    return rows


def _admin_records(users_root: Path, since: date | None) -> list[dict]:
    """관리자 원장의 공식 로더를 재사용하고 요청 기간의 활동 사용자만 남긴다."""
    return [
        record
        for record in load_all(users_root)
        if _on_or_after(record.get("last_seen"), since)
    ]


def _stage_users(admin_records: list[dict], telemetry: list[dict]) -> dict[str, set[str]]:
    """두 원장의 실제 저장 신호를 사람 단위 퍼널 단계로 합친다."""
    reached = {key: set() for _, key in FUNNEL}
    for record in admin_records:
        uid = str(record.get("uid") or "")
        if not uid:
            continue
        counts = record.get("counts") if isinstance(record.get("counts"), dict) else {}
        if _positive(counts.get("visit")):
            reached["visit"].add(uid)
        # connect·submitted 호출은 현재 허용 목록 밖이다. 실제로 남는 시도/성공과
        # export를 함께 받아 관찰용 근접 신호를 만들되, 출력 각주에서 한계를 밝힌다.
        if any(_positive(counts.get(key)) for key in ("connect", "token_try", "inbox")):
            reached["connect"].add(uid)
        if _positive(counts.get("draft")):
            reached["draft"].add(uid)
        if _positive(counts.get("final")):
            reached["final"].add(uid)
        if any(_positive(counts.get(key)) for key in ("submitted", "export")):
            reached["submitted"].add(uid)

    telemetry_stage = {"draft": "draft", "final": "final", "export": "submitted"}
    for row in telemetry:
        uid = str(row.get("_owner_uid") or "")
        key = telemetry_stage.get(str(row.get("stage") or ""))
        if uid and key:
            reached[key].add(uid)
    return reached


def _session_counts(admin_records: list[dict], telemetry: list[dict]) -> dict[str, int]:
    """과제 시작 신호인 draft 수를 세되 두 원장을 더해 중복 계산하지 않는다."""
    admin_counts: dict[str, int] = {}
    for record in admin_records:
        uid = str(record.get("uid") or "")
        counts = record.get("counts") if isinstance(record.get("counts"), dict) else {}
        if uid:
            admin_counts[uid] = _positive(counts.get("draft"))

    telemetry_counts: defaultdict[str, int] = defaultdict(int)
    for row in telemetry:
        uid = str(row.get("_owner_uid") or "")
        if uid and row.get("stage") == "draft":
            telemetry_counts[uid] += 1

    users = set(admin_counts) | set(telemetry_counts)
    return {uid: max(admin_counts.get(uid, 0), telemetry_counts.get(uid, 0))
            for uid in users}


def _masked(uid: str) -> str:
    """관리자 uid는 요구된 최소 범위인 앞 8자만 표시한다."""
    return (uid or "unknown")[:8]


def _print_funnel(stage_users: dict[str, set[str]]) -> None:
    print("퍼널")
    print(f"{'단계':<10} {'도달 사용자':>11} {'이탈률':>10}")
    previous: set[str] | None = None
    for label, key in FUNNEL:
        current = stage_users[key]
        if previous is None:
            reached = current
            dropout = "-"
        else:
            # 우회 경로가 있어도 퍼널은 앞 단계까지 연속 도달한 사용자만 센다.
            reached = previous & current
            rate = (1 - len(reached) / len(previous)) * 100 if previous else 0.0
            dropout = f"{rate:.1f}%"
        print(f"{label:<10} {len(reached):>11} {dropout:>10}")
        previous = reached


def _print_sessions(session_counts: dict[str, int]) -> None:
    print("\n사용자당 세션 수")
    print(f"{'uid(앞 8자)':<14} {'세션':>6}")
    for uid, count in sorted(session_counts.items(), key=lambda item: (_masked(item[0]), item[0])):
        print(f"{_masked(uid):<14} {count:>6}")
    reused = sum(count >= 2 for count in session_counts.values())
    print(f"\n재방문(2개 이상 과제) 사용자: {reused}명")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Until 관찰 세션 비식별 요약")
    parser.add_argument("--users-root", type=Path, required=True,
                        help="uid별 admin.json·telemetry.jsonl이 있는 users 디렉터리")
    parser.add_argument("--since", type=date.fromisoformat,
                        help="이 날짜(YYYY-MM-DD) 이후 기록만 포함")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    admin_records = _admin_records(args.users_root, args.since)
    telemetry = _load_telemetry(args.users_root, args.since)
    if not admin_records and not telemetry:
        print("데이터 없음: 관리자 이벤트와 텔레메트리 기록이 없습니다.")
        return 0

    if args.since is not None and admin_records:
        print("주의: 관리자 이벤트는 누적 카운트라 --since는 last_seen 기준 사용자 필터입니다.")
    _print_funnel(_stage_users(admin_records, telemetry))
    _print_sessions(_session_counts(admin_records, telemetry))
    print("\n주: 연결은 connect/token_try/inbox, 제출표시는 submitted/export 근접 신호입니다.")
    print("현재 관리자 허용 목록에는 connect·submitted가 없어 주로 token_try/inbox·export로 집계됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
