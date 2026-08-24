"""
보관 기간 정책 + 사용자별 전체 삭제 경로.

**처음부터 넣는다.** 나중에 넣으면 못 넣는다 — 스토어가 늘어난 뒤에 삭제 경로를
만들면 반드시 하나를 빠뜨리고, 빠뜨린 파일은 "지웠다"는 약속을 조용히 어긴다.

감사 시점(docs/personalization-audit.md §4)의 실제 상태가 그랬다: 삭제가
`/sessions/delete`·`/history/clear`·`/voice/relearn` 세 라우트에 흩어져 있고
profile.json·feedback.jsonl·telemetry.jsonl·credits.json은 **어느 경로로도
지워지지 않았다.** 이 모듈은 그 목록을 한 곳에 모아 놓는 자리다.

설계 원칙:
  · 대상 파일 목록은 **한 곳(`USER_DATA_FILES`)에만** 있다. 새 스토어를 추가하는
    사람이 여기 한 줄을 넣지 않으면 테스트가 깨지도록 두는 것이 목적이다.
  · KV 미러 키 접두사도 함께 노출하되 **실제 KV 삭제는 하지 않는다** — 네트워크
    호출은 web 계층의 몫이고, 이 모듈은 순수·오프라인으로 남는다.
  · 예외를 밖으로 내지 않는다. 부분 실패도 결과 dict에 그대로 보고한다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

#: 사용자 개인 데이터 파일 전부. **여기 없으면 삭제되지 않는다.**
#: (파일명, 설명) — 클라우드는 users/<uid>/ 하위, 로컬은 _until_work/ 평면.
USER_DATA_FILES: Tuple[Tuple[str, str], ...] = (
    ("profile.json", "신상 프로필(이름·학번·연락처)"),
    ("persona.json", "톤 레지스터 페르소나 + L1 스타일 카드"),
    ("voice_profile.json", "자동 학습 문체 프로파일"),
    ("teacher_feedback.json", "교수 피드백 발췌"),
    ("answer_history.jsonl", "결정 답변 히스토리"),
    ("episodes.jsonl", "L2 에피소드 기억"),
    ("facts.json", "L3 사실 기억"),
    ("edit_events.jsonl", "수정 diff 기록"),
    ("persona_events.jsonl", "채널 중립 페르소나 이벤트"),
    ("feedback.jsonl", "베타 피드백 로그"),
    ("telemetry.jsonl", "비식별 텔레메트리"),
    ("telemetry.jsonl.1", "비식별 텔레메트리(회전본)"),
    ("consent.json", "텔레메트리 동의 기록"),
    ("credits.json", "크레딧 잔액"),
    ("usage.json", "일일 사용량"),
    ("admin.json", "관리자 보드 집계"),
    ("course_profiles.json", "과목 프로파일(사용자 직접 편집)"),
    ("etl_token.json", "보관된 eTL 연결(암호화)"),
)

#: KV 미러 키 접두사 — 삭제를 미러에도 반영하려면 web 계층이 이 목록을 돌아야 한다.
#: 실제 호출을 여기서 하지 않는 이유는 모듈 docstring 참조.
KV_KEY_PREFIXES: Tuple[str, ...] = (
    "prof:", "pers:", "vprof:", "tfb:", "cprof:", "etltok:", "hist:", "epi:", "fact:",
    "edit:", "pevt:", "consent:", "telem:", "credits:", "usage:", "adm:",
    "sess:",
)

#: 스토어별 기본 보관 일수. 0 = 만료 없음(사용자가 직접 지울 때까지).
#: 원문이 담긴 스토어를 짧게, 집계·설정을 길게 잡는다.
RETENTION_DAYS: Dict[str, int] = {
    "persona_events.jsonl": 180,   # 과제 원문·초안·최종본 포함 — 가장 짧게
    "episodes.jsonl": 365,
    "edit_events.jsonl": 365,
    "feedback.jsonl": 365,
    "telemetry.jsonl": 180,
    "telemetry.jsonl.1": 180,
    "answer_history.jsonl": 730,
    "facts.json": 0,               # 만료는 Fact.valid_until이 건별로 관리한다
    "profile.json": 0,
    "persona.json": 0,
    "voice_profile.json": 0,
    "teacher_feedback.json": 0,
    "course_profiles.json": 0,
    "etl_token.json": 0,   # 만료는 봉투의 exp가 건별로 관리한다
    "consent.json": 0,
    "credits.json": 0,
    "usage.json": 0,
    "admin.json": 0,
}

#: 각 JSONL 줄에서 시각을 찾을 때 볼 키(스토어마다 이름이 다르다).
_TS_KEYS = ("created_at", "ts", "timestamp", "learned_at")


@dataclass
class DeletionReport:
    deleted: List[str]
    missing: List[str]
    failed: Dict[str, str]

    @property
    def ok(self) -> bool:
        return not self.failed

    def to_dict(self) -> dict:
        return {"deleted": list(self.deleted), "missing": list(self.missing),
                "failed": dict(self.failed), "ok": self.ok}

    @property
    def headline(self) -> str:
        base = f"삭제 {len(self.deleted)}건 · 없음 {len(self.missing)}건"
        return base + (f" · 실패 {len(self.failed)}건" if self.failed else "")


def _root(root: Optional[Path]) -> Path:
    return Path(root) if root is not None else Path("_until_work")


def delete_all_user_data(root: Optional[Path] = None) -> DeletionReport:
    """한 사용자의 **모든** 개인 데이터를 지운다(데이터 이동권·삭제권 대응).

    부분 실패를 성공으로 포장하지 않는다 — 지우지 못한 파일은 `failed`에 이유와
    함께 남고, 호출자가 사용자에게 그대로 알릴 수 있어야 한다.
    """
    base = _root(root)
    deleted: List[str] = []
    missing: List[str] = []
    failed: Dict[str, str] = {}
    for name, _desc in USER_DATA_FILES:
        target = base / name
        try:
            if not target.exists():
                missing.append(name)
                continue
            target.unlink()
            deleted.append(name)
        except OSError as exc:
            failed[name] = f"{type(exc).__name__}: {exc}"
    return DeletionReport(deleted=deleted, missing=missing, failed=failed)


def kv_keys_for(uid: str) -> List[str]:
    """이 사용자의 KV 미러 키 목록(세션 키는 접두사 조회가 필요해 접두사로 남긴다)."""
    uid = str(uid or "").strip()
    if not uid:
        return []
    return [f"{prefix}{uid}" for prefix in KV_KEY_PREFIXES]


def _row_date(row: dict) -> Optional[date]:
    for key in _TS_KEYS:
        value = row.get(key)
        if not isinstance(value, str) or not value:
            continue
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            continue
    return None


def _purge_jsonl(path: Path, keep_days: int, today: date) -> int:
    """JSONL에서 보관 기간이 지난 줄을 제거하고 제거 건수를 반환한다.

    시각을 못 읽는 줄은 **지우지 않는다** — 형식이 낯설다는 이유로 사용자
    데이터를 조용히 없애는 것이 이 함수가 저지를 수 있는 최악이다.
    """
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8",
                                             errors="replace").splitlines() if ln.strip()]
    except OSError:
        return 0
    kept: List[str] = []
    removed = 0
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            kept.append(line)          # 못 읽는 줄은 보존
            continue
        stamp = _row_date(row) if isinstance(row, dict) else None
        if stamp is not None and (today - stamp).days > keep_days:
            removed += 1
            continue
        kept.append(line)
    if removed:
        try:
            path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        except OSError:
            return 0
    return removed


def purge_expired(today: Optional[date] = None,
                  root: Optional[Path] = None) -> Dict[str, int]:
    """보관 기간이 지난 기록을 제거한다. 반환: {파일명: 제거 건수}(0건은 생략).

    JSONL만 줄 단위로 지운다. JSON 스토어(설정·프로파일)는 보관 기간 0(만료 없음)이라
    대상이 아니며, 만료가 필요한 사실 기억은 `context/facts.py`가 건별
    `valid_until`로 이미 처리한다 — 같은 일을 두 곳에서 하지 않는다.
    """
    base = _root(root)
    day = today or date.today()
    out: Dict[str, int] = {}
    for name, _desc in USER_DATA_FILES:
        keep = RETENTION_DAYS.get(name, 0)
        if keep <= 0 or not name.endswith((".jsonl", ".jsonl.1")):
            continue
        target = base / name
        if not target.exists():
            continue
        removed = _purge_jsonl(target, keep, day)
        if removed:
            out[name] = removed
    return out


def describe(root: Optional[Path] = None) -> str:
    """CLI용 요약 — 무엇이 남아 있고 언제 지워지는지 사용자가 볼 수 있게."""
    base = _root(root)
    present = [(n, d) for n, d in USER_DATA_FILES if (base / n).exists()]
    if not present:
        return f"저장된 개인 데이터 없음 ({base})"
    lines = [f"개인 데이터 {len(present)}건 ({base})"]
    for name, desc in present:
        keep = RETENTION_DAYS.get(name, 0)
        policy = f"{keep}일 보관" if keep else "만료 없음"
        size = (base / name).stat().st_size
        lines.append(f"  · {name} — {desc} · {policy} · {size:,}B")
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    """`python -m until.persona.retention` — 보기 / --purge / --delete-all.

    `--delete-all`은 되돌릴 수 없으므로 `--yes`를 함께 요구한다.
    """
    import sys
    args = list(argv if argv is not None else sys.argv[1:])
    if "--purge" in args:
        removed = purge_expired()
        total = sum(removed.values())
        print(f"보관 기간 경과 {total}건 제거"
              + (f" — {removed}" if removed else ""))
        return 0
    if "--delete-all" in args:
        if "--yes" not in args:
            print("되돌릴 수 없습니다. 확인하려면 --delete-all --yes 로 다시 실행하세요.")
            print(describe())
            return 2
        report = delete_all_user_data()
        print(report.headline)
        for name in report.deleted:
            print(f"  삭제됨: {name}")
        for name, reason in report.failed.items():
            print(f"  실패: {name} — {reason}")
        if not report.ok:
            print("일부 파일을 지우지 못했습니다. 위 목록을 확인하세요.")
        return 0 if report.ok else 1
    print(describe())
    print("(정리: --purge · 전체 삭제: --delete-all --yes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
