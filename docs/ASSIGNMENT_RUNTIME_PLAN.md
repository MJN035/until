# Until Local Assignment Runtime 계획

작성일: 2026-08-16  
상태: 계획 확정, 구현 전
제품 방향: **Local Agent only**

## 1. 한 문장 정의

Until은 사용자가 이미 구독하고 로그인한 로컬 AI 에이전트에게 과제 작업을 맡기고,
eTL 문맥 준비·작업공간 격리·결과 검증·제출 패키징을 담당하는 로컬 과제 런타임이다.

```text
eTL/브라우저
  → Until Local
  → 과제 정책·요구사항 컴파일
  → 격리 작업공간 생성
  → 로그인된 로컬 AI CLI 실행
  → 실제 파일 변경
  → 결정적 검사
  → 제출 패키지
```

## 2. 고정한 제품 결정

### 하는 것

- 사용자의 컴퓨터에서 실행되는 Until 로컬 앱/CLI
- 사용자가 공식 클라이언트에서 직접 로그인한 로컬 AI 에이전트 사용
- eTL 과제와 첨부를 로컬 작업공간으로 준비
- 에이전트가 읽을 구조화 명세와 허용 작업 생성
- 에이전트가 만든 실제 파일을 결정적 코드로 검증
- 검증된 제출 파일 묶음 생성

### 하지 않는 것

- Until 서버가 OpenAI·Anthropic 모델 API 호출
- Hosted 크레딧, BYOK, 모델별 유료 요금제
- Claude/ChatGPT 웹 세션 쿠키 추출 또는 구독을 비공식 API처럼 사용
- 사용자 OAuth·구독 토큰 저장 또는 프록시
- OMC를 학생 제품의 필수 런타임으로 사용
- Chromium 포크
- AI가 스스로 완료 판정 또는 실제 제출
- 학습 증명서·작성 감시

OMC는 **Until을 개발하는 팀의 도구**다. 제품 안에서는 OMC가 없어도 동작하는 작은
Local Agent 계약만 사용한다.

## 3. 사용자 경험

### 최초 1회

1. Until Local 설치
2. 지원되는 AI CLI 설치 여부 확인
3. 사용자가 해당 공식 CLI에서 직접 로그인
4. Until은 `version`, `status`, 최소 dry-run만 확인
5. eTL 읽기 연결

Until은 로그인 화면을 흉내 내거나 인증정보를 전달받지 않는다.

### 과제마다

1. 학생이 eTL 과제에서 `Until로 열기` 선택
2. Until이 과제·첨부·관련 강의자료를 로컬 workspace에 배치
3. 정책 엔진이 AI 허용 범위와 금지 작업 결정
4. 학생에게 실행 계획과 예상 변경 파일 표시
5. 학생이 `로컬 에이전트로 시작` 선택
6. 에이전트가 workspace 내부에서 작업
7. Until validator가 실제 결과 검사
8. 실패하면 정확한 파일·항목과 함께 같은 에이전트에 수정 요청
9. 통과하면 제출 bundle 미리보기
10. 제출은 기존 Submission Gate의 별도 확인 유지

## 4. 시스템 경계

```text
┌──────────────── Browser/eTL ────────────────┐
│ 과제 선택·상태 표시·제출 미리보기          │
└────────────────────┬────────────────────────┘
                     │ localhost, nonce
┌────────────────────▼────────────────────────┐
│ Until Local Daemon                          │
│ Capture / Policy / Workspace / Validator    │
│ Agent Runner / Bundle / Submission Gate     │
└───────────┬────────────────────┬─────────────┘
            │ subprocess         │ local files
┌───────────▼──────────┐  ┌──────▼────────────┐
│ Official Local Agent │  │ Runtime Workspace │
│ user-authenticated   │  │ assignment scoped │
└──────────────────────┘  └───────────────────┘
```

### 신뢰 경계

- 브라우저는 임의 명령을 보내지 않고 등록된 assignment ID만 전달한다.
- Local daemon은 loopback에만 바인딩하고 요청 nonce를 검증한다.
- Agent subprocess의 working directory는 해당 assignment workspace로 고정한다.
- workspace 밖 파일 접근과 심볼릭 링크는 실행 전에 차단한다.
- 환경변수는 allowlist 방식으로 전달하고 토큰성 변수는 모두 제거한다.
- 에이전트 stdout 전체를 클라우드에 업로드하지 않는다.
- 에이전트 성공 메시지는 신뢰하지 않고 validator 결과만 사용한다.

