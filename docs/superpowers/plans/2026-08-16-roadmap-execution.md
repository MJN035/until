# 로드맵 일괄 실행 계획 (2026-08-16)

> Spec(구속 권위): `C:\Users\MJ\Desktop\until-CLEAN\로드맵.md` + 레포 `CLAUDE.md` 불변 규칙 +
> `docs/superpowers/specs/2026-08-14-submission-gate-design.md`(Task 6 한정).
> 목표: 로드맵 Tier 1~4 중 **사용자 개입 없이 코드로 닫을 수 있는 전부**를 구현한다.
> 사용자 몫(실키 라이브 eval 실행, armed 실제출 1회, PG 실계정, Render 유료 전환, 푸시 승인)은 범위 밖.

## Global Constraints (모든 태스크 공통)

1. **테스트 게이트**: `PYTHONIOENCODING=utf-8 python run_tests.py` 전 스위트 통과(현재 62개, 스위트 추가 시 `run_tests.py`의 `SUITES` 리스트에 basename 추가 + README.md·docs/FEATURES.md·CLAUDE.md의 스위트 수 갱신). 각 테스트 파일은 `python tests/test_<name>.py` 단독 실행 가능해야 하고 exit 0=통과.
2. **오프라인 불변**: 모든 테스트는 키·인터넷 없이 통과(`--backend mock`). 네트워크는 FakeHTTP/주입으로만.
3. **린트**: `ruff check .` 클린 (select=F,E9,B,PLE,RUF100; line-length 120).
4. **결정적 모듈**: `capture/`, `context/`, `boundary/`, `prompts/suggest.py`는 LLM 호출 0.
5. **라우팅 동결(8월)**: `until/understanding/`의 라우터·알고리즘(v0.1/v0.2 결정성)을 변경하지 않는다. `measured_check.py`의 탐지 로직도 변경 금지(소비부만 승격).
6. **이중 서버**: 라이브 엔트리포인트는 `until/asgi.py`(FastAPI). 로컬 CLI 서버는 `until/web.py`의 stdlib 핸들러. **새 라우트·수정은 양쪽 모두** 배선한다(불가피하게 한쪽만이면 브리프에 명시된 쪽만).
7. **HTML 이디엄**: 페이지 조각 함수는 `until/web.py`에 두고 `_wrap(body, backend, title)`로 감싼다. 사용자 입력은 `html.escape` 필수.
8. **커밋**: 태스크당 원자 커밋(들), 한국어 요약 스타일(레포 이력 참조), 트레일러 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. **푸시 금지**(사용자 승인 관행).
9. **건드리지 말 것**: `tools/check_determinism.py`, `tools/determinism_baseline.json`, `uv.lock`, `.claude/`, `.omc/`(사용자 리뷰 대기 중인 워킹트리 변경). `git add`는 항상 파일 명시로.
10. **비밀 금지**: 토큰·키를 파일/커밋에 절대 저장 금지. pre-commit gitleaks 우회 금지.
11. **클라우드 안전**: 클라우드에 `UNTIL_CANVAS_TOKEN` 설정 금지 원칙 유지, 클라우드에서 armed 제출 불가 원칙 유지.

---

## Task 1 — 수치 날조 검증기 경고→차단 승격 (로드맵 Tier2-6)

**현황**: `until/understanding/measured_check.py`의 `find_ungrounded_measurements(body, evidence_texts, strategy, stage)`는 hdl_lab / lab_report_cycle(result)에서 근거 텍스트에 없는 단위 수치를 결정적으로 탐지한다. 현재 소비부는 `until/readiness.py:165-177`에서 **status="warn"**(실측 항목)뿐이고, legacy 파이프라인(`until/pipeline.py` ~261-277)은 프롬프트 지침(`measured_ban`)만 주입한다. 제출 게이트(`until/execution/submission_gate.py:88-92`)는 `spec.material_gap`만 보고 regex 발견은 안 본다. CLAUDE.md "🚫 수치 날조 금지"가 요구하는 사후 대조 검증기의 **차단 승격**이 미완.

