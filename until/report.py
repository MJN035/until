"""Markdown report rendering for beta UX."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .pipeline import Result


def render_markdown_report(result: Result, *, backend: Optional[str] = None) -> str:
    """CLI 결과를 공유하기 쉬운 Markdown 리포트로 렌더링한다."""
    lines: list[str] = ["# Until Report", ""]
    if backend:
        lines += [f"- Backend: `{backend}`"]
    lines += [
        f"- BoundaryGuard: {'passed' if result.guard.passed else 'needs review'}",
        f"- Attempts: {result.guard.attempts} (reasks: {result.guard.reasks})",
        f"- Decisions: {result.draft.n_decisions}",
        "",
    ]

    # 제출 준비 점검 — 마감·분량·인용·결정을 한눈에(상단 배치). 하단의 개별 마감/분량
    # 섹션은 이 요약으로 대체한다.
    from .readiness import assess_readiness, render_readiness_lines
    rd = assess_readiness(result)
    if rd.items:
        lines += [f"## 제출 준비 점검 — {rd.headline}", ""]
        lines += render_readiness_lines(rd)
        lines.append("")

    # 체크포인트 플랜 — 볼륨 과제(마감 여유·큰 분량)에만(결정적·LLM 0).
    from .plan import plan_for_result, render_plan_markdown
    plan = plan_for_result(result)
    if plan is not None:
        lines += [render_plan_markdown(plan), ""]

    lines += [
        "## Capture",
        "",
    ]

    for doc in result.documents:
        lines.append(f"- `{doc.source}`: {doc.kind}, {doc.n_chars} chars, {len(doc.sections)} sections")

    lines += [
        "",
        "## Task Spec",
        "",
        "```json",
        json.dumps(result.spec, ensure_ascii=False, indent=2),
        "```",
        "",
    ]

    ctx = result.context
    if ctx and (ctx.course_hits or ctx.my_hits or ctx.voice.n_samples):
        lines += ["## Context", "", ctx.summary(), ""]
        for h in ctx.course_hits:
            lines.append(f"- Course material: `{h.document.source}` (score {h.score}, matched {h.matched})")
        for h in ctx.my_hits:
            lines.append(f"- My file: `{h.document.source}` (score {h.score}, matched {h.matched})")
        if ctx.voice.n_samples:
            lines.append(
                f"- Voice: {ctx.voice.ending_style}, avg {ctx.voice.avg_sentence_len} chars, "
                f"frequent {ctx.voice.frequent_terms[:5]}"
            )
            if ctx.voice.llm_summary:
                lines.append(f"- Voice LLM summary: {ctx.voice.llm_summary}")
        lines.append("")

    if getattr(result, "sources", None):
        import re
        body = (result.final_draft or result.draft).body
        cited = {int(n) for n in re.findall(r"\[자료(\d+)\]", body)}
        from .context.citation_coverage import citation_coverage
        cov = citation_coverage(result.sources, body)
        lines += ["## Sources (근거 자료)", "", f"- {cov.message}", ""]
        for i, title in enumerate(result.sources, 1):
            mark = " — 인용됨" if i in cited else ""
            lines.append(f"- [자료{i}] {title}{mark}")
        lines.append("")

    lines += ["## BoundaryGuard", ""]
    if result.guard.history:
        for i, errors in enumerate(result.guard.history, 1):
            if errors:
                lines.append(f"### Attempt {i} Errors")
                for e in errors:
                    lines.append(f"- {e}")
                lines.append("")
    if result.guard.final_errors:
        lines.append("### Final Errors")
        for e in result.guard.final_errors:
            lines.append(f"- {e}")
        lines.append("")

    lines += [
        "## Draft",
        "",
        result.draft.body.strip(),
        "",
        "## Decision Points",
        "",
    ]
    if result.draft.decisions:
        from .boundary.rationale import classify_decision
        for i, decision in enumerate(result.draft.decisions, 1):
            rat = classify_decision(decision.note)
            lines.append(f"{i}. {decision.note}  _[{rat.category}]_")
    else:
        lines.append("- No unresolved decision points found.")

    if result.final_draft is not None:
        fg = result.final_guard
        lines += [
            "",
            "## Final Draft (결정 반영)",
            "",
        ]
        if fg:
            from .diffview import diff_drafts, summarize_changes
            changes = diff_drafts(result.draft.body, result.final_draft.body)
            lines += [
                f"- BoundaryGuard: {'passed' if fg.passed else 'needs review'}",
                f"- Attempts: {fg.attempts} (reasks: {fg.reasks})",
                f"- Remaining decisions: {result.final_draft.n_decisions}",
                f"- 초안 대비 변경: {summarize_changes(changes)}",
                "",
            ]
            if changes:
                def _trim(s: str, n: int = 160) -> str:
                    s = " ".join((s or "").split())
                    return s if len(s) <= n else s[: n - 1] + "…"
                lines += ["### 변경 상세 (당신의 결정이 반영된 곳)", ""]
                for c in changes[:12]:
                    if c.kind == "changed":
                        lines.append(f"- **수정** {_trim(c.before)}")
                        lines.append(f"  - → {_trim(c.after)}")
                    elif c.kind == "added":
                        lines.append(f"- **추가** {_trim(c.after)}")
                    else:
                        lines.append(f"- **삭제** {_trim(c.before)}")
                if len(changes) > 12:
                    lines.append(f"- …외 {len(changes) - 12}곳")
                lines.append("")
        lines += [result.final_draft.body.strip(), ""]
        if result.final_draft.decisions:
            lines += ["### Remaining Decision Points", ""]
            for i, decision in enumerate(result.final_draft.decisions, 1):
                lines.append(f"{i}. {decision.note}")

    if result.suggested_prompts:
        lines += ["", "## Suggested Prompts", ""]
        for i, prompt in enumerate(result.suggested_prompts, 1):
            lines.append(f"{i}. {prompt}")

    return "\n".join(lines).rstrip() + "\n"


def write_markdown_report(result: Result, path: str | Path, *, backend: Optional[str] = None) -> Path:
    """Markdown 리포트를 파일로 저장하고 최종 경로를 반환한다."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown_report(result, backend=backend), encoding="utf-8")
    return out


