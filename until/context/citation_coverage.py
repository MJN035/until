"""인용 커버리지 점검 — 결정적(LLM 0).

Execution에 넣은 근거자료(sources 범례) 중 초안 본문이 실제로 [자료N]으로 인용한
것이 얼마나 되는지 집계한다. 자료를 줬는데 하나도 인용 안 했으면 사람이 알 수 있게.
경계선 철학 유지: 집계·안내만 하고 억지로 인용을 끼워 넣지 않는다.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List
import re

_CITE_RE = re.compile(r"\[자료(\d+)\]")


@dataclass
class CitationCoverage:
    total: int                             # 제공한 자료 수
    cited_ids: List[int] = field(default_factory=list)     # 본문이 인용한 자료 번호(정렬·중복제거)
    uncited_ids: List[int] = field(default_factory=list)   # 제공했으나 인용 안 된 번호
    invalid_ids: List[int] = field(default_factory=list)   # 범위 밖(가짜) 인용 번호
    status: str = "none"                   # none | uncited | partial | full | invalid
    message: str = ""

    @property
    def n_cited(self) -> int:
        return len(self.cited_ids)


def citation_coverage(sources: List[str] | None, body: str) -> CitationCoverage:
    """근거자료 목록(1-기반)과 본문을 대조해 인용 커버리지를 집계."""
    total = len(sources or [])
    found = {int(n) for n in _CITE_RE.findall(body or "")}
    valid = sorted(n for n in found if 1 <= n <= total)
    invalid = sorted(n for n in found if not (1 <= n <= total))
    uncited = [i for i in range(1, total + 1) if i not in found]

    cov = CitationCoverage(total=total, cited_ids=valid, uncited_ids=uncited,
                           invalid_ids=invalid)

    if total == 0:
        if found:
            # 자료를 안 줬는데 본문이 [자료N]을 인용 — 전부 가짜 인용 신호.
            cov.status = "invalid"
            cov.invalid_ids = sorted(found)
            cov.message = (f"인용 오류 — 자료가 없는데 "
                           f"{', '.join('[자료%d]' % i for i in cov.invalid_ids)} 인용")
            return cov
        cov.status = "none"
        cov.message = "근거자료 없음 — 인용 점검 대상 아님"
        return cov
    if invalid:
        # 범위 밖 인용은 가짜 인용 신호(Execution 프롬프트가 금지하지만 방어적 점검).
        cov.status = "invalid"
        cov.message = (f"인용 오류 — 존재하지 않는 자료 번호 인용: "
                       f"{', '.join('[자료%d]' % i for i in invalid)}")
        return cov
    if not valid:
        cov.status = "uncited"
        cov.message = f"근거 미인용 — 자료 {total}개를 줬지만 본문에 [자료N] 인용이 없습니다"
        return cov
    if len(valid) == total:
        cov.status = "full"
        cov.message = f"인용 충실 — 자료 {total}개 모두 인용됨"
        return cov
    cov.status = "partial"
    cov.message = (f"부분 인용 — 자료 {total}개 중 {len(valid)}개 인용, "
                   f"미인용 {', '.join('[자료%d]' % i for i in uncited)}")
    return cov


# ── 무근거 실명 사례 탐지(결정적·LLM 0) ─────────────────────────────
# 라이브 관측(2026-07-24): 참고 자료 없는 과제에서 모델이 "MIT는 ~를 도입했다" 같은
# 실명 사례를 [출처?] 없이 확신조로 쓰는 사례 → 제출 전 사실 확인을 표면화한다.
# 라틴 고유명(3자 이상, 일반 기술어 제외)은 강한 신호, 구체 수치는 약한 신호(2문장 이상).
_LATIN_PROPER_RE = re.compile(r"[A-Z][A-Za-z]{2,}")
_GENERIC_LATIN = {"THE", "AND", "FOR", "WITH", "DRAFT", "TODO", "URL", "PDF",
                  "GPT", "LLM", "SNS", "APP", "WEB", "HTML", "JSON", "API",
                  "REASK", "DECISION"}
_NUM_CLAIM_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:%|퍼센트|년(?!\s*차)|억|만\s*명|명|배|개국|건)")


def unsourced_claim_sentences(body: str) -> List[str]:
    """[자료N]·[출처?] 없이 실명·수치 주장을 담은 문장 목록(결정적).

    참고 자료가 없는 초안의 제출 전 확인용 — 실명 라틴 고유명이 든 문장은 그대로,
    수치만 있는 문장은 2문장 이상일 때만 결과에 포함한다(연도 언급 등 일상 수치 오탐 완화).
    """
    if not body:
        return []
    # 결정 마커 내용은 본문 주장이 아니다 — 제거 후 판정.
    text = re.sub(r"\[\[DECISION:[^\]]*\]\]", " ", body)
    strong: List[str] = []
    weak: List[str] = []
    for raw in re.split(r"(?<=[.!?])\s+|\n+", text):
        s = raw.strip()
        if not s or "[자료" in s or "[출처?]" in s:
            continue
        latin = [t for t in _LATIN_PROPER_RE.findall(s)
                 if t.upper() not in _GENERIC_LATIN]
        if latin:
            strong.append(s)
        elif _NUM_CLAIM_RE.search(s):
            weak.append(s)
    return strong + (weak if len(weak) >= 2 else [])
