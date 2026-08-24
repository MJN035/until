# AGENTS.md — Until 베타테스트 핸드오프 (Codex용)

> 이 파일을 프로젝트 루트에 두면 Codex가 자동으로 읽는다. 맨 아래 "붙여넣기용 시작 프롬프트"를 Codex에 그대로 던져도 된다.

> ⚠️ **현행화 안내(2026-07-11, v1.2.0+ 기준):** 이 문서는 초기(P0~P4) 핸드오프 스냅샷이라 일부가
> 낡았다. **먼저 `CLAUDE.md`(진행 이력·작업 방식)와 `docs/FEATURES.md`(기능→코드→테스트 지도)를
> 보라.** 특히 다음이 바뀌었다:
> - **루트 경로:** 아래 §1의 Claude 캐시 경로는 옛것 — 현재 루트는 이 파일이 있는 폴더
>   (`C:\Users\MJ\Desktop\until-CLEAN\until-mvp`, git 이력 포함).
> - **테스트:** 5개 스크립트 → **25스위트**, 러너는 `python run_tests.py`(병렬 ~8초, `-j 1`=순차).
> - **백로그:** 아래 "5시간 베타 백로그"(P0~P4)는 전부 완료. 이후 P5~P12·v0.2.0~v1.2.0
>   (제출 직전 점검·답변 재사용·마감 이해·간단 모드·바로 초안·플랜 스캐폴드)까지 진행 —
>   최신 상태는 `CHANGELOG.md` 참조.
> - 핵심 원칙(Draft 경계선·mock 오프라인 불변·작은 변경→테스트→커밋)은 그대로 유효하다.

---

## 0. 붙여넣기용 시작 프롬프트 (Codex에게)

```
너는 Until이라는 학생용 AI 에이전트 제품의 코드베이스에서 작업한다. 이 폴더 루트의 AGENTS.md를
먼저 끝까지 읽어라. 핵심 원칙은 "Draft 경계선" — 자료로 채울 수 있는 건 끝까지 쓰되, 사람의 고유
판단(관점·취향·진로·가치판단)은 절대 대신 정하지 말고 [[DECISION: ...]] 마커로 남긴다.

작업 규칙:
1) 시작 전 반드시 `python -m pytest -q`(또는 아래 5개 테스트 스크립트)를 돌려 베이스라인을 확인하라.
2) 변경 후에도 5개 테스트가 전부 통과해야 한다. 깨지면 고치거나 되돌려라.
3) `--backend mock`은 API 키 없이 항상 돌아가야 한다(오프라인 데모/CI 생명줄). 깨지 말 것.
4) 한 번에 하나의 작은 변경 → 테스트 → 커밋. 큰 리팩터링 금지.
5) 아래 "5시간 베타 백로그"의 우선순위 순서대로 진행하되, 막히면 건너뛰고 다음으로.

지금 할 일: 먼저 베이스라인 테스트를 돌리고 결과를 보고한 뒤, 백로그 P0부터 시작하라.
```

---

## 1. 프로젝트 좌표 (파일 위치)

**루트 (Claude가 작업하던 폴더 그대로 — 압축 풀 필요 없음):**
```
C:\Users\MJ\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\local-agent-mode-sessions\1898ce84-d772-4bd9-8a9b-263f9ac87007\14ed1986-a4d3-4206-9770-a3451ab076ee\local_13517aa2-bdb4-46cf-b984-d6ea9dee1203\outputs\until-mvp
```
PowerShell에선 길어서 변수로 잡는 걸 권장:
```powershell
$proj = "C:\Users\MJ\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\local-agent-mode-sessions\1898ce84-d772-4bd9-8a9b-263f9ac87007\14ed1986-a4d3-4206-9770-a3451ab076ee\local_13517aa2-bdb4-46cf-b984-d6ea9dee1203\outputs\until-mvp"
cd $proj
```
⚠️ 이 폴더는 Claude 앱의 작업 캐시 안이다. 세션 정리/앱 업데이트 때 비워질 수 있으니, 5시간 작업 전에 **이 폴더를 한 번 복사해두거나 git init 해두는 걸 권장**(`git init; git add -A; git commit -m baseline`).

루트에 `until/`, `tests/`, `examples/`, `docs/`, `README.md`가 보이면 맞는 위치다.

