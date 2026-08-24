# Local Agent Runtime — eTL에서 제출 직전까지 한 번에

작성 2026-08-21 · 사용자 지시: "에이전트식으로 처음부터 끝까지 다해줄 수 있도록"
**이 문서는 인수인계용이다.** 중간에 끊기면 다음 세션(또는 codex)이 여기 체크박스부터
이어서 한다. 한 단계 끝낼 때마다 체크하고, 배운 것을 그 자리에 적는다.

## 목표 한 줄

`python -m until.runtime --fast` 하나로 **eTL 과제 선택 → 자료 수집 → 로컬 에이전트
작업 → 결정적 검증 → 제출 파일 + eTL 제출 페이지 링크**까지 간다. 사람이 하는 건
①계획 승인 ②eTL에 파일 올리기 둘뿐이다.

## 지금 어디까지 와 있나 (2026-08-21 기준)

| 조각 | 상태 |
|---|---|
| 에이전트 계약·격리·검증·번들 (Phase 0~4) | 완료, 90스위트 통과 |
| `python -m until.runtime <로컬파일>` 진입점 | 완료(커밋 `005aef6`) |
| **eTL 입력** | **없음 — 로컬 파일만 받는다. 이 문서가 메우는 구멍.** |
| **제출 마무리** | **없음 — 번들 경로만 찍고 끝. eTL 링크가 없다.** |
| Windows 실제 실행 | 샌드박스 없어 fail-closed. WSL2/컨테이너 필요(§5) |

웹 경로(`until.web`)는 이미 eTL→제출직전까지 간다. **웹 UI는 건드리지 않는다**
(사용자 지시: "미적인 부분은 해치지 말고"). 병행 세션이 UI를 계속 고치고 있으니
`until/webassets/`, `DESIGN.md`, `render_*` 함수는 이 작업 범위 밖이다.

## 지켜야 할 제약

1. **Until 모델 API 호출 0.** 이 경로의 전제다(`docs/ASSIGNMENT_RUNTIME_PLAN.md` §8).
   명세는 `runtime/spec_builder.py`가 결정적 판정기로만 만든다. Understanding(LLM) 금지.
2. **fail-closed 유지.** 샌드박스 격리를 거짓 신고하지 않는다. 승인 전 작업 프로세스 0회.
3. **테스트는 전부 오프라인.** 실제 CLI·네트워크 0회. 러너·어댑터 주입으로 대체한다.
4. **제출은 사람이 한다.** 런타임이 eTL에 POST하지 않는다. 링크와 파일까지만.
5. **웹 UI 무변경.** 필요하면 기존 클래스(`matbox`·`btn`·`meta`)만 재사용.
6. 커밋 전 `python run_tests.py` + `ruff check .` + `tools/check_determinism.py`.

## 단계

### [x] S1 — eTL 어댑터를 런타임 입력으로

`until/runtime/etl_input.py` 신규. 토큰으로 어댑터를 만들고
- `--etl-url <과제 URL>` 또는 `--fast`(마감 임박 자동 선택, `inbox_policy.pick_best`)
- 과제 본문 + 첨부 + 관련 강의자료를 임시 폴더에 내려받아 파일 목록 반환
- 과목·과제 id, 제목, 마감, 제출 페이지 URL을 함께 반환

재사용: `capture.sources.discovery.EtlInbox`, `capture.sources.etl.EtlSource`,
`capture.sources.canvas_api.CanvasApiAdapter`, `inbox_policy.pick_best`,
`context.etl_materials`. 전부 LLM 0.

**한 것:** `until/runtime/etl_input.py`. 강의자료는 `fetch_material_texts`(발췌)가 아니라
**파일 자체를 내려받는다** — 에이전트는 작업공간의 파일을 직접 읽으므로 잘라 낸 발췌보다
원본이 낫다. `etl_ws_base()`는 web.py에만 있어서 같은 규칙(`UNTIL_ETL_BASE` → SNU eTL)을
`etl_base_url()`로 런타임 쪽에 뒀다 — 런타임이 웹 서버 모듈을 임포트하면 CLI 하나 쓰려고
HTTP 계층까지 끌고 들어온다.

