# HISTORY — Until 개발 이력 아카이브

> 2026-08-14에 `CLAUDE.md`에서 분리했다. **에이전트가 매 세션 읽을 필요 없는 완료 이력**만 여기 있다.
> `CLAUDE.md`는 지금 지켜야 할 규칙과 현재 트랙만 담는다 — 이 파일은 "왜 이렇게 됐는지"를
> 되짚을 때만 열어라. 릴리스 단위 상세는 `CHANGELOG.md`, 기능→코드 지도는 `docs/FEATURES.md`.
>
> 시간순 아카이브(2026-06-25 ~ 2026-07-11, P0 ~ v1.7.1).

---

## ✅ 지금까지 한 것 (이어받는 맥락)
- 코어 스캐폴드(Capture→…→Boundary) + BoundaryGuard(validate→reask, guardrails 패턴) 완성.
- 최신 OSS 통합: Docling(ETL), Anthropic Citations/캐싱/Structured Outputs, DSPy+GEPA(목적함수=BoundaryGuard).
- 무료 백엔드: `mock`(키X), `local`(Ollama/Groq/Gemini, OpenAI호환). 권장=Groq 무료.
- eTL 현재 제품 경로는 **Moodle Web Services 읽기 전용 어댑터**(`capture/sources/moodle_ws.py`, 웹 `--ws`).
  이전 Canvas/LearningX 환경용 어댑터와 fixture도 호환·회귀 검증 경로로 보존한다
  (`canvas_api.py`, `learningx_adapter.py`, `examples/canvas_fixture/`). 상세는 `docs/FEATURES.md`와
  `docs/ETL_READ_ONLY.md`를 따른다.
- Context/Personalization 레이어: 수업자료·내파일 검색 + 말투 프로파일 → Execution에 주입.
- 베타 기능(Codex 작업): 한국어 가드, 임베딩 검색(옵션), Markdown 리포트(`--out`), 결정 반영(`--resolve`), 말투 LLM 보강(`--voice-llm`).
- 테스트 6스위트 전부 통과(오프라인). `git log`에 P0~P4 커밋.

## ✅ P5 — 라이브 1회 검증 (완료, 2026-06-25)
실제 eTL(`courses/302199/assignments/369118`)에 Playwright로 접속→SSO 로그인→과제
`<나만의 시선으로 읽는 도시>` 추출→Groq(`llama-3.3-70b-versatile`)로 초안→`report.md` 생성 **성공**.
- **P0 한국어 가드가 라이브에서 실증됨:** Attempt 1에 한자 `観` 혼입 → `BoundaryValidator`가 검출·reask →
  Attempt 2 순수 한국어로 복구. BoundaryGuard 통과 / 결정지점 3개 / reask 1회.
- 경계선 원칙 준수: 도시·진로 선택 등 사람 고유판단 3곳을 `[[DECISION]]`로 보존(직접 확정 안 함).
- 실행 메모(다음 세션용):
  - Windows 콘솔(cp949)에서 테스트/러너 출력의 em-dash가 `UnicodeEncodeError` → `PYTHONIOENCODING=utf-8`로 해결(로직 정상).
  - `report.md`·`_until_work/`·Groq 키는 `.gitignore`로 커밋 제외(개인정보/비밀키).
  - 러너: `run_etl_live.py`. 의존: `pip install playwright openai ; python -m playwright install chromium`.
  - 환경변수: `UNTIL_BASE_URL=https://api.groq.com/openai/v1` / `UNTIL_API_KEY=<groq>` / `UNTIL_MODEL=llama-3.3-70b-versatile`.

## ✅ P6~P8 + Canvas API (완료, 2026-06-25)
- **P6 결정 루프 닫기:** `--resolve`가 마커 치환만 하던 것을, 답변 수령 후 Execution **2차 패스(finalize)**로
  사람 결정을 본인 말투로 녹인 **최종 완성본** 생성(`until/execution/drafter.py:finalize_with_decisions`,
  `until/pipeline.py:finalize`). 가드 완화: `min_decisions=0`+`forbid_stance=False`, 한자/가나·본문길이 가드는 유지.
  CLI `--resolve-mode final`(기본)/`splice`(구 치환). 미답 결정 마커는 보존.
- **P7 베타 피드백 로그:** 실행마다 (과제·결정수·reask·통과·만족도) JSONL 적립(`until/feedback.py`).
  CLI `--feedback [경로] --satisfaction 1~5`. `trainset.build_trainset_with_feedback()`로 GEPA 학습셋에 병합(라벨 불필요).
- **P8 최소 UI:** `until/web.py` — 표준 라이브러리 `http.server`만(의존성 0). 과제 붙여넣기 → 초안+결정 체크리스트
  → 결정 반영 최종본. 렌더는 순수 함수로 분리(테스트됨). 실행 `python -m until.web`(기본 127.0.0.1:8000).
- **Canvas REST API 어댑터:** `capture/sources/canvas_api.py` — 학생 액세스 토큰으로 `/api/v1/.../assignments/{aid}`.
  `urllib`만 사용, `BrowserAdapter` 호환 → `EtlSource` 재사용. CLI `--source canvas-api:<URL>`(`UNTIL_CANVAS_TOKEN`).
