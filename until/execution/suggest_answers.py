"""
결정 지점 AI 제안 — 각 [[DECISION]]에 '추천 답 + 한 줄 이유'를 제시한다.

경계선 철학: AI가 사람의 판단을 대신 *확정*하지 않는다. 학생이 한눈에 보고 '수락'하거나
고칠 수 있도록 합리적 기본값을 **제안**할 뿐이다. 최종 확정은 항상 사람의 클릭으로 이뤄진다.
("전기과 학생으로 존재해야 하는 순간"의 막막함은 덜되, "그 사람인 순간"의 선택권은 남긴다.)

LLM을 쓰므로 Execution 레이어에 둔다(capture/context/boundary/prompts는 LLM 호출 0 원칙 유지).
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from ..boundary.models import Draft
from ..llm.base import LLMClient, SourceDoc


SUGGEST_SYSTEM = """\
당신은 대학생을 돕는 'Until'의 **제안(Suggest)** 단계다.
앞 단계가 남긴 각 결정 지점([[DECISION]])에 대해, 그 학생이 고를 법한 **합리적 기본값**을
하나씩 제안한다. 당신이 대신 확정하는 것이 아니다 — 학생이 한눈에 보고 수락하거나
고칠 수 있도록 돕는 '초안 답'일 뿐이다.

[ 규칙 ]
- 각 결정마다: (answer) 1인칭의 짧은 답 1~2문장 + (why) 그 답을 고른 한 줄 근거.
- 자료·상식으로 뒷받침되는, 가장 무난하고 방어 가능한 선택을 제안한다.
- 단정적 강요 금지. 어디까지나 '제안'이다. 학생 고유의 가치판단이 꼭 필요한 경우엔
  answer에 무난한 기본 방향을 주되, why에 '다른 선택도 가능'함을 짧게 덧붙인다.
- 출력은 현대 한국어만. 한자·가나, 악센트 라틴 문자(á, ự 등), 한국어가 아닌 외국어 단어
  금지(외래어는 한글로). 고유명사·약어는 한글 또는 기존 영문(ASCII)만.
- 반드시 아래 JSON 형식만 출력한다(설명·코드펜스 없이):
  {"suggestions":[{"index":1,"answer":"...","why":"..."}]}
