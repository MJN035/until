# eTL 제출물 기반 문체 자동 학습 (voice autolearn) — 설계

날짜: 2026-07-28 · 상태: 승인됨(구현 진행)

## 목적

'내가 쓴 글 올리기' 없이, eTL을 연결하는 순간부터 초안이 내 문체로 나오게 한다.
사용자가 이미 제출한 과제 파일 = 과제 문체를 배우기에 최적의 샘플이며,
토큰/세션으로 접근 가능하므로 업로드 0·클릭 0으로 수집할 수 있다.

## 결정 사항 (브레인스토밍 합의)

- **출처**: eTL 과거 제출물(Canvas API). 컴퓨터 폴더 스캔은 하지 않음.
- **저장 범위**: VoiceProfile(종결어미·문장길이·표현 등 추출값)만 저장. 원문은 분석 직후 폐기.
- **UX**: 전자동(별도 동의·선택 UI 없음). 첫 eTL 인박스 성공 시 자동 학습.
  단, 사후 투명성 유지 — 적용 사실 표시 + 끄기/다시 학습 통제 제공.

## 동작

1. `/inbox` 신선 조회 성공 직후(캐시 히트 아님), 저장된 문체 파일이 없으면 자동 학습:
   - 어댑터가 `list_my_submissions`를 지원할 때만(=CanvasApiAdapter. SSO/WS는 조용히 스킵).
   - 과목 목록(지난 학기 포함)을 돌며 내 제출물 조회 → 후보 필터 → 최신순 최대 10건.
   - 첨부는 임시 폴더에 받아 ingest로 텍스트 변환, 온라인 텍스트 제출은 HTML→텍스트.
   - `build_voice_profile`(결정적, LLM 0) → uid별 `voice_profile.json` 저장 → 원문 삭제.
   - 실패(네트워크·0건 아님)는 저장하지 않고 스킵 → 다음 인박스에서 재시도.
     표본 0건이면 n_docs=0으로 저장(매번 재스캔 방지). 인박스 흐름은 어떤 경우에도 무손상.
2. 결정적 안전 필터(전자동 보완): 조별 과제(`assignment.group_category_id`) 제외,
   미제출 제외, 텍스트 포맷(.docx/.hwpx/.txt/.md/.pdf/온라인 텍스트)만, 건당 파싱 상한.
3. 적용: 업로드 voice_dir > 저장 프로파일 > (기존) 답 히스토리 힌트.
   `assemble_context(voice_profile=)` / `pipeline.run(voice_profile=)`로 주입 —
   붙여넣기(/draft)·eTL 수집(/pick·/collect) 전 경로.
4. 통제: 초안 페이지에 "✍ 내 문체 적용됨(제출물 N건) · 다시 학습 · 끄기" 한 줄.
   끄기 = disabled 플래그 저장(재수집 안 함), 다시 학습 = 파일 삭제(다음 인박스에서 재학습).
5. 클라우드 지속화: `vprof:{uid}` KV 미러(하이드레이션·미러 목록에 추가).

## 저장 형식

`_until_work/voice_profile.json`(로컬) / `_until_work/users/<uid>/voice_profile.json`(클라우드):

```json
{"v": 1, "disabled": false, "n_docs": 7, "learned_at": "2026-07-28T…",
 "profile": {"ending_style": "합니다체", "avg_sentence_len": 41, …}}
```

## 테스트 (mock·오프라인)

- 제출물 파서: 조별 제외·미제출 제외·온라인 텍스트 추출·비-dict 방어.
- 가짜 어댑터로 수집→프로파일 생성(다운로드 픽스처 포함).
- 저장/로드/disabled/손상 파일 방어.
- 우선순위: voice_dir가 voice_profile보다 우선.
- 자동 학습 게이트: 없음→학습 / 있음→스킵 / disabled→스킵 / 실패→무손상.
- `run_tests.py`에 `test_voice_autolearn` 등록.
