# Until MVP — 구조설계 문서

> ⚠️ **용어 주의:** 이 문서에서 데이터 파싱 단계는 **"Capture(파싱)"**로 부른다. 서울대 LMS **"eTL"**(과제를 가져오는 소스)과 혼동 금지. 과거 초안에서 파싱 단계를 "ETL"로 표기했던 흔적이 있으면 모두 Capture를 뜻한다.


**한 줄 정의.** Until은 대학생의 과제·잡무를 *사람의 판단이 필요한 경계선 직전*까지 대신 끝내 주는 AI 에이전트다. 핵심 개념은 **Draft 경계선** — 자료로 채울 수 있는 모든 것은 끝까지 작성하되, 고유한 판단·관점·가치판단이 필요한 지점은 넘지 않고 사람에게 넘긴다.

이 문서는 (1) 기술 스택 추천과 근거, (2) 파이프라인 구조, (3) 핵심 설계 결정, (4) 오픈소스 전략, (5) 이틀 스프린트 플랜을 담는다. 코드 스캐폴드는 `until/` 아래에 동작하는 형태로 들어있다.

---

## 1. 기술 스택 추천과 근거

**Python + 얇은 자체 에이전트 하네스 + 결정적(no-token) Capture(파싱) 레이어.**

| 결정 | 선택 | 근거 |
|---|---|---|
| 언어 | Python 3.10+ | 문서 파싱·LLM SDK 생태계가 가장 두껍고, 너의 vibe coding 흐름과 맞음 |
| 에이전트 프레임워크 | **자체 얇은 하네스** (LangChain 등 미사용) | "다른 wrapper가 어떻게 포장됐는지" 공부하려면 한 줄 한 줄 읽히는 코드가 최선. 추상화 비용 없이 포크/오픈소스 공개 용이 |
| Capture/전처리 | PyMuPDF + 결정적 파서 | 너의 "토큰 안 쓰는 단계"가 여기에 깔끔히 대응. 비용 0, 재현 가능 |
| LLM 백엔드 | 교체 가능한 인터페이스 (`Mock` / `Anthropic`) | API 키 없이도 end-to-end 데모·테스트 가능. 키 넣으면 그대로 라이브 |

**왜 자체 하네스인가 (네 방향과의 연결).** 네가 적은 "남의 오픈소스 가져다 쓰고, 수정·강화 후 다시 오픈소스로 공개" 전략에는 *읽을 수 있고 고칠 수 있는* 코어가 필요하다. 거대 프레임워크를 포크하면 학습·개조 비용이 오히려 커진다. 그래서 코어는 직접 얇게 쓰고, 문서 파싱 같은 **잘 풀린 문제만 OSS를 가져다 끼운다**(아래 4절).

---

## 2. 파이프라인 구조

전체 제품 비전은 5단계: **Capture → Understanding → Execution → Personalization → Review.**
이번 MVP는 그중 **Execution까지 얇게 end-to-end**로 구현하고, Personalization·Review는 형태만 잡아둔 스텁이다.

```
파일(PDF/txt/md)
   │
   ▼  [1] Capture (파싱)  ── 결정적, 토큰 0
Document(정규화 텍스트 + 섹션 + 토큰추정)
   │
   ▼  [2] Understanding  ── LLM 1콜
TaskSpec(JSON: 산출물·요구사항·제약·deadline·open_questions)
   │
   ▼  [3] Execution      ── LLM 1콜
Draft(body + [[DECISION:...]] 마커)
   │
   ▼  [4] Boundary       ── 결정적, 마커 파싱
DecisionPoint[]  +  "다음에 뭐라고 프롬프트할지" 제안
```

코드 매핑:

| 단계 | 모듈 | 토큰 |
|---|---|---|
| Capture(문서 파싱) | `until/capture/ingest.py`, `models.py` | **0 (no-token 단계)** |
| Understanding | `until/understanding/task_spec.py` | LLM 1콜 |
| Execution | `until/execution/drafter.py` | LLM 1콜 |
| Boundary | `until/boundary/models.py` | 0 |
| 프롬프트 추천 | `until/prompts/suggest.py` | 0 |
| 오케스트레이션 | `until/pipeline.py`, `cli.py` | — |
| LLM 래퍼 | `until/llm/{base,anthropic_client,mock_client}.py` | — |

---

## 3. 핵심 설계 결정

**(a) Draft 경계선의 구현.** 모델은 산출물 본문을 끝까지 쓰되, 사람의 판단이 필요한 자리에 `[[DECISION: 무엇을 결정해야 하는지]]` 마커를 남기도록 시스템 프롬프트로 강제한다. `Draft.from_text()`가 이 마커를 파싱해 `DecisionPoint[]`로 분리한다. `crossed_boundary`(결정 지점 0개)는 에이전트가 *사람을 대체해버린* 위험 신호로 검출된다 — 즉 "경계선을 넘었는지"를 코드가 감지한다. 이게 메모리에 적힌 "그 사람인 순간은 건드리지 말라"는 원칙의 기술적 구현이다.

**(b) no-token 단계 분리.** Capture는 LLM을 절대 호출하지 않는다. PDF 적재·섹션화·토큰량 추정이 전부 결정적이라 무료·재현 가능하고, "PDF 등 모두 넣는 즈음"의 비용을 0으로 유지한다. 토큰은 Understanding/Execution에서만 쓴다.

