"""
민감·고위험 상황 탐지 — 사과·거절·갈등은 자동 확정 금지, 사람 승인 대기.

Draft 경계선의 확장이다. 기존 경계선은 "이 **판단**은 사람 몫"을 가르지만, 여기는
"이 **글 전체**가 사람 눈을 한 번 더 거쳐야 한다"를 가른다. 사과문이 잘못 나가면
되돌릴 수 없고, 거절·갈등 상황의 문장 하나가 관계를 바꾼다. 초안 생성 자체는
막지 않는다 — 막으면 제품이 쓸모없어진다. 대신 **자동 확정·자동 제출만** 막는다.

전부 결정적(LLM 0)이다. 판정 근거를 사람이 읽을 수 있어야 승인 화면이 의미를
갖기 때문이다("왜 승인이 필요한가"를 못 보여주면 사용자는 그냥 누르고 만다).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

#: 상황 종류 — 자유 문자열 금지(텔레메트리 열거형 등재와 승인 UI 문구가 여기 묶인다).
SENSITIVE_KINDS = ("사과", "거절", "갈등")

_PATTERNS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("사과", (
        r"사과(문|드립|합니다|의\s*말씀)", r"죄송(합니다|스럽|한\s*말씀)",
        r"송구(합니다|스럽)", r"유감(을\s*표|스럽)", r"잘못(을\s*인정|에\s*대해)",
        r"불미스러운", r"재발\s*방지",
    )),
    ("거절", (
        r"거절(합니다|하고|의\s*뜻)", r"반려", r"수락(하기\s*어렵|할\s*수\s*없)",
        r"참여(가\s*어렵|하기\s*어렵|할\s*수\s*없)", r"불참",
        r"철회(합니다|하고자)", r"사퇴", r"포기(합니다|하고자)",
        r"정중히\s*거(절|둘)", r"어렵겠습니다",
    )),
    ("갈등", (
        r"이의\s*(를\s*)?제기", r"항의", r"불만(을\s*제기|사항)", r"부당(하|합니다)",
        r"재심(사|의)", r"이의신청", r"분쟁", r"고충", r"문제\s*제기",
        r"정정\s*요청", r"시정\s*요구",
    )),
)
_COMPILED = tuple((kind, tuple(re.compile(p) for p in pats))
                  for kind, pats in _PATTERNS)

#: 신호가 이만큼 잡혀야 '그 상황의 글'로 본다. 1건이면 스쳐 지나가는 언급일 수 있다.
MIN_HITS = 2


@dataclass(frozen=True)
class SensitiveFinding:
    kind: str                       # SENSITIVE_KINDS 중 하나
    hits: int
    evidence: Tuple[str, ...] = ()  # 사람이 읽을 근거(매칭된 표현)

    @property
    def message(self) -> str:
        sample = ", ".join(f"'{e}'" for e in self.evidence[:3])
        return (f"{self.kind} 성격의 글로 보입니다({sample} 등 {self.hits}곳). "
                "되돌리기 어려운 종류라 자동 확정 없이 직접 읽고 확인해 주세요.")


@dataclass
class SensitiveReport:
    findings: List[SensitiveFinding] = field(default_factory=list)

    @property
    def needs_approval(self) -> bool:
        return bool(self.findings)

    @property
    def kinds(self) -> List[str]:
        return [f.kind for f in self.findings]

    @property
    def headline(self) -> str:
        if not self.findings:
            return ""
        return " · ".join(f.kind for f in self.findings) + " — 사람 확인 필요"

    def to_dict(self) -> dict:
        return {"needs_approval": self.needs_approval,
                "kinds": self.kinds,
                "messages": [f.message for f in self.findings]}


def _scan(text: str) -> List[SensitiveFinding]:
    findings: List[SensitiveFinding] = []
    for kind, patterns in _COMPILED:
        matched: List[str] = []
        for pat in patterns:
            for m in pat.finditer(text):
                token = m.group().strip()
                if token and token not in matched:
                    matched.append(token)
        if len(matched) >= MIN_HITS:
            findings.append(SensitiveFinding(kind=kind, hits=len(matched),
                                             evidence=tuple(matched[:6])))
    return findings


def detect_sensitive(spec: Optional[dict] = None,
                     documents: Optional[Sequence[object]] = None,
                     body: str = "") -> SensitiveReport:
    """과제 명세·원문·생성 본문에서 고위험 상황을 찾는다. 없으면 빈 리포트.

    셋을 모두 보는 이유: 과제 지시문에만 드러나는 경우(사과문을 쓰라는 과제)와
    본문에서만 드러나는 경우(모델이 거절 어조로 써 버린 경우)가 둘 다 실재한다.
    """
    parts: List[str] = []
    spec = spec or {}
    parts.append(str(spec.get("goal") or ""))
    parts.append(str(spec.get("deliverable") or ""))
    reqs = spec.get("requirements")
    if isinstance(reqs, list):
        parts += [str(r) for r in reqs]
    for d in documents or ():
        parts.append((getattr(d, "text", "") or "")[:4000])
    parts.append(str(body or ""))
    return SensitiveReport(findings=_scan("\n".join(p for p in parts if p)))
