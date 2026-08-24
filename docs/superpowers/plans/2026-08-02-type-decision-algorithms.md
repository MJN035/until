# 유형별 결정 골격·질의 후보·대필 금지 게이트 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/planning/type_algorithms.md`의 우선 구현 순서 1~4를 구현한다 — ① T1b 질의 후보 생성(전용 유형), ② 유형별 결정 골격 분리(반응형이 에세이 결정을 받는 범주 착오 수정), ③ T4 대필 금지 신호 게이트(자필 규정 과제는 학습 보조 모드로 강등), ④ 원료 없음 → 자료 요청 결정 규칙.

**Architecture:** 전부 기존 파이프라인의 확장점에 얹는다 — 유형은 `task_type.py`(결정적 분류), 지침은 `execution/prompts.py`의 `TYPE_GUIDANCE`+`system_extra` 주입, 골격은 `understanding/skeleton.py`, 게이트는 신규 `understanding/integrity.py`(결정적 정규식), 표면화는 `readiness.py`. mock 백엔드에 유형별 결정적 초안을 추가해 오프라인 테스트가 결정의 '종류'까지 검증하게 한다.

**Tech Stack:** Python 표준 라이브러리만(의존성 0). 모든 신규 로직은 LLM 호출 0(결정적) — 프롬프트 지침 주입만 LLM 경로에 영향.

## Global Constraints

- `--backend mock` + 모든 테스트는 키·인터넷 없이 항상 통과해야 한다 (CLAUDE.md 불변 규칙 2).
- `capture/`, `context/`, `boundary/`, `prompts/suggest.py`는 LLM 호출 0 (불변 규칙 3). 신규 `understanding/integrity.py`도 결정적으로 만든다.
- 테스트 실행은 항상 `PYTHONIOENCODING=utf-8` 환경변수와 함께 (Windows cp949 콘솔에서 em-dash·이모지 출력 에러 회피).
- 새 테스트 파일은 `run_tests.py`의 `SUITES`에 반드시 등록 — `test_runners`가 AST 감사로 1:1 대조하므로 누락 시 그 스위트가 실패한다.
- 커밋 메시지는 한국어 요약 + 마지막 줄 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- 한 번에 작은 변경 → 전체 러너(`python run_tests.py`) 통과 → 커밋. 큰 리팩터링 금지.
- 경계선 철학: 사람 판단을 코드가 대신 확정하지 않는다. 게이트·규칙은 "판정·안내"까지만.

---

### Task 1: T1b 질의(inquiry) 전용 유형 + 질문 후보 생성 골격

기획 근거: `type_algorithms.md` T1b — 미제출 1위(13건) 유형. 강의 주제·교수명으로 프레임(전망/사례/진로/방법론/한계)별 질문 후보 5개를 생성하고, "어떤 질문이 진짜 내 관심사인가" 선택만 사람 몫(결정 1개).

**Files:**
- Modify: `until/understanding/task_type.py` (신규 유형 `inquiry`, `reflective_report`에서 `"질의"` 신호 이관)
- Modify: `until/execution/prompts.py` (`TYPE_GUIDANCE["inquiry"]`)
- Modify: `until/understanding/skeleton.py` (`_SKELETONS["inquiry"]`)
- Modify: `until/llm/mock_client.py` (`_TYPE_DRAFTS["inquiry"]`)
- Create: `examples/sample_inquiry.txt`
- Test: `tests/test_task_type.py` (기존 질의 기대값 갱신 + 신규 e2e)

**Interfaces:**
- Consumes: `classify_task_type(spec, docs) -> str` (기존), `pipeline.run(paths, cfg)` (기존).
- Produces: 유형 문자열 `"inquiry"` — `LABELS["inquiry"] == "질의/질문 제출"`. Task 2의 `decision_directive`가 이 키를 사용한다. `FACTUAL_TYPES`에는 넣지 않는다(선택 결정 1개 필수).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_task_type.py`에 추가하고, 49행 부근의 기존 기대값을 수정한다.

기존(약 49행):
```python
        "3주차 질의 (3/16 17:00) 강의를 듣고 질문을 제출하세요": "reflective_report",
```
로 되어 있는 항목을 다음으로 변경:
```python
        "3주차 질의 (3/16 17:00) 강의를 듣고 질문을 제출하세요": "inquiry",
```

파일 끝에 신규 테스트 추가:
```python
def test_inquiry_type_and_candidate_draft():
    """T1b 질의 — 전용 유형 분류 + mock 초안이 '후보 생성 → 선택 결정 1개' 골격."""
    from until.understanding.task_type import classify_task_type, LABELS, FACTUAL_TYPES
    t = classify_task_type(
        {"goal": "다음 수업 교수님들께 질문드릴 내용을 작성하여 제출"},
        [_Doc("5주차 질의 (3/30 17:00) — 질문을 제출하세요")])
    assert t == "inquiry", t
    assert LABELS["inquiry"] == "질의/질문 제출"
    assert "inquiry" not in FACTUAL_TYPES  # 선택 결정 1개는 필수

    # e2e: mock 초안이 질문 후보 + 선택 결정을 낸다(에세이 논지 결정이 아니라).
    from until.config import Config
    from until.pipeline import run
    res = run(["examples/sample_inquiry.txt"], Config(backend="mock"))
    assert res.spec["task_type"] == "inquiry"
    assert res.draft.n_decisions >= 1
    notes = " ".join(d.note for d in res.draft.decisions)
    assert "질문" in notes and "선택" in notes, notes
    assert "논지" not in notes  # 에세이 결정 골격 오적용 회귀 방지
    body = res.draft.body
    assert body.count("?") >= 3 or "궁금합니다" in body  # 후보 질문이 실제 문장으로
    print("OK inquiry — 후보 생성 + 선택 결정 1개")
