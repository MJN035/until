"""P10 — eTL 관련자료 자동수집 + 순위화 테스트 (네트워크 불필요)."""
import json, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.capture.sources.canvas_api import parse_modules, parse_canvas_files
from until.capture.sources.models import Attachment
from until.context.etl_materials import (
    collect_material_refs, rank_materials, collect_related_materials, materials_to_sources,
)

BASE = "https://myetl.snu.ac.kr"
_MODULES = pathlib.Path("examples/canvas_fixture/modules_api.json")
_FILES = pathlib.Path("examples/canvas_fixture/files_api.json")

# 도시 관찰 과제 spec(키워드: 도시/관찰/역사 ...).
_SPEC = {
    "deliverable": "에세이",
    "goal": "도시의 공간과 역사를 관찰하고 분석",
    "requirements": ["도시 관찰 방법 적용", "역사적 맥락 정리"],
}


def test_parse_modules_items():
    mods = parse_modules(json.loads(_MODULES.read_text(encoding="utf-8")), BASE)
    names = [m.name for m in mods]
    assert any("도시 관찰 방법론.pdf" in n for n in names)
    assert any("[1주차 도시 읽기]" in n for n in names)   # 모듈명 라벨 포함
    assert all(m.url.startswith("https://") for m in mods)
    print("OK parse modules items")


class _FakeAdapter:
    """파일·모듈을 fixture로 돌려주는 가짜 어댑터(네트워크 없음)."""
    def list_course_files(self, course_id, base_url):
        return parse_canvas_files(json.loads(_FILES.read_text(encoding="utf-8")), base_url)
    def list_modules(self, course_id, base_url):
        return parse_modules(json.loads(_MODULES.read_text(encoding="utf-8")), base_url)


def test_collect_and_rank():
    ad = _FakeAdapter()
    refs = collect_material_refs(ad, "302199", BASE)
    assert len(refs) >= 3   # 파일 + 모듈 항목 합쳐짐
    hits = collect_related_materials(ad, "302199", _SPEC, BASE, k=5)
    assert hits, "관련 자료가 잡혀야 함"
    # 점수 내림차순 정렬.
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    # '도시 관찰 방법론'(도시+관찰 매칭)이 최상위 = 가장 관련 높은 자료.
    assert "도시 관찰 방법론" in hits[0].name
    top = hits[0].score
    # '통계 기초 정리'가 들어와도(흔한 단어 '정리' 매칭) 도시 자료보다 점수가 낮아야 함.
    stat = next((h.score for h in hits if "통계" in h.name), 0)
    assert stat < top, "관련 낮은 자료는 더 낮은 점수"
    print("OK collect + rank related materials (도시 관찰 최상위)")


def test_prefetched_refs_avoid_duplicate_listing():
    """관련자료와 분산 명세가 같은 과목 목록을 공유해 API 왕복을 중복하지 않는다."""
    class _CountingAdapter(_FakeAdapter):
        def __init__(self):
            self.files_calls = 0
            self.modules_calls = 0

        def list_course_files(self, course_id, base_url):
            self.files_calls += 1
            return super().list_course_files(course_id, base_url)

        def list_modules(self, course_id, base_url):
            self.modules_calls += 1
            return super().list_modules(course_id, base_url)

    from until.context.distributed_spec import collect_distributed_spec
    adapter = _CountingAdapter()
    refs = collect_material_refs(adapter, "302199", BASE)
    collect_related_materials(adapter, "302199", _SPEC, BASE, refs=refs)
    # 이 제목은 분산 명세 게이트를 통과하지만, 전달한 refs만 사용한다.
    collect_distributed_spec(adapter, "302199", BASE, "숙제3", "제출", refs=refs)
    assert adapter.files_calls == 1 and adapter.modules_calls == 1
    print("OK prefetched material refs reused without duplicate listing")


def test_materials_to_sources():
    ad = _FakeAdapter()
    hits = collect_related_materials(ad, "302199", _SPEC, BASE, k=5)
    srcs = materials_to_sources(hits)
    # 자료마다 SourceDoc 하나 — 파일명이 범례([자료N])에 그대로 보인다.
    assert len(srcs) == len(hits)
    assert srcs[0].title == f"[eTL 자료] {hits[0].name}"
    assert "도시 관찰 방법론" in srcs[0].title
    # 본문 미수집 사실이 텍스트에 명시(제목만으로 내용 지어내 인용 방지).
    assert "본문은 수집하지 않았" in srcs[0].text
    assert materials_to_sources([]) == []   # 빈 입력
    print("OK materials -> SourceDoc for Execution (per-file)")


