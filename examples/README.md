# examples/ — 데모·테스트용 샘플

전부 오프라인(mock)으로 동작. 실행: `python -m until.cli examples/<파일> --backend mock`

## 과제 샘플 (유형 감지 시연 — 5유형)

| 파일 | 감지 유형 | 함께 시연되는 것 |
|---|---|---|
| `sample_assignment.txt` | 에세이/논술 | 1500 words 분량 요건, 읽기목록 인용, 결정 3곳(관점) |
| `sample_problemset.txt` | 문제 풀이 | 결정 0개 허용(min_decisions=0), 마감 D-day(2026-07-10) |
| `sample_code.txt` | 코드/구현 | 정형 유형 인용 경고 완화, 마감(2026-07-12) |
| `sample_report.txt` | 보고서/실험 | 페이지 분량(4페이지 내외≈근사 판정), 마감(2026-07-15) |
| `sample_presentation.txt` | 발표 자료 | 슬라이드 구조 지침, 마감(2026-07-18) |
| `sample_extension.txt` | 에세이/논술 | **연장 공지 이해**(7/10→7/17 연장, 시각 23:59, '연장됨' 라벨), 분량 요건 |

## v0.2 신설 경로 픽스처 (2026-2학기 6과목)

`UNTIL_ALGO_VERSION=v0.2`에서만 신설 경로로 떨어진다. v0.1에서는 기존 경로로 가는 것이 정상이며,
그 차이가 곧 v0.2가 실제로 무엇을 바꿨는지의 증거다(2026-08-14 실측).

| 파일 | v0.1 (현행) | v0.2 (신설) |
|---|---|---|
| `sample_hdl_lab.txt` | `code_project` | `hdl_lab` |
| `sample_lab_pre.txt` | `evidence_report` | `lab_report_cycle` (stage=pre) |
| `sample_lab_result.txt` | `evidence_report` | `lab_report_cycle` (stage=result) |
| `sample_textbook_task.txt` | `spec_clarification` | `textbook_problem_set` |

이 4건은 `tools/check_determinism.py`의 지문 대상이다. **빼지 마라** — 빼면 v0.1/v0.2 다이제스트가
동일해져서 결정성 게이트가 v0.2 회귀를 하나도 못 잡는다.
설계 근거는 `docs/COURSE_ALGORITHMS_2026F.md` §4.1~4.3.

유형 분류는 결정적(`until/understanding/task_type.py`) — LLM 없이 키워드로.
end-to-end 검증: `tests/test_task_type.py::test_example_files_detected_and_run`.

## 맥락 주입 폴더 (Personalization 시연)

- `course_materials/` — 수업자료 검색 대상 (`--course-materials`)
- `my_files/` — 내 파일 검색 대상 (`--my-files`)
- `voice_samples/` — 말투 프로파일링 대상 (`--voice`)

```bash
python -m until.cli examples/sample_assignment.txt --backend mock \
  --course-materials examples/course_materials \
  --my-files examples/my_files --voice examples/voice_samples
```

## 소스 커넥터 fixture (로그인 없는 가짜 eTL)

- `etl_fixture/` — `--source etl-demo`가 읽는 오프라인 eTL 과제 페이지
- `canvas_fixture/` — Canvas(LearningX) HTML 파서 테스트용 실제 구조 샘플
