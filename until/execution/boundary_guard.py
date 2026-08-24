"""
BoundaryGuard — Execution 출력이 'Draft 경계선'을 지켰는지 결정적으로 검증하고,
위반 시 모델에 재요청(reask)한다.

설계는 guardrails-ai 의 Guard / Validator / OnFailAction(reask) 패턴을 차용해
의존성 없이 얇게 재구현했다. 출처:
https://github.com/guardrails-ai/guardrails  (Apache-2.0)

guardrails 대응 관계:
  Validator.validate() -> PassResult/FailResult   ≈  BoundaryValidator.validate()
  Guard().use(validator, on_fail=REASK)           ≈  BoundaryGuard(validators, on_fail)
  on_fail="reask" 재프롬프트 루프                    ≈  BoundaryGuard.run() 의 reask 루프
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List

from ..boundary.models import Draft
from ..config import algo_version
from .prompts import DECISION_OPEN


class OnFailAction(str, Enum):
    REASK = "reask"        # 모델에 교정 요청 후 재생성
    EXCEPTION = "exception"  # 최종 실패 시 예외
    WARN = "warn"          # 최종 실패해도 경고만 달고 통과


@dataclass
class ValidationResult:
    passed: bool
    errors: List[str] = field(default_factory=list)


# ── 결정적(no-token) 검증 규칙들 ────────────────────────────────────
# 1인칭으로 '입장을 확정'하는 문장 = 경계선 침범 신호(휴리스틱).
_STANCE_PATTERNS = [
    r"나(는|의)\s*[^.\n]{0,30}(주장|입장|결론|생각|옳|본다|믿|확신)",
    r"나(는|의)\s*[^.\n]{0,40}(찬성|반대|지지|선택|선호)(?:한|했|하|된|했)[^.\n]{0,8}",
    r"결론적으로\s+[^.\n]*(옳|맞|타당|우월)",
    r"따라서\s+[^.\n]*(옳|이라고\s*본|결론)",
    r"\bI\s+(argue|believe|conclude|think)\b",
    r"\bmy\s+(thesis|argument|stance|position)\s+is\b",
]
_STANCE_RE = [re.compile(p, re.IGNORECASE) for p in _STANCE_PATTERNS]
# 마커는 있는데 형식이 깨진 경우 탐지(여는 토큰만 있고 닫힘 없음).
_OPEN_RE = re.compile(re.escape(DECISION_OPEN))
# 한국어 출력에 한자/일본어 가나가 섞이는 라이브 모델 실패를 결정적으로 탐지한다.
_HANJA_KANA_RE = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"  # \uac00\ub098\u00b7CJK\u00b7CJK\ud638\ud658
    r"\u0900-\u097f"   # \ub370\ubc14\ub098\uac00\ub9ac
    r"\u0400-\u04ff"   # \ud0a4\ub9b4
    r"\u0590-\u05ff"   # \ud788\ube0c\ub9ac
    r"\u0600-\u06ff"   # \uc544\ub78d
    r"\u0e00-\u0e7f"   # \ud0dc\uad6d
    r"\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u00ff"  # \ub77c\ud2f4-1 \uc545\uc13c\ud2b8(\u00e1 \u00e9 \u00ed \u00f3 \u00fa \u00f1 \u00fc \ub4f1; \u00d7\u00f7 \uc81c\uc678) \u2014 b\u00e1o c\u00e1o \uac19\uc740 \ub204\uc218
    r"\u0100-\u017f"   # \ub77c\ud2f4 \ud655\uc7a5-A(\u0101 \u0113 \u0161 \u017e \u0142 \ub4f1)
    r"\u1e00-\u1eff"   # \ub77c\ud2f4 \ud655\uc7a5 \ucd94\uac00(\ubca0\ud2b8\ub0a8\uc5b4 \uc131\uc870\ubd80\ud638 \u1ef1 \u1ebf \ub4f1)
    r"]"
)


def _json_dump_error(body: str) -> "str | None":
    """본문 골격이 JSON 객체 덤프인지 결정적으로 판정(제출물은 산문이어야 한다).

    코드펜스를 벗기고 결정 마커를 제외한 나머지가 {...}로 온전히 파싱되면 —
    또는 파싱은 깨져도 "키": 꼴이 지배적이면 — JSON 덤프로 본다. 산문 속의
    짧은 중괄호 예시는 걸리지 않는다(전체가 {로 시작해 }로 끝나야 함)."""
    import json as _json
    txt = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", body.strip(), flags=re.M).strip()
    core = re.sub(re.escape(DECISION_OPEN) + r".*?\]\]", "", txt, flags=re.S).strip()
    if not (core.startswith("{") and core.endswith("}")):
        return None
    looks_like = False
    try:
        looks_like = isinstance(_json.loads(core), dict)
    except ValueError:
        looks_like = len(re.findall(r'"[^"\n]+"\s*:', core)) >= 3
    if looks_like:
        return ("본문이 JSON 구조로 출력됐다. 이것은 제출물이 아니다 — "
                "과제에 실제로 제출할 한국어 산문(질문·글)을 작성하라. "
                "JSON·코드 블록·메타 설명 없이 본문만.")
    return None


class BoundaryValidator:
    """초안 텍스트가 경계선 규칙을 지켰는지 검사. 토큰 미사용."""

    def __init__(self, min_decisions: int = 1, min_body_chars: int = 200,
                 forbid_stance: bool = True):
        self.min_decisions = min_decisions
        self.min_body_chars = min_body_chars
        # finalize(결정 해소 후 2차 패스)에서는 사람이 이미 입장을 정했으므로
        # 1인칭 입장 확정 문장을 허용한다(가치판단을 사람이 넘긴 것).
        self.forbid_stance = forbid_stance

    def validate(self, draft: Draft) -> ValidationResult:
        errors: List[str] = []

        # (1) 과소 작업: 본문이 비어있거나 너무 짧음
        if len(draft.body.strip()) < self.min_body_chars:
            errors.append(
                f"본문이 너무 짧다({len(draft.body.strip())}자). 자료로 채울 수 있는 부분을 끝까지 작성하라."
            )

        # (2) 경계선 침범: 결정 지점 부족(판단을 스스로 다 해버림)
        if draft.n_decisions < self.min_decisions:
            errors.append(
                f"결정 지점이 {draft.n_decisions}개로 부족하다(최소 {self.min_decisions}). "
                "사람만이 내릴 판단을 임의로 확정하지 말고 [[DECISION: ...]]로 표시하라."
            )

        # (3) 1인칭 입장 확정 문장 탐지 (finalize 패스에서는 비활성)
        if self.forbid_stance:
            for rx in _STANCE_RE:
                m = rx.search(draft.body)
                if m:
                    errors.append(
                        f"본인 입장을 단정하는 문장이 있다: \"{m.group(0).strip()}\". "
                        "이 판단은 사람에게 넘기고 [[DECISION: ...]]로 전환하라."
                    )
                    break

        # (4) 깨진/자리표시 마커 탐지
        n_open = len(_OPEN_RE.findall(draft.body))
        if n_open > draft.n_decisions:
            errors.append(
                f"형식이 깨졌거나 자리표시(...,TODO)인 DECISION 마커가 있다"
                f"(여는 토큰 {n_open}개 vs 유효 {draft.n_decisions}개). 모든 마커를 구체적으로 채워라."
            )

        # (5) 라이브 모델 한국어 출력 품질: 한글 외 외국 문자(한자·가나·데바나가리 등) 혼입 방지
        m = _HANJA_KANA_RE.search(draft.body)
        if m:
            errors.append(
                f"한글이 아닌 외국 문자가 섞여 있다: \"{m.group(0)}\"(U+{ord(m.group(0)):04X}). "
                "현대 한국어 한글 중심 문장으로 다시 작성하라."
            )

        # (6) 본문이 JSON 구조 덤프 — 라이브 실측(빈 과제 본문 + 오염된 spec에서
        # 초안 대신 spec JSON을 출력) 방지. 제출물은 산문이어야 한다.
        json_err = _json_dump_error(draft.body)
        if json_err:
            errors.append(json_err)

        return ValidationResult(passed=not errors, errors=errors)


class InventedCandidateValidator:
    """원료가 없다고 판정된 과제에서 **구체적 후보를 제시하면** reask.

    `SYSTEM`은 결정 질문에 "구체적 후보 2~3개"를 제시하라고 요구한다 — 답을 클릭
    한 번으로 만드는 좋은 규칙이다. 그런데 **자료가 없을 때 그 지시는 창작 지시가
    된다.** 실측(2026-08-23):

      · 1주차 소감문 → "다룬 주요 주제는? 후보 — (1) 전력 시스템 개요,
        (2) 정보·통신 기초" (강의 내용을 아무도 모른다)
      · 실험 예비보고서 → "열량계 모델은? 후보 — (1) 전기식 열량계(모델 E-100),
        (2) 고전식 열량계(모델 C-50)" (존재하지 않는 모델명)

    짧은 초안보다 나쁘다. 학생에게 **거짓 선택지**를 주고, 그중 하나를 고르면
    그 거짓이 본문으로 들어간다.

    어휘로 '근거 있는 후보'를 가리려다 실패했다 — 후보 "정보·통신 기초"의 '정보'가
    과목명 "전기·정보세미나"에 substring으로 걸리고, 반대로 "피드백 내용 요약"처럼
    정당하게 추론된 후보는 자료에 그대로 없다. 모델이 알 수 없는 **사실**과
    추론 가능한 **방식**을 어휘로는 못 가른다.
    그래서 이미 계산해 둔 신호에 붙인다: **`material_gap`이 켜진 상태에서만** 적용.
    원료가 있다고 판정된 과제의 후보는 건드리지 않는다.

    답을 미루는 선택지("직접 입력해 주세요"·"기타")는 후보가 아니라 탈출구이므로
    센 적 없다.
    """

    _CAND_BLOCK = re.compile(r"후보\s*[—\-–:]\s*(.+)$", re.S)
    _CAND_ITEM = re.compile(r"\(\s*\d+\s*\)\s*([^,()\[\]]+)")
    _ESCAPE = re.compile(r"직접\s*(입력|적어|쓰)|기타|해당\s*없음|모르겠|없음")

    def _invented(self, note: str) -> List[str]:
        m = self._CAND_BLOCK.search(note or "")
        if not m:
            return []
        return [c.strip() for c in self._CAND_ITEM.findall(m.group(1))
                if c.strip() and not self._ESCAPE.search(c)]

    def validate(self, draft: Draft) -> ValidationResult:
        bad: List[str] = []
        for raw in re.findall(r"\[\[DECISION:(.*?)\]\]", draft.body or "",
                              re.DOTALL):
            bad.extend(self._invented(raw))
        if not bad:
            return ValidationResult(passed=True)
        listed = " · ".join(bad[:4])
        return ValidationResult(passed=False, errors=[
            f"자료에 없는 내용을 결정 후보로 지어냈다 — {listed}. 이 과제는 "
            "핵심 원료가 자료에 없다고 판정된 상태라 구체적 후보를 만들 근거가 "
            "없다. 후보 목록을 지우고 빈칸형으로 물어라 "
            "(예: [[DECISION: 그 강의에서 다룬 주제 한 가지: ___]])."])


class AssignmentMetaValidator:
    """산출물이 **과제 자체를 서술하면** reask — 마감·과제ID·과목코드·"본 과제는…".

    SYSTEM 프롬프트에 이미 두 줄로 적혀 있는 규칙이다("산출물 '자체'를 써라",
    "행정 정보는 본문에 넣지 않는다"). 그런데 실측(2026-08-22, Cerebras
    gpt-oss-120b)에서 **매번** 어겼다. 자료가 충분한 과제에서도 그랬다:

      "본 보고서는 2025-2 현대경제의 이해(002) 과목에서 제시한 과제 331450번을
       수행한다. 제출 마감은 2025년 11월 6일(목) 오후 11시 59분으로 정해졌다."

    학생이 실제로 낸 같은 과제의 제출본은 자기 사례(내일배움카드 부트캠프)를
    구체적으로 다룬 글이었다. 지시만으로는 안 되는 종류라 루프에서 잡는다
    (인용 보존·수치 날조 방어와 같은 계보).

    정밀도는 실데이터로 쟀다 — 3인 코퍼스의 **학생 실제 제출본 165건** 중 3건만
    걸리고(1.8%), 그 3건은 학생이 과제지를 그대로 채워 낸 경우라 원본 헤더의
    "제출 마감일"이 딸려온 것이다. 생성 초안이 그걸 쓰는 건 어차피 막아야 한다.
    양식 과제(표 칸을 채우는 유형)는 마감 칸이 정당할 수 있어 호출부가 면제한다.
    """

    _RULES = (
        ("마감·제출 일정",
         re.compile(r"(?:제출\s*(?:마감|기한|일정)|마감\s*(?:일|은|시각))[^.\n]{0,30}\d"
                    r"|마감(?:이다|이며|으로\s*정해)")),
        ("과제 ID·번호",
         re.compile(r"과제\s*(?:ID|아이디|번호)\s*[:은는]?\s*\d|과제\s*\d{5,}\s*번")),
        ("'이 과제를 수행한다' 류 선언",
         re.compile(r"본\s*(?:보고서|과제|글|리포트)\s*는[^.\n]{0,40}과제"
                    r"[^.\n]{0,20}(?:수행|다룬다|작성한다)")),
        ("'이 과제는 ~을 요구한다' 류 메타 서술",
         re.compile(r"(?:이|본)\s*과제는[^.\n]{0,40}(?:요구한다|목표로|평가한다|제시한다)")),
        ("과목 코드",
         re.compile(r"\d{4}[-‑]\d\s*[가-힣A-Za-z·:\s]{2,24}\(\d{3}\)")),
        ("제출처 안내",
         re.compile(r"(?:eTL|이티엘|과제란|과제\s*페이지)에\s*(?:업로드|제출)")),
    )

    def validate(self, draft: Draft) -> ValidationResult:
        body = draft.body or ""
        # 결정 마커 안의 문장은 산출물 본문이 아니다 — 첨부를 요청하는 질문에
        # 마감이 들어갈 수 있고, 그걸 위반으로 잡으면 요청 자체가 막힌다.
        body = re.sub(r"\[\[DECISION:.*?\]\]", " ", body, flags=re.DOTALL)
        hits = [label for label, rx in self._RULES if rx.search(body)]
        if not hits:
            return ValidationResult(passed=True)
        return ValidationResult(passed=False, errors=[
            "본문이 산출물이 아니라 **과제 자체**를 서술하고 있다 — "
            + " · ".join(hits) + ". 마감·제출 방법·과제 번호·과목 코드는 산출물의 "
            "일부가 아니다(시스템이 따로 보여 준다). 그 문장들을 지우고, 과제가 "
            "요구하는 산출물의 실제 내용으로 채워라."])


class CitationPreservationValidator:
    """2차 패스(finalize)가 초안의 `[자료N]` 인용을 떨구면 reask.

    실측(2026-08-22, Cerebras gpt-oss-120b): 인용 5개가 박힌 초안을 finalize에
    넣으면 완성본에서 **인용이 전부 사라졌다**(3회 중 3회). 원인은 프롬프트에
    인용 보존 규칙이 없어 모델이 본문을 자유롭게 재작성한 것이다. 규칙을 넣으니
    3회 중 1회만 살아남았다 — 지시만으로는 안 되는 종류다. 그래서 생성 루프
    안에서 결정적으로 잡는다(수치 날조 방어와 같은 계보: 지침 + 사후 검증 + reask).

    이게 왜 중요한가: 초안은 인용 검사를 통과했는데 완성본이 '근거 미인용'으로
    떨어진다. 사용자가 보기에 **마무리를 누를수록 결과가 나빠진다.**

    본문을 코드로 고치지 않는다 — 어느 문장이 어느 자료에 근거했는지는 모델만
    안다. 사라진 번호를 알려주고 다시 쓰게 할 뿐이다.
    """

    _CITE = re.compile(r"\[자료(\d+)\]")

    def __init__(self, draft_body: str):
        self.expected = set(self._CITE.findall(draft_body or ""))

    def validate(self, draft: Draft) -> ValidationResult:
        if not self.expected:
            return ValidationResult(passed=True)  # 초안에 인용이 없으면 지킬 게 없다
        got = set(self._CITE.findall(draft.body or ""))
        missing = sorted(self.expected - got, key=int)
        if not missing:
            return ValidationResult(passed=True)
        nums = ", ".join(f"[자료{n}]" for n in missing)
        if not got:
            return ValidationResult(passed=False, errors=[
                f"초안에 있던 근거 인용이 전부 사라졌다 — {nums}. 문장을 다시 쓰더라도 "
                "그 문장이 근거로 삼은 자료 번호를 같은 자리에 남겨라."])
        return ValidationResult(passed=False, errors=[
            f"초안에 있던 근거 인용 {nums}이(가) 빠졌다. 해당 내용을 담은 문장에 "
            "그 번호를 그대로 다시 붙여라(새 번호를 만들지 말 것)."])


class LengthValidator:
    """분량 요건을 생성 루프 '안'에서 강제 — 미달/초과/항목 수 불일치면 reask.

    경계선 철학과의 관계: 본문을 코드로 늘리거나 자르지 않는다. 요건 미달을
    항목별 델타로 알려주고 모델이 다시 쓰게 할 뿐이다 — BoundaryValidator의
    min_body_chars(본문 과소 → 재작성 요구)와 같은 계보. 판정은 결정적(LLM 0).
    """

    def __init__(self, target, expected_items: "int | None" = None):
        self.target = target                  # LengthTarget
        self.expected_items = expected_items  # 양식에서 유도한 기대 항목 수

    def validate(self, draft: Draft) -> ValidationResult:
        from ..understanding.length_target import check_length
        chk = check_length(self.target, draft.body,
                           expected_items=self.expected_items)
        errors: List[str] = []
        if chk.status == "mismatch":
            errors.append(chk.message)
        elif chk.status in ("short", "over"):
            per = getattr(self.target, "per_item", "")
            unit = "단어" if self.target.unit == "단어" else "자"
            lo, hi = self.target.min, self.target.max
            rng = (f"{lo}~{hi}{unit}" if lo is not None and hi is not None
                   else (f"{lo}{unit} 이상" if lo is not None else f"{hi}{unit} 이하"))
            if per and chk.items:
                # 항목마다 개별 에러 — reask 프롬프트에 항목별 델타가 그대로 실린다.
                for label, cur, st in chk.items:
                    mark = (label or "·")[:12]
                    if st == "short" and lo is not None:
                        errors.append(
                            f"{mark} {cur}{unit} → {per}당 {rng}로 "
                            f"약 {lo - cur}{unit} 더 쓸 것(억지 반복 없이 근거·논의로)")
                    elif st == "over" and hi is not None:
                        errors.append(
                            f"{mark} {cur}{unit} → {per}당 {rng}로 "
                            f"약 {cur - hi}{unit} 줄일 것(군더더기부터)")
            elif (chk.status == "over" and algo_version() == "v0.2"
                    and getattr(self.target, "mode", "min") == "max"):
                # v0.2 확장(§4.6(a)): 상한 전용 요건("200자 이내")의 '초과'는
                # 줄이는 방향의 reask 사유다 — 미달 대응(더 쓸 것)과 지시가
                # 정반대라, 일반 문구 대신 감축 지침을 명시한다. 기존 미달
                # 판정 경로와 v0.1 메시지는 아래 else로 바이트 단위 동일 유지.
                errors.append(
                    chk.message + " — 상한 요건이다: 핵심 문장은 남기고 "
                    "군더더기·중복·상투 표현부터 줄여 다시 작성하라"
                    "(억지 압축으로 사실·인용을 왜곡하지 말 것)")
            else:
                errors.append(chk.message + " — 요건에 맞게 다시 작성하라")
        # unknown(항목 구분 실패 + 기대 수 미상)은 여기선 통과 — readiness가 경고로
        # 표면화한다(전체 재생성을 걸 근거가 없음).
        return ValidationResult(passed=not errors, errors=errors)


class FormValidator:
    """양식 구조 유지(표 라벨·①② 항목)를 생성 루프 '안'에서 강제 — 누락 시 reask.

    check_form_fidelity(결정적)를 그대로 재사용. 산문으로 풀어 쓴 출력이
    '판정 불가'로 빠져나가지 않게 한다.
    """

    def __init__(self, form_text: str, form_name: str = "양식"):
        self.form_text = form_text
        self.form_name = form_name

    def validate(self, draft: Draft) -> ValidationResult:
        from ..capture.formfill import check_form_fidelity
        fid = check_form_fidelity(self.form_text, draft.body)
        if fid is None or fid.ok:
            return ValidationResult(passed=True)
        errors: List[str] = []
        if fid.missing_labels:
            errors.append(
                f"양식({self.form_name})의 기본정보 라벨 누락 — 빠진 칸: "
                + ", ".join(fid.missing_labels[:6])
                + ". 원본 표 구조(| 라벨 | 값 |)를 그대로 유지해 출력하라.")
        if fid.missing_items:
            errors.append(
                f"양식({self.form_name})의 서술 항목 누락 — 빠짐: "
                + ", ".join(fid.missing_items[:6])
                + ". ①②… 항목 번호·머리글을 그대로 두고 각 항목을 작성하라.")
        return ValidationResult(passed=not errors, errors=errors)


@dataclass
class GuardReport:
    passed: bool
    attempts: int                 # 총 생성 횟수 (1 + reask 횟수)
    reasks: int
    final_errors: List[str] = field(default_factory=list)
    history: List[List[str]] = field(default_factory=list)  # 시도별 에러 로그


class BoundaryGuard:
    """validate→reask 루프. guardrails 의 on_fail=REASK 동작을 모사."""

    def __init__(
        self,
        validators: List[BoundaryValidator] | None = None,
        on_fail: OnFailAction = OnFailAction.REASK,
        max_reasks: int = 2,
    ):
        self.validators = validators or [BoundaryValidator()]
        self.on_fail = on_fail
        self.max_reasks = max_reasks

    def _validate(self, draft: Draft) -> ValidationResult:
        errors: List[str] = []
        for v in self.validators:
            r = v.validate(draft)
            errors.extend(r.errors)
        return ValidationResult(passed=not errors, errors=errors)

    def run(self, produce: Callable[[List[str], str], str]) -> tuple[Draft, GuardReport]:
        """
        produce(errors, previous_draft) -> 모델 원본 텍스트.
        - 첫 호출: errors=[], previous_draft="".
        - 재요청: 직전 에러/초안을 넘겨 교정 요청.
        """
        history: List[List[str]] = []
        prev_text = ""
        errors: List[str] = []

        for attempt in range(self.max_reasks + 1):
            text = produce(errors, prev_text)
            draft = Draft.from_text(text)
            result = self._validate(draft)
            history.append(result.errors)

            if result.passed:
                return draft, GuardReport(
                    passed=True, attempts=attempt + 1, reasks=attempt, history=history
                )

            prev_text, errors = text, result.errors
            if self.on_fail != OnFailAction.REASK:
                break  # reask 안 하면 첫 실패에서 종료

        # 최종 실패 처리
        report = GuardReport(
            passed=False, attempts=len(history), reasks=len(history) - 1,
            final_errors=errors, history=history,
        )
        if self.on_fail == OnFailAction.EXCEPTION:
            raise ValueError("BoundaryGuard 검증 실패: " + "; ".join(errors))
        return Draft.from_text(prev_text), report
