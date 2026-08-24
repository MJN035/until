"""T4 대필 금지 신호 게이트 — 자필 규정 감지(결정적) + 학습 보조 모드 강등.

기획 근거(docs/planning/type_algorithms.md T4): 물리학 숙제가 "종이에 작성한
답안의 스캔이나 사진, 또는 손글씨를 그대로(폰트 변환 금지)"를 명시하는데
파이프라인이 완성 답안을 그대로 써주던 갭. Draft 경계선의 규정 버전 —
사람이 해야 한다고 규정된 것도 넘지 않는다.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.understanding.integrity import detect_no_ghostwriting


class _Doc:
    def __init__(self, text): self.text = text; self.source = "x"


def test_detect_handwriting_signals():
    # 실코퍼스 문구(물리학 1 숙제) — 반드시 감지.
    g = detect_no_ghostwriting({}, [_Doc(
        "종이에 작성한 답안의 스캔이나 사진, 또는 손글씨를 그대로 남긴 "
        "파일을 제출하세요 (폰트 변환하시면 안됩니다)")])
    assert g is not None
    assert "손글씨" in g.snippet or "종이에" in g.snippet
    # spec 필드에서도 감지.
    g2 = detect_no_ghostwriting({"constraints": ["자필 답안만 인정"]})
    assert g2 is not None
    # 폰트 변환 금지 단독으로도 감지.
    g3 = detect_no_ghostwriting({}, [_Doc("답안 제출 시 폰트 변환하시면 안됩니다")])
    assert g3 is not None
    print("OK 감지 — 손글씨/자필/종이 답안/폰트 변환 금지")


def test_no_false_positives():
    # 오탐 방지: 활동 '사진', 단순 '스캔 제출', '자필 서명', 에세이 지시문.
    assert detect_no_ghostwriting({}, [_Doc("활동 사진을 첨부하고 pdf로 변환 후 업로드")]) is None
    assert detect_no_ghostwriting({}, [_Doc("보고서를 스캔하여 업로드하세요")]) is None
    assert detect_no_ghostwriting({}, [_Doc("서약서에 자필 서명 후 첨부")]) is None
    assert detect_no_ghostwriting({"goal": "자신의 견해를 논하시오"}) is None
    assert detect_no_ghostwriting({}) is None
    print("OK 오탐 0 — 사진/스캔/서명/에세이")


def test_gated_pipeline_study_mode():
    """감지 시: 학습 보조 초안(최종 답안 없음) + readiness '규정' 안내."""
    import tempfile, os
    from until.config import Config
    from until.pipeline import run
    from until.readiness import assess_readiness
    cfg = Config(); cfg.backend = "mock"
    fd, p = tempfile.mkstemp(suffix=".txt", text=True)
    os.write(fd, ("숙제1 (1-8번 각 10점)\n\n문제를 풀어 제출하세요. "
                  "종이에 작성한 답안의 스캔이나 사진, 또는 손글씨를 그대로 "
                  "남긴 파일만 인정합니다 (폰트 변환하시면 안됩니다).").encode("utf-8"))
    os.close(fd)
    try:
        res = run([p], cfg)
    finally:
        os.unlink(p)
    assert res.spec.get("integrity_gate"), "게이트가 spec에 기록돼야 함"
    assert res.guard.passed
    body = res.draft.body
    assert "학습 보조" in body           # 모드 전환된 초안
    assert "검산" in body                # 체크리스트 포함
    assert "## 문제 1" not in body       # 완성 답안 형태가 아님
    r = assess_readiness(res)
    assert any(i.label == "규정" for i in r.items), [i.label for i in r.items]
    print("OK 게이트 e2e — 학습 보조 모드 + 규정 안내")


def test_signature_phrase_not_gated():
    # '자필로 서명'(서약서 조사 변형)이 게이트를 발동시키던 오탐 회귀(리뷰 발견) —
    # 에세이가 학습 보조 모드로 강등되던 케이스.
    from until.understanding.integrity import detect_no_ghostwriting
    for txt in ("표절 서약서에 자필로 서명하여 함께 제출하세요.",
                "서약서에 자필 서명 후 제출.",
                "자필서명을 첨부할 것."):
        assert detect_no_ghostwriting({"goal": txt}) is None, txt
    # 진짜 규정은 여전히 잡는다.
    assert detect_no_ghostwriting({"goal": "답안은 자필로 작성해 스캔 제출"}) is not None
    print("OK '자필로 서명' 오탐 없음(게이트는 유지)")


if __name__ == "__main__":
    test_detect_handwriting_signals()
    test_no_false_positives()
    test_gated_pipeline_study_mode()
    test_signature_phrase_not_gated()
    print("\nINTEGRITY TESTS PASS")
