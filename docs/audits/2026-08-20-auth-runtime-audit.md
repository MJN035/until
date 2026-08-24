# 신규 코드 감사 — 인증 · Local Agent Runtime (2026-08-20)

## 요약
- 심각(즉시 수정): 3건
- 중간: 4건
- 낮음/정보: 1건
- 확인했으나 문제 없음: 11건

이번 감사는 지정된 소스와 기준 문서만 읽고, 네트워크 없이 임시 Python/Node 실행으로
재현했다. 아래에서 “확인”은 현재 호출 경로와 실행 결과가 뒷받침하는 사실이고,
배포 프록시·브라우저 정책처럼 저장소만으로 확정할 수 없는 부분은 “미확인”으로 구분한다.

## 발견 사항

### [심각] 문자열 설정만으로 실제 샌드박스 없이 격리 완료를 신고할 수 있음
- **위치**: `until/runtime/boundary.py:51`, `until/runtime/boundary.py:103`, `until/runtime/local_agent.py:115`
- **문제**: `load_sandbox()`는 `UNTIL_AGENT_SANDBOX_ISOLATES`의 문자열 선언을 검증 없이
  `isolates_filesystem`·`isolates_network`로 옮긴다. `SubprocessBoundary`는 prefix가 하나라도
  있고 두 선언이 있으면 세 격리를 모두 `True`로 신고하며, 커널은 그 불리언만 확인한다.
  따라서 wrapper가 실제 격리를 제공하는지, 필요한 옵션을 포함하는지, 실행 시 실패 없이
  적용됐는지 증명하지 못한다.
- **재현/공격 시나리오**: 임시 실행에서
  `UNTIL_AGENT_SANDBOX=env`,
  `UNTIL_AGENT_SANDBOX_ISOLATES=filesystem,network`를 주자 경계가
  `(True, True, True)`를 신고했고 실제 argv는 `env agent --version`이었다. `env`는 파일시스템과
  네트워크를 격리하지 않는다. 이 설정을 바꿀 권한이 있는 운영자·배포 설정 오염이 필요하지만,
  일단 성립하면 승인된 CLI가 호스트 파일과 네트워크에 그대로 접근할 수 있다.
- **권고**: 자유 문자열 선언을 보안 근거로 쓰지 말고, 지원하는 sandbox별 고정 adapter와
  필수 옵션 검증을 둔다. 실행 전에 격리 내부의 canary로 작업공간 밖 읽기·쓰기와 네트워크가
  실제 차단되는지 검사하고, 검사 실패·알 수 없는 wrapper는 fail-closed 처리한다.

### [심각] 결정 마커 하나만 있으면 다른 사람 고유 판단을 대신 확정해도 검증 통과
- **위치**: `until/runtime/report_runtime.py:272`
- **문제**: `_check_decisions()`는 길이 5 이상의 `[[DECISION: ...]]`가 본문 어디엔가 하나만
  있으면 빈 findings를 반환한다. 사람 고유 판단을 확정한 문장이 다른 곳에 있어도 검사하지
  않으므로 “Draft 경계선”을 강제한다는 주석·계약과 다르다.
- **재현/공격 시나리오**: `_check_decisions("나는 진로를 의사로 확정한다. "
  "[[DECISION: 표지 색상을 고르세요]]")`를 실행한 결과 `[]`였다. 즉 핵심 진로 판단을 대신
  확정하고 중요하지 않은 결정 마커 하나만 남긴 초안이 통과한다.
- **권고**: 단순 존재 검사를 통과 조건으로 삼지 말고, 입력 명세에서 사람 몫 결정 요구를
  구조화해 각 요구와 마커를 대응시키거나 기존 BoundaryValidator의 결정적 위반 검사를 재사용한다.
  대응할 결정 요구가 없는 과제만 별도 명시 상태로 허용한다.

### [심각] 일반 보고서의 근거 없는 수치가 통과하고 검사 예외도 성공으로 처리됨
- **위치**: `until/runtime/report_runtime.py:284`
- **문제**: `_check_measurements()`는 하위 검사기가 전략·단계를 대상으로 삼을 때만 차단하며,
  import 또는 실행 예외가 나면 `[]`로 통과시킨다. `ReportRuntime`이 지원하는
  `general_report`·`evidence_report`에도 CLAUDE.md의 수치 날조 금지 불변 규칙이 적용되지만,
  이 경로는 이를 강제하지 못한다.
