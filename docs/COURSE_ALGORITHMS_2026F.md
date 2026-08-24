# 2026-2학기 6과목 알고리즘 (algo_version v0.2)

> **복원 경위 — 이 문서는 원본이 아니다.**
>
> 이 설계문서는 **한 번도 커밋된 적이 없다.** git 이력 전수 검색과 디스크 검색 모두
> 음성이었다. 그런데 코드·문서·서브에이전트 정의 22곳이 이 문서를 절 번호까지 붙여
> 인용하고 있고 그 번호들이 서로 모순 없이 맞물린다 — 실재했던 문서를 보며 코드를
> 썼으나 커밋되지 않은 것으로 보인다.
>
> **2026-08-21에 세 출처에서 재구성했다:** ① `CHANGELOG.md`의 v0.2 항목(산문 요약),
> ② 이 문서를 인용하는 코드 주석 22곳과 그 구현, ③ `tests/test_assignment_router.py`·
> `tests/test_course_profiles.py`에 고정된 케이스 목록. 각 절 끝에 근거 출처를 적었고,
> **코드에서 역산한 것**과 **산문에서 옮긴 것**을 구분했다.
>
> 따라서 이 문서는 **원본과 다를 수 있다.** 특히 §1·§2는 인용 지점이 거의 없어
> 대부분 재구성이다. 확정할 수 없는 부분은 지어내지 않고 `> ⚠ 미복원:` 으로 남겼다.
> 개강 후 6과목 라우팅을 재검증할 때, 이 문서와 코드가 어긋나면 **코드가 기준이다** —
> 이 문서가 코드에서 역산됐기 때문이다.

---

## §1. 목적과 범위

> ⚠ **인용 지점 없음 — 재구성.** 이 절을 직접 인용하는 코드·문서가 하나도 없다.
> 아래는 다른 절의 문맥에서만 유도한 최소 골격이며, 원본 §1의 실제 내용은 알 수 없다.

이 문서는 **2026년 2학기 6과목을 실제로 수강하면서 나온 과제들**을 현행 라우팅
(`algo_version` v0.1)에 통과시켰을 때 어디가 어긋나는지를 조사하고, 그 간극을 메우는
신설 규칙·골격·측정 계획을 정한다. 여기서 정한 것은 전부 `UNTIL_ALGO_VERSION=v0.2`
게이트 뒤에 구현되며, 기본값(v0.1)의 동작을 바이트 단위로 바꾸지 않는다(§8).

이 문서가 다루지 않는 것: LLM 호출이 개입하는 계층. 라우팅은 완전 결정적이고
LLM 호출이 0이다(불변 규칙 3). v0.2 신설분도 예외가 아니다.

(근거: 이 문서를 인용하는 지점 전체의 문맥 + `CLAUDE.md:57` "6과목 라우팅 설계는
`docs/COURSE_ALGORITHMS_2026F.md`" + `.claude/agents/until-router.md:19` "이 계층은
LLM 호출 0, 완전 결정적이다(불변 규칙 3)")

---

## §2. 6과목 실측 — 현행 라우팅이 어긋나는 지점

> ⚠ **인용 지점이 간접적 — 상당 부분 재구성.** 코드 주석 네 곳이 "§2 실측"을 근거로
> 개별 규칙을 정당화할 뿐, 원본 §2의 과제 목록·판정표는 남아 있지 않다. 아래 표는
> **§6 목표 케이스에 등장하는 과목을 묶어 역산한 것**이다.

`until/context/assignment_router.py:73`은 이 절을 이렇게 요약한다:

> "2026-2학기 6과목 실측(설계문서 §2)에서 **4과목이 현행 라우팅을 벗어나던 것**을 메운다."

§6 목표 케이스에 등장하는 과목은 정확히 6개이며, 과목별로 확인된 어긋남은 다음과 같다.

| 과목 | 현행(v0.1)에서 벌어지던 일 | 처방 | 근거 |
|---|---|---|---|
| 논리설계실습 | `실습 3 보고서`가 `distributed_spec`, `Lab 4`가 `evidence_report`, Verilog zip 첨부는 `zip_project` — HDL 산출물이 세 갈래로 흩어짐 | §4.1 `hdl_lab` 신설 | `assignment_router.py:75`, `tests/test_assignment_router.py:255-267` |
| 실험과목 | 예비/랩노트/결과 세 단계가 각각 `evidence_report`·`spec_clarification`·`evidence_report`로 흩어져, 단계별로 정반대인 금지 규칙을 걸 수 없음 | §4.2 `lab_report_cycle` 신설 | `assignment_router.py:83`, `tests/test_assignment_router.py:284-289` |
| 교재문제과목 | `중간과제 1`이 `spec_clarification`, `과제 3`이 `distributed_spec` — 문항이 교재에 있어 eTL이 못 가져오는 유형을 표현할 경로가 없음 | §4.3 `textbook_problem_set` 신설 | `assignment_router.py:88`, `tests/test_assignment_router.py:247-253` |
| 프로그래밍과목 | `프로그래밍 과제 5`가 `_CODE`에 안 걸려 `spec_clarification`으로 샘 ('프로그래밍'은 '프로그램'의 부분문자열이 아니다) | §4.4 `_CODE_V2` 확장 | `assignment_router.py:95, 277-278`, `tests/test_assignment_router.py:273-274` |
| 활동보고과목 | `활동보고서 제출`이 `_REPORT`(보고서)에 먼저 걸려 `evidence_report`가 됨 | §4.5 `_FORM_V2` 확장 | `assignment_router.py:97`, `tests/test_assignment_router.py:279-282` |
| 세미나과목 | 라우팅은 이미 정확(`reflective_series`). 다만 200자 상한·당일 마감 과제에 5슬롯 골격을 넣으면 슬롯당 40자로 전부 공허해짐 | §4.6(b) 단문 3슬롯 | `until/understanding/skeleton.py:111`, `tests/test_assignment_router.py:291-292` |

> ⚠ **미복원 — 수치가 맞지 않는다.** 코드 주석은 "6과목 중 **4과목**이 현행 라우팅을
> 벗어난다"고 하는데, 위 표에서 라우팅이 어긋나는 과목은 **5개**다(세미나과목만
> 정확). 원본 §2가 프로그래밍과목·활동보고과목 중 하나를 '라우팅 이탈'이 아니라 '어휘 누락'으로
> 따로 분류했거나, 6과목 명단 자체가 위 재구성과 다를 수 있다. **개강 후 재검증 시
> 가장 먼저 확인할 항목이다.**
>
> 명단 자체에도 확정되지 않은 고리가 하나 있다: 코드 주석은 `논리설계실습`,
> 테스트는 `논리설계실습`과 `논리설계실습 FPGA 설계`를 쓰는데, **`논리설계실습`이 `논리설계실습`의
> 축약형이라는 것은 추론이다** — 둘을 같은 과목으로 잇는 근거를 원료에서 찾지 못했다.
> 별개 과목이라면 위 6과목 명단과 "4과목" 수치가 함께 바뀐다.

