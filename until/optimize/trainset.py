"""GEPA 학습용 입력 예시 — 라벨 불필요(메트릭이 구조 검증이라 입력만 있으면 됨)."""
from __future__ import annotations

_RAW = [
    {
        "spec": '{"deliverable":"에세이","goal":"한 기술이 한 제도를 어떻게 재편했는지 분석","requirements":["3개 이상 출처 인용","1500단어"],"open_questions":["핵심 논지 방향"]}',
        "sources": "McLuhan: 미디어는 메시지다. Zuboff: 감시 자본주의의 축적 논리. Benkler: 분산 생산과 네트워크의 부.",
    },
    {
        "spec": '{"deliverable":"코드 과제","goal":"정렬 알고리즘 구현과 복잡도 분석","requirements":["테스트 포함","Big-O 설명"],"open_questions":["어떤 알고리즘 선택"]}',
        "sources": "퀵소트 평균 O(n log n) 최악 O(n^2). 머지소트 항상 O(n log n) 추가 메모리. 힙소트 제자리 정렬.",
    },
    {
        "spec": '{"deliverable":"발표 자료","goal":"기후정책 한 사례 비평","requirements":["슬라이드 10장","근거 데이터"],"open_questions":["옹호/비판 입장"]}',
        "sources": "탄소세 사례: 가격 신호로 배출 감소. 비판: 역진성. 배출권 거래제: 총량 관리. 비판: 가격 변동성.",
    },
    {
        "spec": '{"deliverable":"리포트","goal":"한 기업의 시장 진입 전략 분석","requirements":["프레임워크 적용"],"open_questions":["추천 전략"]}',
        "sources": "포터의 5 forces. 블루오션 전략. 선점 효과 vs 후발 주자 이점.",
    },
]


def _examples_from(rows):
    import dspy
    return [
        dspy.Example(spec=r["spec"], sources=r["sources"]).with_inputs("spec", "sources")
        for r in rows
        if r.get("spec") and r.get("sources")
    ]


def build_trainset():
    return _examples_from(_RAW)


def build_trainset_with_feedback(feedback_path: str | None = None):
    """기본 예시 + 베타 피드백 로그(P7)를 합쳐 GEPA 학습셋을 만든다.

    실제 사용 기록(spec+sources)이 그대로 최적화 데이터가 된다(라벨 불필요).
    로그가 없으면 기본 예시만 반환한다.
    """
    from ..feedback import quality_sorted_examples, DEFAULT_LOG
    # 준비 점검 경고가 적었던(품질 좋은) 실행을 앞에 — GEPA가 좋은 신호부터 본다.
    rows = list(_RAW) + quality_sorted_examples(feedback_path or DEFAULT_LOG)
    return _examples_from(rows)
