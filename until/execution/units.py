"""응답 단위(ResponseUnit) 분할 — 통짜 1회 생성을 단위별 독립 생성으로(3단계).

단위 개수는 **양식에서 유도**한다(서술 항목 자리 → 목록형 표 행 수) — 모델이
임의로 정하게 두지 않는다. 양식이 없으면 자료의 강의 목록 라인에서, 그것도
없으면 1개(산문 과제 — 기존 동작과 동등, 회귀 방지). 결정적·LLM 0.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from ..capture.formfill import detect_form, expected_item_count


@dataclass
class ResponseUnit:
    """독립 생성·검증되는 응답 단위 하나(강의·문항·문서)."""
    index: int                       # 1-기반
    title: str = ""                  # 예: "AI 에이전트 시대의 건설산업"
    meta: dict = field(default_factory=dict)   # 분야·수강일시 등 사실값
    elements: list = field(default_factory=list)   # SkeletonSlot 목록
    length_target: object = None     # 항목당 요건(LengthTarget)
    evidence: object = None          # 근거 원장(EvidenceLedger — 4단계)
    plan: object = None              # 내용 계획(UnitPlan — 5단계)
    body: str = ""

    @property
    def mark(self) -> str:
        marks = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
        return marks[self.index - 1] if 1 <= self.index <= 20 else f"{self.index}."


# 자료(수강 내역 등)에서 강의 제목·메타를 뽑는 결정적 패턴.
# 예: "1) 분야 AI · 강좌명 '생성형 인공지능과 산업의 재편' · 2026-07-01 10:00"
_TITLE_LINE_RE = re.compile(
    r"(?:강좌명|강의명)\s*[:：]?\s*['\"“‘]?([^'\"”’·|\n]{2,40})['\"”’]?")
_FIELD_RE = re.compile(r"분야\s*[:：]?\s*([가-힣A-Za-z0-9/&\s]{1,12}?)(?=\s*[·|,]|\s+강)")
_WHEN_RE = re.compile(r"(20\d{2}[-./]\d{1,2}[-./]\d{1,2}(?:\s+\d{1,2}:\d{2})?)")


def _titles_from_docs(docs) -> List[dict]:
    """자료 텍스트에서 (제목·분야·일시) 목록을 라인 단위로 추출."""
    out: List[dict] = []
    seen = set()
    for d in docs or []:
        text = getattr(d, "text", "") or ""
        for line in text.splitlines():
            ls = line.strip()
            # 양식 스캐폴드 줄("① 강의명: / 수강일시:")은 제목이 아니다 —
            # 빈 자리 표시가 제목으로 오추출되면 단위가 한 칸씩 밀린다.
            if ls[:1] in "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳▷▶":
                continue
            m = _TITLE_LINE_RE.search(line)
            if not m:
                continue
            title = m.group(1).strip(" ·-—/")
            if (not title or title in seen or "수강일시" in title
                    or title.endswith(":") or len(title) < 2):
                continue
            seen.add(title)
            meta = {}
            f = _FIELD_RE.search(line)
            if f:
                meta["분야"] = f.group(1).strip()
            w = _WHEN_RE.search(line)
            if w:
                meta["수강 일시"] = w.group(1)
            out.append({"title": title, "meta": meta})
    return out


def _titles_from_form_rows(form_text: str) -> List[dict]:
    """양식의 목록형 표에 이미 채워진 데이터 행이 있으면 그걸 단위 정보로."""
    fs = detect_form(form_text)
    for t in fs.tables:
        if len(t) < 2:
            continue
        head = [c.strip() for c in t[0]]
        if not any("강좌" in h or "강의" in h for h in head):
            continue
        try:
            ti = next(i for i, h in enumerate(head) if "강좌" in h or "강의" in h)
        except StopIteration:
            continue
        out = []
        for row in t[1:]:
            if ti < len(row) and row[ti].strip():
                meta = {}
                for i, h in enumerate(head):
                    if i != ti and h and i < len(row) and row[i].strip():
                        meta[h] = row[i].strip()
                out.append({"title": row[ti].strip(), "meta": meta})
        if out:
            return out
    return []


def derive_units(docs, form_text: str, slots, length_target,
                 n_override: Optional[int] = None) -> List[ResponseUnit]:
    """응답 단위 목록을 만든다.

    개수 우선순위: n_override(사용자 지정) → 양식(expected_item_count) →
    자료의 강의 목록 라인 수 → 1(산문 — 기존 통짜와 동등).
    제목·메타는 양식의 채워진 행 → 자료 라인 순으로 붙인다(개수만큼).
    """
    infos = _titles_from_form_rows(form_text) if form_text else []
    if not infos:
        infos = _titles_from_docs(docs)
    n = n_override or (expected_item_count(form_text) if form_text else None) \
        or (len(infos) if infos else None) or 1
    units: List[ResponseUnit] = []
    for i in range(1, n + 1):
        info = infos[i - 1] if i - 1 < len(infos) else {}
        units.append(ResponseUnit(
            index=i, title=info.get("title", ""),
            meta=dict(info.get("meta", {})),
            elements=list(slots or []),
            length_target=length_target))
    return units


def render_units(units: List[ResponseUnit]) -> str:
    """진단용 한 줄 요약."""
    lines = []
    for u in units:
        meta = " · ".join(f"{k} {v}" for k, v in u.meta.items())
        lines.append(f"- {u.mark} {u.title or '(제목 미상)'}"
                     + (f"  [{meta}]" if meta else ""))
    return "\n".join(lines)
