"""
반복 시리즈 감지 + '지난 제출물' 맥락 (결정적, LLM 0).

실코퍼스(2026-08, 148과제) 관찰: 'N주차 소감문', '3/10 조별활동 보고서',
'실습N 레포트'처럼 같은 틀이 반복되는 시리즈가 과제의 큰 덩어리고, 미제출도
여기 몰린다(미제출 21건 중 13건이 한 시리즈). 새 회차를 쓸 때 같은 시리즈의
내 지난 제출물을 맥락으로 주입하면 문체·구조·전개가 한 번에 잡힌다.

경계선 유지: 자동 복사가 아니라 참고 자료([자료N])로만 제공하고, 내용을 그대로
옮기지 말라는 지침을 소스 본문에 명시한다. 조별 제출물은 내 글이 아니므로 제외.
"""
from __future__ import annotations

import re
from typing import List, Optional

from ..llm.base import SourceDoc

# 회차 표식 — 주차/날짜는 통으로 지우고, 잔여 숫자(실습4·Assignment #1)는 #로.
_WEEK = re.compile(r"\d+\s*주차")
_DATE = re.compile(r"\(?\s*\b\d{1,2}\s*/\s*\d{1,2}(?:\s+\d{1,2}:\d{2})?\s*\)?")
_NUM = re.compile(r"\d+")
_PUNCT = re.compile(r"[#()\[\]{}:~\-·.,_/\s]+")


def series_key(title: str) -> str:
    """제목에서 회차 표식(주차·날짜·번호)을 지운 시리즈 키. 시리즈 아님 → ''.

    '3주차 소감문 (3/17)'과 '5주차 소감문 제출'이 같은 키가 되게 한다.
    숫자가 아예 없는 제목은 반복 회차로 볼 근거가 없으므로 시리즈가 아니다.
    """
    t = (title or "").strip()
    if not t or not _NUM.search(t):
        return ""
    t = _WEEK.sub(" ", t)
    t = _DATE.sub(" ", t)
    t = _NUM.sub("#", t)
    key = _PUNCT.sub(" ", t).strip().lower()
    return key if len(key) >= 2 else ""


#: 같은 산출물의 '단계'를 나타내는 말 — 줄기를 찾을 때 지운다.
#: 실코퍼스(대학 글쓰기) 관찰: `서론 작성`→`서론 수정`,
#: `과제 1 (논문 쓰기) 초고 제출`→`과제 1 (논문) 최종본 제출`처럼 **회차가 아니라
#: 단계로** 이어지는 과제가 많다. series_key는 숫자가 있어야 하고 단계어를 남겨
#: 두므로 이런 쌍을 하나도 못 잡았다(사용자 지적 2026-08-23).
_STAGE_WORDS = (
    "초고", "최종본", "최종", "수정본", "수정", "재제출", "제출", "작성", "쓰기",
    "완성본", "완성", "초안", "발표본", "본문", "과제", "차",
)
_STAGE_RE = re.compile("|".join(re.escape(w) for w in _STAGE_WORDS))


def stage_stem(title: str) -> str:
    """제목에서 단계어·회차·기호를 지운 '무엇에 대한 것인가' 줄기.

    `서론 작성`·`서론 수정` → `서론` / `과제 1 (논문 쓰기) 초고 제출`·
    `과제 1 (논문) 최종본 제출` → `논문`. 줄기가 너무 짧으면(1글자) 빈 문자열 —
    아무 과제나 이어 붙이는 사고를 막는다.
    """
    t = _WEEK.sub(" ", (title or "").strip())
    t = _DATE.sub(" ", t)
    t = _NUM.sub(" ", t)
    t = _STAGE_RE.sub(" ", t)
    t = _PUNCT.sub(" ", t).strip().lower()
    t = " ".join(t.split())
    return t if len(t.replace(" ", "")) >= 2 else ""