**할 일**:
1. `until/readiness.py`: find_ungrounded_measurements 발견이 1건 이상이면 실측 항목 status를 "warn" → **"fail"** 로 승격(발견 0이면 기존과 동일). 기존 warn 소비부가 fail을 어떻게 다루는지 확인해 렌더가 깨지지 않게.
2. **legacy 파이프라인 사후 차단**: `until/pipeline.py`의 legacy 경로에서 생성 결과 본문에 대해 활성 전략(hdl_lab, lab_report_cycle+result)일 때 `find_ungrounded_measurements`를 호출하고, 발견 수치가 있으면 **1회 reask**(발견 수치 목록을 넣어 "근거 없는 수치를 지우고 빈칸 `[[DECISION: 실측값 입력]]`으로 남겨라" 지시). reask 후에도 남으면 해당 수치 토큰을 기계적으로 `[[DECISION: 실측값 필요 — <원래 수치> 근거 없음]]` 마커로 치환(결정적 후처리). 기존 reask 인프라(BoundaryValidator/LengthValidator reask 패턴)를 따른다. mock 백엔드에서도 계약이 성립해야 함(mock이 수치를 내지 않으면 통과 경로).
3. **제출 게이트 강화**: `submission_gate.py`의 `measured_ban` 하드 블록이 material_gap 외에 **본문 regex 발견**(evidence_texts 대비 find_ungrounded_measurements 결과 ≥1)도 차단 사유로 추가. SubmissionPlan 생성 호출부에서 evidence_texts를 전달할 수 있는지 확인하고, 없으면 옵션 인자(기본 None=기존 동작)로 후방 호환.
4. env 탈출구: `UNTIL_MEASURED_ENFORCE=0`이면 기존(경고만) 동작. 기본값은 승격(=1).
5. 테스트: `tests/test_measured_enforce.py` 신규 — (a) readiness fail 승격 (b) legacy 후처리 치환 (c) 게이트 regex 차단 (d) env=0이면 기존 동작 (e) 비활성 전략(에세이 등)은 무영향. 전부 오프라인.

**주의**: `measured_check.py` 자체(탐지 로직)는 수정 금지. unit 경로는 이미 코드 차단이므로 건드리지 않는다.

---

## Task 2 — 라이브 '채워진 양식' 404 수정 + .hwp 양식 C안 (로드맵 Tier3-12 + 실버그)

**현황**: UI `_submission_links()`(`until/web.py:1345-1380`)는 `find_form_document()` 성공 시 `/dl/{tok}.form` 버튼을 항상 노출하는데, **라이브 서버 `until/asgi.py`의 `GET /dl/{token}.{fmt}`(816-840)에는 "form" 분기가 없어 프로덕션에서 404**. legacy 서버(`web.py:2993-2999` → `until/report.py:512 write_filled_form()`)만 동작. 또한 주입은 `.hwpx`/`.docx`만 지원(`formfill.py:385-393`), `find_form_document()`(522-532)는 `.hwpx/.docx`만 필터 — 한국 대학 1위 포맷 `.hwp`(이진)는 ingest 텍스트 추출(`capture/ingest.py:197+ _read_hwp`)만 되고 채움 불가.

