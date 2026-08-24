## Local Agent 설치·설정

Until은 사용자가 이미 구독하고 공식 CLI에서 직접 로그인한 로컬 AI 에이전트에 작업을 맡긴다. Until은 과제 문맥 준비, 실행 계획 승인, 격리, 결과 검증, 제출 패키징만 담당하며 모델 API를 직접 호출하지 않는다. 따라서 이 경로에서는 Until 서버에 모델 API 키를 맡길 필요가 없고 Until 서버가 부담하는 모델 API 비용도 0이다. 단, 사용 중인 CLI 구독의 이용 한도와 약관은 그대로 적용된다.

## 하지 않는 것

- Until 서버가 OpenAI·Anthropic 모델 API 호출
- Hosted 크레딧, BYOK, 모델별 유료 요금제
- Claude/ChatGPT 웹 세션 쿠키 추출 또는 구독을 비공식 API처럼 사용
- 사용자 OAuth·구독 토큰 저장 또는 프록시
- OMC를 학생 제품의 필수 런타임으로 사용
- Chromium 포크
- AI가 스스로 완료 판정 또는 실제 제출
- 학습 증명서·작성 감시

OMC는 Until을 개발하는 팀의 도구다. 제품 안에서는 OMC 없이 동작하는 작은 Local Agent 계약만 사용한다. Until은 로그인 화면을 흉내 내거나 세션 쿠키·OAuth 토큰을 읽지 않으며, 공식적으로 지원되지 않는 방식으로 구독 사용을 자동화하지 않는다.

## 1단계: CLI 설정

먼저 사용할 AI CLI를 해당 벤더의 공식 안내에 따라 설치하고, 그 CLI에서 사용자가 직접 로그인한다. Until은 벤더별 명령이나 플래그를 추측하지 않는다. 공식 문서에서 현재 설치 버전의 비대화형 실행 방법을 확인한 뒤 `CliSpec`으로 적는다.

권장 방식은 JSON 파일이다.

```bash
export UNTIL_AGENT_SPEC="$PWD/examples/agent-spec.example.json"
```

`UNTIL_AGENT_SPEC`이 설정되면 아래 간이 환경변수보다 우선한다. 예시는 [`examples/agent-spec.example.json`](../examples/agent-spec.example.json)에 있다.

### `CliSpec` 필드

| 이름 | 생략 시 기본값 | 의미 | 예시 |
|---|---|---|---|
| `name` | `command`, 둘 다 비면 `local-agent` | 화면과 receipt에 표시할 에이전트 이름 | `local-agent` |
| `command` | 없음, 필수 | 샌드박스 안에서 찾을 공식 CLI 실행 파일 | `<공식 CLI 실행 파일>` |
| `version_args` | `["--version"]` | 설치·버전 확인 때 `command` 뒤에 붙이는 인자 | `["<버전 확인 플래그>"]` |
| `status_args` | `[]` | 공식 로그인 상태 확인 인자. 비우면 로그인 여부를 실제 실행 때 판정한다 | `["<로그인 상태 확인 플래그>"]` |
| `run_args` | `[]` | 비대화형 작업 실행 인자. `prompt_via=arg`이면 `{prompt}`가 반드시 들어가야 한다 | `["<비대화형 실행 플래그>", "{prompt}"]` |
| `prompt_via` | `arg` | 프롬프트 전달 방식. 허용값은 `arg`, `stdin`뿐이다 | `arg` |
| `login_markers` | 코드의 기본 로그인 오류 문구 목록 | stdout·stderr에서 미로그인을 판별할 문자열 목록. 대소문자는 구분하지 않는다 | `["login required"]` |
| `limit_markers` | 코드의 기본 사용량 한도 문구 목록 | stdout·stderr에서 구독·사용량 한도를 판별할 문자열 목록. 대소문자는 구분하지 않는다 | `["usage limit"]` |
| `probe_timeout_seconds` | `20` | version·status 확인 각각의 제한 시간(초) | `20` |

`prompt_via`에 따른 규칙은 다음과 같다.

