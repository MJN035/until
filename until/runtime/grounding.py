"""근거 없는 수치 찾기 — 유형별 패턴만 갈아 끼우는 공용 판정기 (결정적, LLM 0).

`understanding.measured_check`는 HDL·실험 단위(ns·MHz·LUT·mV)에 맞춰져 있고
**알고리즘 동결 대상**이라 함부로 패턴을 늘릴 수 없다(결정성 기준선이 깨진다).
그래서 런타임 플러그인이 자기 유형의 단위를 여기서 보탠다 — 판정 **규칙**은
그 모듈과 같게 유지한다:

  - 수치 표현 안의 숫자가 근거(자료·학생이 알려 준 답)에 **그대로** 있으면 통과
  - `[[DECISION: ...]]` 안쪽은 '아직 안 정한 자리'이므로 검사하지 않는다

같은 규칙을 두 군데서 다르게 구현하면 "코드에선 걸리는데 양식에선 안 걸린다"가
되므로, 구현은 이 파일 하나로 모은다.
"""
from __future__ import annotations

import re
from typing import Iterable, Pattern

_DECISION_RE = re.compile(r"\[\[DECISION:.*?\]\]", re.DOTALL)
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def ungrounded_numbers(text: str, evidence: Iterable[str],
                       patterns: Iterable[Pattern[str]], *,
                       context: int = 20) -> list[str]:
    """`patterns`에 걸리는 수치 중 근거가 없는 것들을 문맥과 함께 돌려준다."""
    if not text:
        return []
    spans = [m.span() for m in _DECISION_RE.finditer(text)]
    known = set(_NUM_RE.findall("".join(str(e or "") for e in evidence)))
    candidates = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(ds <= start and end <= de for ds, de in spans):
                continue
            if set(_NUM_RE.findall(match.group(0))) <= known:
                continue
            candidates.append((start, end))
    # 같은 수치를 여러 패턴이 **겹쳐** 잡는다: "실행 시간은 0.42"와 "0.42초"는
    # 서로를 포함하지 않으면서 같은 값을 가리킨다. 포함 관계만 걸러내면 한 건이
    # 두 건으로 세어지고, 사람은 고칠 데가 두 군데인 줄 안다. 그래서 **겹치면**
    # 먼저 잡힌(가장 넓은) 것 하나만 남긴다.
    candidates.sort(key=lambda span: (span[0], -(span[1] - span[0])))
    accepted: list[tuple[int, int]] = []
    for start, end in candidates:
        if any(start < b and end > a for a, b in accepted):
            continue
        accepted.append((start, end))
    return [" ".join(text[max(0, s - context):e + context].split())
            for s, e in sorted(accepted)]


#: 코드 과제에서 '돌려 봐야 아는' 수치.
#   - 한글 단위에는 `\b`를 쓰지 않는다. `초`도 `였`도 단어 문자라 그 사이에 경계가
#     없어서 "0.42초였다"가 통째로 안 걸렸다(실측).
#   - 라틴 단위(ms·MB)는 `\b`로 끊어 `MBps` 같은 다른 낱말을 오검출하지 않게 한다.
#   - 라벨과 숫자 사이의 조사("실행 시간은 0.42초")를 허용한다.
CODE_PATTERNS = (
    re.compile(r"\d+(?:\.\d+)?\s*(?:밀리초|초|분|배)"),
    re.compile(r"\d+(?:\.\d+)?\s*(?:ms|sec|seconds?|MB|GB|KB)\b", re.IGNORECASE),
    re.compile(r"(?:실행\s*시간|소요\s*시간|처리량|throughput|메모리|정확도|accuracy|"
               r"통과율|커버리지|coverage)\s*\S{0,3}?\s*[:：=]?\s*(?:약\s*)?\d+(?:\.\d+)?",
               re.IGNORECASE),
)

#: 활동 기록에서 '사람만 아는 사실'에 붙는 수치 — 인원·일시·수량.
#: 지어내면 초안이 아니라 허위 기록이 되므로 코드보다 촘촘히 본다.
ACTIVITY_PATTERNS = (
    re.compile(r"\d+\s*(?:명|인|팀|조)"),
    re.compile(r"\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일"),
    re.compile(r"\d{4}-\d{1,2}-\d{1,2}"),
    re.compile(r"\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?"),
    re.compile(r"\d{1,2}:\d{2}"),
    re.compile(r"\d+\s*(?:부|건|회|차|개)"),
    re.compile(r"\d+(?:\.\d+)?\s*(?:시간|분)간"),
)
