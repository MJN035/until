"""라이브 '채워진 양식' 404 수정 + .hwp 양식 C안 테스트 (오프라인·결정적).

배경: 라이브(ASGI) `/dl/{token}.form`에는 form 분기가 없어 프로덕션에서 404였다
(레거시 web.py만 동작). 또한 .hwp(이진, 국내 대학 1위 포맷)는 셀 주입이 불가능해
(fill_form_file은 hwpx/docx만 지원) find_form_document가 걸러냈다 — 이 테스트는
(a) .hwp 소스 감지 → '양식 슬롯 라벨 | 채운 값' 표 .docx 생성(C안),
(b) ASGI form 분기가 레거시와 같은 파일을 내려주는지(계약),
(c) 기존 .hwpx/.docx 셀 주입 경로가 이번 변경으로 회귀하지 않았는지를 검증한다.

리뷰 회귀(Important) — `_hwp_looks_like_form`이 산문 속 우연한 라벨 단어("...강의명
관련 자료이다")나 "대학교" 같은 부분 문자열까지 라벨로 세어 무관한 .hwp 첨부를
양식으로 오판했고, 그 결과 `write_filled_form`이 문서 내용과 무관하게
profile_mapping()을 통째로 표에 흘려보냈다 — 짧은 '라벨:' 줄 기반 판정 +
mapping을 문서에 실제로 등장한 라벨로 한 번 더 거르는 `filter_mapping_to_hwp_labels`
로 고쳤다. 아래 테스트는 그 회귀를 재현·고정한다.
"""
import sys, pathlib, tempfile, zipfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.boundary.models import Draft
from until.capture.formfill import (
    find_form_document, fill_form_file, filter_mapping_to_hwp_labels,
    hwp_label_lines,
)
from until.capture.ingest import ingest_file
from until.capture.models import Document
from until.execution.boundary_guard import GuardReport
from until.pipeline import Result
from until import profile as profile_mod
from until import report

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _mk_result(documents, body, *, spec=None) -> Result:
    draft = Draft.from_text(body)
    guard = GuardReport(passed=True, attempts=1, reasks=0)
    return Result(documents=documents, spec=spec or {"title": "T"},
                  draft=draft, guard=guard)


# ── (a) .hwp 소스 감지 → 값 표 .docx 생성 ────────────────────────────────

def test_hwp_source_not_detected_without_label_density():
    """표 구조가 없는 일반 .hwp 산문은 양식으로 오판하지 않는다(과다 노출 방지)."""
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "그냥과제.hwp"
        path.write_bytes(b"fake hwp bytes")
        doc = Document(source=str(path), kind="text", text="자유 주제로 에세이를 쓰시오.")
        res = _mk_result([doc], "그냥 산문 초안입니다.")
        assert find_form_document(res) is None
    print("OK .hwp 라벨 밀도 부족 시 양식 미감지")


def test_hwp_source_detected_and_value_table_docx():
    """.hwp(이진)는 라벨 밀도로 양식 감지 → 셀 주입 대신 2열 값 표 .docx(C안)."""
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        src = d / "참가결과보고서.hwp"
        src.write_bytes(b"fake hwp bytes (content irrelevant, only Document.text matters)")
        # _read_hwp는 PARA_TEXT만 추출해 표 셀 구조가 없다 — 라벨 밀도만으로 판정.
        doc_text = "이름:\n학번:\n소속 대학·학과:\n연락처:\n이메일:\n"
        doc = Document(source=str(src), kind="text", text=doc_text)
        body = (
            "| 이름 | 김민준 | 학번 | 2020-12345 |\n"
            "|---|---|---|---|\n"
            "| 소속 대학·학과 | 서울대학교 자유전공학부 | 이메일 | hong@example.com |\n"
            "\n"
            "① 강의명: AI 융합의 최전선 / 수강일시: 2026-07-01\n"
            "▷ 강의 내용\n"
            "충분히 정리한 문장이다. 근거와 함께 서술한다.\n"
            "② 강의명: 데이터 윤리와 사회 / 수강일시: 2026-07-02\n"
            "▷ 강의 내용\n"
            "둘째 강의 본문. 관찰과 적용까지 이어서 쓴 문단이다.\n"
        )
        res = _mk_result([doc], body)
        assert find_form_document(res) == str(src)

        got = report.write_filled_form(res, d / "out.hwp")
        assert got is not None
        out, stats = got
        # .hwp는 셀 주입이 불가능해 출력 확장자가 항상 .docx로 강제된다.
        assert out.suffix == ".docx", out
        assert out.exists()
        assert stats.cells >= 4  # 이름·학번·소속·이메일
        assert stats.items >= 1  # ①② 서술 항목도 표에 옮겨진다

        # 값 표 .docx를 다시 읽어 라벨·값이 실제로 들어갔는지 확인.
        text = ingest_file(out, backend="basic").text
        assert "양식 슬롯 라벨" in text and "채운 값" in text
        assert "이름" in text and "김민준" in text
        assert "학번" in text and "2020-12345" in text
        # 원본 .hwp에 재업로드 안내(붙여넣기 → .hwpx 재업로드)가 담겨 있다.
        assert "hwpx" in text
        # 원본 파일은 손대지 않는다.
        assert src.read_bytes().startswith(b"fake hwp bytes")
    print("OK .hwp 감지 + 채운 값 표 .docx 생성(C안)")