- `arg`: `run_args`의 각 인자 안에서 `{prompt}`를 작업공간 내 프롬프트 파일의 절대 경로로, `{workspace}`를 작업공간 루트의 절대 경로로 치환한다. `{prompt}`가 없으면 설정을 거부한다.
- `stdin`: 프롬프트 파일 본문을 표준입력으로 전달한다. `run_args`에는 `{prompt}`가 없어야 하며, 현재 구현은 이 모드에서 `{prompt}`와 `{workspace}`를 치환하지 않는다.
- 두 모드 모두 명령 문자열을 셸로 실행하지 않고 인자 배열로 실행한다. JSON 배열의 각 원소가 인자 하나다.

실제 벤더 CLI의 플래그는 제품과 버전에 따라 달라진다. 예시의 `<비대화형 실행 플래그>` 같은 자리표시는 그대로 실행할 값이 아니다. 각 CLI의 공식 문서에서 version, 로그인 상태 확인, 비대화형 실행 플래그를 확인해 교체한다.

간이 설정은 다음 환경변수를 지원한다. 쉼표로 구분한 값은 각각 별도 인자가 된다.

```bash
export UNTIL_AGENT_CMD='<공식 CLI 실행 파일>'
export UNTIL_AGENT_NAME='local-agent'
export UNTIL_AGENT_VERSION_ARGS='<버전 확인 플래그>'
export UNTIL_AGENT_STATUS_ARGS='<로그인 상태 확인 플래그>'
export UNTIL_AGENT_RUN_ARGS='<비대화형 실행 플래그>,{prompt}'
export UNTIL_AGENT_PROMPT_VIA='arg'
```

간이 설정에서 `UNTIL_AGENT_CMD`가 없으면 어댑터는 비활성화된다. `login_markers`, `limit_markers`, `probe_timeout_seconds`를 바꾸려면 `UNTIL_AGENT_SPEC` JSON을 사용한다. 인자 자체에 쉼표가 필요한 CLI는 간이 설정으로 정확히 표현할 수 없으므로 JSON을 사용한다.

### auto-approve 금지

승인 게이트는 학생이 확인하는 Until의 plan 승인 하나뿐이다. CLI 쪽 auto-approve까지 켜면 계획에 없던 권한 확대나 확인 우회가 생길 수 있으므로 설정을 읽는 단계에서 거부한다.

거부 목록은 `until/runtime/cli_agent.py`의 `_looks_like_auto_approve`와 동일하다. 앞의 하이픈은 무시하고 `_`는 `-`로 정규화하므로 `--auto_approve`도 거부된다.

- `yes`
- `y`
- `force`
- `auto-approve`
- `auto-accept`
- `dangerously-skip-permissions`
- `no-confirm`
- `assume-yes`
- `accept-all`
- `allow-all`

## 2단계: 샌드박스 설정

샌드박스가 파일시스템과 네트워크를 실제로 격리하지 않으면 **아무것도 실행되지 않는다**. `LocalAgentController`는 `filesystem`, `environment`, `network` 세 격리가 모두 참일 때만 probe와 실행을 허용한다. 환경 격리는 Until이 세탁한 환경만 전달해 보장하고, 나머지 둘은 운영자가 설정한 OS 샌드박스가 보장해야 한다.

설정 형식은 다음과 같다.

```bash
export UNTIL_AGENT_SANDBOX='<샌드박스 실행 파일>,<인자1>,<인자2>'
export UNTIL_AGENT_SANDBOX_ISOLATES='filesystem,network'
```

`UNTIL_AGENT_SANDBOX`는 쉼표로 나뉜 인자 목록이며 마지막에 실제 CLI 명령이 자동으로 붙는다. 각 인자의 `{workspace}`는 작업공간 루트로 치환된다. 쉼표가 들어가는 단일 인자는 이 형식으로 표현할 수 없으므로 별도 프로필 파일이나 래퍼 실행 파일을 사용한다.

`UNTIL_AGENT_SANDBOX_ISOLATES`는 기능을 켜는 옵션이 아니라 **샌드박스가 이미 보장하는 사실을 신고하는 값**이다. 실제로 막지 않은 항목을 적으면 커널이 거짓 신뢰를 바탕으로 프로세스를 실행하므로 보안 구멍이 된다. 공식 문서와 로컬 검증으로 확인한 항목만 `filesystem` 또는 `network`로 적는다. 둘 중 하나라도 빠지면 의도적으로 실행이 거부된다.

