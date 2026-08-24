"""구체성 검증 테스트 (오프라인·결정적) — 재설계 6단계.

수용 기준 3: "체험하였다"류 공허 문장이 재생성 트리거로 잡힌다.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.boundary.models import Draft
from until.execution.specificity import (
    SpecificityValidator, assess_specificity,
)

# 실사용 실패 사례 그대로 — 제약은 지켰지만 답이 아닌 문장.
_EMPTY = ("건설산업에 빅데이터와 AI 에이전트가 어떻게 적용되는지 사례를 소개받고, "
          "해당 개념을 실습을 통해 직접 체험하였다. 많은 도움이 되었고 좋은 "
          "기회였다. 다양한 기술을 배울 수 있었다.")

_CONCRETE = ("강사는 철근 배근 검사에 비전 모델을 적용해 검사 시간이 45분에서 "
             "12분으로 줄어든 현장 사례를 보여줬다 [자료2]. 실습에서는 공정표 "
             "데이터를 노드로 바꿔 지연 전파를 시뮬레이션했고, 병목이 콘크리트 "
             "양생 공정에 몰린다는 결과를 직접 확인했다.")

_SOURCES = ["철근 배근 검사 비전 모델 적용, 공정표 그래프 시뮬레이션, "
            "콘크리트 양생 병목 분석을 다룬 강의 요지"]


def test_empty_sentences_detected():
    rep = assess_specificity(_EMPTY, source_texts=_SOURCES,
                             title="AI 에이전트 시대의 건설산업")
    assert rep.empty_sentences, "공허 문형이 잡혀야"
    assert any("체험하였다" in s for s in rep.empty_sentences)
    assert rep.score < 0.55
    print("OK empty-pattern sentences detected (real failure case)")


def test_concrete_body_scores_high():
    rep = assess_specificity(_CONCRETE, source_texts=_SOURCES,
                             title="AI 에이전트 시대의 건설산업")
    assert not rep.empty_sentences
    assert rep.term_hits >= 3 and rep.has_numbers and rep.has_citation
    assert rep.score >= 0.7, rep.score
    print("OK concrete body scores high")


def test_title_word_repetition_not_credited():
    # 제목 단어만 반복 — 용어 가점 없음(title_only_hits로 따로 집계).
    body = ("AI 에이전트 시대의 건설산업은 중요하다. 건설산업에서 AI 에이전트가 "
            "쓰인다. " * 3)
    rep = assess_specificity(body, source_texts=_SOURCES,
                             title="AI 에이전트 시대의 건설산업")
    assert rep.term_hits == 0 and rep.title_only_hits >= 2
    print("OK title-word repetition not credited")


def test_validator_reask_message_quotes_sentence():
    v = SpecificityValidator(source_texts=_SOURCES,
                             title="AI 에이전트 시대의 건설산업")
    r = v.validate(Draft.from_text(_EMPTY))
    assert not r.passed
    joined = " ".join(r.errors)
    assert "체험하였다" in joined          # 위반 문장을 그대로 인용
    assert "DECISION" in joined            # 근거 없으면 빈칸 지시
    assert v.validate(Draft.from_text(_CONCRETE)).passed
    print("OK validator quotes offending sentence in reask")


if __name__ == "__main__":
    test_empty_sentences_detected()
    test_concrete_body_scores_high()
    test_title_word_repetition_not_credited()
    test_validator_reask_message_quotes_sentence()
    print("\nSPECIFICITY TESTS PASS")
