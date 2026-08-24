---
name: until-release
description: Until 릴리스 게이트 — 버전 태깅 전 테스트·리뷰·CHANGELOG·문서 정합 체크리스트를 순서대로 실행. "릴리스 준비", "vX.Y.0 태깅", "게이트 돌려줘" 요청 시 사용.
---

# Until 릴리스 게이트

이 리포의 릴리스 관행(v0.2.0부터 이어진 것). 하나라도 실패하면 태깅하지 않는다.

## 순서

1. **전체 테스트**: `PYTHONIOENCODING=utf-8 python run_tests.py` — 전 스위트 PASS.
   스위트 수가 늘었으면 test_runners AST 감사가 등록 누락을 잡아준다.
2. **게이트 리뷰**: 이번 트랙의 diff를 코드 리뷰(에이전트 가능하면 에이전트, 한도
   소진 시 인라인). 실버그만 — 스타일 지적 제외. 발견 → 수정 → 회귀 테스트 고정.
3. **웹 워크스루**(웹 표면이 바뀐 릴리스만): mock 서버 띄워 홈→초안→제안→점검→
   finalize→다운로드→세션 목록 전 경로 클릭 체크. 클라우드 표면이 바뀌었으면
   /skill until-smoke 로 라이브도.
4. **CHANGELOG**: Unreleased 절을 `## [X.Y.0] — 날짜 · "별칭"`으로 승격, 빈
   Unreleased 절 재생성. 별칭은 릴리스 성격 한 구(예: "구름 위로, 안전하게").
5. **문서 정합**: README(기능·테스트 수), docs/FEATURES.md(기능→코드→테스트 지도),
   CLAUDE.md 상단 상태 배너 갱신. AGENTS.md는 배너만(본문은 아카이브).
6. **커밋 + 태그**: `git commit` 후 `git tag vX.Y.0`.
7. **푸시는 사용자 승인 후**: `git push origin main --tags` — 관행상 물어보고 실행.

## 주의

- Render는 main 푸시로 자동 재배포된다 — 푸시 = 배포임을 사용자에게 상기.
- 비밀(Groq 키·eTL 토큰)이 diff에 없는지 마지막으로 훑기.
- 클라우드에 `UNTIL_CANVAS_TOKEN` 절대 설정 금지(코드도 차단하지만 문서에서도 유지).