```

(주의: `Config(backend="mock")`처럼 생성자 인자로 주지 말고 기존 파일 관례가 `cfg = Config(); cfg.backend = "mock"`이면 그 관례를 따른다. `Config`는 dataclass라 둘 다 동작한다.)

- [ ] **Step 2: 실패 확인**

Run: `PYTHONIOENCODING=utf-8 python tests/test_task_type.py`
Expected: FAIL — `assert t == "inquiry"` (현재는 `reflective_report` 반환) 또는 examples 파일 부재 에러.

- [ ] **Step 3: 예제 파일 생성** — `examples/sample_inquiry.txt`:

```
5주차 질의 (3/30 17:00)

다음 주 세미나에서 강연하실 교수님께 질문드릴 내용을 작성하여 제출하세요.

- 주제: 인공지능 반도체 설계 동향
- 분량: 질문 2~3개
- 마감: 3월 30일 17:00까지 온라인 텍스트로 제출
```

- [ ] **Step 4: `task_type.py` 수정**

`_SIGNALS`의 `reflective_report` 목록에서 `"질의",` 한 줄을 제거하고, dict에서 `reflective_report` **앞에** `inquiry` 키를 추가한다(동점 시 dict 순서가 앞선 유형이 이기므로):

```python
    # 질의 제출 — 강의 듣고 다음 수업 교수에게 할 질문을 미리 제출(T1b).
    # 산출물이 '질문 목록'이라 반응형 보고서와 골격이 다르다(후보 생성→선택).
    "inquiry": [
        "질의", "질문을 제출", "질문 제출", "질문드릴", "질문을 작성",
        "질문 작성", "궁금한 점을 제출",
    ],
```

`_WEIGHTS`에 추가(질의 신호는 존재 자체가 강함 — reflective와 같은 가중):
```python
_WEIGHTS = {"reflective_report": 2, "inquiry": 2}
```

`LABELS`에 추가:
```python
    "inquiry": "질의/질문 제출",
```

- [ ] **Step 5: `TYPE_GUIDANCE["inquiry"]` 추가** — `until/execution/prompts.py`의 `TYPE_GUIDANCE` dict, `"reflective_report"` 항목 뒤:

```python
    "inquiry": (
        "[ 유형: 질의/질문 제출 ]\n"
        "- 산출물은 '질문 목록'이다. 강의 주제·연사 정보(제목·자료)에서 도출해\n"
        "  관점(프레임)별 질문 후보 5개를 만든다: 전망 / 적용 사례 / 진로·창업 /\n"
        "  방법론 / 한계·난제 — 프레임당 1개, 번호를 붙여 나열한다.\n"
        "- 각 후보는 그대로 제출 가능한 완성된 존댓말 문장으로 쓴다(예/아니오로\n"
        "  끝나지 않는 열린 질문). 후보 생성은 경계선 안 — 끝까지 쓴다.\n"
        "- 결정은 단 하나: [[DECISION: 후보 중 실제로 궁금한 질문 N개 선택(번호) 또는\n"
        "  직접 수정]] — '내가 뭘 궁금해하는가'만 사람 몫이다.\n"
        "- 논지 선택·반론 톤 같은 에세이형 결정을 만들지 말 것."
    ),
```

- [ ] **Step 6: `_SKELETONS["inquiry"]` 추가** — `until/understanding/skeleton.py`의 `_SKELETONS` dict, `"report"` 항목 뒤:

```python
    # 질의 — 각 질문(응답 단위)이 갖출 논리: 강의 연결 → 구체 질문.
    "inquiry": AnswerSkeleton(
        task_type="inquiry", unit_name="질문",
        slots=[
            SkeletonSlot("lecture_link", "강의 주제와의 연결",
                         "질문이 이번 강의·연사와 닿는 지점", "source_document"),
            SkeletonSlot("question_body", "구체 질문 문장",
                         "완성된 존댓말 한 문장, 열린 질문", "source_document"),
        ]),
```

- [ ] **Step 7: mock 초안 추가** — `until/llm/mock_client.py`의 `_TYPE_DRAFTS` dict에 추가:

```python
    "inquiry": (
        "# 질의 후보 (Draft)\n\n"
        "다음 강의의 주제와 연사 정보를 바탕으로 관점별 질문 후보를 만들었다 [자료1]. "
        "아래 후보는 모두 그대로 제출 가능한 문장이다.\n\n"
        "1. (전망) 교수님께서 연구하시는 분야가 10년 뒤 산업에서 어떤 역할을 하게 될지 궁금합니다.\n"
        "2. (사례) 연구 결과가 실제 제품이나 서비스에 적용된 사례가 있다면 소개해 주실 수 있으신가요?\n"
        "3. (진로) 이 분야로 진로를 정하려는 학부생이 지금 준비해야 할 것은 무엇이라고 보시는지요?\n"
        "4. (방법론) 연구에서 가장 중요한 도구나 방법은 무엇이며 어떻게 익히셨는지 궁금합니다.\n"
        "5. (한계) 현재 접근이 가진 한계나 아직 풀리지 않은 난제는 무엇인가요?\n\n"
        "[[DECISION: 위 후보 중 실제로 궁금한 질문 2~3개 선택(번호로 답하거나 문장을 직접 수정)]]\n"
    ),
