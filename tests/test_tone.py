"""톤 레지스터 테스트 (no-token, 오프라인).

핵심 계약 3가지를 고정한다.
  1. render_tone_spec은 결정적 — 같은 ToneSpec이면 항상 같은 바이트(SHA-256 고정).
  2. 상속 + 델타 — 프리셋이 제약하지 않은 축에는 페르소나 기준선이 살아남고,
     사용자가 못박은 override는 프리셋을 이긴다.
  3. 기능 플래그 off면 파이프라인 출력이 기존과 완전히 동일하다.
"""
import hashlib
import json
import os
import sys
import pathlib
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.context.tone import (
    DEFAULT_REGISTER, REGISTER_PRESETS, PersonaBase, PersonaStore,
    RegisterOverride, ToneSpec, base_from_voice, clear_persona, load_persona,
    render_tone_spec, resolve_register_key, resolve_tone, resolve_tone_spec,
    sanitize_delta, save_persona, tone_fingerprint,
)
from until.context.voice import VoiceProfile


class _Route:
    def __init__(self, strategy):
        self.strategy = strategy


def test_render_is_deterministic():
    """같은 ToneSpec → 항상 같은 문자열. 이 성질이 프롬프트 A/B의 전제다."""
    spec = resolve_tone_spec("inquiry_to_professor")
    first = render_tone_spec(spec)
    for _ in range(5):
        assert render_tone_spec(resolve_tone_spec("inquiry_to_professor")) == first
    # 필드가 같으면 인스턴스가 달라도 같은 문자열이어야 한다.
    clone = ToneSpec(**{f: getattr(spec, f) for f in spec.__dataclass_fields__})
    assert render_tone_spec(clone) == first
    assert tone_fingerprint(clone) == tone_fingerprint(spec)
    digest = hashlib.sha256(first.encode("utf-8")).hexdigest()[:12]
    print(f"OK 결정적 직렬화 — inquiry_to_professor sha={digest} len={len(first)}")


def test_every_preset_renders():
    """8개 프리셋 전부 예외 없이 렌더되고 서로 다른 지문을 갖는다."""
    seen = {}
    for key in REGISTER_PRESETS:
        tone = resolve_tone_spec(key)
        text = render_tone_spec(tone)
        assert text.startswith("【톤 레지스터"), key
        assert f"register: {key}" in text
        # 경계선 우선 규칙은 모든 레지스터에 빠짐없이 들어가야 한다.
        assert "[[DECISION]]" in text, key
        seen.setdefault(tone_fingerprint(tone), []).append(key)
    collisions = {k: v for k, v in seen.items() if len(v) > 1}
    assert not collisions, f"프리셋 지문 충돌: {collisions}"
    print(f"OK 프리셋 {len(REGISTER_PRESETS)}종 렌더 — 지문 전부 고유")


def test_inheritance_and_delta():
    """프리셋은 제약하는 축만 덮고, 사용자 override는 프리셋을 이긴다."""
    # academic_prose는 greeting/closing을 제약하지 않는다 → 기준선이 살아남는다.
    base = PersonaBase(defaults={"greeting": "안녕하세요", "warmth": 5})
    tone = resolve_tone_spec("academic_prose", base=base)
    assert tone.greeting == "안녕하세요"        # 프리셋이 안 건드린 축 → 기준선 유지
    assert tone.warmth == 1                     # 프리셋이 제약한 축 → 프리셋 승

    # 사용자가 못박으면 프리셋을 이긴다.
    over = RegisterOverride(delta={"warmth": 4}, pinned=True)
    tone2 = resolve_tone_spec("academic_prose", base=base, override=over)
    assert tone2.warmth == 4
    assert tone2.speech_level == "한다체"       # 건드리지 않은 축은 그대로
    print("OK 상속+델타 — 기준선 유지 / 프리셋 승 / override 최종")


def test_unknown_register_falls_back():
    """미정의 레지스터 키는 새 프리셋을 만들지 않고 기본 프리셋으로 떨어진다."""
    tone = resolve_tone_spec("존재하지_않는_레지스터")
    assert tone.register_key == DEFAULT_REGISTER
    print("OK 미정의 키 폴백 →", DEFAULT_REGISTER)


def test_register_key_resolution_order():
    """명시 지정 > 라우팅 전략 > 과제 유형 > 기본."""
    spec = {"task_type": "essay"}
    assert resolve_register_key(spec, _Route("weekly_inquiry")) == \
        ("inquiry_to_professor", "inferred")           # 전략이 유형을 이긴다
    assert resolve_register_key(spec, _Route("알수없는전략")) == \
        ("academic_prose", "inferred")                 # 유형 폴백
    assert resolve_register_key({}, None) == (DEFAULT_REGISTER, "default")
    # 명시 지정은 무엇이든 이긴다.
    assert resolve_register_key(spec, _Route("weekly_inquiry"),
                                explicit="team_coordination") == \
        ("team_coordination", "explicit")
    # 유효하지 않은 명시값은 무시되고 자동 추론으로 되돌아간다(조용한 오작동 방지).
    assert resolve_register_key(spec, _Route("weekly_inquiry"), explicit="없는키") == \
        ("inquiry_to_professor", "inferred")
    # 라우터가 실제로 내는 전략 전부가 매핑되거나 기본으로 떨어져야 한다(예외 금지).
    from until.context.assignment_router import route_for_strategy  # noqa: F401
    for strategy in ("weekly_inquiry", "team_project", "activity_form",
                     "code_project", "evidence_report", "non_actionable"):
        key, src = resolve_register_key({}, _Route(strategy))
        assert key in REGISTER_PRESETS, (strategy, key)
        assert src in ("inferred", "default")
    print("OK 레지스터 결정 순서 — explicit > strategy > task_type > default")