# ── 제출용 내보내기 ────────────────────────────────────────────────────────
# 리포트(진단용, BoundaryGuard·spec 포함)와 달리, 학생이 그대로 이어서 완성·제출할
# '깨끗한 문서'만 낸다: 본문 + 직접 정할 것(결정) 체크리스트. 경계선 철학 유지 —
# 결정은 대신 채우지 않고, 눈에 띄게 표시해 사람이 채우도록 남긴다.
import re as _re

# 본문 속 [[DECISION: note]] 마커. 위치를 유지한 채 사람이 채울 자리표시로 바꾼다.
_SUB_DECISION_RE = _re.compile(r"\[\[DECISION:\s*(.*?)\]\]", _re.DOTALL)


def resolve_decision_markers(body: str,
                             answers: "dict[str, str] | None" = None
                             ) -> tuple[str, list[str], list[str]]:
    """본문의 `[[DECISION: note]]`를 **제출 가능한 형태로** 바꾼다.

    반환 `(본문, 전체 note, 아직 안 정한 note)`.
      - `answers`에 그 note의 답이 있으면 → 답 문장으로 치환(사람이 정한 것).
      - 없으면 → `【직접 정할 것 N: note】` 자리표시(사람이 채울 자리).

    원문 마커를 그대로 두지 않는 것이 핵심이다. `[[DECISION: ...]]`는 Until의
    내부 표기이고, 그대로 제출되면 교수가 그 대괄호를 본다. 웹(제출 문서 렌더)과
    로컬 에이전트 런타임(제출본 파일)이 **같은 규칙**을 써야 두 경로의 결과물이
    같은 모양이 된다.
    """
    notes: list[str] = []
    open_notes: list[str] = []
    resolved = {str(k).strip(): str(v).strip() for k, v in (answers or {}).items()}

    def _mark(m: "_re.Match[str]") -> str:
        note = m.group(1).strip()
        notes.append(note)
        answer = resolved.get(note, "")
        if answer:
            return answer
        open_notes.append(note)
        # 자리표시 괄호(【】)와 충돌하는 문자는 유사 괄호로 치환 — HTML 강조가
        # 노트 중간의 】에서 끊기지 않게(표시용, notes 원문은 유지).
        safe = note.replace("【", "〔").replace("】", "〕")
        return f"【직접 정할 것 {len(open_notes)}: {safe}】"

    return _SUB_DECISION_RE.sub(_mark, body), notes, open_notes


