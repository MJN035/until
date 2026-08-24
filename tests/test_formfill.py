"""양식 인식·구조 보존·셀 주입 테스트 (오프라인·결정적).

실사용 시나리오: CO-Week Academy 참가 결과 보고서 — 기본정보 표 + 수강 강의 표 +
①②③ 강의 내용 항목이 있는 hwpx 양식.
"""
import sys, pathlib, tempfile, zipfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.capture.formfill import (
    detect_form, form_directive, mapping_from_markdown, rows_from_markdown,
    build_rows_by_header, fill_form_file,
)
from until.capture.ingest import ingest_file

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"


def _p(text=""):
    t = f"<hp:t>{text}</hp:t>" if text else "<hp:t></hp:t>"
    return f"<hp:p><hp:run>{t}</hp:run></hp:p>"


def _tc(text=""):
    return f"<hp:tc><hp:subList>{_p(text)}</hp:subList></hp:tc>"


def _make_form_hwpx(d: pathlib.Path) -> pathlib.Path:
    """기본정보 표(라벨+빈칸) + 강의 표(머리행+빈 행 3) + ①②③ 항목의 최소 hwpx."""
    info = ("<hp:tbl>"
            f"<hp:tr>{_tc('이름')}{_tc()}{_tc('학번')}{_tc()}</hp:tr>"
            f"<hp:tr>{_tc('소속 대학·학과')}{_tc()}{_tc('연락처')}{_tc()}</hp:tr>"
            f"<hp:tr>{_tc('이메일')}{_tc()}{_tc('연계 교과목명')}{_tc()}</hp:tr>"
            "</hp:tbl>")
    lect = ("<hp:tbl>"
            f"<hp:tr>{_tc('분야')}{_tc('강좌명')}{_tc('수강 일시')}</hp:tr>"
            + "".join(f"<hp:tr>{_tc()}{_tc()}{_tc()}</hp:tr>" for _ in range(3))
            + "</hp:tbl>")
    body = (
        f"{_p('제5회 CO-Week Academy 참가 결과 보고서')}"
        f"<hp:p><hp:run>{info}</hp:run></hp:p>"
        f"{_p('수강 완료 강의')}"
        f"<hp:p><hp:run>{lect}</hp:run></hp:p>"
        f"{_p('수강 결과 (분량 제한: 강의당 300자 내외)')}"
        f"{_p('① 강의명: / 수강일시:')}{_p('▷ 강의 내용')}"
        f"{_p('② 강의명: / 수강일시:')}{_p('▷ 강의 내용')}"
        f"{_p('③ 강의명: / 수강일시:')}{_p('▷ 강의 내용')}"
    )
    path = d / "CO-Week_보고서양식.hwpx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Contents/section0.xml",
                   f'<hs:sec xmlns:hs="{HP}" xmlns:hp="{HP}">{body}</hs:sec>')
        z.writestr("Contents/content.hpf", "<manifest/>")  # 기타 항목 보존 확인용
    return path


def test_ingest_preserves_table_structure():
    with tempfile.TemporaryDirectory() as d:
        path = _make_form_hwpx(pathlib.Path(d))
        doc = ingest_file(path, backend="basic")
        # 표가 평탄화되지 않고 | 셀 | 구조로 보존된다.
        assert "| 이름 |" in doc.text and "| 분야 | 강좌명 | 수강 일시 |" in doc.text
        # 항목 헤드·본문 문단도 순서대로 남는다.
        assert "① 강의명" in doc.text and "③ 강의명" in doc.text
        assert doc.text.index("| 이름 |") < doc.text.index("| 분야 |")
    print("OK ingest preserves table structure")


def test_detect_form_and_directive():
    with tempfile.TemporaryDirectory() as d:
        path = _make_form_hwpx(pathlib.Path(d))
        doc = ingest_file(path, backend="basic")
        fs = detect_form(doc.text)
        assert fs.is_form and len(fs.tables) == 2
        assert any("이름" in x for x in fs.labels)
        assert len(fs.item_heads) == 3  # ①②③
        # 실행 지침: 양식 파일명 + 스캐폴드 포함.
        directive = form_directive([doc])
        assert "양식" in directive and "| 이름 |" in directive
        assert "CO-Week_보고서양식.hwpx" in directive
        # 양식 없는 문서엔 지침 없음.
        class _D:
            text = "그냥 산문 과제 설명입니다."
            source = "a.txt"
        assert form_directive([_D()]) == ""
    print("OK detect form + directive")


