"""AGPL 코어가 비공개 서버 계층을 import하지 않는지 강제한다.

**왜 이 시험이 있는가.** Until 코어는 AGPL-3.0으로 공개되고 서버·결제·관리자
계층은 비공개로 남는다. 이 구조는 **의존 방향이 한쪽으로만** 흐를 때만 성립한다.

    비공개 서버 → 코어   (허용)
    코어 → 비공개 서버   (금지)

방향이 뒤집히면 비공개 서버가 AGPL 저작물의 파생물이 되어 공개 의무가 그쪽으로
번질 수 있다. 지금은 저작권자가 단독이라 실무상 문제가 없지만, **외부 기여가 붙는
순간 잠긴다** — 그때는 되돌릴 수 없다. 그래서 첫날부터 건다.

**스텁과 순수 서버 모듈의 구분.** `billing`·`cloudkv`·`adminboard` 같은 이름은
이 저장소에 **무동작 스텁**으로 존재한다(운영 배포가 같은 이름으로 교체). 코어가
그 이름을 부르는 것은 인터페이스를 부르는 것이라 허용된다. 아래 목록은 코어에
스텁조차 두지 않는, 서버에만 존재해야 할 모듈이다.
"""
import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 코어에 스텁조차 두지 않는 순수 서버 계층.
FORBIDDEN = {"runner_service_private", "ops_console", "psp_client"}

# 코어에 스텁으로 존재해야 하는 모듈 — 실제 구현이 새어 들어오지 않았는지 함께 본다.
MUST_BE_STUB = {
    "billing", "cloudkv", "adminboard", "analytics", "betarequests",
    "google_auth", "kakao_auth", "personalization_board", "pg_webhook",
}


def _imported_names(tree: ast.AST) -> list[tuple[str, int]]:
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.level and n.module is None:
            out += [(a.name, n.lineno) for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.append((n.module.split(".")[-1], n.lineno))
        elif isinstance(n, ast.Import):
            out += [(a.name.split(".")[-1], n.lineno) for a in n.names]
    return out


def test_core_does_not_import_private_layer():
    bad = []
    for p in (ROOT / "until").rglob("*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for name, lineno in _imported_names(tree):
            if name in FORBIDDEN:
                bad.append(f"{p.relative_to(ROOT)}:{lineno} -> {name}")
    assert not bad, "AGPL 코어가 비공개 계층을 참조한다:\n" + "\n".join(bad)
    print("OK 코어 → 비공개 서버 import 없음")


def test_private_modules_are_stubs_not_real_implementations():
    """스텁 자리에 실제 구현이 섞여 들어오는 사고를 막는다.

    실제 구현은 네트워크·자격증명·영속 저장을 건드린다. 스텁은 그럴 이유가 없으므로
    그런 흔적이 보이면 비공개 코드가 실수로 커밋된 것이다.
    """
    smells = ("urllib.request", "http.client", "requests", "CLIENT_SECRET",
              "hmac.new", "sqlite3")
    bad = []
    for mod in sorted(MUST_BE_STUB):
        p = ROOT / "until" / f"{mod}.py"
        assert p.exists(), f"스텁이 없다(코어가 import한다): until/{mod}.py"
        src = p.read_text(encoding="utf-8")
        assert "스텁" in src, f"until/{mod}.py 에 스텁 표시가 없다"
        for s in smells:
            if s in src:
                bad.append(f"until/{mod}.py 에 실제 구현 흔적: {s!r}")
    assert not bad, "\n".join(bad)
    print(f"OK 비공개 모듈 {len(MUST_BE_STUB)}개가 전부 스텁")


def test_core_runs_without_any_credentials():
    """스텁 상태에서 과금·로그인 게이트가 코어를 막지 않는지 확인."""
    from until import billing, google_auth, kakao_auth
    assert billing.can_draft() and billing.global_can_draft()
    assert billing.remaining_credits() is None          # None = 무제한
    assert not google_auth.enabled() and not google_auth.any_enabled()
    assert not google_auth.require_login()              # 로그인 강제 안 함
    assert not kakao_auth.enabled()
    print("OK 자격증명 없이 초안 생성 경로가 열려 있음")


def test_stub_admin_gate_is_fail_closed():
    """스텁이 보안을 여는 방향으로 만들어지지 않았는지 — 관리자는 항상 닫힘."""
    from until import adminboard
    assert not adminboard.verify_admin_token("anything", "anykey")
    assert not adminboard.verify_admin_token("", "")
    print("OK 관리자 게이트 fail-closed")


if __name__ == "__main__":
    test_core_does_not_import_private_layer()
    test_private_modules_are_stubs_not_real_implementations()
    test_core_runs_without_any_credentials()
    test_stub_admin_gate_is_fail_closed()
    print("\nLICENSE BOUNDARY TESTS PASS")