**할 일**:
1. **ASGI form 분기 추가**: `asgi.py`의 `/dl/{token}.{fmt}`에 `form` 분기를 추가해 legacy와 동일하게 `write_filled_form()` 경로로 다운로드되게 한다(파일명·Content-Type을 legacy와 일치).
2. **.hwp C안**: `find_form_document()`가 `.hwp`(이진) 원본도 반환하도록 확장하되, 주입 불가 포맷이므로 `write_filled_form()`(또는 상위)에서 `.hwp` 소스일 때는 **채운 값 표를 담은 .docx**를 생성해 내려준다(형식: 감지된 양식 슬롯 라벨 + 채운 값의 2열 표 — 기존 docx 생성 유틸 재사용). UI 버튼 라벨/안내에 ".hwp 양식은 채운 값 .docx로 제공 — 한글에서 열어 붙여넣고 .hwpx로 저장해 업로드하세요" 안내 문구를 노출한다.
3. 실패 시(변환 불가) 사용자 친화 에러(500 스택 노출 금지).
4. 테스트: `tests/test_formfill_hwp.py` 신규 — (a) .hwp 소스 감지→.docx 표 생성 (b) ASGI form 분기 계약(FastAPI TestClient 또는 기존 asgi 테스트 패턴 — `tests/test_cloud.py`류 참조) (c) .hwpx/.docx 기존 경로 회귀 없음.

---

## Task 3 — 다중 사용자 경합 방어: 원자적 쓰기·잠금 (로드맵 Tier3-11)

**현황(실측)**: 원자적 쓰기 패턴(tmp+`os.replace`)은 `billing.py:217-235`, `adminboard.py:169-182`, `teacher_feedback.py`, `voice_autolearn.py`, `consent.py`에 이미 존재. 갭:
- `until/execution/submit_nonce.py`의 `consume_nonce()`(47-62): 전체 읽기→메모리 수정→**plain open("w") 통째 덮어쓰기, 잠금 없음** — 동시 2요청이 둘 다 consumed=False를 볼 수 있어 리플레이 방지 목적 자체가 깨짐.
- `until/web.py:_persist_session()`(477): plain write — 크래시 시 세션 파일 손상.
- `until/profile.py:save_profile()`(63) + `merge_from_lms()`: plain write + 무잠금 RMW — /inbox 동시 호출이 서로 덮어씀.
- `until/adminboard.py record_event()`: 쓰기는 원자적이나 `counts[key]+=1` RMW 무잠금(lost update).
- 파일락 프리미티브(msvcrt/fcntl) 사용처 0.

**할 일**:
1. 공용 유틸: `until/atomicio.py` 신규 — (a) `atomic_write_bytes/json(path, data)` (tmp+os.replace, Windows 재시도 루프는 billing의 기존 구현을 일반화·이관) (b) `path_lock(path)` 컨텍스트매니저: **프로세스 내 threading.Lock(경로별) + OS 파일락(Windows msvcrt.locking / POSIX fcntl.flock, 별도 `.lock` 파일)** 이중. OS 파일락 실패 시(플랫폼 특이) threading.Lock만으로 폴백(베스트에포트, 주석으로 한계 명시).
2. 적용: `consume_nonce()`(락 안에서 RMW + 원자적 재작성), `issue_nonce()`(같은 락), `_persist_session()`(원자적 쓰기), `profile.save_profile()`/`merge_from_lms()`(락+원자적), `adminboard.record_event()`(counts RMW를 락 안으로). `billing.py`는 기존 _atomic_write_json을 atomicio로 위임(동작 불변)하거나 그대로 두되 중복 구현이면 위임.
3. 테스트: `tests/test_atomicio.py` 신규 — (a) 동시 스레드 N개가 consume_nonce 경쟁 시 정확히 1개만 성공 (b) 동시 record_event 증분 무손실 (c) 원자적 쓰기 계약(중간 상태 파일 없음) (d) 프로파일 병행 merge 무손실. 전부 스레드 기반 오프라인.

**주의**: uvicorn 다중 워커(프로세스) 한계는 파일락이 커버 — 주석·docs에 명시. 성능 민감 경로(요청당 수 회)이므로 락 범위 최소화.

---

## Task 4 — 토큰 온보딩: 실시간 검증 + 만료 UX + 격리 수정 (로드맵 Tier1-1)

