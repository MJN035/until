# 설계 — eTL 자동 탐색 + 관련자료 자동수집 (P9~P12)

> 목표: "과제 URL을 직접 줘야 하는" 지금 → **"eTL을 알아서 보고, 할 과제를 찾아,
> 관련 자료까지 모아서, 초안+결정+프롬프트로 정리"**.
> 지금도 엔진(초안→경계선→프롬프트)은 있다. 빠진 건 **입구(자동 탐색·수집)**.

## 1. 무엇이 빠졌나 (현재 → 목표)
| 단계 | 지금 | 목표 |
|---|---|---|
| 과제 찾기 | 과제 **URL을 직접** 입력 | 내 과목·과제를 **자동 목록**(마감 임박 우선) |
| 관련자료 | 내 PC 폴더(`--course-materials`) | eTL **코스 파일/모듈에서 자동 수집** + 과제와 매칭 |
| 초안·결정·프롬프트 | ✅ 됨 | 그대로 재사용 |

## 2. 접속 경로 — 둘 다, 같은 추상화 뒤에
기존 `BrowserAdapter`/`Source` 패턴을 유지하고 **탐색(Discovery) 능력만 추가**한다.
파이프라인 코어는 접속 방식을 모른다(불변규칙 6).

- **A. Canvas REST API (토큰)** — 주 경로. 안정적·빠름. 학생이 토큰 1개 발급(계정>설정).
- **B. 브라우저 SSO (Playwright)** — 폴백. 토큰 불필요하나 페이지 구조 변화에 약함.

```
[A. CanvasApiAdapter]  ┐
                        ├──> EtlInbox(탐색) ──> AssignmentRef[]
[B. PlaywrightAdapter] ┘                          │
                                                  ▼
                          관련자료 수집(코스파일/모듈) ──ranked──> Context
                                                  ▼
                        기존 파이프라인: 초안 → 경계선(결정) → 제안 프롬프트
```

## 3. 새로 필요한 것
### 3.1 탐색 (Discovery)
- 내 과목: `GET /api/v1/courses?enrollment_state=active`
- 과목별 과제: `GET /api/v1/courses/{cid}/assignments?bucket=upcoming` (마감 임박)
  - 대안: `GET /api/v1/users/self/upcoming_events`, planner `GET /api/v1/planner/items`
- 산출: `AssignmentRef{title, course, url, due_at, submitted}` 목록

### 3.2 관련자료 자동수집 (Materials)
- 후보: 코스 파일 `GET /courses/{cid}/files` (이미 파서 있음) + 모듈 `GET /courses/{cid}/modules`
- **순위화: 기존 `context/retrieval.py`(키워드 중첩) 재사용** — 과제 spec과 매칭해 상위 N건만 Context로.
- 다운로드는 임시 폴더(개인정보 — 저장소/캐시에 안 남김).

### 3.3 표면 (Web)
1. 홈: 토큰 입력(또는 서버 env) → **[내 과제 불러오기]**
2. 과제 목록: 표(과목·과제·마감·상태) → 하나 선택
3. (자동) 관련자료 N건 수집 → 초안 페이지(기존) + "이 과제에서 모은 자료 N건" 표시
4. 결정 입력 → 최종본 (기존 흐름)

## 4. 코드 지도 (추가/수정)
- `capture/sources/canvas_api.py`: `list_courses()`, `list_assignments(cid, bucket)`,
  `list_modules(cid)` 추가. (assignment·files 파서는 이미 있음)
- `capture/sources/discovery.py` (신규): `EtlInbox` — 어댑터로부터 `AssignmentRef[]` 조립.
- `context/etl_materials.py` (신규): 코스 자료 수집 → `retrieval`로 순위화 → `SourceDoc[]`.
- `web.py`: 목록 화면(`/inbox`) + 선택→수집→초안(`/pick`).
- 전부 fixture로 오프라인 테스트(네트워크는 `urlopen` 대체).

## 5. 단계 계획 (작게, 테스트 동반)
- **P9** Canvas API 탐색: 과목·과제 목록 파서 + `EtlInbox` (오프라인 fixture 테스트).
- **P10** 관련자료 자동수집 + 순위화(`retrieval` 재사용) → Context 주입 (오프라인 테스트).
- **P11** 웹 흐름: 토큰 → 과제 목록 → 선택 → 자동수집 → 초안 (HTTP 왕복 테스트).
- **P12** 브라우저 SSO 탐색(Playwright) 동등 기능 — 토큰 없는 학생용 폴백.

각 단계: 작은 변경 → 테스트(`run_tests.py`) 통과 → 커밋.

## 6. 불변·안전
- 경계선 원칙 그대로(자동수집이 늘어도 사람 판단은 `[[DECISION]]`로).
- 토큰·세션·자료 본문을 저장소/캐시에 남기지 않음. 다운로드는 임시 폴더.
- mock·오프라인 테스트는 키 없이 항상 통과.

## 7. 라이브 검증에 필요한 것 (빌드 후)
- A 경로: 학생 **Canvas 액세스 토큰**(`UNTIL_CANVAS_TOKEN`).
- B 경로: 브라우저에서 **MySNU SSO 로그인**(사용자 클릭).