### Linux: bubblewrap(bwrap)

다음은 호스트 루트를 읽기 전용으로 두고 작업공간만 쓰기 가능하게 다시 바인드하며 네트워크 네임스페이스를 분리하는 출발점이다.

```bash
export UNTIL_AGENT_SANDBOX='bwrap,--die-with-parent,--new-session,--ro-bind,/,/,--bind,{workspace},{workspace},--chdir,{workspace},--unshare-net'
export UNTIL_AGENT_SANDBOX_ISOLATES='filesystem,network'
```

배포판의 사용자 네임스페이스 정책, CLI 실행 파일·인증 저장소의 읽기 경로, DNS·장치 요구사항에 따라 추가 바인드가 필요할 수 있다. `filesystem,network`는 위 정책이 실제 환경에서도 작업공간 밖 쓰기와 네트워크를 모두 막는 것을 확인한 뒤에만 유지한다.

### Linux: firejail

firejail은 배포판과 프로필에 따라 기본 허용 범위가 달라진다. 다음처럼 검증한 전용 프로필을 사용한다.

```bash
export UNTIL_AGENT_SANDBOX='firejail,--quiet,--profile=/절대경로/until-agent.profile'
export UNTIL_AGENT_SANDBOX_ISOLATES='filesystem,network'
```

전용 프로필은 네트워크를 끄고 작업공간 밖 쓰기를 차단하면서 `{workspace}`에 해당하는 실제 런타임 작업공간만 쓰게 해야 한다. 프로필이 `net none`만 보장하면 `network`만 신고할 수 있지만, 그러면 파일시스템 격리가 부족하므로 Until은 실행하지 않는다. 일반적인 firejail 기본 프로필만 보고 두 격리를 모두 신고하지 않는다.

### macOS: sandbox-exec

검증한 Seatbelt 프로필 파일을 지정한다.

```bash
export UNTIL_AGENT_SANDBOX='sandbox-exec,-f,/절대경로/until-agent.sb'
export UNTIL_AGENT_SANDBOX_ISOLATES='filesystem,network'
```

프로필은 네트워크를 거부하고 실제 Until 작업공간 밖 파일 쓰기를 거부해야 한다. 현재 로더는 프로필 파일 본문 안의 `{workspace}`를 치환하지 않으므로, 고정된 런타임 작업공간 상위 경로를 사용하거나 별도 래퍼가 실행마다 올바른 프로필을 만들어야 한다. macOS 버전에서 `sandbox-exec` 지원 여부와 정책 적용 결과를 직접 확인하기 전에는 격리를 신고하지 않는다.

### Windows 11: WSL2 또는 컨테이너

현재 Windows 네이티브 환경에는 이 계약을 간단히 만족시키는 기본 샌드박스 래퍼가 없다. **WSL2 Ubuntu 안에서 Until과 공식 CLI를 함께 실행**하는 것이 확인된 경로다.

#### WSL2 — 추가 설치 없이 동작 (2026-08-21 확인)

레포의 [`tools/until-sandbox.sh`](../tools/until-sandbox.sh)는 util-linux의 `unshare`만 써서 두 격리를 만든다. bubblewrap·firejail을 따로 깔지 않아도 된다.

```bash
wsl                                   # Ubuntu 24.04
cd /mnt/c/.../until
install -m 755 tools/until-sandbox.sh ~/until-sandbox.sh

export UNTIL_AGENT_SANDBOX="$HOME/until-sandbox.sh,{workspace}"
python -m until.runtime --verify-sandbox --python /usr/bin/python3
```

Ubuntu 24.04 + WSL2에서 이 순서로 세 항목 모두 통과하는 것을 확인했다.

```text
  막힘   ✓  네트워크 차단
  막힘   ✓  작업공간 밖 쓰기 차단
  가능   ✓  작업공간 안 쓰기
```

통과한 **뒤에** 신고를 켠다. 그리고 공식 CLI를 WSL 안에 설치하고 그 안에서 직접 로그인한다.

```bash
export UNTIL_AGENT_SANDBOX_ISOLATES='filesystem,network'
```

