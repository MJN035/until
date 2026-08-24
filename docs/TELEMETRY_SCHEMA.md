# 텔레메트리 스키마 v1.2

**목적:** 팀원·베타 사용자의 **원문을 서버가 읽지 않고도** 알고리즘을 개선한다.
`_until_work/corpus_validation.jsonl` 원장을 공유 규격으로 승격시킨 것이다.

관리자 보드와 개인정보 경계가 다르다. 텔레메트리는 공유 가능한 과제 단위 신호라 자유
문자열을 전면 금지한다. 사람 단위 관리자 보드는 코호트 분석을 위해 `grade`, `college`,
`major_area`의 고정 열거형만 허용하지만 실명·학번·이메일·전화번호는 저장·표시하지
않는다. 두 저장소는 합치지 않는다. 공유·반출되는 텔레메트리에는 관리자 `uid`를
기록하지 않는다.

## 0. 원칙 (협상 대상 아님)

1. **파이프 분리** — 원문 파이프(사용자 소유)와 신호 파이프(텔레메트리)는 물리적으로
   분리한다. 신호 파이프에는 원문·제목·성적이 **구조적으로 들어갈 수 없다**.
2. **Build-up, not strip-down** — 전체 레코드를 만든 뒤 민감 필드를 지우는 방식은
   금지. `TELEMETRY_ALLOWLIST`에 있는 필드만 **새 dict에 담아 올린다**. 필드가 새로
   생겨도 기본값은 "안 나감"이다. (`READ_ALLOWLIST`와 같은 패턴)
3. **Fail-closed** — 전송 직전 원문 대조 스캔을 통과 못 하면 전송하지 않는다.

---

## 1. 지금 원장을 그대로 쓸 수 없는 이유 (실제 결함 3개)

### ① `readiness_warning_details` — 가장 큰 구멍

현재 원장에 이런 게 그대로 들어간다:

```json
{"label": "마감", "message": "D+41 (지남) · 마감 2026-06-24 오전 8시"}
{"label": "인용", "message": "근거 미인용 — 자료 1개를 줬지만 본문에 [자료N] 인용이 없습니다"}
```

`message`는 **자유 문자열**이라 미래에 원문 인용구가 섞일 경로가 열려 있고, 마감
절대시각은 과목·과제를 특정하는 준식별자다.

**→ 텔레메트리는 `*_warning_labels`만 싣는다. `*_warning_details`는 영구 제외.**
마감은 절대시각 대신 `deadline_bucket`(D-N 구간)으로 대체.

### ② 지문이 소금 없이 SHA-256 — 사실상 역산 가능

```python
raw = f"{row.get('course_id')}:{row.get('assignment_id')}"
hashlib.sha256(raw.encode()).hexdigest()[:12]
```

`course_id`·`assignment_id`는 6자리 정수다. 키스페이스가 좁아 **전수 대입으로 원본
복원이 가능**하다. 로컬 감사 원장으로는 문제없지만, 공유되는 순간 지문이 아니게 된다.

**→ 공유용은 HMAC + 팀 공유 소금.** (§4)

### ③ 측정에 필요한 필드가 없다

8월 실험(P2 컨텍스트 A/B, P5 시간)은 지금 원장으로 못 돌린다. `algo_version`,
`context_mode`, 소요시간이 없어서 **어느 버전에서 뭘 측정했는지 사후에 알 수 없다.**
알고리즘 동결 규율은 이 필드가 있어야 강제된다.

---

## 2. 스키마 — 나가는 필드

### 2.1 레코드 식별

| 필드 | 타입 | 예 | 비고 |
|---|---|---|---|
| `schema_version` | enum | `"1.2"` | 스키마 변경 추적. 소비자는 1.0·1.1·1.2를 버전별로 해석 |
| `salt_version` | enum | `"1"` | HMAC 소금 세대. 기본 `"1"` |
| `run_id` | str | `"a3f1c2..."` | 실행 1회 = 1 run. 랜덤 16hex |
| `user_key` | str | `"9c4e...`" | 설치 시 1회 생성 랜덤 16hex. **학번·이메일 파생 금지** |
| `assignment_key` | str\|null | `"bfae8b4f6e8e"` | eTL은 HMAC 지문 (§4), 수동 입력은 null |
| `date` | str | `"2026-08-14"` | **날짜만.** 시각은 제출 시점 추론 가능 → 제외 |