**미리 아는 함정(이번 세션 실측):** `EtlInbox.__init__`의 `base_url` 기본값이 임포트 시점에 묶여서, 테스트에서
`SNU_ETL_BASE`만 바꾸면 안 먹는다 — `base_url=`를 명시적으로 넘겨야 한다.

### [x] S2 — CLI에 eTL 경로 연결

`runtime/cli.py`에 `--etl-url` / `--fast` / `--token`(기본 `UNTIL_CANVAS_TOKEN`) 추가.
파일 인자와 상호배타. 수집 결과를 그대로 `RuntimeRequest.inputs`로.
`assignment_id`는 eTL 과제 id를 쓴다(번들·제출 대조가 같은 id를 봐야 한다).

**한 것:** `--fast`/`--etl-url`/`--list`/`--token`/`--ws`/`--materials`. eTL 경로와 로컬
파일 인자는 상호배타(둘 다 주면 거부). 내려받은 원본은 임시 폴더에만 두고 작업공간에
복사된 뒤 지운다(`_cleanup`, 테스트로 확인).

### [x] S3 — 제출 마무리

검증 통과 후 출력에 **eTL 제출 페이지 URL**과 올릴 파일 경로를 함께 찍는다.
링크는 `web._assignment_link`와 같은 방식(과목·과제 id로 재구성, 원문 URL 미보관).
**한 것:** 검증 통과 출력에 제출 페이지 URL을 찍는다. `--open`은 만들지 않았다 —
링크 한 줄이면 충분하고, 브라우저를 여는 부작용은 CLI가 알아서 할 일이 아니다.
`submit_page_url()`은 id가 숫자가 아니면 링크를 지어내지 않는다(WS·SSO 경로).

### [x] S4 — 테스트

`tests/test_runtime_etl.py` 신규. 가짜 Canvas 어댑터 주입(네트워크 0):
- `--fast`가 마감 임박 미제출 과제를 고른다
- 첨부가 workspace `inputs/`에 들어간다
- 제출 링크가 과목·과제 id로 재구성된다
- 토큰 없으면 안내하고 종료(프로세스 0회)
- 과제가 0건이면 빈손으로 끝내지 않는다

**한 것:** `tests/test_runtime_etl.py` 9케이스, 전부 첫 실행에 통과. 어댑터·러너 주입으로
네트워크 0. 가짜 인박스에 '이미 제출한 과제'와 '마감 더 먼 과제'를 섞어 두고 `--fast`가
마감 임박 **미제출**을 고르는지 본다.

### [x] S5 — Windows 실행 경로 문서화

Docker Desktop이 깔려 있으므로 컨테이너 샌드박스 예시를 `LOCAL_AGENT_SETUP.md`에 추가.
`--network none` + 작업공간만 rw 마운트. **공식 CLI 로그인 상태를 컨테이너 안에서
어떻게 유지하는지**가 관건 — 확인 전에는 격리를 신고하지 말라고 명시할 것.

**한 것:** `LOCAL_AGENT_SETUP.md`에 컨테이너 예시 + 확인 3가지(네트워크 차단·작업공간 밖
쓰기 차단·컨테이너 안 로그인 유지)를 적었다. **이 기계에서 검증하지 못했다** — Docker CLI는
있으나 데몬이 꺼져 있다(`docker info` 실패). 그래서 문서에 "저자가 검증하지 않았다"를
명시하고 `UNTIL_AGENT_SANDBOX_ISOLATES` 줄은 주석 처리해 뒀다. **다음 사람이 실제로
확인하고 주석을 풀 것.**

### [x] S7 — 제출본 마무리 (사용자 지시: "처음 메우는 데서 멈추지 말고 끝까지")

검증된 초안에는 `[[DECISION: ...]]`이 살아 있다(검증기가 남기라고 강제). 번들이 그
파일을 그대로 담고 있어서 **올리면 교수가 내부 대괄호를 본다.**

