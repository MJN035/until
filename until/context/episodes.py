"""
L2 에피소드 기억 — 과거 (입력 상황, 생성 초안, 최종 발송본) 3종 세트.

L1(스타일 카드)이 "이 사람은 대체로 어떻게 쓰는가"라면, L2는 "**이런 상황에서는**
이렇게 썼다"이다. 그래서 통짜 요약이 아니라 **유사 사례 검색**이어야 한다 —
새 요청이 오면 비슷했던 과거 건 3~5개를 찾아 few-shot 예시로 넣는다.

검색은 `context/retrieval.py`와 같은 방식이다: sentence-transformers가 있으면
임베딩 코사인, 없으면 내용어 중첩으로 폴백. 둘 다 **LLM 호출 0**이다.

주입 예시는 `final_output`(사람이 실제로 확정한 글)을 우선한다. 초안은 모델이 쓴
글이라 그걸 예시로 주면 모델이 자기 문체를 다시 학습한다(에코 챔버).

저장은 JSONL 한 줄 = 에피소드 하나. 원문이 담기므로 **원문 파이프**(사용자 소유,
비식별 텔레메트리로 절대 나가지 않음)에 속한다.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading as _threading
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

STORE_VERSION = 1
EPISODES_PATH = Path("_until_work/episodes.jsonl")

#: 보관 상한(오래된 것부터 버림) — 파일 무한 증식 방지.
MAX_KEEP = 200
#: 필드별 저장 상한 — 에피소드는 '예시'지 아카이브가 아니다.
MAX_CONTEXT_CHARS = 1200
MAX_BODY_CHARS = 4000
#: 프롬프트에 넣을 발췌 상한(건당) — few-shot이 본문을 밀어내지 않게.
EXCERPT_CHARS = 700
DEFAULT_K = 4

_WORD = re.compile(r"[가-힣A-Za-z0-9]{2,}")
_STOP = set(
    "그리고 그러나 하지만 그래서 또한 그런데 이런 저런 그런 어떤 무슨 너무 정말 "
    "작성 제출 과제 내용 대하여 대해 있다 없다 한다 위해 통해 경우 다음 이번".split())


@dataclass
class Episode:
    episode_id: str
    input_context: str          # 그때의 과제 상황(명세 요지)
    generated_draft: str        # Until이 만든 초안
    final_output: str           # 사람이 확정한 최종본(없으면 "")
    register_key: str = ""
    task_type: str = ""
    actor_id: str = "local"
    created_at: str = ""

    @property
    def example_body(self) -> str:
        """few-shot으로 쓸 본문 — 최종본 우선, 없으면 초안."""
        return self.final_output or self.generated_draft


@dataclass
class EpisodeHit:
    episode: Episode
    score: float
    matched: List[str] = field(default_factory=list)


# ── 경로(요청 스코프 오버라이드 — answer_history/profile과 같은 패턴) ──

_TL_PATH = _threading.local()


def set_episodes_path_override(p: Optional[Path]) -> None:
    _TL_PATH.value = p


def _resolve_path(path: Optional[Path]) -> Path:
    if path is not None:
        return Path(path)
    o = getattr(_TL_PATH, "value", None)
    return Path(o) if o is not None else EPISODES_PATH


def episodes_path() -> Path:
    return _resolve_path(None)


# ── 적립 / 로드 ──────────────────────────────────────────────────────

def _episode_id(context: str, body: str) -> str:
    raw = f"{context}\n---\n{body}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _clip(text: str, cap: int) -> str:
    return " ".join(str(text or "").split())[:cap] if cap <= MAX_CONTEXT_CHARS \
        else str(text or "")[:cap]


def record_episode(input_context: str, generated_draft: str,
                   final_output: str = "", *, register_key: str = "",
                   task_type: str = "", actor_id: str = "local",
                   path: Optional[Path] = None) -> Optional[Episode]:
    """에피소드 1건 적립. 본문이 비면 저장하지 않는다. 실패는 조용히 None."""
    context = _clip(input_context, MAX_CONTEXT_CHARS)
    draft = str(generated_draft or "")[:MAX_BODY_CHARS]
    final = str(final_output or "")[:MAX_BODY_CHARS]
    if not context or not (draft or final):
        return None
    ep = Episode(
        episode_id=_episode_id(context, final or draft),
        input_context=context, generated_draft=draft, final_output=final,
        register_key=register_key, task_type=task_type, actor_id=actor_id,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    p = _resolve_path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        row = dict(asdict(ep))
        row["v"] = STORE_VERSION
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        _prune(p)
    except OSError:
        return None
    return ep


def _prune(p: Path) -> None:
    try:
        lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if len(lines) > MAX_KEEP:
            p.write_text("\n".join(lines[-MAX_KEEP:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def load_episodes(path: Optional[Path] = None) -> List[Episode]:
    """에피소드 로드. 깨진 줄·미래 버전·타입 불일치는 조용히 건너뛴다."""
    p = _resolve_path(path)
    if not p.exists():
        return []
    allowed = {f.name for f in fields(Episode)}
    out: List[Episode] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("v") != STORE_VERSION:
            continue
        kwargs = {k: v for k, v in row.items() if k in allowed}
        if not isinstance(kwargs.get("input_context"), str):
            continue
        try:
            ep = Episode(**kwargs)
        except TypeError:
            continue
        if ep.example_body.strip():
            out.append(ep)
    return out


def clear_episodes(path: Optional[Path] = None) -> None:
    """전체 삭제(개인정보 통제) — 다음 실행부터 유사 사례 주입이 사라진다."""
    try:
        _resolve_path(path).unlink()
    except OSError:
        pass


# ── 유사 사례 검색 (LLM 0) ───────────────────────────────────────────

def _tokens(text: str) -> set:
    return {w for w in _WORD.findall(text or "") if w not in _STOP}


def query_from_spec(spec: Optional[dict]) -> str:
    """검색 질의 문자열 — retrieval.keywords_from_spec과 같은 필드를 본다."""
    spec = spec or {}
    parts = [str(spec.get("goal") or ""), str(spec.get("deliverable") or "")]
    reqs = spec.get("requirements")
    if isinstance(reqs, list):
        parts += [str(r) for r in reqs]
    return " ".join(p for p in parts if p).strip()


def _keyword_scores(query: str, episodes: List[Episode]) -> List[EpisodeHit]:
    q = _tokens(query)
    hits: List[EpisodeHit] = []
    for ep in episodes:
        shared = q & _tokens(ep.input_context)
        if not shared:
            continue
        # 자카드 유사도 — 긴 에피소드가 단순 중첩 수로 이기지 않게 정규화한다.
        union = q | _tokens(ep.input_context)
        hits.append(EpisodeHit(episode=ep,
                               score=round(len(shared) / max(1, len(union)), 4),
                               matched=sorted(shared)[:6]))
    return hits


def _embedding_scores(query: str, episodes: List[Episode],
                      embedder) -> List[EpisodeHit]:
    texts = [query] + [ep.input_context[:1000] for ep in episodes]
    vectors = embedder.encode(texts)
    from .retrieval import _cosine
    hits: List[EpisodeHit] = []
    for ep, vec in zip(episodes, vectors[1:], strict=True):
        sim = _cosine(vectors[0], vec)
        if sim <= 0:
            continue
        hits.append(EpisodeHit(episode=ep, score=round(float(sim), 4),
                               matched=["embedding"]))
    return hits


def find_similar(query: str, k: int = DEFAULT_K, *,
                 register_key: str = "", task_type: str = "",
                 exclude_ids: Optional[set] = None,
                 path: Optional[Path] = None,
                 use_embeddings: bool = True, embedder=None) -> List[EpisodeHit]:
    """질의와 비슷한 과거 에피소드 상위 k건. 없으면 [].

    같은 레지스터·유형을 우선 후보로 좁힌다 — '비슷한 상황'의 첫 번째 정의가
    과제 성격이기 때문이다. 좁힌 결과가 비면 전체에서 다시 찾는다(빈손보다 낫다).
    """
    episodes = load_episodes(path)
    if not episodes or not (query or "").strip():
        return []
    if exclude_ids:
        episodes = [e for e in episodes if e.episode_id not in exclude_ids]

    def _scoped(items: List[Episode]) -> List[Episode]:
        if register_key:
            narrowed = [e for e in items if e.register_key == register_key]
            if narrowed:
                return narrowed
        if task_type:
            narrowed = [e for e in items if e.task_type == task_type]
            if narrowed:
                return narrowed
        return items

    pool = _scoped(episodes)
    hits: List[EpisodeHit] = []
    if use_embeddings:
        from .retrieval import _load_embedder
        active = embedder or _load_embedder()
        if active is not None:
            try:
                hits = _embedding_scores(query, pool, active)
            except Exception:
                hits = []
    if not hits:
        hits = _keyword_scores(query, pool)
    # 동점은 최신 우선(created_at 내림차순) — 최근 문체가 더 지금의 나다.
    hits.sort(key=lambda h: (h.score, h.episode.created_at), reverse=True)
    return hits[:max(0, k)]


# ── 프롬프트 주입 ────────────────────────────────────────────────────

EPISODES_HEADER = "【과거 유사 사례 — 내가 실제로 확정한 글(문체 예시)】"


def episodes_block(hits: List[EpisodeHit], excerpt: int = EXCERPT_CHARS) -> str:
    """few-shot 블록. 히트가 없으면 빈 문자열(주입 안 함).

    **문체 예시임을 명시**한다 — 여기 담긴 내용이 사실 근거로 오인되면
    모델이 과거 과제의 소재를 이번 과제에 옮겨 적는다(실제로 잘 나는 실패다).
    """
    usable = [h for h in hits if h.episode.example_body.strip()]
    if not usable:
        return ""
    lines = [EPISODES_HEADER]
    for i, hit in enumerate(usable, 1):
        ep = hit.episode
        body = " ".join(ep.example_body.split())[:excerpt]
        tag = ep.register_key or ep.task_type or "일반"
        lines.append(f"[사례 {i} · {tag}] 상황: {ep.input_context[:160]}")
        lines.append(f"  내가 쓴 글: \"{body}\"")
    lines.append(
        "- 위 사례는 **문체·구성 참고용**이다. 사례의 소재·사실·주장을 이번 과제로 "
        "옮겨 오지 마라. 이번 과제의 근거는 아래 [참고 자료]뿐이다.")
    return "\n".join(lines)


def describe(path: Optional[Path] = None) -> str:
    """CLI·설정 화면용 한 줄 요약."""
    eps = load_episodes(path)
    if not eps:
        return "저장된 에피소드 없음"
    finals = sum(1 for e in eps if e.final_output)
    return (f"에피소드 {len(eps)}건(최종본 있는 건 {finals}) · "
            f"최근 {eps[-1].created_at[:10] if eps[-1].created_at else '?'}")
