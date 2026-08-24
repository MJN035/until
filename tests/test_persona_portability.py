"""크로스채널 대비(PHASE 3) 테스트 — 이벤트 스키마 · 버전 기록 · 이동성 · 삭제.

고정하는 계약:
  1. 페르소나는 **채널이 아니라 actor에 귀속**되고, 채널은 태그일 뿐이다.
  2. prompt_version / model_version 이 실제로 남는다 — 모르면 지어내지 않는다.
  3. export/import 왕복에서 데이터가 보존되고, 남의 파일은 예외 없이 **거부**된다.
  4. 개인 식별 정보는 본문과 분리된 절에만 있고, 빼고 내보낼 수 있다.
  5. 전체 삭제가 **USER_DATA_FILES 전부**를 지운다(목록에서 빠지면 테스트가 깨진다).
"""
import json
import pathlib
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.persona.events import (KNOWN_CHANNELS, PersonaEvent, clear_events,
                                  describe, event_from_result, load_events,
                                  make_event, record_event,
                                  set_events_path_override)
from until.persona.portability import (SCHEMA_VERSION, export_persona,
                                       export_to_file, import_from_file,
                                       import_persona)
from until.persona.retention import (KV_KEY_PREFIXES, RETENTION_DAYS,
                                     USER_DATA_FILES, delete_all_user_data,
                                     kv_keys_for, purge_expired)
from until.persona.versions import (PROMPT_VERSION, model_fingerprint,
                                    normalize_models, prompt_fingerprint,
                                    resolve_model_version, resolve_prompt_version,
                                    used_fallback)


class _Draft:
    def __init__(self, body):
        self.body = body


class _Route:
    strategy = "staged_writing"


class _Result:
    """파이프라인 Result의 덕 타이핑 스텁 — 어댑터가 import 없이 읽는지 확인."""
    def __init__(self, final=""):
        self.spec = {"goal": "도시 공간 관찰", "deliverable": "보고서",
                     "requirements": ["동선 계획"], "task_type": "essay"}
        self.draft = _Draft("초안 문단 하나.\n\n초안 문단 둘.")
        self.final_draft = _Draft(final) if final else None
        self.tone_register = "academic_prose"
        self.tone_source = "inferred"
        self.assignment_route = _Route()
        self.needs_approval = False
        self.llm_usage = {"llm_calls": 3, "models": ["cerebras/x", "groq/y"]}
        self.prompt_version = "1.0.0+abc123abc123"
        self.model_version = "cerebras/x+groq/y"


# ── 버전 기록 ────────────────────────────────────────────────────────

def test_prompt_fingerprint_respects_boundaries():
    """('ab','c')와 ('a','bc')가 같은 지문이면 서로 다른 조립을 같다고 보고한다."""
    assert prompt_fingerprint("ab", "c") != prompt_fingerprint("a", "bc")
    assert prompt_fingerprint("x", "y") == prompt_fingerprint("x", "y")
    v = resolve_prompt_version("시스템 지시", "톤 규격")
    assert v.startswith(PROMPT_VERSION + "+") and len(v.split("+")[1]) == 12
    assert resolve_prompt_version() == PROMPT_VERSION
    print(f"OK 프롬프트 지문 — {v}")


def test_model_version_records_actual_responder():
    """설정값이 아니라 **응답한 모델**. 폴백이면 그 사실까지 남는다."""
    res = _Result()
    assert resolve_model_version(res) == "cerebras/x+groq/y"
    assert used_fallback(res) is True
    single = _Result()
    single.llm_usage = {"models": ["cerebras/x"]}
    assert used_fallback(single) is False

    class _Cfg:
        model = "claude-sonnet-4-6"
    empty = _Result()
    empty.llm_usage = {}
    assert resolve_model_version(empty, config=_Cfg()) == "claude-sonnet-4-6"
    # 아무것도 모르면 빈 문자열 — 지어내지 않는다.
    assert resolve_model_version(empty) == ""
    assert normalize_models(["a", "a", " ", "b"]) == ["a", "b"]
    print("OK 모델 버전 — 실제 응답자 · 폴백 표기 · 모르면 빈 값")


