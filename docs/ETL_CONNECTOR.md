# eTL 커넥터 (서울대 LMS)

> ⚠️ 여기서 **eTL = 서울대학교 LMS**다. 새 eTL은 `myetl.snu.ac.kr`의 LearningX/Canvas UI이고,
> 구 eTL은 `etl.snu.ac.kr`의 Moodle UI다. 데이터 파싱 단계(Capture)와는 별개다.

Until의 진짜 입구: **eTL에 접속 → 과제 확인 → 과제 해결에 필요한 첨부파일을 모아와** 파이프라인에 넣는다.

## 왜 브라우저 기반인가
eTL은 **MySNU SSO** 뒤에 있고, LearningX/Canvas 또는 Moodle API 토큰을 학생이 항상 쉽게 받을 수 있는 구조가 아니다. 그래서 **로그인된 브라우저 세션**을 통해 접근한다. 비밀번호는 코드가 다루지 않는다 — 로그인은 사용자가 직접 하고, 커넥터는 이미 열린 세션의 페이지를 읽는다.

## 구조 (관심사 분리)
```
EtlSource.collect()                # 조립·저장 (재사용)
   └─ BrowserAdapter               # 페이지 읽기 + 첨부 다운로드 (교체 가능)
        ├─ FixtureBrowserAdapter   # 오프라인: 로그인 없이 동일 흐름 테스트
        ├─ LearningXBrowserAdapter # 라이브: 새 eTL LearningX/Canvas
        └─ PlaywrightBrowserAdapter# 라이브: 구 Moodle eTL 폴백
```
- `capture/sources/models.py` — `Attachment`, `RawAssignment`, `CollectedAssignment`
- `capture/sources/etl.py` — `EtlSource`, `FixtureBrowserAdapter`, `ChromeBrowserAdapter`
- `capture/sources/learningx_adapter.py` — 새 eTL LearningX/Canvas 파서+어댑터
- `capture/sources/playwright_adapter.py` — 구 Moodle eTL Playwright 어댑터
- `capture/sources/collect.py` — 수집 → ingest용 파일목록 헬퍼

수집 결과는 `CollectedAssignment.to_files()`로 `assignment.md`(과제 본문) + 첨부들을 디스크에 떨군 뒤, 기존 **Capture→Understanding→Execution** 파이프라인에 그대로 들어간다.

## 실행
```bash
# 오프라인 데모 (로그인·네트워크 불필요) — fixture를 가짜 eTL로 사용
python -m until.cli --source etl-demo --backend mock

# 라이브 — 새 eTL LearningX/Canvas 과제 페이지 URL 지정
python -m until.cli --source "etl:https://myetl.snu.ac.kr/courses/302199/assignments/98765" --backend local

# 구 Moodle eTL URL도 폴백 지원
python -m until.cli --source "etl:https://etl.snu.ac.kr/mod/assign/view.php?id=12345" --backend local
```
오프라인 흐름(`etl-demo`)은 end-to-end로 동작한다. 라이브는 Playwright 설치와 첫 로그인 세션이 필요하다.

## 출시 때 무엇으로 접속하나 (Claude in Chrome은 못 씀)

**Claude in Chrome은 에이전트(개발자 도구)의 능력**이라 제품 코드에 못 넣는다. 제품은 같은 `BrowserAdapter` 인터페이스에 아래를 갈아끼운다:

| 대체재 | 코드 | 비번 처리 | 장점 | 한계 |
|---|---|---|---|---|
| **브라우저 확장(자체)** | (로드맵) | 안 함(기존 세션) | 마찰 최소, LMS 무관, SSO 그대로 | 확장 배포·심사 |
| **Playwright 자동화** ✅구현됨 | `learningx_adapter.py`, `playwright_adapter.py` | 안 함(영속 프로필 1회 로그인) | 확장 없이 동작, 데스크톱/서버 | 브라우저 번들 무거움 |
| **Moodle Web Services** | (가용 시) | 토큰 | 가장 깔끔한 공식 API | 학교가 학생 토큰 허용해야 |
| iCal 피드 + 수동 업로드 | `--source files` | 없음 | 항상 동작(폴백) | 첨부 자동수집 X |

핵심: **"어떻게 접속하나"를 어댑터로 분리**했기에 LearningX/Canvas, Moodle, 확장, API 경로를 **코어 변경 없이** 교체할 수 있다.

## Playwright 기반 어댑터 (제품용, 구현 완료)

`capture/sources/learningx_adapter.py`, `capture/sources/playwright_adapter.py` — 실제 브라우저를 제어해 eTL에 접속한다.

- **영속 프로필**(`~/.until/etl_profile`)로 SSO 세션 유지. 첫 실행에서 창이 뜨면 MySNU 로그인을 사용자가 직접 하고, 이후엔 로그인 없이 동작. **비밀번호는 코드가 저장하지 않는다.**
- 새 eTL 과제 페이지 HTML → `parse_learningx_assignment()`(순수 함수, 테스트됨)로 제목·설명·첨부(Canvas file link) 추출.
- 구 Moodle eTL HTML → `parse_moodle_assignment()`(순수 함수, 테스트됨)로 제목·설명·첨부(pluginfile.php 링크) 추출.
- 첨부는 인증된 세션 쿠키로 다운로드.

```bash
pip install playwright && python -m playwright install chromium
python -m until.cli --source "etl:https://myetl.snu.ac.kr/courses/302199/assignments/98765" --backend local
```
첫 실행: 창에서 로그인 → 과제 페이지 도달 시 자동으로 수집·다운로드 → 파이프라인 진행.

### LearningX/Moodle 파싱은 따로 테스트된다
`parse_learningx_assignment(html, url)`와 `parse_moodle_assignment(html, url)`는 브라우저 없이 HTML만으로 동작하는 순수 함수라 `tests/test_learningx_parse.py`, `tests/test_moodle_parse.py`로 검증된다. 라이브 어댑터는 `page.content()`를 이 함수에 넘기기만 한다 — 파싱 로직과 브라우저 구동이 분리돼 있어 유지보수가 쉽다.

### 해외 LMS 확장 (EO "외국버전 파악")
`BrowserAdapter`만 갈아끼우면 Canvas/Blackboard/Moodle(해외) 커넥터로 확장. eTL은 첫 구현일 뿐. 해외 고객 인터뷰에서 각 학교 LMS 구조를 파악해 어댑터를 추가한다.

## 보안·프라이버시
- 비밀번호/세션 쿠키를 코드가 저장하지 않는다. 로그인은 사용자 브라우저(영속 프로필)에서.
- 받아온 과제·첨부는 로컬 작업 폴더(`_until_work/`)에만 둔다.
