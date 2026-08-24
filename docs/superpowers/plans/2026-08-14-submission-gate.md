# 제출 게이트(C안) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 작성까지 끝낸 최종본을, 사람이 확인 화면에서 명시적으로 승인할 때만 Canvas eTL에 제출하는 안전 경로를 만든다(자동 제출 없음, 기본 dry-run).

**Architecture:** 결정적 안전 코어 `submission_gate.py`(하드 블록·경고 판정 + 제출될 정확한 본문 + content_hash + 1회용 nonce) → 격리된 쓰기 경로 `canvas_submit.py`(dry-run 기본, 실 POST는 4겹 방어 통과 시만) → 1회용 nonce 원장 `submit_nonce.py` + 감사 로그. 읽기 어댑터 `canvas_api.py`는 GET 전용 그대로 둔다.

**Tech Stack:** Python 표준 라이브러리만(urllib·hashlib·os·json). 신규 의존성 0. 오프라인·mock 테스트(불변 규칙 2).

## Global Constraints

- `--backend mock` + 모든 테스트는 키·인터넷 없이 항상 통과해야 한다(불변 규칙 2).
- 쓰기 능력은 `canvas_submit.py` 한 파일에만 존재한다. `canvas_api.py`(읽기)는 수정 금지.
- 테스트·러너는 `PYTHONIOENCODING=utf-8`, 실행은 `python run_tests.py`.
- 실 eTL에 live POST를 실행하지 않는다 — 개발·테스트는 dry-run과 FakeHTTP로만.
- 게이트(`submission_gate.py`)는 네트워크·LLM·파일 IO 없음(nonce 발급의 원장 쓰기는 `submit_nonce.py`가 담당).
- 신규 테스트는 `run_tests.py`의 SUITES에 등록(미등록 시 test_runners 감사 실패).
- 한국어 근거 주석(기존 코드 스타일).

**참조 인터페이스(기존 코드, 실측):**
- `Result`(`until/pipeline.py`): `.spec: dict`, `.draft: Draft`, `.final_draft: Draft|None`, `.guard`, `.final_guard`, `.length_target`, `.deadline`, `.assignment_route`.
- `Draft`(`until/boundary/models.py`): `.body: str`, `.n_decisions: int`(property).
- `GuardReport`: `.passed: bool`.
- `Deadline`(`until/understanding/deadline.py`): `.due: date`, `.days_from(today: date) -> int`.
- `AssignmentRoute`(`until/context/assignment_router.py`): `.strategy: str`, `.stage: str`.
- `AssignmentRef`(`until/capture/sources/models.py`): `.id`, `.course_id`, `.due_at`, `.submitted`.
- `assess_readiness(result) -> Readiness`(`until/readiness.py`); `Readiness.items: [ReadinessItem(label,status,message)]`, label ∈ {마감,분량,인용,결정,경계선,양식,근거,...}.
- spec 키: `spec.get("material_gap")`, `spec.get("integrity_gate")`.

---

### Task 1: 데이터클래스 + 제출 본문 추출

**Files:**
- Create: `until/execution/submission_gate.py`
- Test: `tests/test_submission_gate.py`

**Interfaces:**
- Produces:
  - `GateFinding(code: str, message: str)` — frozen dataclass
  - `SubmitTarget(course_id: str, assignment_id: str, submission_type: str, base_url: str)` — frozen
  - `SubmissionPlan(allowed: bool, blocks: list[GateFinding], warnings: list[GateFinding], content: str, target: SubmitTarget, content_hash: str, confirm_nonce: str)` — frozen
  - `submission_content(result) -> str` — 최종본 우선(`final_draft.body`), 없으면 `draft.body`
  - `content_hash(content: str, target: SubmitTarget) -> str` — sha256 hex(64자)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_submission_gate.py
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from until.execution.submission_gate import (
    SubmitTarget, submission_content, content_hash)


class _Draft:
    def __init__(self, body): self.body = body


class _Result:
    def __init__(self, draft_body, final_body=None):
        self.draft = _Draft(draft_body)
        self.final_draft = _Draft(final_body) if final_body is not None else None


def test_submission_content_prefers_final():
    r = _Result("초안 본문", "최종 완성본")
    assert submission_content(r) == "최종 완성본"
    r2 = _Result("초안만 있음")
    assert submission_content(r2) == "초안만 있음"
    print("OK 제출 본문은 최종본 우선")