**한 것:** `until/runtime/finish.py` — 답한 결정은 문장으로 치환, 안 정한 곳은
`【직접 정할 것 N】`로, `artifacts/제출본.md`에 쓴다. `work/draft.md`는 그대로 둔다.
치환 규칙은 웹과 같은 함수(`report.resolve_decision_markers`)를 쓴다.
`--answers 파일`·`--ask`. 치환 뒤 분량·인용·섹션 재검사.

**왜 package() 안에서 안 했나:** 오케스트레이터가 `plugin.package()` 전후로 작업공간
스냅샷을 비교해 "packaging modified validated workspace files"로 막는다. 포장 단계는
파일을 쓸 수 없다. 그래서 CLI 후처리로 두고, 검증한 원본과 올릴 파일을 **둘 다** 남겼다.

### [x] S8 — 샌드박스 자체 검증 `--verify-sandbox`

**한 것:** `until/runtime/sandbox_check.py`. 실제로 시도해 보고 막혀야 통과.
만들면서 실측으로 잡은 두 결함:
- **대조군이 없으면 거짓 합격.** 이 기계는 외부 연결이 원래 안 돼서, 격리 0인 통과
  래퍼에도 "네트워크 막힘 ✓"를 줬다. 샌드박스 **밖**에서 먼저 돌려 보고 밖에서도
  안 되면 `모름`으로 보고하게 고쳤다.
- **`-c` 인라인 코드는 셸 래퍼에서 깨진다.** `cmd /c`로 감싸니 여러 줄 코드가 망가져
  전부 '모름'이 됐다. 프로브를 파일로 쓰고 경로만 넘긴다.

### [x] S9 — Windows 실제 실행 (WSL2, 검증 완료)

**한 것:** `tools/until-sandbox.sh` — `unshare`만으로 작업공간 외 쓰기·네트워크 차단.
**추가 설치 불필요**(bubblewrap·firejail 안 깔아도 된다). Ubuntu 24.04 + WSL2에서
`--verify-sandbox` 3항목 통과, 가짜 CLI로 전체 경로 실제 프로세스 완주까지 확인.

**실측으로 잡은 두 결함:**
- 래퍼의 `sh -c` 안 `shift`가 명령 첫 단어를 먹었다(`exec: t.py: Permission denied`).
  `sh -c "..." arg0 args...`에서 `$@`는 이미 arg1부터다.
- 검증기가 `env={}`를 넘겨 샌드박스가 PATH로 `unshare`를 못 찾았다 → 최소 PATH를 준다.

**후속(2026-08-21 같은 날 해결):** `--allow-write` 인자를 추가했다. 환경변수로 하면
커널의 `sanitize_environment`가 걷어내서 래퍼까지 못 간다 — 그래서 인자다. 허용 경로만
쓰기 가능해지고 나머지·네트워크는 그대로 막히는 것을 실측 확인했다.
Docker 경로도 데몬을 띄워 확인했다: 격리 3항목 통과. 단 `{workspace}`가 호스트 경로로
치환돼 Windows 네이티브에서는 컨테이너 경로가 깨진다(WSL 안에서는 정상). 로그인 상태가
`--rm`으로 사라지는 문제가 남아 **WSL2 래퍼 쪽이 더 단순하다**.

### [x] S10 — 과제 유형 확대

**한 것:** `code`·`presentation`·`form` 런타임 + 보고서 런타임 산문 계열 6종 확대.
`hdl_lab`·`rmd_notebook`은 **의도적 미지원**(실행 엔진 없음 → 통과를 주면 수치 날조 승인).

**배운 것:**
- 발표 슬라이드 표기는 `## 슬라이드 N: 제목`이다(`presentation_export._SLIDE`).
  `## 제목`으로 쓰면 파서가 한 장도 못 읽는다 — 검증한 구조와 변환되는 구조가 달라진다.
- `measured_check`는 HDL·실험 단위 전용이라 "0.42초"·"3명"을 못 잡는다. 동결 모듈이라
  건드리지 않고 `runtime/grounding.py`에 유형별 패턴을 뒀다.
