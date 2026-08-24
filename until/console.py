"""콘솔 출력 인코딩 정규화 — Windows 기본 cp949에서 실행이 죽지 않게 한다.

배경: 이 프로그램의 출력에는 기호(— · ⚠ · ✅ · 🔒 등)가 섞여 있는데, Windows의
기본 콘솔 코드페이지는 cp949라 `print()` 한 줄에서 `UnicodeEncodeError`로 **즉사**한다.
`PYTHONIOENCODING=utf-8`을 걸면 피할 수 있지만, 처음 받아 본 사람은 그걸 모른다 —
`python demo.py`가 첫 명령인데 첫 화면에서 막히면 그걸로 끝이다.

`errors="replace"`인 이유: 콘솔이 정말 못 그리는 글자가 있어도 그 자리만 대체 문자로
떨어지고 **실행은 계속된다**. 표시 품질보다 "끝까지 돌아간다"가 우선이다.
"""
from __future__ import annotations

import sys


def force_utf8(*streams) -> None:
    """표준 출력/에러를 UTF-8로 다시 연다. 이미 UTF-8이면 사실상 무동작.

    호출부는 진입점(main) 첫 줄에서 한 번만 부르면 된다. `reconfigure`가 없는
    스트림(파이프로 갈아끼운 StringIO 등)은 조용히 건너뛴다 — 테스트에서 출력을
    가로채는 경우를 깨지 않기 위해서다.
    """
    for stream in (streams or (sys.stdout, sys.stderr)):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # 이미 닫혔거나 재설정을 지원하지 않는 스트림 — 출력 인코딩 하나 때문에
            # 프로그램을 멈출 이유는 없다.
            pass
