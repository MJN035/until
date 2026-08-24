"""§7 측정 필드의 값을 만드는 자리(결정적·LLM 0).

`schema.TELEMETRY_ALLOWLIST`에 등재하는 것과 값이 실제로 나오는 것은 다르다 —
`route_source`·`lab_stage`·`evidence_missing`은 오래 등재만 돼 있었고 생산자가
없었다(2026-08-21 실측). 여기가 그 생산자이며, **웹(`telemetry/web.py`)과 CLI
코퍼스 러너가 이 함수들만 부른다.** `algo_gate()`와 같은 이유다: 생산자마다 따로
파생하면 언젠가 값이 갈리고, 갈라진 원장은 교차 집계를 조용히 망친다.

**열거형 밖 값은 여기서 이미 버린다.** `assert_no_source_leak`는 마지막 방어선이지
1차 필터가 아니다(docs/TELEMETRY_SCHEMA.md §3 — 자유 문자열 전면 금지). 특히
`AssignmentRoute.required_evidence`·`reason`·`questions`의 한국어 자유 문구는
어떤 경로로도 여기를 통과하지 못한다 — 이 모듈은 고정 어휘 집합과의 멤버십
검사만 하고 원문에서 온 문자열을 그대로 돌려주는 분기가 없다.
"""
from __future__ import annotations

from typing import Any

from ..understanding.requirements import EVIDENCE_KINDS

# schema._ENUMS에 등재된 고정 어휘와 같은 집합이어야 한다(둘을 함께 늘린다).
ROUTE_SOURCES: frozenset[str] = frozenset({"rule", "profile_hint",
                                           "llm_inferred", "clarify"})
LAB_STAGES: frozenset[str] = frozenset({"pre", "notebook", "result"})


def route_source(spec: Any) -> str | None:
    """이 라우트를 무엇이 정했나 — rule | profile_hint | llm_inferred | clarify.

    값은 `pipeline.run()`이 라우트를 확정하는 네 갈래에서 `spec`에 심는다. 파이프
    라인을 거치지 않고 만들어진 결과(구세션 복원 등)는 키 자체가 없으므로 None —
    "측정 안 됨"과 "rule이었음"을 섞지 않는다.
    """
    if not isinstance(spec, dict):
        return None
    value = str(spec.get("route_source") or "")
    return value if value in ROUTE_SOURCES else None


def lab_stage(route: Any) -> str:
    """실험 3단 사이클의 단계 — pre | notebook | result, 해당 없으면 ""."""
    if str(getattr(route, "strategy", "") or "") != "lab_report_cycle":
        return ""
    stage = str(getattr(route, "stage", "") or "")
    return stage if stage in LAB_STAGES else ""


def evidence_missing(result: Any) -> list[str] | None:
    """근거를 못 채워 `[[DECISION]]`으로 남은 요소들의 `evidence_kind` 집합.

    경로: `unit.plan.items` 중 `action == "decision"`인 항목(= 근거 충분성이
    `absent`라 생성을 금지하고 질문으로 돌린 요소) → `element_id`로 `unit.elements`
    (SkeletonSlot)를 찾아 `evidence_kind`. 마커는 `_append_absent_decisions`가
    바로 이 항목들에 결정적으로 붙이므로, 이 집합이 곧 "본문에 빈칸으로 남은
    이유"의 종류다.

    plan이 하나도 없는 실행(legacy 파이프라인)은 이 신호를 만들 수 없다 → None.
    빈 리스트("측정했고 빈 근거 없음")와 구분해야 분모가 오염되지 않는다.
    """
    measured = False
    kinds: set[str] = set()
    for unit in (getattr(result, "units", None) or []):
        plan = getattr(unit, "plan", None)
        if plan is None:
            continue
        measured = True
        by_id = {str(getattr(slot, "id", "")): str(getattr(slot, "evidence_kind", "") or "")
                 for slot in (getattr(unit, "elements", None) or [])}
        for item in (getattr(plan, "items", None) or []):
            if str(getattr(item, "action", "")) != "decision":
                continue
            kind = by_id.get(str(getattr(item, "element_id", "")), "")
            if kind in EVIDENCE_KINDS:
                kinds.add(kind)
    # 정렬 — 같은 실행이 같은 배열을 내야 집계가 결정적이다(집합 순서 비결정성 제거).
    return sorted(kinds) if measured else None