```

- [ ] **Step 8: 테스트 통과 확인**

Run: `PYTHONIOENCODING=utf-8 python tests/test_task_type.py`
Expected: PASS (기존 케이스 포함 전부).

- [ ] **Step 9: 전체 러너**

Run: `python run_tests.py`
Expected: `pass=43 fail=0` (전 스위트 통과). 실패 시 원인 파악 후 수정 — 특히 `"질의"` 신호 이관으로 깨지는 다른 기대값이 있으면 해당 기대를 `inquiry`로 갱신.

- [ ] **Step 10: 커밋**

```bash
git add until/understanding/task_type.py until/execution/prompts.py until/understanding/skeleton.py until/llm/mock_client.py examples/sample_inquiry.txt tests/test_task_type.py
git commit -m "T1b 질의 전용 유형(inquiry) — 프레임별 질문 후보 생성 + 선택 결정 1개

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 유형별 결정 골격 분리 — 반응형에 에세이 결정 오적용 수정

기획 근거: `type_algorithms.md` §9-1 — "경계선 오류의 주 패턴은 떠넘김이 아니라 골격 착오. 결정의 개수는 가드가 지키지만 종류는 아무도 안 지킨다." mock 검증에서 소감문·질의가 에세이 결정 3종(논지/반론 톤/주장 강도)을 받았다. 원인 2가지를 각각 고친다: ① 프롬프트에 '이 유형의 결정은 어떤 종류여야 하는가' 지침이 없음 → `decision_directive` 신설 ② mock에 반응형 초안이 없어 에세이 초안으로 폴백 → `_TYPE_DRAFTS["reflective_report"]` 추가.

**Files:**
- Modify: `until/understanding/skeleton.py` (`DECISION_SKELETONS` + `decision_directive()`)
- Modify: `until/pipeline.py` (`system_extra`에 주입, 122~126행 부근)
- Modify: `until/llm/mock_client.py` (`_TYPE_DRAFTS["reflective_report"]`)
- Test: `tests/test_skeleton.py`

**Interfaces:**
- Consumes: `task_type` 문자열(Task 1의 `inquiry` 포함).
- Produces: `decision_directive(task_type: str) -> str` — 빈 문자열이면 주입 생략. Task 3·4는 이 함수를 건드리지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_skeleton.py` 끝에 추가:

```python
def test_decision_directive_per_type():
    """유형별 결정 골격 — 어떤 판단이 사람 몫인지 유형마다 다르게 지시한다."""
    from until.understanding.skeleton import decision_directive
    refl = decision_directive("reflective_report")
    assert "경험" in refl and "논지" in refl  # 경험 선택만 결정 / 논지 결정 금지 명시
    ess = decision_directive("essay")
    assert "논지" in ess
    inq = decision_directive("inquiry")
    assert "선택" in inq
    assert decision_directive("general") == ""
    assert decision_directive(None) == ""
    print("OK decision_directive — 유형별 결정 종류 분리")


def test_reflective_mock_draft_has_experience_decision():
    """반응형(T1a) mock 초안 — 결정이 '경험 키워드 요청'이지 에세이 논지가 아니다."""
    import tempfile, os
    from until.config import Config
    from until.pipeline import run
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "spec.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("3주차 소감문 (3/17)\n\n오늘 강의 내용을 합쳐서 소감문을 "
                    "공백 포함 400자 이상 작성하여 제출하세요.")
        res = run([p], Config(backend="mock"))
    assert res.spec["task_type"] == "reflective_report", res.spec["task_type"]
    notes = " ".join(d.note for d in res.draft.decisions)
    assert "인상 깊었" in notes, notes          # 경험 요청 결정
    assert "논지" not in notes and "반론" not in notes  # 범주 착오 회귀 방지
    print("OK reflective mock — 경험 결정, 에세이 골격 아님")
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONIOENCODING=utf-8 python tests/test_skeleton.py`
Expected: FAIL — `decision_directive` import 에러.

- [ ] **Step 3: `skeleton.py`에 결정 골격 추가** — 파일 끝에:

```python
# ── 결정 골격 — 유형별로 '어떤 판단이 사람 몫인가' ─────────────────────
# Phase 3 검증(docs/planning/type_algorithms.md §9-1): 결정의 '개수'는 가드가
# 지키지만 '종류'는 아무도 안 지켜, 반응형(소감문·질의)이 에세이 결정 3종
# (논지/반론 톤/주장 강도)을 받는 범주 착오가 확인됐다. 유형별 결정의 종류를
# 지침으로 명시한다. 문장 강제가 아니라 종류 안내(경계선 철학 유지).
DECISION_SKELETONS = {
    "essay": "핵심 논지·관점의 선택, 반론 수용 톤, 마무리 강도",
    "reflective_report": (
        "본인 '경험'의 선택뿐 — 인상 깊었던 대목(키워드), 적용·수강 계획. "
        "논지 선택·반론 톤·주장 강도 같은 에세이형 결정을 만들지 말 것"),
    "inquiry": "질문 후보 중 선택(2~3개) 단 하나 — 그 외 결정을 만들지 말 것",
    "report": "결과 해석의 방향, 개선·후속 실험 방향",
    "presentation": "가장 앞세울 메시지·스토리의 선택",
    "problemset": "억지 결정 금지 — 문제에 없는 가정을 둬야 할 때만",
    "code": "억지 결정 금지 — 설계 선택지(자료구조 등)가 실제로 갈릴 때만",
}


def decision_directive(task_type: str) -> str:
    """유형별 '결정의 종류' 지침 — 실행 시스템 프롬프트에 주입. 없으면 빈 문자열."""
    kinds = DECISION_SKELETONS.get(task_type or "")
    if not kinds:
        return ""
    return (
        "[ 이 유형의 결정 골격 — 어떤 판단이 사람 몫인가 ]\n"
        f"- 이 과제 유형에서 [[DECISION]]으로 남길 판단의 종류: {kinds}.\n"
        "- 위 종류에 해당하지 않는 결정을 억지로 만들거나, 다른 유형의 결정"
        "(예: 소감문에 '반론 수용 톤')을 복제하지 말 것."
    )
