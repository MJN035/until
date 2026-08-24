---
name: until-smoke
description: Until Cloud 라이브 배포 스모크 체크 — 배포/재배포 직후 healthz·베타 게이트·초안 생성·PDF 파싱·uid 격리를 curl로 검증. "라이브 확인해줘", "배포 잘 됐어?", "스모크 돌려줘" 요청 시 사용.
---

# Until Cloud 라이브 스모크

앱: `https://until-app.onrender.com` (Render 무료 — 15분 유휴 후 첫 요청 ~30초 콜드스타트).
랜딩: `https://until-landing.minjun05.workers.dev`.
main 푸시마다 Render가 자동 재배포한다(빌드 ~5분). **healthz 200은 "어떤 버전이든 떠
있다"일 뿐 새 빌드 증거가 아니다** — 버전 확인은 4번처럼 새 코드의 표식으로 한다.

## 절차 (Bash, cookie jar는 /tmp)

1. **healthz**: `curl -s -w '%{http_code}' https://until-app.onrender.com/healthz` → `ok` + 200.
   콜드스타트 대비 `--max-time 90`.
2. **베타 게이트**: 쿠키 없이 `GET /` → 403 게이트 페이지.
   `POST /beta --data 'code=SNU-EARLY'` → 303 + `uid`/`beta` Set-Cookie (jar 저장).
3. **라이브 초안**(Groq 토큰 소모 ~1만 — 하루 한도 주의):
   `POST /draft` (jar, `-F 'assignment=...' -F 'ui=simple'`) → 303 `location: /sv/<token>`.
   429/한도면 8b 폴백이 처리하는지 확인(그래도 303이어야 정상).
4. **PDF 파싱 검증**(pymupdf 빌드 확인 겸): 최소 PDF를 `-F 'files=@t.pdf'`로 첨부 →
   `GET /readiness/<token>.json`에 '자료' 스킵 경고가 **없어야** 한다(있으면 구 빌드/파서 문제).
   최소 PDF 생성 스크립트는 과거 세션 참고 — 수기 xref로 ~600바이트면 충분.
5. **uid 격리**: 새 jar로 `/beta` 통과(다른 uid) → `GET /sessions`에 3번 토큰 없음,
   `GET /sv/<token>` → 404, 쿠키 없이 → 403.
6. **정리**: 테스트로 만든 세션은 사용자에게 알리기(A 계정 /sessions에 남음 — 지워도 됨).

## 판정

- 전부 통과 → "스모크 통과" + 표로 요약.
- 3번이 500이면 본문에서 `처리 중 오류` 메시지를 추출해 원인 보고(과거: openai 미설치).
- 재배포 대기 폴링이 필요하면 45초 간격 백그라운드 루프(포그라운드 sleep 금지).
