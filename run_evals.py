"""준수율 eval 실행 — `python run_evals.py [케이스키...]`.

until 파이프라인 vs raw LLM(비교군)을 골든셋 8케이스에 돌려 결정적 지표
(항목당 분량 준수·양식 보존·원본 주입·사실 칸 환각·reask·호출 수)를 표로 낸다.
백엔드는 UNTIL_BACKEND(기본 mock — 하니스 검증용, 실수치는 라이브 키로).
"""
import sys

from until.console import force_utf8
from until.evals import main

# Windows 기본 cp949 콘솔에서도 기호(—·⚠ 등) 때문에 실행이 죽지 않게 한다.
force_utf8()
sys.exit(main())