def _submission_body_and_decisions(result: Result) -> tuple[str, list[str]]:
    """최종본(있으면)·아니면 초안의 본문에서 결정 마커를 자리표시로 치환하고,
    본문에 나온 순서대로 결정 note 목록을 돌려준다."""
    draft = result.final_draft or result.draft
    body, notes, _open = resolve_decision_markers(draft.body.strip())
    return body, notes


# 과제 유형별 제출 전 팁(결정적) — 유형이 감지된 경우 제출용 문서에 한 줄.
_TYPE_SUBMIT_TIPS = {
    "problemset": "풀이 과정을 단계별로 보이고, 최종 답에 단위·유효숫자를 확인하세요.",
    "code": "제출 전에 코드를 한 번 실행해 보고, 핵심 로직에 주석·복잡도 표기를 확인하세요.",
    "report": "그림·표에 번호와 캡션을 달고, 결과 수치가 본문 서술과 일치하는지 확인하세요.",
    "presentation": "슬라이드당 핵심 메시지 1개로 줄이고, 시간에 맞춰 한 번 소리 내어 연습하세요.",
    "essay": "인용 표기(각주/참고문헌)가 과제 지시 형식과 맞는지 확인하세요.",
}


def _type_tip(result: Result) -> str:
    return _TYPE_SUBMIT_TIPS.get((result.spec or {}).get("task_type") or "", "")


def _requirement_items(result: Result) -> list[str]:
    """명세의 requirements + constraints를 제출 전 확인용 항목 목록으로. 중복·빈 항목 제거."""
    spec = result.spec or {}
    items: list[str] = []
    seen: set[str] = set()
    for key in ("requirements", "constraints"):
        v = spec.get(key)
        if isinstance(v, list):
            for x in v:
                s = str(x).strip()
                if s and s not in seen:
                    seen.add(s)
                    items.append(s)
    return items