- **재현/공격 시나리오**: `_check_measurements("실험 정확도는 99.9%였다.",
  "general_report", "")`는 `[]`를 반환했다. 같은 문장을 `lab_report_cycle`, `result`로
  검사하면 `ungrounded_measurement` block이 생겨 전략별 비대칭도 확인됐다. 예외 경로는 코드상
  두 `except Exception: return []`로 확인했으며 실제 예외 주입은 하지 않았다.
- **권고**: 모든 지원 전략에 공통 수치 근거 검사를 적용하고, 검사기 import·실행 실패는
  `measurement_check_unavailable` block으로 처리한다. 측정이 필요 없는 숫자 범주는 명시적
  allowlist로만 제외한다.

### [중간] 탭을 섞은 `next`가 브라우저에서 외부 URL로 해석됨
- **위치**: `until/google_auth.py:296`, `until/web.py:3851`, `until/web.py:3898`, `until/asgi.py:407`, `until/asgi.py:450`
- **문제**: `safe_next()`는 CR/LF와 선두 `//`, `/\\`는 막지만 탭 등 다른 ASCII 제어문자를
  허용한다. URL 파서는 탭을 제거한 뒤 `//host`로 해석할 수 있어 로그인 완료 후 오픈
  리다이렉트가 된다.
- **재현/공격 시나리오**: 쿼리 디코딩 후 `next`가 `/\t/evil`이 되도록
  `?next=/%09/evil`을 사용했다. `safe_next('/\t/evil')`는 그대로 반환했고 Python
  `urljoin('https://good.example/login', value)`와 Node WHATWG `new URL(value, base)`가 모두
  `https://evil/`로 해석했다. 반면 `/%2F%2Fevil`, 유니코드 분수 슬래시·전각 슬래시,
  `/%5c%5cevil`은 같은 origin의 path로 남았고 `/\\evil`은 `/`로 거부됐다.
- **권고**: ASCII C0 제어문자와 DEL을 전부 거부하고, 백슬래시를 포함한 경로도 거부한다.
  가능하면 URL parser로 해석한 뒤 scheme·netloc이 비어 있고 정규화 경로가 정확히 `/`로
  시작하는지 재검증한다.

### [중간] 상태 변경 POST에 CSRF 토큰·Origin 검사가 없어 same-site 공격을 막지 못함
- **위치**: `until/web.py:4263`, `until/web.py:4274`, `until/web.py:4786`, `until/web.py:4854`, `until/asgi.py:479`, `until/asgi.py:494`, `until/asgi.py:972`, `until/asgi.py:1017`
- **문제**: `/logout`, `/submitted`, `/suggest`, `/finalize`는 모두 POST이지만 CSRF 토큰과
  `Origin`/`Referer` 검사가 없다. 인증·uid 쿠키의 `SameSite=Lax`는 서로 다른 site에서 시작한
  일반 cross-site POST에는 쿠키를 보내지 않으므로 그 경우 피해자 세션 변경을 막는다. 그러나
  같은 registrable domain의 공격자 통제 sibling origin은 cross-origin이어도 same-site라 쿠키가
  전송될 수 있고, Lax는 이를 막지 않는다. `/suggest`·`/finalize`는 세션 내용과 AI 실행 상태를,
  `/submitted`는 제출 표시를 바꾼다.
- **재현/공격 시나리오**: 코드에서 네 라우트 모두 토큰·Origin 검사 없이 폼의 `session`을
  받아 `_get_session()` 후 변이하는 것을 확인했다. 실제 브라우저의 sibling subdomain은 이번
  오프라인 환경에 구성하지 못해 공격 전체는 **미확인**이다. unrelated-site의 일반 POST는
  Lax 쿠키 미전송으로 차단되며, `/logout` 응답의 삭제 `Set-Cookie`를 브라우저가 cross-site
  navigation에서 적용하는지는 브라우저별 정책이라 **미확인**이다.
- **권고**: 세션에 결합한 CSRF 토큰을 모든 상태 변경 폼에 넣고 검증한다. 추가로 HTTPS 운영
  origin의 `Origin`을 exact match하고, 없을 때만 엄격한 `Referer` 검사를 사용한다.

### [중간] ASGI는 프록시 뒤 HTTPS를 앱 코드에서 인식하지 못해 Secure 쿠키가 빠질 수 있음
- **위치**: `until/web.py:3281`, `until/web.py:3294`, `until/asgi.py:267`, `until/asgi.py:411`, `until/asgi.py:451`
- **문제**: stdlib 서버는 `X-Forwarded-Proto`와 `CF-Visitor`를 직접 보고 Secure를 붙인다.
  ASGI는 `request.url.scheme == "https"`만 사용한다. 앞단 프록시가 ASGI scope의 scheme을
  `https`로 정규화하지 않는 배포에서는 외부 연결이 HTTPS여도 `uid`, `gauth`, `auth` 쿠키가
  Secure 없이 발급된다. 두 서버의 삭제 쿠키 속성도 동일하지 않다.
