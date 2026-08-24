"""글쓰기 산출물을 발표자료로 바꾸는 과제의 선행 맥락 수집(결정적, LLM 0).

제목이 단순히 ``피피티 제출``인 과제는 현재 페이지에 내용 명세가 거의 없다.
같은 과목의 과거 제출물 중 주제·개요·원고 단계만 현재 마감 이전에서 골라
Execution에 제공한다. 이후 제출물(예: 기말 완성본)은 미래 정보 누수를 막기 위해
제외한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Iterable, List, Optional

from ..capture.sources.canvas_api import _description_to_text
from ..llm.base import SourceDoc

_GENERIC = re.compile(
    r"(?:피피티|pptx?|슬라이드|발표\s*자료)\s*(?:파일\s*)?(?:제출|업로드)$",
    re.I,
)
_STAGES = (
    ("주제·개요", re.compile(r"주제문|개요|주제[_ ·/]"), 40),
    ("수정 원고", re.compile(r"수정\s*(?:서론|본론|결론|원고|초고)|(?:서론|본론|결론)\s*수정"), 35),
    ("원고", re.compile(r"서론|본론|결론|초고|원고"), 30),
    ("글쓰기", re.compile(r"글쓰기|에세이|보고서"), 15),
)


def is_generic_presentation_assignment(title: str) -> bool:
    """내용 주제가 드러나지 않는 발표파일 제출 제목인지 판정한다."""
    clean = re.sub(r"\s+", " ", (title or "").strip())
    return bool(_GENERIC.search(clean))


def _time(value: object) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        got = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return got if got.tzinfo else got.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _stage(title: str) -> tuple[str, int]:
    for label, rx, score in _STAGES:
        if rx.search(title or ""):
            return label, score
    return "", 0


def find_presentation_predecessors(current_title: str, submissions: Iterable[dict],
                                   *, current_assignment_id: str = "",
                                   current_due_at: str = "", k: int = 4) -> List[dict]:
    """Canvas 제출 JSON에서 발표의 원재료가 될 과거 개인 제출물을 고른다.

    조별 과제·현재 과제·미제출·현재 마감 뒤 제출은 제외한다. 결과는 글의 생성
    순서(주제/개요 → 원고/수정)로 반환해 모델이 변천을 이해하게 한다.
    """
    if not is_generic_presentation_assignment(current_title):
        return []
    due = _time(current_due_at)
    hits = []
    for row in submissions or []:
        if not isinstance(row, dict):
            continue
        assignment = row.get("assignment") if isinstance(row.get("assignment"), dict) else {}
        aid = str(assignment.get("id") or "")
        title = str(assignment.get("name") or "").strip()
        submitted = _time(row.get("submitted_at"))
        if (assignment.get("group_category_id") or not submitted or
                (current_assignment_id and aid == str(current_assignment_id)) or
                title == current_title):
            continue
        # NO DUE이고 아직 제출 전이면 시간 상한을 알 수 없다. Canvas 과제 id의
        # 생성 순서를 보조 안전장치로 써 현재 과제보다 뒤에 생긴 과제를 제외한다.
        if (not due and current_assignment_id and aid.isdigit() and
                str(current_assignment_id).isdigit() and int(aid) > int(current_assignment_id)):
            continue
        if due and submitted > due:
            continue
        label, score = _stage(title)
        if not score:
            continue
        body_html = row.get("body") or ""
        body = _description_to_text(body_html)[0] if body_html else ""
        attachments = [a for a in (row.get("attachments") or []) if isinstance(a, dict)]
        if not body.strip() and not attachments:
            continue
        hits.append({"id": aid, "title": title, "submitted_at": str(row.get("submitted_at") or ""),
                     "body": body.strip(), "attachments": attachments,
                     "stage": label, "score": score})
    hits.sort(key=lambda x: (_time(x["submitted_at"]) or datetime.min.replace(tzinfo=timezone.utc),
                             x["score"], x["title"]))
    # 최근 단계가 남도록 뒤에서 k개를 취하되, 반환은 시간순이다.
    return hits[-max(1, k):]


def presentation_predecessors_to_sources(hits: Iterable[dict],
                                         limit_chars: int = 3500) -> List[SourceDoc]:
    """선행 제출물을 발표 변환용 SourceDoc으로 만든다."""
    out = []
    for hit in hits or []:
        body = str(hit.get("body") or "").strip()
        if not body:
            continue
        title = str(hit.get("title") or "선행 과제")
        stage = str(hit.get("stage") or "원고")
        when = str(hit.get("submitted_at") or "")[:10]
        text = (f"발표자료의 선행 산출물({stage}, {when}).\n"
                "확정된 주제·주제문·개요와 작성된 문단을 발표용으로 압축하는 근거다. "
                "원문을 슬라이드에 통째로 붙이지 말고 핵심 주장과 근거만 재구성할 것.\n\n"
                f"{body[:limit_chars]}")
        out.append(SourceDoc(title=f"[발표 선행 과제] {title}", text=text))
    return out


def hydrate_predecessor_attachments(hits: Iterable[dict], download, dest_dir: str,
                                    *, limit_chars: int = 5000) -> None:
    """본문 없는 선행 제출물의 텍스트 첨부를 내려받아 제자리에서 보강한다.

    download은 BrowserAdapter.download 계약을 따른다. 개별 파일 실패는 다른 선행
    자료까지 막지 않는다. 발표 원재료로 읽을 수 있는 문서 형식만 허용한다.
    """
    from ..capture.ingest import ingest_file
    from ..capture.sources.models import Attachment
    allowed = (".txt", ".md", ".pdf", ".docx", ".hwpx", ".hwp", ".pptx")
    for hit in hits or []:
        chunks = [str(hit.get("body") or "").strip()]
        for raw in hit.get("attachments") or []:
            name = str(raw.get("display_name") or raw.get("filename") or "").strip()
            url = str(raw.get("url") or "").strip()
            if not name.lower().endswith(allowed) or not url:
                continue
            try:
                path = download(Attachment(name=name, url=url), dest_dir)
                text = ingest_file(path, backend="basic").text.strip()
            except Exception:
                continue
            if text:
                chunks.append(f"\n[첨부 {Path(path).name}]\n{text}")
        hit["body"] = "\n".join(c for c in chunks if c)[:limit_chars]


def conversion_directive(sources: Iterable[SourceDoc]) -> str:
    """선행 과제가 있을 때 이미 아는 것과 사용자에게 물을 것을 분리한다."""
    if not any(str(getattr(s, "title", "")).startswith("[발표 선행 과제]")
               for s in sources or []):
        return ""
    return (
        "[발표 변환형 과제]\n"
        "- [발표 선행 과제]에서 확정된 주제·주제문·개요는 다시 묻지 말고 유지한다.\n"
        "- 원고 문단을 그대로 붙이지 말고 한 슬라이드 한 메시지로 압축한다.\n"
        "- 과제 자료에 발표할 범위가 없으면 [[DECISION: 발표할 일부 단락 선택 — 선행 원고의 후보를 2~3개 제시]]만 남긴다.\n"
        "- 발표 시간이 자료에 없으면 [[DECISION: 발표 시간 확인 — 5분 기준으로 먼저 구성해도 되는지]]를 남긴다.\n"
        "- 디자인·슬라이드 수 조건이 없다면 사용자에게 묻지 말고 내용량에 맞춰 직접 정한다."
    )
