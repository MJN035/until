"""Course-scoped outcome memory. Raw work stays local; derived rules are inspectable."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Outcome:
    assignment_id: str
    course_id: str
    score: float | None = None
    comments: tuple[str, ...] = ()
    readiness_blocks: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryRule:
    code: str
    message: str
    occurrences: int
    assignment_ids: tuple[str, ...]


def derive_memory(outcomes: list[Outcome], course_id: str, *, min_occurrences: int = 2) -> list[MemoryRule]:
    """Learn only repeated, auditable signals; never infer traits from one event."""
    scoped = [o for o in outcomes if o.course_id == course_id]
    buckets: dict[str, list[str]] = {}
    phrases = {
        "citation": ("인용", "출처", "참고문헌", "citation", "reference"),
        "specificity": ("구체", "근거", "예시", "specific", "evidence"),
        "structure": ("구조", "목차", "논리", "structure", "organization"),
        "length": ("분량", "짧", "length", "brief"),
    }
    for outcome in scoped:
        text = " ".join(outcome.comments).lower()
        for code, needles in phrases.items():
            if any(n in text for n in needles):
                buckets.setdefault(code, []).append(outcome.assignment_id)
        for block in outcome.readiness_blocks:
            buckets.setdefault(f"readiness:{block}", []).append(outcome.assignment_id)
    messages = {
        "citation": "이 과목에서 출처·참고문헌 지적이 반복되었습니다.",
        "specificity": "이 과목에서 구체적 근거·예시 요구가 반복되었습니다.",
        "structure": "이 과목에서 글의 구조·논리 지적이 반복되었습니다.",
        "length": "이 과목에서 분량 관련 지적이 반복되었습니다.",
    }
    rules = []
    for code, ids in sorted(buckets.items()):
        unique = tuple(dict.fromkeys(ids))
        if len(ids) >= min_occurrences:
            label = code.split(":", 1)[-1]
            rules.append(MemoryRule(
                code, messages.get(code, f"이 과목에서 '{label}' 문제가 반복되었습니다."),
                len(ids), unique))
    return rules


def memory_to_dict(rules: list[MemoryRule]) -> list[dict]:
    return [asdict(rule) for rule in rules]
