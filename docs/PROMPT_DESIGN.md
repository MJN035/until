# Execution 프롬프트 & 경계선 강제 설계

이 문서는 Execution 단계가 **Draft 경계선을 넘지 않도록** 만드는 두 겹의 장치 — (1) 프롬프트 설계, (2) 코드 가드(BoundaryGuard) — 와, 거기에 적용한 GitHub 오픈소스 패턴을 정리한다.

관련 코드: `until/execution/prompts.py`, `until/execution/boundary_guard.py`, `until/boundary/models.py`.

---

## 1. 왜 프롬프트만으로는 부족한가

LLM에 "사람의 판단을 대신하지 말라"고 지시해도, 모델은 종종 (a) 본인 입장을 단정하거나(경계선 침범), (b) 반대로 게을러져 채울 수 있는 부분을 비운다(과소 작업). 프롬프트는 **확률적**이라 항상 지켜지지 않는다. 그래서 *프롬프트 + 결정적 검증 + 재요청 루프*를 함께 쓴다. 이 "validate→reask" 구조가 핵심이다.

---

## 2. 적용한 오픈소스 패턴

우리 코어는 얇은 자체 하네스라 무거운 프레임워크를 통째로 넣지 않는다. 대신 검증된 OSS의 **패턴**을 의존성 없이 차용하고 출처를 명시했다.

### (A) guardrails-ai — validate→reask / OnFailAction (Apache-2.0)

- 리포: https://github.com/guardrails-ai/guardrails
- 차용한 것: `Validator.validate() → Pass/Fail`, `Guard().use(validator, on_fail=...)`, 그리고 검증 실패 시 LLM을 **재프롬프트(reask)** 하는 동작.
- 우리 구현 대응:

  | guardrails | until-mvp |
  |---|---|
  | `Validator.validate()` | `BoundaryValidator.validate(draft) → ValidationResult` |
  | `OnFailAction.REASK / EXCEPTION / NOOP` | `OnFailAction.REASK / EXCEPTION / WARN` |
  | `Guard().use(...).__call__(llm)` reask 루프 | `BoundaryGuard.run(produce)` reask 루프 (`max_reasks`) |

  즉 우리는 guardrails를 설치하는 대신, 동일한 계약(검증→실패 시 교정 재요청)을 60줄짜리 읽히는 코드로 재현해 포크/공개·학습에 적합하게 만들었다.

### (B) LangGraph Human-in-the-Loop — approve/edit/reject/respond

- 문서: https://docs.langchain.com/oss/python/langchain/human-in-the-loop
- 차용한 것: 사람이 보류된 액션을 처리하는 4가지 결정 타입. 우리는 이걸 **결정 지점(DecisionPoint) 해소 스키마**로 가져왔다.
- 우리 구현: `boundary/models.py`의 `Resolution(APPROVE, EDIT, REJECT, RESPOND)` + `DecisionPoint.resolve()`.
  - `approve`: 에이전트 후보안 그대로 채택 · `edit`: 수정 후 채택 · `reject`: 거부+사유(→재작업) · `respond`: 사람이 직접 답을 채움.

> 정리: **guardrails 패턴 = 에이전트가 경계선을 넘지 못하게 막는다(생성 측). LangGraph HITL 패턴 = 사람이 경계선의 결정을 처리하는 방식(소비 측).** 두 패턴이 경계선의 양쪽을 담당한다.

---

## 3. 프롬프트 설계 (`prompts.py`)

핵심 결정:

1. **양방향 실패 정의.** 경계선을 *넘는* 것(판단 가로채기)도 실패, 채울 수 있는 걸 *안 채우는* 것(게으름)도 실패로 못박았다. 한쪽만 막으면 모델이 반대쪽으로 도망간다.
2. **'사람의 판단' 5종 분류.** 가치판단/관점, 개인적 이해관계, 취향·스타일, 자료에 없는 외부 사실, 윤리·정직성. 이 중 하나라도 걸리면 확정 금지 → DECISION으로 이양. (메모리의 "그 사람인 순간은 안 건드린다" 원칙의 조작적 정의.)
3. **기계 파싱 가능한 마커.** `[[DECISION: 한 문장 + 후보]]` 한 줄 하나. 자리표시(`...`, `TODO`) 금지.
4. **자기검증 단계.** 출력 직전 (a) 임의 확정 문장 점검 (b) 게으른 공백 점검 (c) 마커 형식 점검.
5. **few-shot 대조.** "좋은 예"(후보 제시+DECISION 이양)와 "경계선 넘는 나쁜 예"(1인칭 입장 단정)를 나란히 제시.
6. **reask 메시지 빌더.** 검증기가 뱉은 위반 목록 + 직전 초안을 모델에 돌려주고 "고쳐서 전체 재작성"을 요구.

---

## 4. 결정적 검증 규칙 (`BoundaryValidator`, 토큰 0)

| # | 규칙 | 잡아내는 실패 |
|---|---|---|
| 1 | 본문 길이 ≥ `min_body_chars`(기본 200) | 과소 작업/게으름 |
| 2 | 유효 결정 지점 ≥ `min_decisions`(기본 1) | 경계선 침범(판단을 다 해버림) |
| 3 | 1인칭 입장 단정 정규식 미검출 | "나는 ~가 옳다고 본다" 류 관점 가로채기 |
| 4 | 여는 마커 수 == 유효 마커 수 | 깨진/자리표시 DECISION |

규칙 3은 정규식 기반 **휴리스틱**이다(한국어 "나는 …(옳다/본다/주장)", "결론적으로 …옳다", 영어 "I argue/believe", "my thesis is" 등). 완벽 탐지는 아니며, Day 2에서 실제 모델 출력으로 패턴을 보강하거나, 라이브에서는 LLM 자기평가(guardrails의 `llm_critic`류)로 승급할 수 있다.

---

## 5. 동작 흐름 (실측)

Mock 데모는 일부러 1차에 경계선을 넘는 초안을 낸다. 실행 로그:

```
=== 3. Execution — BoundaryGuard: 통과, 시도 2회(재요청 1회) ===
  ↻ 1차 위반:
     - 본문이 너무 짧다(165자) …
     - 결정 지점이 0개로 부족하다(최소 1) …
     - 본인 입장을 단정하는 문장: "나는 감시 자본주의가 더 설득력 있다고 본다" …
→ 재요청 → 2차 통과, 결정 지점 3개 확보
```

`max_reasks` 안에 통과 못 하면 `on_fail` 정책에 따라 `EXCEPTION`(중단) 또는 `WARN`(경고 달고 통과)으로 처리한다.

---

## 6. 라이브 튜닝 체크리스트 (Day 1~2)

1. `--backend anthropic`로 실제 5~10개 과제에 돌려 1차 통과율 측정.
2. 규칙 3의 1인칭 패턴을 실제 위반 사례로 보강(거짓양성/음성 균형).
3. `min_decisions`를 산출물 유형별로 조정(에세이는 ≥2, 단순 정리는 ≥1 등).
4. reask 1회로 대부분 교정되는지 확인 → 안 되면 few-shot에 교정 예시 추가.
5. 비용: 검증은 토큰 0. reask는 1회당 1콜 추가이므로 평균 reask 횟수를 모니터링.