```

- [ ] **Step 4: 파이프라인 주입** — `until/pipeline.py` 121~126행 부근:

```python
    from .understanding.skeleton import skeleton_directive
    system_extra = "\n\n".join(
        b for b in (type_guidance(task_type),
                    skeleton_directive(task_type, content_elements),
                    length_directive(length_target),
                    form_directive(docs), profile_hint()) if b)
```
를 다음으로 변경:
```python
    from .understanding.skeleton import skeleton_directive, decision_directive
    system_extra = "\n\n".join(
        b for b in (type_guidance(task_type),
                    skeleton_directive(task_type, content_elements),
                    decision_directive(task_type),
                    length_directive(length_target),
                    form_directive(docs), profile_hint()) if b)
```

- [ ] **Step 5: mock 반응형 초안 추가** — `until/llm/mock_client.py`의 `_TYPE_DRAFTS`에 추가 (가드 통과 요건: 본문 200자↑·결정 1개↑·1인칭 입장 단정 없음·외국 문자 0):

```python
    "reflective_report": (
        "# 소감문 (Draft)\n\n"
        "## 강의에서 다룬 내용\n"
        "이번 강의가 다룬 주제와 핵심 개념을 자료에 근거해 정리했다 [자료1]. "
        "강의 사실 부분은 자료가 있는 데까지 끝까지 작성했고, 자료에 없는 세부 "
        "내용은 지어내지 않았다.\n\n"
        "## 내 반응 (본인 경험 필요)\n"
        "[[DECISION: 이번 강의에서 인상 깊었던 대목 2~3개 — 키워드만 적어 주세요: ___, ___]]\n"
        "키워드를 받으면 그 대목이 왜 인상 깊었는지, 무엇을 새로 알게 됐는지를 "
        "본인 문체로 풀어 쓴다.\n\n"
        "## 연결\n"
        "키워드에 진로·수강 계획 언급이 있으면 그 연결 문장을 쓰고, 없으면 상투적 "
        "진로 문장은 지어내지 않고 생략한다.\n"
    ),
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `PYTHONIOENCODING=utf-8 python tests/test_skeleton.py`
Expected: PASS.

- [ ] **Step 7: 전체 러너**

Run: `python run_tests.py`
Expected: `fail=0`. 주의: 기존 테스트 중 반응형 spec으로 mock을 돌려 '에세이 reask 루프'(1차 위반→교정)를 기대하는 케이스가 있으면 깨질 수 있다 — 그 경우 해당 테스트의 spec을 에세이형으로 바꾸는 것이 맞다(반응형이 유형 초안을 받는 것이 이제 정상 동작).

- [ ] **Step 8: 커밋**

```bash
git add until/understanding/skeleton.py until/pipeline.py until/llm/mock_client.py tests/test_skeleton.py
git commit -m "유형별 결정 골격 분리 — 반응형에 에세이 결정 오적용 수정(기획 §9-1)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: T4 대필 금지 신호 게이트 — 자필 규정 과제는 학습 보조 모드

기획 근거: `type_algorithms.md` T4 — 물리학 숙제가 "종이에 작성한 답안의 스캔이나 사진, 또는 손글씨를 그대로(폰트 변환 금지)"를 명시하는데 현재 파이프라인은 완성 답안을 그대로 써준다. Draft 경계선의 규정 버전: *사람이 해야 한다고 규정된 것*도 넘지 않는다. 신호 감지는 결정적(정규식), 감지 시 최종 답안 대신 ①개념 정리 ②유사 예제 시연 ③검산 체크리스트만 산출.

**Files:**
- Create: `until/understanding/integrity.py`
- Modify: `until/execution/prompts.py` (`INTEGRITY_STUDY_MODE` 상수 + `study_mode_directive()`)
- Modify: `until/pipeline.py` (감지 → `spec["integrity_gate"]` + 지침 주입 + `min_dec=0`)
- Modify: `until/llm/mock_client.py` (게이트 감지 시 학습 보조 mock 초안)
- Modify: `until/readiness.py` ('규정' 항목)
- Modify: `run_tests.py` (`SUITES`에 `"test_integrity"` 등록)
- Create: `tests/test_integrity.py`

**Interfaces:**
- Consumes: `spec: dict`, `docs`(Document 목록, `.text` 속성).
- Produces:
  - `until.understanding.integrity.detect_no_ghostwriting(spec, docs=None) -> Optional[IntegrityGate]`, `IntegrityGate(dataclass)` 필드: `snippet: str`(매치 원문 조각), `reason: str`(사람이 읽는 한 줄).
  - `spec["integrity_gate"]`에 `reason` 문자열 저장(있을 때만) — mock·readiness·웹이 이 키로 판별.
  - `prompts.study_mode_directive(reason: str) -> str`.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_integrity.py` 신규:

