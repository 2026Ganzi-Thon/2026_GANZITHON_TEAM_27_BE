"""Shared request handling for the exhibition API."""

import base64
import binascii
import json
import os
import re
from datetime import datetime
from typing import Annotated
from uuid import NAMESPACE_DNS, UUID, uuid5

from fastapi import Depends, Header, Request
from fastapi.responses import JSONResponse

from app.database import Database


def _client_ip(request: Request) -> str:
    """Render 등 리버스 프록시 뒤에서 실제 클라이언트 IP를 추출한다."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def resolve_demo_id(
    request: Request,
    x_demo_id: Annotated[str | None, Header(alias="X-Demo-Id")] = None,
) -> UUID:
    """DEMO_IDENTITY_MODE=ip(기본)이면 클라이언트 IP로 결정적 UUID를 생성하고, header이면 X-Demo-Id를 사용한다."""
    mode = os.getenv("DEMO_IDENTITY_MODE", "ip").strip().lower()
    if mode == "header":
        try:
            if not x_demo_id:
                raise ValueError
            value = UUID(x_demo_id.strip())
            if value.version != 4:
                raise ValueError
            return value
        except ValueError:
            raise APIError(422, "INVALID_DEMO_ID", "X-Demo-Id에 UUID v4 값을 보내 주세요.") from None
    return uuid5(NAMESPACE_DNS, f"arithon-ip:{_client_ip(request)}")


DemoId = Annotated[UUID, Depends(resolve_demo_id)]


class APIError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def get_db(request: Request) -> Database:
    database = request.app.state.database
    if database is None:
        raise APIError(503, "DB_UNAVAILABLE", "데이터베이스를 연결해 주세요.")
    return database


def encode_cursor(when: datetime, id: UUID) -> str:
    data = json.dumps([when.isoformat(), str(id)], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        when, item_id = json.loads(raw)
        timestamp = datetime.fromisoformat(when)
        if timestamp.tzinfo is None:
            raise ValueError("timezone missing")
        return timestamp, UUID(item_id)
    except (ValueError, TypeError, AttributeError, binascii.Error, OverflowError):
        raise APIError(422, "INVALID_CURSOR", "목록 조회 커서가 올바르지 않습니다.") from None


# Exhibition guard for explicit phrases. A match bypasses the case workflow.
_SUPPORT_PHRASES = re.compile(
    r"죽고싶|죽어버리고싶|죽을거야|죽으려고|죽는방법|목숨을끊|극단적선택을하"
    r"|자살|자해|손목을긋|손목긋|스스로를해치"
    r"|killmyself|endmylife|wanttodie|suicid|selfharm"
)


def needs_support(*texts: str | None) -> bool:
    return any(_SUPPORT_PHRASES.search(re.sub(r"[\W_]+", "", t.casefold()))
               for t in texts if t)


def support_response() -> JSONResponse:
    return JSONResponse({
        "outcome": "support_required",
        "support": {
            "message": "지금 이용할 수 있는 도움을 확인해 주세요.",
            "contacts": [{"label": "자살예방상담전화", "number": "109", "href": "tel:+82109"}],
        },
    })