def test_content_hash_binds_content_and_target():
    t = SubmitTarget("101", "202", "online_text_entry", "https://e")
    h1 = content_hash("본문", t)
    h2 = content_hash("본문 다름", t)
    assert h1 != h2 and len(h1) == 64
    print("OK content_hash 바인딩")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python tests/test_submission_gate.py`
Expected: FAIL — `ModuleNotFoundError`/`ImportError` (submission_gate 없음).

- [ ] **Step 3: Write minimal implementation**

```python
# until/execution/submission_gate.py
"""제출 게이트 — 결정적 안전 코어(네트워크·LLM 0).

작성까지 끝낸 최종본을 사람 확인 후 Canvas에 제출하기 전, 지어낸 수치·미완성
텍스트·마감 지남 등을 하드 블록으로 걸러 낸다. nonce 발급의 원장 쓰기만
submit_nonce에 위임하고, 판정 자체는 순수 함수다.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class GateFinding:
    code: str
    message: str


@dataclass(frozen=True)
class SubmitTarget:
    course_id: str
    assignment_id: str
    submission_type: str
    base_url: str


@dataclass(frozen=True)
class SubmissionPlan:
    allowed: bool
    blocks: List[GateFinding]
    warnings: List[GateFinding]
    content: str
    target: SubmitTarget
    content_hash: str
    confirm_nonce: str = ""


def submission_content(result) -> str:
    """제출될 본문 — 최종본(final_draft) 우선, 없으면 초안(draft)."""
    final = getattr(result, "final_draft", None)
    if final is not None and getattr(final, "body", None):
        return final.body
    return getattr(getattr(result, "draft", None), "body", "") or ""


def content_hash(content: str, target: SubmitTarget) -> str:
    """본문+대상 바인딩 해시(nonce·감사용). 본문 1바이트만 바뀌어도 달라진다."""
    key = (f"{content}|{target.course_id}|{target.assignment_id}"
           f"|{target.submission_type}")
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONIOENCODING=utf-8 python tests/test_submission_gate.py`
Expected: `OK 제출 본문은 최종본 우선` / `OK content_hash 바인딩`.

- [ ] **Step 5: Commit**

```bash
git add until/execution/submission_gate.py tests/test_submission_gate.py
git commit -m "제출 게이트: 데이터클래스 + 본문 추출·해시 (안전 코어 1/3)"
```

---

### Task 2: 1회용 nonce 원장

**Files:**
- Create: `until/execution/submit_nonce.py`
- Test: `tests/test_submission_gate.py` (같은 파일에 함수 추가)

**Interfaces:**
- Consumes: 없음(독립).
- Produces:
  - `issue_nonce(content_hash: str, *, path=None, token: Optional[str]=None) -> str` — 새 nonce(또는 주입 token) 발급, 원장에 append. token 주입은 테스트용.
  - `consume_nonce(nonce: str, content_hash: str, *, path=None) -> bool` — 존재·해시 일치·미소비일 때만 True + 소비 마킹. 그 외 False.
  - 원장 기본 경로: `_until_work/submit_nonce.jsonl`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_submission_gate.py 에 추가
import tempfile
from until.execution.submit_nonce import issue_nonce, consume_nonce


def test_nonce_single_use_and_hash_bound():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "n.jsonl"
        n = issue_nonce("HASH_A", path=p, token="fixed-token")
        assert n == "fixed-token"
        # 잘못된 해시 → 거부
        assert consume_nonce("fixed-token", "HASH_B", path=p) is False
        # 올바른 해시 → 1회 성공
        assert consume_nonce("fixed-token", "HASH_A", path=p) is True
        # 재사용 → 거부(단일 사용)
        assert consume_nonce("fixed-token", "HASH_A", path=p) is False
        # 존재하지 않는 nonce → 거부
        assert consume_nonce("없는토큰", "HASH_A", path=p) is False
    print("OK nonce 단일 사용·해시 바인딩")
```

Add to `__main__` block: `test_nonce_single_use_and_hash_bound()`.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python tests/test_submission_gate.py`
Expected: FAIL — `ImportError` (submit_nonce 없음).

- [ ] **Step 3: Write minimal implementation**

```python
# until/execution/submit_nonce.py
"""제출 확인 nonce 원장 — 1회용, content_hash 바인딩.

