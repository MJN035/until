"""
The 'Draft 경계선' data model — where the agent stops and the human takes over.

결정 지점 해소(resolution) 스키마는 LangGraph의 Human-in-the-Loop interrupt
패턴(approve / edit / reject / respond)을 차용했다. 출처:
https://docs.langchain.com/oss/python/langchain/human-in-the-loop
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional
import re

# 엄격 파서: [[DECISION: 내용]] — 내용 캡처.
_DECISION_RE = re.compile(r"\[\[DECISION:\s*(.*?)\]\]", re.DOTALL)
# 자리표시/빈 내용으로 간주해 거부할 패턴.
_PLACEHOLDER = {"", "...", "…", "todo", "tbd", "n/a", "내용", "결정"}


class Resolution(str, Enum):
    """사람이 결정 지점을 처리하는 4가지 방식 (LangGraph HITL 차용)."""
    APPROVE = "approve"   # 에이전트가 제시한 후보안을 그대로 채택
    EDIT = "edit"         # 후보안을 수정해 채택
    REJECT = "reject"     # 거부 + 사유 → 에이전트가 다시 작업
    RESPOND = "respond"   # 사람이 직접 답을 채워 넣음


@dataclass
class DecisionPoint:
    """판단이 필요한 지점. 에이전트는 이 선을 넘지 않는다."""
    note: str
    context: str = ""
    resolution: Optional[Resolution] = None
    human_input: str = ""

    def resolve(self, how: Resolution, human_input: str = "") -> None:
        self.resolution = how
        self.human_input = human_input

    @property
    def is_placeholder(self) -> bool:
        return self.note.strip().lower() in _PLACEHOLDER or len(self.note.strip()) < 5


@dataclass
class Draft:
    body: str
    decisions: List[DecisionPoint] = field(default_factory=list)

    @classmethod
    def from_text(cls, text: str) -> "Draft":
        """[[DECISION: ...]] 마커를 엄격 파싱. 자리표시 마커는 제외."""
        decisions: List[DecisionPoint] = []
        for raw in _DECISION_RE.findall(text):
            dp = DecisionPoint(note=raw.strip())
            if not dp.is_placeholder:
                decisions.append(dp)
        return cls(body=text, decisions=decisions)

    @property
    def n_decisions(self) -> int:
        return len(self.decisions)

    @property
    def crossed_boundary(self) -> bool:
        """결정 지점이 0개 = 에이전트가 모든 판단을 스스로 해버렸을 위험."""
        return self.n_decisions == 0