### 2.2 버전·실행 조건 (동결 규율의 핵심)

| 필드 | 타입 | 예 | 왜 필요 |
|---|---|---|---|
| `algo_version` | str | `"1.2.0"` | 릴리스(패키지) 버전 — 버전별 정확도 비교의 축 |
| `algo_gate` | enum | `v0.1` \| `v0.2` | **실행 시점의 알고리즘 동결 게이트**(`UNTIL_ALGO_VERSION`) |
| `git_sha` | hex(7~40) | `"4f9a1c2"` | 정확한 재현. 식별 지문이 아닌 빌드 재현 필드 |
| `context_mode` | enum | `full` \| `no_etl_context` \| `bare` | **P2 A/B의 통제 변수** |
| `pipeline_mode` | enum | `legacy` \| `unit` | 기존 이중 실행 |
| `backend` | enum | `mock` \| `live` | mock 결과를 실측으로 오독 방지 |
| `parser_backend` | enum | `"basic"` | |

`algo_version`과 `algo_gate`는 서로 다른 질문에 답한다. 게이트는 배포물이 아니라
**런타임 환경변수**라 릴리스 번호에 남지 않는다 — 같은 릴리스에서 v0.1로 돈 실행과
v0.2로 돈 실행이 `algo_version`만 보면 똑같아 보인다. 8월 동결·측정은 `algo_gate`로만
갈리므로 두 값을 함께 싣는다.

값은 **웹·CLI 두 생산자가 `until/telemetry/schema.py`의 `algo_gate()` 하나만 부른다.**
이 함수가 게이트 경로 `until.config.algo_version()`을 재사용하고(별도 env 파싱 금지)
`ALGO_GATE_VALUES` 밖이면 `None`을 낸다. 생산자마다 따로 읽고 따로 거르면 언젠가 값이
갈리고, 갈라진 원장은 게이트 기준 교차 집계를 조용히 망친다 — 로직을 복제하지 말 것.
게이트 표기가 늘어나면(v0.3 등) `ALGO_GATE_VALUES`와 `_ENUMS`를 함께 늘린다.
CLI 러너는 run 시작 시 한 번 구한 값을 모든 행에 내려보내, 한 run 안의 레코드가
서로 다른 게이트를 갖지 않게 한다.

### 2.3 라우팅 (P1·P4 — 분류 정확도)

| 필드 | 타입 | 예 |
|---|---|---|
| `strategy` | enum(12) | `staged_writing` |
| `unit_strategy` | enum(12) | `staged_writing` |
| `task_type` | enum | `essay` |
| `actionable` | bool | `true` |
| `route_agreement` | bool | 두 경로 일치 여부 |
| `route_confidence` | float | 라우터가 점수를 내면 |
| `unmatched_route` | bool | 12종 어디에도 안 맞음 → **새 유형 탐지 신호** |

#### v0.2 측정 4축 (schema 1.1, `COURSE_ALGORITHMS_2026F` §7)

| 필드 | 타입 | 예 | 무엇을 알려고 하는가 |
|---|---|---|---|
| `route_strategy` | enum(15) | `lab_report_cycle` | 과제별 라우팅 분포(기존 12 + 신설 3) |
| `route_source` | enum\|null | `rule` \| `profile_hint` \| `llm_inferred` \| `clarify` | 이 라우트를 **무엇이** 정했나 |
| `lab_stage` | enum | `pre` \| `notebook` \| `result` \| `""` | 실험 3단계 중 어디 |
| `evidence_missing` | list[enum] \| null | `["user_experience"]` | 어떤 근거가 자주 비는지 |

`route_strategy`는 `strategy`와 같은 원천(`AssignmentRoute.strategy`)이지만 축이
다르다 — `strategy`는 실행 결과 집계용, `route_strategy`는 §7 라우팅 분포용이라
소비자가 스키마 1.0 레코드와 섞어 집계할 때 두 이름이 모두 있어야 한다.

`route_source`가 null이면 **"측정 안 됨"**이다(파이프라인을 거치지 않은 결과, 구세션).
`rule`과 섞어 세면 안 된다. `evidence_missing`도 마찬가지로 null(= plan이 없는 legacy
파이프라인이라 신호를 만들 수 없음)과 `[]`(= 측정했고 빈 근거 없음)를 구분한다.

