# 제출 게이트(C안) 설계 — 사람 확인 원클릭 제출

> 상태: **설계안(코드 미반영)**. 작성 2026-08-14.
> 대상: Canvas eTL(민준 실제 LMS)에 과제를 **작성까지 끝낸 최종본**을 사람 확인 후
> 제출하는 경로. 자동 제출(A안)은 경계선 철학과 배치되어 채택하지 않는다.
> 관련: 불변 규칙 6(소스 접속은 어댑터 뒤), 🚫 수치 날조 금지, `moodle_ws.py`의
> 읽기 전용 allowlist/denylist 철학.

---

## 0. 한 줄 결론

until은 지금 **읽기 전용**이다(`moodle_ws.py` denylist, `canvas_api.py` GET 전용).
이 설계는 "작성까지 끝낸 최종본"을 **사람이 확인 화면에서 명시적으로 승인할 때만**
Canvas에 제출하는 경로를 연다. 자동 제출은 없다. 실 POST는 4겹 방어를 전부 통과할
때만 열리고, 기본은 **dry-run**(보낼 요청만 렌더, 네트워크 0)이다.

## 1. 왜 C안인가 (설계 결정 기록)

- **A. 완전 자동 제출** — until이 최종본을 만들고 바로 제출. Draft 경계선(사람의 최종
  판단·제출 소유권)을 지운다. 학술 부정 위험 최고. **기각.**
- **B. 초안 스테이징(Moodle draft)** — `mod_assign_save_submission`으로 초안만 저장.
  Canvas는 API에 초안 상태가 없어 이 안이 깔끔히 안 된다. Canvas가 실 eTL이므로 보류.
- **C. 사람 확인 원클릭 제출** — until이 본문·마감·게이트 결과를 보여주고, 하드 블록이
  하나도 없을 때만 활성화되는 '제출' 클릭을 사람이 눌러야 제출. **채택.**
- **D. 현행(읽기+생성+내보내기, 사람이 수동 제출)** — C의 안전 하한선(무장 off = D와 동일).

## 2. 아키텍처 — 3개 유닛

```
Result(최종본) ─▶ build_submission_plan() ─▶ SubmissionPlan ─▶ [사람 검토·확인] ─▶ submit_canvas()
                    (submission_gate.py)        {allowed, blocks[],              (canvas_submit.py)
                     결정적·LLM 0               warnings[], content,              dry-run 기본,
                                                target, confirm_nonce}            실 POST는 4겹 통과 시만
```

### 2.1 `until/execution/submission_gate.py` — 안전 코어(결정적·LLM 0)

- 입력: `Result`(최종본), 대상 `AssignmentRef`(course_id·assignment_id·submission_types·
  마감), 옵션 `submission_type`.
- 출력: `SubmissionPlan` 데이터클래스
  - `allowed: bool` — 하드 블록이 0일 때만 True
  - `blocks: list[GateFinding]` — 차단 사유(코드+사람이 읽는 메시지)
  - `warnings: list[GateFinding]` — 경고(제출은 가능, 확인 화면에 강조)
  - `content: str` — **제출될 정확한 본문**(최종본에서 유도, 마커 없는 완성 텍스트)
  - `target: SubmitTarget` — course_id, assignment_id, submission_type, base_url
  - `confirm_nonce: str` — 이 plan 내용 해시에 바인딩된 1회용 확인 토큰(§4)
  - `content_hash: str` — `sha256(content + target)` 12자리, nonce 바인딩·감사용
- 이 유닛은 네트워크·LLM·파일 IO 없음(불변 규칙 3 계열). 완전 오프라인 테스트 가능.

### 2.2 `until/capture/sources/canvas_submit.py` — 격리된 쓰기 경로

- 읽기 어댑터(`canvas_api.py`)와 **완전히 별개 파일**. 쓰기 능력은 여기에만 존재한다
  (감사 지점). `canvas_api.py`는 GET 전용 그대로 둔다.
