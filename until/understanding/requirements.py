"""요구사항 원자 분해 — 문자열 요구사항을 '셀 수 있는 내용 요소'로.

문제: TASK_SPEC_SCHEMA의 requirements는 string[]라 "핵심 개념, 새로 알게 된 점,
실습 내용 들을 자유롭게 기술"이 문자열 하나로 남는다. 이게 3개의 독립 요소라는 걸
시스템이 모르므로 하나만 쓴 답도 통과한다. 여기서 ContentElement 목록으로 분해해
커버리지 검증(요소 단위)과 근거 충분성 판정(evidence_kind)의 기반을 만든다.

- 추출은 LLM 1회 + Structured Outputs(별도 단계 — task_spec과 합치지 않음:
  실패를 독립 진단). 실패 시 결정적 폴백(나열 규칙 분해).
- evidence_kind가 축이다: user_experience(새로 알게 된 점·소감 등)는 자료로
  절대 채울 수 없다 — 이걸 모델이 지어내는 것이 현행 실패.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List, Optional

from ..llm.base import LLMClient

# 근거 종류 — 이 요소를 채우려면 무엇이 필요한가.
EVIDENCE_KINDS = ("lecture_material",   # 강의·수업 자료(문서)에서 나옴
                  "user_experience",    # 그 사람만 아는 경험·소감·선택 — 자료로 불충족
                  "source_document",    # 과제 첨부·참고 문헌
                  "general_knowledge")  # 일반 지식으로 충분(정의·배경 설명 등)


@dataclass
class ContentElement:
    """요구사항에서 뽑아낸 '셀 수 있는' 내용 요소 하나."""
    id: str                      # 예: core_concept
    label: str                   # 사람이 읽는 이름: "핵심 개념"
    required: bool = True
    scope: str = "per_unit"      # per_unit(항목마다) | whole(문서 전체 1회)
    evidence_kind: str = "source_document"
    source_span: str = ""        # 지시문 원문 근거 조각(왜 이 요소인지)

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "required": self.required,
                "scope": self.scope, "evidence_kind": self.evidence_kind,
                "source_span": self.source_span}

    @classmethod
    def from_dict(cls, d: dict) -> "ContentElement":
        kind = str(d.get("evidence_kind") or "source_document")
        if kind not in EVIDENCE_KINDS:
            kind = "source_document"
        scope = str(d.get("scope") or "per_unit")
        return cls(id=_slug(str(d.get("id") or d.get("label") or "element")),
                   label=str(d.get("label") or d.get("id") or "요소").strip(),
                   required=bool(d.get("required", True)),
                   scope=scope if scope in ("per_unit", "whole") else "per_unit",
                   evidence_kind=kind,
                   source_span=str(d.get("source_span") or "").strip())


ELEMENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "elements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string"},
                    "required": {"type": "boolean"},
                    "scope": {"type": "string", "enum": ["per_unit", "whole"]},
                    "evidence_kind": {"type": "string",
                                      "enum": list(EVIDENCE_KINDS)},
                    "source_span": {"type": "string"},
                },
                "required": ["id", "label", "required", "scope",
                             "evidence_kind", "source_span"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["elements"],
    "additionalProperties": False,
}

_SYSTEM = """\
당신은 대학 과제 지시문을 '채점 가능한 내용 요소'로 분해하는 분석기다.
요구사항 문장에서 독립적으로 확인 가능한 내용 요소를 추출하라.

규칙:
- "A, B, C 들을 기술"처럼 나열된 것은 각각 별도 요소로 분해한다.
- evidence_kind 판정이 가장 중요하다:
  · 그 사람만 아는 것(새로 알게 된 점, 소감, 느낀 점, 본인 경험·선택·계획)
    → user_experience  (자료로 채울 수 없다)
  · 강의·수업에서 다룬 지식(핵심 개념, 강의 내용, 배운 이론)
    → lecture_material
  · 첨부·참고 문헌에서 나오는 것(자료 분석, 인용) → source_document
  · 일반 지식으로 충분한 배경 설명 → general_knowledge