- **테스트 10스위트 전부 통과(오프라인).** 신규: test_resolve, test_feedback, test_web, test_canvas_api.

## ✅ 라이브 검증 2회차 (2026-06-25)
- **P6 finalize 라이브(Groq):** 저장된 초안 → 사람 결정 반영 → 최종본. 답한 결정은 1인칭으로 녹고
  미답은 마커 보존. 러너 `run_finalize_live.py`(draft 저장 → finalize, 재생성 금지로 비결정성 회피).
- **외국문자 가드 강화:** finalize 잘림 시 데바나가리/베트남어 누수 발견 → `_HANJA_KANA_RE`를
  데바나가리·키릴·히브리·아랍·태국·라틴확장추가까지 확장(메시지도 '외국 문자'로 일반화).
- **P8 UI 라이브(Groq):** `python -m until.web --backend local`로 입력→초안(~16s)→최종본(~37s) 확인.
- **GEPA 1회(Groq):** `UNTIL_GEPA_MODEL=groq/...` + `UNTIL_GEPA_BUDGET=18`로 실행. BoundaryGuard가
  목적함수로 동작, reflection LM이 외국문자 피드백을 학습해 프롬프트에 한국어전용 규칙 추가 시도.
  (소규모 예산이라 베이스 유지; 산출물 gitignore.) 전체 11스위트→실제론 10스위트 유지.

## ✅ 백로그 정리 3회차 (2026-06-25)
- **finalize 잘림 방지:** `OpenAICompatClient.max_tokens`를 `UNTIL_MAX_TOKENS`로 상향 가능(기본 2048).
- **Canvas 파일 탭 첨부 병합:** `parse_canvas_files()` + `CanvasApiAdapter(include_course_files=True)`,
  파일 id로 중복 제거. CLI 경로 `UNTIL_CANVAS_FILES=1`.
- **P8 UI 맥락 주입:** `until.web --course-materials/--my-files/--voice`로 개인화 + 초안/최종 페이지에 제안 프롬프트 노출.
- **통합 테스트 러너:** `python run_tests.py`(10스위트 일괄, 인코딩 자동, 종료코드 보고).
- README에 P6~P8·Canvas·환경변수·테스트 문서화.

## ✅ 백로그 마무리 4회차 (2026-06-25)
- **P8 UI 라이브 수집:** `/collect` 핸들러 + 입력폼 — Canvas REST API(토큰)로 과제를 가져와 초안 생성.
  (브라우저 SSO eTL은 비대화형 요청 불가 → `run_etl_live.py` 유지.) `web.collect_canvas()`.
- **finalize 미답 마커 보존(2중):** ① 프롬프트에 '반드시 유지할 마커' 블록 주입 + ② `_restore_missing_markers`로 결정적 복원.
- **GEPA 측정/견고화:** `num_threads`(기본 1, `UNTIL_GEPA_THREADS`)로 TPM 보호, 최적화 후 베이스 대비 검증셋 점수 Δ 출력.
- **ingest 견고화:** `ingest_all`이 파싱 실패 파일(PDF 라이브러리 미설치 등) 스킵, 전부 실패 시만 예외.
- 테스트 10스위트 전부 통과(러너: `python run_tests.py`).

## ✅ P9 — eTL 과제 자동 탐색 (완료, 2026-06-25, 라이브 검증)
"과제 URL 수동입력"을 "내 과목·과제 자동 목록"으로 확장하는 새 트랙(설계: `docs/ETL_AUTO_COLLECT.md`)의 첫 단계.
- `canvas_api.parse_courses/parse_assignments` + `CanvasApiAdapter.list_courses/list_assignments`,
  `capture/sources/discovery.py:EtlInbox`(마감순 정렬·미제출 필터·과목 실패 스킵), 모델 `CourseRef/AssignmentRef`.
- **라이브 검증:** 실제 eTL에서 과목 21개·과제 목록(제출여부 포함) 정상 수집.
- 다음: **P10 관련자료 자동수집+순위화(retrieval 재사용) → P11 웹 흐름(목록→선택→수집→초안) → P12 SSO 폴백.**

## ✅ P10~P12 + 디자인 (완료, 2026-06-25, 라이브 검증)
- **P10 관련자료 자동수집:** `context/etl_materials.py`(코스 파일+모듈 → 과제 키워드로 순위화 → SourceDoc).
  `pipeline.run(extra_context_sources=...)`로 주입. 라이브: 과목 자료 20건 수집·순위화.