**현황(실측)**: 토큰은 홈 폼 인라인 입력(`render_index()` `web.py:738`, 도움말 758-770은 딥링크 `https://myetl.snu.ac.kr/profile/settings` 안내 존재). 검증 전용 엔드포인트 없음(인박스 호출이 겸함). **ASGI 경로에서 인증 실패는 bare raise → 일반 500**(`asgi.py:547-569`, FastAPI exception_handler 미등록) — 친절한 에러 페이지 없음. 2단계 흐름용 토큰 보관 `_TOKENS` dict(`web.py:37`)는 **uid 비구분 프로세스 전역**(sid 키만) — 클라우드 멀티테넌트에서 격리 강화 여지.

**할 일**:
1. **실시간 토큰 검증 API**: `POST /api/v1/token/check`(ASGI) + legacy 동등 — body의 token으로 `CanvasApiAdapter.get_self_profile()`(`canvas_api.py:686-693`) 1콜 → 성공 시 `{ok, name, course_count}`(과목 수는 `list_courses` 1콜, 실패해도 name만으로 ok), 401/403 → `{ok:false, reason:"auth"}`, 네트워크 → `{ok:false, reason:"net"}`. 토큰은 응답·로그에 절대 미포함, 저장 안 함.
2. **홈 폼 UX**: 토큰 입력란에 "연결 확인" 버튼 + 인라인 JS(외부 라이브러리 금지, 기존 페이지 이디엄의 바닐라 JS)로 위 API 호출 → "✅ ○○님, 과목 N개 확인됐어요" / "❌ 토큰이 유효하지 않아요 — 재발급 안내" 즉시 표시. 도움말 블록에 단계별 딥링크 가이드(설정→'+ 새 액세스 토큰'→목적 입력→만료일 비움→생성→복사)를 명시적 번호 목록으로 강화.
3. **만료/인증 실패 UX(ASGI)**: FastAPI exception handler(또는 해당 라우트 try/except)로 `RuntimeError` 중 인증 실패 메시지("eTL 인증 실패")를 **친화 페이지**(만료 가능성 설명 + 재발급 딥링크 + 홈으로)로 렌더(HTTP 401). 파싱/네트워크 실패는 별도 문구(502). legacy 서버의 기존 400/502 렌더(`web.py:3292-3301`)와 문구 일치.
4. **_TOKENS 격리**: dict 키를 `(uid, sid)`로 네임스페이스(클라우드 uid 격리 관행과 일치)하고, 항목에 단조 시각 기반 TTL(예: 15분) 추가 — 만료 항목은 접근 시 폐기. 기존 로컬 흐름(uid 없음)은 uid="local"로 후방 호환.
5. 테스트: `tests/test_token_onboarding.py` 신규 — (a) token/check 성공/auth 실패/네트워크 실패 3계약(어댑터 주입/monkeypatch, 네트워크 0) (b) 응답에 토큰 문자열 미포함 (c) _TOKENS 네임스페이스·TTL (d) ASGI 인증 실패 → 401 친화 페이지.

---

## Task 5 — 베타 게이트 밖 데모 노출 (로드맵 Tier1-4 + Tier4-14)

**현황(실측)**: ASGI `operational_boundary` 미들웨어(`asgi.py:178-230`) allowlist는 `/healthz, /beta, /about, /asset/*, /admin*`(+`/`) — **`GET /demo`(정적 쇼케이스, LLM 0·크레딧 0)가 게이트에 막혀 403**. 403 페이지 `render_beta_gate()`(`web.py:880-892`)는 초대 코드 폼만 있고 데모 링크 없음.

**할 일**:
1. `/demo`를 베타 게이트 allowlist에 추가(정적 픽스처라 안전 — `until/demo_showcase.py` 재확인: LLM·크레딧·개인화 0 확인 후). `/simple?demo=1`(프리필 체험)은 게이트 유지(실 LLM 소모 경로).
2. `render_beta_gate()`에 "초대 코드가 없어도 → 작동 예시 보기(/demo)" 링크 블록 추가. 텔레메트리 consent 게이트와의 순서 상호작용 확인(데모는 consent 리다이렉트도 우회해야 자연스러움 — allowlist 처리 방식을 consent 분기에도 일관 적용).
3. 랜딩(`deploy/landing/public/index.html`)의 CTA 근처에 "체험: 작동 예시" 링크(앱 `/demo`)가 이미 있는지 확인하고 없으면 추가(히어로/CTA 이디엄 유지).
4. 테스트: 기존 `tests/test_cloud.py`(베타 게이트 케이스 있는 스위트)에 (a) 무코드 GET /demo 200 (b) 403 페이지에 /demo 링크 존재 케이스 추가(신규 스위트 대신 기존 스위트 확장 허용).

