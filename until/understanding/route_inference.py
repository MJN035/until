"""spec_clarification 폴백 — 수집 컨텍스트로 처리 경로를 LLM이 추정(근거 인용 강제).

결정적 라우터(16규칙)가 어느 규칙에도 걸리지 않으면 spec_clarification으로
'묻기'만 남는다(실코퍼스 기여자 B 44%). 그중 다수는 명세가 제목·본문이 아니라
첨부·공지·컨텍스트 번들에 살아 있다 — 사람이 읽으면 알 수 있는 것을 되묻지
않도록, 여기서 LLM 1회(보조 패스·경량 티어)로 기존 strategy 중 하나를 추정한다.

환각 가드(결정적): LLM이 근거로 댄 인용문이 실제로 모델에게 보여 준 발췌 텍스트에
부분문자열로 존재해야만 채택한다(unsourced_claim·evidence.py와 같은 발상).
인용 검증 실패·확신 낮음·unknown·파싱 실패·mock이면 None을 돌려 기존
spec_clarification(묻기)을 유지한다 — 검증된 추정만 질문을 대체한다.
non_actionable(과제 아님 판정)은 LLM에 맡기지 않는다(실과제 누락 위험).
"""
from __future__ import annotations

import json as jsonlib
import re
from typing import Iterable, Optional

from ..context.assignment_router import AssignmentRoute
from ..llm.base import LLMClient

# LLM이 고를 수 있는 경로 — 결정적 라우터의 '내용 기반' strategy만.
# 첨부 구조가 근거인 경로(rmd_notebook·zip_project)와 제외 판정(non_actionable),
# '명세가 딴 곳' 계열(distributed_spec)은 추정 대상이 아니다.
_ROUTE_TABLE: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "evidence_report": (
        "실험·실습·조사 보고서",
        ("실습 지시서", "실측 결과·사진", "보고서 형식"),
        ("직접 얻은 결과·관찰·오류가 무엇인가요?",)),
    "staged_writing": (
        "글쓰기 단계 또는 장문 과제",
        ("현재 단계 지시", "직전 단계 제출물", "피드백"),
        ("주제·관점이 정해지지 않았다면 후보 중 직접 선택해주세요.",)),
    "code_project": (
        "코드 구현 산출물",
        ("입출력·제약", "제공 코드", "테스트 기준"),
        ("실행 환경과 제출 파일 구조가 명세에 없으면 알려주세요.",)),
    "presentation_conversion": (
        "발표·슬라이드·스피치 산출물",
        ("선행 글·개요", "발표 범위", "시간·형식 조건"),
        ("발표 범위와 시간을 확인할 수 없으면 알려주세요.",)),
    "team_project": (
        "팀 합의와 역할 분담이 필요한 공동 산출물",
        ("최종 산출물 형식", "팀 합의본·공유 파일", "본인 담당 범위", "마감·평가 기준"),
        ("팀이 합의한 방향과 본인 담당 부분은 무엇인가요?",
         "다른 팀원의 미완성 부분은 대신 작성하지 않고 빈칸으로 둘까요?")),
    "activity_form": (
        "실제 활동 사실을 양식에 기록",
        ("원본 양식", "참여자·활동·결과", "사진 요구 여부"),
        ("실제로 누가 무엇을 했고 결과가 어땠나요?",)),
    "reflective_series": (
        "반복형 강의 소감",
        ("해당 주차 강의 메모·자료", "본인 인상·적용 계획"),
        ("인상 깊었던 대목과 본인 경험 연결점은 무엇인가요?",)),
    "weekly_inquiry": (
        "주차별 사전 질의",
        ("질의 순번 공지", "담당 교수·강연 주제", "프로필 학번"),
        ("질의 순번표에서 본인 학번을 찾을 수 있나요?",)),
}

ROUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "strategy": {"type": "string",
                     "enum": sorted(_ROUTE_TABLE) + ["unknown"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "evidence_quotes": {"type": "array", "items": {"type": "string"}},
        "deliverable": {"type": "string"},
    },
    "required": ["strategy", "confidence", "evidence_quotes", "deliverable"],
    "additionalProperties": False,
}

