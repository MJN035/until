"""
Offline backend — deterministic stub so the full pipeline runs with no API key.

It does NOT call a model. It inspects the `tag` and the user prompt and returns
plausible, structured output so you can demo end-to-end, write tests, and design
prompts before spending a single token.
"""
from __future__ import annotations
import json as jsonlib
import re
from .base import LLMResult


def _guess_deliverable(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ["essay", "에세이", "리포트", "보고서", "paper"]):
        return "에세이/리포트"
    if any(k in t for k in ["code", "프로그램", "구현", "implement"]):
        return "코드 과제"
    if any(k in t for k in ["presentation", "발표", "ppt", "slide"]):
        return "발표 자료"
    return "문서 과제"


# 유형별 결정적 초안(mock) — 가드 통과(>200자, 외국문자 0, 입장 단정 없음, 유형별 결정).
_TYPE_DRAFTS = {
    "problemset": (
        "# 문제 풀이 (Draft)\n\n"
        "## 문제 1\n"
        "주어진 조건에서 요구된 값을 정의에 따라 계산한다. 단계적으로 식을 세우고 정리하면 "
        "결과를 얻는다 [자료1]. 계산 과정과 중간 결과를 모두 적었고, 단위와 유효숫자를 확인했다.\n\n"
        "## 문제 2\n"
        "기본 법칙을 적용해 미지수를 구한다. 각 단계의 근거를 자료에 따라 명시했다 [자료1]. "
        "풀 수 있는 부분은 끝까지 전개했다.\n\n"
        "## 가정\n"
        "[[DECISION: 문제에 명시되지 않은 초기 조건(예: 이상적 소자 가정 여부)을 어떻게 둘지 — 본인 판단 필요]]\n"
    ),
    "report": (
        "# 실험 보고서 (Draft)\n\n"
        "## 목적\n이 실험의 목적과 배경을 자료에 근거해 정리한다 [자료1].\n\n"
        "## 방법\n사용한 재료와 절차를 순서대로 기술한다. 측정 항목과 조건을 정리했다.\n\n"
        "## 결과\n측정값을 정리하고 표로 제시한다. 자료에서 도출되는 객관적 결과는 끝까지 작성했다.\n\n"
        "## 논의\n"
        "[[DECISION: 결과 해석의 방향(오차의 주요 원인을 무엇으로 볼지) — 본인 판단 필요]]\n"
        "[[DECISION: 향후 개선·추가 실험 방향을 어디에 둘지 — 본인 판단 필요]]\n\n"
        "## 결론\n객관적으로 확인된 결과를 요약한다.\n"
    ),
    "code": (
        "# 구현 과제 (Draft)\n\n"
        "## 설계 개요\n요구 기능을 입력·처리·출력으로 분해했다 [자료1]. 모듈 경계를 정리했다.\n\n"
        "## 골격\n"
        "```python\n"
        "def solve(data):\n"
        "    # 핵심 로직: 명세대로 처리\n"
        "    result = process(data)\n"
        "    return result\n"
        "```\n"
        "명세로 정해지는 부분은 끝까지 구현했다.\n\n"
        "## 설계 선택\n"
        "[[DECISION: 자료구조·알고리즘 선택(예: 정렬 방식)과 트레이드오프를 어떻게 둘지 — 본인 판단 필요]]\n"
    ),
    "reflective_report": (
        "# 소감문 (Draft)\n\n"
        "## 강의에서 다룬 내용\n"
        "이번 강의가 다룬 주제와 핵심 개념을 자료에 근거해 정리했다 [자료1]. "
        "강의 사실 부분은 자료가 있는 데까지 끝까지 작성했고, 자료에 없는 세부 "
        "내용은 지어내지 않았다.\n\n"
        "## 내 반응 (본인 경험 필요)\n"
        "[[DECISION: 이번 강의에서 인상 깊었던 대목 2~3개 — 키워드만 적어 주세요: ___, ___]]\n"
        "키워드를 받으면 그 대목이 왜 인상 깊었는지, 무엇을 새로 알게 됐는지를 "
        "본인 문체로 풀어 쓴다.\n\n"
        "## 연결\n"
        "키워드에 진로·수강 계획 언급이 있으면 그 연결 문장을 쓰고, 없으면 상투적 "
        "진로 문장은 지어내지 않고 생략한다.\n"
    ),
    "inquiry": (
        "# 질의 후보 (Draft)\n\n"
        "다음 강의의 주제와 연사 정보를 바탕으로 관점별 질문 후보를 만들었다 [자료1]. "
        "아래 후보는 모두 그대로 제출 가능한 문장이다.\n\n"
        "1. (전망) 교수님께서 연구하시는 분야가 10년 뒤 산업에서 어떤 역할을 하게 될지 궁금합니다.\n"
        "2. (사례) 연구 결과가 실제 제품이나 서비스에 적용된 사례가 있다면 소개해 주실 수 있으신가요?\n"
        "3. (진로) 이 분야로 진로를 정하려는 학부생이 지금 준비해야 할 것은 무엇이라고 보시는지요?\n"
        "4. (방법론) 연구에서 가장 중요한 도구나 방법은 무엇이며 어떻게 익히셨는지 궁금합니다.\n"
        "5. (한계) 현재 접근이 가진 한계나 아직 풀리지 않은 난제는 무엇인가요?\n\n"
        "[[DECISION: 위 후보 중 실제로 궁금한 질문 2~3개 선택(번호로 답하거나 문장을 직접 수정)]]\n"
    ),
    "presentation": (
        "# 발표 자료 (Draft)\n\n"
        "## 슬라이드 1: 개요\n- 주제와 배경을 한 줄로 정리한다 [자료1]\n"
        "- 발표 범위와 듣는 사람이 얻어 갈 것을 명시한다\n\n"
        "## 슬라이드 2: 핵심 내용\n- 자료에서 도출한 요점 A와 그 근거를 제시한다 [자료1]\n"
        "- 요점 B를 예시와 함께 설명한다\n- 요점들이 어떻게 연결되는지 한 줄로 잇는다\n\n"
        "## 슬라이드 3: 정리\n- 핵심 메시지를 한 문장으로 요약한다\n- 다음 행동/질문 거리를 남긴다\n\n"
        "발표 흐름과 자료 근거는 끝까지 구성했고, 메시지의 우선순위만 남겨 둔다.\n"
        "[[DECISION: 발표의 강조점(어떤 메시지를 가장 앞세울지) — 본인 판단 필요]]\n"
    ),
}