def test_model_fingerprint_is_telemetry_safe():
    """모델명은 자유 문자열이라 텔레메트리에 이름 대신 지문을 싣는다."""
    fp = model_fingerprint("cerebras/llama-3.3-70b")
    assert len(fp) == 12 and all(c in "0123456789abcdef" for c in fp)
    assert fp == model_fingerprint("cerebras/llama-3.3-70b")     # 안정적
    assert fp != model_fingerprint("groq/other")
    assert model_fingerprint("") == ""
    from until.telemetry.schema import _allowed_string
    assert _allowed_string(fp) and _allowed_string(resolve_prompt_version("a"))
    print(f"OK 모델 지문 — {fp} (텔레메트리 허용 형식)")


def test_pipeline_records_provenance():
    """실제 파이프라인 실행에 두 버전이 실린다(빈 값으로 통과하지 않는다)."""
    from until.config import Config
    import until.pipeline as pl
    with tempfile.TemporaryDirectory() as d:
        src = pathlib.Path(d) / "a.txt"
        src.write_text("# 에세이\n\n논하시오.\n", encoding="utf-8")
        res = pl.run([str(src)], Config(backend="mock"))
        assert res.prompt_version.startswith(PROMPT_VERSION)
        assert res.model_version == "mock"
        from until import session_store
        blob = session_store.encode({"result": res, "answers": None,
                                     "suggestions": None, "review": None}, ts=0.0)
        back = session_store.decode(blob)["result"]
        assert back.prompt_version == res.prompt_version
        assert back.model_version == res.model_version
    print(f"OK 파이프라인 출처 기록 — {res.prompt_version} / {res.model_version}")


# ── 채널 중립 이벤트 ─────────────────────────────────────────────────

def test_event_schema_is_channel_neutral():
    """페르소나는 actor에 귀속되고 채널은 태그다 — 채널을 강제하지 않는다."""
    names = set(PersonaEvent.__dataclass_fields__)
    required = {"event_id", "actor_id", "channel", "register_key", "task_type",
                "recipient_ref", "input_context", "generated_draft", "final_output",
                "edit_diff", "accepted", "latency_ms", "model_version",
                "prompt_version", "created_at", "raw_payload"}
    assert required <= names, sorted(required - names)
    # 알려지지 않은 채널도 받는다(새 채널이 붙어도 스키마를 안 고쳐도 된다).
    e = make_event(channel="carrier_pigeon", actor_id="u1",
                   generated_draft="본문", input_context="상황")
    assert e is not None and e.channel == "carrier_pigeon" and e.actor_id == "u1"
    assert "web" in KNOWN_CHANNELS
    # 같은 actor면 채널이 달라도 같은 페르소나에 귀속된다(actor_id가 좌표).
    e2 = make_event(channel="email", actor_id="u1", generated_draft="본문",
                    input_context="상황")
    assert e2.actor_id == e.actor_id
    # 본문이 전부 비면 적립하지 않는다.
    assert make_event(channel="web", input_context="상황만") is None
    print("OK 채널 중립 스키마 — actor 귀속, 채널은 태그")


def test_raw_payload_keeps_original_and_survives_junk():
    """정규화가 틀렸을 때 돌아갈 자리 — 단, JSON 불가 값이 파일을 죽이면 안 된다."""
    e = make_event(channel="etl", generated_draft="본문", input_context="상황",
                   raw_payload={"course_id": 42, "when": datetime(2026, 8, 20)})
    assert e.raw_payload["course_id"] == 42          # 원본 보관
    assert isinstance(e.raw_payload["when"], str)    # 직렬화 불가 → 문자열로
    huge = make_event(channel="etl", generated_draft="본문", input_context="상황",
                      raw_payload={"blob": "x" * 20000})
    assert huge.raw_payload.get("_truncated") is True
    bad = make_event(channel="etl", generated_draft="본문", input_context="상황",
                     raw_payload="not a dict")
    assert bad.raw_payload == {}
    print("OK raw_payload 원본 보관 · 절단 · 타입 방어")