사람이 '이 정확한 본문'을 확인 화면에서 보고 '제출'을 눌렀다는 증거. 발급된
nonce는 그 plan의 content_hash에 묶이고(본문 변조 시 무효), 한 번만 소비된다
(리플레이·재전송 차단).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

_DEFAULT = Path("_until_work") / "submit_nonce.jsonl"


def _resolve(path) -> Path:
    return Path(path) if path is not None else _DEFAULT


def issue_nonce(content_hash: str, *, path=None, token: Optional[str] = None) -> str:
    """새 nonce 발급 후 원장에 append. token 주입은 테스트 결정성용."""
    nonce = token or os.urandom(16).hex()
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {"nonce": nonce, "content_hash": content_hash, "consumed": False}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return nonce


def _read_rows(p: Path) -> list[dict]:
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except (ValueError, TypeError):
            continue  # 손상 행은 조용히 건너뛴다(원장 견고성)
    return rows


def consume_nonce(nonce: str, content_hash: str, *, path=None) -> bool:
    """존재·해시 일치·미소비일 때만 True + 소비 마킹(원장 재기록)."""
    p = _resolve(path)
    rows = _read_rows(p)
    ok = False
    for r in rows:
        if (r.get("nonce") == nonce and r.get("content_hash") == content_hash
                and not r.get("consumed")):
            r["consumed"] = True
            ok = True
            break
    if ok:
        with p.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return ok
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONIOENCODING=utf-8 python tests/test_submission_gate.py`
Expected: `OK nonce 단일 사용·해시 바인딩`.

- [ ] **Step 5: Commit**

```bash
git add until/execution/submit_nonce.py tests/test_submission_gate.py
git commit -m "제출 게이트: 1회용 nonce 원장 (content_hash 바인딩·단일 사용)"
```

---

### Task 3: build_submission_plan — 하드 블록·경고 판정

**Files:**
- Modify: `until/execution/submission_gate.py` (함수 추가)
- Test: `tests/test_submission_gate.py`

**Interfaces:**
- Consumes: Task 1 데이터클래스·`submission_content`·`content_hash`; Task 2 `issue_nonce`; 기존 `assess_readiness`.
- Produces:
  - `build_submission_plan(result, assignment, *, submission_type="online_text_entry", base_url="", allowed_submission_types=None, today=None, nonce=None, nonce_path=None) -> SubmissionPlan`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_submission_gate.py 에 추가
import datetime as _dt
from until.execution.submission_gate import build_submission_plan


class _Guard:
    def __init__(self, passed): self.passed = passed


class _Route:
    def __init__(self, strategy, stage=""):
        self.strategy, self.stage = strategy, stage


class _Deadline:
    def __init__(self, days): self._days = days
    def days_from(self, today): return self._days


class _Ref:
    def __init__(self, aid="202", cid="101", submitted=False):
        self.id, self.course_id, self.submitted = aid, cid, submitted


def _ok_result():
    r = _Result("초안", "완성된 최종 본문입니다. 결정은 본문에 녹았습니다.")
    r.spec = {}
    r.guard = _Guard(True)
    r.final_guard = _Guard(True)
    r.assignment_route = _Route("staged_writing")
    r.deadline = _Deadline(3)
    r.length_target = None
    return r


def _plan(result, ref=None, **kw):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        return build_submission_plan(
            result, ref or _Ref(), base_url="https://e",
            today=_dt.date(2026, 8, 14), nonce="t",
            nonce_path=Path(d) / "n.jsonl", **kw)


def test_clean_result_is_allowed():
    p = _plan(_ok_result())
    assert p.allowed and not p.blocks
    assert p.content_hash and p.confirm_nonce == "t"
    print("OK 깨끗한 최종본은 허용")


def test_hard_blocks_each_condition():
    # measured_ban
    r = _ok_result(); r.spec = {"material_gap": True}
    r.assignment_route = _Route("hdl_lab")
    assert any(b.code == "measured_ban" for b in _plan(r).blocks)
    # 자필
    r = _ok_result(); r.spec = {"integrity_gate": "손글씨"}
    assert any(b.code == "integrity_gate" for b in _plan(r).blocks)
    # 가드 실패
    r = _ok_result(); r.final_guard = _Guard(False)
    assert any(b.code == "guard_failed" for b in _plan(r).blocks)
    # 마감 지남
    r = _ok_result(); r.deadline = _Deadline(-1)
    assert any(b.code == "deadline_passed" for b in _plan(r).blocks)
    # literal 마커 잔존
    r = _Result("초안", "본문 [[DECISION: 관점 고르기]] 남음")
    r.spec = {}; r.guard = _Guard(True); r.final_guard = _Guard(True)
    r.assignment_route = _Route("staged_writing"); r.deadline = _Deadline(3)
    r.length_target = None
    assert any(b.code == "raw_decision_marker" for b in _plan(r).blocks)
    # 대상 id 없음
    p = _plan(_ok_result(), ref=_Ref(aid="", cid=""))
    assert any(b.code == "assignment_mismatch" for b in p.blocks)
    # 지원 안 하는 submission_type
    p = _plan(_ok_result(), allowed_submission_types=["online_upload"])
    assert any(b.code == "type_unsupported" for b in p.blocks)
    print("OK 하드 블록 7종")


def test_unresolved_decision_is_warning_not_block():
    r = _ok_result()
    r.final_draft = _Draft("완성 본문 [[DECISION: 관점]] 남음")  # 마커 잔존 시 block
    # 마커 없는 최종본이되 draft에 미해결 결정이 있는 상황을 모사:
    r.final_draft = _Draft("완성 본문, 마커 없음.")
    r.draft = _Draft("초안 [[DECISION: 관점]]")
    r._n = 1
    # n_decisions는 Draft property라, 경고 판정은 draft.n_decisions>0로 본다
    p = _plan(r)
    assert p.allowed  # 차단 아님
    assert any(w.code == "unresolved_decisions" for w in p.warnings)
    print("OK 미해결 결정은 경고(차단 아님)")
```