`evidence_missing`의 원소는 `requirements.EVIDENCE_KINDS`의 고정 4종뿐이다.
`route.required_evidence`·`reason`·`questions`의 한국어 자유 문구는 **절대 싣지 않는다**
(§3 금지, `test_route_free_text_never_reaches_record`가 고정).

#### 배선 실측 — 어디서 생산되는가 (2026-08-21)

allowlist 등재와 실제 방출은 다르다. 그 전까지 `route_source`는 `profile_hint` 한 값만,
`lab_stage`·`evidence_missing`·`route_strategy`는 웹 레코드에 아예 없었다.

| 필드 | 생산자 | 방출부 |
|---|---|---|
| `route_source` | `until/pipeline.py`가 라우트를 확정하는 **네 갈래**에서 `spec["route_source"]`에 심는다 | `telemetry/fields.py::route_source` |
| `lab_stage` | `AssignmentRoute.stage`(`context/assignment_router.py`) | `telemetry/fields.py::lab_stage` |
| `evidence_missing` | `unit.plan.items` 중 `action == "decision"` → `element_id` → `unit.elements`(SkeletonSlot)의 `evidence_kind` | `telemetry/fields.py::evidence_missing` |
| `route_strategy` | `AssignmentRoute.strategy` | 웹은 `telemetry/web.py::build_record`, 페르소나 이벤트 원장은 `persona/events.py`(별도 원장) |

`route_source`의 네 갈래(`until/pipeline.py`, 2.0.0~2.0.2 구간):

1. 결정적 규칙이 잡음(`spec_clarification`이 아님) → `rule`
2. `course_profiles` 힌트 적용(v0.2) → `profile_hint`
3. LLM 추정이 인용 검증까지 통과 → `llm_inferred`
4. 확정 못 하고 묻기로 남음(능동형 후보 제시 포함) → `clarify`

**열거형 밖 값은 `telemetry/fields.py`가 먼저 버린다.** `assert_no_source_leak`는 마지막
방어선이지 1차 필터가 아니다. 파생 로직은 `algo_gate()`와 같은 이유로 한 모듈에만 둔다 —
웹과 CLI가 각자 파생하면 언젠가 값이 갈리고, 갈라진 원장은 교차 집계를 조용히 망친다.

**고쳐졌다(2026-08-21):** `profile_hint` 갈래는 한동안 프로덕션에서 도달하지 못했다 —
라우팅은 `spec["course"]`를 보는데 `extract_task_spec`의 스키마에 `course`가 없고
(`additionalProperties: false`), 호출자들은 `run()`이 **끝난 뒤** 화면 표기용으로
`result.spec["course"]`에 과목명을 넣고 있었다. `run(course_name=...)`으로 라우팅 전에
넘기도록 고쳤고, 시임 없이 실경로로 켜지는지를 시험이 고정한다
(`test_profile_hint_fires_through_the_real_call_path`).
**따라서 2026-08-21 이전 레코드의 `profile_hint` 0%는 '안 쓰였다'가 아니라
'못 켜졌다'로 읽어야 한다** — 그 경계로 잘라서 집계할 것.

CLI 코퍼스 러너(`run_corpus_validation.py`)는 아직 이 네 필드를 싣지 않는다 — 웹 원장만
v0.2 축을 갖는다.

### 2.4 실행 결과

| 필드 | 타입 | 비고 |
|---|---|---|
| `status` | enum | `passed` \| `failed` \| `excluded` |
| `failures` | list[enum] | `["route_mismatch:staged_writing->essay"]` — **enum 조합만.** 자유문자열 금지 |
| `checks` | list[enum] | 기존 그대로 |
| `capture_warnings` | int | |
| `readiness_warning_labels` | list[enum] | `["마감","인용","근거"]` — **labels만** |
| `unit_readiness_warning_labels` | list[enum] | |
| `guard_passed` / `unit_guard_passed` | bool | |
| `decisions` / `unit_decisions` | int | |
| `unit_count` | int | |

### 2.5 규모 — 원문은 버킷, 산출물은 실수치

