"""과제 소스(eTL 등)에서 수집한 데이터 모델."""
from __future__ import annotations
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

_ILLEGAL_FN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str) -> str:
    """서버가 준 첨부 이름을 안전한 파일명으로 정규화한다.

    - 경로 구분자/`..` 제거(디렉터리 탈출 방지) → basename만.
    - Windows 금지문자(`<>:"/\\|?*`·제어문자)를 `_`로 치환.
    빈 결과는 'attachment'로 폴백."""
    base = os.path.basename((name or "").replace("\\", "/").strip())
    base = _ILLEGAL_FN.sub("_", base).strip(". ")
    return base or "attachment"


@dataclass
class Attachment:
    name: str
    url: str
    local_path: Optional[str] = None   # 다운로드 후 채워짐


@dataclass
class RawAssignment:
    """BrowserAdapter가 과제 페이지에서 추출한 원본(다운로드 전)."""
    title: str
    course: str
    description: str
    attachments: List[Attachment] = field(default_factory=list)
    url: str = ""


@dataclass
class CourseRef:
    """eTL 과목 한 건(탐색용)."""
    id: str
    name: str
    term: str = ""            # 학기 이름(Canvas term.name, 예: "2026-1") — 필터용
    ended: bool = False       # 지난 과목(학기 종료·완료 상태) — '지난 학기 포함' 표시용


@dataclass
class AssignmentRef:
    """탐색으로 찾은 과제 1건의 가벼운 참조(본문/첨부 수집 전)."""
    id: str
    title: str
    course_id: str
    course_name: str = ""
    url: str = ""
    due_at: str = ""          # ISO8601 문자열(없으면 "")
    submitted: bool = False   # 이미 제출했는지(알 수 있으면)
    term: str = ""            # 소속 과목의 학기 이름 — 목록 필터·표시용
    # 제출할 것이 있는 '할 일'인지. 시험·중간 총점·출석 점수 같은 성적부 자리표시
    # (submission_types가 none/on_paper뿐)는 False — 실코퍼스에서 과제의 19%였다.
    # 정보가 없는 어댑터(WS·SSO)는 True 유지(fail-open).
    actionable: bool = True


@dataclass
class CollectedAssignment:
    """첨부 다운로드까지 끝난 수집 결과. 파이프라인 입력으로 변환 가능."""
    title: str
    course: str
    description: str
    url: str
    attachments: List[Attachment] = field(default_factory=list)

    def to_files(self, dest_dir: str | Path) -> List[str]:
        """과제 설명을 .md로 저장 + 첨부 경로들과 합쳐 ingest용 파일 목록 반환."""
        d = Path(dest_dir); d.mkdir(parents=True, exist_ok=True)
        # 설명 파일명 = 과제 제목 — 이 이름이 근거 범례·인라인 [자료N] 라벨에 그대로
        # 보이므로 'assignment.md' 같은 무의미한 이름 대신 사람이 읽는 이름으로.
        # 제목 속 '/'는 basename 절단 방지 치환, 60자 절단은 파일명 길이 한계 방지
        # (Linux 255바이트≈한글 85자, Windows 260자 경로 — 긴 공지형 제목이면 크래시).
        title = self.title.strip()
        desc = d / (safe_filename(f"{title[:60]}.md".replace("/", "_"))
                    if title else "assignment.md")
        # 과목명을 모르면 '과목:' 줄을 아예 빼둔다 — 자리를 채우려고 만든 값
        # ('Canvas course 302199' 같은 내부 식별자)은 이 파일을 통해 LLM 입력과
        # 제출 문서 본문까지 그대로 실려 나간다.
        course = (self.course or "").strip()
        head = f"# {self.title}\n\n" + (f"과목: {course}\n" if course else "")
        desc.write_text(f"{head}출처: {self.url}\n\n{self.description}\n",
                        encoding="utf-8")
        files = [str(desc)]
        files += [a.local_path for a in self.attachments if a.local_path]
        return files
