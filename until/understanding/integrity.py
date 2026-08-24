"""대필 금지 신호 감지 — Draft 경계선의 '규정' 버전 (결정적, LLM 0).

실코퍼스 근거(docs/planning/type_algorithms.md T4): 물리학 숙제가 "종이에 작성한
답안의 스캔이나 사진, 또는 손글씨를 그대로(폰트 변환 금지)"를 명시 — 대필 산출물
제출을 규정으로 차단한 유형. 사람의 고유 '판단'뿐 아니라 사람이 해야 한다고
'규정된 것'도 넘지 않는다. 감지 시 파이프라인은 최종 답안 생성을 학습 보조 모드
(개념 정리·유사 예제 시연·검산 체크리스트)로 강등한다.

오탐 주의: '사진'(조별 활동사진)·'스캔'(단순 업로드 절차)·'자필 서명'(서약서)은
자필 '답안' 요구가 아니다 — 신호를 좁게 잡는다. 넓히면 에세이("본인이 직접
작성하세요")까지 게이트돼 제품이 망가진다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class IntegrityGate:
    snippet: str   # 매치된 원문 조각(근거 인용)
    reason: str    # 사람이 읽는 한 줄


_SIGNALS = [
    re.compile(r"손\s*글씨"),
    # '자필 서명'·'자필로 서명'(서약서)은 답안 규정이 아니다 — 조사 변형까지 제외.
    re.compile(r"자필(?!\s*(?:로(?:써)?\s*)?서명)"),
    re.compile(r"수기로\s*(작성|기록|풀)"),
    re.compile(r"손으로\s*(작성|쓴|풀|직접\s*푼)"),
    re.compile(r"종이에\s*(작성|풀|쓴)[^\n]{0,20}답안"),
    re.compile(r"답안[^\n]{0,20}종이에\s*(작성|풀|쓴)"),
    re.compile(r"폰트\s*변환[^\n]{0,15}(안\s*됩|안됨|금지|불가|마세요)"),
]


def _texts(spec: dict, docs) -> list:
    parts = [
        str(spec.get("goal") or ""),
        str(spec.get("deliverable") or ""),
        " ".join(str(r) for r in (spec.get("requirements") or [])),
        " ".join(str(c) for c in (spec.get("constraints") or [])),
    ]
    for d in docs or []:
        parts.append((getattr(d, "text", "") or "")[:4000])
    return parts


def detect_no_ghostwriting(spec: dict, docs=None) -> Optional[IntegrityGate]:
    """spec·원문에서 자필 제출 규정 신호를 찾는다. 없으면 None."""
    for text in _texts(spec or {}, docs):
        for rx in _SIGNALS:
            m = rx.search(text)
            if m:
                lo = max(0, m.start() - 15)
                snippet = text[lo:m.end() + 25].strip().replace("\n", " ")
                return IntegrityGate(
                    snippet=snippet,
                    reason=f"자필 제출 규정 감지 — \"{snippet}\"",
                )
    return None
