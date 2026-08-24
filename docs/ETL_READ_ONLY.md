# eTL 읽기 전용 원칙 (협상 대상 아님)

**until은 eTL(Moodle)에 대해 읽기 전용이다.** 토큰 권한상 과제 최종 제출·퀴즈
응시·포럼 글쓰기·쪽지 발송 같은 쓰기 함수가 호출 "가능"하지만, until은 이것을
**코드 레벨에서 영구 차단**한다. 과제 자동 제출·퀴즈 자동 응시는 until의 '경계선'
철학(사람의 판단이 필요한 지점 직전까지만) 정면 위반이고, 학사 사고 위험이다.

## 어떻게 강제하는가 — allowlist 방식

`until/capture/sources/moodle_ws.py`:

- **`READ_ALLOWLIST`** — until이 호출할 수 있는 읽기 함수 목록(현재 17종).
- **`WRITE_DENYLIST`** — 토큰상 가능하지만 영구 미사용인 쓰기 함수(명시 8종).
- 모든 Moodle WS 호출은 `MoodleWsClient.call()`을 거치고, `call()`은 맨 먼저
  **`assert_read_only(wsfunction)`** 를 통과해야 한다.
  - allowlist에 **없는** 함수(쓰기 함수 포함)는 `WriteFunctionBlocked` 예외로
    막힌다 — **네트워크 요청이 생성조차 되지 않는다**(테스트 `test_write_call_makes_no_request`).
  - `WRITE_DENYLIST`의 함수는 allowlist에 실수로 추가돼도 이중 방어로 거부된다.
  - 시작 시 `assert`로 두 목록이 겹치지 않음을 검증(모순 설정 조기 발견).

즉, **쓰기 함수를 호출하는 코드 경로 자체가 존재하지 않는다.** 새 쓰기 함수를
쓰려면 사람이 이 파일의 allowlist를 직접 고쳐야 하고, denylist에 오른 함수는
그렇게 해도 막힌다.

함수 allowlist는 **호출할 함수**를 통제할 뿐, 허용된 함수 응답 안의 개별 필드까지
자동으로 안전하게 만들지는 않는다. 따라서 필드 수준 개인정보 통제는 파서 경계에서
별도로 시행한다. 예를 들어 포럼 작성자의 `userfullname`/`display_name`은
`Announcement` 생성 전에 폐기하고, 응답에 명시적인 역할 근거가 있을 때만
`instructor | ta | student | unknown` 역할 라벨로 보존한다. 역할 근거가 없으면
`unknown`이다. 공지 본문 자유 텍스트 안에 작성자가 직접 적은 실명은 아직 이 통제의
범위 밖이며 후속 본문 비식별화 과제로 남긴다.

## 영구 차단 쓰기 함수 (8종)

| 함수 | 왜 금지 |
|------|---------|
| `mod_assign_save_submission` | 과제 초안을 서버에 저장 |
| `mod_assign_submit_for_grading` | 과제 최종 제출 |
| `mod_quiz_start_attempt` | 퀴즈 응시 시작 |
| `mod_quiz_process_attempt` | 퀴즈 응답 제출 |
| `mod_quiz_finish_attempt` | 퀴즈 제출 완료 |
| `mod_forum_add_discussion` | 포럼 새 글 |
| `mod_forum_add_discussion_post` | 포럼 답글 |
| `core_message_send_instant_messages` | 쪽지 발송 |

## 읽기 함수 지형 조사

`MoodleWsClient.get_site_info()`(= `core_webservice_get_site_info`)를 최초 1회
호출하면 이 토큰에 실제로 활성화된 함수 목록이 나온다. 헬퍼:

- `activated_functions(info)` — 활성 함수 전체
- `allowed_activated(info)` — 그중 until이 실제로 쓸 수 있는 읽기 함수
- `blocked_activated(info)` — 그중 until이 **의도적으로 쓰지 않는** 쓰기 함수(투명성 보고용)

## 토큰 취급

토큰(`UNTIL_ETL_WS_TOKEN`, 없으면 `UNTIL_CANVAS_TOKEN` 폴백)은 **POST 바디**로만
전송한다 — URL 쿼리스트링에 넣지 않아 서버 로그·브라우저 히스토리·리퍼러에 토큰이
새지 않는다.
