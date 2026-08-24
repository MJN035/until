"""응답 단위 분할 테스트 (오프라인·결정적) — 논리구조 재설계 3단계."""
import sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.execution.units import ResponseUnit, derive_units, render_units
from until.capture.ingest import ingest_file

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_formfill import _make_form_hwpx


class _Doc:
    def __init__(self, text): self.text = text; self.source = "노트.txt"


_NOTE = """[수강 확인 내역]
1) 분야 AI · 강좌명 '생성형 인공지능과 산업의 재편' · 2026-07-01 10:00
2) 분야 데이터 · 강좌명 '데이터 윤리와 프라이버시' · 2026-07-02 14:00
3) 분야 창업 · 강좌명 '딥테크 창업: 연구를 제품으로' · 2026-07-03 16:00
"""


def test_unit_count_from_form():
    # 단위 개수는 양식(서술 항목 ①②③)에서 유도 — 모델 임의 결정 금지.
    with tempfile.TemporaryDirectory() as d:
        form_text = ingest_file(_make_form_hwpx(pathlib.Path(d)),
                                backend="basic").text
    units = derive_units([_Doc(_NOTE)], form_text, slots=[], length_target=None)
    assert len(units) == 3
    # 제목·메타는 자료의 강의 라인에서 붙는다.
    assert units[0].title == "생성형 인공지능과 산업의 재편"
    assert units[0].meta.get("분야") == "AI"
    assert units[1].meta.get("수강 일시", "").startswith("2026-07-02")
    assert units[0].mark == "①" and units[2].mark == "③"
    print("OK unit count from form + titles from source lines")


def test_prose_falls_back_to_single_unit():
    # 양식 없는 산문 과제 → 단위 1개(기존 통짜 동작과 동등 — 회귀 방지).
    units = derive_units([_Doc("자유 주제 에세이를 작성하시오.")], "",
                         slots=[], length_target=None)
    assert len(units) == 1 and units[0].index == 1
    print("OK prose = single unit (regression-safe)")


def test_override_and_form_filled_rows():
    # 사용자 지정 개수가 최우선.
    units = derive_units([_Doc(_NOTE)], "", slots=[], length_target=None,
                         n_override=2)
    assert len(units) == 2
    # 양식 표에 이미 채워진 행이 있으면 그 행이 제목·메타의 1순위.
    form_md = ("| 분야 | 강좌명 | 수강 일시 |\n|---|---|---|\n"
               "| AI | 채워진 강좌 | 2026-07-09 |\n\n① 강의명:\n▷ 강의 내용\n"
               "② 강의명:\n▷ 강의 내용\n")
    units2 = derive_units([], form_md, slots=[], length_target=None)
    assert len(units2) == 2  # ①② 항목 자리 기준
    assert units2[0].title == "채워진 강좌"
    assert units2[0].meta.get("수강 일시") == "2026-07-09"
    print("OK override + filled-row titles")


def test_render_units():
    out = render_units([ResponseUnit(index=1, title="강좌 A",
                                     meta={"분야": "AI"})])
    assert "①" in out and "강좌 A" in out and "분야 AI" in out
    print("OK render units")


if __name__ == "__main__":
    test_unit_count_from_form()
    test_prose_falls_back_to_single_unit()
    test_override_and_form_filled_rows()
    test_render_units()
    print("\nUNITS TESTS PASS")
