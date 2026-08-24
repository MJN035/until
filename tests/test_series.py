"""반복 시리즈 감지 + '지난 제출물' 맥락 테스트 (오프라인·결정적)."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from until.context.series import (
    series_key, find_predecessors, predecessors_to_sources,
    rows_from_canvas_submissions,
)


def test_series_key():
    # 실코퍼스 실측 시리즈 3형 — 주차형·날짜형·번호형이 각각 같은 키로 묶인다.
    assert series_key("3주차 소감문 (3/17)") == series_key("5주차 소감문 (3/31 23:59)") != ""
    assert series_key("1주차 소감문 제출") == series_key("14주차 소감문 제출") != ""
    assert series_key("3/10 조별활동 보고서") == series_key("3/24 조별활동 보고서") != ""
    assert series_key("실습4 레포트") == series_key("실습 10 레포트") != ""
    assert series_key("Assignment #1") == series_key("Assignment #3") != ""
    # 다른 시리즈끼리는 다른 키.
    assert series_key("3주차 소감문") != series_key("3주차 질의")
    # 숫자 없는 제목·빈 제목은 시리즈가 아니다.
    assert series_key("기말 보고서") == ""
    assert series_key("") == ""
    print("OK series key (주차·날짜·번호 정규화)")


def test_find_predecessors():
    subs = [
        {"title": "2주차 소감문 (3/10)", "submitted_at": "2026-03-10T08:00:00Z",
         "body": "2주차 본문"},
        {"title": "5주차 소감문 (3/31)", "submitted_at": "2026-03-31T08:00:00Z",
         "body": "5주차 본문"},
        {"title": "3주차 질의 (3/16)", "submitted_at": "2026-03-16T08:00:00Z",
         "body": "질의 본문"},                              # 다른 시리즈
        {"title": "4주차 소감문 (3/24)", "submitted_at": "2026-03-24T08:00:00Z",
         "body": ""},                                       # 본문 없음 → 제외
        {"title": "6주차 소감문 (4/7)", "submitted_at": "2026-04-07T08:00:00Z",
         "body": "자기 자신"},
    ]
    got = find_predecessors("6주차 소감문 (4/7)", subs, k=2)
    # 최신순 k건, 같은 제목(자기 자신)·다른 시리즈·빈 본문 제외.
    assert [g["title"] for g in got] == ["5주차 소감문 (3/31)", "2주차 소감문 (3/10)"]
    # 시리즈 아닌 제목은 빈 목록.
    assert find_predecessors("기말 보고서", subs) == []
    assert find_predecessors("6주차 소감문", None) == []
    print("OK find predecessors (최신순·자기제외·본문필수)")


def test_sources_framing():
    srcs = predecessors_to_sources(
        [{"title": "5주차 소감문", "submitted_at": "2026-03-31T08:00:00Z",
          "body": "본문" * 2000}], limit_chars=100)
    assert len(srcs) == 1
    s = srcs[0]
    assert s.title.startswith("[지난 제출물]")
    # 복사 금지 지침이 본문에 명시(경계선 유지) + 길이 상한.
    assert "복사" in s.text and "참고" in s.text
    assert len(s.text) < 400
    assert predecessors_to_sources([]) == []
    print("OK predecessor sources (라벨·복사금지 지침·상한)")


def test_rows_from_canvas():
    data = [
        {"assignment": {"name": "2주차 소감문", "group_category_id": None},
         "submitted_at": "2026-03-10T08:00:00Z",
         "body": "<p>첫 문단</p><p>둘째 &amp; 문단</p>"},
        {"assignment": {"name": "조별 보고서", "group_category_id": 7},
         "submitted_at": "2026-03-11T08:00:00Z", "body": "<p>팀 글</p>"},  # 조별 제외
        "쓰레기",                                                          # 비-dict 방어
        {"assignment": {}, "submitted_at": "", "body": ""},
    ]
    rows = rows_from_canvas_submissions(data)
    titles = [r["title"] for r in rows]
    assert "2주차 소감문" in titles and "조별 보고서" not in titles
    first = rows[0]
    assert "첫 문단" in first["body"] and "둘째 & 문단" in first["body"]
    assert "<p>" not in first["body"]
    assert rows_from_canvas_submissions(None) == []
    print("OK canvas rows (HTML 평문화·조별 제외·방어)")


def test_collect_wiring_smoke():
    # collect_with_materials가 시리즈 소스를 실제로 주입하는지 — 가짜 어댑터로.
    from until.context.series import find_predecessors as fp  # noqa: F401
    from until import web
    import inspect
    src = inspect.getsource(web.collect_with_materials)
    assert "my_submissions_json" in src and "predecessors_to_sources" in src
    print("OK collect_with_materials 배선")


def test_stage_predecessors_link_draft_to_revision():
    """회차가 아니라 **단계**로 이어진 과제를 잇는다 — '초고→최종본', '작성→수정'.

    실코퍼스(대학 글쓰기 1·2)의 과제명은 `요약문 쓰기`·`서론 작성`·`서론 수정`·
    `과제 1 (논문 쓰기) 초고 제출`·`과제 1 (논문) 최종본 제출`처럼 회차 번호가
    없거나 단계어만 다르다. `series_key`는 **숫자를 요구하고 단계어를 남겨** 두므로
    이 쌍을 하나도 못 잡았다 — 앞 과제에서 쓴 글이 있는데도 새 과제가 처음부터
    시작했다(사용자 지적 2026-08-23).
    """
    from until.context.series import (find_stage_predecessors,
                                      predecessors_to_sources, series_key,
                                      stage_stem)

    # 종전 규칙으로는 전부 미매칭이라는 사실을 함께 고정한다.
    assert series_key("서론 작성") == "" and series_key("서론 수정") == ""
    assert series_key("과제 1 (논문 쓰기) 초고 제출") != series_key(
        "과제 1 (논문) 최종본 제출")

    # 줄기는 '무엇에 대한 것인가'만 남는다.
    assert stage_stem("서론 작성") == stage_stem("서론 수정") == "서론"
    assert stage_stem("과제 1 (논문 쓰기) 초고 제출") == stage_stem(
        "과제 1 (논문) 최종본 제출") == "논문"
    assert stage_stem("요약문 쓰기") == "요약문"
    # 줄기가 1글자로 쪼그라들면 매칭하지 않는다(아무거나 이어 붙이기 방지).
    assert stage_stem("제출") == "" and stage_stem("과제") == ""

    subs = [
        {"title": "서론 작성", "submitted_at": "2025-04-01", "body": "내 서론 초안. " * 20},
        {"title": "요약문 쓰기", "submitted_at": "2025-03-10", "body": "요약문. " * 20},
        {"title": "과제 1 (논문 쓰기) 초고 제출", "submitted_at": "2025-10-01",
         "body": "논문 초고. " * 20},
    ]
    assert [h["title"] for h in find_stage_predecessors("서론 수정", subs)] == ["서론 작성"]
    assert [h["title"] for h in find_stage_predecessors("과제 1 (논문) 최종본 제출", subs)] \
        == ["과제 1 (논문 쓰기) 초고 제출"]
    # 관계없는 과제는 끌어오지 않는다.
    assert find_stage_predecessors("피피티 제출", subs) == []
    # 자기 자신(재제출)은 제외.
    assert find_stage_predecessors("서론 작성", subs) == []
    # 본문 없는 제출은 참고 대상이 아니다.
    assert find_stage_predecessors(
        "서론 수정", [{"title": "서론 작성", "submitted_at": "2025-04-01", "body": ""}]) == []

    # 맥락으로 넣을 때 '그대로 옮기지 말라'는 경계선 지침이 함께 간다.
    srcs = predecessors_to_sources(find_stage_predecessors("서론 수정", subs))
    assert srcs and "서론 작성" in srcs[0].title
    print("OK 단계 연결 (초고→최종본 · 작성→수정, 무관 과제는 제외)")


if __name__ == "__main__":
    test_series_key()
    test_find_predecessors()
    test_stage_predecessors_link_draft_to_revision()
    test_sources_framing()
    test_rows_from_canvas()
    test_collect_wiring_smoke()
    print("\nSERIES TESTS PASS")