def test_event_roundtrip_and_corruption():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "persona_events.jsonl"
        assert record_event(make_event(channel="web", generated_draft="A",
                                       input_context="상황 A"), p) is not None
        assert record_event(None, p) is None
        rows = load_events(p)
        assert len(rows) == 1 and rows[0].channel == "web"
        with p.open("a", encoding="utf-8") as f:
            f.write("깨진 줄\n")
            f.write(json.dumps({"v": 99, "event_id": "z"}) + "\n")
            f.write(json.dumps({"v": 1}) + "\n")        # event_id 없음
        assert len(load_events(p)) == 1
        assert "이벤트 1건" in describe(p)
        clear_events(p)
        assert load_events(p) == []
    print("OK 이벤트 왕복 · 손상 내성")


def test_event_from_result_normalizes_pipeline_output():
    res = _Result(final="최종 문단 하나.\n\n최종 문단 둘 수정됨.")
    e = event_from_result(res, channel="web", actor_id="u9", latency_ms=1234)
    assert e.register_key == "academic_prose" and e.task_type == "essay"
    assert e.model_version == "cerebras/x+groq/y"
    assert e.prompt_version == "1.0.0+abc123abc123"
    assert e.latency_ms == 1234
    # **최종본이 있다는 것은 수락이 아니다** — 증거가 생길 때까지 미상으로 둔다.
    assert e.accepted is None
    assert e.edit_diff and "수정" in e.edit_diff        # diffview 재사용 결과
    assert e.raw_payload["route_strategy"] == "staged_writing"
    assert "도시 공간 관찰" in e.input_context
    # 최종본이 없으면 diff도 없다(초안만으로 변경을 지어내지 않는다).
    assert event_from_result(_Result()).edit_diff == ""
    print("OK 어댑터 정규화 — 채널별 진입점이 같은 스키마로 수렴")


def test_path_override_isolates_users():
    with tempfile.TemporaryDirectory() as d:
        target = pathlib.Path(d) / "u1" / "persona_events.jsonl"
        set_events_path_override(target)
        try:
            record_event(make_event(channel="web", generated_draft="A",
                                    input_context="상황"))
            assert target.exists() and len(load_events()) == 1
        finally:
            set_events_path_override(None)
    print("OK 요청 스코프 경로 격리")


# ── 이동성 ───────────────────────────────────────────────────────────

def _isolate(d):
    """모든 스토어를 임시 경로로 돌린다(실행자 개인 파일 오염 방지)."""
    from until import profile as prof
    from until.context import edit_events as ee, episodes as ep, facts as fa
    from until.context import tone as tn
    root = pathlib.Path(d)
    tn.set_persona_path_override(root / "persona.json")
    fa.set_facts_path_override(root / "facts.json")
    ep.set_episodes_path_override(root / "episodes.jsonl")
    ee.set_edit_events_path_override(root / "edit_events.jsonl")
    prof.set_profile_path_override(root / "profile.json")
    set_events_path_override(root / "persona_events.jsonl")


def _release():
    from until import profile as prof
    from until.context import edit_events as ee, episodes as ep, facts as fa
    from until.context import tone as tn
    tn.set_persona_path_override(None)
    fa.set_facts_path_override(None)
    ep.set_episodes_path_override(None)
    ee.set_edit_events_path_override(None)
    prof.set_profile_path_override(None)
    set_events_path_override(None)