> ⚠ **미복원.** 원본 §2에는 과목별 과제 목록과 v0.1 판정 컬럼이 있었던 것으로 보이나
> 복원 불가. 다만 그 판정값은 이미 **낡아 있었다** — `tests/test_assignment_router.py:243-244`가
> "v0.1 기대값은 설계문서 §2가 아니라 '현재 코드를 v0.1로 실행해 얻은 실측 판정'이다
> (**§2는 코드보다 낡았다**)"라고 못 박아 뒀다. 즉 원본 §2의 판정표를 그대로 복원했더라도
> 기준으로 쓸 수 없었다.

---

## §3. course_profiles — 과목 프로파일 폴백

과목명이 축약형이고 본문·첨부가 비면 어휘 감지가 무력하다(논리설계실습 첫 주차 실측).
학기 초 1회, 사용자가 과목마다 알고리즘 힌트를 확정하는 **얇은 폴백 레이어**를 둔다.
저장 위치는 `_until_work/course_profiles.json`(사용자 소유·로컬).

> **2026-08-22 갱신 — 사용자별 저장 + 입력 화면.** 원안의 저장 경로는 프로세스
> 전역 파일 하나였다. 로컬 단일 사용자에서는 맞지만 **클라우드에서는 한 사람이
> 적은 힌트가 전원에게 걸린다.** 게다가 값을 적을 화면이 없어서, 설계·구현·시험이
> 다 있는 이 절이 라이브에서 성립한 적이 없었다(`run(course_name=…)` 미전달로
> 판정 시점 과목명이 늘 비어 있던 2026-08-21 건과 같은 계보 — 그쪽을 고쳐도
> 읽을 파일이 없었다).
>
> - 경로는 요청 스코프 오버라이드다(`set_course_profiles_path_override`,
>   `profile.py`·`answer_history`와 같은 패턴). 클라우드는
>   `users/<uid>/course_profiles.json`, 로컬은 종전 경로 그대로.
> - KV 미러 키 `cprof:{uid}`(영속 TTL) — 학기 초 1회 적는 값이라 콜드스타트에
>   날아가면 그 학기 내내 폴백이 꺼진 채 돌고 사용자는 이유를 알 수 없다.
> - 입력 화면은 `/profile`의 **과목 유형** 패널, 저장은 `POST /profile/courses`
>   (두 서버 모두). 과목명은 최근 세션의 과목명을 `datalist`로 자동완성한다.
> - 저장은 로더와 **같은 검증**을 통과한 것만 남긴다(`save_course_profiles`) —
>   허용 밖 힌트·유형 미지정·조회 불가 항목은 버린다. 저장된 것은 반드시 적용
>   가능한 것뿐이어야 "적었는데 안 먹는다"가 안 생긴다.
> - 화면은 적용 조건 (a)(b)를 그대로 밝힌다 — 본문이 이기고, `non_actionable`은
>   못 뒤집는다. v0.1 배포에서는 "아직 적용되지 않음"을 명시한다.

### 스키마

최상위는 dict, `courses`는 리스트. 각 항목은 `course_id` 또는 `alias` 중 하나는
있어야 한다(둘 다 없으면 어느 키로도 조회할 수 없어 버린다). 그 밖의 키
(`toolchain`·`board`·`series`·`cycle` 등)는 소비자가 쓸 수 있게 그대로 보존한다.

> ⚠ 아래 예시는 **원본 문서에서 옮긴 것이 아니라 `tests/test_course_profiles.py:29-38`의
> `_VALID` 픽스처**다. 스키마의 **형태**는 이것으로 확정되지만, 개별 값
> (`123456`·`Basys3`·`vivado`)이 원본 §3의 예시와 같았는지는 알 수 없다.

```json
{
  "algo_version": "v0.2",
  "courses": [
    {"course_id": "123456", "alias": "논리설계실습", "route_hint": "hdl_lab",
     "toolchain": ["vivado"], "board": "Basys3", "series": ["실습", "lab"]},
    {"course_id": "123457", "alias": "실험과목", "route_hint": "lab_report_cycle",
     "cycle": ["pre", "notebook", "result"]},
    {"course_id": "123458", "alias": "교재문제과목", "route_hint": "textbook_problem_set"}
  ]
}
```

`route_hint`의 허용값은 **v0.2 신설 strategy 3종뿐**이다:
`hdl_lab` · `lab_report_cycle` · `textbook_problem_set`.
**허용 집합 밖이면 무시한다** — 오타·임의 문자열이 존재하지 않는 경로를 켜는 사고를 막는다.

조회는 `course_id` 정확 일치 우선, 없으면 `alias`↔과목명 **부분일치**. 부분일치를 쓰는
이유는 eTL 과목명이 `논리설계실습(디지털) 설계 및 실험`처럼 길고 사용자가 적는 alias는
`논리설계실습` 같은 축약형이라 정확 일치가 성립하지 않기 때문이다.

### 적용 규칙 (코드로 강제)

- **(a)** `route_hint`는 **결정적 규칙이 아무것도 못 잡았을 때만**(= `spec_clarification`
  직전) 적용하는 폴백이다. **어휘 규칙을 이기지 못한다** — 사용자가 틀리게 적어도
  실제 명세가 이긴다.
- **(b)** `non_actionable` 판정은 힌트로 뒤집지 않는다(`route_inference`와 같은 안전 원칙).
  (b)를 (a)보다 먼저 본다 — 퀴즈·시험·증빙 슬롯에 초안을 만드는 사고를 막는다.

### 실패 처리

파일 없음·권한·JSON 깨짐·스키마 불일치는 **전부 빈 값/None으로 흡수**하고 예외를 밖으로
내지 않는다. 프로파일은 있으면 좋은 폴백이지, 없다고 파이프라인이 죽을 이유가 없다.
BOM을 허용한다(`utf-8-sig`) — 메모장으로 직접 편집하는 사용자 소유 파일이다.

### 역할 경계

