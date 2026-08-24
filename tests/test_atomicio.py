"""원자적 쓰기 + 파일 잠금 유틸(atomicio) 테스트 (오프라인·스레드 기반).

로드맵 Tier3-11 — 다중 사용자 경합 방어. 잠금 없는 RMW(읽기-수정-쓰기)가
동시 요청에서 lost update를 내는 지점(nonce 소비, 관리자 카운터, 프로필 병합)과
원자적 쓰기 계약(중간 상태 파일 없음)을 검증한다. 전부 스레드 기반·오프라인 —
sleep을 동기화에 쓰지 않고 Barrier/Event + join(timeout)만 쓴다.
"""
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from until import atomicio
from until import profile as prof
from until.execution import submit_nonce

_JOIN_TIMEOUT = 30  # 넉넉한 상한(스레드 데드락 시 무한 대기 방지) — 정상 실행은 훨씬 빠름.


def _run_all(threads):
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=_JOIN_TIMEOUT)
    for t in threads:
        assert not t.is_alive(), "스레드가 타임아웃 내에 끝나지 않음(데드락 의심)"


# ─────────────────────────────────────────────────────────────────────────────
# (a) N-스레드가 같은 nonce를 동시에 소비 시도 → 정확히 1개만 성공
# ─────────────────────────────────────────────────────────────────────────────
def test_nonce_race_exactly_one_winner():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "submit_nonce.jsonl"
        content_hash = "hash-abc-123"
        nonce = submit_nonce.issue_nonce(content_hash, path=p, token="race-token")

        N = 16
        results: list = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(N)

        def worker():
            barrier.wait(timeout=_JOIN_TIMEOUT)
            ok = submit_nonce.consume_nonce(nonce, content_hash, path=p)
            with results_lock:
                results.append(ok)

        _run_all([threading.Thread(target=worker) for _ in range(N)])

        assert len(results) == N
        assert sum(1 for r in results if r) == 1, f"승자가 1이 아님: {results}"

        # 리플레이 방어: 소비된 뒤엔 몇 번을 더 시도해도 전부 실패.
        assert submit_nonce.consume_nonce(nonce, content_hash, path=p) is False

        # 원장 자체도 손상 없이 정확히 1행, consumed=True.
        rows = submit_nonce._read_rows(p)
        assert len(rows) == 1 and rows[0]["consumed"] is True
    print("OK nonce race -> exactly one winner (no replay)")


