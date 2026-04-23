import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
import structlog

from database import engine, Base
from rate_limiter import limiter
from scheduler import start_scheduler
from routers import auth, teams, checkin, invite, waitlist, blockers, billing, reports, automation, ai_task_radar

# ── Structured logging setup ────────────────────────────────────────────────
_ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
        if _ENVIRONMENT != "production"
        else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    logger_factory=structlog.PrintLoggerFactory(),
)

logging.basicConfig(level=logging.INFO)
_log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    start_scheduler()
    _log.info("app.started", environment=_ENVIRONMENT)
    yield
    from scheduler import scheduler
    if scheduler.running:
        scheduler.shutdown(wait=False)
    _log.info("app.stopped")


app = FastAPI(
    title="StandupSync API",
    description="Daily check-in tool for remote teams",
    version="1.0.0",
    lifespan=lifespan,
)

# ── Rate limiting ────────────────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS — restrict to FRONTEND_URL in production ────────────────────────────
_FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
_ALLOWED_ORIGINS = (
    ["*"] if _ENVIRONMENT != "production" else [_FRONTEND_URL]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    _log.exception("app.unhandled_exception", path=str(request.url), error=str(exc))
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


app.include_router(auth.router,          prefix="/auth",         tags=["auth"])
app.include_router(teams.router,         prefix="/teams",        tags=["teams"])
app.include_router(checkin.router,       prefix="/checkin",      tags=["checkin"])
app.include_router(invite.router,        prefix="/invite",       tags=["invite"])
app.include_router(waitlist.router,      prefix="/waitlist",     tags=["waitlist"])
app.include_router(blockers.router,      prefix="/blockers",     tags=["blockers"])
app.include_router(billing.router,       prefix="/billing",      tags=["billing"])
app.include_router(reports.router,       prefix="/reports",      tags=["reports"])
app.include_router(automation.router,    prefix="/automation",   tags=["automation"])
app.include_router(ai_task_radar.router, prefix="/ai-task-radar", tags=["ai-task-radar"])


@app.get("/")
async def root():
    return {"status": "StandupSync API running"}


@app.get("/health")
async def health():
    return {"status": "ok"}