def find_stage_predecessors(title: str, submissions: Optional[List[dict]],
                            k: int = 2) -> List[dict]:
    """같은 **줄기**의 내 지난 제출물(단계가 다른 것)을 최신순 k건.

    `find_predecessors`(회차 시리즈)가 아무것도 못 찾을 때의 보완이다. 이쪽은
    '3주차→5주차'가 아니라 '초고→최종본', '작성→수정'을 잇는다.

    같은 제목(재제출)은 제외하고, 본문이 있는 것만 쓴다. 줄기가 비면 아무것도
    하지 않는다 — 줄기 없는 제목끼리 묶으면 관계없는 과제가 맥락에 섞인다.
    """
    stem = stage_stem(title)
    if not stem:
        return []
    hits: List[dict] = []
    for s in submissions or []:
        if not isinstance(s, dict):
            continue
        stitle = str(s.get("title") or "")
        if stitle.strip() == (title or "").strip():
            continue
        if stage_stem(stitle) != stem:
            continue
        if not str(s.get("body") or "").strip():
            continue
        hits.append(s)
    hits.sort(key=lambda s: str(s.get("submitted_at") or ""), reverse=True)
    return hits[:k]


def find_predecessors(title: str, submissions: Optional[List[dict]],
                      k: int = 2) -> List[dict]:
    """같은 시리즈의 내 지난 제출물(본문 있는 것만)을 최신순 k건.

    submissions: [{"title","submitted_at"(ISO),"body"(평문)}...]. 결정적.
    같은 제목(재제출·자기 자신)은 제외 — '지난 회차'만 참고 대상이다.
    """
    key = series_key(title)
    if not key:
        return []
    hits: List[dict] = []
    for s in submissions or []:
        if not isinstance(s, dict):
            continue
        stitle = str(s.get("title") or "")
        if stitle.strip() == (title or "").strip():
            continue
        if series_key(stitle) != key:
            continue
        if not str(s.get("body") or "").strip():
            continue
        hits.append(s)
    hits.sort(key=lambda s: str(s.get("submitted_at") or ""), reverse=True)
    return hits[:k]


def predecessors_to_sources(hits: List[dict],
                            limit_chars: int = 1500) -> List[SourceDoc]:
    """지난 제출물 → Execution 맥락(SourceDoc). 복사 금지 지침을 본문에 명시."""
    out: List[SourceDoc] = []
    for h in hits or []:
        title = str(h.get("title") or "지난 제출물").strip()
        when = str(h.get("submitted_at") or "")[:10]
        head = f"내가 같은 시리즈에서 이전에 제출한 글('{title}'"
        head += f", {when})." if when else ")."
        text = (f"{head}\n"
                "문체·구조·전개 방식 참고용이다 — 내용·소재를 그대로 복사해 "
                "새 회차에 옮기지 말 것(이번 회차의 자료로 새로 쓴다).\n\n"
                + str(h.get("body") or "")[:limit_chars])
        out.append(SourceDoc(title=f"[지난 제출물] {title}", text=text))
    return out


# ── 실험 단위 연결 — lab_report_cycle (COURSE_ALGORITHMS_2026F §4.2) ──────
# series_key()는 회차 번호를 '지워서' 같은 표면형끼리 묶는다(예비보고서 3주차 ↔
# 예비보고서 5주차 = 문체 참고용 시리즈). lab_report_cycle에 필요한 건 정반대다 —
# 실험 번호를 '남겨서' 표면형이 다른 세 단계(예비·랩노트·결과)를 같은 실험으로
# 묶어야 한다('실험 4 결과보고서'를 쓸 때 '예비보고서 4주차'가 맥락이 되게).
# 그래서 series_key()는 한 글자도 건드리지 않고 새 함수를 옆에 둔다 — 표면형 기준
# 시리즈는 지금도 문체·구조 참고 경로(find_predecessors)에서 그대로 쓰인다.

# 실험 번호 추출 — '실험/실습/lab 4'형이 '4주차'형보다 우선(더 명시적 신호).
_EXP_ROUND = re.compile(r"(?:실험|실습|\blab)\s*#?\s*(\d+)", re.I)
_EXP_WEEK = re.compile(r"(\d+)\s*(?:주차|회차)")
# 예비 단계 표면형(설계 §4.2 _LAB_PRE와 동일 어휘) — 예비 제출물만 결과 맥락 후보.
_PRE_TITLE = re.compile(r"예비\s*(?:보고서|레포트)|pre-?lab|사전\s*보고서", re.I)


