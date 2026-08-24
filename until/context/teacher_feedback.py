"""
교수 피드백 학습 — eTL 제출물의 코멘트·루브릭 평가를 모아 다음 초안에 참고시킨다.

문체 자동 학습(voice_autolearn)과 같은 원리·같은 API 1콜을 공유한다:
  · 수집·저장·힌트 생성 전부 결정적(LLM 0). 파서는 canvas_api.parse_my_feedback.
  · 저장은 짧은 피드백 발췌(JSON)만 — 제출물 원문·첨부는 받지 않는다.
  · 실패는 조용히(빈 결과) — 인박스·초안 흐름을 절대 막지 않는다.

쓰임: Execution 프롬프트에 '지난 피드백' 블록 주입(같은 지적 반복 방지) +
준비 점검·초안 페이지에 참고 사실 표시. ChatGPT 복붙으론 불가능한, 내 수강
이력이 있어야만 나오는 개인화다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List

#: 저장할 피드백 항목 수 상한(최신순) — 문체 학습(MAX_DOCS=30)과 같은 폭.
#: 프롬프트 주입은 feedback_hint(max_items=8)가 따로 자르므로 폭주하지 않는다.
MAX_ENTRIES = 30
#: 훑을 과목 수 상한(voice_autolearn과 동일 기준 — 3~4학기치, 1회성 ~20콜).
MAX_COURSES = 20
#: 코멘트 1건당 발췌 길이 상한.
MAX_COMMENT_CHARS = 300

STORE_VERSION = 1


def collect_feedback_entries(adapter, base_url: str, courses,
                             max_entries: int = MAX_ENTRIES) -> List[dict]:
    """과목들을 돌며 교수 피드백 항목을 모은다(최신순 max_entries건).

    adapter는 list_my_feedback(course_id, base_url)를 지원해야 한다
    (CanvasApiAdapter). 과목 하나가 실패해도 나머지는 계속.
    """
    from .voice_autolearn import _newest_courses  # 최신 개설 과목 우선(같은 기준 공유)
    entries: List[dict] = []
    for c in _newest_courses(courses, MAX_COURSES):
        cid = getattr(c, "id", "") or ""
        if not cid:
            continue
        try:
            for e in adapter.list_my_feedback(cid, base_url):
                e = dict(e)
                e["course"] = getattr(c, "name", "") or ""
                e["comments"] = [t[:MAX_COMMENT_CHARS] for t in e.get("comments", [])]
                e["rubric"] = [t[:MAX_COMMENT_CHARS] for t in e.get("rubric", [])]
                entries.append(e)
        except Exception:
            continue
    entries.sort(key=lambda e: e.get("submitted_at") or "", reverse=True)
    return entries[:max_entries]


# ── 저장/로드 ────────────────────────────────────────────────────────

def save_feedback(path: Path, entries: List[dict], disabled: bool = False) -> None:
    """피드백 항목을 JSON으로 저장(원자적 교체 — 동시 요청 반쪽 파일 방지).

    Windows는 대상 파일이 열려 있으면 교체가 PermissionError로 실패할 수 있어
    voice_autolearn.save_stored_voice와 같은 재시도(5회)+직접 쓰기 폴백을 둔다."""
    payload = {"v": STORE_VERSION, "disabled": bool(disabled),
               "learned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "entries": list(entries)[:MAX_ENTRIES]}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    tmp.write_text(text, encoding="utf-8")
    import time as _time
    for attempt in range(5):
        try:
            tmp.replace(path)
            return
        except OSError:
            if attempt == 4:
                path.write_text(text, encoding="utf-8")
                try:
                    tmp.unlink()
                except OSError:
                    pass
            else:
                _time.sleep(0.02)


def load_feedback(path: Path) -> tuple:
    """저장 파일 로드 → (entries 목록, disabled). 없음/손상/끔 → ([], …)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], False
    if not isinstance(data, dict) or data.get("v") != STORE_VERSION:
        return [], False
    if data.get("disabled"):
        return [], True
    entries = [e for e in data.get("entries") or [] if isinstance(e, dict)]
    return entries, False


def disable_feedback(path: Path) -> None:
    save_feedback(Path(path), [], disabled=True)


def clear_feedback(path: Path) -> None:
    try:
        Path(path).unlink()
    except OSError:
        pass


# ── 프롬프트 힌트/표시 ───────────────────────────────────────────────

def feedback_hint(entries: List[dict], max_items: int = 8) -> str:
    """Execution에 주입할 '지난 피드백' 블록(결정적). 항목 없으면 빈 문자열.

    경계선 유지 — 피드백은 품질 참고이지 판단 대체가 아니다. 지시는
    '같은 지적을 반복하지 않게 반영'까지만."""
    lines: List[str] = []
    for e in entries[:max_items]:
        name = e.get("assignment") or "(과제)"
        for t in (e.get("comments") or [])[:2]:
            lines.append(f"- [{name}] {t}")
        for t in (e.get("rubric") or [])[:2]:
            lines.append(f"- [{name}·루브릭] {t}")
    if not lines:
        return ""
    return ("【지난 과제에서 받은 교수 피드백 — 같은 지적이 반복되지 않게 참고】\n"
            + "\n".join(lines[:max_items * 2]) + "\n"
            "위 피드백은 품질 참고다. 이번 과제 요구와 충돌하면 이번 요구를 따른다.")


def feedback_summary(entries: List[dict]) -> str:
    """준비 점검·UI용 한 줄 요약. 항목 없으면 빈 문자열."""
    if not entries:
        return ""
    n = sum(len(e.get("comments") or []) + len(e.get("rubric") or [])
            for e in entries)
    first = ""
    for e in entries:
        pool = (e.get("comments") or []) + (e.get("rubric") or [])
        if pool:
            first = pool[0][:40]
            break
    tail = f' — 예: "{first}…"' if first else ""
    return f"지난 과제 피드백 {n}건 참고됨(과제 {len(entries)}개){tail}"
