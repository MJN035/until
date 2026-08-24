# 개인화 감사 (PHASE 0) — 톤 레지스터 / 맥락 깊이 / 크로스채널 대비

> 작성일 2026-08-20 · 대상 커밋 `ab19477` (`agent/roadmap-checkpoint-1`)
> 목적: (1) 과제별 톤 전환, (2) 맥락의 깊이, (3) 크로스채널 대비를 얹기 전에
> **현재 코드에 무엇이 이미 있고 무엇이 없는지**를 확정한다. 구현은 하지 않는다.

---

## 1. 진입점과 전체 아키텍처

| 항목 | 값 |
|---|---|
| 언어/런타임 | Python ≥3.10 (`pyproject.toml`, ruff target py313) |
| 코어 의존성 | **없음** (`dependencies = []`) — 표준 라이브러리만. 나머지는 전부 optional extras |
| 웹 | FastAPI/uvicorn (`until/asgi.py`, 프로덕션 엔트리) + 레거시 `http.server` (`until/web.py`, 렌더링·핸들러 실체) |
| DB | **없음.** 로컬 파일(`_until_work/`) + Cloudflare Workers KV 미러(`until/cloudkv.py`) |
| 배포 | Render(`render.yaml`, `uvicorn until.asgi:app`) + Cloudflare(랜딩/컨테이너, `deploy/`) |
| 규모 | `until/` 아래 파이썬 26,817줄 |

### 진입점
- `until/cli.py` → `main()` (`python -m until`, 콘솔 스크립트 `until`)
- `until/asgi.py::create_app()` → 라우트 대부분이 `until.web`의 렌더러·핸들러를 `run_in_threadpool`로 호출
- `until/web.py::serve()` — 레거시 HTTP 서버 + **모든 HTML 렌더링·세션 상태·클라우드 uid 스코프의 실제 구현부**
- 배치·검증 러너: `run_tests.py`, `run_evals.py`, `run_corpus_validation.py`, `run_etl_*.py`, `demo.py`

### 파이프라인 (CLAUDE.md와 일치)

```
eTL/파일 → Capture(파싱, LLM 0) → Understanding(LLM) → Context(수업자료·내 파일·내 말투)
        → Execution(경계선 초안) → Boundary(결정 지점) → Readiness/Submission Gate
```

오케스트레이터는 `until/pipeline.py::run()` 하나다(591줄). 여기서 spec·라우팅·
`system_extra`·`voice_hint`가 전부 조립된다.

### 디렉터리 역할

| 경로 | 역할 | LLM |
|---|---|---|
| `until/capture/` | 파일·LMS 파싱, 어댑터(`sources/canvas_api.py`, `moodle_ws.py`, `elice_api.py`, playwright) | 0 (불변 규칙) |
| `until/understanding/` | 과제 명세 추출, 유형 분류, 분량·마감·요건 감지, 라우트 추론 | 일부 사용 |
| `until/context/` | **개인화 레이어** — 말투, 답변 히스토리, 교수 피드백, 수업자료 검색, 과목 프로파일 | 원칙상 0 (예외 1건, §3) |
| `until/execution/` | 프롬프트, 초안 생성, 경계선 가드, 단위 파이프라인, 제출 게이트 | 사용 |
| `until/boundary/` | `[[DECISION]]` 모델·분류·해소 | 0 (불변 규칙) |
| `until/llm/` | 백엔드 추상화 (인터페이스 1개) | — |
| `until/telemetry/` | 비식별 신호 파이프(allowlist 방식) | 0 |
| `until/evals/`, `until/optimize/` | 골든셋 채점, GEPA 프롬프트 최적화 | 사용 |
| `until/runtime/` | 최근 추가된 로컬 에이전트 런타임(과제 실행 커널) | 사용 |

---

## 2. LLM 호출 지점 전수 (17곳)

