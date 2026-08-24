"""결정 지점 '왜 당신 몫인지' 분류 — 결정적(LLM 0).

Until의 핵심은 '자료로 채울 수 있는 건 끝까지, 사람 고유 판단은 넘기지 않는다'이다.
각 [[DECISION]]이 *왜* 사람의 몫인지를 한 줄로 밝히면, 학생은 그 자리가 게으른 떠넘김이
아니라 '당신이어야 하는 순간'임을 이해하고 스스로 채운다. 경계선 개념을 UI에서 강화.

키워드 기반 결정적 분류(카테고리 + 한 줄 근거). boundary 패키지 = LLM 0.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List

# (카테고리, 근거 한 줄, 신호 키워드들). 위에서부터 먼저 맞는 것을 채택.
_CATEGORIES: List[tuple] = [
    ("가치판단", "옳고 그름·중요도의 기준은 당신의 가치관에서 나옵니다.",
     ["가치", "옳", "윤리", "도덕", "정당", "바람직", "중요하다고", "우선순위", "trade-off", "타협"]),
    ("관점·논지", "어느 편에 설지는 근거만으로 정해지지 않는 당신의 입장입니다.",
     ["관점", "논지", "입장", "주장", "견해", "핵심 논지", "어느 것을", "어느 쪽", "찬성", "반대", "톤"]),
    ("진로·경험", "당신의 경험과 진로에 연결되는 부분이라 대신 정할 수 없습니다.",
     ["진로", "경험", "본인", "자신의", "나의", "개인", "장래", "목표", "동기"]),
    ("취향·스타일", "정답이 없는 표현·구성의 선택이라 당신 취향이 기준입니다.",
     ["취향", "스타일", "어조", "표현", "제목", "구성", "형식", "느낌", "톤앤", "디자인"]),
    ("범위·선택", "무엇을 넣고 뺄지는 당신의 목적에 따라 달라지는 선택입니다.",
     ["범위", "무엇을", "선택", "고를", "다룰", "포함", "제외", "우선", "강조", "focus"]),
]

_DEFAULT = ("고유 판단", "자료로 답이 정해지지 않는, 당신이 정해야 하는 지점입니다.")


@dataclass
class DecisionRationale:
    category: str       # 가치판단 | 관점·논지 | 진로·경험 | 취향·스타일 | 범위·선택 | 고유 판단
    why: str            # 왜 사람의 몫인지 한 줄


def classify_decision(note: str) -> DecisionRationale:
    """결정 노트 한 줄을 카테고리+근거로 분류. 신호 없으면 기본(고유 판단)."""
    text = (note or "").lower()
    for cat, why, kws in _CATEGORIES:
        if any(kw.lower() in text for kw in kws):
            return DecisionRationale(category=cat, why=why)
    return DecisionRationale(category=_DEFAULT[0], why=_DEFAULT[1])
