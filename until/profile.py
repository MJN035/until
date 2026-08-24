"""사용자 프로필 — 기본 신상정보를 1회 저장해 이후 자동 채움(결정적·LLM 0).

실사용 피드백 대응: 학교·소속·이메일처럼 '개인 GPT였으면 안 물어봐도 될' 값을
until이 매번 되물어 GPT 대비 강점이 사라진다. 프로필을 로컬에 1회 저장하면
  - 초안 생성 시 【내 프로필】 힌트로 주입 → 신상 칸을 되묻지 않고 채움
  - 양식 셀 주입(formfill) 때 라벨 매핑으로 사용
  - LMS(Canvas /users/self/profile)에서 아는 값은 빈 필드에 자동 보충
저장 위치는 `_until_work/profile.json`(gitignore 영역 — 개인 데이터 커밋 방지).
클라우드(멀티유저)는 answer_history와 같은 thread-local 경로 오버라이드 패턴.
"""
from __future__ import annotations

import json
import threading as _threading
from pathlib import Path
from typing import Dict, Optional

from . import atomicio

PROFILE_PATH = Path("_until_work/profile.json")

# 표준 필드(저장 스키마) — (키, 표시 이름, 양식 라벨 별칭들)
FIELDS = (
    ("name", "이름", ("이름", "성명", "신청자")),
    ("university", "소속 대학", ("대학", "소속", "소속대학")),
    ("department", "학과", ("학과", "전공", "소속대학·학과", "소속 대학·학과")),
    ("student_id", "학번", ("학번",)),
    ("phone", "연락처", ("연락처", "전화", "전화번호", "휴대폰", "휴대전화")),
    ("email", "이메일", ("이메일", "e-mail", "email", "메일")),
)

_TL_PATH = _threading.local()


def set_profile_path_override(p: Optional[Path]) -> None:
    """이 스레드(요청)의 프로필 경로 오버라이드 — 클라우드 사용자별 경로."""
    _TL_PATH.value = p


def _resolve_path(path: Optional[Path]) -> Path:
    if path is not None:
        return Path(path)
    o = getattr(_TL_PATH, "value", None)
    return Path(o) if o is not None else PROFILE_PATH


def profile_path() -> Path:
    return _resolve_path(None)


def load_profile(path: Optional[Path] = None) -> Dict[str, str]:
    """저장된 프로필 {키: 값}. 없거나 손상이면 빈 dict(비치명적)."""
    p = _resolve_path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    keys = {k for k, _, _ in FIELDS}
    return {k: str(v).strip() for k, v in raw.items()
            if k in keys and isinstance(v, (str, int)) and str(v).strip()}


def _write_profile(clean: Dict[str, str], p: Path) -> None:
    """잠금 없이 원자적으로만 쓴다 — 호출자가 이미 `path_lock`을 잡고 있어야 한다."""
    p.parent.mkdir(parents=True, exist_ok=True)
    atomicio.atomic_write_json(p, clean, indent=2)


def save_profile(values: Dict[str, str], path: Optional[Path] = None) -> Path:
    """프로필 저장(전체 교체 — 빈 값은 제거). 반환: 저장 경로."""
    p = _resolve_path(path)
    keys = {k for k, _, _ in FIELDS}
    clean = {k: str(v).strip() for k, v in (values or {}).items()
             if k in keys and str(v or "").strip()}
    with atomicio.path_lock(p):
        _write_profile(clean, p)
    return p


def merge_from_lms(values: Dict[str, str], path: Optional[Path] = None) -> Dict[str, str]:
    """LMS에서 가져온 값(name/email 등)으로 **빈 필드만** 보충한다.

    사용자가 직접 저장한 값은 절대 덮어쓰지 않는다. 반환: 병합 후 프로필.

    읽기→병합→쓰기 전체를 경로락 안에서 수행한다(잠금 없이 하면 /inbox의
    동시 호출이 서로의 병합 결과를 덮어쓸 수 있다 — 두 요청이 같은 "빈 필드"를
    보고 각자 다른 값으로 채운 뒤 마지막에 쓴 쪽만 남는다)."""
    p = _resolve_path(path)
    with atomicio.path_lock(p):
        cur = load_profile(p)
        added = {k: str(v).strip() for k, v in (values or {}).items()
                 if str(v or "").strip() and not cur.get(k)}
        if added:
            cur.update(added)
            _write_profile(cur, p)
        return cur


def student_id_from_lms_profile(values: Dict[str, str]) -> str:
    """Canvas 프로필의 명시적 식별자에서만 학번을 추출한다(이메일 추측 금지)."""
    import re
    for key in ("sis_user_id", "login_id", "integration_id"):
        m = re.fullmatch(r"\s*(\d{4})[-\s]?(\d{5})\s*", str((values or {}).get(key) or ""))
        if m:
            return f"{m.group(1)}-{m.group(2)}"
    return ""


def profile_hint(path: Optional[Path] = None) -> str:
    """실행 프롬프트에 넣는 【내 프로필】 블록 — 신상 칸을 되묻지 않고 채우게 한다."""
    prof = load_profile(path)
    if not prof:
        return ""
    label = {k: disp for k, disp, _ in FIELDS}
    lines = [f"- {label[k]}: {prof[k]}" for k, _, _ in FIELDS if prof.get(k)]
    return (
        "【내 프로필 — 본인이 저장한 기본정보 】\n"
        + "\n".join(lines) + "\n"
        "- 양식의 이름·소속·학번·연락처·이메일 칸은 위 값으로 채워라. 이 값들에 대해\n"
        "  [[DECISION]]으로 되묻지 말 것(이미 본인이 확인한 정보다). 프로필에 없는\n"
        "  신상 칸만 빈칸형 DECISION으로 남긴다."
    )


def profile_mapping(path: Optional[Path] = None) -> Dict[str, str]:
    """양식 셀 주입용 라벨→값 매핑('이름'→…, '학번'→… + 별칭 포함)."""
    prof = load_profile(path)
    out: Dict[str, str] = {}
    for key, disp, aliases in FIELDS:
        v = prof.get(key)
        if not v:
            continue
        out.setdefault(disp, v)
        for a in aliases:
            out.setdefault(a, v)
    # 소속 대학·학과가 따로 저장돼 있으면 합친 라벨도 제공(흔한 양식 표기).
    if prof.get("university") and prof.get("department"):
        both = f"{prof['university']} {prof['department']}"
        for a in ("소속 대학·학과", "소속대학·학과", "소속(학과)", "소속 및 학과"):
            out[a] = both
    return out


def describe(path: Optional[Path] = None) -> str:
    """CLI/웹 표시용 한 줄 요약."""
    prof = load_profile(path)
    if not prof:
        return "저장된 프로필 없음"
    label = {k: disp for k, disp, _ in FIELDS}
    return " · ".join(f"{label[k]} {prof[k]}" for k, _, _ in FIELDS if prof.get(k))


def main(argv: Optional[list] = None) -> int:
    """`python -m until.profile` — 보기 / `--set 키=값 ...` — 저장."""
    import sys
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] == "--set":
        cur = load_profile()
        for pair in args[1:]:
            if "=" in pair:
                k, _, v = pair.partition("=")
                cur[k.strip()] = v.strip()
        save_profile(cur)
        print(f"저장됨: {profile_path()}")
    print(describe())
    keys = ", ".join(k for k, _, _ in FIELDS)
    print(f"(설정: python -m until.profile --set name=홍길동 student_id=2020-12345 …  키: {keys})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