이 레이어는 **순수 로더·판정만** 둔다. 힌트 → `AssignmentRoute` 변환은 라우터의
`route_for_strategy()`가, 버전 게이트(v0.2에서만 적용)는 호출부(파이프라인)가 맡는다.
그래서 로더에는 `algo_version` 분기가 없다.

(근거: `until/context/course_profiles.py` 전문 — 모듈 docstring이 §3 규칙 (a)(b)를
따옴표째 인용하고 있어 원문 복원도가 가장 높은 절이다 + `tests/test_course_profiles.py:29-38`의
`_VALID` 픽스처가 스키마를, `:81`·`:109`·`:126`이 각 규칙을 고정 + 적용 위치는
`until/pipeline.py:132-148`)

---

## §4. v0.2 신설 규칙

전부 `algo_version() == "v0.2"`에서만 발동한다. 기본(v0.1)은 아래 어느 분기도 타지 않는다(§8).

신설 strategy는 3종이다:

| strategy | 무엇인가 | task_type 매핑 |
|---|---|---|
| `hdl_lab` | HDL 실습 — RTL·테스트벤치·파형 증빙·설계 근거 고찰의 혼합 산출물 | `hdl_lab`(신설) |
| `lab_report_cycle` | 실험 3단 사이클(예비/랩노트/결과), `AssignmentRoute.stage`로 구분 | `report` |
| `textbook_problem_set` | 교재 문제 풀이 — 문항 본문이 제출함 밖에 있음 | `problemset` |

기존 규칙 확장은 §4.4(코드)·§4.5(활동 보고서)·§4.6(분량) 세 갈래다.

(근거: `until/context/assignment_router.py:72` 절 헤더 + `CHANGELOG.md:477-492` 산문 요약 +
`until/pipeline.py:178-188` strategy→task_type 매핑)

---

### §4.1 HDL 실습 (`hdl_lab`)

#### 왜 새 strategy인가

`evidence_report`(사진 증빙·방법→결과→고찰 골격)도 `code_project`(보고서 파트 소실)도
맞지 않는 **혼합 산출물**이다. 셋의 차이는 이렇다:

- 사전 설계가 구현보다 **앞선다**.
- 검증 증빙이 사진이 아니라 **파형·합성 리포트**다.
- 고찰의 채점 포인트가 "문장이 매끄러운가"가 아니라 **"왜 이 설계를 골랐는가"**다.

#### 왜 `code`로 흡수하지 않는가

`code`는 `FACTUAL_TYPES`라 **결정 0개가 허용**되는데, HDL 보고서의 '고찰'은 결정이
반드시 필요하다. 이것이 별도 `task_type`을 만드는 **유일한 이유**다. 그래서
`hdl_lab`은 `FACTUAL_TYPES`에 넣지 않는다.

`task_type` 진입은 **파이프라인의 strategy 매핑으로만** 한다 —
`classify_task_type()`의 키워드 감지에는 넣지 않는다(v0.1 경로에서 이 유형이 분류·발동되면 안 된다).

#### 감지

과목명/본문/첨부의 HDL 신호 **그리고** 회차형 제목(또는 본문·첨부 자체의 신호).

- HDL 어휘: `verilog` · `vhdl` · `systemverilog` · `fpga` · `vivado` · `quartus` ·
  `테스트벤치`/`testbench` · `xdc` · `rtl` · `논리 설계` · `hdl` · `합성 결과` · `넷리스트`
- HDL 첨부 확장자: `.v` `.sv` `.vhd` `.vhdl` `.xdc` `.qsf` `.bit`
- 회차형 제목: `^(실습|실험|lab)\s*#?\s*\d+`

툴체인 어휘를 **과목명까지** 보는 이유는 첫 주차처럼 본문·첨부가 비면 본문 어휘 감지가
무력하기 때문이다(§3 실측). 남는 구멍(축약 과목명 + 빈 본문)은 §3 폴백 몫이다.

HDL 신호가 없으면 삼키지 않는다 — `기초회로`의 `실습 1 보고서`는 `distributed_spec`으로 남는다(§6).

#### §4.1.5 행정 항목 배제 체크리스트

HDL 신호가 제목에 있어도 **산출물이 아닌 행정 항목**은 배제한다:
`설치` · `대여` · `신청` · `배부`.

적대적 회귀에서 `FPGA 보드 대여 신청`·`Verilog 설치 안내`가 `hdl_lab`으로 새던 실측 대응이다.
**`안내`는 넣지 않는다** — 실과제 제목(`Chapter 1,2,3 과제 안내`)에도 흔하다.

> ⚠ **미복원.** 원본이 §4.1.5를 "체크리스트"라 부른 것으로 보아 배제 항목 표가 더
> 길었을 가능성이 있다. 코드에 실재하는 것은 위 4개뿐이다.

#### 골격 (8슬롯)

슬롯 순서는 **작업 순서를 강제하는 장치**다. 실습 세션 2시간 안에 못 끝내는 원인이
사전 설계 누락이라, 코드부터 쓰고 사전 설계를 역산하지 않도록 골격이 순서를 강제한다.

| # | 슬롯 | 근거 종류 |
|---|---|---|
| 1 | 설계 목표·사양 | source_document |
| 2 | **사전 설계(진리표·상태도·K-map)** — 구현보다 먼저 | source_document |
| 3 | RTL 구현 | source_document |
| 4 | 검증(테스트벤치·시나리오) | source_document |
| 5 | 실측 증빙(파형·보드 동작) | user_experience |
| 6 | 합성 결과(LUT/FF·타이밍) | user_experience |
| 7 | 고찰 — 설계 선택의 근거 | user_experience |
| 8 | 오류·디버깅 기록 | user_experience |

5~8번은 툴 실행·본인 경험 없이는 채울 수 없는 `user_experience`다.

#### 하드 금지 — 실측 날조

**파형·합성 수치·보드 동작은 결정이 아니라 사실이다.** 자료에 실재할 때만 쓰고,
없으면 지어내지 말고 빈칸형 `[[DECISION]]`만 남긴다. 추정값 환각은 그대로 제출되어
학문적 부정이 된다.

이 금지는 세 겹으로 강제된다:
1. **지침** — `measured_ban`을 상시 주입(일반 원료 판정과 무관하게 항상).
2. **결정 골격** — `DECISION_SKELETONS["hdl_lab"]`이 "파형·합성 수치·보드 동작은
   결정이 아니라 사실이다 — 없으면 빈칸으로 남기고 절대 추정값을 쓰지 않는다"를 명시.
3. **사후 검출** — readiness가 `find_ungrounded_measurements()`로 근거 없는 수치를
   결정적으로 잡아 `fail` 처리.

