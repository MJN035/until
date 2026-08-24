# Until MCP 서버 — 도구 레퍼런스

Until은 **stdio JSON-RPC 2.0**(줄바꿈 구분) MCP 서버다. 진입점은 `python -m until.mcp_server`.
프로토콜 버전은 `2025-06-18`을 기본으로 협상한다.

```bash
python -m until.mcp_server --list-tools    # 도구 정의를 JSON으로 출력하고 종료
python -m until.mcp_server                 # stdio 서버 (에이전트가 붙는다)
```

---

## 전역 규칙

| 규칙 | 내용 |
|---|---|
| **LLM 호출** | **0건.** 어떤 도구도 모델을 부르지 않는다. 생성 기능은 노출하지 않는다 |
| **결정성** | 모든 도구가 같은 입력 → 같은 출력. `tools/check_determinism.py`가 강제한다 |
| **토큰** | `UNTIL_CANVAS_TOKEN` 환경변수로만 읽는다. **디스크에 쓰지 않는다** |
| **의존성** | 런타임 의존성 0개. MCP SDK를 쓰지 않는다 |
| **stdout** | 도구 실행 구간의 stdout은 stderr로 우회된다 — 하위 모듈의 `print` 한 줄이 프로토콜 스트림을 깨뜨리기 때문 |
| **오류** | 실패는 예외가 아니라 `isError` 결과로 돌아온다. 토큰이 없으면 크래시하지 않고 **무엇이 없는지 말한다** |

> until MCP는 LLM을 호출하지 않는다. 추론은 전부 붙인 쪽 에이전트가 한다.
> 서버 운영자의 API 키·크레딧이 소모되지 않으며, 이는 `tests/test_mcp_server.py`의
> 모듈 그래프 검사로 강제된다 — `until.mcp_server`를 import하는 것만으로 `until.llm`이
> 로딩되면 테스트가 빨간불이다(주석이 아니라 테스트가 지킨다). **eTL(LMS)에는 붙는다**
> — 여기서 말하는 건 "네트워크를 안 쓴다"가 아니라 "LLM을 호출하지 않는다"와
> "웹 서버(Render)를 경유하지 않는다" 둘뿐이다.

### 환경변수

| 변수 | 뜻 |
|---|---|
| `UNTIL_CANVAS_TOKEN` | Canvas REST 토큰 (권장 경로) |
| `UNTIL_CANVAS_BASE` | LMS 호스트 주소 |
| `UNTIL_WS_MODE` | Moodle Web Services 모드로 전환 |

---

## 토큰이 필요 없는 도구

`until_route` · `until_readiness` 두 개는 네트워크도 토큰도 쓰지 않는다.
**연결 전에 먼저 이 둘로 확인하는 것을 권한다.**

---

## `until_route` — 과제 유형 분류 *(토큰 불필요)*

제목·본문·첨부명만으로 처리 전략을 분류하고, **왜 그렇게 분류했는지**와 **무엇이 부족한지**를 함께 돌려준다.

**입력**

| 필드 | 타입 | 필수 | 설명 |
|---|---|:---:|---|
| `title` | string | ✅ | 과제 제목 |
| `description` | string | | 과제 본문 (있으면 정확도가 오른다) |
| `attachment_names` | string[] | | 첨부 파일명 목록 |
| `course_name` | string | | 과목명 |

**출력**

```jsonc
{
  "strategy": "lab_report_cycle",     // 처리 전략
  "reason": "...",                    // 그렇게 분류한 근거
  "required_evidence": ["..."],       // 이 유형이 반드시 요구하는 재료
  "questions": ["..."],               // 부족한 정보를 묻는 질문
  "actionable": true,                 // 제출할 것이 있는 과제인지
  "stage": "..."                      // 단계(있을 때)
}
```

---

## `until_readiness` — 초안 점검 *(토큰 불필요)*

**초안을 만들지 않는다.** 받은 텍스트를 검사만 한다.

**입력**

| 필드 | 타입 | 필수 | 설명 |
|---|---|:---:|---|
| `draft` | string | ✅ | 점검할 초안 본문 |
| `assignment_text` | string | | 과제 지시문 (주면 분량·마감까지 대조) |
| `title` | string | | 과제 제목 |

**출력**

