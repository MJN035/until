# Until Cloud 배포

> **현재 운영 경로(무료): Render + Cloudflare KV.** 리포 루트의 `render.yaml`
> Blueprint로 배포한다 — render.com → New → Blueprint → 이 저장소 → Apply →
> 시크릿 입력. 무료 플랜은 15분 유휴 시 잠들고 콜드스타트 ~30초(KV 미러로
> 데이터는 유지). 랜딩은 Cloudflare(무료, 아래 3절).
>
> ⚠ **`UNTIL_SESSION_KEY` 필수** — 세션 서명 키(v2 서명 JSON). 미설정이면
> 재시작마다 랜덤 키가 새로 생겨 KV에서 복원한 세션이 전부 서명 불일치로
> 유실된다(2026-08-05 실측·수정). 32바이트+ 무작위로 1회 설정, 이후 교체 금지.
>
> 아래 Cloudflare Containers 경로는 **Workers Paid($5/월) 활성화 시** 콜드스타트
> 수 초로 업그레이드하는 이사 옵션: `npx wrangler login` 후
> `powershell -File deploy/deploy.ps1` 한 번.

구성 요소 둘 — 각각 독립 wrangler 타깃, 자체 workers.dev URL:

| 타깃 | 내용 | 요금 |
|---|---|---|
| `deploy/landing/` | 정적 랜딩 페이지(Workers Assets) | 무료 |
| `deploy/app/` | 얇은 Worker → **Container**(Python 앱 통째) | Workers Paid **$5/월** |

## 0. 준비물
- Cloudflare 계정 + **Workers Paid 플랜**(Containers 필수)
- 로컬 **Docker 실행 중**(이미지 빌드는 배포 시 로컬에서 일어남)
- `wrangler login` 1회(브라우저 클릭)
- Groq API 키(무료: console.groq.com)

## 1. KV 네임스페이스(세션·히스토리 지속화)
```sh
npx wrangler kv namespace create UNTIL_KV
# 출력의 id를 메모 → UNTIL_KV_NAMESPACE 값
```
Cloudflare 대시보드에서 **API 토큰** 생성: 권한 `Account > Workers KV Storage > Edit`
(계정 한정, 만료 없이). → `UNTIL_KV_TOKEN` 값. 계정 ID(대시보드 우측)는
`UNTIL_KV_ACCOUNT` 값.

## 2. 앱 배포
```sh
cd deploy/app
npm install                      # @cloudflare/containers
npx wrangler secret put UNTIL_API_KEY        # Groq 키
npx wrangler secret put UNTIL_KV_ACCOUNT     # 계정 ID
npx wrangler secret put UNTIL_KV_NAMESPACE   # KV 네임스페이스 id
npx wrangler secret put UNTIL_KV_TOKEN       # KV API 토큰
npx wrangler secret put UNTIL_BETA_CODE      # (선택) 초대 코드 — 걸면 클로즈드 베타
npx wrangler secret put UNTIL_ADMIN_KEY      # (선택) 관리자 보드 로그인 키
npx wrangler deploy
```
배포 URL 예: `https://until-app.<계정>.workers.dev` → `/healthz`가 `ok`면 정상.

## 3. 랜딩 배포
```sh
cd deploy/landing
npx wrangler deploy
```
`public/index.html`의 앱 링크(`data-app-url`)를 2에서 나온 앱 URL로 맞춘다.

## 4. 확인 체크리스트
- [ ] `GET /healthz` → ok
- [ ] 홈 → 간단 모드 → 붙여넣기 → 초안(라이브 백엔드)
- [ ] 다른 브라우저(시크릿)에서 `/sessions`가 서로 안 보임(uid 격리)
- [ ] 컨테이너 잠들었다 깨어나도(15분+) `/sessions`·`/history` 유지(KV)
- [ ] `UNTIL_BETA_CODE` 설정 시 초대 코드 게이트 동작
- [ ] `/admin` POST 로그인 → 쿠키 인증 후 보드 200

## 운영 메모
- **절대 설정 금지:** `UNTIL_CANVAS_TOKEN`(운영자 eTL 계정이 전 사용자에게 노출됨 —
  코드에서도 클라우드 모드는 이 env를 무시한다).
- **모델·한도 전략:** 429(한도 소진) 시 같은 제공자 안에서 자동 강등(Groq:
  70b→8b, env `UNTIL_MODEL_FALLBACK`로 사슬 커스텀), 2차 제공자(`UNTIL_BASE_URL_2/
  UNTIL_API_KEY_2/UNTIL_MODEL_2`)가 있으면 거기까지 자동. **권장 구성**(품질·토큰
  최대, 전부 무료): 주=Cerebras `qwen-3-235b-a22b-instruct-2507`(1M 토큰/일,
  cloud.cerebras.ai 카드 불필요) → 2차=Groq 70b → 8b. render.yaml 주석 참고.
- 전역 일일 초안 상한 `UNTIL_GLOBAL_DAILY_DRAFTS`(기본 200)·1인 `UNTIL_FREE_DRAFTS`(기본 5).
- **채널별 초대 코드:** `UNTIL_BETA_CODE=SNU-EARLY,SNU-ETA,SNU-KATALK`처럼 쉼표로 복수.
  통과 시 서버 로그에 `[beta] pass: <코드>`가 남아 호스팅 로그에서 채널별 유입 집계.
  코드 하나를 목록에서 빼면 그 채널 쿠키만 무효(코드별 해시).
- `/healthz` 응답에 배포 커밋이 병기됨(`ok abc1234`) — 재배포 반영 여부 즉시 확인.
- SSO(Playwright)·GEPA는 클라우드 이미지에서 제외 — 로컬 전용 기능.
- 로컬 스모크(배포 전 검증):
  ```sh
  docker build -f deploy/Dockerfile -t until-app .
  docker run --rm -p 8080:8080 until-app     # http://localhost:8080 (mock 백엔드)
  ```
- `@cloudflare/containers`/wrangler의 containers 필드는 아직 진화 중 — 배포 에러 시
  `npx wrangler --version` 최신화 후 [공식 문서](https://developers.cloudflare.com/containers/) 대조.
