import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.board import router as board_router
from app.cases import router as cases_router
from app.common import APIError
from app.database import Database
from app.workflow import build_workflow


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    load_dotenv()
    dsn = os.getenv("DATABASE_URL", "").strip()
    app.state.database = Database(dsn) if dsn else None
    provider = os.getenv("AI_PROVIDER", "mock").strip()
    if provider == "mock":
        app.state.workflow = build_workflow()
    elif provider == "upstage":
        app.state.workflow = build_workflow(
            provider="upstage",
            model=os.getenv("AI_MODEL", "").strip() or None,
            api_key=os.getenv("UPSTAGE_API_KEY", "").strip(),
        )
    else:
        raise RuntimeError("AI_PROVIDER must be mock or upstage")
    yield


app = FastAPI(title="체포하겠어 백엔드 · 전시용", version="1.4.0", lifespan=lifespan)
app.include_router(cases_router)
app.include_router(board_router)


@app.middleware("http")
async def prevent_api_caching(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/v1/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message, "request_id": str(uuid4())}},
        status_code=status,
    )


@app.exception_handler(APIError)
async def api_error(request: Request, exc: APIError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    if any(tuple(error["loc"]) == ("header", "X-Demo-Id") for error in exc.errors()):
        return error_response(422, "INVALID_DEMO_ID", "X-Demo-Id에 UUID v4 값을 보내 주세요.")
    return error_response(422, "VALIDATION_ERROR", "요청 형식과 필수 입력값을 확인해 주세요.")


@app.exception_handler(psycopg.Error)
async def database_error(request: Request, exc: psycopg.Error) -> JSONResponse:
    return error_response(503, "DB_UNAVAILABLE", "데이터베이스에 연결할 수 없거나 처리에 실패했습니다.")


@app.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health", tags=["health"], responses={503: {"description": "Database unavailable"}})
async def readiness(request: Request) -> JSONResponse:
    database: Database | None = request.app.state.database
    if database is None:
        return JSONResponse({"status": "unavailable", "database": "unconfigured"}, status_code=503)
    if not await database.check():
        return JSONResponse({"status": "unavailable", "database": "unreachable"}, status_code=503)
    return JSONResponse({"status": "ok", "database": "connected"})


def configure_cors() -> None:
    load_dotenv()
    origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
    origins.extend(origin.strip() for origin in os.getenv("CORS_ORIGINS", "").split(",") if origin.strip())
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["X-Demo-Id", "Idempotency-Key", "Content-Type"],
    )


configure_cors()