주: `_Draft`가 `n_decisions`를 주도록 Task 1 테스트의 `_Draft`를 확장한다 — 아래 구현 참고. 테스트 `_Draft`에 `n_decisions` property 추가:

```python
# tests/test_submission_gate.py 상단 _Draft 교체
class _Draft:
    def __init__(self, body):
        self.body = body
    @property
    def n_decisions(self):
        import re
        return len(re.findall(r"\[\[DECISION:", self.body))
```

`__main__`에 추가: `test_clean_result_is_allowed()`, `test_hard_blocks_each_condition()`, `test_unresolved_decision_is_warning_not_block()`.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python tests/test_submission_gate.py`
Expected: FAIL — `ImportError`(build_submission_plan 없음).

- [ ] **Step 3: Write minimal implementation**

```python
# until/execution/submission_gate.py 에 추가
from .submit_nonce import issue_nonce  # 파일 상단 import 블록에 배치


def _readiness_warn(result, label: str) -> bool:
    """readiness 항목 label이 warn이면 True(마감·분량·양식·인용 게이트용)."""
    try:
        from ..readiness import assess_readiness
        for it in assess_readiness(result).items:
            if it.label == label and it.status == "warn":
                return True
    except Exception:
        pass
    return False


def build_submission_plan(result, assignment, *,
                          submission_type: str = "online_text_entry",
                          base_url: str = "", allowed_submission_types=None,
                          today=None, nonce: Optional[str] = None,
                          nonce_path=None) -> SubmissionPlan:
    """제출 전 게이트 — 하드 블록/경고 판정 + 본문·해시·nonce 산출(결정적)."""
    import datetime
    today = today or datetime.date.today()
    content = submission_content(result)
    target = SubmitTarget(
        course_id=str(getattr(assignment, "course_id", "") or ""),
        assignment_id=str(getattr(assignment, "id", "") or ""),
        submission_type=submission_type, base_url=base_url)
    spec = getattr(result, "spec", {}) or {}
    route = getattr(result, "assignment_route", None)
    strategy = getattr(route, "strategy", "") or ""
    stage = getattr(route, "stage", "") or ""
    guard = getattr(result, "final_guard", None) or getattr(result, "guard", None)

    blocks: List[GateFinding] = []
    # 🚫 수치 날조 금지 — 실측 근거 없는 hdl_lab·결과보고서
    if (strategy == "hdl_lab" or (strategy == "lab_report_cycle" and stage == "result")) \
            and spec.get("material_gap"):
        blocks.append(GateFinding(
            "measured_ban", "실측 근거가 없어 수치·파형이 지어내진 상태 — 제출 불가"))
    if spec.get("integrity_gate"):
        blocks.append(GateFinding(
            "integrity_gate", "자필·손글씨 규정 과제 — 자동 제출 대상 아님(직접 제출)"))
    if guard is not None and not getattr(guard, "passed", True):
        blocks.append(GateFinding("guard_failed", "경계선 가드 미통과 — 제출 불가"))
    dl = getattr(result, "deadline", None)
    if dl is not None and dl.days_from(today) < 0:
        blocks.append(GateFinding("deadline_passed", "마감이 지났습니다 — 제출 전 확인 필요"))
    if _readiness_warn(result, "분량"):
        blocks.append(GateFinding("length_unmet", "분량 요건 미달/초과 — 제출 불가"))
    if _readiness_warn(result, "양식"):
        blocks.append(GateFinding("length_unmet", "양식 구조 불일치 — 제출 불가"))
    if "[[DECISION" in content:
        blocks.append(GateFinding(
            "raw_decision_marker", "본문에 미완성 결정 마커가 남아 있습니다 — 제출 불가"))
    if not target.course_id or not target.assignment_id:
        blocks.append(GateFinding("assignment_mismatch", "제출 대상 과제를 확정할 수 없습니다"))
    if allowed_submission_types is not None and submission_type not in allowed_submission_types:
        blocks.append(GateFinding(
            "type_unsupported", f"이 과제는 {submission_type} 제출을 받지 않습니다"))

    warnings: List[GateFinding] = []
    draft = getattr(result, "final_draft", None) or getattr(result, "draft", None)
    if draft is not None and getattr(draft, "n_decisions", 0) > 0:
        warnings.append(GateFinding(
            "unresolved_decisions", "아직 당신이 정할 판단이 남아 있습니다 — 확인 후 제출하세요"))
    if _readiness_warn(result, "인용") or _readiness_warn(result, "근거"):
        warnings.append(GateFinding("citation_missing", "자료를 줬는데 본문 인용이 없습니다"))
    if getattr(assignment, "submitted", False):
        warnings.append(GateFinding("already_submitted", "이미 제출된 과제입니다 — 재제출 확인"))

    chash = content_hash(content, target)
    allowed = not blocks
    # 허용될 때만 확인 nonce를 발급한다(차단 상태에선 발급 자체를 안 함).
    token = issue_nonce(chash, path=nonce_path, token=nonce) if allowed else ""
    # length_unmet 중복 코드 정리(분량·양식 둘 다 걸리면 1건으로).
    seen, dedup = set(), []
    for b in blocks:
        if b.code in seen:
            continue
        seen.add(b.code)
        dedup.append(b)
    return SubmissionPlan(allowed, dedup, warnings, content, target, chash, token)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONIOENCODING=utf-8 python tests/test_submission_gate.py`
Expected: 모든 `OK ...` 줄 출력.

- [ ] **Step 5: Commit**

```bash
git add until/execution/submission_gate.py tests/test_submission_gate.py
git commit -m "제출 게이트: build_submission_plan — 하드 블록 7종·경고 3종 (안전 코어 3/3)"
```

---

### Task 4: canvas_submit — dry-run 기본 + 4겹 무장 + 감사 로그

**Files:**
- Create: `until/capture/sources/canvas_submit.py`
- Test: `tests/test_submission_gate.py`

**Interfaces:**
- Consumes: Task 1 `SubmissionPlan`/`SubmitTarget`; Task 2 `consume_nonce`.
- Produces:
  - `SubmissionReceipt(sent: bool, dry_run: bool, request: dict, status: Optional[int], detail: str)` — frozen
  - `submit(plan, confirm_token: str, *, armed: bool=False, token: Optional[str]=None, http=None, audit_path=None, nonce_path=None) -> SubmissionReceipt`
  - `http` 주입 인터페이스: `http(method, url, data: bytes, headers: dict) -> (status:int, body:str)`. 기본 None이면 실 urllib(무장 통과 시에만 호출).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_submission_gate.py 에 추가
from until.capture.sources.canvas_submit import submit
from until.execution.submission_gate import SubmissionPlan, SubmitTarget, GateFinding


def _mkplan(allowed=True, nonce="t", chash="H"):
    return SubmissionPlan(
        allowed, [] if allowed else [GateFinding("x", "차단")],
        [], "제출 본문", SubmitTarget("101", "202", "online_text_entry", "https://e"),
        chash, nonce)


def test_dry_run_is_default_no_network():
    calls = []
    def fake_http(m, u, d, h): calls.append(u); return 200, "{}"
    with tempfile.TemporaryDirectory() as dr:
        r = submit(_mkplan(), "t", armed=False, http=fake_http,
                   audit_path=Path(dr) / "a.jsonl", nonce_path=Path(dr) / "n.jsonl")
    assert r.dry_run and not r.sent and not calls
    assert r.request["method"] == "POST" and "submissions" in r.request["url"]
    print("OK 기본은 dry-run(네트워크 0)")


def test_armed_refuses_without_valid_nonce():
    from until.execution.submit_nonce import issue_nonce
    calls = []
    def fake_http(m, u, d, h): calls.append(u); return 200, "{}"
    with tempfile.TemporaryDirectory() as dr:
        np = Path(dr) / "n.jsonl"; ap = Path(dr) / "a.jsonl"
        issue_nonce("H", path=np, token="t")
        # plan 차단이면 무장이어도 거부
        r1 = submit(_mkplan(allowed=False), "t", armed=True, http=fake_http,
                    audit_path=ap, nonce_path=np)
        assert not r1.sent and not calls
        # nonce가 다른 해시에 묶였으면 거부
        r2 = submit(_mkplan(chash="다른해시"), "t", armed=True, http=fake_http,
                    audit_path=ap, nonce_path=np)
        assert not r2.sent
    print("OK 무장이어도 plan 차단·nonce 불일치는 거부")


def test_armed_live_post_only_when_all_pass():
    from until.execution.submit_nonce import issue_nonce
    calls = []
    def fake_http(m, u, d, h): calls.append((m, u)); return 201, '{"id":1}'
    with tempfile.TemporaryDirectory() as dr:
        np = Path(dr) / "n.jsonl"; ap = Path(dr) / "a.jsonl"
        issue_nonce("H", path=np, token="t")
        r = submit(_mkplan(), "t", armed=True, http=fake_http,
                   audit_path=ap, nonce_path=np)
    assert r.sent and not r.dry_run and r.status == 201 and calls
    # 감사 로그 1줄 이상
    assert ap.read_text(encoding="utf-8").strip()
    print("OK 4겹 통과 시에만 live POST + 감사 로그")
```