- index는 주어진 결정 번호와 정확히 일치시킨다. 모든 번호에 대해 하나씩 제안한다.
"""


# 카테고리별 제안 지침 — 결정 성격에 맞는 톤으로 제안하도록(결정적 분류 활용).
_CATEGORY_HINTS = {
    "가치판단": "가치 기준이 걸린 결정이다. 가장 방어 가능한 입장을 제안하되 why에 반대 입장도 성립함을 한 줄로 남겨라.",
    "관점·논지": "입장 선택이다. 자료 근거가 가장 많은 쪽을 제안하고 why에 근거를 붙여라.",
    "진로·경험": "학생 개인의 경험·진로가 걸렸다. answer는 빈칸 채우기 틀(예: '나는 ~한 경험에서')로 제안하고 why에 본인 사례로 바꾸라고 안내하라.",
    "취향·스타일": "정답 없는 스타일 선택이다. 과제 맥락에 가장 무난한 기본값을 제안하라.",
    "범위·선택": "무엇을 다룰지의 선택이다. 자료가 풍부해 쓰기 쉬운 범위를 제안하고 why에 이유를 붙여라.",
    "고유 판단": "일반 판단이다. 무난하고 방어 가능한 기본값을 제안하라.",
}


def suggest_user_message(spec_json: str, decisions: List[str], sources: str,
                         past: Optional[Dict[int, str]] = None,
                         mine: Optional[Dict[int, str]] = None,
                         only: Optional[List[int]] = None) -> str:
    # 각 결정에 결정적 분류(왜 사람 몫인지) 카테고리를 태깅해 제안 톤을 맞춘다.
    from ..boundary.rationale import classify_decision
    lines, cats = [], []
    for i, note in enumerate(decisions, 1):
        cat = classify_decision(note).category
        cats.append(cat)
        lines.append(f"{i}. [{cat}] {note}")
    listed = "\n".join(lines)
    hints = "\n".join(f"- {c}: {_CATEGORY_HINTS[c]}" for c in sorted(set(cats))
                      if c in _CATEGORY_HINTS)
    # 과거 내 답(히스토리) — AI 제안이 학생의 기존 선택 성향과 어긋나지 않게.
    past_block = ""
    if past:
        past_lines = "\n".join(f"{i}. {past[i]}" for i in sorted(past))
        past_block = ("[ 내 과거 결정 답(비슷한 결정에서) — 이 성향과 일관되게 제안하되 "
                      "그대로 복사하지는 마라 ]\n" + past_lines + "\n\n")
    # 이번 과제에서 학생이 **직접 채운 답** — 남은 빈칸을 이것과 어긋나지 않게 잇는다.
    # 과거 히스토리(past)보다 강한 맥락이다: 논지·범위·톤이 여기서 이미 정해졌다.
    mine_block = ""
    if mine:
        mine_lines = "\n".join(
            f"{i}. {decisions[i - 1]}\n   → 내 답: {mine[i]}"
            for i in sorted(mine) if 1 <= i <= len(decisions) and str(mine[i]).strip())
        if mine_lines:
            mine_block = (
                "[ 내가 이미 정한 답(이번 과제) — **가장 중요한 맥락**. 남은 결정은 이 "
                "선택들과 논지·범위·톤이 일관되어야 하고, 이미 정한 것을 뒤집으면 안 된다 ]\n"
                + mine_lines + "\n\n")
    only_block = ""
    if only:
        only_block = ("[ 제안할 번호 ] " + ", ".join(str(i) for i in sorted(only))
                      + " — 이 번호에만 제안하라. 나머지는 이미 정해졌으니 건드리지 마라.\n\n")
    tail = "위 번호에 대해서만" if only else "각 결정에 대해"
    return (
        f"[ 과제 명세(JSON) ]\n{spec_json}\n\n"
        f"[ 참고 자료 ]\n{sources}\n\n"
        f"{mine_block}"
        f"{past_block}"
        f"[ 결정 목록 — 대괄호는 결정의 성격이다 ]\n{listed}\n\n"
        f"{only_block}"
        f"[ 성격별 제안 지침 ]\n{hints}\n\n"
        f"{tail} 합리적 기본값(answer)과 한 줄 근거(why)를 위 JSON 형식으로만 출력하라."
    )


def parse_suggestions(text: str, n: int) -> Dict[int, dict]:
    """모델 출력(JSON)에서 {번호: {answer, why}}를 결정적으로 추출. 코드펜스/잡텍스트 허용."""
    out: Dict[int, dict] = {}
    data = None
    try:
        m = re.search(r"\{.*\}", text or "", re.DOTALL)  # 첫 JSON 오브젝트
        data = json.loads(m.group(0)) if m else json.loads(text)
    except Exception:
        return out
    if not isinstance(data, dict):
        return out
    for item in (data.get("suggestions") or []):
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if 1 <= idx <= n and idx not in out:
            ans = str(item.get("answer") or "").strip()
            why = str(item.get("why") or "").strip()
            if ans:
                out[idx] = {"answer": ans[:400], "why": why[:200]}
    return out


def suggest_answers(draft: Draft, spec: dict, llm: LLMClient, *,
                    context_sources: Optional[List[SourceDoc]] = None,
                    voice_hint: str = "",
                    past_answers: Optional[Dict[int, str]] = None,
                    my_answers: Optional[Dict[int, str]] = None,
                    only: Optional[List[int]] = None) -> Dict[int, dict]:
    """초안의 각 결정에 대한 {번호: {answer, why}} 제안을 LLM 1회 호출로 만든다.

    past_answers: 번호→과거 내 답(히스토리). 제안이 내 기존 성향과 일관되게 한다.
    my_answers:   번호→**이번 과제에서 내가 이미 채운 답**. 남은 빈칸을 그 선택과
                  어긋나지 않게 잇는 가장 강한 맥락.
    only:         제안할 번호 목록(보통 '아직 비어 있는 결정'). None이면 전부.
                  결정 목록 자체는 전부 보여 준다 — 맥락이 있어야 일관된 제안이 나온다.
    """
    notes = [d.note for d in draft.decisions]
    if not notes:
        return {}
    wanted = sorted({i for i in (only or []) if 1 <= i <= len(notes)})
    if only is not None and not wanted:
        return {}                      # 채울 빈칸이 없다 — LLM 호출 자체를 생략
    spec_json = json.dumps(spec, ensure_ascii=False)
    system = SUGGEST_SYSTEM if not voice_hint else (SUGGEST_SYSTEM + "\n\n" + voice_hint)
    user = suggest_user_message(spec_json, notes, "(아래 첨부된 자료 문서 참조)",
                                past=past_answers, mine=my_answers,
                                only=wanted or None)
    res = llm.complete(system, user, tag="suggest", documents=context_sources)
    out = parse_suggestions(res.text, len(notes))
    if wanted:
        # 모델이 지시를 어기고 이미 정한 칸까지 제안해도 내 답을 덮지 않게 잘라낸다.
        allow = set(wanted)
        out = {i: v for i, v in out.items() if i in allow}
    return out
