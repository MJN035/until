# Until

[![CI](https://github.com/MJN035/until/actions/workflows/ci.yml/badge.svg)](https://github.com/MJN035/until/actions/workflows/ci.yml)
[![Determinism](https://github.com/MJN035/until/actions/workflows/determinism.yml/badge.svg)](https://github.com/MJN035/until/actions/workflows/determinism.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

> **eTL에서 과제를 읽고, 수업자료를 찾아, 당신이 정할 것만 물어본 뒤 제출 파일까지 준비합니다.**

대학생의 과제·잡무를 **사람의 판단이 필요한 경계선(Draft 경계선) 직전까지** 대신 끝내 주는 AI 에이전트.
모델은 산출물을 끝까지 쓰되, 본인이 정해야 하는 자리에는 `[[DECISION]]` 마커를 남기고 **대신 확정하지 않는다.**

파이프라인은 `Capture(eTL·Canvas·Moodle) → Understanding → Execution → Boundary → 제출 준비`이며,
CLI와 웹 UI 두 표면으로 쓴다. 88개 오프라인 스위트가 키·인터넷 없이 전부 돌고, 파이프라인은 결정적이다.
설계 근거는 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), 기능 지도는 [`docs/FEATURES.md`](docs/FEATURES.md) 참고.

## 빠른 실행 (API 키 불필요 — Mock 백엔드)

```bash
python demo.py                                   # 6개 유형 샘플 일괄 데모(추천 첫 실행)
python -m until examples/sample_assignment.txt   # (= python -m until.cli)
python tests/test_pipeline.py
```
`demo.py`는 유형 감지 → 경계선 결정(+왜 당신 몫인지) → 제출 준비 점검 → 제출용 문서 저장까지
한 번에 보여준다(`-v`로 초안 본문까지). 샘플 설명은 [`examples/README.md`](examples/README.md).

```text
▶ sample_extension.txt  [에세이/논술]
   결정 1 (관점·논지) 세 논점 중 어느 것을 핵심 논지로 세울지 — 본인…
   • [마감] D-14 · 마감 2026-07-17 23:59 · 연장됨      ← 연장 공지·시각까지 이해
   ⚠️ [분량] 분량 부족 — 요건 1500자 이상, 현재 248자 (약 1252자 더 필요)
   • [결정] 당신이 정할 곳 3곳 남음 — 채우면 완성에 가까워집니다
   제출용 저장: _until_work\demo\sample_extension.md
```

## 무료로 실행 (결제 없이)

```bash
# 로컬 Ollama — 키도 결제도 불필요
ollama pull llama3.2 && pip install openai
python -m until.cli examples/sample_assignment.txt --backend local
```
개발·데모·테스트는 `--backend mock`으로 충분($0, 설치 0). 자세한 무료 경로는 [`docs/TECH_STACK.md`](docs/TECH_STACK.md) 참고.

## 라이브 (Anthropic)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-...
python -m until.cli path/to/assignment.pdf --backend anthropic
```

옵션: `--auto-accept` (모두 수락 모드).

결과 저장/결정 반영:
```bash
python -m until.cli examples/sample_assignment.txt --backend mock --out report.md

# answers.json 예: {"1": "내가 고른 핵심 논지", "2": "반론은 짧게 다룬다"}
# --resolve-mode final(기본): 답변을 녹여 Execution 2차 패스로 '최종 완성본' 작성.
#                  splice    : 마커 자리에 답변 문자열만 치환(LLM 미사용).
python -m until.cli examples/sample_assignment.txt --backend mock \
  --resolve answers.json --out resolved_report.md

# 제출용 문서 내보내기(진단정보 없이 학생이 이어서 완성·제출할 깨끗한 문서)
#   .md → 본문+'직접 정할 것' 체크리스트, .html → 인쇄용 단독 문서.
python -m until.cli examples/sample_assignment.txt --backend mock \
  --submission 제출용.md      # 또는 제출용.html / 제출용.docx(워드, 의존성 0)
```

## 결정 루프 닫기 (P6) — 최종 완성본

`--resolve`로 결정 답변을 주면, 답한 결정은 본인 말투로 본문에 **녹여 넣고**(1인칭으로 확정), 답하지 않은 결정은 `[[DECISION]]` 마커로 **그대로 남긴다**(부분 해소). 이때 가드는 완화되지만(이미 사람이 판단함) 한글 외 외국 문자·본문 과소 작성 검사는 유지된다. 코드: `until/execution/drafter.py:finalize_with_decisions`, `until/pipeline.py:finalize`.

## 결정 AI 제안 + "모두 수락" — 빈칸의 막막함 제거

각 `[[DECISION]]`에 AI가 **추천 답 + 한 줄 근거**를 제시하면, 학생은 한눈에 보고 **그대로 수락**하거나 고친다. 핵심은 **AI가 대신 확정하지 않는다**는 것 — 제안일 뿐이고 최종 확정은 항상 사람의 클릭이다. 막막한 빈칸("그 역할로 존재해야 하는 순간")은 AI가 무난한 기본값으로 덜어주되, 고유한 가치판단("그 사람이어야 하는 순간")의 선택권은 그대로 사람에게 남긴다. 웹에선 **🤖 AI 제안 채우기** → 칸이 채워지고 버튼이 **"전부 제안대로 수락 → 최종본"**으로 바뀐다. 코드: `until/execution/suggest_answers.py`, `until/pipeline.py:suggest_decision_answers`, 웹 `POST /suggest`.

## 제출 준비 점검 + 제출용 내보내기 — 경계선 유지

초안이 나오면 **제출 직전에 사람이 확인할 것**을 결정적으로(토큰 0) 한 번에 보여준다:

- **마감 D-day** — 절대(`YYYY-MM-DD`·`N월 N일`·`M/D`)·상대(`내일`·`금요일`·`다음 주 월요일`) 날짜,
  **연장 공지**('7/10에서 7/17로 연장' → 연장된 날짜+'연장됨' 표시), **마감 시각**(`23:59`·`오후 6시`·`자정`)까지
  파싱. 소수·버전·'오늘날' 같은 비날짜 표현은 문맥 게이트로 걸러냄. 임박(3일 이내)·지남 강조.
- **분량 요건** — `2000자 이상`·`500~800자`·`N페이지`·`N words` 감지 → 초안(결정 마커 제외) 측정 → 충족/부족/초과.
- **인용 커버리지** — 준 근거자료 중 본문이 `[자료N]`으로 실제 인용한 비율(미인용·부분·충실·가짜번호).
- **남은 결정** — 아직 안 채운 `[[DECISION]]` 수. 이건 경고가 아니라 **"당신이 정할 곳"** 안내다.

CLI는 `6. 제출 준비 점검` 블록으로, 웹은 초안·최종 페이지 상단 패널로 보여준다(경고는 강조색).
`--submission`으로 내보낸 **제출용 문서**에는 본문 + `직접 정할 것` 체크리스트 + `과제 요건 점검`
(명세 requirements/constraints) + `제출 준비 점검`이 함께 담긴다. 경계선 철학 유지 — 어떤 점검도
사람 판단을 대신 확정하지 않고, 억지로 분량을 늘리거나 인용을 끼워 넣지 않는다.
코드: `until/readiness.py`, `until/understanding/{length_target,deadline}.py`, `until/context/citation_coverage.py`, `until/report.py`.

더 있는 것들:
- **유형별 조정** — 문제풀이·코드 같은 정형 과제는 수업자료 미인용을 경고하지 않는다(억지 경고 방지).
- **JSON 내보내기** — `--readiness-json 점검.json`(CLI) / `GET /readiness/<token>.json`(웹)으로
  점검 결과를 기계가 읽는 형식으로. 에디터·CI 연동용.
- **결정 근거(왜 당신 몫인지)** — 각 `[[DECISION]]`을 가치판단/관점·논지/진로·경험/취향·스타일/
  범위·선택으로 결정적 분류해 🔒 한 줄 근거를 붙인다. 남긴 결정이 떠넘김이 아니라 '당신이어야
  하는 순간'임을 밝힌다(웹·CLI·제출용·리포트 전 표면). AI 제안도 이 분류에 맞춰 톤을 바꾼다.
- **변경 투명화** — 결정 반영 후 최종본이 초안에서 어디가 달라졌는지 문단 diff로 보여준다
  (웹 '초안에서 달라진 부분' 토글, 리포트 '변경 상세'). AI가 손댄 곳을 숨기지 않는다. `until/diffview.py`.
- **지난 답 재사용** — 결정에 답할 때마다 로컬에 적립(`_until_work/answer_history.jsonl`,
  커밋 제외)하고, 비슷한 결정이 다시 나오면 '🕘 지난 답' 칩으로 재제안한다(클릭으로만 채움).
  AI 제안·최종본 반영 문장도 이 성향과 **내 답변 문체**(종결어미)에 맞춰진다.
  무엇이 기억되고 있는지는 `python -m until.context.answer_history`로 확인(삭제=파일 삭제).
  `until/context/answer_history.py`.

## 웹 UI (P8~P12) — 학생용 표면

```bash
python -m until.web                       # http://127.0.0.1:8000 (backend=mock)
python -m until.web --backend local       # Groq/Ollama 등 라이브
# eTL 자동 흐름까지 보려면 토큰을 환경변수로:
UNTIL_CANVAS_TOKEN=<토큰> python -m until.web --backend local
# 토큰 없이 브라우저 SSO로(로그인 한 번): playwright 필요
python -m until.web --backend local --sso
```

FastAPI 전환 경계는 기존 서버와 병렬로 검증할 수 있다. 아직 인증·과금·클라우드
사용자 격리·과금·프로필·세션·eTL·완성/점검 흐름까지 이관되어 프로덕션 컨테이너는
`uvicorn until.asgi:app`으로 실행한다. 기존 `python -m until.web`은 로컬 SSO와 긴급 복구용으로
당분간 보존한다.

```powershell
uvicorn until.asgi:app --host 127.0.0.1 --port 8001
# JSON: POST /api/v1/drafts · GET /api/v1/sessions/{id}/readiness
# HTMX fragment: POST /hx/draft
```
의존성 0(표준 라이브러리 `http.server`). 홈은 초미니멀 — **'내 eTL 과제 불러오기'** 하나에 집중.
화면 순서는 **클릭이 먼저, 토큰이 나중**이다: 홈에서 무엇을 할지 고르면 그다음 화면
(`/connect`)에서 eTL 액세스 토큰을 묻는다. 토큰이 없으면 그 화면의 '붙여넣기로 하기'로
빠져나가 과제 본문만으로 똑같이 초안까지 갈 수 있다.
1. **내 과제 불러오기(자동)** — 토큰을 넣으면 내 과목·과제를 자동 목록화(P9, 마감순·미제출 필터,
   '기한 지난 과제 숨기기' 체크) → 과제 선택 시 관련 자료 자동 수집(P10) → 초안.
   (`/connect`→`/inbox`→`/pick`)
2. **⚡ 바로 초안(`/quick`)** — 목록도 안 보고 최우선 과제(미제출 > 기한 안 지남 > 마감 임박)를
   자동 선택해 곧장 초안까지.
3. **간단 모드(`/simple`)** — 글자 최소·원-액션: 붙여넣기 한 칸 → ① 초안 → ② 답 → ③ 완성본.
   홈 하단 '직접 붙여넣기' 링크. 간단↔자세히 상호 전환, 세션은 두 모드 공유.
   답할 것이 많아도 **처음엔 질문 4개만** 펴 둔다(`SIMPLE_FIRST_N`) — 나머지는 같은 폼
   안에 접혀 있어 펴서 채우면 함께 제출된다.
   **내 자료 첨부**(선택, 최대 5개·합계 25MB): PDF·DOCX·HWPX·PPTX·TXT를 올리면
   `[내 자료]` 근거로 주입돼 초안이 `[자료N]`으로 인용한다(파싱 실패는 준비 점검 경고).
   **내가 쓴 글 첨부**(선택): 내 글을 올리면 문체 프로파일이 초안에 적용된다(내 말투).
4. **플랜(`/plan`)** — 사용량·무료 일일 한도 안내(제품화 스캐폴드, `until/billing.py`. mock 백엔드는 무제한).

**브라우저 SSO 모드(`--sso`)**: 토큰 발급 없이, `/inbox` 첫 요청 때 브라우저 창이 열려 MySNU 로그인을
한 번 하면(최대 5분) 그 세션으로 내 과제를 웹에서 바로 불러온다. Playwright sync는 스레드 고정이
필요해 SSO 모드는 단일 스레드 서버로 동작(`_sso_adapter` 공유). CLI 러너 `python run_etl_inbox.py`도 그대로 유효.

각 결과: 경계선 초안 + 결정 체크리스트 → 결정 반영 최종본 + 제안 프롬프트.
**Copy/Download .md** 버튼으로 결과 저장, 제출 시 **로딩 오버레이**(수십 초 안내), **다크모드 토글**(상단 바).
작업은 디스크에 **자동 저장**되어 서버를 껐다 켜도 유지 — `/sessions`(홈의 '↺ 이전 작업 다시 열기')에서
목록으로 보고 이어서 열거나 삭제할 수 있다(개인정보 통제, `_until_work/web_sessions`·gitignore 영역).
디자인은 크림 종이·테라코타 액센트의 에디토리얼(세리프 조판·종이 질감·스크롤 리빌,
`prefers-reduced-motion` 대응, 다크모드는 차콜). 코드: `until/web.py`, `until/capture/sources/{discovery,canvas_api,playwright_discovery}.py`, `until/context/etl_materials.py`.

## Until Cloud — 호스팅(멀티유저) 모드

```bash
python -m until.web --cloud            # 0.0.0.0:$PORT, 익명 uid 쿠키로 사용자 격리
```
같은 코드베이스의 **모드**다(로컬 동작 불변). 사용자마다 세션·답 히스토리·사용량이
익명 `uid` 쿠키로 격리되고(128bit 세션 토큰, 소유자 검사), 초대 코드 게이트
(`UNTIL_BETA_CODE`)·전역 일일 상한(`UNTIL_GLOBAL_DAILY_DRAFTS`)·보안 헤더+CSP·
`GET /healthz`가 켜진다. Cloudflare KV(`UNTIL_KV_ACCOUNT/NAMESPACE/TOKEN` 설정 시)가
컨테이너 재시작 후에도 사용자 데이터를 복원한다(`until/cloudkv.py`, 의존성 0).
클라우드에서는 운영 env의 `UNTIL_CANVAS_TOKEN` 폴백과 라이선스 파일 활성화가
차단된다(계정 공유 사고 방지). SSO·GEPA는 로컬 전용. **배포 절차: `deploy/DEPLOY.md`**
(Cloudflare Containers + 정적 랜딩, Docker 스모크 포함).

### 계정 로그인 (선택 — Kakao / Google)

익명 `uid` 쿠키만으로는 브라우저를 바꾸거나 쿠키가 지워지면 내 과제가 사라진다.
Kakao 또는 Google 로그인을 켜면 `uid`가 제공자 계정 id에서 유도된 값으로 고정돼,
기기가 달라도 같은 작업 공간으로 돌아온다. 구현은 `until/kakao_auth.py`와
`until/google_auth.py`이며 OAuth 2.0 Authorization Code + PKCE를 **표준 라이브러리만으로**
처리해 외부 로그인 스크립트를 싣지 않는다(CSP 불변).

```bash
# Kakao Developers: REST API 키 + Client Secret
export UNTIL_KAKAO_CLIENT_ID=...
export UNTIL_KAKAO_CLIENT_SECRET=...
export UNTIL_KAKAO_REDIRECT_URI=http://localhost:8000/auth/kakao/callback

# Google을 함께 또는 대신 켤 때
export UNTIL_GOOGLE_CLIENT_ID=...apps.googleusercontent.com
export UNTIL_GOOGLE_CLIENT_SECRET=...
export UNTIL_GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback
python -m until.web --cloud
```

푸시 없이 검증하려면 Kakao Developers의 **카카오 로그인 → Redirect URI**에 위 localhost
주소를 정확히 등록한다. 카카오 계정 `id`만 계정 키로 사용하고, 닉네임·이메일은 사용자가
동의해 실제 응답에 포함된 경우에만 표시한다. Google은 Cloud Console의 승인된 리디렉션
URI에 같은 방식으로 등록하며 요청 범위는 `openid email profile`뿐이다.

- **로그인 전에 만든 초안도 잃지 않는다** — 붙여넣고 초안까지 만든 뒤 로그인하면 그
  세션·프로필이 계정으로 넘어간다(`_adopt_anon_data`, 기존 데이터는 덮어쓰지 않음).
- `UNTIL_REQUIRE_LOGIN=1` — 초안 생성 전에 로그인을 강제한다. **기본은 off**:
  먼저 써 보게 하고 저장 시점에 로그인시키는 쪽이 전환율에 낫다.
- `UNTIL_GOOGLE_ALLOWED_DOMAIN=snu.ac.kr` — 학교 계정만 받는다(콜백에서 결정적 차단).
- 제공자별 client id/secret이 모두 없으면 그 로그인 버튼은 노출되지 않는다(부분 설정=off).

## 베타 피드백 로그 (P7) → GEPA

```bash
python -m until.cli examples/sample_assignment.txt --backend mock \
  --feedback --satisfaction 5     # 실행마다 (과제·결정수·reask·만족도) JSONL 적립
```
적립된 기록은 `until.optimize.trainset.build_trainset_with_feedback()`로 GEPA 학습셋에 병합된다(라벨 불필요 — 실제 사용 spec+sources가 그대로 최적화 데이터). 코드: `until/feedback.py`.

## Canvas REST API 어댑터 (권장 제품 경로)

브라우저 스크래핑 대신 학생 액세스 토큰으로 Canvas 공식 API 호출:
```bash
export UNTIL_CANVAS_TOKEN=<계정>설정>새 액세스 토큰>
export UNTIL_CANVAS_FILES=1   # (선택) 파일 탭 첨부도 병합
python -m until.cli --source "canvas-api:https://myetl.snu.ac.kr/courses/302199/assignments/369118" \
  --backend local
```
`urllib`만 사용(의존성 0), `BrowserAdapter` 호환. 코드: `until/capture/sources/canvas_api.py`.

## eTL = Moodle Web Services (읽기 전용) — `--ws`

eTL은 Moodle이다. 학생 토큰으로 Moodle Web Services를 **읽기 전용**으로 호출해
과목·과제 본문·마감·**강의자료(자동 다운로드)**·**공지/Q&A(숨은 명세)**까지 자동
수집한다 — 복붙으로는 못 얻는 정보. **쓰기 함수(과제 제출·퀴즈·글쓰기·쪽지)는
코드 레벨에서 영구 차단**된다(allowlist, 상세 `docs/ETL_READ_ONLY.md`).

```bash
export UNTIL_ETL_WS_TOKEN=<eTL>계정>설정>새 웹서비스 토큰>
# 0) 함수 지형 조사 — 이 토큰에 무엇이 활성인지(사용/미사용 분리)
python -m until.capture.sources.moodle_ws https://myetl.snu.ac.kr
# 1) 웹 UI를 Moodle WS 모드로(인박스→선택→자료·공지 수집→초안)
python -m until.web --backend local --ws
# 2) CLI 단건(과제 URL엔 courseid가 있어야 무상태 조회 — 인박스 링크 권장)
python -m until.cli --source "moodle-ws:https://myetl.snu.ac.kr/mod/assign/view.php?id=100&courseid=42" --backend local
# 3) 라이브 검증 러너(지형 조사 + 미제출 목록 + 초안/리포트)
python run_etl_ws_live.py            # 목록만
python run_etl_ws_live.py 3          # 3번 과제로 초안
```
코드: `until/capture/sources/moodle_ws.py`(클라이언트·어댑터·allowlist),
`until/context/etl_announcements.py`(공지). `--ws`는 `UNTIL_ETL_WS=1`과 동일.

## 테스트

```bash
python run_tests.py        # 88개 오프라인 스위트 일괄 실행(병렬 ~20초)(키 불필요, 인코딩 자동)
```

### 환경변수 요약
| 변수 | 용도 |
|---|---|
| `UNTIL_BASE_URL` / `UNTIL_API_KEY` / `UNTIL_MODEL` | local 백엔드(Groq/Ollama 등) |
| `UNTIL_MAX_TOKENS` | local 백엔드 출력 토큰 상한(기본 2048; 긴 에세이·finalize 잘림 방지) |
| `UNTIL_CANVAS_TOKEN` / `UNTIL_CANVAS_FILES` | Canvas REST API 토큰 / 파일 탭 병합 |
| `UNTIL_ETL_WS` / `UNTIL_ETL_WS_TOKEN` | Moodle WS 모드 on / WS 토큰(없으면 `UNTIL_CANVAS_TOKEN` 폴백) |
| `UNTIL_ETL_BASE` | eTL 베이스 URL(기본 SNU eTL) |
| `UNTIL_ETL_AUTODOWNLOAD` | 강의자료 자동 다운로드 on/off(기본 on, `0`=끔) |
| `UNTIL_MATERIAL_MAX_MB` / `UNTIL_MATERIAL_TOTAL_MB` | 자료 다운로드 파일당/배치 용량 상한(기본 20/60MB) |
| `UNTIL_FREE_CREDITS` / `UNTIL_CREDIT_COST` | 신규 무료 체험 크레딧(기본 3) / 과제 1건당 차감(기본 1) |
| `UNTIL_CREDIT_CODES` / `UNTIL_PAY_URL` | 충전 코드표("CODE:개수,…") / 결제 링크 |
| `UNTIL_PLAN` / 라이선스 | `pro`면 크레딧 무관 무제한(기관/무제한 패스) |
| `UNTIL_GLOBAL_DAILY_DRAFTS` | 전역 일일 상한(운영 비용 방어 — 크레딧과 별개) |
| `UNTIL_GA_MEASUREMENT_ID` / `UNTIL_META_PIXEL_ID` | 선택형 GA4/Meta Pixel ID. 명시적 동의 뒤 공개 소개 화면 PageView만 측정(기본 off) |
| `UNTIL_GEPA_MODEL` / `UNTIL_GEPA_BUDGET` | GEPA student/reflection 모델 / 호출 예산(max_metric_calls) |

## 구조

```
until/
  capture/      # [1] ETL — PDF/txt/md → 정규화 Document (토큰 0)
  understanding/# [2] TaskSpec 추출 (LLM)
  execution/    # [3] 경계선까지 Draft 작성 (LLM)
  boundary/     # [4] Draft 경계선 모델 — 결정 지점 분리
  prompts/      #     "다음에 뭐라고 프롬프트할지" 제안
  llm/          # 교체 가능한 LLM 래퍼 (Mock / Anthropic)
  pipeline.py   # 전체 오케스트레이션
  cli.py        # 데모 진입점
```


## eTL 소스 커넥터 (서울대 LMS)

Until의 진짜 입구 — eTL에서 과제+첨부를 모아와 파이프라인에 넣는다. (eTL=서울대 LMS, 데이터 파싱 단계와 별개)

```bash
# 오프라인 데모 (로그인 불필요, fixture를 가짜 eTL로 사용)
python -m until.cli --source etl-demo --backend mock
```
라이브(`--source "etl:<과제URL>"`)는 Playwright 영속 프로필로 붙는다. 새 eTL
LearningX/Canvas URL(`https://myetl.snu.ac.kr/courses/.../assignments/...`)은
`LearningXBrowserAdapter`가 처리하고, 구 Moodle eTL URL은 기존 Playwright 어댑터로 폴백한다.
설계·구현 가이드: [`docs/ETL_CONNECTOR.md`](docs/ETL_CONNECTOR.md).

**첨부 파싱 지원:** 텍스트·마크다운·PDF(PyMuPDF)·**docx/pptx/html/hwpx(내장, 의존성 0)**.
`pip install docling`이 있으면 고품질 변환을 우선 시도. 지원하지 않는 이진 포맷
(.doc/구형 .hwp 등)은 조용히 깨진 글자를 넣는 대신 '이 자료 없이 작성됨' 경고로 표면화된다.


## Personalization/Context — Execution에 맥락 주입

Execution은 LLM이 혼자 생각하지 않는다. 초안 쓰기 전에 **3가지 맥락**을 모아 근거+문체로 먹인다:
1. **수업자료/과제파일** (`--course-materials DIR`) — 과제 관련 자료 검색
2. **내 관련 파일** (`--my-files DIR`) — 내 폴더에서 관련 파일 검색
3. **내 말투** (`--voice DIR`) — 내 기존 글에서 문체·자주 쓰는 표현 프로파일링

```bash
python -m until.cli examples/sample_assignment.txt --backend mock \
  --course-materials examples/course_materials \
  --my-files examples/my_files --voice examples/voice_samples

# 선택: LLM 1회로 말투 요약 보강(실패 시 기존 결정적 분석으로 폴백)
python -m until.cli examples/sample_assignment.txt --backend mock \
  --voice examples/voice_samples --voice-llm
```
모두 토큰 0(결정적 검색·문체 분석). 경계선은 그대로 유지 — 맥락이 풍부해져도 개인 판단은 `[[DECISION]]`으로 남는다. 코드: `until/context/{voice,retrieval,bundle}.py`.

핵심 개념 **Draft 경계선**: 자료로 채울 수 있는 건 끝까지 쓰되, 고유 판단이 필요한 자리는 `[[DECISION: ...]]`로 남기고 넘지 않는다.

**경계선 강제 (BoundaryGuard).** Execution 출력은 결정적 검증을 거쳐, 경계선을 넘으면(입장 단정·결정 지점 0개·게으른 공백) 모델에 자동 **재요청(reask)** 한다. guardrails-ai의 validate→reask 패턴을 의존성 없이 차용했고, 결정 지점 처리(approve/edit/reject/respond) 스키마는 LangGraph HITL 패턴을 따른다. 설계: [`docs/PROMPT_DESIGN.md`](docs/PROMPT_DESIGN.md).

## 차용한 오픈소스 패턴
- [guardrails-ai/guardrails](https://github.com/guardrails-ai/guardrails) (Apache-2.0) — validate→reask / OnFailAction → `until/execution/boundary_guard.py`
- [LangGraph Human-in-the-Loop](https://docs.langchain.com/oss/python/langchain/human-in-the-loop) — approve/edit/reject/respond → `until/boundary/models.py` (`Resolution`)

## 다른 wrapper와 비교 (공부용)

Until은 무거운 프레임워크 없이 **얇은 자체 하네스**로 짠 LLM 앱이다(2026 트렌드 부합). 표준 도구의 개념과 1:1로 대응한다:

| 표준 개념 | 대표 도구 | Until 구현 |
|---|---|---|
| 계층형 파이프라인(ingest→retrieve→prompt→parse→UI) | LlamaIndex | `capture→understanding→context→execution→boundary` |
| 구조화 출력(JSON 강제) | Instructor / Anthropic Structured Outputs | `MockClient`/`complete(schema=...)`, 결정 제안 JSON |
| 가드레일(검증→재요청) | Guardrails | `execution/boundary_guard.py` (validate→reask) |
| Human-in-the-Loop(승인/수정/거부/응답) | LangGraph HITL | `boundary/models.py` `Resolution` + 결정 AI 제안 |
| 키 없는 테스트 백엔드 | — (모범) | `--backend mock`, 56 오프라인 스위트 |

전체 조사·인용은 [`docs/WRAPPER_STUDY.md`](docs/WRAPPER_STUDY.md).

## 개인 데이터 저장 위치 (로컬 전용)

Until은 어떤 개인 데이터도 서버로 보내지 않는다(LLM 백엔드 호출 제외). 아래는 전부
**로컬 `_until_work/`**(`.gitignore` 포함 — 커밋되지 않음)에만 쌓이며, 지우면 그만이다:

| 파일/폴더 | 내용 | 지우는 법 |
|---|---|---|
| `web_sessions/*.pkl` | 웹 작업 세션(초안·답변·점검) | `/sessions`의 삭제 버튼 또는 파일 삭제 |
| `answer_history.jsonl` | 결정에 답한 기록('지난 답' 재제안용) | 파일 삭제 |
| `feedback.jsonl` | 실행 통계(결정수·통과율·만족도) | 파일 삭제 |
| `demo/` | demo.py 산출물 | 폴더 삭제 |

비밀 키(Groq·Canvas 토큰)는 파일로 저장하지 말고 환경변수로만(`.env.example` 참고).

## 라이선스

[GNU Affero General Public License v3.0 only](LICENSE) (AGPL-3.0-only).

AGPL은 네트워크 조항(§13)을 둔다 — 이 소프트웨어를 **수정해 네트워크 서비스로
제공하면** 그 이용자에게 수정된 소스를 제공해야 한다. 자기 학습·연구·사내 사용에는
아무 의무가 없다.

**이 저장소는 코어다. 서버·결제·관리자 계층(billing·PG 웹훅·관리자 보드·운영 KV)은
포함되지 않는다.** 코어는 그 계층 없이 단독으로 동작한다 — `python demo.py`와
`python -m until <파일>`은 API 키도 서버도 없이 그대로 돌아간다.

재구현해 차용한 외부 패턴의 출처는 [`NOTICE`](NOTICE)에 있다.
기여 방법은 [`CONTRIBUTING.md`](CONTRIBUTING.md), 기여자 동의서는 [`CLA.md`](CLA.md) 참고.
비밀키는 절대 커밋하지 말 것(`.env.example` 참고).