def test_nonce_issue_and_consume_survive_concurrent_appends():
    """서로 다른 nonce를 동시에 발급 + 소비해도 원장이 유실 없이 전부 남는다."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "submit_nonce.jsonl"
        N = 12
        content_hash = "hash-multi"
        nonces = [f"n-{i}" for i in range(N)]
        barrier = threading.Barrier(N)

        def issuer(i):
            barrier.wait(timeout=_JOIN_TIMEOUT)
            submit_nonce.issue_nonce(content_hash, path=p, token=nonces[i])

        _run_all([threading.Thread(target=issuer, args=(i,)) for i in range(N)])

        rows = submit_nonce._read_rows(p)
        assert {r["nonce"] for r in rows} == set(nonces), "동시 append 중 원장 행 유실"

        # 이제 각자 다른 nonce를 동시에 소비 — 전부 성공해야 한다(서로 다른 행).
        results: list = []
        results_lock = threading.Lock()
        barrier2 = threading.Barrier(N)

        def consumer(i):
            barrier2.wait(timeout=_JOIN_TIMEOUT)
            ok = submit_nonce.consume_nonce(nonces[i], content_hash, path=p)
            with results_lock:
                results.append(ok)

        _run_all([threading.Thread(target=consumer, args=(i,)) for i in range(N)])
        assert all(results) and len(results) == N
        rows = submit_nonce._read_rows(p)
        assert all(r["consumed"] for r in rows) and len(rows) == N
    print("OK concurrent issue + consume of distinct nonces (no lost rows)")


# ─────────────────────────────────────────────────────────────────────────────
# (b) 동시 record_event → counts 증분 무손실
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# (c) 원자적 쓰기 계약 — 중간(잘린/섞인) 상태 파일이 절대 관측되지 않는다
# ─────────────────────────────────────────────────────────────────────────────
def test_atomic_write_contract():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "state.bin"

        # 단발 계약: 쓰기 후 tmp 잔존 없음 + 내용 정확히 일치.
        atomicio.atomic_write_bytes(p, b"hello-atomic")
        assert p.read_bytes() == b"hello-atomic"
        assert not p.with_name(p.name + ".tmp").exists()

        atomicio.atomic_write_json(p, {"a": 1, "b": "값"})
        assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1, "b": "값"}
        assert not p.with_name(p.name + ".tmp").exists()

        # 스트레스: 라이터가 서로 다른 길이의 두 페이로드를 반복 교체하는 동안
        # 여러 리더가 계속 읽는다. 매 읽기는 두 완전한 페이로드 중 하나이거나
        # (아직 안 만들어졌으면) 파일 없음이어야 한다 — 잘리거나 섞인 상태는
        # 절대 없어야 한다(원자적 교체 계약).
        stress_path = Path(d) / "stress.bin"
        payload_a = b"A" * 50_000
        payload_b = b"B" * 61_000
        iterations = 150
        stop = threading.Event()
        violations: list = []
        violations_lock = threading.Lock()

        # 리더가 관측할 수 있는 유일한 "쓰기 전" 상태를 이미 알려진 값으로 고정
        # (그렇지 않으면 리더가 이 테스트 이전의 무관한 잔여 내용을 잡아 오탐한다).
        atomicio.atomic_write_bytes(stress_path, payload_a)

        write_errors: list = []

        def writer():
            # 예외가 나도 stop을 반드시 세운다 — 안 그러면 리더들이 영원히 돌고
            # 테스트는 '데드락 의심'으로 죽어 진짜 원인(쓰기 실패)을 가린다.
            try:
                for i in range(iterations):
                    atomicio.atomic_write_bytes(
                        stress_path, payload_a if i % 2 == 0 else payload_b)
            except atomicio.AtomicWriteError as exc:
                write_errors.append(str(exc))
            finally:
                stop.set()

        def reader():
            while not stop.is_set():
                try:
                    data = stress_path.read_bytes()
                except OSError:
                    continue
                if data not in (payload_a, payload_b):
                    with violations_lock:
                        violations.append(len(data))

        threads = [threading.Thread(target=writer)] + [
            threading.Thread(target=reader) for _ in range(4)
        ]
        _run_all(threads)

        # 계약: 리더는 완전한 페이로드만 본다. 예전에는 최후 폴백이
        # truncate 후 쓰기라서 부하가 걸리면 길이 0이 관측됐다(실측 [0,0,0]).
        assert violations == [], f"중간 상태 관측됨(길이): {violations[:10]}"
        # 교체를 못 했으면 던지고 **대상은 그대로** 둔다 — 쓰다 만 파일이 아니라
        # 이전 내용이 남아야 한다.
        if write_errors:
            assert stress_path.read_bytes() in (payload_a, payload_b), (
                "쓰기 실패 후 대상 파일이 온전하지 않다")
        assert not stress_path.with_name(stress_path.name + ".tmp").exists()
    print("OK atomic write contract (no partial/mixed state observed)"
          + (f" · 쓰기 실패 {len(write_errors)}회(계약은 유지)" if write_errors else ""))


def test_failed_replace_raises_and_leaves_target_untouched():
    """교체가 끝내 안 되면 조용히 덮어쓰지 않고 던진다.

    예전 동작(비원자적 덮어쓰기)은 그 찰나 리더에게 **길이 0**을 보여 줬다.
    세션·nonce·크레딧이 모두 이 함수를 쓰므로, 계약을 조용히 깨는 대신
    실패를 알리는 쪽이 맞다.
    """
    import os as _os
    with tempfile.TemporaryDirectory() as d:
        target = Path(d) / "kept.bin"
        atomicio.atomic_write_bytes(target, b"ORIGINAL")

        real_replace = _os.replace
        atomicio.os.replace = lambda *a, **k: (_ for _ in ()).throw(
            PermissionError("target is locked"))
        # 재시도 예산을 줄여 테스트가 2초를 기다리지 않게 한다.
        fast, slow = atomicio._FAST_RETRIES, atomicio._SLOW_RETRIES
        atomicio._FAST_RETRIES, atomicio._SLOW_RETRIES = 2, 2
        try:
            raised = False
            try:
                atomicio.atomic_write_bytes(target, b"NEW-CONTENT")
            except atomicio.AtomicWriteError:
                raised = True
            assert raised, "교체 실패인데 조용히 성공한 것처럼 굴었다"
        finally:
            atomicio.os.replace = real_replace
            atomicio._FAST_RETRIES, atomicio._SLOW_RETRIES = fast, slow

        assert target.read_bytes() == b"ORIGINAL"      # 대상은 그대로
        assert not target.with_name(target.name + ".tmp").exists()   # 찌꺼기 없음
    print("OK 교체 실패 → 예외 + 대상 보존 + tmp 정리")


# ─────────────────────────────────────────────────────────────────────────────
# (d) 동시 프로필 merge_from_lms → 서로 다른 필드 채움이 무손실
# ─────────────────────────────────────────────────────────────────────────────
def test_concurrent_profile_merge_lossless():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "profile.json"
        keys = [k for k, _, _ in prof.FIELDS]
        assert len(keys) >= 2
        barrier = threading.Barrier(len(keys))

        def worker(key):
            barrier.wait(timeout=_JOIN_TIMEOUT)
            prof.merge_from_lms({key: f"val-{key}"}, p)

        _run_all([threading.Thread(target=worker, args=(k,)) for k in keys])

        got = prof.load_profile(p)
        assert set(got.keys()) == set(keys), f"필드 유실: {sorted(got.keys())}"
        for k in keys:
            assert got[k] == f"val-{k}"
    print("OK concurrent profile merges (distinct fields) lossless")


def test_concurrent_profile_merge_respects_existing_values():
    """이미 사용자가 저장한 값은 동시 LMS 병합이 있어도 절대 덮이지 않는다."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "profile.json"
        prof.save_profile({"name": "직접 저장"}, p)
        N = 10
        barrier = threading.Barrier(N)

        def worker(i):
            barrier.wait(timeout=_JOIN_TIMEOUT)
            prof.merge_from_lms({"name": f"lms-{i}", "email": f"lms{i}@snu.ac.kr"}, p)

        _run_all([threading.Thread(target=worker, args=(i,)) for i in range(N)])

        got = prof.load_profile(p)
        assert got["name"] == "직접 저장"          # 절대 덮어쓰기 안 됨
        assert got["email"].startswith("lms")       # 빈 필드는 누군가 채움(유실 없이)
    print("OK concurrent LMS merges never overwrite user-saved values")


if __name__ == "__main__":
    test_nonce_race_exactly_one_winner()
    test_nonce_issue_and_consume_survive_concurrent_appends()
    test_atomic_write_contract()
    test_failed_replace_raises_and_leaves_target_untouched()
    test_concurrent_profile_merge_lossless()
    test_concurrent_profile_merge_respects_existing_values()
    print("\nATOMICIO TESTS PASS")