| 필드 | 타입 | 비고 |
|---|---|---|
| `spec_chars_bucket` | enum | `<500` \| `500-2k` \| `2k-8k` \| `8k+` — 원문 길이는 준식별자 |
| `intro_files` | int | 개수만 |
| `intro_file_exts` | list[enum] | `["pdf","xlsx"]` — **허용된 확장자만. 파일명 금지** |
| `draft_chars` | int | 우리 산출물이므로 실수치 OK (측정에 필요) |
| `unit_draft_chars` | int | |
| `deadline_bucket` | enum | `D-7+` \| `D-3~6` \| `D-1~2` \| `D0` \| `overdue` |

### 2.6 정답셋 대조

| 필드 | 타입 | 비고 |
|---|---|---|
| `has_reference_submission` | bool | 존재 여부만 |
| `reference_kinds` | list[enum] | `["docx"]` — 형식만 |
| `reference_parse_failures` | list[enum 조합] | `"pdf:UnicodeDecodeError"` — 확장자+예외 클래스명만 |
| `reference_format_match` | bool | |

### 2.7 측정 (P2·P5 — 8월 실험 전용)

| 필드 | 타입 | 비고 |
|---|---|---|
| `elapsed_ms` | dict[str,int] | 단계별 `{"capture":820,"pipeline":4100,...}` |
| `llm_calls` / `llm_tokens_in` / `llm_tokens_out` | int | 원가 모델의 근거 |
| `edit_ratio` | float | 사용자 수정량 = `levenshtein(초안, 최종)/len(초안)`, 소수 2자리. **텍스트는 절대 안 나감** |
| `edit_ops` | dict[str,int] | `{"insert":12,"delete":3,"replace":7}` |
| `user_rating` | int(1-5)\|null | 사용자 자가평가 |
| `voice_match` | enum(`yes`,`no`)\|null | VoiceProfile 적용 결과가 내 말투와 맞았는지 |
| `user_reported_minutes` | int\|null | 자가 기록 소요시간 |

> `edit_ratio` 하나가 "초안이 얼마나 쓸 만했나"의 가장 값싼 대리 지표다. 성적을
> 안 받아도 품질을 잰다.

### 2.8 결정 로그 (DECISION 응답)

| 필드 | 타입 | 비고 |
|---|---|---|
| `decision_total` | int | 초안에 남긴 `[[DECISION]]` 총 개수 |
| `decision_answered` | int\|null | 사용자가 실제로 답한 수. 출처가 없으면 null |
| `decision_partial` | int\|null | 답했으나 길이·형식이 불충분. 판정이 없으면 null |
| `decision_skipped` | int\|null | 무응답 제출 이벤트가 확인된 수. 없으면 null |
| `decision_response_rate` | float\|null | answered / total, 소수 2자리. total=0 또는 answered 미계측이면 null |
| `decision_median_seconds` | int\|null | 결정 1건당 응답 소요 중앙값. 시작 시각 미계측이면 null |
| `decision_kinds` | list[enum] | 기존 `classify_decision()`의 6개 유형만 사용 |
| `ai_suggestion_offered` | int\|null | 제안 제시 수 |
| `ai_suggestion_accepted` | int\|null | provenance가 확인된 그대로 수용 수 |
| `ai_suggestion_edited` | int\|null | provenance가 확인된 수정 수용 수 |
| `ai_suggestion_rejected` | int\|null | 명시적 거부 수 |
| `warning_shown` | list[enum]\|null | 사용자에게 실제 노출된 readiness 경고 라벨 |
| `warning_resolved` | list[enum]\|null | 재생성·수정 전후 비교로 해소된 경고 라벨 |

결정 유형은 새 분류기를 만들지 않고 이미 제품에서 사용하는 `가치판단`, `관점·논지`,
`진로·경험`, `취향·스타일`, `범위·선택`, `고유 판단`만 허용한다. 현재 코퍼스
검증은 사용자 상호작용을 수행하지 않으므로 `decision_total`과 `decision_kinds`만
채우며 나머지는 추정하지 않고 null로 둔다.

### 2.9 웹 과제 생애주기

| 필드 | 타입 | 비고 |
|---|---|---|
| `stage` | enum | `draft` \| `final` \| `export` |
| `source` | enum | `etl` \| `manual` |
| `user_seconds` | int\|null | 초안 완료부터 final/export까지의 체류 초. draft는 null |
| `revision_count` | int | 최초 final은 0, 같은 세션의 재생성마다 증가 |