---

## Task 6 — 제출 확인 웹 배선 + armed env 실효화 (로드맵 Tier2-5)

**Spec**: `docs/superpowers/specs/2026-08-14-submission-gate-design.md` (C안). 이 태스크의 구속 권위.

**현황(실측)**: 게이트·nonce·dry-run·CLI·웹 미리보기 구현 완료. 웹 미리보기(`web.py:1403-1466`)의 '제출' 버튼은 **항상 disabled**, 실제 submit() 호출 웹 라우트 없음. `UNTIL_SUBMIT_ARMED`는 **어떤 코드도 읽지 않음**(CLI `--submit-dry-run`은 armed=False 하드코딩, `cli.py:271-286`).

**할 일**:
1. **웹 확인 제출 라우트**: `POST /submit/confirm`(ASGI+legacy) — 세션의 SubmissionPlan에 대해 confirm_nonce를 실어 `canvas_submit.submit(plan, nonce, armed=<아래 판정>)` 호출. armed 판정 = `UNTIL_SUBMIT_ARMED=1` **이고** 클라우드 모드가 아닐 때만 True(클라우드에서는 env가 있어도 강제 False + 화면에 "클라우드에선 실제 전송이 열리지 않아요" 명시 — spec §4-1). 기본은 dry-run 결과(보낼 요청 렌더)를 확인 화면에 표시.
2. **버튼 활성화**: 미리보기의 '제출' 버튼을 `plan.allowed==True`일 때만 활성(하드 블록 있으면 기존대로 disabled). 클릭 → 위 라우트. 결과 화면: dry-run이면 receipt(method/url/body) + "실 전송은 로컬에서 UNTIL_SUBMIT_ARMED=1로" 안내, live면 SubmissionReceipt 결과.
3. **CLI armed 실효화**: `--submit-dry-run`은 이름대로 dry-run 유지. 신규 `--submit-confirm`(또는 spec의 의도에 맞는 이름)이 `armed=os.getenv("UNTIL_SUBMIT_ARMED")=="1"`을 반영하되 실행 전 stdin 확인 프롬프트(y 입력) 1회 — 개발 중 실 POST 금지 원칙(spec §4 추가 안전장치)은 그대로: 테스트는 전부 FakeHTTP.
4. 감사 로그(`_until_work/submit_audit.jsonl`) 경로가 웹 라우트에서도 동일하게 append되는지 확인(미리보기의 issue=False 계약 유지 — 렌더는 부작용 0, confirm POST만 nonce 소비·감사 기록).
5. 테스트: `tests/test_submission_web.py`(기존 존재 시 확장, 없으면 신규) — (a) allowed=False면 confirm 라우트가 거부 (b) 클라우드 모드 + env=1 → armed 강제 False (c) nonce 재사용 거부 (d) dry-run receipt 렌더 (e) CLI armed env 반영·확인 프롬프트. 전부 FakeHTTP·오프라인.

---

## Task 7 — Elice 어댑터 인박스 배선 (로드맵 Tier2-8)

**현황(실측)**: `until/capture/sources/elice_api.py`(420줄, 읽기 전용 allowlist·curl 서브프로세스 전송·테스트 완료)는 **어디에서도 import되지 않음**. 인박스는 `discovery.py`의 `DiscoveryAdapter` Protocol(`list_courses(base_url)`, `list_assignments(course, base_url, bucket=)`)을 `EtlInbox`가 소비. `collect_with_materials(url, cfg, token=, adapter=, ws=)`(`web.py:3821`)가 어댑터를 선택(ws=True→Moodle, else Canvas), 선택 확장 훅은 hasattr 덕타이핑.

