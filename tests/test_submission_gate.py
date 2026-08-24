import datetime as _dt
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from until.execution.submission_gate import (
    SubmitTarget, submission_content, content_hash, build_submission_plan,
    SubmissionPlan, GateFinding)
from until.execution.submit_nonce import issue_nonce, consume_nonce
from until.understanding.length_target import LengthTarget
from until.capture.sources.canvas_submit import submit


class _Draft:
    def __init__(self, body):
        self.body = body
    @property
    def n_decisions(self):
        import re
        return len(re.findall(r"\[\[DECISION:", self.body))


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


def test_nonce_survives_corrupted_ledger_row():
    # 원장에 깨진 JSON 줄이 섞여 있어도 consume_nonce가 죽지 않고 정상 동작한다.
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "n.jsonl"
        issue_nonce("HASH_A", path=p, token="good")
        with p.open("a", encoding="utf-8") as f:
            f.write("깨진 줄 { not json\n")   # 손상 행 주입
        # 손상 행을 건너뛰고 유효 nonce는 정상 소비된다.
        assert consume_nonce("good", "HASH_A", path=p) is True
        assert consume_nonce("good", "HASH_A", path=p) is False
    print("OK 손상 원장 행 견고성")


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


def test_length_unmet_blocks_alone():
    # readiness의 '분량' 항목이 실제로 warn을 내도록(스텁 아님) 하한을 크게
    # 걸고 짧은 본문을 준다 — assess_readiness가 진짜로 short 판정해야 한다.
    from until.readiness import assess_readiness
    r = _ok_result()
    r.length_target = LengthTarget(unit="자", min=5000)
    r.deadline = None  # 실제 assess_readiness가 요구하는 dday_label 스텁을 피함(마감은 이 테스트 관심사 아님)
    ready = assess_readiness(r)
    assert any(it.label == "분량" and it.status == "warn" for it in ready.items), \
        "테스트 전제 실패: assess_readiness가 분량 warn을 내지 않음"
    p = _plan(r)
    assert p.allowed is False
    assert any(b.code == "length_unmet" for b in p.blocks)
    print("OK 분량 미달 단독으로 제출 차단")


def test_blocked_plan_issues_no_nonce():
    r = _ok_result()
    r.final_guard = _Guard(False)  # 하드 블록(guard_failed) 유발
    p = _plan(r)
    assert p.allowed is False
    assert p.confirm_nonce == ""
    print("OK 차단 시 확인 nonce 미발급")


def test_issue_false_skips_nonce_and_writes_no_ledger():
    # 웹 미리보기 렌더용 경로 — 허용 상태여도 issue=False면 nonce를
    # 발급하지 않고, 원장 파일에 아무것도 쓰지 않는다(새로고침 무한증가 방지).
    with tempfile.TemporaryDirectory() as d:
        np = Path(d) / "n.jsonl"
        p = build_submission_plan(
            _ok_result(), _Ref(), base_url="https://e",
            today=_dt.date(2026, 8, 14), nonce_path=np, issue=False)
        assert p.allowed is True
        assert p.confirm_nonce == ""
        assert not np.exists(), "issue=False는 nonce 원장 파일을 만들면 안 된다"
    print("OK issue=False는 nonce 미발급·원장 미기록")


def test_control_tower_block_reaches_submission_gate():
    from until.control_tower import ControlTowerReport, TowerFinding
    report = ControlTowerReport(
        "202", "blocked",
        (TowerFinding("block", "required_files_missing", "필수 첨부가 없습니다."),),
        1)
    p = _plan(_ok_result(), control_report=report)
    assert not p.allowed and p.confirm_nonce == ""
    assert any(b.code == "control:required_files_missing" for b in p.blocks)
    print("OK 관제실 차단이 제출 게이트와 nonce 발급을 중단")


