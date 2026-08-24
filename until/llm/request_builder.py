"""
순수 함수로 Anthropic Messages 요청 본문을 구성한다. SDK 없이 단위 테스트 가능.

적용 기술 (출처):
  - Citations:        https://platform.claude.com/docs/en/build-with-claude/citations
  - Prompt caching:   https://platform.claude.com/docs/en/build-with-claude/prompt-caching
  - Structured out.:  https://platform.claude.com/docs/en/build-with-claude/structured-outputs
"""
from __future__ import annotations
from typing import List, Optional
from .base import SourceDoc


def build_user_content(user: str, documents: Optional[List[SourceDoc]], cache: bool) -> list:
    """
    user 텍스트 + (선택)document 블록들로 content 배열 구성.
    - 각 document는 citations.enabled=True → 응답이 원문 span을 인용.
    - 마지막 document에 cache_control(ephemeral) → 자료 prefix를 캐싱(반복 호출/ reask에서 절감).
    """
    content: list = []
    docs = documents or []
    for i, d in enumerate(docs):
        block = {
            "type": "document",
            "source": {"type": "text", "media_type": "text/plain", "data": d.text},
            "title": d.title,
            "citations": {"enabled": True},
        }
        if cache and i == len(docs) - 1:
            block["cache_control"] = {"type": "ephemeral"}
        content.append(block)
    content.append({"type": "text", "text": user})
    return content


def build_request(
    model: str, system: str, user: str, max_tokens: int, *,
    schema: Optional[dict] = None,
    documents: Optional[List[SourceDoc]] = None,
    cache: bool = True,
) -> dict:
    # system은 cache_control 가능한 블록 형태로(재사용되는 긴 지시문 캐싱).
    system_blocks = [{"type": "text", "text": system}]
    if cache:
        system_blocks[0]["cache_control"] = {"type": "ephemeral"}

    req: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_blocks,
        "messages": [
            {"role": "user", "content": build_user_content(user, documents, cache)}
        ],
    }
    if schema is not None:
        # Structured Outputs: 출력이 JSON 스키마를 따르도록 강제.
        req["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
    return req


def parse_citations(content_blocks) -> list:
    """응답 content 블록에서 citation을 추출 → list[Citation]."""
    from .base import Citation
    out = []
    for block in content_blocks or []:
        cits = getattr(block, "citations", None)
        if not cits:
            continue
        for c in cits:
            out.append(Citation(
                cited_text=getattr(c, "cited_text", ""),
                doc_title=getattr(c, "document_title", "") or "",
                location=str(getattr(c, "start_char_index", "")),
            ))
    return out
