"""
완성도 점검(Self-Review) — 초안이 '경계선까지 충분히' 작성됐는지 AI가 스스로 점검한다.

리서치(docs/WRAPPER_STUDY.md)의 'completeness critic' 패턴. BoundaryGuard가 *형식*(경계선
위반)을 막는다면, 이 단계는 *내용*을 본다:
  1) 제공된 자료를 충분히 활용했는가(근거 누락),
  2) 자료로 채울 수 있었는데 게으르게 비운 곳은 없는가(gap),
  3) 남긴 [[DECISION]]이 진짜 '사람의 판단'인가, 아니면 채울 수 있는 걸 떠넘긴 건가.

LLM 1회 호출(검토는 생성보다 싸다). 결과는 사람에게 '점검 리포트'로 보여줄 뿐, 자동 수정은
하지 않는다 — 무엇을 더 채울지/넘길지는 사람이 본다. 경계선 철학 유지.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

from ..boundary.models import Draft
from ..llm.base import LLMClient, SourceDoc

# 종합 등급(사람이 읽는 라벨로 정규화).
_LEVELS = {"충분": "충분", "보완 권장": "보완 권장", "보완권장": "보완 권장", "부족": "부족"}


@dataclass
class ReviewReport:
    level: str = "보완 권장"        # 충분 | 보완 권장 | 부족
    coverage: str = ""             # 자료 활용 평가 한 줄
    gaps: List[str] = field(default_factory=list)   # 채울 수 있었는데 빠진 것
    decision_check: str = ""       # 결정 지점이 진짜 사람 판단인지 평가
    summary: str = ""              # 한 줄 총평


REVIEW_SYSTEM = """\
당신은 대학생을 돕는 'Until'의 **완성도 점검(Review)** 단계다.
앞 단계가 만든 '경계선 초안'을 받아, 제출 직전 관점에서 **내용이 충분한지** 점검한다.
당신은 초안을 고치지 않는다 — 무엇이 부족한지 짚어 주기만 한다.

[ 점검 항목 ]
1. 자료 활용(coverage): 제공된 참고 자료를 근거로 충분히 활용했는가. 핵심 자료를 빠뜨렸는가.
2. 빈 곳(gaps): 자료·상식으로 채울 수 있었는데 게으르게 비우거나 얕게 쓴 부분. 구체적으로.
3. 결정 점검(decision_check): 남긴 [[DECISION]]이 정말 '사람만 내릴 판단'인가, 아니면
   채울 수 있는 걸 떠넘긴 것인가. 떠넘김으로 의심되면 지적한다.

[ 종합 등급(level) ] — "충분" | "보완 권장" | "부족" 중 하나.

[ 출력 ] — 반드시 아래 JSON만(설명·코드펜스 없이). 현대 한국어만(외국 문자·외국어 단어 금지):
{"level":"충분|보완 권장|부족","coverage":"한 줄","gaps":["...","..."],"decision_check":"한 줄","summary":"한 줄 총평"}
"""


def review_user_message(spec_json: str, draft_body: str, sources_legend: List[str],
                        readiness_lines: Optional[List[str]] = None) -> str:
    legend = "\n".join(f"[자료{i}] {t}" for i, t in enumerate(sources_legend or [], 1)) or "(없음)"
    # 결정적 사전 점검(마감·분량·인용·결정)을 근거로 제시 — 모델이 사실에 기반해 판단하도록.
    pre = ""
    if readiness_lines:
        pre = ("[ 결정적 사전 점검(참고 사실) ]\n"
               + "\n".join(readiness_lines)
               + "\n(위는 코드가 계산한 사실이다. 이를 반영해 판단하되, 단순 반복하지 말고 "
                 "무엇을 더 채우거나 넘길지 구체적으로 짚어라.)\n\n")
    return (
        f"[ 과제 명세(JSON) ]\n{spec_json}\n\n"
        f"[ 제공된 참고 자료 목록 ]\n{legend}\n\n"
        f"{pre}"
        f"[ 점검할 초안 ]\n{draft_body}\n\n"
        "위 초안을 점검해 JSON으로만 평가하라. gaps는 '채울 수 있었는데 빠진' 것만, 구체적으로."
    )


def parse_review(text: str) -> ReviewReport:
    """모델 출력(JSON)을 ReviewReport로. 깨진 출력은 안전한 기본값."""
    data = None
    try:
        m = re.search(r"\{.*\}", text or "", re.DOTALL)
        data = json.loads(m.group(0)) if m else json.loads(text)
    except Exception:
        return ReviewReport(summary="(점검 결과를 해석하지 못했습니다.)")
    if not isinstance(data, dict):
        return ReviewReport(summary="(점검 결과 형식이 올바르지 않습니다.)")
    raw_gaps = data.get("gaps") or []
    gaps = [str(g).strip() for g in raw_gaps if str(g).strip()][:6] if isinstance(raw_gaps, list) else []
    level = _LEVELS.get(str(data.get("level") or "").strip().replace(" ", ""), None) \
        or _LEVELS.get(str(data.get("level") or "").strip(), "보완 권장")
    return ReviewReport(
        level=level,
        coverage=str(data.get("coverage") or "").strip()[:300],
        gaps=gaps,
        decision_check=str(data.get("decision_check") or "").strip()[:300],
        summary=str(data.get("summary") or "").strip()[:300],
    )


def review_draft(draft: Draft, spec: dict, llm: LLMClient, *,
                 context_sources: Optional[List[SourceDoc]] = None,
                 sources_legend: Optional[List[str]] = None,
                 readiness_lines: Optional[List[str]] = None) -> ReviewReport:
    """초안 1건을 LLM으로 점검해 ReviewReport를 만든다.

    readiness_lines가 주어지면 결정적 사전 점검(마감·분량·인용)을 근거로 함께 넣어
    AI 점검이 사실에 기반하게 한다.
    """
    spec_json = json.dumps(spec, ensure_ascii=False)
    user = review_user_message(spec_json, draft.body, sources_legend or [], readiness_lines)
    res = llm.complete(REVIEW_SYSTEM, user, tag="review", json=True, documents=context_sources)
    return parse_review(res.text)