레코드 단위는 요청이 아니라 과제 한 건의 생애주기 지점이다. 집계자는
`(user_key, assignment_key, stage, salt_version)`으로 중복 제거하고 같은 stage가
반복되면 마지막 레코드를 사용한다. 수동 붙여넣기·업로드는 내용 기반 식별자를 만들지
않고 `assignment_key: null`로 방출하므로 사용자(정확히는 브라우저) 단위로만 집계한다.
웹 방출은 `UNTIL_TELEMETRY=1`일 때만 켜지며 uid별 로컬
`_until_work/users/<uid>/telemetry.jsonl`에 기록한다.

---

## 3. 절대 나가지 않는 것 (DENYLIST)

| 항목 | 왜 |
|---|---|
| 과제 원문·명세·첨부 본문 | 파이프 분리의 전제 |
| 생성 초안 본문 / 최종 제출물 본문 | " |
| 과제 제목, 과목명, `course_id`, `assignment_id` 원값 | 직접 식별 |
| 학번, 이름, 이메일, eTL user_id | 직접 식별 |
| **성적, 교수·조교 코멘트, 루브릭 점수·코멘트** | `parse_my_feedback()` 산출물 전량 |
| eTL 토큰, 세션 쿠키 | 자명 |
| 파일명, 디렉터리 경로 | 파일명에 이름·학번이 흔함 |
| URL (`myetl.snu.ac.kr/courses/...`) | 과목·과제 직접 노출 |
| 마감 **절대시각** | 준식별자 → 버킷으로 대체 |
| `readiness_warning_details[].message` | 자유 문자열 = 원문 누출 경로 |
| **결정 질문 텍스트·사용자 답변 텍스트** | 가장 사적인 관점·가치판단·진로 데이터. 개수·유형·시간만 허용 |
| 자유서술 필드 일체 | 스키마에 없는 문자열은 전부 금지 |

**규칙 한 줄: 텔레메트리의 모든 문자열 값은 열거형 또는 구조가 고정된 값(hex 지문,
날짜, SemVer, git SHA, 확장자+예외 클래스명)이다. 자유 문자열은 없다.** 식별에 쓰는
`run_id`·`user_key`·`assignment_key`는 12자리 이상 hex이며, 7자리 git SHA는 빌드
재현용이라 식별 지문 규칙과 분리한다.

---

## 4. 지문 설계

```python
assignment_key = hmac.new(PROJECT_SALT, f"{course_id}:{assignment_id}".encode(),
                          hashlib.sha256).hexdigest()[:12]
```

- `UNTIL_PROJECT_SALT` — 팀 공유 비밀. **저장소·텔레메트리 밖**(`.env`, 배포 시 시크릿).
- 같은 소금이므로 **같은 과제 = 같은 키** → 3인 교차 집계가 된다
  ("이 과제에서 3명 다 `route_mismatch`" 같은 발견이 가능).
- CLI의 `user_key`는 설치 시 랜덤 생성한다. 학번에서 파생하면 소금이 있어도 링크된다.

### 4.1 웹 다중 사용자 `user_key`

CLI의 `_until_work/.user_key`는 설치 단위라 웹 서버에서 그대로 쓰면 모든 베타 사용자가
같은 값으로 합쳐진다. 웹·클라우드 모드는 다음처럼 별도 소금으로 UID를 가명화한다.

```python
user_key = hmac.new(TELEMETRY_SALT, uid.encode(), hashlib.sha256).hexdigest()[:16]
```

- `UNTIL_TELEMETRY_SALT`는 사용자 가명화 전용 비밀이며 과제 지문용
  `UNTIL_PROJECT_SALT`와 별개다. 웹 방출 시 둘 중 필요한 소금이 없으면 예외를 내고
  레코드를 만들지 않는다. 무소금·설치 키 폴백은 두지 않는다.
- 실험 중 소금을 바꾸면 `salt_version`도 반드시 올린다. 서로 다른
  `salt_version`의 레코드는 같은 `user_key` 문자열처럼 보여도 서로 다른 사용자
  공간으로 집계한다. 버전을 올리지 않은 소금 교체는 종단 데이터를 오류 없이 조용히
  끊으므로 운영 변경 절차에서 금지한다.