#### CLI가 실행 중 자기 설정을 써야 한다면

작업공간 밖이 읽기 전용이라, 공식 CLI가 실행 중 홈 디렉터리로 토큰을 갱신하는 종류면 그대로는 실패한다. 그 경로만 좁게 열 수 있다.

```bash
export UNTIL_AGENT_SANDBOX="$HOME/until-sandbox.sh,--allow-write,$HOME/.config/그CLI,{workspace}"
```

**환경변수가 아니라 인자다.** 커널이 에이전트에게 넘기는 환경을 세탁하므로(`sanitize_environment`) 환경변수로는 래퍼까지 도달하지 않는다.

열어 준 경로 말고는 그대로 막힌다(실측: 허용 경로 쓰기 성공, 그 외 경로·네트워크는 여전히 차단). 그래도 **연 만큼 격리는 약해진다** — 열었으면 `--verify-sandbox`를 다시 돌리고, 무엇을 왜 열었는지 적어 둘 것. 인증 파일을 호스트에서 복사해 넣는 것은 다른 얘기이고, 이 제품이 하지 않기로 한 일이다.

`unshare`가 권한 없는 user namespace를 못 만드는 환경(일부 커널 정책)에서는 래퍼가 `SANDBOX_SETUP_FAILED`로 종료한다 — 조용히 뚫린 채 돌지 않는다.

Windows에서 단순히 `cwd`를 작업공간으로 지정하거나 환경변수를 지운 것만으로는 `filesystem`·`network`를 신고할 수 없다. 컨테이너도 작업공간 외 쓰기 금지와 네트워크 차단을 둘 다 실제 설정하고 검증한 경우에만 두 값을 신고한다.

#### 컨테이너 (2026-08-21 확인)

Docker Desktop이 있으면 컨테이너로도 된다. 아래 형태로 격리 세 항목을 확인했다 — 다만 **WSL 안에서** Until을 돌릴 때만 그대로 쓸 수 있다(이유는 아래).

```bash
export UNTIL_AGENT_SANDBOX='docker,run,--rm,--network,none,--read-only,-v,{workspace}:{workspace}:rw,-w,{workspace},<로그인된 CLI가 든 이미지>'
python -m until.runtime --verify-sandbox
```

`{workspace}`는 **호스트 경로 그대로** 치환된다. 그래서 Windows 네이티브에서 돌리면 `C:\Users\...`가 컨테이너 경로 자리에 들어가 `invalid working directory`로 죽는다(실측). WSL 안에서 돌리면 경로가 `/tmp/...` 형태라 그대로 맞는다.

남은 문제는 **로그인 상태**다. `--rm`은 컨테이너를 지우므로 CLI 로그인이 매번 사라진다. 인증을 named volume에 두는 방법은 해당 CLI 공식 문서를 따르고, 호스트에서 인증 파일을 복사해 넣지는 마라 — 이 제품이 하지 않기로 한 일이다. 이 점 때문에 **WSL2 래퍼 쪽이 더 단순하다**(로그인이 WSL 사용자 계정에 그대로 남는다).

## 3단계: 동작 확인

먼저 **샌드박스가 실제로 막는지** 시험한다. 이 명령은 에이전트 설정 없이도 돌아간다.

```bash
python -m until.runtime --verify-sandbox            # 샌드박스 안의 python 경로가 다르면 --python 으로
```

샌드박스 안에서 실제로 ①외부 연결 ②작업공간 밖 쓰기를 시도해 보고, **실패해야** 통과로 친다. 네트워크는 대조군을 함께 본다 — 샌드박스 밖에서도 연결이 안 되는 기계라면 안에서 실패한 것이 격리의 증거가 아니므로 `모름`으로 보고한다. 증명되지 않은 항목을 `UNTIL_AGENT_SANDBOX_ISOLATES`에 적어 두었으면 그것도 짚어 준다.

두 격리가 모두 확인된 뒤에 신고를 켜고, 그 다음 probe로 CLI를 확인한다.

```bash
python -m until.runtime --probe
```

샌드박스 격리 신고가 하나라도 빠지면 probe 전에 `isolated local-agent execution boundary is unavailable`로 차단된다.

