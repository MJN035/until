# 텔레메트리 동의 UI · KV 미러 · llm_tokens 계측 — 설계 (2026-08-05)

9월 베타 전 필수 항목 3건. 수집 데이터는 기존 fail-closed 비식별 스키마
(`until/telemetry/schema.py`) 그대로이며, 이 설계는 **동의 절차·유실 방지·원가
계측**만 추가한다. 동의 모델은 **명시적 opt-in**으로 확정(사용자 선택).

## 1. 텔레메트리 고지 + opt-in 동의 UI

### 동의 상태 저장
- `until/telemetry/consent.py` (신규, 결정적·LLM 0):
  - `get_consent(uid) -> bool | None` — None=기록 없음(미고지).
  - `set_consent(uid, granted: bool)` — `_until_work/users/<uid>/consent.json`
    `{"telemetry": bool, "at": "<iso>", "notice_version": 1}` 원자적 쓰기.
- KV 미러: `_mirror_user`에 `consent:<uid>` (TTL 1년, hist와 동일 계보),
  `_hydrate_user`에 복원 추가. 재시작·인스턴스 교체에도 동의 상태 유지.

### 고지 화면 흐름 (stdlib 서버, 클라우드 모드 전용)
- `_begin_request`: 베타 게이트 통과 뒤, **CLOUD ∧ `UNTIL_TELEMETRY=1` ∧
  동의 기록 없음 ∧ 면제 경로 아님** → `render_consent_notice()` 200 응답으로
  요청 종료(베타 게이트와 같은 패턴, POST면 본문 드레인 후 응답).
  - 면제 경로: `/healthz`, `/beta`, `/about`, `/asset/*`, `/admin*`, `/consent`.
- 고지 내용: 수집하는 것(비식별 집계 신호 — 과제 유형·결정 응답률·경고 해소·
  소요시간·토큰량), 수집하지 않는 것(과제 원문·초안·결정 질문/답변·첨부 —
  8-gram fail-closed 차단), 목적(베타 품질 개선), 언제든 변경 가능.
- 버튼 둘 다 동등한 스타일: "동의하고 시작" / "동의 안 하고 시작" —
  `POST /consent` (`choice=yes|no`) → 저장 → `/` 리다이렉트. **어느 쪽이든 앱
  사용에 제약 없음**(다크패턴 금지).
- `GET /consent`: 현재 상태 확인·변경 페이지(철회 = opt-out). 홈 푸터에
  "데이터 설정" 링크.

### 방출 게이트 (fail-closed)
- `web._telemetry_emit`(stdlib·ASGI 공용 브리지)에서: 유효 uid가 `"local"`이
  아니면(=멀티유저) `get_consent(uid) is True`일 때만 방출. 기록 없음·False 모두
  미방출. 로컬 단일사용자 모드는 기존 env 게이트 유지(운영자=사용자).

## 2. 웹 텔레메트리 KV 미러링

- `cloudkv.TTL_TELEM = 180일` 추가. 키: `telem:<uid>`(활성 파일),
  `telem:<uid>:1`(로테이션 파일) — uid당 최대 2키로 상한.
- `emit_sync(..., mirror=False)`: 로컬 append 성공 후 `mirror=True`면 파일
  전체 바이트를 `put_async`(기존 FIFO 워커·비차단). `emit_best_effort`도 전달.
- `web._telemetry_emit`이 `mirror = CLOUD ∧ hydrated_ok`를 계산해 전달 —
  하이드레이션 미확정 요청의 미러 금지(감사 14회차 계보와 동일한 이유).
- `_hydrate_user`: 로컬에 `telemetry.jsonl`(.1 포함) 없으면 KV에서 복원 —
  인스턴스 재시작 후 append가 KV 사본을 절단하지 않게.
- **관리자 보드 집계**: `adminboard.load_web_telemetry(users_dir)`가 uid별
  `telemetry.jsonl`(+`.jsonl.1`)을 전부 읽고, 클라우드에선 KV `telem:` prefix도
  병합. 레코드 dedup 키 = `run_id`(레코드마다 고유). 기존 코퍼스 CLI 텔레메트리
  (`load_telemetry`)와 합쳐 `summarize_telemetry`에 전달.

## 3. llm_tokens_in/out 계측

- `until/llm/meter.py` (신규): `MeteredClient(inner, usage: dict)` —
  `complete()` 위임 후 `llm_calls`/`llm_tokens_in`/`llm_tokens_out` 누적
  (스레드 안전 lock). 코퍼스 러너 `_MeteredClient`와 같은 계보.
- `Result.llm_usage: dict | None = None` 정식 dataclass 필드 추가.
  `session_store`에 직렬화/복원 등록(`_plain` dict, 구세션은 `.get` 폴백).
- `pipeline.run()`: usage dict 하나로 본 패스·경량 패스 클라이언트를 감싸고
  `result.llm_usage`에 부착. `finalize`·`suggest_decision_answers`·
  `review_result`(보조 호출)도 같은 dict(세션의 `result.llm_usage`)에 누적.
- `telemetry/web.build_record`: `result.llm_usage` 있으면 `llm_calls`·
  `llm_tokens_in`·`llm_tokens_out` 충전, 없으면(구세션) 기존대로 null.
  mock 백엔드는 0. 정수 필드라 8-gram 검사와 무관.

## 테스트

- 동의: 저장/조회 왕복, 기록 없음→미방출, False→미방출, True→방출,
  고지 화면 1회 표시→POST 후 미표시, `/consent` 변경(철회) 반영.
- KV 미러: fake transport로 emit→`telem:` put 확인, 하이드레이션 복원,
  로테이션 파일 미러, adminboard KV 병합·run_id dedup.
- 계측: mock 백엔드로 run→`llm_usage.llm_calls ≥ 1`, finalize 후 누적 증가,
  세션 저장/복원 왕복, 텔레메트리 레코드에 정수 충전, 구세션 null 유지.
- 기존 45스위트 무회귀(`python run_tests.py`).

## 범위 밖

- 사용자별 원가 대시보드 표시(관리자 보드 총합만), 중앙 수집 서버,
  ASGI 앱의 자체 동의 화면(클라우드 엔트리포인트는 stdlib 서버 유지 중).