def test_korean_concatenated_filename_matches():
    # 붙여쓰는 한국어 파일명도 부분문자열로 매칭돼야 한다(토큰 완전일치로는 누락).
    mats = [
        Attachment(name="도시문화론_3주차.pdf", url="https://x/files/1"),  # '도시' 포함
        Attachment(name="통계학개론.pdf", url="https://x/files/2"),        # 무관
    ]
    hits = rank_materials(mats, ["도시", "관찰"], k=5)
    names = [h.name for h in hits]
    assert any("도시문화론" in n for n in names), names
    assert all("통계학개론" not in n for n in names), names
    print("OK Korean concatenated filename matched by substring")


def test_generic_words_do_not_match():
    # '과제'·'제출' 같은 일반어만 겹치는 자료는 관련자료로 뽑히면 안 된다
    # (실관측: 무관한 '<서비스디자인> 팀과제 제출' 모듈이 초안 근거로 인용됨).
    mats = [
        Attachment(name="<서비스디자인> 팀과제 제출 [2주차 모듈]", url="https://x/m/1"),
        Attachment(name="도시 관찰 방법론.pdf", url="https://x/files/1"),
    ]
    hits = rank_materials(mats, ["과제", "제출", "도시", "관찰"], k=5)
    names = [h.name for h in hits]
    assert all("서비스디자인" not in n for n in names), names
    assert any("도시 관찰" in n for n in names), names
    print("OK generic words excluded from matching")


def test_same_file_deduped_across_sources():
    # 같은 파일(id 동일)이 파일 탭과 모듈에서 다른 URL·표기로 잡혀도 한 번만.
    # 반대로 이름이 같아도 파일 id가 다르면(주차별 동명 항목) 둘 다 살아야 한다(리뷰 발견).
    class _DupAdapter:
        def list_course_files(self, course_id, base_url):
            return [Attachment(name="SWP강의노트-도시.pdf", url="https://x/files/9")]
        def list_modules(self, course_id, base_url):
            return [
                Attachment(name="SWP강의노트-도시.pdf [1주차 모듈]",
                           url="https://x/files/9/download"),      # 같은 파일 id=9
                Attachment(name="발표 자료 [1주차 모듈]", url="https://x/files/21/download"),
                Attachment(name="발표 자료 [12주차 모듈]", url="https://x/files/22/download"),
            ]
    refs = collect_material_refs(_DupAdapter(), "302199", BASE)
    names = [r.name for r in refs]
    assert names.count("SWP강의노트-도시.pdf") == 1              # id 동일 → 합침
    assert "SWP강의노트-도시.pdf [1주차 모듈]" not in names       # 파일 탭 쪽이 남음
    assert len([n for n in names if n.startswith("발표 자료")]) == 2  # id 다름 → 둘 다
    print("OK dedup by file id (same file merged, distinct same-name kept)")


def test_fetch_material_texts_top_files():
    # 상위 파일형 자료만 본문 다운로드→파싱, 페이지/링크형은 스킵, 실패는 조용히.
    from until.context.etl_materials import fetch_material_texts, MaterialHit
    import pathlib as _pl
    sample = _pl.Path("examples/sample_assignment.txt").resolve()

    class _DlAdapter:
        def download(self, att, dest):
            import shutil
            dst = _pl.Path(dest) / (att.name + ".txt")
            shutil.copyfile(sample, dst)
            return str(dst)

    hits = [
        MaterialHit(name="도시 강의노트.pdf", url="https://x/files/9", score=2, matched=["도시"]),
        MaterialHit(name="외부 링크", url="https://example.com/page", score=1, matched=["도시"]),
        MaterialHit(name="둘째 파일.pdf [1주차 모듈]", url="https://x/files/10", score=1, matched=["도시"]),
        MaterialHit(name="셋째 파일.pdf", url="https://x/files/11", score=1, matched=["도시"]),
    ]
    texts = fetch_material_texts(_DlAdapter(), hits, top=2, chars=200)
    assert set(texts) == {"도시 강의노트.pdf", "둘째 파일.pdf [1주차 모듈]"}  # 파일형 상위 2건만
    assert all(len(v) <= 220 for v in texts.values())                      # 발췌 절단
    # 본문이 있으면 SourceDoc에 실제 발췌가, 모든 자료에 eTL 위치(URL)가 담긴다.
    srcs = materials_to_sources(hits, texts)
    assert "본문 발췌" in srcs[0].text and "본문은 수집하지 않았" in srcs[1].text
    assert "eTL 위치: https://x/files/9" in srcs[0].text
    assert "eTL 위치: https://example.com/page" in srcs[1].text
    # 다운로드가 없는 어댑터/실패 → 빈 결과(제목-만 폴백 유지).
    assert fetch_material_texts(None, hits) == {}

    # 이진 파일이 확장자 없이 저장돼 텍스트 폴백으로 깨져도 발췌에 주입되지 않는다
    # (리뷰 발견 — 대체문자 비율 백스톱).
    class _BinAdapter:
        def download(self, att, dest):
            dst = _pl.Path(dest) / att.name          # 확장자 없음
            dst.write_bytes(b"\xff\xfe" * 400)       # utf-8/cp949 모두 무효 → � 폭탄
            return str(dst)
    bin_hits = [MaterialHit(name="3주차 강의노트", url="https://x/files/77/download",
                            score=1, matched=["도시"])]
    assert fetch_material_texts(_BinAdapter(), bin_hits, top=1) == {}
    from until.context.etl_materials import _looks_garbled
    assert _looks_garbled("�" * 100) and not _looks_garbled("정상 한국어 텍스트")
    print("OK fetch material texts (top-2 files, excerpt, garbled backstop)")


