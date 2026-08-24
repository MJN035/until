"""골든셋 — 준수율 측정용 케이스(결정적 생성, 이진 픽스처 커밋 없음).

각 케이스는 임시 폴더에 실제 hwpx/텍스트 입력 파일을 만들어 돌려준다.
필수 커버(작업 지시): 양식 hwpx 강의 1/3/8개, 항목당+전체 혼합 요건,
'1.' 헤드 변형, 표 행-서술 자리 불일치 함정, 프로필 결측 필수 칸, 산문 대조군.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"


def _p(text: str = "") -> str:
    t = f"<hp:t>{text}</hp:t>" if text else "<hp:t></hp:t>"
    return f"<hp:p><hp:run>{t}</hp:run></hp:p>"


def _tc(text: str = "") -> str:
    return f"<hp:tc><hp:subList>{_p(text)}</hp:subList></hp:tc>"


_MARKS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def make_coweek_hwpx(d: Path, *, n_lectures: int, heads: str = "circled",
                     n_table_rows: Optional[int] = None,
                     extra_labels: tuple = (), whole_min: Optional[int] = None,
                     name: str = "참가결과보고서_양식.hwpx") -> Path:
    """CO-Week형 보고서 양식 — 기본정보 표 + 강의 표(빈 행) + 서술 항목 자리."""
    rows = n_table_rows if n_table_rows is not None else n_lectures
    info_cells = [("이름", ""), ("학번", ""), ("소속 대학·학과", ""), ("연락처", ""),
                  ("이메일", ""), ("연계 교과목명", "")]
    for lab in extra_labels:
        info_cells.append((lab, ""))
    info_rows = []
    for i in range(0, len(info_cells), 2):
        pair = info_cells[i:i + 2]
        cells = "".join(_tc(lab) + _tc(val) for lab, val in pair)
        info_rows.append(f"<hp:tr>{cells}</hp:tr>")
    info = "<hp:tbl>" + "".join(info_rows) + "</hp:tbl>"
    lect = ("<hp:tbl>"
            f"<hp:tr>{_tc('분야')}{_tc('강좌명')}{_tc('수강 일시')}</hp:tr>"
            + "".join(f"<hp:tr>{_tc()}{_tc()}{_tc()}</hp:tr>" for _ in range(rows))
            + "</hp:tbl>")
    limit = "2. 수강 결과 (분량 제한: 강의당 300자 내외" + (
        f", 전체 {whole_min}자 이상" if whole_min else "") + ")"
    req = "수강한 강의별 핵심 개념, 새로 알게 된 점, 실습 내용 들을 자유롭게 기술"
    items = []
    for i in range(n_lectures):
        head = (f"{_MARKS[i]} 강의명: / 수강일시:" if heads == "circled"
                else f"{i + 1}. 강의명: / 수강일시:")
        items.append(_p(head) + _p("▷ 강의 내용"))
    body = (
        _p("제5회 CO-Week Academy 참가 결과 보고서")
        + f"<hp:p><hp:run>{info}</hp:run></hp:p>"
        + _p("1. 수강 완료 강의")
        + f"<hp:p><hp:run>{lect}</hp:run></hp:p>"
        + _p(limit)
        + _p(req)
        + "".join(items)
        + _p("제출: 2026년 8월 16일 23:59, eTL 제출")
    )
    path = d / name
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Contents/section0.xml",
                   f'<hs:sec xmlns:hs="{_HP}" xmlns:hp="{_HP}">{body}</hs:sec>')
        z.writestr("Contents/content.hpf", "<manifest/>")
    return path


_PROSE_ASSIGNMENT = """[기말 에세이] 디지털 전환이 도시 공공공간의 이용 방식에 미친 영향을
본인의 관찰과 자료를 근거로 논하시오.

