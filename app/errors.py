"""공통 오류 형식: { "error": { "code", "message", "request_id" } }."""

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("arithon")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def not_found(message: str = "찾을 수 없습니다.") -> ApiError:
    return ApiError(404, "NOT_FOUND", message)


def conflict(code: str, message: str) -> ApiError:
    return ApiError(409, code, message)


def invalid_state(message: str) -> ApiError:
    return ApiError(409, "INVALID_CASE_STATE", message)


def request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if not rid:
        rid = "req_" + uuid.uuid4().hex[:12]
        request.state.request_id = rid
    return rid


def error_response(request: Request, status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "request_id": request_id(request)}},
        headers=headers,
    )


_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
}


def _field_messages(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(p) for p in err.get("loc", []) if p not in ("body", "query", "header", "path"))
        msg = err.get("msg", "")
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, "):]
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "입력 형식을 확인해 주세요. " + "; ".join(parts)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return error_response(request, exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        # 원문이 로그에 남지 않도록 입력값(input)은 기록하지 않는다.
        return error_response(request, 422, "VALIDATION_ERROR", _field_messages(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = _HTTP_CODES.get(exc.status_code, "ERROR")
        message = exc.detail if isinstance(exc.detail, str) else "요청을 처리할 수 없습니다."
        if exc.status_code == 404 and message == "Not Found":
            message = "찾을 수 없습니다."
        return error_response(request, exc.status_code, code, message)

    @app.exception_handler(OperationalError)
    async def _db(request: Request, exc: OperationalError):
        logger.error("DB 오류 request_id=%s: %s", request_id(request), type(exc).__name__)
        return error_response(request, 503, "DB_UNAVAILABLE", "데이터베이스에 잠시 연결할 수 없어요. 다시 시도해 주세요.")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        logger.exception("처리되지 않은 오류 request_id=%s", request_id(request))
        return error_response(request, 500, "INTERNAL_ERROR", "서버 오류가 발생했어요.")
