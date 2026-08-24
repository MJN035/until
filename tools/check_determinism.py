"""결정성 게이트 — 같은 입력을 2회 돌려 SHA-256이 일치하는지, 그리고 v0.1 다이제스트가
기준선에서 바뀌지 않았는지 검사한다.

  python tools/check_determinism.py              # v0.1·v0.2 둘 다 검사(기본)
  python tools/check_determinism.py --version v0.1
  python tools/check_determinism.py --update     # 기준선 갱신(의도한 변경일 때만!)

왜 필요한가
-----------
`UNTIL_ALGO_VERSION=v0.2`를 건드릴 때 v0.1이 바이트 단위로 그대로인지 확인하는 규칙이
지금까지 **사람 기억**에 의존했다. 이 스크립트가 그걸 기계로 강제한다. 8월은 algo_version을
동결하고 측정하는 달이므로, v0.1 다이제스트가 바뀌면 그 달의 백테스트가 전부 무의미해진다.

입력은 `examples/sample_*.txt`(커밋된 픽스처)만 쓴다 — 개인 코퍼스(`_until_work/`)는
gitignore 영역이라 CI에서 존재하지 않는다.

날짜 의존 필드(마감 D-day 등)는 다이제스트에서 **의도적으로 제외**한다. 오늘 날짜가 바뀌면
값이 달라지는 게 정상이라, 포함하면 매일 실패하는 쓸모없는 게이트가 된다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASELINE = Path(__file__).resolve().parent / "determinism_baseline.json"

SAMPLES = [
    "examples/sample_assignment.txt",
    "examples/sample_problemset.txt",
    "examples/sample_code.txt",
    "examples/sample_report.txt",
    "examples/sample_presentation.txt",
    "examples/sample_inquiry.txt",
    # v0.2 신설 경로를 실제로 태우는 픽스처 — 이게 없으면 v0.1/v0.2 다이제스트가
    # 똑같이 나와서 게이트가 v0.2 회귀를 하나도 못 잡는다(2026-08-14 실측 확인).
    "examples/sample_hdl_lab.txt",          # -> hdl_lab
    "examples/sample_lab_pre.txt",          # -> lab_report_cycle(pre)
    "examples/sample_lab_result.txt",       # -> lab_report_cycle(result)
    "examples/sample_textbook_task.txt",    # -> textbook_problem_set
]

# 날짜에 따라 정상적으로 값이 바뀌는 필드 — 다이제스트에서 제외한다.
_DATE_DEPENDENT = {"deadline", "d_day", "days_left", "due", "extended", "time_str"}


def _stable(obj, _depth: int = 0):
    """어떤 객체든 정렬된 순수 JSON 값으로 환원한다(집합·객체 순서 비결정성 제거)."""
    if _depth > 8:
        return "<depth>"
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_stable(x, _depth + 1) for x in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(_stable(x, _depth + 1) for x in obj)
    if isinstance(obj, dict):
        return {str(k): _stable(v, _depth + 1)
                for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))
                if str(k) not in _DATE_DEPENDENT}
    d = getattr(obj, "__dict__", None)
    if d:
        return {str(k): _stable(v, _depth + 1)
                for k, v in sorted(d.items(), key=lambda kv: str(kv[0]))
                if not str(k).startswith("_") and str(k) not in _DATE_DEPENDENT}
    return repr(obj)


def _fingerprint_one(path: str) -> dict:
    """샘플 1건을 mock으로 돌리고 날짜 무관 지문을 뽑는다."""
    from until.config import Config
    from until.pipeline import run

    res = run([path], Config(backend="mock"))
    spec = res.spec if isinstance(res.spec, dict) else {}
    draft = getattr(res, "draft", None)
    guard = getattr(res, "guard", None)

    return {
        # 절대 경로를 넣으면 다이제스트가 머신·체크아웃 위치에 묶인다(CI·팀원 3인 전부 불일치) —
        # 레포 상대 POSIX 경로로 고정한다.
        "sample": Path(path).resolve().relative_to(ROOT).as_posix(),
        "task_type": spec.get("task_type"),
        "route": _stable(getattr(res, "assignment_route", None)),
        "body": getattr(draft, "body", "") or "",
        "guard": _stable(guard),
        "length_target": _stable(getattr(res, "length_target", None)),
        "units": [_stable(u) for u in (getattr(res, "units", None) or [])],
        "sources": list(getattr(res, "sources", None) or []),
        "content_elements": [_stable(c) for c in (getattr(res, "content_elements", None) or [])],
        "suggested_prompts": list(getattr(res, "suggested_prompts", None) or []),
        "capture_warnings": list(getattr(res, "capture_warnings", None) or []),
    }


def digest_for(version: str) -> str:
    os.environ["UNTIL_ALGO_VERSION"] = version
    # 캐시된 모듈이 이전 버전 상수를 붙들고 있지 않도록 until 패키지를 비운다.
    for name in [m for m in list(sys.modules) if m == "until" or m.startswith("until.")]:
        del sys.modules[name]

    payload = []
    for s in SAMPLES:
        p = ROOT / s
        if not p.exists():
            print(f"  [skip] {s} 없음")
            continue
        payload.append(_fingerprint_one(str(p)))

    if not payload:
        raise SystemExit("샘플을 하나도 못 읽었다 — examples/ 확인 필요")

    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def check(version: str) -> tuple[bool, str]:
    a = digest_for(version)
    b = digest_for(version)          # 같은 프로세스 안에서 2회 — 실행 간 비결정성 검출
    if a != b:
        print(f"[FAIL] {version}: 같은 입력 2회 실행이 다른 결과 — 비결정성 존재")
        print(f"       1회차 {a}\n       2회차 {b}")
        return False, a
    print(f"[ OK ] {version}: 2회 실행 일치  sha256={a[:16]}…")
    return True, a


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", action="append", dest="versions",
                    help="검사할 algo_version (반복 지정 가능). 기본 v0.1 v0.2")
    ap.add_argument("--update", action="store_true",
                    help="기준선을 현재 값으로 갱신한다. 의도한 알고리즘 변경일 때만 쓸 것.")
    args = ap.parse_args()
    versions = args.versions or ["v0.1", "v0.2"]

    base = {}
    if BASELINE.exists():
        base = json.loads(BASELINE.read_text(encoding="utf-8"))

    ok = True
    now = {}
    for v in versions:
        stable, dg = check(v)
        ok = ok and stable
        now[v] = dg

    if args.update:
        base.update(now)
        BASELINE.write_text(json.dumps(base, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"\n기준선 갱신됨 → {BASELINE.name}")
        print("⚠ 이 파일 변경은 '알고리즘 출력이 바뀌었다'는 뜻이다. 커밋 메시지에 이유를 남겨라.")
        return 0 if ok else 1

    for v, dg in now.items():
        want = base.get(v)
        if want is None:
            print(f"[warn] {v}: 기준선 없음 — `--update`로 최초 1회 기록해라")
            continue
        if want != dg:
            ok = False
            print(f"[FAIL] {v}: 기준선과 다르다 — 알고리즘 출력이 바뀌었다")
            print(f"       기준선 {want}\n       현재   {dg}")
            if v == "v0.1":
                print("       ⚠ v0.1은 동결 대상이다. v0.2 작업이 v0.1로 새어나갔는지 확인해라.")
        else:
            print(f"[ OK ] {v}: 기준선 일치")

    print("\n==== " + ("PASS" if ok else "FAIL") + " ====")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