def test_specific_task_type_beats_generic_strategy():
    """일반 전략이 유형 분류기의 의도적 구분을 덮지 않는다.

    회귀 출처: CO-Week 참가결과보고서는 task_type.py가 '보고서'라는 단어의 오분류를
    막으려고 reflective_report로 따로 분류해 둔 유형인데, 라우터는 일반 바구니인
    evidence_report로 잡는다. 전략을 무조건 우선하면 그 보정이 통째로 무효가 되어
    소감문이 실험 보고서 톤으로 나온다(run_tone_ab.py가 실제로 잡아낸 건).
    """
    coweek = {"task_type": "reflective_report"}
    assert resolve_register_key(coweek, _Route("evidence_report")) == \
        ("reflective", "inferred")
    assert resolve_register_key({"task_type": "inquiry"},
                                _Route("spec_clarification")) == \
        ("inquiry_to_professor", "inferred")
    # 구체 전략은 여전히 이긴다 — 예외는 '일반 전략'에만 적용된다.
    assert resolve_register_key(coweek, _Route("team_project")) == \
        ("team_coordination", "inferred")
    # 구체적이지 않은 유형은 예외 대상이 아니다(일반 전략이 그대로 이긴다).
    assert resolve_register_key({"task_type": "essay"}, _Route("evidence_report")) == \
        ("lab_report", "inferred")
    print("OK 구체 유형이 일반 전략을 이김 — reflective_report·inquiry 보정 보존")


def test_sanitize_rejects_garbage():
    """손상·조작 입력이 톤 규격을 오염시키지 않는다."""
    dirty = {"speech_level": "야자체", "formality": 99, "deference": -3,
             "emoji_policy": "무제한", "self_reference": "짐",
             "banned": "  대박  ", "unknown_field": "x", "target_sentences": "abc"}
    clean = sanitize_delta(dirty)
    assert "speech_level" not in clean          # 열거형 밖 → 통째로 버림
    assert clean["formality"] == 5              # 상한 클램프
    assert clean["deference"] == 1              # 하한 클램프
    assert "emoji_policy" not in clean
    assert "self_reference" not in clean
    assert clean["banned"] == ["대박"]           # 공백 정규화 + 리스트화
    assert "unknown_field" not in clean
    assert clean["target_sentences"] == 0       # 숫자 아님 → 0(미지정)
    assert sanitize_delta("nonsense") == {}
    print("OK 입력 검증 — 열거형 밖·범위 밖·미지 필드 전부 차단")


def test_voice_profile_is_absorbed_not_replaced():
    """VoiceProfile은 버려지지 않고 페르소나 기준선의 입력 소스가 된다."""
    empty = base_from_voice(VoiceProfile())
    assert empty.defaults == {} and empty.source == "default"

    voice = VoiceProfile(ending_style="해요체", avg_sentence_len=22,
                         uses_emoji=True, n_samples=7)
    base = base_from_voice(voice)
    assert base.source == "voice_profile"
    assert base.defaults["speech_level"] == "해요체"
    assert base.defaults["formality"] == 2       # 짧은 문장 → 구어체 쪽
    assert base.defaults["emoji_policy"] == "최소"
    # 하지만 수신자가 없는 레지스터는 프리셋이 한다체로 되돌린다.
    assert resolve_tone_spec("academic_prose", base=base).speech_level == "한다체"
    # 수신자가 있는 팀 문서에서는 이 사람의 해요체가 그대로 산다.
    assert resolve_tone_spec("team_coordination", base=base).speech_level == "해요체"
    print("OK VoiceProfile 흡수 — 기준선 반영 + 레지스터 요구가 우선")


def test_persona_roundtrip_and_corruption():
    """저장→로드 왕복. 손상 파일·미래 버전은 조용히 빈 스토어."""
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "persona.json"
        store = PersonaStore(
            base=PersonaBase(actor_id="u1", defaults={"warmth": 4}, source="user"),
            registers={"reflective": RegisterOverride(delta={"deference": 5},
                                                      pinned=True)},
            pinned_register="reflective")
        save_persona(store, p)
        back = load_persona(p)
        assert back.base.actor_id == "u1"
        assert back.base.defaults == {"warmth": 4}
        assert back.registers["reflective"].delta == {"deference": 5}
        assert back.registers["reflective"].pinned is True
        assert back.pinned_register == "reflective"

        p.write_text("{ not json", encoding="utf-8")
        assert load_persona(p).base.defaults == {}
        p.write_text(json.dumps({"v": 99, "base": {"defaults": {"warmth": 5}}}),
                     encoding="utf-8")
        assert load_persona(p).base.defaults == {}   # 미래 버전 → 무시
        clear_persona(p)
        assert not p.exists()
        clear_persona(p)                             # 없는 파일 삭제도 조용히
    print("OK 페르소나 저장 왕복 · 손상/미래버전 폴백")