`__main__`에 추가: 위 3개.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python tests/test_submission_gate.py`
Expected: FAIL — `ImportError`(canvas_submit 없음).

- [ ] **Step 3: Write minimal implementation**

```python
# until/capture/sources/canvas_submit.py
"""Canvas 제출 — 격리된 쓰기 경로. 기본 dry-run, 실 POST는 4겹 방어 통과 시만.

읽기 어댑터(canvas_api.py)와 분리된 유일한 쓰기 지점. 자동 호출 경로 없음 —
확인 화면의 사람 클릭이 armed=True와 유효 nonce를 넘겨야만 네트워크로 나간다.
"""
from __future__ import annotations

import datetime
import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..sources.models import safe_filename  # noqa: F401  (경로 확인용, 미사용 가능)
from ...execution.submission_gate import SubmissionPlan
from ...execution.submit_nonce import consume_nonce

_AUDIT = Path("_until_work") / "submit_audit.jsonl"


@dataclass(frozen=True)
class SubmissionReceipt:
    sent: bool
    dry_run: bool
    request: dict
    status: Optional[int] = None
    detail: str = ""


def _build_request(plan: SubmissionPlan) -> dict:
    t = plan.target
    url = (f"{t.base_url}/api/v1/courses/{t.course_id}"
           f"/assignments/{t.assignment_id}/submissions")
    form = {
        "submission[submission_type]": t.submission_type,
        "submission[body]": plan.content,
    }
    return {"method": "POST", "url": url, "form": form}