def test_hwp_reviewer_repro_not_detected_as_form():
    """리뷰 Important 재현 — 산문 속 우연한 라벨 단어(강의명·날짜·분야)와 '대학'의
    부분 문자열 매칭('대학교')만으로는 양식으로 오판하지 않는다.

    리뷰어가 제시한 정확한 재현 텍스트: 히트로 잡혔던 단어는 강의명/날짜/분야,
    그리고 letterhead의 '대학교'가 '대학'의 부분 문자열로 걸렸었다.
    """
    repro = ("이 과제는 데이터베이스 개론 강의명 관련 자료이다.\n"
            "제출 날짜는 다음 주 금요일이며, 분야별로 자유롭게 서술하시오.")
    assert hwp_label_lines(repro) == []  # 짧은 '라벨:' 줄 자체가 없다
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "무관한과제.hwp"
        path.write_bytes(b"fake hwp bytes")
        doc = Document(source=str(path), kind="text", text=repro)
        res = _mk_result([doc], "이 과제는 자유 주제 산문 초안입니다.")
        assert find_form_document(res) is None

    # "대학교"는 "대학"의 부분 문자열이지만 별개 토큰이라 라벨로 세지 않는다.
    letterhead = "서울대학교 자유전공학부\n대학교 공지사항입니다.\n"
    assert hwp_label_lines(letterhead) == []
    print("OK 리뷰 재현 텍스트(산문 속 우연한 라벨 단어)는 양식으로 오판되지 않음")


def test_hwp_genuine_form_table_excludes_labels_absent_from_document():
    """짧은 라벨 줄(이름:/학번:/제출 날짜:)이 있는 .hwp는 양식으로 감지되고,
    값 표에는 문서에 실제로 등장한 라벨만 담긴다 — 초안 표에 '이메일' 항목이
    있어도 원본 .hwp에 그 라벨 줄이 없으면 표에서 빠진다."""
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        profile_mod.set_profile_path_override(d / "no_profile.json")
        try:
            src = d / "신청서.hwp"
            src.write_bytes(b"fake hwp bytes")
            doc_text = "이름:\n학번:\n제출 날짜:\n"
            doc = Document(source=str(src), kind="text", text=doc_text)
            body = ("| 이름 | 김민준 | 학번 | 2020-12345 | 이메일 | hong@example.com |\n"
                   "|---|---|---|---|---|---|\n")
            res = _mk_result([doc], body)
            assert find_form_document(res) == str(src)

            got = report.write_filled_form(res, d / "out.hwp")
            assert got is not None
            out, stats = got
            text = ingest_file(out, backend="basic").text
            assert "이름" in text and "김민준" in text
            assert "학번" in text and "2020-12345" in text
            # 초안 표엔 있어도 .hwp 원문 라벨 줄엔 없는 "이메일"은 표에 담기지 않는다.
            assert "hong@example.com" not in text
        finally:
            profile_mod.set_profile_path_override(None)
    print("OK .hwp 값 표엔 문서에 실제로 등장한 라벨만 포함(초안에만 있는 라벨 제외)")