모든 호출은 **`until/llm/base.py::LLMClient.complete()` 하나**만 지난다(불변 규칙 4).
시그니처: `complete(system, user, *, tag, json, schema, documents, cache) -> LLMResult`.
백엔드 3종: `anthropic_client.py`(Citations / Prompt caching / Structured Outputs),
`openai_compat.py`(Groq·Cerebras·Ollama 등), `mock_client.py`(결정적, 테스트 기본).
계측 래퍼는 `llm/meter.py::MeteredClient` → `Result.llm_usage`.

| # | 파일:줄 | tag | system 프롬프트 출처 | 톤 지침 주입? |
|---|---|---|---|---|
| 1 | `execution/drafter.py:50` | `execution` | `prompts.SYSTEM` + `system_extra` + **`voice_hint`** | O |
| 2 | `execution/drafter.py:122` | `finalize` | `prompts.FINALIZE_SYSTEM` + **`voice_hint`** | O |
| 3 | `execution/drafter.py:270` | `execution` | #1과 동일(측정값 근거 reask) | O |
| 4 | `execution/revise.py:32` | `execution` | `prompts.SYSTEM` + 부분수정 지시 | **X 누락** |
| 5 | `execution/unit_pipeline.py:126` | `execution-unit` | 단위별 조립 + `system_extra` | **X 누락** |
| 6 | `execution/review.py:107` | `review` | `REVIEW_SYSTEM` | — |
| 7 | `execution/suggest_answers.py:119` | `suggest` | 결정 답 제안 | X (제안문 톤) |
| 8 | `execution/content_plan.py:131` | `content-plan` | 단위 계획 | — |
| 9 | `execution/coverage.py:78` | `coverage` | 커버리지 판정 | — |
| 10 | `understanding/task_spec.py:58` | `understanding` | 명세 추출 | — |
| 11 | `understanding/requirements.py:181` | `requirements` | 요건 원자 분해(schema) | — |
| 12–14 | `understanding/route_inference.py:133,204,307` | `route-*` | 라우트 추론(schema) | — |
| 15 | `context/voice.py:101` | `voice` | **말투 요약 추출**(JSON) | — |
| 16 | `evals/runner.py:90` | `raw-baseline` | 비교군 | — |
| 17 | `evals/runner.py:37` | (위임) | 계측 래퍼 | — |

