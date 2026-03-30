import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.config import get_settings
from app.database import Base, engine
from app.redis_client import close_redis, get_redis
from app.routers import admin, agents, analytics, auth, events, notifications, tickets, ws
from app.routers import inbound_email, categories
from app.services.email_poller import email_poller

# Import all models so Base.metadata knows about all tables
import app.models  # noqa: F401

settings = get_settings()

# Safe print that never crashes on Windows cp1252 consoles
def _log(msg: str) -> None:
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ────────────────────────────────────────────────────────
    # 1. Redis
    redis = await get_redis()
    await redis.ping()
    _log("[OK] Redis connected")

    # 2. Seed built-in categories if the table is empty
    try:
        from app.database import AsyncSessionLocal
        from app.models.category import Category
        async with AsyncSessionLocal() as db:
            existing = await db.execute(select(Category))
            if not existing.scalars().first():
                _BUILTIN_CATS = [
                    ("hardware", "Hardware", "#8B5CF6", "Physical equipment issues",       1),
                    ("software", "Software", "#3B82F6", "Application and OS issues",       2),
                    ("network",  "Network",  "#10B981", "Connectivity and network issues", 3),
                    ("access",   "Access",   "#F59E0B", "Permissions and login issues",    4),
                    ("email",    "Email",    "#EF4444", "Email and messaging issues",       5),
                    ("security", "Security", "#EC4899", "Security incidents and threats",  6),
                    ("other",    "Other",    "#6B7280", "Uncategorised requests",          7),
                ]
                for slug, name, color, desc, order in _BUILTIN_CATS:
                    db.add(Category(slug=slug, name=name, color=color,
                                    description=desc, is_builtin=True, sort_order=order))
                await db.commit()
                _log("[OK] Built-in categories seeded")
    except Exception as e:
        _log(f"  Category seeding failed: {e}")

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
                _log("  Email poller is disabled (configure via Admin -> Email -> Inbound)")
    except Exception as e:
        _log(f"  Email poller could not start: {e}")

    yield

    # ── Shutdown ───────────────────────────────────────────────────────
    email_poller.stop()
    await close_redis()
    _log("[OK] Shutdown complete")


app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
    lifespan=lifespan,
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
app.include_router(inbound_email.router)
app.include_router(categories.router)
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
    }