## 5. 핵심 계약

### 5.1 `LocalAgent`

초기 제품은 하나의 계약만 제공한다.

```python
class LocalAgent(Protocol):
    name: str
    def probe(self) -> AgentAvailability: ...
    def plan(self, job: AgentJob) -> AgentPlan: ...
    def execute(self, job: AgentJob, approval: Approval) -> AgentReceipt: ...
    def continue_job(self, receipt, feedback) -> AgentReceipt: ...
```

`LocalAgent`는 모델 API 추상화가 아니다. 사용자 컴퓨터에 설치되고 로그인된 공식 CLI를
제한된 subprocess로 실행하는 계약이다.

초기 구현은 **한 CLI adapter만 완성**한다. 두 번째 CLI 지원은 첫 adapter의 실제 사용자
검증 이후 같은 계약의 추가 구현으로만 허용한다. 제품·결제·UX 경로는 나누지 않는다.

### 5.2 `AgentJob`

- assignment ID와 workspace 상대 경로
- 컴파일된 과제 요구사항
- 적용된 정책과 허용 작업
- 수정 가능한 파일 allowlist
- 읽기 가능한 자료 allowlist
- 금지 작업
- 예상 산출물
- timeout과 최대 반복 횟수
- validator 명령 목록

### 5.3 `AgentReceipt`

- 실행 시작·종료 상태
- 변경된 파일 상대 경로
- 실행한 허용 도구 종류
- exit status
- 중단 또는 사용자 입력 필요 이유
- stdout/stderr의 제한된 진단 요약

모델의 자연어 자기평가는 receipt의 성공 근거가 아니다.

### 5.4 `RuntimePlugin`

과제 유형별 플러그인은 동일한 생명주기를 구현한다.

```python
class RuntimePlugin(Protocol):
    def supports(self, request) -> SupportDecision: ...
    def prepare(self, request) -> WorkspacePlan: ...
    def build_job(self, workspace) -> AgentJob: ...
    def validate(self, workspace, receipt) -> ValidationResult: ...
    def repair_feedback(self, validation) -> AgentFeedback: ...
    def package(self, workspace, validation) -> SubmissionBundle: ...
```

## 6. 기존 코드 연결

| 기존 자산 | Local Runtime 역할 |
|---|---|
| `until/capture/` | eTL 원문·첨부 입력 |
| `assignment_router.py` | RuntimePlugin 선택 |
| `policy_hierarchy.py` | AgentJob 허용·금지 작업 |
| `academic_graph.py` | 필요한 강의자료 관계 선택 |
| `structured_assignment.py` | 코드·Rmd·ZIP 구조 |
| `formfill.py` | 양식 validator와 package |
| `presentation_export.py` | PPTX validator와 package |
| `measured_check.py` | 실행 근거 없는 수치 차단 |
| `readiness.py` | 공통 validation |
| `submission_gate.py` | 최종 bundle 경계 |

## 7. 단계별 구현

### Phase 0 — Local Agent 계약 고정

목표: 공식 CLI를 실행하기 전에 경계와 실패 상태를 테스트로 고정한다.

예정 파일:

- `until/runtime/models.py`
- `until/runtime/local_agent.py`
- `until/runtime/security.py`
- `tests/test_local_agent_contract.py`

작업:

1. `AgentAvailability`, `AgentJob`, `AgentPlan`, `AgentReceipt` 정의
2. 상태를 `unavailable`, `login_required`, `ready`, `busy`, `failed`로 제한
3. 가짜 CLI fixture로 probe/plan/execute/continue 계약 테스트
4. workspace 밖 경로, 심볼릭 링크, 미승인 환경변수 차단
5. 실행 전 승인 객체 없이는 subprocess 0회 보장
6. 출력 크기, timeout, 취소 계약 정의

완료 조건:

