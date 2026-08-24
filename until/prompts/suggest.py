"""
'뭐라고 프롬프트 시킬지 알려주는' 모듈 — 교육 모드.

각 결정 지점마다, 사용자가 그 자리를 직접 채우려 할 때 모델에 던지면 좋은
프롬프트를 제안한다. 단순 문장이 아니라 **서로 다른 프롬프트 기법**을 번갈아 보여주고,
"왜 이 기법이 좋은지"까지 설명해 **AI 공부와 병행**되도록 한다.

LLM 호출 0 — 전부 결정적(템플릿). (capture/context/boundary/prompts LLM-0 원칙)
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, List, Tuple

from ..boundary.models import Draft


@dataclass
class PromptSuggestion:
    """제안 프롬프트 1건 — 텍스트 + 어떤 기법인지(pattern) + 왜 좋은지(why, 교육용)."""
    text: str
    pattern: str
    why: str


# 결정마다 번갈아 적용할 프롬프트 기법들 — 각자 다른 프롬프트 엔지니어링 패턴을 가르친다.
# (pattern 이름, why=왜 좋은지 한 줄, build=결정 노트로 프롬프트 문장 생성)
_PATTERNS: List[Tuple[str, str, Callable[[str], str]]] = [
    (
        "기준 분해 + 비교표",
        "막연한 선택을 '평가 기준'으로 쪼개면 비교 가능한 문제로 바뀐다. 표로 요구하면 "
        "답이 구조화돼 한눈에 들어온다.",
        lambda note: (
            f"나는 이 결정을 내려야 한다: \"{note}\". "
            "이 선택을 좌우할 핵심 평가 기준 3가지를 정하고, 각 선택지를 그 기준으로 "
            "비교하는 표를 만들어줘. 어느 쪽을 고를지는 내가 정할 테니 결론은 강요하지 마."
        ),
    ),
    (
        "역할 부여 + 반론(스틸맨)",
        "모델에 '비판자' 역할을 주면 한쪽으로 치우치지 않는다. 내 잠정 입장의 가장 강한 "
        "반론을 먼저 들으면 약점을 미리 메울 수 있다.",
        lambda note: (
            f"이 결정에서 내 잠정 입장을 말한다: \"{note}\". "
            "너는 깐깐한 비판자 역할을 맡아, 내 입장에 대한 '가장 설득력 있는 반론' 2가지와 "
            "그 반론을 넘어설 보강 논거를 제시해줘."
        ),
    ),
    (
        "단계적 사고(생각의 사슬)",
        "'단계적으로 생각하라'고 명시하면 모델이 근거→중간결론→최종을 분리해 보여줘서, "
        "어디서 판단이 갈리는지 짚을 수 있다.",
        lambda note: (
            f"다음 결정을 단계적으로 풀어줘: \"{note}\". "
            "①관련 사실 정리 → ②가능한 선택지 나열 → ③각 선택지의 근거와 위험, "
            "이 순서로 '생각 과정'을 보여주되 최종 선택은 비워 두고 내가 정하게 해줘."
        ),
    ),
    (
        "예시 주도(few-shot) 요청",
        "원하는 출력의 '예시 형식'을 함께 요구하면 결과 품질이 크게 오른다. 좋은 답이 "
        "어떤 모양인지 모델과 맞추는 기법.",
        lambda note: (
            f"이 결정을 도와줘: \"{note}\". "
            "먼저 '좋은 답안 1개'의 예시를 짧게 보여주고, 같은 형식으로 서로 다른 방향의 "
            "후보 2개를 더 만들어줘. 선택은 내가 한다."
        ),
    ),
]


def suggest_prompts_detailed(draft: Draft) -> List[PromptSuggestion]:
    """결정마다 기법을 번갈아 적용한 제안(텍스트+기법명+이유)을 만든다."""
    out: List[PromptSuggestion] = []
    for i, d in enumerate(draft.decisions):
        name, why, build = _PATTERNS[i % len(_PATTERNS)]
        out.append(PromptSuggestion(text=build(d.note), pattern=name, why=why))
    return out


def suggest_prompts(draft: Draft) -> List[str]:
    """하위 호환 — 프롬프트 문장 목록만(리포트·기존 호출부에서 사용)."""
    return [s.text for s in suggest_prompts_detailed(draft)]
