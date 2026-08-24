"""
VoiceProfile — 사용자의 기존 글에서 말투·문체를 추출(결정적, 토큰 0).

"나의 기존 작성하는 말투 & 맥락 확인" 요구의 구현. 과거 글 샘플을 받아
종결어미 성향·문장 길이·자주 쓰는 표현을 뽑고, Execution 시스템 프롬프트에
넣을 '문체 지침' 문자열을 만든다. (LLM은 이 지침대로 사용자 말투를 모사)
"""
from __future__ import annotations
import re
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

_STOP = set("그리고 그러나 하지만 그래서 또한 그런데 이런 저런 그런 어떤 무슨 너무 정말 그냥 좀 더 또 의 가 이 을 를 은 는 에 와 과 도 로 으로 에서 에게 한 그 저 이런게 것 수 등".split())


@dataclass
class VoiceProfile:
    ending_style: str = "미상"          # 해요체 | 한다체 | 합니다체 | 반말 | 혼합
    avg_sentence_len: int = 0
    frequent_terms: List[str] = field(default_factory=list)
    uses_emoji: bool = False
    exclaim_ratio: float = 0.0
    n_samples: int = 0
    llm_summary: str = ""

    def to_prompt_hint(self) -> str:
        if self.n_samples == 0:
            return ""  # 샘플 없으면 문체 지침 없음
        terms = ", ".join(self.frequent_terms[:6]) or "(없음)"
        emoji = "가끔 사용" if self.uses_emoji else "거의 안 씀"
        hint = (
            "【문체 지침 — 사용자 말투 모사】\n"
            f"- 종결어미: 주로 '{self.ending_style}'.\n"
            f"- 평균 문장 길이: 약 {self.avg_sentence_len}자.\n"
            f"- 자주 쓰는 표현: {terms}.\n"
            f"- 이모지/느낌표: {emoji}.\n"
        )
        if self.llm_summary:
            hint += f"- LLM 요약: {self.llm_summary}\n"
        return hint + "위 말투를 자연스럽게 따라 쓰되, 과제 격식이 필요한 부분은 적절히 조정하라."


def _split_sentences(text: str) -> List[str]:
    parts = re.split(r"[.!?\n]+", text)
    return [s.strip() for s in parts if s.strip()]


def _detect_ending(sentences: List[str]) -> str:
    c = Counter()
    for s in sentences:
        t = s.rstrip()
        # '니다'로 끝나면 합니다체 — 단 평서형 '아니다'(한다체)는 제외(부정 lookbehind).
        if re.search(r"(?<!아)니다$", t): c["합니다체"] += 1
        elif t.endswith("요"):                        c["해요체"] += 1
        elif re.search(r"(한다|이다|는다|다)$", t):    c["한다체"] += 1
        elif re.search(r"(어|아|지|네|야|군|구나)$", t): c["반말"] += 1
    if not c:
        return "미상"
    top, n = c.most_common(1)[0]
    # 1위가 과반 못 넘으면 혼합
    return top if n >= sum(c.values()) * 0.5 else "혼합"


def _frequent_terms(text: str, k: int = 8) -> List[str]:
    tokens = re.findall(r"[가-힣A-Za-z]{2,}", text)
    cnt = Counter(w for w in tokens if w not in _STOP)
    return [w for w, _ in cnt.most_common(k)]


def build_voice_profile(texts: List[str]) -> VoiceProfile:
    texts = [t for t in texts if t.strip()]
    if not texts:
        return VoiceProfile()
    joined = "\n".join(texts)
    sents = _split_sentences(joined)
    avg = int(sum(len(s) for s in sents) / max(1, len(sents)))
    return VoiceProfile(
        ending_style=_detect_ending(sents),
        avg_sentence_len=avg,
        frequent_terms=_frequent_terms(joined),
        uses_emoji=bool(re.search(r"[\U0001F300-\U0001FAFF☀-➿]", joined)),
        exclaim_ratio=round(joined.count("!") / max(1, len(sents)), 2),
        n_samples=len(texts),
    )


def enhance_voice_profile(profile: VoiceProfile, texts: List[str], llm=None) -> VoiceProfile:
    """LLM 1콜로 말투 요약을 보강한다. 실패하면 원래 프로파일을 그대로 반환."""
    if llm is None or profile.n_samples == 0:
        return profile
    sample = "\n\n---\n\n".join(t[:2000] for t in texts if t.strip())[:6000]
    user = (
        "아래 사용자 글 샘플의 말투를 한국어 JSON으로 요약하라. "
        "필드는 summary(한 문장), frequent_terms(문자열 배열)만 사용한다.\n\n"
        f"{sample}"
    )
    try:
        res = llm.complete(
            "당신은 말투 분석기다. 과제 내용 판단은 하지 말고 문체 특징만 요약한다.",
            user,
            tag="voice",
            json=True,
        )
        data = json.loads(res.text)
    except Exception:
        return profile

    summary = str(data.get("summary", "")).strip()
    terms = data.get("frequent_terms", [])
    if isinstance(terms, list):
        merged = profile.frequent_terms[:]
        for term in terms:
            text = str(term).strip()
            if text and text not in merged:
                merged.append(text)
        profile.frequent_terms = merged[:8]
    if summary:
        profile.llm_summary = summary
    return profile


def voice_from_dir(folder: str, *, llm=None) -> VoiceProfile:
    """폴더의 글 샘플(txt/md)로 말투 프로파일 생성. 미존재/빈 폴더면 빈 프로파일."""
    p = Path(folder)
    if not p.exists():
        return VoiceProfile()
    texts = []
    for f in sorted(p.glob("**/*")):
        if f.suffix.lower() in (".txt", ".md") and f.is_file():
            texts.append(f.read_text(encoding="utf-8", errors="ignore"))
    return enhance_voice_profile(build_voice_profile(texts), texts, llm=llm)
