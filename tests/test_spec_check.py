"""Canvas API 스펙 대조 판정 로직 테스트 — 오프라인·결정적(네트워크 없음)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from run_spec_check import REQUIRED, evaluate_docs, missing_endpoints


def _doc(*apis):
    return {"apis": [
        {"path": p, "operations": [
            {"method": m, "parameters": [{"name": n} for n in params]}]}
        for p, m, params in apis
    ]}


def _full_docs():
    """REQUIRED 전부를 만족하는 최소 문서 셋(실제 Swagger 1.2 형태 축약)."""
    docs = {}
    for resource, method, path, params in REQUIRED:
        doc = docs.setdefault(resource, {"apis": []})
        doc["apis"].append({"path": path, "operations": [
            {"method": method,
             "parameters": [{"name": f"{p}[]"} for p in params]}]})
    return docs


def test_all_present():
    assert missing_endpoints(_full_docs(), REQUIRED) == []
    print("OK all present (배열 표기 student_ids[] 정규화 포함)")


def test_missing_path_reported():
    docs = _full_docs()
    docs["submissions"]["apis"] = []  # 제출물 엔드포인트 제거 시뮬레이션
    probs = missing_endpoints(docs, REQUIRED)
    assert any("students/submissions" in p and "사라짐" in p for p in probs)
    print("OK missing path reported")


def test_missing_param_and_method():
    docs = _full_docs()
    # 파라미터 제거 → 파라미터 경고
    for api in docs["submissions"]["apis"]:
        api["operations"][0]["parameters"] = []
    probs = missing_endpoints(docs, REQUIRED)
    assert any("student_ids" in p for p in probs)
    # 메서드가 다르면(POST만 남음) 사라진 것으로 판정
    docs2 = _full_docs()
    docs2["courses"]["apis"][0]["operations"][0]["method"] = "POST"
    probs2 = missing_endpoints(docs2, REQUIRED)
    assert any(p.startswith("courses:") for p in probs2)
    print("OK missing param + method mismatch")


def test_unreadable_doc():
    docs = _full_docs()
    docs["users"] = None  # 다운로드 실패 시뮬레이션
    probs = missing_endpoints(docs, REQUIRED)
    assert any(p.startswith("users:") and "읽지 못함" in p for p in probs)
    # httpMethod(구형 키) 표기도 인식
    docs3 = _full_docs()
    op = docs3["modules"]["apis"][0]["operations"][0]
    op["httpMethod"] = op.pop("method")
    assert not any("modules" in p for p in missing_endpoints(docs3, REQUIRED))
    print("OK unreadable doc + httpMethod key")


def test_evaluate_docs_skip_vs_removed():
    # 네트워크 실패(None)는 '확인 불가(SKIP)' — 어긋남 목록에 안 들어감(exit 0 근거).
    docs = _full_docs()
    docs["users"] = None
    problems, skipped = evaluate_docs(docs, REQUIRED)
    assert problems == [] and skipped == ["users"], (problems, skipped)
    # 문서를 받았는데 엔드포인트가 사라진 경우는 여전히 어긋남(exit 1 근거).
    docs2 = _full_docs()
    docs2["users"] = None
    docs2["courses"]["apis"] = []
    problems2, skipped2 = evaluate_docs(docs2, REQUIRED)
    assert any(p.startswith("courses:") and "사라짐" in p for p in problems2)
    assert skipped2 == ["users"] and not any("users" in p for p in problems2)
    print("OK evaluate_docs separates SKIP(fetch fail) from removed endpoints")


def test_fetch_docs_retries_transient_failure():
    # 일시 실패 2회 후 성공 — 재시도로 회복(백오프 sleep은 무력화).
    import run_spec_check as rsc
    calls = {"n": 0}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"apis": []}'

    def fake_urlopen(url, timeout=30):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("connection reset")
        return _Resp()

    orig_open, orig_sleep = rsc.urllib.request.urlopen, rsc.time.sleep
    rsc.urllib.request.urlopen = fake_urlopen
    rsc.time.sleep = lambda _s: None
    try:
        docs = rsc.fetch_docs("http://spec.test", ["courses"])
    finally:
        rsc.urllib.request.urlopen = orig_open
        rsc.time.sleep = orig_sleep
    assert docs["courses"] == {"apis": []} and calls["n"] == 3, calls
    print("OK fetch_docs retries transient failures")


if __name__ == "__main__":
    test_all_present()
    test_missing_path_reported()
    test_missing_param_and_method()
    test_unreadable_doc()
    test_evaluate_docs_skip_vs_removed()
    test_fetch_docs_retries_transient_failure()
    print("\nSPEC CHECK TESTS PASS")
