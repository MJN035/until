"""
문체 자동 학습 — eTL(Canvas) 과거 제출물에서 VoiceProfile을 만들어 저장한다.

'내가 쓴 글 올리기' 없이, eTL을 연결하는 순간부터 초안이 내 문체로 나오게 하는
레이어(설계: docs/superpowers/specs/2026-07-28-voice-autolearn-design.md).

원칙:
  · 수집·필터·프로파일 추출은 전부 결정적(LLM 0). 조별 과제 제외 등 안전 필터는
    parse_my_submissions(canvas_api)가 코드로 강제한다.
  · 저장은 **프로파일(JSON 몇 줄)만** — 제출물 원문·첨부는 분석 직후 폐기.
  · 실패는 조용히(빈 결과) — 인박스·초안 흐름을 절대 막지 않는다.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from .voice import VoiceProfile, build_voice_profile

#: 학습에 쓸 제출물 수 상한(최신순) — 첫 인박스 지연·API 호출 방어.
#: 30건: 3학기 재학생(학기당 과제 ~10건)도 충분히 담는 수준 — 10건은 실사용자가
#: '너무 적다'고 체감한 값이고, 표본이 늘수록 프로파일 추출은 더 안정적이다.
MAX_DOCS = 30
#: 제출물을 훑을 과목 수 상한(과목당 API 1콜).
#: 20과목: 3~4학기치(학기당 5~6과목)를 덮으면서 첫 인박스 1회성 호출 ~20콜로 허용 수준.
MAX_COURSES = 20
#: 표본 1건당 사용할 텍스트 상한(문체 추출엔 충분 — 파싱 폭주 방지).
MAX_CHARS = 12000

STORE_VERSION = 1


# ── 수집 → 프로파일 ─────────────────────────────────────────────────

def _newest_courses(courses, cap: int = MAX_COURSES) -> list:
    """과목을 최신 개설 순으로 cap개만 — Canvas /courses는 사실상 오래된 순이라
    그대로 자르면 가장 오래된 과목에서만 배운다. Canvas id는 증가 발급이므로
    숫자 id 내림차순 = 최신 개설 우선(숫자가 아닌 id는 0 취급으로 방어)."""
    def _key(c) -> int:
        try:
            return int(str(getattr(c, "id", "") or "").strip())
        except (TypeError, ValueError):
            return 0
    return sorted(courses, key=_key, reverse=True)[:cap]


def collect_voice_texts(adapter, base_url: str, courses,
                        max_docs: int = MAX_DOCS,
                        stats: Optional[dict] = None) -> List[str]:
    """과목들을 돌며 내 제출물에서 문체 표본 텍스트를 모은다(최신순 max_docs건).

    adapter는 list_my_submissions(course_id, base_url)를 지원해야 한다
    (CanvasApiAdapter — SSO/WS 어댑터는 호출부가 hasattr로 거른다).
    과목 하나가 실패해도 나머지는 계속(비치명적).
    """
    subs: List[dict] = []
    selected_courses = _newest_courses(courses)
    submitted_total = 0
    scanned_courses = 0
    exact_total = True
    for c in selected_courses:
        cid = getattr(c, "id", "") or ""
        if not cid:
            continue
        try:
            if hasattr(adapter, "list_my_submissions_with_counts"):
                rows, submitted = adapter.list_my_submissions_with_counts(cid, base_url)
                submitted_total += max(0, int(submitted))
            else:
                rows = adapter.list_my_submissions(cid, base_url)
                submitted_total += len(rows)
                exact_total = False
            subs.extend(rows)
            scanned_courses += 1
        except Exception:
            continue
    subs.sort(key=lambda s: s.get("submitted_at") or "", reverse=True)

    texts: List[str] = []
    workdir = tempfile.mkdtemp(prefix="until_vlearn_")
    try:
        for s in subs:
            if len(texts) >= max_docs:
                break
            body = (s.get("body") or "").strip()
            if body:
                texts.append(body[:MAX_CHARS])
                continue
            for att in s.get("attachments") or []:
                if len(texts) >= max_docs:
                    break
                try:
                    from ..capture.ingest import ingest_file
                    path = adapter.download(att, workdir)
                    extracted = ingest_file(Path(path)).text.strip()
                    if extracted:
                        texts.append(extracted[:MAX_CHARS])
                except Exception:
                    continue  # 파싱 못 하는 첨부는 표본에서 제외(문체 학습은 옵션 기능)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)  # 원문은 프로파일 추출 후 즉시 폐기
    if stats is not None:
        stats.update({
            "courses_total": len(courses),
            "courses_scanned": scanned_courses,
            "submitted_total": submitted_total,
            "eligible_submissions": len(subs),
            "samples_used": len(texts),
            "submitted_total_exact": exact_total,
            "sample_cap": max_docs,
        })
    return texts


def learn_voice_profile(adapter, base_url: str, courses) -> Tuple[VoiceProfile, int]:
    """제출물 수집 → 결정적 프로파일. 반환 (VoiceProfile, 표본 수)."""
    texts = collect_voice_texts(adapter, base_url, courses)
    return build_voice_profile(texts), len(texts)


def learn_voice_profile_with_stats(adapter, base_url: str, courses) -> tuple:
    """프로파일과 표본 수에 더해 수집 범위 계측을 반환한다."""
    stats: dict = {}
    texts = collect_voice_texts(adapter, base_url, courses, stats=stats)
    return build_voice_profile(texts), len(texts), stats


# ── 저장/로드 (프로파일만 — 원문 없음) ──────────────────────────────

def save_stored_voice(path: Path, profile: VoiceProfile, n_docs: int,
                      disabled: bool = False, stats: Optional[dict] = None) -> None:
    """프로파일을 JSON으로 저장(원자적 교체 — 동시 요청 반쪽 파일 방지)."""
    payload = {"v": STORE_VERSION, "disabled": bool(disabled), "n_docs": int(n_docs),
               "learned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "profile": asdict(profile)}
    if stats:
        payload["stats"] = {k: v for k, v in stats.items()
                            if isinstance(v, (bool, int))}
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


def load_stored_voice(path: Path) -> Tuple[Optional[VoiceProfile], bool, int]:
    """저장된 문체 파일 로드 → (프로파일 또는 None, disabled, n_docs).

    파일 없음/손상/미래 버전 → (None, False, 0): 켜진 상태로 간주하되 프로파일 없음
    (다음 인박스에서 재학습). disabled=True면 프로파일도 None(적용 중단).
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, False, 0
    if not isinstance(data, dict) or data.get("v") != STORE_VERSION:
        return None, False, 0
    disabled = bool(data.get("disabled"))
    n_docs = data.get("n_docs")
    n_docs = n_docs if isinstance(n_docs, int) and n_docs >= 0 else 0
    if disabled:
        return None, True, n_docs
    raw = data.get("profile")
    if not isinstance(raw, dict):
        return None, False, 0
    allowed = {f.name for f in fields(VoiceProfile)}
    kwargs = {k: v for k, v in raw.items() if k in allowed}
    try:
        profile = VoiceProfile(**kwargs)
    except TypeError:
        return None, False, 0
    if profile.n_samples <= 0:  # 표본 0건 기록 — 적용할 문체 없음(재스캔 방지용 마커)
        return None, False, n_docs
    return profile, False, n_docs


def load_stored_voice_stats(path: Path) -> dict:
    """새 저장 파일의 익명 집계만 로드한다. 구버전 파일은 빈 dict로 호환한다."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    raw = data.get("stats") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, (bool, int))}


def disable_stored_voice(path: Path) -> None:
    """문체 적용 끄기 — disabled 플래그 저장(재수집도 안 함)."""
    save_stored_voice(Path(path), VoiceProfile(), 0, disabled=True)


def clear_stored_voice(path: Path) -> None:
    """저장 파일 삭제 — 다음 eTL 인박스에서 다시 학습된다."""
    try:
        Path(path).unlink()
    except OSError:
        pass
