"""과제 지시문의 명시적 AI 사용 금지 — LLM 호출 전 결정적 하드 게이트."""
from __future__ import annotations

import re
from typing import Iterable


class AiUseProhibitedError(ValueError):
    """교수자 지시가 AI 사용을 금지해 Until이 초안을 만들 수 없음."""


_PROHIBITED = re.compile(
    r"(?:AI|인공지능|생성형\s*AI|ChatGPT|챗GPT|챗지피티)"
    r"\s*(?:의\s*)?(?:사용\s*)?(?:여부\s*[:：]?\s*)?"
    r"(?:불가능|불가|금지|허용하지\s*않|사용하지\s*말|쓰지\s*말)"
    r"|(?:AI|인공지능|생성형\s*AI|ChatGPT|챗GPT|챗지피티)\s*(?:를|을)?\s*"
    r"(?:사용하면\s*안|사용해선\s*안|사용해서는\s*안)",
    re.IGNORECASE,
)


def ai_use_prohibited(documents: Iterable[object]) -> bool:
    """문서 원문에 명시적인 AI 금지 표현이 있으면 True."""
    text = "\n".join(str(getattr(doc, "text", "") or "") for doc in documents)
    return bool(_PROHIBITED.search(text))


def enforce_ai_use_policy(documents: Iterable[object]) -> None:
    if ai_use_prohibited(documents):
        raise AiUseProhibitedError(
            "이 과제는 AI 사용을 명시적으로 금지합니다. Until은 초안이나 답안을 "
            "생성하지 않습니다. 과제 지시를 직접 따라 작성하세요."
        )