def render_submission_markdown(result: Result) -> str:
    """제출용 Markdown — 본문 + '직접 정할 것' 체크리스트 + (있으면) 근거 자료."""
    spec = result.spec or {}
    title = spec.get("title") or spec.get("topic") or "과제 초안"
    body, notes = _submission_body_and_decisions(result)

    lines: list[str] = [f"# {title}", ""]
    lines += [body, ""]

    from .readiness import assess_readiness, render_readiness_lines
    rd = assess_readiness(result)
    if rd.items:
        lines += ["---", "", f"## 제출 준비 점검 — {rd.headline}", ""]
        lines += render_readiness_lines(rd)
        tip = _type_tip(result)
        if tip:
            lines.append(f"✍ {tip}")
        lines.append("")

    if notes:
        lines += [
            "---",
            "",
            "## 직접 정할 것 (제출 전 채우세요)",
            "",
            "> 아래는 당신 고유의 판단이 필요해 남겨둔 부분입니다. 본문의 【직접 정할 것 N】과 대응합니다.",
            "",
        ]
        from .boundary.rationale import classify_decision
        for i, note in enumerate(notes, 1):
            rat = classify_decision(note)
            lines.append(f"- [ ] **{i}.** {note}")
            lines.append(f"  - 🔒 *{rat.category}* — {rat.why}")
        lines.append("")

    reqs = _requirement_items(result)
    if reqs:
        lines += ["---", "", "## 과제 요건 점검 (제출 전 확인)", ""]
        for r in reqs:
            lines.append(f"- [ ] {r}")
        lines.append("")

    if getattr(result, "sources", None):
        lines += ["---", "", "## 참고 자료", ""]
        for i, title_ in enumerate(result.sources, 1):
            lines.append(f"- [자료{i}] {title_}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


_SUB_HTML_STYLE = (
    "body{font-family:'Georgia',serif;max-width:44rem;margin:3rem auto;"
    "padding:0 1.5rem;line-height:1.75;color:#111}"
    "h1{font-family:'Helvetica Neue',Arial,sans-serif;font-size:1.8rem;"
    "border-bottom:2px solid #111;padding-bottom:.5rem}"
    "h2{font-family:'Helvetica Neue',Arial,sans-serif;font-size:1.1rem;"
    "text-transform:uppercase;letter-spacing:.05em;margin-top:2.5rem}"
    "p{margin:1rem 0;white-space:pre-wrap}"
    "mark{background:#ffe1d6;color:#ff4d12;font-weight:600;padding:.05em .3em;"
    "border-radius:2px}"
    "ul{list-style:none;padding-left:0}"
    "li{margin:.6rem 0}"
    ".todo{border-left:3px solid #ff4d12;padding-left:.8rem}"
    ".sources{color:#666;font-size:.9rem;font-family:'Helvetica Neue',Arial,sans-serif}"
    "hr{border:none;border-top:1px solid #ddd;margin:2.5rem 0}"
    ".printbtn{position:fixed;top:1rem;right:1rem;font-family:'Helvetica Neue',Arial,sans-serif;"
    "font-size:.85rem;padding:.45em .9em;border:1px solid #111;background:#fff;cursor:pointer}"
    "@media print{body{margin:0}mark{background:none;text-decoration:underline}"
    ".printbtn{display:none}}"
)


def render_submission_html(result: Result) -> str:
    """제출용 단독 HTML — 인쇄/공유 가능. 결정 자리는 <mark>로 강조.

    본문은 escape 후 결정 자리표시만 <mark>로 감싸고, 문단은 <p>로 분리한다.
    """
    spec = result.spec or {}
    title = _html_escape(str(spec.get("title") or spec.get("topic") or "과제 초안"))
    body, notes = _submission_body_and_decisions(result)

    # escape 먼저 → 자리표시 마커를 <mark>로. (마커는 우리가 만든 안전한 텍스트)
    esc = _html_escape(body)
    esc = _re.sub(r"【(직접 정할 것 [^】]*)】", r"<mark>【\1】</mark>", esc)
    paras = "".join(f"<p>{p}</p>" for p in esc.split("\n\n") if p.strip())

    parts = [
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>",
        f"<title>{title}</title><style>{_SUB_HTML_STYLE}</style></head><body>",
        "<button class='printbtn' onclick='window.print()'>인쇄 / PDF 저장</button>",
        f"<h1>{title}</h1>",
        paras,
    ]
    from .readiness import assess_readiness, render_readiness_lines
    rd = assess_readiness(result)
    if rd.items:
        parts.append(f"<hr><h2>제출 준비 점검 — {_html_escape(rd.headline)}</h2><ul>")
        for line in render_readiness_lines(rd):
            parts.append(f"<li>{_html_escape(line)}</li>")
        tip = _type_tip(result)
        if tip:
            parts.append(f"<li>✍ {_html_escape(tip)}</li>")
        parts.append("</ul>")
    if notes:
        from .boundary.rationale import classify_decision
        parts.append("<hr><h2>직접 정할 것</h2><ul>")
        for i, note in enumerate(notes, 1):
            rat = classify_decision(note)
            parts.append(f"<li class='todo'><b>{i}.</b> {_html_escape(note)}"
                         f"<br><small>🔒 {_html_escape(rat.category)} — {_html_escape(rat.why)}</small></li>")
        parts.append("</ul>")
    reqs = _requirement_items(result)
    if reqs:
        parts.append("<hr><h2>과제 요건 점검</h2><ul>")
        for r in reqs:
            parts.append(f"<li class='todo'>☐ {_html_escape(r)}</li>")
        parts.append("</ul>")
    if getattr(result, "sources", None):
        parts.append("<hr><h2>참고 자료</h2><ul class='sources'>")
        for i, title_ in enumerate(result.sources, 1):
            parts.append(f"<li>[자료{i}] {_html_escape(str(title_))}</li>")
        parts.append("</ul>")
    parts.append("</body></html>")
    return "".join(parts)


def _xml_safe_text(s: str) -> str:
    """XML 1.0 금지 문자(C0 제어 등) 제거 후 escape.

    PDF 추출물 등에 섞인 \x07 하나로 Word가 못 여는 파일이 조용히 만들어지는
    것을 방지(escape는 &<>만 처리) — render_submission_docx와 .hwp 값 표(C안)가
    공유하는 텍스트 정화.
    """
    import re as _re
    from xml.sax.saxutils import escape
    invalid = _re.compile("[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD]")
    return escape(invalid.sub("", s or ""))


def _minimal_docx_bytes(body_xml: str) -> bytes:
    """word:body 내용(XML 조각)을 최소 유효 OOXML .docx 바이트로 감싼다(의존성 0).

    zip 3파트(Content_Types/rels/document)만 있으면 Word가 여는 최소 골격 —
    render_submission_docx와 양식 C안(.hwp 값 표) 생성이 공유하는 패키징.
    """
    from io import BytesIO
    import zipfile
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<w:document xmlns:w="{w}"><w:body>{body_xml}</w:body></w:document>')
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>')
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type='
        '"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>')
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