```
until-mvp/
├─ README.md                         # 빠른 시작·실행법
├─ AGENTS.md                         # ← 이 파일
├─ requirements.txt / pyproject.toml # 의존성(전부 선택사항; mock은 의존성 0)
├─ NOTICE                            # 차용 OSS 출처(guardrails, LangGraph)
│
├─ until/                            # ── 제품 패키지 ──
│  ├─ pipeline.py                    # ★ 오케스트레이션: Capture→Understanding→Context→Execution→Boundary
│  ├─ cli.py                         # ★ CLI 진입점(--source, --backend, --my-files/--voice/--course-materials)
│  ├─ config.py                      # 설정(backend, parser_backend, max_reasks 등)
│  │
│  ├─ capture/                       # [1] 문서 파싱 (토큰 0)
│  │  ├─ ingest.py                   #   PDF/txt/md → Document (docling→basic 폴백)
│  │  ├─ models.py                   #   Document, Section
│  │  └─ sources/                    #   ── 과제 소스 커넥터(eTL 등) ──
│  │     ├─ base.py                  #     BrowserAdapter / Source 프로토콜
│  │     ├─ models.py                #     Attachment, RawAssignment, CollectedAssignment
│  │     ├─ etl.py                   #     EtlSource + FixtureBrowserAdapter + ChromeBrowserAdapter(개발용 스텁)
│  │     ├─ moodle.py                #     parse_moodle_assignment() 순수 파서(구버전/Moodle용; eTL은 Canvas라 P1에서 canvas.py 추가)
│  │     ├─ playwright_adapter.py    #     ★ 제품용 라이브 어댑터(영속 프로필 SSO)
│  │     └─ collect.py               #     수집→ingest 파일목록 헬퍼
│  │
│  ├─ understanding/task_spec.py     # [2] LLM: TaskSpec 추출(Structured Outputs)
│  │
│  ├─ context/                       # [2.5] Personalization/Context (토큰 0) ★최근 추가
│  │  ├─ voice.py                    #   말투 프로파일(종결어미·문장길이·자주쓰는표현)
│  │  ├─ retrieval.py                #   관련 파일/수업자료 검색(키워드 중첩 점수)
│  │  └─ bundle.py                   #   셋을 모아 ContextBundle → Execution에 주입
│  │
│  ├─ execution/                     # [3] 경계선까지 초안
│  │  ├─ prompts.py                  #   ★ SYSTEM 프롬프트(경계선 규칙·few-shot·자기검증)
│  │  ├─ drafter.py                  #   맥락 근거+말투로 초안, BoundaryGuard 호출
│  │  └─ boundary_guard.py           #   ★ validate→reask 루프 + BoundaryValidator(경계선 규칙)
│  │
│  ├─ boundary/models.py             # [4] Draft/DecisionPoint/Resolution(approve·edit·reject·respond)
│  ├─ prompts/suggest.py             #     "다음에 뭐라고 프롬프트할지" 제안
│  │
│  ├─ llm/                           # ── 교체 가능한 LLM 래퍼 ──
│  │  ├─ base.py                     #   LLMClient 프로토콜 + build_client() 팩토리 + SourceDoc/Citation
│  │  ├─ mock_client.py              #   ★ 오프라인 결정적 백엔드(키 불필요)
│  │  ├─ openai_compat.py            #   local 백엔드(Ollama/Groq/Gemini 무료, OpenAI 호환)
│  │  ├─ anthropic_client.py         #   anthropic 백엔드(citations/캐싱/structured)
│  │  └─ request_builder.py          #   Anthropic 요청 구성 순수함수(테스트됨)
│  │
│  └─ optimize/                      # [별도] DSPy+GEPA 프롬프트 자동 최적화
│     ├─ metric.py                   #   목적함수=BoundaryGuard(라벨 불필요)
│     ├─ program.py / trainset.py    #   DSPy 프로그램·학습 입력
│     └─ run_gepa.py                 #   러너(python -m until.optimize.run_gepa)
│
├─ tests/                            # 18개 테스트(전부 오프라인·mock)
│  ├─ test_pipeline.py               #   end-to-end + BoundaryGuard
│  ├─ test_integrations.py           #   citations/caching/structured/로컬백엔드/GEPA메트릭
│  ├─ test_etl_source.py             #   eTL fixture 수집→ingest
│  ├─ test_moodle_parse.py           #   Moodle 파서
│  └─ test_context.py                #   말투/검색/번들
│
├─ examples/                         # 데모 입력 fixture
│  ├─ sample_assignment.txt          #   샘플 과제
│  ├─ etl_fixture/                   #   가짜 eTL 과제+첨부
│  ├─ my_files/ course_materials/ voice_samples/   # 맥락 데모용
│
└─ docs/                             # 설계 문서(읽고 시작할 것)
   ├─ ARCHITECTURE.md   PROMPT_DESIGN.md   TECH_STACK.md   ETL_CONNECTOR.md
```

★ = 변경 시 영향 큰 핵심 파일.

---

## 2. 실행 / 테스트 (Windows PowerShell)