실측 근거가 사실상 없으면(명세뿐) 원료 요청 결정으로 전환한다 — 일반 판정이
예비보고서 텍스트를 원료로 오인해 결과 수치까지 쓰게 두지 않는다.

#### 필요 근거·확인 질문

필요 근거: 실습 지시서(사전 설계 요구) · 진리표·상태도·K-map · Verilog/VHDL 소스·테스트벤치 ·
시뮬레이션 파형·보드 동작 캡처 · 합성 리포트(LUT/FF·타이밍) · 보고서 양식.

확인 질문 3개: 파형·보드 동작 캡처 확보 여부 / 합성 결과 수치(LUT/FF, 최대 주파수) /
설계를 택한 근거(인코딩·상태기계 방식).

(근거: `until/context/assignment_router.py:74-81, 99-119, 205-212` 구현 +
`until/understanding/task_type.py:65-72, 88` + `until/understanding/skeleton.py:83-108, 240-246` +
`until/execution/prompts.py:294-315` + `until/pipeline.py:184-186, 294-311` +
`until/readiness.py:160-182` + CHANGELOG 480-482, 490-491행)

---

### §4.2 실험 3단 사이클 (`lab_report_cycle`)

#### 구조

세 단계가 **같은 strategy를 공유하되 `stage`로 구분**한다. 단계마다 가능한 것과
금지된 것이 정반대이기 때문이다.

| stage | 감지 어휘(제목) | 계약 |
|---|---|---|
| `pre` | `예비 보고서`/`예비 레포트` · `pre-lab`/`prelab` · `사전 보고서` | 이론·목적·절차. **아직 실험 전 — 실측값 서술 금지** |
| `notebook` | `랩 노트` · `실험 노트` · `lab note(book)` · `노트 제출` | 현장 기록물 — **기록 템플릿까지만 생성, 내용 대필 금지** |
| `result` | `결과 보고서`/`결과 레포트` · `result report` · `본 보고서` | **랩노트 실측 없이 수치·그래프 생성 금지** |

`AssignmentRoute.stage`는 `lab_report_cycle`에서만 비어 있지 않다. 기본값 있는 마지막
필드로 두어 기존 위치 인자 호출과 하위호환을 지킨다. `stage`가 비면
`assignment_route_directive()`의 단계 줄이 통째로 빠져 v0.1 출력과 바이트 단위로 동일하다.

#### 단계명은 '제목'에서만 본다

결과보고서 본문이 "예비보고서를 바탕으로"를 언급하는 것이 정상이라, 본문까지 보면
단계가 뒤집힌다.

또한 단계명에**만** 반응하므로 `생물학실험`의 `실험 3 보고서` 같은 일반 실험 보고서를
뺏지 않는다(§6).

#### `experiment_id` — 실험 단위 연결

`series_key()`는 회차 번호를 **지워서** 같은 표면형끼리 묶는다(예비보고서 3주차 ↔
예비보고서 5주차 = 문체 참고용 시리즈). `lab_report_cycle`에 필요한 것은 **정반대**다 —
실험 번호를 **남겨서** 표면형이 다른 세 단계를 같은 실험으로 묶어야 한다.
그래서 `series_key()`는 한 글자도 건드리지 않고 `experiment_id()`를 옆에 둔다.

추출 규칙(순서대로, 앞 규칙이 잡으면 뒤는 보지 않는다):

1. `실험/실습/lab N` — 실험 회차의 가장 명시적인 표기
2. `N주차`/`N회차` — 실험과목 실코퍼스는 주차 = 실험 회차
3. 잔여 숫자가 **정확히 하나**면 그 숫자 (`예비보고서 4` 류)

날짜(`3/17`)는 실험 번호가 아니므로 먼저 지운다. 잔여 숫자가 둘 이상이면 확정할 수
없으므로 `""` — **잘못 묶는 것보다 안 묶는 것이 안전하다.**

결과: `실험 4 결과보고서` · `예비보고서 4주차` · `랩노트 제출(4주차)`가 전부 `exp-4`.
`기말 보고서`·`예비보고서 (3/17)`·`3장 문제 5`는 `""`.

#### 예비 → 결과 맥락 주입

**"결과보고서는 예비보고서 바탕"** — `stage="result"`일 때 같은 `experiment_id`의
**예비 단계 제출물만** 결과보고서 초안의 참고 자료로 주입한다.

제외 규칙: 자기 자신(재제출) · 다른 실험 번호 · 예비가 아닌 단계(랩노트·결과) ·
본문이 빈 것 · 조별 제출물(수집 단계에서 이미 걸러짐). 같은 실험의 예비는 보통 1건이라
재제출 대비 상한 2건.

주입되는 SourceDoc 본문에는 **경계선 지침을 명시한다** — 예비보고서에는 실측값이 없으므로
이 소스로 결과 수치를 만들면 안 된다(§4.2 하드 금지와 같은 원칙).

맥락 보강은 비치명이다 — 실패해도 초안 생성은 계속한다.

(근거: `until/context/assignment_router.py:82-87, 121-142, 213-218` +
`until/context/series.py:85-160` + `until/pipeline.py:228-238` +
`tests/test_course_profiles.py:138-186` + `.claude/agents/until-cycle.md:19-25` +
CHANGELOG 481-483행)

---

### §4.3 교재 문제 풀이 (`textbook_problem_set`)

#### 왜 새 strategy인가

문항이 교재에 있어 **eTL이 못 가져온다.** 기존 `problem_set`은 '문항 본문·데이터가
제출함 안에 있음'을 가정하므로 다른 경로가 필요하다.

#### 감지

`_MIDTERM_TASK`(`^(중간|기말)\s*과제\s*\d*$`) **또는**
교재 참조 어휘(`교재` · `textbook` · `N장 문제/연습` · `chapter N`)가 있고
제목에 `과제`/`문제`/`숙제`가 있을 때.

단, **`problem set` 계열(`_PROBLEM_SET`)은 제외한다** — 문항이 제출함 안에 있는 기존
`problem_set` 경로에 남긴다.

`중간과제 1`은 `_EXAM_ONLY`와 충돌하지 않는다 — `_EXAM_ONLY`는 단독 `중간`/`기말` 뒤에
숫자만 허용하므로 `과제`가 붙으면 매치되지 않는다.

#### 동작 — 학습 보조가 기본

**문항 본문이 없으면(기본) 학습 보조 모드까지만:** 교재·문항 확정 질문, 공식 정리,
유사 예제 시연. 문항 본문(사진·스캔)을 확보했을 때에만 `problemset` 골격으로 푼다.

