"""Apply human answers to DECISION markers."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping
from json import JSONDecodeError

from .models import DecisionPoint, Draft


_DECISION_RE = re.compile(r"\[\[DECISION:\s*(.*?)\]\]", re.DOTALL)


def load_resolution_answers(path: str | Path) -> dict[int, str]:
    """
    Load answers for decision points.

    Supported JSON formats:
    - {"1": "answer", "2": "answer"}
    - ["answer for first decision", "answer for second decision"]
    - {"answers": {"1": "answer"}}
    """
    p = Path(path)
    try:
        raw_text = p.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise ValueError(f"결정 지점 답변 파일을 읽을 수 없습니다: {p}") from e
    try:
        raw = json.loads(raw_text)
    except JSONDecodeError as e:
        raise ValueError(f"결정 지점 답변 JSON 형식이 올바르지 않습니다: {p}") from e
    if isinstance(raw, dict) and "answers" in raw:
        raw = raw["answers"]
    if isinstance(raw, list):
        return {i + 1: str(v).strip() for i, v in enumerate(raw) if str(v).strip()}
    if isinstance(raw, dict):
        out: dict[int, str] = {}
        for key, value in raw.items():
            try:
                idx = int(key)
            except (TypeError, ValueError):
                continue
            text = str(value).strip()
            if text:
                out[idx] = text
        return out
    raise ValueError("resolve JSON은 리스트 또는 번호->답변 객체여야 합니다.")


def pair_resolved_decisions(
    draft: Draft, answers: Mapping[int, str]
) -> list[tuple[int, str, str]]:
    """답변이 있는 결정 지점만 (1-based 번호, 결정 내용, 사람 답변)으로 묶는다.

    finalize(2차 패스) 프롬프트에 넣을 '번호: 결정 → 답' 블록의 원천.
    답이 없는 결정은 제외(=마커를 본문에 그대로 남겨 둠).
    """
    out: list[tuple[int, str, str]] = []
    for i, dp in enumerate(draft.decisions, 1):
        ans = str(answers.get(i, "")).strip()
        if ans:
            out.append((i, dp.note, ans))
    return out


# 결정 성격별 반영(블렌딩) 지침 — finalize가 답을 본문에 녹일 때 톤을 맞춘다.
_BLEND_HINTS = {
    "가치판단": "선택한 가치 기준을 본문에 명시하고 그 기준으로 일관되게 서술하라.",
    "관점·논지": "이 답을 1인칭의 명확한 논지로 세우고 반론 처리도 이 입장에서 하라.",
    "진로·경험": "학생 개인의 경험·목표로 자연스럽게 1인칭 서술하라(일반론 금지).",
    "취향·스타일": "이 선택을 해당 문장만이 아니라 문서 전체 톤에 일관 적용하라.",
    "범위·선택": "선택된 범위만 다루고, 제외된 항목은 본문에서 언급하지 마라.",
}


def render_resolved_block(pairs: list[tuple[int, str, str]]) -> str:
    """pair_resolved_decisions 결과를 프롬프트용 텍스트 블록으로 렌더링한다.

    각 답에 결정 성격([카테고리])을 태깅하고, 성격별 반영 지침을 덧붙여
    finalize가 답의 종류에 맞는 톤으로 본문에 녹이게 한다(결정적·LLM 0).
    """
    if not pairs:
        return "(반영할 결정 답변 없음)"
    from .rationale import classify_decision
    lines, cats = [], set()
    for i, note, ans in pairs:
        cat = classify_decision(note).category
        cats.add(cat)
        lines.append(f"{i}. [{cat}] {note} → {ans}")
    block = "\n".join(lines)
    hints = [f"- {c}: {_BLEND_HINTS[c]}" for c in sorted(cats) if c in _BLEND_HINTS]
    if hints:
        block += "\n\n[ 성격별 반영 지침 ]\n" + "\n".join(hints)
    return block


def apply_resolution_answers(draft: Draft, answers: Mapping[int, str]) -> Draft:
    """Replace valid DECISION markers with human-provided answers by 1-based index."""
    decision_idx = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal decision_idx
        note = match.group(1).strip()
        dp = DecisionPoint(note=note)
        if dp.is_placeholder:
            return match.group(0)
        decision_idx += 1
        answer = str(answers.get(decision_idx, "")).strip()
        return answer if answer else match.group(0)

    return Draft.from_text(_DECISION_RE.sub(replace, draft.body))
