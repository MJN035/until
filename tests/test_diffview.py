"""초안→최종본 변경 요약(diffview) 테스트 (오프라인·결정적)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.diffview import diff_drafts, summarize_changes, Change


def test_identical_no_changes():
    body = "## 서론\n\n같은 문단이다.\n\n## 결론\n\n끝."
    assert diff_drafts(body, body) == []
    assert summarize_changes([]) == "변경 없음"
    print("OK identical -> no changes")


def test_changed_paragraph_paired():
    draft = "서론 문단.\n\n[[DECISION: 어느 도시로 할지 — 본인 판단]]\n\n결론 문단."
    final = "서론 문단.\n\n나는 서울을 골랐다. 서울은 내 경험과 맞닿아 있기 때문이다.\n\n결론 문단."
    ch = diff_drafts(draft, final)
    # 결정 마커 문단이 답으로 대체 — 유사도 낮아 삭제+추가 또는 수정 하나로 잡힌다.
    assert ch, "변경이 감지돼야 함"
    kinds = {c.kind for c in ch}
    assert kinds <= {"added", "removed", "changed"}
    # 유사 문단 수정 짝짓기: 작은 수정은 changed 하나로.
    ch2 = diff_drafts("긴 본문 문단이 여기 있다. 결론은 아직 비었다.",
                      "긴 본문 문단이 여기 있다. 결론은 서울로 정했다.")
    assert len(ch2) == 1 and ch2[0].kind == "changed"
    assert "비었다" in ch2[0].before and "서울" in ch2[0].after
    print("OK changed paragraph paired (similar -> changed)")


def test_added_and_removed():
    ch = diff_drafts("문단 A.\n\n문단 B.", "문단 A.\n\n문단 B.\n\n새 문단 C.")
    assert len(ch) == 1 and ch[0].kind == "added" and "C" in ch[0].after
    ch = diff_drafts("문단 A.\n\n지워질 문단.", "문단 A.")
    assert len(ch) == 1 and ch[0].kind == "removed"
    print("OK added + removed detected")


def test_summarize_counts():
    ch = [Change("changed", "a", "b"), Change("changed", "c", "d"), Change("added", "", "e")]
    assert summarize_changes(ch) == "수정 2 · 추가 1"
    print("OK summarize counts ordered")


def test_pipeline_finalize_diff_and_web():
    from until.config import Config
    from until.pipeline import run, finalize
    from until import web, report
    cfg = Config(); cfg.backend = "mock"
    res = run(["examples/sample_assignment.txt"], cfg)
    answers = {1: "형식 결정론을 핵심 논지로"}
    res = finalize(res, answers, cfg)
    assert res.final_draft is not None
    ch = diff_drafts(res.draft.body, res.final_draft.body)
    assert ch, "finalize 후 변경이 감지돼야 함"
    # 웹 최종 페이지에 변경 패널.
    h = web.render_final(res, session_id="tok", answered={1})
    assert "초안에서 달라진 부분" in h
    # 리포트 Final Draft 섹션에 변경 한 줄 + 상세 목록.
    md = report.render_markdown_report(res)
    assert "초안 대비 변경:" in md
    assert "### 변경 상세" in md and ("**수정**" in md or "**추가**" in md)
    print("OK pipeline finalize -> diff panel (web) + report line + detail")


if __name__ == "__main__":
    test_identical_no_changes()
    test_changed_paragraph_paired()
    test_added_and_removed()
    test_summarize_counts()
    test_pipeline_finalize_diff_and_web()
    print("\nDIFFVIEW TESTS PASS")