_DRAFT_BODY = """# 제5회 CO-Week Academy 참가 결과 보고서

| 이름 | 김민준 | 학번 | 2020-12345 |
|---|---|---|---|
| 소속 대학·학과 | 서울대학교 자유전공학부 | 연락처 | 010-1234-5678 |
| 이메일 | hong@example.com | 연계 교과목명 | (공유)빅데이터 종합설계 |

## 수강 완료 강의

| 분야 | 강좌명 | 수강 일시 |
|---|---|---|
| AI | AI 융합의 최전선 | 2026-07-01 |
| 데이터 | 데이터 윤리와 사회 | 2026-07-02 |
| 창업 | 딥테크 창업 특강 | 2026-07-03 |

① 강의명: AI 융합의 최전선 / 수강일시: 2026-07-01
▷ 강의 내용
본문입니다.
"""


def test_mapping_and_rows_from_markdown():
    m = mapping_from_markdown(_DRAFT_BODY)
    assert m["이름"] == "김민준" and m["학번"] == "2020-12345"
    assert m["연계 교과목명"] == "(공유)빅데이터 종합설계"
    rows = rows_from_markdown(_DRAFT_BODY, ["분야", "강좌명", "수강 일시"])
    assert len(rows) == 3 and rows[0][1] == "AI 융합의 최전선"
    # 값이 결정 마커면 주입하지 않는다(지어내지 않음).
    m2 = mapping_from_markdown("| 이름 | [[DECISION: 이름을 확인해 주세요]] |")
    assert "이름" not in m2
    print("OK mapping + rows from markdown")


def test_fill_hwpx_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        src = _make_form_hwpx(d)
        form_text = ingest_file(src, backend="basic").text
        out = d / "filled.hwpx"
        stats = fill_form_file(src, out, mapping_from_markdown(_DRAFT_BODY),
                               build_rows_by_header(form_text, _DRAFT_BODY))
        n = stats.cells
        assert n >= 6, stats  # 기본정보 6칸 + 강의 표 9칸
        # 채워진 파일을 다시 파싱 — 라벨 옆 칸·표 행에 값이 들어갔다.
        doc = ingest_file(out, backend="basic")
        assert "| 이름 | 김민준 |" in doc.text
        assert "hong@example.com" in doc.text
        assert "| AI | AI 융합의 최전선 | 2026-07-01 |" in doc.text
        # 원본의 다른 zip 항목은 그대로 보존된다.
        with zipfile.ZipFile(out) as z:
            assert "Contents/content.hpf" in z.namelist()
        # 원본 파일은 변경되지 않는다.
        assert "김민준" not in ingest_file(src, backend="basic").text
    print("OK fill hwpx roundtrip")


def test_pipeline_injects_form_directive_and_filled_export():
    import until.pipeline as pl
    from until.config import Config
    from until import report
    from until.llm.mock_client import MockClient

    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        src = _make_form_hwpx(d)
        captured = {}
        orig = pl.build_client

        class Rec:
            def __init__(self, inner): self.inner = inner
            def complete(self, system, user, **kw):
                if kw.get("tag") in ("execution", "execution-unit"):
                    captured.setdefault("sys", system)
                r = self.inner.complete(system, user, **kw)
                # mock 초안 대신 양식 구조를 갖춘 본문을 흉내(양식 주입 경로 검증용).
                r.text = _DRAFT_BODY + "\n[[DECISION: 강의에서 가장 인상 깊었던 점 하나: ___]]"
                return r

        pl.build_client = lambda backend, model=None: Rec(MockClient())
        try:
            cfg = Config(); cfg.backend = "mock"
            cfg.pipeline_mode = "legacy"  # legacy 주입 기제 검증(8/14 unit 기본 전환 후 고정)
            res = pl.run([str(src)], cfg)
        finally:
            pl.build_client = orig
        # 실행 시스템 프롬프트에 양식 지침 + 스캐폴드가 주입됐다.
        assert "양식 준수" in captured["sys"] and "| 이름 |" in captured["sys"]
        # 초안의 값이 원본 hwpx 셀로 주입돼 '원본 형식 그대로' 내보내진다.
        got = report.write_filled_form(res, d / "out.hwpx")
        assert got is not None
        out, stats = got
        assert out.exists() and stats.cells >= 6
        text = ingest_file(out, backend="basic").text
        assert "| 이름 | 김민준 |" in text
    print("OK pipeline form directive + filled export")


