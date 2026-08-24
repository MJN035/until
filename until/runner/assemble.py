"""초안 + 과제 첨부 → 러너에 보낼 파일 묶음 (결정적, LLM 0).

웹 초안은 마크다운이다. 거기서 **돌릴 수 있는 것**을 꺼내야 러너에 보낼 수 있다:
초안의 파이썬 코드 블록이 학생의 답이고, 과제가 함께 준 `test_*.py` 첨부가
채점 기준이다. 둘이 다 있을 때만 실행이 의미가 있다.

보내지 않는 것도 규칙이다.
  - 테스트가 없으면 **보내지 않는다.** "테스트 0개 통과"라는 무의미한 초록불을
    주느니 아무 말도 안 하는 게 낫다.
  - 과제 원문·강의자료 같은 문서는 보내지 않는다. 러너는 실행에 필요한 파일만
    받는다 — 보낼 이유가 없는 것을 보내지 않는 것이 유출 표면을 줄이는 가장
    싼 방법이다.
"""
from __future__ import annotations

import re
from pathlib import Path

#: 실행을 붙일 과제 유형. 산문 과제의 예시 코드까지 돌리면 노이즈다.
RUNNABLE_STRATEGIES = frozenset({"code_project", "zip_project"})

_PY_BLOCK_RE = re.compile(r"```(?:python|py)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_TEST_NAME_RE = re.compile(r"^(?:test_[A-Za-z0-9_]+|[A-Za-z0-9_]+_test)\.py$")
#: 학생 답이 놓일 이름. 과제가 준 테스트가 `from solution import ...`을 가정하는
#: 관례를 따른다. 다른 이름을 가정하는 테스트는 import 에러로 정직하게 실패한다.
SOLUTION_NAME = "solution.py"


def files_for(result) -> "tuple[str, dict] | None":
    """(job, files) 또는 실행할 게 없으면 None.

    `result`는 웹 파이프라인의 `Result`. 초안(최종본 우선)에서 코드를, 수집한
    문서에서 테스트를 찾는다.
    """
    strategy = str(getattr(getattr(result, "assignment_route", None),
                           "strategy", "") or "")
    if strategy not in RUNNABLE_STRATEGIES:
        return None

    draft = getattr(result, "final_draft", None) or getattr(result, "draft", None)
    body = getattr(draft, "body", "") or ""
    blocks = [b for b in _PY_BLOCK_RE.findall(body) if b.strip()]
    if not blocks:
        return None

    tests = _test_files(result)
    if not tests:
        return None

    files = {SOLUTION_NAME: "\n\n".join(block.strip() for block in blocks) + "\n"}
    files.update(tests)
    return "python_unittest", files


def _test_files(result) -> dict:
    """과제가 준 테스트 파일들 — 이름이 테스트 관례를 따르는 것만."""
    out: dict[str, str] = {}
    for doc in (getattr(result, "documents", None) or []):
        name = Path(str(getattr(doc, "source", "") or "")).name
        if not _TEST_NAME_RE.match(name):
            continue
        text = str(getattr(doc, "text", "") or "")
        if text.strip():
            out[name] = text
    return out


def summarize(outcome: "dict | None") -> "tuple[str, str] | None":
    """러너 응답 → (status, 사람이 읽는 한 줄). 보여줄 게 없으면 None.

    **못 돌린 것과 실패한 것을 구분한다.** 러너가 없거나 격리를 증명 못 해서
    안 돌아간 것을 '테스트 실패'로 적으면 학생은 멀쩡한 코드를 고치려 든다.
    """
    if not outcome:
        return None
    status = str(outcome.get("status") or "")
    if status == "succeeded":
        return "ok", "제출 코드가 과제 테스트를 통과했습니다"
    if status == "failed":
        tail = _last_line(outcome)
        return "warn", f"과제 테스트가 통과하지 않았습니다 — {tail}" if tail else \
                       "과제 테스트가 통과하지 않았습니다"
    if status == "timeout":
        return "warn", ("테스트가 제한 시간 안에 끝나지 않았습니다 — 무한 루프를 "
                        "확인하세요")
    # blocked · tool_missing · error — 우리 쪽 사정이지 학생 코드 문제가 아니다.
    detail = str(outcome.get("detail") or status or "사유 미상")
    return "info", f"테스트를 돌리지 못했습니다(코드가 틀렸다는 뜻이 아닙니다) — {detail[:80]}"


def _last_line(outcome: dict) -> str:
    for key in ("stderr", "stdout"):
        lines = [ln.strip() for ln in str(outcome.get(key) or "").splitlines()
                 if ln.strip()]
        if lines:
            return lines[-1][:80]
    return ""
