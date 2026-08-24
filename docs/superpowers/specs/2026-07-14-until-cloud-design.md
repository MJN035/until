# Until Cloud — Cloudflare 호스팅 전환 설계 (2026-07-14)

## 목표
로컬 전용 앱(`python -m until.web`)을 **설치 없이 URL로 쓰는 베타 서비스**로.
서울대생이 브라우저에서 과제를 붙여넣거나 eTL 토큰으로 불러와 초안→결정→완성본까지.
로컬 앱은 그대로 유지(회귀 0) — 클라우드는 **같은 코드베이스의 모드**다.

## 런타임 결정: Cloudflare Containers (+ 정적 Worker)
| 후보 | 판정 |
|---|---|
| **Containers** (채택) | 현 Python `http.server` 앱이 거의 그대로 동작. Docker로 로컬 검증 가능. Workers Paid $5/월 필요. 유휴 시 잠들고 첫 요청에 수 초 콜드스타트(베타 허용). |
| Python Workers (무료) | 순수 stdlib은 지원되나 **동기 파이프라인이 의존하는 동기 HTTP(JSPI run_sync)가 아직 불확실** → 코어 비동기 전환 리스크. 무료 티어 포트는 로드맵으로. |
| 무료 PaaS/VPS | 사용자가 Cloudflare 선택(MCP 연결 활용). |

구성: **wrangler 타깃 두 개** — ① `until-landing`(정적 Workers Assets, 무료,
자체 workers.dev URL) ② `until-app`(얇은 JS Worker가 전 경로를 Container(DO 바인딩)로
프록시). 앱은 절대경로(`/draft` 등)로 링크를 만들므로 프리픽스 라우팅 대신
호스트 분리 — 경로 재작성 0.

## 아키텍처 (클라우드 모드 = `UNTIL_CLOUD=1`)

### 1. 사용자 식별 — 익명 uid 쿠키
- 첫 방문에 `uid = secrets.token_urlsafe(24)`(128+bit) 발급. HttpOnly·SameSite=Lax,
  HTTPS 뒤에서는 Secure(CF 뒤 `X-Forwarded-Proto`/`CF-Visitor`로 판정).
- 계정/로그인 없음(베타). 쿠키 지우면 새 사용자 — 문서에 명시.
- 선택 베타 게이트: `UNTIL_BETA_CODE` 설정 시 첫 방문에 초대 코드 입력 → 통과 쿠키.

### 2. 데이터 네임스페이스 — 전부 uid 스코프
- 세션 pickle: `_until_work/web_sessions/<uid>/<token>.pkl` (로컬 모드는 기존 평면 유지).
- `list_sessions`/`delete_session`/`_persist_session`/`_get_session`에 uid 파라미터
  (클라우드에서 필수). 세션 토큰(64bit)은 **uid 네임스페이스 안에서만** 조회되므로
  타 사용자 토큰 추측이 무의미해진다.
- answer_history·voice·usage도 uid 하위 경로로 (`_until_work/users/<uid>/…`).
- uid당 세션 보관 20개(_SESS_KEEP은 로컬용 100 유지), uid 폴더 총량 상한.

### 3. 지속화 — Cloudflare KV 백킹(선택, env 게이트)
컨테이너 디스크는 잠들면 소멸 → 재방문 기능(세션 목록·지난 답)이 깨진다.
- `until/cloudkv.py`: KV REST API(`api.cloudflare.com/client/v4/.../storage/kv/...`)
  클라이언트, **urllib만**(의존성 0). env: `UNTIL_KV_ACCOUNT`/`UNTIL_KV_NAMESPACE`/
  `UNTIL_KV_TOKEN`. 미설정 시 디스크만(로컬·테스트 동작 불변).
