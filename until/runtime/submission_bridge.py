"""Phase 4 — Submission Bridge.

검증된 번들을 제출 게이트 입력으로 넘기는 **읽기 전용** 다리.

nonce 발급과 실제 전송은 끝까지 `until.execution.submission_gate` /
`until.capture.sources.canvas_submit`가 독점한다. 이 모듈이 하는 일은 네 가지뿐:

1. 번들 파일명·확장자·MIME·개수를 결정적으로 검사한다.
2. validator를 통과한 artifact들의 해시를 하나의 content hash로 묶는다
   (제출 게이트가 nonce에 결합할 값).
3. 검증 이후 파일이 바뀌면 그 해시가 달라져 **기존 nonce가 자동으로 무효**가 된다.
4. 네트워크 접근 없는 dry-run 미리보기를 만든다.

runtime이 block 상태면 어떤 것도 내보내지 않는다 → nonce 발급 0회.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .models import RuntimeReport, SubmissionBundle, SubmissionFile
from .security import RuntimeSecurityError, confined_path, safe_relative_path
from .workspace import sha256_file

# 제출 파일로 허용하는 확장자 — 학교 제출함이 실제로 받는 것들만.
ALLOWED_SUFFIXES = frozenset({
    ".md", ".pdf", ".docx", ".hwpx", ".pptx", ".txt", ".zip", ".ipynb", ".py", ".csv",
})
# 확장자별로 기대하는 MIME 앞부분(정확 일치 대신 접두 비교 — 플랫폼마다 조금씩 다름).
_EXPECTED_MIME_PREFIX = {
    ".md": ("text/",),
    ".txt": ("text/",),
    ".csv": ("text/",),
    ".py": ("text/",),
    ".ipynb": ("application/", "text/"),
    ".pdf": ("application/pdf",),
    ".zip": ("application/zip", "application/x-zip"),
    ".docx": ("application/vnd.openxmlformats", "application/octet-stream"),
    ".pptx": ("application/vnd.openxmlformats", "application/octet-stream"),
    ".hwpx": ("application/", "text/"),
}
MAX_FILES = 10
MAX_TOTAL_BYTES = 50 * 1024 * 1024
_SAFE_NAME_RE = re.compile(r"\A[\w가-힣][\w가-힣 .\-()]{0,120}\Z")


class BundleRejected(ValueError):
    """번들이 제출 조건을 못 맞췄다 — 사람에게 그대로 보여줄 한국어 사유."""


def validated_submission_files(report: RuntimeReport) -> tuple[SubmissionFile, ...]:
    """runtime이 ready이고 validator가 통과했을 때만 파일 목록을 내준다."""
    if report.status != "ready" or report.bundle is None:
        return ()
    if report.validation is None or report.validation.blocked:
        return ()
    return report.bundle.files


def check_bundle(bundle: SubmissionBundle, *, required_suffixes=()) -> tuple[str, ...]:
    """파일명·확장자·MIME·개수를 결정적으로 검사. 반환 = 위반 사유 목록(빈 = 통과)."""
    problems: list[str] = []
    if bundle.missing:
        problems.append("빠진 파일: " + ", ".join(bundle.missing[:5]))
    if not bundle.files:
        problems.append("제출할 파일이 없습니다")
        return tuple(problems)
    if len(bundle.files) > MAX_FILES:
        problems.append(f"파일이 너무 많습니다({len(bundle.files)}개, 최대 {MAX_FILES}개)")
    total = 0
    seen: set[str] = set()
    for item in bundle.files:
        # basename만 보면 `../탈출.md` 같은 경로 표기가 통과한다 — 상대 경로 자체를
        # 커널과 같은 규칙(safe_relative_path)으로 먼저 검증한다.
        try:
            safe_relative_path(item.path)
        except RuntimeSecurityError:
            problems.append(f"제출에 쓸 수 없는 경로입니다: {item.path[:60]}")
            continue
        name = Path(item.path).name
        suffix = Path(item.path).suffix.lower()
        if name in seen:
            problems.append(f"파일명이 중복됩니다: {name}")
        seen.add(name)
        if not _SAFE_NAME_RE.match(name):
            problems.append(f"제출에 쓸 수 없는 파일명입니다: {name}")
        if suffix not in ALLOWED_SUFFIXES:
            problems.append(f"허용되지 않는 형식입니다: {name}")
        else:
            prefixes = _EXPECTED_MIME_PREFIX.get(suffix, ())
            mime = (item.mime_type or "").lower()
            if prefixes and not any(mime.startswith(p) for p in prefixes):
                problems.append(f"형식과 MIME이 어긋납니다: {name} ({item.mime_type})")
        if item.size <= 0:
            problems.append(f"빈 파일은 제출할 수 없습니다: {name}")
        if len(item.sha256) != 64:
            problems.append(f"해시가 없습니다: {name}")
        total += max(item.size, 0)
    if total > MAX_TOTAL_BYTES:
        problems.append(f"합계 용량 초과({total} bytes)")
    want = {str(s).lower() for s in required_suffixes if str(s).strip()}
    if want:
        have = {Path(item.path).suffix.lower() for item in bundle.files}
        for suffix in sorted(want - have):
            problems.append(f"과제가 요구하는 형식이 없습니다: {suffix}")
    return tuple(problems)


def bundle_content_hash(bundle: SubmissionBundle) -> str:
    """번들 전체의 결정적 content hash — 제출 게이트가 nonce에 결합할 값.

    파일 하나라도 내용이 바뀌면 값이 달라져 먼저 받은 nonce가 무효가 된다."""
    payload = json.dumps(
        {"assignment_id": bundle.assignment_id,
         "files": [{"path": f.path, "sha256": f.sha256, "size": f.size}
                   for f in sorted(bundle.files, key=lambda f: f.path)]},
        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def bundle_unchanged(bundle: SubmissionBundle, root: Path) -> bool:
    """검증 시점 해시와 지금 디스크의 해시가 같은지 — 사후 변조 탐지."""
    for item in bundle.files:
        try:
            path = confined_path(Path(root), item.path, must_exist=True)
        except (RuntimeSecurityError, OSError):
            # 검증 후 파일이 삭제되면 must_exist=True가 FileNotFoundError를 던진다.
            # 그건 '변경됨'으로 봐야 할 신호이지 호출자를 죽일 예외가 아니다.
            return False
        try:
            if sha256_file(path) != item.sha256 or path.stat().st_size != item.size:
                return False
        except OSError:
            return False
    return True


@dataclass(frozen=True)
class SubmissionPreview:
    """dry-run 미리보기 — 네트워크 접근 0, nonce 발급 0."""
    allowed: bool
    assignment_id: str
    content_hash: str
    files: tuple[SubmissionFile, ...] = ()
    problems: tuple[str, ...] = ()

    def describe(self) -> str:
        if not self.allowed:
            return "제출할 수 없습니다 — " + "; ".join(self.problems)
        names = ", ".join(Path(f.path).name for f in self.files)
        return f"{len(self.files)}개 파일 준비됨: {names}"


def preview_submission(report: RuntimeReport, *, root: Path | None = None,
                       required_suffixes=()) -> SubmissionPreview:
    """제출 직전 미리보기. 하드 블록이 하나라도 있으면 allowed=False."""
    assignment_id = report.bundle.assignment_id if report.bundle else ""
    files = validated_submission_files(report)
    if not files:
        reason = "런타임 검증을 통과한 파일이 없습니다"
        if report.validation is not None and report.validation.blocked:
            blocking = [f.message for f in report.validation.findings if f.level == "block"]
            reason = "; ".join(blocking[:3]) or reason
        return SubmissionPreview(False, assignment_id, "", (), (reason,))
    bundle = report.bundle
    problems = list(check_bundle(bundle, required_suffixes=required_suffixes))
    workspace_root = root or (report.workspace.root if report.workspace else None)
    if workspace_root is not None and not bundle_unchanged(bundle, Path(workspace_root)):
        problems.append("검증 이후 파일이 바뀌었습니다 — 다시 검증해야 합니다")
    if problems:
        return SubmissionPreview(False, assignment_id, "", files, tuple(problems))
    return SubmissionPreview(True, assignment_id, bundle_content_hash(bundle), files)


def submission_binding(report: RuntimeReport, *, uid: str, session_id: str) -> str:
    """제출 게이트 nonce에 넘길 결합 문자열(사용자·세션·번들 내용에 묶임)."""
    preview = preview_submission(report)
    if not preview.allowed:
        raise BundleRejected(preview.describe())
    return f"{uid or 'local'}:{session_id}:{preview.content_hash}"
