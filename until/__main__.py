"""`python -m until <파일>` — CLI 진입점 단축(= `python -m until.cli`)."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