```python
"""T4 대필 금지 신호 게이트 — 자필 규정 감지(결정적) + 학습 보조 모드 강등."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.understanding.integrity import detect_no_ghostwriting


class _Doc:
    def __init__(self, text): self.text = text; self.source = "x"


def test_detect_handwriting_signals():
    # 실코퍼스 문구(물리학 1 숙제) — 반드시 감지.
    g = detect_no_ghostwriting({}, [_Doc(
        "종이에 작성한 답안의 스캔이나 사진, 또는 손글씨를 그대로 남긴 "
        "파일을 제출하세요 (폰트 변환하시면 안됩니다)")])
    assert g is not None
    assert "손글씨" in g.snippet or "종이에" in g.snippet
    # spec 필드에서도 감지.
    g2 = detect_no_ghostwriting({"constraints": ["자필 답안만 인정"]})
    assert g2 is not None
    print("OK 감지 — 손글씨/자필/종이 답안")


def test_no_false_positives():
    # 오탐 방지: 활동 '사진', 단순 '스캔 제출', '자필 서명', 에세이 지시문.
    assert detect_no_ghostwriting({}, [_Doc("활동 사진을 첨부하고 pdf로 변환 후 업로드")]) is None
    assert detect_no_ghostwriting({}, [_Doc("보고서를 스캔하여 업로드하세요")]) is None
    assert detect_no_ghostwriting({}, [_Doc("서약서에 자필 서명 후 첨부")]) is None
    assert detect_no_ghostwriting({"goal": "자신의 견해를 논하시오"}) is None
    print("OK 오탐 0 — 사진/스캔/서명/에세이")


def test_gated_pipeline_study_mode():
    """감지 시: 학습 보조 초안(최종 답안 없음) + readiness '규정' 안내."""
    import tempfile, os
    from until.config import Config
    from until.pipeline import run
    from until.readiness import assess_readiness
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "spec.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("숙제1 (1-8번 각 10점)\n\n문제를 풀어 제출하세요. "
                    "종이에 작성한 답안의 스캔이나 사진, 또는 손글씨를 그대로 "
                    "남긴 파일만 인정합니다 (폰트 변환하시면 안됩니다).")
        res = run([p], Config(backend="mock"))
    assert res.spec.get("integrity_gate"), "게이트가 spec에 기록돼야 함"
    body = res.draft.body
    assert "학습 보조" in body           # 모드 전환된 초안
    assert "검산" in body                # 체크리스트 포함
    assert "## 문제 1" not in body       # 완성 답안 형태가 아님
    r = assess_readiness(res)
    assert any(i.label == "규정" for i in r.items), [i.label for i in r.items]
    print("OK 게이트 e2e — 학습 보조 모드 + 규정 안내")


if __name__ == "__main__":
    test_detect_handwriting_signals()
    test_no_false_positives()
    test_gated_pipeline_study_mode()
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONIOENCODING=utf-8 python tests/test_integrity.py`
Expected: FAIL — `until.understanding.integrity` 모듈 없음.

- [ ] **Step 3: `until/understanding/integrity.py` 작성**

```python
"""대필 금지 신호 감지 — Draft 경계선의 '규정' 버전 (결정적, LLM 0).

실코퍼스 근거(docs/planning/type_algorithms.md T4): 물리학 숙제가 "종이에 작성한
답안의 스캔이나 사진, 또는 손글씨를 그대로(폰트 변환 금지)"를 명시 — 대필 산출물
제출을 규정으로 차단한 유형. 사람의 고유 '판단'뿐 아니라 사람이 해야 한다고
'규정된 것'도 넘지 않는다. 감지 시 파이프라인은 최종 답안 생성을 학습 보조 모드
(개념 정리·유사 예제 시연·검산 체크리스트)로 강등한다.

오탐 주의: '사진'(조별 활동사진)·'스캔'(단순 업로드 절차)·'자필 서명'(서약서)은
자필 '답안' 요구가 아니다 — 신호를 좁게 잡는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class IntegrityGate:
    snippet: str   # 매치된 원문 조각(근거 인용)
    reason: str    # 사람이 읽는 한 줄


# 강한 신호만 — 넓히면 에세이("본인이 직접 작성")까지 게이트돼 제품이 망가진다.
_SIGNALS = [
    re.compile(r"손\s*글씨"),
    re.compile(r"자필(?!\s*서명)"),          # '자필 서명'(서약서)은 제외
    re.compile(r"수기로\s*(작성|기록|풀)"),
    re.compile(r"손으로\s*(작성|쓴|풀|직접\s*푼)"),
    re.compile(r"종이에\s*(작성|풀|쓴)[^\n]{0,20}답안"),
    re.compile(r"답안[^\n]{0,20}종이에\s*(작성|풀|쓴)"),
    re.compile(r"폰트\s*변환[^\n]{0,15}(안\s*됩|안됨|금지|불가|마세요)"),
]


def _texts(spec: dict, docs) -> list:
    parts = [
        str(spec.get("goal") or ""),
        str(spec.get("deliverable") or ""),
        " ".join(str(r) for r in (spec.get("requirements") or [])),
        " ".join(str(c) for c in (spec.get("constraints") or [])),
    ]
    for d in docs or []:
        parts.append((getattr(d, "text", "") or "")[:4000])
    return parts


def detect_no_ghostwriting(spec: dict, docs=None) -> Optional[IntegrityGate]:
    """spec·원문에서 자필 제출 규정 신호를 찾는다. 없으면 None."""
    for text in _texts(spec or {}, docs):
        for rx in _SIGNALS:
            m = rx.search(text)
            if m:
                lo = max(0, m.start() - 15)
                snippet = text[lo:m.end() + 25].strip().replace("\n", " ")
                return IntegrityGate(
                    snippet=snippet,
                    reason=f"자필 제출 규정 감지 — \"{snippet}\"",
                )
    return None
```

- [ ] **Step 4: 학습 보조 모드 지침** — `until/execution/prompts.py`, `TYPE_GUIDANCE` dict 정의 **앞**(`length_directive` 함수 뒤)에 추가:

```python
def study_mode_directive(reason: str) -> str:
    """대필 금지 규정 감지 시 실행 지침 — 최종 답안 대신 학습 보조만 산출.

    Draft 경계선의 규정 버전: 사람이 해야 한다고 규정된 것은 넘지 않는다.
    """
    return (
        "[ 규정 게이트 — 자필 제출 과제 (최우선, 다른 유형 지침보다 우선) ]\n"
        f"- 이 과제는 자필 답안 제출이 규정이다({reason}).\n"
        "- **과제 문제의 최종 답·수치·풀이를 쓰지 마라** — 대신 써 주면 규정 위반"
        " 산출물이 된다.\n"
        "- 대신 딱 3가지만 산출한다:\n"
        "  ① 이 문제들을 푸는 데 필요한 핵심 개념·공식 정리(자료 근거)\n"
        "  ② 같은 유형의 '유사 예제'를 하나 만들어 풀이 과정을 단계별로 시연\n"
        "  ③ 스스로 검산할 체크리스트(단위·극한값·근거 설명 가능 여부)\n"
        "- 첫 줄에 왜 답안을 쓰지 않았는지 한 줄로 밝힌다"
        "(예: '이 과제는 자필 제출이 규정이라 학습 보조만 담았어요')."
    )
```

- [ ] **Step 5: 파이프라인 배선** — `until/pipeline.py`, `spec["task_type"] = task_type` 직후(80행 부근)에:

```python
    # 2.1 대필 금지 신호 게이트(결정적) — 자필 규정 과제는 최종 답안 대신
    #     학습 보조 모드(기획 T4). 감지 근거는 spec에 실려 mock·readiness가 본다.
    from .understanding.integrity import detect_no_ghostwriting
    gate = detect_no_ghostwriting(spec, docs)
    if gate is not None:
        spec["integrity_gate"] = gate.reason
```

`min_dec` 계산(106행 부근)을 다음으로 변경:

```python
    min_dec = 0 if (task_type in FACTUAL_TYPES or gate is not None) else cfg.min_decisions
```

`system_extra` 조립(Task 2에서 수정한 블록)을 게이트 시 학습 보조 지침이 **앞에** 오도록 변경:

```python
    from .execution.prompts import study_mode_directive
    gate_directive = study_mode_directive(gate.reason) if gate is not None else ""
    system_extra = "\n\n".join(
        b for b in (gate_directive,
                    type_guidance(task_type),
                    skeleton_directive(task_type, content_elements),
                    decision_directive(task_type),
                    length_directive(length_target),
                    form_directive(docs), profile_hint()) if b)
```

- [ ] **Step 6: mock 학습 보조 초안** — `until/llm/mock_client.py`의 `_execution` 시작부, `ttype = self._task_type(user)` **앞**에:

```python
        # 규정 게이트(spec.integrity_gate) — 유형 초안보다 우선: 답안 대신 학습 보조.
        if '"integrity_gate"' in user:
            return (
                "# 학습 보조 (자필 제출 규정)\n\n"
                "이 과제는 자필 답안 제출이 규정이라, 최종 답안 대신 학습 보조만 담았어요.\n\n"
                "## 핵심 개념 정리\n"
                "문제를 푸는 데 필요한 개념과 공식을 자료에 근거해 정리했다 [자료1]. "
                "각 개념이 어느 문제에 쓰이는지 함께 표시했다.\n\n"
                "## 유사 예제 풀이 시연\n"
                "과제와 같은 유형의 예제를 하나 만들어 풀이 과정을 단계별로 시연한다. "
                "이 흐름을 참고해 본 문제는 직접 풀어 보길 권한다.\n\n"
                "## 검산 체크리스트\n"
                "- 단위와 유효숫자를 확인했는가\n"
                "- 극한값(0·무한대)에서 답이 상식과 맞는가\n"
                "- 각 단계의 근거(법칙·정의)를 말로 설명할 수 있는가\n"
            )
```

- [ ] **Step 7: readiness '규정' 항목** — `until/readiness.py`의 `assess_readiness`, '자료' 점검 블록 **앞**(68행 부근)에:

```python
    # 규정 — 자필 제출 규정이 감지되면 학습 보조 모드로 강등됐음을 안내(info).
    # 경고가 아니라 '왜 답안이 없는가'의 설명이다(제품 신뢰 — 기획 T4).
    gate_reason = (result.spec or {}).get("integrity_gate")
    if gate_reason:
        r.items.append(ReadinessItem(
            "규정", "info", f"{gate_reason} — 최종 답안 대신 학습 보조(개념·예제·검산)만 담았어요"))
```

- [ ] **Step 8: 러너 등록** — `run_tests.py`의 `SUITES` 마지막 그룹에 `"test_integrity",` 추가:

```python
    "test_voice_autolearn", "test_spec_check", "test_teacher_feedback",
    "test_integrity",
```

- [ ] **Step 9: 테스트 통과 확인**

Run: `PYTHONIOENCODING=utf-8 python tests/test_integrity.py`
Expected: PASS (3 테스트).

- [ ] **Step 10: 전체 러너**

Run: `python run_tests.py`
Expected: `pass=44 fail=0`. 주의: 기존 problemset 테스트(examples/sample_problemset.txt)가 손글씨 문구를 포함하면 게이트에 걸려 깨진다 — 그 경우 예제 파일이 아니라 **테스트 기대를 확인**하고, 예제에 자필 문구가 없으면 영향 없음(먼저 `grep -n "손글씨\|자필" examples/*.txt`로 확인).

- [ ] **Step 11: 커밋**

