import secrets
import urllib.parse
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_admin
from app.database import get_db
from app.models.admin import EmailConfig, OAuthProvider, SLAConfig
from app.models.ticket import Ticket, TicketPriority, TicketStatus
from app.models.user import User
from app.redis_client import get_redis
from app.schemas.admin import (
    AdminStats,
    EmailConfigOut,
    EmailConfigUpdate,
    OAuthAuthorizeUrl,
    OAuthCallbackRequest,
    SLAConfigOut,
    SLAConfigUpdate,
)
from app.services import cache_service

# Provider OAuth endpoint presets
_OAUTH_PRESETS = {
    OAuthProvider.google: {
        "auth_endpoint":  "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
    },
    OAuthProvider.microsoft: {
        "auth_endpoint":  "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
    },
}

router = APIRouter(prefix="/admin", tags=["admin"])


# ── SLA ────────────────────────────────────────────────────────────────────

@router.get("/sla", response_model=SLAConfigOut)
async def get_sla(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(SLAConfig))
    sla = result.scalar_one_or_none()
    if not sla:
        raise HTTPException(status_code=404, detail="SLA config not found")
    return SLAConfigOut.model_validate(sla)


@router.put("/sla", response_model=SLAConfigOut)
async def update_sla(
    body: SLAConfigUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(SLAConfig))
    sla = result.scalar_one_or_none()
    if not sla:
        sla = SLAConfig()
        db.add(sla)

    sla.critical_hours = body.critical_hours
    sla.high_hours = body.high_hours
    sla.medium_hours = body.medium_hours
    sla.low_hours = body.low_hours

    await db.flush()
    await db.refresh(sla)
    return SLAConfigOut.model_validate(sla)


# ── Email Config ───────────────────────────────────────────────────────────

@router.get("/email", response_model=EmailConfigOut)
async def get_email(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(EmailConfig))
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="Email config not found")
    return EmailConfigOut.model_validate(cfg)


@router.put("/email", response_model=EmailConfigOut)
async def update_email(
    body: EmailConfigUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(EmailConfig))
    cfg = result.scalar_one_or_none()
    if not cfg:
        cfg = EmailConfig()
        db.add(cfg)

    cfg.type = body.type
    if body.triggers:
        cfg.trigger_new = body.triggers.trigger_new
        cfg.trigger_assign = body.triggers.trigger_assign
        cfg.trigger_resolve = body.triggers.trigger_resolve

    if body.smtp and body.type.value == "smtp":
        cfg.smtp_host = body.smtp.host
        cfg.smtp_port = body.smtp.port
        cfg.smtp_security = body.smtp.security
        cfg.smtp_from = body.smtp.from_address
        cfg.smtp_user = body.smtp.user
        if body.smtp.password:
            cfg.smtp_pass = body.smtp.password

    if body.m365 and body.type.value == "m365":
        cfg.m365_tenant_id = body.m365.tenant_id
        cfg.m365_client_id = body.m365.client_id
        cfg.m365_from = body.m365.from_address
        if body.m365.client_secret:
            cfg.m365_client_secret = body.m365.client_secret

    if body.oauth and body.type.value == "oauth":
        o = body.oauth
        cfg.oauth_provider = o.provider
        cfg.oauth_client_id = o.client_id
        cfg.oauth_redirect_uri = o.redirect_uri
        cfg.oauth_scopes = o.scopes
        cfg.oauth_from = o.from_address
        if o.client_secret:
            cfg.oauth_client_secret = o.client_secret
        # Use preset endpoints if not custom
        preset = _OAUTH_PRESETS.get(o.provider, {})
        cfg.oauth_auth_endpoint  = o.auth_endpoint  or preset.get("auth_endpoint", "")
        cfg.oauth_token_endpoint = o.token_endpoint or preset.get("token_endpoint", "")

    await db.flush()
    await db.refresh(cfg)
    return EmailConfigOut.model_validate(cfg)


# ── OAuth 2.0 Flow ─────────────────────────────────────────────────────────