- `submit(plan: SubmissionPlan, confirm_token: str, *, armed: bool = False,
  http=None) -> SubmissionReceipt`
  - **기본(dry-run)**: 보낼 HTTP 요청(method·url·body)을 `SubmissionReceipt`로 반환.
    네트워크 0. 어떤 인자에서도 4겹이 안 맞으면 여기로 폴백한다.
  - **live POST**: §4의 4겹을 전부 통과할 때만. Canvas
    `POST /api/v1/courses/{cid}/assignments/{aid}/submissions`,
    `submission[submission_type]=online_text_entry`+`submission[body]=content`
    (또는 파일 업로드 3단계는 v2 백로그).
  - 인증: `UNTIL_CANVAS_TOKEN`(읽기와 같은 토큰, 쓰기 스코프 보유). env로만.
- `http` 주입 인자로 테스트는 FakeHTTP를 넣어 네트워크 없이 요청 정확성 검증.

### 2.3 웹/CLI 확인 화면 — 프로토타입은 최소

- 확인 화면: 제출될 본문(content) 전문 + 대상 과제·마감 + 게이트 결과(blocks 빨강·
  warnings 노랑). **하드 블록이 하나라도 있으면 '제출' 버튼 비활성.**
- '제출' 클릭 → 그 plan의 `confirm_nonce`를 실어 `submit(armed=UNTIL_SUBMIT_ARMED)`.
- 프로토타입 범위: CLI `--submit-plan`(dry-run 렌더)까지 필수, 웹 버튼 배선은 설계만.

## 3. 안전 게이트 — 하드 블록 / 경고

### 3.1 하드 블록(하나라도 걸리면 `allowed=False`)

| 코드 | 조건 | 근거 |
|---|---|---|
| `measured_ban` | strategy=hdl_lab 또는 lab_report_cycle(result)인데 `spec.material_gap`(실측 근거 없음) | 🚫 수치 날조 금지 — 지어낸 값 제출=학술 부정 |
| `integrity_gate` | `spec.integrity_gate` 설정(자필·손글씨 규정) | 자동 제출 자체 차단, 사람이 손으로 내야 함 |
| `guard_failed` | `result.guard.passed == False` | 경계선 가드 미통과 |
| `deadline_passed` | `result.deadline`가 오늘보다 과거 | 마감 지난 제출 방지(사람 재확인 전 차단) |
| `length_unmet` | readiness 분량 status가 short/over, 또는 양식 mismatch | 요건 미달 제출 방지 |
| `raw_decision_marker` | 제출 본문에 literal `[[DECISION` 잔존 | **구조적**: 완성 안 된 텍스트(마커 원문)는 제출 불가 |
| `assignment_mismatch` | plan.target.assignment_id ≠ Result가 유래한 과제 id | 다른 과제함 오제출 방지 |
| `type_unsupported` | target.submission_type이 과제의 허용 submission_types 밖 | Canvas 400 사전 차단 |

### 3.2 경고(제출 가능, 확인 화면에 강조)

- `unresolved_decisions` — **미해결 결정(개념적)**. 최종본은 결정을 본문에 녹이므로
  literal 마커가 없어도 통과하나, "남은 판단이 있었다"를 사람에게 강하게 표시하고
  최종 승인을 받는다. (사용자 결정: 하드 블록 아님, 강한 경고.)
- `citation_missing` — 자료를 줬는데 본문 인용 없음(readiness 근거 경고).
- `already_submitted` — 제출 상태 조회 결과 이미 제출됨 → 재제출 전 재확인.

## 4. 무장(arming)·확인 다층 방어

실 POST가 열리려면 **4겹을 전부** 통과해야 한다. 하나라도 없으면 dry-run이 기본이다.

1. **배포 레벨 옵트인** — `UNTIL_SUBMIT_ARMED=1`(기본 미설정=off). **클라우드 라이브엔
   설정하지 않는다**(eTL 토큰·팀원 성적 취급 정책과 동일, 로컬 단일 사용자만).