- **재현/공격 시나리오**: ASGI 코드 경로상 `X-Forwarded-Proto`를 직접 읽는 부분이 없음을
  확인했다. 현재 환경에는 Starlette/FastAPI가 없어 scope 기반 실행 재현은 못 했다. 실제
  운영 서버가 proxy headers를 신뢰·정규화하는지는 저장소 범위에서 **미확인**이다.
- **권고**: 신뢰할 프록시 목록과 forwarded-header middleware를 운영 엔트리포인트에서
  명시하고, 앱에는 단일 `is_https()` 정책을 두어 두 서버가 공유한다. 클라우드 모드에서는
  HTTPS가 확인되지 않으면 인증 시작을 fail-closed하거나 Secure를 강제한다.

### [중간] auto-approve 차단은 결합형 인자와 probe 인자를 우회 가능
- **위치**: `until/runtime/cli_agent.py:91`, `until/runtime/cli_agent.py:125`, `until/runtime/cli_agent.py:212`, `until/runtime/cli_agent.py:232`
- **문제**: 검사는 `run_args`의 인자 전체가 금지 문자열과 정확히 같을 때만 동작한다.
  `--yes=true` 같은 `=` 결합형과 유니코드 호환문자 변형을 허용하며,
  `version_args`·`status_args`는 아예 검사하지 않는다. 두 probe 명령은 사용자 승인 전에
  실행된다. workspace 변경은 이후 snapshot 비교로 탐지되지만, 실행 자체와 workspace 밖
  부작용은 되돌릴 수 없다.
- **재현/공격 시나리오**: 임시 실행에서 `run_args=('--yes=true','{prompt}')`,
  `run_args=('--ｙｅｓ','{prompt}')`, `version_args=('--yes',)`,
  `status_args=('--dangerously-skip-permissions',)`가 모두 `CliSpec` 생성에 성공했다.
  대소문자 `--YES`는 정상 거부됐다.
- **권고**: 모든 실행 인자(`version_args`, `status_args`, `run_args`)에 같은 정책을 적용하고,
  `--flag=value`는 `=` 앞 이름도 검사한다. NFKC+casefold 정규화 후 허용된 probe 인자와
  벤더별 run 인자를 allowlist하는 방식이 안전하다. probe는 읽기 전용 고정 명령만 허용한다.

### [낮음] 메모리 세션의 소유자 표가 없으면 소유권 검사가 fail-open
- **위치**: `until/web.py:776`, `until/asgi.py:468`, `until/asgi.py:479`
- **문제**: `_get_session()`은 `_OWNER[token]`이 존재하면서 다른 uid일 때만 거부한다.
  `_SESSIONS`에는 결과가 있지만 `_OWNER`가 비어 있는 상태에서는 현재 uid와 무관하게 반환한다.
  정상 생성·복원 경로는 `_persist_session()` 또는 `_claim_session()`을 거치므로 현재 코드에서
  이 불일치 상태가 자연 발생하는 경로는 찾지 못했다. 따라서 실제 원격 공격 가능성은 낮고
  선행 조건은 **미확인**이지만, 소유권 심층방어 자체는 fail-closed가 아니다.
- **재현/공격 시나리오**: 임시로 cloud 모드에서 `_SESSIONS['leaked-token']`만 넣고
  `_OWNER`를 비운 뒤 공격자 uid로 `_get_session('leaked-token')`을 호출하자 객체가 반환됐다.
  같은 토큰의 owner를 피해자 uid로 넣으면 `None`이 반환됐다.
- **권고**: cloud 모드의 메모리 hit는 `owner == current_uid`일 때만 허용한다. owner가 없으면
  현재 uid namespace의 디스크/KV에서 복원해 소유권을 확정하거나 404로 거부한다.

## 확인했고 문제를 못 찾은 항목
- **1 인증 쿠키 위조**: `unsign()`의 HMAC·만료 검사와 `unpack_user()`의
  `uid == uid_for(sub)` 재검사를 함께 통과해야 한다. uid 재검사만으로는 인증이 아니지만,
  현재 결합에서는 키 없이 다른 uid로 권한 상승하는 경로를 못 찾았다.
