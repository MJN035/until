"""Offline end-to-end + BoundaryGuard tests using the Mock backend (no API key)."""
import tempfile
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.config import Config
from until.console import force_utf8
from until.pipeline import run
from until.report import render_markdown_report, write_markdown_report
from until.boundary.resolve import apply_resolution_answers, load_resolution_answers
from until.boundary.models import Draft, Resolution
from until.execution.boundary_guard import BoundaryValidator, BoundaryGuard, OnFailAction


def test_explicit_ai_prohibition_stops_before_drafting():
    from until.academic_policy import AiUseProhibitedError
    with tempfile.TemporaryDirectory() as td:
        path = pathlib.Path(td) / "assignment.txt"
        path.write_text("AI 사용 여부: 불가능. 반드시 자신의 의견을 작성할 것.",
                        encoding="utf-8")
        cfg = Config(); cfg.backend = "mock"
        try:
            run([str(path)], cfg)
            assert False, "AI 금지 과제는 초안 생성 전에 멈춰야 함"
        except AiUseProhibitedError:
            pass


def test_ai_permission_does_not_false_positive():
    from until.academic_policy import ai_use_prohibited
    class Doc:
        text = "생성형 AI 사용 가능. 사용한 경우 도구명을 밝히세요."
    assert not ai_use_prohibited([Doc()])


def test_end_to_end_with_guard():
    # legacy 기제(통짜 reask 루프) 자체를 검증 — 기본 unit 전환(8/14) 후 명시 고정.
    cfg = Config(); cfg.backend = "mock"; cfg.pipeline_mode = "legacy"
    res = run(["examples/sample_assignment.txt"], cfg)

    assert res.documents and res.documents[0].sections
    assert "deliverable" in res.spec

    # Guard: mock crosses the boundary on attempt 1, then is corrected on reask.
    assert res.guard.reasks >= 1, "가드가 1차 위반을 잡고 재요청했어야 함"
    assert res.guard.passed, "재요청 후 최종 통과해야 함"
    assert res.draft.decisions and not res.draft.crossed_boundary
    assert len(res.suggested_prompts) == len(res.draft.decisions)
    print(f"OK e2e — attempts={res.guard.attempts}, reasks={res.guard.reasks}, "
          f"decisions={res.draft.n_decisions}")


def test_validator_flags_stance_and_missing_decisions():
    bad = Draft.from_text("## 본론\n나는 Zuboff가 옳다고 본다. 따라서 결론은 정해졌다.\n")
    r = BoundaryValidator(min_decisions=1).validate(bad)
    assert not r.passed
    assert any("입장" in e for e in r.errors)      # 1인칭 단정 탐지
    assert any("결정 지점" in e for e in r.errors)  # 결정 지점 부족 탐지
    print("OK validator — errors:", len(r.errors))


def test_validator_flags_direct_human_choices():
    """찬반·선택·선호를 1인칭으로 확정하는 우회 표현도 경계선 침범이다."""
    tail = " 근거 자료를 비교하고 쟁점을 정리했다." * 20
    for sentence in [
        "나는 이 정책에 찬성한다.",
        "나는 두 대안 중 첫 번째를 선택했다.",
        "나는 공공성 중심의 관점을 지지한다.",
        "나는 효율성보다 형평성을 선호한다.",
    ]:
        draft = Draft.from_text(
            sentence + tail + "\n[[DECISION: 최종 입장은 본인이 정할 것]]\n"
        )
        result = BoundaryValidator(min_decisions=1).validate(draft)
        assert not result.passed, sentence
        assert any("입장" in error for error in result.errors), sentence
    print("OK validator rejects direct first-person choices")


def test_validator_passes_clean_draft():
    good = Draft.from_text(
        "## 본론\n자료에서 논점 A/B/C를 정리했다. " + "충분한 분량의 본문. " * 20 +
        "\n[[DECISION: 어느 논점을 핵심 논지로 세울지 — 본인 관점 필요]]\n"
    )
    r = BoundaryValidator(min_decisions=1).validate(good)
    assert r.passed, r.errors
    print("OK clean draft passes")


def test_placeholder_markers_rejected():
    d = Draft.from_text("본문." * 50 + "\n[[DECISION: ...]]\n[[DECISION: TODO]]\n")
    assert d.n_decisions == 0, "자리표시 마커는 유효 결정으로 세지 않음"
    print("OK placeholder markers rejected")


