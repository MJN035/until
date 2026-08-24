"""형식 검증기 — 과제가 요구한 형식과 산출물 자체의 형식을 보고, 고칠 수 있으면 고친다.

두 층을 한 검증기에서 본다(사용자 지시 2026-08-23).

  (A) **과제가 요구한 형식** — `understanding.format_spec`이 뽑은 규칙: 표지·참고문헌·
      파일 형식·파일명·서식.
  (B) **산출물 자체의 형식** — 과제가 뭐라 했든 Until의 결과가 늘 지켜야 하는 모양:
      결정 마커가 파손되지 않았는가, 인용 번호가 실제 자료 범위 안인가, 내부 슬롯
      라벨이 새지 않았는가.

**알리기 전에 고친다.** 기계적으로 확실한 것만 고치고(`fixed=True`), 무엇을 왜 고쳤는지
반드시 화면에 밝힌다 — 몰래 고치면 학생은 자기가 쓴 줄 알고 낸다. 판단이 필요한 것은
고치지 않고 알리기만 한다(`fixed=False`).

지어내지 않는 원칙은 여기서도 그대로다. 표지에 넣을 학번·이름은 프로필에 있는 것만
쓰고, 없으면 `[[DECISION: 학번]]`으로 빈칸을 남긴다. 참고문헌은 실제로 수집한 자료
제목만 적는다 — 없으면 절을 만들지 않는다.
"""
from __future__ import annotations

import re

from ..understanding.format_spec import (
    COVER,
    FILE_NAME,
    FILE_TYPE,
    REFERENCES,
    detect_format_rules,
)

# 자동으로 붙인 표지를 나중에 알아보기 위한 표식(사람 눈에는 안 띄는 주석 줄).
COVER_MARK = "<!-- until:cover -->"
REFS_MARK = "<!-- until:references -->"

_DECISION_OK = re.compile(r"\[\[DECISION:\s*[^\]]*\]\]")
# 파손형: 대괄호 하나 · 콜론 없음 — 모델이 자주 흘리는 두 가지.
_DECISION_BROKEN_SINGLE = re.compile(r"(?<!\[)\[DECISION\s*[::]\s*([^\]\n]{1,120})\]")
_DECISION_BROKEN_NOCOLON = re.compile(r"\[\[DECISION\s+([^\]\n]{1,120})\]\]")

_CITE = re.compile(r"\[자료\s*(\d{1,3})\]")
# 내부 슬롯 라벨 — 요구사항 요소에 제목이 없을 때 붙던 자리표시가 본문으로 샜다
# (실사용 2026-08-23: "'① 항목' 강의에서 본인의 고찰"). 사람이 읽을 말이 아니다.
_SLOT_LABEL = re.compile(r"['\"‘“]?\s*[①-⑳]\s*(?:항목|요소|슬롯)\s*['\"’”]?")

_REF_HEADING = re.compile(r"^#{1,4}\s*(참고\s*문헌|references|출처)\s*$",
                          re.IGNORECASE | re.MULTILINE)


class FormatIssue:
    """형식 어긋남 하나. `fixed`면 이미 고친 것 — 화면에는 '고쳤다'로 알린다."""

    __slots__ = ("kind", "message", "fixed", "fix_note", "source")

    def __init__(self, kind: str, message: str, *, fixed: bool = False,
                 fix_note: str = "", source: str = ""):
        self.kind, self.message = kind, message
        self.fixed, self.fix_note, self.source = fixed, fix_note, source

    def __repr__(self) -> str:      # 시험 실패 메시지를 읽을 수 있게
        return f"FormatIssue({self.kind!r}, fixed={self.fixed}, {self.message!r})"


# ── (B) 산출물 자체의 형식 ────────────────────────────────────────────

