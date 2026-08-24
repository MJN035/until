# CLAUDE.md — Until 프로젝트

> 이 파일은 **매 세션·매 서브에이전트에 통째로 주입된다.** 지금 지켜야 할 규칙과 현재 트랙만 담는다.
> 완료 이력은 `docs/HISTORY.md`, 릴리스 상세는 `CHANGELOG.md`, 기능→코드 지도는 `docs/FEATURES.md`,
> 설계·파일 지도는 `AGENTS.md`, 2학기 과목 알고리즘은 `docs/COURSE_ALGORITHMS_2026F.md`.
> **여기에 완료 로그를 다시 쌓지 마라** — 새 이력은 `CHANGELOG.md`의 Unreleased 절로.

> Claude Code는 이 폴더에서 셸을 직접 실행할 수 있다 — 사용자에게 명령을 복붙시키지 말고
> **cd·git·pip·python·테스트를 네가 직접 돌리고 에러도 네가 고쳐라.**
> 사용자가 직접 해야 하는 건 단 둘: **(1) 브라우저 SSO 로그인 클릭, (2) 비밀 키 제공.**

## 이 제품이 뭔가
**Until** = 대학생의 과제·잡무를 *사람의 판단이 필요한 경계선 직전*까지 대신 끝내 주는 AI 에이전트.
핵심 개념 **Draft 경계선**: 자료로 채울 수 있는 건 끝까지 쓰되, 사람의 고유 판단(관점·취향·진로·가치판단)은
절대 대신 정하지 말고 `[[DECISION: ...]]` 마커로 남긴다. `BoundaryValidator`가 이를 코드로 강제한다.

## 파이프라인
`eTL/파일 → Capture(파싱,토큰0) → Understanding(LLM) → Context(수업자료·내파일·내말투) → Execution(경계선 초안) → Boundary(결정지점 + 프롬프트 제안)`

## 절대 불변 규칙
1. Execution은 사람 판단을 직접 확정 금지 → `[[DECISION]]`로. (`execution/boundary_guard.py`)
   단, **사용자가 답을 비운 채 '완성하기'를 누르면** finalize가 남은 칸을 AI 제안으로
   채운다(사용자 지시 2026-08-20, `web._fill_blank_decisions`, 끄기
   `UNTIL_AUTOFILL_DECISIONS=0`). Execution이 확정하는 게 아니라 **사람이 위임한**
   것이므로 규칙 위반이 아니다. 대신 채운 사실과 내용을 화면에 반드시 밝힌다 —
   이 표시를 지우면 학생이 자기가 정한 줄 알고 제출한다. 되돌리지 마라.
2. `--backend mock` + 모든 테스트(89스위트)는 키·인터넷 없이 항상 통과해야 한다.
3. `capture/`,`context/`,`boundary/`,`prompts/suggest.py`는 LLM 호출 0 (결정적).
4. LLM은 `llm/base.py`의 `LLMClient.complete()` 하나로만.
5. 용어: 파싱단계="Capture", 서울대 LMS="eTL"(현재 Moodle WS 기반, 레거시 Canvas 어댑터 보존).
6. 소스 접속 방식은 `BrowserAdapter` 뒤에. 파이프라인 코어는 접속 방식 모름.

## 🚫 타협 불가 — 수치 날조 금지
`hdl_lab`의 **파형·합성 수치**, `lab_report_cycle(result)`의 **실측값**을 생성하지 않는다.
근거가 없으면 값을 채우지 말고 빈칸 `[[DECISION]]`으로 남긴다. 지어낸 수치는 그대로 제출되어
**학문적 부정**이 된다. unit 경로는 코드로 차단돼 있고, legacy 경로도 사후 검증기
(`measured_check.find_ungrounded_measurements`)로 readiness fail 승격 + 1회 reask +
결정적 치환 + 제출 게이트 하드 블록까지 코드로 강제된다(탈출구 `UNTIL_MEASURED_ENFORCE=0`).

