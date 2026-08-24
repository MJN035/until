# Until 라이브 품질 평가 플레이북

이 절차의 1차 판정자는 LLM이 아니라 사람입니다. 목표 수치는 과제 유형별로 사람이
`예`라고 판정한 생성물의 비율, 즉 **제출 가능 비율 = 예 / 전체 채점 수**입니다.

## 1. 라이브 백엔드 준비

PowerShell에서 사용할 제공자의 키와 모델을 설정합니다. 키는 파일에 기록하거나 커밋하지
않습니다. 아래 값은 예시이며 실제 제공자 설정은 `README.md`의 백엔드 안내를 따릅니다.

```powershell
$env:UNTIL_BACKEND="local"
$env:UNTIL_BASE_URL="https://api.groq.com/openai/v1"
$env:UNTIL_API_KEY="<본인 키>"
$env:UNTIL_MODEL="<사용할 모델>"
```

## 2. 생성 및 채점 시트 만들기

전체 골든셋의 legacy, unit, raw 세 경로를 실행하고 자기완결 HTML을 만듭니다.

```powershell
python run_evals.py --grade-out _until_work/eval-grading
```

특정 유형만 먼저 확인하려면 키를 덧붙입니다.

```powershell
python run_evals.py evidence_report reflective_report problemset hdl_lab --grade-out _until_work/eval-grading
```

`_until_work/eval-grading/grading.html`을 브라우저로 열어 각 결과를 읽고 `예`, `부분`,
`아니오` 중 하나를 선택합니다. `예`는 그대로 제출해도 되는 수준일 때만 선택합니다.
사소한 편집이 필요하면 `부분`, 중요한 누락·날조·경계선 위반이 있으면 `아니오`입니다.

특히 성찰문의 경험 창작, HDL 실험의 파형·합성 수치 날조는 문장이 자연스러워도
`아니오`입니다. 조사 보고서는 제공 자료의 인용·근거 커버리지를, 문제 세트는 문항별 답변
분리를 확인합니다.

채점 HTML과 내보낸 JSON에는 과제 지문·생성 본문·채점 메모가 그대로 들어갑니다. 현재
골든셋처럼 합성·비식별 자료만 사용하고, 실제 학생 이름·학번·연락처·비공개 과제는 넣지
마세요. 파일은 git에서 제외된 로컬 `_until_work/` 아래에 보관하고 외부에 공유하지 않습니다.
실행 실패 카드는 원인과 함께 표시되며 제출 가능 비율의 분모에서 제외됩니다.

## 3. JSON 내보내기와 비율 산출

HTML 아래의 **채점 결과 JSON 내보내기**를 눌러 `until-grades.json`을 저장한 뒤 집계합니다.

```powershell
python run_evals.py --grade-in "$HOME/Downloads/until-grades.json"
```

출력 표의 `제출 가능 비율`이 유형별 핵심 지표입니다. `부분`은 별도 건수로 보존하며 제출
가능 분자에는 넣지 않습니다. 비교할 때는 같은 골든셋, 같은 모델, 같은 채점 기준을 유지하고
실행 날짜·모델명을 결과 파일과 함께 기록합니다.

## 4. 오프라인 하네스 점검

API 키 없이 구조만 검증하려면 mock 백엔드를 사용합니다. mock 결과의 품질 수치는 제품
성능으로 해석하지 않습니다.

```powershell
$env:UNTIL_BACKEND="mock"
python tests/test_evals_grading.py
python run_evals.py hdl_lab --grade-out _until_work/eval-grading-mock
```