def experiment_id(title: str) -> str:
    """제목에서 실험 번호를 뽑아 'exp-N'. 번호를 못 찾으면 ''.

    '실험 4 결과보고서'·'예비보고서 4주차'·'랩노트 제출(4주차)'가 전부 'exp-4'.
    번호 추출 규칙(순서대로, 앞 규칙이 잡으면 뒤는 보지 않는다):
      1. '실험/실습/lab N'  — 실험 회차의 가장 명시적인 표기.
      2. 'N주차/N회차'      — 실험과목 실코퍼스는 주차 = 실험 회차.
      3. 잔여 숫자가 정확히 하나면 그 숫자 — '예비보고서 4' 류.
    날짜(3/17)는 실험 번호가 아니므로 먼저 지운다. 잔여 숫자가 둘 이상이면
    어느 것이 실험 번호인지 확정할 수 없으므로 ''(잘못 묶는 것보다 안 묶는 게 안전).
    """
    t = (title or "").strip()
    if not t:
        return ""
    t = _DATE.sub(" ", t)
    m = _EXP_ROUND.search(t) or _EXP_WEEK.search(t)
    if m:
        return f"exp-{int(m.group(1))}"
    nums = _NUM.findall(t)
    if len(nums) == 1:
        return f"exp-{int(nums[0])}"
    return ""


def experiment_pre_sources(title: str, submissions: Optional[List[dict]],
                           limit_chars: int = 1500) -> List[SourceDoc]:
    """같은 실험(experiment_id)의 내 예비보고서 본문 → 결과보고서 맥락(SourceDoc).

    설계 §4.2: "결과보고서는 예비보고서 바탕" — 같은 실험 번호의 예비(pre 단계)
    제출물을 결과보고서 초안의 참고 자료로 주입한다. 입력·반환 형태는
    find_predecessors/predecessors_to_sources와 대칭(제목 + 제출물 rows → SourceDoc).

    조별 제출물 제외 정책 유지: rows는 rows_from_canvas_submissions()가 만들 때
    group_category_id 제출물을 이미 걸러낸다(같은 입구를 쓰는 한 자동 유지).
    경계선: 예비보고서에는 실측값이 없다 — 이 소스로 결과 수치를 만들면 안 된다는
    지침을 소스 본문에 명시한다(§4.2 하드 금지와 같은 원칙).
    """
    eid = experiment_id(title)
    if not eid:
        return []
    hits: List[dict] = []
    for s in submissions or []:
        if not isinstance(s, dict):
            continue
        stitle = str(s.get("title") or "")
        # 자기 자신(재제출)은 맥락이 아니다 — find_predecessors와 같은 정책.
        if stitle.strip() == (title or "").strip():
            continue
        if experiment_id(stitle) != eid:
            continue
        # 예비 단계 제출물만 — 랩노트·결과 등 다른 단계는 이 맥락의 대상이 아니다.
        if not _PRE_TITLE.search(stitle):
            continue
        if not str(s.get("body") or "").strip():
            continue
        hits.append(s)
    hits.sort(key=lambda s: str(s.get("submitted_at") or ""), reverse=True)
    out: List[SourceDoc] = []
    for h in hits[:2]:  # 같은 실험의 예비는 보통 1건 — 재제출 대비 상한 2
        stitle = str(h.get("title") or "예비보고서").strip()
        when = str(h.get("submitted_at") or "")[:10]
        head = f"내가 같은 실험({eid})에 대해 제출한 예비보고서('{stitle}'"
        head += f", {when})." if when else ")."
        text = (f"{head}\n"
                "이론·원리·시약·절차·예상 결과의 근거로만 쓴다 — 예비보고서에는 "
                "실측값이 없으므로, 이 자료로 결과 수치·그래프·관찰을 만들지 말 것"
                "(실측은 랩노트 기록에서만 온다).\n\n"
                + str(h.get("body") or "")[:limit_chars])
        out.append(SourceDoc(title=f"[예비보고서] {stitle}", text=text))
    return out


def rows_from_canvas_submissions(data) -> List[dict]:
    """Canvas my_submissions_json 원본 → find_predecessors 입력 행. 결정적.

    조별 과제(assignment.group_category_id)는 팀 공동 작성물이라 내 문체·구조의
    근거가 아니므로 제외(voice_autolearn과 같은 안전 필터).
    """
    from ..capture.sources.canvas_api import _description_to_text
    rows: List[dict] = []
    for s in data or []:
        if not isinstance(s, dict):
            continue
        assign = s.get("assignment") if isinstance(s.get("assignment"), dict) else {}
        if assign.get("group_category_id"):
            continue
        body_html = s.get("body") or ""
        body = _description_to_text(body_html)[0] if body_html else ""
        rows.append({
            "title": str(assign.get("name") or "").strip(),
            "submitted_at": str(s.get("submitted_at") or "").strip(),
            "body": body,
        })
    return rows