2. **게이트 통과** — `plan.allowed == True`(하드 블록 0).
3. **1회용 확인 nonce** — 게이트가 plan마다 발급. 사람이 확인 화면에서 '제출'을
   누를 때만 넘긴다. **plan.content_hash에 바인딩**(본문 1바이트 변경 시 무효) +
   **단일 사용**(발급·소비 원장으로 리플레이 차단).
4. **명시적 armed 인자** — 호출부가 `submit(..., armed=True)`를 의도적으로 넘겨야 함.
   자동 경로 없음.

추가 안전장치:
- **감사 로그** `_until_work/submit_audit.jsonl` — 모든 시도(dry-run 포함) append:
  시각·course/assignment id·content_hash·allowed·dry|live·결과 코드. 되돌리기 어려운
  동작의 흔적.
- **멱등 방지** — `already_submitted` 경고 + 재제출 전 재확인.
- **개발 원칙** — 이 프로토타입 개발·테스트 중 **실 eTL에 live POST를 실행하지 않는다.**
  dry-run으로만 검증하고, 실제 무장 전송은 사용자가 env·확인을 직접 넣어 실행하는 몫.

## 5. 데이터 흐름 (예시)

```
1. 사용자가 eTL에서 과제 수집 → 초안 → 결정 답 → finalize(최종본)  [기존]
2. build_submission_plan(result, assignment)
   → 게이트 8종 + 경고 3종 판정, content 추출, content_hash·nonce 발급
3. 확인 화면: content 전문 + 마감 + blocks(빨강)/warnings(노랑)
   - blocks 있으면 '제출' 비활성 → 사용자는 until로 돌아가 보완
   - blocks 0이면 '제출' 활성(경고는 표시하되 클릭 가능)
4. '제출' 클릭 → submit_canvas(plan, nonce, armed=UNTIL_SUBMIT_ARMED)
   - armed off → dry-run 렌더(보낼 요청 표시), 감사 로그 dry
   - 4겹 통과 → live POST → SubmissionReceipt, 감사 로그 live+결과
```

## 6. 테스트 (전부 오프라인, live POST 0)

- **게이트 테스트**: 각 하드 블록 8종 개별 1케이스 + 전부 통과 → allowed=True.
- **경고 테스트**: 미해결 결정 → warnings에 표면화, allowed 유지(차단 아님).
- **무장 거부 테스트**: armed off → dry-run / nonce 불일치 → 거부 / plan 차단인데
  armed → 거부 / content 변조로 hash 불일치 → nonce 무효.
- **dry-run 렌더 테스트**: FakeHTTP로 보낼 method·url·body 정확성(네트워크 0).
- **감사 로그 테스트**: 시도마다 1줄 append, 필드 스키마 검증.
- `--backend mock`·키/인터넷 없이 항상 통과(불변 규칙 2). `run_tests.py`에 등록.

## 7. 범위 밖 / 백로그

- 파일 업로드 제출(online_upload 3단계) — v2. 프로토타입은 online_text_entry.
- Moodle 초안 저장(B안) — Canvas 확정 후 별도.
- 웹 확인 화면 실제 배선 — 설계만, 프로토타입은 CLI dry-run까지.
- 클라우드 라이브 무장 — 정책상 열지 않음(로컬 단일 사용자 전용).

## 8. 미해결·리스크

- **Canvas 제출 상태 재조회**: 멱등 방지용 `already_submitted`는 제출 상태 GET(읽기
  어댑터)로 확인 — 실 API 응답 형태 확인 필요(라이브 미검증, dry-run 설계).
- **토큰 쓰기 스코프**: 읽기 토큰이 쓰기까지 되는지는 무장 전송 첫 1회에서만 실측 가능
  (사용자가 직접). 설계는 스코프 있다고 가정하되 401/403을 사람이 읽는 에러로.
- **마감 경계**: `deadline_passed`는 D-day 파싱(기존 deadline.py)에 의존 — 마감 시각
  단위 오차 가능성. 경계 근처는 사람 확인을 신뢰.
