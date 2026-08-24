"""
ContextBundle — Execution에 주입할 맥락을 한데 모은다.

  (1) 수업자료/과제파일  (2) 내 관련 파일  (3) 내 말투(VoiceProfile)
→ Execution은 (1)(2)를 '근거 자료(SourceDoc)'로, (3)을 '문체 지침'으로 받는다.
경계선 원칙은 그대로 — 맥락이 풍부해져도 사람 판단 지점은 DECISION으로 남긴다.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional

from ..llm.base import SourceDoc
from .voice import VoiceProfile, voice_from_dir
from .retrieval import find_relevant, keywords_from_spec, Hit


@dataclass
class ContextBundle:
    course_hits: List[Hit] = field(default_factory=list)   # 수업자료
    my_hits: List[Hit] = field(default_factory=list)       # 내 파일
    voice: VoiceProfile = field(default_factory=VoiceProfile)
    keywords: List[str] = field(default_factory=list)

    def to_sources(self) -> List[SourceDoc]:
        srcs: List[SourceDoc] = []
        for h in self.course_hits:
            srcs.append(SourceDoc(title=f"[수업자료] {h.document.source}", text=h.document.text[:6000]))
        for h in self.my_hits:
            srcs.append(SourceDoc(title=f"[내 파일] {h.document.source}", text=h.document.text[:6000]))
        return srcs

    @property
    def voice_hint(self) -> str:
        return self.voice.to_prompt_hint()

    def summary(self) -> str:
        return (
            f"키워드 {len(self.keywords)}개 | 수업자료 {len(self.course_hits)}건 "
            f"| 내 파일 {len(self.my_hits)}건 | 말투샘플 {self.voice.n_samples}건"
            + (f"({self.voice.ending_style})" if self.voice.n_samples else "")
        )


def assemble_context(
    spec: dict, *,
    course_dir: Optional[str] = None,
    my_files_dir: Optional[str] = None,
    voice_dir: Optional[str] = None,
    voice_llm=None,
    voice_profile: Optional[VoiceProfile] = None,
    k: int = 3,
) -> ContextBundle:
    """voice_profile: 미리 추출된 프로파일(eTL 제출물 자동 학습 등).

    voice_dir(직접 올린 글 샘플)가 있으면 그쪽이 우선 — 명시 입력 > 자동 학습."""
    kws = keywords_from_spec(spec)
    if voice_dir:
        voice = voice_from_dir(voice_dir, llm=voice_llm)
    else:
        voice = voice_profile or VoiceProfile()
    return ContextBundle(
        keywords=kws,
        course_hits=find_relevant(course_dir, kws, k) if course_dir else [],
        my_hits=find_relevant(my_files_dir, kws, k) if my_files_dir else [],
        voice=voice,
    )