| probe 상태 | 뜻 | 고칠 것 |
|---|---|---|
| `unavailable` | 격리 러너가 없거나, CLI 실행 파일을 찾지 못했거나, version 확인이 시간 초과·실패했다 | `UNTIL_AGENT_SANDBOX`와 격리 신고를 확인하고, 샌드박스 안의 `PATH`, `command`, `version_args`, `probe_timeout_seconds`를 고친다 |
| `login_required` | status 출력이 로그인 marker와 일치했거나 status 명령이 실패했다 | 해당 공식 CLI를 직접 열어 로그인하고, 필요하면 공식 문서에 맞게 `status_args`와 `login_markers`를 고친다 |
| `ready` | version 확인을 통과했고, status를 설정했다면 로그인 상태 확인도 통과했다 | plan 내용을 확인한 뒤 Until에서 승인한다. `status_args`가 비어 있으면 로그인 여부는 첫 실행에서 판정된다 |

status 출력이 사용량 marker와 일치하면 probe는 `busy`를 반환한다. 구독 한도가 회복될 때까지 기다리거나 공식 CLI의 구독 상태를 확인한다.

## 4단계: 과제 실행

```bash
python -m until.runtime --fast                       # eTL에서 마감 임박 과제를 골라 그대로
python -m until.runtime --list                       # 과제 목록만 보기
python -m until.runtime --etl-url <과제 URL>          # 특정 과제
python -m until.runtime 과제.md [첨부.pdf ...]        # eTL 없이 로컬 파일로
```

eTL 경로는 액세스 토큰이 필요하다(`--token` 또는 `UNTIL_CANVAS_TOKEN`). 과제 선택은 웹의 '가장 가까운 과제 하나'와 같은 규칙을 쓴다 — 미제출·기한 전·마감 임박 순.

순서는 다음과 같고, 각 단계는 앞 단계를 통과해야만 진행한다.

1. **수집** — 파일을 파싱한다(결정적, LLM 호출 0).
2. **AI 금지 확인** — 과제 지시문이 AI 사용을 명시적으로 금지하면 에이전트를 띄우기 전에 멈춘다.
3. **명세·라우팅·정책** — 필수 항목·분량·인용 요건과 유효 AI 정책을 결정적으로 정한다. 과제가 AI 정책에 침묵하면 진행하지 않고 멈춘다(fail-closed). 강의계획서 문구를 파일로 저장해 `--course-policy`로 넘기면 그 정책을 상속한다.
4. **작업공간·계획** — 격리된 작업공간을 만들고 에이전트가 무엇을 바꿀지 보여 준다. 여기까지 실행되는 프로세스는 probe뿐이다.
5. **승인** — 사람이 `y`를 입력해야 실행한다. 비대화형으로 돌리려면 `--yes`. 승인이 없으면 작업 프로세스는 0회다.
6. **실행 → 검증 → 1회 수정** — 결정적 검증에 걸리면 막힌 항목만 담아 **한 번만** 다시 시킨다.
7. **검증 명령 실행** — 과제 유형이 실행할 명령을 선언했으면(예: 코드 과제의 테스트) 커널이 **에이전트와 같은 격리 안에서** 돌린다. 명령은 에이전트가 돌기 전에 플러그인이 정해 두고, 커널이 자기 천장(`security.KERNEL_ALLOWED_COMMANDS`)으로 다시 거른다 — 에이전트가 쓴 파일이 명령줄이 되는 경로는 없다. 테스트 **실패**는 차단(재시도 1회가 붙는다), 도구가 없어 **못 돌린 것**은 경고다. 둘을 섞으면 멀쩡한 코드를 고치게 된다.
8. **제출본** — 검증을 통과한 초안에는 경계선 표식 `[[DECISION: ...]]`이 살아 있다(검증기가 남기라고 강제한다). 그대로 올리면 교수가 그 대괄호를 보므로, 마지막에 **올려도 되는 제출본**을 따로 만든다(`artifacts/제출본.md`).
   - 사람이 정할 곳을 하나씩 물어보고, 답한 것은 그 문장으로 치환한다.
   - 비대화형이면 `--answers 파일`(JSON 또는 줄당 하나)로 미리 줄 수 있고, `--yes` 로 돌려도 `--ask` 를 붙이면 결정만은 물어본다.
   - 끝까지 안 정한 곳은 `【직접 정할 것 N: ...】` 자리표시로 남고 화면이 경고한다 — **여기서 대신 정하지 않는다.**
   - 치환 뒤 분량·인용·필수 항목을 한 번 더 본다("검증은 통과했는데 올릴 파일은 요건 미달"을 막는다).
   - eTL에서 가져온 과제면 **제출하러 갈 eTL 페이지 URL**도 함께 찍는다.

