# Until 디자인 시스템 — "흰 지면 위의 도구"

단일 소스: 이 문서. 적용 표면 셋 — **앱**(`until/web.py`의 `_PAGE` CSS `:root`),
**랜딩**(`deploy/landing/public/index.html`), (향후) 제출용 HTML. 값을 바꿀 땐
세 곳을 함께 바꾸고 이 표를 갱신한다.

> 2026-07-26 리테마: 크림+테라코타(클로드풍이라는 사용자 피드백) →
> **화이트 + 딥 그린**. 랜딩이 기준이고 앱이 랜딩을 따른다(폰트 포함).

폰트: 앱·랜딩 모두 **산세리프 단일**(Pretendard, 시스템 폴백). 앱은 CDN
(cdn.jsdelivr.net) 링크 + 폴백 — 클라우드 CSP는 이 오리진을 명시 허용해야
한다(`web.py send_response`). 세리프(명조) 헤딩은 폐기 — `--serif` 변수는
하위호환을 위해 남아 있으나 값은 산세리프 스택과 동일하다.

## 원칙
1. **흰 지면**: 순백 배경 + 연회색 밴드(`--paper`) + 헤어라인.
2. **헤딩은 산세리프 볼드**(700~800) — 랜딩·앱 동일(제품형 SaaS 인상).
3. **강조는 딥 그린 한 색**: 액션·강조·인용 마커 전부 `--accent` 하나로.
   경고/성공만 별도(`--warn`/`--ok`). 색이 셋을 넘으면 잘못 가고 있는 것.
   **주황/테라코타 금지**(사용자 지시 — 클로드 연상 회피).
4. **사진은 중화**: 웜톤 사진은 grayscale 필터로 뉴트럴하게.
5. **모션은 장식이 아니라 안내**: 로딩·리빌만. `prefers-reduced-motion` 존중.
6. **다크모드는 그린 틴트 차콜** — 시스템 기본 + 토글(`data-theme`) 오버라이드.

## 1층 — Primitive (원값)

| 토큰 | Light | Dark | 용도 힌트 |
|---|---|---|---|
| ink | `#1d1f1c` | `#e8eae6` | 잉크(본문 글자) |
| bg / page | `#ffffff` | `#212420` | 지면 |
| paper | `#f4f5f1` | `#2a2e29` | 한 겹 위 밴드(패널) |
| field | `#ffffff` | `#2f342e` | 입력칸 |
| muted | `#676b64` | `#9aa198` | 보조 글자 |
| line | `#e5e7e1` | `#3a4038` | 옅은 괘선 |
| rule | `#d2d6cf` | `#4a514a` | 진한 괘선 |
| accent | `#2f6b4f` | `#7ec8a3` | 딥 그린 |
| accent-2 | `#24543e` | `#97d6b5` | 그린 호버 |
| warn | `#b0483b` | `#e2726b` | 경고(레드 계열 — 주황 아님) |
| ok | `#3f8a63` | `#84b598` | 충족/성공 |
| ov | `rgba(255,255,255,.93)` | `rgba(33,36,32,.94)` | 오버레이 |

랜딩 전용 별칭: `--head #1f3328`(헤딩 잉크 — 딥 그린 블랙),
`--accent-soft #e7f0ea`(STEP 배지 배경), `--band/--band2`(paper 계열).

서체 스택:
- `--sans`(= `--serif`) `'Pretendard Variable',Pretendard,-apple-system,'Segoe UI','Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif`
- `--mono` `ui-monospace,'SF Mono','JetBrains Mono','D2Coding',Consolas,monospace`

## 2층 — Semantic (용도 별칭)

현재 CSS 변수 이름이 곧 시맨틱 층이다(1:1). 새 용도가 생기면 primitive를 직접
쓰지 말고 별칭을 추가한다. 예: 결정 지점 강조 = `--accent`(별도 색 금지),
D-day 임박 = `--warn`, 분량 충족 = `--ok`.

## 3층 — Component (컴포넌트 규약)

| 컴포넌트 | 규약 |
|---|---|
| `.btn` | bg `--accent`, hover `--accent-2`, ghost 변형은 투명+`--rule` 테두리 |
| `.lab` | 모노 소문자 라벨 + `--rule` 헤어라인(`.ln`) — 섹션 머리 공통 |
| `.pill` | `--line` 테두리 배지. 경고 상태는 색·테두리만 `--warn`으로 |
| `.sec` | 지면 위 섹션. 테두리는 `--line` 이하 강도만 |
| `.tgsec` | 접이식(details). 열림 상태 마커는 `--accent` |
| 입력(`input,textarea`) | bg `--field`, 포커스 테두리 `--accent` |
| `.cite`(인용 강조) | `--accent` 계열 배경 틴트, 본문 잉크 유지 |
| 오버레이 | `--ov` + 모노 안내 문구 |
| 사진(`.ph img`) | `grayscale(1)` 중화(라이트), 다크는 +`brightness(.78)` |

## 접근성 체크(유지 조건)
- 본문 대비: ink/bg ≥ 15:1(라이트) — AA 이상 유지.
- muted/bg ≥ 4.5:1 근처 유지(보조 글자도 읽히게).
- `--accent #2f6b4f` 위 흰 글자(버튼) ≈ 5.9:1 — AA 통과. 버튼 글자 굵게(600+).
- 포커스 가시성: 입력·버튼에 `--accent` 계열 표시 유지.
- `prefers-reduced-motion: reduce`에서 리빌·모션 제거(앱 구현 준수).

## 표면 간 드리프트 점검
```bash
# 앱과 랜딩의 토큰 블록을 눈으로 대조(값 검색)
grep -n "accent:#" until/web.py deploy/landing/public/index.html
```
