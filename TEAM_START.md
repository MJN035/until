# Until 팀원용 시작 가이드 (5분이면 됩니다)

## Until이 뭐예요?
**과제를 "내가 직접 정해야 하는 부분 직전까지" 대신 써 주는 도우미**예요.

- 자료로 채울 수 있는 건 끝까지 써 줍니다.
- 대신, **나만 정할 수 있는 것**(어떤 주제로 할지, 내 생각·취향)은 멋대로 안 정하고
  `[[DECISION: ...]]` 라는 **빈칸 표시**로 남겨 둡니다.
- 내가 그 빈칸에 답을 적으면, **내 말투로 최종 완성본**까지 써 줍니다.

## 준비물
- **Python 3** 설치 (https://www.python.org → "Download")
- 끝! (AI 체험만 할 거면 인터넷/결제/가입 **필요 없음**)

## 1) 받기
- 이 폴더(`until`)를 통째로 복사받거나, git이면:
  ```
  git clone <저장소 주소>
  cd until
  ```

## 2) 웹 화면으로 써 보기 (제일 쉬움)
```
python -m until.web
```
- 위 명령을 친 뒤 브라우저에서 **http://127.0.0.1:8000** 을 엽니다.
- 홈 하단 **'직접 붙여넣기'**(간단 모드)로 들어가 과제를 붙여넣고 **[초안 만들기 →]**
  → 빈칸(결정 지점)에 내 생각을 적고 **[완성하기 →]** 를 누르면 끝.
- 수업 PDF·워드·한글(HWPX) 파일이 있으면 **내 자료 첨부**로 올려 보세요 — 초안이
  그 자료를 `[자료N]`으로 인용합니다. 완성본은 **제출용 .docx**로 바로 저장돼요.
- (이때는 가짜 AI(mock)라 결과가 견본 수준이에요. 진짜 AI는 아래 4)번 참고.)

## 3) 잘 돌아가는지 확인 (선택)
```
python run_tests.py
```
- 맨 아래에 `pass=25 fail=0` 이 보이면 정상입니다.

## 4) 진짜 AI로 쓰고 싶다면 (무료 Groq)
1. https://console.groq.com/keys 에서 무료 키(`gsk_...`)를 발급받습니다.
2. (Windows PowerShell) 아래를 입력한 뒤 웹을 다시 켭니다:
   ```powershell
   $env:UNTIL_BASE_URL="https://api.groq.com/openai/v1"
   $env:UNTIL_API_KEY="여기에_본인_키"
   $env:UNTIL_MODEL="llama-3.3-70b-versatile"
   python -m until.web --backend local
   ```
3. 키는 **남에게 공유하거나 코드/깃에 올리지 마세요.**

## 자주 막히는 곳
- **글자가 깨져 보여요(한글):** 명령 앞에서 한 번
  `$env:PYTHONIOENCODING="utf-8"` 를 실행하세요.
- **`python`이 없대요:** Python 설치 후 컴퓨터(또는 터미널)를 다시 켜세요.
- **포트 8000이 이미 쓰여요:** `python -m until.web --port 8001` 처럼 번호를 바꾸세요.

## 더 알고 싶으면
- 사용법 전체: `README.md`
- 설계/구조: `docs/` 폴더, 개발 이어가기: `CLAUDE.md` / `AGENTS.md`

## 환경 고정 (3인 공통 — 편차가 곧 백테스트 오염)

결정성 SHA-256이 파이썬·라이브러리 버전에 흔들린다. 셋이 같은 환경에서 돌려야 코퍼스 결과를 합칠 수 있다.

```bash
# 최초 1회
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync                      # requirements.txt 대신 uv.lock 기준으로 재현

# 커밋 훅(eTL 토큰 사고 방지) — 최초 1회
pip install pre-commit && pre-commit install

# 알고리즘 출력이 안 바뀌었는지 확인
python tools/check_determinism.py
```

⚠ `.env`(eTL 토큰)는 **절대 공유하지 않는다.** 각자 자기 토큰으로 자기 노트북에서 돌리고,
공유하는 건 비식별 `telemetry.jsonl`뿐이다.