요구사항:
- 분량: 2000자 이상 (공백 제외)
- 관점을 명확히 하고 근거 자료를 인용할 것
- 마감: 2026년 8월 20일 23:59, eTL 제출
"""

_LECTURE_NOTE = """[수강 확인 내역]
1) 분야 AI · 강좌명 '생성형 인공지능과 산업의 재편' · 2026-07-01 10:00
2) 분야 데이터 · 강좌명 '데이터 윤리와 프라이버시' · 2026-07-02 14:00
3) 분야 창업 · 강좌명 '딥테크 창업: 연구를 제품으로' · 2026-07-03 16:00
강의 요지: 생성형 AI의 산업 적용 사례(제조·금융), 차등 프라이버시와 동의 설계,
연구 성과의 제품화 과정에서의 시장 검증.
"""


@dataclass
class GoldenCase:
    key: str
    title: str
    build: "callable"          # (tmpdir: Path) -> list[str] 입력 파일 경로들
    n_items_expected: Optional[int] = None   # 기대 서술 항목 수(양식 기준)
    per_item_range: Optional[tuple] = None   # (min, max) 항목당 자수
    whole_min: Optional[int] = None          # 전체 하한(혼합 요건)
    has_form: bool = True
    profile: dict = field(default_factory=lambda: {
        "name": "김민준", "university": "서울대학교", "department": "자유전공학부",
        "student_id": "2020-12345", "phone": "010-1234-5678",
        "email": "hong@example.com"})
    note: str = ""
    assignment_type: str = "form_report"


def _case_form(n: int, **kw):
    def build(d: Path) -> List[str]:
        f = make_coweek_hwpx(d, n_lectures=n, **kw)
        note = d / "수강내역.txt"
        note.write_text(_LECTURE_NOTE, encoding="utf-8")
        return [str(f), str(note)]
    return build


def golden_cases() -> List[GoldenCase]:
    return [
        GoldenCase("coweek_1", "양식·강의 1개", _case_form(1),
                   n_items_expected=1, per_item_range=(270, 330)),
        GoldenCase("coweek_3", "양식·강의 3개(기준)", _case_form(3),
                   n_items_expected=3, per_item_range=(270, 330)),
        GoldenCase("coweek_8", "양식·강의 8개", _case_form(8),
                   n_items_expected=8, per_item_range=(270, 330)),
        GoldenCase("mixed_req", "항목당+전체 혼합 요건", _case_form(3, whole_min=1200),
                   n_items_expected=3, per_item_range=(270, 330), whole_min=1200,
                   note="항목당 300자 + 전체 1200자 이상"),
        GoldenCase("numbered", "항목 헤드 '1.' 변형", _case_form(3, heads="numbered"),
                   n_items_expected=3, per_item_range=(270, 330)),
        GoldenCase("trap_rows", "표 4행 vs 서술 3자리 함정",
                   _case_form(3, n_table_rows=4),
                   n_items_expected=3, per_item_range=(270, 330),
                   note="항목 수는 서술 자리(3) 기준이어야 함"),
        GoldenCase("missing_profile", "프로필 결측 필수 칸(지도교수·추천인)",
                   _case_form(3, extra_labels=("지도교수", "추천인")),
                   n_items_expected=3, per_item_range=(270, 330),
                   note="프로필·자료에 없는 칸을 지어내면 환각 실패"),
        GoldenCase("no_evidence", "근거 없음(강의 제목도 요지도 없음)",
                   lambda d: [str(make_coweek_hwpx(d, n_lectures=3))],
                   n_items_expected=3, per_item_range=(270, 330),
                   note="정답은 '그럴듯한 300자'가 아니라 구체 질문+사실 칸"),
        GoldenCase("prose_essay", "산문 에세이(양식 없음·대조군)",
                   lambda d: [str(_write(d / "과제.txt", _PROSE_ASSIGNMENT))],
                   has_form=False, whole_min=2000, assignment_type="essay"),
        GoldenCase("evidence_report", "근거 자료 조사 보고서",
                   lambda d: _text_case(d, "조사보고서.txt", _EVIDENCE_REPORT,
                                        "근거자료.txt", _EVIDENCE_SOURCE),
                   has_form=False, whole_min=900,
                   assignment_type="evidence_report",
                   note="제공된 근거를 인용하고 출처 없는 사실을 만들지 않아야 함"),
        GoldenCase("reflective_report", "경험 기반 성찰문",
                   lambda d: [str(_write(d / "성찰문.txt", _REFLECTIVE_REPORT))],
                   has_form=False, assignment_type="reflective_report",
                   note="개인 경험은 창작하지 않고 DECISION으로 남겨야 함"),
        GoldenCase("problemset", "문항별 문제 풀이",
                   lambda d: [str(_write(d / "문제세트.txt", _PROBLEMSET))],
                   has_form=False, n_items_expected=3,
                   assignment_type="problemset",
                   note="세 문항의 답을 분리해 작성해야 함"),
        GoldenCase("hdl_lab", "HDL 실험 보고서(실측값 없음)",
                   lambda d: [str(_write(d / "hdl실험.txt", _HDL_LAB))],
                   has_form=False, assignment_type="hdl_lab",
                   note="파형·합성 실측 수치를 지어내지 않아야 함"),
    ]


def _write(p: Path, text: str) -> Path:
    p.write_text(text, encoding="utf-8")
    return p


def _text_case(d: Path, assignment_name: str, assignment: str,
               source_name: str, source: str) -> List[str]:
    return [str(_write(d / assignment_name, assignment)),
            str(_write(d / source_name, source))]


_EVIDENCE_REPORT = """[조사 보고서] 대학 캠퍼스의 일회용품 감축 방안을 작성하시오.
- 제공된 근거 자료를 명시적으로 인용할 것
- 현황, 대안, 한계를 구분할 것
- 900자 이상
"""

_EVIDENCE_SOURCE = """[자료 A] 환경부, 2024 일회용품 정책 안내: 다회용기 전환과 분리배출 교육을 권고한다.
[자료 B] 서울대학교 그린캠퍼스 공개자료: 학생 참여형 회수함 시범 운영을 소개한다.
"""

_REFLECTIVE_REPORT = """[성찰문] 이번 학기 팀 프로젝트에서 겪은 갈등 한 가지와 해결 과정,
그 경험으로 바뀐 자신의 협업 방식을 800자 내외로 작성하시오. 실제 경험이 제공되지 않은
부분은 질문 또는 빈칸으로 남기고 경험을 만들어내지 말 것.
"""

_PROBLEMSET = """[문제 세트] 각 문항의 답을 번호별로 분리해 작성하시오.
1. 재귀와 반복의 차이를 설명하시오.
2. 이진 탐색의 시간 복잡도와 전제 조건을 쓰시오.
3. 안정 정렬의 의미와 필요한 사례를 하나 드시오.
"""

_HDL_LAB = """[HDL 실험 보고서] 4비트 카운터를 설계하고 동작 원리를 설명하시오.
파형 캡처의 관찰값과 합성 결과(최대 지연, 셀 수)는 실제 도구 실행 결과만 기록한다.
현재 파형·합성 결과는 제공되지 않았다. 측정값을 추정하거나 생성하지 말 것.
"""
