# eTL 추출 확대 + 질문 세분화 — 설계 제안 (2·3·4·6)

> 상태: **핵심 추출 계층 구현 완료(2026-07-17)**. 팀원(기여자 C) 방향 피드백 반영.
> 0번(쓰기 함수 차단)은 별도 완료 → `docs/ETL_READ_ONLY.md`, `moodle_ws.py`.

## ✅ 구현 완료(코드·테스트) — 28스위트

- **2번:** `moodle_ws.py` 파서(`parse_ws_courses/assignments/course_contents`,
  `assignment_from_ws`) + `MoodleWsAdapter`(Discovery/Material/Browser 프로토콜 호환,
  `EtlInbox`·자료순위화·`EtlSource` 재사용). `collect.py`에 `collect_moodle_ws_to_files`.
- **3번:** `MoodleWsAdapter.download`(fileurl+토큰 `with_token`) + `fetch_material_texts`
  용량 상한 정책(파일당/배치, env 조정) + `_FILE_URL_RE` Moodle 확장.
- **4번:** forum 파서 + `Announcement` + `collect_announcements`(답글=숨은 명세),
  `etl_announcements.py`(순위화·SourceDoc·홈 요약).
- **6번 계측:** `feedback.py` 추출 신호 + `decisions_per_source` 파생 지표.
- **6번 질문:** Execution 프롬프트 결정 마커 지침 강화(후보·빈칸 형태·30초 답변 가능).

## ✅ 웹 UI 배선도 완료(2026-07-17)

- `python -m until.web --ws`(또는 `UNTIL_ETL_WS=1`): 인박스·pick·collect가 Moodle
  WS 어댑터로 동작. `collect_with_materials`를 어댑터 무관하게 일반화(`_resolve_course`).
- 초안 페이지 '📢 이 과제 관련 eTL 공지' 패널 + WS 인박스 상단 '최신 공지' 섹션
  (`collect_inbox_announcements` — 과목 앞쪽 몇 개만, 지연 방어).
- `--source moodle-ws:<과제URL>` CLI + `python -m until.capture.sources.moodle_ws <base>`
  함수 지형 조사 + 라이브 러너 `run_etl_ws_live.py`.
- 자동 다운로드 토글 `UNTIL_ETL_AUTODOWNLOAD`(기본 on).

## ⏳ 남은 것(라이브 검증 — 사용자 몫)

코드·오프라인 테스트(28스위트)는 완료. 실제 eTL 토큰으로 1회 검증만 남았다:
- `python -m until.capture.sources.moodle_ws https://myetl.snu.ac.kr` 로 활성 함수 확인
  (SNU eTL이 어떤 WS 함수를 열어 뒀는지 실측 — 일부 비활성이면 해당 기능만 스킵됨).
- `python run_etl_ws_live.py` 로 인박스→초안 1회.
- 6번 suggest 카테고리 템플릿은 기존 `suggest_answers.py`가 이미 커버(추가 작업 불요).

---


## 관통 전략 — "복붙이 절대 못 하는 것"

무료 ChatGPT에 과제를 복붙하는 것보다 나으려면, until만의 이유가 있어야 한다.
그 이유 = **eTL에서 자동으로 긁어오는 정보**. 복붙 사용자는 이걸 손으로 못 한다.
2·3·4는 전부 이 한 답의 변주다. 그리고 **추출을 늘리면 우리가 뚫어야 할 빈칸
(=결정 질문)이 줄고, 동시에 차별화가 커진다** — 같은 방향이다.

## 계측 먼저 — "결정 질문 수 = eTL 추출 실패 지표" (6번의 절반)

팀원 지적: "질문 개수가 많을수록 우리가 빈칸을 많이 뚫었다는 거고, 그만큼 eTL에서
못 뽑아왔다는 것." → 결정 질문 수를 **추출 실패 지표로 계측**한다.

- 지금도 `feedback.py`가 `n_decisions`(초안 결정 수)·`n_final_decisions`를 JSONL에
  적립한다. 여기에 **추출 성공 신호**를 함께 남긴다: 과제 본문 길이, 첨부 수,
  강의자료 본문 수집 건수, 공지 수집 여부 등(`n_sources`, `chars_extracted`).
- `feedback.summarize()`에 **`avg_decisions_per_source`** 같은 파생 지표를 추가 —
  "자료를 N건 뽑았을 때 결정이 M개"의 추세를 본다. 추출 기능을 켜기 전/후로
  이 값이 내려가면 그게 곧 차별화가 커졌다는 증거다.