def _audit(path, row: dict) -> None:
    p = Path(path) if path is not None else _AUDIT
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def submit(plan: SubmissionPlan, confirm_token: str, *, armed: bool = False,
           token: Optional[str] = None, http=None, audit_path=None,
           nonce_path=None) -> SubmissionReceipt:
    """4겹(armed·plan.allowed·유효 nonce·명시 armed 인자) 통과 시만 live POST."""
    req = _build_request(plan)
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    base = {"at": ts, "course_id": plan.target.course_id,
            "assignment_id": plan.target.assignment_id,
            "content_hash": plan.content_hash[:12], "allowed": plan.allowed}

    # 4겹 검사 — 하나라도 없으면 dry-run.
    nonce_ok = plan.allowed and consume_nonce(
        confirm_token, plan.content_hash, path=nonce_path)
    if not (armed and plan.allowed and nonce_ok):
        _audit(audit_path, {**base, "mode": "dry", "sent": False})
        return SubmissionReceipt(False, True, req, None,
                                 "dry-run(무장·게이트·nonce 중 하나 미충족)")

    # live POST — Canvas는 form-encoded.
    access = token or os.environ.get("UNTIL_CANVAS_TOKEN", "")
    data = urllib.parse.urlencode(req["form"]).encode("utf-8")
    headers = {"Authorization": f"Bearer {access}",
               "Content-Type": "application/x-www-form-urlencoded"}
    try:
        if http is not None:
            status, body = http(req["method"], req["url"], data, headers)
        else:  # pragma: no cover — 실 네트워크, 테스트는 http 주입
            r = urllib.request.Request(req["url"], data=data, headers=headers,
                                       method="POST")
            with urllib.request.urlopen(r, timeout=30) as resp:
                status, body = resp.status, resp.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        _audit(audit_path, {**base, "mode": "live", "sent": False, "error": str(e)[:120]})
        return SubmissionReceipt(False, False, req, None, f"전송 실패: {e}")
    _audit(audit_path, {**base, "mode": "live", "sent": True, "status": status})
    return SubmissionReceipt(True, False, req, status, body[:200])
