"""Normalized document model produced by the no-token Capture (파싱) layer."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List


@dataclass
class Section:
    heading: str
    text: str


@dataclass
class Document:
    """A single ingested source, normalized. Contains NO LLM-derived data."""
    source: str                       # file path or label
    kind: str                         # "pdf" | "text" | "markdown"
    text: str                         # full extracted plain text
    sections: List[Section] = field(default_factory=list)
    n_chars: int = 0
    n_tokens_est: int = 0             # rough estimate (chars/4) — for cost preview, not billed

    def preview(self, n: int = 280) -> str:
        return (self.text[:n] + "…") if len(self.text) > n else self.text