def _fix_broken_markers(body: str) -> tuple:
    """`[DECISION: x]`·`[[DECISION x]]`를 정본 `[[DECISION: x]]`로 되돌린다.

    파손된 마커는 화면에서 결정 지점으로 안 잡혀 **묻지 않고 지나간다** — 사람이
    정해야 할 것을 안 물어보는 셈이라 이 제품에서 제일 나쁜 실패다.
    """
    issues, out = [], body
    n = 0
    out, k = _DECISION_BROKEN_SINGLE.subn(lambda m: f"[[DECISION: {m.group(1).strip()}]]", out)
    n += k
    out, k = _DECISION_BROKEN_NOCOLON.subn(lambda m: f"[[DECISION: {m.group(1).strip()}]]", out)
    n += k
    if n:
        issues.append(FormatIssue(
            "decision_marker", f"결정 마커 {n}개가 파손된 형태였다",
            fixed=True, fix_note=f"결정 마커 {n}개를 정상 형식으로 되돌렸어요 "
                                 "(그대로 두면 결정 지점으로 잡히지 않아 묻지 않고 넘어갑니다)"))
    return out, issues


def _fix_out_of_range_citations(body: str, n_sources: int) -> tuple:
    """`[자료N]`의 N이 실제 자료 수를 넘으면 `[출처?]`로 강등한다.

    있지도 않은 자료를 가리키는 인용은 지어낸 근거다. 지우지 않고 `[출처?]`로 낮추는
    이유는, 그 자리에 근거가 필요하다는 사실 자체는 맞기 때문이다.
    """
    bad = sorted({int(m.group(1)) for m in _CITE.finditer(body)
                  if int(m.group(1)) > max(n_sources, 0) or int(m.group(1)) == 0})
    if not bad:
        return body, []
    out = _CITE.sub(
        lambda m: ("[출처?]" if (int(m.group(1)) > n_sources or int(m.group(1)) == 0)
                   else m.group(0)), body)
    listed = ", ".join(f"[자료{i}]" for i in bad[:5])
    return out, [FormatIssue(
        "citation_range", f"자료 {n_sources}개뿐인데 {listed}를 가리켰다",
        fixed=True, fix_note=f"없는 자료를 가리킨 인용 {len(bad)}종을 [출처?]로 바꿨어요 — "
                             "그 자리에 근거가 필요한 건 맞지만 자료 번호가 틀렸습니다")]


def _fix_slot_labels(body: str) -> tuple:
    """내부 슬롯 자리표시('① 항목')를 지운다 — 사람이 읽을 말이 아니다."""
    n = len(_SLOT_LABEL.findall(body))
    if not n:
        return body, []
    out = _SLOT_LABEL.sub("", body)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out, [FormatIssue(
        "slot_label", f"내부 자리표시 {n}개가 본문에 노출됐다",
        fixed=True, fix_note=f"내부 자리표시 {n}개를 지웠어요('① 항목' 같은 것 — "
                             "Until 내부 슬롯 이름이지 과제 용어가 아닙니다)")]


# ── (A) 과제가 요구한 형식 ────────────────────────────────────────────

def _cover_block(items, profile: dict) -> str:
    """표지 블록 — 아는 값만 채우고 모르는 값은 빈칸 DECISION으로 남긴다."""
    known = {"이름": (profile or {}).get("name", ""),
             "학번": (profile or {}).get("student_id", ""),
             "학과": (profile or {}).get("department", "")}
    lines = [COVER_MARK]
    for item in items or ["이름", "학번"]:
        got = known.get(item, "")
        lines.append(f"- {item}: {got}" if got else f"- {item}: [[DECISION: {item}]]")
    return "\n".join(lines)


def _apply_cover(body: str, rule, profile: dict) -> tuple:
    has = COVER_MARK in body
    if rule.forbidden:
        if not has:
            return body, []
        out = re.sub(re.escape(COVER_MARK) + r".*?(?:\n\n|\Z)", "", body, count=1, flags=re.S)
        return out, [FormatIssue(
            COVER, "과제가 표지를 빼라고 했는데 표지가 있었다", fixed=True,
            fix_note="표지를 뺐어요 — 과제가 '표지 없이'라고 했습니다", source=rule.source)]
    if has:
        return body, []
    block = _cover_block(rule.extras, profile)
    blanks = block.count("[[DECISION:")
    note = "과제가 요구한 표지를 맨 앞에 붙였어요"
    if blanks:
        note += f" — {blanks}칸은 프로필에 없어 빈칸으로 두었습니다(프로필을 채우면 자동으로 들어갑니다)"
    return block + "\n\n" + body, [FormatIssue(
        COVER, "과제가 요구한 표지가 없었다", fixed=True, fix_note=note, source=rule.source)]


