"""Understanding step — structured task spec via Structured Outputs (schema-constrained)."""
from __future__ import annotations
import json
import re
from typing import List

from ..capture.models import Document
from ..llm.base import LLMClient, SourceDoc

SYSTEM = (
    "당신은 대학생의 과제를 대신 준비하는 에이전트의 '이해' 단계입니다. "
    "제공된 자료를 읽고 과제 명세를 구조화하세요. 사람의 판단이 필요한 항목은 open_questions로 분리하세요."
)

# Structured Outputs용 JSON 스키마 — 출력이 이 구조를 따르도록 강제됨.
TASK_SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "deliverable": {"type": "string"},
        "goal": {"type": "string"},
        "requirements": {"type": "array", "items": {"type": "string"}},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "deadline": {"type": "string"},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["deliverable", "goal", "requirements", "open_questions"],
    "additionalProperties": False,
}


# 추출기 자기 지시 반향(echo) — 과제 본문이 빈약하면 모델이 "명세를 추출해
# JSON으로 응답"이라는 자기 임무를 과제 요구사항으로 착각한다(라이브 실측:
# '2주차 질의'에서 초안이 spec JSON 덤프로 붕괴). 결정적으로 걸러낸다.
_ECHO_RE = re.compile(
    r"(?:명세|스펙)[^\n]{0,20}추출|추출[^\n]{0,12}(?:명세|스펙|JSON)"
    r"|JSON[^\n]{0,10}(?:형식|포맷|응답|출력)|(?:형식|포맷)[^\n]{0,8}JSON"
    r"|마크다운\s*파일|구조화(?:하여|해서|된)?\s*(?:응답|출력)"
    r"|open_questions|Structured\s*Outputs?", re.IGNORECASE)


def sanitize_task_spec(spec: dict) -> dict:
    """spec에서 추출기 자기 지시 반향을 제거한다(결정적, LLM 0)."""
    if not isinstance(spec, dict):
        return spec
    for key in ("requirements", "constraints"):
        items = spec.get(key)
        if isinstance(items, list):
            spec[key] = [x for x in items if not _ECHO_RE.search(str(x))]
    for key in ("goal", "deliverable"):
        if _ECHO_RE.search(str(spec.get(key) or "")):
            spec[key] = ""
    return spec


def extract_task_spec(docs: List[Document], llm: LLMClient) -> dict:
    sources = [SourceDoc(title=d.source, text=d.text[:6000]) for d in docs]
    user = "제공된 자료에서 과제 명세를 추출하세요."
    res = llm.complete(
        SYSTEM, user, tag="understanding", json=True,
        schema=TASK_SPEC_SCHEMA, documents=sources,
    )
    try:
        return sanitize_task_spec(json.loads(res.text))
    except json.JSONDecodeError:
        return {"_raw": res.text, "_parse_error": True}