class MockClient:
    def complete(self, system: str, user: str, *, tag: str = "", json: bool = False,
                 schema=None, documents=None, cache: bool = True) -> LLMResult:
        if tag == "understanding":
            out = self._understanding(user)
        elif tag == "execution":
            out = self._execution(user)
        elif tag == "finalize":
            out = self._finalize(user)
        elif tag == "suggest":
            out = self._suggest(user)
        elif tag == "execution-unit":
            out = self._execution_unit(user)
        elif tag == "review":
            out = self._review(user)
        elif tag == "voice":
            out = jsonlib.dumps({
                "summary": "관찰한 장면을 차분하게 풀어 쓰고, 궁금한 점을 자연스럽게 덧붙이는 말투",
                "frequent_terms": ["관찰", "궁금", "다음"],
            }, ensure_ascii=False)
        else:
            out = "(mock) " + user[:120]
        if tag == "execution":
            out = self._respect_execution_contract(
                out, system + "\n" + user, len(documents or []),
                safety='"integrity_gate"' in user)
        return LLMResult(text=out, backend="mock", tokens_in=0, tokens_out=0,
                         model="mock")

    @staticmethod
    def _respect_execution_contract(text: str, prompt: str, n_sources: int,
                                    safety: bool = False) -> str:
        """mock도 실제 자료 번호·분량 지시를 지킨다(오프라인 계약)."""
        def citation(match):
            if n_sources <= 0:
                return ""
            return f"[자료{min(int(match.group(1)), n_sources)}]"
        text = re.sub(r"\[자료(\d+)\]", citation, text)
        if safety:
            return text
        minimum = 0
        ranges = re.findall(r"\*\*(\d+)\s*[~～-]\s*(\d+)\s*자", prompt)
        if ranges:
            minimum = max(minimum, max(int(lo) for lo, _ in ranges))
        mins = re.findall(r"\*\*(\d+)\s*자\s*이상", prompt)
        if mins:
            minimum = max(minimum, max(map(int, mins)))
        for count, unit in re.findall(r"\*\*(\d+)\s*(페이지|매|장)\s*이상", prompt):
            minimum = max(minimum, int(count) * (600 if unit in ("페이지", "장") else 200))
        if not minimum:
            return text
        # readiness 측정은 마크다운 헤더·결정 마커·공백을 제외하므로, 문자열 길이와
        # 최대 수십 자 차이가 난다. 최소치 딱 맞춤 대신 5%+20자 안전 여유를 둔다.
        minimum = int(minimum * 1.05) + 20
        body_chars = len(re.sub(r"\[\[DECISION:.*?\]\]", "", text,
                                flags=re.DOTALL).replace(" ", ""))
        additions = (
            "제공된 자료의 핵심 조건을 구분하고 각 조건이 결론에 미치는 영향을 순서대로 설명한다.",
            "근거가 확인되는 범위와 확인되지 않는 범위를 나누어 과도한 일반화를 피한다.",
            "앞서 정리한 개념과 사례의 연결 관계를 구체적으로 밝히고 요구사항에 맞춰 내용을 보완한다.",
        )
        extra, i = [], 0
        while body_chars + sum(len(x.replace(" ", "")) for x in extra) < minimum:
            extra.append(additions[i % len(additions)])
            i += 1
        marker = text.find("[[DECISION:")
        block = "\n\n" + " ".join(extra) + "\n"
        return text[:marker] + block + text[marker:] if marker >= 0 else text + block

    def _understanding(self, user: str) -> str:
        deliverable = _guess_deliverable(user)
        m = re.search(r"(\d+)\s*(pages?|페이지|장|단어|words?)", user, re.I)
        length = m.group(0) if m else "명시 안 됨"
        spec = {
            "deliverable": deliverable,
            "goal": "주어진 자료를 바탕으로 요구된 산출물의 완성 직전 초안 작성",
            "requirements": [
                "과제 지시문에 적힌 핵심 질문에 답할 것",
                f"분량: {length}",
                "참고자료의 근거를 인용",
            ],
            "constraints": ["학문적 정직성 준수", "지정 형식 준수"],
            "deadline": "지시문에서 추출 필요(미상이면 사용자 확인)",
            "open_questions": [
                "주제/논지를 어느 방향으로 잡을지 (사용자 판단 필요)",
                "제출 형식·인용 스타일 확정",
            ],
        }
        return jsonlib.dumps(spec, ensure_ascii=False, indent=2)

    def _execution_unit(self, user: str) -> str:
        """unit 경로(UNTIL_PIPELINE=unit) 단위 본문 스텁 — 이전엔 분기가 없어
        '(mock) '+프롬프트 조각이 본문이 됐다. 요청된 요소 라벨과 근거 발췌를
        실제로 담아 커버리지·구체성 검증까지 결정적으로 통과한다(오프라인 계약)."""
        if '"integrity_gate"' in user:
            # legacy 스텁과 같은 학습 보조 계약 — 개념·유사 예제·검산 체크리스트.
            # unit이 기본 경로가 되면서(8/14) 이 안전 불변식 테스트가 unit 스텁을
            # 지나므로, 문구 계약을 동일하게 유지해야 한다.
            return ("자필 답안 제출 규정이 감지되어, 최종 답안 대신 개념 정리와 "
                    "유사 예제 중심의 학습 보조 내용만 담는다. 핵심 개념을 정리하고 "
                    "유사 예제의 풀이 순서를 보인 뒤, 제출 전 검산 체크리스트"
                    "(단위·유효숫자·부호)를 덧붙인다.")
        # 원료 없음(material_gap) 계약: 실제 모델은 gap 지침에 따라 본문에
        # 원료 요청 결정을 남긴다 — mock도 같은 계약을 지켜야 readiness의
        # '결정 0개인데 짧음 = 과소 작업' 판정이 실측과 같아진다.
        gap_marker = ("\n[[DECISION: 이 과제가 실제로 요구하는 내용"
                      "(안내문·첨부·수업 자료): ___]]"
                      if '"material_gap": true' in user else "")
        m = re.search(r"\[써야 할 요소[^\]]*\]\n(.*)", user, re.DOTALL)
        sec = m.group(1) if m else ""
        sec = re.split(r"\n\[금지\]|\n=== 재요청 ===", sec, maxsplit=1)[0]
        labels = []
        for ln in sec.splitlines():
            if ln.startswith("- "):
                lab = re.split(r"\s*\(근거가 얇음|\s*\|", ln[2:], maxsplit=1)[0]
                if lab.strip():
                    tm = re.search(r"목표 약\s*(\d+)자", ln)
                    labels.append((lab.strip(), int(tm.group(1)) if tm else 0))
        evs = [ln.strip().lstrip("· ").strip() for ln in sec.splitlines()
               if ln.strip().startswith("·") and "일반 지식" not in ln]
        out = []
        default_target = max(70, 240 // max(len(labels), 1))
        for i, (lab, target) in enumerate(labels):
            target = target or default_target
            # 인용 계약(legacy 스텁의 [자료N]과 동일): 발췌를 실은 '그 문장'에
            # 인용을 붙인다 — 문단 끝에 별도 문장으로 달면 실명이 든 발췌 문장이
            # 무출처 주장(근거 경고)으로 잡혀 readiness가 실측과 어긋난다.
            cite = " [자료1]" if i < len(evs) else ""
            ev = f" — {evs[i][:80]}" if i < len(evs) else ""
            paragraph = f"{lab}{ev}{cite}. 위 근거를 바탕으로 구체적으로 정리하였다."
            fillers = (
                " 자료에 나타난 조건과 핵심 개념의 관계를 순서대로 설명한다.",
                " 각 근거가 이 항목의 주장에 어떻게 연결되는지 구체적으로 밝힌다.",
                " 확인되지 않은 경험이나 수치는 추가하지 않고 제공된 범위만 서술한다.",
            )
            j = 0
            while len(paragraph.replace(" ", "")) < target:
                paragraph += fillers[j % len(fillers)]
                j += 1
            out.append(paragraph)
        if out:
            body = "\n".join(out)
            # 전역 분량 하한 계약 — 요소별 목표 합이 과제 요건("N자 이상")에
            # 못 미치면 legacy 스텁(_respect_execution_contract)처럼 채운다.
            mins = re.findall(r"(\d{3,5})\s*자\s*이상", user)
            minimum = max(map(int, mins)) if mins else 0
            k = 0
            filler_pool = (" 근거와 주장의 연결을 한 단계씩 확인하며 서술을 보강한다.",
                           " 각 항목이 전체 논지에서 맡는 역할을 덧붙여 설명한다.")
            while minimum and len(body.replace(" ", "").replace("\n", "")) < minimum:
                body += filler_pool[k % len(filler_pool)]
                k += 1
            return body + gap_marker
        # 슬롯 없는 일반 단위도 검증 가능한 유형별 본문을 반환한다. 24자 고정문은
        # 코드·Rmd·발표 과제 전부를 unit 경로에서 영구 실패시키던 mock 계약 위반.
        task_type = self._task_type(user)
        return _TYPE_DRAFTS.get(task_type, _TYPE_DRAFTS["report"]) + gap_marker

    def _finalize(self, user: str) -> str:
        """2차 패스(결정 해소) 결정적 스텁 — 사람의 답을 본문에 녹인 '최종 완성본'.

        프롬프트의 '사람이 내린 결정' 블록에서 답(→ 뒤)을 뽑아 1인칭으로 서술한다.
        결정이 모두 답해졌다고 보고 DECISION 마커 없는 완성본을 낸다(미답은 가드가 아닌
        실제 모델이 남기지만, mock은 결정적 데모를 위해 받은 답만 반영한다)."""
        block = ""
        m = re.search(r"\[ 사람이 내린 결정.*?\]\n(.*?)\n\n\[", user, re.DOTALL)
        if m:
            block = m.group(1).strip()
        answers = [
            # 노트 자체에 '→'가 들어갈 수 있으므로(예: "서론→본론 구성"),
            # 형식상 마지막 ' → '(공백 포함)를 기준으로 우측 분리한다.
            line.rsplit(" → ", 1)[1].strip()
            for line in block.splitlines()
            if " → " in line
        ]
        woven = (
            "\n".join(f"- {a}" for a in answers)
            if answers else "- (반영할 결정 답변이 없어 초안 골격을 유지한다.)"
        )
        return (
            "# 최종 완성본\n\n"
            "## 서론\n"
            "이 글은 제공된 자료를 정리하고, 사람이 직접 내린 판단을 반영해 마무리한 "
            "최종본이다. 자료로 채울 수 있는 부분은 끝까지 작성했고, 관점과 취향은 "
            "본인이 고른 방향을 따랐다.\n\n"
            "## 본론\n"
            "본인이 선택한 방향은 다음과 같으며, 이를 중심으로 논지를 전개한다.\n"
            f"{woven}\n"
            "선택한 방향에 따라 자료의 근거를 배치하고, 흐름이 일관되도록 정리했다.\n\n"
            "## 결론\n"
            "위에서 정한 방향에 따라 글을 마무리한다. 본인이 고른 관점이 전체 구성을 "
            "관통하도록 다듬었으며, 제출 가능한 완성 상태로 정리했다.\n"
        )

    def _suggest(self, user: str) -> str:
        """결정 제안 결정적 스텁 — 프롬프트의 '결정 목록' 번호마다 무난한 기본값 JSON."""
        block = ""
        m = re.search(r"\[ 결정 목록.*?\]\n(.*?)\n\n", user, re.DOTALL)
        if m:
            block = m.group(1)
        sug = []
        for line in block.splitlines():
            mm = re.match(r"\s*(\d+)\.\s+(.*)", line)
            if not mm:
                continue
            idx = int(mm.group(1))
            note = mm.group(2).strip()
            # 성격 태그([관점·논지] 등)가 붙어 오면 벗겨서 답에 새지 않게.
            note = re.sub(r"^\[[^\]]*\]\s*", "", note)
            topic = note.split("—")[0].strip()[:24] or "이 결정"
            sug.append({
                "index": idx,
                "answer": f"'{topic}'은(는) 자료로 방어 가능한 가장 무난한 방향으로 정한다.",
                "why": "근거가 분명하고 일반적으로 받아들여지는 선택(다른 선택도 가능).",
            })
        return jsonlib.dumps({"suggestions": sug}, ensure_ascii=False)

    @staticmethod
    def _task_type(user: str) -> str:
        m = re.search(r'"task_type"\s*:\s*"(\w+)"', user)
        return m.group(1) if m else "essay"

    def _review(self, user: str) -> str:
        """완성도 점검 결정적 스텁 — 초안의 인용/결정 유무로 그럴듯한 평가 JSON."""
        m = re.search(r"\[ 점검할 초안 \]\n(.*)$", user, re.DOTALL)
        body = m.group(1) if m else user
        cited = "[자료" in body
        has_dec = "[[DECISION:" in body
        gaps = []
        if not cited:
            gaps.append("제공된 자료의 근거를 본문 문장에 더 연결할 수 있습니다([자료N] 인용).")
        if body.count("##") < 2:
            gaps.append("섹션 구분이 적습니다. 구조(서론·본론·결론 등)를 더 나눌 수 있습니다.")
        level = "충분" if (cited and has_dec and not gaps) else "보완 권장"
        report = {
            "level": level,
            "coverage": ("제공된 자료를 근거로 인용했습니다."
                         if cited else "자료 인용이 부족합니다. 근거를 더 연결하세요."),
            "gaps": gaps,
            "decision_check": ("남긴 결정은 관점·취향 등 사람의 판단에 해당합니다."
                               if has_dec else "남긴 결정이 없습니다(정형 과제면 정상)."),
            "summary": ("경계선까지 충실히 작성됐습니다." if level == "충분"
                        else "대체로 충실하나 위 항목을 보완하면 더 좋습니다."),
        }
        return jsonlib.dumps(report, ensure_ascii=False)

    def _execution(self, user: str) -> str:
        """1차: 경계선을 넘는(입장 단정 + 결정 지점 0) 나쁜 초안을 내서 가드를 발동시킨다.
        재요청(REASK)을 받으면: 규칙을 지킨 교정 초안을 낸다. → reask 루프 데모.
        에세이 외 유형(문제풀이·보고서·코드·발표)은 유형에 맞는 초안을 바로 낸다."""
        # 규정 게이트(spec.integrity_gate) — 유형 초안보다 우선: 답안 대신 학습 보조.
        if '"integrity_gate"' in user:
            return (
                "# 학습 보조 (자필 제출 규정)\n\n"
                "이 과제는 자필 답안 제출이 규정이라, 최종 답안 대신 학습 보조만 담았어요.\n\n"
                "## 핵심 개념 정리\n"
                "문제를 푸는 데 필요한 개념과 공식을 자료에 근거해 정리했다 [자료1]. "
                "각 개념이 어느 문제에 쓰이는지 함께 표시했다.\n\n"
                "## 유사 예제 풀이 시연\n"
                "과제와 같은 유형의 예제를 하나 만들어 풀이 과정을 단계별로 시연한다. "
                "이 흐름을 참고해 본 문제는 직접 풀어 보길 권한다.\n\n"
                "## 검산 체크리스트\n"
                "- 단위와 유효숫자를 확인했는가\n"
                "- 극한값(0·무한대)에서 답이 상식과 맞는가\n"
                "- 각 단계의 근거(법칙·정의)를 말로 설명할 수 있는가\n"
            )
        ttype = self._task_type(user)
        if ttype in _TYPE_DRAFTS:
            return _TYPE_DRAFTS[ttype]
        is_reask = "재요청(REASK)" in user
        if not is_reask:
            # 일부러 경계선 침범: 1인칭 입장 단정 + DECISION 마커 없음.
            return (
                "# 초안 (Draft)\n\n"
                "## 서론\n"
                "이 과제는 한 기술이 사회 제도를 어떻게 재편했는지 분석한다.\n\n"
                "## 본론\n"
                "나는 감시 자본주의가 더 설득력 있다고 본다. 따라서 이 글의 논지는 "
                "Zuboff가 옳다는 것이다. 세 자료 모두 이 결론을 뒷받침한다.\n\n"
                "## 결론\n"
                "결론적으로 감시 자본 관점이 옳다.\n"
            )
        # 교정본: 후보 제시 + 판단은 DECISION 으로 이양.
        return (
            "# 초안 (Draft — 경계선까지)\n\n"
            "## 서론\n"
            "이 과제는 제공된 자료의 핵심 쟁점을 정리하고, 한 기술이 한 사회 제도를 "
            "어떻게 재편했는지 분석하는 것을 목표로 한다. 자료로 채울 수 있는 부분은 "
            "끝까지 작성했고, 사람의 판단이 필요한 지점은 마커로 표시했다.\n\n"
            "## 본론\n"
            "McLuhan은 '미디어=메시지'로 형식의 힘을 [자료1], Zuboff는 감시 자본의 축적 논리를 [자료2], "
            "Benkler는 분산 생산의 가능성을 제시한다. 세 자료에서 도출되는 논점 A/B/C를 정리했다.\n"
            "[[DECISION: 세 논점(형식 결정론 / 감시 자본 / 분산 생산) 중 어느 것을 핵심 논지로 세울지 — 본인의 관점 필요]]\n"
            "각 논점의 근거는 위 자료에서 인용했다 [자료1].\n"
            "[[DECISION: 반론을 어디까지 수용/반박할지 톤 결정]]\n\n"
            "## 결론\n"
            "초안 수준의 요약을 제시한다. [[DECISION: 최종 주장 강도와 마무리 메시지 확정]]\n"
        )
