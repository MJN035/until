"""업로드 슬롯 감지 — 명세에서 '실제 과제 내용'이 몇 자인지 잰다(결정적·LLM 0).

eTL 실코퍼스에는 본문이 마감·분반·제출 안내(로지스틱스)뿐인 제출함이 많다
(실험 예비보고서 슬롯·회의록 제출함 등 기초회로 코퍼스 15건). 이런 과제는
컨텍스트 번들이 붙어 있어도 쓸 원료가 없으므로 material_gap(원료 없음) 경로로
보내야 한다 — 200자를 억지로 채운 그럴듯한 초안이 아니라 질문이 정답이다.
"""
from __future__ import annotations

import re

# 수집기(spec.md)의 메타데이터 헤더 줄 — 과제 내용이 아니다.
_META_PREFIX = ("과목:", "학기:", "출처:", "과제ID:", "마감:")
# 제출·마감·분반 안내 등 행정(로지스틱스) 문장 신호 — 결정적 판정.
_LOGISTICS = re.compile(
    r"제출해?\s*주시기|제출\s*바랍니다|제출하면\s*됩니다|업로드|다운로드|"
    r"문의\s*사항|문의사항|게시판|분반|까지\s*제출|기한|마감|지각|"
    r"클릭|첨부\s*파일|파일명|양식에\s*맞게|형식으로\s*제출|"
    r"워드로|타이핑|손글씨|스캔|Q\s*&\s*A", re.I)
_URL = re.compile(r"https?://\S+")


def substantive_chars(text: str) -> int:
    """명세 텍스트에서 로지스틱스·메타데이터를 뺀 실내용 글자 수."""
    total = 0
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if any(line.startswith(p) for p in _META_PREFIX):
            continue
        line = _URL.sub("", line)
        if _LOGISTICS.search(line):
            continue
        total += len(line)
    return total


# 과제 본문이 가리키는 첨부 파일명 — "HW1.pdf" 한 줄이 곧 과제 내용인 경우가 많다.
# 확장자는 우리가 실제로 파싱할 수 있는 것 + 흔한 배포 형식.
_ATTACH_RE = re.compile(
    r"[\w가-힣][\w가-힣 .\-()#]{0,60}?\.(?:pdf|hwpx?|docx?|pptx?|xlsx?|zip|txt|md|ipynb|rmd)",
    re.I)


def referenced_attachments(text: str) -> list:
    """명세 본문이 이름으로 가리키는 첨부 파일 목록(중복 제거, 등장 순서).

    URL은 먼저 지운다 — 링크 끝의 `...download.pdf`가 첨부 이름으로 잡히면
    "없는 파일을 달라"는 엉뚱한 요청이 된다.
    """
    body = _URL.sub(" ", text or "")
    out, seen = [], set()
    for m in _ATTACH_RE.finditer(body):
        name = m.group(0).strip()
        key = name.lower()
        if key not in seen:
            seen.add(key)
            out.append(name)
    return out


def missing_attachments(text: str, docs) -> list:
    """본문이 가리키는데 **읽어 온 문서에는 없는** 첨부 이름.

    이게 비어 있지 않으면 '자료가 없다'가 아니라 **'어느 파일이 없는지 안다'**는
    뜻이다. 그 차이가 크다 — 막연히 원료를 달라고 하는 대신 파일 하나를 콕
    집어 요청할 수 있다(2026-08-22 실측: 물리학1 HW#1의 명세는 마감 + `HW1.pdf`
    한 줄뿐이고 그 PDF가 수집되지 않았는데, 시스템은 그 사실을 모른 채 숙제
    내용을 추측해 1,000자 넘게 써냈다).
    """
    import os
    # 명세 문서 말고 **실제로 읽어 온 파일이 하나라도 있으면** 첨부가 없다고
    # 단정하지 않는다. 이름이 조금만 달라도(다운로드명·URL 유래명) 있는 파일을
    # '없다'고 말하게 되고, 그러면 원료가 있는데도 원료 없음 경로로 떨어져
    # 유형 지침이 꺼진다 — 물리 숙제에서 문제 풀이 과정·공식이 사라진 회귀가
    # 이것이다(2026-08-23 사용자 보고). 놓치는 쪽이 지어내는 쪽보다 안전하다.
    if len(list(docs or [])) > 1:
        return []
    have = set()
    for d in docs or []:
        src = str(getattr(d, "source", "") or "")
        if src:
            have.add(os.path.basename(src).lower())
    out = []
    for name in referenced_attachments(text):
        base = os.path.basename(name).lower()
        if base not in have and not any(base in h or h in base for h in have):
            out.append(name)
    return out
