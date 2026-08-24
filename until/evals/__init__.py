"""준수율 eval — until 파이프라인 vs raw LLM의 결정적 지표 비교.

실행: `python -m until.evals` 또는 `python run_evals.py`
백엔드는 UNTIL_BACKEND(기본 mock — 하니스 검증용, 실수치는 라이브 키로).
"""
from .runner import main  # noqa: F401
