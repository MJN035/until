# LLM 래퍼·에이전트 앱 아키텍처 스터디 (공부용)

> 목적: 다른 오픈소스 LLM "wrapper"/에이전트 앱들이 **어떻게 구조화·패키징되는지** 조사해서 (1) Until이 배울 점을 찾고 (2) 오픈소스 공개를 준비한다. 모든 출처는 2025~2026 기준 현행 자료. 각 절 끝의 **→ Until 적용**은 이 프로젝트(Capture→Understanding→Context→Execution→Boundary, mock/local/anthropic 백엔드, 결정적 BoundaryGuard+reask, stdlib `http.server` 웹 UI, Canvas/SSO 어댑터, AI 결정 제안)에 바로 적용할 구체 교훈이다.

---

## 1. LLM 래퍼·에이전트 앱의 공통 구조

조사한 대표 프레임워크들은 역할이 명확히 갈린다.

- **LangChain** — 범용 오케스트레이션: 프롬프트 포매팅 → LLM 호출 → 툴 실행 → 출력 파싱 → 메모리. "체인" 추상화로 단계를 잇는다.
- **LangGraph** — 그래프 기반 상태 워크플로. 루프/분기/상태 영속화/human-in-the-loop를 1급으로 다룬다. 멀티 액터 에이전트가 협업·반복할 수 있게 한다.
- **LlamaIndex** — 데이터 인입·검색 레이어 특화. 핵심 프리미티브가 깔끔하다: **readers**(로더) → **Documents/Nodes**(청크 모델) → **indexes** → **retrievers** → **query engines**(retriever+synthesis) → **agents** → **workflows**(이벤트 구동). ([LlamaIndex 가이드](https://www.digitalocean.com/resources/articles/what-is-llamaindex), [DataCamp: LLM Agents](https://www.datacamp.com/blog/llm-agents))

이걸 가로지르는 **공통 레이어 스택**은 거의 항상 다음 형태다:

```
인입(ingestion) → 검색(retrieval) → 프롬프트 조립 → LLM 호출 → 출력 파싱/검증 → UI/액션
                                      └ 메모리/상태 ─┘
```

학술 분석은 이를 더 세분화해 **Prompt Ingestion Layer**(입력·파일·RAG 문서 수집), **Memory/Context Handler**(이전 상태 재수화), **Logic Execution Engine**(지시 파싱·역할·제어흐름 평가)으로 나눈다. ([LPCI arXiv](https://arxiv.org/pdf/2507.10457), [Agentic RAG Survey](https://arxiv.org/pdf/2501.09136))

주목할 2026년 트렌드: 무거운 프레임워크(LangChain/LlamaIndex)를 **가벼운 Agent SDK + 직접 작성 코어**로 갈아타는 흐름이 뚜렷하다. 추상화 비용·디버깅 난이도·버전 변동성 때문에, "읽히는 얇은 코어"를 선호하는 팀이 늘었다. ([MindStudio: frameworks replaced by Agent SDKs](https://www.mindstudio.ai/blog/llm-frameworks-replaced-by-agent-sdks))

**→ Until 적용:** Until의 `pipeline.py`는 이미 이 공통 스택의 **교과서적 분리**다 — Capture=ingestion(토큰 0), Context=retrieval, Execution=프롬프트+LLM, Boundary=출력 파싱. LlamaIndex의 `readers→Nodes→retrievers` 어휘를 차용해 `capture/sources/*`(readers), `context/retrieval.py`(retriever), `SourceDoc`(Node)를 README 다이어그램에서 **표준 용어로 라벨링**하면, 공부하는 독자가 즉시 "아, 이게 LlamaIndex로 치면 retriever구나"로 매핑할 수 있다. 그리고 "얇은 자체 하네스" 선택은 2026 트렌드와 정확히 일치하므로, ARCHITECTURE.md에서 이를 **의도된 설계(부채가 아니라 트렌드 부합)**로 명시하라.

---

## 2. 프롬프트·경계 처리: 모델이 선을 넘지 않게 하는 법

성숙한 도구들이 환각·과잉행동을 막는 방식은 크게 셋이다.

**(a) 구조화 출력(Structured Outputs).** "JSON으로 답해줘" 프롬프트가 아니라, **스키마를 문법(grammar)으로 컴파일해 추론 중 토큰 생성을 제약**하는 방식이 표준이 됐다. Anthropic은 2025-11-14 Claude Sonnet 4.5/Opus 4.1에 Structured Outputs를 공개했고, `output_config.format`에 JSON Schema를 주면 응답이 스키마를 **보장**한다(파싱 에러·재시도 로직 제거). 단, **citations와 structured outputs는 동시 사용 불가**(인용 블록과 strict JSON이 충돌, 400 에러). ([Anthropic Structured Outputs 문서](https://platform.claude.com/docs/en/build-with-claude/structured-outputs), [TDS 핸즈온](https://towardsdatascience.com/hands-on-with-anthropics-new-structured-output-capabilities/))

**(b) 검증→재시도 루프(Instructor 패턴).** [Instructor](https://github.com/567-labs/instructor)(월 300만+ 다운로드)는 Pydantic 모델로 출력을 정의하고, **검증 실패 시 Tenacity로 자동 재시도**하며, 부분 결과 스트리밍을 지원한다. 15+ 프로바이더 공통 인터페이스. 핵심 사상: "LLM 호출을 텍스트 생성이 아니라 **타입 있는 함수 호출**처럼 다룬다." ([Instructor 공식](https://python.useinstructor.com/), [동작 원리](https://ivanleo.com/blog/how-does-instructor-work))

**(c) Human-in-the-loop 인터럽트(LangGraph).** [LangGraph HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)는 모델이 위험 액션을 제안하면 **실행을 멈추고 상태를 checkpointer로 영속화**한 뒤 사람에게 결정을 넘긴다. 결정 타입이 표준화돼 있다: **approve(그대로) / edit(수정 후 실행) / reject(피드백과 함께 거부) / respond(질문에 직접 답)**. 2024-12 도입된 `interrupt()` + `Command` 프리미티브가 구식 `interrupt_before/after`를 대체했다. ([LangChain 블로그](https://www.langchain.com/blog/making-it-easier-to-build-human-in-the-loop-agents-with-interrupt))

**(d) 계층 방어.** 시스템 프롬프트 + 길이 거버너 + 시간 경계 + RAG 인용 + 신뢰도 스코어 + 에스컬레이션을 겹치면 환각을 71~89% 줄였다는 보고. 핵심 원칙: **"모른다/넘긴다"를 허용**하는 게 지어내는 것보다 낫다. ([Guardrails 모범사례](https://www.leanware.co/insights/llm-guardrails), [환각 감축 12 가드레일](https://swiftflutter.com/reducing-ai-hallucinations-12-guardrails-that-cut-risk-immediately))

**→ Until 적용:** Until의 `BoundaryGuard`(validate→reask)는 이미 Instructor의 **"검증 실패 시 재프롬프트"** 패턴을 의존성 없이 구현했고, 결정 스키마(approve/edit/reject/respond)는 LangGraph HITL과 1:1 대응한다 — 이건 자랑할 자산이다. 두 가지 강화: ① **결정적 정규식 가드 위에, `live` 백엔드에서만 Anthropic native Structured Outputs로 TaskSpec(Understanding 단계)을 강제**하면 JSON 파싱 실패가 원천 차단된다(단 Execution 본문은 인용·자연어라 structured outputs 부적합 — citations와 못 섞이는 제약과도 일치하니 **Understanding=structured, Execution=guard+reask**로 역할을 갈라라). ② Until의 `crossed_boundary`(결정 0개 감지)는 "모른다를 허용"의 코드화인데, 이를 **명시적 신뢰도 신호로 로그**(피드백 JSONL에 이미 reask 수 적립 중)에 넣어 GEPA 목적함수로 활용하라.

---

## 3. 오픈소스 패키징: 잘 포장된 Python LLM 프로젝트의 체크리스트

조사에서 반복적으로 나온 "잘 된 패키징"의 공통 요소:

1. **`pyproject.toml` + `setuptools.build_meta`** (setup.py 폐기). 현행 표준이며 future-proof. ([Packaging User Guide](https://packaging.python.org/en/latest/specifications/pyproject-toml/))
2. **베이스 의존성 0, 프로바이더는 extras.** 모범 사례는 `pip install pkg[openai]` / `pkg[anthropic]`처럼 무거운 SDK(openai, anthropic, pydantic)를 **optional-dependencies(extras)**로 빼고 코어는 의존성 없이 import 가능하게 한다. ([5 LLM 패키지 회고](https://medium.com/@sayedebad.777/i-built-5-python-packages-for-llm-developers-heres-everything-i-learned-cecbc3bb71be), [pyOpenSci 의존성 가이드](https://www.pyopensci.org/python-package-guide/package-structure-code/declare-dependencies.html))
3. **API 안 때리는 테스트.** 프로바이더 호출을 `unittest.mock`으로 모킹해 **키 없이·무료·빠르게** 전체 스위트가 돈다. 사용자가 키 없이 테스트 가능해야 한다.
4. **명확한 모듈 경계.** 공개 API는 `__init__.py`, 데이터클래스는 `models.py`, 로직은 `core.py`, 테스트는 `tests/`(모킹).
5. **시크릿 0 + env 기반 설정**, README 퀵스타트, LICENSE, 기여 문서.

**모범 레포 3선:**
- **[Instructor](https://github.com/567-labs/instructor)** — 다국어(Python/TS/Go/Ruby) 단일 패턴, 프로바이더별 extras, Pydantic 단일 의존, 문서 사이트가 곧 튜토리얼. "하나의 작은 일을 모든 모델에서 잘한다"의 표본.
- **LlamaIndex** — 코어(`llama-index-core`)와 통합(`llama-index-llms-*`, `llama-index-readers-*`)을 **별도 패키지로 쪼개** 무거운 의존성을 격리한 모노레포 전략.
- **Poetry/PEP 표준 레퍼런스** — extras를 `[project.optional-dependencies]`에 선언하고 DRY하게 묶는 법. ([Poetry pyproject](https://python-poetry.org/docs/pyproject/), [재귀적 optional deps](https://hynek.me/articles/python-recursive-optional-dependencies/))

**→ Until 적용:** Until의 `pyproject.toml`은 이미 **모범 그 자체**다 — `dependencies = []`(베이스 0), extras로 `pdf/etl/live/optimize/retrieval` 분리, mock 백엔드로 키 없이 12+ 스위트 통과. 공개 전 마무리 3가지: ① **LICENSE 파일 추가**(현재 없음 — 차용한 guardrails-ai가 Apache-2.0이니 Apache-2.0 권장, 호환·기업 친화) + `[project] license` 필드. ② **`local` 백엔드(OpenAI 호환/Groq/Ollama)용 extra 추가** — 지금 `requirements.txt`엔 있지만 `openai`가 extra로 안 보임. `web = []`(stdlib만이라 비어있음을 README에 강조). ③ **CONTRIBUTING.md + `.env.example`**(키 이름만, 값 없음)로 "시크릿 0" 원칙을 가시화. `.gitignore`에 `report.md`/`_until_work/`/키가 이미 빠져있는 건 잘 했다.

---

## 4. "진짜 도와준다" 느낌을 주는 UX 패턴

얇은 래퍼와 진짜 조력자를 가르는 제품 패턴:

- **인용·출처(Citations) = 최강 신뢰 장치.** Perplexity의 인라인 출처, You.com의 답변+원문 링크처럼 "어떻게 그 결론에 도달했는지"를 보여주면 신뢰가 급상승한다. ([Amestris: LLM UX 패턴](https://amestris.com.au/blog/llm-ux-patterns.html), [CMSWire 10패턴](https://www.cmswire.com/digital-experience/10-ux-design-patterns-that-improve-ai-accuracy-and-customer-trust/))
- **점진적 공개(Progressive Disclosure).** 짧은 답을 먼저, 근거·세부는 펼쳐서. 인지 부하를 줄이고 필요할 때만 복잡도를 노출. ([UXPin](https://www.uxpin.com/studio/blog/what-is-progressive-disclosure/), [ShapeOfAI: Disclosure](https://www.shapeof.ai/patterns/disclosure))
- **불확실성을 허용 가능하게.** "모르겠다/사람에게 넘긴다"를 떳떳하게. 지어내는 것보다 거부·에스컬레이션이 낫다.
- **투명성 배지.** "AI-generated", "AI-edited", "Summarized with AI" 같은 칩/워터마크로 AI 산출물을 구분.
- **수락 가능한 좋은 기본값 + 스트리밍.** 사용자가 **그대로 받아들이는** 제안(좋은 디폴트)과, 생성 과정을 보여주는 스트리밍이 "살아있는 조력자" 느낌을 만든다. ([koruux 14패턴](https://www.koruux.com/ai-patterns-for-ui-design/))

**→ Until 적용:** Until의 **AI 결정 제안 + "전부 수락" 버튼**은 정확히 "수락 가능한 좋은 기본값" 패턴이고, **AI가 대신 확정하지 않는다**는 원칙은 "불확실성을 사람에게 넘김"의 모범 구현이다 — 이미 최고 수준. 두 가지 추가: ① **인용 노출** — Until은 Context 레이어에서 수업자료·내파일을 이미 검색하므로, 초안 문장에 **출처 자료명을 인라인 각주**(예: `…근거[도시문화론 3주차]`)로 달면 "진짜 자료 보고 썼다"는 신뢰가 폭발적으로 오른다(Anthropic Citations API가 `live`에서 이걸 정확히 지원 — 단 §2의 structured outputs와는 못 섞으니 Execution에서만). ② **점진적 공개는 이미 적용됨**(`<details>` 3섹션 토글) — 여기에 더해 `live` 백엔드에서 **스트리밍 출력**(수십 초 로딩 오버레이를 토큰 스트림으로 대체)을 붙이면 체감 속도와 "작동 중" 신뢰가 크게 오른다. "AI가 채운 칸"에 투명성 배지(🤖)는 이미 있다.

---

## 핵심 요약 (Until 최고가치 교훈 5)

1. **이미 잘 한 것을 "표준 용어로" 포장하라.** Until의 layered pipeline·mock 백엔드·extras 분리·BoundaryGuard·HITL 결정 스키마는 LangGraph/Instructor/LlamaIndex의 모범과 1:1 대응한다. README/ARCHITECTURE에 그 매핑을 명시하면 "공부용 비교 기준"이라는 목표가 즉시 달성된다.
2. **역할별로 검증 방식을 분리하라:** Understanding(TaskSpec JSON)은 Anthropic **native Structured Outputs**로 강제, Execution(자연어+인용 본문)은 기존 **guard→reask** 유지 — 이는 "citations와 structured outputs는 못 섞인다"는 제약과도 자연히 들어맞는다.
3. **인용(Citations)을 노출하라.** Context 레이어가 이미 자료를 검색하니, 초안에 출처를 인라인으로 달면 "진짜 도와준다" 신뢰가 가장 크게 오른다.
4. **공개 직전 패키징 마무리:** LICENSE(Apache-2.0 권장) + `local` 백엔드 extra + `CONTRIBUTING.md`/`.env.example`. 코어 의존성 0·키 없는 테스트는 이미 모범.
5. **얇은 자체 하네스는 트렌드 부합이다.** 2026년 흐름은 무거운 프레임워크→가벼운 코어이므로, "LangChain 안 씀"을 부채가 아니라 **의도된 설계**로 문서화하라.

---

### 출처
- [LlamaIndex 가이드 (DigitalOcean)](https://www.digitalocean.com/resources/articles/what-is-llamaindex) · [LLM Agents (DataCamp)](https://www.datacamp.com/blog/llm-agents) · [Frameworks→Agent SDKs (MindStudio)](https://www.mindstudio.ai/blog/llm-frameworks-replaced-by-agent-sdks)
- [LPCI (arXiv)](https://arxiv.org/pdf/2507.10457) · [Agentic RAG Survey (arXiv)](https://arxiv.org/pdf/2501.09136)
- [Anthropic Structured Outputs 문서](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) · [핸즈온 (TDS)](https://towardsdatascience.com/hands-on-with-anthropics-new-structured-output-capabilities/)
- [Instructor (GitHub)](https://github.com/567-labs/instructor) · [Instructor 공식](https://python.useinstructor.com/) · [동작 원리](https://ivanleo.com/blog/how-does-instructor-work)
- [LangGraph HITL 문서](https://docs.langchain.com/oss/python/langchain/human-in-the-loop) · [interrupt 블로그](https://www.langchain.com/blog/making-it-easier-to-build-human-in-the-loop-agents-with-interrupt)
- [LLM Guardrails 모범사례](https://www.leanware.co/insights/llm-guardrails) · [환각 감축 가드레일](https://swiftflutter.com/reducing-ai-hallucinations-12-guardrails-that-cut-risk-immediately)
- [Packaging User Guide](https://packaging.python.org/en/latest/specifications/pyproject-toml/) · [pyOpenSci 의존성](https://www.pyopensci.org/python-package-guide/package-structure-code/declare-dependencies.html) · [5 LLM 패키지 회고](https://medium.com/@sayedebad.777/i-built-5-python-packages-for-llm-developers-heres-everything-i-learned-cecbc3bb71be) · [재귀 optional deps (hynek)](https://hynek.me/articles/python-recursive-optional-dependencies/)
- [LLM UX 패턴 (Amestris)](https://amestris.com.au/blog/llm-ux-patterns.html) · [10 UX 패턴 (CMSWire)](https://www.cmswire.com/digital-experience/10-ux-design-patterns-that-improve-ai-accuracy-and-customer-trust/) · [Progressive Disclosure (UXPin)](https://www.uxpin.com/studio/blog/what-is-progressive-disclosure/) · [Disclosure (ShapeOfAI)](https://www.shapeof.ai/patterns/disclosure) · [14 AI 패턴 (koruux)](https://www.koruux.com/ai-patterns-for-ui-design/)