def test_validator_rejects_hanja_or_kana_mixing():
    bad = Draft.from_text(
        "## 본론\n" + "충분한 분량의 본문. " * 20 +
        "\n資料를 바탕으로 정리했다.\n"
        "[[DECISION: 핵심 논지 선택 — 본인 관점 필요]]\n"
    )
    r = BoundaryValidator(min_decisions=1).validate(bad)
    assert not r.passed
    assert any("외국 문자" in e for e in r.errors)
    print("OK validator rejects hanja/kana mixing")


def test_validator_rejects_non_korean_scripts():
    # 라이브에서 관측된 외국 문자 누수(데바나가리·키릴 등)도 잡아야 한다.
    base = "충분한 분량의 본문. " * 20 + "\n[[DECISION: 핵심 논지 선택 — 본인 관점]]\n"
    for label, bad in [("데바나가리", "계획하는 데 महत्व 하다. "),
                       ("키릴", "이것은 Привет 이다. "),
                       ("베트남어", "역사적 지역을 trực tiếp 답사한다. "),
                       ("베트남어-악센트", "이 báo cáo는 중요하다. "),  # á=U+00E1 누수
                       ("악센트라틴", "우리는 café 에 갔다. ")]:
        r = BoundaryValidator(min_decisions=1).validate(Draft.from_text(bad + base))
        assert not r.passed, label
        assert any("외국 문자" in e for e in r.errors), label
    # 한국어 + 영어 혼용은 허용(영문 약어/고유명사).
    ok = "this is OK. 도시는 중요하다. " + base
    assert BoundaryValidator(min_decisions=1).validate(Draft.from_text(ok)).passed
    print("OK validator rejects non-Korean scripts (devanagari/cyrillic), allows latin")


def test_resolution_schema():
    d = Draft.from_text("본문." * 50 + "\n[[DECISION: 핵심 논지 선택 — 본인 관점]]\n")
    dp = d.decisions[0]
    dp.resolve(Resolution.RESPOND, human_input="감시 자본 관점으로 간다")
    assert dp.resolution == Resolution.RESPOND and dp.human_input
    print("OK resolution (approve/edit/reject/respond) schema")


def test_on_fail_exception_when_never_passes():
    # min_decisions 너무 높게 잡아 절대 통과 못하게 → EXCEPTION 동작 확인
    docs_text = "본문." * 100
    guard = BoundaryGuard([BoundaryValidator(min_decisions=99)],
                          on_fail=OnFailAction.EXCEPTION, max_reasks=1)
    raised = False
    try:
        guard.run(lambda errors, prev: docs_text + "\n[[DECISION: 한 개뿐]]\n")
    except ValueError:
        raised = True
    assert raised
    print("OK on_fail=EXCEPTION raises after max reasks")


def test_ingest_all_skips_unparseable():
    import tempfile
    from until.capture.ingest import ingest_all
    with tempfile.TemporaryDirectory() as d:
        good = pathlib.Path(d) / "a.txt"; good.write_text("정상 텍스트 본문", encoding="utf-8")
        bad = pathlib.Path(d) / "b.pdf"; bad.write_bytes(b"%PDF-1.4 not really")  # PDF 라이브러리 없으면 실패
        docs = ingest_all([str(good), str(bad)])
        assert len(docs) == 1 and docs[0].source.endswith("a.txt")  # 실패 파일은 스킵
        # 경고 표면화 — 스킵된 파일명이 경고 목록에 남는다.
        from until.capture.ingest import ingest_all_with_warnings
        docs2, warns = ingest_all_with_warnings([str(good), str(bad)])
        assert len(docs2) == 1 and len(warns) == 1 and "b.pdf" in warns[0]
        # 전부 실패하면 예외
        raised = False
        try:
            ingest_all([str(bad)])
        except RuntimeError:
            raised = True
        assert raised
    print("OK ingest_all skips unparseable, raises if all fail")


