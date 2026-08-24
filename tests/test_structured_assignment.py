"""네 번째·다섯 번째 알고리즘: Rmd 템플릿과 ZIP 프로젝트."""
from pathlib import Path
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from until.capture.ingest import ingest_file
from until.context.structured_assignment import (
    structured_assignment_directive,
    structured_assignment_kind,
)


def test_rmd_template_slots_preserved():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "assignment.Rmd"
        path.write_text("---\ntitle: hw\n---\n## 문제 1\n```{r}\n### Todo ###\n```\n",
                        encoding="utf-8")
        doc = ingest_file(path, backend="basic")
    assert doc.kind == "rmd-template"
    assert "## 문제 1" in doc.text and "[R 청크" in doc.text
    assert "[[ANSWER_SLOT:" in doc.text
    assert structured_assignment_kind([doc]) == "rmd"
    hint = structured_assignment_directive([doc])
    assert "수치를 지어내지" in hint and "원본 문제 순서" in hint
    print("OK Rmd 문제·청크·답안 슬롯 보존")


def test_zip_project_reads_specs_and_code_without_execution():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "starter.zip"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("README.md", "함수 solve를 구현하고 테스트하세요")
            z.writestr("src/main.py", "def solve():\n    raise NotImplementedError\n")
            z.writestr("__pycache__/bad.pyc", b"binary")
            z.writestr("../outside.py", "SHOULD_NOT_APPEAR")
        doc = ingest_file(path, backend="basic")
    assert doc.kind == "zip-project"
    assert "FILE: README.md" in doc.text and "FILE: src/main.py" in doc.text
    assert "SHOULD_NOT_APPEAR" not in doc.text and "pyc" not in doc.text
    assert structured_assignment_kind([doc]) == "zip"
    assert "절대 실행된 것으로 가정하지" in structured_assignment_directive([doc])
    print("OK ZIP 명세·소스 안전 수집, 실행 금지")


def test_extensionless_html_is_sniffed():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "질의순번 리스트"
        path.write_text("<!DOCTYPE html><html><body><h1>질의 순번</h1>2099-12345</body></html>",
                        encoding="utf-8")
        doc = ingest_file(path, backend="basic")
    assert doc.kind == "html(sniffed)"
    assert "질의 순번" in doc.text and "<html" not in doc.text.lower()
    print("OK 확장자 없는 HTML 시그니처 감지")


def test_binary_image_is_not_misread_as_text():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "result.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(64)))
        try:
            ingest_file(path, backend="basic")
        except RuntimeError as exc:
            assert "OCR" in str(exc)
        else:
            raise AssertionError("PNG binary must not be decoded as assignment text")
    print("OK 이미지 바이너리의 텍스트 오염 차단")


if __name__ == "__main__":
    test_rmd_template_slots_preserved()
    test_zip_project_reads_specs_and_code_without_execution()
    test_extensionless_html_is_sniffed()
    test_binary_image_is_not_misread_as_text()
    print("\nSTRUCTURED ASSIGNMENT TESTS PASS")