_SYSTEM = """\
당신은 대학 과제의 '산출물 형식'을 판정하는 분석기다. 과제 명세·첨부·공지
발췌를 읽고, 무엇을 제출하는 과제인지 아래 strategy 중 하나로 판정하라.

- evidence_report: 실험·실습·조사 결과를 정리한 보고서
- staged_writing: 에세이·서평 등 글쓰기(단계형 포함)
- code_project: 코드·프로그램 구현물
- presentation_conversion: 발표 슬라이드·스피치
- team_project: 팀 공동 산출물
- activity_form: 활동 사실을 양식에 기록(일지·회의록)
- reflective_series: 강의 소감·성찰
- weekly_inquiry: 주차별 사전 질문 제출

규칙:
- evidence_quotes에는 판정 근거가 된 문장을 제공된 발췌에서 '원문 그대로'
  복사해 담는다(1~3개). 발췌에 없는 문장을 지어내면 판정 전체가 폐기된다.
- 발췌만으로 산출물 형식을 확정할 수 없으면 strategy를 unknown으로 하라.
  억지로 고르는 것보다 unknown이 낫다.
- confidence: 발췌에 산출물 형식이 명시돼 있으면 high, 정황 추정이면 medium 이하.
- deliverable에는 제출물을 한 구절로 요약한다(예: "실험 결과 보고서 1부").
"""

_MAX_PER_DOC = 1500
_MAX_TOTAL = 6000
_MIN_QUOTE_CHARS = 8


def _norm(s: str) -> str:
    return " ".join(str(s or "").split())


def _build_excerpt(docs: Iterable[object], context_sources: Iterable[object]) -> str:
    parts: list[str] = []
    total = 0
    for d in list(docs or []) + list(context_sources or []):
        text = _norm(getattr(d, "text", "") or "")[:_MAX_PER_DOC]
        if not text:
            continue
        name = str(getattr(d, "source", "") or getattr(d, "title", "") or "자료")
        parts.append(f"[{name}]\n{text}")
        total += len(text)
        if total >= _MAX_TOTAL:
            break
    return "\n\n".join(parts)


def infer_route(spec: dict, docs, context_sources,
                llm: Optional[LLMClient]) -> Optional[AssignmentRoute]:
    """검증된 추정 라우트 또는 None(묻기 유지). 예외를 밖으로 내지 않는다."""
    if llm is None:
        return None
    excerpt = _build_excerpt(docs, context_sources)
    if len(excerpt) < 80:  # 읽을 원료 자체가 없으면 추정할 수 없다 — 묻는 게 맞다.
        return None
    try:
        title = str(spec.get("deliverable") or spec.get("goal") or "") if isinstance(spec, dict) else ""
        user = (f"[과제 제목·목표]\n{_norm(title)}\n\n[명세·첨부·공지 발췌]\n{excerpt}"
                "\n\n위 발췌를 근거로 산출물 형식을 판정하라.")
        res = llm.complete(_SYSTEM, user, tag="route-inference", json=True,
                           schema=ROUTE_SCHEMA)
        data = jsonlib.loads(res.text)
    except Exception:
        return None
    strategy = str(data.get("strategy") or "")
    if strategy not in _ROUTE_TABLE or data.get("confidence") != "high":
        return None
    # 환각 가드 — 인용문이 모델에게 보여 준 발췌에 실제로 있어야 한다.
    norm_excerpt = _norm(excerpt)
    quotes = [_norm(q) for q in (data.get("evidence_quotes") or [])
              if isinstance(q, str)]
    verified = [q for q in quotes
                if len(q) >= _MIN_QUOTE_CHARS and q in norm_excerpt]
    if not verified:
        return None
    # 2단 판정 — 자주 틀리는 '인접 유형'이면 짧은 이지선다로 재확인(라이브 실측:
    # 채택 정확도 82%→96%, 악화 0). 1단 프롬프트는 건드리지 않아 스키마 준수를
    # 유지한다(단일 프롬프트를 부풀린 변형은 0%로 붕괴했음). 인용 검증된
    # 다운그레이드만 인정 → 안전 케이스의 오플립을 막는다.
    strategy = _disambiguate(llm, strategy, norm_excerpt)
    reason, evidence, questions = _ROUTE_TABLE[strategy]
    quote = verified[0][:80]
    return AssignmentRoute(
        strategy, f"{reason} — 컨텍스트 근거 추정(“{quote}”)",
        evidence, questions)