```jsonc
{
  "headline": "...",                  // 한 줄 요약
  "n_warnings": 2,
  "items": [ { "kind": "length", "message": "분량 부족 — 요건 1500자, 현재 248자" } ],
  "n_decisions": 3,                   // 남은 [[DECISION]] 개수
  "decisions": ["관점·논지 선택", "..."],
  "crossed_boundary": false           // 사람이 정해야 할 것을 모델이 넘어가 정했는지
}
```

---

## `until_inbox` — 과제 목록 *(토큰 필요)*

마감 임박순 정렬. **성적부 열은 기본으로 빠진다.**

**입력**

| 필드 | 타입 | 기본 | 설명 |
|---|---|---|---|
| `status` | `all` \| `todo` \| `done` | `todo` | 미제출만 보는 것이 기본 |
| `kind` | `assignment` \| `gradebook` \| `all` | `assignment` | 성적부 열 제외가 기본 |
| `hide_past` | boolean | `true` | 마감 지난 과제 숨김 |
| `term` | string | | 학기로 거르기 (예: `2026-2`) |
| `limit` | integer | `50` | 1~200 |

**출력**

```jsonc
{
  "count": 3,
  "total_found": 109,
  "gradebook_rows": 47,               // 성적부 열로 걸러낸 수
  "filtered_out": 0,
  "items": [{
    "assignment_id": "...", "course_id": "...", "course_name": "...",
    "title": "...", "due_at": "2026-09-14T23:59:00", "dday": "D-3",
    "urgent": true, "past_due": false, "submitted": false,
    "actionable": true, "kind": "assignment", "url": "..."
  }]
}
```

> **`kind`를 반드시 보라.** LMS는 `중간고사`·`출석 점수`·`M3`를 과제와 같은 자료구조로 준다.
> 실제 계정 전수 대조에서 148항목 중 **47건(32%)**이 성적부 열이었다.

---

## `until_assignment` — 과제 1건 명세 *(토큰 필요)*

**입력**: `url` (string, 필수) — eTL 과제 페이지 주소

**출력**

```jsonc
{
  "assignment_id": "...", "course_id": "...", "course_name": "...",
  "title": "...", "due_at": "...", "page_url": "...",
  "spec": {
    "goal": "...", "course": "...",
    "required_sections": ["..."],     // 요구된 섹션
    "citation_style": "APA",
    "min_chars": 1500,
    "requires_citation": true
  },
  "content_elements": [ /* 요구 항목을 원자 단위로 분해 */ ],
  "length_target": { "unit": "chars", "min": 1500, "max": 0,
                     "mode": "min", "per_item": false,
                     "raw": "1500자 이상", "describe": "..." },
  "deadline": { "due": "2026-09-14T23:59:00", "had_year": true,
                "time_str": "23:59", "extended": false, "raw": "..." },
  "route": { /* until_route와 같은 형태 */ },
  "attachment_count": 2,
  "skipped": ["..."],                 // 읽지 못한 첨부
  "capture_warnings": ["..."],
  "body_excerpt": "…(발췌)"
}
```

> **`spec`에는 `requirements`·`constraints`·`deliverable`이 없다.** 그 필드들은 LLM Understanding이 채우는 것이라
> LLM 0 경로에서는 나오지 않는다. **없는 키를 빈 배열로 지어내지 않았다** — 요구사항의 원자 분해는 `content_elements`가 한다.

> **`attachment_count`와 `skipped`를 보라.** 실제 과제 101건 중 **43건(43%)의 지시문이 120자 미만**이고,
> 지시의 본체가 첨부에 있다("첨부파일 양식 참고"). 첨부를 못 읽었다면 그 사실이 `skipped`에 남는다.

---

## `until_materials` — 관련 강의자료 *(토큰 필요)*

**입력**

| 필드 | 타입 | 필수 | 기본 | 설명 |
|---|---|:---:|---|---|
| `url` | string | ✅ | | eTL 과제 페이지 주소 |
| `top` | integer | | `5` | 상위 몇 건 (1~20) |
| `with_text` | boolean | | `false` | 본문 발췌까지 내려받기 (상위 3건까지) |

**출력**

