# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-08-20
- Primary product surfaces: 홈, 계정 로그인, 과제 연결/등록, 초안, 완성본, 소개
- Evidence reviewed: `docs/DESIGN_SYSTEM.md`, `until/web.py`, `until/webassets/app.css`, `deploy/landing/public/index.html`, Kakao Login 디자인 가이드, Google Sign in 브랜딩 가이드

## Brand
- Personality: 조용하고 실용적인 대학생 작업 도구. 연습장처럼 친숙하고 편집물처럼 정돈되어야 한다.
- Trust signals: 실제 과제명·마감·저장 상태를 먼저 보여 주고, 외부 로그인은 각 제공자의 공식 브랜드 자산을 사용한다.
- Avoid: 챗봇식 그라데이션, 반짝임, 과도한 카드, 이모지 라벨, 의미 없는 영어 대문자, AI 능력을 과장하는 문구, 임의로 다시 그린 타사 로고.

## Product goals
- Goals: 사용자가 가장 가까운 과제를 빠르게 시작하고, 만든 결과를 계정에 안전하게 이어 저장하게 한다.
- Non-goals: AI 자체를 주인공으로 만들기, SNS형 피드, 장식적인 대시보드.
- Success signals: 첫 행동까지의 시간이 짧고, 로그인 제공자를 즉시 식별하며, 저장·개인정보 범위를 오해하지 않는다.

## Personas and jobs
- Primary personas: eTL을 사용하는 대학생, 여러 기기에서 과제를 이어 하는 학생.
- User jobs: 해야 할 과제를 찾기, 요구사항을 이해하기, 초안을 만들기, 직접 판단할 부분을 결정하기, 제출 파일로 정리하기.
- Key contexts of use: 노트북 중심, 마감 직전의 짧은 세션, 모바일에서 상태 확인과 간단 입력.

## Information architecture
- Primary navigation: 홈의 단일 eTL 주요 행동과 `둘러보기 / 계정·설정` 하단 도구 메뉴. 운영 서비스의 새 작업은 eTL 토큰 연결을 통해서만 시작한다.
- Core routes/screens: `/`, `/login`, `/connect`, `/about`, `/plan`, `/consent`, `/sessions`, `/archive`. `/new`와 `/simple`은 로컬 개발용으로만 유지한다. 옛 `/demo`(작동 예시)는 2026-08-21에 없애고 `/about`으로 영구 이동시켰다 — 소개가 같은 5단계를 이미 보여 준다.
- Content hierarchy: 현재 과제와 다음 행동 → 과제·자료 입력 → 직접 결정 → 완성본 → 제출 직전 점검·파일 확인. 경계선 초안은 내부에서 준비하고 질문 전에는 본문을 펼치지 않는다.

## Design principles
- 실제 작업을 먼저 보여 준다: 브랜드나 AI 설명보다 과제·마감·남은 판단을 우선한다.
- 질문부터 시작한다: 초안 생성은 내부 과정이며, 학생 화면은 과제 설명이나 초안 본문보다 직접 답할 결정부터 보여 준다.
- 익숙한 표준을 존중한다: 로그인과 결제 등 신뢰가 필요한 표면은 공급자 공식 UI 자산과 명칭을 사용한다.
- Tradeoffs: 작업 화면은 각진 종이/잉크 체계를 유지하되, 타사 로그인 버튼은 브랜드 가이드의 radius와 색상을 예외로 허용한다.

## Visual language
- Color: 종이 `#faf9f5`, 잉크 `#191813`, 형광펜 노랑, 마감 빨강. 타사 브랜드 색상은 해당 버튼 안에서만 사용한다.
- Typography: 본문은 Pretendard/시스템 산세리프, 날짜·숫자·작은 상태는 모노.
- Spacing/layout rhythm: 8px 기반, 넉넉한 세로 여백, 720px 본문 폭. 로그인 선택지는 360px 안에서 정렬한다.
- Shape/radius/elevation: 제품 UI는 기본 radius 0·그림자 없음. Kakao/Google 로그인 버튼만 공식 가이드에 따라 radius를 사용한다.
- Motion: 짧은 형광펜 진입 외에는 최소화하며 `prefers-reduced-motion`을 따른다.
- Imagery/iconography: 작업 화면에는 장식 이미지를 넣지 않는다. 실제 기능·브랜드를 식별하는 공식 로고만 쓴다.