def render_submission_docx(result: Result) -> bytes:
    """제출용 .docx 바이트(의존성 0) — 제출본 Markdown을 워드 문단으로.

    최소 유효 OOXML(zip 3파트: [Content_Types].xml, _rels/.rels, word/document.xml).
    헤딩(#/##)은 굵게+크게, 나머지는 본문 문단. 체크박스는 ☐ 문자로.
    """
    paras = []
    for line in render_submission_markdown(result).splitlines():
        s = line.rstrip()
        if s.startswith("#"):
            level = len(s) - len(s.lstrip("#"))
            text = _xml_safe_text(s.lstrip("# ").strip())
            size = {1: "32", 2: "28"}.get(level, "24")  # half-point
            paras.append(f'<w:p><w:r><w:rPr><w:b/><w:sz w:val="{size}"/></w:rPr>'
                         f'<w:t xml:space="preserve">{text}</w:t></w:r></w:p>')
        else:
            text = _xml_safe_text(s.replace("- [ ]", "☐"))
            paras.append(f'<w:p><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>')
    return _minimal_docx_bytes("".join(paras))


def render_submission_pdf(result: Result) -> bytes:
    """제출용 .pdf 바이트(의존성 0) — 제출본 Markdown을 A4 페이지로.

    실코퍼스에서 학생 제출 1위 포맷이 PDF(내려받은 제출파일 65개 중 31개)라
    md/html/docx만으로는 '마지막 변환' 단계가 남았다. 한글은 **비내장 CID 폰트**
    (Adobe-Korea1 / UniKS-UCS2-H)로 표기 — 폰트 파일을 내장하지 않아도
    Acrobat·Chrome(PDFium) 등이 시스템 한글 폰트로 대체 렌더한다(의존성 0 유지).
    UCS-2(BMP) 밖 문자는 공백 치환. 텍스트는 UTF-16BE 16진 문자열로 기록.
    """
    page_w, page_h, margin, lead = 595, 842, 50, 17
    top_y, usable_w = page_h - 60, page_w - 2 * margin

    def _clean(s: str) -> str:
        return "".join(ch if (0x20 <= ord(ch) <= 0xD7FF
                              or 0xE000 <= ord(ch) <= 0xFFFD) else " "
                       for ch in s)

    def _wrap(text: str, size: int) -> list:
        if not text:
            return [""]
        segs, cur, w = [], [], 0.0
        for ch in text:
            # 글리프 전진폭은 폰트 딕셔너리 /W·/DW를 따르는데 이 PDF는 /DW 1000
            # 뿐이라(비내장 폰트) 뷰어가 ASCII도 전각(1.0em)으로 전진시킨다 —
            # 줄바꿈도 같은 기준이어야 영문·URL 긴 줄이 페이지 밖으로 안 잘린다.
            cw = size * 1.0
            if w + cw > usable_w and cur:
                segs.append("".join(cur))
                cur, w = [], 0.0
            cur.append(ch)
            w += cw
        segs.append("".join(cur))
        return segs

    lines: list = []  # (크기, 텍스트)
    for raw in render_submission_markdown(result).splitlines():
        s = raw.rstrip()
        if s.startswith("#"):
            level = len(s) - len(s.lstrip("#"))
            size, text = (16 if level == 1 else 13), s.lstrip("# ").strip()
        else:
            # 마크다운 표기는 평문화(굵게 별표 제거, 체크박스는 □ — KS 완성형 글리프).
            size, text = 11, s.replace("- [ ]", "□").replace("**", "")
        for seg in _wrap(_clean(text), size):
            lines.append((size, seg))

    per_page = max(1, int((top_y - margin) / lead))
    chunks = [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[]]

    # 객체 1=Catalog 2=Pages 3=Type0폰트 4=CID폰트 5=FontDescriptor, 이후 페이지·본문 쌍.
    objs: list = [b""] * 5
    kids = []
    for pi, chunk in enumerate(chunks):
        page_no, content_no = 6 + 2 * pi, 7 + 2 * pi
        ops = []
        for li, (size, text) in enumerate(chunk):
            if not text:
                continue
            y = top_y - li * lead
            hexs = text.encode("utf-16-be").hex()
            ops.append(f"BT /F1 {size} Tf 1 0 0 1 {margin} {y} Tm <{hexs}> Tj ET")
        stream = "\n".join(ops).encode("ascii")
        objs.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_w} {page_h}] "
                     f"/Resources << /Font << /F1 3 0 R >> >> "
                     f"/Contents {content_no} 0 R >>").encode("ascii"))
        objs.append(f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
                    + stream + b"\nendstream")
        kids.append(f"{page_no} 0 R")
    objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = (f"<< /Type /Pages /Kids [{' '.join(kids)}] "
               f"/Count {len(kids)} >>").encode("ascii")
    objs[2] = (b"<< /Type /Font /Subtype /Type0 /BaseFont "
               b"/HYSMyeongJo-Medium-UniKS-UCS2-H /Encoding /UniKS-UCS2-H "
               b"/DescendantFonts [4 0 R] >>")
    objs[3] = (b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /HYSMyeongJo-Medium "
               b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Korea1) "
               b"/Supplement 1 >> /FontDescriptor 5 0 R /DW 1000 >>")
    objs[4] = (b"<< /Type /FontDescriptor /FontName /HYSMyeongJo-Medium /Flags 4 "
               b"/FontBBox [-92 -148 1012 880] /ItalicAngle 0 /Ascent 880 "
               b"/Descent -148 /CapHeight 720 /StemV 91 >>")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0] * (len(objs) + 1)
    for i, body in enumerate(objs, 1):
        offsets[i] = len(out)
        out += f"{i} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode("ascii") + b"0000000000 65535 f \n"
    for i in range(1, len(objs) + 1):
        out += f"{offsets[i]:010d} 00000 n \n".encode("ascii")
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n").encode("ascii")
    return bytes(out)