def test_pinned_register_wins_in_resolve_tone():
    """저장된 pinned_register가 자동 추론을 이긴다(명시 지정 경로 분리)."""
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "persona.json"
        save_persona(PersonaStore(pinned_register="team_coordination"), p)
        r = resolve_tone({"task_type": "essay"}, _Route("weekly_inquiry"), path=p)
        assert r.register_key == "team_coordination"
        assert r.source == "explicit"
        # 저장이 없으면 자동 추론으로 돌아온다.
        clear_persona(p)
        r2 = resolve_tone({"task_type": "essay"}, _Route("weekly_inquiry"), path=p)
        assert (r2.register_key, r2.source) == ("inquiry_to_professor", "inferred")
        assert r2.block and r2.fingerprint
    print("OK pinned_register가 자동 추론을 이김")


def test_resolve_tone_never_raises():
    """어떤 입력이 와도 예외를 내지 않는다 — 톤 때문에 초안이 막히면 안 된다."""
    for spec, route in ((None, None), ({}, object()), ({"task_type": 3}, _Route(7))):
        r = resolve_tone(spec, route, path=pathlib.Path("/없는/경로/persona.json"))
        assert r.register_key in REGISTER_PRESETS
        assert r.block.startswith("【톤 레지스터")
    print("OK 어떤 입력에도 예외 없음")


def _run_pipeline(tmpdir, flag):
    """플래그 상태만 바꿔 mock 파이프라인을 한 번 돌리고 초안 본문을 돌려준다."""
    import importlib
    from until import config as cfgmod
    from until.config import Config
    import until.pipeline as pl
    from until.context import tone as tonemod

    src = pathlib.Path(tmpdir) / "assignment.txt"
    src.write_text("에세이 과제: 도시 공간에 대해 논하시오. 1500자 이상 서술하시오.",
                   encoding="utf-8")
    old = os.environ.get("UNTIL_TONE_REGISTER")
    os.environ["UNTIL_TONE_REGISTER"] = flag
    tonemod.set_persona_path_override(pathlib.Path(tmpdir) / "persona.json")
    try:
        importlib.reload(cfgmod)  # .env 로드 경로 재평가(게이트는 매 호출 os.getenv)
        res = pl.run([str(src)], Config(backend="mock"))
        return res
    finally:
        tonemod.set_persona_path_override(None)
        if old is None:
            os.environ.pop("UNTIL_TONE_REGISTER", None)
        else:
            os.environ["UNTIL_TONE_REGISTER"] = old


def test_flag_off_keeps_existing_behaviour():
    """플래그 off면 톤 필드가 비고, on이면 채워진다. mock 출력은 결정적이다."""
    with tempfile.TemporaryDirectory() as d:
        off = _run_pipeline(d, "0")
        assert off.tone_block == "" and off.tone_register == ""
        assert "register_key" not in off.spec
        body_off = off.draft.body
    with tempfile.TemporaryDirectory() as d:
        off2 = _run_pipeline(d, "0")
        assert off2.draft.body == body_off      # 기존 경로는 여전히 결정적
    with tempfile.TemporaryDirectory() as d:
        on = _run_pipeline(d, "1")
        assert on.tone_block.startswith("【톤 레지스터")
        assert on.tone_register in REGISTER_PRESETS
        assert on.tone_source in ("explicit", "inferred", "default")
        assert on.spec.get("register_key") == on.tone_register
    print(f"OK 플래그 off=기존 동작 / on={on.tone_register}({on.tone_source})")


def test_session_roundtrip_keeps_tone():
    """Result에 필드를 늘렸으니 세션 직렬화도 함께 살아 있어야 한다."""
    from until import session_store
    with tempfile.TemporaryDirectory() as d:
        res = _run_pipeline(d, "1")
        blob = session_store.encode({"result": res, "answers": None,
                                     "suggestions": None, "review": None}, ts=0.0)
        back = session_store.decode(blob)
        assert back is not None
        assert back["result"].tone_block == res.tone_block
        assert back["result"].tone_register == res.tone_register
        assert back["result"].tone_source == res.tone_source
    print("OK 세션 왕복에서 톤 규격 보존")


if __name__ == "__main__":
    test_render_is_deterministic()
    test_every_preset_renders()
    test_inheritance_and_delta()
    test_unknown_register_falls_back()
    test_register_key_resolution_order()
    test_specific_task_type_beats_generic_strategy()
    test_sanitize_rejects_garbage()
    test_voice_profile_is_absorbed_not_replaced()
    test_persona_roundtrip_and_corruption()
    test_pinned_register_wins_in_resolve_tone()
    test_resolve_tone_never_raises()
    test_flag_off_keeps_existing_behaviour()
    test_session_roundtrip_keeps_tone()
    print("\n=== test_tone: all passed ===")