- **P11 웹 흐름:** `web.py` `/inbox`(EtlInbox 병렬 조회 ~3초) → `/pick`(collect_with_materials→run). 라이브 확인.
- **P12 SSO 폴백:** `playwright_discovery.py`(로그인 쿠키로 토큰리스 API), 러너 `run_etl_inbox.py`.
- **ingest 견고화:** 첨부 파싱 실패(PDF 라이브러리 미설치) 스킵.
- **디자인:** 스위스 미니멀/모노(흑백·직각·헤어라인·강조색 #ff4d12). `web.py`의 `_PAGE`/`render_*`.
  렌더는 `.replace("__BODY__"/"__BACKEND__")` 센티넬(CSS 중괄호 충돌 회피).
- **테스트 12스위트.** 신규: test_discovery, test_materials. run_tests.py에 등록.
- **운영 메모:** Groq 무료 한도 — 70b는 **하루 토큰(TPD) 100k**라 쉽게 소진됨. 막히면
  `UNTIL_MODEL=llama-3.1-8b-instant`(하루 한도 큼)로 전환. 웹 라이브는 토큰+키 env로 띄움.

## ✅ 품질·SSO·견고화 5회차 (2026-06-26)
- **품질 점검 패스(다중 에이전트 리뷰→수정):**
  - finalize 미답 결정 마커 **이중 보존** — 가드 하한 `min_decisions=len(미답)`으로 reask 강제 +
    `_restore_missing_markers` 정규화 비교(공백/대소문자 리워딩 중복·누락 방지).
  - 첨부 파일명 **디렉터리 탈출/Windows 금지문자 방어**(`models.safe_filename`) — 모든 download 경로.
  - canvas description 첨부 **파일 id 기준 중복 제거**(미리보기+download 합침).
  - `etl_materials` 키워드 매칭을 **부분문자열**로(붙여쓰는 한국어 파일명 '도시문화론'에 '도시' 매칭).
  - canvas_api/playwright_discovery: 비-JSON(로그인 HTML)·401/403 → **사람이 읽는 에러**.
  - playwright_discovery 토큰리스 어댑터에 **rel=next 페이지네이션**(100개 초과 누락 방지).
  - 웹: `_read_form` 방어(잘못된 Content-Length/비-UTF8), `do_GET` 렌더 예외 → 500 페이지(스레드 보호),
    `_wrap` 센티넬 순서, `Optional` import.
- **B — 브라우저 SSO eTL을 웹 UI에 연결(백로그 완료):** `python -m until.web --sso`.
  토큰 없이 로그인된 브라우저 세션으로 `/inbox`→`/pick`. Playwright sync 스레드 고정 때문에 **단일 스레드
  HTTPServer** + 공유 어댑터(`_sso_adapter`). `EtlInbox`는 `max_workers<=1`이면 **순차** 조회(신규).
  홈은 SSO 모드에서 토큰 입력칸 없는 INBOX·SSO 섹션 + 로딩 오버레이에 로그인 안내 메시지.
- **C — 엣지 테스트 견고화:** 비-JSON/401/safe_filename/첨부 dedup/한국어 부분문자열/SSO 인박스 플로우.
- 테스트 **12스위트 통과**. 신규 테스트는 기존 파일에 흡수(스위트 수 유지).

## ✅ SSO 라이브 검증 + 토글 UI + 결정 AI 제안 (2026-06-26)
- **SSO 라이브 성공:** 실제 MySNU 로그인 → eTL 과제 목록 → 초안까지 `--sso` 웹으로 검증.
  과정에서 잡은 버그: sync_playwright 엔진 스레드당 1회만 시작(재연결은 컨텍스트만 재오픈),
  창 닫힘 자동 재연결(_raw_get), 목록 대신 에러객체(dict) 표면화, parse_* 비-dict 방어.
- **초안 페이지 토글화:** '경계선 초안/당신이 정할 것만/막히면 이렇게' 3섹션을 `<details>`로
  접고 펼치게(기본 접힘, JS 0). 전용 `.tgsec` 스타일.
- **결정 AI 제안 + '모두 수락'(경계선 철학 유지):** 각 [[DECISION]]에 AI가 추천 답+근거 제시 →
  사람이 수락/수정(대신 확정 안 함). `execution/suggest_answers.py`(LLM 1회·JSON),
  `pipeline.suggest_decision_answers`, 웹 `POST /suggest`(PRG, 칸 프리필+근거+'전부 수락' 버튼).
  mock `_suggest`로 오프라인 데모/테스트. **13스위트**(신규 test_suggest).
- ⚠️ 테스트 함정 메모: `"...]]\n" "본론."*30` 처럼 인접 문자열 리터럴은 `*`가 `+`보다 우선이라
  암묵 연결 후 반복됨(마커 30배 복제). 멀티라인 본문 조립 시 `+` 명시할 것.
- **완성도 점검(Self-Review):** 초안을 AI가 스스로 점검(자료활용·빈 곳·결정 적정성).
  `execution/review.py`(LLM 1회·JSON), `pipeline.review_result`, 웹 '✅ 완성도 점검' 버튼/패널.
  점검만·자동수정 없음(경계선 유지). mock _review로 오프라인. test_review.
- **과제 유형 확장:** 에세이 외 문제풀이·보고서·코드·발표 감지(`understanding/task_type.py`,
  결정적). 유형별 지침(`prompts.TYPE_GUIDANCE`)을 시스템에 주입, 정형(문제풀이·코드)은
  `min_decisions=0`(억지 결정 금지). `spec["task_type"]`로 mock 분기·UI 배지. test_task_type.
- **근거(Citations) 노출:** Context가 모은 자료를 초안에 가시화(백엔드 무관). `build_messages`가
  인라인 자료에 번호 부여([자료1: ...]) → 모델이 `[자료N]` 인용 → `Result.sources` 범례 +
  웹 '📚 근거 자료' 패널('인용됨' 배지) + 본문 강조(.cite) + 리포트 Sources 섹션.
  Execution 프롬프트에 가짜 인용 금지 규칙. mock 초안이 [자료N] 시연.

## ✅ 제출용 내보내기 + 분량 점검 (2026-07-03, Fable 자율 루프)
- **제출용 문서 내보내기(경계선 유지):** 진단 리포트와 별개로, 학생이 그대로 이어서 완성·제출할
  '깨끗한 문서'만 낸다 — 본문 + '직접 정할 것' 체크리스트(+참고자료). 결정 마커는 본문에
  `【직접 정할 것 N】` 자리표시로 유지(대신 채우지 않음). `report.render_submission_markdown/html`,
  `write_submission`(확장자로 .md/.html 추론). CLI `--submission <경로>`.
  웹 다운로드 `GET /dl/<token>.md|.html`(`_send_download`, Content-Disposition, **헤더 파일명은
  ASCII** — non-latin1 헤더 인코딩 오류 회피), 초안·최종 페이지에 '제출용 .md/.html' 버튼.
- **분량 요건 감지·판정(`understanding/length_target.py`, 결정적·LLM 0):** 명세
  (requirements·constraints·goal)·원문에서 'N자 이상/이하', '500~800자'(범위), 'N페이지'(≈600자),
  'N매'(≈200자), 'N words' 감지 → 초안 측정(결정 마커·공백 제외 글자수/단어수) → 충족/부족/초과.
  '자·단어'를 페이지·매보다 우선. 경계선 유지(판정만, 억지 늘림/자름 없음). `Result.length_target`,
  리포트 '분량' 섹션, 제출용 📏 라인, 웹 초안·최종 배지(상태색: 부족/초과=강조색, 충족=녹색).
- **과제 요건 체크리스트(제출용):** 명세 requirements+constraints를 제출 전 확인 체크박스로
  (중복·빈 항목 제거). `report._requirement_items`, 제출용 md(`- [ ]`)·html(☐).
- **인용 커버리지 점검(`context/citation_coverage.py`, 결정적·LLM 0):** 제공한 근거자료 중 본문이
  `[자료N]`으로 실제 인용한 비율 집계 → 미인용/부분/충실/가짜번호(범위 밖) 판정. 경계선 유지(집계·안내만).
  리포트 Sources 섹션·웹 근거 패널에 상태색 메시지(🔎).
- **마감 D-day 감지(`understanding/deadline.py`, 결정적·LLM 0):** 명세 deadline·원문에서
  YYYY-MM-DD·N월 N일·M/D 파싱 → 오늘 기준 D-day. 연도 생략 시 다가오는 마감 추론(지난 날짜는 내년).
  `Result.deadline`, 웹 초안 배지(임박 3일내 강조), 리포트 '마감' 섹션.
- **제출 준비 점검(readiness, `until/readiness.py`, 결정적·LLM 0):** 위 결정적 점검들(마감·분량·인용·
  결정)을 한 요약으로. 경계선 유지 — 남은 결정은 경고 아닌 '당신이 정할 곳' 안내(info). CLI '6.
  제출 준비 점검' 블록, 제출용 md/html 섹션, 웹 초안·최종 패널(경고 강조색). 웹 개별 마감·분량 배지는
  이 패널로 통합(중복 제거, `_deadline_html`/`_length_html` 제거).
- **피드백 로그에 준비 점검 경고수:** `record_from_result`가 `n_readiness_warnings` 적립,
  `summarize`에 `avg_readiness_warnings`, CLI 피드백 줄 노출. 구버전 레코드 하위호환(default None).
- **준비 점검 유형별 조정:** readiness가 `spec.task_type`을 보고 정형(문제풀이·코드)은 수업자료
  미인용을 경고→안내(info), 결정 0개 경계선 경고 제외. 가짜(범위 밖) 인용은 유형 무관 경고 유지.
- **유형별 예제 추가:** `examples/sample_{problemset,code,report}.txt`(마감·분량 요건 포함).
  test_task_type이 실제 예제 ingest→분류→파이프라인까지 end-to-end 검증(각 유형 올바른 태깅).
- **진단 리포트 상단 준비 점검 통합:** `render_markdown_report` 헤더 바로 아래에 마감·분량·인용·결정
  통합 요약 배치. 하단 개별 '마감'·'분량' 섹션 제거(중복). 제출용·CLI·웹·리포트가 모두 같은 요약을 공유.
- **self-review에 준비 점검 주입:** 완성도 점검(AI)이 결정적 사실(마감·분량·인용·결정)을 근거로
  판단하도록 review user 메시지 초안 앞에 '결정적 사전 점검' 블록 주입(`review_draft(readiness_lines=)`,
  `pipeline.review_result`). mock body 파싱 보호 위해 초안 '앞'에 배치.
- **준비 점검 JSON 내보내기:** `Readiness.to_dict()`(headline·n_warnings·items), CLI
  `--readiness-json <경로>`로 저장. 웹 `GET /readiness/<token>.json`(만료 세션 404 JSON). 에디터·자동화 연동.
- **결정 지점 '왜 당신 몫인지' 근거(`boundary/rationale.py`, 결정적·LLM 0):** 결정 노트를
  가치판단/관점·논지/진로·경험/취향·스타일/범위·선택/고유판단으로 분류하고 왜 사람의 몫인지 한 줄.
  웹 결정 필드에 🔒 배지(.mine)로 노출 — 떠넘김이 아니라 '당신이어야 하는 순간'임을 밝힘. test_rationale(21스위트).
  제출용 md/html '직접 정할 것'·진단 리포트 Decision Points·CLI Boundary 섹션에도 동일 근거 노출(전 표면 일관).
- **AI 제안에 카테고리 활용:** `suggest_user_message`가 결정마다 rationale 분류를 `[카테고리]` 태깅 +
  성격별 제안 지침(가치판단=반대 입장 병기, 진로·경험=빈칸 틀 등). 예제 발표 유형 추가(5유형 완성).
- **초안→최종본 변경 투명화(`until/diffview.py`, 결정적·difflib):** finalize가 결정을 어떻게 녹였는지
  문단 diff(유사 문단은 '수정' 짝짓기). 웹 최종 '초안에서 달라진 부분' 토글, 리포트 변경 한 줄. test_diffview(22스위트).
- **웹 홈 기능 안내 카드 3장**(.feats 그리드) + README에 신규 기능 4종·테스트 수 22 반영.
- **CLI `--suggest [경로]`:** AI 제안 출력 + --resolve용 answers JSON 템플릿 저장(제안→수정→반영 왕복).
  mock _suggest가 [카테고리] 태그를 답에 새지 않게 벗김.
- **웹 세션 지속화:** 세션(Result+답변+제안+점검)을 토큰별 pickle로 `_until_work/web_sessions`에
  저장(최근 100개, gitignore 영역). 조회는 메모리→디스크 폴백(`_get_session`), 변경 6지점에서
  `_persist_session`. 토큰 정규식 검증(경로탈출 방지). 서버 재시작해도 학생 작업 유지.
- **`python -m until.feedback [경로]`:** 베타 기록 요약 출력(print_summary).
- **웹 `/sessions` 이전 작업 다시 열기 + 삭제:** 지속화 세션 목록(제목·시각·최종본/결정 배지,
  `list_sessions`), 홈에 ↺ 링크(세션 존재 시). 항목별 삭제(POST /sessions/delete, `delete_session` —
  메모리+디스크 제거, 토큰 검증). 서버 재시작 후에도 이어서 작업 가능.
- **러너 병렬화:** `run_tests.py`가 ThreadPool(기본 min(8,CPU), `-j N`, `-j 1`=순차)로 22스위트를
  **~10초**에 실행(이전 2분+). 출력은 SUITES 순서 결정적, 소요시간 표기.
- **세션 목록 유형 배지 + 제출용 HTML 인쇄 버튼**(printbtn, @media print 숨김).
- **docs/FEATURES.md** — 기능→코드→테스트 지도(팀 온보딩용). **examples/README.md** — 샘플 지도.
- **피드백 로그에 결정 성격 통계:** `decision_categories`(결정별 rationale 분류) 적립,
  `summarize().decision_category_counts`, `python -m until.feedback`에 '결정 성격 상위' 줄.
- **demo.py:** 5유형 샘플 일괄 데모(유형→결정+성격→준비 점검→제출용 저장, `-v`=본문).
  세션 pickle에 `v:1` 버전 태그(미래 버전 복원 차단, 태그 이전 파일 허용).
- **품질 리뷰 2회차(다중 에이전트)→실버그 4건 수정:** deadline 소수·버전 오탐(숫자형 M/D는
  마감 문맥 필수), length '장' 챕터 오탐(분량 문맥만 페이지)·선행 수식어(최대 N자→상한)+
  min/max 병합·'이내', list_sessions 손상 파일이 유효 세션 가림(필터 후 limit). 회귀 테스트 포함.
- **리뷰 잔여 2건:** 빈 자료+가짜 [자료N] → invalid 표면화(readiness의 if srcs 가드 제거),
  제출용 자리표시의 노트 속 【】→〔〕 치환(<mark> 경계 보호).
- **인코딩 견고화:** ingest 텍스트 읽기 utf-8-sig → cp949 → 대체문자 폴백(메모장 파일 대응).
- **파싱 실패 첨부 경고 표면화:** `ingest_all_with_warnings` + `Result.capture_warnings` →
  CLI '⚠ 스킵' 줄·readiness '자료' 경고(초안이 그 자료 없이 작성됨을 알림). README /sessions 문서화.
- **릴리스 0.2.0(태그 v0.2.0):** '제출 직전 점검' 릴리스. CHANGELOG.md 신설(추가/수정/개선 정리).

## 0.3.0 트랙 (2026-07-03~)
- **결정 답변 재사용(`context/answer_history.py`, 결정적):** 답한 결정을 로컬 JSONL 적립
  (웹 finalize·CLI --resolve), 비슷한 결정(유사도≥0.5, 최신 우선)에 '🕘 지난 답' 칩 재제안.
  칩은 data-val 원문으로 채움(pick()이 data-val 우선). 자동 채움 아님(경계선). test_answer_history(23스위트).
- **히스토리 인지 AI 제안:** suggest 프롬프트에 '내 과거 결정 답' 블록 주입(성향 일관·복사 금지),
  CLI --suggest에 '🕘 지난 답' 병기. `pipeline.suggest_decision_answers`가 answer_history 매칭 전달.
- **리포트 '변경 상세' 목록**(문단별 수정/추가/삭제, 12개 상한) + **/sessions 제목 검색 필터**.
  README·FEATURES 0.3.0 반영. boundary_guard 메시지는 점검 결과 이미 명확해 유지.
- **품질 리뷰 3회차→실버그 4건 수정:** load_history 타입 검증(비문자열 행→웹 500 방지),
  과매칭 3중 게이트(유사도+성격+내용어 — 상투 어미 오제안 차단), CLI --resolve 자기 반향
  (적립을 출력 뒤로), 웹 /finalize delta만 적립(중복이 prune 지평 잠식 방지).
  README 개인 데이터 절, demo 재방문 시나리오.
- **GEPA 학습셋 품질 우선 샘플링**(quality_sorted_examples — 준비경고 적은 실행 우선).
- **릴리스 0.3.0(태그 v0.3.0):** '기억하는 조수' — 답변 재사용·히스토리 인지 제안. CHANGELOG 반영.

## 0.4.0 트랙 (2026-07-03~)
- **최종본 결정 진행률(M/N+진행 바)**, **AGENTS.md 현행화 배너**(옛 경로·백로그 낡음 안내).
- **성향 반영 안내**(히스토리 존재 시 suggest 버튼 문구 변화) + **/sessions ⚠N 준비경고 배지**.
- **초안·최종 과제 제목(📄)** + **list_sessions 메타 캐시**((mtime,날짜) 키 — D-day 경고 스테일 방지,
  저장·삭제 시 축출). **릴리스 게이트 리뷰 4회차→버그 2+엣지 1 수정**(캐시 날짜 키·limit=0·persist 축출).
- **릴리스 0.4.0(태그 v0.4.0):** '오리엔테이션' — 진행률·제목·경고 배지·메타 캐시. CHANGELOG 반영.

## 0.5.0 트랙 (2026-07-03~)
- **내 답 문체 연계:** `answers_style_hint`(최근 30답 종결어미, 표본<3/미상/혼합 생략) →
  suggest·finalize voice_hint 병합. **voice 버그 수정:** 'ㅂ니다' 자모 패턴이 조합형과 매치 안 돼
  합니다체가 한다체로 오분류 → `니다$`.
- **finalize 성격별 반영 지침:** `render_resolved_block`이 답마다 [카테고리] 태깅 +
  '성격별 반영 지침'(관점=1인칭 논지, 진로=개인 경험, 범위=제외 미언급 등) — suggest와 대칭.
  mock 파서 계약 보존(답 줄이 지침 앞).
- **게이트 리뷰 5회차→수정:** '아니다' 합니다체 오분류 회귀(`(?<!아)니다$`), mock → 오분리(rsplit).
- **릴리스 0.5.0(태그 v0.5.0):** '내 문체 그대로'. CHANGELOG 반영. .row flex·FEATURES 갱신 포함.

## 0.6.0 트랙 (2026-07-03~)
- **히스토리 요약 명령**(`python -m until.context.answer_history` — 적립·성격 분포·문체·최근 답·삭제 안내).
  README 확인 명령·문체 연계 반영.
- **상대 날짜 마감:** 오늘/내일/모레·요일·이번/다음 주 파싱(문맥 게이트, 절대 날짜 우선,
  무수식 요일=다가오는 요일).
- **게이트 6회차→수정 3건:** '오늘날' 오탐((?!날)), '내일모레'=+2, category 비문자열 크래시.
- **릴리스 0.6.0(태그 v0.6.0):** '마감을 알아듣는'. demo 문체 시연 포함. CHANGELOG 반영.

## 0.7.0 트랙 (2026-07-03~)
- **과거 날짜 내년 범프에 문맥 요구:** 미래 MD_KR은 무문맥 신뢰(기존), 과거는 마감 문맥 시
  범프 '후보'로 보관→다른 단서 없을 때 마지막 사용. 참고 언급('6월 1일 강의')이 진짜 마감을 가리지 않음.
- **연장 마감:** 텍스트에 '연장'이 있으면 전 형태 후보를 모아 가장 늦은 날짜 채택(_emit 수집 모드).
- **게이트 7회차→수정:** YMD 부분문자열 재매칭(ymd_spans 스킵), 숫자형 과거 범프 비대칭
  (_md_candidate 통일), 연말 걸침(past_bumped min), 트리거 정밀화('연장전' 배제).
- **릴리스 0.7.0(태그 v0.7.0):** '마감 이해 고도화'. 웹 오류 경로 테스트 포함. CHANGELOG 반영.

## 0.8.0 트랙 (2026-07-03~)
- **마감 시각 병기:** Deadline.time_str(23:59/오후 N시/자정 등, '9시간' 기간 배제) → D-day 라벨.
  parse_deadline = 코어(_parse_deadline_date)+시각 부착 래퍼.
- **docs/PROGRESS-2026-07-03.md:** 오늘 세션 종합 리포트(릴리스 6·리뷰 7·버그 17).
- **연장됨 라벨(Deadline.extended)** + **게이트 8회차→시각 탐지 버그 3건 수정**
  (슬라이스 절단 조작·첫 등장 앵커·무효 시각 — raw_pos 앵커+원문 finditer+_valid_time).
- **릴리스 0.8.0(태그 v0.8.0):** '시각까지 아는'. CHANGELOG 반영.

## 0.9.0 트랙 (2026-07-03~)
- **연장 공지 예제**(sample_extension.txt, e2e: 연장+시각+라벨) + README 마감 이해 갱신.
- **광역 스모크(70케이스)→오탐 2계열 수정:** 페이지 참조(교재 N페이지 참고/N~M쪽 읽고) 배제
  (_is_reading_ref), 번호 참조(버전 1.2/문제 3.2/5.2절) 배제(_NUMREF_*).
- **알려진 한계(보류):** 무문맥 미래 'N월 N일' 신뢰(설계), '13시30분' 미탐, 과거시제 마감
  ('~까지였습니다') 내년 범프, '학번 4자/사진 1매' 하한 무해 오표기.
- **릴리스 0.9.0(태그 v0.9.0):** '오탐 소탕'. CHANGELOG 반영.

## 🏁 1.0.0 (2026-07-03, 태그 v1.0.0) — "경계선까지, 완성"
- 완성도 트랙: 문서 링크 무결성(전부 정상), README demo 출력 예시, CONTRIBUTING 품질 게이트 관행.
- **웹 UI 전 경로 워크스루 통과(에이전트, 67체크/실패 0)** — 홈→초안→제안→점검→finalize(부분/전체)
  →진행률→다운로드→JSON→세션 목록·삭제→히스토리 재제안→서버 생존.
- 누적: 릴리스 9개(v0.2.0~v1.0.0), 게이트 리뷰 8회+스모크 70케이스, 실버그 22건 수정, 23스위트.
- 알려진 한계는 CHANGELOG 1.0.0 절에 명시.

## 1.x 트랙 (2026-07-03~)
- **웹 /history:** 내 답 히스토리 보기(성격 분포·문체·최근 15)+전체 삭제(POST /history/clear),
  홈 🕘 링크. 개인정보 통제 웹 완성.
- **readiness JSON severity 정렬**(사람용은 의미 순서 유지), **유형 분류 스모크 20케이스 오분류 0**
  (대표 5건 회귀 고정), **유형별 제출 팁(✍)**, PROGRESS 1.0.0 갱신.
- **릴리스 1.1.0(태그 v1.1.0).** ⚠ 서브에이전트 세션 한도 소진 시(리셋 11:50pm CT) 리뷰·스모크는 인라인로.
- 1.1.0 후: 인라인 미니 리뷰(이상 없음·/history XSS 회귀 고정), /history 검색, CHANGELOG
  Unreleased 관행, **test_runners(24스위트)** — 러너 5종 컴파일·demo e2e·문서-코드 정합 감사.
- **종합 리뷰 9회차→수정:** 2차 패스(finalize·suggest·review) [자료N] 번호가 범례와 어긋나고
  eTL 자료 누락 → `Result.source_docs` 공유(+구세션 폴백 `_all_source_docs`).
- **릴리스 1.2.0(태그 v1.2.0):** 번호 체계 일치·`python -m until`·정합 감사. CLAUDE 상단 상태 배너.
- **테스트 20스위트.** 신규: test_submission, test_length, test_citation, test_deadline,
  test_readiness(마감·분량·인용·결정 통합 판정). test_feedback에 준비경고 기록 케이스 추가.

## 1.3.0 트랙 (2026-07-05~11)
- **제품화 UX:** 홈 초미니멀('내 eTL 과제 불러오기' 중심), **간단 모드 /simple**(원-액션:
  붙여넣기→초안→답→완성본, ui=simple 전 경로 전달), **⚡ 바로 초안 /quick**(미제출>기한>임박
  자동 선택), 기한 지난 과제 숨김 필터, **플랜·사용량·결제 스캐폴드**(billing.py, 라이브만
  게이트·mock 무제한, /plan). 디자인: 에디토리얼→클로드 리테마(크림 종이·테라코타)+모션
  레이어+히어로 실사진(Unsplash, /asset 정적 라우트).
- **게이트 리뷰 10회차(에이전트 3)→실버그 8건 수정:** billing 비-UTF8 라이선스 전면 크래시·
  적립 경합(잠금+원자 교체+Windows 재시도), answer_history 이중 unescape(정화 load 일원화·
  완전 엔티티 1패스 치환·대문자 X), 웹 /simple 막다른 에러 링크·fast 폴백이 제출완료 과제
  자동초안·공백 env 토큰 판정 모순. 러너 미등록 테스트 2건 발견·등록.
- **릴리스 1.3.0(태그 v1.3.0) "한 번에, 간단하게".** 테스트 25스위트(신규 test_billing).
  docs(README·FEATURES·AGENTS 배너) 현행화.

## 1.4.0 트랙 (2026-07-11)
- **간단 모드 마무리:** 준비 점검 ⚠ 한 줄(경고 있을 때만·'자세히' 링크), '🕘 지난 답' 칩
  (클릭 채움·자동 아님), /plan 키 실패 피드백(err=1), /sessions ✳ 간단 열기.
- **docx/pptx/html 내장 폴백 파서(의존성 0):** docling 없이 zip 바이트 오염 방지 —
  zipfile+ElementTree·html.parser. 이진 포맷(.doc/.hwp 등)은 명확한 예외→경고 표면화.
- **테스트 등록 누락 영구 방지:** test_runners AST 감사(함수 참조+SUITES 1:1).
- **릴리스 게이트:** 웹 전 경로 워크스루 14체크 통과(간단 3단 왕복·다운로드·플랜 변형·
  에러 경로·XSS, 에이전트). NOTICE 히어로 사진 표기.
- **릴리스 1.4.0(태그 v1.4.0) "첨부까지 읽는, 간단하게 끝내는".**

## 1.5.0 트랙 (2026-07-11)
- **제출용 .docx 내보내기(의존성 0):** `report.render_submission_docx`(최소 유효 OOXML),
  CLI --submission·웹 /dl/<t>.docx·간단 완성본 버튼·demo 시연. 자체 docx 폴백 왕복 검증.
- **HWPX 첨부 파싱(의존성 0):** Contents/section*.xml 순서 추출(로컬명 순회). .hwp는 경고 유지.
- **인박스 D-day 태그**(3일 이내 임박 강조)+**60초 캐시**(라이브 재방문 즉시).
- **답 입력 로컬 보존**(localStorage, 세션·필드별 키, 제출 시 삭제, 서버 전송 없음).
- **게이트 리뷰 11회차→수정 3건:** D-day UTC 달력→로컬 환산(KST 자정 마감 하루 오차),
  당일 시각 지난 마감 '지남' 일관, 테스트 자정 플레이크. localStorage·캐시 지적 0건.
- **릴리스 1.5.0(태그 v1.5.0) "워드로 내고, 한글도 읽는".**

## 1.6.0 트랙 (2026-07-11)
- **웹 내 자료 파일 첨부:** /simple 폼 multipart(email 파서, 의존성 0) → 최대 5개·
  8000자/건·합계 25MB, `[내 자료]` SourceDoc 주입 → `[자료N]` 인용. 파싱 실패·빈
  파일은 준비 점검 '자료' 경고. `run_text(extra_sources=)` 배관.
- **게이트 리뷰 12회차→수정 4건:** 413 응답 유실(드레인 후 응답), 중복 파일명 `(2)`
  접미사, 0바이트 경고 표면화, 위조 가능 센티널 제거. 한글 파일명·RFC5987·바이너리
  무결성·빈 file input 회귀 등 20여 케이스 재현 검증 이상 없음.
- TEAM_START 현행화. `_read_form`에도 25MB 상한.
- **릴리스 1.6.0(태그 v1.6.0) "내 자료까지 함께".**

## 1.7.0 트랙 (2026-07-11)
- **웹 '내가 쓴 글' 업로드 → 문체 반영:** voice_files 입력 → ingest(.docx/.hwpx 변환)
  → `.voice.txt` 임시 폴더 → `voice_from_dir` 프로파일 → 초안이 내 말투. 자료/문체
  필드 분리(문체가 근거로 새지 않음), 실패 경고, 사용 후 정리.
- **릴리스 게이트: 워크스루 13차 10체크 전부 통과**(빈 filename 파트·한글 파일명·
  26MB 413 온전 수신·잘못된 multipart 생존·.docx 다운로드 PK 시그니처).
- **릴리스 1.7.0(태그 v1.7.0) "내 말투까지 함께".** README·FEATURES·PROGRESS 갱신.

## 1.7.1 패치 (2026-07-11) — 종합 리뷰 13회차
- **web.py 전체 리뷰(실서버 재현)→4건:** 게이트가 업로드 본문 안 읽고 303→RST 유실
  (폼 먼저 읽기, 3경로), 재방문 프리필 내 답>AI 제안('전부 수락'이 내 답 되돌리던
  실측 버그), 간단 모드 프리필 연결, multipart 예외 2-튜플.
- **파서 견고성 리뷰(27케이스)→3건:** .docx C0 제어문자→Word 못 여는 파일(XML 금지
  문자 제거), HTML close() 누락 꼬리 유실, zip 폭탄 50MB 사전 상한(_zip_read_capped).
- [자료N] 번호 일관·세션 수명·escape·스레드 안전·깨진 아카이브 수렴은 이상 없음 확인.

## 백로그에서 명시적으로 제외한 것
- **.hwp→.hwpx 변환 대행(보류, 2026-08-05):** 변환은 사실상 한글 프로그램만 가능
  (클라우드엔 한글 없음). 검토안 — A) 로컬 한글 COM 자동화, B) 재구성 .hwpx 생성
  (원본 서식 유실·제출물 위험이라 비권장), C) .hwp 양식 감지 시 채운 값 .docx 표
  제공+.hwpx 재업로드 안내. 사용자 결정: 일단 보류. 재개 시 C(+A 로컬 옵션) 권장.
- **세션 내보내기/가져오기(다른 PC 이전):** 단일 사용자 로컬 MVP에선 `_until_work/web_sessions/`
  폴더(+`answer_history.jsonl`)를 그대로 복사하면 동일 효과 — 전용 UI의 추가 가치가 낮아 제외(2026-07-03).

