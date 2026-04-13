import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.config import get_settings
from app.database import Base, engine
from app.redis_client import close_redis, get_redis
from app.routers import admin, agents, analytics, auth, dashboard, events, notifications, tickets, ws
from app.routers import inbound_email, categories, sla
from app.services.email_poller import email_poller
from app.services.sla_service import sla_breach_detector

# Import all models so Base.metadata knows about all tables
import app.models  # noqa: F401

settings = get_settings()

# Safe print that never crashes on Windows cp1252 consoles
def _log(msg: str) -> None:
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"))


async def _auto_migrate() -> None:
    """
    Run 'alembic upgrade head' on every app startup.

    Why: Azure App Service restarts after each deployment. By running migrations
    here, we guarantee the DB schema is always up-to-date the moment the new
    code starts serving traffic — no manual steps, no CI secrets required.

    Self-healing: If the production DB was bootstrapped with create_tables.py
    (no alembic tracking), the first upgrade attempt fails with
    DuplicateObjectError.  We detect that, stamp the DB to revision 006
    (the baseline schema that already exists), then retry — so only the
    missing revisions 007-009 are applied.

    Implementation detail: alembic's env.py calls asyncio.run() internally.
    That call creates a *new* event loop inside the thread-pool thread, so it
    never conflicts with FastAPI's running event loop.
    """
    def _run_sync() -> None:
        from alembic.config import Config as AlembicConfig
        from alembic import command as alembic_command

        cfg = AlembicConfig("alembic.ini")
        # Override the hard-coded localhost URL in alembic.ini
        cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

        try:
            alembic_command.upgrade(cfg, "head")
        except Exception as first_err:
            err_lower = str(first_err).lower()
            if "already exists" in err_lower or "duplicate" in err_lower:
                # DB schema was created outside Alembic (e.g. create_tables.py).
                # Stamp to revision 006 — the baseline that already exists —
                # then upgrade so only the missing revisions (007-009) run.
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
    # 0. Auto-migrate: run 'alembic upgrade head' before anything else.
    #    This is idempotent — alembic skips revisions already applied.
    # try:
    #     _log("  Running database migrations…")
    #     await _auto_migrate()
    #     _log("[OK] Database migrations up to date")
    # except Exception as e:
    #     # Log but don't crash — a failed migration is better diagnosed
    #     # from the alembic output than a silent server crash.
    #     _log(f"  [WARN] Migration failed (will attempt to continue): {e}")

    # # 1. Redis
    # redis = await get_redis()
    # await redis.ping()
    # _log("[OK] Redis connected")

    # # 2. Seed built-in categories if the table is empty
    # try:
    #     from app.database import AsyncSessionLocal
    #     from app.models.category import Category
    #     async with AsyncSessionLocal() as db:
    #         existing = await db.execute(select(Category))
    #         if not existing.scalars().first():
    #             _BUILTIN_CATS = [
    #                 ("hardware", "Hardware", "#8B5CF6", "Physical equipment issues",       1),
    #                 ("software", "Software", "#3B82F6", "Application and OS issues",       2),
    #                 ("network",  "Network",  "#10B981", "Connectivity and network issues", 3),
    #                 ("access",   "Access",   "#F59E0B", "Permissions and login issues",    4),
    #                 ("email",    "Email",    "#EF4444", "Email and messaging issues",       5),
    #                 ("security", "Security", "#EC4899", "Security incidents and threats",  6),
    #                 ("other",    "Other",    "#6B7280", "Uncategorised requests",          7),
    #             ]
    #             for slug, name, color, desc, order in _BUILTIN_CATS:
    #                 db.add(Category(slug=slug, name=name, color=color,
    #                                 description=desc, is_builtin=True, sort_order=order))
    #             await db.commit()
    #             _log("[OK] Built-in categories seeded")
    # except Exception as e:
    #     _log(f"  Category seeding failed: {e}")

    # # 3. Seed default SLA config if the table is empty
    # try:
    #     from app.database import AsyncSessionLocal
    #     from app.models.admin import SLAConfig
    #     async with AsyncSessionLocal() as db:
    #         result = await db.execute(select(SLAConfig))
    #         if not result.scalar_one_or_none():
    #             db.add(SLAConfig(critical_hours=1, high_hours=4, medium_hours=8, low_hours=24))
    #             await db.commit()
    #             _log("[OK] Default SLA config seeded")
    # except Exception as e:
    #     _log(f"  SLA config seeding failed: {e}")

    # # 4. Start email poller only if inbound email is enabled in DB
    # try:
    #     from app.database import AsyncSessionLocal
    #     from app.models.inbound_email import InboundEmailConfig
    #     async with AsyncSessionLocal() as db:
    #         result = await db.execute(select(InboundEmailConfig))
    #         cfg = result.scalar_one_or_none()
    #         if cfg and cfg.enabled:
    #             email_poller.start()
    #             _log("[OK] Email poller started")
    #         else:
    #             _log("  Email poller is disabled (configure via Admin -> Email -> Inbound)")
    # except Exception as e:
    #     _log(f"  Email poller could not start: {e}")

    # # 5. Start SLA breach detector (runs every 60 s)
    # try:
    #     sla_breach_detector.start()
    #     _log("[OK] SLA breach detector started")
    # except Exception as e:
    #     _log(f"  SLA breach detector could not start: {e}")

    # yield

    # # ── Shutdown ───────────────────────────────────────────────────────
    # email_poller.stop()
    # sla_breach_detector.stop()
    # await close_redis()
    # _log("[OK] Shutdown complete")
    ...

app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
app.include_router(events.router)
app.include_router(ws.router)


@app.get("/health", tags=["health"])
async def health():
    redis = await get_redis()
    redis_ok = await redis.ping()
    return {
        "status": "ok",
        "redis": redis_ok,
        "email_poller": "running" if (
            email_poller._task and not email_poller._task.done()
        ) else "stopped",
        "sla_breach_detector": "running" if (
            sla_breach_detector._task and not sla_breach_detector._task.done()
        ) else "stopped",
    }