def _render_hwp_value_table_docx(mapping: dict, rows_by_header: dict,
                                 items: list) -> "tuple[bytes, object]":
    """.hwp(이진, 셀 주입 불가) 원본을 대신해 '양식 슬롯 라벨 | 채운 값' 2열 표 .docx를 만든다.

    양식 C안 — .hwp는 fill_form_file이 지원하지 않는 유일한 포맷(hwpx/docx만
    지원). 원본 셀에 못 넣는 대신 채운 값을 표로 내려주고, 한글에서 직접
    옮겨 붙인 뒤 .hwpx로 저장해 재업로드하는 경로를 사람이 잇는다.
    """
    from .capture.formfill import FillStats
    stats = FillStats()
    pairs: list = []
    for label, value in mapping.items():
        pairs.append((label, value))
        stats.cells += 1
    for header, data_rows in rows_by_header.items():
        head_label = " · ".join(header)
        for row in data_rows:
            vals = [c for c in row if c]
            if not vals:
                continue
            pairs.append((head_label, " / ".join(vals)))
            stats.cells += 1
    for head, chunk in items or []:
        pairs.append((head.strip(), chunk.strip()))
        stats.items += 1
        stats.paragraphs += 1

    def _run(text: str) -> str:
        lines = (text or "").split("\n")
        parts = []
        for i, ln in enumerate(lines):
            if i:
                parts.append("<w:br/>")
            parts.append(f'<w:t xml:space="preserve">{_xml_safe_text(ln)}</w:t>')
        return f"<w:r>{''.join(parts)}</w:r>"

    def _cell(text: str, width: int, *, bold: bool = False) -> str:
        run = _run(text)
        if bold:
            run = run.replace("<w:r>", "<w:r><w:rPr><w:b/></w:rPr>", 1)
        return f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/></w:tcPr><w:p>{run}</w:p></w:tc>'

    def _row(a: str, b: str, *, bold: bool = False) -> str:
        return f"<w:tr>{_cell(a, 3000, bold=bold)}{_cell(b, 6000, bold=bold)}</w:tr>"

    border = ('<w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
              '<w:left w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
              '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
              '<w:right w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
              '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
              '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="auto"/>')
    tbl_rows = [_row("양식 슬롯 라벨", "채운 값", bold=True)]
    tbl_rows += [_row(label, value) for label, value in pairs]
    tbl = (f'<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:tblBorders>{border}</w:tblBorders>'
          '</w:tblPr><w:tblGrid><w:gridCol w:w="3000"/><w:gridCol w:w="6000"/></w:tblGrid>'
          + "".join(tbl_rows) + "</w:tbl>")
    note_text = (".hwp 원본은 셀에 자동으로 채워 넣을 수 없어 값만 표로 옮겨 드립니다 — "
                "한글에서 이 표의 값을 원본 .hwp 양식에 붙여넣고, .hwpx로 저장해 다시 "
                "올려 주시면 다음부터는 원본 서식 그대로 채워 드립니다.")
    note = f'<w:p><w:r><w:t xml:space="preserve">{_xml_safe_text(note_text)}</w:t></w:r></w:p>'
    return _minimal_docx_bytes(note + tbl), stats


