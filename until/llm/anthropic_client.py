"""Live backend — Anthropic Messages API with citations, caching, structured outputs."""
from __future__ import annotations
from typing import List, Optional
from .base import LLMResult, SourceDoc
from .request_builder import build_request, parse_citations


class AnthropicClient:
    def __init__(self, model: str, max_tokens: int = 2048):
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("pip install anthropic 필요") from e
        self._client = anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens

    def complete(
        self, system: str, user: str, *,
        tag: str = "", json: bool = False,
        schema: Optional[dict] = None,
        documents: Optional[List[SourceDoc]] = None,
        cache: bool = True,
    ) -> LLMResult:
        if json and schema is None:
            system = system + "\n\n반드시 유효한 JSON 객체만 출력하세요."
        req = build_request(
            self.model, system, user, self.max_tokens,
            schema=schema, documents=documents, cache=cache,
        )
        msg = self._client.messages.create(**req)
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        usage = msg.usage
        return LLMResult(
            text=text.strip(),
            backend="anthropic",
            tokens_in=getattr(usage, "input_tokens", 0),
            tokens_out=getattr(usage, "output_tokens", 0),
            cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
            citations=parse_citations(msg.content),
            model=str(getattr(msg, "model", "") or self.model),
        )