# ── 2단 판정 — 인접 유형 이지선다(라이브 귀납) ─────────────────────────────
# 1단이 자주 혼동하는 5개 인접쌍. 각 쌍의 한 줄 판별로 짧은 A/B만 묻는다.
_ADJ_DEFS = {
    "staged_writing": "스스로 주제를 세워 논증·설명·서평·요약문·주제문·개요 등 '글 자체'가 산출물",
    "reflective_series": "특정 강의·경험·시청물에 대한 감상·소감·독후감·서평적 '반응'",
    "presentation_conversion": "최종물이 '발표'이고 그 준비(주제제안서·개요·스크립트·슬라이드)까지 포함",
    "activity_form": "본인이 실제로 한 활동·측정 사실을 양식·일지·계획서에 '기록'",
    "weekly_inquiry": "주차별 사전 '질문(질의)'을 작성해 제출",
    "evidence_report": "실험·조사로 얻은 '데이터·관찰 결과'를 분석한 보고서",
}
# (a, b) — 1단이 a로 채택됐을 때 b일 가능성을 이지선다로 재확인한다.
# staged_writing이 가장 자주 과채택되므로 그 쌍들을 먼저 둔다(우선순위 단락).
_ADJ_PAIRS = (
    ("staged_writing", "reflective_series"),
    ("staged_writing", "presentation_conversion"),
    ("staged_writing", "activity_form"),
    ("staged_writing", "weekly_inquiry"),
    ("evidence_report", "activity_form"),
)

_STAGE2_SCHEMA = {
    "type": "object",
    "properties": {
        "choice": {"type": "string", "enum": ["A", "B"]},
        "quote": {"type": "string"},
    },
    "required": ["choice", "quote"],
    "additionalProperties": False,
}

_STAGE2_SYSTEM = """\
과제 발췌를 읽고 산출물이 A와 B 중 어느 쪽인지 하나만 고른다.
- quote에는 판단 근거가 된 문장을 발췌에서 '원문 그대로' 복사한다.
- 애매하면 A를 고른다(1차 판정 유지가 기본).
JSON {choice:"A"|"B", quote:"..."} 만 출력한다.
"""


def _stage2_binary(llm, norm_excerpt: str, a: str, b: str) -> bool:
    """A(a)/B(b) 이지선다 — B로 판정 + 인용 검증 통과 시에만 True(=flip)."""
    user = (f"A: {_ADJ_DEFS[a]}\nB: {_ADJ_DEFS[b]}\n\n"
            f"[발췌]\n{norm_excerpt[:_MAX_TOTAL]}\n\nA와 B 중 하나만 고르라.")
    try:
        res = llm.complete(_STAGE2_SYSTEM, user, tag="route-stage2",
                           json=True, schema=_STAGE2_SCHEMA)
        data = jsonlib.loads(res.text)
    except Exception:
        return False
    if data.get("choice") != "B":
        return False
    q = _norm(data.get("quote") or "")
    return len(q) >= _MIN_QUOTE_CHARS and q in norm_excerpt


def _disambiguate(llm, strategy: str, norm_excerpt: str) -> str:
    """1단 strategy가 인접쌍 멤버면 이지선다로 재확인. 첫 검증된 flip에서 단락."""
    for a, b in _ADJ_PAIRS:
        if strategy != a:
            continue
        if _stage2_binary(llm, norm_excerpt, a, b):
            return b  # 인용 검증된 다운그레이드 — 단락(충돌 없음, 라이브 실측)
    return strategy


_INFERRED_RE = re.compile(r"컨텍스트 근거 추정")


def is_inferred(route: AssignmentRoute) -> bool:
    """이 라우트가 LLM 추정으로 채택됐는지(UI·텔레메트리 표시용)."""
    return bool(route and _INFERRED_RE.search(route.reason or ""))


# ── 가드 거절 후의 2차 시도 — 능동형 묻기(후보 추정) ─────────────────────
# infer_route가 거절한 과제를 "무엇을 제출하는지 확인해주세요" 한 줄로 끝내지
# 않는다. 라우트는 확정하지 않되(가드 원칙: 몰래 배정 금지), AI가 할 수 있는
# 최대치 — 유형 후보 2개+근거, 필요 원료 목록, 선택 질문 하나 — 를 만들어
# '묻기'를 질의 유형의 "후보 제시+선택 1결정" 패턴으로 승격시킨다.
# 후보의 인용 검증은 요구하지 않는다: 산출물이 '추정임이 명시된 질문'이라
# 틀려도 사용자가 한 번의 선택으로 교정한다(초안에 몰래 스며들지 않음).

CANDIDATES_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "strategy": {"type": "string",
                                 "enum": sorted(_ROUTE_TABLE)},
                    "rationale": {"type": "string"},
                },
                "required": ["strategy", "rationale"],
                "additionalProperties": False,
            },
        },
        "needed_materials": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["candidates", "needed_materials"],
    "additionalProperties": False,
}