def test_ingest_office_builtin_fallback():
    """docx/pptx/html 내장 폴백(의존성 0) — zip 바이트 오염 대신 본문 추출.
    이진 포맷(.hwp 등)은 명확한 예외 → 경고 표면화."""
    import tempfile, zipfile
    from until.capture.ingest import ingest_file, ingest_all_with_warnings
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        # 최소 docx(word/document.xml만).
        w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        docx = d / "과제안내.docx"
        with zipfile.ZipFile(docx, "w") as z:
            z.writestr("word/document.xml",
                       f'<w:document xmlns:w="{w}"><w:body>'
                       '<w:p><w:r><w:t>서론을 쓰고</w:t></w:r>'
                       '<w:r><w:t> 근거를 대라</w:t></w:r></w:p>'
                       '<w:p><w:r><w:t>3000자 이상</w:t></w:r></w:p>'
                       '</w:body></w:document>')
        doc = ingest_file(docx, backend="basic")
        assert "서론을 쓰고 근거를 대라" in doc.text and "3000자 이상" in doc.text
        assert "PK" not in doc.text  # zip 시그니처 오염 없음
        # 최소 pptx(슬라이드 2장 순서 보존).
        a = "http://schemas.openxmlformats.org/drawingml/2006/main"
        pptx = d / "발표.pptx"
        with zipfile.ZipFile(pptx, "w") as z:
            for i, txt in ((2, "두번째 슬라이드"), (1, "첫 슬라이드")):
                z.writestr(f"ppt/slides/slide{i}.xml",
                           f'<p:sld xmlns:a="{a}" '
                           'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
                           f'<a:t>{txt}</a:t></p:sld>')
        doc = ingest_file(pptx, backend="basic")
        assert doc.text.index("첫 슬라이드") < doc.text.index("두번째 슬라이드")
        # html 폴백 — script 제외, 텍스트만.
        page = d / "notice.html"
        page.write_text("<html><script>alert(1)</script><body><h1>공지</h1>"
                        "<p>마감은 6월 30일</p></body></html>", encoding="utf-8")
        doc = ingest_file(page, backend="basic")
        assert "공지" in doc.text and "마감은 6월 30일" in doc.text
        assert "alert" not in doc.text
        # hwpx(한글 zip) 폴백 — 섹션 순서 보존.
        hp = "http://www.hancom.co.kr/hwpml/2011/paragraph"
        hwpx = d / "레포트.hwpx"
        with zipfile.ZipFile(hwpx, "w") as z:
            for i, txt in ((1, "둘째 절"), (0, "첫째 절")):
                z.writestr(f"Contents/section{i}.xml",
                           f'<hs:sec xmlns:hs="{hp}" xmlns:hp="{hp}">'
                           f'<hp:p><hp:run><hp:t>{txt}</hp:t></hp:run></hp:p></hs:sec>')
        doc = ingest_file(hwpx, backend="basic")
        assert doc.kind == "hwpx"
        assert doc.text.index("첫째 절") < doc.text.index("둘째 절")
        # 이진 포맷은 깨진 텍스트 대신 경고로.
        hwp = d / "옛문서.hwp"; hwp.write_bytes(b"\xd0\xcf\x11\xe0 binary")
        docs, warns = ingest_all_with_warnings([str(page), str(hwp)], backend="basic")
        assert len(docs) == 1 and len(warns) == 1 and "옛문서.hwp" in warns[0]
    print("OK office/html/hwpx builtin fallback + binary warning")


