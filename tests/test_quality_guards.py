"""생성 품질 안전장치 테스트 — n-gram 중복 · 금지 표현 · 민감 상황 승인 대기.

고정하는 계약:
  1. 중복 검사가 **시스템이 만든 정형 문자열**([[DECISION]]·[자료N]·표)을 표절로
     신고하지 않는다.
  2. 금지 표현은 프롬프트 지시가 아니라 **생성 후 검증**으로 잡힌다.
  3. 민감 상황은 초안 생성을 막지 않고 **자동 제출만** 막는다.
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.boundary.models import Draft
from until.execution.boundary_guard import CitationPreservationValidator
from until.execution.quality_guards import (BannedPhraseValidator,
                                            RepetitionValidator,
                                            build_quality_validators, ngrams,
                                            overlap_ngrams)
from until.execution.sensitive import (MIN_HITS, SENSITIVE_KINDS,
                                       SensitiveReport, detect_sensitive)
from until.context.tone import resolve_tone_spec

_SHARED = ("도시의 골목은 사람들이 걷는 속도에 따라 전혀 다른 공간으로 읽힌다는 점을 "
           "관찰에서 확인할 수 있었고 이는 설계 의도와 무관하게 발생한다")


def test_ngram_overlap_detects_recycled_sentences():
    old = f"{_SHARED} 그래서 관찰은 중요하다."
    new = f"이번 과제에서도 {_SHARED} 따라서 결론은 다르다."
    hits = overlap_ngrams(new, [old])
    assert hits, "그대로 재활용된 긴 문장은 잡혀야 한다"
    result = RepetitionValidator([old]).validate(Draft.from_text(new))
    assert not result.passed and "반복" in result.errors[0]
    # 전혀 다른 글은 통과.
    assert RepetitionValidator([old]).validate(
        Draft.from_text("완전히 다른 주제의 짧은 글이다.")).passed
    # 비교 대상이 없으면 항상 통과(첫 과제 보호).
    assert RepetitionValidator([]).validate(Draft.from_text(new)).passed
    print(f"OK n-gram 중복 감지 — 공유 {len(hits)}개")


def test_ngram_ignores_system_boilerplate():
    """결정 마커·인용 표식·표는 시스템이 만드는 정형 문자열이라 당연히 겹친다."""
    boiler = ("[[DECISION: 분석 대상 도시를 어디로 할까? 후보 — (1) 서울, (2) 부산, "
              "(3) 대구 중 하나를 고르거나 직접 적어 주세요]]\n\n"
              "| 항목 | 내용 |\n| --- | --- |\n| 활동일 | |\n")
    other = boiler + "\n전혀 다른 본문 문장이 여기에 온다."
    assert overlap_ngrams(other, [boiler]) == []
    assert RepetitionValidator([boiler]).validate(Draft.from_text(other)).passed
    assert ngrams("짧은 글", 8) == set()      # 어절 부족이면 빈 집합
    print("OK 정형 문자열은 중복 판정에서 제외")


def test_banned_phrase_is_checked_after_generation():
    v = BannedPhraseValidator(["대박", "ㅎㅎ"])
    bad = v.validate(Draft.from_text("이번 실험 결과는 대박이었다."))
    assert not bad.passed and "대박" in bad.errors[0]
    assert v.validate(Draft.from_text("이번 실험 결과는 유의미했다.")).passed
    # 결정 마커 안의 텍스트는 검사 대상이 아니다(시스템이 만든 질문).
    assert v.validate(Draft.from_text("[[DECISION: 대박이라는 표현을 쓸까요?]]")).passed
    assert BannedPhraseValidator([]).validate(Draft.from_text("아무 글")).passed
    print("OK 금지 표현 사후 검증")


def test_build_quality_validators_is_empty_when_nothing_to_check():
    """검사할 게 없으면 빈 검증기를 끼우지 않는다(reask 비용 0 유지)."""
    plain = resolve_tone_spec("academic_prose")
    assert build_quality_validators(plain, []) == []
    assert len(build_quality_validators(plain, ["과거 본문"])) == 1
    from dataclasses import replace
    with_banned = replace(plain, banned=("대박",))
    built = build_quality_validators(with_banned, ["과거 본문"])
    assert len(built) == 2
    print("OK 검증기 조립 — 필요할 때만 생성")


def test_sensitive_detection_needs_multiple_signals():
    """한 번 스친 언급으로 승인 대기를 걸면 사용자가 플래그를 무시하게 된다."""
    weak = detect_sensitive({"goal": "죄송합니다만 자료를 정리해 주세요"})
    assert not weak.needs_approval
    strong = detect_sensitive({"goal": "지도교수님께 드릴 사과문 작성",
                               "requirements": ["죄송하다는 뜻을 밝힐 것",
                                                "재발 방지 대책 포함"]})
    assert strong.needs_approval and "사과" in strong.kinds
    assert strong.findings[0].hits >= MIN_HITS
    assert "사과" in strong.headline and strong.findings[0].evidence
    print(f"OK 민감 상황 탐지 — {strong.kinds}, 근거 {strong.findings[0].evidence[:3]}")


def test_sensitive_covers_refusal_and_conflict():
    refuse = detect_sensitive(None, None,
                              "참여하기 어렵다는 뜻을 전하며 정중히 거절합니다. "
                              "이번 제안은 철회합니다.")
    assert "거절" in refuse.kinds
    conflict = detect_sensitive({"goal": "성적 이의신청서 작성"}, None,
                                "채점이 부당하다고 판단해 재심사를 요청드립니다. "
                                "이의를 제기합니다.")
    assert "갈등" in conflict.kinds
    assert set(SENSITIVE_KINDS) == {"사과", "거절", "갈등"}
    assert SensitiveReport().to_dict()["needs_approval"] is False
    print("OK 거절·갈등 탐지")


def test_sensitive_blocks_submission_not_generation():
    """생성은 되고, 자동 제출만 막힌다 — 막으면 제품이 쓸모없어진다."""
    from until.config import Config
    from until.execution.submission_gate import build_submission_plan
    import until.pipeline as pl

    class _Assignment:
        course_id, id, submitted = "c1", "a1", False

    with tempfile.TemporaryDirectory() as d:
        src = pathlib.Path(d) / "apology.txt"
        src.write_text(
            "# 사과문 작성\n\n지도교수님께 드릴 사과문을 작성하시오. "
            "죄송하다는 뜻과 재발 방지 대책을 포함할 것.\n", encoding="utf-8")
        res = pl.run([str(src)], Config(backend="mock"))
        assert res.draft.body, "민감 과제여도 초안 생성 자체는 막지 않는다"
        assert res.needs_approval and res.approval_kinds
        assert res.approval_messages and "확인" in res.approval_messages[0]

        plan = build_submission_plan(
            res, _Assignment(), nonce_path=pathlib.Path(d) / "nonce.json")
        assert not plan.allowed
        assert any(b.code == "needs_human_approval" for b in plan.blocks)
        assert plan.confirm_nonce == ""      # 차단 상태에서는 nonce 미발급

        # 세션 왕복에서도 승인 플래그가 살아남아야 한다.
        from until import session_store
        blob = session_store.encode({"result": res, "answers": None,
                                     "suggestions": None, "review": None}, ts=0.0)
        back = session_store.decode(blob)
        assert back["result"].needs_approval is True
        assert back["result"].approval_kinds == res.approval_kinds
    print("OK 민감 상황 — 생성 허용 / 자동 제출 차단 / 세션 보존")


def test_plain_assignment_stays_unblocked():
    """평범한 과제에 승인 플래그가 붙으면 안 된다(오탐 회귀 가드)."""
    from until.config import Config
    import until.pipeline as pl
    with tempfile.TemporaryDirectory() as d:
        src = pathlib.Path(d) / "essay.txt"
        src.write_text("# 매체 이론 에세이\n\n두 이론을 비교해 논하시오. 1500자 이상.\n",
                       encoding="utf-8")
        res = pl.run([str(src)], Config(backend="mock"))
        assert res.needs_approval is False and res.approval_kinds == []
    print("OK 일반 과제 오탐 없음")


def test_citation_preservation_validator():
    """2차 패스가 초안의 [자료N]을 떨구면 reask 대상이다.

    실측(2026-08-22, Cerebras gpt-oss-120b, 실제 길이 초안 8인용 기준):
    수정 전 3회 중 3회 인용 전량 유실 → 수정 후(프롬프트 규칙 + 이 검증기) 8회 중
    8회 보존. 초안은 인용 검사를 통과했는데 완성본이 '근거 미인용'으로 떨어지면
    사용자에게는 마무리를 누를수록 결과가 나빠지는 것으로 보인다.
    """
    draft_body = "첫 문장이다 [자료1]. 둘째 문장이다 [자료2]. 셋째다 [자료1]."
    v = CitationPreservationValidator(draft_body)

    # 전량 유실 — 실패 + 사라진 번호를 모두 알려준다.
    res = v.validate(Draft(body="인용 없이 다시 쓴 완성본이다."))
    assert res.passed is False
    assert "[자료1]" in res.errors[0] and "[자료2]" in res.errors[0]

    # 일부 유실 — 빠진 번호만 알려준다.
    res = v.validate(Draft(body="첫 문장이다 [자료1]. 나머지는 다시 썼다."))
    assert res.passed is False and "[자료2]" in res.errors[0]
    assert "[자료1]" not in res.errors[0], "살아남은 번호까지 요구하면 안 된다"

    # 보존(순서·문장이 바뀌어도 번호가 남아 있으면 통과) + 번호 추가는 막지 않는다
    # (없는 번호 인용은 readiness의 '인용 오류'가 따로 잡는다 — 역할 분리).
    assert v.validate(Draft(body="다시 쓴 문장 [자료2]. 또 다른 문장 [자료1].")).passed
    assert v.validate(Draft(body="[자료1][자료2][자료3]")).passed

    # 초안에 인용이 없으면 지킬 게 없다 — 빈 계약으로 통과.
    assert CitationPreservationValidator("인용 없는 초안").validate(
        Draft(body="아무거나")).passed
    print("OK 인용 보존 검증기 (전량·일부 유실 · 순서 무관 보존 · 빈 계약)")


def test_assignment_meta_is_not_the_deliverable():
    """산출물이 과제 자체(마감·과제ID·과목코드)를 서술하면 reask 대상이다.

    SYSTEM에 이미 두 줄로 적혀 있는 규칙인데("산출물 '자체'를 써라", "행정
    정보는 본문에 넣지 않는다") 실측(2026-08-22, Cerebras gpt-oss-120b)에서
    자료가 충분한 과제에서도 매번 어겼다. 지시가 아니라 루프로 잡아야 하는
    종류다(인용 보존·수치 날조 방어와 같은 계보).

    정밀도는 실데이터로 쟀다 — 3인 코퍼스의 학생 실제 제출본 165건 중 3건만
    걸리고(1.8%), 그 3건은 학생이 과제지를 그대로 채워 낸 경우다.
    """
    from until.boundary.models import Draft
    from until.execution.boundary_guard import AssignmentMetaValidator

    v = AssignmentMetaValidator()

    # 실측된 위반 그대로.
    bad = ("본 보고서는 2025-2 현대경제의 이해(002) 과목에서 제시한 과제 331450번을 "
           "수행한다. 제출 마감은 2025년 11월 6일 오후 11시 59분으로 정해졌다.")
    res = v.validate(Draft.from_text(bad))
    assert res.passed is False
    assert "과제 자체" in res.errors[0]

    for text in ("과제 초고 제출 일정은 2025년 11월 7일까지이며 이는 지정된 마감이다.",
                 "과제 ID는 290995이며 eTL 과제란에 제출해야 한다."):
        assert v.validate(Draft.from_text(text)).passed is False, text

    # 진짜 산출물 문장은 통과해야 한다 — 학생 제출본에서 그대로 가져온 문장들.
    for text in ("대학교에서 수행되는 과학 글쓰기는 실험 절차 보고를 뛰어넘는다.",
                 "내일배움카드를 이용한 민간 교육과정은 결코 무위험이라 할 수 없다.",
                 "실험 결과 마찰계수는 0.32로 측정되어 이론값과 5% 차이를 보였다."):
        assert v.validate(Draft.from_text(text)).passed, text

    # 결정 마커 **안**의 문장은 산출물 본문이 아니다 — 첨부 요청 질문에 마감이
    # 들어갈 수 있고, 그걸 위반으로 잡으면 요청 자체가 막힌다.
    marker = ("# 과제\n[[DECISION: 과제 본문이 가리키는 HW1.pdf 를 못 읽었습니다. "
              "제출 마감 2025년 3월 21일 전에 올려 주시면 이어서 씁니다]]")
    assert v.validate(Draft.from_text(marker)).passed
    print("OK 산출물이 과제 자체를 서술하면 잡는다 (진짜 산출물·결정 마커는 통과)")


def test_invented_candidates_are_blocked_when_material_is_absent():
    """원료가 없다고 판정된 과제에서 '구체적 후보'는 창작이다.

    SYSTEM은 결정 질문에 "구체적 후보 2~3개"를 요구한다 — 답을 클릭 한 번으로
    만드는 좋은 규칙인데, **자료가 없을 때는 창작 지시가 된다**. 실측(2026-08-23):
    1주차 소감문에 "다룬 주요 주제는? 후보 — (1) 전력 시스템 개요, (2) 정보·통신
    기초"(강의 내용을 아무도 모른다), 실험 예비보고서에 "열량계 모델은? 후보 —
    (1) 전기식 열량계(모델 E-100)"(없는 모델명). 짧은 초안보다 나쁘다 — 학생에게
    **거짓 선택지**를 주고, 고르면 그 거짓이 본문으로 들어간다.

    어휘로 '근거 있는 후보'를 가리려다 실패했다: 후보 '정보·통신 기초'의 '정보'가
    과목명 '전기·정보세미나'에 substring으로 걸리고, 반대로 '피드백 내용 요약'처럼
    정당하게 추론된 후보는 자료에 그대로 없다. 그래서 이미 계산된 신호
    (`material_gap`)에 붙였다 — 호출부가 그때만 이 검증기를 켠다.
    """
    from until.boundary.models import Draft
    from until.execution.boundary_guard import InventedCandidateValidator

    v = InventedCandidateValidator()

    bad = ("[[DECISION: 1주차 강의에서 다룬 주요 주제는? 후보 — "
           "(1) 전력 시스템 개요, (2) 정보·통신 기초, (3) 직접 입력해 주세요]]")
    res = v.validate(Draft.from_text(bad))
    assert res.passed is False
    assert "전력 시스템 개요" in res.errors[0]
    assert "빈칸형" in res.errors[0], "무엇으로 바꾸라는 안내가 있어야 reask가 는다"

    assert v.validate(Draft.from_text(
        "[[DECISION: 열량계 모델은? 후보 — (1) 전기식 열량계(모델 E-100), "
        "(2) 고전식 열량계(모델 C-50)]]")).passed is False

    # 빈칸형·질문형·원료 요청은 후보가 없으니 통과.
    for text in ("[[DECISION: 이 과제에서 본인의 '고찰' 한 가지: ___ (한 줄이면 충분해요)]]",
                 "[[DECISION: 과제 본문이 가리키는 HW1.pdf 를 못 읽었습니다. "
                 "파일을 올려 주시면 이어서 씁니다 — 지금 올릴 수 있나요?]]",
                 "본문만 있고 결정 마커가 없는 초안이다."):
        assert v.validate(Draft.from_text(text)).passed, text

    # 답을 미루는 선택지는 후보가 아니라 탈출구 — 세지 않는다.
    assert v.validate(Draft.from_text(
        "[[DECISION: 무엇으로 할까? 후보 — (1) 직접 입력해 주세요, (2) 기타]]")).passed
    print("OK 원료 없음 상태의 지어낸 후보 차단 (빈칸형·탈출구는 통과)")


if __name__ == "__main__":
    test_ngram_overlap_detects_recycled_sentences()
    test_ngram_ignores_system_boilerplate()
    test_banned_phrase_is_checked_after_generation()
    test_build_quality_validators_is_empty_when_nothing_to_check()
    test_sensitive_detection_needs_multiple_signals()
    test_sensitive_covers_refusal_and_conflict()
    test_sensitive_blocks_submission_not_generation()
    test_plain_assignment_stays_unblocked()
    test_citation_preservation_validator()
    test_assignment_meta_is_not_the_deliverable()
    test_invented_candidates_are_blocked_when_material_is_absent()
    print("\n=== test_quality_guards: all passed ===")
