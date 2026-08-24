"""
P7 — 베타 피드백 로그.

실행마다 (과제·결정수·재요청수·통과여부·만족도)를 JSONL 한 줄로 적립한다.
이 로그는 GEPA(optimize/)의 학습 입력으로 재사용된다 — 라벨이 필요 없고
입력(spec + sources)만 있으면 BoundaryGuard 메트릭으로 자기지도 최적화가 되므로,
실제 베타 사용 기록이 그대로 최적화 데이터가 된다.

설계 원칙:
- 결정적·토큰 0. LLM 호출 없음(파이프라인 산출물만 직렬화).
- 개인정보 보호: 자료 본문은 요약 길이로 잘라 저장(전체 첨부 원문 저장 안 함).
"""
from __future__ import annotations

import json
from dataclasses import fields
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # 순환 import 회피(런타임엔 불필요).
    from .pipeline import Result

DEFAULT_LOG = "_until_work/feedback.jsonl"
_SOURCE_SNIPPET_CHARS = 600


@dataclass
class FeedbackRecord:
    """한 번의 실행 기록. spec/sources는 GEPA 입력으로 그대로 쓰인다."""
    assignment: str               # 과제명
    spec: str                     # 과제 명세 JSON 문자열 (GEPA 입력)
    sources: str                  # 자료 요약 텍스트 (GEPA 입력)
    n_decisions: int              # 경계선 결정 지점 수
    reasks: int                   # 재요청(reask) 횟수
    passed: bool                  # BoundaryGuard 통과 여부
    satisfaction: Optional[int] = None       # 사용자 만족도 1~5 (없으면 None)
    voice_match: Optional[bool] = None       # 적용된 VoiceProfile이 내 말투와 맞았는지
    n_final_decisions: Optional[int] = None  # finalize 후 남은 결정 수(없으면 None)
    n_readiness_warnings: Optional[int] = None  # 제출 준비 점검 경고 수(마감/분량/인용 등)
    decision_categories: Optional[List[str]] = None  # 결정별 성격(관점·논지/가치판단 등, 순서 일치)
    # ── eTL 추출 지표(팀원: "결정 질문 수 = eTL 추출 실패 지표") ──
    # 자동으로 뽑아온 자료가 많을수록(=n_sources·chars_extracted↑) 우리가 채우고,
    # 그만큼 사람에게 넘기는 결정(n_decisions)이 줄어야 한다. 이 비율을 추세로 본다.
    n_sources: Optional[int] = None        # Execution에 넣은 자료(SourceDoc) 수
    chars_extracted: Optional[int] = None  # 자동 추출한 자료 본문 총 글자수
    backend: str = ""
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_example_dict(self) -> dict:
        """GEPA trainset 형식({spec, sources})으로 변환."""
        return {"spec": self.spec, "sources": self.sources}


def _sources_text(result: "Result") -> str:
    parts: List[str] = []
    for d in result.documents:
        snippet = " ".join(d.text.split())[:_SOURCE_SNIPPET_CHARS]
        parts.append(f"[{d.source}] {snippet}")
    return "\n".join(parts)


def record_from_result(
    result: "Result", *, satisfaction: Optional[int] = None, backend: str = "",
    timestamp: str = "", voice_match: Optional[bool] = None,
) -> FeedbackRecord:
    """파이프라인 Result에서 피드백 레코드를 만든다(토큰 0)."""
    spec = result.spec or {}
    assignment = (
        spec.get("assignment_name")
        or spec.get("deliverable")
        or spec.get("goal")
        or "과제"
    )
    if satisfaction is not None and not (1 <= int(satisfaction) <= 5):
        raise ValueError("satisfaction은 1~5 사이여야 합니다.")
    # 제출 준비 점검 경고 수(결정적) — 마감 임박·분량 미달·인용 문제 등.
    try:
        from .readiness import assess_readiness
        n_warn = len(assess_readiness(result).warnings)
    except Exception:  # 점검 실패가 로깅을 막지 않도록 방어.
        n_warn = None
    # 결정별 성격(결정적 분류) — 어떤 종류의 판단이 사람에게 넘어가는지 통계용.
    try:
        from .boundary.rationale import classify_decision
        cats = [classify_decision(d.note).category for d in result.draft.decisions] or None
    except Exception:
        cats = None
    # eTL 추출량 신호 — Execution에 실제로 들어간 자료 수·총 글자수(결정적).
    try:
        srcs = getattr(result, "source_docs", None) or []
        docs = result.documents or []
        n_sources = len(srcs) if srcs else len(docs)
        chars_extracted = sum(len(getattr(d, "text", "") or "")
                              for d in (srcs or docs))
    except Exception:
        n_sources = chars_extracted = None
    return FeedbackRecord(
        assignment=str(assignment),
        spec=json.dumps(spec, ensure_ascii=False),
        sources=_sources_text(result),
        n_decisions=result.draft.n_decisions,
        reasks=result.guard.reasks,
        passed=result.guard.passed,
        satisfaction=int(satisfaction) if satisfaction is not None else None,
        voice_match=voice_match,
        n_final_decisions=(
            result.final_draft.n_decisions if result.final_draft is not None else None
        ),
        n_readiness_warnings=n_warn,
        decision_categories=cats,
        n_sources=n_sources,
        chars_extracted=chars_extracted,
        backend=backend,
        timestamp=timestamp,
    )


