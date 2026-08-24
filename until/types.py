"""
`Result`를 `pipeline.py`에서 분리한 전용 모듈.

MCP 서버(`mcp_server.py`)가 `Result` 타입 하나 때문에 `pipeline.py`(그리고 그 안의
`llm.base.build_client`, `context.bundle`→`llm.base` 체인)를 통째로 로딩하지 않도록
하는 것이 목적이다. 이 모듈은 가벼운 의존성만 가져야 한다 —
`until.llm`·`until.pipeline`·`until.web`을 import하지 마라.
`ContextBundle`은 `context.bundle`이 `llm.base`를 끌어오므로 TYPE_CHECKING 가드로만 참조한다.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

from .capture.models import Document
from .boundary.models import Draft
from .execution.boundary_guard import GuardReport

if TYPE_CHECKING:
    from .context.bundle import ContextBundle


@dataclass
class Result:
    documents: List[Document]
    spec: dict
    draft: Draft
    guard: GuardReport
    suggested_prompts: List[str] = field(default_factory=list)
    context: Optional["ContextBundle"] = None
    # P6 — 결정 해소 후 2차 패스(finalize)로 만든 최종 완성본(있을 때만).
    final_draft: Optional[Draft] = None
    final_guard: Optional[GuardReport] = None
    # P10/P11 — eTL에서 자동수집해 주입한 관련자료(표시용; MaterialHit 목록).
    etl_materials: list = field(default_factory=list)
    # 4번 — 이 과제 관련 eTL 공지(표시용; Announcement 목록).
    etl_announcements: list = field(default_factory=list)
    # 근거 자료 범례 — Execution에 1-기반 순서로 넣은 자료 제목들([자료N] 인용과 매칭).
    sources: List[str] = field(default_factory=list)
    # Execution에 실제로 넣은 SourceDoc 목록(범례와 같은 순서) — finalize·suggest·review
    # 2차 패스가 동일 번호 체계를 쓰도록 재사용한다.
    source_docs: list = field(default_factory=list)
    # 분량 요건 감지 결과(LengthTarget | None). 표시·판정용, 결정적.
    length_target: object = None
    # 마감일 감지 결과(Deadline | None). D-day 표시용, 결정적.
    deadline: object = None
    # 요구사항 원자 분해(ContentElement 목록) — 커버리지·근거 판정의 기반.
    content_elements: list = field(default_factory=list)
    # 단위별 경로(UNTIL_PIPELINE=unit)의 ResponseUnit 목록(진단·eval용).
    units: list = field(default_factory=list)
    # Capture 단계에서 파싱 실패로 스킵된 첨부 경고("파일명: 사유").
    capture_warnings: List[str] = field(default_factory=list)
    # 주차별 질의순번표에서 프로필 학번으로 찾은 담당 교수·실제 마감(있을 때만).
    inquiry_assignment: object = None
    # 형식 검증기(execution/format_guard)가 찾은 어긋남 — 고친 것과 남은 것.
    # `pipeline.run`은 채우지 않는다(8월 결정성 동결 — 지문이 바뀌면 안 된다).
    # 화면으로 나가기 직전 `web._apply_format_pass`가 채우고, 저장은 하지 않는다
    # (조회할 때마다 결정적으로 다시 계산된다). **필드로 등록하는 것 자체가 목적**이다 —
    # 등록하지 않고 속성만 붙이면 session_store._result가 TypeError를 내서 세션 저장이
    # 통째로 조용히 멈춘다(test_submit_ready·test_cloud가 잡았다).
    format_issues: list = field(default_factory=list)
    # 전수 과제 라우터가 정한 처리 알고리즘(AssignmentRoute).
    assignment_route: object = None
    # 코드 실행 러너의 결과({status, exit_code, stdout, stderr, detail}) — 웹이
    # 붙일 때만 채워진다. 파이프라인 자신은 코드를 실행하지 않는다(러너가 별도
    # 서비스인 이유: 웹 프로세스에 세션·토큰이 함께 있다).
    run_check: object = None
    # LLM 사용량 합산({llm_calls, llm_tokens_in, llm_tokens_out}) — run()·2차
    # 패스(finalize/suggest/review)가 같은 dict에 누적. 텔레메트리 원가 원천.
    llm_usage: Optional[dict] = None
    # 최초 Execution 프롬프트에 VoiceProfile 지침이 실제 주입됐는지의 provenance.
    voice_applied: bool = False
    # 톤 레지스터(UNTIL_TONE_REGISTER=1)로 확정된 말투 규격. 2차 패스(finalize·
    # suggest·revise)가 같은 문자열을 재사용해야 초안과 최종본의 톤이 갈리지 않는다.
    # 플래그가 꺼져 있으면 빈 문자열 — 그때 동작은 기존과 완전히 동일하다.
    tone_block: str = ""
    tone_register: str = ""      # 확정된 register_key(표시·텔레메트리용)
    tone_source: str = ""        # explicit | inferred | default
    # 민감·고위험 상황(사과·거절·갈등) — 자동 확정 금지, 사람 승인 대기 플래그.
    # 초안 생성 자체는 막지 않는다. 막는 것은 자동 확정·자동 제출뿐이다.
    needs_approval: bool = False
    approval_kinds: List[str] = field(default_factory=list)
    approval_messages: List[str] = field(default_factory=list)
    # 출처 기록 — 이게 없으면 나중에 "톤이 바뀐 게 모델 때문인지 프롬프트 때문인지"를
    # 영원히 가릴 수 없다. prompt_version은 SemVer+실제 조립 지문, model_version은
    # 설정값이 아니라 **응답한 모델**(폴백 사슬이면 관여한 순서대로 이어 붙인다).
    prompt_version: str = ""
    model_version: str = ""
    # 생성 소요 시간(ms). 이벤트의 latency_ms 원천 — 0으로 두면 "즉시 나왔다"는
    # 거짓 신호가 되고, 나중에 채널·모델별 체감 속도를 비교할 수 없다.
    # 결정성 게이트는 필드 allowlist 기반이라(draft/spec/sources/…) 영향이 없다.
    elapsed_ms: int = 0
    # 과거 과제 연습은 실제 제출 흐름과 분리한다. 감사 결과는 화면·세션에 보존.
    practice_mode: bool = False
    practice_audit: Optional[dict] = None