```bash
git add until/understanding/integrity.py until/execution/prompts.py until/pipeline.py until/llm/mock_client.py until/readiness.py run_tests.py tests/test_integrity.py
git commit -m "T4 대필 금지 신호 게이트 — 자필 규정 과제는 학습 보조 모드로 강등

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: 원료 없음 → 자료 요청 결정 규칙

기획 근거: `type_algorithms.md` §9-2 — 원료 없는 유형이 초안을 지어낼 위험(T1a 강의 키워드 없음, T3 실습지 없음). "자료가 없으면 본문 대신 자료 요청 결정을 내라"(경계선의 소극적 형태). 결정적 판정: 참고 자료가 과제 명세 문서 하나뿐이면 원료 없음.

**Files:**
- Modify: `until/execution/prompts.py` (`material_gap_directive()`)
- Modify: `until/pipeline.py` (판정 + 주입 + `spec["material_gap"]`)
- Modify: `until/readiness.py` ('자료' 안내 항목)
- Test: `tests/test_readiness.py` (판정·안내), `tests/test_skeleton.py` (지침 내용)

**Interfaces:**
- Consumes: `docs`(과제 문서 목록), `context_sources`(SourceDoc 목록), `task_type`.
- Produces: `prompts.material_gap_directive(task_type: str) -> str`(해당 유형 아니면 빈 문자열), `spec["material_gap"] = True`(판정 시).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_skeleton.py`에 추가:
```python
def test_material_gap_directive():
    """원료 없음 지침 — 반응형·실습레포트만, 지어내기 금지 + 자료 요청 결정."""
    from until.execution.prompts import material_gap_directive
    refl = material_gap_directive("reflective_report")
    assert "지어내" in refl and "DECISION" in refl
    rep = material_gap_directive("report")
    assert rep != ""
    assert material_gap_directive("essay") == ""      # 에세이는 spec 자체가 원료
    assert material_gap_directive("inquiry") == ""    # 질의는 제목·주제면 충분
    print("OK material_gap_directive — 대상 유형 한정")
```

`tests/test_readiness.py`에 추가:
```python
def test_material_gap_flag_and_readiness():
    """원료 없음(첨부·맥락 0) — spec 플래그 + readiness '자료' 안내."""
    import tempfile, os
    from until.config import Config
    from until.pipeline import run
    from until.readiness import assess_readiness
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "spec.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("실습2 레포트\n\n이번 주 실습 내용을 정리하여 레포트로 제출하세요.")
        res = run([p], Config(backend="mock"))
    assert res.spec.get("task_type") == "report", res.spec.get("task_type")
    assert res.spec.get("material_gap") is True
    r = assess_readiness(res)
    msgs = " ".join(i.message for i in r.items if i.label == "자료")
    assert "원료" in msgs or "자료 없이" in msgs, msgs
    print("OK material gap — 플래그 + 자료 안내")
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONIOENCODING=utf-8 python tests/test_skeleton.py` → FAIL (`material_gap_directive` import 에러)
Run: `PYTHONIOENCODING=utf-8 python tests/test_readiness.py` → FAIL (`material_gap` 키 없음)

- [ ] **Step 3: 지침 함수** — `until/execution/prompts.py`, `study_mode_directive` 뒤에:

```python
# 원료(강의 내용·실습 데이터)가 과제 명세 밖에 있는 유형 — 원료 없이 돌리면
# 일반론 초안을 지어낼 위험(기획 §9-2). 유형별 '요청할 원료'가 다르다.
_MATERIAL_GAP_ASKS = {
    "reflective_report": "강의에서 인상 깊었던 대목 2~3개(키워드면 충분)",
    "report": "실습지(과제 안내 pdf)나 측정 결과 요약(수치·관찰 몇 줄)",
}


def material_gap_directive(task_type: str) -> str:
    """원료 없음 감지 시 실행 지침 — 본문을 일반론으로 지어내지 말고 자료 요청.

    경계선의 소극적 형태: 자료로 채울 수 없으면 채우지 않는 것도 경계선이다.
    """
    ask = _MATERIAL_GAP_ASKS.get(task_type or "")
    if not ask:
        return ""
    return (
        "[ 원료 없음 — 지어내지 말 것 ]\n"
        "- 이 과제의 핵심 원료(강의 내용·실측 데이터)가 참고 자료에 없다.\n"
        "- 일반 상식·그럴듯한 일반론으로 본문을 채우지 마라 — 그건 이 학생의 "
        "산출물이 아니라 거짓말이다.\n"
        "- 자료로 확실한 부분(구조·골격·형식)까지만 쓰고, 원료가 들어갈 자리에는 "
        f"빈칸형 결정을 남겨라: [[DECISION: {ask}: ___]]\n"
        "- 원료를 받으면 끝까지 쓸 수 있음을 본문 첫머리에 한 줄로 안내하라."
    )
```

- [ ] **Step 4: 파이프라인 판정·주입** — `until/pipeline.py`. `context_sources` 계산 직후(105행 부근)에:

```python
    # 원료 없음 판정(결정적) — 과제 명세 문서 1개뿐이고 맥락 자료도 없으면,
    # 원료가 필요한 유형(반응형·실습레포트)은 지어내기 대신 자료 요청 결정(기획 §9-2).
    from .execution.prompts import material_gap_directive
    gap_directive = ""
    if len(docs) <= 1 and not context_sources:
        gap_directive = material_gap_directive(task_type)
        if gap_directive:
            spec["material_gap"] = True
```

`system_extra` 조립에 `gap_directive` 추가(게이트 지침 다음, 유형 지침 앞):

```python
    system_extra = "\n\n".join(
        b for b in (gate_directive,
                    gap_directive,
                    type_guidance(task_type),
                    skeleton_directive(task_type, content_elements),
                    decision_directive(task_type),
                    length_directive(length_target),
                    form_directive(docs), profile_hint()) if b)
```