- CLI 코퍼스 방출은 기존 `_until_work/.user_key`의 무작위 16 hex를 계속 사용한다.
- 같은 서버 소금 아래에서는 같은 UID가 같은 `user_key`가 되어 사용자 단위 집계가
  가능하지만, 텔레메트리에는 UID 원값이나 대응표를 넣지 않는다.

여기서 UID는 계정이 아니라 익명 브라우저 쿠키다. 따라서 `user_key`의 실제 단위는
**사용자(per-user)가 아니라 브라우저(per-browser)**다. 같은 사람이 노트북과
데스크톱을 쓰면 서로 다른 두 키가 되고, 쿠키를 삭제해도 새 키가 된다. 이 값으로
리텐션·재방문 사용자 수를 해석하면 안 된다. 관리자 보드의 `token_fp`를 가져오면
동일인을 더 오래 판별할 수 있지만, 텔레메트리에 사용하면 두 저장소를 조인하는 식별자가
되므로 금지한다. 웹 `user_key`는 과제 생애주기 stage 중복 제거와 동일 브라우저 안의
단기 집계에만 사용한다.

**보장 범위의 한계:** 서버 운영자는 관리자 UID와 `UNTIL_TELEMETRY_SALT`를 모두
가지므로 UID 후보에 HMAC을 적용해 `uid ↔ user_key`를 역연결할 수 있다. 두 소금을
분리해도 서버 내부 운영자로부터 연결을 막지는 않는다. 이 설계가 보장하는 것은
**공유·반출된 `telemetry.jsonl` 단독으로는 관리자 보드 UID와 연결할 수 없다**는
범위까지다. 서버 내부 비연결성이나 운영자 비식별성을 과장해 주장하지 않는다.

**트레이드오프(명시):** `assignment_key`가 공유되면 "누가 누구와 같은 과목을 듣는다"는
사실이 원장 안에서 드러난다. 3인 실험은 서로 아는 사이라 무해. 9월 베타에서 과목 단위
집계가 불필요해지면 사용자별 소금으로 전환한다.

---

## 5. 강제 방식 — allowlist를 코드로

`until/telemetry/schema.py`:

```python
TELEMETRY_ALLOWLIST: frozenset[str] = frozenset({...})  # §2 전 필드

DENY_SUBSTRING_SOURCES = ("spec_text", "draft_body", "attachment_text")

def to_telemetry(record: dict, *, sources: dict) -> dict:
    """전체 레코드 → 텔레메트리. allowlist에 있는 필드만 새 dict에 담는다."""
    out = {k: record[k] for k in TELEMETRY_ALLOWLIST if k in record}
    assert_no_source_leak(out, sources)   # fail-closed
    return out
```

`assert_no_source_leak` — 방출 직전 런타임 검사:

- `out`의 모든 문자열 값이 열거형 화이트리스트 또는 hex 지문 패턴인가
- 원문에서 뽑은 8-gram 샘플이 `out`의 어떤 문자열에도 등장하지 않는가
- 위반 시 `TelemetryLeakBlocked` 예외 — **네트워크 요청이 생성되지 않는다**

### 필요한 테스트 (기존 `test_write_call_makes_no_request` 대응물)