def test_export_import_roundtrip_and_identity_separation():
    from until.context.facts import make_fact, save_facts
    from until.context.tone import (PersonaBase, PersonaStore, RegisterOverride,
                                    load_persona, save_persona)
    from until.profile import load_profile, save_profile

    with tempfile.TemporaryDirectory() as d:
        _isolate(d)
        try:
            save_persona(PersonaStore(
                base=PersonaBase(defaults={"warmth": 4}, source="user"),
                registers={"reflective": RegisterOverride({"deference": 5}, True)},
                pinned_register="reflective"))
            save_facts([make_fact("결정", "주제", "도시 공간으로 확정")])
            save_profile({"name": "홍길동", "student_id": "2020-12345"})

            payload = export_persona()
            assert payload["schema_version"] == SCHEMA_VERSION
            # 신상은 identity 절에만 — 본문 절에 섞이지 않는다.
            assert payload["identity"]["name"] == "홍길동"
            body = json.dumps({k: v for k, v in payload.items() if k != "identity"},
                              ensure_ascii=False)
            assert "홍길동" not in body and "2020-12345" not in body
            # 원문은 기본적으로 빠진다(실수 유출 방지).
            assert "episodes" not in payload and "persona_events" not in payload
            assert "홍길동" not in json.dumps(export_persona(include_identity=False),
                                            ensure_ascii=False)

            # 전부 지우고 되돌려 넣기.
            save_persona(PersonaStore())
            save_facts([])
            save_profile({})
            result = import_persona(payload, replace=True)
            assert result["ok"], result
            back = load_persona()
            assert back.pinned_register == "reflective"
            assert back.registers["reflective"].delta == {"deference": 5}
            assert back.base.defaults == {"warmth": 4}
            assert load_profile()["name"] == "홍길동"
            from until.context.facts import load_facts
            assert len(load_facts()) == 1
        finally:
            _release()
    print("OK export/import 왕복 · 신상 분리 · 원문 기본 제외")


def test_import_rejects_foreign_payloads():
    assert import_persona("not a dict")["ok"] is False
    assert import_persona({"schema_version": 99})["ok"] is False
    bad = import_persona({"schema_version": SCHEMA_VERSION, "surprise": 1})
    assert bad["ok"] is False and "surprise" in bad["reason"]
    with tempfile.TemporaryDirectory() as d:
        missing = import_from_file(pathlib.Path(d) / "nope.json")
        assert missing["ok"] is False and "읽을 수 없" in missing["reason"]
        broken = pathlib.Path(d) / "broken.json"
        broken.write_text("{ not json", encoding="utf-8")
        assert import_from_file(broken)["ok"] is False
    print("OK 이질 페이로드 거부 — 예외 없이 이유 반환")


def test_import_without_replace_preserves_existing():
    """남의 파일을 잘못 열어도 내 페르소나가 통째로 날아가지 않는다."""
    from until.context.tone import (PersonaBase, PersonaStore, load_persona,
                                    save_persona)
    with tempfile.TemporaryDirectory() as d:
        _isolate(d)
        try:
            save_persona(PersonaStore(
                base=PersonaBase(defaults={"warmth": 2}, source="user"),
                pinned_register="lab_report"))
            payload = {"schema_version": SCHEMA_VERSION,
                       "persona": {"base": {"defaults": {"warmth": 5}},
                                   "registers": {}, "pinned_register": "reflective",
                                   "style_card": None}}
            assert import_persona(payload, replace=False)["ok"]
            keep = load_persona()
            assert keep.base.defaults == {"warmth": 2}       # 기존 값 보존
            assert keep.pinned_register == "lab_report"
            # 알 수 없는 레지스터는 건너뛰되 경고로 남긴다(조용한 무시 금지).
            noisy = import_persona({"schema_version": SCHEMA_VERSION,
                                    "persona": {"registers": {"없는키": {"delta": {}}}}})
            assert noisy["ok"] and any("없는키" in w for w in noisy["warnings"])
        finally:
            _release()
    print("OK replace=False 보존 · 미지 레지스터 경고")


def test_export_to_file_cli_path():
    with tempfile.TemporaryDirectory() as d:
        _isolate(d)
        try:
            out = export_to_file(pathlib.Path(d) / "out.json")
            assert out.exists()
            data = json.loads(out.read_text(encoding="utf-8"))
            assert data["schema_version"] == SCHEMA_VERSION
            assert import_from_file(out)["ok"]
        finally:
            _release()
    print("OK 파일 export/import")


# ── 보관 기간 · 전체 삭제 ────────────────────────────────────────────

