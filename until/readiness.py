"""제출 준비 점검 — 결정적 점검들(마감·분량·인용·결정·경계선)을 한 요약으로 묶는다.

이미 있는 결정적 헬퍼(length_target·deadline·citation_coverage)와 Draft 상태를 모아
'제출 전에 사람이 확인할 것'을 한눈에 보여준다. LLM 0. 경계선 철학 유지 — 남은 결정은
'해결하라'가 아니라 '당신이 정할 곳'으로 안내만 한다.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List

from .pipeline import Result


@dataclass
class ReadinessItem:
    label: str          # 마감 | 분량 | 인용 | 결정 | 경계선 | 실측
    status: str         # ok | warn | info | fail(경고보다 심각 — 제출 게이트가 하드 블록)
    message: str


@dataclass
class Readiness:
    items: List[ReadinessItem] = field(default_factory=list)

    @property
    def warnings(self) -> List[ReadinessItem]:
        # fail은 warn보다 심각도가 높을 뿐 같은 '확인 필요' 부류 — 경고 집계·표면화
        # 소비부(카운트·간단 모드 한 줄)가 fail을 누락하지 않도록 함께 포함한다.
        return [i for i in self.items if i.status in ("warn", "fail")]

    @property
    def headline(self) -> str:
        n = len(self.warnings)
        if n == 0:
            return "제출 준비 양호 — 확인할 경고 없음"
        return f"제출 전 확인 {n}건"

    def to_dict(self) -> dict:
        """툴 연동용 직렬화 — 헤드라인·경고수·항목 목록(label/status/message).

        items는 심각도순(fail→warn→info→ok, 동순위는 원래 순서) — 툴이 경고를 먼저 본다.
        사람용 표면(render_readiness_lines)은 의미 순서를 유지한다.
        """
        rank = {"fail": -1, "warn": 0, "info": 1, "ok": 2}
        ordered = sorted(self.items, key=lambda i: rank.get(i.status, 3))
        return {
            "headline": self.headline,
            "n_warnings": len(self.warnings),
            "items": [
                {"label": i.label, "status": i.status, "message": i.message}
                for i in ordered
            ],
        }


def assess_readiness(result: Result) -> Readiness:
    """Result에서 결정적 점검을 모아 Readiness로. 감지 안 된 항목은 건너뛴다.

    과제 유형(spec.task_type)에 맞춰 경고 강도를 조정한다 — 정형(문제풀이·코드)은
    수업자료 인용이 필수가 아니라 미인용을 경고로 올리지 않는다(억지 경고 방지).
    """
    from datetime import date
    from .understanding.task_type import FACTUAL_TYPES
    r = Readiness()
    draft = result.final_draft or result.draft
    body = draft.body if draft else ""
    task_type = (result.spec or {}).get("task_type")
    is_factual = task_type in FACTUAL_TYPES

    # 규정 — 자필 제출 규정이 감지되면 학습 보조 모드로 강등됐음을 안내(info).
    # 경고가 아니라 '왜 답안이 없는가'의 설명이다(제품 신뢰 — 기획 T4).
    gate_reason = (result.spec or {}).get("integrity_gate")
    if gate_reason:
        r.items.append(ReadinessItem(
            "규정", "info",
            f"{gate_reason} — 최종 답안 대신 학습 보조(개념·예제·검산)만 담았어요"))

    # 이번 주차 질의는 내 차례가 아니다 — 안 해도 되는 과제라고 먼저 말한다.
    # 순번표에 다른 학생 학번이 실제로 있고 내 것이 없을 때만 켜진다(판단이
    # 불확실하면 아예 안 켜진다 — 잘못 분류하면 진짜 과제를 놓친다).
    if (result.spec or {}).get("inquiry_not_my_turn"):
        r.items.append(ReadinessItem(
            "차례", "info",
            "순번표에 이번 주차 담당으로 본인 학번이 없어요 — 안 해도 되는 "
            "주차로 보입니다. 순번표가 바뀌었다면 그대로 제출하셔도 됩니다."))

    # 자료(원료 없음) — 첨부·맥락 자료가 없어 초안이 골격까지만 작성됐음을 안내.
    if (result.spec or {}).get("material_gap"):
        r.items.append(ReadinessItem(
            "자료", "info",
            "핵심 원료(강의 내용·실측 데이터)가 자료에 없어 초안은 골격까지만 — "
            "결정 칸에 원료를 답하면 마저 채울 수 있어요"))

    # 자료 — 파싱 실패로 빠진 첨부는 경고(초안이 그 자료 없이 쓰였음).
    cw = getattr(result, "capture_warnings", None) or []
    if cw:
        first = cw[0].split(":")[0]
        more = f" 외 {len(cw) - 1}건" if len(cw) > 1 else ""
        r.items.append(ReadinessItem(
            "자료", "warn", f"첨부 {len(cw)}개 파싱 실패({first}{more}) — 초안이 이 자료 없이 작성됨"))

    # 양식 — 양식 첨부(표·항목)가 있으면 초안이 그 구조를 유지했는지 시스템이
    # 검증해 근거를 보여준다('양식이 맞나?'를 사용자에게 떠넘기지 않기 — 실사용 불만).
    from .capture.formfill import check_form_fidelity, detect_form
    for doc in getattr(result, "documents", None) or []:
        text = getattr(doc, "text", "") or ""
        if not detect_form(text).is_form:
            continue
        fid = check_form_fidelity(text, body)
        if fid is not None:
            r.items.append(ReadinessItem(
                "양식", "ok" if fid.ok else "warn", fid.message))
        break  # 양식 문서 하나만(첫 번째)

    # 마감 — 임박(3일 이내)·지남은 warn.
    dl = getattr(result, "deadline", None)
    if dl is not None:
        days = dl.days_from(date.today())
        status = "warn" if days <= 3 else "info"
        r.items.append(ReadinessItem("마감", status, dl.dday_label(date.today())))

    # 분량 — 부족/초과/항목 수 불일치는 warn. '판정 불가(unknown)'도 통과처럼
    # 보이면 안 되므로 warn(양식을 무시한 산문이 ✅로 보이던 구멍).
    lt = getattr(result, "length_target", None)
    if lt is not None:
        from .capture.formfill import expected_item_count
        from .understanding.length_target import check_length
        expected = None
        if getattr(lt, "per_item", ""):
            for doc in getattr(result, "documents", None) or []:
                expected = expected_item_count(getattr(doc, "text", "") or "")
                if expected:
                    break
        chk = check_length(lt, body, expected_items=expected)
        status = "ok" if chk.status == "ok" else "warn"
        r.items.append(ReadinessItem("분량", status, chk.message))

    # 인용 — 미인용/가짜번호는 warn, 부분은 info.
    # 자료가 없어도 본문에 가짜 [자료N]이 있으면 invalid로 표면화되므로 항상 점검.
    from .context.citation_coverage import citation_coverage
    cov = citation_coverage(getattr(result, "sources", None), body)
    if cov.status == "invalid":
        status = "warn"          # 가짜(범위 밖·자료 없음) 인용은 유형 무관하게 경고.
    elif cov.status == "uncited":
        # 정형(문제풀이·코드)은 수업자료 인용이 필수가 아니므로 안내로만.
        status = "info" if is_factual else "warn"
    elif cov.status == "partial":
        status = "info"
    else:
        status = "ok"
    if cov.status != "none":
        r.items.append(ReadinessItem("인용", status, cov.message))

    # 근거 — 참고 자료(과제 문서 제외)가 하나도 없는데 실명·수치 사례가 [출처?]
    # 없이 확신조로 쓰였으면 경고(라이브 관측: MIT 등 무근거 실명 사례).
    # 참고 자료가 있으면 위 인용 점검이 커버리지로 담당한다.
    refs = [s for s in (getattr(result, "sources", None) or [])
            if not str(s).startswith("과제:")]
    if not refs and body and not is_factual:
        from .context.citation_coverage import unsourced_claim_sentences
        claims = unsourced_claim_sentences(body)
        if claims:
            r.items.append(ReadinessItem(
                "근거", "warn",
                f"참고 자료 없이 실명·수치 사례 {len(claims)}문장이 확신조로 쓰임 — "
                "사실 확인 후 제출하세요(또는 자료 첨부로 재생성)"))

    # 실측 — hdl_lab·lab_report_cycle(result) 초안에서 근거 없는 실측 수치를
    # 사후 검출(결정적). LLM이 measured_ban 지침을 무시해 수치를 지어내도
    # 코드로 잡는다(CLAUDE.md "🚫 타협 불가 — 수치 날조 금지").
    route = getattr(result, "assignment_route", None)
    route_strategy = getattr(route, "strategy", "") or ""
    route_stage = getattr(route, "stage", "") or ""
    if route_strategy == "hdl_lab" or (route_strategy == "lab_report_cycle"
                                        and route_stage == "result"):
        from .understanding.measured_check import find_ungrounded_measurements
        evidence_texts = [getattr(sd, "text", "") or ""
                           for sd in (getattr(result, "source_docs", None) or [])]
        spec_reqs = (result.spec or {}).get("requirements")
        if isinstance(spec_reqs, list):
            evidence_texts.extend(str(x) for x in spec_reqs)
        ungrounded = find_ungrounded_measurements(
            body, evidence_texts, strategy=route_strategy, stage=route_stage)
        if ungrounded:
            # 로드맵 Tier2-6 — 경고→차단 승격. 발견 1건 이상이면 fail(제출 게이트가
            # 하드 블록으로 이어받는다). UNTIL_MEASURED_ENFORCE=0이면 기존(경고만) 동작.
            from .config import measured_enforce_active
            r.items.append(ReadinessItem(
                "실측", "fail" if measured_enforce_active() else "warn",
                f"근거 없는 실측 수치 {len(ungrounded)}건 — 파형·합성·측정값은 "
                "실제 데이터만, 없으면 빈칸으로 두세요"))

    # 과제 유형별 점검 — 로컬 에이전트 런타임의 결정적 검증기를 웹에서도 쓴다.
    # 그쪽 플러그인들과 **같은 규칙**을 쓰되(같은 판정기·같은 패턴), 모델 호출이
    # 필요 없는 것만 옮겼다. 코드 실행은 못 옮긴다 — 서버에서 학생 코드를 돌리는
    # 건 별개의 인프라·보안 문제다.
    r.items.extend(_type_specific_items(result, body, route_strategy))

    # 실행 — 별도 러너가 과제 테스트를 실제로 돌린 결과(웹이 붙였을 때만).
    # 문법 검사와 달리 이건 '동작'을 말한다.
    from .runner.assemble import summarize
    verdict = summarize(getattr(result, "run_check", None))
    if verdict is not None:
        r.items.append(ReadinessItem("실행", verdict[0], verdict[1]))

    # 지난 피드백 — 이전 과제에서 받은 교수 코멘트·루브릭이 참고됐음을 상기(info).
    # 웹이 학습된 피드백을 result.teacher_feedback으로 붙일 때만 나타난다.
    tf = getattr(result, "teacher_feedback", None) or []
    if tf:
        from .context.teacher_feedback import feedback_summary
        msg = feedback_summary(tf)
        if msg:
            r.items.append(ReadinessItem("피드백", "info", msg))

    # 결정 — 남은(미답) 결정은 사람이 정할 곳(info, 경고 아님).
    n_dec = draft.n_decisions if draft else 0
    if n_dec:
        r.items.append(ReadinessItem(
            "결정", "info", f"당신이 정할 곳 {n_dec}곳 남음 — 채우면 완성에 가까워집니다"))
    elif not is_factual:
        # 정형(문제풀이·코드)은 결정 0개가 정상(min_decisions=0)이라 경고 대상 아님.
        # 그 외 유형에서 결정 0개 + 가드 미통과면 경계선 넘었을 위험 안내.
        g = result.final_guard or result.guard
        if g and not g.passed:
            r.items.append(ReadinessItem("경계선", "warn", "BoundaryGuard 미통과 — 초안 검토 필요"))

    r.items += _format_items(result)
    return r


def _format_items(result: "Result") -> List[ReadinessItem]:
    """형식 검증기(execution/format_guard)의 결과를 점검 줄로.

    **고친 것도 반드시 알린다.** 몰래 고치면 학생은 자기가 쓴 줄 알고 낸다 — 자동
    채움 고지와 같은 규칙이다. 고친 것은 info(할 일이 없다), 못 고친 것은 warn
    (사람이 맞춰야 한다).
    """
    issues = list(getattr(result, "format_issues", None) or [])
    if not issues:
        return []
    out = []
    fixed = [i for i in issues if i.fixed and i.fix_note]
    if fixed:
        out.append(ReadinessItem(
            "형식", "info",
            f"형식 {len(fixed)}건을 맞춰 뒀어요 — " + " / ".join(i.fix_note for i in fixed[:3])))
    remaining = [i for i in issues if not i.fixed]
    if remaining:
        out.append(ReadinessItem(
            "형식", "warn",
            "제출 전에 맞출 것 — " + " / ".join(i.message for i in remaining[:4])))
    return out


def render_readiness_lines(readiness: Readiness) -> List[str]:
    """Markdown/CLI 공용 — 상태 아이콘이 붙은 점검 줄 목록."""
    icon = {"ok": "✅", "warn": "⚠️", "fail": "🚫", "info": "•"}
    return [f"{icon.get(i.status, '•')} [{i.label}] {i.message}" for i in readiness.items]


# ── 과제 유형별 점검 ─────────────────────────────────────────────────


def _type_specific_items(result: "Result", body: str, strategy: str) -> List[ReadinessItem]:
    """산출물 모양에 맞는 점검만 돌려준다. 해당 없으면 빈 목록.

    로컬 에이전트 런타임의 플러그인들과 같은 판정기를 쓴다 — 두 표면이 다른
    기준으로 판정하면 "CLI에선 걸리는데 웹에선 안 걸린다"가 된다.
    """
    items: List[ReadinessItem] = []
    if not body.strip():
        return items
    evidence = _evidence_texts(result)

    items.extend(_code_items(body, strategy, evidence))
    if strategy == "presentation_conversion":
        items.extend(_slide_items(body))
    if strategy == "activity_form":
        items.extend(_activity_items(body, evidence))
    return items


def _evidence_texts(result: "Result") -> list:
    """근거로 인정할 텍스트 — 수집한 자료 + 명세 요구사항 + 사람이 답한 결정."""
    texts = [getattr(sd, "text", "") or ""
             for sd in (getattr(result, "source_docs", None) or [])]
    spec_reqs = (result.spec or {}).get("requirements")
    if isinstance(spec_reqs, list):
        texts.extend(str(x) for x in spec_reqs)
    draft = getattr(result, "final_draft", None) or getattr(result, "draft", None)
    for point in (getattr(draft, "decisions", None) or []):
        answer = getattr(point, "human_input", "") or ""
        if answer:
            texts.append(str(answer))
    return texts


def _prose_only(body: str) -> str:
    """수치 판정에 쓸 본문 — 코드 블록과 결정 표식을 걷어낸다.

    코드 블록 안의 `timeout = 30`은 주장한 실행 결과가 아니라 코드의 일부다.
    결정 표식은 '아직 안 정한 자리'라 애초에 판정 대상이 아니고, 남겨 두면
    사람에게 보여 줄 발췌에 대괄호 뭉치가 섞여 읽기 어려워진다.
    """
    import re

    text = re.sub(r"```.*?```", " ", body, flags=re.DOTALL)
    return re.sub(r"\[\[DECISION:.*?\]\]", " ", text, flags=re.DOTALL)


def _python_blocks(body: str) -> list:
    import re
    return re.findall(r"```(?:python|py)\s*\n(.*?)```", body, re.DOTALL | re.IGNORECASE)


def _code_items(body: str, strategy: str, evidence: list) -> List[ReadinessItem]:
    """코드 블록 문법 + 실행하지 않고 적은 결과 수치.

    문법 검사는 언어를 명시한 블록에만 건다(의사코드 오검출 방지). 수치 검사는
    코드 계열 과제에서만 — 산문 과제의 '3배 늘었다'까지 잡으면 노이즈가 된다.
    """
    import ast

    items: List[ReadinessItem] = []
    broken = []
    for index, block in enumerate(_python_blocks(body), 1):
        if not block.strip():
            continue
        try:
            ast.parse(block)
        except SyntaxError as exc:
            broken.append(f"{index}번째 블록 {exc.lineno}행: {exc.msg}")
    if broken:
        items.append(ReadinessItem(
            "코드", "warn",
            f"파이썬 문법 오류 {len(broken)}건 — 그대로 제출하면 실행되지 않습니다 "
            f"({broken[0]})"))

    if strategy in ("code_project", "zip_project"):
        from .runtime.grounding import CODE_PATTERNS, ungrounded_numbers
        prose = _prose_only(body)
        hits = ungrounded_numbers(prose, evidence, CODE_PATTERNS, context=10)
        if hits:
            items.append(ReadinessItem(
                "실행결과", "warn",
                f"돌려 보지 않고 적은 결과 수치 {len(hits)}건 — 실행 시간·정확도 같은 "
                f"값은 직접 확인 후 채우세요 ({hits[0][:50]})"))
    return items


def _slide_items(body: str) -> List[ReadinessItem]:
    """발표 자료의 구조 — 장수·빈 장. 표기는 PPTX 변환기와 같은 파서로 읽는다."""
    from .presentation_export import parse_slide_markdown

    slides = [(t, b) for t, b in parse_slide_markdown(body) if t and t != "발표 자료"]
    items: List[ReadinessItem] = []
    if len(slides) < 3:
        items.append(ReadinessItem(
            "발표", "warn",
            f"슬라이드가 {len(slides)}장입니다 — `## 슬라이드 N: 제목` 표기로 "
            "3장 이상 만들어야 발표 자료로 변환됩니다"))
        return items
    empty = [t for t, bullets in slides if not bullets]
    if empty:
        items.append(ReadinessItem(
            "발표", "warn",
            f"내용이 없는 슬라이드 {len(empty)}장 — {', '.join(empty[:3])}"))
    crowded = [t for t, bullets in slides if len(bullets) > 7]
    if crowded:
        items.append(ReadinessItem(
            "발표", "info",
            f"한 장에 7줄을 넘는 슬라이드 {len(crowded)}장 — 발표는 읽는 글이 "
            f"아닙니다 ({', '.join(crowded[:2])})"))
    return items


def _activity_items(body: str, evidence: list) -> List[ReadinessItem]:
    """활동 기록 — 다른 점검과 방향이 반대다. '덜 썼다'가 아니라 '모르는 걸 썼다'."""
    from .runtime.grounding import ACTIVITY_PATTERNS, ungrounded_numbers

    hits = ungrounded_numbers(_prose_only(body), evidence, ACTIVITY_PATTERNS,
                              context=10)
    if not hits:
        return []
    return [ReadinessItem(
        "활동기록", "warn",
        f"자료에 근거가 없는 인원·일시·수량 {len(hits)}건 — 실제 활동 기록은 "
        f"지어내면 허위 기록이 됩니다 ({hits[0][:50]})")]
