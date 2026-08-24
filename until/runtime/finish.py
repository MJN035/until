"""검증된 초안 → **올려도 되는 제출본** (결정적, LLM 0).

왜 따로 있나: 오케스트레이터가 `ready`로 내주는 번들은 `work/draft.md`,
즉 에이전트가 쓴 **작업 파일 그대로**다. 그 안에는 경계선 표식
`[[DECISION: ...]]`이 살아 있다 — 검증기가 그걸 남기라고 강제하기 때문이다.
그대로 올리면 교수가 Until의 내부 대괄호를 본다(실측으로 확인).

그래서 마지막 한 칸을 여기서 만든다.
  - 사람이 답한 결정 → 그 답 문장으로 치환
  - 아직 안 정한 결정 → `【직접 정할 것 N: ...】` 자리표시(웹 제출 문서와 같은 규칙)
  - 결과를 `artifacts/`에 **새 파일**로 쓴다

`work/draft.md`는 건드리지 않는다. 검증을 통과한 원본이 그대로 남아 있어야
"무엇을 검증했는가"와 "무엇을 올리는가"를 나중에 대조할 수 있다.

치환은 문자열 바꾸기다 — 모델을 부르지 않으므로 이 경로의 비용은 0이고,
없는 사실을 지어낼 여지도 없다. 대신 사람의 답을 **문장으로 녹여 주지는
않는다**. 문장으로 녹이려면 에이전트를 한 번 더 돌려야 하고, 그건 `cli.py`의
2차 패스(`--fill-with-agent`)가 맡는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .report_runtime import DRAFT_RELPATH
from .security import confined_path

#: 제출본이 놓이는 자리. `artifacts/`는 작업공간 계획이 항상 만드는 디렉터리다.
SUBMISSION_RELPATH = "artifacts/제출본.md"


@dataclass(frozen=True)
class FinishedSubmission:
    path: Path
    relpath: str
    notes: tuple[str, ...] = ()          # 초안에 있던 결정 전체(본문 순서)
    open_notes: tuple[str, ...] = ()     # 아직 사람이 안 정한 것
    answered: tuple[str, ...] = ()       # 사람이 정한 것
    warnings: tuple[str, ...] = ()       # 치환 뒤 결정적 재검사에서 걸린 것

    @property
    def ready(self) -> bool:
        """빈칸 없이 그대로 올려도 되는 상태인가."""
        return not self.open_notes and not self.warnings


def read_decision_notes(workspace) -> tuple[str, ...]:
    """검증된 초안에 남은 결정 note를 본문 순서대로. 없으면 빈 튜플."""
    from ..report import resolve_decision_markers
    try:
        body = confined_path(workspace.root, DRAFT_RELPATH,
                             must_exist=True).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ()
    _text, notes, _open = resolve_decision_markers(body)
    return tuple(notes)


def finish(workspace, spec, answers: "dict[str, str] | None" = None
           ) -> FinishedSubmission:
    """제출본을 만들어 `artifacts/`에 쓰고 결과를 돌려준다.

    spec은 치환 뒤 요건이 무너지지 않았는지 다시 보는 데만 쓴다(분량·인용·섹션).
    치환은 마커를 **지우거나 답으로 바꾸는** 일이라 분량이 줄 수 있다 —
    "검증은 통과했는데 올릴 파일은 요건 미달"이 되지 않게 여기서 한 번 더 본다.
    """
    from ..report import resolve_decision_markers
    from .report_runtime import _check_citations, _check_length, _check_sections

    source = confined_path(workspace.root, DRAFT_RELPATH, must_exist=True)
    body, notes, open_notes = resolve_decision_markers(
        source.read_text(encoding="utf-8"), answers)

    target = confined_path(workspace.root, SUBMISSION_RELPATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")

    spec = spec or {}
    warnings = [f.message for check in (_check_sections, _check_length, _check_citations)
                for f in check(body, spec)]

    resolved = {str(k).strip() for k, v in (answers or {}).items() if str(v).strip()}
    return FinishedSubmission(
        path=target, relpath=SUBMISSION_RELPATH,
        notes=tuple(notes), open_notes=tuple(open_notes),
        answered=tuple(n for n in notes if n in resolved),
        warnings=tuple(warnings),
    )


def load_answers(path: Path, notes: "tuple[str, ...]") -> dict:
    """답변 파일을 읽는다 — JSON({note: 답} 또는 {번호: 답}) 또는 줄당 하나.

    번호로 주면 초안에 나온 순서로 매긴다(사람이 화면에서 본 번호와 같다).
    비대화형 실행(CI·스크립트)에서 결정을 미리 정해 두는 용도.
    """
    import json

    raw = Path(path).read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    data = None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        pass
    out: dict[str, str] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            text = str(value).strip()
            if not text:
                continue
            note = _note_for(str(key).strip(), notes)
            if note:
                out[note] = text
    else:                       # 줄당 하나 — 초안에 나온 순서대로 매긴다
        for index, line in enumerate(raw.splitlines()):
            text = line.strip()
            if text and index < len(notes):
                out[notes[index]] = text
    return out


def _note_for(key: str, notes: "tuple[str, ...]") -> str:
    if key in notes:
        return key
    if key.isdigit() and 1 <= int(key) <= len(notes):
        return notes[int(key) - 1]
    return ""