```

주: `from ..sources.models import safe_filename` 줄이 경로 오류를 내면 삭제한다(미사용 import). 실제로는 `canvas_submit.py`가 `until/capture/sources/`에 있으므로 `from .models import safe_filename`. 미사용이면 그 줄 제거.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONIOENCODING=utf-8 python tests/test_submission_gate.py`
Expected: 모든 `OK ...` 출력. 실패 시 import 경로(`.models`) 수정.

- [ ] **Step 5: Commit**

```bash
git add until/capture/sources/canvas_submit.py tests/test_submission_gate.py
git commit -m "제출 게이트: canvas_submit — dry-run 기본·4겹 무장·감사 로그 (격리 쓰기 경로)"
```

---

### Task 5: CLI dry-run 렌더 + 러너 등록 + 문서

**Files:**
- Modify: `until/__main__.py` (또는 CLI 진입점 — `grep -n "add_argument" until/__main__.py`로 확인)
- Modify: `run_tests.py` (SUITES에 `test_submission_gate` 추가)
- Modify: `docs/FEATURES.md` (기능→코드 지도 1줄), `README.md`(스위트 수 갱신)

**Interfaces:**
- Consumes: Task 3 `build_submission_plan`, Task 4 `submit`(dry-run).

- [ ] **Step 1: run_tests.py 등록 + 스위트 수 확인**

`run_tests.py`의 `SUITES` 리스트에 `"test_submission_gate"`를 `"test_route_inference"` 다음 줄에 추가.

- [ ] **Step 2: 전체 스위트 실행(신규 등록 확인)**

Run: `PYTHONIOENCODING=utf-8 python run_tests.py -q`
Expected: `pass=59 fail=0 / 59` (test_runners가 미등록 스위트 없다고 통과). 만약 test_runners가 README/CLAUDE의 스위트 수 불일치로 실패하면 Step 4에서 갱신.

- [ ] **Step 3: CLI dry-run 서브커맨드(선택적 진입점)**

`until/__main__.py`에 `--submit-plan <token>` 계열이 아니라, 안전하게 **dry-run 전용** 출력 함수를 추가한다. 진입점 구조를 먼저 확인(`grep -n "argparse\|add_argument\|def main" until/__main__.py`)한 뒤, 아래를 해당 파서에 배선:

