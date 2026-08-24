"""커버리지 검증 — 필수 내용 요소가 실제로 다뤄졌는지(7단계, 결정적 우선).

- 1차는 결정적: element 라벨·근거 발췌의 토큰이 단위 본문에 등장하는지.
- 계획이 decision(의도된 공백)인 요소는 항상 '결정으로 커버'로 본다 —
  마커는 조립 단계에서 결정적으로 붙으므로(모델 출력에 요구하지 않는다).
- LLM 보조 판정은 선택(assist_llm)이며, **결정적 판정이 '커버'면 LLM이
  뒤집을 수 없다**(최종 판단은 결정적 규칙 우선).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]{2,}")
_STOP = {"강의", "내용", "것을", "있다", "한다", "대해", "통해"}


def _tokens(s: str) -> List[str]:
    return [t for t in _TOKEN_RE.findall(s or "") if t not in _STOP]


@dataclass
class CoverageReport:
    unit_index: int
    covered: List[str] = field(default_factory=list)       # element_id
    by_decision: List[str] = field(default_factory=list)   # 의도된 공백(DECISION)
    missing: List[tuple] = field(default_factory=list)     # (element_id, label)

    @property
    def ok(self) -> bool:
        return not self.missing


def check_unit_coverage(unit, *, assist_llm=None) -> CoverageReport:
    """단위 본문이 계획된 요소를 다뤘는지 판정. unit.plan·unit.body 필요."""
    rep = CoverageReport(unit_index=getattr(unit, "index", 0))
    body = getattr(unit, "body", "") or ""
    plan = getattr(unit, "plan", None)
    if plan is None:
        return rep
    ambiguous = []
    for it in plan.items:
        if it.action == "decision":
            # 의도된 공백 — 생성 루프 시점엔 마커가 아직 없다(조립 때
            # _append_absent_decisions가 결정적으로 붙인다). 여기서 누락으로
            # 세면 '쓰지 말라'는 지시를 지킨 모델이 매 시도 reask를 맞고,
            # reask 지시(마커를 남겨라)가 프롬프트 금지와 모순된다.
            rep.by_decision.append(it.element_id)
            continue
        # 결정적 신호: 라벨 토큰 또는 근거 발췌 토큰이 본문에 등장.
        label_hit = any(t in body for t in _tokens(it.label))
        ev_tokens = [t for x in it.excerpts for t in _tokens(x)]
        ev_hits = sum(1 for t in set(ev_tokens) if t in body)
        if label_hit or ev_hits >= 2:
            rep.covered.append(it.element_id)
        else:
            ambiguous.append(it)
    # 애매한 것만 LLM 보조(선택) — '커버'로 판정된 것은 못 뒤집는다.
    for it in ambiguous:
        verdict = None
        if assist_llm is not None:
            verdict = _llm_assist(assist_llm, it, body)
        if verdict == "covered":
            rep.covered.append(it.element_id)
        else:
            rep.missing.append((it.element_id, it.label))
    return rep


def _llm_assist(llm, item, body: str) -> Optional[str]:
    """보조 판정(선택·비치명) — 본문이 해당 요소를 실질적으로 다뤘는지."""
    import json
    schema = {"type": "object",
              "properties": {"covered": {"type": "boolean"}},
              "required": ["covered"], "additionalProperties": False}
    try:
        res = llm.complete(
            "본문이 주어진 내용 요소를 실질적으로 다뤘는지 판정한다(형식적 언급은 미커버).",
            f"[요소] {item.label}\n[본문]\n{body[:2000]}", tag="coverage",
            json=True, schema=schema)
        return "covered" if json.loads(res.text).get("covered") else "missing"
    except Exception:
        return None


def coverage_errors(rep: CoverageReport) -> List[str]:
    """재생성 사유(구체적 델타) — 어떤 요소가 빠졌는지."""
    out = []
    for _eid, label in rep.missing:
        out.append(f"필수 요소 '{label}' 미커버 — 이 항목 본문에 해당 내용이 없다. "
                   "근거 발췌를 인용해 다루거나, 근거가 없으면 그 자리에 "
                   "[[DECISION: ...]]을 남겨라.")
    return out
