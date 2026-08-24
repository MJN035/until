"""과목 프로파일 로더 — course_profiles.json → 검증된 라우트 힌트 (결정적, LLM 0).

설계(COURSE_ALGORITHMS_2026F §3): 과목명이 축약형이고 본문·첨부가 비면 어휘 감지가
무력하다(논리설계실습 첫 주차 실측). 학기 초 1회, 사용자가 과목마다 알고리즘
힌트를 확정하는 얇은 레이어 — `_until_work/course_profiles.json`(사용자 소유·로컬).

이 모듈의 역할 경계:
- 순수 로더·판정만 둔다. 힌트 → AssignmentRoute 변환은 라우터의
  route_for_strategy가, 버전 게이트(v0.2에서만 적용)는 호출부(파이프라인)가 맡는다.
  그래서 여기엔 algo_version 분기가 없다 — v0.1에서 호출돼도 파일을 읽는 것 외
  부작용이 없는 순수 함수들이다.
- 파일 없음·JSON 깨짐·스키마 불일치는 전부 빈 값/None으로 흡수한다(예외를 밖으로
  내지 않음) — 프로파일은 있으면 좋은 폴백이지, 없다고 파이프라인이 죽을 이유가 없다.

§3 규칙(hint_applies가 코드로 강제):
  (a) route_hint는 결정적 규칙이 아무것도 못 잡았을 때만(= spec_clarification 직전)
      적용하는 폴백이다. 어휘 규칙을 이기지 못한다 — 사용자가 틀리게 적어도
      실제 명세가 이긴다.
  (b) non_actionable 판정은 힌트로 뒤집지 않는다(route_inference와 같은 안전 원칙).
"""
from __future__ import annotations

import json
import threading as _threading
from pathlib import Path
from typing import Dict, List, Optional

# 허용 힌트 = v0.2 신설 strategy 3종(공유 계약). 이 밖의 값은 조용히 무시한다 —
# 오타·임의 문자열이 존재하지 않는 경로를 켜는 사고를 막는다(§3 "route_hint가
# 허용 strategy 집합 밖이면 무시").
ALLOWED_ROUTE_HINTS = frozenset({
    "hdl_lab", "lab_report_cycle", "textbook_problem_set",
})

# 힌트별 사람이 읽는 이름 — 입력 화면과 안내 문구가 같은 이름을 쓰게 한 곳에서만
# 정한다(화면마다 다른 말로 부르면 사용자가 같은 것인지 알 수 없다).
ROUTE_HINT_LABELS = (
    ("hdl_lab", "HDL 실습 (Verilog·VHDL, 보드·합성)"),
    ("lab_report_cycle", "실험 보고서 3단 사이클 (예비→랩노트→결과)"),
    ("textbook_problem_set", "교재 문제 풀이"),
)

# 기본 저장 경로 — 테스트에서 임시 경로로 바꿀 수 있게 모듈 전역(answer_history 관행).
PROFILES_PATH = Path("_until_work/course_profiles.json")

# 클라우드는 사용자마다 다른 파일을 봐야 한다. 전역 경로 하나면 한 사람이 적은
# 힌트가 전원에게 적용되고(오적용), 애초에 프로파일을 적을 화면도 없었다 —
# 설계·구현·테스트가 다 있는 §3 폴백이 라이브에서 성립하지 않던 이유다
# (2026-08-22). profile.py·answer_history와 같은 요청 스코프 오버라이드 패턴.
_TL_PATH = _threading.local()


def set_course_profiles_path_override(p: Optional[Path]) -> None:
    """이 스레드(요청)의 과목 프로파일 경로 오버라이드 — 클라우드 사용자별 경로."""
    _TL_PATH.value = p


def _resolve_path(path: Optional[Path]) -> Path:
    if path is not None:
        return Path(path)
    o = getattr(_TL_PATH, "value", None)
    return Path(o) if o is not None else PROFILES_PATH


def course_profiles_path() -> Path:
    return _resolve_path(None)


def load_course_profiles(path: Optional[Path] = None) -> List[dict]:
    """course_profiles.json → 검증된 프로파일 dict 리스트. 실패는 전부 [].

    검증(§3 스키마): 최상위 dict + courses 리스트만 신뢰하고, 각 항목은
    course_id 또는 alias가 있어야 조회 가능하므로 둘 다 없으면 버린다.
    route_hint는 허용 집합 밖이면 ""로 지운다. 그 외 키(toolchain·series 등)는
    소비자(파이프라인·골격)가 쓸 수 있게 그대로 보존한다.
    """
    p = _resolve_path(path)
    try:
        # utf-8-sig: 메모장으로 직접 편집하는 사용자 소유 파일이라 BOM 허용.
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []  # 파일 없음·권한·JSON 깨짐 — 예외를 밖으로 내지 않는다.
    if not isinstance(raw, dict):
        return []
    courses = raw.get("courses")
    if not isinstance(courses, list):
        return []
    out: List[dict] = []
    for c in courses:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("course_id") or "").strip()
        alias = str(c.get("alias") or "").strip()
        if not cid and not alias:
            continue  # 어느 키로도 조회할 수 없는 항목은 쓸모가 없다.
        prof = dict(c)  # 원본 dict를 오염시키지 않는 사본.
        prof["course_id"] = cid
        prof["alias"] = alias
        hint = c.get("route_hint")
        prof["route_hint"] = hint if hint in ALLOWED_ROUTE_HINTS else ""
        out.append(prof)
    return out