- scope: 항목(강의·문항)마다 반복돼야 하면 per_unit, 문서 전체에 한 번이면 whole.
- source_span에는 그 요소를 뽑은 지시문 원문 조각을 그대로 담는다.
- 형식 요건(분량·마감·제출 방법)은 내용 요소가 아니다 — 제외한다.
- 실제로 지시문에 있는 것만. 요소를 지어내지 말 것(보통 2~6개).
"""


def _slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", s.strip().lower())
    return re.sub(r"_+", "_", s).strip("_") or "element"


# ── 결정적 폴백 — LLM 실패 시 나열 규칙 분해 ─────────────────────────
# "…별 A, B, C 들을/등을 …기술/작성/서술/정리" 꼴에서 A·B·C를 요소로.
_LIST_RE = re.compile(
    r"([가-힣A-Za-z0-9·\s]{2,20}?(?:,\s*[가-힣A-Za-z0-9·\s]{2,20}){1,6})"
    r"\s*(?:들을|등을|을|를)?\s*(?:자유롭게\s*)?(?:기술|서술|작성|정리|포함|기재)")
# 사람 고유(경험) 신호 — 폴백에서 evidence_kind 판정.
_EXPERIENCE_HINTS = ("새로 알게", "느낀", "소감", "배운 점", "인상", "본인",
                     "자신의", "나의", "적용 계획", "다짐")
_LECTURE_HINTS = ("강의", "수업", "특강", "세미나", "교육", "실습", "핵심 개념",
                  "내용")


_LABEL_PREFIX_RE = re.compile(r"^(?:수강한\s*)?(?:강의|과목|항목)별\s*|^각\s*")
_LABEL_SUFFIX_RE = re.compile(r"\s*(?:들을|등을|들|등|을|를|자유롭게)\s*$")


def _clean_label(s: str) -> str:
    s = _LABEL_PREFIX_RE.sub("", s.strip(" ·"))
    prev = None
    while prev != s:  # 꼬리 조사·부사 반복 제거("들을 자유롭게" 등)
        prev, s = s, _LABEL_SUFFIX_RE.sub("", s)
    return s.strip()


def _fallback_elements(chunks: List[str]) -> List[ContentElement]:
    out: List[ContentElement] = []
    seen = set()
    for chunk in chunks:
        for m in _LIST_RE.finditer(chunk):
            for part in m.group(1).split(","):
                label = _clean_label(part)
                if not label or len(label) > 20 or label in seen:
                    continue
                seen.add(label)
                low = label.lower()
                if any(h in low for h in _EXPERIENCE_HINTS):
                    kind = "user_experience"
                elif any(h in low for h in _LECTURE_HINTS):
                    kind = "lecture_material"
                else:
                    kind = "source_document"
                out.append(ContentElement(
                    id=_slug(label) or f"e{len(out) + 1}",
                    label=label, required=True, scope="per_unit",
                    evidence_kind=kind, source_span=chunk.strip()[:120]))
    return out


def extract_content_elements(spec: dict, docs=None,
                             llm: Optional[LLMClient] = None
                             ) -> List[ContentElement]:
    """spec.requirements(+원문 앞부분)에서 내용 요소를 분해한다.

    LLM 1회(Structured Outputs) → 파싱/스키마 실패 시 결정적 폴백.
    요소가 하나도 없으면 [](호출부가 '요소 검증 없음'으로 동작 — 산문 회귀 보호).
    """
    chunks: List[str] = []
    if isinstance(spec, dict):
        chunks += [str(r) for r in (spec.get("requirements") or [])]
        if spec.get("goal"):
            chunks.append(str(spec["goal"]))
    doc_head = ""
    for d in docs or []:
        doc_head = (getattr(d, "text", "") or "")[:2500]
        if doc_head:
            break

    if llm is not None:
        try:
            user = ("[요구사항]\n" + "\n".join(f"- {c}" for c in chunks)
                    + ("\n\n[지시문 원문 발췌]\n" + doc_head if doc_head else "")
                    + "\n\n위에서 내용 요소를 분해하라.")
            res = llm.complete(_SYSTEM, user, tag="requirements", json=True,
                               schema=ELEMENTS_SCHEMA)
            data = json.loads(res.text)
            elems = [ContentElement.from_dict(e)
                     for e in (data.get("elements") or []) if isinstance(e, dict)]
            # id 중복 제거(순서 유지).
            seen, out = set(), []
            for e in elems:
                if e.id in seen:
                    continue
                seen.add(e.id)
                out.append(e)
            if out:
                return out
        except Exception:
            pass  # → 결정적 폴백
    return _fallback_elements(chunks + ([doc_head] if doc_head else []))


def render_elements(elements: List[ContentElement]) -> str:
    """진단·리포트용 한 줄 요약 목록."""
    kind_ko = {"lecture_material": "강의자료", "user_experience": "본인 경험",
               "source_document": "첨부자료", "general_knowledge": "일반지식"}
    lines = []
    for e in elements:
        lines.append(f"- {e.label} [{kind_ko.get(e.evidence_kind, e.evidence_kind)}"
                     f" · {'항목별' if e.scope == 'per_unit' else '전체 1회'}"
                     f"{'' if e.required else ' · 선택'}]")
    return "\n".join(lines)