def _apply_references(body: str, rule, source_titles) -> tuple:
    """참고문헌 절이 없으면 **실제 수집한 자료 제목으로만** 만든다.

    자료가 하나도 없으면 만들지 않는다 — 빈 참고문헌은 붙일 이유가 없고, 있지도 않은
    문헌을 채우는 건 학문적 부정이다.
    """
    if _REF_HEADING.search(body) or REFS_MARK in body:
        return body, []
    titles = [str(t).strip() for t in (source_titles or []) if str(t).strip()]
    if not titles:
        return body, [FormatIssue(
            REFERENCES, "과제가 참고문헌을 요구하는데 수집된 자료가 없다",
            fixed=False, source=rule.source)]
    listed = "\n".join(f"{i}. {t}" for i, t in enumerate(titles, 1))
    block = f"\n\n{REFS_MARK}\n## 참고문헌\n\n{listed}\n"
    return body.rstrip() + block, [FormatIssue(
        REFERENCES, "과제가 요구한 참고문헌 절이 없었다", fixed=True,
        fix_note=f"실제로 읽은 자료 {len(titles)}건으로 참고문헌 절을 만들었어요 — "
                 "양식(APA·IEEE 등)은 과목마다 달라 제목만 나열했습니다",
        source=rule.source)]


def _delivery_issues(rules, result, profile: dict) -> list:
    """본문 밖에서 지키는 요구 — 파일 형식·파일명·서식.

    파일 형식·파일명은 다운로드 배선(web/asgi)이 **실제로 반영한다**. 그러니 그것까지
    "제출 전에 맞출 것"으로 띄우면 거짓말이다 — 이미 맞춰 놓고 사람에게 또 시키는
    셈이라, 진짜 남은 일(서식)이 묻힌다. 반영된 것은 반영됐다고 말한다.
    """
    out = []
    for r in rules:
        if r.kind in (COVER, REFERENCES):
            continue
        if r.kind == FILE_TYPE:
            note = (f"{r.value}는 받지 않는다고 해서 제출 버튼에서 뒤로 뺐어요"
                    if r.forbidden else
                    f"제출 파일을 {r.value}로 맞춰 뒀어요 — 제출 버튼 맨 앞이 그 형식입니다")
            out.append(FormatIssue(r.kind, f"과제 요구: {r.describe()}",
                                   fixed=True, fix_note=note, source=r.source))
        elif r.kind == FILE_NAME:
            named = submission_filename(result, "pdf", profile=profile, rules=rules)
            if named:
                out.append(FormatIssue(
                    r.kind, f"과제 요구: {r.describe()}", fixed=True,
                    fix_note=f"파일명을 규칙대로 지어 뒀어요 ({named[:-4]}…)",
                    source=r.source))
            else:
                out.append(FormatIssue(
                    r.kind, f"과제 요구: {r.describe()} — 프로필(이름·학번)을 채우면 "
                            "파일명을 자동으로 지어 드려요",
                    fixed=False, source=r.source))
        else:
            # 서식(pt·줄간격·글꼴)은 마크다운으로 표현할 수 없다 — 한글·워드에서 사람이
            # 마지막에 맞춘다. 맞췄다고 말할 수 없으므로 경고로 남긴다.
            out.append(FormatIssue(r.kind, f"과제 요구: {r.describe()}",
                                   fixed=False, source=r.source))
    return out


# ── 진입점 ───────────────────────────────────────────────────────────

