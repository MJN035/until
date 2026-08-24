"""eTL 소스 커넥터 오프라인 테스트 (FixtureBrowserAdapter, 로그인/네트워크 불필요)."""
import sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.capture.sources.etl import EtlSource, ChromeBrowserAdapter
from until.capture.sources.collect import collect_etl_fixture
from until.capture.ingest import ingest_all


def test_etl_fixture_collect_and_ingest():
    with tempfile.TemporaryDirectory() as d:
        collected, files = collect_etl_fixture("examples/etl_fixture", d)
        # 과제 메타 + 첨부 다운로드
        assert collected.title and collected.course
        assert len(collected.attachments) == 2
        assert all(a.local_path for a in collected.attachments)
        # to_files: assignment.md + 첨부 2개 = 3개
        assert len(files) == 3
        # 그대로 파이프라인 Capture에 흘러감
        docs = ingest_all(files)
        assert len(docs) == 3 and all(doc.n_chars > 0 for doc in docs)
    print("OK eTL fixture — collect→download→ingest")


def test_chrome_adapter_is_stub():
    src = EtlSource("https://etl.snu.ac.kr/mod/assign/view.php?id=1", ChromeBrowserAdapter())
    raised = False
    try:
        src.collect("/tmp/x")
    except NotImplementedError:
        raised = True
    assert raised, "라이브 어댑터는 아직 스텁이어야 함"
    print("OK ChromeBrowserAdapter stub raises (라이브 구현 안내)")


if __name__ == "__main__":
    test_etl_fixture_collect_and_ingest()
    test_chrome_adapter_is_stub()
    print("\nETL SOURCE TESTS PASS")
