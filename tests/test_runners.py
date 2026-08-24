"""루트 러너 스크립트 정합 스모크 — 컴파일 + demo 전체 실행(오프라인·mock).

라이브 러너(run_*_live 등)는 네트워크가 필요해 실행하지 않고, 문법·임포트 대상이
현재 API와 어긋나지 않는지 컴파일로만 확인한다. demo.py는 mock으로 끝까지 돌린다.
"""
import sys, pathlib, py_compile, subprocess, os
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ["run_etl_live.py", "run_finalize_live.py", "run_etl_inbox.py",
           "run_etl_ws_live.py", "demo.py", "run_tests.py"]


def test_scripts_compile():
    for f in SCRIPTS:
        py_compile.compile(str(ROOT / f), doraise=True)
    print(f"OK {len(SCRIPTS)} runner scripts compile")


def test_demo_runs_end_to_end():
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, str(ROOT / "demo.py")], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=env,
                       cwd=str(ROOT), timeout=180)
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-800:]
    out = r.stdout
    # 6개 샘플 전부 처리 + 재방문 시나리오 + 제출용 저장.
    assert out.count("▶ sample_") == 6
    assert "연장됨" in out            # 연장 공지 이해
    assert "재방문 시나리오" in out and "🕘 재제안" in out
    assert "완료 — 제출용 문서 6건" in out
    print("OK demo.py end-to-end (6 samples + revisit)")


