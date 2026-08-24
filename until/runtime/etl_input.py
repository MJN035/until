"""eTL 과제 하나를 로컬 에이전트 런타임의 입력으로 바꾼다 (결정적, LLM 0).

런타임 진입점은 지금까지 로컬 파일만 받았다. 그래서 "처음부터 끝까지"의 **처음**이
비어 있었다 — 학생이 eTL을 열어 과제 본문을 복사하고 첨부를 손으로 내려받아야
비로소 `python -m until.runtime`을 쓸 수 있었다. 이 모듈이 그 구간을 메운다.

여기서 하는 일은 **가져오기뿐**이다. 판정·검증·실행은 전부 기존 모듈이 한다:
  - 과제 선택      → `inbox_policy.pick_best` (마감 임박·미제출 우선, 결정적)
  - 과제·첨부 수집 → `capture.sources.etl.EtlSource`
  - 관련 강의자료  → `context.etl_materials`
  - 명세 조립      → `runtime.spec_builder`

`docs/ASSIGNMENT_RUNTIME_PLAN.md` §8의 전제를 지킨다: **모델 API 호출 0.**
과제를 고르는 것도 자료를 고르는 것도 규칙이지 추론이 아니다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

#: 과제와 함께 작업공간에 넣을 강의자료 수. 늘리면 에이전트가 읽을 게 많아지지만
#: 작업공간이 무거워지고 무관한 자료가 초안을 끌고 간다(웹 경로도 상위 2건만 쓴다).
DEFAULT_MATERIALS = 2


class EtlInputError(RuntimeError):
    """eTL에서 과제를 가져오지 못했다 — 사람이 읽을 수 있는 사유."""


@dataclass(frozen=True)
class EtlAssignment:
    """가져온 과제 1건 + 작업공간에 넣을 파일들."""
    assignment_id: str
    course_id: str
    title: str
    course_name: str = ""
    due_at: str = ""
    page_url: str = ""
    files: tuple[Path, ...] = ()
    #: 첨부·자료 중 내려받지 못한 것(권한·만료). 조용히 삼키면 학생은 자료가
    #: 통째로 빠진 초안을 받고도 이유를 모른다.
    skipped: tuple[str, ...] = field(default_factory=tuple)


def build_adapter(token: str, *, ws: bool = False):
    """토큰으로 eTL 어댑터를 만든다. 토큰이 없으면 여기서 막는다.

    어댑터 계층에는 `UNTIL_CANVAS_TOKEN` env 폴백이 있지만 그건 개발자 편의다 —
    CLI는 무엇으로 접속하는지 명시적으로 정해서 넘긴다."""
    if not (token or "").strip():
        raise EtlInputError(
            "eTL 액세스 토큰이 필요합니다. eTL › 계정 › 설정 › '+ 새 액세스 토큰'에서 "
            "발급한 뒤 --token 으로 주거나 UNTIL_CANVAS_TOKEN 으로 넘기세요.")
    if ws:
        from ..capture.sources.moodle_ws import MoodleWsAdapter
        return MoodleWsAdapter(etl_base_url(), token=token)
    from ..capture.sources.canvas_api import CanvasApiAdapter
    return CanvasApiAdapter(token=token)


def etl_base_url(adapter=None) -> str:
    """eTL 베이스 URL — 어댑터가 알면 그것, 아니면 env, 아니면 SNU eTL.

    `web.etl_ws_base()`와 같은 규칙이지만 web을 임포트하지 않는다. 런타임이
    웹 서버 모듈에 의존하면 CLI 하나 쓰려고 HTTP 계층까지 끌고 들어온다."""
    import os
    from ..capture.sources.discovery import SNU_ETL_BASE
    known = str(getattr(adapter, "base_url", "") or "").strip()
    return known or (os.getenv("UNTIL_ETL_BASE") or SNU_ETL_BASE).strip()


def list_assignments(adapter, *, base_url: str = "", max_workers: int = 8) -> list:
    """미제출·기한 전 과제를 마감 임박순으로. 실패는 읽을 수 있는 사유로 바꾼다."""
    from ..capture.sources.discovery import EtlInbox
    base = base_url or etl_base_url(adapter)
    # base_url을 반드시 명시한다 — EtlInbox의 기본값은 임포트 시점에 묶여서
    # 모듈 상수만 바꾼 테스트/대체 서버에서는 실제 eTL로 나가 버린다(실측).
    inbox = EtlInbox(adapter, base_url=base)
    try:
        return inbox.list_assignments(bucket=None, only_unsubmitted=False,
                                      max_workers=max_workers)
    except Exception as exc:  # 어댑터가 던지는 예외 종류가 백엔드마다 다르다
        from ..user_errors import user_error_message
        raise EtlInputError(user_error_message(exc, "eTL 과제 목록을 불러오")) from exc


def pick_nearest(items) -> Optional[object]:
    """마감이 가장 가까운 '할 수 있는' 과제. 웹의 딸깍과 같은 정책을 쓴다."""
    from ..inbox_policy import pick_best
    return pick_best(items)


def submit_page_url(base_url: str, course_id: str, assignment_id: str) -> str:
    """제출하러 갈 eTL 과제 페이지 — 과목·과제 id로 재구성한다.

    원문 URL을 보관하지 않는 기존 방침과 같다(`web._assignment_link`). id가
    숫자가 아니면(WS·SSO 경로) 링크를 지어내지 않고 빈 문자열."""
    course, assignment = str(course_id or ""), str(assignment_id or "")
    if not (course.isdigit() and assignment.isdigit()):
        return ""
    return f"{base_url.rstrip('/')}/courses/{course}/assignments/{assignment}"


def collect(adapter, url: str, dest_dir: Path, *,
            materials: int = DEFAULT_MATERIALS,
            base_url: str = "") -> EtlAssignment:
    """과제 본문 + 첨부 + 관련 강의자료를 dest_dir에 내려받고 참조를 돌려준다.

    자료 수집 실패는 치명적이지 않다 — 과제 본문만으로도 작업은 시작할 수 있고,
    무엇이 빠졌는지는 `skipped`로 올려 보내 화면에 밝힌다.
    """
    from ..capture.sources.etl import EtlSource

    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    base = base_url or etl_base_url(adapter)
    try:
        collected = EtlSource(url, adapter).collect(str(dest))
    except Exception as exc:
        from ..user_errors import user_error_message
        raise EtlInputError(user_error_message(exc, "과제를 수집")) from exc

    files = [Path(p) for p in collected.to_files(str(dest))]
    skipped = [f"{att.name} — 내려받지 못함"
               for att in collected.attachments if not att.local_path]

    course_id, assignment_id = _ids_from_url(url, adapter)
    if materials > 0 and course_id:
        picked, missed = _download_materials(
            adapter, course_id, base, collected, dest, top=materials)
        files.extend(picked)
        skipped.extend(missed)

    return EtlAssignment(
        assignment_id=assignment_id or _slug(collected.title),
        course_id=course_id,
        title=collected.title,
        course_name=collected.course,
        page_url=collected.url or url,
        files=tuple(files),
        skipped=tuple(skipped),
    )


# ── 내부 ────────────────────────────────────────────────────────────
def _ids_from_url(url: str, adapter) -> tuple[str, str]:
    """(course_id, assignment_id). 해석 못 하면 빈 문자열(치명적 아님)."""
    if hasattr(adapter, "course_id_for_url"):          # Moodle WS
        return str(adapter.course_id_for_url(url) or ""), ""
    try:
        from ..capture.sources.canvas_api import parse_assignment_url
        _base, course_id, assignment_id = parse_assignment_url(url)
        return course_id, assignment_id
    except ValueError:
        return "", ""


def _download_materials(adapter, course_id: str, base_url: str, collected,
                        dest: Path, *, top: int) -> tuple[list, list]:
    """과제 키워드로 순위화한 상위 강의자료를 실제 파일로 내려받는다.

    본문 발췌(`fetch_material_texts`)가 아니라 **파일**을 가져온다 — 에이전트는
    작업공간 안의 파일을 직접 읽으므로, 잘라 낸 발췌보다 원본이 낫다.
    """
    from ..context.etl_materials import collect_related_materials

    spec_like = {"deliverable": "과제", "goal": collected.title,
                 "requirements": [(collected.description or "")[:800]]}
    try:
        refs = collect_material_attachments(adapter, course_id, base_url)
        hits = collect_related_materials(adapter, course_id, spec_like, base_url,
                                         k=top, refs=refs)
    except Exception:
        return [], []                     # 자료가 없어도 과제 본문으로 진행한다

    by_url = {att.url: att for att in refs}
    got, missed = [], []
    for hit in hits[:top]:
        att = by_url.get(hit.url)
        if att is None:
            continue
        try:
            path = adapter.download(att, str(dest))
        except Exception:
            missed.append(f"{hit.name} — 강의자료를 내려받지 못함")
            continue
        if path:
            got.append(Path(path))
    return got, missed


def collect_material_attachments(adapter, course_id: str, base_url: str) -> list:
    from ..context.etl_materials import collect_material_refs
    return collect_material_refs(adapter, course_id, base_url)


def _slug(value: str) -> str:
    import re
    cleaned = re.sub(r"[^0-9A-Za-z가-힣._-]+", "-", value or "").strip("-")
    return cleaned[:64] or "assignment"
