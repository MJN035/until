# 과목별 알고리즘 1 — 주차별 질의 배정

## 문제

`N주차 질의`는 강의 전에 제출하므로 지난 강의 내용이나 아직 듣지 않은 강연을
근거로 쓸 수 없다. 이 과목은 eTL 과제의 `NO DUE`와 별개로 공지에서 “배정된
교수에게 수업 전날 월요일 17시까지”를 요구하고, 교수 배정은 외부 Google Sheets
질의순번표에 있다.

## 결정적 연결

`과제 제목의 N주차 → 관련 공지의 Sheets 링크 → 프로필 학번과 같은 셀의 교수 열
→ 서울대 전기정보공학부 공식 교수 페이지의 연구 분야 → 질문 후보`

- 학번은 표 매칭 단계에서만 쓰고 SourceDoc·LLM·UI에 원문을 전달하지 않는다.
- Canvas 프로필의 `sis_user_id/login_id/integration_id`가 정확한 학번 형식일 때만
  Until 프로필의 빈 `student_id`를 보충한다. 이메일에서는 추측하지 않는다.
- 최신 질의순번표를 강의계획서보다 우선한다.
- 표의 수업일에서 하루를 빼고 17시를 실제 마감으로 사용한다.
- 공식 연구 분야를 못 찾으면 세부 연구 내용을 생성하지 않는다.
- 링크·학번 매칭이 없거나 중복이면 교수 배정을 추측하지 않고 기존 흐름으로 폴백한다.

## 네트워크 경계

읽기 전용 공개 자료만 허용한다.

- `docs.google.com`: 공개 Sheets CSV(`gviz/tq?tqx=out:csv`)
- `ece.snu.ac.kr`: 공식 전임교수 목록·상세 프로필

응답은 2MB로 제한하며 eTL 토큰을 외부 호스트로 전달하지 않는다.

## 질문 생성 규칙

- `[질의 배정]` 자료의 담당 교수 한 명만 대상으로 한다.
- 아직 듣지 않은 강의 내용을 들었다고 가정하지 않는다.
- 공식 공개 연구 분야에서 전망·적용 사례·진로/창업·방법론·한계/난제 프레임의
  열린 질문 후보를 만든다.
- 무엇이 실제로 궁금한지는 사용자가 선택·수정하도록 `[[DECISION]]`으로 남긴다.

## 코드·테스트

- 파서·매칭: `until/context/inquiry_assignment.py`
- Canvas 공개 자료 조회·공지 링크 보존: `until/capture/sources/canvas_api.py`
- 웹 연결·표시: `until/web.py`
- 프롬프트 경계: `until/execution/prompts.py`
- 회귀: `tests/test_inquiry_assignment.py`, `tests/test_profile.py`,
  `tests/test_announcements.py`
