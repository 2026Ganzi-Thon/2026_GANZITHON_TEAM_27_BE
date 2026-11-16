"""지원 안내(support_required) 판단.

자유 입력에 자해·자살 등 위기 신호가 있으면 법정 흐름을 멈추고 상담 연락처를 안내한다.
안내로 전환한 입력 원문은 저장하거나 로그에 남기지 않는다.
"""

import re

from app.config import get_settings

settings = get_settings()

# 공백을 제거한 뒤 비교한다 ("죽고 싶" == "죽고싶")
_CRISIS_KEYWORDS = [
    "자살",
    "자해",
    "죽고싶",
    "죽고만싶",
    "죽어버리고싶",
    "죽어버릴",
    "죽을래",
    "죽어야겠",
    "죽으려고",
    "죽고말",
    "목숨을끊",
    "목숨끊",
    "삶을끝",
    "인생을끝내",
    "끝내고싶어",
    "사라지고싶",
    "없어지고싶",
    "살기싫",
    "살고싶지않",
    "손목을긋",
    "손목긋",
    "뛰어내리",
    "투신",
    "유서",
    "번개탄",
    "목을매",
    "목매",
    "수면제를모아",
    "약을모아",
    "killmyself",
    "suicide",
    "wanttodie",
    "selfharm",
]

_WS = re.compile(r"\s+")


def needs_support(*texts: str | None) -> bool:
    for text in texts:
        if not text:
            continue
        compact = _WS.sub("", text).lower()
        if any(k in compact for k in _CRISIS_KEYWORDS):
            return True
    return False


def support_payload() -> dict:
    return {
        "outcome": "support_required",
        "support": {
            "message": settings.support_message,
            "contacts": settings.support_contact_list,
        },
    }