def test_every_store_is_listed_for_deletion():
    """새 스토어를 추가하고 목록에 안 넣으면 여기서 걸린다."""
    listed = {name for name, _ in USER_DATA_FILES}
    must_have = {"profile.json", "persona.json", "voice_profile.json",
                 "teacher_feedback.json", "answer_history.jsonl", "episodes.jsonl",
                 "facts.json", "edit_events.jsonl", "persona_events.jsonl",
                 "feedback.jsonl", "telemetry.jsonl", "consent.json",
                 "credits.json", "usage.json"}
    assert must_have <= listed, sorted(must_have - listed)
    assert set(RETENTION_DAYS) >= listed, sorted(listed - set(RETENTION_DAYS))
    # 원문이 담긴 스토어가 가장 짧게 보관돼야 한다.
    assert 0 < RETENTION_DAYS["persona_events.jsonl"] <= RETENTION_DAYS["episodes.jsonl"]
    assert len(kv_keys_for("u1")) == len(KV_KEY_PREFIXES)
    assert kv_keys_for("") == []
    print(f"OK 삭제 목록 {len(listed)}종 · KV 키 {len(KV_KEY_PREFIXES)}종")


def test_delete_all_user_data_reports_honestly():
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        made = []
        for name, _ in USER_DATA_FILES:
            (root / name).write_text("{}", encoding="utf-8")
            made.append(name)
        report = delete_all_user_data(root)
        assert report.ok and sorted(report.deleted) == sorted(made)
        assert not report.missing
        assert not any((root / n).exists() for n in made)
        # 두 번째 호출은 전부 '없음' — 실패로 포장하지 않는다.
        again = delete_all_user_data(root)
        assert again.ok and len(again.missing) == len(made) and not again.deleted
        assert "삭제 0건" in again.headline
    print("OK 전체 삭제 — 정직한 보고")


def test_purge_expired_keeps_unreadable_rows():
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat(
            timespec="seconds")
        new = datetime.now(timezone.utc).isoformat(timespec="seconds")
        target = root / "persona_events.jsonl"
        target.write_text("\n".join([
            json.dumps({"v": 1, "event_id": "old", "created_at": old}),
            json.dumps({"v": 1, "event_id": "new", "created_at": new}),
            json.dumps({"v": 1, "event_id": "nodate"}),      # 시각 없음 → 보존
            "완전히 깨진 줄",                                  # 못 읽음 → 보존
        ]) + "\n", encoding="utf-8")
        removed = purge_expired(today=date.today(), root=root)
        assert removed.get("persona_events.jsonl") == 1
        kept = target.read_text(encoding="utf-8")
        assert "old" not in kept
        assert "new" in kept and "nodate" in kept and "완전히 깨진 줄" in kept
        # 만료 없음 정책 파일은 건드리지 않는다.
        (root / "persona.json").write_text("{}", encoding="utf-8")
        assert "persona.json" not in purge_expired(root=root)
    print("OK 보관 기간 정리 — 못 읽는 줄은 보존")


def test_prompt_version_discipline_is_enforced():
    """프롬프트를 고치고 버전을 안 올리면 기계가 잡는다.

    `PROMPT_VERSION`은 손으로 올린다(올리는 행위가 곧 "의도한 변경"의 선언이다).
    손으로 하는 일은 반드시 잊히므로, 잊었을 때 알려주는 게이트가 함께 있어야
    PHASE 3의 출처 기록이 거짓말이 되지 않는다.
    """
    import importlib.util
    root = pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "check_prompt_version", root / "tools" / "check_prompt_version.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    from until.persona.versions import prompt_surface_fingerprints
    current = prompt_surface_fingerprints()
    baseline = mod.load_baseline()
    assert baseline, "tools/prompt_baseline.json 이 없습니다 — --update 로 생성하세요"
    problems, changed = mod.compare(current, baseline)
    assert not problems, (
        "프롬프트가 바뀌었는데 PROMPT_VERSION이 그대로입니다: " + str(changed)
        + " — 의도한 변경이면 until/persona/versions.py의 PROMPT_VERSION을 올리고 "
        "python tools/check_prompt_version.py --update 를 실행하세요.")

    # 게이트가 실제로 작동하는지 — 지문 하나를 흔들면 반드시 실패해야 한다.
    tampered = dict(current)
    tampered["SYSTEM"] = "0" * 12
    bad, bad_changed = mod.compare(tampered, baseline)
    assert bad and "SYSTEM" in bad_changed, "게이트가 변경을 못 잡는다"
    # 버전을 함께 올렸으면 통과한다(의도한 변경 경로).
    bumped = dict(tampered, PROMPT_VERSION="9.9.9")
    ok, ok_changed = mod.compare(bumped, baseline)
    assert not ok and "SYSTEM" in ok_changed
    # 표면 목록이 비면 이 게이트는 아무것도 지키지 못한다.
    assert len([k for k in current if k != "PROMPT_VERSION"]) >= 10
    print(f"OK 프롬프트 버전 규율 — 표면 {len(current) - 1}개 감시 중")



