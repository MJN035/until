# 대학 과제 특화 참고자료 자동 주입 — 조사·설계 초안 (P2-5)

> 상태: **조사/설계 문서만** (코드 구현 X — 팀 결정 대기).
> 작성: 2026-07-26, 실사용 피드백 5번("대학 과제 수행 특화가 핵심 차별화") 대응.
> 전제: 유저가 돈을 내게 하려면 일반 GPT/Claude에 시키는 것보다 결과가 좋아야
> 하고, 그 강점은 "이 수업·이 교수·이 학교"의 맥락 데이터에서 나온다.

---

## 1부 — 데이터 소스별 수집 가능성·API·약관/합법성

### 1.1 LMS 강의자료·기출 (eTL/강의실+ 계열)

| 항목 | 내용 |
|---|---|
| 실체 | 서울대 eTL = **Canvas LMS**(myetl.snu.ac.kr). '강의실+'(LearningX)는 Xinics가 Canvas를 확장한 제품으로 동아대·단국대·수원대 등 다수 대학이 사용 — **같은 Canvas REST API**가 깔려 있다 |
| API | Canvas REST API 공식 문서 존재([Instructure 문서](https://www.canvas.instructure.com/doc/api/)). 학생 본인 계정의 액세스 토큰으로 `courses / files / modules / assignments / announcements` 조회 가능 — **이미 우리 코드가 하는 일** (`capture/sources/canvas_api.py`, `context/etl_materials.py`) |
| 수집 범위 | 강의자료(파일·모듈), 공지, 과제 명세·첨부. '기출'은 교수가 LMS에 올린 경우에만 존재 — 별도 크롤링 대상이 아니라 **이미 있는 코스 파일 수집의 부분집합** |
| 약관 리스크 | **낮음~중간.** 학생이 자기 계정·자기 토큰으로 자기 수강 과목을 읽는 read-only 접근이라 구조적으로 정당. 단, 일부 대학은 학생의 API 토큰 발급을 정책으로 제한([예: TAMU](https://lms.tamu.edu/support/canvas-api), [UW](https://uwconnect.uw.edu/it?id=kb_article_view&sysparm_article=KB0034590)) — 학교별 정책 확인 필요. 서울대 eTL은 설정 화면에서 학생 토큰 발급이 열려 있음(라이브 검증 완료) |
| 저작권 리스크 | 강의자료·기출은 **교수 저작물**. 학생 본인의 과제 수행을 위한 사적 참조는 통상 허용 범위이나, ① 우리 서버에 **영구 저장·재배포**하거나 ② **모델 파인튜닝 학습 데이터**로 쓰는 순간 별개의 복제·이용 행위가 된다 → 파인튜닝 재료로는 부적합. "요청 시 조회 → 프롬프트 컨텍스트로만 사용 → 세션 후 파기"가 안전선 |
| 결론 | **1순위 소스.** 신규 개발 거의 불필요 — 기존 `etl_materials`(키워드 순위화)를 임베딩 검색·자료 유형 태깅(기출/강의노트/공지)으로 강화하는 방향 |

### 1.2 에브리타임(에타) 강의평

| 항목 | 내용 |
|---|---|
| API | **공개 API 없음.** 서드파티 연동도 전부 비공식 크롤링/리버스 엔지니어링([사례](https://velog.io/@hsh111366/OneTime-%EC%97%90%EB%B8%8C%EB%A6%AC%ED%83%80%EC%9E%84-%EC%8B%9C%EA%B0%84%ED%91%9C%EB%A5%BC-%EC%9B%90%ED%83%80%EC%9E%84%EC%9C%BC%EB%A1%9C-%EA%B0%80%EC%A0%B8%EC%99%80%EB%B3%B4%EC%9E%90)) |
| 약관 리스크 | **높음.** 이용약관이 자동수집을 금지하고, 크롤링 계정 차단 사례가 알려져 있다([나무위키 정리](https://namu.wiki/w/%EC%97%90%EB%B8%8C%EB%A6%AC%ED%83%80%EC%9E%84)). 약관·경고문구가 명확한 사이트의 무단 크롤링은 위법성 인정 가능성을 높인다는 것이 실무 평가([법률신문 기고](https://www.lawtimes.co.kr/news/articleView.html?idxno=202909), [대법원 판결 해설](https://www.shinkim.com/kor/media/newsletter/1843) — 여기어때/사람인 계열 판례: 형사는 무죄 사례도 있으나 민사 손배·부정경쟁행위 인정) |
| 추가 리스크 | 강의평은 **작성 학생의 저작물 + 교수 실명이 결부된 평판 정보**. 수집·재표시 시 저작권과 명예훼손·개인정보 문제가 이중으로 걸린다. 유료 제품(과금 중)에 넣으면 "영리 목적 무단 이용"이 돼 리스크가 더 커진다 |
| 대안 | ① **사용자 반입(BYO) 방식**: 학생이 본인 계정으로 본 강의평을 복사해 '내 자료'로 붙여넣게 유도(이미 있는 내 자료 첨부 경로 재사용, 우리가 수집 주체가 아님) ② 학교 공식 강의평가 공개 데이터가 있는 경우 그것만 사용 ③ 제휴/라이선스 협의(장기) |
| 결론 | **자동 수집(크롤링)은 하지 않는다.** 에타 데이터는 BYO 붙여넣기 UX로만 지원 — "에타에서 이 교수 강의평을 복사해 붙여넣으면 과제 톤을 맞춰 드려요" 같은 안내가 약관·법 리스크 없이 같은 효용의 80%를 얻는 경로 |

### 1.3 그 외 후보 (요약)

- **수강편람/강의계획서(공식 포털)**: 학교 공개 페이지 — 리스크 낮음, 과제 맥락(평가 기준·주교재) 파악에 유용. 학교별 파서 필요.
- **도서관 DB·RISS/DBpia**: 로그인·라이선스 제약. 인용 근거로 초록 수준만. 중기 과제.
- **교수 개인 홈페이지/OCW**: 공개 자료라 리스크 낮음, 커버리지 낮음.

### 1.4 파인튜닝에 대한 판단

"대학과제 특화 파인튜닝"의 재료로 LMS 자료·에타 강의평을 쓰는 것은 **양쪽 다
부적합**(교수 저작물 학습 이용·약관 위반 데이터 학습). 합법적 재료는
① 사용자가 명시 동의한 자기 산출물(P7 피드백 로그 — 이미 GEPA 학습셋 배관 존재),
② 공개 라이선스 학술 자료. 단기적으로는 파인튜닝보다 **컨텍스트 주입(RAG)**이
비용·리스크·효과 모두 우위 — GEPA(프롬프트 최적화)가 이미 그 역할의 절반을 한다.

---

## 2부 — 참고자료 프롬프트 주입 아키텍처 초안

### 2.1 현재 구조 (이미 있는 것)

```
pipeline.run(paths, extra_context_sources=[SourceDoc...])
  ├─ context/bundle.py      : 수업자료·내 파일·말투 → SourceDoc 목록
  ├─ context/etl_materials.py: 코스 파일+모듈 → 과제 키워드 순위화 → SourceDoc
  └─ execution/drafter.py   : SourceDoc에 1-기반 번호 → [자료N] 인용 강제
```
주입 계약은 이미 `SourceDoc(title, text)` 하나로 통일돼 있다. **새 소스는
"SourceDoc 생산자"만 만들면 파이프라인 수정 없이 꽂힌다.**

### 2.2 제안 구조 — SourceProvider 레지스트리

```
until/context/providers/          (신설 — 각 파일이 소스 1개)
  base.py       : SourceProvider 프로토콜
                  fetch(spec, course_ref, budget) -> list[SourceDoc]
                  · 결정적/캐시 우선, LLM 호출 0 (기존 불변 규칙 3 유지)
  lms_files.py  : (기존 etl_materials 이관) 코스 파일·모듈·공지
  lms_past.py   : 같은 과목의 지난 학기 과제·기출(있으면) — P1-6의
                  include_past 코스 조회 재사용
  syllabus.py   : 강의계획서(공식 포털 파서, 학교별 어댑터)
  byo.py        : 사용자 붙여넣기(에타 강의평 등) — 웹 '내 자료' 경로 재사용,
                  title 접두사 "[강의평]"으로 태깅
```

파이프라인 쪽 변경은 한 줄 개념:
`extra_context_sources = 순위화( ∑ provider.fetch(...) , token_budget )`

### 2.3 순위화·토큰 예산 (핵심 설계 결정)

1. **2단 순위화**: ① 소스 유형 가중치(과제 첨부 > 기출 > 강의노트 > 공지 >
   강의평) ② 유형 내 관련도(현행 키워드 부분문자열 → 임베딩 검색 옵션
   `context/retrieval.py` 재사용).
2. **토큰 예산**: 기존 `_trimmed_source_docs` 패턴 일반화 — 소스별 상한
   (기출 2000자 > 강의평 800자 발췌)을 두고 총합이 모델 TPM 예산의 일정
   비율을 넘지 않게 절단. Groq 무료(TPD 100k) 제약이 이미 코드에 반영돼
   있으므로 같은 티어링(`UNTIL_MODEL_LIGHT`)을 따른다.
3. **인용 유지**: 모든 주입 자료는 기존 [자료N] 번호 체계로 — 강의평이
   근거로 쓰이면 초안에 `[자료N]`이 남아 사용자가 출처를 안다(가짜 인용
   금지 규칙 그대로).

### 2.4 캐시·프라이버시 원칙

- 조회 결과는 **사용자별·세션 스코프 캐시**(현행 `_INBOX_CACHE`/세션 pickle
  패턴)만 — 서버 공용 저장소에 타 사용자 강의자료를 쌓지 않는다.
- 강의평 등 BYO 텍스트는 `_until_work/`(gitignore) 밖으로 나가지 않고,
  클라우드 모드에선 사용자별 KV 미러만(기존 prof:/hist: 키 패턴).
- 파인튜닝/GEPA 학습셋에는 **사용자 동의 플래그가 있는 자기 데이터만**
  (P7 feedback 로그 확장 — `consent: true` 필드 신설 제안).

### 2.5 단계별 도입 제안 (팀 논의용)

| 단계 | 내용 | 예상 효과 | 리스크 |
|---|---|---|---|
| A (즉시) | lms_past — 지난 학기 같은 과목 과제·자료 주입 | "이 수업 스타일"을 아는 초안 | 낮음(기존 API) |
| B (단기) | byo — 강의평/친구 필기 붙여넣기 태깅 주입 | 교수 취향 반영 | 낮음(사용자 반입) |
| C (중기) | syllabus — 강의계획서 파서(학교별) | 평가 기준 정렬 | 중간(파서 유지비) |
| D (보류) | 에타 자동 수집 | — | **높음 — 하지 않음** |

**Sources:**
- [Canvas LMS REST API Documentation](https://www.canvas.instructure.com/doc/api/)
- [TAMU Canvas API Access Token Policy](https://lms.tamu.edu/support/canvas-api)
- [UW Canvas API Access and Access Tokens](https://uwconnect.uw.edu/it?id=kb_article_view&sysparm_article=KB0034590)
- [법률신문 — 무단 크롤링의 법적 함정](https://www.lawtimes.co.kr/news/articleView.html?idxno=202909)
- [세종/신김 — 크롤링 관련 최근 대법원 판결과 시사점](https://www.shinkim.com/kor/media/newsletter/1843)
- [나무위키 — 에브리타임](https://namu.wiki/w/%EC%97%90%EB%B8%8C%EB%A6%AC%ED%83%80%EC%9E%84)