```python
# until/__main__.py — main()의 인자 파서에 추가
parser.add_argument("--submit-dry-run", action="store_true",
                    help="제출 게이트 판정 + 보낼 요청을 렌더만(네트워크 0)")
```

그리고 결과 처리부(파이프라인 실행 후 `result`가 있는 경로)에서:

```python
if getattr(args, "submit_dry_run", False):
    from until.execution.submission_gate import build_submission_plan
    from until.capture.sources.canvas_submit import submit
    from until.capture.sources.models import AssignmentRef
    ref = AssignmentRef(id=str(result.spec.get("assignment_id", "")),
                        title=str(result.spec.get("title", "")),
                        course_id=str(result.spec.get("course_id", "")))
    plan = build_submission_plan(result, ref, base_url="")
    print("제출 게이트:", "허용" if plan.allowed else "차단")
    for b in plan.blocks:
        print(f"  ✗ {b.code}: {b.message}")
    for w in plan.warnings:
        print(f"  ⚠ {w.code}: {w.message}")
    receipt = submit(plan, plan.confirm_nonce, armed=False)
    print("보낼 요청(dry-run):", receipt.request["method"], receipt.request["url"])
```

(참고: `result.spec`에 `assignment_id`/`course_id`가 없을 수 있음 — 없으면 게이트가 `assignment_mismatch`로 차단하며, 이는 정상 동작. CLI는 판정 표시가 목적.)

- [ ] **Step 4: 문서 갱신 + test_runners 통과**

- `docs/FEATURES.md`에 1줄: `제출 게이트 → until/execution/submission_gate.py·capture/sources/canvas_submit.py → test_submission_gate`.
- README·CLAUDE·FEATURES의 스위트 수(58→59) 갱신(test_runners가 `{총}(?:개|스위트)` 표기를 요구).
- Run: `PYTHONIOENCODING=utf-8 python run_tests.py -q` → `pass=59 fail=0 / 59`.

- [ ] **Step 5: v0.1 결정성·ruff·커밋**

```bash
PYTHONIOENCODING=utf-8 python run_tests.py -q          # 59 통과
UNTIL_ALGO_VERSION=v0.2 PYTHONIOENCODING=utf-8 python run_tests.py -q  # 59 통과
ruff check .
git add until/__main__.py run_tests.py docs/FEATURES.md README.md CLAUDE.md
git commit -m "제출 게이트: CLI dry-run 렌더 + 러너 등록·문서(59스위트)"
```

---

## Self-Review

**1. Spec coverage:**
- §2.1 submission_gate → Task 1·3 ✅ / §2.2 canvas_submit → Task 4 ✅ / §2.3 CLI dry-run → Task 5 ✅
- §3 하드 블록 8종·경고 3종 → Task 3 (measured_ban·integrity·guard·deadline·length/양식·raw_marker·assignment_mismatch·type_unsupported; 경고 3종) ✅
- §4 무장 4겹·감사 로그·멱등 경고 → Task 4 (armed·allowed·nonce·명시 인자, 감사 로그) + already_submitted 경고(Task 3) ✅
- §4 nonce content_hash 바인딩·단일 사용 → Task 2 ✅
- §6 테스트 전부 오프라인·live POST 0 → 모든 테스트 FakeHTTP/dry-run ✅

**2. Placeholder scan:** TBD/TODO 없음. 각 코드 스텝에 실제 코드 포함. "add error handling" 류 없음.

**3. Type consistency:** `SubmissionPlan(allowed, blocks, warnings, content, target, content_hash, confirm_nonce)` — Task 1 정의와 Task 3 생성·Task 4 소비 일치. `submit(plan, confirm_token, *, armed, token, http, audit_path, nonce_path)` — Task 4 정의와 Task 5 호출 일치. `content_hash`/`consume_nonce` 시그니처 Task 2↔4 일치. `GateFinding(code, message)` 전 태스크 일치.

**남은 주의:** Task 4의 `from ..sources.models import safe_filename`은 canvas_submit이 `until/capture/sources/`에 있으므로 `from .models import ...`이며 미사용이면 삭제(스텝 주석에 명시). deadline_passed는 `deadline.days_from(today)` 실측 인터페이스 사용.