**할 일**:
1. `EliceAdapter`에 `DiscoveryAdapter` 프로토콜 어댑팅 레이어 추가(`elice_api.py` 내 또는 인접): `list_courses(base_url)` → 기존 코스 목록, `list_assignments(course, base_url, bucket=)` → `list_coding_assignments()` 결과를 `AssignmentRef`(canvas와 동일 데이터클래스)로 변환. 과제→URL은 기존 `exercise_url` 빌더.
2. **옵트인 배선**: env `UNTIL_ELICE=1` + `UNTIL_ELICE_TOKEN` 있을 때 인박스가 Canvas/Moodle 결과에 **Elice 코딩 과제를 병합**(과목 라벨에 "Elice" 구분 표시, 마감순 정렬 기존 로직에 합류). Elice 쪽 실패는 경고로 삼키고 주 어댑터 결과는 정상 반환(가용성 우선). `collect_with_materials`가 Elice 출처 URL을 받으면 EliceAdapter로 라우팅(`--source elice:` CLI 프리픽스도 moodle-ws: 이디엄 따라 추가).
3. 신규 의존성 0, curl 서브프로세스 유지, 읽기 전용 allowlist 불변(불변 규칙 6: 접속 방식은 어댑터 뒤).
4. 테스트: `tests/test_elice_inbox.py` 신규 — (a) 프로토콜 어댑팅(fixture JSON→AssignmentRef 변환) (b) 병합·정렬·Elice 실패 시 격리 (c) env 미설정 시 완전 무영향(기존 계약). 전송은 전부 fake(서브프로세스 호출 금지).

---

## Task 8 — PG 웹훅 스캐폴드 (로드맵 Tier3-9)

**현황(실측)**: `billing.charge(ref=)`·`add_credits(n, code=)`는 이미 멱등. 웹훅 엔드포인트는 코드베이스에 전무. 현행: 결제 링크(`UNTIL_PAY_URL`) → 운영자가 수동으로 `UNTIL_CREDIT_CODES` 발급.

**할 일**:
1. `POST /billing/webhook`(ASGI 전용, legacy 불필요): env `UNTIL_PG_WEBHOOK_SECRET` 미설정 시 **404**(존재 자체 은닉). 설정 시: 요청 body의 HMAC-SHA256 서명 헤더(`X-Until-Signature` — PSP 중립 규격, Toss/포트원 연동 시 얇은 매핑만 추가하면 되는 구조) 검증 → 페이로드 `{order_id, uid, credits}` → `add_credits(credits, code=f"pg:{order_id}")`로 **멱등 충전**(같은 order_id 재전송 무해). uid 검증: 존재하는 크레딧 네임스페이스 규칙 재사용(클라우드 사용자별 credits.json + KV `credits:{uid}` 미러 — 기존 하이드레이션 계약 유지, KV 미러 경로가 웹훅 충전에도 일관 적용되는지 확인).
2. 실패 응답: 서명 불일치 401, 스키마 불일치 400, 성공 200 `{ok, balance}`. 모든 시도 adminboard 이벤트 기록(금액·uid는 해시/열거 원칙 — 텔레메트리 스키마 원칙 준수).
3. `BILLING_CREDIT_ANALYSIS.md`(비공개 저장소)(기존)에 웹훅 연동 절 추가: PSP 실계정 연동 시 남은 일(사용자 몫) 명시.
4. 테스트: `tests/test_billing_webhook.py` 신규 — (a) secret 미설정 404 (b) 서명 불일치 401 (c) 정상 충전+잔액 (d) 같은 order_id 재전송 멱등 (e) 스키마 불량 400.

---