```powershell
$proj = "C:\Users\MJ\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\local-agent-mode-sessions\1898ce84-d772-4bd9-8a9b-263f9ac87007\14ed1986-a4d3-4206-9770-a3451ab076ee\local_13517aa2-bdb4-46cf-b984-d6ea9dee1203\outputs\until-mvp"
cd $proj

# 베이스라인 테스트 (키 불필요)
python tests\test_pipeline.py
python tests\test_integrations.py
python tests\test_etl_source.py
python tests\test_moodle_parse.py
python tests\test_context.py
# pytest가 있으면: python -m pytest -q

# 기본 데모(오프라인)
python -m until.cli examples\sample_assignment.txt --backend mock

# 맥락 주입 데모
python -m until.cli examples\sample_assignment.txt --backend mock `
  --course-materials examples\course_materials --my-files examples\my_files --voice examples\voice_samples

# eTL 수집 데모(오프라인 fixture)
python -m until.cli --source etl-demo --backend mock

# 무료 라이브 모델(Groq) — 키 환경변수 후
$env:UNTIL_BASE_URL="https://api.groq.com/openai/v1"
$env:UNTIL_API_KEY="<groq키>"
$env:UNTIL_MODEL="llama-3.3-70b-versatile"
python -m until.cli examples\sample_assignment.txt --backend local
```

---

## 3. 절대 깨면 안 되는 불변 규칙 (INVARIANTS)

1. **경계선 원칙.** Execution 출력은 사람의 고유 판단을 직접 확정하지 않는다. 그런 지점은 `[[DECISION: ...]]`로 남긴다. `BoundaryValidator`(`execution/boundary_guard.py`)가 이를 강제한다 — 규칙을 약화시키지 말 것.
2. **mock 백엔드는 항상 무키로 동작.** `--backend mock` + 모든 테스트는 인터넷·API 키 없이 돌아야 한다.
3. **토큰 0 단계 유지.** `capture/`, `context/`, `boundary/`, `prompts/suggest.py`는 LLM을 호출하지 않는다(결정적). 여기에 LLM 호출 넣지 말 것.
4. **LLM은 `LLMClient.complete()` 한 인터페이스로만 호출.** 새 기능도 이 계약을 통해서. (`llm/base.py`)
5. **용어:** 데이터 파싱 단계 = "Capture". 서울대 LMS = "eTL". 둘을 섞지 말 것.
6. **adapter 패턴 유지.** 소스 접속 방식(Chrome/Playwright/확장)은 `BrowserAdapter` 뒤에 둔다. 파이프라인 코어는 접속 방식을 몰라야 한다.

---

## 4. 5시간 베타 백로그 (우선순위)

**P0 — 라이브 1회 검증 (가장 먼저)**
- Groq 무료 키로 `--backend local` 실제 통과 확인. 한국어 출력에 한자/일본어 섞이는 문제 → `execution/prompts.py` SYSTEM에 "반드시 한국어로만, 외국 문자 혼용 금지" 1줄 추가하고 효과 측정.
- (선택) `BoundaryValidator`에 "한자/가나 혼입" 검출 규칙 추가 → 위반 시 reask.

**P1 — eTL Canvas 어댑터** (★ 라이브 확인 완료)
- 서울대 eTL은 **Canvas LMS** 기반이다(`myetl.snu.ac.kr`, `window.ENV.COURSE_ID` 존재). vanilla Moodle 아님.
- **샘플 HTML 준비됨:** `examples/canvas_fixture/assignment_page.html` (+ `README.md`에 선택자·API 정리).
- 확인된 선택자: 제목 `div.assignment-title`(안 `h1.title`), 본문 `div.description.user_content`, 첨부 `a.instructure_file_link[data-api-endpoint]`(href `/courses/{cid}/files/{fid}/download`).
- 할 일: `capture/sources/canvas.py`에 순수 파서 `parse_canvas_assignment(html, base_url)` 작성(`moodle.py` 패턴) + `tests/test_canvas_parse.py`(fixture로 검증). 그 다음 `CanvasBrowserAdapter`(Playwright)로 라이브 연결. 구버전 `moodle.py`는 보존.
- **권장 제품 경로:** 브라우저 파싱 대신 **Canvas REST API** — `GET /api/v1/courses/{cid}/assignments/{aid}`(이름·설명HTML·마감), 인증은 사용자가 발급한 액세스 토큰(계정>설정). 학생 토큰 접근이 Moodle보다 쉬움. `CanvasApiAdapter`(BrowserAdapter 인터페이스 동일)로 추가 가능.

**P2 — 관련도 검색 업그레이드**
- `context/retrieval.py`의 키워드 중첩을 임베딩 유사도로 승급(예: `sentence-transformers` 또는 무료 임베딩 API). import-guard로 미설치 시 기존 키워드 방식 폴백.

**P3 — 베타 UX**
- 결과를 보기 좋은 마크다운/HTML 리포트로 저장하는 `--out report.md` 옵션.
- 결정 지점마다 사람이 답을 채우면 그걸 반영해 초안을 완성하는 `resolve` 흐름(이미 `Resolution` 스키마 있음 → drafter에 2차 패스 추가).

**P4 — 말투 정교화**
- `context/voice.py`에 (선택) LLM 1콜로 말투 요약을 더해 프로파일 보강. 단 키 없을 때 결정적 버전으로 폴백 유지.

각 항목: 작은 단위로 구현 → 해당 테스트 추가 → 5개+신규 테스트 전부 통과 → 커밋.

---

## 5. 확장 지점 (어떻게 추가하나)

- **새 LLM 백엔드:** `llm/`에 클라이언트 클래스(메서드 `complete(...)`) 추가 → `llm/base.py`의 `build_client()`에 분기 등록.
- **새 소스 커넥터(다른 LMS):** `capture/sources/`에 `XxxBrowserAdapter`(`fetch_assignment`/`download`) 추가. 순수 파서 함수는 따로 빼서 테스트.
- **새 경계선 규칙:** `execution/boundary_guard.py`의 `BoundaryValidator.validate()`에 체크 추가(결정적). reask 메시지는 `execution/prompts.py:reask_message`.
- **새 맥락 소스:** `context/`에 수집 함수 추가 → `bundle.assemble_context()`에서 합류 → `to_sources()`로 Execution에 전달.

---

## 6. 알려진 이슈 / 함정

- **Windows PowerShell:** 환경변수는 `export`가 아니라 `$env:VAR="값"`.
- **openai 구버전 충돌:** `proxies` 에러 나면 `python -m pip install --upgrade openai`(2.x). `~penai` 경고는 깨진 잔여물 → 무시 또는 site-packages에서 폴더 삭제.
- **Groq JSON 모드:** `response_format=json_object` 쓰려면 메시지에 'json' 단어가 있어야 함 → `llm/openai_compat.py`에서 이미 처리됨.
- **LearningX ≠ Moodle:** 위 P1 참고. 현재 Playwright/Moodle 파서는 구버전 eTL 가정.
- **개인정보:** eTL/드라이브 접속 시 비밀번호·세션을 코드에 저장하지 말 것. 로그인은 사용자 브라우저(영속 프로필).

---

## 7. 한 줄 요약
Until = "과제를 경계선 직전까지 대신 끝내되, 사람의 고유 판단은 절대 넘지 않는" 에이전트.
파이프라인: **eTL/파일 → Capture → Understanding → Context(수업자료·내 파일·내 말투) → Execution(경계선 초안) → Boundary(결정 지점 + 프롬프트 제안).**

<!-- OMX:AGENTS:START -->
<!-- AUTONOMY DIRECTIVE — DO NOT REMOVE -->
YOU ARE AN AUTONOMOUS CODING AGENT. EXECUTE TASKS TO COMPLETION WITHOUT ASKING FOR PERMISSION.
DO NOT STOP TO ASK "SHOULD I PROCEED?" — PROCEED. DO NOT WAIT FOR CONFIRMATION ON OBVIOUS NEXT STEPS.
IF BLOCKED, TRY AN ALTERNATIVE APPROACH. ONLY ASK WHEN TRULY AMBIGUOUS OR DESTRUCTIVE.
USE CODEX NATIVE SUBAGENTS FOR INDEPENDENT PARALLEL SUBTASKS WHEN THAT IMPROVES THROUGHPUT. THIS IS COMPLEMENTARY TO OMX TEAM MODE.
<!-- END AUTONOMY DIRECTIVE -->
<!-- omx:generated:agents-md -->

# oh-my-codex - Intelligent Multi-Agent Orchestration

You are running with oh-my-codex (OMX), a coordination layer for Codex CLI.
This AGENTS.md is the top-level operating contract for the workspace.
Role prompts under `prompts/*.md` are narrower execution surfaces. They must follow this file, not override it.
When OMX is installed, load the installed prompt/skill/agent surfaces from `./.codex/prompts`, `./.codex/skills`, and `./.codex/agents` (or the project-local `./.codex/...` equivalents when project scope is active).

<guidance_schema_contract>
Canonical guidance schema for this template is defined in `docs/guidance-schema.md`.
Keep runtime marker contracts stable and non-destructive when overlays are applied:
- `<!-- OMX:RUNTIME:START --> ... <!-- OMX:RUNTIME:END -->`
- `<!-- OMX:TEAM:WORKER:START --> ... <!-- OMX:TEAM:WORKER:END -->`
</guidance_schema_contract>

<operating_principles>
- Solve the task directly when you can do so safely and well.
- Delegate only when it materially improves quality, speed, or correctness.
- Keep progress short, concrete, and useful.
- Prefer evidence over assumption; verify before claiming completion.
- Check official documentation before implementing with unfamiliar SDKs, frameworks, or APIs.
- Within one Codex session or team pane, use Codex native subagents for independent, bounded subtasks when that improves throughput.
<!-- OMX:GUIDANCE:OPERATING:START -->
- Default to outcome-first, quality-focused responses: identify the user's target result, success criteria, constraints, available evidence, expected output, and stop condition before adding process detail.
- Keep collaboration style short and direct. Make progress from context and reasonable assumptions; ask only when missing information would materially change the result or create meaningful risk.
- Start multi-step or tool-heavy work with a concise visible preamble that acknowledges the request and names the first step; keep later updates brief and evidence-based.
- Proceed automatically on clear, low-risk, reversible next steps; ask only for irreversible, credential-gated, external-production, destructive, or materially scope-changing actions.
- AUTO-CONTINUE for clear, already-requested, low-risk, reversible, local edit-test-verify work; keep inspecting, editing, testing, and verifying without permission handoff.
- ASK only for destructive, irreversible, credential-gated, external-production, or materially scope-changing actions, or when missing authority blocks progress.
- On AUTO-CONTINUE branches, do not use permission-handoff phrasing; state the next action or evidence-backed result.
- Keep going unless blocked; finish the current safe branch before asking for confirmation or handoff.
- Ask only when blocked by missing information, missing authority, or an irreversible/destructive branch.
- Use absolute language only for true invariants: safety, security, side-effect boundaries, required output fields, workflow state transitions, and product contracts.
- Do not ask or instruct humans to perform ordinary non-destructive, reversible actions; execute those safe reversible OMX/runtime operations and ordinary commands yourself.
- Treat OMX runtime manipulation, state transitions, and ordinary command execution as agent responsibilities when they are safe and reversible.
- Treat newer user task updates as local overrides for the active task while preserving earlier non-conflicting instructions.
- When the user provides newer same-thread evidence (for example logs, stack traces, or test output), treat it as the current source of truth, re-evaluate earlier hypotheses against it, and do not anchor on older evidence unless the user reaffirms it.
- Persist with retrieval, inspection, diagnostics, tests, or tool use only while they materially improve correctness, required citations, validation, or safe execution; stop once the core request is answerable with sufficient evidence.
- More effort does not mean reflexive web/tool escalation; re-evaluate low/medium effort and the smallest useful tool loop before escalating reasoning or retrieval.
<!-- OMX:GUIDANCE:OPERATING:END -->
</operating_principles>

## Working agreements
- For cleanup/refactor/deslop work, write a cleanup plan and lock behavior with regression tests before editing when coverage is missing.
- Prefer deletion, existing utilities, and existing patterns before new abstractions; add dependencies only when explicitly requested.
- Keep diffs small, reviewable, and reversible.
- Verify with lint, typecheck, tests, and static analysis after changes; final reports include changed files, simplifications, and remaining risks.


<delegation_rules>
Default posture: work directly.

Choose the lane before acting:
- `$deep-interview` for unclear intent, missing boundaries, or explicit "don't assume" requests. It clarifies and hands off; it does not implement.
- `$ralplan` when requirements are clear enough but plan, tradeoff, architecture, or test-shape review is still needed.
- `$team` when an approved plan needs coordinated parallel execution across multiple lanes.
- `$ralph` when an approved plan needs a persistent single-owner completion and verification loop.
- Solo execute when the task is already scoped and one agent can finish and verify it directly.
- Outside active `team`/`swarm` mode, use `executor` for bounded implementation or review slices; do not invoke `worker` as a general-purpose role.
- Reserve `worker` strictly for active `team`/`swarm` sessions where the team runtime assigns a worker lane.
- `worker` is a team-runtime surface, not a general-purpose child role.


Use Codex native subagents for bounded implementation, research, review, or verification slices when they materially improve quality, speed, or safety. Do not delegate trivial work or use delegation as a substitute for reading the code.
- While a Conductor workflow is active, native children are verification/advice-only: they may perform positively classified reads, but child-to-leader reporting also requires separate host-authenticated caller, parent, and target proof. Codex 0.145.0 does not expose that proof, so collaboration reporting and source/product mutations remain denied. Route implementation through Team only after Team's separate host-authority checks pass; when Team is unavailable or denied, return a bounded read-only result or blocker instead of treating local state, task text, session fields, trackers, or child provenance as authority.
</delegation_rules>

<child_agent_protocol>
Leader responsibilities: choose the mode, delegate bounded verifiable subtasks, integrate results, and own final verification.
Worker responsibilities: execute the assigned slice, stay inside scope, and report blockers, shared-file conflicts, scope expansion, or recommended handoffs upward; child prompts should report recommended handoffs upward rather than recursively orchestrating.
Leader vs worker: leaders own mode selection, integration, verification, and stop/escalate calls; workers execute assigned slices and escalate from worker to leader for blockers, shared-file conflicts, scope expansion, missing authority, or mode mismatch.
Rules: max 6 concurrent child agents; child prompts remain under AGENTS.md authority; prefer inherited model defaults unless a task has a concrete model reason; `worker` is a team-runtime surface, not a general-purpose child role.
</child_agent_protocol>


<invocation_conventions>
- `$name` — invoke a workflow skill.
- `/skills` — browse available skills.
- Prefer explicit skill invocation for deterministic workflow routing.
</invocation_conventions>

<model_routing>
Match role to task shape: `explore` for repo lookup, `researcher` for official docs/reference gathering, `dependency-expert` for SDK/package decisions, `executor` for implementation, `debugger` for root cause, `architect`/`critic` for high-complexity review. Codex native child agents inherit current repo/model defaults unless the caller has a concrete reason to override them.
</model_routing>

<specialist_routing>
Leader/workflow routing contract:
<!-- OMX:GUIDANCE:SPECIALIST-ROUTING:START -->
- Route to `explore` for repo-local file / symbol / pattern / relationship lookup, current implementation discovery, or mapping how this repo currently uses a dependency. `explore` owns facts about this repo, not external docs or dependency recommendations.
- Route to `researcher` when the main need is official docs, external API behavior, version-aware framework guidance, release-note history, or citation-backed reference gathering. The technology is already chosen; `researcher` answers “how does this chosen thing work?” and is not the default dependency-comparison role.
- Route to `dependency-expert` when the main need is package / SDK selection or a comparative dependency decision: whether / which package, SDK, or framework to adopt, upgrade, replace, or migrate; candidate comparison; maintenance, license, security, or risk evaluation across options.
- Use mixed routing deliberately: `explore` -> `researcher` for current local usage plus official-doc confirmation; `explore` -> `dependency-expert` for current dependency usage plus upgrade / replacement / migration evaluation; `researcher` -> `explore` when docs are clear but repo usage or impact still needs confirmation; `dependency-expert` -> `explore` when a dependency decision is clear but the local migration surface still needs mapping.
- Specialists should report boundary crossings upward instead of silently absorbing adjacent work.
- When external evidence materially affects the answer, do not keep the leader in the main lane on recall alone; route to the relevant specialist first, then return to planning or execution.
<!-- OMX:GUIDANCE:SPECIALIST-ROUTING:END -->
</specialist_routing>

<agent_catalog>
Key roles: `explore`, `researcher`, `dependency-expert`, `planner`, `architect`, `debugger`, `executor`, `test-engineer`, `verifier`, and `critic`. Use the installed role catalog for full descriptions.
</agent_catalog>

<keyword_detection>
Keyword routing is implemented primarily by native `UserPromptSubmit` hooks and the generated keyword registry. Treat hook-injected routing context as authoritative for the current turn, then load the named `SKILL.md` or prompt file as instructed.

Fallback behavior when hook context is unavailable:
- Explicit `$name` invocations run left-to-right and override implicit keywords.
- Bare skill names do not activate skills by themselves; skill-name activation requires explicit `$skill` invocation. Natural-language routing phrases may still map to a workflow. Examples: `analyze` / `investigate` → `$analyze` for read-only deep analysis with ranked synthesis, explicit confidence, and concrete file references; `deep interview`, `interview`, `don't assume`, or `ouroboros` → `$deep-interview` for Socratic deep interview requirements clarification.
- Keep the detailed keyword list in `src/hooks/keyword-registry.ts`; do not duplicate it here.

Runtime workflows such as `autopilot`, `ralph`, `ultrawork`, `ultraqa`, `team`/`swarm`, and `ecomode` require OMX CLI runtime support. In Codex App, outside-tmux, or plain Codex sessions without OMX tmux runtime, explain that those workflows are not directly available there and continue with the nearest App-safe surface unless the user explicitly wants to launch OMX CLI from shell first.
- When deep-interview is active in attached-tmux OMX CLI/runtime, ask each interview round via `omx question`; after launching `omx question` in a background terminal, wait for that terminal to finish and read the JSON answer before continuing; preserve the leader pane with `OMX_QUESTION_RETURN_PANE=$TMUX_PANE` when invoking it through Bash/tool paths. Outside tmux or native surfaces that cannot render `omx question` should use the native structured question path when available; otherwise ask exactly one concise plain-text question and wait for the answer.

</keyword_detection>

<skills>
Skills are workflow commands. Always load the relevant installed `SKILL.md` before following a skill-specific process. Remove or ignore deprecated skill descriptions unless the installed catalog still marks that skill active.
</skills>

<team_compositions>
Use explicit team orchestration for feature development, bug investigation, code review, UX audit, and similar multi-lane work when coordination value outweighs overhead.
</team_compositions>

<team_pipeline>
Team mode is the structured multi-agent surface. Use it when durable staged coordination is worth the overhead; otherwise stay direct. Terminal states: `complete`, `failed`, `cancelled`.
</team_pipeline>

<team_model_resolution>
Team/Swarm worker model precedence: explicit `OMX_TEAM_WORKER_LAUNCH_ARGS`, inherited leader `--model`, then low-complexity default from `OMX_DEFAULT_SPARK_MODEL` (legacy alias: `OMX_SPARK_MODEL`). Normalize model flags to one canonical `--model <value>` entry and use `OMX_DEFAULT_FRONTIER_MODEL` / `OMX_DEFAULT_SPARK_MODEL` rather than guessing defaults.
</team_model_resolution>

<!-- OMX:MODELS:START -->
## Model Capability Table

Auto-generated by `omx setup` from the current `config.toml` plus OMX model overrides.

| Role | Model | Reasoning Effort | Use Case |
| --- | --- | --- | --- |
| Frontier (leader) | `gpt-5.6-sol` | high | Primary leader/orchestrator for planning, coordination, and frontier-class reasoning. |
| Spark (explorer/fast) | `gpt-5.6-luna` | low | Fast triage, explore, lightweight synthesis, and low-latency routing. |
| Standard (subagent default) | `gpt-5.6-sol` | high | Default standard-capability model for installable specialists and secondary worker lanes unless a role is explicitly frontier or spark. |
| `explore` | `gpt-5.6-luna` | low | Fast codebase search and file/symbol mapping (fast-lane, fast) |
| `analyst` | `gpt-5.6-sol` | medium | Requirements clarity, acceptance criteria, hidden constraints (frontier-orchestrator, frontier) |
| `planner` | `gpt-5.6-sol` | medium | Task sequencing, execution plans, risk flags (frontier-orchestrator, frontier) |
| `architect` | `gpt-5.6-sol` | xhigh | System design, boundaries, interfaces, long-horizon tradeoffs (frontier-orchestrator, frontier) |
| `debugger` | `gpt-5.6-sol` | high | Root-cause analysis, regression isolation, failure diagnosis (deep-worker, standard) |
| `executor` | `gpt-5.6-sol` | medium | Code implementation, refactoring, feature work (deep-worker, standard) |
| `team-executor` | `gpt-5.6-sol` | medium | Supervised team execution for conservative delivery lanes (deep-worker, frontier) |
| `verifier` | `gpt-5.6-sol` | high | Completion evidence, claim validation, test adequacy (frontier-orchestrator, standard) |
| `code-reviewer` | `gpt-5.6-sol` | high | Comprehensive review across all concerns (frontier-orchestrator, frontier) |
| `dependency-expert` | `gpt-5.6-sol` | high | External SDK/API/package evaluation (frontier-orchestrator, standard) |
| `test-engineer` | `gpt-5.6-sol` | medium | Test strategy, coverage, flaky-test hardening (deep-worker, frontier) |
| `designer` | `gpt-5.6-sol` | high | UX/UI architecture, interaction design (deep-worker, standard) |
| `writer` | `gpt-5.6-sol` | high | Documentation, migration notes, user guidance (fast-lane, standard) |
| `git-master` | `gpt-5.6-sol` | high | Commit strategy, history hygiene, rebasing (deep-worker, standard) |
| `code-simplifier` | `gpt-5.6-sol` | high | Simplifies recently modified code for clarity and consistency without changing behavior (deep-worker, frontier) |
| `researcher` | `gpt-5.6-terra` | high | External documentation and reference research (fast-lane, standard) |
| `prometheus-strict-metis` | `gpt-5.6-sol` | high | Prometheus Strict requirements interviewer and ambiguity mapper (frontier-orchestrator, frontier) |
| `prometheus-strict-momus` | `gpt-5.6-sol` | high | Prometheus Strict adversarial plan critic and risk challenger (frontier-orchestrator, frontier) |
| `prometheus-strict-oracle` | `gpt-5.6-sol` | high | Prometheus Strict implementation readiness verifier and handoff judge (frontier-orchestrator, standard) |
| `critic` | `gpt-5.6-sol` | high | Plan/design critical challenge and review (frontier-orchestrator, frontier) |
| `scholastic` | `gpt-5.6-sol` | high | Ontology-first reasoning reviewer: category mistakes, hidden assumptions, modality separation, scholastic critique, and minimal-repair proposals (frontier-orchestrator, frontier) |
| `vision` | `gpt-5.6-sol` | low | Image/screenshot/diagram analysis (fast-lane, frontier) |
<!-- OMX:MODELS:END -->

<verification>
Verify before claiming completion.
<!-- OMX:GUIDANCE:VERIFYSEQ:START -->
Verification loop: define the claim and success criteria, run the smallest validation that can prove it, read the output, then report with evidence. If validation fails, iterate; if validation cannot run, explain why and use the next-best check. Keep evidence summaries concise but sufficient.

- Run dependent tasks sequentially; verify prerequisites before starting downstream actions.
- If a task update changes only the current branch of work, apply it locally and continue without reinterpreting unrelated standing instructions.
- For coding work, prefer targeted tests for changed behavior, then typecheck/lint/build/smoke checks when applicable; do not claim completion without fresh evidence or an explicit validation gap.
- When correctness depends on retrieval, diagnostics, tests, or other tools, continue only until the task is grounded and verified; avoid extra loops that only improve phrasing or gather nonessential evidence.
<!-- OMX:GUIDANCE:VERIFYSEQ:END -->
</verification>

<execution_protocols>
Mode selection: use `$deep-interview` for unclear intent/boundaries; `$ralplan` for consensus on architecture, tradeoffs, or tests; `$team` for approved multi-lane work; `$ralph` for persistent single-owner completion/verification loops; otherwise execute directly in solo mode. Switch modes only when evidence shows the current lane is mismatched or blocked.

Command routing: use normal Codex repository inspection tools/subagents as the default surface for simple read-only repository lookup tasks; use `omx sparkshell` only for explicit shell-native read-only evidence or bounded verification.
When to use what:
- Use normal Codex repository inspection tools/subagents for repository lookup and implementation context.
- Use `omx sparkshell --tmux-pane` only as an explicit opt-in operator aid for shell-native tmux evidence or bounded verification; it does not replace raw evidence capture.

Supervisor tmux handoff safety:
- Never paste from tmux's implicit/current buffer. Load handoff text into a fresh named buffer with `tmux set-buffer -b <name> -- "$message"` or a temp-file-backed `tmux load-buffer -b <name> <file>`; never use `tmux load-buffer -- <message>`.
- Verify the named buffer with `tmux show-buffer -b <name>` before any paste. A failed load or mismatched buffer is a blocker; do not run `paste-buffer` or submit keys after it.
- Clear the pane composer with `tmux send-keys -t <pane> C-u` immediately before paste, then use bracketed paste (`tmux paste-buffer -t <pane> -b <name> -p -d`) and submit intentionally.
- Recapture the pane after paste/Enter and verify the intended turn was accepted rather than leaving stale draft text visible.

Leader vs worker: leaders choose mode, delegate bounded work, integrate, and own verification; workers execute their slice and escalate blockers, scope expansion, shared-file conflicts, or mode mismatch upward. Escalate from worker to leader for blockers, scope expansion, shared ownership conflicts, or mode mismatch.

Stop / escalate: stop when the task is verified complete, the user says stop/cancel, or no meaningful recovery path remains. Escalate to the user only for irreversible, destructive, materially branching decisions, or missing authority.

Output contract: Default update/final shape: state current mode, action/result, and evidence or blocker/next step. Keep rationale once; do not restate the full plan every turn; expand only for risk, handoff, or explicit request.

Anti-slop workflow:
- Cleanup/refactor/deslop work still follows the same `$deep-interview` -> `$ralplan` -> `$team`/`$ralph` path; use `$ai-slop-cleaner` as a bounded helper inside the chosen execution lane, not as a competing top-level workflow.
- Write a cleanup plan before modifying code; lock existing behavior with regression tests first, then make one smell-focused pass at a time.
- Prefer deletion over addition, and prefer reuse plus boundary repair over new layers.
- No new dependencies without explicit request.
- Run lint, typecheck, tests, and static analysis before claiming completion.
- Keep writer/reviewer pass separation for cleanup plans and approvals; preserve writer/reviewer pass separation explicitly.

Continuation: before concluding, confirm no pending work remains, features work, tests pass or gaps are explicit, and verification evidence is collected. If not, continue.
</execution_protocols>

<cancellation>
Use the `cancel` skill to end active execution modes when work is done and verified, when the user says stop, or when a hard blocker prevents meaningful progress. Do not cancel while recoverable work remains.
</cancellation>

<state_management>
Hooks own normal skill-active and workflow-state persistence under `.omx/state/`. OMX runtime state lives under `.omx/`; do not manually duplicate hook-owned activation state unless recovering from missing or stale state.
</state_management>

## Setup

Execute `omx setup` to install all components. Execute `omx doctor` to verify installation.
<!-- OMX:AGENTS:END -->