```jsonc
{
  "course_id": "...", "count": 5, "total_materials": 38,
  "items": [{ "name": "3주차 강의노트.pdf", "score": 4,
              "matched": ["연산증폭기", "주파수응답"],
              "url": "...", "excerpt": "…(발췌)" }]
}
```

---

## `until_series` — 내 지난 제출물 교차참조 *(토큰 필요, Canvas 모드)*

`3주차 소감문` ↔ `5주차 소감문` 같은 **시리즈**, `서론 작성` ↔ `서론 수정` 같은 **단계 줄기**를 찾는다.

**입력**

| 필드 | 타입 | 필수 | 기본 |
|---|---|:---:|---|
| `title` | string | ✅ | |
| `course_id` | string | ✅ | |
| `k` | integer | | `2` (1~5) |

**출력**

```jsonc
{
  "series_key": "소감문", "stage_stem": "...",
  "matched_by": "series",             // series | stage | none
  "count": 2, "scanned": 41,
  "items": [{ "title": "...", "submitted_at": "...", "chars": 812, "excerpt": "…(발췌)" }]
}
```

---

## `until_brief` — 주차 브리프 *(토큰 필요)*

과목의 주차 공지·브리프를 결정적으로 발췌한다.

**입력**

| 필드 | 타입 | 필수 | 설명 |
|---|---|:---:|---|
| `course_id` | string | ✅ | eTL 과목 id |
| `week` | integer | | 주차 (없으면 최근) |
| `limit` | integer | | 최대 건수 |

---

## `until_semester` — 학기 전체 상태 *(토큰 필요)*

한 번의 호출로 학기 전체를 본다. 에이전트가 "이번 주 뭐 해야 해?"에 답할 때 첫 호출로 쓰라고 만든 도구다.

**입력**

| 필드 | 타입 | 필수 | 설명 |
|---|---|:---:|---|
| `term` | string | | 학기 (없으면 최근) |

**출력**

```jsonc
{
  "term": "2026-2",
  "n_courses": 13, "n_assignments": 101, "n_gradebook_rows": 47,
  "n_todo": 3, "n_urgent": 1, "n_past_due": 95,
  "next_due": { "title": "...", "course_name": "...", "due_at": "...", "dday": "D-3" },
  "courses": [{ "course_id": "...", "course_name": "...",
                "n_assignments": 15, "n_todo": 1, "next_due": "..." }]
}
```

---

## `until_control_tower` — 제출 가능 판정 *(토큰 필요)*

과제 1건을 지금 제출할 수 있는 상태인지 판정한다. 필수 첨부·팀 역할·분량·과목 정책을 한 자리에서 본다.

**입력**

| 필드 | 타입 | 필수 | 설명 |
|---|---|:---:|---|
| `url` | string | ✅ | eTL 과제 페이지 주소 |
| `draft` | string | | 지금까지 쓴 초안 (주면 분량까지 대조) |
| `team_role_confirmed` | boolean | | 조별 과제에서 내 역할이 확정됐는지 |

**출력**

```jsonc
{
  "assignment_id": "...",
  "submit_state": "blocked",          // 제출 가능 상태
  "findings": [{
    "severity": "block",              // block | warn | info
    "code": "required_files_missing",
    "message": "필수 첨부 2건 중 0건",
    "basis": ["근거 발췌 또는 출처 URL"]
  }],
  "provenance_count": 3               // 판정이 근거로 삼은 출처 수
}
```

> **`basis`가 비어 있는 판정을 믿지 마라.** 근거 없이 나온 `block`은 버그다.

---

## 오류 형태

```jsonc
{ "isError": true,
  "content": [{ "type": "text", "text": "eTL 토큰이 없습니다. UNTIL_CANVAS_TOKEN을 설정하세요." }] }
```

토큰 없음·권한 없음·과제 문서를 못 읽음 등은 전부 이 형태로 돌아온다. 서버는 죽지 않는다.

---

## 구현 상태

| 도구 | 상태 |
|---|---|
| `until_inbox` `until_assignment` `until_materials` `until_route` `until_readiness` `until_series` | 구현 완료 |
| `until_brief` `until_semester` `until_control_tower` | 이 문서가 명세다 (구현 중) |

이 문서의 스키마가 계약이다. 구현이 여기서 벗어나면 **문서가 아니라 구현을 고친다.**
