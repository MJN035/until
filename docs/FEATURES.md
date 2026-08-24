# Until 기능 지도 (팀 온보딩용)

> 무엇이 · 어디에 · 어떻게 검증되는지 한 장. 사용법은 `README.md`, 진행 이력은 `CLAUDE.md`.
> 원칙: **경계선**(사람 판단은 `[[DECISION]]`, 대신 확정 금지) · capture/context/boundary/prompts는 **LLM 0** · 모든 기능은 mock으로 오프라인 테스트.

> ### ⛔ 표시에 대하여 — 이 저장소는 **코어**다
>
> Until의 서버·결제·관리자 계층(과금·PG 웹훅·관리자 보드·운영 KV 미러·소셜 로그인)은
> 이 공개 저장소에 **포함되어 있지 않다.** 해당 모듈들은 인터페이스만 유지하는
> **무동작 스텁**으로 들어 있어(`until/billing.py`·`cloudkv.py`·`adminboard.py` 등),
> 코어는 그것들 없이 단독으로 동작한다 — `python demo.py`와 `python -m until <파일>`은
> API 키도 서버도 없이 그대로 돌아간다.
>
> 아래 표에서 **⛔** 가 붙은 행은 완성된 제품에는 있지만 **이 저장소에는 없는** 기능이다.
> 무엇을 만들었는지 보여 주기 위해 목록에는 남겨 두었다. 해당 행의 코드 경로는
> 스텁 파일을 가리키며, 적힌 테스트도 이 저장소에는 없다.

외부 방문 분석은 ⛔ 비공개 계층이다. 완성된 제품에서는 `until/analytics.py`가
fail-closed로 관리해 GA4/Meta ID와 명시적 동의가 모두 있을 때만 공개 소개 화면의
PageView를 전송하며, 과제·세션 URL과 원문·초안·답변은 측정 대상에서 제외한다.
**이 저장소의 `analytics.py`는 빈 값만 돌려주는 스텁이라 어떤 추적도 하지 않는다.**

## 파이프라인 코어

