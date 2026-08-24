"""
Capture layer (문서 파싱) — the *no-token* stage. (※ 서울대 LMS 'eTL'과 무관)

Deterministic, no LLM. Two backends:
  - "docling": IBM Docling (MIT) — 고품질 마크다운 + 구조 인식 (PDF/표/헤딩). 권장.
    https://github.com/docling-project/docling
  - "basic":   PyMuPDF/텍스트 + 정규식 섹션화 — 의존성 최소 폴백.
"docling"이 설치돼 있지 않거나 실패하면 자동으로 "basic"으로 폴백한다.
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import List, Tuple

from .models import Document, Section


# ── 섹션화 (공통, no-LLM) ────────────────────────────────────────────
def _split_sections(text: str) -> List[Section]:
    sections: List[Section] = []
    cur_head, buf = "본문", []
    for line in text.splitlines():
        is_md = line.lstrip().startswith("#")
        is_caps = bool(re.match(r"^[A-Z0-9][A-Z0-9 \-:]{3,}$", line.strip()))
        if is_md or is_caps:
            if buf:
                sections.append(Section(cur_head, "\n".join(buf).strip()))
                buf = []
            cur_head = line.lstrip("# ").strip()
        else:
            buf.append(line)
    if buf:
        sections.append(Section(cur_head, "\n".join(buf).strip()))
    return [s for s in sections if s.text]


# ── 백엔드별 텍스트 추출 ─────────────────────────────────────────────
def _read_with_docling(path: Path) -> str:
    """Docling으로 마크다운 추출. 미설치/실패 시 ImportError/Exception을 올려 폴백 유도."""
    from docling.document_converter import DocumentConverter  # type: ignore
    converter = DocumentConverter()
    result = converter.convert(str(path))
    return result.document.export_to_markdown()


def _read_text_robust(path: Path) -> str:
    """한국어 환경 인코딩 견고화 — UTF-8(BOM 포함) → cp949(윈도우 한국어) → 대체문자.

    학생 파일은 메모장 저장(cp949·UTF-8 BOM)이 흔해 UTF-8 고정이면 파싱이 통째로 실패한다.
    """
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


_ZIP_MEMBER_CAP = 50 * 1024 * 1024  # 압축 해제 50MB 상한 — zip 폭탄 OOM 방지


def _zip_read_capped(z, name: str) -> bytes:
    """압축 해제 크기를 사전 검사 후 읽기(작은 크래프트 파일이 수백 MB로 부풀지 않게)."""
    info = z.getinfo(name)
    if info.file_size > _ZIP_MEMBER_CAP:
        raise RuntimeError(f"압축 해제 크기가 너무 큼({info.file_size // (1024*1024)}MB)")
    return z.read(name)


def _local(tag: str) -> str:
    """'{ns}name' → 'name' (네임스페이스 버전 차이 무시)."""
    return tag.rsplit("}", 1)[-1]


def _cell_text(tc, t_name: str) -> str:
    """표 셀 안의 텍스트를 한 줄로(셀 내 문단은 공백 연결)."""
    return " ".join(t.text.strip() for t in tc.iter()
                    if _local(t.tag) == t_name and t.text and t.text.strip()).strip()


def _table_lines(tbl, *, tr: str, tc: str, t: str) -> List[str]:
    """표 요소 → 마크다운 표 행들. 셀 구조(빈 칸 포함)를 보존한다 —
    평탄화하면 양식(보고서 서식)의 칸 구조가 LLM에 전달되지 않는다."""
    rows = []
    for row in (e for e in tbl.iter() if _local(e.tag) == tr):
        cells = [_cell_text(c, t) for c in row.iter() if _local(c.tag) == tc]
        if cells:
            rows.append("| " + " | ".join(c.replace("|", "／") for c in cells) + " |")
    if rows:  # 마크다운 표 문법(첫 행 뒤 구분선) — 렌더러·후속 파서 호환
        n = rows[0].count("|") - 1
        rows.insert(1, "|" + "---|" * max(n, 1))
    return rows


def _read_docx(path: Path) -> str:
    """docx 내장 폴백(zipfile+XML, 의존성 0) — 문단은 순서대로, 표는 구조 보존."""
    import zipfile
    from xml.etree import ElementTree as ET
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(_zip_read_capped(z, "word/document.xml"))
    body = next((e for e in root.iter() if _local(e.tag) == "body"), root)
    out: List[str] = []

    def _walk(el) -> None:
        # p/tbl이 아닌 래퍼(w:sdt 콘텐츠 컨트롤·sdtContent 등)는 자식으로 재귀 —
        # Word 양식/템플릿에서 흔한 구조라 통째로 건너뛰면 과제 요건이 유실된다.
        # 문서 순서는 자식 순회 순서 그대로 보존된다.
        for child in el:
            name = _local(child.tag)
            if name == "tbl":
                out.extend(_table_lines(child, tr="tr", tc="tc", t="t"))
            elif name == "p":
                text = "".join(t.text or "" for t in child.iter() if _local(t.tag) == "t")
                if text.strip():
                    out.append(text.strip())
            else:
                _walk(child)

    _walk(body)
    return "\n".join(out)


def _read_pptx(path: Path) -> str:
    """pptx 내장 폴백 — 슬라이드 순서대로 텍스트 상자 추출."""
    import re
    import zipfile
    from xml.etree import ElementTree as ET
    a = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    out = []
    with zipfile.ZipFile(path) as z:
        slides = sorted((n for n in z.namelist()
                         if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                        key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[1]).group()))
        for name in slides:
            root = ET.fromstring(_zip_read_capped(z, name))
            texts = [t.text for t in root.iter(f"{a}t") if t.text and t.text.strip()]
            if texts:
                out.append("\n".join(texts))
    return "\n\n".join(out)


def _read_hwpx(path: Path) -> str:
    """hwpx(한글 2010+, OWPML zip) 내장 폴백 — 섹션 순서대로 문단 텍스트 추출.

    네임스페이스 버전 차이를 타지 않도록 로컬명(}p·}t)으로 순회한다.
    (이진 .hwp는 _read_hwp가 별도로 읽는다.)
    """
    import re
    import zipfile
    from xml.etree import ElementTree as ET
    out = []
    with zipfile.ZipFile(path) as z:
        sections = sorted(
            (n for n in z.namelist() if re.fullmatch(r"Contents/section\d+\.xml", n)),
            key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[1]).group()))
        for name in sections:
            root = ET.fromstring(_zip_read_capped(z, name))
            paras = []
            # 최상위 문단만 순회 — 표 안의 문단은 표 렌더(_table_lines)가 셀 구조
            # 그대로 담는다(전부 평탄화하면 양식의 칸 구조가 사라짐).
            for p in (e for e in root if _local(e.tag) == "p"):
                tbls = [e for e in p.iter() if _local(e.tag) == "tbl"]
                if tbls:
                    # 표 내부 요소를 표시해 두고(중복 방지), 표는 구조 보존으로 담는다.
                    in_tbl = {id(e) for tbl in tbls for e in tbl.iter()}
                    for tbl in tbls:
                        lines = _table_lines(tbl, tr="tr", tc="tc", t="t")
                        if lines:
                            paras.append("\n".join(lines))
                    # 같은 문단의 표 밖 텍스트(표 앞 안내문 등)도 버리지 않고 담는다 —
                    # continue만 하면 '아래 표를 채우시오' 같은 지시문이 유실된다.
                    outside = [t.text for t in p.iter()
                               if _local(t.tag) == "t" and id(t) not in in_tbl
                               and t.text and t.text.strip()]
                    if outside:
                        paras.append("".join(outside).strip())
                    continue
                texts = [t.text for t in p.iter()
                         if _local(t.tag) == "t" and t.text and t.text.strip()]
                if texts:
                    paras.append("".join(texts).strip())
            if not paras:  # 루트 구조가 예상과 다르면 구버전 방식(전체 평탄화) 폴백
                for p in root.iter():
                    if _local(p.tag) == "p":
                        texts = [t.text for t in p.iter()
                                 if _local(t.tag) == "t" and t.text and t.text.strip()]
                        if texts:
                            paras.append("".join(texts).strip())
            if paras:
                out.append("\n".join(paras))
    if not out:
        raise RuntimeError("hwpx에서 본문을 찾지 못했어요")
    return "\n\n".join(out)


# ── .hwp(한글 5.x 이진, OLE 컨테이너) 내장 폴백 ──────────────────────────────
# eTL 실코퍼스에서 교수 첨부 1위 포맷(.hwp 11건)이라 '변환해 주세요' 예외만으로는
# 본문 없는 과제(33%)의 명세를 놓친다. 표준 라이브러리만으로 텍스트를 뽑는다:
# CFB(OLE) 최소 리더 → BodyText/Section* 스트림 zlib 해제 → PARA_TEXT(태그 67)
# 레코드의 UTF-16LE 문단 텍스트. 암호화·배포용 문서는 명확한 예외 → 경고 표면화.

def _cfb_streams(data: bytes) -> dict:
    """CFB(OLE2) 최소 리더 — {스트림명: 바이트}. 계층은 평탄화(이름만 사용).

    HWP가 쓰는 부분집합만 지원: FAT/미니FAT 체인, 루트 미니스트림. 손상 파일은
    예외(호출부가 경고로 수렴). 스트림명 충돌은 HWP 구조상 Section류가 BodyText에만
    있어 문제되지 않는다(배포용 ViewText는 암호화 플래그에서 먼저 걸러진다).
    """
    import struct
    if len(data) < 512 or data[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise RuntimeError("OLE 컨테이너가 아니에요")
    ssz = 1 << struct.unpack_from("<H", data, 30)[0]
    mssz = 1 << struct.unpack_from("<H", data, 32)[0]
    n_fat = struct.unpack_from("<I", data, 44)[0]
    dir_start = struct.unpack_from("<i", data, 48)[0]
    mini_cutoff = struct.unpack_from("<I", data, 56)[0]
    minifat_start = struct.unpack_from("<i", data, 60)[0]
    difat_start = struct.unpack_from("<i", data, 68)[0]
    n_difat = struct.unpack_from("<I", data, 72)[0]

    def sector(i: int) -> bytes:
        off = 512 + i * ssz
        if off < 512 or off + ssz > len(data):
            raise RuntimeError("OLE 섹터 범위 밖(손상 파일)")
        return data[off: off + ssz]

    per = ssz // 4
    difat = [v for v in struct.unpack_from("<109i", data, 76)]
    s = difat_start
    # n_difat는 무검증 헤더 값 — 파일 크기상 존재 가능한 섹터 수로 클램프하고,
    # 방문 섹터를 기억해 자기/순환 참조 시 즉시 중단(chain()과 같은 패턴).
    # 안 그러면 1KB 조작 파일(n_difat=거대값+자기참조)로 메모리 폭주·행이 가능.
    seen_difat: set = set()
    for _ in range(min(n_difat, max((len(data) - 512) // ssz, 0))):
        if s < 0 or s in seen_difat:
            break
        seen_difat.add(s)
        vals = struct.unpack(f"<{per}i", sector(s))
        difat.extend(vals[:-1])
        s = vals[-1]
    fat: list = []
    for fs in [f for f in difat if f >= 0][:n_fat]:
        fat.extend(struct.unpack(f"<{per}i", sector(fs)))

    def chain(start: int) -> list:
        out, s, seen = [], start, set()
        while 0 <= s < len(fat) and s not in seen:
            seen.add(s)
            out.append(s)
            if len(out) * ssz > _ZIP_MEMBER_CAP:
                raise RuntimeError("OLE 스트림이 너무 큼")
            s = fat[s]
        return out

    def read_chain(start: int, size: int) -> bytes:
        return b"".join(sector(i) for i in chain(start))[:size]

    dirdata = b"".join(sector(i) for i in chain(dir_start))
    entries = []
    for off in range(0, len(dirdata) - 127, 128):
        e = dirdata[off: off + 128]
        nlen = struct.unpack_from("<H", e, 64)[0]
        if not (2 <= nlen <= 64):
            continue
        name = e[: nlen - 2].decode("utf-16-le", "ignore")
        entries.append((name, e[66],
                        struct.unpack_from("<i", e, 116)[0],
                        struct.unpack_from("<Q", e, 120)[0]))
    root = next((e for e in entries if e[1] == 5), None)
    minifat: list = []
    if minifat_start >= 0:
        for i in chain(minifat_start):
            minifat.extend(struct.unpack(f"<{per}i", sector(i)))
    ministream = read_chain(root[2], root[3]) if root and root[2] >= 0 else b""

    def read_mini(start: int, size: int) -> bytes:
        out, s, seen = [], start, set()
        while 0 <= s < len(minifat) and s not in seen:
            seen.add(s)
            out.append(ministream[s * mssz: (s + 1) * mssz])
            s = minifat[s]
        return b"".join(out)[:size]

    streams = {}
    for name, etype, start, size in entries:
        if etype != 2 or start < 0:
            continue
        streams[name] = (read_mini(start, size) if size < mini_cutoff
                         else read_chain(start, size))
    return streams


#: PARA_TEXT 안에서 부가 정보 7워드가 따라붙는 인라인/확장 컨트롤 문자 코드.
_HWP_CTRL_EXTENDED = frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12,
                                14, 15, 16, 17, 18, 19, 20, 21, 22, 23})


def _hwp_para_text(payload: bytes) -> str:
    """PARA_TEXT 레코드 페이로드(UTF-16LE + 컨트롤) → 평문."""
    spans: List[bytes] = []
    i, n = 0, len(payload) - (len(payload) % 2)
    run_start = None
    while i < n:
        c = int.from_bytes(payload[i:i + 2], "little")
        if c < 32:
            if run_start is not None:
                spans.append(payload[run_start:i])
                run_start = None
            if c in (10, 13):
                spans.append(b"\n\x00")
            i += 16 if c in _HWP_CTRL_EXTENDED else 2
            continue
        if run_start is None:
            run_start = i
        i += 2
    if run_start is not None:
        spans.append(payload[run_start:n])
    return b"".join(spans).decode("utf-16-le", "ignore")


def _read_hwp(path: Path) -> str:
    """hwp 5.x 이진 내장 폴백 — BodyText 섹션 순서대로 문단 텍스트 추출."""
    import re
    import zlib
    streams = _cfb_streams(path.read_bytes())
    fh = streams.get("FileHeader") or b""
    if not fh.startswith(b"HWP Document File"):
        raise RuntimeError("hwp 5.x 형식이 아니에요 — PDF/HWPX로 변환해 주세요")
    flags = int.from_bytes(fh[36:40], "little") if len(fh) >= 40 else 0
    if flags & 0x2 or flags & 0x4:
        raise RuntimeError("암호/배포용 .hwp는 읽을 수 없어요 — 한글에서 PDF로 변환해 주세요")
    compressed = bool(flags & 0x1)
    names = sorted((n for n in streams if re.fullmatch(r"Section\d+", n)),
                   key=lambda n: int(n[7:]))
    paras: List[str] = []
    for name in names:
        raw = streams[name]
        if compressed:
            try:
                d = zlib.decompressobj(-15)
                raw = d.decompress(raw, _ZIP_MEMBER_CAP)
                if d.unconsumed_tail:
                    raise RuntimeError("압축 해제 크기가 너무 큼")
            except zlib.error:
                continue  # 손상 섹션은 건너뛰고 나머지에서 추출
        pos = 0
        while pos + 4 <= len(raw):
            hdr = int.from_bytes(raw[pos:pos + 4], "little")
            pos += 4
            tag, size = hdr & 0x3FF, (hdr >> 20) & 0xFFF
            if size == 0xFFF:
                if pos + 4 > len(raw):
                    break
                size = int.from_bytes(raw[pos:pos + 4], "little")
                pos += 4
            if tag == 67:  # HWPTAG_PARA_TEXT
                t = _hwp_para_text(raw[pos:pos + size]).strip()
                if t:
                    paras.append(t)
            pos += size
    if not paras:
        raise RuntimeError(".hwp에서 본문을 찾지 못했어요")
    return "\n".join(paras)


def _read_html_text(path: Path) -> str:
    """html 내장 폴백 — 태그 제거 텍스트(script/style 제외)."""
    from html.parser import HTMLParser

    class _T(HTMLParser):
        def __init__(self):
            super().__init__()
            self.buf: List[str] = []
            self._skip = 0

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style"):
                self._skip += 1

        def handle_endtag(self, tag):
            if tag in ("script", "style") and self._skip:
                self._skip -= 1
            if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr"):
                self.buf.append("\n")

        def handle_data(self, data):
            if not self._skip and data.strip():
                self.buf.append(data)

    t = _T()
    t.feed(_read_text_robust(path))
    t.close()  # 미호출이면 '&amp'류로 끝나는 텍스트 런 전체가 버퍼에 갇혀 유실됨
    lines = [" ".join(ln.split()) for ln in "".join(t.buf).splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _read_rmd(path: Path) -> str:
    """R Markdown 과제 템플릿을 실행하지 않고 문제·청크·답안 슬롯으로 구조화한다."""
    text = _read_text_robust(path)
    # 청크 실행은 사람 환경의 데이터·패키지에 달려 있다. Capture에서는 코드와
    # Todo 위치만 보존하고 어떤 코드도 실행하지 않는다.
    text = re.sub(r"```\{r([^}]*)\}", r"```r\n# [R 청크\1]", text)
    text = re.sub(r"(?im)^\s*#{2,}\s*todo\s*#{2,}\s*$",
                  "[[ANSWER_SLOT: 이 위치의 코드 또는 해설을 작성]]", text)
    return "[RMD_TEMPLATE: 원본 순서·코드 청크·답안 슬롯을 유지]\n\n" + text


def _read_zip_project(path: Path) -> str:
    """ZIP 프로젝트의 명세·소스만 안전하게 읽는다(추출·실행 없음)."""
    import zipfile
    text_exts = {".py", ".r", ".rmd", ".md", ".txt", ".csv", ".json",
                 ".yaml", ".yml", ".toml", ".ini", ".c", ".h", ".cpp",
                 ".java", ".js", ".ts", ".html", ".css", ".ino"}
    ignored = {"__pycache__", ".git", ".idea", "node_modules", ".venv"}
    blocks, total, kept = ["[ZIP_PROJECT: 파일을 실행하지 않고 구조만 읽음]"], 0, 0
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if len(infos) > 2000:
            raise RuntimeError(f"ZIP 항목이 너무 많아요({len(infos)}개)")
        for info in infos:
            name = info.filename.replace("\\", "/")
            parts = [p for p in name.split("/") if p]
            if (info.is_dir() or not parts or ".." in parts
                    or any(p in ignored for p in parts)):
                continue
            if info.file_size > _ZIP_MEMBER_CAP:
                raise RuntimeError(f"ZIP 내부 파일이 너무 커요: {name}")
            suffix = Path(parts[-1]).suffix.lower()
            if suffix == ".pdf":
                try:
                    import fitz
                    data = _zip_read_capped(z, info.filename)
                    doc = fitz.open(stream=data, filetype="pdf")
                    body = "\n".join(page.get_text() for page in doc)
                except Exception:
                    body = "[PDF 본문 추출 실패]"
            elif suffix in text_exts:
                data = _zip_read_capped(z, info.filename)
                body = next((data.decode(enc) for enc in ("utf-8-sig", "cp949")
                             if _can_decode(data, enc)), data.decode("utf-8", "replace"))
            else:
                continue
            total += len(body)
            if total > 2_000_000:
                raise RuntimeError("ZIP에서 읽을 텍스트가 2MB를 넘어요")
            blocks.append(f"\n## FILE: {name}\n{body[:100_000]}")
            kept += 1
    if not kept:
        raise RuntimeError("ZIP 안에서 읽을 수 있는 명세·소스 파일을 찾지 못했어요")
    return "\n".join(blocks)


def _can_decode(data: bytes, encoding: str) -> bool:
    try:
        data.decode(encoding)
        return True
    except UnicodeDecodeError:
        return False


def _read_basic(path: Path) -> Tuple[str, str]:
    suffix = path.suffix.lower()
    # Canvas/Google Drive 다운로드는 Content-Disposition에 확장자가 없는 경우가
    # 있다(실코퍼스 `질의순번 리스트` 11건). 이름보다 시그니처를 먼저 보고,
    # HTML 태그 덩어리를 평문 근거로 흘려보내지 않는다.
    if not suffix:
        head = path.read_bytes()[:512].lstrip().lower()
        if head.startswith((b"<!doctype html", b"<html")):
            return "html(sniffed)", _read_html_text(path)
    if suffix == ".pdf":
        try:
            import fitz  # PyMuPDF
        except ImportError as e:
            raise RuntimeError("PDF엔 docling 또는 pymupdf 필요: pip install docling") from e
        doc = fitz.open(path)
        return "pdf", "\n".join(page.get_text() for page in doc)
    # Office/HTML은 docling 없이도 내장 폴백으로 — zip 바이트를 텍스트로 읽어
    # 깨진 글자가 초안에 흘러들지 않게 한다(실패는 예외 → 경고로 표면화).
    if suffix == ".docx":
        return "docx", _read_docx(path)
    if suffix == ".pptx":
        return "pptx", _read_pptx(path)
    if suffix == ".hwpx":
        return "hwpx", _read_hwpx(path)
    if suffix == ".hwp":
        return "hwp", _read_hwp(path)
    if suffix in (".html", ".htm"):
        return "html", _read_html_text(path)
    if suffix == ".rmd":
        return "rmd-template", _read_rmd(path)
    if suffix == ".zip":
        return "zip-project", _read_zip_project(path)
    if suffix in (".md", ".markdown"):
        return "markdown", _read_text_robust(path)
    if suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        raise RuntimeError(f"{suffix} 이미지는 아직 OCR할 수 없어요 — PDF나 텍스트 설명을 함께 주세요")
    # 이진 문서 포맷은 텍스트로 읽으면 깨진 글자만 남는다 — 명확한 예외로
    # ingest_all_with_warnings 경고에 잡히게(초안 오염 방지).
    if suffix in (".doc", ".xlsx"):
        raise RuntimeError(f"{suffix} 형식은 아직 지원하지 않아요 — PDF/DOCX/HWPX/텍스트로 변환해 주세요")
    return "text", _read_text_robust(path)


def ingest_file(path: str | Path, backend: str = "auto") -> Document:
    """
    backend: "auto"(docling 시도 후 basic 폴백) | "docling" | "basic".
    결정적·토큰 0.
    """
    p = Path(path)
    kind = p.suffix.lower().lstrip(".") or "text"
    text: str | None = None

    if backend in ("auto", "docling") and p.suffix.lower() in (".pdf", ".docx", ".pptx", ".html"):
        try:
            text = _read_with_docling(p)
            kind = f"{kind}(docling)"
        except Exception:
            if backend == "docling":
                raise
            text = None  # 폴백

    if text is None:
        kind, text = _read_basic(p)

    text = text.strip()
    return Document(
        source=str(p),
        kind=kind,
        text=text,
        sections=_split_sections(text),
        n_chars=len(text),
        n_tokens_est=len(text) // 4,
    )


def ingest_all_with_warnings(
    paths: list[str | Path], backend: str = "auto",
) -> Tuple[List[Document], List[str]]:
    """파일들을 Document로 파싱하고, 스킵된 파일 경고 목록을 함께 돌려준다.

    첨부 하나가 전체 실행을 막지 않도록 실패 파일은 건너뛰되, 무엇이 빠졌는지는
    경고로 표면화한다(조용히 버리면 초안이 그 자료 없이 쓰인 걸 사람이 모른다).
    모든 파일이 실패하면 예외.
    """
    docs: List[Document] = []
    warnings: List[str] = []
    for p in paths:
        try:
            docs.append(ingest_file(p, backend=backend))
        except Exception as e:  # PDF 라이브러리 미설치 등 — 해당 파일만 스킵
            warnings.append(f"{Path(p).name}: {e}")
    if not docs:
        raise RuntimeError("파싱 가능한 파일이 없습니다. " + " | ".join(warnings))
    return docs, warnings


def ingest_all(paths: list[str | Path], backend: str = "auto") -> List[Document]:
    """(하위호환) 경고 없이 Document 목록만."""
    docs, _ = ingest_all_with_warnings(paths, backend=backend)
    return docs
