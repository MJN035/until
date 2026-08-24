"""
과제 유형 분류 — 에세이만이 아니라 문제풀이·보고서·코드·발표까지.

"전기과 학생으로 존재해야 하는 순간"(역할로서 해야 하는 정형 작업)은 유형마다 모양이
다르다. 유형을 감지해 Execution이 유형에 맞는 구조로 끝까지 쓰도록 하고, 경계선(사람
판단=[[DECISION]])은 유형마다 다르게 적용한다(예: 문제풀이의 '정답'은 단정 가능).

LLM 호출 0 — 결정적 키워드 매칭. (understanding은 보통 LLM을 쓰지만 이 분류는 결정적)
"""
from __future__ import annotations

# 유형별 신호 키워드(소문자 비교). 많이 맞을수록 그 유형.
_SIGNALS = {
    "code": [
        "코드", "프로그램", "구현", "implement", "함수", "알고리즘", "파이썬", "python",
        "java", "c++", "컴파일", "디버그", "코딩", "소스", "def ", "class ", "회로 설계",
        "verilog", "matlab", "스크립트",
    ],
    "problemset": [
        "문제", "풀이", "문항", "계산하", "증명", "prove", "solve", "연습문제", "구하시오",
        "구하라", "계산하라", "답을", "값을 구", "유도하", "problem set", "pset", "p-set",
        "풀어", "도출하", "회로 해석",
    ],
    "report": [
        "보고서", "report", "실험", "lab ", "결과 및", "고찰", "논의", "실험 결과",
        "측정", "데이터 분석", "결과를 정리", "실험 보고", "method", "재료 및 방법",
        # eTL 코퍼스 실측(2026-08) — '실습N 레포트'가 essay로 오분류되던 어휘.
        "실습", "레포트", "결과보고서",
    ],
    # 질의 제출 — 강의 듣고 다음 수업 교수에게 할 질문을 미리 제출(기획 T1b).
    # 산출물이 '질문 목록'이라 반응형 보고서와 골격이 다르다(후보 생성→선택).
    # reflective_report보다 dict 순서가 앞이어야 동점 시 이긴다.
    "inquiry": [
        "질문을 제출", "질문 제출", "질문드릴", "질문을 작성",
        "질문 작성", "궁금한 점을 제출",
    ],
    # 참가/활동 보고서 — '보고서'라는 단어 때문에 report(실험 보고서 전제:
    # 방법→결과→고찰)로 오분류되던 유형. 논리구조가 다르다(사실→지식→개인 관점).
    "reflective_report": [
        "참가", "수료", "참여 후", "활동 보고", "후기", "소감", "결과 보고서",
        "워크숍", "워크샵", "특강", "세미나", "캠프", "박람회", "아카데미",
        "수강한 강의", "참관",
    ],
    "presentation": [
        "발표", "ppt", "슬라이드", "presentation", "slide", "프레젠테이션", "발표자료",
        "발표 자료", "피피티",
    ],
    "essay": [
        "에세이", "essay", "논하시오", "서술하시오", "감상", "비평", "논평", "논거",
        "자신의 생각", "자신의 견해", "논지", "고찰하시오", "분석하시오",
        # 실코퍼스(대학 글쓰기 1 기말리포트) — 과제가 "'논증문'이어야 합니다"라고
        # 유형을 명시하는데 어휘에 없어 신호 0으로 떨어졌다.
        "논증문", "논술문", "논설문", "1차자료", "2차자료",
    ],
}

# 사람이 읽는 라벨.
LABELS = {
    "essay": "에세이/논술",
    "report": "보고서/실험",
    "reflective_report": "참가/활동 보고서",
    "inquiry": "질의/질문 제출",
    "problemset": "문제 풀이",
    "code": "코드/구현",
    "presentation": "발표 자료",
    # HDL 실습(v0.2, COURSE_ALGORITHMS_2026F §4.1) — code로 흡수하지 않는다:
    # code는 FACTUAL_TYPES라 결정 0개가 허용되는데, HDL 보고서의 '고찰'은
    # "왜 이 설계를 골랐는가"라는 결정이 반드시 필요하다. 이게 별도 task_type을
    # 만드는 유일한 이유다. classify_task_type의 키워드 감지에는 넣지 않는다 —
    # v0.1 경로에서 이 유형이 분류·발동되면 안 되고, 진입은 파이프라인의
    # strategy 매핑("hdl_lab", algo_version v0.2)으로만 한다.
    "hdl_lab": "HDL 실습",
    "general": "일반 과제",
}