> **중요:** `UNTIL_PIPELINE` 기본값이 2026-08-14부터 `unit`이다. 즉 **현재 기본 경로(#5)에는
> `voice_hint`가 전달되지 않는다.** 말투 개인화는 `legacy` 경로(#1)와 finalize(#2)에서만 작동한다.

### 프롬프트 조립 방식 (핵심 경로)

`until/pipeline.py:307~331`에서 두 덩어리를 만들어 `drafter`로 넘긴다.

```python
system_extra = "\n\n".join(b for b in (
    gate_directive,                     # 자필 제출 규정 게이트
    gap_directive,                      # 원료 없음
    measured_ban,                       # 수치 날조 금지
    distributed_spec_directive(...), structured_assignment_directive(...),
    assignment_route_directive(route),  # 라우트별 지시
    type_guidance(task_type),           # 과제 유형별 지시 (TYPE_GUIDANCE dict)
    skeleton_directive(...), skeleton_contract,
    conversion_hint or decision_directive(task_type),
    length_directive(length_target),    # 분량
    form_directive(docs),               # 양식
    profile_hint(),                     # 내 프로필(이름·학번 …)
) if b)

voice_hint  = ctx.voice_hint            # VoiceProfile.to_prompt_hint()
voice_hint += answers_context_hint()    # 과거 결정 답에서 뽑은 '내 맥락'
voice_hint += feedback_hint             # 교수 피드백
```

그리고 `drafter.draft_to_boundary()`가
`system = prompts.SYSTEM + "\n\n" + system_extra + "\n\n" + voice_hint`로 이어 붙인다.

**관찰:** 조립은 전부 **문자열 concat**이고 순서는 `pipeline.run()` 안에 하드코딩돼 있다.
결과적으로 결정적이긴 하나, 그것을 보장·테스트하는 전용 직렬화 함수·버전·해시는 없다.

---

## 3. 지금 "말투"에 해당하는 개념 — 있다. 3겹으로 흩어져서.

### (a) `VoiceProfile` — 통계적 문체 프로파일 (`until/context/voice.py`, 134줄)

```python
@dataclass
class VoiceProfile:
    ending_style: str = "미상"     # 해요체 | 한다체 | 합니다체 | 반말 | 혼합 | 미상
    avg_sentence_len: int = 0
    frequent_terms: List[str] = []
    uses_emoji: bool = False
    exclaim_ratio: float = 0.0
    n_samples: int = 0
    llm_summary: str = ""          # enhance_voice_profile()가 LLM 1콜로 채움
```

- 추출: `build_voice_profile(texts)` — 정규식 기반 결정적. `_detect_ending()`은
  종결어미 4종을 카운트해 과반 미달이면 `"혼합"`.
- 주입: `to_prompt_hint()` → `【문체 지침 — 사용자 말투 모사】` 블록 문자열.
- **PHASE 1이 확장해야 할 기존 개념이 바로 이것이다.** 새로 만들 것이 아니라 상위 개념
  (`ToneSpec`)의 한 입력 소스로 흡수해야 한다.

### (b) 저장 — 프로파일 JSON 하나뿐 (`until/context/voice_autolearn.py`, 218줄)

- eTL(Canvas) 과거 **내 제출물 최대 30건**(과목 최대 20개)을 훑어 문체를 자동 학습.
  원문은 프로파일 추출 직후 폐기(`shutil.rmtree`).
- 저장: `_until_work/voice_profile.json`(로컬) / `_until_work/users/<uid>/voice_profile.json`(클라우드)

  ```json
  {"v": 1, "disabled": false, "n_docs": 12, "learned_at": "...", "profile": {...}, "stats": {...}}
  ```

- 원자적 교체 쓰기(Windows `replace` 재시도 포함), 버전 불일치·손상은 조용히 `None`.
- 통제 경로: `/voice/off`(disabled 플래그 저장), `/voice/relearn`(파일 삭제 → 다음 인박스에서 재학습).
- KV 미러 키 `vprof:<uid>`.

### (c) 하드코딩된 문체 규칙 — `until/execution/prompts.py` (29.8KB)

`SYSTEM` 안에 이미 상당한 문체 지시가 박혀 있다.

- `[ 자연스러운 글쓰기 ]` — 상투 도입구 금지, 빈 골조 헤딩 금지, 문장 리듬 다양화,
  AI식 상투 마무리 금지, 메타·헤지 줄이기
- 언어 강제 — "현대 한국어만, 한자·가나·악센트 라틴 문자 금지"
- 날짜 표기 규칙("2026년 7월 27일 오전 9시")
- `TYPE_GUIDANCE` dict — essay / report / reflective_report / inquiry / presentation … 유형별 구조 지시
- `FINALIZE_SYSTEM` — "글의 말투·문체는 [말투 지침]이 있으면 그대로 따른다"

**결론:** "말투"는 (a) 사용자별 저장 프로파일, (b) 프롬프트 조립 시 삽입되는 힌트 문자열,
(c) 전역 하드코딩 시스템 프롬프트 — 3겹으로 흩어져 있다.
**존댓말/반말 축조차 없다.** `ending_style` 하나가 speech_level과 formality를 뭉뚱그린
유일한 축이며, deference·warmth·directness·verbosity·호칭·인사 정형구·금지 표현·
시그니처 표현은 **개념 자체가 없다.**

---

## 4. 사용자별 데이터 모델

**스키마 정의 파일도, 마이그레이션 디렉터리도, ORM도 없다.** 전부 파일이다.

| 저장소 | 경로(로컬 / 클라우드) | 형식 | 버전 필드 | KV 키 | 삭제 경로 |
|---|---|---|---|---|---|
| 신상 프로필 | `profile.json` | JSON dict | 없음 | `prof:<uid>` | 없음(덮어쓰기만) |
| 문체 프로파일 | `voice_profile.json` | JSON | `v: 1` | `vprof:<uid>` | `/voice/relearn` |
| 교수 피드백 | `teacher_feedback.json` | JSON | 있음 | `tfb:<uid>` | `/voice/relearn` |
| 결정 답 히스토리 | `answer_history.jsonl`(최근 500줄) | JSONL | 없음 | `hist:<uid>` | `/history/clear` |
| 베타 피드백 로그 | `feedback.jsonl` | JSONL | 없음 | — | 없음 |
| 웹 세션 | `sessions[/<uid>]/<token>.json` | HMAC 서명 JSON | `VERSION = 2` | `sess:<uid>:<token>` | `/sessions/delete` |
| 과목 프로파일 | `course_profiles.json` | JSON(사용자 직접 편집) | 없음 | — | 없음 |
| 텔레메트리 | `users/<uid>/telemetry.jsonl` | JSONL | `SCHEMA_VERSION = "1.2"` | — | — |
| 동의 | `users/<uid>/consent.json` | JSON | `notice_version` | `consent:<uid>` | — |
| 크레딧·빌링 | `credits.json` | JSON | — | — | — |

로컬 경로는 전부 `_until_work/` 아래(gitignore 영역), 클라우드는 `_until_work/users/<uid>/` 아래다.

- 요청 스코프 격리 패턴: `web.py::_uid()`(쿠키) → `_user_root(uid)`, 그리고 thread-local
  오버라이드(`answer_history.set_history_path_override`, `profile.set_profile_path_override`).
  **새 저장소를 추가하면 이 패턴을 그대로 따라야 한다.**
- **마이그레이션 관행:** 버전 필드 + 로더 측 방어적 폴백 + 필요 시 `tools/migrate_*.py`
  스크립트(선례: `tools/migrate_sessions_v1_to_v2.py`). SQL 마이그레이션은 존재하지 않는다.
- **단일 "내 데이터 전부 삭제" 경로가 없다.** 삭제가 3개 라우트에 흩어져 있고,
  `profile.json` · `feedback.jsonl` · `telemetry.jsonl` · `credits.json`은 어느 경로로도 지워지지 않는다.

### 세션 직렬화의 엄격성 (착수 전 반드시 인지)

`until/session_store.py`는 `Result`의 모든 필드를 **명시적으로 열거**해 변환한다.

```python
known  = set(Result.__dataclass_fields__)
extras = set(vars(value)) - known
if extras - {"teacher_feedback"}:
    raise TypeError(f"unsupported Result fields: ...")
```

→ **`Result`에 필드를 하나라도 추가하면 `session_store.py`의 `_result()` / `_result_from()`도
반드시 같이 고쳐야 한다.** 안 고치면 세션 저장이 예외로 죽는다.
`tests/test_session_store.py`가 이를 강제한다.

---

## 5. 생성 결과에 대한 사용자 피드백 — 부분적으로만 기록된다

### 기록되는 것

| 신호 | 어디에 | 무엇이 |
|---|---|---|
| 결정 답변(채택) | `answer_history.jsonl` | `{note, answer, category, ts}` — `/finalize`에서 델타만 적립 |
| 만족도 1~5 | `feedback.jsonl`(`/rate`) | `satisfaction` |
| 말투 일치 여부 | 세션 `voice_match` + `feedback.jsonl`(`/rate/voice`) | bool 1개 |
| 재생성 횟수 | `telemetry_meta["revision_count"]` | 정수 카운터만 |
| 초안 → 최종 변경 | `until/diffview.py` | **표시용만** — 문단 단위 diff를 화면에 보여주고 버린다 |
| 집계 지표 | `telemetry.jsonl` | 결정 응답률·경고·가드 통과·토큰 등(전부 비식별) |
| 부분 수정 이력 | `_WORKSPACES[token]["versions"]` | **최근 5개 본문 스냅샷**(되돌리기용, 메모리 + 세션) |

### 기록되지 않는 것 — 가장 큰 공백

- **사용자가 손으로 고친 최종본이 어디에도 없다.** 초안 페이지에 편집 가능한 본문 입력란이 없다
  (`textarea`는 결정 답변 칸·재생성 지시문·숨김 복사 버퍼용뿐). 사용자는
  `.md/.docx/.pdf/.html`로 **다운로드해 밖에서 고치고** eTL에 낸다.
  즉 `(초안, 최종 발송본, diff)` 3종 세트의 **세 번째 항목이 시스템에 들어오지 않는다.**
  - 예외: `/submit/confirm`(Canvas 직접 제출)은 본문을 알지만, 그 본문도 Until이 만든 그대로다.
- `telemetry/schema.py`의 allowlist에는 **`edit_ratio`, `edit_ops` 필드가 이미 등재돼 있으나
  이 값을 만드는 코드가 전 코드베이스에 존재하지 않는다.** 자리만 예약돼 있다.
- **`prompt_version` / `model_version` — 어디에도 없다**(grep 0건).
  지금은 어떤 프롬프트·어떤 모델로 만든 출력인지 사후에 알 방법이 없다.
  `Config.model`은 env `UNTIL_MODEL`(기본 `"claude-sonnet-4-6"`)이지만, 라이브 운영은
  Cerebras → Kimi → Gemini → Groq 폴백 사슬이라 **실제로 어느 모델이 응답했는지 기록되지 않는다.**
- 재생성 시 사용자가 적은 **수정 지시문**(`instruction`)이 저장되지 않는다(호출 후 버려짐).

---

## 6. 테스트 인프라

- **러너:** `python run_tests.py` — 74개 스위트를 독립 프로세스로 병렬 실행(기본 jobs=8, ~21초).
  `-q` 요약, `-j 1` 직렬. 각 테스트 파일은 `python tests/test_x.py` 단독 실행도 된다.
- **현재 상태(직접 측정):** `pass=74 fail=0 / 74 (21.3s)`.
  첫 실행에서 1건 실패가 관측됐으나 재실행 시 전부 통과 — 병렬 플래키로 보이며 재현되지 않았다.
- **불변 규칙 2:** `--backend mock` + 전 스위트는 **키·인터넷 없이 항상 통과**해야 한다.
- 테스트 스타일: pytest 관용구를 거의 쓰지 않는다. `sys.path.insert` → 평범한 `assert` →
  `print("OK ...")`. fixture 대신 `tempfile.TemporaryDirectory()`. 바이너리 픽스처는 커밋하지 않고
  `evals/goldens.py`가 hwpx·zip을 **런타임에 생성**한다.
- **결정성 CI:** `.github/workflows/determinism.yml` — v0.2 코드를 건드리면 v0.1 출력의
  SHA-256 불변을 강제한다.
- 린트: `ruff check .` — 스타일 규칙 없음, 실버그 규칙만(`F, E9, B, PLE, RUF100`), line-length 120.
- 개인화 관련 기존 스위트: `test_context`, `test_voice_autolearn`, `test_voice_feedback`,
  `test_answer_history`, `test_teacher_feedback`, `test_profile`, `test_diffview`,
  `test_feedback`, `test_session_store`, `test_telemetry`, `test_evals`.
- 회귀 하니스: `run_evals.py`(골든 8케이스 × until vs raw LLM, 결정적 채점 표),
  `run_corpus_validation.py`(실제 3인 eTL 코퍼스, 지문화된 원장).
  **사람이 읽고 판단하는 side-by-side 리포트는 아직 없다.**

---

## 7. 코드 컨벤션

| 항목 | 관행 |
|---|---|
| 주석·docstring | **한국어**. 첫 줄은 한 문장 요약, 이어서 *왜 이렇게 했는지*(설계 근거·실관측·반례)를 길게 적는다. 이 리포의 가장 강한 문화 |
| 타입 | `from __future__ import annotations` 항상. dataclass 중심. `Optional` / `List` 명시. mypy 설정 없음 — 힌트는 문서 목적 |
| 데이터 모델 | `@dataclass`(일부 `frozen=True`). Pydantic은 `asgi.py`의 요청 바디에만 |
| 에러 처리 | **비치명 경로는 광범위 `except Exception: pass`.** 개인화·수집·미러는 "실패해도 본 흐름을 절대 막지 않는다"가 명시적 원칙. 반대로 세션 직렬화·텔레메트리는 **fail-closed로 예외를 던진다** |
| 기능 플래그 | 전부 **환경변수**. `config.py`의 게이트 함수(`measured_enforce_active()`, `algo_version()`) 또는 `Config` dataclass 필드로. 기본값은 하위호환 쪽. 탈출구를 항상 남긴다 |
| 파일 쓰기 | `atomicio.atomic_write_json` + `path_lock`, 또는 tmp → `replace` 재시도(Windows) |
| 네이밍 | 모듈·함수 snake_case, 비공개 `_prefix`. 도메인 용어 고정: Capture / Understanding / Execution / Boundary / eTL / Draft 경계선 / `[[DECISION]]` |
| 저장 경로 | 개인 데이터는 무조건 `_until_work/`(gitignore). 클라우드는 `users/<uid>/` 하위 |
| 커밋 | 한국어 한 줄 요약. 작은 변경 → 74스위트 통과 → 커밋. gitleaks pre-commit 우회 금지 |

---

## 8. Until의 실제 사용 시나리오 (레지스터 프리셋 도출 근거)

레지스터 프리셋은 임의로 정하지 않고, 코드가 실제로 분기하는 두 축에서 도출한다.

**축 A — 과제 유형** (`understanding/task_type.py::classify_task_type`, 결정적):
`essay` / `report` / `reflective_report` / `inquiry` / `problemset` / `code` /
`presentation` / `hdl_lab` / `general`

**축 B — 라우팅 전략** (`context/assignment_router.py::route_assignment`, 18종):
`weekly_inquiry`, `presentation_conversion`, `team_project`, `activity_form`,
`rmd_notebook`, `zip_project`, `reflective_series`, `distributed_spec`, `problem_set`,
`code_project`, `evidence_report`, `staged_writing`, `textbook_problem_set`,
`hdl_lab`, `lab_report_cycle`, `personal_upload`, `non_actionable`, `spec_clarification`

**수신자 관계** — 코드에 명시적 개념은 없으나 다음에서 유도 가능하다.

| 수신자 | 근거 |
|---|---|
| 담당 교수(실명 확인됨) | `context/inquiry_assignment.py::InquiryAssignment.professor` — 주차별 질의 순번표에서 결정적으로 매칭 |
| 채점자(익명) | 일반 제출함 — 기본값 |
| 팀원 | `team_project` 전략 |
| 행정 담당 | `activity_form`(참가결과보고서·신청서 양식) |
| 없음(기계 채점) | `code`, `problemset`, `rmd_notebook` |

즉 **존댓말 내부 레지스터가 실제로 갈리는 자리**는
`weekly_inquiry`(교수에게 직접 가는 질문), `reflective_series`·`reflective_report`(소감·후기),
`activity_form`(행정 양식), `team_project`(팀 커뮤니케이션)이고,
`essay`·`report`·`code`·`problemset`은 수신자 없는 문어체다.

---

## 9. 감사 결론 — 세 목표 대비 격차

| 목표 | 이미 있는 것 | 없는 것 |
|---|---|---|
| **(1) 톤 레지스터** | `VoiceProfile.ending_style` 1축, 유형별 `TYPE_GUIDANCE`, 저장·통제·KV 미러 배선 일체 | 존댓말 내부 축(speech_level / formality / deference / warmth / directness / verbosity), 표층 규칙, 상속 + 델타 구조, register_key, 결정적 직렬화 함수, 자동추론 ↔ 명시지정 분리 |
| **(2) 맥락 깊이** | 결정 답 히스토리(유사도 재제안), 교수 피드백, 수업자료 임베딩 검색, `diffview` | 3계층 분리 자체(L1/L2/L3가 전부 `voice_hint` 한 문자열에 뭉쳐 주입됨), 에피소드 저장·유사사례 검색, 사실 기억 분리 주입, **수정 diff 캡처**, n-gram 중복 검사, 고위험 상황 승인 대기, 금지 표현 사후 필터 |
| **(3) 크로스채널 대비** | uid 스코프 격리, KV 미러, allowlist 텔레메트리, 동의 저장 | 채널 중립 이벤트 스키마, `prompt_version` / `model_version`, 페르소나 export / import, 보관 기간 정책, 단일 전체 삭제 경로 |

### 이번 작업에서 코드가 강제하는 제약

1. `Result`에 필드 추가 → `session_store.py::_result()` / `_result_from()` 동시 수정 필수(안 하면 예외).
2. 텔레메트리에 나가는 문자열은 열거형·해시·고정 형식만. `model_version` 같은 자유 문자열은
   `_ENUMS` 등재 없이는 `TelemetryLeakBlocked`로 차단된다.
3. `capture/` · `boundary/` · `prompts/suggest.py`는 LLM 0.
   `context/`도 원칙상 0이지만 `context/voice.py::enhance_voice_profile(llm=None)`이 유일한 예외 —
   **호출자가 llm을 주입해야만 켜지는 형태**로만 예외를 늘려야 한다.
4. mock 백엔드에서 전 스위트가 키·인터넷 없이 통과해야 한다.
5. v0.2 게이트를 건드리면 v0.1 결정성 SHA-256 불변을 함께 검증해야 한다(8월 동결 규율).
6. 기본 생성 경로가 `unit`이므로, 톤 주입을 legacy에만 넣으면 **실사용자에게는 아무 일도 일어나지 않는다.**

---

## 10. 확정된 설계 결정 (2026-08-20, 사용자 승인)

| # | 결정 | 근거·영향 |
|---|---|---|
| D1 | **레지스터 프리셋은 기존 시나리오에서만 8종 도출.** 채널·산출물 추가 없음 | `task_type` × `assignment_route.strategy` × 수신자에서 도출(§8). deference·warmth 축이 실제로 작동하는 자리는 `inquiry_to_professor` · `reflective` · `form_admin` · `team_coordination` 4종이고, 나머지 4종은 해당 축을 중립값으로 고정한다 |
| D2 | **수정 diff는 UI 변경 없이 기존 전후만 로깅.** 편집 textarea·`POST /edit` 신설하지 않음 | 수집원은 ① `revise_session`의 `workspace["versions"]` 전후 + 지금 버려지는 `instruction`, ② `finalize`의 draft → final_draft. **한계: 이건 "LLM이 고친 것"이지 "사람이 고친 것"이 아니다.** 사람 편집 신호는 이번 범위에서 확보되지 않으며, `edit_events.py`의 스키마에 `edit_source ∈ {"llm_revise","finalize","human"}` 필드를 처음부터 넣어 나중에 human 소스를 추가해도 스키마 변경이 없게 한다 |
| D3 | **저장소는 기존 파일 패턴 유지.** SQLite 도입 없음 | 버전 필드 있는 JSON/JSONL + 로더 방어적 폴백 + 필요 시 `tools/migrate_*.py`. KV 미러·uid 스코프·원자적 쓰기 배선을 그대로 재사용한다. L2 에피소드 검색은 파일 전량 스캔 + 임베딩 코사인(현 `context/retrieval.py`와 동일 방식) — 사용자당 수백 건 규모라 충분 |
