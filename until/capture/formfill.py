"""양식(서식) 인식·재현·주입 — 결정적(LLM 0).

실사용 피드백 대응: 표·①② 항목이 있는 '보고서 양식' 첨부를 냈는데 결과가
통짜 산문으로 나와 사용자가 칸마다 복붙해야 했다. 이 모듈은
  1) ingest가 보존한 표 구조(| a | b | 행)에서 양식 여부를 감지하고,
  2) '원본 양식 구조 그대로 채워 출력하라'는 실행 지침(스캐폴드 포함)을 만들고,
  3) 초안의 마크다운 표에서 라벨→값을 뽑아 **원본 hwpx/docx 셀에 주입**해
     업로드된 원본 형식 그대로의 채워진 파일을 만든다.
경계선 유지: 값을 지어내지 않는다 — 초안(사람이 확인한)과 프로필에 있는 값만 주입.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# 양식의 기본정보 칸에서 흔한 라벨(신상·과목 정보) — 양식 감지와 값 매핑에 쓴다.
KNOWN_LABELS = (
    "이름", "성명", "소속", "학과", "대학", "학번", "연락처", "전화", "휴대폰",
    "이메일", "e-mail", "email", "교과목", "과목명", "강좌명", "강의명", "분야",
    "일시", "날짜", "학년", "전공", "지도교수",
)

# 그중 '사실(신상) 칸' — 자료·프로필에 없으면 지어내면 안 되는 값(환각 채점 대상).
FACT_LABELS = ("이름", "성명", "소속", "학과", "대학", "학번", "연락처", "전화",
               "휴대폰", "이메일", "e-mail", "email", "지도교수", "추천인")

_TABLE_ROW_RE = re.compile(r"^\s*\|.+\|\s*$")
_SEP_ROW_RE = re.compile(r"^\s*\|(?:\s*:?-{2,}:?\s*\|)+\s*$")
_ITEM_HEAD_RE = re.compile(r"^\s*[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]")


def _split_row(line: str) -> List[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


@dataclass
class FormStructure:
    """텍스트(ingest 산출)에서 감지한 양식 구조."""
    tables: List[List[List[str]]] = field(default_factory=list)  # 표 → 행 → 셀
    labels: List[str] = field(default_factory=list)              # 발견된 기본정보 라벨
    item_heads: List[str] = field(default_factory=list)          # ①② 항목 헤드 줄
    scaffold: str = ""                                           # 양식 부분 원문 발췌

    @property
    def is_form(self) -> bool:
        """양식으로 볼 근거 — 표가 있고, 알려진 라벨 2개 이상 또는 빈 칸 비율이 높다."""
        if not self.tables:
            return False
        if len(self.labels) >= 2:
            return True
        cells = [c for t in self.tables for r in t for c in r]
        return bool(cells) and sum(1 for c in cells if not c) >= max(2, len(cells) // 4)


def detect_form(text: str) -> FormStructure:
    """ingest가 보존한 표 행(| a | b |)·①② 항목에서 양식 구조를 감지한다."""
    fs = FormStructure()
    lines = (text or "").splitlines()
    cur: List[List[str]] = []
    keep: List[str] = []
    for ln in lines:
        if _TABLE_ROW_RE.match(ln):
            keep.append(ln)
            if not _SEP_ROW_RE.match(ln):
                cur.append(_split_row(ln))
            continue
        if cur:
            fs.tables.append(cur)
            cur = []
        if _ITEM_HEAD_RE.match(ln):
            fs.item_heads.append(ln.strip())
            keep.append(ln)
    if cur:
        fs.tables.append(cur)
    low_known = tuple(k.lower() for k in KNOWN_LABELS)
    for t in fs.tables:
        for row in t:
            for c in row:
                cl = c.strip().lower()
                if cl and any(k in cl for k in low_known) and len(cl) <= 12:
                    fs.labels.append(c.strip())
    fs.scaffold = "\n".join(keep)
    return fs


def form_directive(docs) -> str:
    """과제 문서들에서 양식이 감지되면 '구조 그대로 채워 출력' 실행 지침을 만든다.

    없으면 빈 문자열. pipeline.run이 length_directive와 같은 방식으로 시스템에 주입.
    """
    for d in docs or []:
        fs = detect_form(getattr(d, "text", "") or "")
        if not fs.is_form:
            continue
        name = Path(getattr(d, "source", "") or "양식").name
        return (
            "[ 양식 준수 — 원본 구조 그대로 채워서 출력 ]\n"
            f"- 이 과제는 정해진 양식({name})이 있다. 산문으로 풀어 쓰지 말고, 아래 원본\n"
            "  양식 구조(표·항목 번호)를 **그대로 유지한 채** 각 칸·각 항목을 채워 출력하라.\n"
            "- 표는 마크다운 표(| 칸 | 값 |)로, 같은 행·열 구조를 유지한다. 빈 칸을 채우되\n"
            "  칸 자체를 없애거나 순서를 바꾸지 말 것.\n"
            "- 이름·학번·소속·연락처처럼 자료에 없는 개인 정보 칸은 지어내지 말고\n"
            "  【프로필】 값이 주어졌으면 그 값으로, 없으면 그 칸에만 [[DECISION: ...]]을 남긴다.\n"
            "- ①, ②… 항목 구조(강의명·수강일시·▷ 내용 등)가 있으면 항목 수·머리글을\n"
            "  그대로 두고 각 항목의 본문을 채운다.\n"
            "- '이 양식이 맞는지', '형식(hwp/hwpx/PDF)이 무엇인지'를 [[DECISION]]으로\n"
            "  사용자에게 확인 요구하지 말 것 — 양식 준수는 시스템이 검증해 근거를\n"
            "  보여준다. 사용자에게 남기는 결정은 '내용'에 대한 것만.\n"
            "[ 원본 양식 구조 ]\n"
            f"{fs.scaffold}"
        )
    return ""


def mapping_from_markdown(body: str) -> Dict[str, str]:
    """초안의 마크다운 표에서 라벨→값 매핑을 뽑는다(짝수 셀 = 라벨/값 쌍).

    '| 이름 | 홍길동 | 학번 | 2020-12345 |'처럼 한 행에 여러 쌍이 있어도 처리.
    값이 비었거나 결정 마커면 건너뛴다(지어내지 않은 칸은 주입하지 않음).
    """
    out: Dict[str, str] = {}
    low_known = tuple(k.lower() for k in KNOWN_LABELS)
    for ln in (body or "").splitlines():
        if not _TABLE_ROW_RE.match(ln) or _SEP_ROW_RE.match(ln):
            continue
        cells = _split_row(ln)
        if len(cells) % 2 != 0:
            continue
        for i in range(0, len(cells), 2):
            label, value = cells[i].strip(), cells[i + 1].strip()
            if not label or not value or "[[DECISION" in value or "직접 정할 것" in value:
                continue
            if any(k in label.lower() for k in low_known) and label not in out:
                out[label] = value
    return out


def rows_from_markdown(body: str, header: List[str]) -> List[List[str]]:
    """초안 마크다운 표 중 머리행이 header와 일치하는 표의 데이터 행들을 돌려준다."""
    want = [h.strip() for h in header if h.strip()]
    if not want:
        return []
    rows: List[List[str]] = []
    collecting = False
    for ln in (body or "").splitlines():
        if not _TABLE_ROW_RE.match(ln):
            if collecting:
                break
            continue
        if _SEP_ROW_RE.match(ln):
            continue
        cells = _split_row(ln)
        if not collecting:
            got = [c for c in cells if c]
            if got and all(any(w in c for c in got) for w in want):
                collecting = True
            continue
        if any(c.strip() for c in cells):
            rows.append(cells)
    return rows


# ── 원본 파일 셀·문단 주입(hwpx/docx zip XML) ────────────────────────
@dataclass
class FillStats:
    """주입 결과 수치 — 표 칸과 서술 문단을 구분해 사용자에게 보여준다."""
    cells: int = 0        # 채운 표 칸 수
    paragraphs: int = 0   # 채운/추가한 서술 문단 수(①② 항목 본문)
    items: int = 0        # 본문이 들어간 서술 항목 수

    @property
    def total(self) -> int:
        return self.cells + self.paragraphs

    def describe(self) -> str:
        parts = [f"표 {self.cells}칸"]
        if self.items:
            parts.append(f"서술 {self.items}항목({self.paragraphs}문단)")
        return " · ".join(parts)


def _norm(s: str) -> str:
    return re.sub(r"[\s:：*※()（）]+", "", (s or "")).lower()


def _register_namespaces(xml_bytes: bytes) -> None:
    """원본 접두사(hp:, w: 등)를 보존해 재직렬화 시 한글/워드가 못 여는 파일이 되지 않게."""
    from xml.etree import ElementTree as ET
    for prefix, uri in re.findall(rb'xmlns:([A-Za-z0-9]+)="([^"]+)"', xml_bytes):
        ET.register_namespace(prefix.decode(), uri.decode())
    m = re.search(rb'xmlns="([^"]+)"', xml_bytes)
    if m:
        ET.register_namespace("", m.group(1).decode())


def _cell_text_local(tc, t_name: str) -> str:
    return " ".join(t.text.strip() for t in tc.iter()
                    if t.tag.rsplit("}", 1)[-1] == t_name and t.text and t.text.strip())


def _set_cell_text(tc, value: str, *, t_name: str, run_name: str) -> bool:
    """셀의 첫 텍스트 요소에 값을 넣는다. 텍스트 요소가 없으면 기존 run 아래 생성."""
    import copy
    from xml.etree import ElementTree as ET
    ts = [t for t in tc.iter() if t.tag.rsplit("}", 1)[-1] == t_name]
    if ts:
        ts[0].text = value
        for extra in ts[1:]:
            extra.text = ""
        return True
    runs = [r for r in tc.iter() if r.tag.rsplit("}", 1)[-1] == run_name]
    if runs:
        ns = runs[0].tag.rsplit("}", 1)[0] + "}"
        t = ET.SubElement(runs[0], f"{ns}{t_name}")
        t.text = value
        return True
    # run조차 없으면 셀 안 첫 문단에 run+t를 만든다(문단의 네임스페이스 재사용).
    ps = [p for p in tc.iter() if p.tag.rsplit("}", 1)[-1] == "p"]
    if ps:
        ns = ps[0].tag.rsplit("}", 1)[0] + "}"
        run = ET.SubElement(ps[0], f"{ns}{run_name}")
        t = ET.SubElement(run, f"{ns}{t_name}")
        t.text = value
        return True
    _ = copy  # (deepcopy 예비 — 현재 경로에선 미사용)
    return False


def _fill_tables_in_root(root, mapping: Dict[str, str],
                         rows_by_header: Optional[Dict[tuple, List[List[str]]]],
                         *, t_name: str, run_name: str) -> int:
    """루트의 모든 표에 라벨→값·헤더 표 행들을 주입. 채운 칸 수를 돌려준다."""
    filled = 0
    norm_map = {_norm(k): v for k, v in mapping.items()}
    for tbl in (e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "tbl"):
        rows = [[c for c in tr.iter() if c.tag.rsplit("}", 1)[-1] == "tc"]
                for tr in tbl.iter() if tr.tag.rsplit("}", 1)[-1] == "tr"]
        rows = [r for r in rows if r]
        if not rows:
            continue
        # 1) 라벨 칸 → 같은 행의 다음 빈 칸(없으면 바로 아래 칸)에 값.
        for ri, row in enumerate(rows):
            for ci, tc in enumerate(row):
                label = _norm(_cell_text_local(tc, t_name))
                if not label or label not in norm_map:
                    continue
                value = norm_map[label]
                target = None
                for cand in row[ci + 1:]:
                    if not _cell_text_local(cand, t_name):
                        target = cand
                        break
                if target is None and ri + 1 < len(rows) and ci < len(rows[ri + 1]):
                    below = rows[ri + 1][ci]
                    if not _cell_text_local(below, t_name):
                        target = below
                if target is not None and _set_cell_text(
                        target, value, t_name=t_name, run_name=run_name):
                    filled += 1
        # 2) 머리행 일치 표 → 데이터 행 채움(빈 행부터 순서대로).
        if rows_by_header:
            head = tuple(_norm(_cell_text_local(c, t_name)) for c in rows[0])
            for header, data in rows_by_header.items():
                if not header or not all(h in head for h in header):
                    continue
                empty_rows = [r for r in rows[1:]
                              if not any(_cell_text_local(c, t_name) for c in r)]
                for r, values in zip(empty_rows, data, strict=False):
                    for tc, v in zip(r, values, strict=False):
                        if v and _set_cell_text(tc, v, t_name=t_name, run_name=run_name):
                            filled += 1
    return filled


_ITEM_MARK_RE = re.compile(r"^\s*([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])")
_SUBHEAD_RE = re.compile(r"^\s*[▷▶►]")  # 항목 내부 소제목("▷ 강의 내용")


def _para_text(p, t_name: str) -> str:
    return "".join(t.text or "" for t in p.iter()
                   if t.tag.rsplit("}", 1)[-1] == t_name).strip()


def _set_para_text(p, value: str, *, t_name: str, run_name: str) -> bool:
    """문단의 텍스트를 통째로 교체(첫 t에 값, 나머지 t는 비움)."""
    return _set_cell_text(p, value, t_name=t_name, run_name=run_name)


def _fill_item_paragraphs(root, item_bodies: List[tuple],
                          *, t_name: str, run_name: str) -> "tuple[int, int]":
    """양식의 ①② 서술 자리(표 밖 문단)에 초안 항목 본문을 주입한다.

    표 칸만 채우면 사용자는 '표는 채워졌는데 본문은 여전히 복붙'하게 된다(실사용
    갭 3). 원본 양식의 항목 헤드(①…)와 소제목(▷…) 문단은 서식을 유지한 채
    텍스트만 갱신하고, 본문 줄은 기존 문단을 deepcopy(스타일 참조 보존)해
    그 뒤에 삽입한다. 반환: (본문이 들어간 항목 수, 주입 문단 수).
    """
    import copy
    # 표 안의 문단은 제외(문서 순서 유지) + 부모 맵(ElementTree는 부모 포인터 없음).
    in_table = set()
    for tbl in (e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "tbl"):
        for e in tbl.iter():
            in_table.add(id(e))
    parent_of = {}
    for parent in root.iter():
        for child in parent:
            parent_of[id(child)] = parent
    paras = [p for p in root.iter()
             if p.tag.rsplit("}", 1)[-1] == "p" and id(p) not in in_table]
    # 항목 마크 → (헤드 문단 인덱스)
    head_idx: Dict[str, int] = {}
    for i, p in enumerate(paras):
        m = _ITEM_MARK_RE.match(_para_text(p, t_name))
        if m and m.group(1) not in head_idx:
            head_idx[m.group(1)] = i

    n_items = n_paras = 0
    for label, chunk in item_bodies:
        m = _ITEM_MARK_RE.match(label or "")
        if not m or m.group(1) not in head_idx:
            continue
        i = head_idx[m.group(1)]
        # 1) 헤드 문단 갱신 — "① 강의명: / 수강일시:" → 초안의 채워진 헤드 라인.
        if _norm(label) != _norm(_para_text(paras[i], t_name)):
            if _set_para_text(paras[i], label.strip(), t_name=t_name, run_name=run_name):
                n_paras += 1
        # 2) 본문 줄 — 초안 항목 청크에서 소제목(▷)·빈 줄 제외.
        lines = [ln.strip() for ln in (chunk or "").splitlines()
                 if ln.strip() and not _SUBHEAD_RE.match(ln)]
        if not lines:
            continue
        # 3) 삽입 기준점: 헤드 다음의 ▷ 문단(있으면), 없으면 헤드 자신.
        nxt = min((head_idx[k] for k in head_idx if head_idx[k] > i),
                  default=len(paras))
        anchor = paras[i]
        for j in range(i + 1, nxt):
            if _SUBHEAD_RE.match(_para_text(paras[j], t_name)):
                anchor = paras[j]
                break
        parent = parent_of.get(id(anchor))
        if parent is None:
            continue
        # 4) 기준점과 다음 헤드 사이의 '빈 문단'을 먼저 재사용, 모자라면 복제 삽입.
        empties = [paras[j] for j in range(i + 1, nxt)
                   if not _para_text(paras[j], t_name)]
        placed = 0
        for ln in lines:
            if empties:
                target = empties.pop(0)
                if _set_para_text(target, ln, t_name=t_name, run_name=run_name):
                    placed += 1
                continue
            clone = copy.deepcopy(anchor)  # 서식(스타일 참조) 유지 복제
            if not _set_para_text(clone, ln, t_name=t_name, run_name=run_name):
                continue
            try:
                pos = list(parent).index(anchor)
            except ValueError:
                continue
            parent.insert(pos + 1 + placed, clone)
            placed += 1
        if placed:
            n_items += 1
            n_paras += placed
    return n_items, n_paras


def fill_form_file(src: str | Path, out: str | Path, mapping: Dict[str, str],
                   rows_by_header: Optional[Dict[tuple, List[List[str]]]] = None,
                   item_bodies: Optional[List[tuple]] = None) -> FillStats:
    """원본 양식(hwpx/docx)을 복사하며 표 칸·서술 문단에 값을 주입한다.

    - hwpx: Contents/section*.xml, docx: word/document.xml 만 수정, 나머지는 그대로 —
      스타일·서식은 원본 그대로 유지된다('원본 형식을 그대로 따름' 기본값).
    - mapping: 라벨 → 값(라벨 칸의 오른쪽/아래 빈 칸에 주입).
    - rows_by_header: {정규화된 머리행 라벨 tuple: [행 값들]} — 목록형 표(수강 강의 등).
    - item_bodies: [(항목 헤드 라인, 본문 청크)] — ①② 서술 자리(표 밖 문단)에 주입.
    반환: FillStats(표 칸 수·서술 항목/문단 수).
    """
    import zipfile
    from xml.etree import ElementTree as ET
    src, out = Path(src), Path(out)
    suffix = src.suffix.lower()
    if suffix == ".hwpx":
        target_re = re.compile(r"Contents/section\d+\.xml")
        t_name, run_name = "t", "run"
    elif suffix == ".docx":
        target_re = re.compile(r"word/document\.xml")
        t_name, run_name = "t", "r"
    else:
        raise RuntimeError(f"{suffix} 양식 주입은 지원하지 않아요(.hwpx/.docx만)")
    stats = FillStats()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w",
                                                     zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if target_re.fullmatch(item.filename):
                _register_namespaces(data)
                root = ET.fromstring(data)
                stats.cells += _fill_tables_in_root(root, mapping, rows_by_header,
                                                    t_name=t_name, run_name=run_name)
                if item_bodies:
                    ni, np_ = _fill_item_paragraphs(root, item_bodies,
                                                    t_name=t_name, run_name=run_name)
                    stats.items += ni
                    stats.paragraphs += np_
                data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            zout.writestr(item, data)
    return stats


@dataclass
class FormFidelity:
    """초안이 원본 양식 구조를 유지했는지의 결정적 판정(사용자에게 되묻지 않기 위한 근거)."""
    n_labels: int = 0
    missing_labels: List[str] = field(default_factory=list)
    n_items: int = 0
    missing_items: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing_labels and not self.missing_items

    @property
    def message(self) -> str:
        kept_l = self.n_labels - len(self.missing_labels)
        kept_i = self.n_items - len(self.missing_items)
        parts = []
        if self.n_labels:
            parts.append(f"기본정보 라벨 {kept_l}/{self.n_labels}")
        if self.n_items:
            parts.append(f"항목 {kept_i}/{self.n_items}")
        kept = " · ".join(parts) or "구조 요소 없음"
        if self.ok:
            return f"원본 양식 구조 유지 확인 — {kept} 일치 (ETL 공지를 다시 볼 필요 없어요)"
        miss = ", ".join((self.missing_labels + self.missing_items)[:4])
        return f"양식 구조 일부 누락 — {kept} (빠짐: {miss})"


def expected_item_count(form_text: str) -> Optional[int]:
    """양식에서 '서술 항목이 몇 개여야 하는지'를 결정적으로 유도한다.

    우선순위: ①②… 항목 헤드의 고유 마크 수 → 목록형 표(머리행+데이터 행)의
    데이터 행 수(채워질/채워진 행). 유도 불가면 None.
    분량 검증(check_length expected_items)과 양식 구조를 교차검증하는 다리 —
    이 연결이 없으면 양식을 무시한 산문이 '판정 불가'로 빠져나간다.
    """
    fs = detect_form(form_text)
    if not fs.is_form:
        return None
    marks = []
    for head in fs.item_heads:
        m = head.strip()[:1]
        if m and m not in marks:
            marks.append(m)
    if marks:
        return len(marks)
    best = 0
    for t in fs.tables:
        if len(t) < 2:
            continue
        head = [c for c in t[0] if c.strip()]
        if len(head) < 2:
            continue
        best = max(best, len(t) - 1)  # 머리행 제외 데이터 행 수
    return best or None


def check_form_fidelity(form_text: str, body: str) -> Optional[FormFidelity]:
    """원본 양식의 표 라벨·①② 항목이 초안 본문에 유지됐는지 결정적으로 대조.

    양식이 감지되지 않으면 None. '양식이 맞나?'라는 판단을 사용자에게 떠넘기지
    않고 시스템이 근거를 제시하기 위한 점검(readiness '양식' 항목).
    """
    fs = detect_form(form_text)
    if not fs.is_form:
        return None
    body_norm = _norm(body)
    fid = FormFidelity()
    seen: List[str] = []
    for lab in fs.labels:
        if lab in seen:
            continue
        seen.append(lab)
        if _norm(lab) not in body_norm:
            fid.missing_labels.append(lab)
    fid.n_labels = len(seen)
    marks: List[str] = []
    for head in fs.item_heads:
        mark = head.strip()[:1]
        if mark and mark not in marks:
            marks.append(mark)
            if mark not in (body or ""):
                fid.missing_items.append(f"{mark} 항목")
    fid.n_items = len(marks)
    return fid


def build_rows_by_header(form_text: str, body: str) -> Dict[tuple, List[List[str]]]:
    """양식의 '목록형 표'(머리행 + 빈 데이터 행)마다 초안에서 데이터 행을 찾는다.

    예: 양식의 '| 분야 | 강좌명 | 수강 일시 |' 표 ↔ 초안의 같은 머리행 표.
    반환: {정규화된 머리행 라벨 tuple: [행 값들]} — fill_form_file의 rows_by_header.
    """
    out: Dict[tuple, List[List[str]]] = {}
    for t in detect_form(form_text).tables:
        if len(t) < 2:
            continue
        head = [c for c in t[0] if c.strip()]
        if len(head) < 2:
            continue
        if not any(all(not c.strip() for c in r) for r in t[1:]):
            continue  # 빈 데이터 행이 없는 표는 채울 목록형 표가 아니다
        rows = rows_from_markdown(body, head)
        if rows:
            out[tuple(_norm(c) for c in t[0] if _norm(c))] = rows
    return out


_HWP_LABEL_LINE_MAXLEN = 20   # 라벨 줄 전체 길이 상한(산문 문장 배제)
_HWP_LABEL_HEAD_MAXLEN = 12   # 콜론 앞 라벨부 길이 상한(detect_form 셀 길이<=12와 동일 기준)
_HWP_TOKEN_SPLIT_RE = re.compile(r"[\s/·,()（）]+")


def hwp_label_lines(text: str) -> List[str]:
    """.hwp(이진) 원문에서 표 구조 없이 실제 '라벨:' 필드로 보이는 줄만 뽑는다.

    detect_form()은 표 셀 텍스트 길이(<=12)로 라벨 후보를 좁히는데, .hwp는
    표 셀이 없어(ingest._read_hwp가 PARA_TEXT만 추출) 같은 제약을 줄 단위로
    건다 — 짧은 줄(<=20자)의 콜론 앞부분(<=12자)을 토큰으로 쪼개 KNOWN_LABELS와
    **완전 일치**하는 토큰이 있을 때만 라벨 줄로 인정한다. 부분 문자열 매칭이면
    '대학교'가 '대학'에 우연히 걸리고, 길이 제한이 없으면 본문 중간의 우연한
    라벨 단어("...강의명 관련 자료이다")까지 라벨로 오판한다.
    """
    low_known = {lab.lower() for lab in KNOWN_LABELS}
    out: List[str] = []
    for raw in (text or "").splitlines():
        ln = raw.strip()
        if not ln or len(ln) > _HWP_LABEL_LINE_MAXLEN:
            continue
        head = re.split(r"[:：]", ln, maxsplit=1)[0].strip()
        if not head or len(head) > _HWP_LABEL_HEAD_MAXLEN:
            continue
        tokens = [t.strip().lower() for t in _HWP_TOKEN_SPLIT_RE.split(head) if t.strip()]
        if any(tok in low_known for tok in tokens):
            out.append(head)
    return out


def _hwp_looks_like_form(text: str) -> bool:
    """.hwp(이진) 원문에서 표 구조 없이 짧은 라벨 줄 밀도로 양식 여부를 판정한다.

    서로 다른(정규화 기준) 라벨 줄이 2개 이상이어야 양식으로 본다 — 우연히
    라벨 단어 하나가 섞인 일반 산문 과제와 구분하기 위한 최소 기준.
    """
    lines = hwp_label_lines(text)
    return len({_norm(ln) for ln in lines}) >= 2


def filter_mapping_to_hwp_labels(mapping: Dict[str, str], text: str) -> Dict[str, str]:
    """.hwp 값 표(C안) 전용 — mapping 중 원문에 실제로 등장한 라벨 줄과 일치하는
    항목만 남긴다.

    find_form_document/write_filled_form의 .hwp 경로는 라벨 '밀도'만으로 양식
    여부를 판정하므로, 무관한 .hwp 첨부에도(라벨 단어가 우연히 2개 이상 섞이면)
    mapping = {**profile_mapping(), **mapping_from_markdown(body)}이 프로필
    값을 통째로 흘려보낼 수 있다 — 값 하나하나는 '원문에 실제로 있던 라벨인지'로
    한 번 더 걸러야 사용자 프로필이 무관한 문서의 '채운 값'인 것처럼 노출되지 않는다.
    """
    allowed = {_norm(ln) for ln in hwp_label_lines(text)}
    return {k: v for k, v in mapping.items() if _norm(k) in allowed}


def find_form_document(result) -> Optional[str]:
    """Result의 과제 문서 중 '양식 파일'(hwpx/docx는 주입 가능, .hwp는 값 표로 대체) 경로.

    .hwp(이진)는 셀 단위 주입이 불가능하지만(fill_form_file 미지원), 원본이 있다는
    사실 자체는 알려줘야 write_filled_form이 '채운 값 .docx 표' 대체 경로(C안)를
    태울 수 있다 — 그래서 반환은 하되 호출부가 확장자로 분기한다.
    """
    for d in getattr(result, "documents", None) or []:
        src = str(getattr(d, "source", "") or "")
        suffix = Path(src).suffix.lower()
        if suffix not in (".hwpx", ".docx", ".hwp"):
            continue
        if not Path(src).exists():
            continue
        text = getattr(d, "text", "") or ""
        if suffix == ".hwp":
            if _hwp_looks_like_form(text):
                return src
        elif detect_form(text).is_form:
            return src
    return None
