"""
eTL 과거 코퍼스 수집 러너 — **읽기 전용** (Canvas REST API).

⚠ 프로토콜 주의: eTL(myetl.snu.ac.kr)은 **Canvas LMS**다. Moodle WS 엔드포인트
  (/webservice/rest/server.php)는 이 서버에서 HTTP 422를 반환한다(2026-08-02 라이브
  확인). 이 러너는 라이브 검증된 CanvasApiAdapter(GET 전용)로 수집한다.

토큰 1개(=한 사람)로 그 사람이 수강했던 **모든 과목(지난 학기 포함)**을 훑어
  · 과제 명세(제목·마감·본문·과제 첨부)
  · 본인 제출물(온라인 텍스트 본문 + 제출 파일)
을 로컬 코퍼스 폴더로 덤프한다. 세 명이 각자 자기 토큰으로 돌리면
분류(3·4번) 원료 + 검증 정답셋 + 말투 학습 데이터가 한 번에 모인다.

  # 준비: 각자 eTL > 계정 > 설정 > '새 액세스 토큰' 발급 후 env로 (< > 괄호 없이!)
  #   PowerShell : $env:UNTIL_ETL_WS_TOKEN = "내토큰"
  #   bash/zsh   : export UNTIL_ETL_WS_TOKEN=내토큰
  python run_etl_corpus.py --label minjun          # 나(민준) 것 전부
  python run_etl_corpus.py --label jihu --current-only   # 이번 학기만
  python run_etl_corpus.py --label jaewon --no-download  # 파일 다운로드 없이 명세만(빠른 조사)

산출물: _until_work/corpus/<label>/
    manifest.jsonl                     # 과제 1건 = 1줄(분류용 인덱스)
    _course_context/<course_id>/course_context.json  # 과목 원본 목록(조회 1회)
    <과목>/<과제>/spec.md              # 과제 명세(제목·마감·본문)
    <과목>/<과제>/etl_context/context.md # 과제별 관련 eTL 자료·공지 번들
    <과목>/<과제>/intro_files/         # 교수가 준 과제 첨부
    <과목>/<과제>/submission.md        # 내가 낸 온라인 텍스트 본문(있으면)
    <과목>/<과제>/submission_files/    # 내가 낸 제출 파일(있으면)

⚠ 읽기 전용 보증: 이 러너는 CanvasApiAdapter의 조회(GET) 메서드만 호출한다.
  과제 제출·수정·삭제 같은 쓰기 요청은 어디에도 없다(순수 조회+다운로드).
  남의 제출물은 애초에 학생 토큰으로 조회되지 않는다(각자 본인 것만).

프라이버시: 모아진 데이터는 전부 로컬 _until_work/ (=.gitignore) 안에만 쌓인다.
  이 러너 자체는 LLM/서버로 아무것도 보내지 않는다(순수 조회+다운로드).
"""
from __future__ import annotations

import argparse
import json
import os
import time
from html.parser import HTMLParser
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── stdlib 전용 순수 헬퍼 (until 미의존 → 단독 테스트 가능) ────────────────────
import re as _re
from until.console import force_utf8

_ILLEGAL_FN = _re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe(name: str, limit: int = 80) -> str:
    """서버가 준 이름을 안전한 파일/폴더명으로. 경로탈출·금지문자 제거 + 길이 절단.

    절단 시 확장자는 보존한다 — 꼬리 절단으로 '.pdf'가 '.p'가 되면 검증에서
    형식 판정이 불가능해진다(실코퍼스 결함 실측)."""
    base = os.path.basename((name or "").replace("\\", "/").strip())
    base = _ILLEGAL_FN.sub("_", base).strip(". ")
    if len(base) > limit:
        stem, ext = os.path.splitext(base)
        ext = ext if len(ext) <= 10 else ""  # 비정상적으로 긴 '확장자'는 무시
        base = stem[:max(1, limit - len(ext))].strip(". ") + ext
    return base or "untitled"