_CANDIDATES_SYSTEM = """\
당신은 산출물 형식이 불확실한 대학 과제의 '가장 그럴듯한 후보'를 좁히는 분석기다.
발췌만으로 확정할 수 없는 상황이다 — 확신할 필요 없다. 추정이 목적이다:
- 본문이 비어도 **과목명·학기·제목의 표면 단서를 적극 활용**하라. 예:
  컴퓨터·프로그래밍 과목의 'Day-N'은 실습 코드 제출일 가능성, 체육 과목의
  자유 서술은 소감·경험 서술일 가능성, 세미나 과목은 질문·소감 제출일 가능성.
- candidates: 가능성 높은 순으로 최대 2개. 각 항목은 정확히 두 키 —
  "strategy"(아래 값 중 하나 그대로)와 "rationale"(그 단서를 근거로 사람이
  읽고 판단할 한 줄, 추정임을 전제).
  strategy 허용값: evidence_report(실험·조사 보고서) | staged_writing(글쓰기)
  | code_project(코드 구현물) | presentation_conversion(발표 자료)
  | team_project(팀 산출물) | activity_form(활동 기록 양식·일지)
  | reflective_series(강의 소감·성찰) | weekly_inquiry(사전 질문 제출)
  rationale과 needed_materials는 반드시 한국어로 쓴다.
- needed_materials: 형식을 확정하거나 초안을 시작하는 데 필요한 자료·정보를
  사용자가 찾아올 수 있는 구체적 문자열로 1~3개(예: "과제 지시서 원문",
  "수업에서 배부한 양식", "교재 몇 장 몇 번인지").
- 빈 배열은 과목명·제목에서도 단서를 '정말 아무것도' 못 얻을 때만.
"""

_KO_LABEL = {
    "evidence_report": "실험·조사 보고서",
    "staged_writing": "글쓰기(에세이·서평 등)",
    "code_project": "코드 구현물",
    "presentation_conversion": "발표 자료",
    "team_project": "팀 공동 산출물",
    "activity_form": "활동 기록 양식",
    "reflective_series": "강의 소감·성찰",
    "weekly_inquiry": "사전 질문 제출",
}


def clarify_candidates(spec: dict, docs, context_sources,
                       llm: Optional[LLMClient]
                       ) -> Optional[tuple[AssignmentRoute, list[dict]]]:
    """(능동형 spec_clarification 라우트, 후보 목록) 또는 None(기존 묻기 유지)."""
    if llm is None:
        return None
    excerpt = _build_excerpt(docs, context_sources)
    try:
        title = str(spec.get("deliverable") or spec.get("goal") or "") \
            if isinstance(spec, dict) else ""
        user = (f"[과제 제목·목표]\n{_norm(title)}\n\n[명세·첨부·공지 발췌]\n"
                f"{excerpt or '(발췌 없음)'}\n\n후보와 필요 자료를 제시하라.")
        res = llm.complete(_CANDIDATES_SYSTEM, user, tag="route-candidates",
                           json=True, schema=CANDIDATES_SCHEMA)
        data = jsonlib.loads(res.text)
    except Exception:
        return None
    cands = [c for c in (data.get("candidates") or [])
             if isinstance(c, dict) and c.get("strategy") in _ROUTE_TABLE
             and str(c.get("rationale") or "").strip()][:2]
    if not cands:
        return None
    materials = [str(m).strip() for m in (data.get("needed_materials") or [])
                 if str(m).strip()][:3]
    # 선택 질문 — 후보를 사람 언어로, 추정임을 명시. 답 한 번이면 경로가 선다.
    named = " / ".join(
        f"{_KO_LABEL.get(c['strategy'], c['strategy'])}"
        f"({str(c['rationale'])[:40]})" for c in cands)
    questions = [f"이 과제는 {named} 중 하나로 보입니다(추정) — 어느 쪽이 맞나요? "
                 "둘 다 아니면 제출물 형식을 알려주세요."]
    if materials:
        questions.append("확정에 필요한 자료: " + " · ".join(materials)
                         + " — 찾아서 붙여넣어 주시면 바로 이어갑니다.")
    route = AssignmentRoute(
        "spec_clarification",
        "형식 미확정 — AI 후보 추정으로 좁힘(확정은 사용자 몫)",
        ("과제 본문", "첨부", "공지·모듈", "제출 형식"),
        tuple(questions))
    return route, cands
