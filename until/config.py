"""Runtime configuration for the Until pipeline."""
from __future__ import annotations
from dataclasses import dataclass, field
import os
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """의존성 없이 단순 KEY=VALUE .env를 기존 환경보다 낮은 우선순위로 읽는다."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ[key] = value


load_dotenv()


def measured_enforce_active() -> bool:
    """실측 사후 검증기 강제 게이트 — UNTIL_MEASURED_ENFORCE, 기본 활성(승격).

    "0"이면 readiness fail 승격·legacy 사후 reask+결정적 치환·제출 게이트
    regex 하드 블록을 모두 끄고 기존(경고만) 동작으로 되돌린다
    (로드맵 Tier2-6 "수치 날조 검증기 경고→차단 승격"의 탈출구).
    """
    return os.getenv("UNTIL_MEASURED_ENFORCE", "1") != "0"


def tone_register_active() -> bool:
    """과제별 톤 레지스터 게이트 — UNTIL_TONE_REGISTER, 기본 **비활성**.

    "1"일 때만 `context/tone.py`의 ToneSpec이 Execution 프롬프트에 주입된다.
    꺼져 있으면 기존 VoiceProfile 힌트만 들어가 출력이 바이트 동일하다
    (신규 경로는 전부 플래그 뒤 — 기존 동작을 깨지 않는다는 작업 원칙).
    """
    return os.getenv("UNTIL_TONE_REGISTER", "0") == "1"


def context_depth_active() -> bool:
    """맥락 3계층 게이트 — UNTIL_CONTEXT_DEPTH, 기본 **비활성**.

    "1"일 때만 L2(에피소드 유사 사례 few-shot)와 L3(사실 기억)이 프롬프트에
    주입된다. L1(스타일 카드)은 ToneSpec 기준선으로 녹아들므로
    `tone_register_active()` 쪽 게이트를 따른다.
    """
    return os.getenv("UNTIL_CONTEXT_DEPTH", "0") == "1"


def edit_capture_active() -> bool:
    """수정 diff 캡처 게이트 — UNTIL_EDIT_CAPTURE, 기본 **활성**.

    다른 신규 경로와 달리 기본값이 켜짐인 이유: 이 로깅은 프롬프트·출력을 전혀
    바꾸지 않고(결정성 불변) 오직 파일에만 쓴다. 반면 꺼 두면 개인화의 가장
    값싸고 정확한 신호가 영영 쌓이지 않는다. 탈출구는 UNTIL_EDIT_CAPTURE=0.
    """
    return os.getenv("UNTIL_EDIT_CAPTURE", "1") != "0"


def algo_version() -> str:
    """알고리즘 버전 게이트 — UNTIL_ALGO_VERSION, 기본 "v0.1".

    2026-08 동결·측정 규율: 신설 라우팅·골격(v0.2, COURSE_ALGORITHMS_2026F)은
    이 값이 정확히 "v0.2"일 때만 켜진다. 알 수 없는 값은 전부 v0.1로 정규화해
    오타가 조용히 신규 경로를 켜는 사고를 막는다.
    """
    v = (os.getenv("UNTIL_ALGO_VERSION") or "").strip()
    return "v0.2" if v == "v0.2" else "v0.1"


@dataclass
class Config:
    # LLM backend: "mock" (offline, deterministic) or "anthropic" (live).
    backend: str = field(default_factory=lambda: os.getenv("UNTIL_BACKEND", "mock"))
    model: str = field(default_factory=lambda: os.getenv("UNTIL_MODEL", "claude-sonnet-4-6"))

    # auto_accept: 사용자 확인 없이 각 단계 산출물을 자동 수락 ("모두 수락" 모드).
    # False면 경계선의 결정 지점들을 사람에게 넘기고 멈춘다.
    auto_accept: bool = field(default_factory=lambda: os.getenv("UNTIL_AUTO_ACCEPT", "0") == "1")

    # suggest_prompts: 다음 단계에서 "뭐라고 프롬프트하면 되는지" 사용자에게 제안.
    suggest_prompts: bool = True

    # 페르소나(개인화) 파일 경로 — Personalization 단계의 스텁.
    persona_path: str | None = field(default_factory=lambda: os.getenv("UNTIL_PERSONA"))
    # BoundaryGuard 설정
    max_reasks: int = field(default_factory=lambda: int(os.getenv("UNTIL_MAX_REASKS", "2")))
    min_decisions: int = 1

    # 준수 강제(생성 루프 내 검증) — 분량·양식 미달 시 reask로 재생성.
    # mock 백엔드는 결정적(재생성해도 같음)이라 기본 제외 — UNTIL_ENFORCE_MOCK=1로 강제.
    enforce_length: bool = field(
        default_factory=lambda: os.getenv("UNTIL_ENFORCE_LENGTH", "1") == "1")
    enforce_form: bool = field(
        default_factory=lambda: os.getenv("UNTIL_ENFORCE_FORM", "1") == "1")
    enforce_on_mock: bool = field(
        default_factory=lambda: os.getenv("UNTIL_ENFORCE_MOCK", "0") == "1")

    # 근거 충분성 판정 임계값(4단계 — evidence.sufficiency). 초기값은 보수적,
    # eval로 조정한다(감으로 고치지 말 것).
    evidence_sufficient_chars: int = field(
        default_factory=lambda: int(os.getenv("UNTIL_EVIDENCE_CHARS", "100")))
    evidence_min_tokens: int = field(
        default_factory=lambda: int(os.getenv("UNTIL_EVIDENCE_TOKENS", "2")))
    # 구체성 최소 점수(6단계 — specificity). 초기값은 eval로 조정한다.
    specificity_min: float = field(
        default_factory=lambda: float(os.getenv("UNTIL_SPECIFICITY_MIN", "0.55")))

    # 생성 경로 — unit(단위별 근거·계획·검증·재생성, 기본) | legacy(통짜 1회).
    # 2026-08-14 사용자 결정으로 unit을 기본 전환: 3인 코퍼스에서 legacy의 유일한
    # 잔여 실패(기여자 B 양식 조립 건)를 unit이 통과했고, 근거 원장·absent 생성 금지
    # 등 환각 차단이 코드 수준인 경로라 안전측이다. UNTIL_PIPELINE=legacy로 회귀 가능.
    pipeline_mode: str = field(
        default_factory=lambda: os.getenv("UNTIL_PIPELINE", "unit"))
    # 단위별 생성 병렬도(라이브 TPM 보호를 위해 보수적 기본값).
    unit_parallel: int = field(
        default_factory=lambda: int(os.getenv("UNTIL_UNIT_PARALLEL", "3")))

    # 문서 파서 백엔드: "auto"(docling→basic) | "docling" | "basic"
    # (주의: 데이터 파싱 단계. 서울대 LMS "eTL"과는 무관 — 이름 충돌 회피용으로 parser_backend로 명명)
    parser_backend: str = field(default_factory=lambda: os.getenv("UNTIL_PARSER", "auto"))
