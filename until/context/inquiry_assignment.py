"""주차별 질의 과제 → 공개 순번표 → 내 담당 교수 연결(결정적, LLM 0).

공지 속 Google Sheets 링크와 Until 프로필 학번을 이용한다. 학번은 표 매칭에만
쓰고 SourceDoc/LLM에는 절대 넣지 않는다. 표·공식 프로필을 못 읽거나 매칭이
불확실하면 빈 결과로 폴백하며 교수 배정을 추측하지 않는다.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, timedelta
from html.parser import HTMLParser
from typing import Callable, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from ..llm.base import SourceDoc

_WEEK_RE = re.compile(r"(?<!\d)(\d{1,2})\s*주차")
_STUDENT_ID_RE = re.compile(r"(?<!\d)(\d{4})[-\s]?(\d{5})(?!\d)")
_DATE_RE = re.compile(r"\(?\s*(\d{1,2})\s*/\s*(\d{1,2})\s*\)?")
_SHEET_RE = re.compile(
    r"https://docs\.google\.com/spreadsheets/d/[A-Za-z0-9_-]+[^\s<>\"']*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class InquiryAssignment:
    week: int
    professor: str
    sheet_url: str
    class_date: Optional[date] = None
    due_date: Optional[date] = None
    due_time: str = "오후 5시"
    professor_field: str = ""
    professor_url: str = ""

    def to_source(self) -> SourceDoc:
        lines = [
            "질의순번표와 Until 프로필 학번을 결정적으로 대조한 결과입니다.",
            f"- 주차: {self.week}주차",
            f"- 이번 질의 대상 교수: {self.professor} 교수",
            "- 주의: 아직 듣지 않은 강의 내용을 들었다고 가정하지 말 것.",
        ]
        if self.class_date:
            lines.append(f"- 수업일: {self.class_date.isoformat()}")
        if self.due_date:
            lines.append(f"- 실제 제출 마감: {self.due_date.isoformat()} {self.due_time}")
        if self.professor_field:
            lines += [
                f"- 공식 공개 연구 분야: {self.professor_field}",
                "- 질문은 위 공개 연구 분야를 근거로 만들고, 공개 정보에 없는 강연 내용은 지어내지 말 것.",
            ]
        else:
            lines.append("- 공식 연구 분야가 확보되지 않았으므로 세부 연구 내용을 지어내지 말 것.")
        return SourceDoc(title=f"[질의 배정] {self.week}주차 {self.professor} 교수",
                         text="\n".join(lines), url=self.professor_url or self.sheet_url)


def normalize_student_id(value: str) -> str:
    """학번을 YYYY-NNNNN으로 정규화. 형식 불명은 빈 문자열."""
    m = _STUDENT_ID_RE.search(str(value or ""))
    return f"{m.group(1)}-{m.group(2)}" if m else ""


def week_from_title(title: str) -> Optional[int]:
    m = _WEEK_RE.search(title or "")
    return int(m.group(1)) if m else None


def spreadsheet_links(texts) -> list[str]:
    seen, out = set(), []
    for text in texts or []:
        for m in _SHEET_RE.finditer(str(text or "")):
            url = m.group(0).rstrip(".,);]")
            if url not in seen:
                seen.add(url); out.append(url)
    return out


def sheet_csv_url(url: str) -> str:
    """Google Sheets 편집 URL → 공개 CSV 조회 URL."""
    p = urlparse(url)
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", p.path)
    if p.scheme != "https" or p.netloc != "docs.google.com" or not m:
        raise ValueError("지원하지 않는 질의순번표 URL")
    q = parse_qs(p.query)
    gid = (q.get("gid") or ["0"])[0]
    path = f"/spreadsheets/d/{m.group(1)}/gviz/tq"
    return urlunparse(("https", "docs.google.com", path, "",
                       urlencode({"tqx": "out:csv", "gid": gid}), ""))


def student_in_week(text: str, week: int, student_id: str) -> Optional[bool]:
    """이번 주차가 **내 차례인가.** True=내 차례 · False=아님 · None=판단 불가.

    질의 과제는 주차마다 담당 학생이 정해져 있어서, 내 차례가 아닌 주의 과제는
    할 일이 아니다(사용자 지시 2026-08-23). 그런데 기존 매칭은 '내 차례 아님'과
    '표를 못 읽음'을 똑같이 None으로 돌려줬다 — 그 둘을 가르지 않으면 아무것도
    분류할 수 없다.

    **틀리는 방향이 대칭이 아니다.** 잘못 '내 차례'라고 하면 안 해도 될 걸 하지만,
    잘못 '내 차례 아님'이라고 하면 **진짜 과제를 놓친다.** 그래서 False는
    아래를 모두 만족할 때만 낸다:
      · 그 주차 블록을 찾았고,
      · 담당 열(교수)이 있고,
      · 그 블록에 **다른 학생 학번이 실제로 적혀 있고**(표가 채워져 있다는 증거),
      · 그중 내 학번이 없다.
    표가 비었거나 아직 안 채워졌으면 None(모름)이다.
    """
    sid = normalize_student_id(student_id)
    if not sid:
        return None
    rows = list(csv.reader(io.StringIO(text or "")))
    for ri, row in enumerate(rows):
        for ci, cell in enumerate(row):
            wm = _WEEK_RE.fullmatch(cell.strip())
            if not wm or int(wm.group(1)) != week:
                continue
            cols = [cj for cj in range(ci + 1, len(row)) if row[cj].strip()]
            if not cols:
                return None            # 담당 열이 없다 — 이 표로는 모른다
            stop = len(rows)
            for rj in range(ri + 1, len(rows)):
                if any(_WEEK_RE.fullmatch(x.strip()) for x in rows[rj] if x.strip()):
                    stop = rj
                    break
            found_any = False
            for rj in range(ri + 1, stop):
                for cj in cols:
                    if cj >= len(rows[rj]):
                        continue
                    other = normalize_student_id(rows[rj][cj])
                    if not other:
                        continue
                    found_any = True
                    if other == sid:
                        return True
            return False if found_any else None
    return None


def parse_assignment_csv(text: str, week: int, student_id: str,
                         *, year: Optional[int] = None,
                         due_previous_day: bool = False,
                         due_time: str = "") -> Optional[InquiryAssignment]:
    """가로 블록형 순번표에서 주차·학번이 만나는 교수 열을 찾는다."""
    sid = normalize_student_id(student_id)
    if not sid:
        return None
    rows = list(csv.reader(io.StringIO(text or "")))
    for ri, row in enumerate(rows):
        for ci, cell in enumerate(row):
            wm = _WEEK_RE.fullmatch(cell.strip())
            if not wm or int(wm.group(1)) != week:
                continue
            professors = []
            cj = ci + 1
            while cj < len(row) and row[cj].strip():
                name = re.sub(r"\s*교수님?\s*$", "", row[cj].strip()).strip()
                if name:
                    professors.append((cj, name))
                cj += 1
            stop = len(rows)
            for rj in range(ri + 1, len(rows)):
                if any(_WEEK_RE.fullmatch(x.strip()) for x in rows[rj] if x.strip()):
                    stop = rj; break
            matched = []
            for col, name in professors:
                for rj in range(ri + 1, stop):
                    if col < len(rows[rj]) and normalize_student_id(rows[rj][col]) == sid:
                        matched.append(name); break
            if len(matched) != 1:
                return None
            class_day = None
            for rj in range(ri + 1, min(stop, ri + 3)):
                if ci < len(rows[rj]):
                    dm = _DATE_RE.fullmatch(rows[rj][ci].strip())
                    if dm and year:
                        try:
                            class_day = date(year, int(dm.group(1)), int(dm.group(2)))
                        except ValueError:
                            pass
                        break
            return InquiryAssignment(
                week=week, professor=matched[0], sheet_url="",
                class_date=class_day,
                due_date=(class_day - timedelta(days=1)
                          if class_day and due_previous_day else None),
                due_time=due_time or "",
            )
    return None


class _FacultyParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.links = []; self.text = []
        self._href = ""; self._link_text = []
    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href", ""); self._link_text = []
    def handle_data(self, data):
        self.text.append(data)
        if self._href:
            self._link_text.append(data)
    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append(("".join(self._link_text).strip(), self._href))
            self._href = ""; self._link_text = []


def official_professor_profile(professor: str, fetch_text: Callable[[str], str]) -> tuple[str, str]:
    """서울대 전기정보공학부 공식 교수 페이지에서 연구 분야와 URL을 찾는다."""
    index = "https://ece.snu.ac.kr/research-faculty/faculty/full-time"
    p = _FacultyParser(); p.feed(fetch_text(index))
    def _name_key(value: str) -> str:
        return re.sub(r"[^0-9A-Za-z가-힣]", "", value or "").removesuffix("교수")
    target = _name_key(professor)
    candidates = [(t, h) for t, h in p.links
                  if target and target == _name_key(t)]
    if len(candidates) != 1:
        return "", ""
    url = urljoin(index, candidates[0][1])
    detail = _FacultyParser(); detail.feed(fetch_text(url))
    plain = " ".join(" ".join(detail.text).split())
    m = re.search(
        r"분야\s*[:：]\s*(.{2,300}?)(?=\s+(?:연구실(?:소개파일[^:：]*)?|학력|경력|연락처|홈페이지)\s*[:：]|$)",
        plain,
    )
    return ((m.group(1).strip() if m else ""), url)


def resolve_inquiry_assignment(*, title: str, student_id: str, announcements,
                               fetch_text: Callable[[str], str],
                               year: Optional[int] = None) -> Optional[InquiryAssignment]:
    """공지 링크→시트→학번 매칭→공식 교수 분야를 한 번에 연결한다."""
    week = week_from_title(title)
    if week is None or not normalize_student_id(student_id):
        return None
    texts = []
    for a in announcements or []:
        texts += [getattr(a, "body", ""), *(getattr(a, "links", []) or [])]
    links = spreadsheet_links(texts)
    rule_text = " ".join(str(t or "") for t in texts)
    previous_day = bool(re.search(r"수업\s*전날", rule_text))
    tm = re.search(r"(오전|오후)\s*(\d{1,2})\s*시", rule_text)
    due_time = f"{tm.group(1)} {int(tm.group(2))}시" if tm else ""
    for link in links:
        try:
            got = parse_assignment_csv(fetch_text(sheet_csv_url(link)), week,
                                       student_id, year=year,
                                       due_previous_day=previous_day,
                                       due_time=due_time)
        except Exception:
            continue
        if got is None:
            continue
        field = profile_url = ""
        try:
            field, profile_url = official_professor_profile(got.professor, fetch_text)
        except Exception:
            pass
        return InquiryAssignment(
            week=got.week, professor=got.professor, sheet_url=link,
            class_date=got.class_date, due_date=got.due_date,
            professor_field=field, professor_url=profile_url,
        )
    return None