def test_python_dash_m_until():
    """`python -m until`이 CLI로 동작(단축 진입점)."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-m", "until",
                        "examples/sample_assignment.txt", "--backend", "mock"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, cwd=str(ROOT), timeout=120)
    assert r.returncode == 0, r.stdout[-500:] + r.stderr[-500:]
    assert "경계선" in r.stdout and "제출 준비 점검" in r.stdout
    print("OK python -m until entrypoint")


def test_docs_code_paths_exist():
    """문서 부패 방지 — FEATURES.md의 코드 경로·심볼, examples/README의 샘플이 실존."""
    import re
    feat = (ROOT / "docs" / "FEATURES.md").read_text(encoding="utf-8")
    refs = re.findall(r"`(until/[\w/]+\.py)(?::(\w+))?`", feat)
    assert refs, "FEATURES.md에서 코드 경로를 찾지 못함(형식 변경?)"
    for path, sym in refs:
        p = ROOT / path
        assert p.exists(), f"FEATURES.md가 없는 파일 참조: {path}"
        if sym:
            assert sym in p.read_text(encoding="utf-8"), f"심볼 없음: {path}:{sym}"
    exr = (ROOT / "examples" / "README.md").read_text(encoding="utf-8")
    for name in re.findall(r"`(sample_\w+\.txt)`", exr):
        assert (ROOT / "examples" / name).exists(), f"examples/README가 없는 샘플 참조: {name}"
    print(f"OK docs-code integrity ({len(refs)} refs)")


# 저장소가 소유한 디렉터리 — 이 접두어가 붙은 문서 참조만 검사한다. 접두어 없는
# 맨 `spec.md`·`draft.md`류는 전부 **런타임 산출물 파일명**이라(작업공간에 생성됨)
# 저장소에 있을 이유가 없다. 그것까지 잡으려 들면 오탐이 실참조를 압도한다(실측:
# 고유 참조 111개 중 접두어 없는 52개가 거의 전부 산출물·테스트 픽스처였다).
_OWNED_DIRS = ("docs", "deploy", "examples", ".claude")

# 이 저장소가 만들지 않는 문서 — 외부 템플릿(oh-my-codex)이 자기 규약을 가리킨다.
# 여기 넣는 것은 "없어도 우리 잘못이 아니다"라는 선언이므로 함부로 늘리지 마라.
_EXTERNAL_DOC_REFS = frozenset({"docs/guidance-schema.md"})


def test_doc_references_resolve():
    """**역방향** 부패 방지 — 코드·문서가 가리키는 문서가 실존하는가.

    기존 `test_docs_code_paths_exist`는 문서→코드 한 방향만 본다. 그래서
    `docs/COURSE_ALGORITHMS_2026F.md`가 **한 번도 커밋되지 않았는데도** 코드·
    CLAUDE.md·서브에이전트 정의 22곳이 그 문서를 절 번호까지 붙여 가리키는 상태가
    어떤 게이트에도 안 걸렸다(2026-08-21 발견, 같은 날 재구성해 복원).

    이게 왜 조용한 사고인가: 서브에이전트 정의(`.claude/agents/until-router.md`)가
    "먼저 설계문서를 읽는다"로 시작하는데 그 문서가 없으면, 개강 후 라우팅
    재검증이 첫 줄에서 막힌다 — 그때 가서야 안다.
    """
    import re
    pattern = re.compile(
        r"(?<![\w/.-])((?:" + "|".join(re.escape(d) for d in _OWNED_DIRS)
        + r")/[A-Za-z0-9_./-]*\.md)\b")
    scanned = []
    for glob in ("*.md", "docs/**/*.md", "examples/**/*.md", ".claude/**/*.md",
                 "until/**/*.py", "tests/**/*.py", "tools/**/*.py", "run_tests.py"):
        scanned += [q for q in ROOT.glob(glob) if q.is_file()]
    dangling, checked = [], 0
    for src in sorted(set(scanned)):
        for ref in set(pattern.findall(src.read_text(encoding="utf-8", errors="replace"))):
            if ref in _EXTERNAL_DOC_REFS:
                continue
            checked += 1
            if not (ROOT / ref).exists():
                dangling.append(f"{src.relative_to(ROOT).as_posix()} → {ref}")
    assert checked, "문서 참조를 하나도 못 찾음(형식 변경?)"
    assert not dangling, (
        "없는 문서를 가리킨다 — 문서를 만들거나 참조를 고쳐라:\n  "
        + "\n  ".join(sorted(dangling)))
    print(f"OK doc references resolve ({checked} refs)")


def test_all_test_functions_registered():
    """테스트 등록 누락 방지 — 10회차 리뷰에서 미등록 2건 발견된 것의 영구 고정.

    각 tests/test_*.py의 test_* 함수가 파일 안 어딘가에서 참조(호출 목록 포함)되고,
    모든 테스트 파일이 run_tests.py SUITES에 올라 있어야 한다."""
    import ast, re
    files = sorted((ROOT / "tests").glob("test_*.py"))
    for p in files:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        defs = {n.name for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}
        refs = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        missing = sorted(defs - refs)
        assert not missing, f"{p.name}: __main__ 목록에 없는 테스트 {missing}"
    runner = (ROOT / "run_tests.py").read_text(encoding="utf-8")
    suites = set(re.findall(r"[\"'](test_\w+)[\"']", runner))
    stems = {p.stem for p in files}
    assert stems <= suites, f"run_tests.py에 미등록 스위트: {sorted(stems - suites)}"
    assert suites <= stems, f"run_tests.py에 유령 스위트: {sorted(suites - stems)}"
    print(f"OK all test fns registered ({len(files)} files)")


def test_release_metadata_consistent():
    """릴리스·러너·현행 문서 숫자가 다시 서로 어긋나지 않게 고정한다."""
    import ast
    import re
    import tomllib
    from importlib.metadata import PackageNotFoundError
    from until.telemetry import web as telemetry_web

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    release = project["version"]
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    released = re.search(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.MULTILINE)
    assert released and released.group(1) == release, (
        f"pyproject {release} != CHANGELOG 최신 릴리스 "
        f"{released.group(1) if released else '없음'}"
    )
    with patch.object(telemetry_web, "version", side_effect=PackageNotFoundError):
        assert telemetry_web._algo_version() == release

    tree = ast.parse((ROOT / "run_tests.py").read_text(encoding="utf-8"))
    suites = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "SUITES" for target in node.targets)
    )
    total = len(suites)
    current_docs = {
        "README.md": (ROOT / "README.md").read_text(encoding="utf-8"),
        "CLAUDE.md 상단": "\n".join(
            (ROOT / "CLAUDE.md").read_text(encoding="utf-8").splitlines()[:30]
        ),
        "docs/FEATURES.md": (ROOT / "docs" / "FEATURES.md").read_text(encoding="utf-8"),
    }
    for label, text in current_docs.items():
        assert re.search(rf"{total}(?:개|스위트)", text), (
            f"{label}에 현재 테스트 수 {total} 표기가 없음"
        )
    print(f"OK release metadata ({release}, {total} suites)")


if __name__ == "__main__":
    test_scripts_compile()
    test_python_dash_m_until()
    test_docs_code_paths_exist()
    test_doc_references_resolve()
    test_all_test_functions_registered()
    test_release_metadata_consistent()
    test_demo_runs_end_to_end()
    print("\nRUNNERS TESTS PASS")
