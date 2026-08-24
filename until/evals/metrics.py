"""준수율 지표 — 전부 결정적(LLM 판정 0). 케이스 하나의 출력 텍스트를 채점한다."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..capture.formfill import (FACT_LABELS, check_form_fidelity, detect_form,
                                mapping_from_markdown)
from ..understanding.length_target import (LengthTarget, measure_length,
                                           split_items)


@dataclass
class CaseScore:
    key: str
    variant: str                       # legacy | unit | raw
    item_compliance: Optional[float] = None   # 항목당 분량 준수율(0~1)
    n_items_found: int = 0
    form_fidelity: Optional[float] = None     # 양식 구조 보존율(0~1)
    injection: Optional[float] = None         # 원본 주입 성공률(0~1)
    injected: str = ""                        # "표 15칸 · 서술 3항목(7문단)"
    hallucinated_cells: int = 0               # 사실 칸 환각 건수(0이어야 함)
    whole_ok: Optional[bool] = None           # 전체 분량 하한 충족(혼합 요건)
    reasks: int = 0
    llm_calls: int = 0
    # 품질 지표(9단계) — 전부 결정적.
    specificity: Optional[float] = None       # 구체성 점수(0~1)
    n_empty: int = 0                          # 공허 문장 수
    coverage: Optional[float] = None          # 필수 요소 커버리지(0~1)
    ungrounded: int = 0                       # 근거 없이 생성된 본인경험 요소(0이어야)
    decisions_ok: Optional[float] = None      # absent→DECISION 전환율(0~1)
    type_compliance: Optional[float] = None   # 유형별 결정적 핵심 계약(0~1)
    notes: List[str] = field(default_factory=list)
    assignment_type: str = field(default="", repr=False)
    title: str = field(default="", repr=False)
    assignment_text: str = field(default="", repr=False)
    generated_body: str = field(default="", repr=False)


_WORD_CH = "0-9A-Za-z가-힣"


def _whole_value_in(value: str, hay: str) -> bool:
    """값이 자료·프로필 텍스트에 '온전한 값'으로 등장하는가.

    단순 부분문자열이면 절단 조작('2020-1234' ⊂ '2020-12345')이 통과하므로,
    앞뒤가 단어 문자(영숫자·한글·하이픈)가 아닌 경계에서만 인정한다.
    """
    if not value:
        return False
    pat = (rf"(?<![{_WORD_CH}\-])" + re.escape(value)
           + rf"(?![{_WORD_CH}\-])")
    return re.search(pat, hay) is not None


def score_output(key: str, variant: str, body: str, *,
                 per_item_range: Optional[tuple], n_items_expected: Optional[int],
                 whole_min: Optional[int], form_text: str,
                 profile: Dict[str, str], source_text: str,
                 elements: Optional[list] = None,
                 has_user_input: bool = False) -> CaseScore:
    s = CaseScore(key=key, variant=variant)
    body = body or ""

    # 1) 항목당 분량 준수율 — 항목 단위로 센다(기대 수 대비, 빠진 항목 = 위반).
    if per_item_range and n_items_expected:
        lo, hi = per_item_range
        items = split_items(body)
        if not items and n_items_expected == 1:
            # 단일 항목 양식 — 헤드가 1개면 split(≥2 필요)이 못 나눔: ① 줄 이후를
            # 그 항목 본문으로 간주(없으면 항목 0개로 채점).
            import re as _re
            m = _re.search(r"^\s*[①1１]\S*.*$", body, _re.M)
            if m:
                items = [(body[m.start():m.end()], body[m.end():])]
        s.n_items_found = len(items)
        ok = 0
        for _lb, chunk in items[:n_items_expected]:
            n = measure_length(chunk)[0]
            if lo <= n <= hi:
                ok += 1
        s.item_compliance = ok / n_items_expected
        if len(items) != n_items_expected:
            s.notes.append(f"항목 {len(items)}/{n_items_expected}")

    # 2) 양식 구조 보존율 — 라벨·항목 유지 비율.
    if form_text:
        fid = check_form_fidelity(form_text, body)
        if fid is not None:
            kept = ((fid.n_labels - len(fid.missing_labels))
                    + (fid.n_items - len(fid.missing_items)))
            total = fid.n_labels + fid.n_items
            s.form_fidelity = (kept / total) if total else None

    # 3) 사실 칸 환각 — 프로필·자료 어디에도 없는 값이 신상 칸에 채워짐(0이어야).
    if form_text:
        prof_vals = {v.strip() for v in profile.values() if v and v.strip()}
        hay = (source_text or "") + " " + " ".join(prof_vals)
        # 복합 값 판정용 — 프로필 값들의 정확한 공백 토큰 집합(부분문자열 금지).
        prof_toks = {t for pv in prof_vals for t in pv.split() if len(t) >= 2}
        for label, value in mapping_from_markdown(body).items():
            base = label.strip().lower()
            if not any(k in base for k in FACT_LABELS):
                continue
            v = value.strip()
            if not v or v in ("-", "―", "미정"):
                continue
            if v in prof_vals or _whole_value_in(v, hay):
                continue
            # 복합 값("서울대학교 자유전공학부")은 토큰 전부가 프로필 값 토큰과
            # 완전 일치할 때만 정상(합쳐 쓴 경우) — 자료 본문 단어를 짜깁기한
            # 조합('데이터'+'윤리')이나 절단은 환각으로 남긴다.
            toks = v.split()
            if (len(toks) >= 2 and all(len(t) >= 2 for t in toks)
                    and all(t in prof_toks for t in toks)):
                continue
            s.hallucinated_cells += 1
            s.notes.append(f"환각 칸: {label}={v[:14]}")

    # 4) 전체 분량 하한(혼합 요건·산문).
    if whole_min:
        s.whole_ok = measure_length(body)[0] >= whole_min

    # 5) 품질 지표(9단계) — 구체성·커버리지·무근거 본인경험·DECISION 전환.
    from ..execution.specificity import assess_specificity
    rep = assess_specificity(body, source_texts=[source_text])
    s.specificity = rep.score
    s.n_empty = len(rep.empty_sentences)
    if elements:
        req = [e for e in elements if getattr(e, "required", True)]
        if req:
            covered = 0
            # DECISION 마커는 '그 요소를 묻는' 마커일 때만 해당 요소를 커버 —
            # 무관한 마커 1개가 커버리지를 100%로 만드는 왜곡 방지(요소별 판정).
            dec_text = " ".join(re.findall(r"\[\[DECISION:(.*?)\]\]", body, re.S))
            for e in req:
                # 내용어 토큰은 길이 2 이상만 — 1글자("점"·"된") 과대 판정 방지.
                toks = [t for t in str(getattr(e, "label", "")).split()
                        if len(t) >= 2]
                label_hit = any(t in body for t in toks)
                dec_hit = any(t in dec_text for t in toks)
                if label_hit or dec_hit:
                    covered += 1
            s.coverage = covered / len(req)
        # 본인경험(user_experience) 요소 — 답 없이 산문으로 채웠으면 무근거.
        exp = [e for e in req
               if getattr(e, "evidence_kind", "") == "user_experience"]
        if exp and not has_user_input:
            has_dec = "[[DECISION" in body
            s.decisions_ok = 1.0 if has_dec else 0.0
            if not has_dec:
                s.ungrounded = len(exp)

    # 6) 확장 골든의 유형별 핵심 계약. 사람 채점 전에도 명백한 구조·안전
    # 위반을 결정적으로 잡는다(LLM 심판 0).
    if key == "evidence_report":
        source_labels = re.findall(r"\[(자료\s*[A-Z가-힣0-9]+)\]", source_text)
        s.type_compliance = (sum(label in body for label in source_labels)
                             / len(source_labels)) if source_labels else 0.0
    elif key == "reflective_report":
        s.type_compliance = 1.0 if "[[DECISION:" in body else 0.0
    elif key == "problemset":
        found = {m.group(1) for m in re.finditer(r"(?m)^\s*([1-3])[.)]\s+", body)}
        s.type_compliance = len(found) / 3
    elif key == "hdl_lab":
        from ..understanding.measured_check import find_ungrounded_measurements
        violations = find_ungrounded_measurements(
            body, [source_text], strategy="hdl_lab")
        s.type_compliance = 1.0 if not violations else 0.0
        if violations:
            s.notes.append(f"근거 없는 실측값 {len(violations)}건")

    return s


def per_item_target(rng: Optional[tuple], scope: str = "강의") -> Optional[LengthTarget]:
    if not rng:
        return None
    return LengthTarget(unit="자", min=rng[0], max=rng[1], per_item=scope)


def form_slot_count(form_text: str) -> int:
    """양식에서 '채울 수 있는 자리' 수(빈 표 칸 + 서술 항목 자리) — 주입률 분모."""
    fs = detect_form(form_text)
    empty_cells = sum(1 for t in fs.tables for row in t for c in row if not c.strip())
    return empty_cells + len(fs.item_heads)