def _mkplan(allowed=True, nonce="t", chash="H"):
    return SubmissionPlan(
        allowed, [] if allowed else [GateFinding("x", "차단")],
        [], "제출 본문", SubmitTarget("101", "202", "online_text_entry",
                                      "https://myetl.snu.ac.kr"),
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
        r = submit(_mkplan(), "t", armed=True, token="secret", http=fake_http,
                   audit_path=ap, nonce_path=np)
        assert r.sent and not r.dry_run and r.status == 201 and calls
        # 감사 로그 1줄 이상
        assert ap.read_text(encoding="utf-8").strip()
    print("OK 4겹 통과 시에만 live POST + 감사 로그")


def test_dry_run_does_not_consume_nonce():
    # dry-run 미리보기(armed=False)는 유효 nonce를 소비하면 안 된다 —
    # 이후 실제 armed 제출이 정상 동작해야 한다.
    from until.execution.submit_nonce import issue_nonce
    def fake_http(m, u, d, h): return 201, '{"id":1}'
    with tempfile.TemporaryDirectory() as dr:
        np = Path(dr) / "n.jsonl"; ap = Path(dr) / "a.jsonl"
        issue_nonce("H", path=np, token="t")
        pre = submit(_mkplan(), "t", armed=False, http=fake_http,
                     audit_path=ap, nonce_path=np)
        assert pre.dry_run and not pre.sent          # 미리보기
        real = submit(_mkplan(), "t", armed=True, token="secret", http=fake_http,
                      audit_path=ap, nonce_path=np)
        assert real.sent and not real.dry_run        # nonce 살아있어 실제 전송 성공
    print("OK dry-run은 nonce를 소비하지 않는다")


def test_nonce_replay_blocked_at_submit():
    # 같은 nonce로 두 번째 armed 제출은 dry-run으로 거부(단일 사용).
    from until.execution.submit_nonce import issue_nonce
    def fake_http(m, u, d, h): return 201, '{"id":1}'
    with tempfile.TemporaryDirectory() as dr:
        np = Path(dr) / "n.jsonl"; ap = Path(dr) / "a.jsonl"
        issue_nonce("H", path=np, token="t")
        first = submit(_mkplan(), "t", armed=True, token="secret", http=fake_http,
                       audit_path=ap, nonce_path=np)
        second = submit(_mkplan(), "t", armed=True, token="secret", http=fake_http,
                        audit_path=ap, nonce_path=np)
    assert first.sent and not second.sent and second.dry_run
    print("OK nonce 리플레이는 submit 레벨에서 거부")


def test_nonce_binding_expiry_and_prune():
    from until.execution.submit_nonce import issue_nonce, consume_nonce, NONCE_TTL
    with tempfile.TemporaryDirectory() as dr:
        np = Path(dr) / "n.jsonl"
        issue_nonce("H", path=np, token="bound", binding="u:s", now=100.0)
        assert not consume_nonce("bound", "H", path=np, binding="other", now=101.0)
        assert not consume_nonce("bound", "H", path=np, binding="u:s",
                                 now=100.0 + NONCE_TTL + 1)
        assert np.read_text(encoding="utf-8") == ""
    print("OK nonce는 사용자·세션 결합 + TTL 만료·정리")


def test_live_requires_token_trusted_origin_and_2xx():
    from until.execution.submit_nonce import issue_nonce
    calls = []
    def fake_http(m, u, d, h): calls.append((u, h)); return 403, "denied"
    with tempfile.TemporaryDirectory() as dr:
        np = Path(dr) / "n.jsonl"; ap = Path(dr) / "a.jsonl"
        issue_nonce("H", path=np, token="missing")
        assert submit(_mkplan(), "missing", armed=True, http=fake_http,
                      audit_path=ap, nonce_path=np).dry_run
        assert not calls
        evil = _mkplan()
        object.__setattr__(evil, "target", SubmitTarget(
            "101", "202", "online_text_entry", "https://evil.example"))
        issue_nonce("H", path=np, token="evil")
        assert submit(evil, "evil", armed=True, token="secret", http=fake_http,
                      audit_path=ap, nonce_path=np).dry_run
        assert not calls
        issue_nonce("H", path=np, token="status")
        receipt = submit(_mkplan(), "status", armed=True, token="secret",
                         http=fake_http, audit_path=ap, nonce_path=np)
        assert not receipt.sent and receipt.status == 403 and len(calls) == 1
    print("OK live는 토큰+신뢰 origin 필요, 비-2xx는 성공 아님")


if __name__ == "__main__":
    test_submission_content_prefers_final()
    test_content_hash_binds_content_and_target()
    test_nonce_single_use_and_hash_bound()
    test_nonce_survives_corrupted_ledger_row()
    test_clean_result_is_allowed()
    test_hard_blocks_each_condition()
    test_unresolved_decision_is_warning_not_block()
    test_length_unmet_blocks_alone()
    test_blocked_plan_issues_no_nonce()
    test_issue_false_skips_nonce_and_writes_no_ledger()
    test_control_tower_block_reaches_submission_gate()
    test_dry_run_is_default_no_network()
    test_armed_refuses_without_valid_nonce()
    test_armed_live_post_only_when_all_pass()
    test_dry_run_does_not_consume_nonce()
    test_nonce_replay_blocked_at_submit()
    test_nonce_binding_expiry_and_prune()
    test_live_requires_token_trusted_origin_and_2xx()
