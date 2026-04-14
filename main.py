import asyncio
import logging
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.config import get_settings
from app.database import engine
from app.redis_client import close_redis, get_redis
from app.routers import admin, agents, analytics, auth, dashboard, events, notifications, tickets, ws
from app.routers import inbound_email, categories, sla, groups
from app.services.email_poller import email_poller
from app.services.sla_service import sla_breach_detector

# Import all models so Base.metadata knows about all tables
import app.models  # noqa: F401

settings = get_settings()
logger = logging.getLogger("uvicorn.error")


def _log(msg: str) -> None:
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"))


async def _auto_migrate() -> None:
    def _run_sync() -> None:
        from alembic.config import Config as AlembicConfig
        from alembic import command as alembic_command

        cfg = AlembicConfig("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

        try:
            alembic_command.upgrade(cfg, "head")
        except Exception as first_err:
            err_lower = str(first_err).lower()
            if "already exists" in err_lower or "duplicate" in err_lower:
                _log("  Detected untracked schema — stamping to baseline (006)…")
                alembic_command.stamp(cfg, "006")
                alembic_command.upgrade(cfg, "head")
            else:
                raise

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_sync)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ────────────────────────────────────────────────────────

    # 0. Auto-migrate
    try:
        _log("  Running database migrations...")
        await _auto_migrate()
        _log("[OK] Database migrations up to date")
    except Exception as e:
        _log(f"  [WARN] Migration failed (will attempt to continue): {e}")

    # 1. Redis (optional — caching removed, Redis not required)
    try:
        redis = await get_redis()
        await redis.ping()
        _log("[OK] Redis connected")
    except Exception as e:
        _log(f"  [INFO] Redis unavailable (not required): {e}")

    # 2. Seed default SLA config if the table is empty
    try:
        from app.database import AsyncSessionLocal
        from app.models.admin import SLAConfig
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(SLAConfig))
            if not result.scalar_one_or_none():
                db.add(SLAConfig(critical_hours=1, high_hours=4, medium_hours=8, low_hours=24))
                await db.commit()
                _log("[OK] Default SLA config seeded")
    except Exception as e:
        _log(f"  [WARN] SLA config seeding failed: {e}")

    # 3. Start email poller only if inbound email is enabled in DB
    try:
        from app.database import AsyncSessionLocal
        from app.models.inbound_email import InboundEmailConfig
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(InboundEmailConfig))
            cfg = result.scalar_one_or_none()
            if cfg and cfg.enabled:
                email_poller.start()
                _log("[OK] Email poller started")
            else:
                _log("  Email poller disabled (configure via Admin -> Email -> Inbound)")
    except Exception as e:
        _log(f"  [WARN] Email poller could not start: {e}")

    # 4. Start SLA breach detector (runs every 60 s)
    try:
        sla_breach_detector.start()
        _log("[OK] SLA breach detector started")
    except Exception as e:
        _log(f"  [WARN] SLA breach detector could not start: {e}")

    yield

    # ── Shutdown ───────────────────────────────────────────────────────
    email_poller.stop()
    sla_breach_detector.stop()
    await close_redis()
    await engine.dispose()
    _log("[OK] Shutdown complete")


app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Log every unhandled exception with a full traceback so 500s are visible."""
    tb = traceback.format_exc()
    logger.error(f"Unhandled exception on {request.method} {request.url}\n{tb}")
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {type(exc).__name__}: {exc}"},
    )


# Routers
app.include_router(auth.router)
app.include_router(agents.router)
app.include_router(tickets.router)
app.include_router(notifications.router)
app.include_router(admin.router)
app.include_router(analytics.router)
app.include_router(dashboard.router)
app.include_router(inbound_email.router)
app.include_router(categories.router)
app.include_router(sla.router)
app.include_router(groups.router)
app.include_router(events.router)
app.include_router(ws.router)


@app.get("/health", tags=["health"])
async def health():
    redis_status = "unavailable"
    try:
        redis = await get_redis()
        ok = await redis.ping()
        redis_status = "ok" if ok else "unavailable"
    except Exception:
        redis_status = "unavailable"

    return {
        "status": "ok",
        "redis": redis_status,
        "email_poller": "running" if (
            email_poller._task and not email_poller._task.done()
        ) else "stopped",
        "sla_breach_detector": "running" if (
            sla_breach_detector._task and not sla_breach_detector._task.done()
        ) else "stopped",
    }