## 프롬프트 버전 규율
- 프롬프트(`execution/prompts.py`의 SYSTEM·FINALIZE_SYSTEM·TYPE_GUIDANCE·few-shot,
  `context/tone.py`의 톤 직렬화, `context/{episodes,facts}.py`의 주입 블록)를 **의미 있게**
  고치면 `until/persona/versions.py`의 `PROMPT_VERSION`을 손으로 올린다.
  올리는 행위가 곧 "이건 의도한 변경"의 선언이다 — 자동 증가 금지.
- 그다음 `python tools/check_prompt_version.py --update`로 기준선을 갱신한다.
- 잊으면 `test_persona_portability`가 실패한다(감시 표면 17개). **기준선을 무작정
  갱신하지 마라** — 무엇이 왜 바뀌었는지 먼저 확인하고, 의도치 않은 변경이면 되돌린다.
- 이걸 어기면 "톤이 바뀐 게 모델 때문인지 프롬프트 때문인지"를 사후에 가릴 수 없고,
  이벤트 로그의 `prompt_version`이 통째로 거짓말이 된다.

## 알고리즘 버전 게이트
- `UNTIL_ALGO_VERSION` 기본값 **v0.1**. v0.2는 게이트 뒤에 구현돼 있다.
- **v0.2 코드를 건드리면 반드시 v0.1 불변(결정성 SHA-256 일치)을 함께 확인한다.**
  `.github/workflows/determinism.yml`이 CI에서 강제하지만, 로컬에서도 먼저 돌려라.
- 8월은 `algo_version`을 **동결하고 측정하는 달**이다. 자동 알고리즘 업데이트(자가발전) 금지 —
  결정성이 깨지면 백테스트가 무의미해진다. 변경은 제안 → 사람 승인 → 버전 태깅.
- 6과목 라우팅 설계는 `docs/COURSE_ALGORITHMS_2026F.md`. 신설 구간은 제외 판정(퀴즈·성적) 뒤,
  `_INQUIRY` 앞에 온다.

## 데이터 원칙
- **팀원 eTL 토큰을 받지 않는다.** 각자 자기 노트북에서 자기 토큰으로 돌리고, 공유는
  비식별 텔레메트리(`telemetry.jsonl`)만. 스키마 `docs/TELEMETRY_SCHEMA.md`.
  **예외 하나(사용자 지시 2026-08-21):** 베타 신청 폼의 eTL 토큰 칸은 신청자가
  **선택적으로** 적어 `/admin`에서 보인다(`until/betarequests.py`). 예외이므로 반경을
  좁혀 뒀다 — 폼에 "eTL 계정 전체를 여는 열쇠"임을 명시, 보드는 기본 가림(펼쳐야 원문),
  KV 90일 TTL, `UNTIL_BETA_COLLECT_TOKEN=0`이면 칸 자체 제거. **베타 온보딩이 끝나면
  이 칸부터 끈다.** 파이프라인·세션 경로는 여전히 토큰을 저장하지 않는다.
- 텔레메트리의 모든 문자열 값은 **열거형이거나 해시**. 자유 문자열 금지. allowlist build-up 방식.
- 파이프 2개 분리: 원문 파이프(사용자 소유·학습 미사용) / 신호 파이프(비식별·알고리즘 개선용).
- `capture/sources/canvas_api.py`의 `parse_my_feedback()`은 성적·교수 코멘트·루브릭 점수를 수집한다.
  이 경로를 확장할 때 무엇이 로컬에 남는지 항상 확인할 것.
- **미검증 서드파티 MCP 서버를 이 레포에 붙이지 않는다**(eTL 베어러 토큰·팀원 성적 취급).

## 현재 트랙 (2026-08-22)
- `algo_version=v0.2` 게이트 뒤 6과목 알고리즘 구현 완료(커밋 `64304da`), 3인 코퍼스 불변 검증됨.
- `UNTIL_PIPELINE` 기본값 **unit**(커밋 `51c10e6`).
- **§3 과목 프로파일이 이제 실제로 성립한다** — 라우팅 전 과목명 전달(`dc1374c`) +
  사용자별 저장·입력 화면(`c901edc`, `/profile`의 '과목 유형'). 둘 다 있어야 켜진다.
