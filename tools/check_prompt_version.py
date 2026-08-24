"""프롬프트 버전 규율 게이트 — 고쳤으면 버전을 올렸는지 기계가 확인한다.

`PROMPT_VERSION`은 손으로 올린다. 올리는 행위 자체가 "이건 의도한 변경"이라는
선언이기 때문이다(자동 증가는 그 선언을 없애 버린다). 그런데 손으로 하는 일은
반드시 잊힌다 — 잊었을 때 알려주는 것이 이 스크립트다.

    python tools/check_prompt_version.py            # 대조(CI·테스트가 부른다)
    python tools/check_prompt_version.py --update   # 기준선 갱신(버전 올린 뒤에만)

판정 규칙:
  · 지문이 그대로면            → 통과.
  · 지문이 바뀌고 버전도 올랐으면 → 통과(의도한 변경). --update로 기준선을 갱신한다.
  · 지문만 바뀌고 버전은 그대로면 → **실패**. 프롬프트를 고치고 버전을 안 올린 것이다.
    이 상태로 두면 나중에 "톤이 바뀐 게 모델 때문인지 프롬프트 때문인지"를
    가릴 수 없고, PHASE 3의 출처 기록이 통째로 거짓말이 된다.

기준선을 무작정 갱신하지 마라. 먼저 무엇이 왜 바뀌었는지 확인하고,
의도한 변경이면 `until/persona/versions.py`의 `PROMPT_VERSION`을 올린 뒤 갱신한다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tools" / "prompt_baseline.json"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_baseline() -> dict:
    try:
        data = json.loads(BASELINE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_baseline(current: dict) -> None:
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(current, ensure_ascii=False, indent=1,
                                   sort_keys=True) + "\n", encoding="utf-8")


def compare(current: dict, baseline: dict) -> tuple:
    """(문제 목록, 바뀐 표면 목록). 문제가 비면 통과."""
    if not baseline:
        return ["기준선이 없습니다 — `--update`로 최초 생성하세요"], []
    changed = sorted(
        key for key in set(current) | set(baseline)
        if key != "PROMPT_VERSION" and current.get(key) != baseline.get(key))
    if not changed:
        return [], []
    old_version = baseline.get("PROMPT_VERSION")
    new_version = current.get("PROMPT_VERSION")
    if old_version == new_version:
        return ([f"프롬프트 {len(changed)}곳이 바뀌었는데 PROMPT_VERSION이 "
                 f"{new_version!r} 그대로입니다"], changed)
    return [], changed


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    from until.persona.versions import prompt_surface_fingerprints

    current = prompt_surface_fingerprints()
    baseline = load_baseline()

    if "--update" in args:
        write_baseline(current)
        print(f"[ OK ] 기준선 갱신 — PROMPT_VERSION={current['PROMPT_VERSION']} "
              f"· 표면 {len(current) - 1}개")
        return 0

    problems, changed = compare(current, baseline)
    for key in changed:
        print(f"  변경: {key}  {baseline.get(key)} → {current.get(key)}")
    if problems:
        for p in problems:
            print(f"[FAIL] {p}")
        print("\n  1) 무엇이 왜 바뀌었는지 먼저 확인하세요(위 목록).")
        print("  2) 의도한 변경이면 until/persona/versions.py의 PROMPT_VERSION을 올리고")
        print("     python tools/check_prompt_version.py --update 로 기준선을 갱신하세요.")
        print("  3) 의도치 않은 변경이면 프롬프트를 되돌리세요.")
        return 1
    if changed:
        print(f"[ OK ] 프롬프트 {len(changed)}곳 변경 + 버전 상승 "
              f"({baseline.get('PROMPT_VERSION')} → {current['PROMPT_VERSION']}) — "
              "`--update`로 기준선을 갱신하세요")
        return 0
    print(f"[ OK ] 프롬프트 불변 — PROMPT_VERSION={current['PROMPT_VERSION']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