def test_hwp_value_table_excludes_profile_fields_absent_from_document():
    """리뷰 Important 회귀 — 무관한 .hwp에 라벨이 우연히 2개 이상 섞여 양식으로
    감지돼도, mapping = {**profile_mapping(), **mapping_from_markdown(body)}이
    프로필 필드를 통째로 표에 흘려보내지 않는다(문서에 없는 라벨은 제외)."""
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        profile_path = d / "profile.json"
        profile_mod.set_profile_path_override(profile_path)
        try:
            profile_mod.save_profile({
                "name": "김민준", "student_id": "2020-12345",
                "email": "hong@example.com", "phone": "010-1234-5678",
            })
            src = d / "무관한과제.hwp"
            src.write_bytes(b"fake hwp bytes")
            # 문서엔 이름·학번 라벨만 있다 — 이메일·연락처 라벨 줄은 없다.
            doc_text = "이름:\n학번:\n"
            doc = Document(source=str(src), kind="text", text=doc_text)
            res = _mk_result([doc], "산문 초안입니다 — 표 없음.")
            assert find_form_document(res) == str(src)

            got = report.write_filled_form(res, d / "out.hwp")
            assert got is not None
            out, stats = got
            text = ingest_file(out, backend="basic").text
            assert "김민준" in text and "2020-12345" in text
            # 문서 라벨 줄에 없던 프로필 필드(이메일·연락처)는 노출되지 않는다.
            assert "hong@example.com" not in text
            assert "010-1234-5678" not in text
        finally:
            profile_mod.set_profile_path_override(None)
    print("OK .hwp 값 표 — 문서에 없는 프로필 필드는 새어나가지 않음(review 회귀)")


def test_filter_mapping_to_hwp_labels_unit():
    """filter_mapping_to_hwp_labels 단위 — 원문 라벨 줄과 정확히(정규화 기준)
    일치하는 키만 남기고, 부분 일치("대학"이 "대학교"에 걸리는 식)는 걸러낸다."""
    text = "이름:\n학번:\n"
    mapping = {"이름": "김민준", "학번": "2020-12345", "이메일": "x@y.com",
              "대학": "서울대학교"}
    out = filter_mapping_to_hwp_labels(mapping, text)
    assert out == {"이름": "김민준", "학번": "2020-12345"}
    print("OK filter_mapping_to_hwp_labels — 원문에 없는 라벨은 제외")


# ── (b) ASGI form 분기 계약(레거시와 동일 파일이 내려오는지) ───────────────

def _wp(text: str = "") -> str:
    t = f"<w:t>{text}</w:t>" if text else "<w:t></w:t>"
    return f"<w:p><w:r>{t}</w:r></w:p>"


def _wtc(text: str = "") -> str:
    return f"<w:tc>{_wp(text)}</w:tc>"


def _make_form_docx(path: pathlib.Path) -> pathlib.Path:
    """기본정보 표(라벨+빈칸)만 있는 최소 .docx 양식(주입 가능 경로 검증용)."""
    tbl = ("<w:tbl>"
          f"<w:tr>{_wtc('이름')}{_wtc()}{_wtc('학번')}{_wtc()}</w:tr>"
          "</w:tbl>")
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               f'<w:document xmlns:w="{W}"><w:body>{tbl}</w:body></w:document>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", document)
    return path


def test_asgi_form_route_serves_filled_docx_and_404s_without_form():
    """/dl/{token}.form 라이브(ASGI) 분기 — 레거시(web._download_filled_form)와
    같은 파일명·Content-Type을 내려준다(그전엔 404 하드코딩이었음)."""
    from fastapi.testclient import TestClient
    from until.asgi import create_app, _store_result
    from until import web

    old_sess_dir = web._SESS_DIR
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        web._SESS_DIR = d / "web_sessions"
        try:
            client = TestClient(create_app("mock"))

            # 양식 첨부가 없는 세션 — 스택 노출 없는 친절한 404(JSON detail).
            plain = _mk_result([], "그냥 산문 초안이다.")
            tok0 = _store_result(plain, backend="mock")
            r0 = client.get(f"/dl/{tok0}.form")
            assert r0.status_code == 404 and isinstance(r0.json().get("detail"), str)

            # 주입 가능한 .docx 양식이 있는 세션 — 200 + 원본 형식(.docx) 그대로.
            form_path = _make_form_docx(d / "과제양식.docx")
            doc = Document(source=str(form_path), kind="docx",
                           text="| 이름 |  | 학번 |  |\n|---|---|---|---|\n")
            body = "| 이름 | 김민준 | 학번 | 2020-12345 |\n|---|---|---|---|\n"
            res = _mk_result([doc], body)
            tok = _store_result(res, backend="mock")
            r = client.get(f"/dl/{tok}.form")
            assert r.status_code == 200, r.text
            assert r.headers["content-type"].startswith(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            assert 'filename="until-form.docx"' in r.headers["content-disposition"]
            assert len(r.content) > 0

            # 존재하지 않는 세션 — session_not_found 404(기존 계약 유지).
            assert client.get("/dl/doesnotexist.form").status_code == 404
        finally:
            web._SESS_DIR = old_sess_dir
    print("OK ASGI form 분기 계약(레거시와 동일 파일·404 무스택)")


# ── (c) .hwpx/.docx 기존 경로 회귀 없음 ────────────────────────────────

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"


def _hp(text: str = "") -> str:
    t = f"<hp:t>{text}</hp:t>" if text else "<hp:t></hp:t>"
    return f"<hp:p><hp:run>{t}</hp:run></hp:p>"


def _hp_tc(text: str = "") -> str:
    return f"<hp:tc><hp:subList>{_hp(text)}</hp:subList></hp:tc>"


def _make_form_hwpx(path: pathlib.Path) -> pathlib.Path:
    info = ("<hp:tbl>"
           f"<hp:tr>{_hp_tc('이름')}{_hp_tc()}{_hp_tc('학번')}{_hp_tc()}</hp:tr>"
           "</hp:tbl>")
    body = f"<hp:p><hp:run>{info}</hp:run></hp:p>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Contents/section0.xml",
                   f'<hs:sec xmlns:hs="{HP}" xmlns:hp="{HP}">{body}</hs:sec>')
    return path