- **실사용 마찰 원장 1회차** `docs/launch/2026-08-22-friction-ledger-01.md`(17건).
  고친 것: `A4 N매` 오독·쪽당 900자·헤더 백엔드 노출·초안 접기·`/profile` 링크.
  남은 결정: **D-2 eTL 토큰을 새 작업의 전제로 둘 것인가**(콜드 스타트 벽 3개).
- **2회차 실 LLM 완주(Cerebras `gpt-oss-120b`, 라이브 실토큰 + 로컬 키)** — D-4
  확정·수정(완성본이 인용을 전부 잃던 것: 0/3 → 8/8). 새로 나온 것:
  - ~~U-1~~ **수정됨** — 본문도 질문도 없이 끝나는 초안(`_ensure_answerable`).
    fail-open 3겹(골격 없는 task_type → 빈 계획 → 검증기 소멸 + `safety_mode`
    하한 1자)이 겹친 결과였다. 실 LLM 4회 막다른 길 0/4.
  - **U-5 일부 수정** — 파생 버그 둘을 고쳤다: ①페이지 요건이 unit 경로에서
    증발(`base = tgt.max`를 글자 수로 그대로 써서 '페이지 5'=5자 → 60자 게이트에
    걸려 분량 검증기 미부착) → `target_in_chars()` 환산, ②슬라이드 장수를 산문
    분량으로 오독(`슬라이드 8~12장`→10,800자) → 슬라이드 문맥 배제.
    ⚠ **골격 없는 유형에 분량 목표를 주지 마라** — 시도했다가 코퍼스 9건이 깨졌다
    (`problemset`·`code`·`presentation`에 산문 글자 수 요구는 애초에 틀렸다).
    **본체는 라우팅이라 미해결**: 산문 과제가 `problem_set`으로 오추정돼 골격
    구멍(`get_skeleton()`→`None`)으로 떨어진다. 갈래는 (a)추정 정확도
    (b)세 유형에 진짜 골격 설계 — 둘 다 코퍼스 재검증 크기.
  - ~~U-2~~ **수정됨(이번 세션의 본질 문제)** — 원료가 없으면 자료를 요청하지 않고
    **과제 메타데이터에 대한 글**을 썼다(HW#1: 1,383자 추측 + `[출처]` 날조,
    예비보고서: 없는 "모델 E-100"). 원인 3겹: ①essay가 원료 게이트 면제인데
    essay가 **기본값**이다 ②유형 지침("끝까지 써라")이 원료 없음 지침과 충돌
    ③본문이 가리키는 첨부가 없다는 사실을 아무도 안 봤다. 각각 실내용 글자 수
    게이트 · 유형 지침 끄기 · `missing_attachments()`로 고침. HW#1 → 두 줄
    ("`HW1.pdf`를 못 읽었습니다. 올려 주시면 이어서 씁니다").
  - **U-3 (다음 1순위) 질문의 품질** — 산출물이 과제 자체를 서술하던 것을
    `AssignmentMetaValidator`로 막고 나니(3회차) 남는 건 질문인데, 그 질문이
    `[[DECISION: '① 항목' 강의에서 본인의 '고찰' …]]` 처럼 **내부 슬롯 라벨이
    새고 과제와 무관**하다. 이 제품의 값어치는 "사람만 정할 수 있는 것만 묻는다"
    인데 묻는 내용이 틀렸다. 초안을 길게 만드는 것보다 이게 먼저다.
  - U-4 같은 입력에 초안 길이가 87~2,738자로 널뜀 — **eval은 1회 실행으로 믿으면
    안 된다**(반복 평균 필요). "unit이 못 만든다"는 초기 판단은 이 분산을 n=1로
    본 오독이었다.
- **다음:** ① **U-3 질문 품질** ② U-5 본체(라우트 오추정 / 세 유형 골격) ③ 개강 첫 주 6과목 실제 과제 제목 덤프 → 45케이스
  예상 제목 교체·재검증 ④ 3인 `course_profiles` 작성(이제 화면에서)
  ⑤ 3~6주차 텔레메트리 측정 후 v0.2 기본 승격 판단.
