# 최신 기술·OSS 통합 (2026)

Until 파이프라인 레이어별로 적용한 최신 기술과 오픈소스. 모두 **graceful fallback** — 미설치/무키 상태에서도 Mock 백엔드로 오프라인 데모·테스트가 그대로 돌아간다.

| # | 레이어 | 적용 기술/OSS | 코드 | 키/설치 필요 |
|---|---|---|---|---|
| 1 | Capture(문서 파싱) | **Docling** (IBM, MIT) | `capture/ingest.py` | `pip install docling` |
| 2 | Execution 근거 | **Anthropic Citations API** | `llm/request_builder.py` | API 키 |
| 3 | 비용 | **Prompt Caching** | `llm/request_builder.py` | API 키 |
| 4 | Understanding | **Structured Outputs** | `understanding/task_spec.py` | API 키 |
| 5 | 프롬프트 품질 | **DSPy + GEPA** | `optimize/` | `pip install dspy` + 키 |

---

## 1. Docling — 문서 파서 품질 업그레이드 (토큰 0 유지)

기존 PyMuPDF+정규식 섹션화를 Docling 경로로 교체. PDF/DOCX/PPTX/HTML을 구조 인식 마크다운으로 추출(표·헤딩 보존). `parser_backend="auto"`면 Docling 시도 후 실패 시 basic으로 자동 폴백 → 의존성 없이도 안전.

```bash
UNTIL_PARSER=docling python -m until.cli assignment.pdf   # 또는 auto(기본)
```

근거: 2026 오픈소스 PDF 파서 비교에서 Docling은 자체 호스팅 RAG용 최상위권(MIT, GPU 45p/s). 복잡한 표가 많으면 Unstructured도 대안.

## 2. Citations API — "진짜 잘해준다"의 핵심

Execution에서 자료를 `document` 블록으로 전달하고 `citations.enabled=true`를 켠다. 응답이 **원문 span을 직접 인용** → `[출처?]` 자리표시와 환각 출처가 사라짐. 학문적 정직성에 직결. 결과는 `LLMResult.citations`로 노출.

## 3. Prompt Caching — "토큰 안 쓰는 단계"의 연장

Capture(파싱)는 토큰 0을 유지하고, Understanding·Execution·**reask에서 반복 투입되는 자료/시스템 프롬프트**에 `cache_control: ephemeral`을 건다. reask 루프가 돌수록 절감 효과가 커진다. (2026.2부터 캐시는 워크스페이스 단위 격리.) `LLMResult.cache_read`로 캐시 적중 토큰 확인 가능.

## 4. Structured Outputs — 파싱 깨짐 제거

Understanding의 TaskSpec를 JSON 스키마(`TASK_SPEC_SCHEMA`)로 강제. `json.loads` 실패 경로가 사실상 사라지고, 컴파일된 grammar가 24h 캐시돼 빨라진다.

## 5. DSPy + GEPA — 경계선 프롬프트 자동 최적화 ⭐

GEPA(Generalized Error-driven Prompt Augmentation)는 실패를 자연어로 성찰해 프롬프트를 진화시키는 옵티마이저(RL 대비 35× 적은 rollout으로 +20%, 논문 *Reflective Prompt Evolution Can Outperform RL*).

**우리만의 강점: 라벨 데이터가 필요 없다.** 목적함수가 `BoundaryValidator`(경계선 통과 여부)라서, 과제 입력만 있으면 자기지도로 최적화된다. 메트릭은 점수 + **위반 내역(자연어 feedback)**을 반환 → GEPA reflection LM이 "1인칭 단정/게으른 공백/마커 누락" 패턴을 분석해 Execution 프롬프트를 다시 쓴다.

```bash
pip install dspy
export ANTHROPIC_API_KEY=...
python -m until.optimize.run_gepa     # → until/optimize/optimized_prompt.txt
```

비대칭 구조(HF cookbook): 저렴한 student LM이 99% 추론, 똑똑한 reflection LM이 1% 성찰. `UNTIL_MODEL`(student) / `UNTIL_REFLECT_MODEL`(reflection)로 분리 지정.

