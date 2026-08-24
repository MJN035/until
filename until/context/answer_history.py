"""결정 답변 히스토리 — 과거 내 답을 비슷한 결정에 다시 제안한다(결정적·LLM 0).

학생이 결정을 채울 때마다 (결정 노트, 답, 성격)을 로컬 JSONL로 적립하고,
새 결정이 과거 것과 충분히 비슷하면 "지난번엔 이렇게 답했어요"로 보여준다.
경계선 철학 유지: 자동 채움이 아니라 제안 — 확정은 사람의 클릭.
저장 위치는 `_until_work/`(gitignore 영역, 개인 데이터 커밋 방지).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional

# 기본 저장 경로 — 테스트에서 임시 경로로 바꿀 수 있게 모듈 전역.
HISTORY_PATH = Path("_until_work/answer_history.jsonl")

# 클라우드(멀티유저) 모드용 요청 스코프 경로 오버라이드 — 웹 핸들러가 요청 시작 시
# 사용자별 경로를 걸면, path 인자를 안 넘기는 깊은 호출부(pipeline 등)도 그 사용자의
# 히스토리를 읽고 쓴다. 스레드=요청(ThreadingHTTPServer)이라 thread-local이 곧 요청 스코프.
import threading as _threading
_TL_PATH = _threading.local()


def set_history_path_override(p: Optional[Path]) -> None:
    """이 스레드(요청)의 히스토리 경로 오버라이드 설정. None이면 해제."""
    _TL_PATH.value = p


def _resolve_path(path: Optional[Path]) -> Path:
    if path is not None:
        return Path(path)
    o = getattr(_TL_PATH, "value", None)
    return Path(o) if o is not None else HISTORY_PATH


def history_path() -> Path:
    """현재 스코프(오버라이드 포함)의 히스토리 파일 경로 — 표시·삭제용."""
    return _resolve_path(None)
# 재제안 최소 유사도(문자열 비율). 과제가 달라도 반복되는 결정(말투·톤·진로 연결 등)을 잡는 선.
_MIN_SIMILARITY = 0.5
_MAX_KEEP = 500  # 파일 무한 증식 방지(오래된 것부터 버림)


@dataclass
class PastAnswer:
    note: str
    answer: str
    category: str = ""
    similarity: float = 0.0


# HTML 엔티티 무해화 — 비브라우저 클라이언트(스크립트·자동 테스트)가 페이지에서 긁은
# 이스케이프된 값(&#x27; 등)을 그대로 제출하면 히스토리가 오염된다.
# 정화는 **load 한쪽에서만**(적립은 원문 보존 — 사용자가 의도한 엔티티 텍스트를
# 저장 시점에 비가역 훼손하지 않기 위해). 변환도 html.unescape 전체가 아니라
# **세미콜론까지 완전한 엔티티만** 치환(단일 패스, 세미콜론 없는 'R&amp'류·
# 잘린 부분 엔티티 '&#x2'는 보존 — 조용한 삭제/과변환 방지).
import re as _ent_re_mod
_ENT_RE = _ent_re_mod.compile(r"&(?:#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]{1,10});")


def _dehtml(s: str) -> str:
    """완전한 엔티티만 골라 1패스 치환(평문·부분 엔티티는 그대로)."""
    if not _ENT_RE.search(s):
        return s
    import html as _html

    def _one(m):
        u = _html.unescape(m.group(0))
        # 해석 결과가 비거나 제어문자(&#x2; 등)면 원문 유지(조용한 삭제 방지).
        if not u or (len(u) == 1 and (ord(u) < 32 and u not in "\t\n")):
            return m.group(0)
        return u

    return _ENT_RE.sub(_one, s)


def record_answers(notes: List[str], answers: Dict[int, str],
                   path: Optional[Path] = None) -> int:
    """답이 채워진 결정만 히스토리에 적립. 반환=적립 건수. 실패는 조용히 0."""
    p = _resolve_path(path)
    from ..boundary.rationale import classify_decision
    rows = []
    for i, note in enumerate(notes, 1):
        # 원문 그대로 적립(정화는 load 한쪽에서만 — 이중 unescape로 인한
        # 비가역 훼손 방지. 사용자가 의도한 '&amp;' 같은 텍스트도 보존).
        ans = (answers.get(i) or "").strip()
        note = (note or "").strip()
        if not ans or not note:
            continue
        rows.append({
            "note": note, "answer": ans[:400],
            "category": classify_decision(note).category,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
    if not rows:
        return 0
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        _prune(p)
    except OSError:
        return 0
    return len(rows)


def _prune(p: Path) -> None:
    """최근 _MAX_KEEP줄만 유지."""
    try:
        lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if len(lines) > _MAX_KEEP:
            p.write_text("\n".join(lines[-_MAX_KEEP:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def load_history(path: Optional[Path] = None) -> List[dict]:
    """히스토리 로드(깨진 줄·비문자열 필드 스킵). 없으면 []."""
    p = _resolve_path(path)
    if not p.exists():
        return []
    out: List[dict] = []
    # 비-UTF8로 저장된 파일이 전체 기능을 조용히 죽이지 않게 대체문자로 읽는다.
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        # 타입 검증 — note/answer가 비문자열이면(손상·조작) 스킵(렌더 500 방지).
        if (isinstance(row, dict)
                and isinstance(row.get("note"), str) and row["note"].strip()
                and isinstance(row.get("answer"), str) and row["answer"].strip()):
            # category도 문자열 강제(비문자열이면 "" — 요약 Counter 크래시 방지).
            if not isinstance(row.get("category"), str):
                row["category"] = ""
            # 과거에 이스케이프된 채 적립된 레거시 행 무해화(렌더·재제안 오염 방지).
            row["note"] = _dehtml(row["note"])
            row["answer"] = _dehtml(row["answer"])
            out.append(row)
    return out


# 내용어 추출용 — 결정 노트의 상투 어구(정하세요/어떻게 등)는 유사도 비교에서 제외.
import re as _re
_STOPWORDS = {
    "어떻게", "어디로", "어디에", "어떤", "무엇을", "어느", "할지", "정할지",
    "정하세요", "고르세요", "하세요", "정하기", "결정", "본인", "판단", "필요",
    "당신", "선택",
}


def _content_tokens(text: str) -> set:
    words = _re.findall(r"[가-힣A-Za-z0-9]{2,}", text or "")
    return {w for w in words if w not in _STOPWORDS}


def print_history_summary(path: Optional[Path] = None) -> None:
    """내 답 히스토리 요약 출력 — 무엇이 기억되고 있는지 투명하게 보여준다.

    `python -m until.context.answer_history [경로]`. 삭제는 파일을 지우면 된다.
    """
    p = _resolve_path(path)
    rows = load_history(p)
    if not rows:
        print(f"기록 없음: {p}")
        return
    from collections import Counter
    cats = Counter(r.get("category") or "고유 판단" for r in rows)
    print(f"=== 내 결정 답 히스토리 ({p}) ===")
    print(f"  적립 {len(rows)}건 · 성격 분포: "
          + " · ".join(f"{k} {v}" for k, v in cats.most_common(4)))
    style = answers_style_hint(path=p)
    if style:
        print(f"  {style.lstrip('- ')}")
    print("  최근 답:")
    for r in rows[-5:]:
        note = r["note"][:34] + ("…" if len(r["note"]) > 34 else "")
        ans = r["answer"][:40] + ("…" if len(r["answer"]) > 40 else "")
        print(f"    · {note} → {ans}")
    print(f"  (지우려면 파일 삭제: {p})")


def answers_style_hint(path: Optional[Path] = None, min_samples: int = 3) -> str:
    """과거 내 답들의 문체(종결어미) 한 줄 힌트 — voice 프로파일과 연계용(결정적).

    표본이 min_samples 미만이거나 문체가 '미상'이면 빈 문자열(주입 안 함).
    """
    answers = [r["answer"] for r in load_history(path)][-30:]  # 최근 30답
    if len(answers) < min_samples:
        return ""
    from .voice import _split_sentences, _detect_ending
    sents: List[str] = []
    for a in answers:
        sents.extend(_split_sentences(a))
    if not sents:
        return ""
    style = _detect_ending(sents)
    if style in ("미상", "혼합"):
        return ""
    return (f"- 결정 답변 문체: 이 학생은 결정에 답할 때 주로 '{style}'를 쓴다. "
            "제안·반영 문장도 같은 문체로 맞춰라.")


def answers_context_hint(path: Optional[Path] = None, min_samples: int = 3,
                         k: int = 8) -> str:
    """과거 결정 답에서 추출한 '내 맥락' 힌트(결정적·LLM 0) — 입력 단계 없이,
    사용할수록 초안이 이 학생의 소재(전공·관심·경험)로 개인화되게 한다.

    - 자주 등장한 내용어(2회 이상)와, 개인 소재가 진하게 담기는 성격(진로·경험,
      관점·논지)의 최근 답 원문 몇 개를 힌트로 만든다.
    - 경계선 유지: '소재 우선 활용' 힌트일 뿐 자동 확정이 아니며, 힌트에 없는
      경험을 지어내지 말라는 규칙(프롬프트 [개인 맥락 활용])과 짝으로 동작한다.
    - 표본 min_samples 미만이면 빈 문자열(주입 안 함)."""
    rows = load_history(path)
    if len(rows) < min_samples:
        return ""
    from collections import Counter
    cnt: Counter = Counter()
    for r in rows[-100:]:                       # 최근 100답만(옛 소재 고착 방지)
        cnt.update(_content_tokens(r["answer"]))
    common = [w for w, c in cnt.most_common(k * 3) if c >= 2][:k]
    personal = [r["answer"] for r in rows
                if (r.get("category") or "") in ("진로·경험", "관점·논지")][-3:]
    if not common and not personal:
        return ""
    lines = ["【내 맥락 — 과거 결정 답에서 추출(자동)】"]
    if common:
        lines.append(f"- 자주 등장한 소재·표현: {', '.join(common)}.")
    for a in personal:
        a = a[:120] + ("…" if len(a) > 120 else "")
        lines.append(f"- 최근 관점·경험 답변: \"{a}\"")
    lines.append(
        "- 위 소재가 이 과제와 맞으면 예시·후보·논거에 우선 활용하라. "
        "단, 여기 없는 개인 경험·사실을 지어내지는 말 것.")
    return "\n".join(lines)


def suggest_from_history(note: str, path: Optional[Path] = None) -> Optional[PastAnswer]:
    """과거 답 중 가장 비슷한 결정의 답을 제안. 조건 미달이면 None.

    과매칭 방지 3중 게이트(한국어 결정 노트는 상투 어미가 유사도를 지배함):
      ① 문자 유사도 ≥ _MIN_SIMILARITY, ② 결정 성격(category) 일치,
      ③ 내용어(상투어 제외 토큰) 1개 이상 공유.
    같은 노트가 여러 번이면 최신(파일 뒤쪽) 답을 우선한다.
    """
    note = (note or "").strip()
    if not note:
        return None
    from ..boundary.rationale import classify_decision
    query_cat = classify_decision(note).category
    query_tokens = _content_tokens(note)
    best: Optional[PastAnswer] = None
    for row in load_history(path):
        r = SequenceMatcher(None, note, row["note"]).ratio()
        if r < _MIN_SIMILARITY:
            continue
        # 성격 게이트 — 저장 시 기록한 분류와 불일치하면 다른 종류의 결정.
        row_cat = row.get("category") or classify_decision(row["note"]).category
        if row_cat != query_cat:
            continue
        # 내용어 게이트 — 상투 어미만 닮은 경우("제목을 정하세요" vs "결론을 정하세요") 배제.
        if query_tokens and not (query_tokens & _content_tokens(row["note"])):
            continue
        # >= 로 비교해 동률이면 뒤(최신) 기록이 이긴다.
        if best is None or r >= best.similarity:
            best = PastAnswer(note=row["note"], answer=row["answer"],
                              category=row_cat, similarity=round(r, 3))
    return best


if __name__ == "__main__":  # python -m until.context.answer_history [경로]
    import sys
    print_history_summary(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