def test_fill_item_paragraphs_roundtrip():
    """갭 3 — 표 칸만이 아니라 ①② 서술 자리(표 밖 문단)에도 본문이 들어간다."""
    body_1 = "첫 강의에서 배운 내용을 충분히 정리한 문장이다. 근거와 함께 서술한다."
    body_2 = "둘째 강의 본문. 관찰과 적용 계획까지 이어서 쓴 문단이다."
    items = [
        ("① 강의명: AI 융합의 최전선 / 수강일시: 2026-07-01",
         f"▷ 강의 내용\n{body_1}\n{body_2}"),
        ("② 강의명: 데이터 윤리와 사회 / 수강일시: 2026-07-02",
         f"▷ 강의 내용\n{body_1}"),
    ]
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        src = _make_form_hwpx(d)
        out = d / "filled.hwpx"
        stats = fill_form_file(src, out, {}, item_bodies=items)
        # 항목 2개에 본문 주입(헤드 갱신 + 본문 문단), ③은 초안에 없어 유지.
        assert stats.items == 2 and stats.paragraphs >= 3, stats
        text = ingest_file(out, backend="basic").text
        assert "① 강의명: AI 융합의 최전선 / 수강일시: 2026-07-01" in text
        assert body_1 in text and body_2 in text
        # 본문이 헤드/▷ 다음 위치에 들어갔다(항목 순서 보존).
        assert text.index("① 강의명") < text.index(body_1) < text.index("② 강의명")
        # ③ 자리(빈 헤드)는 원본 그대로 남는다.
        assert "③ 강의명: / 수강일시:" in text
    print("OK item paragraph injection roundtrip")


def test_form_fidelity_check():
    from until.capture.formfill import check_form_fidelity
    with tempfile.TemporaryDirectory() as d:
        src = _make_form_hwpx(pathlib.Path(d))
        form_text = ingest_file(src, backend="basic").text
        # 양식 구조를 유지한 초안 — ok + '다시 볼 필요 없어요' 근거 문구.
        good = _DRAFT_BODY + "\n② 강의명: B\n③ 강의명: C\n"
        fid = check_form_fidelity(form_text, good)
        assert fid is not None and fid.ok, (fid.missing_labels, fid.missing_items)
        assert "원본 양식 구조 유지" in fid.message
        # 항목·라벨이 빠진 초안 — 누락을 지목.
        bad = "그냥 산문으로 쓴 결과 보고서입니다."
        fid2 = check_form_fidelity(form_text, bad)
        assert fid2 is not None and not fid2.ok
        assert fid2.missing_labels and any("①" in x for x in fid2.missing_items)
        # 양식 아닌 문서는 None(점검 자체를 만들지 않음).
        assert check_form_fidelity("산문 과제 설명", "본문") is None
    print("OK form fidelity check")


def test_readiness_has_form_item():
    # 양식 첨부가 있으면 준비 점검에 '양식' 항목이 뜬다(근거 표시 — P1-4).
    import until.pipeline as pl
    from until.config import Config
    from until.llm.mock_client import MockClient
    from until.readiness import assess_readiness
    with tempfile.TemporaryDirectory() as d:
        src = _make_form_hwpx(pathlib.Path(d))
        orig = pl.build_client

        class Rec:
            def __init__(self, inner): self.inner = inner
            def complete(self, system, user, **kw):
                r = self.inner.complete(system, user, **kw)
                r.text = _DRAFT_BODY + "\n② 강의명: B\n③ 강의명: C\n" \
                    + "\n[[DECISION: 가장 인상 깊었던 점: ___]]"
                return r

        pl.build_client = lambda backend, model=None: Rec(MockClient())
        try:
            cfg = Config(); cfg.backend = "mock"
            res = pl.run([str(src)], cfg)
        finally:
            pl.build_client = orig
        rd = assess_readiness(res)
        form_items = [i for i in rd.items if i.label == "양식"]
        assert form_items and form_items[0].status == "ok"
        assert "원본 양식 구조 유지" in form_items[0].message
    print("OK readiness includes form fidelity item")


if __name__ == "__main__":
    test_ingest_preserves_table_structure()
    test_detect_form_and_directive()
    test_mapping_and_rows_from_markdown()
    test_fill_hwpx_roundtrip()
    test_pipeline_injects_form_directive_and_filled_export()
    test_fill_item_paragraphs_roundtrip()
    test_form_fidelity_check()
    test_readiness_has_form_item()
    print("\nFORMFILL TESTS PASS")
