"""Versioned public policy profiles. These are baselines, never course permission."""
from __future__ import annotations

from .policy_hierarchy import PolicyLayer, PolicySource


SNU_AI_GUIDELINE_URL = "https://www.snu.ac.kr/snunow/press?bbsidx=166511&md=v"
SNU_STUDENT_GUIDE_URL = "https://humanities.snu.ac.kr/ai/students"


def snu_2026_baseline() -> PolicyLayer:
    """SNU-wide ethical floor; instructor policy still controls permitted use."""
    return PolicyLayer(
        scope="institution", scope_id="snu", ai_use="unclear",
        required_actions=(
            "follow_instructor_policy", "disclose_ai_use", "verify_facts_and_sources",
            "respect_copyright", "retain_human_responsibility",
        ),
        hard_constraints=(
            "no_sensitive_or_private_data_upload",
            "no_misrepresentation_of_ai_work_as_own_judgment",
            "no_instructor_prohibited_use",
        ),
        source=PolicySource(
            "snu-ai-guideline-2026", "서울대학교 AI 가이드라인",
            SNU_AI_GUIDELINE_URL, "2026-03-18",
            "교수자는 허용·금지 범위와 보고 방식을 안내하고 학생은 수업 정책을 준수"),
    )