@router.get("/email/oauth/authorize", response_model=OAuthAuthorizeUrl)
async def oauth_get_authorize_url(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Build the OAuth authorization URL to redirect the user to."""
    result = await db.execute(select(EmailConfig))
    cfg: EmailConfig | None = result.scalar_one_or_none()
    if not cfg or not cfg.oauth_client_id or not cfg.oauth_auth_endpoint:
        raise HTTPException(status_code=400, detail="OAuth not configured — save credentials first")

    state = secrets.token_urlsafe(16)
    params = {
        "client_id":     cfg.oauth_client_id,
        "redirect_uri":  cfg.oauth_redirect_uri or "",
        "response_type": "code",
        "scope":         cfg.oauth_scopes or "",
        "access_type":   "offline",  # Google: request refresh token
        "prompt":        "consent",
        "state":         state,
    }
    url = cfg.oauth_auth_endpoint + "?" + urllib.parse.urlencode(params)
    return OAuthAuthorizeUrl(url=url, state=state)


@router.post("/email/oauth/callback", response_model=EmailConfigOut)
async def oauth_callback(
    body: OAuthCallbackRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Exchange authorization code for access + refresh tokens."""
    result = await db.execute(select(EmailConfig))
    cfg: EmailConfig | None = result.scalar_one_or_none()
    if not cfg or not cfg.oauth_client_id or not cfg.oauth_token_endpoint:
        raise HTTPException(status_code=400, detail="OAuth not configured")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            cfg.oauth_token_endpoint,
            data={
                "code":          body.code,
                "client_id":     cfg.oauth_client_id,
                "client_secret": cfg.oauth_client_secret or "",
                "redirect_uri":  cfg.oauth_redirect_uri or "",
                "grant_type":    "authorization_code",
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {resp.text}")

    token_data = resp.json()
    expires_in = token_data.get("expires_in", 3600)
    cfg.oauth_access_token  = token_data.get("access_token")
    cfg.oauth_refresh_token = token_data.get("refresh_token")
    cfg.oauth_token_expiry  = datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() + expires_in, tz=timezone.utc
    )
    await db.flush()
    await db.refresh(cfg)
    return EmailConfigOut.model_validate(cfg)


@router.delete("/email/oauth/revoke", response_model=EmailConfigOut)
async def oauth_revoke(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Revoke stored OAuth tokens."""
    result = await db.execute(select(EmailConfig))
    cfg: EmailConfig | None = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="Email config not found")

    cfg.oauth_access_token  = None
    cfg.oauth_refresh_token = None
    cfg.oauth_token_expiry  = None
    await db.flush()
    await db.refresh(cfg)
    return EmailConfigOut.model_validate(cfg)


# ── Stats / Overview ───────────────────────────────────────────────────────

@router.get("/stats", response_model=AdminStats)
async def get_stats(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    redis = await get_redis()
    cache_key = cache_service.stats_key()
    cached = await cache_service.cache_get(redis, cache_key)
    if cached:
        return cached

    # Count by status
    status_counts: dict[str, int] = {}
    for s in TicketStatus:
        res = await db.execute(
            select(func.count()).select_from(Ticket).where(Ticket.status == s)
        )
        status_counts[s.value] = res.scalar_one()

    critical_res = await db.execute(
        select(func.count()).select_from(Ticket).where(
            Ticket.priority == TicketPriority.critical,
            Ticket.status.notin_([TicketStatus.resolved, TicketStatus.closed]),
        )
    )
    critical = critical_res.scalar_one()

    unassigned_res = await db.execute(
        select(func.count()).select_from(Ticket).where(
            Ticket.assignee_id.is_(None),
            Ticket.status.notin_([TicketStatus.resolved, TicketStatus.closed]),
        )
    )
    unassigned = unassigned_res.scalar_one()

    # Agent workload
    agents_res = await db.execute(select(User).where(User.is_active == True))
    agents = agents_res.scalars().all()
    agent_workload = []
    for agent in agents:
        open_res = await db.execute(
            select(func.count()).select_from(Ticket).where(
                Ticket.assignee_id == agent.id,
                Ticket.status.notin_([TicketStatus.resolved, TicketStatus.closed]),
            )
        )
        agent_workload.append({
            "id": str(agent.id),
            "name": agent.name,
            "initials": agent.initials,
            "group": agent.group,
            "open_tickets": open_res.scalar_one(),
        })

    total = sum(status_counts.values())
    stats = AdminStats(
        total_tickets=total,
        open_tickets=status_counts.get("open", 0),
        in_progress_tickets=status_counts.get("in-progress", 0),
        resolved_tickets=status_counts.get("resolved", 0),
        closed_tickets=status_counts.get("closed", 0),
        critical_tickets=critical,
        unassigned_tickets=unassigned,
        agent_workload=agent_workload,
    )

    await cache_service.cache_set(redis, cache_key, stats.model_dump(), cache_service.STATS_TTL)
    return stats
