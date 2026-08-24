# Canvas 과제 페이지 fixture (LearningX 파서 P1용)

서울대 eTL은 **Canvas LMS** 기반(`myetl.snu.ac.kr`). 라이브 페이지에서 확인한 구조:

| 요소 | 선택자 |
|---|---|
| 과제 제목 | `div.assignment-title` (안 `h1.title`) |
| 과제 본문 | `div.description.user_content` |
| 첨부 | `a.instructure_file_link[data-api-endpoint]` (href `/courses/{cid}/files/{fid}/download`) |
| 과목 ID | `window.ENV.COURSE_ID` (JS 전역) |

`assignment_page.html`은 이 구조의 샘플(개인정보 없음). Codex는 이걸로
`capture/sources/canvas.py`의 순수 파서 `parse_canvas_assignment(html, base_url)`를 짜고
단위 테스트(`tests/test_canvas_parse.py`)를 붙이면 된다.

## 제품화 권장: Canvas REST API
브라우저 파싱보다 깔끔한 길 — Canvas 공식 API.
- 과제: `GET /api/v1/courses/{courseId}/assignments/{assignmentId}` → `{name, description(HTML), due_at, ...}`
- 첨부/파일: `GET /api/v1/courses/{courseId}/files` 또는 description 내 file 링크의 `data-api-endpoint`
- 인증: 사용자가 **계정 > 설정 > 새 액세스 토큰** 발급 → `Authorization: Bearer <token>`.
  (비번 불필요, 학생이 직접 토큰 발급. Moodle보다 학생 토큰 접근이 쉬움.)
