"""과목 여러 위치에 흩어진 실제 과제 명세를 연결한다(결정적, LLM 0).

제출함의 ``숙제3 제출``만으로는 문제를 알 수 없는 실사용 패턴을 다룬다.
같은 번호·주차의 강의자료 PDF 전체와 코딩 게시글을 찾아, 문서 중간/끝에 있는
Homework·Exercise·과제 구간까지 발췌해 Execution 근거로 제공한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import tempfile
from typing import Iterable, List

from ..capture.sources.models import Attachment
from ..llm.base import SourceDoc

_NUMBER = re.compile(
    r"(?:(\d+)\s*주차|(?:숙제|과제|실습|assignment|homework|problem\s*set|lab|hw)\s*#?\s*(\d+))",
    re.I,
)
_SPEC_MARKER = re.compile(
    r"(?:homework|assignment|problem\s*set|exercise|coding\s*(?:assignment|project)|"
    r"programming\s*(?:assignment|project)|과제|숙제|연습\s*문제|제출|구현\s*사항)",
    re.I,
)
_CODE = re.compile(r"코딩|프로그래밍|coding|programming|소스\s*코드|github|zip", re.I)
_GENERIC = re.compile(
    r"^(?:숙제|과제|assignment|homework|hw)\s*#?\s*\d+(?:\s*제출)?$|"
    r"^실습\s*\d+\s*(?:레포트|보고서)?$|"
    r"^(?:코딩|프로그래밍)\s*과제(?:\s*제출)?$", re.I,
)
_FILE_URL = re.compile(r"/files/\d+|pluginfile\.php/", re.I)
_REPORT = re.compile(r"레포트|보고서|실험\s*결과|고찰|report|results?", re.I)


@dataclass(frozen=True)
class SpecCandidate:
    title: str
    text: str
    url: str
    location: str
    score: int


def assignment_identity(title: str) -> tuple[str, bool]:
    """과제 제목에서 연결용 번호와 코딩 여부를 뽑는다."""
    match = _NUMBER.search(title or "")
    number = next((x for x in match.groups() if x), "") if match else ""
    return number, bool(_CODE.search(title or ""))


def needs_distributed_spec(title: str, description: str = "") -> bool:
    """현재 제출 페이지 자체로 명세가 부족할 가능성이 큰 제목인지 판정."""
    clean = " ".join((title or "").split())
    short = len(" ".join((description or "").split())) < 900
    return short and bool(_GENERIC.search(clean))


def _identity_score(text: str, number: str, code: bool) -> int:
    low = text or ""
    score = 0
    if number:
        # 숫자 부분문자열(1 in 10) 오탐을 막고, 번호와 과제 표식이 같이 있을 때 강화.
        if re.search(rf"(?<!\d){re.escape(number)}(?!\d)", low):
            score += 4
        else:
            return 0
    if _SPEC_MARKER.search(low):
        score += 3
    if code and _CODE.search(low):
        score += 3
    return score


def extract_spec_windows(text: str, *, number: str = "", code: bool = False,
                         radius: int = 900, max_windows: int = 3) -> str:
    """문서 어디에 있든 과제 표식 주변 문맥을 겹치지 않게 발췌한다."""
    source = " ".join((text or "").split())
    if not source:
        return ""
    centers = []
    for marker in _SPEC_MARKER.finditer(source):
        around = source[max(0, marker.start() - 180):marker.end() + 180]
        if _identity_score(around, number, code) >= (7 if number else 3):
            centers.append(marker.start())
    if code:
        centers.extend(m.start() for m in _CODE.finditer(source)
                       if _identity_score(source[max(0, m.start()-250):m.end()+250],
                                          number, code) >= (7 if number else 6))
    spans = []
    for center in sorted(set(centers)):
        start, end = max(0, center - radius), min(len(source), center + radius)
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
        if len(spans) >= max_windows:
            break
    return "\n\n…\n\n".join(source[a:b] for a, b in spans)


def rank_reference_names(refs: Iterable[Attachment], title: str,
                         limit: int = 10) -> List[Attachment]:
    """전 과목 파일 중 번호·주차가 맞고 다운로드 가능한 자료를 우선한다."""
    number, code = assignment_identity(title)
    ranked = []
    for ref in refs or []:
        if not _FILE_URL.search(ref.url or ""):
            continue
        score = _identity_score(ref.name, number, code)
        if score:
            ranked.append((score, ref.name.lower(), ref))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return [row[2] for row in ranked[:limit]]


def discussion_candidates(rows: Iterable[dict], title: str) -> List[SpecCandidate]:
    """Canvas 토론/코딩 게시글에서 현재 과제와 연결되는 명세 후보를 만든다."""
    number, code = assignment_identity(title)
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        heading = str(row.get("title") or "")
        body = str(row.get("body") or "")
        score = _identity_score(f"{heading}\n{body}", number, code)
        if score < (7 if number else 6):
            continue
        excerpt = extract_spec_windows(body, number=number, code=code)
        if not excerpt:
            excerpt = body[:3000]
        out.append(SpecCandidate(heading or "과제 게시글", excerpt,
                                 str(row.get("url") or ""), "코딩/토론 게시판",
                                 score + 2))
    return out


def collect_distributed_spec(adapter, course_id: str, base_url: str,
                             title: str, description: str = "",
                             *, max_files: int = 10,
                             refs: "List[Attachment] | None" = None) -> List[SourceDoc]:
    """과목 자료 PDF 전체와 게시판을 훑어 상위 분산 명세를 SourceDoc으로 반환."""
    if not needs_distributed_spec(title, description):
        return []
    from .etl_materials import collect_material_refs
    number, code = assignment_identity(title)
    candidates: List[SpecCandidate] = []
    if refs is None:
        try:
            refs = collect_material_refs(adapter, course_id, base_url)
        except Exception:
            refs = []
    for ref in rank_reference_names(refs, title, limit=max_files):
        tmp = tempfile.mkdtemp(prefix="until_spec_")
        try:
            from ..capture.ingest import ingest_file
            path = Path(adapter.download(Attachment(ref.name, ref.url), tmp))
            if path.stat().st_size > 20 * 1024 * 1024:
                continue
            # 모듈 라벨 때문에 이름이 `노트.pdf [3주차]`가 되어 suffix 판정이 깨질
            # 수 있다. 확장자 유무와 무관하게 매직 바이트로 PDF임을 복원한다.
            if path.read_bytes()[:4].startswith(b"%PDF") and path.suffix.lower() != ".pdf":
                path = path.rename(path.with_suffix(".pdf"))
            full = ingest_file(path).text
            excerpt = extract_spec_windows(full, number=number, code=code)
            if excerpt:
                score = _identity_score(ref.name, number, code) + 3
                candidates.append(SpecCandidate(ref.name, excerpt, ref.url,
                                                "강의자료 전체 본문", score))
        except Exception:
            continue
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    if hasattr(adapter, "list_discussion_topics"):
        try:
            candidates += discussion_candidates(
                adapter.list_discussion_topics(course_id, base_url), title)
        except Exception:
            pass
    candidates.sort(key=lambda c: (-c.score, c.title))
    out = []
    for c in candidates[:3]:
        text = (f"현재 제출함 '{title}'의 실제 명세 후보.\n"
                f"eTL 위치: {c.location}\n"
                "과제 번호·주차·명세 표식으로 연결했으며, 다른 회차 내용은 사용하지 말 것.\n\n"
                f"{c.text[:5000]}")
        out.append(SourceDoc(title=f"[분산 과제 명세] {c.title}", text=text, url=c.url))
    return out


def distributed_task_type(sources: Iterable[SourceDoc]) -> str:
    """분산 명세가 있으면 그 본문으로 code/problemset 유형을 바로잡는다."""
    found = [s for s in sources or []
             if str(getattr(s, "title", "")).startswith("[분산 과제 명세]")]
    if not found:
        return ""
    joined = "\n".join(f"{s.title}\n{s.text}" for s in found)
    if _CODE.search(joined):
        return "code"
    if _REPORT.search(joined):
        return "report"
    return "problemset"


def distributed_spec_directive(sources: Iterable[SourceDoc]) -> str:
    """연결된 명세를 현재 과제의 권위 있는 실행 입력으로 쓰게 한다."""
    if not distributed_task_type(sources):
        return ""
    return (
        "[분산 과제 명세 연결]\n"
        "- [분산 과제 명세]는 현재의 짧은 제출함과 같은 번호·주차로 연결한 실제 요구사항이다.\n"
        "- 문항·입출력·제약·제출 형식을 이 명세에서 추출해 단위별로 끝까지 수행한다.\n"
        "- 다른 회차 문제를 섞지 말고, 번호 충돌이나 필수 데이터 누락만 DECISION으로 남긴다."
    )
