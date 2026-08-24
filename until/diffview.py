"""초안→최종본 변경 요약 — 결정적(LLM 0, 표준 라이브러리 difflib).

finalize(2차 패스)가 사람의 결정을 본문에 '어떻게' 녹였는지 투명하게 보여준다.
경계선 철학의 연장: AI가 손댄 곳을 숨기지 않아야 학생이 결과를 신뢰하고 검토할 수 있다.
문단 단위 비교(유사 문단은 '수정'으로 짝지음). 판정·표시만 하고 본문은 건드리지 않는다.
"""
from __future__ import annotations
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import List

# 수정(changed)으로 짝지을 최소 문단 유사도. 이보다 낮으면 삭제+추가로 본다.
_SIMILAR = 0.45


@dataclass
class Change:
    kind: str      # added | removed | changed
    before: str    # 초안 문단(추가면 "")
    after: str     # 최종 문단(삭제면 "")

    @property
    def label(self) -> str:
        return {"added": "추가", "removed": "삭제", "changed": "수정"}[self.kind]


def _paras(text: str) -> List[str]:
    return [p.strip() for p in (text or "").split("\n\n") if p.strip()]


def _pair_replace(olds: List[str], news: List[str]) -> List[Change]:
    """replace 블록 안에서 유사한 문단끼리 '수정'으로 짝짓고, 남는 건 삭제/추가."""
    out: List[Change] = []
    used_new: set[int] = set()
    for o in olds:
        best_j, best_r = -1, 0.0
        for j, n in enumerate(news):
            if j in used_new:
                continue
            r = SequenceMatcher(None, o, n).ratio()
            if r > best_r:
                best_j, best_r = j, r
        if best_j >= 0 and best_r >= _SIMILAR:
            used_new.add(best_j)
            out.append(Change("changed", o, news[best_j]))
        else:
            out.append(Change("removed", o, ""))
    for j, n in enumerate(news):
        if j not in used_new:
            out.append(Change("added", "", n))
    return out


def diff_drafts(draft_body: str, final_body: str) -> List[Change]:
    """초안·최종본을 문단 단위로 비교해 변경 목록을 만든다. 동일하면 []."""
    a, b = _paras(draft_body), _paras(final_body)
    sm = SequenceMatcher(None, a, b)
    changes: List[Change] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            changes.extend(Change("added", "", p) for p in b[j1:j2])
        elif tag == "delete":
            changes.extend(Change("removed", p, "") for p in a[i1:i2])
        else:  # replace — 유사 문단은 수정으로 짝짓기
            changes.extend(_pair_replace(a[i1:i2], b[j1:j2]))
    return changes


def summarize_changes(changes: List[Change]) -> str:
    """헤드라인 한 줄 — '수정 2 · 추가 1' 형식(없으면 '변경 없음')."""
    if not changes:
        return "변경 없음"
    counts: dict[str, int] = {}
    for c in changes:
        counts[c.label] = counts.get(c.label, 0) + 1
    order = ["수정", "추가", "삭제"]
    return " · ".join(f"{k} {counts[k]}" for k in order if k in counts)