def test_hwpx_and_docx_paths_still_inject_cells_not_value_table():
    """.hwp의 값 표 대체(C안) 로직이 .hwpx/.docx의 원본 셀 주입 경로를 건드리지
    않는다 — 출력 확장자는 여전히 원본 그대로, 통계도 fill_form_file 그대로."""
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        body = "| 이름 | 김민준 | 학번 | 2020-12345 |\n|---|---|---|---|\n"

        hwpx_src = _make_form_hwpx(d / "양식.hwpx")
        hwpx_text = ingest_file(hwpx_src, backend="basic").text
        assert "| 이름 |" in hwpx_text
        hwpx_doc = Document(source=str(hwpx_src), kind="hwpx", text=hwpx_text)
        res_hwpx = _mk_result([hwpx_doc], body)
        got_hwpx = report.write_filled_form(res_hwpx, d / "out.hwpx")
        assert got_hwpx is not None
        out_hwpx, stats_hwpx = got_hwpx
        assert out_hwpx.suffix == ".hwpx"  # .hwp만 .docx로 강제 대체, hwpx는 그대로
        assert stats_hwpx.cells >= 2
        filled_text = ingest_file(out_hwpx, backend="basic").text
        assert "| 이름 | 김민준 |" in filled_text  # 원본 셀에 실제로 주입됨

        docx_src = _make_form_docx(d / "양식.docx")
        docx_text = "| 이름 |  | 학번 |  |\n|---|---|---|---|\n"
        docx_doc = Document(source=str(docx_src), kind="docx", text=docx_text)
        res_docx = _mk_result([docx_doc], body)
        got_docx = report.write_filled_form(res_docx, d / "out.docx")
        assert got_docx is not None
        out_docx, stats_docx = got_docx
        assert out_docx.suffix == ".docx"
        assert stats_docx.cells >= 2
        filled_docx_text = ingest_file(out_docx, backend="basic").text
        assert "이름" in filled_docx_text and "김민준" in filled_docx_text

        # fill_form_file 직접 호출도 이번 변경으로 시그니처·동작이 바뀌지 않았다.
        direct = fill_form_file(hwpx_src, d / "direct.hwpx",
                                {"이름": "홍길동", "학번": "2019-1"})
        assert direct.cells >= 2
    print("OK .hwpx/.docx 셀 주입 경로 회귀 없음")


if __name__ == "__main__":
    test_hwp_source_not_detected_without_label_density()
    test_hwp_source_detected_and_value_table_docx()
    test_hwp_reviewer_repro_not_detected_as_form()
    test_hwp_genuine_form_table_excludes_labels_absent_from_document()
    test_hwp_value_table_excludes_profile_fields_absent_from_document()
    test_filter_mapping_to_hwp_labels_unit()
    test_asgi_form_route_serves_filled_docx_and_404s_without_form()
    test_hwpx_and_docx_paths_still_inject_cells_not_value_table()
    print("\nFORM FILL HWP TESTS PASS")