**제출은 하지 않는다.** 이 명령은 검증된 파일이 어디 있는지 알려 줄 뿐이고, eTL에 올리는 것은 사람 몫이다.

종료 코드는 `0` 통과, `1` 차단(정책·승인 거부·검증 실패), `2` 설정 문제다. `--json`으로 `RuntimeReport` 전문을 받을 수 있다.

## 문제 해결

실행 뒤 receipt의 `status`와 `reason`, 잘린 stdout·stderr 요약을 함께 본다.

| receipt 상태 | 가능한 원인 | 대처 |
|---|---|---|
| `login_required` | 공식 CLI 로그인 만료, 로그인 marker 검출 | 공식 CLI에서 사용자가 직접 다시 로그인한다. 쿠키나 OAuth 토큰을 Until에 복사하지 않는다 |
| `usage_limited` | 구독 사용량·요청 한도·크레딧 제한 marker 검출 | 공식 CLI의 구독 상태를 확인하고 한도 회복을 기다린다. Until은 결제·요금제를 우회하거나 다른 인증정보로 재시도하지 않는다 |
| `timeout` | 작업이 `AgentJob.timeout_seconds` 안에 끝나지 않음 | 로그에서 멈춘 단계를 확인하고 작업 범위를 줄이거나 런타임의 작업 제한 시간을 합리적으로 조정한다 |
| `cancelled` | 사용자가 중단했거나 종료 코드가 중단으로 분류됨 | 변경 파일을 검토한 뒤 새 plan으로 다시 시작한다. 중단된 실행을 성공으로 간주하지 않는다 |
| `failed` | 실행 파일 없음, 프롬프트 읽기 실패, 비정상 종료 코드, 격리 러너 누락 등 | `reason`, exit code, stdout·stderr 요약을 확인한다. 공식 CLI 명령과 샌드박스 안의 경로·권한부터 고친다 |

설정 자체가 잘못되면 receipt 전에 거부될 수 있다. 대표적으로 `prompt_via=arg`인데 `{prompt}`가 없거나, auto-approve 플래그가 있거나, `command`가 비어 있으면 `CliSpecError`가 발생한다.

## 보안 요약

- 환경변수는 `sanitize_environment`가 작업별 allowlist에 든 이름만 남긴다. 이름에 `TOKEN`, `SECRET`, `PASSWORD`, `PASSWD`, `API_KEY`, `COOKIE`, `SESSION`, `AUTH`, `CREDENTIAL`, `BEARER`, `PRIVATE_KEY`, `ACCESS_KEY`가 들어간 시크릿 환경변수는 allowlist에 있어도 어댑터에 전달하지 않는다.
- receipt의 stdout, stderr, reason은 토큰·비밀번호·쿠키·세션·API 키와 Bearer 인증값 패턴을 마스킹하고 각각 최대 8,192자로 자른다. 프로세스 캡처 자체도 출력별 256 KiB 상한을 둔다.
- 작업공간 경로는 절대경로·상위 이동·심볼릭 링크·reparse point를 거부한다. 실행 전후 스냅샷과 receipt의 변경 파일을 비교하고 승인된 편집 범위 밖 변경은 검증에서 차단한다.
- OS 샌드박스가 작업공간 밖 쓰기와 네트워크를 실제로 막아야 한다. `UNTIL_AGENT_SANDBOX_ISOLATES`는 검증을 대신하지 않으며 거짓 신고해서는 안 된다.
- AI가 만든 결과는 성공 종료만으로 제출되지 않는다. 결정적 validator를 통과한 뒤 별도의 제출 확인을 거친다.