- 쓰기 경로: 디스크 write-through + KV put(베스트에포트, 응답 안 막음 — 백그라운드 스레드).
- 읽기 경로: 메모리 → 디스크 → KV(콜드스타트 복원) 순. KV 키:
  `sess:<uid>:<token>`, `hist:<uid>`, `usage:<uid>:<YYYY-MM-DD>`, `voice:<uid>`.
- 테스트: urlopen 주입식 fake로 오프라인 검증(새 스위트 test_cloud).

### 4. 비밀 취급
- **Groq 키**: 서버 env(Cloudflare secret) — 사용자는 키 없이 사용.
- **Canvas(eTL) 토큰**: 지금처럼 서버 메모리 `_TOKENS`(sid)만, 디스크/KV 저장 금지.
  컨테이너가 잠들면 재입력(화면에 안내 문구). 로그에 절대 남기지 않음.
- SSO(Playwright)·GEPA는 클라우드 이미지에서 제외(로컬 전용 기능으로 명시).

### 5. 한도(남용·비용 방어)
- billing.py 확장: 클라우드에서 usage를 uid별 적립(`usage:<uid>:<day>`),
  free 플랜 하루 N회(기본 5) + **전역 일일 상한** `UNTIL_GLOBAL_DAILY_DRAFTS`
  (Groq TPD 보호, 기본 200). 초과 시 기존 /plan 안내 재사용.
- 업로드 상한(25MB 등) 기존 그대로. 요청 타임아웃·바디 캡 기존 그대로.

### 6. 보안 헤더·운영
- 응답에 `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, 최소 CSP(inline style 허용 — 현 디자인 유지).
- `--cloud`: 0.0.0.0 + `$PORT`(기본 8080) 바인딩, ThreadingHTTPServer.
- 헬스체크 `GET /healthz`(200 "ok") — Worker/모니터링용.

### 7. 배포 산출물 (`deploy/` 새 디렉터리)
- `deploy/Dockerfile` — python:3.13-slim, 앱 복사, `python -m until.web --cloud`.
- `deploy/app/index.js` — 전 경로 → Container 프록시(공식 `@cloudflare/containers`
  Container 클래스), `/healthz` 패스스루. `deploy/app/wrangler.jsonc`.
- `deploy/landing/` — 정적 랜딩(assets 전용 Worker). `deploy/landing/wrangler.jsonc`.
- `deploy/DEPLOY.md` — `wrangler login` 후 배포 1커맨드, secrets 설정 목록.
- 로컬 검증: `docker build` + 컨테이너 기동 스모크. `wrangler deploy`는 인증 필요
  (사용자 클릭) — 준비 완료 상태로 두고 배포 명령 문서화.

## 구현 단계
1. **클라우드 모드 코어**: uid 쿠키·네임스페이스·베타 게이트·보안 헤더·/healthz·
   per-uid billing·전역 상한 (+ test_cloud, 기존 25스위트 회귀 0)
2. **KV 백킹**: cloudkv.py + persist/restore/list/history 배선 (+ fake 테스트)
3. **패키징**: Dockerfile·worker·wrangler·DEPLOY.md + docker 로컬 스모크
4. (별도 트랙) 랜딩 페이지·보안 리뷰·디자인

## 명시적 비범위 (YAGNI)
- 계정/이메일 로그인, PG 웹훅 결제, 멀티 리전, 큐잉 — 베타 이후.
- Python Workers 무료 포트 — JSPI 안정화 후 재평가.
- 컨테이너 다중 인스턴스 수평 확장 — 단일 인스턴스(베타 규모)로 시작.

## 성공 기준
- 기존 25스위트 + 신규 test_cloud 오프라인 전부 통과, 로컬 모드 동작 불변.
- `docker run` 로컬에서 클라우드 모드 전 경로(홈→초안→완성본→세션 목록) 동작.
- uid 격리: 다른 uid의 세션 토큰으로 조회 불가(테스트로 고정).
- `wrangler deploy` 한 번으로 랜딩+앱이 올라가는 상태(인증만 남김).
