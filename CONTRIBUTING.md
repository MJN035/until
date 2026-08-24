# Contributing to Until

고맙습니다! Until은 "대학생의 과제를 **사람의 판단이 필요한 경계선 직전**까지 대신 끝내는"
AI 에이전트입니다. 기여 전에 아래만 지켜 주세요.

## 빠른 시작

```bash
git clone https://github.com/MJN035/until
cd until
python run_tests.py          # 13개 오프라인 스위트(키·인터넷 불필요, 전부 mock)
```

라이브로 돌리려면 `.env.example`를 복사해 키를 채우세요(절대 커밋 금지).

```bash
cp .env.example .env         # 값 채운 뒤 셸에 export
pip install -e ".[live]"     # 또는 .[pdf,browser,optimize,retrieval]
python -m until.web --backend local        # 웹 UI
python -m until.web --backend local --sso  # 토큰 없이 브라우저 SSO
```

## 절대 불변 규칙 (PR 전 자가 점검)

이 규칙들은 코드(특히 `until/execution/boundary_guard.py`)로 강제됩니다. 깨는 PR은 머지되지 않습니다.

1. **경계선:** Execution은 사람의 판단(관점·취향·이해관계·윤리)을 직접 확정하지 않는다.
   그 자리는 `[[DECISION: ...]]` 마커로 남긴다. (AI 제안은 "제안"일 뿐, 확정은 사람의 클릭)
2. **mock 우선:** `--backend mock` + 모든 테스트는 **키·인터넷 없이 항상 통과**해야 한다.
3. **결정적 레이어:** `capture/`, `context/`, `boundary/`, `prompts/suggest.py`는 **LLM 호출 0**.
   LLM이 필요한 로직은 `execution/`(또는 `llm/`)에 둔다.
4. LLM 호출은 `llm/base.py`의 `LLMClient.complete()` 하나로만.
5. 소스 접속 방식은 `BrowserAdapter`/어댑터 뒤에. 파이프라인 코어는 접속 방식을 모른다.

## 작업 방식

- **작은 변경 → 테스트 통과 → 커밋.** 큰 리팩터링은 먼저 이슈로 논의.
- 새 기능엔 **오프라인 테스트**를 함께(mock으로 결정적). `tests/`에 추가하고 `run_tests.py`에 등록.
- 비밀(키·토큰)은 **절대 커밋/파일 저장 금지**. env로만. 대화·로그에 노출됐다면 폐기·재발급.
- Windows 콘솔 인코딩: 테스트/러너는 `PYTHONIOENCODING=utf-8`로 실행(em-dash 출력 에러 회피).
- 출력은 **현대 한국어만**(한자·가나·악센트 라틴·외국어 단어 금지) — 가드가 강제.

## 구조 한눈에

```
eTL/파일 → Capture(파싱·토큰0) → Understanding(LLM) → Context(수업자료·내파일·말투)
        → Execution(경계선 초안, Guard로 강제) → Boundary(결정지점 + 프롬프트 제안)
```

자세한 설계는 `AGENTS.md`·`docs/ARCHITECTURE.md`, 다른 wrapper와의 비교는 `docs/WRAPPER_STUDY.md`.
기능→코드→테스트 지도는 `docs/FEATURES.md`, 최근 진행은 `CHANGELOG.md`.

## 품질 게이트 관행 (v0.2.0~ 자율 루프에서 확립)

릴리스 전 **다중 에이전트 코드 리뷰**(재현 확인된 버그만 보고)와 **광역 스모크**(실사용풍
입력 수십 케이스로 파서 오탐/미탐 검사)를 돌린다. 발견된 버그는 반드시 회귀 테스트와
함께 수정한다. 러너는 병렬(`python run_tests.py`, ~5초)이며, 순차 디버깅은 `-j 1`.

**린트:** `ruff check .` (설정 `ruff.toml` — 실버그 규칙만: pyflakes·bugbear·문법.
스타일 규칙은 의도적으로 껐다). 커밋 전 한 번 돌려 깨끗해야 한다. `pip install ruff`.

## 라이선스와 기여자 동의서(CLA)

이 프로젝트는 **AGPL-3.0-only**로 배포됩니다(`LICENSE`).
재구현해 차용한 외부 패턴은 `NOTICE`에 출처를 남깁니다 — 새 차용이 있으면 거기에 추가해 주세요.

**첫 PR에는 CLA 서명이 필요합니다.** Until의 코어는 AGPL로 공개되지만 서버·결제·관리자
계층은 비공개로 남습니다. 이 구조가 성립하려면 프로젝트 소유자가 기여물을 AGPL이 아닌
조건으로도 사용할 수 있어야 하고(듀얼 라이선스·비공개 서버 반영), 그 권한은 기여자만
줄 수 있습니다. 기여가 쌓인 뒤 소급 동의를 받는 것은 현실적으로 불가능하므로 첫 기여부터
받습니다.

절차 — [`CLA.md`](CLA.md)를 읽고 PR 본문에 아래를 포함하세요. **1회만 서명하면 이후
기여에 계속 적용됩니다.**

```
I have read the CLA Document (CLA.md) and I hereby sign the CLA.
Name: <실명 또는 사용하는 이름>
GitHub: @<사용자명>
Date: <YYYY-MM-DD>
```

CLA 3조 4항을 특히 확인해 주세요 — 이 프로젝트는 LMS 데이터를 다루므로 **개인정보·
자격증명·제3자의 학업 데이터(성적·제출물·교수 코멘트·실명)가 기여물에 섞이면 안 됩니다.**