def save_course_profiles(courses: List[Dict], path: Optional[Path] = None) -> Path:
    """과목 프로파일 전체 교체 저장. 반환: 저장 경로.

    로더와 **같은 검증**을 통과한 것만 쓴다 — 화면에서 들어온 값을 그대로 얹으면
    허용 밖 route_hint나 조회 불가 항목(course_id·alias 둘 다 없음)이 파일에 남아,
    다음에 열었을 때 "적었는데 안 먹는다"가 된다. 여기서 걸러 두면 저장된 것은
    반드시 적용 가능한 것뿐이다.

    빈 route_hint 항목은 지운다 — 힌트 없는 과목 프로파일은 §3에서 하는 일이
    아무것도 없다(다른 키는 아직 소비자가 없다). '없음'을 고른 과목이 목록에
    쌓여 화면만 길어지는 걸 막는다.
    """
    from .. import atomicio
    p = _resolve_path(path)
    clean: List[dict] = []
    seen: set = set()
    for c in (courses or []):
        if not isinstance(c, dict):
            continue
        cid = str(c.get("course_id") or "").strip()
        alias = " ".join(str(c.get("alias") or "").split())
        hint = c.get("route_hint")
        if not cid and not alias:
            continue
        if hint not in ALLOWED_ROUTE_HINTS:
            continue
        key = (cid, alias)
        if key in seen:
            continue  # 같은 과목을 두 번 적으면 앞의 것이 이긴다(조회도 앞부터).
        seen.add(key)
        item = dict(c)
        item["course_id"] = cid
        item["alias"] = alias
        item["route_hint"] = hint
        clean.append(item)
    payload = {"algo_version": "v0.2", "courses": clean}
    with atomicio.path_lock(p):
        p.parent.mkdir(parents=True, exist_ok=True)
        atomicio.atomic_write_json(p, payload, indent=2)
    return p


def profile_for_course(course_id: str = "", course_name: str = "", *,
                       path: Optional[Path] = None) -> Optional[dict]:
    """course_id 정확 일치 우선, 없으면 alias↔과목명 부분일치. 못 찾으면 None.

    부분일치를 쓰는 이유: eTL 과목명은 '논리설계실습(디지털) 설계 및 실험'처럼 길고,
    사용자가 적는 alias는 '논리설계실습' 같은 축약형이라 정확 일치가 성립하지 않는다.
    """
    profiles = load_course_profiles(path)
    cid = str(course_id or "").strip()
    name = " ".join(str(course_name or "").split())
    if cid:
        for prof in profiles:
            if prof["course_id"] and prof["course_id"] == cid:
                return prof
    if name:
        for prof in profiles:
            alias = prof["alias"]
            if alias and (alias in name or name in alias):
                return prof
    return None


def route_hint_for_course(course_id: str = "", course_name: str = "", *,
                          path: Optional[Path] = None) -> str:
    """과목의 검증된 route_hint 문자열. 프로파일 없음·힌트 없음 → ""."""
    prof = profile_for_course(course_id, course_name, path=path)
    return str(prof.get("route_hint") or "") if prof else ""


def hint_applies(route) -> bool:
    """현재 라우트에 route_hint를 적용해도 되는가 — §3 규칙 두 개를 코드로.

    (a) "route_hint는 결정적 규칙이 아무것도 못 잡았을 때만(= spec_clarification
        직전) 적용하는 폴백이다. 어휘 규칙을 이기지 못한다" — 그래서 현재 라우트가
        spec_clarification일 때만 True. 어휘 규칙이 잡은 다른 모든 strategy는 False.
    (b) "non_actionable 판정은 힌트로 뒤집지 않는다" — actionable=False면 무조건
        False(퀴즈·시험·증빙 슬롯에 초안을 만드는 사고 방지). (a)보다 먼저 본다.
    """
    if route is None:
        return False
    if not getattr(route, "actionable", False):
        return False  # (b) — non_actionable은 절대 뒤집지 못한다.
    return getattr(route, "strategy", "") == "spec_clarification"  # (a)
