"""
GEPA 메트릭 — 우리 BoundaryGuard 검증을 목적함수로 쓴다.

핵심: 라벨 데이터가 필요 없다. '경계선을 지켰는가'는 BoundaryValidator로
결정적으로 측정되므로, 입력(과제 명세+자료)만 있으면 자기지도(self-supervised)로
프롬프트를 최적화할 수 있다. feedback은 위반 내역(자연어) → GEPA reflection이 학습.
"""
from __future__ import annotations
from typing import Tuple

from ..boundary.models import Draft
from ..execution.boundary_guard import BoundaryValidator

_N_CHECKS = 5  # BoundaryValidator의 검사 개수(점수 정규화용)


def score_and_feedback(draft_text: str, min_decisions: int = 1,
                       length_target=None, form_text: str = "") -> Tuple[float, str]:
    """순수 함수. dspy 없이 단위 테스트 가능.

    length_target/form_text가 주어지면 분량·양식 준수(Length/FormValidator,
    결정적)를 같은 정규화 점수에 가중 합산한다 — GEPA가 경계선만 최적화하고
    준수를 무시하던 상태의 보완(입력이 없으면 기존 점수와 완전 동일).
    """
    from ..execution.boundary_guard import FormValidator, LengthValidator
    draft = Draft.from_text(draft_text)
    validators = [BoundaryValidator(min_decisions=min_decisions)]
    n_checks = _N_CHECKS
    if length_target is not None:
        validators.append(LengthValidator(length_target))
        n_checks += 1
    if form_text:
        validators.append(FormValidator(form_text))
        n_checks += 2  # 라벨·항목 두 축
    errors: list[str] = []
    for v in validators:
        errors.extend(v.validate(draft).errors)
    if not errors:
        return 1.0, (f"통과: 경계선·준수 규칙을 모두 지킴. "
                     f"결정 지점 {draft.n_decisions}개.")
    score = max(0.0, 1.0 - len(errors) / n_checks)
    fb = (
        "위반:\n" + "\n".join(f"- {e}" for e in errors) +
        "\n수정 지침: 임의로 확정한 판단은 [[DECISION: ...]]로 전환하고, "
        "게으르게 비운 부분은 자료로 채워라. 분량·양식 요건은 델타대로 맞춰라."
    )
    return score, fb


def gepa_metric(example, prediction, trace=None, pred_name=None, pred_trace=None):
    """DSPy GEPA용 metric_with_feedback. dspy.Prediction(score, feedback) 반환."""
    import dspy
    text = getattr(prediction, "draft", None) or str(prediction)
    score, feedback = score_and_feedback(text)
    return dspy.Prediction(score=score, feedback=feedback)
