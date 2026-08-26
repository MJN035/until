# Until

[![CI](https://github.com/MJN035/until/actions/workflows/ci.yml/badge.svg)](https://github.com/MJN035/until/actions/workflows/ci.yml)
[![Determinism](https://github.com/MJN035/until/actions/workflows/determinism.yml/badge.svg)](https://github.com/MJN035/until/actions/workflows/determinism.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-stdio-black.svg)](docs/MCP.md)

> **대학 LMS(eTL·Canvas·Moodle)를 아무 AI 에이전트에나 물릴 수 있게 하는 MCP 서버.**
> 과제 목록·마감·명세·강의자료·유형 분류·제출 가능 판정을 **결정적으로** 돌려준다.
> **LLM 호출 0건 · 런타임 의존성 0개 · 토큰을 디스크에 쓰지 않음.**

Until은 앱이 아니라 **부품**이다. Claude Code·Codex CLI·Cowork 같은 에이전트가 Until을 호출해서
"내 이번 주 과제가 뭐고, 이건 무슨 유형이고, 뭘 내야 끝나는지"를 가져간다. 화면이 필요 없다.

---

## 설치

### Claude Code — 플러그인 (가장 짧다, npm 불필요)

```
/plugin marketplace add https://github.com/MJN035/until
/plugin install until
```

### npm — Claude Code · Codex CLI · 그 밖의 MCP 클라이언트

```bash
npm install -g until-mcp
until setup            # 감지되는 클라이언트에 자동 등록(기존 설정은 병합, 덮어쓰지 않음)
```

파이썬 소스가 패키지에 동봉돼 있다. **pip도 uv도 필요 없고 시스템 `python3`만 있으면 된다** — 런타임 의존성이 0개라서 가능하다.

### 파이썬 (PyPI 미배포 — git 직접 설치)

```bash
pip install git+https://github.com/MJN035/until
until-mcp setup
```

### 직접 등록 (설정 파일에 붙여넣기)

```jsonc
{
  "mcpServers": {
    "until": {
      "command": "python",
      "args": ["-m", "until.mcp_server"],
      "env": { "UNTIL_CANVAS_TOKEN": "<eTL 토큰>" }
    }
  }
}
```

Codex CLI는 `~/.codex/config.toml`의 `[mcp_servers.until]`에 같은 내용을 쓴다.

---

## 도구 9종

| 도구 | 하는 일 | 토큰 |
|---|---|:---:|
| `until_inbox` | 과제 목록을 마감 임박순으로. D-day·임박·제출 여부·종류가 붙는다 | 필요 |
| `until_assignment` | 과제 1건의 명세·요구 항목·분량 요건·마감·첨부 수·본문 발췌 | 필요 |
| `until_materials` | 그 과제와 키워드가 겹치는 강의자료 상위 N건(+본문 발췌) | 필요 |
| `until_series` | 같은 시리즈·단계의 **내 지난 제출물** 교차참조 | 필요 |
| `until_brief` | 과목 주차 브리프·공지 발췌 | 필요 |
| `until_semester` | 학기 전체 상태 한 응답 — 과목별 과제 수·임박·다음 마감 | 필요 |
| `until_control_tower` | 과제 1건의 **제출 가능 상태** — 필수 첨부·팀 역할·분량·정책을 severity별로 | 필요 |
| `until_route` | 제목·본문·첨부명만으로 처리 전략 분류 + 근거 + 부족 정보 질문 | **불필요** |
| `until_readiness` | 초안 본문을 받아 마감·분량·인용·남은 결정을 점검 | **불필요** |

입출력 스키마 전문은 **[`docs/MCP.md`](docs/MCP.md)**.

### 토큰 없이 30초 만에 확인하기

`until_route`와 `until_readiness`는 네트워크도 토큰도 쓰지 않는다. 붙자마자 이걸로 확인하면 된다.

```bash
python -m until.mcp_server --list-tools     # 도구 목록만 출력하고 종료
```

에이전트에게 이렇게 물어보면 된다:

> "until_route로 '3주차 실험보고서'가 무슨 유형인지 분류해 줘. 첨부는 report_form.hwp야."

---

## 성적부 열은 과제가 아니다

실제 계정 전수 대조(21과목 **148항목**) 결과, **47건(32%)이 과제가 아니라 성적부 열**이었다 —
`중간고사`·`출석 점수`·`M1`~`M7`·`총점`·`태도`. 실제 과제는 **101건**이다.

`until_inbox`는 이것을 **기본으로 빼고**(`kind: "assignment"`), 지우지는 않는다(`kind: "gradebook"`으로 꺼내 볼 수 있다).
LMS API를 그대로 흘려보내면 에이전트가 `M3`를 과제로 믿는다. **인박스의 신뢰는 첫 응답에서 결정된다.**

---

## 원칙 — 타협하지 않는 네 가지

| 원칙 | 뜻 |
|---|---|
| **LLM 호출 0건** | MCP 경로에는 생성 기능이 없다. 초안·문장을 쓰지 않는다. 부르는 쪽 모델이 쓴다 |
| **완전 결정적** | 같은 입력 → 같은 출력. CI에서 기계로 강제한다([determinism 워크플로](.github/workflows/determinism.yml)) |
| **토큰 미저장** | eTL 토큰은 환경변수 `UNTIL_CANVAS_TOKEN`으로만 읽는다. 디스크에 쓰지 않는다 |
| **의존성 0** | `dependencies = []`. MCP SDK도 쓰지 않는다 — stdio는 줄바꿈 구분 JSON-RPC라 표준 라이브러리로 충분하다 |

토큰이 없으면 **크래시가 아니라 무엇이 없는지 말하는 오류**를 돌려준다.

**왜 생성을 안 넣나.** 실제 제출물 18건의 원본을 추적해 보니 강의실에서 들은 말 15건,
활동지·조원 피드백 2건, 내 지난 제출물 1건이었다. LMS 데이터만으로 만들 수 있는 초안의 상한은 **5.6%**다.
그 5.6%를 파느니, 나머지 94%를 쓰는 사람(과 그 사람의 에이전트)에게 **정확한 재료와 판정**을 주는 편이 낫다.

---

## eTL 연결

```bash
export UNTIL_CANVAS_TOKEN=...     # Canvas REST 토큰 (권장 경로)
export UNTIL_CANVAS_BASE=https://<lms-host>
```

Moodle Web Services 모드(`--ws`)와 브라우저 SSO 경로도 있다 — [`docs/ETL_CONNECTOR.md`](docs/ETL_CONNECTOR.md),
[`docs/ETL_READ_ONLY.md`](docs/ETL_READ_ONLY.md) 참고. **읽기 전용이다. LMS에 아무것도 쓰지 않는다.**

---

## 검증

```bash
python run_tests.py -q                 # 오프라인 90개 스위트 (키·인터넷 불필요)
python tools/check_determinism.py      # 같은 입력 → 같은 출력
```

---

## 그 밖의 표면 (CLI·웹)

Until은 MCP 이전에 CLI와 웹 UI로 먼저 만들어졌고, 그 코드는 이 저장소에 남아 있다.
**지금의 정문은 MCP다.** CLI·웹은 개발·디버깅용 보조 표면으로 유지된다.

```bash
python demo.py                                   # 키 없이 도는 샘플 데모
python -m until examples/sample_assignment.txt   # CLI 단건
```

자세한 사용법은 [`docs/FEATURES.md`](docs/FEATURES.md), 설계 근거는 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## 라이선스

AGPL-3.0-only. 기여는 [CLA](CLA.md) 동의가 필요하다 — [`CONTRIBUTING.md`](CONTRIBUTING.md) 참고.