**(c) "뭐라고 프롬프트할지 알려주기."** 각 결정 지점마다, 사용자가 직접 채울 때 모델에 던질 프롬프트 문장을 `suggest_prompts()`가 생성한다. 이게 네가 적은 "AI 공부와 병행" 포인트 — 사용자는 제안 프롬프트를 보며 프롬프팅을 학습한다.

**(d) auto-accept 모드.** `Config.auto_accept`(`UNTIL_AUTO_ACCEPT=1` 또는 `--auto-accept`). 켜면 단계별 확인 없이 통과 — 너의 "모두 수락하게 할 수도" 옵션.

**(e) 교체 가능한 LLM 래퍼.** 모든 단계는 `LLMClient.complete(system, user, tag, json)` 하나로만 모델과 대화한다. `MockClient`는 API 키 없이 결정적 응답을 돌려 오프라인 데모/테스트를 가능케 하고, `AnthropicClient`는 `ANTHROPIC_API_KEY`가 있으면 라이브로 동작한다. 이 인터페이스가 곧 "wrapper" — 다른 제품들의 래퍼와 비교하며 공부할 기준점이 된다.

### 세션 지속화와 신뢰 경계

웹 세션은 `_until_work/web_sessions/` 아래에 v2 JSON으로 저장한다. 봉투는
`v`, 저장 시각 `ts`, 표시용 `meta`, 실제 `payload`, HMAC-SHA256 `sig`로 구성된다.
서명은 정렬된 UTF-8 JSON payload 전체(표시 메타의 서명 사본 포함)에 적용한다.
키는 `UNTIL_SESSION_KEY`를 우선 사용하고, 로컬 무설정 실행에서는
`_until_work/.session_key`를 자동 생성한다.

`Result`와 그 하위 객체는 `until/session_store.py`가 타입별 고정 필드로 변환한다.
`__dict__` 순회나 범용 객체 직렬화는 사용하지 않으며, 새 타입은 명시적 변환기가
없으면 실패한다. 디스크 및 Cloudflare KV의 `sess:<uid>:<token>` 값은 동일한 서명
JSON bytes다. JSON 파싱 실패, 미지 버전, 서명 불일치는 손상 세션처럼 건너뛰며
pickle 런타임 fallback은 없다. 과거 신뢰된 로컬 파일은 서버와 분리된 일회성 도구
`tools/migrate_sessions_v1_to_v2.py`로만 변환한다.

---

## 4. 오픈소스 전략

너의 방향: **가져다 쓰고 → 수정·강화 → 다시 공개.**

- **가져다 쓸 OSS (코어 아님, 잘 풀린 문제):** PyMuPDF(PDF), 추후 `unstructured`/`docling`(복잡한 레이아웃), 인용·파싱 유틸.
- **직접 쓰는 코어 (공개 대상):** 얇은 에이전트 하네스 + Draft 경계선 모델 + 프롬프트 추천. 이게 Until의 차별점이자, 공부하며 다른 wrapper와 비교할 자산.
- **공개 형태:** `until/` 패키지를 그대로 라이선스 붙여 공개 가능(이미 단일 패키지·의존성 최소·Mock으로 키 없이 실행됨).

> 공부용 비교 대상 후보: 다른 "AI 비서/wrapper"들이 (a) 어디서 결정적 처리를 끝내고 (b) 어디서 LLM을 부르며 (c) 사람에게 무엇을 언제 넘기는지. 우리 코드의 `pipeline.py`가 그 비교의 기준 틀이 된다.

---

## 5. 이틀 스프린트 플랜

**Day 1 — 코어 동작 + 라이브 연결**
1. (완료) 스캐폴드: Capture → Understanding → Execution → Boundary, Mock으로 end-to-end 통과.
2. `ANTHROPIC_API_KEY` 넣고 `--backend anthropic`로 실제 1회 통과 검증.
3. 실제 과제 PDF 1개로 인제스트 품질 확인(PyMuPDF 설치), 섹션화 휴리스틱 보정.
4. Execution 시스템 프롬프트 튜닝 — "경계선을 넘지 않는" 행동을 실제 모델에서 안정화.

**Day 2 — 품질("진짜 잘해준다") + 데모 마감**
5. Understanding→Execution 사이에 자료 인용 정확도 점검(근거 누락 시 결정 지점으로 승격).
6. Personalization 스텁을 실사용: persona 파일이 프롬프트 톤을 바꾸도록 연결.
7. 결정 지점 UX 다듬기 + 프롬프트 추천 문구 개선.
8. 데모 시나리오 1개 고정(에세이 과제) + README의 실행법 검증. 필요시 가벼운 웹/CLI 녹화.

**경계 밖(이틀에 넣지 말 것):** 멀티유저·인증·결제·DB·복잡한 UI. MVP는 "한 과제 입력 → 경계선까지 초안 + 결정 지점"이 끝까지 도는 것만 증명한다.

---

## 부록 — 실행법

```bash
# 오프라인(키 불필요)
python -m until.cli examples/sample_assignment.txt

# 라이브
export ANTHROPIC_API_KEY=...
python -m until.cli path/to/assignment.pdf --backend anthropic

# 테스트
python tests/test_pipeline.py
```