def test_new_surfaces_exist_on_production_entrypoint():
    """운영 엔트리포인트는 `uvicorn until.asgi:app`이다.

    stdlib 서버(`until/web.py`)에만 라우트를 붙이면 `/profile` 화면의 삭제 버튼은
    보이는데 누르면 404가 난다 — '보이지만 작동하지 않는' 상태가 가장 나쁘다.
    두 표면이 갈라지지 않게 여기서 고정한다.
    """
    import inspect
    from until import asgi, web
    src = inspect.getsource(asgi.create_app)
    for route in ("/profile/tone", "/data/export.json", "/data/delete"):
        assert route in src, f"ASGI에 {route} 없음(프로덕션에서 도달 불가)"
        assert route in inspect.getsource(web._Handler), f"stdlib 서버에 {route} 없음"
    print("OK 신규 라우트 3종이 두 서버 모두에 있다")


def test_channel_tag_is_not_hardcoded():
    """채널 중립 스키마의 유일한 채널 정보가 고정값이면 처음부터 거짓이 된다."""
    import inspect
    from until import asgi, pipeline, web
    assert "channel" in inspect.signature(pipeline.finalize).parameters
    # 웹 표면의 finalize 호출은 전부 channel="web"으로 태깅돼야 한다.
    for mod in (web, asgi):
        src = inspect.getsource(mod)
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith(("res = finalize(", "result = web.finalize(")):
                assert 'channel="web"' in line or line.rstrip().endswith(","), (
                    f"채널 태그 없는 finalize 호출: {stripped}")
    print("OK finalize 채널 태그 — 웹 호출은 web으로 기록")


def test_edit_signal_is_actually_produced():
    """감사에서 'allowlist에만 있고 생산 코드 0건'이라 지적된 필드를 실제로 채운다."""
    from until.telemetry.web import _edit_signal

    class _D:
        def __init__(self, body):
            self.body = body

    class _R:
        draft = _D("문단 하나입니다.\n\n문단 둘입니다.")
        final_draft = _D("문단 하나예요.\n\n문단 둘입니다.\n\n새 문단.")

    signal = _edit_signal(_R())
    assert signal["edit_ops"] >= 1 and signal["edit_ratio"] > 0
    from until.telemetry.schema import TELEMETRY_ALLOWLIST
    assert {"edit_ratio", "edit_ops"} <= TELEMETRY_ALLOWLIST

    class _NoFinal:
        draft = _D("초안만 있음")
        final_draft = None
    # 최종본이 없으면 키 자체를 내지 않는다(0으로 채워 '변경 없음'을 지어내지 않는다).
    assert _edit_signal(_NoFinal()) == {}
    print(f"OK 변경량 신호 생산 — {signal}")



def test_acceptance_is_evidence_not_assumption():
    """수락률이 항상 100%가 되면 그 지표는 통째로 무의미하다."""
    from until.persona.events import (update_acceptance,
                                      update_acceptance_for_result)
    res = _Result(final="최종 문단 하나.\n\n최종 문단 둘 수정됨.")
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "persona_events.jsonl"
        ev = record_event(event_from_result(res, channel="web"), p)
        assert ev is not None and ev.accepted is None      # 생성 시점엔 모른다

        # 증거가 생기면 되돌아와 채운다(제출 성공·사용자 평가).
        assert update_acceptance_for_result(res, True, channel="web", path=p) is True
        assert load_events(p)[0].accepted is True
        # 같은 값으로 다시 부르면 파일을 건드리지 않는다.
        assert update_acceptance_for_result(res, True, channel="web", path=p) is False
        # 거절도 표현된다(낮은 평가).
        assert update_acceptance_for_result(res, False, channel="web", path=p) is True
        assert load_events(p)[0].accepted is False
        # 없는 id·빈 id는 조용히 실패한다.
        assert update_acceptance("없는id", True, p) is False
        assert update_acceptance("", True, p) is False
        # 채널이 다르면 다른 이벤트다 — 남의 채널 기록을 덮지 않는다.
        assert update_acceptance_for_result(res, True, channel="email", path=p) is False
        assert load_events(p)[0].accepted is False
    print("OK 수락 여부는 증거로만 — 생성 시점엔 미상")


