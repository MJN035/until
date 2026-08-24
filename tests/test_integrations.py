"""Offline tests for the new integrations (no SDK / no API key needed)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.llm.base import SourceDoc
from until.llm.request_builder import build_request
from until.optimize.metric import score_and_feedback


def test_request_builder_citations_and_cache():
    docs = [SourceDoc("syllabus.pdf", "A"*100), SourceDoc("reading.pdf", "B"*100)]
    req = build_request("claude-x", "SYS", "질문", 2048, documents=docs, cache=True)
    content = req["messages"][0]["content"]
    doc_blocks = [c for c in content if c["type"] == "document"]
    assert len(doc_blocks) == 2
    # 모든 문서가 citations 활성
    assert all(b["citations"]["enabled"] for b in doc_blocks)
    # 마지막 문서에만 cache_control
    assert "cache_control" not in doc_blocks[0]
    assert doc_blocks[1]["cache_control"]["type"] == "ephemeral"
    # system도 캐시 블록
    assert req["system"][0]["cache_control"]["type"] == "ephemeral"
    # 마지막 블록은 user 텍스트
    assert content[-1]["type"] == "text" and content[-1]["text"] == "질문"
    print("OK request builder — citations + caching")


def test_request_builder_structured_output():
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    req = build_request("claude-x", "SYS", "q", 1024, schema=schema)
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert req["output_config"]["format"]["schema"] == schema
    print("OK request builder — structured outputs")


def test_no_docs_no_cache():
    req = build_request("m", "s", "u", 10, cache=False)
    assert req["messages"][0]["content"][-1]["text"] == "u"
    assert "cache_control" not in req["system"][0]
    print("OK no-docs/no-cache path")


def test_gepa_metric_core():
    # 경계선 넘는 초안 → 낮은 점수 + 위반 feedback
    bad = "나는 Zuboff가 옳다고 본다. 따라서 결론은 정해졌다."
    s_bad, fb_bad = score_and_feedback(bad)
    assert s_bad < 1.0 and "위반" in fb_bad
    # 규칙 지킨 초안 → 1.0
    good = "## 본론\n" + "충분한 분량의 본문. "*20 + "\n[[DECISION: 핵심 논지 선택 — 본인 관점 필요]]\n"
    s_good, fb_good = score_and_feedback(good)
    assert s_good == 1.0 and "통과" in fb_good
    print(f"OK GEPA metric — bad={s_bad:.2f}, good={s_good:.2f}")



def test_local_backend_inlines_documents():
    from until.llm.openai_compat import build_messages
    from until.llm.base import SourceDoc
    msgs = build_messages("SYS", "초안 작성", [SourceDoc("syllabus", "내용A"), SourceDoc("reading", "내용B")])
    assert msgs[0]["role"] == "system" and msgs[0]["content"] == "SYS"
    u = msgs[1]["content"]
    # 번호가 붙은 자료(인용 가능) — [자료1: syllabus], [자료2: reading]
    assert "[자료1: syllabus]" in u and "[자료2: reading]" in u
    assert "내용A" in u and "내용B" in u and u.endswith("초안 작성")
    print("OK local backend — numbered documents inlined (citations degrade gracefully)")


def test_local_backend_max_tokens_configurable():
    # 긴 에세이/finalize 잘림 방지: UNTIL_MAX_TOKENS로 상향 가능(기본 2048).
    import os
    try:
        import openai  # noqa: F401 — 클라이언트 생성에 필요(없으면 스킵: 의존성 0 불변 규칙)
    except ImportError:
        print("SKIP local backend max_tokens (openai 미설치 환경)")
        return
    from until.llm.openai_compat import OpenAICompatClient
    prev = os.environ.get("UNTIL_MAX_TOKENS")
    try:
        os.environ.pop("UNTIL_MAX_TOKENS", None)
        assert OpenAICompatClient(model="m").max_tokens == 2048           # 기본값
        os.environ["UNTIL_MAX_TOKENS"] = "8192"
        assert OpenAICompatClient(model="m").max_tokens == 8192           # 환경변수 상향
        assert OpenAICompatClient(model="m", max_tokens=4096).max_tokens == 4096  # 인자 우선
    finally:
        if prev is None:
            os.environ.pop("UNTIL_MAX_TOKENS", None)
        else:
            os.environ["UNTIL_MAX_TOKENS"] = prev
    print("OK local backend — max_tokens configurable via UNTIL_MAX_TOKENS")


def test_rate_limit_fallback_model():
    # 한도 소진(429) 시 폴백 모델 '사슬' — env(쉼표 복수) 우선, 제공자별 기본, 주 모델 제외.
    from until.llm.openai_compat import fallback_models, fallback_model, _is_rate_limit
    groq = "https://api.groq.com/openai/v1"
    cere = "https://api.cerebras.ai/v1"
    # Groq 기본 사슬: 70b → 8b (주 모델은 건너뜀).
    assert fallback_models(groq, "llama-3.3-70b-versatile", None) == ["llama-3.1-8b-instant"]
    assert fallback_models(groq, "openai/gpt-oss-120b", None) == [
        "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
    # Cerebras 기본: 프리뷰(glm-4.7) 소진 시 프로덕션(gpt-oss-120b)으로.
    assert fallback_models(cere, "zai-glm-4.7", None) == ["gpt-oss-120b"]
    # NVIDIA NIM 기본: Kimi 소진 시 NVIDIA 호스팅 Llama로 강등(주 모델 제외).
    nvidia = "https://integrate.api.nvidia.com/v1"
    assert fallback_models(nvidia, "moonshotai/kimi-k2-instruct", None) == [
        "meta/llama-3.3-70b-instruct", "meta/llama-3.1-8b-instruct"]
    # env가 사슬을 통째로 정의(쉼표), 주 모델과 중복은 제거.
    assert fallback_models(groq, "a", "b, a, c") == ["b", "c"]
    assert fallback_models("http://localhost:11434/v1", "llama3.2", None) == []  # 기본 없음
    # 하위호환 단수형.
    assert fallback_model(groq, "llama-3.3-70b-versatile", None) == "llama-3.1-8b-instant"
    class _E(Exception):
        status_code = 429
    assert _is_rate_limit(_E("boom"))
    assert _is_rate_limit(Exception("Rate limit reached for model"))
    assert not _is_rate_limit(Exception("500 internal error"))
    print("OK 429 fallback chain (env/provider defaults) + detection")


def test_multi_provider_backup_chain():
    # 백업 제공자 사슬(_2, _3, … _9): 번호 순서·중간 빈칸 건너뜀·키 없는 슬롯 제외.
    import os
    try:
        import openai  # noqa: F401 — 클라이언트 생성에 필요(없으면 스킵: 의존성 0 불변 규칙)
    except ImportError:
        print("SKIP multi-provider chain (openai 미설치 환경)")
        return
    from until.llm.openai_compat import OpenAICompatClient
    keys = ["UNTIL_BASE_URL", "UNTIL_API_KEY", "UNTIL_MODEL"]
    for i in range(2, 10):
        keys += [f"UNTIL_BASE_URL_{i}", f"UNTIL_API_KEY_{i}", f"UNTIL_MODEL_{i}"]
    saved = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            os.environ.pop(k, None)
        # 주=Cerebras, _2=NVIDIA(Kimi), _3은 비움(gap), _4=Groq. _5는 base만(키 없음 → 제외).
        os.environ["UNTIL_BASE_URL"] = "https://api.cerebras.ai/v1"
        os.environ["UNTIL_API_KEY"] = "cere"
        os.environ["UNTIL_BASE_URL_2"] = "https://integrate.api.nvidia.com/v1"
        os.environ["UNTIL_API_KEY_2"] = "nv"
        os.environ["UNTIL_MODEL_2"] = "moonshotai/kimi-k2-instruct"
        os.environ["UNTIL_BASE_URL_4"] = "https://api.groq.com/openai/v1"
        os.environ["UNTIL_API_KEY_4"] = "gq"
        os.environ["UNTIL_MODEL_4"] = "llama-3.3-70b-versatile"
        os.environ["UNTIL_BASE_URL_5"] = "https://openrouter.ai/api/v1"  # 키 없음 → 활성 안 됨
        c = OpenAICompatClient(model="gpt-oss-120b")
        # _2(NVIDIA)와 _4(Groq)만 활성 — _3(빈칸)·_5(키 없음)는 제외, 번호 오름차순 유지.
        assert len(c._alts) == 2, [a[1] for a in c._alts]
        assert c._alts[0][1] == "https://integrate.api.nvidia.com/v1"
        assert c._alts[0][2] == "moonshotai/kimi-k2-instruct"
        assert c._alts[0][3] == "UNTIL_MODEL_FALLBACK_2"
        assert c._alts[1][1] == "https://api.groq.com/openai/v1"
        assert c._alts[1][2] == "llama-3.3-70b-versatile"
        # 모델 미지정 슬롯은 주 모델로 폴백(UNTIL_MODEL_N 생략 허용).
        os.environ.pop("UNTIL_MODEL_2", None)
        c2 = OpenAICompatClient(model="primary-x")
        assert c2._alts[0][2] == "primary-x"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("OK multi-provider backup chain (_2.._9, gap-tolerant, key-gated)")


if __name__ == "__main__":
    for fn in [test_request_builder_citations_and_cache, test_request_builder_structured_output,
               test_no_docs_no_cache, test_gepa_metric_core,
               test_local_backend_inlines_documents, test_local_backend_max_tokens_configurable,
               test_rate_limit_fallback_model, test_multi_provider_backup_chain]:
        fn()
    print("\nINTEGRATION TESTS PASS")