## Components
- Existing components to reuse: `.bar`, `.wrap`, `.sec`, `.btn`, `.meta`, 디자인 토큰.
- New/changed components: `.auth-shell`, `.auth-actions`, `.auth-provider`, `.auth-google`, `.auth-privacy`, `.home-tools`, `.home-resume`, `.utility-page`, `.page-head`, `.task-form-section`, `.setting-status`, `.demo-flow`, `.demo-stage`.
- Variants and states: Kakao 노랑 버튼, Google 라이트 버튼, 공급자 미설정 안내, 허용 도메인 안내, OAuth 오류.
- Token/component ownership: 공통 토큰은 `until/webassets/app.css`; 로그인 마크업은 `until/web.py`; 공식 PNG는 `until/webassets/`.

## Accessibility
- Target standard: WCAG 2.1 AA.
- Keyboard/focus behavior: 모든 로그인 링크에 명확한 `focus-visible` 외곽선을 제공한다.
- Contrast/readability: Kakao/Google 공식 색상과 텍스트 대비를 유지하고 로고 색상을 변경하지 않는다.
- Screen-reader semantics: 공급자 링크에 구체적인 `aria-label`을 두고 장식용 Google 아이콘은 숨긴다.
- Reduced motion and sensory considerations: 로고와 버튼은 모션 없이 표시한다.

## Responsive behavior
- Supported breakpoints/devices: 1440px, 768px, 390px.
- Layout adaptations: 로그인 버튼은 최대 360px, 좁은 화면에서는 가용 폭 100%를 사용한다.
- Touch/hover differences: 최소 45px 높이를 확보하고 hover 없이도 공급자와 행동이 명확해야 한다.

## Interaction states
- Loading: OAuth 공급자 페이지로 이동하는 기본 탐색을 사용한다.
- Empty: 공급자 자격정보가 없으면 관리자 설정 안내와 홈 링크를 표시한다.
- Error: OAuth 오류는 버튼 위에 짧고 구체적으로 표시한다.
- Success: 원래 `next` 경로로 복귀하고 상단 계정 슬롯으로 로그인 상태를 확인한다.
- Disabled: 설정되지 않은 공급자는 렌더링하지 않는다.
- Offline/slow network, if applicable: 정적 브랜드 자산은 앱에 번들하고 캐시해 외부 CDN에 의존하지 않는다.

## Content voice
- Tone: 짧고 구체적인 한국어. 무엇이 저장되고 어떤 권한을 쓰지 않는지 직접 말한다.
- Terminology: “AI 로그인”이 아니라 “Kakao/Google로 계속하기”, “계정에 저장”, “지금은 로그인하지 않기”.
- Microcopy rules: 화살표·이모지·영어 장식 라벨을 반복하지 않고, 한 문장에 한 약속만 둔다.

## Implementation constraints
- Framework/styling system: 서버 렌더링 Python HTML + 정적 CSS/JS, FastAPI와 stdlib 서버 양쪽 지원.
- Design-token constraints: 기존 `app.css` 토큰을 재사용하고 새로운 전역 디자인 계층을 만들지 않는다.
- Performance constraints: 로고는 로컬 PNG로 번들하고 외부 런타임 요청을 추가하지 않는다.
- Compatibility constraints: CSP를 유지하고 인라인 스크립트나 외부 로그인 SDK를 추가하지 않는다.
- Test/screenshot expectations: 정적 자산 응답, 로그인 마크업, 보조 페이지의 공통 정보 구조, `/about`과 실제 5단계 작업 흐름의 일치, 데스크톱·390px, 라이트·다크 모드를 확인한다.

## Open questions
- [ ] 실제 사용자 관찰에서 Kakao와 Google 버튼 순서가 로그인 완료율에 미치는 영향 / 제품 / 낮음