**"아마 이런 문제일 것"이라며 문항을 지어내지 않는다(하드 금지).**

필요 근거: 교재명·장·문항 번호 · 문항 본문(사진·스캔) · 해당 장 공식·정의 ·
풀이 표기·수기 제출 규정.

#### 수기 풀이 지시

교재문제과목처럼 **손으로 옮겨 적어 제출**하는 과제에서는, 규정을 지킨 산출물이라도 긴 문단·
마크다운 표로 나오면 옮겨 적기 어려워 실사용이 막힌다. 그 마찰의 해소책으로,
자필 규정 게이트(`study_mode_directive`) 발동 시 v0.2에서는 형식 지시를 덧붙인다:

> 학생이 손으로 옮겨 적을 것을 전제로 **단계 번호 + 한 줄씩** 끊어 쓴다.
> 긴 문단·마크다운 표를 쓰지 않는다.

`handwritten` 기본값은 `False`이고 그때 출력은 현행과 바이트 동일하다. v0.2에서만 `True`로 넘긴다.

(근거: `until/context/assignment_router.py:88-93, 144-156, 219-223` +
`until/execution/prompts.py:156-186` + `until/pipeline.py:263-266` +
`tests/test_assignment_router.py:247-253, 380-` + CHANGELOG 483-484행)

---

### §4.4 코드 과제 — 어휘 확장과 스켈레톤 계약

#### (a) `프로그래밍 과제/숙제` → `code_project`

프로그래밍과목의 `프로그래밍 과제 5`가 `spec_clarification`으로 새던 실측 대응.
원인은 단순하다 — **`프로그래밍`은 `프로그램`의 부분문자열이 아니라** 기존 `_CODE`
어휘에 걸리지 않는다.

v0.2 전용 **별도 패턴**(`_CODE_V2`)으로 둔다. 기존 `_CODE`를 넓히지 않는 이유는
v0.1 불변 보장이다(§8).

#### (b) 스켈레톤 계약 지시

스켈레톤이 제공되는 프로그래밍 과제(자료구조 등)의 **최대 실패 모드는 명세 오해가 아니라
시그니처·파일 구조 변경**이다 — 채점기가 그대로 떨어뜨린다.

제공 코드·zip에서 TODO류 마커
(`// TODO` · `# TODO` · `/* TODO` · `구현하시오/세요/라` · `여기에 작성` ·
`your code here` · `fill in`)가 감지되면 실행 프롬프트에 계약 지시를 주입한다:

- 제공된 **함수 시그니처·파일명·클래스명을 한 글자도 바꾸지 않는다.**
- **TODO 표시된 자리 안에서만** 작성한다. 그 밖의 제공 코드는 건드리지 않는다.
- 제공되지 않은 표준 라이브러리 import를 추가하기 전에 금지 목록이 있는지 확인하고,
  없으면 `DECISION`으로 남긴다.

발동 대상은 `task_type`이 `code` 또는 `hdl_lab`일 때다.

(근거: `until/context/assignment_router.py:94-96, 277-284` +
`until/execution/prompts.py:188-203` + `until/pipeline.py:317-331` +
`tests/test_assignment_router.py:273-274` + CHANGELOG 485-487행)

---

### §4.5 활동 보고서 → `activity_form`

활동보고과목의 `활동보고서 제출`이 `_REPORT`(보고서)에 먼저 걸려 `evidence_report`가 되던
실측 대응. `활동 보고서?` 패턴(`_FORM_V2`)을 `activity_form`에 추가한다.

#### 두 겹의 회귀 방지

1. **`실험`·`실습`·`lab` 문맥이면 `evidence_report`로 남긴다**(`_FORM_EXCLUDE`) —
   `실험 활동 보고서`류가 양식 기록으로 넘어가지 않게. 실측으로 확인한 케이스다.
2. **제외 조건은 v0.2 확장분에만 건다.** 기존 `_FORM`과의 결합 전체에 걸면
   v0.1이 잡던 `실습일지`가 v0.2에서 `evidence_report`로 **역행한다**(적대적 회귀 실측).

즉 판정식은 `_FORM.search(joined) or (v2 and _FORM_V2.search(joined) and not _FORM_EXCLUDE.search(joined))`
이며, `_FORM`이 잡는 경로는 v0.1 동작 그대로다.

(근거: `until/context/assignment_router.py:97-100, 241-248` +
`tests/test_assignment_router.py:279-282` + CHANGELOG 485행, 492-493행)

---

### §4.6 분량 — 상한 요건과 단문 골격

#### (a) `LengthTarget.mode`

기존 분량 감지는 **하한 판정만** 상정했다. `200자 이내`처럼 **상한 전용** 요건은
초과를 잡을 방법이 없었다.

`LengthTarget`에 `mode` 필드를 둔다 — `"min"` | `"max"` | `"range"`:

- `min` — 하한만(현행 다수, 미달 방지가 목적). **기본값 = 현행 유지**
- `max` — 상한만(`200자 이내/이하`, 초과 차단이 목적)
- `range` — 하한·상한 둘 다(`500~800자`, `300자 내외`)

**값 산출 자체는 v0.1에서도 무해하다** — 기존 `describe()`·`check_length()`가 이 필드를
읽지 않아 v0.1 출력이 불변이다. **이 값을 소비하는 신규 판정 분기는 반드시
`algo_version()=="v0.2"` 게이트 안에만 둔다.**

소비 지점은 `BoundaryValidator`다. 상한 전용 요건의 '초과'는 **줄이는 방향의 reask 사유**이며,
미달 대응(더 쓸 것)과 지시가 정반대라 일반 문구 대신 감축 지침을 명시한다:

> 상한 요건이다: 핵심 문장은 남기고 군더더기·중복·상투 표현부터 줄여 다시 작성하라
> (억지 압축으로 사실·인용을 왜곡하지 말 것).

기존 미달 판정 경로와 v0.1 메시지는 바이트 단위로 동일하게 유지한다.

`mode`는 세션 직렬화에도 반드시 실어야 한다. 빠뜨리면 상한 전용 판정이 **복원한
세션에서만** 조용히 `"min"`으로 되돌아가 — 같은 과제인데 새 세션과 복원 세션의 분량
판정이 달라지는, 재현하기 어려운 버그가 된다.

#### (b) 단문 소감문 3슬롯

세미나과목처럼 **200자 상한·당일 마감** 과제에 현행 5슬롯 골격을 넣으면 슬롯당
40자로 전부 공허해진다. 3슬롯으로 줄이고 배분을 힌트에 명시한다.