- **5 id_token 수신 경로**: 운영 호출은 stdlib·ASGI 모두 `exchange_code()` 직후
  `decode_id_token()`을 호출하며 토큰 endpoint는 상수 HTTPS URL이다. 별도 운영 호출자는
  검색되지 않았다. 독립 호출 시 위조 서명 JWT도 claims만 맞으면 통과함을 확인했으므로
  이 결론은 두 함수의 현재 결합과 기본 transport에 한정되며, 서명 검증과 동등한 일반 보장은 아니다.
- **6 인증 실패 로그·화면 유출**: token endpoint의 body, code, id_token, client_secret을
  출력하거나 예외에 넣는 경로를 못 찾았다. 사용자 화면에는 고정 한국어 오류만 전달된다.
- **7 익명 데이터 경로 조작**: 현재 두 호출점의 `anon_uid`는 `_UID_RE`를 통과한 쿠키 값이고
  대상 uid는 `uid_for(sub)` 산출물이다. `_adopt_anon_data()` 자체는 재검증하지 않지만 현재
  호출 경로에서 사용자 입력으로 경로를 탈출시키는 방법을 못 찾았다.
- **8 인수인계 중 파일 소실**: 각 source/destination은 같은 base directory 아래이고
  `Path.replace()`는 파일 단위 원자 이동이다. 예외가 나면 그 파일은 source에 남고, 성공하면
  destination에 남는다. 여러 파일 중 일부만 이동되는 부분 완료는 가능하지만 양쪽 모두에서
  사라지는 코드 경로는 못 찾았다.
- **9 과제 링크 입력 주입**: course·assignment가 모두 `isdigit()`일 때만 URL에 들어간다.
  사용자 문자열을 host나 path 구조로 삽입하는 경로를 못 찾았다. base URL은 운영 환경 설정이다.
- **11 계획서 §2 경계**: 감사한 runtime 모듈은 모델 API를 직접 호출하지 않고 공식 CLI를
  subprocess 계약으로만 실행한다. CLI 세션 파일·쿠키·OAuth 토큰을 읽는 코드와 AI가 스스로
  완료 판정하거나 실제 제출하는 코드도 못 찾았다. 제출 bridge는 dry-run·hash·binding까지만 한다.
- **13 환경 세탁 우회**: 정상 orchestrator 경로는 controller가 `sanitize_environment()`를
  호출하고, boundary runner는 adapter가 넘긴 env를 무시하고 그 결과 dict만 subprocess에 넘긴다.
  이 경로에서 allowlist 밖 env가 전달되는 방법을 못 찾았다.
- **15 작업공간 경로 탈출**: `../x`, 절대경로, Windows drive·백슬래시 traversal,
  UNC 경로, symlink ancestor를 임시 directory에서 시도했고 모두 `RuntimeSecurityError`로 거부됐다.
  정상 상대경로만 workspace 아래로 해석됐다.
- **16 bundle content hash**: assignment id와 path·SHA-256·size를 정규 JSON으로 직렬화한 뒤
  SHA-256을 적용한다. 파일 순서만 다른 같은 bundle은 의도적으로 같고, 다른 byte content가
  같은 hash가 되려면 파일 SHA-256 또는 최종 SHA-256 충돌이 필요하다. 실용적인 충돌·직렬화
  모호성은 못 찾았다. MIME은 hash에 없지만 제출 전 별도 검사를 거친다.
- **텔레메트리 자유 문자열**: 감사 대상의 신규 인증·runtime 경로에서 code, token, 과제 원문,
  결정 답변을 텔레메트리 자유 문자열로 내보내는 경로를 못 찾았다. 확인한 admin event는 고정
  event 값 또는 기존 fingerprint 경로를 사용한다.

## 감사 범위 밖
- 실제 운영 프록시/Uvicorn의 forwarded-header 신뢰 설정과 외부 HTTPS 종단 구성.
- 실제 브라우저를 이용한 sibling-subdomain CSRF 및 cross-site logout 삭제 쿠키 동작.
- Google token endpoint·인증서 검증·OIDC 키 배포의 네트워크 실검증(네트워크 금지로 미수행).
- 운영자가 지정할 bubblewrap/firejail/container 명령의 실제 격리 강도와 플랫폼별 옵션.
- 공격자가 동시에 symlink/reparse point를 바꾸는 TOCTOU 경쟁과 별도 호스트 프로세스 공격.
- 지정 심볼 밖의 `until/web.py`, 지정 라우트 밖의 `until/asgi.py`, 제출 gate·Canvas 실제 전송 구현.