| 테스트 | 검증 |
|---|---|
| `test_telemetry_only_allowlisted_fields` | allowlist 밖 필드가 레코드에 있어도 안 나감 |
| `test_telemetry_contains_no_source_text` | 실제 코퍼스 1건으로 원문 8-gram 미검출 |
| `test_telemetry_no_free_strings` | 모든 문자열이 enum \| hex |
| `test_telemetry_leak_makes_no_request` | 누출 감지 시 요청 미생성 |
| `test_fingerprint_requires_salt` | `PROJECT_SALT` 없으면 예외(무소금 폴백 금지) |
| `test_grade_fields_never_present` | `grade`/`rubric`/`comments` 키 부재 |
| `test_decision_text_never_present` | 결정 질문·답변 텍스트 미방출 + 누출 대조 source 포함 |
| `test_decision_rate_null_when_no_decisions` | total=0이면 rate가 0.0이 아닌 null |
| `test_algo_gate_emitted_per_run` | 실행 시점 게이트가 레코드에 남음 (`test_telemetry_web.py`) |
| `test_algo_gate_never_free_string` | 게이트 오타·미래 값이 자유 문자열로 안 샘 (`test_telemetry_web.py`) |
| `test_cli_telemetry_carries_algo_gate` | CLI 코퍼스 원장도 게이트를 실음 (`test_corpus_validation.py`) |
| `test_cli_and_web_algo_gate_share_one_source` | 두 생산자가 같은 함수 객체를 부름 (`test_corpus_validation.py`) |
| `test_route_source_{rule,clarify,llm_inferred,profile_hint}_*` | 네 갈래가 각각 자기 값을 냄 (`test_telemetry_web.py`) |
| `test_route_source_null_when_pipeline_did_not_set_it` | 미측정·열거형 밖은 null (rule로 오염되지 않음) |
| `test_lab_stage_carries_route_stage_under_v02` | `AssignmentRoute.stage`가 그대로 실림 |
| `test_lab_stage_empty_on_frozen_v01` | v0.2 게이트 뒤의 것이 v0.1에서 안 켜짐 |
| `test_route_free_text_never_reaches_record` | `required_evidence`·`reason`·`questions` 한국어 문구 미방출 |
| `test_evidence_missing_lists_decision_evidence_kinds` | DECISION으로 남은 요소의 kind 집합 = 방출값, 정렬·중복 없음 |
| `test_evidence_missing_null_without_unit_plans` | plan 없는 legacy는 `[]`가 아니라 null |
| `test_measurement_fields_survive_session_roundtrip` | 복원된 세션도 같은 네 값(`route.stage` 직렬화 포함) |

---

## 6. 예시 레코드

```json
{
  "schema_version": "1.2",
  "run_id": "a3f1c2d4e5b60789",
  "user_key": "9c4e7a1b2d3f4e5a",
  "assignment_key": "bfae8b4f6e8e",
  "date": "2026-08-14",

  "algo_version": "1.2.0",
  "algo_gate": "v0.1",
  "git_sha": "4f9a1c2",
  "context_mode": "full",
  "pipeline_mode": "legacy",
  "backend": "live",

  "strategy": "staged_writing",
  "unit_strategy": "staged_writing",
  "task_type": "essay",
  "actionable": true,
  "route_agreement": true,
  "unmatched_route": false,

  "route_strategy": "staged_writing",
  "route_source": "rule",
  "lab_stage": "",
  "evidence_missing": ["user_experience"],

  "status": "passed",
  "failures": [],
  "capture_warnings": 0,
  "readiness_warning_labels": ["마감"],
  "unit_readiness_warning_labels": ["마감", "인용", "근거"],
  "guard_passed": true,
  "unit_guard_passed": true,
  "decisions": 3,
  "unit_decisions": 1,
  "unit_count": 1,

  "spec_chars_bucket": "500-2k",
  "intro_files": 0,
  "intro_file_exts": [],
  "draft_chars": 471,
  "unit_draft_chars": 485,
  "deadline_bucket": "overdue",

  "has_reference_submission": true,
  "reference_kinds": ["docx"],
  "reference_parse_failures": [],
  "reference_format_match": true,

  "elapsed_ms": {"capture": 820, "pipeline": 4100, "readiness": 90},
  "llm_calls": 3,
  "llm_tokens_in": 8200,
  "llm_tokens_out": 1400,
  "edit_ratio": 0.18,
  "edit_ops": {"insert": 12, "delete": 3, "replace": 7},
  "user_rating": 4,
  "user_reported_minutes": 35
}
```

---

## 7. 회색지대 — 판단을 내려둔 것

| 항목 | 위험 | 결정 |
|---|---|---|
| `strategy` 분포 | `rmd_notebook`+`team_project` 조합이면 수강 과목 추정 가능 | 3인 단계는 허용. 100명 넘으면 재검토 |
| `elapsed_ms` | 사용 시각대 추론 | 소요시간만, 절대시각 없음 → 허용 |
| `assignment_key` 공유 | 동일 수강 사실 노출 | §4 트레이드오프대로 허용, 베타에서 재검토 |
| `user_reported_minutes` | 자가보고라 부정확 | 보조 지표로만. 주 지표는 `elapsed_ms`+`edit_ratio` |

---

## 8. 적용 체크리스트

**8월 (3인, 로컬 실행)**