| 슬롯 | 배분 | 근거 종류 |
|---|---|---|
| 이 강의가 실제로 다룬 것 | ~70자 — 사실만 | lecture_material |
| 그중 핵심 개념 하나 | ~60자 — 하나만 고른다 | lecture_material |
| 내 관점·적용 | ~70자 — 자료로 채울 수 없음 | user_experience |

3번은 자료로 채울 수 없다 — **200자라고 관점을 지어내면 안 되고**, 근거 없으면 빈칸형
`DECISION` 하나만 남긴다(현행 정책 그대로).

**발동 조건:** `task_type == "reflective_report"` **그리고** `algo_version() == "v0.2"`
**그리고** `length_target.max <= 400`. `length_target` 기본값(`None`)이면 현행 동작 그대로다.

발동 조건만 골격 모듈에 두고, 배선(호출부에서 `length_target`을 넘기는 일)은
오케스트레이터 몫이다.

(근거: `until/understanding/length_target.py:44-52, 176-184` +
`until/execution/boundary_guard.py:188-198` + `until/session_store.py:104-110` +
`until/understanding/skeleton.py:111-142` + `.claude/agents/until-cycle.md:26` +
CHANGELOG 485-486행)

---

## §5. 라우팅 우선순위 표

**판정 순서는 고정이다. 순서를 바꾸면 회귀가 난다.**

| # | 조건 | 결과 | 게이트 |
|---|---|---|---|
| 1 | 통계학실험 + `과제 N`/`중간고사`/`기말고사` 계열 | `rmd_notebook` | — |
| 2 | 성적·출석 표시(`M1`·`중간 총점`·`환산 점수`·`출석`) | `non_actionable` | — |
| 3 | 시험 표시·답안 슬롯(단독 `중간`/`기말`·`Final problem N`) | `non_actionable` | — |
| 4 | 응시형 퀴즈(제작·출제 제외, 제출 템플릿 첨부 제외) | `non_actionable` | — |
| 5 | 외부 시스템 응시(`UNIMe`·`유니미`) | `non_actionable` | — |
| 6 | 본인 서류·증빙(`증빙`·`수료증`·`구매 내역`·`결석계` 등) | `personal_upload` | — |
| 7 | 실물 인증 사진(`인증샷`·`인증 사진`, 본문까지 검사) | `personal_upload` | — |
| 8 | **HDL 신호 + 회차형/본문·첨부 신호** (행정 항목 제외) | `hdl_lab` | v0.2 |
| 9 | **실험 단계명(예비/랩노트/결과) — 제목에서만** | `lab_report_cycle` | v0.2 |
| 10 | **교재 참조 + 과제/문제/숙제** (`problem set` 계열 제외) | `textbook_problem_set` | v0.2 |
| 11 | 주차별 질의(`N주차 …질의`·`질문 제출`) | `weekly_inquiry` | — |
| 12 | 발표·슬라이드·스피치 | `presentation_conversion` | — |
| 13 | 팀 과제·팀 프로젝트 | `team_project` | — |
| 14 | 조별활동 보고·일지·회의록 / **활동 보고서**(실험·실습·lab 제외) | `activity_form` | 확장만 v0.2 |
| 15 | `.rmd` 첨부 | `rmd_notebook` | — |
| 16 | `.zip` 첨부 | `zip_project` | — |
| 17 | 소감문·성찰·감상문·독후감·자기평가 | `reflective_series` | — |
| 18 | 번호형 제출함(`숙제 N`·`HW4`·`실습 N 보고서`) + 본문 900자 미만 | `distributed_spec` | — |
| 19 | `problem set` 계열·`Chapter N 과제` | `problem_set` | — |
| 20 | 코드 어휘·코드 확장자 / **`프로그래밍 과제/숙제`** | `code_project` | 확장만 v0.2 |
| 21 | 레포트·보고서·실험·실습·lab | `evidence_report` | — |
| 22 | 글쓰기·서론·에세이·자기소개서 | `staged_writing` | — |
| 23 | 위 어느 것도 아님 | `spec_clarification` | — |
| 24 | **course_profiles `route_hint` 폴백** (§3) | 힌트 strategy | v0.2 |
| 25 | LLM 라우트 추정(인용 검증 통과 시에만 교체) | 추정 strategy | — |
| 26 | 추정 거절 → 능동형 묻기(후보 2개+원료+선택 질문) | `spec_clarification` | — |

### 왜 이 순서인가

- **신설 3규칙은 제외 판정 뒤에 온다.** 퀴즈·성적 항목이 실습·실험 어휘를 품고 있어서,
  앞에 두면 `실험 5 퀴즈`(응시물)가 `hdl_lab`으로 새어 **응시물에 초안을 만들게 된다.**
- **신설 3규칙은 `_INQUIRY` 앞에 온다.** 신설 규칙의 신호(과목 툴체인·실험 단계명·교재 참조)는
  `_DISTRIBUTED`(번호형)·`_REPORT`(보고서)·`_CODE`보다 강하므로 그보다 앞서야 한다.
- **`course_profiles` 폴백은 LLM 추정보다 앞이다** — 사용자가 학기 초 확정한 힌트가
  모델 추정보다 강한 신호다.
- **`_QUIZ`는 `_REPORT`보다 앞이다** — `실험 N 퀴즈`의 '실험'이 `evidence_report`에 먼저
  걸려 **퀴즈에 실험 보고서 초안**이 나오던 회귀 대응.

> ⚠ **번호 매김은 역산이다.** `until/pipeline.py:132`가 course_profiles 폴백을
> "**§5의 24번 위치**"라고 부른다. 위 표는 그 번호와 맞도록 `_PERSONAL_DOC`과
> `_PHOTO_PROOF`를 별도 행으로 나눠 24행에 폴백이 오도록 구성한 것이다
> (두 패턴은 검사 대상이 다르다 — 전자는 제목, 후자는 제목+본문+첨부명).
> **원본 표의 행 분할이 달랐다면 다른 행 번호가 어긋난다.** 25·26행은 파이프라인
> 구현 순서에서 유도한 것으로, 원본 §5 표에 실려 있었는지 확인할 수 없다.

(근거: `until/context/assignment_router.py:159-300` 실행 순서 전수 +
`until/pipeline.py:130-172` 폴백 3단 + `.claude/agents/until-router.md:14, 17` 운영 규칙 +
`CLAUDE.md:57` "신설 구간은 제외 판정(퀴즈·성적) 뒤, `_INQUIRY` 앞에 온다")

---

