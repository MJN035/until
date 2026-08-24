"""Focused draft revision with the same BoundaryGuard safety boundary."""
from __future__ import annotations

import json
from typing import List

from ..boundary.models import Draft
from ..llm.base import LLMClient, SourceDoc
from . import prompts
from .boundary_guard import BoundaryGuard, BoundaryValidator, OnFailAction


def revise_draft(draft: Draft, spec: dict, instruction: str, llm: LLMClient, *,
                 source_docs: List[SourceDoc], excluded: set[int] | None = None,
                 max_reasks: int = 2, voice_hint: str = ""):
    """Revise only what the user requested while preserving decisions and citations."""
    excluded = excluded or set()
    docs = []
    for index, source in enumerate(source_docs, 1):
        text = ("[사용자가 이 자료를 제외함 — 근거로 사용하지 말 것]"
                if index in excluded else source.text)
        docs.append(SourceDoc(title=source.title, text=text, url=getattr(source, "url", "")))
    system = (prompts.SYSTEM + "\n\n"
              "당신은 기존 초안의 부분 수정자다. 사용자가 요청한 부분만 고치고 나머지 "
              "문단·[[DECISION]]·[자료N] 번호는 보존하라. 제외 자료는 인용하거나 근거로 "
              "사용하지 말라. 새로운 개인 경험이나 가치판단을 만들지 말라.")
    if voice_hint:
        # 부분 수정도 초안과 같은 레지스터로 — 고친 문단만 말투가 튀면 안 된다.
        system += "\n\n" + voice_hint
    base = (f"과제 명세(JSON):\n{json.dumps(spec, ensure_ascii=False)}\n\n"
            f"수정 지시:\n{instruction.strip()}\n\n기존 초안:\n{draft.body}")

    def produce(errors, previous):
        user = base if not errors else base + "\n\n" + prompts.reask_message(previous, errors)
        return llm.complete(system, user, tag="execution", documents=docs).text

    guard = BoundaryGuard(
        validators=[BoundaryValidator(min_decisions=draft.n_decisions)],
        on_fail=OnFailAction.REASK, max_reasks=max_reasks)
    return guard.run(produce)
