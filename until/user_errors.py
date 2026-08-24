"""내부 예외를 짧고 행동 가능한 사용자 메시지로 바꾼다.

SDK 예외 원문에는 제공자 URL·모델명·요청 정보가 섞일 수 있으므로 웹 화면에
그대로 내보내지 않는다. 로깅·분류는 원래 예외 객체를 사용하는 호출부의 몫이다.
"""
from __future__ import annotations


def is_auth_error(exc: BaseException) -> bool:
    """래핑된 예외의 원인까지 따라가 eTL 인증 실패를 판별한다."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        status = (getattr(current, "status_code", None)
                  or getattr(current, "code", None))
        text = str(current).lower()
        if status in (401, 403) or any(word in text for word in (
                "인증 실패", "토큰 무효", "토큰이 무효", "토큰 만료", "세션 만료",
                "unauthorized", "forbidden", "invalid token")):
            return True
        current = current.__cause__ or current.__context__
    return False


def user_error_message(exc: Exception, action: str = "요청을 처리") -> str:
    chain = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current)); chain.append(current)
        current = current.__cause__ or current.__context__
    text = " ".join(str(item).lower() for item in chain)
    status = next((getattr(item, "status_code", None) or getattr(item, "code", None)
                   for item in chain
                   if getattr(item, "status_code", None) is not None
                   or getattr(item, "code", None) is not None), None)
    from .academic_policy import AiUseProhibitedError
    if any(isinstance(item, AiUseProhibitedError) for item in chain):
        return ("이 과제는 AI 사용을 명시적으로 금지합니다. Until은 초안이나 답안을 "
                "생성하지 않습니다. 과제 지시를 직접 따라 작성하세요.")
    from .practice_audit import PracticePreflightError
    for item in chain:
        if isinstance(item, PracticePreflightError):
            return str(item)
    if is_auth_error(exc):
        return "eTL 로그인 정보가 만료됐어요. 홈에서 다시 연결해 주세요."
    if status == 429 or any(word in text for word in (
            "rate limit", "rate_limit", "사용량 한도", "quota")):
        return "AI 사용량이 잠시 한도에 도달했어요. 잠시 후 다시 시도해 주세요."
    if any(isinstance(item, (TimeoutError, ConnectionError)) for item in chain) or any(word in text for word in (
            "timed out", "timeout", "connection", "network", "name resolution")):
        return "네트워크 연결이 불안정해요. 연결을 확인하고 다시 시도해 주세요."
    if "pip install" in text or "no module named" in text:
        return "서버에 필요한 구성요소가 준비되지 않았어요. 관리자에게 알려 주세요."
    return f"{action} 중 문제가 생겼어요. 잠시 후 다시 시도해 주세요."