## Task 9 — 문체 "내 글 같나요?" 평가 루프 (로드맵 Tier2-7)

**현황(실측)**: 평점 UI는 최종 페이지의 1-5별(`_rating_html()` `web.py:2327-2344` → `POST /rate` → `feedback.append_record`)뿐. 문체(voice) 자동 학습(`voice_autolearn`) 결과물이 실제로 "내 글 같은지" 측정하는 루프 없음.

**할 일**:
1. VoiceProfile이 적용된 초안/최종 페이지에 한정해 별점 옆에 **"내 말투 같아요?" 예/아니오** 1클릭 위젯 추가(문체 표시 배지 옆, 기존 문체 끄기/다시학습 UI 인접).
2. `POST /rate/voice`(ASGI+legacy) → `FeedbackRecord`에 `voice_match: bool|None` 필드(후방 호환: 기존 레코드 None) 기록 + 텔레메트리 `review` 스테이지에 열거형 필드 1개(`voice_match: yes|no`) 추가 — `docs/TELEMETRY_SCHEMA.md`·`telemetry/schema.py` allowlist 갱신(자유 문자열 금지 원칙).
3. 세션당 1회 dedup(기존 `_RATINGS` 이디엄).
4. 테스트: 기존 feedback/telemetry 스위트에 케이스 추가 또는 `tests/test_voice_feedback.py` 신규 — (a) 기록 계약 (b) 스키마 allowlist (c) voice 미적용 세션엔 위젯 미노출.

---

## Task 10 — 품질 eval: 골든셋 확장 + 사람 채점 게이트 (로드맵 Tier1-3)

**현황(실측)**: `until/evals/`(goldens 9케이스 — 전부 양식/코위크 계열 합성 픽스처, 메트릭 10종 결정적, legacy/unit/raw 3변형 비교, `UNTIL_BACKEND`로 라이브 지원). **사람 채점 출력 없음.** 로드맵 성공 기준: "유형별 초안의 '제출 가능' 비율을 숫자로".

**할 일**:
1. **골든셋 유형 확장**: 현재 9케이스가 못 덮는 유형 축을 추가 — (a) `evidence_report`(근거 자료 있는 조사 보고서: 자료 인용 커버리지 측정) (b) `reflective_report`(성찰문: 경험 창작 금지→DECISION 빈칸 검증) (c) `problemset`(문항 슬롯: 문항별 응답 분리) (d) `hdl_lab` 스텁(실측 수치 날조 0 검증 — Task 1의 차단과 정합). 각각 in-memory 합성 픽스처(이진 커밋 금지 관행 유지), 정답 기준은 결정적 메트릭으로.
2. **사람 채점 게이트**: `run_evals.py --grade-out <dir>` — 케이스×변형별 생성물을 **자기완결 HTML 채점 시트**(과제 지문 + 생성 본문 + "제출 가능 수준인가? 예/아니오/부분" 라디오 + 메모란, 순수 정적 HTML+JS로 채점 결과를 JSON으로 내보내기 버튼)로 떨어뜨리고, `--grade-in <json>`으로 채점 JSON을 읽어 **유형별 '제출 가능' 비율 표**를 출력하는 서브커맨드. LLM 심판 없이 사람 채점이 1차(로드맵 요구).
3. 라이브 실행 절차 문서화: `docs/EVAL_PLAYBOOK.md` 신규 — 키 설정→라이브 실행→채점→비율 산출 절차(사용자가 키만 넣으면 돌릴 수 있게).
4. 테스트: `tests/test_evals_grading.py` 신규 — (a) 신규 골든 mock 경로 통과 (b) 채점 시트 생성 계약 (c) grade-in 집계 정확성. 라이브 호출은 테스트에서 금지.

---

## Task 11 — keep-warm cron + 깨우는 중 UX (로드맵 Tier1-2)

