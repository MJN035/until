"""제출 형식 요건 추출 — 실코퍼스 문장으로 고정한다.

여기 있는 문장은 전부 3인 코퍼스(1,450개 문서)에서 그대로 가져온 것이다. 지어낸
문장으로 정규식을 시험하면 정규식에 맞는 문장만 시험하게 된다 — 실제로 이 추출기를
붙이면서 잡은 오탐 셋(배점 "(10 pts)"를 글자 크기로, "폰트 변환하시면 안됩니다"를
글꼴 이름으로, **"표지 없음"을 표지 요구로**) 전부 코퍼스를 훑다가 나왔다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from until.understanding.format_spec import (
    COVER,
    FILE_NAME,
    FILE_TYPE,
    REFERENCES,
    TYPOGRAPHY,
    detect_format_rules,
    forbidden_extensions,
    required_extension,
)


def _kinds(rules, kind):
    return [r for r in rules if r.kind == kind]


def test_file_type_required_and_forbidden():
    """'pdf로 제출'과 'pdf x'는 정반대 — 금지가 요구를 이긴다."""
    req = detect_format_rules(
        "보고서를 작성하여 pdf 파일 변환하여 pdf파일로 제출하세요.")
    assert required_extension(req) == ".pdf"
    assert not forbidden_extensions(req)

    # "한글 or 워드 파일 원본으로 제출(pdf x)" — 확장자와 '로 제출' 사이에 수식어가
    # 둘(파일·원본) 끼어 있어도 잡아야 한다. 초기 정규식은 하나만 흘려 놓쳤다.
    both = detect_format_rules(
        "형식: 한글 or 워드 파일 원본으로 제출(pdf x), 한글 기준 11pt, 줄간격 180")
    assert ".pdf" in forbidden_extensions(both)
    assert required_extension(both) == ".docx"
    # 같은 확장자를 금지하면서 요구할 수는 없다.
    assert ".pdf" != required_extension(both)

    # 문장이 아니라 **항목**으로 적힌 형태 — 실사용에서 이걸 놓쳤다
    # (라이브 확인 2026-08-23, '피피티 제출': "**제출 형식:** PowerPoint 파일(PPT)").
    labelled = detect_format_rules("- **제출 형식:** PowerPoint 파일(PPT)")
    assert required_extension(labelled) == ".pptx", labelled
    assert required_extension(detect_format_rules("파일 형식 — PDF")) == ".pdf"

    # 괄호가 끼어드는 실제 표기: "파일(가급적 pdf 형식)로 만들어 제출"
    paren = detect_format_rules(
        "손글씨를 그대로 남긴 전자노트를 파일(가급적 pdf 형식)로 만들어 제출해 주세요.")
    assert required_extension(paren) == ".pdf"
    print("OK 파일 형식 요구·금지")


def test_file_name_rule_with_example():
    rules = detect_format_rules(
        '파일명을 "학번_이름" 으로 하여 제출하세요.(예. 123456_홍길동.pdf)')
    got = _kinds(rules, FILE_NAME)
    assert len(got) == 1 and got[0].value == "학번_이름"
    assert "123456_홍길동" in got[0].source, "예시가 근거에 남아야 화면에서 보여 줄 수 있다"
    print("OK 파일명 규칙 + 예시")


def test_cover_required_items_and_negation():
    """'표지 없음'을 표지 요구로 읽으면 검증기가 과제를 어기는 쪽으로 고친다."""
    want = detect_format_rules("레포트 표지에 조와 조원분들의 이름과 학번을 추가해주시길 바랍니다.")
    cover = _kinds(want, COVER)[0]
    assert not cover.forbidden
    assert "이름" in cover.extras and "학번" in cover.extras and "조원" in cover.extras

    # 항목이 다음 문장에 나열되는 실제 표기.
    across = detect_format_rules(
        "기본양식은 다음과 같습니다. 1. 첫장은 표지이다. 제목/주제문/개요/학과/학번/성명이 실려")
    items = _kinds(across, COVER)[0].extras
    assert "학번" in items and "학과" in items and "제목" in items

    for text in ("* 중간 보고서 양식 : A4 2매 분량, 표지 없음",
                 "2) 파일로 eTL 제출 표지 없이 A4 8페이지 분량 이내로 작성하여"):
        neg = _kinds(detect_format_rules(text), COVER)
        assert len(neg) == 1 and neg[0].forbidden, text

    # 목록 항목 + 괄호 나열 — 요구 동사가 없어도 요구다(라이브 확인 2026-08-23).
    bullet = _kinds(detect_format_rules("- 표지 슬라이드(과제명·학번·이름·발표일)"), COVER)
    assert len(bullet) == 1 and not bullet[0].forbidden
    assert "학번" in bullet[0].extras and "이름" in bullet[0].extras

    # 요구가 아닌 언급은 규칙이 아니다(자료 설명·시험 안내).
    for text in ("시험지 표지에 함수 목록이 있으니, 시험 시 참고하시면 됩니다.",
                 "(개인정보 보호를 위해 이름과 학번이 적혀있는 표지는 삭제하였습니다.)"):
        assert not _kinds(detect_format_rules(text), COVER), text
    print("OK 표지 요구·금지·오탐 차단")


def test_references_and_typography():
    ref = detect_format_rules(
        "*Reference는 IEEE나 APA 스타일 등 양식에 맞춰 작성하면 좋습니다.")
    assert any(r.value == "IEEE" for r in _kinds(ref, REFERENCES))

    typo = detect_format_rules("글꼴: 바탕체, 글자 크기 11pt, 줄간격 160%로 작성하세요.")
    values = {r.value for r in _kinds(typo, TYPOGRAPHY)}
    assert values == {"바탕체", "11pt", "160%"}, values

    # 배점 "(10 pts)"는 글자 크기가 아니다 — 문제지마다 나온다.
    score = detect_format_rules("1. (10 pts) 배달 요금 계산 시스템")
    assert not _kinds(score, TYPOGRAPHY)
    # "폰트 변환하시면 안됩니다"를 글꼴 이름으로 읽던 오탐.
    hand = detect_format_rules("손글씨를 그대로 남긴(폰트 변환하시면 안됩니다) 전자노트를")
    assert not _kinds(hand, TYPOGRAPHY)
    print("OK 인용 양식·서식 + 배점/서술어 오탐 차단")


def test_submission_channel_is_not_guessed():
    """제출 경로는 뽑지 않는다 — 정규 경로와 예외 경로를 정규식으로 못 가른다.

    코퍼스의 메일 언급 155건 중 대부분이 "문의는 메일로", "지각·부득이한 경우 조교
    메일로"였다. 틀린 제출 경로를 알리는 것은 침묵보다 나쁘다.
    """
    for text in ("기타 문의 사항은 제 메일로 보내주시면 감사하겠습니다.",
                 "9/10(제출 마감일) 이후엔 메일로 제출해주세요(1일 경과마다 1점 감점).",
                 "완성한 보고서는 담당 조교 이메일로 제출하세요."):
        assert all(r.kind != "submit_channel" for r in detect_format_rules(text)), text
    print("OK 제출 경로는 추측하지 않는다")


def test_spec_fields_and_empty_input():
    """spec의 requirements·constraints도 함께 읽는다. 형식 언급이 없으면 규칙 0."""
    rules = detect_format_rules("", {"requirements": ['파일명을 "학번_이름"으로 제출'],
                                     "constraints": ["pdf 파일로 제출"]})
    assert required_extension(rules) == ".pdf"
    assert _kinds(rules, FILE_NAME)[0].value == "학번_이름"

    assert detect_format_rules("") == []
    assert detect_format_rules("공백 포함 400자 이상 작성해주시기 바랍니다.") == [], \
        "분량은 length_target 몫 — 여기서 또 규칙으로 만들면 같은 말을 두 번 한다"
    print("OK spec 병합 · 형식 무언급 시 규칙 0")


def test_rules_are_deduped_and_describable():
    """공지는 여러 번 반복 인용된다 — 같은 규칙을 여러 줄로 띄우면 화면이 시끄럽다."""
    text = "pdf로 제출하세요. " * 5
    got = _kinds(detect_format_rules(text), FILE_TYPE)
    assert len(got) == 1
    assert got[0].describe() == "PDF로 제출 (.pdf)"
    assert forbidden_extensions(detect_format_rules("한글 파일로 제출(pdf x)")) == {".pdf"}
    print("OK 중복 제거 · describe()")


if __name__ == "__main__":
    test_file_type_required_and_forbidden()
    test_file_name_rule_with_example()
    test_cover_required_items_and_negation()
    test_references_and_typography()
    test_submission_channel_is_not_guessed()
    test_spec_fields_and_empty_input()
    test_rules_are_deduped_and_describable()
    print("\nFORMAT SPEC TESTS PASS")