- 가짜 에이전트만으로 모든 계약 테스트 통과
- 로그인 필요와 CLI 미설치를 구분
- 인증정보가 job, receipt, 로그에 들어가지 않음
- 기존 73개 테스트와 결정성 기준선 통과

### Phase 1 — Local Runtime Kernel

목표: 과제를 안전한 workspace와 AgentJob으로 바꾸는 공통 실행기를 만든다.

예정 파일:

- `until/runtime/workspace.py`
- `until/runtime/registry.py`
- `until/runtime/orchestrator.py`
- `until/runtime/manifest.py`
- `tests/test_runtime_kernel.py`

작업:

1. `_until_work/runtime/<assignment>/<run>` 작업공간 생성
2. 입력 파일을 read-only 영역, 작업 파일을 editable 영역으로 분리
3. 파일 상대 경로와 SHA-256만 manifest에 저장
4. route에 맞는 RuntimePlugin 선택
5. 정책을 AgentJob 허용·금지 규칙으로 변환
6. `prepare → plan preview → approval → execute → validate` 구현
7. validator 실패를 최대 1회 repair feedback으로 변환
8. 반복 후에도 실패하면 사람에게 중단 이유 반환

완료 조건:

- mock assignment 전체 생명주기 통과
- 승인 전 실행 0회
- 동일 입력은 동일 WorkspacePlan과 AgentJob 생성
- workspace 밖 수정 감지 시 즉시 block
- 에이전트가 실패해도 원본 입력 보존

### Phase 2 — 최초 공식 CLI Adapter

목표: 사용자가 구독으로 로그인한 공식 로컬 CLI 하나를 실제 연결한다.

원칙:

- 공식 설치·로그인 흐름만 사용
- 세션 파일·쿠키·OAuth token을 Until이 읽지 않음
- CLI가 제공하는 비대화형/구조화 출력 범위 안에서만 자동화
- 공식적으로 지원되지 않는 subscription automation은 구현하지 않음

작업:

1. 설치 및 버전 probe
2. 사용자가 직접 로그인했는지 CLI status로만 확인
3. 명시된 workspace와 prompt file로 실행
4. auto-approve 대신 Until plan 승인 사용
5. 종료 코드·변경 파일·제한된 로그를 receipt로 변환
6. 취소와 timeout 처리
7. subscription limit 도달 시 결제 우회 없이 `usage_limited`

완료 조건:

- 실제 구독 CLI에서 샘플 workspace 파일 수정 성공
- 미로그인·사용량 제한·timeout·사용자 취소 구분
- Until 측 모델 API 호출과 API 비용 0
- 인증정보 저장 0

### Phase 3 — Report Runtime MVP

목표: 일반 보고서 한 종류에서 Local Agent 제품 가치를 검증한다.

지원 범위:

- eTL 과제와 관련 강의자료 workspace 준비
- 필수 섹션·분량·인용·첨부 검사
- Markdown/DOCX 결과
- 교수 제공 DOCX/HWPX 양식 연결
- 가치판단·개인 경험은 기존 `[[DECISION]]` 경계 유지

작업:

1. `ReportRuntime` 구현
2. 과제 명세·자료·작성 파일을 AgentJob으로 구성
3. 에이전트가 workspace의 초안 파일만 수정
4. requirement trace, citation coverage, readiness 검증
5. 실패 1회 자동 수정 후 중단
6. 검증된 문서와 필수 첨부를 SubmissionBundle로 생성

완료 조건:

- 실제 과거 과제 10건 중 지원 가능한 건에서 workspace 준비 성공률 90% 이상
- 필수 항목·첨부·결정 표식 누락 100% 차단
- 과제 시작부터 첫 수정 가능한 파일까지 사용자 조작 3회 이하
- 같은 작업을 Claude/ChatGPT에 수동 전달하는 기준 대비 준비시간 70% 이상 감소

### Phase 4 — Submission Bridge

목표: Report Runtime이 중간 결과 생성기로 끝나지 않게 한다.

작업:

1. bundle 파일명·확장자·MIME·개수 검사
2. validator 통과 artifact hash를 nonce에 결합
3. 검증 후 변경된 파일은 nonce 무효
4. dry-run 제출 요청 미리보기
5. 실제 제출은 기존 다중 확인 유지

완료 조건:

- 잘못된 파일·과제·형식 차단
- 기본 네트워크 POST 0회
- runtime block 시 nonce 발급 0회

### Phase 5 — 실제 사용자 검증 게이트

다음 RuntimePlugin 개발 전에 보고서 MVP로 검증한다.

측정:

- 과제 열기 → 작업 시작까지 시간
- 제출 bundle까지 클릭·복사·업로드 수
- Local Agent 실행 성공률
- validator 실패 후 자동 수정 성공률
- 다음 과제 재사용 여부

진행 기준:

- 3명 이상이 각 2개 이상의 실제 과제에 사용
- 60% 이상이 다음 과제에서 재사용
- 준비시간 중앙값 70% 이상 감소
- API 원가 0원 유지

기준 미달이면 Code/HDL 확장을 중단하고 설치·로그인·작업공간 UX를 먼저 수정한다.

### Phase 6 — Code/HDL Runtime

보고서 검증 게이트 통과 후에만 시작한다.

- Python과 HDL을 별도 validator로 지원
- Local Agent가 코드를 작성하되 Until이 테스트·시뮬레이션 실행
- 허용 명령, timeout, network-off 기본
- 실행 artifact 없는 수치·파형·결과 차단
- starter code와 필수 프로젝트 구조 보존

### Phase 7 — Data/Rmd, Presentation/Form 확장

Code/HDL 안정화 후 순차 진행한다.

- Data/Rmd: 코드·표·그래프·보고서 수치 일치
- Presentation: PPTX 구조·슬라이드 수·발표 시간
- Form: DOCX/HWPX/HWP 필수 필드와 템플릿 보존

새 플러그인은 LocalAgent 계약을 변경하지 않는다.

## 8. 비용 구조

Until의 모델 API 비용은 항상 0이다.

Until이 부담하는 비용:

- eTL 동기화와 최소 메타데이터 서버
- 앱 업데이트·결제·계정
- 선택적 암호화 백업

사용자의 AI 사용량은 사용자가 선택한 공식 구독의 제한을 따른다. 사용량이 끝나면 Until이
별도 API로 몰래 전환하지 않고 기다림 또는 구독 서비스의 공식 안내만 보여준다.

판매 대상은 토큰이 아니라 다음이다.

- eTL 자동 문맥 준비
- 과제별 안전 workspace
- RuntimePlugin과 validator
- 제출 패키징
- 과목별 플레이북

## 9. OMC 구현 작업 경계

CLI 재시작 후 OMC는 개발에만 사용한다.

| lane | 소유 범위 |
|---|---|
| contract | `runtime/models.py`, `local_agent.py`, 계약 테스트 |
| security | `runtime/security.py`, 경로·환경·subprocess 적대 테스트 |
| workspace | `runtime/workspace.py`, `manifest.py`, fixture |
| verifier | 기존 경계 회귀·결정성·비밀정보 검사 |

`pipeline.py`, `web.py`, `asgi.py`, `submission_gate.py`, `run_tests.py`는 leader만 수정한다.
Phase 0 계약 통합 전에는 실제 공식 CLI adapter를 구현하지 않는다.

## 10. 첫 OMC 세션 범위

**Phase 0과 Phase 1만 구현한다.**

종료 조건:

- LocalAgent 계약과 상태 모델 완성
- mock agent 기반 작업공간 생명주기 완성
- 승인 전 subprocess 0회
- path escape·symlink·secret env 차단
- 기존 73개 테스트, 신규 테스트, ruff, 결정성, CI 통과
- 작은 단위 커밋·푸시

## 11. CLI 재시작 프롬프트

```text
docs/ASSIGNMENT_RUNTIME_PLAN.md를 전부 읽고 git 상태를 확인해줘.
제품 방향은 Local Agent only다. Hosted API, BYOK, OMC 제품 의존성은 추가하지 마.
OMC 팀으로 Phase 0과 Phase 1만 구현해. contract, security, workspace, verifier lane을
분리하고 공유 파일은 leader만 수정해. mock agent로 승인 전 실행 0회, workspace 탈출
차단, secret env 제거, 결정성을 검증해. 전체 테스트·커밋·푸시·PR CI까지 완료해.
```
