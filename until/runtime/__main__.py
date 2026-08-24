"""`python -m until.runtime <과제파일>` — Local Agent Runtime 진입점."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
