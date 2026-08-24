"""근거 원장(EvidenceLedger)과 충분성 판정 — 재설계 4단계, 결정적(LLM 0).

핵심 규칙(모델 재량 금지, 코드로 강제):
- sufficient → 근거를 인용하며 생성
- thin      → 생성하되 확신 표현 금지, 근거 범위 안에서만
- absent    → **생성 금지** — 일반론으로 분량을 채우지 말고 구체적 빈칸형
              [[DECISION]]을 남긴다(왜 필요한지 + 답하면 무엇이 채워지는지).

user_experience 계열(새로 알게 된 점·소감·적용)은 자료로 절대 충족되지 않는다 —
사용자 입력(user_input)이 없으면 무조건 absent. 이걸 모델이 지어내는 것이
현행 실패("~을 체험하였다" 300자)다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 출처 종류 — 단위별 원장에 실리는 근거의 계보.
EVIDENCE_SOURCES = ("assignment_doc", "etl_material", "etl_announcement",
                    "my_file", "user_input", "profile")


@dataclass
class EvidenceItem:
    kind: str            # EVIDENCE_SOURCES 중 하나
    title: str
    excerpt: str         # 관련 발췌(생성 프롬프트에 그대로 실림)
    relevance: int = 0   # 단위 제목·메타 토큰 매칭 수(결정적)


@dataclass
class EvidenceLedger:
    unit_title: str = ""
    items: List[EvidenceItem] = field(default_factory=list)

    def of_kinds(self, kinds) -> List[EvidenceItem]:
        return [i for i in self.items if i.kind in kinds]

    @property
    def total_chars(self) -> int:
        return sum(len(i.excerpt) for i in self.items)


_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]{2,}")
# 검색 노이즈가 되는 상투 토큰(강의·과제 문서 어디에나 있음).
_STOP = {"강의", "강좌", "수강", "과제", "내용", "안내", "제출", "작성", "보고서",
         "일시", "분야", "확인", "내역", "the", "and"}


def _tokens(s: str) -> List[str]:
    return [t for t in _TOKEN_RE.findall(s or "") if t.lower() not in _STOP]


def _kind_of_source(title: str) -> str:
    t = (title or "").strip()
    if t.startswith("과제:"):
        return "assignment_doc"
    if t.startswith("[수업자료]") or t.startswith("[eTL"):
        return "etl_material"
    if t.startswith("[공지"):
        return "etl_announcement"
    if t.startswith("[내 파일]") or t.startswith("[내 자료]") or t.startswith("[강의평]"):
        return "my_file"
    return "assignment_doc"


_ENTRY_START_RE = re.compile(r"^\s*(?:\d+[).]|[①-⑳]|[-•*])\s")


def _blocks(text: str) -> List[str]:
    """자료를 문단 블록으로 — 빈 줄 기준, 없으면 항목 시작(N)/①/-)으로 그루핑.

    "1) 강좌명 '…'\\n요지: …" 같은 목록형 노트에서 요지 줄이 제목 줄과 한 블록으로
    묶여야 단위 검색에 걸린다(줄 단위로 쪼개면 요지가 제목 토큰 없이 떨어져 나감).
    """
    out = []
    for block in re.split(r"\n\s*\n", text or ""):
        b = " ".join(block.split())
        if len(b) >= 15:
            out.append(b)
    if len(out) <= 1 and text:
        groups: List[List[str]] = []
        for ln in text.splitlines():
            s = ln.strip()
            if not s:
                continue
            if _ENTRY_START_RE.match(ln) or not groups:
                groups.append([s])
            else:
                groups[-1].append(s)
        merged = [" ".join(" ".join(g).split()) for g in groups]
        merged = [m for m in merged if len(m) >= 15]
        if len(merged) > 1:
            out = merged
    return out


def build_ledger(unit, source_docs, *, user_answers: Optional[Dict] = None,
                 top_k: int = 4, excerpt_cap: int = 500,
                 generic_ok: bool = False) -> EvidenceLedger:
    """단위별 근거 수집 — **단위 제목·메타 기준으로** 자료를 검색한다.

    (기존 etl_materials는 과제 전체 기준 — 그래서 강의 3개가 같은 근거를 베낀다.)
    user_answers: {결정 노트/질문: 사람의 답} — user_input 근거로 원장에 실림.
    """
    led = EvidenceLedger(unit_title=getattr(unit, "title", "") or "")
    quer = _tokens(led.unit_title) + [v for v in
                                      getattr(unit, "meta", {}).values() if v]
    if not quer and generic_ok:
        # 단일 단위(산문 과제) — 과제 문서 전체가 이 단위의 근거다.
        # (다중 단위에서 제목이 없으면 근거 없음 그대로 — 정체불명 단위에
        #  아무 자료나 붙이면 단위 간 근거 섞임이 재발한다.)
        for sd in source_docs or []:
            title = str(getattr(sd, "title", "") or "")
            for block in _blocks(str(getattr(sd, "text", "") or ""))[:2]:
                led.items.append(EvidenceItem(kind=_kind_of_source(title),
                                              title=title,
                                              excerpt=block[:excerpt_cap],
                                              relevance=1))
        led.items = led.items[:top_k]
        for _q, a in (user_answers or {}).items():
            a = str(a or "").strip()
            if a:
                led.items.append(EvidenceItem(kind="user_input", title="내 답",
                                              excerpt=a[:excerpt_cap],
                                              relevance=99))
        return led
    scored: List[EvidenceItem] = []
    for sd in source_docs or []:
        title = str(getattr(sd, "title", "") or "")
        text = str(getattr(sd, "text", "") or "")
        kind = _kind_of_source(title)
        for block in _blocks(text):
            score = sum(1 for t in quer if t and t in block)
            if score <= 0:
                continue
            scored.append(EvidenceItem(kind=kind, title=title,
                                       excerpt=block[:excerpt_cap],
                                       relevance=score))
    scored.sort(key=lambda i: -i.relevance)
    # 상대 임계값 — 최고 매칭의 절반 미만(예: 다른 강의 블록의 우연한 1토큰)은
    # 이 단위의 근거로 넣지 않는다(단위 간 근거 섞임 방지).
    if scored:
        thr = max(1, (scored[0].relevance + 1) // 2)
        scored = [i for i in scored if i.relevance >= thr]
    led.items = scored[:top_k]
    # 사람이 이미 답한 내용(결정 답)은 그 자체가 1급 근거다.
    for q, a in (user_answers or {}).items():
        a = str(a or "").strip()
        if not a:
            continue
        # 이 단위와 관련된 답만(제목 토큰이 질문·답에 걸리면). 단위 제목이 없으면 전부.
        if led.unit_title and not any(t in (str(q) + a) for t in _tokens(led.unit_title)):
            continue
        led.items.append(EvidenceItem(kind="user_input", title="내 답",
                                      excerpt=a[:excerpt_cap], relevance=99))
    return led


def sufficiency(ledger: EvidenceLedger, evidence_kind: str, *,
                min_chars: int = 100, min_tokens: int = 2) -> str:
    """슬롯의 근거 충분성 — 'sufficient' | 'thin' | 'absent' (결정적).

    - user_experience: user_input이 있어야만 충족(자료로는 절대 불충족).
    - lecture_material/source_document: 관련 발췌가 있고 양이 되면 sufficient,
      조금이라도 있으면 thin, 없으면 absent.
    - general_knowledge: 항상 sufficient(일반 지식으로 충분한 자리).
    """
    if evidence_kind == "general_knowledge":
        return "sufficient"
    if evidence_kind == "user_experience":
        mine = ledger.of_kinds({"user_input"})
        if not mine:
            return "absent"
        return "sufficient" if sum(len(i.excerpt) for i in mine) >= 20 else "thin"
    docs = ledger.of_kinds({"assignment_doc", "etl_material",
                            "etl_announcement", "my_file"})
    if not docs:
        return "absent"
    strong = [i for i in docs if i.relevance >= min_tokens]
    chars = sum(len(i.excerpt) for i in docs)
    if strong and chars >= min_chars:
        return "sufficient"
    return "thin"


def absent_decision_question(unit, slot) -> str:
    """absent 슬롯을 위한 '구체적' 빈칸형 질문 — 나쁜 예("내용 알려주세요") 금지.

    형식: 어떤 강의의 무엇인지 특정 + 한 줄이면 충분 + 답하면 무엇이 채워지는지.

    **강의 이름은 실제로 있을 때만 붙인다.** 예전에는 단위 제목이 없으면
    `mark + " 항목"`으로 채워 `'① 항목' 강의에서 본인의 '결론 후보' …`라는 질문이
    나갔다 — 있지도 않은 강의를 지어내고 내부 슬롯 기호(①)를 사용자에게 노출하는
    문구다(2026-08-23 실사용). 강의 목록 양식(활동 보고서)에서는 제목이 실재하므로
    그 경우에만 종전 형식을 쓰고, 산문 과제처럼 단위가 하나뿐이면 강의를 빼고
    과제 자체를 가리킨다.
    """
    title = str(getattr(unit, "title", "") or "").strip()
    label = getattr(slot, "label", "내용")
    tgt = getattr(unit, "length_target", None)
    fill = ""
    if tgt is not None and getattr(tgt, "max", None):
        fill = f" — 이걸로 {tgt.describe()} 서술을 채웁니다"
    if title:
        when = getattr(unit, "meta", {}).get("수강 일시", "")
        when_s = f"({when}) " if when else ""
        where = f"'{title}' {when_s}강의에서 "
    else:
        where = "이 과제에서 "
    return (f"{where}본인의 '{label}' 한 가지: ___ "
            f"(한 줄이면 충분해요{fill})")