## §6. 목표 케이스와 회귀 케이스

두 묶음으로 나눈다. **목표 17케이스**는 v0.2에서 판정이 바뀌어야 하는 것,
**회귀 28케이스**는 세 환경(기본·v0.1·v0.2) 전부에서 바뀌면 안 되는 것이다.

`v0.1 기대값`은 이 문서 §2가 아니라 **현재 코드를 v0.1로 실행해 얻은 실측 판정**이다
(§2는 코드보다 낡았다).

### 목표 17케이스

| 과목 | 제목(요약) | v0.1 | v0.2 | stage |
|---|---|---|---|---|
| 교재문제과목 | `중간과제 1` | `spec_clarification` | `textbook_problem_set` | |
| 교재문제과목 | `중간과제 2` | `spec_clarification` | `textbook_problem_set` | |
| 교재문제과목 | `과제 3` (+ "교재 12장 연습문제") | `distributed_spec` | `textbook_problem_set` | |
| 논리설계실습 | `실습 3 보고서` (+ Verilog 본문) | `distributed_spec` | `hdl_lab` | |
| 논리설계실습 | `실습 3` (+ `lab3.v`) | `distributed_spec` | `hdl_lab` | |
| 논리설계실습 | `FPGA & Implementation` | `spec_clarification` | `hdl_lab` | |
| 논리설계실습 | `Lab 4 - Sequential Logic` | `evidence_report` | `hdl_lab` | |
| 논리설계실습 | `실습 2 레포트` (+ `lab2_starter.zip`) | `zip_project` | `hdl_lab` | |
| 논리설계실습 | `06.09 화 분반 실험 5 퀴즈` | `non_actionable` | `non_actionable` | |
| 프로그래밍과목 | `프로그래밍 과제 5` | `spec_clarification` | `code_project` | |
| 프로그래밍과목 | `Assignment #3` (+ `skeleton.zip`) | `zip_project` | `zip_project` | |
| 활동보고과목 | `활동보고서 제출` | `evidence_report` | `activity_form` | |
| 활동보고과목 | `활동 보고서` | `evidence_report` | `activity_form` | |
| 실험과목 | `예비보고서 3주차` | `evidence_report` | `lab_report_cycle` | `pre` |
| 실험과목 | `랩노트 제출` | `spec_clarification` | `lab_report_cycle` | `notebook` |
| 실험과목 | `실험 4 결과보고서` | `evidence_report` | `lab_report_cycle` | `result` |
| 세미나과목 | `3주차 소감문` | `reflective_series` | `reflective_series` | |

목록에 **판정이 바뀌지 않는 케이스 3건**(퀴즈·`Assignment #3`·소감문)이 섞여 있는 것은
의도다 — 신설 규칙이 **뺏으면 안 되는 것**을 목표 목록 안에서 함께 못 박는다.
특히 `실험 5 퀴즈`는 §5의 보호(제외 판정을 신설 구간보다 앞에 둔 것)가 실제로 작동하는지의 확인이다.

v0.1·기본(미설정)에서는 `stage`가 **항상 빈 문자열**이어야 한다.

### 회귀 28케이스

기존 코드 주석에 기록된 실코퍼스 유형 전수. 세 환경 전부에서 아래 판정을 유지해야 한다.

| 묶음 | 케이스 | 기대 |
|---|---|---|
| 성적 항목 3 | `M1` · `중간 총점` · `출석` | `non_actionable` |
| 시험 슬롯 3 | `중간고사` · `기말` · `Final problem 1` | `non_actionable` |
| 외부 시스템 1 | `UNIMe 13주차` | `non_actionable` |
| 응시형 퀴즈 2 | `06.02 화 분반 실험 6 퀴즈` · `Quiz, 연구실책임자용` | `non_actionable` |
| 본인 서류·증빙 2 | `소자 구매 내역 제출` · `프로젝트 결석 증빙자료` | `personal_upload` |
| 질의·발표·팀·양식 4 | `5주차 질의` / `피피티 제출` / `서비스디자인 팀과제 제출` / `3/17 조별활동 보고서`·`회의록 제출` | 각각 `weekly_inquiry`·`presentation_conversion`·`team_project`·`activity_form` |
| 통계학실험 Rmd 3 + Rmd 첨부 1 | `과제 1` · `중간고사 제출 연습` · `기말고사` · `과제 1`(+`.Rmd`) | `rmd_notebook` |
| zip·problem set 2 | `Project`(+`starter.zip`) → `zip_project` / `problem set_…` → `problem_set` | 표기대로 |
| 감상문·서론·번호형 3 | `농구 감상문` · `개인과제 서론 제출` · `HW4` | `reflective_series`·`staged_writing`·`distributed_spec` |
| **다른 실습 과목 보호** 1 | 기초회로 `실습 1 보고서` | `distributed_spec` (hdl_lab이 삼키면 안 됨) |
| **일반 실험 보고서 보호** 1 | 생물학실험 `실험 3 보고서` | `evidence_report` (lab_report_cycle이 뺏으면 안 됨) |
| 글쓰기 1 | `자기소개서` | `staged_writing` |

`problem set` 계열은 v0.1부터 `problem_set`이며 `textbook_problem_set`이 뺏으면 안 된다
(§4.3의 `_PROBLEM_SET` 제외 조건이 이것을 보장한다).

`non_actionable`·`personal_upload`는 `actionable=False`여야 하고, 나머지는
`actionable=True`이면서 `required_evidence`가 비어 있지 않아야 한다.

(근거: `tests/test_assignment_router.py:242-370` 전수 — 이 절은 코드로 완전히 고정돼
있어 복원도가 가장 높다. 케이스별 주석의 과목·사유도 그대로 옮겼다)

---

## §7. 측정 필드 (telemetry schema 1.1)

v0.2 라우팅이 실제로 어떻게 동작하는지 사후에 가릴 수 있도록 텔레메트리 스키마를
1.0 → 1.1로 올린다(2026-08-13). **값은 전부 고정 어휘 열거형만 허용한다** —
자유 문자열은 `assert_no_source_leak`가 차단한다.

### 추가 필드 4개

| 필드 | 값 | 무엇을 알려고 하는가 |
|---|---|---|
| `route_strategy` | 기존 strategy 전부 + 신설 3종 | 과제별 라우팅 분포 |
| `route_source` | `rule` \| `profile_hint` \| `llm_inferred` \| `clarify` | §3 폴백이 얼마나 쓰이는지 |
| `lab_stage` | `pre` \| `notebook` \| `result` \| `""` | 실험 3단계 중 어디 |
| `evidence_missing` | `EVIDENCE_KINDS` 4종의 배열 | 어떤 근거가 자주 비는지 |

