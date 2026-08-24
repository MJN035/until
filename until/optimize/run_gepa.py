"""
GEPA로 Execution 경계선 프롬프트를 자동 최적화한다.

전제: pip install dspy + ANTHROPIC_API_KEY.
실행:  python -m until.optimize.run_gepa
결과:  until/optimize/optimized_prompt.txt (최적화된 instruction)

GEPA 비대칭 구조(HF cookbook): 저렴한 student LM이 99% 추론, 똑똑한 reflection LM이
1% 성찰. 우리 메트릭(BoundaryGuard)이 자연어 feedback을 주면 reflection LM이
경계선 위반 패턴을 분석해 프롬프트를 진화시킨다.
"""
from __future__ import annotations
import os
from pathlib import Path


def main() -> int:
    import dspy
    from dspy import GEPA
    from .program import build_program
    from .metric import gepa_metric
    from .trainset import build_trainset_with_feedback

    # 무료 기본값: 로컬 Ollama. (Groq 무료: UNTIL_GEPA_MODEL=groq/llama-3.3-70b-versatile + GROQ_API_KEY)
    # DSPy는 litellm 표기를 그대로 받는다: "ollama_chat/llama3.2", "groq/llama-3.3-70b-versatile",
    # "anthropic/claude-...", "openrouter/...:free" 등.
    model = os.getenv("UNTIL_GEPA_MODEL", "ollama_chat/llama3.2")
    reflect = os.getenv("UNTIL_REFLECT_MODEL", model)
    # api_base는 provider 접두사가 없는(=로컬 Ollama 등) 경우에만 명시. groq/anthropic 등은 litellm 기본 사용.
    api_base = os.getenv("UNTIL_GEPA_API_BASE")
    if api_base is None and "/" in model and model.split("/", 1)[0] in {"ollama", "ollama_chat"}:
        api_base = os.getenv("UNTIL_BASE_URL", "http://localhost:11434")
    lm_kw = {"max_tokens": 4096, "temperature": 1.0}
    if api_base:
        lm_kw["api_base"] = api_base

    student = dspy.LM(model, **lm_kw)
    reflection = dspy.LM(reflect, **{**lm_kw, "max_tokens": 8192})
    dspy.configure(lm=student)

    program = build_program()
    # P7 — 베타 피드백 로그(실제 사용 기록)를 기본 예시와 합쳐 학습셋 구성.
    trainset = build_trainset_with_feedback()
    print(f"GEPA 학습셋: {len(trainset)}개 (기본 + 피드백 로그)")

    # 예산: UNTIL_GEPA_BUDGET(=max_metric_calls) 지정 시 그 횟수로 제한(무료 티어/빠른 시연용).
    # 미지정 시 auto="light"(전체 최적화, 호출 수백 회).
    budget = os.getenv("UNTIL_GEPA_BUDGET")
    gepa_kw = {"max_metric_calls": int(budget)} if budget else {"auto": "light"}
    # 무료 티어 TPM 보호: 동시성 기본 1(UNTIL_GEPA_THREADS로 조정). 버스트 429를 줄인다.
    threads = int(os.getenv("UNTIL_GEPA_THREADS", "1"))
    print(f"GEPA 예산: {gepa_kw} | 동시성: {threads}")
    optimizer = GEPA(
        metric=gepa_metric,
        reflection_lm=reflection,
        track_stats=True,
        reflection_minibatch_size=2,
        num_threads=threads,
        **gepa_kw,
    )
    optimized = optimizer.compile(program, trainset=trainset, valset=trainset)

    instructions = optimized.predict.signature.instructions
    out = Path(__file__).parent / "optimized_prompt.txt"
    out.write_text(instructions, encoding="utf-8")
    optimized.save(str(Path(__file__).parent / "optimized_program.json"))

    # 베이스 대비 개선 측정(검증셋 집계 점수). 후보 0 = 베이스 프로그램.
    stats = getattr(optimized, "detailed_results", None)
    scores = getattr(stats, "val_aggregate_scores", None) if stats else None
    if scores:
        best_idx = getattr(stats, "best_idx", None)
        best_idx = best_idx if best_idx is not None else max(range(len(scores)), key=lambda i: scores[i])
        base, best = scores[0], scores[best_idx]
        verdict = "개선됨" if best > base + 1e-9 else ("베이스 유지" if best <= base else "")
        print("\n=== 검증셋 점수(BoundaryGuard 메트릭) ===")
        print(f"베이스: {base:.3f} → 최적: {best:.3f} (Δ {best - base:+.3f}) | "
              f"후보 {len(scores)}개, 채택 #{best_idx} | {verdict}")

    print("\n=== GEPA 최적화된 instruction ===\n")
    print(instructions)
    print(f"\n저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