- 한글 단위 뒤에 ``를 쓰면 안 된다. `초`도 `였`도 단어 문자라 경계가 없어서
  "0.42초였다"가 통째로 안 걸렸다.
- **`plan.files`는 파일을 `touch`로 미리 만든다.** 그래서 플러그인들의
  `if not path.exists()` 스캐폴드가 전부 죽은 코드였다. 양식 런타임만 고쳤고
  (스캐폴드가 기능의 핵심), 보고서·코드 런타임은 무해해서 그대로 뒀다 —
  **다음 사람이 스캐폴드를 추가한다면 `exists()`만 보면 안 된다.**

### [x] S11 — 실행 엔진 (검증 명령 실제 실행)

`plan.steps`가 검증만 되고 실행되지 않던 구멍을 메웠다.
- `boundary.run_step` / `controller.run_steps` — **에이전트와 같은 격리·같은 세탁 환경**.
- `security.KERNEL_ALLOWED_COMMANDS` — 플러그인이 선언한 명령을 커널이 다시 거른다.
  셸(`sh`·`bash`·`cmd`)과 네트워크 도구는 천장에서 뺐다.
- 명령은 **에이전트가 돌기 전에** plan에 박힌다. 에이전트가 쓴 파일이 명령줄이 되는
  경로가 없는 것이 실행을 열어 준 전제다.
- 결과는 `observe_run` 훅으로 플러그인에 전달(기존 `validate` 시그니처 불변).
- 코드 런타임: pytest 기본, `spec["test_command"]`로 교체 가능.
  **실패는 차단, 못 돌림은 경고** — 섞으면 멀쩡한 코드를 고치게 된다.

**실측으로 잡은 버그:** 테스트가 만든 `__pycache__`가 재시도 라운드에서
'에이전트가 허용 범위 밖을 고쳤다'로 뒤집혀 나왔다(`workspace_escape`·
`agent_plan_scope` 오검출). 재시도 기준선을 **단계 실행 후 스냅샷**으로 바꿨다.

**라이브 확인(WSL2 + 실제 unittest):** 통과 코드 → `ready` + `run: succeeded`,
틀린 코드 → `blocked` + `tests_failed` **단독**(오검출 없음).

**아직 안 한 것:** `hdl_lab`·`rmd_notebook`은 여전히 미지원. 실행 엔진은 생겼지만
시뮬레이터·R 환경을 어떻게 준비·검증할지는 별개 설계다(계획서 Phase 6~7).

### [ ] S6 — 마무리

CHANGELOG Unreleased, `docs/FEATURES.md`, 스위트 수 갱신(`README`·`CLAUDE.md`·`FEATURES`),
전체 게이트 통과 후 커밋.

## [해결] `atomicio` 원자성 최후 폴백 (2026-08-21 수정)

병렬 테스트가 가끔 `test_atomicio`에서 실패한다. 이번 세션 실측: 병렬 실행 14회 중 1회
(`jobs=8`). 순차 실행(`-j 1`)과 단독 실행은 항상 통과한다. 원인을 잡았다.

`atomic_write_bytes`는 `os.replace`가 계속 실패하면 마지막에 `p.write_bytes(data)`로
폴백한다. 이건 **truncate-in-place**라서 그 순간 다른 리더가 길이 0을 본다 —
테스트가 잡은 `중간 상태 관측됨(길이): [0, 0, 0]`이 정확히 그것이다. 함수 docstring이
이미 "이 최후 폴백 경로에서는 '중간 상태 없음' 계약이 깨질 수 있다"고 적어 두고 있다.

왜 지금 더 자주 보이나: Windows에서 무잠금 리더가 대상 파일을 여는 찰나 `os.replace`가
`PermissionError`를 낸다. 재시도 예산은 빠른 500회 + 느린 20회×10ms(=0.2초)인데,
CPU가 포화되면 이 예산 안에 못 잡는다. 스위트가 늘어 경합이 커지자 재현률이 올라갔다.