def _make_hwp(path, text="안녕하세요 과제 안내입니다", password=False):
    """합성 .hwp(v5) 픽스처 — CFB 헤더+FAT+디렉터리+미니FAT+미니스트림 최소 구성."""
    import struct, zlib
    # PARA_TEXT(태그 67): 확장 컨트롤(코드3+부가 7워드) + 본문 + 문단끝(13)
    payload = b"\x03\x00" + b"\x00\x00" * 7 + text.encode("utf-16-le") + b"\x0d\x00"
    rec = struct.pack("<I", 67 | (len(payload) << 20)) + payload
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    comp = co.compress(rec) + co.flush()
    flags = 0x1 | (0x2 if password else 0)          # bit0=압축, bit1=암호
    fh = b"HWP Document File" + b"\x00" * 19 + struct.pack("<I", flags)
    fh += b"\x00" * (256 - len(fh))
    mini = fh + comp + b"\x00" * ((-len(comp)) % 64)  # 미니섹터 0-3=FileHeader, 4..=Section0
    n_mini = len(mini) // 64

    def dirent(name, etype, start, size):
        nm = name.encode("utf-16-le") + b"\x00\x00"
        e = bytearray(128)
        e[0:len(nm)] = nm
        struct.pack_into("<H", e, 64, len(nm))
        e[66] = etype
        for off in (68, 72, 76):                     # 트리 포인터는 미사용(-1)
            struct.pack_into("<i", e, off, -1)
        struct.pack_into("<i", e, 116, start)
        struct.pack_into("<Q", e, 120, size)
        return bytes(e)

    n_ms_sect = (len(mini) + 511) // 512
    dirsect = (dirent("Root Entry", 5, 3, len(mini)) + dirent("FileHeader", 2, 0, 256)
               + dirent("BodyText", 1, -1, 0)
               + dirent("Section0", 2, 4, len(comp))).ljust(512, b"\x00")
    mf = [1, 2, 3, -2]                               # FileHeader 체인
    mf += [4 + j + 1 if j < (n_mini - 4) - 1 else -2 for j in range(n_mini - 4)]
    minifat = struct.pack("<128i", *(mf + [-1] * (128 - len(mf))))
    fat = [-3, -2, -2]                               # 0=FAT, 1=DIR, 2=miniFAT
    fat += [3 + k + 1 if k < n_ms_sect - 1 else -2 for k in range(n_ms_sect)]
    fatsect = struct.pack("<128i", *(fat + [-1] * (128 - len(fat))))
    header = bytearray(512)
    header[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<H", header, 28, 0xFFFE)
    struct.pack_into("<H", header, 30, 9)            # 섹터 512
    struct.pack_into("<H", header, 32, 6)            # 미니섹터 64
    struct.pack_into("<I", header, 44, 1)            # FAT 섹터 1개
    struct.pack_into("<i", header, 48, 1)            # 디렉터리 시작=섹터1
    struct.pack_into("<I", header, 56, 4096)         # 미니 컷오프
    struct.pack_into("<i", header, 60, 2)            # 미니FAT 시작=섹터2
    struct.pack_into("<I", header, 64, 1)
    struct.pack_into("<i", header, 68, -2)           # DIFAT 체인 없음
    struct.pack_into("<i", header, 76, 0)            # DIFAT[0]=FAT 섹터 0
    for k in range(1, 109):
        struct.pack_into("<i", header, 76 + 4 * k, -1)
    mini_padded = mini + b"\x00" * (n_ms_sect * 512 - len(mini))
    path.write_bytes(bytes(header) + fatsect + dirsect + minifat + mini_padded)


def test_hwp_builtin_fallback():
    # 이진 .hwp(v5) 내장 파서 — 교수 첨부 1위 포맷(실코퍼스 11건, 라이브 11/11 파싱).
    import tempfile
    from until.capture.ingest import ingest_file, ingest_all_with_warnings
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        hwp = d / "안내.hwp"
        _make_hwp(hwp, text="조별 활동 보고서 양식입니다")
        doc = ingest_file(hwp, backend="basic")
        assert doc.kind == "hwp"
        # 본문 추출 + 컨트롤 문자(확장 코드3·부가 워드)는 새지 않는다.
        assert "조별 활동 보고서 양식입니다" in doc.text
        assert "\x03" not in doc.text
        # 암호화 .hwp는 명확한 예외 → 경고 표면화(초안 오염 방지).
        locked = d / "암호.hwp"
        _make_hwp(locked, password=True)
        ok = d / "ok.txt"; ok.write_text("정상", encoding="utf-8")
        docs, warns = ingest_all_with_warnings([str(ok), str(locked)], backend="basic")
        assert len(docs) == 1 and len(warns) == 1 and "암호.hwp" in warns[0]
        assert "PDF" in warns[0]
    print("OK hwp(v5) builtin fallback + encrypted warning")


def test_parser_robustness_review13():
    """종합 리뷰 13회차 회귀: HTML 꼬리 유실·zip 폭탄 상한."""
    import tempfile, zipfile
    from until.capture.ingest import ingest_file, ingest_all_with_warnings
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        # ① '&amp'류로 끝나는 HTML도 텍스트 런이 유실되지 않는다(close 누락 회귀).
        h = d / "cut.html"
        h.write_text("<p>AT&T 실험 결과는 유의미했다 &amp", encoding="utf-8")
        doc = ingest_file(h, backend="basic")
        assert "유의미했다" in doc.text
        # ② 고압축 zip 폭탄(60MB 해제)은 경고로 수렴(메모리 폭주 없이).
        bomb = d / "bomb.docx"
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("word/document.xml", b"0" * (60 * 1024 * 1024))
        ok = d / "ok.txt"; ok.write_text("정상", encoding="utf-8")
        docs, warns = ingest_all_with_warnings([str(ok), str(bomb)], backend="basic")
        assert len(docs) == 1 and len(warns) == 1 and "bomb.docx" in warns[0]
        assert "너무 큼" in warns[0]
    print("OK parser robustness (html tail + zip cap)")


def test_parser_fixes_review14():
    """리뷰 14회차 회귀: DIFAT 순환 폭탄·docx sdt 누락·hwpx 표 밖 텍스트 유실."""
    import struct, tempfile, time, zipfile
    from until.capture.ingest import ingest_file, ingest_all_with_warnings
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        # ① 조작 .hwp: n_difat=거대값 + DIFAT 섹터 자기참조 — 수정 전엔 같은 섹터를
        #    수천만 번 읽어 메모리 폭주/행. 순환 중단+클램프로 즉시 경고 수렴해야 한다.
        header = bytearray(512)
        header[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        struct.pack_into("<H", header, 30, 9)            # 섹터 512
        struct.pack_into("<H", header, 32, 6)
        struct.pack_into("<i", header, 48, -2)           # 디렉터리 없음
        struct.pack_into("<I", header, 56, 4096)
        struct.pack_into("<i", header, 60, -2)
        struct.pack_into("<i", header, 68, 0)            # DIFAT 시작=섹터0
        struct.pack_into("<I", header, 72, 50_000_000)   # n_difat 조작(거대값)
        for k in range(109):
            struct.pack_into("<i", header, 76 + 4 * k, -1)
        sect0 = struct.pack("<128i", *([-1] * 127 + [0]))  # 마지막 워드=자기 자신(순환)
        bomb = d / "difat.hwp"
        bomb.write_bytes(bytes(header) + sect0)          # 1KB 조작 파일
        ok = d / "ok.txt"; ok.write_text("정상", encoding="utf-8")
        t0 = time.monotonic()
        docs, warns = ingest_all_with_warnings([str(ok), str(bomb)], backend="basic")
        assert time.monotonic() - t0 < 5, "DIFAT 순환이 즉시 중단되지 않음"
        assert len(docs) == 1 and len(warns) == 1 and "difat.hwp" in warns[0]
        # ② docx: body 직속 w:sdt(콘텐츠 컨트롤) 안의 문단·표도 읽는다(양식 요건 유실 방지).
        w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        docx = d / "양식.docx"
        with zipfile.ZipFile(docx, "w") as z:
            z.writestr("word/document.xml",
                       f'<w:document xmlns:w="{w}"><w:body>'
                       '<w:p><w:r><w:t>일반 문단</w:t></w:r></w:p>'
                       '<w:sdt><w:sdtContent>'
                       '<w:p><w:r><w:t>컨트롤 속 요건: 3000자 이상</w:t></w:r></w:p>'
                       '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>항목</w:t></w:r></w:p></w:tc>'
                       '<w:tc><w:p><w:r><w:t>배점</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
                       '</w:sdtContent></w:sdt>'
                       '</w:body></w:document>')
        doc = ingest_file(docx, backend="basic")
        assert "일반 문단" in doc.text and "3000자 이상" in doc.text
        assert "| 항목 | 배점 |" in doc.text
        # ③ hwpx: 표 포함 문단의 표 밖 텍스트(지시문)도 담되, 셀 텍스트는 중복 없음.
        hp = "http://www.hancom.co.kr/hwpml/2011/paragraph"
        hwpx = d / "표양식.hwpx"
        with zipfile.ZipFile(hwpx, "w") as z:
            z.writestr("Contents/section0.xml",
                       f'<hs:sec xmlns:hs="{hp}" xmlns:hp="{hp}">'
                       '<hp:p><hp:run><hp:t>아래 표를 채우시오</hp:t></hp:run>'
                       '<hp:run><hp:tbl><hp:tr>'
                       '<hp:tc><hp:subList><hp:p><hp:run><hp:t>이름</hp:t></hp:run>'
                       '</hp:p></hp:subList></hp:tc>'
                       '<hp:tc><hp:subList><hp:p><hp:run><hp:t>학번</hp:t></hp:run>'
                       '</hp:p></hp:subList></hp:tc>'
                       '</hp:tr></hp:tbl></hp:run></hp:p></hs:sec>')
        doc = ingest_file(hwpx, backend="basic")
        assert "아래 표를 채우시오" in doc.text
        assert "| 이름 | 학번 |" in doc.text
        assert doc.text.count("이름") == 1  # 표 내부 텍스트는 표 렌더에만(중복 없음)
    print("OK parser fixes (difat cycle + docx sdt + hwpx outside-table text)")


def test_ingest_cp949_and_bom_text():
    # 메모장 저장(cp949·UTF-8 BOM) 파일도 파싱된다 — 한국어 환경 견고화.
    import tempfile
    from until.capture.ingest import ingest_file
    with tempfile.TemporaryDirectory() as d:
        cp = pathlib.Path(d) / "cp949.txt"
        cp.write_bytes("과제: 도시 에세이를 쓰시오. 분량 2000자 이상.".encode("cp949"))
        doc = ingest_file(cp)
        assert "도시 에세이" in doc.text and "2000자" in doc.text
        bom = pathlib.Path(d) / "bom.md"
        bom.write_bytes("﻿# 제목\n\n본문입니다.".encode("utf-8"))
        doc2 = ingest_file(bom)
        assert doc2.text.startswith("# 제목") and "﻿" not in doc2.text
    print("OK cp949 + UTF-8 BOM text ingested")


def test_pdf_attachment_is_parsed_when_available():
    # 첨부 PDF 본문을 실제로 읽는다(PyMuPDF 있을 때). 없으면 스킵(오프라인 불변 유지).
    try:
        import fitz  # PyMuPDF
    except Exception:
        print("OK pdf parse (skipped — pymupdf 미설치)"); return
    import tempfile, os
    from until.capture.ingest import ingest_file
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "reading.pdf")
        doc = fitz.open(); pg = doc.new_page()
        pg.insert_text((72, 72), "Surveillance capitalism reshapes institutions.")
        doc.save(p); doc.close()
        out = ingest_file(p)
        assert out.kind.startswith("pdf") and "Surveillance" in out.text
    print("OK pdf attachment parsed (pymupdf)")


def test_markdown_report_render_and_write():
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    report = render_markdown_report(res, backend=cfg.backend)
    assert "# Until Report" in report
    assert "## Draft" in report and "## Decision Points" in report
    assert "BoundaryGuard" in report and "mock" in report
    with tempfile.TemporaryDirectory() as d:
        out = write_markdown_report(res, pathlib.Path(d) / "report.md", backend=cfg.backend)
        assert out.exists()
        assert out.read_text(encoding="utf-8") == report
    print("OK markdown report render/write")


def test_apply_resolution_answers():
    d = Draft.from_text(
        "서론.\n[[DECISION: 핵심 논지 선택 — 본인 관점]]\n"
        "본론.\n[[DECISION: 결론 톤 선택]]\n"
    )
    resolved = apply_resolution_answers(d, {1: "감시 자본 관점을 중심으로 쓴다.", 2: "조심스럽게 마무리한다."})
    assert resolved.n_decisions == 0
    assert "감시 자본 관점" in resolved.body
    assert "조심스럽게 마무리한다" in resolved.body
    print("OK resolution answers applied")


def test_load_resolution_answers():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "answers.json"
        p.write_text('{"answers": {"1": "첫 답", "2": "둘째 답"}}', encoding="utf-8")
        answers = load_resolution_answers(p)
        assert answers == {1: "첫 답", 2: "둘째 답"}
        missing = pathlib.Path(d) / "missing.json"
        try:
            load_resolution_answers(missing)
        except ValueError as e:
            assert "읽을 수 없습니다" in str(e)
        else:
            raise AssertionError("missing resolve file should raise ValueError")
        bad = pathlib.Path(d) / "bad.json"
        bad.write_text("{bad json", encoding="utf-8")
        try:
            load_resolution_answers(bad)
        except ValueError as e:
            assert "JSON 형식" in str(e)
        else:
            raise AssertionError("invalid resolve JSON should raise ValueError")
    print("OK resolution answers loaded")


def test_llm_usage_metering():
    """run()이 LLM 호출·토큰을 합산해 Result.llm_usage로 부착하고,
    2차 패스(finalize)가 같은 dict에 누적한다(mock: 호출≥1, 토큰 0)."""
    from until.config import Config
    from until.pipeline import run, finalize
    cfg = Config()
    cfg.backend = "mock"
    result = run(["examples/sample_assignment.txt"], cfg)
    usage = result.llm_usage
    assert isinstance(usage, dict) and usage["llm_calls"] >= 1
    assert usage["llm_tokens_in"] == 0 and usage["llm_tokens_out"] == 0
    assert result.draft.decisions, "mock 초안에 결정이 있어야 finalize 누적 검증 가능"
    before = usage["llm_calls"]
    finalize(result, {1: "첫 결정에 대한 내 답"}, cfg)
    assert result.llm_usage is usage and usage["llm_calls"] > before
    print("OK llm usage metering")


def test_upload_slot_spec_gets_material_gap():
    """실코퍼스 회귀(기초회로 실험 슬롯 등 15건): 명세가 마감·분반·제출 안내
    (로지스틱스)뿐인 업로드 슬롯은 컨텍스트 번들이 문서로 붙어 있어도 쓸 원료가
    없다 — 200자를 억지로 채운 초안 대신 material_gap(질문 남기기)으로 가야 한다."""
    from until.understanding.substance import substantive_chars
    logistics_spec = (
        "# [월]실험6. 2차 회로의 응답\n\n"
        "과목: 기초회로이론 및 실험\n학기: e-Class\n"
        "출처: https://myetl.snu.ac.kr/courses/1/assignments/2\n과제ID: 2\n\n"
        "마감: 2026년 6월 1일(월) 오후 6시 30분\n\n"
        "월요일\n18:30-20:20 분반 학생들은\n\n"
        "실험 6. 2차 회로의 응답 예비 보고서를 제출해 주시기 바랍니다.\n\n"
        "문의사항은 좌측 하단의 게시판 클릭-> 실험 Q&A 게시판에 글 작성해 주시기 바랍니다.\n")
    assert substantive_chars(logistics_spec) < 200, "로지스틱스만 있는 명세"
    rich_spec = "# 에세이\n\n" + "다음 주제에 대해 논증하라: 기술과 사회의 관계. " * 10
    assert substantive_chars(rich_spec) > 200, "실내용 명세는 원료로 집계"

    with tempfile.TemporaryDirectory() as d:
        spec_path = pathlib.Path(d) / "spec.md"
        spec_path.write_text(logistics_spec, encoding="utf-8")
        ctx_path = pathlib.Path(d) / "context.md"
        ctx_path.write_text(
            "# eTL 과목 컨텍스트 번들\n\n## 컨텍스트 1: [eTL 공지] 실험실 안전 안내\n"
            "본문:\n실험실에서는 안전 수칙을 지켜 주시기 바랍니다.\n", encoding="utf-8")
        cfg = Config(); cfg.backend = "mock"; cfg.pipeline_mode = "unit"
        res = run([str(spec_path), str(ctx_path)], cfg)
        assert res.spec.get("material_gap"), "업로드 슬롯은 원료 없음으로 판정돼야"
        assert res.guard.passed, [h.errors for h in (res.guard.history or [])]
    # 폴백 ask — 유형별 ask가 없는 과제(발표 등)도 원료 없음 지침을 받아야 한다.
    from until.execution.prompts import material_gap_directive
    assert material_gap_directive("presentation", fallback=True)
    assert material_gap_directive("presentation") == ""
    print("OK 업로드 슬롯 material_gap")


if __name__ == "__main__":
    # README가 이 파일을 직접 실행하라고 안내한다 — Windows cp949 콘솔에서
    # 죽지 않게 한다(run_tests.py 경유는 자식 env로 이미 보장됨).
    force_utf8()
    for fn in [test_explicit_ai_prohibition_stops_before_drafting,
               test_ai_permission_does_not_false_positive,
               test_end_to_end_with_guard, test_validator_flags_stance_and_missing_decisions,
               test_validator_flags_direct_human_choices,
               test_validator_passes_clean_draft, test_placeholder_markers_rejected,
               test_validator_rejects_hanja_or_kana_mixing,
               test_validator_rejects_non_korean_scripts,
               test_resolution_schema, test_on_fail_exception_when_never_passes,
               test_ingest_all_skips_unparseable, test_ingest_office_builtin_fallback,
               test_hwp_builtin_fallback,
               test_parser_robustness_review13,
               test_parser_fixes_review14,
               test_ingest_cp949_and_bom_text,
               test_pdf_attachment_is_parsed_when_available,
               test_markdown_report_render_and_write,
               test_apply_resolution_answers, test_load_resolution_answers,
               test_llm_usage_metering, test_upload_slot_spec_gets_material_gap]:
        fn()
    print("\nALL PASS")
