"""수정 diff 캡처 + 반복 패턴 배치 인터페이스 테스트 (no-token, 오프라인).

고정하는 계약:
  1. 캡처는 프롬프트·출력을 바꾸지 않는다(기본 켜짐인 근거).
  2. `edit_source`가 절대 뭉개지지 않는다 — 사람 수정과 LLM 재생성은 다른 신호다.
  3. 배치는 **제안만** 한다. `confirm=True` 없이는 페르소나를 건드리지 않는다.
"""
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.context.edit_events import (EDIT_SOURCES, SOURCE_WEIGHT, EditEvent,
                                       clear_edit_events, describe,
                                       load_edit_events, record_edit_event,
                                       summarize_diff)
from until.context.edit_patterns import (MIN_EVENTS, apply_patterns_to_persona,
                                         summarize_edit_patterns)
from until.context.tone import load_persona

_A = ("서론 문단입니다. 도시 공간을 살펴봅니다.\n\n"
      "본론 문단입니다. 자료를 근거로 설명합니다.\n\n"
      "결론 문단입니다. 정리합니다.")
_B = ("서론 문단이에요. 도시 공간을 살펴봐요.\n\n"
      "본론 문단이에요. 자료를 근거로 설명해요.\n\n"
      "결론 문단이에요. 정리해요.")


def _with_flag(value, fn):
    old = os.environ.get("UNTIL_EDIT_CAPTURE")
    if value is None:
        os.environ.pop("UNTIL_EDIT_CAPTURE", None)
    else:
        os.environ["UNTIL_EDIT_CAPTURE"] = value
    try:
        return fn()
    finally:
        if old is None:
            os.environ.pop("UNTIL_EDIT_CAPTURE", None)
        else:
            os.environ["UNTIL_EDIT_CAPTURE"] = old


def test_diff_summary_reuses_display_diff():
    """학습용 diff와 화면용 diff가 같은 함수를 써야 어긋나지 않는다."""
    ops, ratio, changes = summarize_diff(_A, _B)
    assert ops.get("수정") == 3 and ratio > 0
    assert changes and changes[0].kind == "changed"
    from until.diffview import diff_drafts
    assert len(changes) == len(diff_drafts(_A, _B))
    print(f"OK diff 재사용 — ops={dict(ops)} ratio={ratio}")


def test_record_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "edit_events.jsonl"
        ev = record_edit_event(_A, _B, edit_source="finalize",
                               register_key="reflective", task_type="report",
                               path=p)
        assert ev is not None and ev.edit_ratio > 0
        rows = load_edit_events(p)
        assert len(rows) == 1 and rows[0].edit_source == "finalize"
        assert rows[0].register_key == "reflective"
        # 변화 없음·빈 본문·미지 출처는 저장하지 않는다.
        assert record_edit_event(_A, _A, edit_source="human", path=p) is None
        assert record_edit_event("", _B, edit_source="human", path=p) is None
        assert record_edit_event(_A, _B, edit_source="telepathy", path=p) is None
        assert len(load_edit_events(p)) == 1
        # 손상 줄·미래 버전은 건너뛴다.
        with p.open("a", encoding="utf-8") as f:
            f.write("nope\n")
            f.write(json.dumps({"v": 99, "edit_source": "human"}) + "\n")
        assert len(load_edit_events(p)) == 1
        clear_edit_events(p)
        assert load_edit_events(p) == []
    print("OK 적립·로드 왕복 · 무변화/손상 내성")


def test_flag_off_disables_capture():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "edit_events.jsonl"
        assert _with_flag("0", lambda: record_edit_event(
            _A, _B, edit_source="human", path=p)) is None
        assert not p.exists()
        assert _with_flag("1", lambda: record_edit_event(
            _A, _B, edit_source="human", path=p)) is not None
    print("OK UNTIL_EDIT_CAPTURE=0 탈출구")


def test_edit_source_is_never_flattened():
    """사람 수정 ≫ finalize > llm_revise. 가중치가 뒤집히면 학습이 오염된다."""
    assert set(EDIT_SOURCES) == {"human", "finalize", "llm_revise"}
    assert SOURCE_WEIGHT["human"] > SOURCE_WEIGHT["finalize"] > SOURCE_WEIGHT["llm_revise"]
    ev = EditEvent(event_id="x", edit_source="llm_revise", before=_A, after=_B)
    assert ev.weight == SOURCE_WEIGHT["llm_revise"]
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "e.jsonl"
        record_edit_event(_A, _B, edit_source="llm_revise", path=p)
        text = describe(p)
        assert "llm_revise 1" in text
        assert "사람이 직접 고친 기록 0건" in text   # 한계를 숨기지 않는다
    print("OK edit_source 구분 유지 + 한계 표면화")


def test_patterns_need_enough_samples():
    summary = summarize_edit_patterns([])
    assert not summary.enough and summary.is_empty()
    # llm_revise만 잔뜩 있어도 가중치 하한을 못 넘으면 제안하지 않는다.
    weak = [EditEvent(event_id=str(i), edit_source="llm_revise", before=_A, after=_B)
            for i in range(MIN_EVENTS + 2)]
    weak_summary = summarize_edit_patterns(weak)
    assert weak_summary.n_events > MIN_EVENTS
    assert not weak_summary.enough, "약한 신호만으로 문체를 바꾸면 안 된다"
    print(f"OK 표본·가중치 하한 — llm_revise {len(weak)}건 가중 {weak_summary.weighted}")


def test_patterns_detect_repeated_speech_level_change():
    events = [EditEvent(event_id=str(i), edit_source="human", before=_A, after=_B)
              for i in range(MIN_EVENTS)]
    summary = summarize_edit_patterns(events)
    assert summary.enough and not summary.is_empty()
    assert summary.suggested_delta.get("speech_level") == "해요체"
    assert any("종결어미" in e for e in summary.evidence)
    print(f"OK 반복 수정 패턴 감지 — {summary.suggested_delta}")


def test_apply_requires_explicit_confirm():
    events = [EditEvent(event_id=str(i), edit_source="human", before=_A, after=_B)
              for i in range(MIN_EVENTS)]
    summary = summarize_edit_patterns(events)
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "persona.json"
        assert apply_patterns_to_persona(summary, path=p) is False   # 기본 미적용
        assert not p.exists()
        assert apply_patterns_to_persona(summary, confirm=True, path=p) is True
        card = load_persona(p).style_card
        assert card is not None and card.source == "edit_patterns"
        assert card.fields.get("speech_level") == "해요체"
        assert card.notes                                            # 근거 보존
    print("OK 배치는 제안만 — confirm=True에서만 반영")


if __name__ == "__main__":
    test_diff_summary_reuses_display_diff()
    test_record_and_load_roundtrip()
    test_flag_off_disables_capture()
    test_edit_source_is_never_flattened()
    test_patterns_need_enough_samples()
    test_patterns_detect_repeated_speech_level_change()
    test_apply_requires_explicit_confirm()
    print("\n=== test_edit_capture: all passed ===")
