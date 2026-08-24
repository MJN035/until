"""
P10 — eTL 관련 자료 자동수집 + 순위화.

과제가 속한 과목의 자료(파일 탭 + 모듈 항목)를 모아, 과제 키워드와 가장 관련 있는
상위 N건을 고른다. 본문(PDF 등)을 받지 않아도 되도록 **이름/제목 기준 키워드 중첩**으로
순위화한다(토큰 0, 결정적). 라이브 본문 파싱이 가능하면 나중에 확장 가능.

파이프라인엔 SourceDoc 목록으로 주입된다(Execution 맥락). 접속은 어댑터 뒤에.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol

from ..capture.sources.models import Attachment
from ..llm.base import SourceDoc
from .retrieval import keywords_from_spec

# 모듈 항목 이름 끝의 " [N주차 모듈]" 라벨 — 같은 파일이 파일 탭과 모듈에서
# 다른 이름으로 잡히는 원인(중복 인용 실관측).
_MODULE_LABEL_RE = re.compile(r"\s*\[[^\[\]]*\]\s*$")
# 다운로드 가능한 파일형 자료 URL — 페이지·외부링크는 제외.
#   Canvas: /files/123      Moodle: (webservice/)pluginfile.php/...
_FILE_URL_RE = re.compile(r"/files/\d+|pluginfile\.php/")

# 자동 다운로드 정책 — 무차별 수집 금지(팀원 주의: 용량·프라이버시).
#   ① 범위: rank_materials 상위 k건만(호출부에서 이미 제한).
#   ② 파일당 용량 상한(기본 20MB) + 배치 합계 상한(기본 60MB) — 초과 시 스킵.
#   ③ 프라이버시: 임시 폴더로만 받고 파싱 후 즉시 삭제(휘발성; 영속 캐시 없음).
_MB = 1024 * 1024


def _max_file_bytes() -> int:
    import os
    try:
        return max(1, int(os.getenv("UNTIL_MATERIAL_MAX_MB", "20"))) * _MB
    except ValueError:
        return 20 * _MB


def _max_total_bytes() -> int:
    import os
    try:
        return max(1, int(os.getenv("UNTIL_MATERIAL_TOTAL_MB", "60"))) * _MB
    except ValueError:
        return 60 * _MB


def _base_name(name: str) -> str:
    return _MODULE_LABEL_RE.sub("", name or "").strip()


class MaterialAdapter(Protocol):
    def list_course_files(self, course_id: str, base_url: str) -> List[Attachment]: ...
    def list_modules(self, course_id: str, base_url: str) -> List[Attachment]: ...


@dataclass
class MaterialHit:
    name: str
    url: str
    score: float
    matched: List[str]


def collect_material_refs(adapter: MaterialAdapter, course_id: str, base_url: str) -> List[Attachment]:
    """과목 파일 + 모듈 항목을 자료 후보로 모은다. 한쪽 실패는 무시."""
    refs: List[Attachment] = []
    for fn in ("list_course_files", "list_modules"):
        try:
            refs.extend(getattr(adapter, fn)(course_id, base_url))
        except Exception:
            continue
    # 중복 제거 — 파일 id(URL의 /files/N)가 있으면 그것이 정체성: 같은 파일이
    # 파일 탭과 모듈에서 다른 표기·다른 URL로 두 번 잡히는 실관측 방지.
    # id가 없으면(페이지·외부링크) 라벨 벗긴 이름+URL로. 이름만으로 합치면
    # 주차별 동명 항목("발표 자료")이 서로 다른 파일인데 소실된다(리뷰 발견).
    from ..capture.sources.canvas_api import _file_id
    seen, out = set(), []
    for r in refs:
        fid = _file_id(r.url or "")
        key = fid if fid else (_base_name(r.name), r.url)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _score(name: str, keywords: List[str]) -> tuple[float, List[str]]:
    # 한국어 파일명은 공백 없이 붙는 경우가 많아(예: '도시문화론.pdf') 토큰 완전일치로는
    # 키워드 '도시'가 매칭되지 않는다 → retrieval._keyword_hit과 동일하게 부분문자열 매칭.
    low = name.lower()
    matched = [k for k in keywords if k.lower() in low]
    return float(len(matched)), matched


# 어떤 과제에나 나오는 일반어 — 이런 단어만으로 매칭되면 무관한 자료가 끌려온다
# (실관측: '과제'·'제출' 매칭으로 다른 과목 성격의 '팀과제 제출' 모듈이 관련자료로 선정).
_GENERIC_WORDS = frozenset({
    "과제", "제출", "수업", "강의", "자료", "마감", "확인", "작성", "안내",
    "파일", "첨부", "주차", "모듈", "개인과제", "팀과제", "공지", "참고",
})


def rank_materials(materials: List[Attachment], keywords: List[str], k: int = 5) -> List[MaterialHit]:
    """자료 이름을 과제 키워드와 매칭해 상위 k건. 매칭 0인 자료는 제외.

    일반어(과제·제출 등)는 매칭 키워드에서 제외 — 내용어(주제어)로만 순위화한다."""
    kw = [w for w in keywords if len(w) >= 2 and w not in _GENERIC_WORDS]
    hits: List[MaterialHit] = []
    for m in materials:
        s, matched = _score(m.name, kw)
        if s > 0:
            hits.append(MaterialHit(name=m.name, url=m.url, score=s, matched=matched))
    hits.sort(key=lambda h: (-h.score, h.name))
    return hits[:k]


def collect_related_materials(
    adapter: MaterialAdapter, course_id: str, spec: dict, base_url: str, *, k: int = 5,
    extra_keywords: Optional[List[str]] = None,
    refs: Optional[List[Attachment]] = None,
) -> List[MaterialHit]:
    """과목 자료를 모아 과제(spec) 키워드로 순위화한 상위 k건."""
    keywords = keywords_from_spec(spec) + list(extra_keywords or [])
    materials = collect_material_refs(adapter, course_id, base_url) if refs is None else refs
    return rank_materials(materials, keywords, k=k)


def _looks_garbled(text: str, sample: int = 800, threshold: float = 0.05) -> bool:
    """이진 파일이 텍스트 폴백으로 깨져 디코드된 흔적 — 대체문자(�) 비율로 판정.

    (cp949 우연 디코드까지 다 잡진 못하지만, 흔한 케이스의 백스톱.)"""
    s = text[:sample]
    if not s:
        return False
    return (s.count("�") / len(s)) > threshold


#: 매직 바이트 → 확장자. 모듈 항목 제목엔 확장자가 없을 때가 많은데
#: ("3주차 강의노트"), 그대로 ingest하면 이진 바이트가 텍스트 폴백으로 깨져
#: 발췌에 주입된다.
#:
#: 한글 파일이 특히 위험하다: **hwpx는 zip이라 PK로 시작해** 예전 규칙에서
#: `.docx`로 이름 붙어 잘못된 파서를 탔고, 이진 **hwp는 CFB(D0 CF 11 E0)**
#: 라 아무 규칙에도 안 걸려 텍스트 폴백으로 깨졌다. eTL 공지·자료에는 한글
#: 파일이 흔하다(사용자 확인 2026-08-23).
def _sniff_suffix(path) -> str:
    head = path.read_bytes()[:8]
    if head.startswith(b"%PDF"):
        return ".pdf"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return ".hwp"          # CFB — 한글 5.x(이진). _read_hwp가 형식을 재확인한다.
    if head.startswith(b"PK"):
        # zip 계열 — hwpx와 docx를 내용물로 가른다(둘 다 PK로 시작한다).
        try:
            import zipfile
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
            if any(n.startswith("Contents/") for n in names):
                return ".hwpx"
        except Exception:
            pass
        return ".docx"
    return ".txt"


def fetch_material_texts(adapter, hits: List[MaterialHit], *,
                         top: int = 2, chars: int = 3000) -> Dict[str, str]:
    """상위 자료의 실제 본문 수집 — 파일형(Canvas /files/ URL)만 다운로드→파싱.

    과제 원문이 첨부가 아니라 과목 강의자료(PDF)에 있는 실관측 대응: 제목만으로는
    초안도 프롬프트 번들도 알맹이가 없다. 토큰 예산을 위해 상위 top건·건당 chars자만.
    개별 실패(다운로드/파싱)는 조용히 스킵 — 제목-만 폴백이 있으므로 전체를 막지 않는다."""
    import shutil
    import tempfile
    out: Dict[str, str] = {}
    if adapter is None or not hasattr(adapter, "download"):
        return out
    max_file = _max_file_bytes()
    total_budget = _max_total_bytes()
    spent = 0
    for h in hits:
        if len(out) >= top or spent >= total_budget:
            break
        if not _FILE_URL_RE.search(h.url or ""):
            continue
        tmp = tempfile.mkdtemp(prefix="until_mat_")
        try:
            from pathlib import Path as _P

            from ..capture.ingest import ingest_file
            path = _P(adapter.download(
                Attachment(name=_base_name(h.name) or "material", url=h.url), tmp))
            # 용량 정책: 파일당 상한 초과 또는 배치 합계 초과 시 파싱하지 않고 스킵
            # (거대 파일이 메모리·시간을 잠식하는 것 방지).
            size = path.stat().st_size
            if size > max_file:
                continue
            spent += size
            # 모듈 항목 제목엔 확장자가 없을 수 있다("3주차 강의노트") — 그대로
            # ingest하면 PDF 바이트가 텍스트 폴백으로 깨져 발췌에 주입된다(리뷰 발견).
            # 매직 바이트로 유형을 붙여 올바른 파서를 태운다.
            if not path.suffix:
                path = path.rename(path.with_suffix(_sniff_suffix(path)))
            text = " ".join((ingest_file(str(path)).text or "").split())
            if text and not _looks_garbled(text):
                out[h.name] = text[:chars] + (" …(발췌 — 뒷부분 생략)"
                                              if len(text) > chars else "")
        except Exception:
            continue
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return out


def materials_to_sources(hits: List[MaterialHit],
                         texts: Optional[Dict[str, str]] = None) -> List[SourceDoc]:
    """순위화된 자료를 Execution 맥락(SourceDoc)으로 — 자료마다 하나씩, 파일명이 그대로
    범례([자료N])에 보이도록. texts(fetch_material_texts 결과)에 본문이 있으면 실제
    발췌를 담고, 없으면 제목만 — 그 사실을 명시해 모델이 제목만으로 내용을 지어내
    인용하는 것을 막는다."""
    texts = texts or {}
    out = []
    for h in hits:
        # eTL 정확한 위치 — 모듈 항목은 이름의 [N주차 모듈] 라벨로, 파일 탭은 URL로 구분.
        where = f"eTL 위치: {h.url}" if h.url else "eTL 위치: (URL 없음)"
        body = texts.get(h.name)
        if body:
            text = (f"eTL 과목 강의자료 '{h.name}'\n{where}\n본문 발췌:\n{body}")
        else:
            text = (f"eTL 과목 강의자료 제목: {h.name}\n{where}\n"
                    "(과제 키워드와 제목이 겹쳐 선별됨. 본문은 수집하지 않았으므로 "
                    "제목에서 확인되는 사실 외에는 이 자료를 근거로 인용하지 말 것.)")
        out.append(SourceDoc(title=f"[eTL 자료] {h.name}", text=text, url=h.url or ""))
    return out
