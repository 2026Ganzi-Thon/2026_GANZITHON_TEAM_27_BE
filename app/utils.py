"""비밀번호 해시(Argon2id)·커서 페이지네이션 등 공용 도구."""

import base64
import json
import uuid
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.errors import ApiError, not_found

_hasher = PasswordHasher()  # 기본값이 Argon2id
T = TypeVar("T")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def parse_uuid(value: str, message: str = "찾을 수 없습니다.") -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise not_found(message) from None


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return 20
    if limit < 1 or limit > 50:
        raise ApiError(422, "VALIDATION_ERROR", "limit 은 1~50 사이여야 합니다.")
    return limit


def _encode(key: Sequence[Any]) -> str:
    raw = json.dumps(list(key), ensure_ascii=False, default=str).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(cursor: str) -> list:
    try:
        pad = "=" * (-len(cursor) % 4)
        return json.loads(base64.urlsafe_b64decode(cursor + pad).decode())
    except Exception:
        raise ApiError(422, "VALIDATION_ERROR", "cursor 형식이 올바르지 않습니다.") from None


def paginate(rows: list[T], key: Callable[[T], tuple], cursor: str | None, limit: int) -> tuple[list[T], str | None]:
    """key 기준 내림차순으로 정렬된 rows 에서 cursor 다음 limit 개를 고른다."""
    if cursor:
        after = tuple(_decode(cursor))
        rows = [r for r in rows if tuple(str(x) for x in key(r)) < tuple(str(x) for x in after)]
    page = rows[:limit]
    next_cursor = _encode(key(page[-1])) if len(rows) > limit and page else None
    return page, next_cursor
