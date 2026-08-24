"""eTL 등 소스에서 수집 → 파이프라인 입력 파일 목록으로 변환하는 헬퍼."""
from __future__ import annotations
from typing import List, Tuple

from .etl import EtlSource, FixtureBrowserAdapter
from .models import CollectedAssignment


def collect_etl_to_files(
    assignment_url: str, dest_dir: str, *, adapter=None
) -> Tuple[CollectedAssignment, List[str]]:
    """
    eTL 과제를 수집해 (CollectedAssignment, ingest용 파일경로 리스트) 반환.
    adapter 미지정 시 PlaywrightBrowserAdapter(제품용 브라우저 자동화).
    오프라인 테스트는 FixtureBrowserAdapter를 주입.
    """
    if adapter is None:
        from .learningx_adapter import LearningXBrowserAdapter, is_learningx_url
        if is_learningx_url(assignment_url):
            adapter = LearningXBrowserAdapter()
        else:
            from .playwright_adapter import PlaywrightBrowserAdapter
            adapter = PlaywrightBrowserAdapter()
    src = EtlSource(assignment_url, adapter)
    collected = src.collect(dest_dir)
    files = collected.to_files(dest_dir)
    if hasattr(adapter, "close"):
        try: adapter.close()
        except Exception: pass
    return collected, files


def collect_canvas_api_to_files(
    assignment_url: str, dest_dir: str, *, token: str | None = None,
    include_course_files: bool | None = None,
) -> Tuple[CollectedAssignment, List[str]]:
    """Canvas REST API로 과제를 수집(권장 제품 경로). 토큰 미지정 시 UNTIL_CANVAS_TOKEN 사용.

    include_course_files 미지정 시 UNTIL_CANVAS_FILES=1 이면 코스 파일 첨부도 병합.
    """
    import os
    from .canvas_api import CanvasApiAdapter
    if include_course_files is None:
        include_course_files = os.getenv("UNTIL_CANVAS_FILES", "0") == "1"
    adapter = CanvasApiAdapter(token=token, include_course_files=include_course_files)
    return collect_etl_to_files(assignment_url, dest_dir, adapter=adapter)


def collect_moodle_ws_to_files(
    assignment_url: str, dest_dir: str, *, base_url: str, token: str | None = None,
    adapter=None,
) -> Tuple[CollectedAssignment, List[str]]:
    """Moodle WS(읽기 전용)로 과제를 수집. 토큰 미지정 시 UNTIL_ETL_WS_TOKEN 사용.

    주의: Moodle WS는 과제를 과목 단위로 조회하므로, adapter가 이미 인박스
    (list_assignments)로 해당 과제를 캐시했을 때 fetch_assignment(url)가 동작한다.
    단독 URL 수집은 discovery→pick 흐름(MoodleWsAdapter 재사용)을 권장."""
    from .moodle_ws import MoodleWsAdapter
    if adapter is None:
        adapter = MoodleWsAdapter(base_url, token=token)
    return collect_etl_to_files(assignment_url, dest_dir, adapter=adapter)


def collect_elice_to_files(assignment_url: str, dest_dir: str, *, token: str | None = None,
                           adapter=None) -> Tuple[CollectedAssignment, List[str]]:
    """Elice Exercise URL을 읽기 전용 어댑터로 수집한다."""
    if adapter is None:
        from .elice_api import EliceAdapter
        adapter = EliceAdapter(token=token)
    return collect_etl_to_files(assignment_url, dest_dir, adapter=adapter)


def collect_etl_fixture(fixture_dir: str, dest_dir: str) -> Tuple[CollectedAssignment, List[str]]:
    """오프라인 데모: fixture 디렉터리를 가짜 eTL로 보고 수집."""
    adapter = FixtureBrowserAdapter(fixture_dir)
    src = EtlSource("fixture://assignment", adapter)
    collected = src.collect(dest_dir)
    return collected, collected.to_files(dest_dir)