`evidence_missing`의 원소는 `requirements.EVIDENCE_KINDS`의 고정 4종
(`lecture_material` · `user_experience` · `source_document` · `general_knowledge`)뿐이다.
`route.required_evidence`의 한국어 자유 문구는 **절대 싣지 않는다**(§3 금지).

### 함께 등재한 것

- **기존 strategy 중 1.0 등재 누락분** — `personal_upload` · `problem_set`.
  `course_id` 같은 원문이 아니라 코드 사전에서만 나오는 고정 어휘라 열거형 등재가 맞다.
- **신설 strategy 3종** — `hdl_lab` · `lab_report_cycle` · `textbook_problem_set`
  (`route_strategy`·`strategy` 공용).
- **`algo_version` 게이트 값** `"v0.1"`/`"v0.2"`.

### `algo_gate` — 왜 별도 축인가

`algo_version`(릴리스 SemVer)만으로는 같은 릴리스의 v0.1 실행과 v0.2 실행을 구분할 수 없다 —
게이트는 런타임 env(`UNTIL_ALGO_VERSION`)라 빌드에 남지 않는다. 이 값이 없으면 이벤트
로그만 보고 "어느 알고리즘이 이 결과를 냈나"를 사후에 가릴 수 없어 **8월 동결·측정이
성립하지 않는다.**

웹과 CLI 두 생산자가 `algo_gate()` **함수 하나만** 부른다. 각자 config를 읽어 각자
거르면 언젠가 값이 갈리고, 그때 갈라진 원장은 게이트 기준 교차 집계를 조용히 망친다.

`elapsed_ms`·`algo_version` 키 자체는 1.0부터 allowlist에 있어 이때 키 추가는 없다.

> **배선 실측(2026-08-21 현재).** allowlist 등재와 실제 방출은 다르다. 코드를 훑은 결과
> `route_source`는 `profile_hint` 한 값만 파이프라인에서 세팅되고(`until/pipeline.py:147`),
> `lab_stage`·`evidence_missing`은 **생산자가 아직 없다.** `route_strategy`는
> `until/persona/events.py:203`이 채운다. 설계상 4필드가 모두 등재된 것과 별개로,
> §7 측정 계획을 실제로 돌리려면 나머지 배선이 필요하다.

(근거: `until/telemetry/schema.py:17-23, 43-47, 77-95, 96-127` +
`until/understanding/requirements.py:23-26` + `until/pipeline.py:147` +
`until/persona/events.py:203` + CHANGELOG 487-488행 + `docs/TELEMETRY_SCHEMA.md`)

---

## §8. 동결·게이트 규율

**8월은 `algo_version`을 동결하고 측정하는 달이다.** 결정성이 깨지면 백테스트가
무의미해진다.

1. **v0.2는 전부 게이트 뒤에 둔다.** `UNTIL_ALGO_VERSION`이 **정확히 `"v0.2"`**일 때만
   켜진다. 알 수 없는 값은 전부 v0.1로 정규화해 오타가 조용히 신규 경로를 켜는 사고를 막는다.
2. **v0.1은 바이트 단위로 불변이다.** 신설 규칙·패턴 확장은 v0.2 전용 별도 패턴으로 두고
   기존 패턴을 넓히지 않는다(§4.4(a)·§4.5가 그 예). 새 필드는 값 산출까지만 v0.1에서
   허용하고, **소비하는 판정 분기는 반드시 게이트 안에** 둔다(§4.6(a)).
3. **기계로 강제한다.** `python tools/check_determinism.py`가 같은 입력 2회의 SHA-256
   일치와 v0.1 다이제스트의 기준선 불변을 검사한다. CI는
   `.github/workflows/determinism.yml`이 강제하지만 로컬에서 먼저 돌린다.
4. **v0.2 픽스처를 빼지 마라.** `examples/sample_hdl_lab.txt` ·
   `sample_lab_pre.txt` · `sample_lab_result.txt` · `sample_textbook_task.txt` 4건이
   지문 대상에서 빠지면 v0.1/v0.2 다이제스트가 동일해져 **결정성 게이트가 v0.2 회귀를
   하나도 못 잡는다**(2026-08-14 실측 확인).
5. **자동 알고리즘 업데이트(자가발전) 금지.** 변경은 제안 → 사람 승인 → 버전 태깅.
6. **규칙을 추가하면 반대 방향 회귀 테스트를 같이 넣는다.** `_FORM`에 활동보고서 패턴을
   넣었을 때 `_FORM_EXCLUDE`가 같이 들어간 이유가 그것이다.

(근거: `until/config.py:66-78` + `until/context/assignment_router.py:164-166` +
`tools/check_determinism.py:1-45` + `examples/README.md:20-30` +
`CLAUDE.md:52-58` + `.claude/agents/until-router.md:24-27`)

---

## 부록 — 이 문서를 인용하는 지점

복원의 정확성을 다시 검증할 때 쓸 목록이다. 절 번호를 바꾸면 이 22곳을 함께 고쳐야 한다.

| 인용 지점 | 절 |
|---|---|
| `until/context/course_profiles.py:3` | §3 |
| `tests/test_course_profiles.py:3` | §3, §4.2 |
| `until/context/assignment_router.py:72` | §4 |
| `until/understanding/task_type.py:65` | §4.1 |
| `until/understanding/skeleton.py:83` | §4.1 |
| `until/execution/prompts.py:294` | §4.1 |
| `until/context/series.py:85` | §4.2 |
| `until/execution/prompts.py:161` | §4.3 |
| `until/execution/prompts.py:188` | §4.4 |
| `until/understanding/length_target.py:44` | §4.6(a) |
| `.claude/agents/until-router.md:17, 30` | §5 |
| `tests/test_assignment_router.py:242` | §6 |
| `until/telemetry/schema.py:17, 43, 77` | §7 |
| `until/config.py:72` | §8(문서 전체) |
| `CHANGELOG.md:477` | 문서 전체 |
| `examples/README.md:30` | §4.1~4.3 |
| `CLAUDE.md:5, 57` | 문서 전체, §5 |
| `.claude/agents/until-guidance.md:37` | 골격 정의(§4.1·§4.6(b)) |

절 번호를 붙이지 않고 이 문서의 내용을 파생시킨 곳: `.claude/agents/until-cycle.md`
(§4.2 단계별 계약·§4.6 분량), `.claude/agents/until-measure.md`(§7·§8 측정 규율).