def assignment_text(result) -> str:
    """규칙을 뽑을 원문 — 과제 명세 문서의 본문."""
    parts = []
    for d in (getattr(result, "documents", None) or []):
        parts.append(str(getattr(d, "text", "") or ""))
    if not parts:
        parts.append(str((getattr(result, "spec", None) or {}).get("goal", "")))
    return "\n".join(parts)[:40000]


def check_and_fix(result, *, profile: dict | None = None,
                  body: str | None = None) -> tuple:
    """(고쳐진 본문, FormatIssue 목록). LLM 0 — 전부 결정적이다.

    본문을 안 바꾸는 경우에도 목록은 돌려준다. 화면(readiness)이 '무엇을 고쳤고
    무엇이 남았는지'를 그대로 보여 줘야 하기 때문이다.
    """
    draft = getattr(result, "final_draft", None) or getattr(result, "draft", None)
    out = body if body is not None else str(getattr(draft, "body", "") or "")
    if not out.strip():
        return out, []

    issues = []
    # (B) 산출물 형식 — 과제가 뭐라 했든 늘 본다.
    sources = list(getattr(result, "sources", None) or [])
    for step in (_fix_broken_markers,
                 lambda b: _fix_out_of_range_citations(b, len(sources)),
                 _fix_slot_labels):
        out, got = step(out)
        issues += got

    # (A) 과제가 요구한 형식.
    rules = detect_format_rules(assignment_text(result),
                                getattr(result, "spec", None) or {})
    for rule in rules:
        if rule.kind == COVER:
            out, got = _apply_cover(out, rule, profile or {})
            issues += got
        elif rule.kind == REFERENCES and rule.value == "참고문헌":
            out, got = _apply_references(out, rule, sources)
            issues += got
    issues += _delivery_issues(rules, result, profile or {})
    return out, issues


_NAME_SLOTS = {
    "학번": ("profile", "student_id"), "이름": ("profile", "name"),
    "성명": ("profile", "name"), "학과": ("profile", "department"),
    "전공": ("profile", "department"),
    "과목": ("spec", "course"), "과목명": ("spec", "course"),
    "과제": ("spec", "title"), "과제명": ("spec", "title"),
}
_ILLEGAL_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def submission_filename(result, fmt: str, *, profile: dict | None = None,
                        rules: list | None = None) -> str:
    """과제가 정한 파일명 규칙대로 제출 파일 이름을 짓는다. 못 지으면 ""(기본값 사용).

    `학번_이름` 같은 규칙의 칸을 프로필·과제 정보로 채운다. **한 칸이라도 못 채우면
    이름을 바꾸지 않는다** — `학번_이름.pdf`라는 파일을 주는 건 `until-submission.pdf`
    보다 나쁘다(학생이 그대로 낼 수 있고, 그러면 규칙을 어긴 티가 더 난다).
    """
    if rules is None:
        rules = detect_format_rules(assignment_text(result),
                                    getattr(result, "spec", None) or {})
    pattern = next((r.value for r in rules if r.kind == FILE_NAME and r.value), "")
    if not pattern:
        return ""
    spec = getattr(result, "spec", None) or {}
    prof = profile or {}
    out = pattern
    for slot, (where, key) in _NAME_SLOTS.items():
        if slot not in out:
            continue
        got = str((prof if where == "profile" else spec).get(key, "") or "").strip()
        if not got:
            return ""       # 못 채운 칸이 있으면 규칙 적용을 포기한다
        out = out.replace(slot, got)
    out = _ILLEGAL_FS.sub("", out).strip(" .")
    if not out or len(out) > 80:
        return ""
    return f"{out}.{fmt.lstrip('.')}"


def fixed_notes(issues) -> list:
    """화면용 — 자동으로 고친 것만, 사람이 읽는 문장으로."""
    return [i.fix_note for i in issues if i.fixed and i.fix_note]


def remaining_notes(issues) -> list:
    """화면용 — 못 고쳐서 사람이 맞춰야 하는 것."""
    return [i.message for i in issues if not i.fixed]