| 기능 | 코드 | 테스트 |
|---|---|---|
| Capture(파싱, 토큰0) | `until/capture/` (ingest, sources/*) | test_pipeline, test_etl_source, test_canvas_api, test_learningx_parse, test_moodle_parse |
| 첨부 내장 폴백(docx/pptx/html/hwpx — docling 없이, PDF는 PyMuPDF, 이진 포맷은 경고) | `until/capture/ingest.py:_read_basic` | test_pipeline |
| Canvas 확장자 없는 첨부명 복원(`Content-Disposition`/`Content-Type`→안전 파일명) | `until/capture/sources/canvas_api.py:_download_name` | test_canvas_api |
| Understanding(TaskSpec) | `until/understanding/task_spec.py` | test_pipeline |
| 과제 유형 분류(결정적) | `until/understanding/task_type.py` | test_task_type (+examples/sample_* 5유형 e2e) |
| Context(수업자료·내파일·말투) | `until/context/{retrieval,voice,bundle}.py` | test_context |
| 과제별 톤 레지스터(ToneSpec 상속+델타, 프리셋 8종, 결정적 직렬화 · 플래그 `UNTIL_TONE_REGISTER=1`) | `until/context/tone.py` | test_tone |
| 톤 A/B 회귀(골든 19건 off/on side-by-side 리포트) | `run_tone_ab.py`, `until/evals/{tone_cases,tone_metrics}.py` | test_tone |
| L1 스타일 카드(ToneSpec 필드로만 저장 — 자유 서술 금지, LLM은 schema 강제·선택) | `until/context/style_card.py` | test_memory_layers |
| L2 에피소드 기억(입력·초안·최종본 3종 + 유사 사례 검색 few-shot · 플래그 `UNTIL_CONTEXT_DEPTH=1`) | `until/context/episodes.py` | test_memory_layers |
| L3 사실 기억(문체와 분리 저장·별도 섹션 주입·만료 제외) | `until/context/facts.py` | test_memory_layers |
| 수정 diff 캡처(edit_source 구분·기본 켜짐 `UNTIL_EDIT_CAPTURE=0`으로 해제) | `until/context/edit_events.py`, `until/{pipeline,web}.py` | test_edit_capture |
| 반복 수정 패턴 → 스타일 카드 배치(제안만, `confirm=True` 필요 · 스케줄링 TODO) | `until/context/edit_patterns.py` | test_edit_capture |
| 생성 품질 안전장치(최근 생성물 n-gram 중복·금지 표현 사후 검증) | `until/execution/quality_guards.py` | test_quality_guards |
| 민감·고위험 상황(사과·거절·갈등) 승인 대기 — 생성은 허용, 자동 제출만 차단 | `until/execution/sensitive.py`, `until/execution/submission_gate.py` | test_quality_guards |
| 채널 중립 페르소나 이벤트(actor 귀속·채널은 태그·raw_payload 원본 보관) | `until/persona/events.py` | test_persona_portability |
| 출처 기록 prompt_version/model_version(실제 응답 모델·폴백 표기, 텔레메트리엔 지문만) | `until/persona/versions.py`, `until/llm/{base,meter}.py`, `until/telemetry/` | test_persona_portability, test_telemetry_web |
| 페르소나 export/import(신상 분리·원문 기본 제외·이질 파일 거부) | `until/persona/portability.py` | test_persona_portability |
| 보관 기간 정책 + 사용자별 전체 삭제(`POST /data/delete`, KV 미러 포함) | `until/persona/retention.py`, `until/web.py` | test_persona_portability |
| 말투 명시 지정 UI(`/profile` 패널 · `POST /profile/tone` · 해제 가능, 플래그 off면 미표시) | `until/web.py:_render_tone_panel` | test_cloud |
| 내 데이터 패널(저장 목록·보관 정책 표시 · 내보내기 · 확인 문구 삭제) | `until/web.py:_render_data_panel`, `GET /data/export.json` | test_cloud |
| 프롬프트 버전 규율 게이트(고치고 버전 안 올리면 실패, 표면 17개 감시) | `tools/check_prompt_version.py`, `tools/prompt_baseline.json`, `until/persona/versions.py:prompt_surface_fingerprints` | test_persona_portability |
| eTL 자동수집 자료 | `until/context/etl_materials.py` | test_materials |
| eTL 관련 공지(4번, 숨은 명세) | `until/context/etl_announcements.py` | test_announcements |
| 주차별 질의 자동 배정(프로필 학번→공지 Sheets→담당 교수·공식 연구분야·실제 마감) | `until/context/inquiry_assignment.py` | test_inquiry_assignment |
| 발표 변환형 선행 과제 연결(주제·개요→원고, 미래·조별 제외) | `until/context/presentation_conversion.py` | test_presentation_conversion |
| 분산 명세 연결(강의자료 PDF 전체·모듈·코딩 게시판→과제 번호/주차 매칭) | `until/context/distributed_spec.py`, `capture/sources/canvas_api.py:list_discussion_topics` | test_distributed_spec |
| 과제 전수 처리 라우터(109개 요청 범위보다 큰 148개 감사, 미커버 0) | `until/context/assignment_router.py`, `run_corpus_coverage.py` | test_assignment_router |
| **Local Agent Runtime Phase 0–1**(공식 CLI 계약·1회성 승인·격리 workspace·secret env 제거·변경범위 검증·1회 repair, 실제 CLI adapter는 후속) | `until/runtime/`, `tests/runtime_fixtures/` | test_runtime_kernel |
| **Local Agent Runtime Phase 2**(공식 CLI 어댑터: 설정 기반·auto-approve 금지·미로그인/한도/timeout/취소 구분 + 격리 경계, 샌드박스 미설정이면 실행 거부) | `until/runtime/cli_agent.py`, `until/runtime/boundary.py` | test_runtime_phase2 |
| **Local Agent Runtime Phase 3**(ReportRuntime: 작업공간·명세·프롬프트 준비, 결정적 검증 5종, 1회 자동 수정) | `until/runtime/report_runtime.py` | test_runtime_phase2 |
| **Local Agent Runtime Phase 4**(Submission Bridge: 파일명·확장자·MIME·개수 검사, content hash로 nonce 결합, 검증 후 변조 탐지, block 시 발급 0) | `until/runtime/submission_bridge.py` | test_runtime_phase2 |
| **Local Agent Runtime 진입점**(`python -m until.runtime`: 수집→AI금지확인→명세·라우팅·정책→작업공간·계획→사람 승인→실행→검증→번들. 명세는 LLM 0으로 조립) | `until/runtime/cli.py`, `until/runtime/spec_builder.py` | test_runtime_cli |
| **MCP 서버**(`python -m until.mcp_server`, stdio JSON-RPC. 읽기 전용 6도구 — inbox·assignment·materials·route·readiness·series. 생성 도구 없음·LLM 0·의존성 0·토큰 미저장) | `until/mcp_server.py` | test_mcp_server |
| **런타임 eTL 입력**(`--fast`로 마감 임박 미제출 과제 자동 선택, 본문·첨부·관련 자료 수집, 통과 시 제출 페이지 링크. 선택 정책은 웹 딸깍과 동일) | `until/runtime/etl_input.py` | test_runtime_etl |
| **런타임 제출본 마무리**(검증된 초안의 `[[DECISION]]`을 답변으로 치환하거나 `【직접 정할 것 N】` 자리표시로 — 원문 마커가 제출 파일에 남지 않는다. 치환 뒤 요건 재검사) | `until/runtime/finish.py`, `until/report.py:resolve_decision_markers` | test_runtime_cli |
| **샌드박스 자체 검증**(`--verify-sandbox`: 네트워크·작업공간 밖 쓰기를 실제로 시도해 막히는지 확인, 네트워크는 대조군 대비. 증명 안 된 격리 신고를 잡아냄) | `until/runtime/sandbox_check.py` | test_runtime_cli |
| **과제 유형별 런타임**(코드: 파일·문법·스켈레톤 보존·지어낸 실행 결과 차단 / 발표: 슬라이드 구조 / 활동 양식: 근거 없는 사실 차단. hdl_lab·rmd_notebook은 실행 엔진이 없어 의도적 미지원) | `until/runtime/{code,presentation,form}_runtime.py`, `until/runtime/grounding.py` | test_runtime_plugins |
| **WSL2 샌드박스 래퍼**(`unshare`만으로 작업공간 외 쓰기·네트워크 차단, 추가 설치 불필요. `--allow-write`로 CLI 설정 경로만 좁게 개방. `--verify-sandbox` 3항목 통과 확인) | `tools/until-sandbox.sh` | (수동 검증) |
| **검증 명령 실행 엔진**(플러그인이 사전 선언한 명령을 에이전트와 같은 격리에서 실행. 커널 천장으로 재검열, 실패는 차단·못 돌림은 경고) | `until/runtime/local_agent.py:run_steps`, `until/runtime/boundary.py:run_step`, `until/runtime/security.py:KERNEL_ALLOWED_COMMANDS` | test_runtime_plugins |
| **과제 유형별 제출 점검(웹)**(코드 블록 문법·지어낸 실행 결과·발표 슬라이드 구조·활동 기록 사실 — 런타임 플러그인과 **같은 판정기**를 쓰되 모델 호출 0. 웹앱 유저도 받는다) | `until/readiness.py:_type_specific_items` | test_readiness_types |
| **코드 실행 러너**(별도 서비스: 격리 자체검증 통과해야 실행, 요청은 argv를 정하지 못함, HMAC 서명·크기·타임아웃·출력 상한. 웹은 HTTP로 부르기만) | `until/runner/`, `deploy/Dockerfile.runner`, `RUNNER_SETUP.md`(비공개 저장소) | test_runner |
| **러너 로컬 실행 스크립트**(`up`/`down`/`status` 한 명령. 격리망 러너 + 포트만 잇는 릴레이 2컨테이너, 키 자동 생성·재사용) | `tools/runner-local.sh` | (수동 검증) |
| 코퍼스 실제 검증(legacy+unit·guard·readiness·정답셋 형식, 비식별 JSONL 원장) | `run_corpus_validation.py` | test_corpus_validation |
| Rmd 답안 슬롯·ZIP 프로젝트 안전 수집(실행 없음) | `until/capture/ingest.py`, `until/context/structured_assignment.py` | test_structured_assignment |
| Execution(경계선 초안) | `until/execution/{drafter,prompts}.py` | test_pipeline |
| BoundaryGuard(validate→reask) | `until/execution/boundary_guard.py` | test_pipeline, test_resolve |
| 결정 모델·해소(HITL) | `until/boundary/{models,resolve}.py` | test_resolve |
| finalize(결정 반영 2차 패스) | `until/execution/drafter.py:finalize_with_decisions` | test_resolve |
| 과제 자동 탐색(inbox) | `until/capture/sources/discovery.py` | test_discovery |
| Elice 코딩 과제 opt-in 병합·단건 수집(GET allowlist, 실패 격리) | `until/capture/sources/elice_api.py`, `until/web.py` | test_elice_api, test_elice_inbox |

Elice 인박스 병합은 `UNTIL_ELICE=1`, `UNTIL_ELICE_TOKEN`, 그리고 접근할 과목 ID를
쉼표로 적은 `UNTIL_ELICE_COURSE_IDS`를 설정해야 한다. Elice에는 이 어댑터가 안전하게
사용할 수 있는 전체 과목 조회 API가 없어 과목 ID를 명시적으로 제한한다. 빠진 경우 인박스에
안내를 표시하며 eTL 결과는 그대로 유지한다. `elice:<과제 URL>` CLI 단건 수집은 과목 ID가
필요 없다.
| SSO 토큰리스 어댑터 | `until/capture/sources/playwright_discovery.py` | test_discovery(mock) |

## eTL = Moodle WS (읽기 전용, 협상 대상 아님)

> eTL은 Moodle. 학생 토큰으로 Moodle Web Services를 **읽기 전용**으로 호출한다.
> 상세는 `docs/ETL_READ_ONLY.md`(0번)·`docs/ETL_EXPANSION_PROPOSAL.md`(2·3·4·6).

| 기능 | 코드 | 테스트 |
|---|---|---|
| 읽기 전용 강제(allowlist·쓰기함수 차단) | `until/capture/sources/moodle_ws.py:assert_read_only` | test_moodle_ws |
| WS 클라이언트(토큰 POST 바디) | `until/capture/sources/moodle_ws.py:MoodleWsClient` | test_moodle_ws |
| 어댑터(과목·과제·자료·다운로드) | `until/capture/sources/moodle_ws.py:MoodleWsAdapter` | test_moodle_ws |
| 강의자료 다운로드(fileurl+토큰) | `until/capture/sources/moodle_ws.py:with_token` | test_moodle_ws |
| 함수 지형 조사 CLI | `until/capture/sources/moodle_ws.py:print_site_inventory` | test_moodle_ws |
| 자료 자동 다운로드 정책(용량 상한) | `until/context/etl_materials.py:fetch_material_texts` | test_materials |
| 관련 공지 수집·주입(숨은 명세) | `until/context/etl_announcements.py:collect_related_announcements` | test_announcements |
| 웹 WS 모드 배선(--ws) | `until/web.py:collect_with_materials` | test_web |
| 결정=추출 실패 지표 계측 | `until/feedback.py:summarize` | test_feedback |

## 제출 직전 점검 (전부 결정적·LLM 0)

| 기능 | 코드 | 테스트 |
|---|---|---|
| 분량 요건 감지·판정 | `until/understanding/length_target.py` | test_length |
| 마감 D-day(절대·상대·연장·시각, 문맥 게이트) | `until/understanding/deadline.py` | test_deadline |
| 인용 커버리지 | `until/context/citation_coverage.py` | test_citation |
| **통합 준비 점검** (자료·마감·분량·인용·결정, 유형별 조정) | `until/readiness.py` | test_readiness |
| 요구사항→근거→초안 추적(`반영됨/부분 반영/미반영/내 판단 필요`, LLM 0) | `until/requirement_trace.py`, `until/web.py` | test_requirements, test_web |
| 요구사항 상태→초안·근거 앵커 이동 | `until/web.py:_requirement_trace_html` | test_web |
| 파싱 실패 첨부 경고(capture_warnings) | `until/capture/ingest.py:ingest_all_with_warnings` | test_pipeline, test_readiness |
| 결정 근거(왜 당신 몫인지) | `until/boundary/rationale.py` | test_rationale |
| 초안→최종 변경 투명화 | `until/diffview.py` | test_diffview |
| 결정 답변 히스토리(지난 답 재제안·3중 게이트·문체 힌트) | `until/context/answer_history.py` | test_answer_history |
| finalize 성격별 반영 지침(카테고리 태깅) | `until/boundary/resolve.py:render_resolved_block` | test_resolve |

준비 점검 소비 표면: CLI 블록 · 웹 패널 · 제출용 문서 · 진단 리포트 상단 · self-review 근거 주입 ·
JSON(CLI `--readiness-json`, 웹 `GET /readiness/<token>.json`).

## LLM 보조 (Execution 레이어, mock 지원)

| 기능 | 코드 | 테스트 |
|---|---|---|
| 결정 AI 제안(+카테고리별 톤) | `until/execution/suggest_answers.py` | test_suggest |
| 완성도 점검(self-review, readiness 근거) | `until/execution/review.py` | test_review |
| 프롬프트 교육 모드 | `until/prompts/suggest.py` | test_suggest |
| GEPA 최적화 | `until/optimize/` | (라이브 전용, 소규모) |
| 429 자동 폴백(`UNTIL_MODEL_FALLBACK`, Groq 기본 8b) | `until/llm/openai_compat.py:fallback_model` | test_integrations |
| 모델 티어링(제안·점검 경량 모델, `UNTIL_MODEL_LIGHT`) + 보조 패스 자료 절단 | `until/pipeline.py:_light_model/_trimmed_source_docs` | test_citation |

## 산출물·표면

| 기능 | 코드 | 테스트 |
|---|---|---|
| 발표 초안 → 실제 16:9 `.pptx` 다운로드(결정 표식 보존) | `until/presentation_export.py`, `until/web.py` | test_presentation_conversion |
| Jinja2 페이지 셸·정적 CSS/JS(기존 서버 호환) | `until/web_templates.py`, `until/templates/base.html`, `until/webassets/` | test_web |
| FastAPI 운영 표면(HTML 폼·JSON API·HTMX·사용자 격리·과금·eTL·readiness) | `until/asgi.py` | test_asgi |
| ⛔ PG 중립 HMAC 웹훅(timestamp 서명·pending 주문 바인딩·WAL 멱등 정산·KV 미러) | `until/asgi.py:/billing/webhook`, `until/pg_webhook.py`, `until/billing.py:add_credits_checked` | test_billing_webhook |
| 과거 과제 연습 감사(수집 정확도·정책/자료 하드 중단·제출 격리·전후 비교) | `until/practice_audit.py`, `tools/audit_assignments.py`, `until/web.py` | test_practice_audit |
| ⛔ 관리자 보드(실패 포함 퍼널·내부 제외·결정 응답률·전체 텔레메트리 집계·HMAC 쿠키 인증) | `until/adminboard.py`, `until/{web,asgi}.py` | test_adminboard |
| 웹 과제 생애주기 텔레메트리(기본 off·원문 누출 차단·uid별 bounded JSONL) | `until/telemetry/{schema,web}.py`, `until/{web,asgi}.py` | test_telemetry, test_telemetry_web |
| 텔레메트리 opt-in 동의(1회 고지·동등 버튼·/consent 철회·KV 미러·재시작 재고지 방지) | `until/telemetry/consent.py`, `until/{web,asgi}.py` | test_telemetry_web, test_cloud, test_asgi |
| ⛔ 텔레메트리 KV 미러·관리자 보드 병합(telem:<uid>·run_id dedup·하이드레이션 복원) | `until/telemetry/web.py`, `until/cloudkv.py`, `until/adminboard.py:load_web_telemetry` | test_telemetry_web, test_adminboard |
| LLM 사용량 계측(호출·입출력 토큰 → Result.llm_usage → 텔레메트리 원가 충전) | `until/llm/meter.py`, `until/pipeline.py`, `until/session_store.py` | test_pipeline, test_session_store, test_telemetry_web |
| 최소 `.env` 로더(기존 환경 우선, 표준 라이브러리) | `until/config.py:load_dotenv` | test_adminboard |

| 기능 | 코드 | 테스트 |
|---|---|---|
| 진단 리포트(.md) | `until/report.py:render_markdown_report` | test_length, test_deadline, test_rationale |
| **🤖 프롬프트로 복사**(채팅 LLM용 자기완결 번들: 명세 폴백·자료 발췌·경계선 규칙 수출) | `until/promptpack.py`, 웹 `_prompt_button` | test_web |
| eTL 관련자료 본문 수집(상위 2건 발췌 3,000자·위치 URL·일반어 배제·라벨 dedup) | `until/context/etl_materials.py:fetch_material_texts` | test_materials |
| **제출용 내보내기**(.md/.html/.docx — 의존성 0 OOXML, 체크리스트+인쇄 버튼) | `until/report.py:render_submission_*` | test_submission |
| 웹 UI(의존성 0, 세션 지속화·/sessions 목록/검색/삭제·경고 배지·진행률·다운로드) | `until/web.py` | test_web, test_submission, test_readiness |
| **간단 모드**(원-액션: 붙여넣기→초안→답→완성본, `/simple`·`/sv/<t>`·`/svf/<t>`, `ui=simple` 전달) | `until/web.py:render_simple_*` | test_web |
| **먼저 질문 4개**(간단 모드 결정 폼은 앞 4개만 펴고 나머지는 같은 폼 안에 접기) | `until/web.py:SIMPLE_FIRST_N`, `_simple_decisions_form` | test_google_auth |
| **eTL 연결 단계**(홈은 클릭만 받고 토큰은 다음 화면에서 — `/connect?mode=fast\|list\|practice`) | `until/web.py:render_connect`, `until/asgi.py` | test_web, test_token_onboarding, test_google_auth |
| 내 자료 업로드(→`[내 자료]` 근거 주입, multipart 의존성 0, 실패는 경고) | `until/web.py:_sources_from_uploads` | test_web |
| 내가 쓴 글 업로드(→문체 프로파일 적용, docx/hwpx 변환) | `until/web.py:_voice_dir_from_uploads` | test_web |
| **⚡ 바로 초안**(인박스에서 최우선 과제 자동 선택: 미제출>기한 안 지남>임박) | `until/web.py` (`/quick`, 홈 버튼) | test_web |
| 결정 한 문항씩 해결(첫 결정 빠른 폼→최종본에서 남은 결정 순차 반영, 전체 입력도 유지) | `until/web.py` | test_web, test_answer_history |
| 자료별 선택 이유·제외→영향 문단 재작성(BoundaryGuard 재검증) | `until/execution/revise.py`, `until/web.py:/revise` | test_review, test_asgi |
| 문단별 자연어 수정·최근 5버전 복원(세션/KV 지속) | `until/execution/revise.py`, `until/session_store.py` | test_review, test_session_store, test_asgi |
| 제출 파일 우선 추천(원본 양식→PPTX/Word)·직접 제출 경계 | `until/web.py:_submission_status_html` | test_web, test_submission |
| 과거 교수 코멘트·루브릭→현재 프롬프트·점검 규칙 표시 | `until/context/teacher_feedback.py`, `until/web.py:_teacher_rules_html` | test_teacher_feedback |
| 인박스 기한 필터(기한 지난 과제 숨기기 체크) | `until/web.py` (`hide_past`) | test_web |
| ⛔ 플랜·사용량 스캐폴드(무료 일일 한도 게이트, mock은 무제한) | `until/billing.py`, 웹 `/plan` | test_billing |
| CLI(--submission/--suggest/--resolve/--readiness-json) | `until/cli.py` | test_suggest(CLI 왕복) |
| 제출 게이트(C안: 하드블록·4겹 무장 확인 nonce, dry-run 기본·자동 제출 없음) | `until/execution/submission_gate.py`, `until/execution/submit_nonce.py`, `until/capture/sources/canvas_submit.py` | test_submission_gate |
| 피드백 로그(+준비경고)·요약 | `until/feedback.py` (`python -m until.feedback`) | test_feedback |
| **Until Cloud**(멀티유저: uid 쿠키 격리·소유자 검사·베타 게이트·보안 헤더·/healthz·전역 상한) | `until/web.py` (`--cloud`, `_begin_request`) | test_cloud |
| ⛔ **Kakao·Google 로그인**(OAuth2 code+PKCE, 의존성 0 — provider+계정 id로 uid를 고정해 기기·브라우저가 바뀌어도 과제 유지) | `until/kakao_auth.py`, `until/google_auth.py`, `until/web.py`(`/login`·`/auth/kakao/*`·`/auth/google/*`·`/logout`), `until/asgi.py` | test_kakao_auth, test_google_auth |
| 로그인 시 익명 작업 인수인계(세션·프로필·KV 키 재기록, 덮어쓰기 금지) | `until/web.py:_adopt_anon_data` | test_kakao_auth, test_google_auth |
| ⛔ 로그인 강제(선택 `UNTIL_REQUIRE_LOGIN=1`)·학교 도메인 제한(`UNTIL_GOOGLE_ALLOWED_DOMAIN`) | `until/google_auth.py:require_login,allowed_domain` | test_google_auth |
| ⛔ KV 백킹(재시작 생존: 세션·히스토리·사용량 미러+하이드레이션, 의존성 0) | `until/cloudkv.py`, `web.py:_hydrate_user/_mirror_user` | test_cloud |
| 배포 패키징(Cloudflare Containers+정적 랜딩, Docker 스모크) | `deploy/` (`DEPLOY.md`) | — (docker 스모크) |

## 개발 루틴

```bash
PYTHONIOENCODING=utf-8 python run_tests.py      # 89스위트 병렬(~15s). -j 1 = 순차
python -m until.cli examples/sample_assignment.txt --backend mock   # 데모
python -m until.web                              # 웹(mock)
```

작은 변경 → 러너 통과 → 한국어 커밋. 비밀키는 env로만(커밋 금지).
