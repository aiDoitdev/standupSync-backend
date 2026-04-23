from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
import structlog

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.database import Base, engine, check_db_health
from app.api.v1.router import router as v1_router
from app.jobs.scheduler import start_scheduler, scheduler
from app.middleware.request_id import RequestIDMiddleware
from app.utils.rate_limiter import limiter

configure_logging()
_settings = get_settings()
_log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # In production, rely on Alembic migrations — create_all only for dev convenience
    if not _settings.is_production:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    start_scheduler()
    _log.info("app.started", environment=_settings.environment)
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)
    _log.info("app.stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="StandupSync API",
        description="Daily check-in tool for remote teams",
        version="1.0.0",
        docs_url="/docs" if not _settings.is_production else None,
        redoc_url="/redoc" if not _settings.is_production else None,
        lifespan=lifespan,
    )

    # ── Middleware ────────────────────────────────────────────────────────────
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Rate limiting ─────────────────────────────────────────────────────────
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # ── Global error handler ──────────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        _log.exception("app.unhandled_exception", path=str(request.url), error=str(exc))
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

    # ── Routes ────────────────────────────────────────────────────────────────
    app.include_router(v1_router)

    @app.get("/", include_in_schema=False)
    async def root():
        return {"status": "StandupSync API running", "version": "1.0.0", "docs": "/docs"}

    @app.get("/health", tags=["health"])
    async def health():
        db_ok = await check_db_health()
        status = "ok" if db_ok else "degraded"
        return {
            "status": status,
            "checks": {"database": "ok" if db_ok else "error"},
            "environment": _settings.environment,
        }

    return app


app = create_app()
