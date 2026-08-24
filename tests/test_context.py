"""Context/Personalization 레이어 테스트 (no-token, 오프라인)."""
import tempfile
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.context.voice import build_voice_profile, voice_from_dir
from until.llm.base import LLMResult
from until.context.retrieval import keywords_from_spec, find_relevant
from until.context.bundle import assemble_context

SPEC = {
    "deliverable": "도시 관찰 보고서 초안",
    "goal": "도시의 공간을 관찰하고 질문·가설·동선 계획과 인사이트를 정리",
    "requirements": ["관찰 대상 선정", "관찰 방법과 동선", "얻고 싶은 인사이트"],
}


def test_voice_profile():
    v = build_voice_profile(["골목을 돌아봤어요. 사람들이 어떻게 쓰는지 궁금했어요. 다음엔 질문을 정하고 가려고요."])
    assert v.n_samples == 1
    assert v.ending_style == "해요체"      # 종결어미 탐지
    assert v.avg_sentence_len > 0
    assert v.to_prompt_hint()              # 문체 지침 생성
    # 종결어미 판정 회귀 케이스: 합니다체('갑니다/아닙니다') vs 한다체('아니다').
    from until.context.voice import _detect_ending
    assert _detect_ending(["형식 결정론으로 갑니다", "그건 사실이 아닙니다"]) == "합니다체"
    assert _detect_ending(["그것은 우연이 아니다", "이것이 핵심이다"]) == "한다체"
    print(f"OK voice — {v.ending_style}, 평균 {v.avg_sentence_len}자, 자주 {v.frequent_terms[:3]}")


def test_voice_profile_llm_enhancement(tmp_path=None):
    class FakeLLM:
        def complete(self, *args, **kwargs):
            return LLMResult(
                text='{"summary":"장면을 먼저 묘사하고 질문을 덧붙이는 말투",'
                     '"frequent_terms":["장면","질문"]}',
                backend="fake",
            )

    with tempfile.TemporaryDirectory() as d:
        folder = pathlib.Path(d)
        (folder / "sample.md").write_text("골목을 돌아봤어요. 사람들이 어떻게 쓰는지 궁금했어요.", encoding="utf-8")
        v = voice_from_dir(str(folder), llm=FakeLLM())
    assert v.llm_summary and "장면" in v.to_prompt_hint()
    assert "질문" in v.frequent_terms
    print("OK voice LLM enhancement")


def test_retrieval_finds_relevant_files():
    kws = keywords_from_spec(SPEC)
    assert "관찰" in kws or any("관찰" in k for k in kws)
    hits = find_relevant("examples/my_files", kws, k=3)
    assert hits, "내 파일에서 관련 파일을 찾아야 함"
    assert hits[0].score > 0 and hits[0].matched
    print(f"OK retrieval — {len(hits)}건, top={pathlib.Path(hits[0].document.source).name} 점수 {hits[0].score}")


def test_retrieval_can_use_embedding_similarity():
    with tempfile.TemporaryDirectory() as d:
        tmp_path = pathlib.Path(d)
        (tmp_path / "walk.md").write_text("골목의 보행 흐름과 사람들이 머무는 위치를 기록했다.", encoding="utf-8")
        (tmp_path / "food.md").write_text("파스타 조리법과 소스 배합을 정리했다.", encoding="utf-8")

        class FakeEmbedder:
            def encode(self, texts):
                out = []
                for text in texts:
                    if "도시" in text or "관찰" in text or "골목" in text or "보행" in text:
                        out.append([1.0, 0.0])
                    else:
                        out.append([0.0, 1.0])
                return out

        hits = find_relevant(str(tmp_path), ["도시", "관찰"], k=1, embedder=FakeEmbedder())
        assert hits and pathlib.Path(hits[0].document.source).name == "walk.md"
        assert hits[0].matched == ["embedding"]
        print(f"OK retrieval embeddings — top={pathlib.Path(hits[0].document.source).name} 점수 {hits[0].score:.2f}")


def test_assemble_context_bundle():
    ctx = assemble_context(
        SPEC,
        course_dir="examples/course_materials",
        my_files_dir="examples/my_files",
        voice_dir="examples/voice_samples",
    )
    srcs = ctx.to_sources()
    assert srcs, "수업자료+내 파일이 SourceDoc로 모여야 함"
    assert any(s.title.startswith("[수업자료]") for s in srcs)
    assert any(s.title.startswith("[내 파일]") for s in srcs)
    assert ctx.voice.n_samples > 0 and ctx.voice_hint
    print("OK bundle —", ctx.summary())


def test_empty_context_is_safe():
    ctx = assemble_context(SPEC)  # 폴더 미지정
    assert ctx.to_sources() == [] and ctx.voice_hint == ""
    print("OK empty context (맥락 미지정 시 안전)")


if __name__ == "__main__":
    for fn in [test_voice_profile, test_voice_profile_llm_enhancement,
               test_retrieval_finds_relevant_files,
               test_retrieval_can_use_embedding_similarity,
               test_assemble_context_bundle, test_empty_context_is_safe]:
        fn()
    print("\nCONTEXT TESTS PASS")
