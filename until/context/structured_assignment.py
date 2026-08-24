"""Rmd·ZIP처럼 산출물 구조 자체가 명세인 과제를 위한 결정적 지침."""
from __future__ import annotations

from typing import Iterable


def structured_assignment_kind(documents: Iterable[object]) -> str:
    kinds = {str(getattr(d, "kind", "")).lower() for d in documents or []}
    texts = "\n".join(str(getattr(d, "text", ""))[:500] for d in documents or [])
    if "rmd-template" in kinds or "[RMD_TEMPLATE:" in texts:
        return "rmd"
    if "zip-project" in kinds or "[ZIP_PROJECT:" in texts:
        return "zip"
    return ""


def structured_assignment_directive(documents: Iterable[object]) -> str:
    kind = structured_assignment_kind(documents)
    if kind == "rmd":
        return (
            "[R Markdown 템플릿 과제]\n"
            "- 원본 문제 순서, YAML 머리말, 코드 청크 옵션과 ANSWER_SLOT 위치를 바꾸지 말 것.\n"
            "- 코드 슬롯에는 실행 가능한 R 코드를, 해설 슬롯에는 그 코드가 산출할 값·그래프의 "
            "해석 구조를 작성한다. 실제 데이터가 없으면 수치를 지어내지 말고 실행 후 채울 "
            "[[DECISION]]으로 남긴다.\n"
            "- 교수 제공 코드와 학생 답안 영역을 구분하고, 패키지·데이터 파일 누락을 먼저 알린다."
        )
    if kind == "zip":
        return (
            "[ZIP 코드 프로젝트 과제]\n"
            "- FILE 경계를 보존해 요구사항 PDF/README, 제공 스켈레톤, 테스트, 학생 소스를 구분한다.\n"
            "- 제공된 함수명·입출력 계약·디렉터리 구조를 바꾸지 말고 파일별 수정안을 작성한다.\n"
            "- ZIP 내용은 절대 실행된 것으로 가정하지 않는다. 실행 결과가 필요한 검증은 명령과 "
            "예상 판정 기준까지만 제시하고, 환경 의존 결과를 지어내지 않는다."
        )
    return ""
