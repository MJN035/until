"""기억 3계층 테스트 — L1 스타일 카드 / L2 에피소드 / L3 사실 (no-token, 오프라인).

고정하는 계약:
  1. L1은 **자유 서술로 저장되지 않는다** — ToneSpec 필드로만 매핑된다.
  2. L2는 통짜 요약이 아니라 **유사 사례 검색**이고, 예시는 최종본을 쓴다.
  3. L3는 문체와 **분리 저장·분리 주입**되고, 만료된 사실은 주입되지 않는다.
"""
import json
import pathlib
import sys
import tempfile
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.context.episodes import (Episode, clear_episodes, episodes_block,
                                    find_similar, load_episodes, query_from_spec,
                                    record_episode)
from until.context.facts import (FACT_KINDS, active_facts, add_fact, clear_facts,
                                 facts_block, load_facts, make_fact, remove_fact,
                                 save_facts)
from until.context.style_card import (StyleCard, build_style_card, extract_style_fields, merge_card)
from until.context.tone import (PersonaBase, PersonaStore, load_persona,
                                resolve_tone, save_persona)
from until.context.voice import VoiceProfile
from until.llm.base import LLMResult


class _FakeLLM:
    """구조화 출력 흉내 — schema 인자를 실제로 받는지도 함께 확인한다."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def complete(self, system, user, **kw):
        self.calls.append(kw)
        return LLMResult(text=json.dumps(self.payload, ensure_ascii=False),
                         backend="fake")


# ── L1 스타일 카드 ───────────────────────────────────────────────────

def test_style_card_is_structured_not_prose():
    """LLM이 무엇을 뱉든 저장되는 것은 ToneSpec 필드뿐이다."""
    llm = _FakeLLM({
        "speech_level": "해요체", "formality": 2, "deference": 3,
        "warmth": 4, "directness": 2, "endings": ["해요", "할게요"],
        "summary": "따뜻하고 부드러운 말투입니다",     # 자유 서술 — 버려져야 한다
        "made_up_field": "x",
    })
    fields = extract_style_fields(["오늘은 자료를 정리해 봤어요."], llm=llm)
    assert "summary" not in fields and "made_up_field" not in fields
    assert fields["speech_level"] == "해요체" and fields["warmth"] == 4
    assert llm.calls and llm.calls[0].get("schema") is not None   # 스키마 강제
    # llm 없이는 호출 자체가 없다(context/ 기본 결정성 유지).
    assert extract_style_fields(["아무 글"], llm=None) == {}
    print("OK L1 구조화 저장 — 자유 서술 필드 폐기, 스키마 강제")


def test_style_card_llm_overrides_stats_but_survives_failure():
    voice = VoiceProfile(ending_style="한다체", avg_sentence_len=70, n_samples=9)
    stat_card = build_style_card(voice)
    assert stat_card.fields["speech_level"] == "한다체"
    assert stat_card.source == "voice_profile"

    class _Broken:
        def complete(self, *a, **k):
            raise RuntimeError("boom")

    assert build_style_card(voice, ["글"], llm=_Broken()).fields == stat_card.fields

    llm = _FakeLLM({"speech_level": "하십시오체", "formality": 4, "deference": 5,
                    "warmth": 2, "directness": 3})
    rich = build_style_card(voice, ["존경하는 교수님께 말씀드립니다."], llm=llm)
    assert rich.fields["speech_level"] == "하십시오체"   # LLM이 통계를 덮는다
    assert rich.fields["deference"] == 5                 # 통계가 못 재는 축
    assert rich.source == "llm" and rich.notes            # 근거는 notes에만
    print("OK L1 LLM 우선 · 실패 시 통계 카드 생존")


def test_style_card_merge_is_not_overwrite():
    """느리게 변하는 층 — 새 카드가 말하지 않는 축은 지워지지 않는다."""
    old = StyleCard(fields={"speech_level": "해요체", "warmth": 4}, n_samples=20)
    new = StyleCard(fields={"warmth": 2}, n_samples=3, source="edit_patterns")
    merged = merge_card(old, new)
    assert merged.fields["speech_level"] == "해요체"   # 유지
    assert merged.fields["warmth"] == 2                # 갱신
    assert merged.n_samples == 20                      # 표본은 큰 쪽
    assert merge_card(old, StyleCard()).fields == old.fields   # 빈 카드는 무시
    print("OK L1 병합 — 미언급 축 보존, 표본 감소 방지")


def test_style_card_feeds_tone_baseline():
    """L1은 별도 블록이 아니라 ToneSpec 기준선으로 녹아든다."""
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "persona.json"
        store = PersonaStore(style_card=StyleCard(
            fields={"speech_level": "해요체", "greeting": "안녕하세요"}, n_samples=5))
        save_persona(store, p)
        assert load_persona(p).style_card.fields["greeting"] == "안녕하세요"
        # 프리셋이 제약하지 않는 축(greeting)은 카드 값이 살아남는다.
        r = resolve_tone({"task_type": "essay"}, None, path=p)
        assert "안녕하세요" in r.block
        # 사용자가 직접 정한 기준선이 있으면 그쪽이 카드를 이긴다.
        store.base = PersonaBase(defaults={"greeting": "반갑습니다"}, source="user")
        save_persona(store, p)
        assert "반갑습니다" in resolve_tone({}, None, path=p).block
    print("OK L1 → ToneSpec 기준선 · 사용자 설정이 학습을 이김")


# ── L2 에피소드 ──────────────────────────────────────────────────────

def _seed_episodes(p):
    record_episode("도시 공간 관찰 보고서 작성", "초안 A", "최종 A",
                   register_key="academic_prose", task_type="essay", path=p)
    record_episode("실험 결과 보고서 오차 분석", "초안 B", "최종 B",
                   register_key="lab_report", task_type="report", path=p)
    record_episode("도시 관찰 에세이 공간 분석", "초안 C", "최종 C",
                   register_key="academic_prose", task_type="essay", path=p)


def test_episode_roundtrip_and_similarity_search():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "episodes.jsonl"
        _seed_episodes(p)
        eps = load_episodes(p)
        assert len(eps) == 3
        # 통짜가 아니라 검색 — 도시 질의는 도시 사례만 끌어와야 한다.
        hits = find_similar("도시 공간 관찰", k=2, path=p, use_embeddings=False)
        assert hits and all("도시" in h.episode.input_context for h in hits)
        assert hits[0].score > 0
        # 본문이 비면 저장하지 않는다.
        assert record_episode("맥락만 있음", "", "", path=p) is None
        # 깨진 줄·미래 버전은 건너뛴다.
        with p.open("a", encoding="utf-8") as f:
            f.write("{ broken\n")
            f.write(json.dumps({"v": 99, "input_context": "x"}) + "\n")
        assert len(load_episodes(p)) == 3
        clear_episodes(p)
        assert load_episodes(p) == []
    print("OK L2 왕복 · 유사 사례 검색 · 손상 내성")


def test_episode_scope_narrows_then_falls_back():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "episodes.jsonl"
        _seed_episodes(p)
        same = find_similar("도시 공간", register_key="academic_prose", path=p,
                            use_embeddings=False)
        assert same and all(h.episode.register_key == "academic_prose" for h in same)
        # 해당 레지스터 사례가 없으면 빈손 대신 전체에서 찾는다.
        other = find_similar("도시 공간", register_key="form_admin", path=p,
                             use_embeddings=False)
        assert other, "좁힌 결과가 비면 전체 폴백이어야 한다"
    print("OK L2 레지스터 우선 검색 → 없으면 전체 폴백")


def test_episode_example_prefers_final_output():
    """few-shot 예시는 사람이 확정한 최종본 — 초안을 쓰면 에코 챔버가 된다."""
    ep = Episode(episode_id="x", input_context="상황", generated_draft="모델 초안",
                 final_output="사람 최종본")
    assert ep.example_body == "사람 최종본"
    assert Episode(episode_id="y", input_context="상황",
                   generated_draft="모델 초안", final_output="").example_body == "모델 초안"
    print("OK L2 예시는 최종본 우선")


def test_episodes_block_marks_itself_as_style_only():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "episodes.jsonl"
        _seed_episodes(p)
        hits = find_similar("도시 공간", path=p, use_embeddings=False)
        block = episodes_block(hits)
        assert block.startswith("【과거 유사 사례")
        assert "문체·구성 참고용" in block      # 사실 근거로 오인 방지
        assert "옮겨 오지 마라" in block
        assert episodes_block([]) == ""          # 히트 없으면 주입 안 함
    print("OK L2 주입 블록 — 문체 예시임을 명시")


def test_query_from_spec_uses_same_fields_as_retrieval():
    q = query_from_spec({"goal": "도시 관찰", "deliverable": "보고서",
                         "requirements": ["동선 계획"]})
    assert "도시 관찰" in q and "보고서" in q and "동선 계획" in q
    assert query_from_spec(None) == ""
    print("OK L2 질의 구성")


# ── L3 사실 기억 ─────────────────────────────────────────────────────

def test_fact_validation_and_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "facts.json"
        assert make_fact("헛소리", "x", "y") is None          # 어휘 밖 종류
        assert make_fact("사안", "x", "   ") is None          # 빈 내용
        f = make_fact("일정", "졸업논문", "9월 12일까지 초록 제출",
                      source="학과 공지", valid_until="2026-09-12")
        assert f is not None and f.kind in FACT_KINDS
        assert add_fact(f, p)
        assert len(load_facts(p)) == 1
        # 같은 내용은 교체(중복 누적 없음).
        assert add_fact(make_fact("일정", "졸업논문", "9월 12일까지 초록 제출"), p)
        assert len(load_facts(p)) == 1
        assert remove_fact(f.fact_id, p) is True
        assert remove_fact("없는id", p) is False
        # 깨진 날짜는 사실을 죽이지 않고 '만료 없음'이 된다.
        weird = make_fact("결정", "주제", "A안으로 간다", valid_until="언젠가")
        assert weird.valid_until == "" and weird.is_expired() is False
        p.write_text("{ not json", encoding="utf-8")
        assert load_facts(p) == []
        clear_facts(p)
    print("OK L3 검증 · 왕복 · 손상 내성")


def test_expired_facts_are_not_injected():
    """지난 학기 마감이 이번 초안에 살아 나오는 것이 이 층의 대표 실패다."""
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "facts.json"
        save_facts([
            make_fact("일정", "지난 과제", "3월 2일 마감", valid_until="2026-03-02"),
            make_fact("사안", "졸업논문", "지도교수와 주제 확정함"),
        ], p)
        live = active_facts(today=date(2026, 8, 20), path=p)
        assert len(live) == 1 and live[0].kind == "사안"
        block = facts_block(today=date(2026, 8, 20), path=p)
        assert "3월 2일 마감" not in block
        assert "지도교수와 주제 확정함" in block
    print("OK L3 만료 사실 주입 제외")


def test_facts_block_is_separated_from_style():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "facts.json"
        save_facts([make_fact("인물", "김교수", "실험설계 강의 담당"),
                    make_fact("결정", "주제", "도시 공간으로 확정")], p)
        block = facts_block(path=p)
        assert block.startswith("【진행 중 사안")
        assert "문체 예시가 아니다" in block       # 오인 방지 문구
        assert "지어내지 말 것" in block
        assert "[인물]" in block and "[결정]" in block   # 종류별로 묶임
        assert facts_block(facts=[]) == ""
    print("OK L3 별도 섹션 · 종류별 묶음 · 문체 오인 방지")


if __name__ == "__main__":
    test_style_card_is_structured_not_prose()
    test_style_card_llm_overrides_stats_but_survives_failure()
    test_style_card_merge_is_not_overwrite()
    test_style_card_feeds_tone_baseline()
    test_episode_roundtrip_and_similarity_search()
    test_episode_scope_narrows_then_falls_back()
    test_episode_example_prefers_final_output()
    test_episodes_block_marks_itself_as_style_only()
    test_query_from_spec_uses_same_fields_as_retrieval()
    test_fact_validation_and_roundtrip()
    test_expired_facts_are_not_injected()
    test_facts_block_is_separated_from_style()
    print("\n=== test_memory_layers: all passed ===")
