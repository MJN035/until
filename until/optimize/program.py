"""DSPy 프로그램 — Execution(경계선까지 초안 작성)을 최적화 대상으로 선언."""
from __future__ import annotations


def build_program():
    import dspy

    class DraftToBoundary(dspy.Signature):
        """과제 명세와 자료로 '완성 직전 초안'을 작성한다.
        자료로 채울 수 있는 건 끝까지 쓰되, 사람만이 내릴 판단(관점/가치/취향/외부사실/윤리)은
        직접 확정하지 말고 그 자리에 [[DECISION: 무엇을 결정해야 하는지]] 마커로 남긴다.
        본인 입장을 단정하는 문장을 쓰지 말 것."""
        spec: str = dspy.InputField(desc="과제 명세 JSON")
        sources: str = dspy.InputField(desc="참고 자료 텍스트")
        draft: str = dspy.OutputField(
            desc="마크다운 초안. 판단 지점은 [[DECISION: ...]] 마커로 표시."
        )

    return dspy.ChainOfThought(DraftToBoundary)