- 구현 비용 작음(피드백 스키마에 필드 몇 개 + 하위호환 default None). **6번의
  계측 부분은 2번과 함께 먼저 넣는 걸 추천.**

---

## 2번 — eTL 추출 최대화 (Moodle WS 확장)

현재 라이브 추출은 Canvas REST(`canvas_api.py`) 경로다. eTL이 Moodle임이 확정됐고
`moodle_ws.py`(읽기 전용 클라이언트)가 이미 생겼으니, 아래 읽기 함수로 추출원을 넓힌다.
우선순위(팀원 지정 순):

| 우선 | 함수 | 뽑는 것 | until에서의 쓰임 |
|------|------|---------|------------------|
| ★★★ | `core_course_get_contents` | 주차별 섹션 + 전체 모듈 + `fileurl` | 자료 목록의 기반(3번 자동 다운로드가 여기 물림) |
| ★★★ | `mod_assign_get_assignments` | 본문(intro) + duedate/cutoffdate/allowsubmissionsfromdate + 첨부 | 과제 명세 정확화 → 결정 질문 ↓ |
| ★★ | `mod_assign_get_submission_status` | 제출 상태·남은 시간·기존 피드백 | 제출 여부 TODO 플래그를 실제 상태로 대체 |
| ★★ | `mod_forum_get_discussion_posts` | 글 본문 + 답글 전체 | **숨은 명세 소스** — 교수가 Q&A에서 추가한 조건(4번과 겹침) |
| ★ | `mod_label_get_labels_by_courses` | 섹션 중간 설명 텍스트 | 과목 규칙·주차 안내 |
| ★ | `mod_page_get_pages_by_courses` | HTML 본문(파싱 불필요) | 페이지형 강의자료 본문 |
| ★ | `core_calendar_get_action_events_by_courses` | 마감 이벤트 | 인박스 정렬·마감 정확화 |

### 설계 방향
- 각 함수마다 **순수 파서**(`parse_course_contents(json) -> [...]` 등)를 두어
  네트워크 없이 테스트(canvas_api와 동일 패턴). `MoodleWsClient.call()`이 유일한 I/O.
- `moodle_ws.py`에 얇은 메서드 추가(`get_course_contents(courseid)` 등) — 전부
  allowlist를 이미 통과하는 읽기 함수라 0번 원칙과 충돌 없음.
- **어댑터 통합:** `MoodleWsAdapter`가 `DiscoveryAdapter`/`MaterialAdapter` 프로토콜
  (`discovery.py`/`etl_materials.py`)을 만족하게 만들어, 기존 `EtlInbox`·자료 순위화
  파이프라인을 **그대로 재사용**한다(코어는 접속 방식 모름 — 불변 규칙 6).
- **점진 도입:** 먼저 `core_course_get_contents` + `mod_assign_get_assignments`
  두 개만(★★★). 이것만으로 본문·마감·첨부 정확도가 오르고 결정 질문이 준다.

---

## 3번 — 강의자료 자동 다운로드 (최대 차별화 포인트)

지금은 사용자가 수업 자료를 **손으로 업로드**한다(웹 `/simple` 첨부). Moodle은
`fileurl`에 토큰을 붙이면(`{fileurl}?token=TOKEN`, URL에 `?`가 있으면 `&token=`)
파일 바이트를 바로 받는다 → **eTL에서 자동으로 끌어온다.** 복붙 사용자가 절대
못 하는 일.

### 이미 있는 배관
`etl_materials.py`의 `fetch_material_texts()`가 이미 "상위 자료 다운로드 → 파싱 →
발췌(건당 3000자) → `[자료N]` 인용 파이프라인 주입"을 한다. Canvas용으로 동작 중.
**Moodle `fileurl` 다운로더만 붙이면 그대로 재사용**된다.

### 정책 제안 (팀원 주의: 용량·캐시·프라이버시 — 무차별 금지)
- **범위 제한:** 전체 과목 파일을 무차별로 받지 않는다. **해당 과제와 관련된 자료**
  (`rank_materials` 상위 k건, 이미 키워드 순위화 있음)만 다운로드. 현재 `top=2` 상한 유지·조정.
