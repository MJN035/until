"""
관련 파일 검색 — '내가 가진 관련 파일 확인' + '수업자료 불러오기'의 구현.

폴더(내 파일 / 수업자료)에서 과제 키워드와 관련 있는 파일을 점수화해 상위 N개를 고른다.
sentence-transformers가 있으면 임베딩 유사도를 사용하고, 없거나 로딩 실패 시 기존
키워드 중첩 점수로 폴백한다. 두 경로 모두 LLM 호출은 하지 않는다.
"""
from __future__ import annotations
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Protocol, Sequence

from ..capture.ingest import ingest_file
from ..capture.models import Document

_WORD = re.compile(r"[가-힣A-Za-z]{2,}")
_DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_EMBEDDER = None
_EMBEDDER_ATTEMPTED = False


def keywords_from_spec(spec: dict) -> List[str]:
    """TaskSpec(goal/requirements)에서 검색 키워드 추출."""
    blob = " ".join([
        str(spec.get("goal", "")),
        " ".join(spec.get("requirements", []) or []),
        str(spec.get("deliverable", "")),
    ])
    seen, out = set(), []
    for w in _WORD.findall(blob):
        if len(w) >= 2 and w not in seen:
            seen.add(w); out.append(w)
    return out


@dataclass
class Hit:
    document: Document
    score: float
    matched: List[str]


class Embedder(Protocol):
    def encode(self, texts: Sequence[str]):
        ...


def _keyword_hit(doc: Document, keywords: List[str]) -> tuple[float, List[str]]:
    text = doc.text.lower()
    matched = [w for w in keywords if w in text]
    return float(sum(text.count(w) for w in matched)), matched


def _cosine(a, b) -> float:
    aa = [float(x) for x in a]
    bb = [float(x) for x in b]
    denom = math.sqrt(sum(x * x for x in aa)) * math.sqrt(sum(x * x for x in bb))
    if denom == 0:
        return 0.0
    return sum(x * y for x, y in zip(aa, bb, strict=True)) / denom


def _load_embedder():
    global _EMBEDDER, _EMBEDDER_ATTEMPTED
    if _EMBEDDER_ATTEMPTED:
        return _EMBEDDER
    _EMBEDDER_ATTEMPTED = True
    try:
        from sentence_transformers import SentenceTransformer
        model_name = os.getenv("UNTIL_EMBEDDING_MODEL", _DEFAULT_EMBEDDING_MODEL)
        _EMBEDDER = SentenceTransformer(model_name)
    except Exception:
        _EMBEDDER = None
    return _EMBEDDER


def _embedding_hits(docs: List[Document], keywords: List[str], embedder: Embedder) -> List[Hit]:
    query = " ".join(keywords)
    texts = [query] + [d.text[:4000] for d in docs]
    vectors = embedder.encode(texts)
    query_vec = vectors[0]
    hits: List[Hit] = []
    for doc, vec in zip(docs, vectors[1:], strict=True):
        keyword_score, matched = _keyword_hit(doc, keywords)
        similarity = _cosine(query_vec, vec)
        if similarity <= 0 and keyword_score <= 0:
            continue
        score = round(similarity * 100, 4) + keyword_score
        hits.append(Hit(document=doc, score=score, matched=matched or ["embedding"]))
    return hits


def find_relevant(
    folder: str,
    keywords: List[str],
    k: int = 3,
    *,
    use_embeddings: bool = True,
    embedder: Embedder | None = None,
) -> List[Hit]:
    p = Path(folder)
    if not p.exists() or not keywords:
        return []
    kw = [w.lower() for w in keywords]
    docs: List[Document] = []
    for f in sorted(p.glob("**/*")):
        if not f.is_file() or f.suffix.lower() not in (".txt", ".md", ".pdf"):
            continue
        try:
            docs.append(ingest_file(f))
        except Exception:
            continue
    if not docs:
        return []

    if use_embeddings:
        active_embedder = embedder or _load_embedder()
        if active_embedder is not None:
            try:
                hits = _embedding_hits(docs, kw, active_embedder)
                hits.sort(key=lambda h: h.score, reverse=True)
                return hits[:k]
            except Exception:
                pass

    hits: List[Hit] = []
    for doc in docs:
        score, matched = _keyword_hit(doc, kw)
        if score > 0:
            hits.append(Hit(document=doc, score=score, matched=matched))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:k]
