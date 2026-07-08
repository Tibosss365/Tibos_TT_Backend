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
from app.routers import email_inbox
from app.routers import csat, admin_features, activity
from app.routers.sso import auth_router as sso_auth_router, admin_router as sso_admin_router
from app.services.email_poller import email_poller
from app.services.sla_service import sla_breach_detector
from app.services.report_scheduler import report_scheduler
from app.services.condition_alert_service import condition_alert_service
from app.services.audit_cleanup import audit_cleanup
from app.services.trash_cleanup import trash_cleanup
from app.services.escalation_service import escalation_service
from app.services.recurring_ticket_service import recurring_ticket_service

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

    # 0. Auto-migrate — retry (DB may not be reachable in the first seconds),
    # then refuse to boot: running with a stale schema turns every affected
    # request into a 500 (e.g. the 2026-06-10 login outage on missing column).
    MIGRATE_ATTEMPTS = 3
    for attempt in range(1, MIGRATE_ATTEMPTS + 1):
        try:
            _log(f"  Running database migrations (attempt {attempt}/{MIGRATE_ATTEMPTS})...")
            await _auto_migrate()
            _log("[OK] Database migrations up to date")
            break
        except Exception as e:
            _log(f"  [ERROR] Migration attempt {attempt} failed: {e}")
            if attempt == MIGRATE_ATTEMPTS:
                _log("  [FATAL] Could not migrate database — refusing to start with a stale schema.")
                raise
            await asyncio.sleep(5 * attempt)

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

    # 2b. Seed default groups if the table is empty
    try:
        from app.database import AsyncSessionLocal
        from app.models.group import Group
        DEFAULT_GROUPS = [
            {"id": "microsoft-365",         "name": "Microsoft 365",           "description": "Exchange Online, Teams, SharePoint, Intune and all M365 workloads", "color": "#0078D4"},
            {"id": "migration-services",    "name": "Migration Services",      "description": "Mailbox, tenant-to-tenant and file-share migration projects",        "color": "#7C3AED"},
            {"id": "security-compliance",   "name": "Security & Compliance",   "description": "Defender, Conditional Access, DLP, Purview and threat response",     "color": "#DC2626"},
            {"id": "infrastructure-network","name": "Infrastructure & Network","description": "Active Directory, networking, servers and virtualisation",            "color": "#059669"},
            {"id": "end-user-support",      "name": "End User Support L1",     "description": "First-line support for accounts, hardware, software and email",      "color": "#D97706"},
            {"id": "azure-cloud",           "name": "Azure & Cloud",           "description": "Azure infrastructure, Entra ID, backup and cloud cost management",   "color": "#2563EB"},
        ]
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Group))
            existing_ids = {g.id for g in result.scalars().all()}
            seeded = 0
            for g in DEFAULT_GROUPS:
                if g["id"] not in existing_ids:
                    db.add(Group(id=g["id"], name=g["name"], description=g["description"], color=g["color"], is_builtin=False))
                    seeded += 1
            if seeded:
                await db.commit()
                _log(f"[OK] Seeded {seeded} default group(s)")
    except Exception as e:
        _log(f"  [WARN] Group seeding failed: {e}")

    # 2c. Seed default ticket config items (hold reasons / resolution codes /
    #     canned responses) — shared, backend-stored. Seeds per-kind only when
    #     that kind has no rows yet, so admin edits are never overwritten.
    try:
        from app.database import AsyncSessionLocal
        from app.models.feature_models import TicketConfigItem
        _CR_BODY = {
            "Password Reset Instructions": 'Hi {contact_name},\n\nTo reset your password:\n1. Go to the login page\n2. Click "Forgot Password"\n3. Enter your email address\n4. Check your email for the reset link\n\nBest regards,\n{agent_name}',
            "Ticket Acknowledged": 'Hi {contact_name},\n\nThank you for contacting us. We have received your request (#{ticket_id}) and our team is reviewing it. We will keep you updated on progress.\n\nBest regards,\n{agent_name}',
            "Request for More Information": 'Hi {contact_name},\n\nThank you for reaching out. To assist you better, could you please provide the following additional information:\n\n- \n- \n\nBest regards,\n{agent_name}',
        }
        DEFAULT_CONFIG_ITEMS = (
            [("hold_reason", lbl, None) for lbl in [
                "Waiting for Customer Response", "Waiting for Third Party / Vendor",
                "Waiting for Parts / Hardware", "Scheduled Maintenance Window",
                "Pending Internal Approval", "Customer Requested Delay",
            ]]
            + [("resolution_code", lbl, None) for lbl in [
                "Fixed — Software Issue", "Fixed — Hardware Issue",
                "Fixed — Configuration Change", "Fixed — Network / Connectivity",
                "Workaround Provided", "User Training / Guidance",
                "Third Party / Vendor Action", "No Issue Found", "Duplicate Ticket",
            ]]
            + [("canned_response", lbl, _CR_BODY[lbl]) for lbl in _CR_BODY]
        )
        async with AsyncSessionLocal() as db:
            present = await db.execute(select(TicketConfigItem.kind).distinct())
            kinds_present = {row[0] for row in present.all()}
            seeded = 0
            for i, (kind, label, body) in enumerate(DEFAULT_CONFIG_ITEMS):
                if kind in kinds_present:
                    continue  # this kind already has items — don't overwrite admin edits
                db.add(TicketConfigItem(kind=kind, label=label, body=body, sort_order=i))
                seeded += 1
            if seeded:
                await db.commit()
                _log(f"[OK] Seeded {seeded} default ticket config item(s)")
    except Exception as e:
        _log(f"  [WARN] Ticket config seeding failed: {e}")

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

    # 5. Start scheduled report sender (checks every 60 s)
    try:
        report_scheduler.start()
        _log("[OK] Report scheduler started")
    except Exception as e:
        _log(f"  [WARN] Report scheduler could not start: {e}")

    # 5b. Start condition alert service (checks alert conditions every 60 s)
    try:
        condition_alert_service.start()
        _log("[OK] Condition alert service started")
    except Exception as e:
        _log(f"  [WARN] Condition alert service could not start: {e}")

    # 6. Start audit log cleanup (runs every 24 h, retains last 30 days)
    try:
        audit_cleanup.start()
        _log("[OK] Audit cleanup started (30-day retention)")
    except Exception as e:
        _log(f"  [WARN] Audit cleanup could not start: {e}")

    # 7. Start trash cleanup (runs every 24 h, permanently removes tickets soft-deleted > 30 days)
    try:
        trash_cleanup.start()
        _log("[OK] Trash cleanup started (30-day soft-delete retention)")
    except Exception as e:
        _log(f"  [WARN] Trash cleanup could not start: {e}")

    # 8. Start escalation service (runs every 1 h)
    try:
        escalation_service.start()
        _log("[OK] Escalation service started")
    except Exception as e:
        _log(f"  [WARN] Escalation service could not start: {e}")

    # 9. Start recurring ticket service (runs every 60 s, respects cron schedules)
    try:
        recurring_ticket_service.start()
        _log("[OK] Recurring ticket service started")
    except Exception as e:
        _log(f"  [WARN] Recurring ticket service could not start: {e}")

    yield

    # ── Shutdown ───────────────────────────────────────────────────────
    email_poller.stop()
    sla_breach_detector.stop()
    report_scheduler.stop()
    condition_alert_service.stop()
    audit_cleanup.stop()
    trash_cleanup.stop()
    escalation_service.stop()
    recurring_ticket_service.stop()
    await close_redis()
    await engine.dispose()
    _log("[OK] Shutdown complete")


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


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Log every unhandled exception with a full traceback so 500s are visible."""
    tb = traceback.format_exc()
    logger.error(f"Unhandled exception on {request.method} {request.url}\n{tb}")
    # This handler runs outside CORSMiddleware, so CORS headers must be added
    # manually — otherwise browsers hide the 500 behind "Failed to fetch".
    origin = request.headers.get("origin")
    cors_headers = (
        {"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Credentials": "true"}
        if origin else {}
    )
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {type(exc).__name__}: {exc}"},
        headers=cors_headers,
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
app.include_router(email_inbox.router)
app.include_router(categories.router)
app.include_router(sla.router)
app.include_router(groups.router)
app.include_router(events.router)
app.include_router(ws.router)
app.include_router(sso_auth_router)
app.include_router(sso_admin_router)
app.include_router(csat.router)
app.include_router(admin_features.router)
app.include_router(activity.router)


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
        "report_scheduler": "running" if report_scheduler.is_running else "stopped",
        "condition_alert_service": "running" if condition_alert_service.is_running else "stopped",
        "escalation_service": "running" if (
            escalation_service._task and not escalation_service._task.done()
        ) else "stopped",
        "recurring_ticket_service": "running" if (
            recurring_ticket_service._task and not recurring_ticket_service._task.done()
        ) else "stopped",
    }