- **용량 상한:** 파일당 상한(예: 20MB) + 합계 상한. 초과 시 스킵하고 준비 점검에 경고.
- **캐시:** `fileurl`/파일 id를 키로 `_until_work/etl_cache/`에 캐시(이미 세션 pickle
  패턴 있음) — 같은 파일 재다운로드 방지, mtime/만료로 축출.
- **프라이버시:** 받은 파일은 로컬에만. 세션 삭제 시 함께 정리(기존 tempdir 정리 패턴).
  자동 다운로드 여부를 **사용자가 끌 수 있는 옵션**으로(기본값은 팀 결정 사항).
- **투명성:** "eTL에서 자동으로 가져온 자료 N건"을 UI에 명시(손 업로드와 구분).

---

## 4번 — eTL 공지 가져오기 (팀원 반응 최고 → 우선순위 ↑)

`mod_forum_get_forums_by_courses` → `get_forum_discussions` → `get_discussion_posts`
로 공지·Q&A를 수집. 셋 다 읽기 allowlist에 이미 있음.

### 표시 방안 (제안 — 택1 또는 조합)
1. **홈에 "최신 공지" 섹션** — 인박스 위에 과목별 최근 공지 N건(제목·날짜·과목).
   가볍게 시작하기 좋음.
2. **과제와 엮어 "이 과제 관련 공지"** — 과제 키워드로 공지를 매칭(이미 있는
   `rank_materials` 키워드 로직 재사용), 초안 맥락(`SourceDoc`)으로도 주입 →
   교수가 Q&A에서 푼 추가 조건이 초안·결정 질문에 반영됨(**숨은 명세 흡수**).
   차별화·빈칸 축소 효과가 가장 큼.

→ **추천: 1을 먼저(홈 공지 섹션, 저비용) + 2를 다음 단계(명세 흡수, 고가치).**
공지 수집은 과목당 API 3콜이라 인박스처럼 병렬/캐시로 지연 관리.

---

## 6번 — 결정 질문 세분화 (2번과 균형)

팀원: "질문이 우리 타겟층(대충 끝내고 싶은 사람)이 답할 수 있게 더 세부화돼야."
방향 = **개수는 줄이고(2번 추출로), 남은 각각은 더 구체적·답하기 쉽게.**

현재 결정 마커 형식(`execution/prompts.py`):
`[[DECISION: 무엇을 결정해야 하는지 한 문장 + (선택지가 있으면) 후보들]]`

문제: 추상적이면("핵심 논지를 어느 쪽으로 세울지") 대충 하려는 사람이 못 쓴다.

### 제안
- **프롬프트 지침 강화:** 마커에 **① 구체적 질문 + ② 2~3개 선택지 후보 + ③
  '이렇게 답하면 됨' 예시 형태**를 요구. 이미 `suggest_answers.py`(AI가 답 추천)가
  있으니, 초안 단계에서 후보를 더 강하게 붙이도록.
- **rationale 카테고리별 질문 틀:** `boundary/rationale.py`가 결정을 5종으로 분류
  중(가치판단·진로·취향 등). 카테고리마다 **답하기 쉬운 질문 템플릿**을 매핑
  (예: 가치판단 → "A와 B 중 어느 쪽? 이유 한 줄"; 진로 → "관심 분야를 빈칸에").
  suggest에 이미 카테고리별 지침이 있으니 그 자산을 질문 표면까지 확장.
- **계측과 연결:** 위 "결정 수 = 추출 실패 지표"로, 세분화가 과도한 결정 양산을
  부르지 않는지 감시(개수는 줄이는 게 목표).

---

## 구현 순서 제안

0. ✅ **쓰기 함수 차단(0번)** — 완료.
1. **계측(6번 절반) + Moodle WS 핵심 2함수(2번 ★★★):**
   `core_course_get_contents` + `mod_assign_get_assignments` 파서·어댑터,
   피드백에 추출 신호 필드. → 가장 큰 빈칸 축소, 위험 낮음.
2. **3번 자동 다운로드:** `fileurl` 다운로더 + 정책(범위·용량·캐시·프라이버시).
   기존 `fetch_material_texts`에 물림.
3. **4번 공지:** 홈 공지 섹션(저비용) → 과제 연계 명세 흡수(고가치).
4. **6번 질문 세분화:** 프롬프트·카테고리 템플릿, 계측으로 검증.

각 단계 = 작은 변경 → 27스위트 통과 → 커밋(기존 작업 방식). **1단계부터 착수할지,
순서를 조정할지 확인 부탁.**