- [ ] **Step 5: readiness 안내** — `until/readiness.py`, '규정' 항목 블록 뒤에:

```python
    # 자료(원료 없음) — 첨부·맥락 자료가 없어 초안이 골격까지만 작성됐음을 안내.
    if (result.spec or {}).get("material_gap"):
        r.items.append(ReadinessItem(
            "자료", "info",
            "핵심 원료(강의 내용·실측 데이터)가 자료에 없어 초안은 골격까지만 — "
            "결정 칸에 원료를 답하면 마저 채울 수 있어요"))
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `PYTHONIOENCODING=utf-8 python tests/test_skeleton.py` → PASS
Run: `PYTHONIOENCODING=utf-8 python tests/test_readiness.py` → PASS

- [ ] **Step 7: 전체 러너**

Run: `python run_tests.py`
Expected: `fail=0`. 주의: report 유형 mock 초안(`_TYPE_DRAFTS["report"]`)은 그대로 나온다(유형 초안이 게이트 다음 우선) — material_gap은 지침 주입이라 mock 본문엔 영향 없음. e2e 단언은 spec 플래그·readiness로만 한다(위 테스트가 이미 그렇게 작성됨).

- [ ] **Step 8: 커밋**

```bash
git add until/execution/prompts.py until/pipeline.py until/readiness.py tests/test_skeleton.py tests/test_readiness.py
git commit -m "원료 없음 → 자료 요청 결정 규칙 — 반응형·실습레포트 지어내기 방지(기획 §9-2)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: 문서 갱신 — CHANGELOG·기획 문서 상태 반영

**Files:**
- Modify: `CHANGELOG.md` (Unreleased 절 최상단)
- Modify: `docs/planning/type_algorithms.md` (우선 구현 순서 표에 구현 상태 표기)

**Interfaces:** 없음(문서만).

- [ ] **Step 1: CHANGELOG Unreleased 최상단에 항목 추가**

```markdown
- **유형별 알고리즘 1차 구현(기획 `type_algorithms.md` 우선순위 1~4, 2026-08-02):**
  ① T1b 질의 전용 유형(`inquiry`) — 프레임(전망/사례/진로/방법론/한계)별 질문
  후보 5개 생성 + 선택 결정 1개(미제출 1위 유형) ② 유형별 결정 골격 분리
  (`skeleton.decision_directive`) — 반응형이 에세이 결정 3종을 받던 범주 착오
  수정, mock에 반응형·질의 초안 추가 ③ T4 대필 금지 신호 게이트
  (`understanding/integrity.py`, 결정적) — 손글씨·자필 규정 감지 시 최종 답안
  대신 학습 보조(개념·유사 예제·검산)로 강등 + readiness '규정' 안내
  ④ 원료 없음 → 자료 요청 결정 규칙 — 반응형·실습레포트가 원료(강의 내용·
  실측) 없이 일반론을 지어내지 않도록 빈칸형 결정 주입 + readiness '자료' 안내.
  신규 스위트 test_integrity(44스위트).
```

- [ ] **Step 2: 기획 문서 상태 표기** — `docs/planning/type_algorithms.md`의 "우선 구현 순서 제안" 표(219행 부근)의 각 행에 상태 열을 추가하거나 표 아래에 한 줄 추가:

```markdown
> 구현 현황(2026-08-02): 1(질의 후보)·2(결정 골격)·3(대필 게이트)·4(원료 없음 규칙)
> 구현 완료 — CHANGELOG Unreleased 참조. 5(Rmd 청크)·6(zip 파싱)은 미착수.
```

- [ ] **Step 3: 전체 러너 최종 확인**

Run: `python run_tests.py`
Expected: `pass=44 fail=0` (test_runners의 문서-코드 정합 감사 포함 통과).

- [ ] **Step 4: 린트**

Run: `ruff check .`
Expected: 에러 0. 위반 시 해당 파일만 수정.

- [ ] **Step 5: 커밋**

```bash
git add CHANGELOG.md docs/planning/type_algorithms.md
git commit -m "docs: 유형별 알고리즘 1차 구현 기록 — CHANGELOG·기획 문서 상태

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 자체 점검 메모 (계획 작성 시 확인한 리스크)

- **`"질의"` 신호 이관(Task 1):** `tests/test_task_type.py` 49행의 기대값이 유일한 기존 참조(확인됨). 다른 스위트가 질의 spec으로 reflective를 기대하면 전체 러너에서 잡힌다.
- **mock 유형 초안 추가(Task 1·2)가 reask 루프 테스트를 깨는지:** `test_pipeline.py::test_end_to_end_with_guard`는 `sample_assignment.txt`(에세이)를 쓰므로 reask 루프 검증은 유지된다. 반응형·질의는 유형 초안 즉시 통과가 이제 정상.
- **게이트 mock 판별 `'"integrity_gate"' in user`(Task 3):** spec_json이 user 메시지에 그대로 실리므로 안전. 단 finalize·suggest 프롬프트에도 spec_json이 실린다 — mock의 게이트 분기는 `_execution`에만 넣는다(다른 태그 핸들러는 건드리지 않음).
- **`min_dec=0` + 게이트:** 학습 보조 초안은 결정 0개가 정상(내용이 답안이 아니므로). readiness의 '경계선' 경고는 가드 통과 시 안 나온다(위 초안은 본문 200자 이상이라 통과).
- **material_gap과 reflective mock의 정합(Task 2·4):** 반응형 mock 초안의 결정이 이미 '키워드 요청' 형태라 원료 없음 규칙과 서사가 일치한다(mock은 지침을 안 읽지만 산출이 규칙에 부합).
