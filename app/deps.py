"""공통 의존성: 체험 ID(X-Demo-Id), 요청 제한."""

import time
import uuid
from collections import defaultdict, deque
from threading import Lock

from fastapi import Header, Request

from app.config import get_settings
from app.errors import ApiError

settings = get_settings()


class RateLimiter:
    """프로세스 메모리 기반 슬라이딩 윈도 요청 제한 (전시용 단일 인스턴스 기준)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def hit(self, key: str, limit: int, window: float = 60.0) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def demo_id_dep(request: Request, x_demo_id: str | None = Header(default=None, alias="X-Demo-Id")) -> uuid.UUID:
    """X-Demo-Id 헤더의 UUID v4 를 확인해 체험 ID 로 쓴다."""
    try:
        if not x_demo_id:
            raise ValueError
        value = uuid.UUID(x_demo_id.strip())
        if value.version != 4:
            raise ValueError
    except ValueError:
        raise ApiError(422, "INVALID_DEMO_ID", "X-Demo-Id 헤더에 UUID v4 체험 ID가 필요합니다.") from None

    if not limiter.hit(f"req:{value}", settings.rate_limit_per_minute):
        raise ApiError(429, "RATE_LIMITED", "요청이 많아요. 잠시 후 다시 시도해 주세요.")
    request.state.demo_id = value
    return value
