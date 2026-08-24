"""과거 과제 연습용 수집 감사와 정직한 중단 규칙(결정적, LLM 0)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re


class PracticePreflightError(ValueError):
    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("연습을 시작하기 전에 확인이 필요합니다: " + " / ".join(reasons))


@dataclass
class PracticeAudit:
    policy: str
    body_present: bool
    deadline_present: bool
    formats: list[str] = field(default_factory=list)
    attachment_count: int = 0
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


_AI = re.compile(r"AI|인공지능|생성형\s*AI|ChatGPT|챗GPT|챗지피티", re.I)
_ALLOW = re.compile(r"(?:AI|인공지능|ChatGPT|챗GPT).{0,16}(?:허용|가능|사용\s*가능)", re.I)
_DENY = re.compile(r"(?:AI|인공지능|ChatGPT|챗GPT).{0,16}(?:금지|불가|불가능|허용하지)", re.I)
_DEADLINE = re.compile(r"마감|기한|due|\d{1,2}[./월]\s*\d{1,2}", re.I)
_FORMAT = re.compile(r"(?<![a-z0-9])(hwp|hwpx|docx?|pdf|pptx?|zip|ipynb|rmd)(?![a-z0-9])", re.I)


def audit_assignment(text: str, *, attachment_count: int = 0) -> PracticeAudit:
    raw = text or ""
    if _DENY.search(raw):
        policy = "prohibited"
    elif _ALLOW.search(raw):
        policy = "allowed"
    elif _AI.search(raw):
        policy = "unclear"
    else:
        policy = "unspecified"
    formats = sorted({m.lower() for m in _FORMAT.findall(raw)})
    body_present = len(re.sub(r"\s+", "", raw)) >= 40
    blockers: list[str] = []
    warnings: list[str] = []
    if policy == "prohibited":
        blockers.append("과제에서 AI 사용을 금지합니다")
    if policy == "unclear":
        blockers.append("AI 사용 범위가 불명확합니다")
    if not body_present:
        blockers.append("과제 본문이 비어 있거나 너무 짧습니다")
    if re.search(r"첨부|파일을?\s*(?:참고|확인)|별첨", raw) and attachment_count == 0:
        blockers.append("필수로 보이는 첨부파일을 가져오지 못했습니다")
    if re.search(r"조별|팀\s*과제|팀프로젝트", raw) and not re.search(
            r"담당|역할|내\s*부분|본인\s*파트", raw):
        blockers.append("팀 과제에서 내 담당 범위가 확인되지 않았습니다")
    if re.search(r"실험|측정|시뮬레이션", raw) and re.search(r"결과|수치|데이터", raw):
        if not re.search(r"\d+(?:\.\d+)?\s*(?:V|A|Hz|ms|s|%|℃|Ω|ohm)\b", raw, re.I):
            blockers.append("실험·측정 결과값이 없습니다")
    if policy == "unspecified":
        warnings.append("AI 사용 정책이 지시문에 없습니다")
    if not _DEADLINE.search(raw):
        warnings.append("마감 정보를 확인하지 못했습니다")
    return PracticeAudit(policy, body_present, bool(_DEADLINE.search(raw)), formats,
                         max(0, int(attachment_count)), blockers, warnings)


def enforce_practice_preflight(audit: PracticeAudit) -> None:
    if audit.blockers:
        raise PracticePreflightError(audit.blockers)