구성요소: `optimize/metric.py`(목적함수=BoundaryGuard), `program.py`(DSPy 프로그램), `trainset.py`(입력 예시 4건, 라벨 없음), `run_gepa.py`(러너).

---

## 검증 상태

- 오프라인 테스트: `tests/test_pipeline.py`(6) + `tests/test_integrations.py`(4) 전부 통과.
- API 요청 구성(citations/caching/structured)은 SDK 없이 순수 함수(`request_builder.py`)로 단위 검증.
- GEPA 메트릭 코어(`score_and_feedback`)는 dspy 없이 단위 검증.
- 키/딥 의존성이 필요한 라이브 경로(2·3·4·5)는 코드 완성 + import-guard. 실제 호출 검증은 키 투입 후 1회 필요.

## 다음 단계 (라이브 1회 검증)

1. `pip install anthropic docling dspy` + `ANTHROPIC_API_KEY`.
2. `python -m until.cli <과제.pdf> --backend anthropic` — citations/caching 적중 확인.
3. `python -m until.optimize.run_gepa` — 최적화된 instruction 산출 → `prompts.SYSTEM`에 반영.

---

## 무료로 돌리기 (API 결제 없이) 💸

라이브 모델 출력이 필요한데 돈을 쓰기 싫다면:

**옵션 A — 로컬 Ollama (완전 무료, 권장)**
```bash
# 1) Ollama 설치 후 모델 받기 (예: 가벼운 3B)
ollama pull llama3.2
# 2) 파이프라인을 로컬 백엔드로 실행 (키 불필요)
pip install openai
python -m until.cli examples/sample_assignment.txt --backend local
# 3) GEPA 최적화도 로컬로 (기본값이 ollama)
pip install dspy
python -m until.optimize.run_gepa
```
`--backend local`은 OpenAI 호환 엔드포인트를 쓰며 기본값이 Ollama(`localhost:11434`)다. 키도 결제도 없음. 단, Anthropic 전용 기능(citations/캐싱)은 로컬에선 자동 비활성(자료는 프롬프트에 인라인).

**옵션 B — 무료 API 티어** (노트북이 약하면)
환경변수만 바꾸면 됨:
```bash
# Groq 무료 티어 예시
export UNTIL_BASE_URL=https://api.groq.com/openai/v1
export UNTIL_API_KEY=<groq_free_key>
export UNTIL_MODEL=llama-3.3-70b-versatile
python -m until.cli 과제.txt --backend local
```
Gemini(무료 티어, OpenAI 호환 엔드포인트), OpenRouter(모델명 `:free`)도 같은 방식.

**옵션 C — 개발/데모는 그냥 Mock ($0, 설치 0)**
실제 추론이 필요 없는 단계(파이프라인 검증, UX 데모, 테스트)는 `--backend mock`으로 끝. 지금 모든 테스트가 이걸로 돈다.

> 백엔드별 비용: `mock`=0 / `local`(Ollama)=0 / `local`(무료 API 티어)=0~소액 / `anthropic`=유료(citations·캐싱 풀 기능).


---

## Windows PowerShell 사용자 ⚠️

PowerShell에선 `export`가 안 먹는다(그건 Mac/Linux 문법). `$env:`를 써라:

```powershell
cd <until-mvp 폴더 경로>
$env:UNTIL_BASE_URL="https://api.groq.com/openai/v1"
$env:UNTIL_API_KEY="gsk_..."        # Groq 무료 키
$env:UNTIL_MODEL="llama-3.3-70b-versatile"
python -m until.cli examples\sample_assignment.txt --backend local
```

GEPA도 동일:
```powershell
$env:UNTIL_GEPA_MODEL="groq/llama-3.3-70b-versatile"
python -m until.optimize.run_gepa
```

**만약 anyio 관련 충돌/에러가 나면** (pip가 anyio를 3.7.1로 낮춰서 생기는 경고):
```powershell
python -m pip install --upgrade anyio
```
설치 중 `~penai`(깨진 openai 잔여물) 경고가 보이면 무시해도 되고, 깔끔히 하려면 site-packages에서 `~penai` 폴더만 지우면 된다.