- ~~드문 간헐 테스트 실패~~ **원인 규명·수정됨(2026-08-23)** — 트레이스백을 잡으니
  `TelemetryLeakBlocked: free string blocked in telemetry: '발표'`였다. readiness 경고
  라벨은 텔레메트리 열거형(`telemetry/schema.py` `_ENUMS`)에 등재돼야 하는데
  **발표·코드·실측·실행결과·활동기록** 다섯이 빠져 있었다. 그 라벨이 뜨는 표본이
  걸린 실행만 터져서 무작위·병렬 문제처럼 보였다. 다섯을 등재하고,
  `test_telemetry_web`이 누락을 기계로 막는다(라벨을 새로 만들면 여기 등재해라).
  같은 날 CI에서 **두 번째 원인**도 잡았다: `assert res.elapsed_ms > 0`가 mock 실행이
  1ms 안에 끝나면 터졌다(`monotonic` + `int()` 절삭 → 0ms = 미측정과 구분 불가).
  `perf_counter` + 올림으로 고쳤다 — 텔레메트리도 0이면 그 실행을 통째로 빠뜨렸다.
- ⚠ 코퍼스 기준선: minjun **104/44**(기록된 105/43은 낡음 — `ai_use_prohibited`
  제외 1건이 늘어난 것으로 정상) · jihu 116/63/1 · jaewon 150/61/0.
- 외부 자원 필요: GEPA 유료 티어 대규모 예산 실행, IR 중간발표(`docs/launch/until-qa-준비.md`).

## 라이브 운영
- 앱 https://until-app.onrender.com (Render Blueprint `render.yaml`, **main 푸시 = 자동 재배포**,
  엔트리포인트 **ASGI** `uvicorn until.asgi:app`), 랜딩 https://until-landing.minjun05.workers.dev.
- 베타 게이트 `UNTIL_BETA_CODE`. 주 제공자 Cerebras(`UNTIL_API_KEY`), 백업 사슬 `_2` Kimi·`_3` Gemini·`_4` Groq.
- 텔레메트리 env: `UNTIL_TELEMETRY=1` + `UNTIL_TELEMETRY_SALT` + `UNTIL_PROJECT_SALT`.
- 재시작 생존: `UNTIL_KV_TOKEN`(`cfut_`, Bearer 단어 제외) + `UNTIL_SESSION_KEY`.
- **제출 실행: 라이브에서 켜져 있다**(`render.yaml`, 사용자 결정 2026-08-23). 확인 화면 클릭으로 eTL에 실제 제출한다
  (`capture/sources/moodle_submit` — 저장 → 채점 확정 두 걸음). 끄려면 render.yaml에서
  값을 "0"으로 바꾸거나 키를 지운다(코드 변경 불필요).
  스위치가 켜져도 자동 제출 경로는 없다 — 한 건마다 1회용 nonce·제출 게이트·신뢰
  호스트(myetl.snu.ac.kr)를 통과해야 하고, 원장은 `_until_work/submit_audit.jsonl`.
  `UNTIL_SUBMIT_BACKEND=canvas`로 레거시 Canvas 경로를 되살릴 수 있다.
- ⚠️ **소금(`*_SALT`)과 세션 서명 키(`UNTIL_SESSION_KEY`)는 교체 금지** — 교체 시 기존 해시·세션이 전부 깨진다.

## 작업 방식
- 한 번에 작은 변경 → `python run_tests.py`(99스위트, 병렬 ~15초) 통과 → 커밋. 큰 리팩터링 금지.
- 린트 `ruff check .`.
- Windows 콘솔 인코딩: `PYTHONIOENCODING=utf-8` (em-dash 출력 에러 회피).
- 작업 시작 전 `git status`로 변경 범위 확인. 기존 git 이력 유지.
- 커밋 전 시크릿 확인 — `.pre-commit-config.yaml`(gitleaks)이 걸려 있다. 우회 금지.
- 프로젝트 스킬: `/until-smoke`(라이브 점검) · `/until-release`(릴리스 게이트).
- 서브에이전트: `.claude/agents/`에 router·guidance·cycle·measure 4종 정의됨. 알고리즘 작업은 이걸 쓴다.
