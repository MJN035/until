"""과제 원문 → `RuntimeRequest.spec` (결정적, LLM 0).

Local Agent 경로의 전제는 **Until이 모델 API를 부르지 않는다**는 것이다
(`docs/ASSIGNMENT_RUNTIME_PLAN.md` §8). 그래서 웹 경로가 쓰는
`understanding.extract_task_spec`(LLM 1회)을 여기서 쓸 수 없다. 대신 이미
결정적인 판정기들을 그대로 재사용해 명세를 조립한다:

  - 필수 항목·인용 양식 → `policy_compiler.compile_policy`
  - 분량 요건            → `understanding.length_target.detect_length_target`

모르는 값은 **추측해서 채우지 않는다.** 값이 없으면 그 항목은 검증에서 빠질
뿐이고(`report_runtime._check_*`는 없는 키를 그냥 통과시킨다), 지어낸 요건으로
에이전트를 잘못된 방향에 묶는 쪽이 훨씬 나쁘다.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

#: 과제 원문이 자료 인용을 요구한다는 신호. 이 표현이 없으면 인용을 강제하지 않는다.
_CITATION_HINT = re.compile(
    r"인용|출처|참고\s*문헌|참고\s*자료|references?\b|citation|각주", re.I)
#: `capture.sources.models.CollectedAssignment.to_files()`가 붙이는 머리말.
_COURSE_LINE = re.compile(r"^과목:\s*(.+)$", re.M)


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            title = stripped.lstrip("# ").strip()
            if title:
                return title
    return ""


def _doc_text(doc: Any) -> str:
    return str(getattr(doc, "text", "") or "")


def build_runtime_spec(documents: Iterable[Any], *, title: str = "") -> dict:
    """수집한 문서에서 런타임 명세를 만든다. 못 정한 항목은 아예 넣지 않는다."""
    docs = list(documents or [])
    if not docs:
        raise ValueError("과제 문서가 없습니다")
    head = _doc_text(docs[0])
    whole = "\n".join(_doc_text(doc) for doc in docs)

    spec: dict = {}
    resolved_title = (title or "").strip() or _first_heading(head)
    if resolved_title:
        spec["title"] = resolved_title
        # ReportRuntime.supports는 title·goal 중 하나만 있으면 된다. goal을 따로
        # 지어내지 않고 제목을 그대로 목표로 둔다 — 여기서 문장을 만들어 내면
        # 그건 명세가 아니라 창작이다.
        spec["goal"] = resolved_title

    course = _COURSE_LINE.search(head)
    if course:
        spec["course"] = course.group(1).strip()

    from ..policy_compiler import compile_policy
    policy = compile_policy(head)
    if policy.required_sections:
        spec["required"] = list(policy.required_sections)
    if policy.citation_style:
        spec["citation_style"] = policy.citation_style

    from ..understanding.length_target import detect_length_target
    target = detect_length_target({}, docs)
    # 항목당 요건(per_item)은 전체 본문 하한으로 바꿔 쓸 수 없다 — "강의당 300자"를
    # 전체 300자로 읽으면 요건이 통째로 무력해진다. 그때는 분량 검사를 걸지 않는다.
    if target is not None and target.unit == "자" and target.min and not target.per_item:
        spec["min_chars"] = int(target.min)

    if _CITATION_HINT.search(whole):
        spec["requires_citation"] = True
    return spec