class _Strip(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"p", "div", "br", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def strip_html(html: str) -> str:
    """HTML 조각 → 평문(태그 제거·블록은 줄바꿈). Canvas 온라인 텍스트 본문 정리용."""
    if not html:
        return ""
    p = _Strip()
    try:
        p.feed(html)
    except Exception:
        return _re.sub(r"<[^>]+>", " ", html).strip()
    text = unescape("".join(p.parts))
    text = "\n".join(line.strip() for line in text.splitlines())
    return _re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_submission(s: Any) -> Dict[str, Any]:
    """Canvas 제출물 JSON(딕셔너리 1건) → 본인 제출물 추출.

    반환: {
      "text":  str,                        # 온라인 텍스트 본문(HTML→평문)
      "files": [{"name","url","area"}...], # 제출 파일(중복 URL 제거)
      "submitted": bool,                   # 제출/채점 완료 여부
      "status": str,                       # 원 상태 문자열(submitted/graded/unsubmitted/...)
    }
    """
    out: Dict[str, Any] = {"text": "", "files": [], "submitted": False, "status": ""}
    if not isinstance(s, dict):
        return out
    status = (s.get("workflow_state") or "").strip().lower()
    out["status"] = status
    out["submitted"] = bool((s.get("submitted_at") or "").strip()) or status in (
        "submitted", "graded", "complete")
    out["text"] = strip_html(s.get("body") or "")
    seen_urls = set()
    for f in s.get("attachments") or []:
        if not isinstance(f, dict):
            continue
        name = (f.get("display_name") or f.get("filename") or "").strip()
        url = (f.get("url") or "").strip()
        if not name or name in (".", "..") or not url or url in seen_urls:
            continue
        seen_urls.add(url)
        out["files"].append({"name": name, "url": url, "area": "submission"})
    return out


def _kst_date(iso: Any) -> str:
    """Canvas ISO8601(UTC) → 'YYYY-MM-DD'(KST). 없으면 ''. 코퍼스 라벨·정렬용."""
    import datetime as _dt
    if not isinstance(iso, str) or not iso.strip():
        return ""
    try:
        t = _dt.datetime.fromisoformat(iso.strip().replace("Z", "+00:00"))
    except ValueError:
        return ""
    if t.tzinfo is None:
        t = t.replace(tzinfo=_dt.timezone.utc)
    return t.astimezone(_dt.timezone(_dt.timedelta(hours=9))).strftime("%Y-%m-%d")


class _CachedCourseContextAdapter:
    """과목당 한 번 조회한 자료·공지를 기존 관련도 수집기에 제공한다."""

    def __init__(self, live, materials: List[Any], announcements: List[Any]):
        self.live = live
        self.materials = materials
        self.announcements = announcements

    def list_course_files(self, course_id: str, base_url: str) -> List[Any]:
        return self.materials

    def list_modules(self, course_id: str, base_url: str) -> List[Any]:
        return []  # materials에 파일+모듈을 이미 합쳐 중복 조회하지 않는다.

    def collect_announcements(self, course, *, limit=5, news_only=True,
                              include_replies=False) -> List[Any]:
        # Canvas 공지 전용 endpoint 결과만 사용한다. Q&A/답글은 저장하지 않는다.
        return list(self.announcements[:limit])

    def download(self, attachment, dest_dir: str) -> str:
        return self.live.download(attachment, dest_dir)


def _source_bundle_text(sources: List[Any]) -> str:
    parts = ["# eTL 과목 컨텍스트 번들", ""]
    if not sources:
        parts.append("(과제 키워드와 일치하는 과목 자료·공지가 없음)")
    for index, source in enumerate(sources, 1):
        parts.extend([
            f"## 컨텍스트 {index}: {source.title}",
            f"원문 위치: {source.url}" if source.url else "원문 위치: (없음)",
            source.text,
            "",
        ])
    return "\n".join(parts).rstrip() + "\n"


def _write_course_context_cache(root: Path, course_id: str, materials: List[Any],
                                announcements: List[Any]) -> Path:
    """재현성용 과목 원본 목록. 작성자 자유 문자열은 저장하지 않는다."""
    from until.capture.sources.moodle_ws import announcement_author_role
    target = root / "_course_context" / _safe(course_id, 40) / "course_context.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "course_id": str(course_id),
        "materials": [{"name": x.name, "url": x.url} for x in materials],
        "announcements": [{
            "subject": x.subject, "body": x.body,
            "author": announcement_author_role(x.author),
            "created_iso": x.created_iso, "forum": x.forum, "url": x.url,
        } for x in announcements],
        "privacy": {
            "author_policy": "instructor|ta|student|unknown",
            "discussion_replies_included": False,
            "body_names_redacted": False,
        },
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def _write_assignment_context(adir: Path, sources: List[Any]) -> Path:
    """0건도 명시적 번들로 저장해 full과 미수집 상태를 구분한다."""
    target = adir / "etl_context" / "context.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_source_bundle_text(sources), encoding="utf-8")
    return target


# ── 라이브 수집 (until 라이브러리 사용) ───────────────────────────────────────
def _download(adapter, url: str, dest: Path, max_bytes: int) -> Optional[int]:
    """Canvas 파일 URL에서 파일을 받아 dest에 저장. 반환=바이트수(용량초과 스킵=None).

    adapter.download와 같은 2단 전략(Bearer → 서명 URL 403이면 인증 없이 재시도)에
    용량 상한과 재실행 idempotent(이미 있으면 스킵)를 더한 것. GET만 쓴다."""
    import urllib.error
    import urllib.request
    from until.capture.sources.canvas_api import _StripAuthOnRedirect
    if dest.exists() and dest.stat().st_size > 0:
        return dest.stat().st_size  # 이미 있음(재실행 idempotent)
    try:
        r = urllib.request.urlopen(adapter._request(url), timeout=adapter.timeout)
    except urllib.error.HTTPError:
        opener = urllib.request.build_opener(_StripAuthOnRedirect())
        r = opener.open(urllib.request.Request(url), timeout=adapter.timeout)
    with r:
        body = r.read(max_bytes + 1)
    if len(body) > max_bytes:
        return None  # 용량 초과 → 스킵(경고는 호출부에서)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    return len(body)


def main(argv: Optional[List[str]] = None) -> int:
    # Windows 기본 cp949 콘솔에서도 기호(—·⚠ 등) 때문에 실행이 죽지 않게 한다.
    force_utf8()
    ap = argparse.ArgumentParser(
        description="eTL 과거 코퍼스 수집(읽기 전용, Canvas API): 과제 명세 + 본인 제출물")
    ap.add_argument("--label", default=os.getenv("USER") or "me",
                    help="사람 라벨(폴더명). 세 명이 각자 다르게 (예: minjun/jihu/jaewon)")
    ap.add_argument("--out", default="_until_work/corpus", help="코퍼스 출력 루트")
    ap.add_argument("--base", default=os.getenv("UNTIL_ETL_BASE") or "https://myetl.snu.ac.kr",
                    help="eTL 베이스 URL")
    ap.add_argument("--current-only", action="store_true",
                    help="지난 학기 제외(이번 학기 과목만)")
    ap.add_argument("--no-download", action="store_true",
                    help="파일 다운로드 없이 명세·본문·파일목록만(빠른 조사)")
    ap.set_defaults(no_feedback=True)
    ap.add_argument("--no-feedback", dest="no_feedback", action="store_true",
                    help="성적·교수 코멘트·루브릭을 API 응답에서 제외(팀원 프리셋 기본)")
    ap.add_argument("--include-feedback", dest="no_feedback", action="store_false",
                    help="개인 진단용으로 피드백 필드를 요청(코퍼스에는 여전히 저장하지 않음)")
    ap.add_argument("--max-file-mb", type=float, default=20.0, help="파일당 다운로드 상한(MB)")
    ap.add_argument("--limit-courses", type=int, default=0, help="테스트용: 과목 N개만")
    ap.add_argument("--inventory", action="store_true", help="시작 전 토큰 소유자 확인 출력")
    args = ap.parse_args(argv)

    try:
        from until.capture.sources.canvas_api import (
            CanvasApiAdapter, parse_canvas_api_assignment, parse_courses,
        )
        from until.context.etl_materials import (
            collect_material_refs, collect_related_materials, fetch_material_texts,
            materials_to_sources,
        )
        from until.context.etl_announcements import (
            collect_related_announcements, announcements_to_sources, spec_announcements,
        )
    except Exception as e:
        print(f"until 패키지를 불러오지 못했습니다(레포 루트에서 실행하세요): {e}")
        return 2

    # 토큰: 팀 안내대로 UNTIL_ETL_WS_TOKEN 우선, 기존 UNTIL_CANVAS_TOKEN도 허용.
    token = (os.getenv("UNTIL_ETL_WS_TOKEN") or os.getenv("UNTIL_CANVAS_TOKEN") or "").strip()
    if not token:
        print("eTL 액세스 토큰이 필요합니다. eTL > 계정 > 설정 > '새 액세스 토큰' 발급 후\n"
              "  PowerShell : $env:UNTIL_ETL_WS_TOKEN = \"내토큰\"\n"
              "  bash/zsh   : export UNTIL_ETL_WS_TOKEN=내토큰\n"
              "으로 전달하세요(< > 괄호 없이 토큰만).")
        return 1
    adapter = CanvasApiAdapter(token=token)

    base = args.base.strip().rstrip("/")

    if args.inventory:
        prof = adapter.get_self_profile(base)
        print(f"토큰 확인: {prof.get('name') or '?'} ({prof.get('primary_email') or '이메일 비공개'})\n")

    max_bytes = int(args.max_file_mb * 1024 * 1024)
    root = Path(args.out) / _safe(args.label, 40)
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "manifest.jsonl"

    # 1) 내 수강 과목 전체(지난 학기 포함) — active에 남은 지난 과목 + completed 등록 병합.
    api_courses = f"{base}/api/v1/courses?include[]=term&per_page=100"
    raw_courses = adapter._get_paginated(f"{api_courses}&enrollment_state=active")
    if not args.current_only:
        try:
            raw_courses = list(raw_courses) + list(
                adapter._get_paginated(f"{api_courses}&enrollment_state=completed"))
        except Exception:
            pass
    courses = parse_courses(raw_courses, include_past=not args.current_only)
    # 최신 학기부터(학기명 내림차순 — eTL 학기명은 'YYYY...' 접두라 문자열 정렬로 충분).
    courses.sort(key=lambda c: c.term or "", reverse=True)
    if args.limit_courses > 0:
        courses = courses[: args.limit_courses]

    print(f"[{args.label}] 대상 과목 {len(courses)}개 "
          f"({'이번 학기만' if args.current_only else '지난 학기 포함'}) → {root}")

    n_assign = n_with_text = n_with_files = n_dl = 0
    rows: List[str] = []
    t0 = time.time()

    for ci, c in enumerate(courses, 1):
        term = c.term
        cdir = root / _safe(f"{term} {c.name}".strip())
        print(f"  ({ci}/{len(courses)}) [{term or '학기?'}] {c.name[:40]}", flush=True)

        try:
            assigns = adapter._get_paginated(
                f"{base}/api/v1/courses/{c.id}/assignments?per_page=100")
        except Exception as e:
            print(f"      ! 과제 조회 실패: {e}")
            continue
        assigns = [a for a in (assigns if isinstance(assigns, list) else [])
                   if isinstance(a, dict) and a.get("id")]
        if not assigns:
            continue

        # 과목 컨텍스트는 과목당 한 번만 조회하고, 과제별 순위화는 캐시 위에서 한다.
        # 공지 전용 endpoint만 사용해 다른 학생의 Q&A/답글은 수집하지 않는다.
        try:
            course_materials = collect_material_refs(adapter, c.id, base)
        except Exception as e:
            print(f"      · 과목 자료 목록 조회 실패: {e}")
            course_materials = []
        try:
            course_announcements = adapter.collect_announcements(
                c, limit=100, news_only=True, include_replies=False)
        except Exception as e:
            print(f"      · 과목 공지 조회 실패: {e}")
            course_announcements = []
        _write_course_context_cache(
            root, c.id, course_materials, course_announcements)
        context_adapter = _CachedCourseContextAdapter(
            adapter, course_materials, course_announcements)

        # 본인 제출물은 과목당 1콜(교수 피드백 학습과 같은 엔드포인트) → 과제 id로 매칭.
        subs_by_aid: Dict[str, dict] = {}
        try:
            for s in adapter.my_submissions_json(
                    c.id, base, include_feedback=not args.no_feedback) or []:
                if isinstance(s, dict):
                    aid = str(s.get("assignment_id")
                              or (s.get("assignment") or {}).get("id") or "").strip()
                    if aid:
                        subs_by_aid[aid] = s
        except Exception as e:
            print(f"      · 제출물 조회 실패(과목 전체): {e}")

        for a in assigns:
            aid = str(a.get("id")).strip()
            title = (a.get("name") or "(제목 없음)").strip()
            adir = cdir / _safe(f"{aid} {title}")
            adir.mkdir(parents=True, exist_ok=True)

            # --- 과제 명세(spec.md) + 과제 첨부 ---
            raw = parse_canvas_api_assignment(a, base)
            spec = (f"# {title}\n\n"
                    f"과목: {c.name}\n학기: {term}{'(지난 학기)' if c.ended else ''}\n"
                    f"출처: {raw.url}\n과제ID: {aid}\n\n{raw.description}\n")
            (adir / "spec.md").write_text(spec, encoding="utf-8")

            # --- eTL 과목 컨텍스트(기존 관련도 수집기 재사용) ---
            spec_like = {
                "goal": title,
                "deliverable": title,
                "requirements": [raw.description],
            }
            related_materials = collect_related_materials(
                context_adapter, c.id, spec_like, base, k=5)
            material_texts = (fetch_material_texts(context_adapter, related_materials)
                              if not args.no_download else {})
            related_announcements = collect_related_announcements(
                context_adapter, c, spec_like, k=3, include_replies=False)
            context_sources = (
                materials_to_sources(related_materials, material_texts)
                + announcements_to_sources(spec_announcements(related_announcements))
            )
            context_path = _write_assignment_context(adir, context_sources)

            n_intro = 0
            if raw.attachments and not args.no_download:
                idir = adir / "intro_files"
                for att in raw.attachments:
                    try:
                        got = _download(adapter, att.url, idir / _safe(att.name), max_bytes)
                        if got is None:
                            print(f"      · 과제첨부 용량초과 스킵: {att.name[:30]}")
                        else:
                            n_intro += 1
                    except Exception as e:
                        print(f"      · 과제첨부 실패({att.name[:24]}): {e}")
            elif raw.attachments:
                n_intro = len(raw.attachments)

            # --- 본인 제출물(submission.md + submission_files/) ---
            sub = parse_submission(subs_by_aid.get(aid))

            if sub["text"]:
                n_with_text += 1
                (adir / "submission.md").write_text(
                    f"# [제출본] {title}\n\n상태: {sub['status']}\n\n{sub['text']}\n",
                    encoding="utf-8")

            n_sub_files = 0
            if sub["files"]:
                n_with_files += 1
                if not args.no_download:
                    sdir = adir / "submission_files"
                    for f in sub["files"]:
                        try:
                            got = _download(adapter, f["url"], sdir / _safe(f["name"]), max_bytes)
                            if got is None:
                                print(f"      · 제출파일 용량초과 스킵: {f['name'][:30]}")
                            else:
                                n_sub_files += 1
                                n_dl += 1
                        except Exception as e:
                            print(f"      · 제출파일 실패({f['name'][:24]}): {e}")
                else:
                    n_sub_files = len(sub["files"])

            n_assign += 1
            rows.append(json.dumps({
                "label": args.label,
                "course_id": c.id,
                "course_name": c.name,
                "term": term,
                "ended": c.ended,
                "assignment_id": aid,
                "title": title,
                "due_at": _kst_date(a.get("due_at")),
                "url": raw.url,
                "n_intro_attachments": n_intro,
                "submitted": sub["submitted"],
                "submission_status": sub["status"],
                "has_submission_text": bool(sub["text"]),
                "n_submission_files": n_sub_files,
                "context_path": str(context_path.relative_to(root)),
                "n_context_sources": len(context_sources),
                "n_context_materials": len(related_materials),
                "n_context_announcements": len(related_announcements),
                "dir": str(adir.relative_to(root)),
            }, ensure_ascii=False))

    manifest.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")

    dt = time.time() - t0
    print("\n=== 수집 완료 ===")
    print(f"  과목 {len(courses)} · 과제 {n_assign}건")
    print(f"  제출본문 있는 과제 {n_with_text}건 · 제출파일 있는 과제 {n_with_files}건"
          f"{'' if args.no_download else f' · 내려받은 제출파일 {n_dl}개'}")
    print(f"  매니페스트: {manifest.resolve()}")
    print(f"  코퍼스 루트: {root.resolve()}   ({dt:.1f}s)")
    if args.no_download:
        print("  (--no-download: 파일은 목록만. 실제 파일이 필요하면 플래그 빼고 재실행)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