def test_moodle_pluginfile_url_matches():
    # Moodle fileurl(pluginfile.php)도 파일형으로 인식돼 다운로드 대상이 된다.
    from until.context.etl_materials import fetch_material_texts, MaterialHit
    import pathlib as _pl
    sample = _pl.Path("examples/sample_assignment.txt").resolve()

    class _DlAdapter:
        def download(self, att, dest):
            import shutil
            dst = _pl.Path(dest) / (att.name + ".txt")
            shutil.copyfile(sample, dst)
            return str(dst)

    hits = [MaterialHit(
        name="강의노트.pdf",
        url="https://myetl.snu.ac.kr/webservice/pluginfile.php/1/mod_resource/content/0/강의노트.pdf",
        score=2, matched=["도시"])]
    texts = fetch_material_texts(_DlAdapter(), hits, top=1, chars=200)
    assert set(texts) == {"강의노트.pdf"}
    print("OK Moodle pluginfile URL 인식")


def test_material_size_cap_skips_large_files():
    # 파일당 용량 상한 초과 파일은 파싱하지 않고 스킵(무차별 대용량 수집 방지).
    import os
    from until.context.etl_materials import fetch_material_texts, MaterialHit
    import pathlib as _pl

    class _BigAdapter:
        def download(self, att, dest):
            dst = _pl.Path(dest) / (att.name + ".txt")
            dst.write_text("가" * (3 * 1024 * 1024), encoding="utf-8")  # ~9MB
            return str(dst)

    hits = [MaterialHit(name="대용량.pdf", url="https://x/files/1", score=1, matched=["도시"])]
    saved = os.environ.get("UNTIL_MATERIAL_MAX_MB")
    os.environ["UNTIL_MATERIAL_MAX_MB"] = "1"  # 1MB 상한 → 스킵
    try:
        assert fetch_material_texts(_BigAdapter(), hits, top=1) == {}
    finally:
        if saved is None:
            os.environ.pop("UNTIL_MATERIAL_MAX_MB", None)
        else:
            os.environ["UNTIL_MATERIAL_MAX_MB"] = saved
    print("OK 용량 상한 초과 파일 스킵")


def test_pipeline_accepts_extra_sources():
    from until.config import Config
    from until.pipeline import run
    ad = _FakeAdapter()
    srcs = materials_to_sources(collect_related_materials(ad, "302199", _SPEC, BASE))
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg, extra_context_sources=srcs)
    assert res.draft.n_decisions >= 1   # 추가 맥락이 있어도 정상 동작
    print("OK pipeline accepts extra_context_sources")


if __name__ == "__main__":
    test_parse_modules_items()
    test_collect_and_rank()
    test_prefetched_refs_avoid_duplicate_listing()
    test_materials_to_sources()
    test_korean_concatenated_filename_matches()
    test_generic_words_do_not_match()
    test_same_file_deduped_across_sources()
    test_fetch_material_texts_top_files()
    test_moodle_pluginfile_url_matches()
    test_material_size_cap_skips_large_files()
    test_pipeline_accepts_extra_sources()
    print("\nMATERIALS TEST PASS")