# 신호 가중치 — reflective_report 신호는 report의 일반 신호('보고서')와 자주
# 공존하므로, 존재 자체가 강한 신호인 키워드에 가중을 줘 report보다 우선시킨다.
_WEIGHTS = {"reflective_report": 2, "inquiry": 2}

# 부분문자열 오탐을 피해야 하는 신호는 정규식으로 — '연수'는 '자연수'에,
# '질의'는 안내문 상투구 '질의응답 시간'에 걸린다(감상문이 inquiry로 오분류).
import re as _re
_REGEX_SIGNALS = {
    "reflective_report": [_re.compile(r"(?<!자)연수")],
    "inquiry": [_re.compile(r"질의(?!\s*응답)")],
}

# 정형(사실형) 작업이라 '결정 지점 0개'도 정상인 유형 — 가드의 min_decisions를 강제하지 않는다.
# hdl_lab은 여기 넣지 않는다 — 고찰(설계 선택 근거) 결정이 필수인 유형이다(§4.1).
FACTUAL_TYPES = {"problemset", "code"}


def _spec_text(spec: dict) -> str:
    """명세에서 뽑힌 것만 — 과제가 스스로 밝힌 유형 신호."""
    return " ".join([
        str(spec.get("deliverable") or ""),
        str(spec.get("goal") or ""),
        str(spec.get("title") or ""),
        " ".join(str(r) for r in (spec.get("requirements") or [])),
    ]).lower()


def _docs_text(docs, limit: int = 2000) -> str:
    # 앞부분만(지시문은 보통 처음에 있음).
    return " ".join((getattr(d, "text", "") or "")[:limit]
                    for d in (docs or [])).lower()


def _haystack(spec: dict, docs) -> str:
    return (_spec_text(spec) + " " + _docs_text(docs)).strip()


def _score(text: str) -> dict:
    if not text.strip():
        return {}
    return {t: (sum(1 for kw in kws if kw in text)
                + sum(1 for rx in _REGEX_SIGNALS.get(t, []) if rx.search(text)))
            * _WEIGHTS.get(t, 1)
            for t, kws in _SIGNALS.items()}


def classify_task_type(spec: dict, docs=None) -> str:
    """spec(+원문)에서 과제 유형을 추정한다. 신호가 없으면 'essay'(경계선 보수적 기본)."""
    # 과제 자신의 텍스트(명세 + 과제 문서)를 **먼저** 본다. 여기서 신호가 나오면
    # 거기서 끝낸다 — 수업자료·컨텍스트 번들의 어휘가 유형을 정하면 안 된다.
    #
    # 실코퍼스(대학 글쓰기 1 기말리포트, 2026-08-22): 과제 문서만 보면 essay인데,
    # 글쓰기 강의자료 번들("문제점은 현황에서 도출…")이 붙는 순간 **'문제' 한
    # 단어**로 problemset이 이겼다. 그리고 problemset에는 골격이 없어서 초안이
    # 통째로 부실해졌다(원장 U-1·U-5). 남의 문서 한 단어가 과제 유형을 뒤집는
    # 구조 자체가 문제다.
    docs = list(docs or [])
    primary = (_spec_text(spec) + " " + _docs_text(docs[:1])).strip()
    scores = _score(primary)
    if not scores or max(scores.values()) == 0:
        # 과제 텍스트에 신호가 없을 때만 나머지 자료까지 넓혀 본다(종전 동작).
        scores = _score(_haystack(spec, docs))
    if not scores:
        return "essay"
    best = max(scores, key=lambda t: scores[t])
    if scores[best] == 0:
        # 아무 신호도 없으면 deliverable 추정으로 폴백, 그래도 모르면 essay.
        deliv = str(spec.get("deliverable") or "").lower()
        if any(k in deliv for k in ("코드", "code", "구현")):
            return "code"
        if any(k in deliv for k in ("보고서", "report", "실험")):
            return "report"
        if any(k in deliv for k in ("발표", "presentation", "slide")):
            return "presentation"
        return "essay"
    return best
