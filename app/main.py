"""생각법정 FastAPI 앱 (전시용 v1.1)."""

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.ai.llm import provider_name
from app.config import get_settings
from app.db import engine, init_db
from app.errors import register_error_handlers
from app.routers import board, cases, court

settings = get_settings()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("arithon")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    logger.info("생각법정 API 시작 (db=%s, ai=%s)", engine.dialect.name, provider_name())
    yield


app = FastAPI(
    title="생각법정 API (전시용)",
    version="1.1.0",
    description="2026 간지톤 · 체포하겠어 — 생각법정 백엔드. 모든 /api/v1 요청에 X-Demo-Id(UUID v4) 헤더가 필요합니다.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["X-Demo-Id", "Idempotency-Key", "Content-Type", "Accept"],
    max_age=600,
)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request.state.request_id = "req_" + uuid.uuid4().hex[:12]
    response = await call_next(request)
    response.headers["X-Request-Id"] = request.state.request_id
    return response


register_error_handlers(app)

API_PREFIX = "/api/v1"
app.include_router(cases.router, prefix=API_PREFIX)
app.include_router(court.router, prefix=API_PREFIX)
app.include_router(board.router, prefix=API_PREFIX)


@app.get("/health/live", tags=["health"])
def health_live():
    return {"status": "ok"}


@app.get("/health", tags=["health"])
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "db": "ok", "ai": provider_name()}
    except Exception:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"status": "error", "db": "unavailable", "ai": provider_name()})


@app.get("/", include_in_schema=False)
def root():
    return {"service": "생각법정 API", "docs": "/docs", "health": "/health"}