- [x] `until/telemetry/schema.py` + 테스트 6종
- [x] `run_corpus_validation.py`에 `--emit-telemetry` 추가 (기본 off)
- [ ] `UNTIL_PROJECT_SALT` `.env` 배포 (`.gitignore` 확인)
- [x] `--no-feedback` 플래그: 팀원 수집 경로에서 피드백 필드 API 요청 안 함
- [ ] `algo_version` 동결 태그 — 8월 내내 고정
- [ ] 팀원은 자기 노트북에서 실행, `telemetry.jsonl`만 공유

**9월 (베타, 웹앱)**

- [x] `_until_work/web_sessions/*.pkl` → 서명 JSON 교체 (pickle 역직렬화 취약점)
- [ ] 원문 파이프 TTL 정책 확정 (또는 E2E)
- [ ] 텔레메트리 저장소를 원문 저장소와 물리 분리
- [ ] 수집 항목 고지 화면 — 이 문서 §2/§3 표를 그대로 사용자에게 노출

---

## 9. 부수 효과

이 문서는 심사역이 IR 첫 줄에 남긴 **"암호화 등 구체적인 방식 설명"**에 대한 답변
초안이기도 하다. §3 표와 §5 테스트 목록은 슬라이드 한 장으로 그대로 옮겨진다.

---

## 10. v1.0 구현 결정

- 텔레메트리 파일은 `--emit-telemetry PATH`가 있을 때만 생성한다. 네트워크 전송은
  없고, 기존 `corpus_validation.jsonl`은 로컬 감사 원장으로 포맷을 그대로 유지한다.
- `--context-mode`는 `full`, `no_etl_context`, `bare` 세 enum만 받는다. 코퍼스 러너의
  직접 과제 첨부는 eTL 외부 컨텍스트가 아니므로 `no_etl_context`에서도 유지하고,
  `bare`만 `spec.md` 단독 실행으로 통제한다. `full`은 과제별
  `etl_context/context.md`가 반드시 있어야 하며, 없으면 `missing_context_bundle`로
  실패시켜 이름만 full인 비교군을 만들지 않는다.
- 코퍼스 검증의 `--backend`는 `mock | local | anthropic`이며 기본은 `mock`이다.
  live 백엔드는 legacy+unit 예상 호출·토큰을 먼저 출력하고, `--yes`가 없으면 확인을
  요구한다. `LLMResult`의 실제 `tokens_in`/`tokens_out`을 모든 보조·unit 호출까지
  합산해 텔레메트리에 기록한다.
- `elapsed_ms`는 `capture`, `pipeline`, `readiness`, `unit_pipeline`,
  `unit_readiness`, `total`의 정수 밀리초만 싣고 절대시각은 싣지 않는다.
- CLI `user_key`는 `_until_work/.user_key`의 랜덤 16 hex다. 웹 `user_key`는
  `UNTIL_TELEMETRY_SALT`로 UID를 HMAC한 16 hex를 사용한다. 어느 경로도 학번·이메일·
  eTL ID를 입력으로 받지 않는다. 과제 지문은 `UNTIL_PROJECT_SALT`가 없으면 생성하지
  않으며 웹 사용자 키도 전용 소금이 없으면 생성하지 않는다.
- 팀원 프리셋 `run_etl_corpus.py`는 `--no-feedback`이 기본이며 Canvas 요청 자체에서
  `submission_comments`와 `rubric_assessment` include를 제외한다. 개인 웹 자동학습은
  별도 로컬 기능으로 기존 동작을 유지한다.
- 원문 8-gram 검사는 JSON 키 이름이 아니라 방출되는 문자열 **값**을 대상으로 한다.
  키는 고정 `TELEMETRY_ALLOWLIST` 자체이므로 원문에서 유래할 수 없다.
- `_ENUMS`, 고정 failure/reference-failure 코드는 원문에서 파생될 수 없는 사전이므로
  우연한 동어 충돌을 막기 위해 8-gram 대상에서 제외한다. hex·date·version·git SHA는
  허용 형식 안에 내용 해시가 숨을 수 있어 계속 원문과 대조한다.
- 결정 질문·답변 텍스트는 출력 후보가 아니라 `assert_no_source_leak()`의 `sources`에
  반드시 포함한다. 코퍼스 실행에는 답변이 없으므로 빈 목록을 명시해도 질문 원문은
  항상 대조하며, 향후 웹 연결 시 `_ANSWERS` 값도 같은 source 채널에만 넣는다.
