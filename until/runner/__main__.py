"""`python -m until.runner` — 코드 실행 러너 서비스."""
import sys

from .service import main

if __name__ == "__main__":
    sys.exit(main())