**제품 영향(가설, 미검증):** `_persist_session`도 이 함수를 쓴다. 폴백이 발동한 찰나에
다른 요청이 세션 파일을 읽으면 잘린 내용을 보고 `session_store.decode()`가 None을
돌려준다 → 사용자에게는 "세션 만료"로 보인다. fail-closed라 데이터가 깨지진 않지만
작업을 잃는다. nonce·크레딧 경로(`billing`)도 같은 함수를 쓴다 — 그쪽 영향은 확인 필요.

**한 것(사용자 지시로 진행):** 위 3안을 그대로 적용했다.
1. 느린 재시도 예산 0.2초 → 2초(`_SLOW_RETRIES` 20→100, 간격 10ms→20ms).
2. 그래도 실패하면 `AtomicWriteError`(OSError 하위)를 던진다. **대상 파일은 손대지
   않은 채로 남고 tmp도 지운다.**
3. 폴백 진입 시 경고 로그.

**호출부 19곳 확인:** `_persist_session`(`except Exception`)·`adminboard`(`except OSError`)는
이미 삼킨다. `submit_nonce`는 예외가 곧 fail-closed(제출이 진행되지 않는다)라 안전.
`billing`·`profile`·`facts`·`tone`·`pg_webhook`은 전파되지만, 전파되는 게 맞다 —
저장이 안 됐는데 됐다고 하는 것보다 낫다.

**회귀 확인:** 병렬 실행 10회 연속 통과(그중 2회는 WSL 부하로 49s·77s까지 늘어난
상태 — 예전에 실패가 나던 바로 그 조건이다).

## 알려진 동작 — 연습(과거 과제) 경로 없음 (사용자 판단으로 미수정)

웹에는 '이미 한 과제로 다시 해보기'(연습 모드)가 있다: `inbox_policy.pick_practice`로
지난·제출한 과제를 고르고, `practice_audit`으로 사전 점검하고, 연습이면 제출 표면을
아예 그리지 않는다(`web.render_submit_ready`의 `practice_mode` 분기).

**런타임 CLI는 그 절반만 가져왔다.** eTL 연동 때 선택 정책(`pick_best`)은 옮겼지만
연습 경로는 안 옮겼다. 그래서:

- `--practice`가 없다. `--fast`는 미제출만 고르므로 **방학·개강 전처럼 할 과제가
  없으면 쓸 수 없다**(웹은 그때 쓰라고 연습 모드를 만들어 뒀다).
- **이미 제출한 과제를 `--etl-url`로 지정하면 "제출하러 가기" 링크가 그대로 뜬다**
  (실측). 웹이 막아 둔 상황을 CLI는 통과시킨다. 런타임이 제출을 하지는 않으므로
  (네트워크 POST 0회) 링크가 뜰 뿐이지만, 표시로는 틀렸다.
- `practice_audit` 사전 점검이 돌지 않는다.

**미수정 이유:** 사용자 판단(2026-08-21). 현재 CLI 사용자는 개발자 본인 한 명이고,
`--etl-url`로 **직접 지정**했을 때만 나오는 상황이라 실사용 위험이 낮다.
CLI에 다른 사용자가 생기면 그때 최소한 제출 가드부터 붙일 것.

## 검증 방법

```bash
PYTHONIOENCODING=utf-8 python run_tests.py      # 전 스위트
ruff check .
PYTHONIOENCODING=utf-8 PYTHONHASHSEED=0 TZ=Asia/Seoul python tools/check_determinism.py
```

라이브 흉내(실제 eTL 없이): `tests/test_runtime_etl.py`의 가짜 어댑터를 쓰거나,
이전 세션이 쓴 가짜 eTL 서버 패턴(Canvas REST 최소 구현 + `SNU_ETL_BASE` 치환)을 재현한다.

## codex로 넘길 때

`/codex-do`에 이 파일 경로와 다음 미완료 단계를 준다. 넘긴 뒤 **diff를 반드시 리뷰**하고
위 게이트를 직접 돌린다. 커밋은 리뷰 후에만.