def test_recipient_ref_is_fingerprint_not_name():
    """수신자 구분은 필요하지만 제3자의 실명을 남길 이유는 없다."""
    from until.persona.events import recipient_ref_for

    class _Inquiry:
        professor = "김철수"

    class _WithProf(_Result):
        def __init__(self):
            super().__init__(final="최종본")
            self.inquiry_assignment = _Inquiry()

    ref = recipient_ref_for(_WithProf())
    assert ref.startswith("professor:") and "김철수" not in ref
    assert len(ref.split(":")[1]) == 12
    assert ref == recipient_ref_for(_WithProf())        # 같은 교수 = 같은 참조

    # 이벤트에도 이름이 아니라 지문이 실린다.
    e = event_from_result(_WithProf(), channel="web")
    assert e.recipient_ref == ref
    assert "김철수" not in json.dumps(e.__dict__, ensure_ascii=False)

    class _Team(_Result):
        def __init__(self):
            super().__init__(final="최종본")
            self.assignment_route = type("R", (), {"strategy": "team_project"})()
    assert recipient_ref_for(_Team()) == "team"
    # 수신자가 없으면 지어내지 않는다(채점자가 읽는 산문).
    assert recipient_ref_for(_Result(final="x")) == ""
    print(f"OK 수신자 참조 — {ref} (실명 없음)")


def test_latency_is_measured_not_zero():
    """0으로 두면 '즉시 나왔다'는 거짓 신호가 된다."""
    from until.config import Config
    import until.pipeline as pl
    with tempfile.TemporaryDirectory() as d:
        src = pathlib.Path(d) / "a.txt"
        src.write_text("# 에세이\n\n논하시오.\n", encoding="utf-8")
        res = pl.run([str(src)], Config(backend="mock"))
        assert res.elapsed_ms > 0, "생성 소요 시간이 측정되지 않았다"
        from until import session_store
        blob = session_store.encode({"result": res, "answers": None,
                                     "suggestions": None, "review": None}, ts=0.0)
        assert session_store.decode(blob)["result"].elapsed_ms == res.elapsed_ms
        # 이벤트가 Result의 측정값을 그대로 쓴다(인자를 안 줘도 0이 아니다).
        e = event_from_result(res, channel="cli")
        if e is not None:
            assert e.latency_ms == res.elapsed_ms
    print(f"OK 소요 시간 측정 — {res.elapsed_ms}ms")



if __name__ == "__main__":
    test_prompt_fingerprint_respects_boundaries()
    test_model_version_records_actual_responder()
    test_model_fingerprint_is_telemetry_safe()
    test_pipeline_records_provenance()
    test_event_schema_is_channel_neutral()
    test_raw_payload_keeps_original_and_survives_junk()
    test_event_roundtrip_and_corruption()
    test_event_from_result_normalizes_pipeline_output()
    test_path_override_isolates_users()
    test_export_import_roundtrip_and_identity_separation()
    test_import_rejects_foreign_payloads()
    test_import_without_replace_preserves_existing()
    test_export_to_file_cli_path()
    test_every_store_is_listed_for_deletion()
    test_delete_all_user_data_reports_honestly()
    test_purge_expired_keeps_unreadable_rows()
    test_prompt_version_discipline_is_enforced()
    test_new_surfaces_exist_on_production_entrypoint()
    test_channel_tag_is_not_hardcoded()
    test_edit_signal_is_actually_produced()
    test_acceptance_is_evidence_not_assumption()
    test_recipient_ref_is_fingerprint_not_name()
    test_latency_is_measured_not_zero()
    print("\n=== test_persona_portability: all passed ===")