**현황(실측)**: Render 무료 티어 콜드스타트 30~50초 실측. 랜딩은 Cloudflare 정적 assets 워커(`deploy/landing/wrangler.jsonc`, main 스크립트·cron 없음). 랜딩 배포는 `cd deploy/landing && npx wrangler deploy`(wrangler 인증 유지됨).

**할 일**:
1. `deploy/landing`에 워커 스크립트 추가(`src/index.js` + wrangler.jsonc에 `"main"`·`"triggers": {"crons": ["*/10 * * * *"]}`): scheduled 핸들러가 `https://until-app.onrender.com/healthz`를 fetch(타임아웃·실패 무시, 응답 본문 미저장). fetch 핸들러는 assets 기본 서빙 유지(assets 바인딩 `ASSETS.fetch` 패스스루 — 정적 서빙 동작 불변 확인 필수).
2. **깨우는 중 UX**: 랜딩 `public/index.html`의 앱 CTA에 인라인 JS — 페이지 로드 시 `healthz`를 백그라운드 pre-ping(no-cors)하고, CTA 클릭 시 응답 전이면 버튼에 "서버 깨우는 중… (최대 40초)" 상태 표시 후 응답 오면 이동. JS 실패 시 기존 즉시 이동(프로그레시브).
3. 로컬 검증: `npx wrangler deploy --dry-run`(또는 `wrangler versions upload --dry-run` 상당)으로 구성 검증 후 **실제 `npx wrangler deploy` 1회 실행**(랜딩 배포는 기존 관행상 세션 내 수행). 배포 후 랜딩 URL 200 확인. cron 등록 여부는 wrangler 출력으로 확인.
4. Render 유료 전환/Cloudflare Containers 대안은 사용자 결정 몫 — `render.yaml`은 건드리지 않는다.
5. 테스트: 파이썬 스위트 무관(레포 테스트는 불변 통과 확인만). 워커 스크립트는 단순성 유지(외부 의존 0).

---

## Task 12 — LLM 원가·한도 전략 문서 (로드맵 Tier3-10)

**할 일**: `docs/LLM_COST_STRATEGY.md` 신규 — 현 4단 무료 사슬(Cerebras 1M tok/일 → NVIDIA Kimi → Gemini Flash → Groq 70b TPD 100k→8b 강등)의 실측 한도·품질 강등 지점 정리, 사용자 증가 시나리오별(10/100/1000 DAU) 일일 토큰 수요 추정(초안 1건당 평균 토큰은 텔레메트리/feedback 로그 필드에서 산출 가능한 식으로 명시), 유료 제공자 편입 기준(무료 사슬 소진율 임계), 사용량당 원가 모델(크레딧 1개=초안 1건 대비 제공자 원가 표), 라이트 티어링 재설계 옵션(보조 패스 경량 모델 복원 조건). 코드 변경 0. 근거 없는 수치는 "측정 필요"로 명시(날조 금지 — 아는 가격표만 인용하되 2026-08 기준 변동 가능 표기).

---

## 실행 순서·의존성

순서대로 1→12 직렬(SDD 규칙: 구현자 병렬 금지). 파일 겹침 주의:
- `web.py`/`asgi.py`는 Task 2·4·5·6·7·8·9가 순차로 만짐 — 각 태스크는 시작 전 `git status` 확인, 자기 파일만 스테이징.
- Task 1의 게이트 강화와 Task 6의 웹 배선은 같은 `submission_gate.py` 인접 — 1이 먼저.
- Task 10은 Task 1의 measured 차단과 정합(hdl_lab 골든) — 1 이후면 됨.

## 명시적 범위 밖 (사용자 개입 필요 — 완료 후 보고)

- main 푸시(→Render 자동 재배포) — 사용자 승인 후.
- 실키 라이브 eval 실행·GEPA 예산 실행.
- armed 실제출 1회 실검증(로컬, 사용자 토큰).
- PG 실계정(Toss/포트원) 연동·시크릿 발급.
- Render 유료 인스턴스 전환 결정.
- 실사용자 베타 확보·텔레메트리 실데이터(Tier4-13).
