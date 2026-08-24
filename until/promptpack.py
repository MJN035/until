"""프롬프트 번들 — 채팅 LLM에 그대로 붙여넣는 자기완결 프롬프트 (결정적, LLM 0).

채팅형 LLM(ChatGPT·클로드 등)은 링크·자료명 참조를 열어볼 수 없다. 그래서 초안
문서를 그대로 붙여넣으면 "자료를 아는 척하지만 실제론 못 본" 답이 나온다.
이 번들은 과제 명세·자료 실제 발췌·현재 본문·남은 결정을 전부 텍스트로 담아
외부 연결 없이 어떤 채팅 LLM에서도 작동하게 한다.

경계선은 지시문으로 수출한다 — 받는 LLM이 [[DECISION]]을 대신 정하지 않도록
규칙을 최상단에 박는다(받는 모델이 지킬지는 보장 못 하지만, 명시는 우리 몫).
"""
from __future__ import annotations

import re

# 자료 발췌 상한 — 채팅창 붙여넣기 한계와 토큰 낭비를 고려한 보수적 기본값.
PER_DOC_CHARS = 2500
TOTAL_DOC_CHARS = 14000

_HEADING_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_SOURCE_URL_RE = re.compile(r"출처:\s*(https?://\S+)")


def _spec_lines(spec: dict) -> list:
    out = []
    label = {"title": "제목", "goal": "목표", "deliverable": "제출물",
             "deadline": "마감", "task_type": "유형"}
    for key in ("title", "goal", "deliverable", "deadline"):
        v = spec.get(key)
        if v:
            out.append(f"- {label[key]}: {v}")
    for key, name in (("requirements", "요구사항"), ("constraints", "제약")):
        items = [str(x).strip() for x in (spec.get(key) or []) if str(x).strip()]
        if items:
            out.append(f"- {name}: " + " / ".join(items))
    return out


def _augmented_spec_lines(result) -> list:
    """명세 줄 — LLM 추출(spec)이 비어도(경량 모델 폴백 실관측) 결정적 정보로 보강:
    과제 문서의 제목 헤딩·'출처:' URL, 마감 감지기(Deadline) 결과."""
    lines = _spec_lines(result.spec or {})
    joined = "\n".join(lines)
    docs_text = "\n".join(str(getattr(d, "text", "") or "")
                          for d in (result.documents or [])[:2])
    if "- 제목:" not in joined:
        m = _HEADING_RE.search(docs_text)
        if m:
            lines.insert(0, f"- 제목: {m.group(1).strip()}")
    if "- 마감:" not in joined:
        dl = getattr(result, "deadline", None)
        if dl is not None:
            wd = "월화수목금토일"[dl.due.weekday()]
            time_part = f" {dl.time_str}" if dl.time_str else ""
            ext = " (연장됨)" if getattr(dl, "extended", False) else ""
            lines.append(f"- 마감: {dl.due.year}년 {dl.due.month}월 "
                         f"{dl.due.day}일({wd}){time_part}{ext}")
    m = _SOURCE_URL_RE.search(docs_text)
    if m and "출처" not in joined:
        lines.append(f"- 출처(eTL): {m.group(1)}")
    return lines or ["- (명세 정보 없음 — 아래 본문 참고)"]


def _doc_excerpts(source_docs: list) -> list:
    """SourceDoc들의 실제 본문 발췌. 자료당·전체 상한으로 절단(절단 사실 명시)."""
    out, used = [], 0
    for i, sd in enumerate(source_docs or [], 1):
        title = str(getattr(sd, "title", "") or f"자료{i}")
        text = " ".join(str(getattr(sd, "text", "") or "").split())
        room = min(PER_DOC_CHARS, TOTAL_DOC_CHARS - used)
        if room <= 0:
            out.append(f"[자료{i}] {title}\n(지면 한계로 발췌 생략)")
            continue
        cut = text[:room]
        used += len(cut)
        mark = " …(발췌 — 뒷부분 생략)" if len(text) > len(cut) else ""
        out.append(f"[자료{i}] {title}\n{cut}{mark}")
    return out


def _resolve_source_docs(result) -> list:
    """번들에 담을 자료 — 구버전 세션(source_docs 없음)도 과제 문서+맥락으로 재구성.

    본문의 [자료N] 인용과 발췌 번호가 어긋나는 자기모순 번들 방지(리뷰 발견)."""
    try:
        from .pipeline import _all_source_docs
        return _all_source_docs(result)
    except Exception:
        return list(getattr(result, "source_docs", None) or [])


def render_prompt_bundle(result) -> str:
    """Result → 채팅 LLM용 자기완결 프롬프트 텍스트."""
    draft = result.final_draft or result.draft
    # 결정 목록 중복 제거(공백 정규화 비교) — 본문에 같은 마커가 두 번 있어도 한 번만.
    decisions, seen = [], set()
    for d in (draft.decisions or []):
        key = " ".join(d.note.split()).lower()
        if key in seen:
            continue
        seen.add(key)
        decisions.append(d.note)
    parts = [
        "아래는 AI 조수(until)가 내 과제 자료로 만든 초안이다. 너는 이걸 이어받아 나를 돕는다.",
        "",
        "[반드시 지킬 규칙]",
        "1. 본문의 [[DECISION: ...]] 표시는 내가 직접 정할 몫이다. 절대 대신 정하거나 "
        "임의로 채우지 말 것. 필요하면 나에게 하나씩 질문해서 내 답을 받은 뒤에만 반영하라.",
        "2. 아래 '자료 발췌'에 없는 사실을 근거처럼 지어내지 마라. 본문의 [자료N] 번호는 "
        "아래 발췌의 번호와 같다.",
        "3. 한국어로만 작성하라(한자·외국 문자 금지).",
        "",
        "[과제 명세]",
        *_augmented_spec_lines(result),
        "",
        "[자료 발췌]",
        *(_doc_excerpts(_resolve_source_docs(result))
          or ["(제공된 자료 없음)"]),
        "",
        "[현재 본문]",
        draft.body.strip(),
    ]
    if decisions:
        parts += ["", "[내가 아직 정하지 않은 결정]"]
        parts += [f"{i}. {n}" for i, n in enumerate(decisions, 1)]
    parts += ["", "이제 위 규칙을 지키면서, 무엇을 도와줄지 나에게 물어보며 시작하라."]
    return "\n".join(parts)