def append_record(record: FeedbackRecord, path: str | Path = DEFAULT_LOG) -> Path:
    """레코드를 JSONL 한 줄로 추가(append)한다. 부모 디렉터리는 자동 생성."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    from .atomicio import path_lock
    with path_lock(p):
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    return p


def load_records(path: str | Path = DEFAULT_LOG) -> List[FeedbackRecord]:
    """JSONL 로그를 읽어 레코드 목록으로 복원. 깨진 줄은 건너뛴다."""
    p = Path(path)
    if not p.exists():
        return []
    out: List[FeedbackRecord] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
            if not isinstance(raw, dict):
                continue
            allowed = {f.name for f in fields(FeedbackRecord)}
            clean = {k: v for k, v in raw.items() if k in allowed}
            vm = clean.get("voice_match")
            if vm is not None and type(vm) is not bool:
                continue
            out.append(FeedbackRecord(**clean))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def feedback_examples(path: str | Path = DEFAULT_LOG) -> List[dict]:
    """로그를 GEPA trainset 형식({spec, sources}) 목록으로 변환한다."""
    return [r.to_example_dict() for r in load_records(path)]


def quality_sorted_examples(path: str | Path = DEFAULT_LOG,
                            limit: Optional[int] = None) -> List[dict]:
    """GEPA 입력을 품질 신호로 정렬 — 준비 점검 경고가 적은 실행 우선.

    경고(분량 미달·인용 문제 등)가 적었던 실행이 더 좋은 최적화 신호다.
    경고 수가 없는 구버전 기록은 뒤로(신호 불명). 같은 경고 수 안에서는 원래 순서 유지.
    """
    recs = load_records(path)
    recs.sort(key=lambda r: (r.n_readiness_warnings is None,
                             r.n_readiness_warnings or 0))
    out = [r.to_example_dict() for r in recs]
    return out[:limit] if limit is not None else out


def print_summary(path: str | Path = DEFAULT_LOG) -> None:
    """피드백 로그 집계를 사람이 읽게 출력. `python -m until.feedback [경로]`."""
    s = summarize(path)
    if not s.get("runs"):
        print(f"기록 없음: {path}")
        return
    print(f"=== Until 베타 피드백 요약 ({path}) ===")
    print(f"  실행 {s['runs']}회 · 평균 결정 {s['avg_decisions']} · "
          f"평균 재요청 {s['avg_reasks']} · 가드 통과율 {s['pass_rate']}")
    if s.get("avg_satisfaction") is not None:
        print(f"  만족도 평균 {s['avg_satisfaction']} (평가 {s['rated_runs']}회)")
    if s.get("avg_readiness_warnings") is not None:
        print(f"  제출 준비 경고 평균 {s['avg_readiness_warnings']}건/회")
    if s.get("decisions_per_source") is not None:
        print(f"  eTL 추출 지표: 자료 {s['avg_sources']}건/회 · "
              f"추출 {s['avg_chars_extracted']}자/회 · "
              f"자료당 결정 {s['decisions_per_source']}개(낮을수록 추출↑·빈칸↓)")
    cats = s.get("decision_category_counts")
    if cats:
        top = " · ".join(f"{k} {v}" for k, v in list(cats.items())[:4])
        print(f"  결정 성격 상위: {top}")


def summarize(path: str | Path = DEFAULT_LOG) -> dict:
    """집계: 실행 수, 평균 결정수/재요청수, 통과율, 평균 만족도."""
    recs = load_records(path)
    n = len(recs)
    if not n:
        return {"runs": 0}
    sats = [r.satisfaction for r in recs if r.satisfaction is not None]
    warns = [r.n_readiness_warnings for r in recs if r.n_readiness_warnings is not None]
    srcs = [r.n_sources for r in recs if r.n_sources is not None]
    chars = [r.chars_extracted for r in recs if r.chars_extracted is not None]
    # 추출 실패 지표 — 자료 1건당 넘어가는 결정 수. 낮을수록 eTL 추출로 빈칸을
    # 많이 뚫었다는 뜻(복붙 대비 차별화↑). n_sources가 기록된 실행만 대상.
    with_src = [r for r in recs if r.n_sources]
    dec_per_src = (round(sum(r.n_decisions for r in with_src) /
                         sum(r.n_sources for r in with_src), 2)
                   if with_src else None)
    from collections import Counter
    cat_counter: Counter = Counter()
    for r in recs:
        if r.decision_categories:
            cat_counter.update(r.decision_categories)
    return {
        "runs": n,
        "avg_decisions": round(sum(r.n_decisions for r in recs) / n, 2),
        "avg_reasks": round(sum(r.reasks for r in recs) / n, 2),
        "pass_rate": round(sum(1 for r in recs if r.passed) / n, 2),
        "avg_satisfaction": round(sum(sats) / len(sats), 2) if sats else None,
        "rated_runs": len(sats),
        "avg_readiness_warnings": round(sum(warns) / len(warns), 2) if warns else None,
        "decision_category_counts": dict(cat_counter.most_common()) or None,
        "avg_sources": round(sum(srcs) / len(srcs), 2) if srcs else None,
        "avg_chars_extracted": round(sum(chars) / len(chars)) if chars else None,
        "decisions_per_source": dec_per_src,
    }


if __name__ == "__main__":  # python -m until.feedback [로그경로]
    import sys
    print_summary(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LOG)
