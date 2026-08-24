"""
The LLM wrapper — thin and fully readable.

complete() 시그니처는 모든 백엔드가 공유한다. 새 인자:
  - schema:    구조화 출력(Structured Outputs)용 JSON 스키마. 있으면 모델 출력이 스키마에 강제됨.
  - documents: 인용 소스 문서들(SourceDoc[]). 있으면 Citations + prompt caching 대상이 됨.
  - cache:     prompt caching 사용 여부.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Protocol


@dataclass
class SourceDoc:
    """인용 소스 1건 (Anthropic document content block로 변환됨)."""
    title: str
    text: str
    url: str = ""  # 출처 원본 위치(eTL 자료/공지 등). 있으면 본문 [자료N]을 링크로 만든다.


@dataclass
class Citation:
    """모델 응답이 가리킨 출처 한 조각."""
    cited_text: str
    doc_title: str
    location: str = ""


@dataclass
class LLMResult:
    text: str
    backend: str
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0          # 캐시에서 읽은 토큰 (prompt caching 효과)
    citations: List[Citation] = field(default_factory=list)
    # 실제로 응답한 모델 식별자. 라이브 운영은 Cerebras→Kimi→Gemini→Groq 폴백
    # 사슬이라 설정값(Config.model)과 실제 응답자가 다를 수 있다. 이 값이 없으면
    # "톤이 바뀐 게 모델 때문인지 프롬프트 때문인지"를 사후에 가릴 수 없다.
    model: str = ""
    # 주 제공자가 실패해 백업 제공자/모델이 답했는가와 그 사유(열거형).
    # 이 신호가 없으면 주 제공자가 몇 주째 죽어 있어도 아무도 모른다.
    degraded: bool = False
    degrade_reason: str = ""


class LLMClient(Protocol):
    def complete(
        self, system: str, user: str, *,
        tag: str = "", json: bool = False,
        schema: Optional[dict] = None,
        documents: Optional[List[SourceDoc]] = None,
        cache: bool = True,
    ) -> LLMResult: ...


def build_client(backend: str, model: str) -> LLMClient:
    if backend == "anthropic":
        from .anthropic_client import AnthropicClient
        return AnthropicClient(model=model)
    if backend in ("local", "ollama", "openai-compat"):
        from .openai_compat import OpenAICompatClient
        return OpenAICompatClient(model=model)
    if backend == "mock":
        from .mock_client import MockClient
        return MockClient()
    raise ValueError(f"unknown backend: {backend!r}")