def write_filled_form(result: Result, path: str | Path | None = None
                      ) -> "tuple[Path, int] | None":
    """원본 양식(hwpx/docx)에 초안의 표 값을 셀 단위로 주입한 파일을 만든다.

    업로드된 '원본 형식 그대로' 반환한다(서식·스타일 원본 유지). .hwp(이진)
    원본은 셀 주입을 지원하지 않아 대신 '채운 값 표' .docx를 만든다(양식 C안,
    출력 확장자는 항상 .docx로 강제 — 호출부는 반환 Path.suffix로 판정할 것).
    양식 첨부가 없거나 채울 값이 없으면 None. 반환: (저장 경로, 통계).
    경계선 유지 — 초안(사람이 확인하는 산출물)에 이미 있는 값만 옮겨 넣는다.
    """
    from .capture.formfill import (find_form_document, mapping_from_markdown,
                                   build_rows_by_header, fill_form_file)
    from .understanding.length_target import split_items
    src = find_form_document(result)
    if not src:
        return None
    draft = result.final_draft or result.draft
    body = (draft.body if draft else "") or ""
    # 프로필(저장된 기본정보)을 바탕에 깔고, 초안 표의 값(사람이 확인)이 우선.
    from .profile import profile_mapping
    mapping = {**profile_mapping(), **mapping_from_markdown(body)}
    form_text = next((d.text for d in result.documents or []
                      if str(getattr(d, "source", "")) == src), "")
    rows = build_rows_by_header(form_text, body)
    # 서술 항목(①②…) 본문 — 표 칸만 채우면 본문은 여전히 복붙(갭 3).
    items = [(lb, ch) for lb, ch in split_items(body) if lb.strip()[:1] in "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"]
    if not mapping and not rows and not items:
        return None
    src_suffix = Path(src).suffix.lower()
    is_hwp = src_suffix == ".hwp"  # 이진 .hwp는 주입 불가 — 값 표 .docx로 대체(C안)
    if path:
        out = Path(path)
        if is_hwp:
            out = out.with_suffix(".docx")
    else:
        out = Path(src).with_name(
            Path(src).stem + "_작성본" + (".docx" if is_hwp else src_suffix))
    out.parent.mkdir(parents=True, exist_ok=True)
    if is_hwp:
        # 라벨 '밀도'만으로 양식을 감지했으므로, 값은 원문에 실제로 등장한
        # 라벨과 일치하는 것만 표에 담는다 — 무관한 .hwp에 프로필을 통째로
        # 흘려보내지 않기 위한 방어(find_form_document 오탐과는 별개의 방어선).
        from .capture.formfill import filter_mapping_to_hwp_labels
        hwp_mapping = filter_mapping_to_hwp_labels(mapping, form_text)
        data, stats = _render_hwp_value_table_docx(hwp_mapping, rows, items)
        if stats.total == 0:
            return None
        out.write_bytes(data)
        return out, stats
    stats = fill_form_file(src, out, mapping, rows, item_bodies=items or None)
    if stats.total == 0:
        try:
            out.unlink()  # 아무것도 못 채웠으면 빈 복사본을 남기지 않는다
        except OSError:
            pass
        return None
    return out, stats


def write_submission(result: Result, path: str | Path) -> Path:
    """확장자(.html/.htm → HTML, .docx → Word, .pdf → PDF, 그 외 → Markdown)에 맞춰 저장한다."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    suffix = out.suffix.lower()
    if suffix in (".html", ".htm"):
        out.write_text(render_submission_html(result), encoding="utf-8")
    elif suffix == ".docx":
        out.write_bytes(render_submission_docx(result))
    elif suffix == ".pdf":
        out.write_bytes(render_submission_pdf(result))
    else:
        out.write_text(render_submission_markdown(result), encoding="utf-8")
    return out
