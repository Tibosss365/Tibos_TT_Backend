"""
SSO / OIDC Router
=================
Handles two concerns in one file:

  Auth flow  (no authentication required)
  ─────────────────────────────────────────
  GET  /auth/sso/public    → public config the login page needs
  GET  /auth/sso/login     → build Azure AD auth URL and redirect
  GET  /auth/sso/callback  → exchange code → validate ID token → issue app JWT

  Admin config  (admin JWT required)
  ─────────────────────────────────────────
  GET  /admin/sso          → full SSO config (secret masked)
  PUT  /admin/sso          → save SSO config
  POST /admin/sso/test     → verify credentials reach Azure AD discovery endpoint

OIDC provider supported
  • Microsoft Entra ID (Azure AD) — provider="microsoft"
  • Generic OIDC — provider="custom" (supply all endpoints manually)
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.deps import get_current_user, require_admin
from app.core.security import create_access_token, hash_password
from app.database import get_db
from app.models.sso import SSOConfig
from app.models.user import User, UserRole
from app.schemas.sso import SSOConfigOut, SSOConfigUpdate, SSOPublicConfig

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Two routers so FastAPI groups them properly in /docs ───────────────────────
auth_router  = APIRouter(prefix="/auth/sso",  tags=["SSO Auth"])
admin_router = APIRouter(prefix="/admin/sso", tags=["SSO Admin"])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _microsoft_endpoints(tenant_id: str) -> dict:
    base = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0"
    return {
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint":         f"{base}/token",
        "jwks_uri":               f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys",
        "issuer":                 f"https://login.microsoftonline.com/{tenant_id}/v2.0",
    }


def _make_state(nonce: str) -> str:
    """HMAC-signed state = '{nonce}.{sig}' — no server-side storage needed."""
    sig = hmac.new(settings.SECRET_KEY.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    return f"{nonce}.{sig}"


def _verify_state(state: str) -> str | None:
    """Return the nonce if valid, else None."""
    try:
        nonce, sig = state.rsplit(".", 1)
        expected = hmac.new(settings.SECRET_KEY.encode(), nonce.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(sig, expected):
            return nonce
    except Exception:
        pass
    return None


async def _get_or_create_sso_config(db: AsyncSession) -> SSOConfig:
    result = await db.execute(select(SSOConfig))
    cfg = result.scalar_one_or_none()
    if not cfg:
        cfg = SSOConfig(id=1)
        db.add(cfg)
        await db.flush()
    return cfg


async def _fetch_jwks(jwks_uri: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(jwks_uri)
        r.raise_for_status()
        return r.json()


def _validate_id_token(token: str, jwks: dict, audience: str, issuer: str, nonce: str) -> dict:
    """Validate RS256 ID token against JWKS and return claims."""
    try:
        claims = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
        )
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"ID token validation failed: {e}")

    if claims.get("nonce") != nonce:
        raise HTTPException(status_code=401, detail="Nonce mismatch — possible replay attack")

    return claims


def _extract_user_claims(claims: dict) -> dict:
    """Map OIDC claims to our user fields."""
    email = (
        claims.get("email")
        or claims.get("preferred_username")
        or claims.get("upn")
        or ""
    ).lower().strip()
    name  = claims.get("name") or email.split("@")[0] or "SSO User"
    oid   = claims.get("oid") or claims.get("sub") or ""
    groups: list[str] = claims.get("groups") or []
    return {"email": email, "name": name, "external_id": oid, "groups": groups}


def _resolve_role(groups: list[str], sso_cfg: SSOConfig) -> UserRole:
    """Map Azure group membership to an app role."""
    admin_ids = set(sso_cfg.admin_group_ids or [])
    agent_ids = set(sso_cfg.agent_group_ids or [])
    for gid in groups:
        if gid in admin_ids:
            return UserRole.admin
        if gid in agent_ids:
            return UserRole.technician
    return UserRole(sso_cfg.default_role)


# ══════════════════════════════════════════════════════════════════════════════
# Auth flow
# ══════════════════════════════════════════════════════════════════════════════

@auth_router.get("/public", response_model=SSOPublicConfig, summary="Public SSO info for login page")
async def sso_public(db: AsyncSession = Depends(get_db)):
    """Returns whether SSO is enabled — called by the login page, no auth needed."""
    cfg = await _get_or_create_sso_config(db)
    label_map = {"microsoft": "Sign in with Microsoft", "custom": "Sign in with SSO"}
    return SSOPublicConfig(
        enabled=cfg.enabled,
        provider=cfg.provider,
        label=label_map.get(cfg.provider, "Sign in with SSO"),
    )


@auth_router.get("/login", summary="Initiate OIDC login — redirects to identity provider")
async def sso_login(db: AsyncSession = Depends(get_db)):
    cfg = await _get_or_create_sso_config(db)
    if not cfg.enabled:
        raise HTTPException(status_code=400, detail="SSO is not enabled")
    if not cfg.client_id or not cfg.redirect_uri:
        raise HTTPException(status_code=400, detail="SSO is not fully configured")

    eps = (
        _microsoft_endpoints(cfg.tenant_id)
        if cfg.provider == "microsoft"
        else {
            "authorization_endpoint": cfg.authorization_endpoint,
            "token_endpoint":         cfg.token_endpoint,
            "jwks_uri":               cfg.jwks_uri,
            "issuer":                 cfg.issuer,
        }
    )

    nonce = secrets.token_urlsafe(16)
    state = _make_state(nonce)

    params = {
        "client_id":     cfg.client_id,
        "response_type": "code",
        "redirect_uri":  cfg.redirect_uri,
        "response_mode": "query",
        "scope":         "openid profile email",
        "state":         state,
        "nonce":         nonce,
    }
    auth_url = eps["authorization_endpoint"] + "?" + urlencode(params)
    return RedirectResponse(auth_url)


@auth_router.get("/callback", summary="OIDC callback — exchange code, issue app JWT")
async def sso_callback(
    code:  str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    # ── 1. Handle provider-side errors ────────────────────────────────────────
    if error:
        logger.warning("SSO provider error: %s — %s", error, error_description)
        return RedirectResponse(f"{settings.FRONTEND_URL}/login?sso_error={error}")

    if not code or not state:
        return RedirectResponse(f"{settings.FRONTEND_URL}/login?sso_error=missing_params")

    # ── 2. Validate CSRF state ────────────────────────────────────────────────
    nonce = _verify_state(state)
    if not nonce:
        logger.warning("SSO callback: invalid state parameter")
        return RedirectResponse(f"{settings.FRONTEND_URL}/login?sso_error=invalid_state")

    # ── 3. Load SSO config ────────────────────────────────────────────────────
    cfg = await _get_or_create_sso_config(db)
    if not cfg.enabled:
        return RedirectResponse(f"{settings.FRONTEND_URL}/login?sso_error=sso_disabled")

    eps = (
        _microsoft_endpoints(cfg.tenant_id)
        if cfg.provider == "microsoft"
        else {
            "authorization_endpoint": cfg.authorization_endpoint,
            "token_endpoint":         cfg.token_endpoint,
            "jwks_uri":               cfg.jwks_uri,
            "issuer":                 cfg.issuer,
        }
    )

    # ── 4. Exchange authorization code for tokens ─────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            token_resp = await client.post(
                eps["token_endpoint"],
                data={
                    "client_id":     cfg.client_id,
                    "client_secret": cfg.client_secret,
                    "code":          code,
                    "redirect_uri":  cfg.redirect_uri,
                    "grant_type":    "authorization_code",
                },
                headers={"Accept": "application/json"},
            )
            token_resp.raise_for_status()
            tokens = token_resp.json()
    except Exception as e:
        logger.error("SSO token exchange failed: %s", e)
        return RedirectResponse(f"{settings.FRONTEND_URL}/login?sso_error=token_exchange_failed")

    id_token = tokens.get("id_token")
    if not id_token:
        return RedirectResponse(f"{settings.FRONTEND_URL}/login?sso_error=no_id_token")

    # ── 5. Validate ID token signature + claims ───────────────────────────────
    try:
        jwks = await _fetch_jwks(eps["jwks_uri"])
        claims = _validate_id_token(id_token, jwks, cfg.client_id, eps["issuer"], nonce)
    except HTTPException as e:
        logger.warning("SSO ID token invalid: %s", e.detail)
        return RedirectResponse(f"{settings.FRONTEND_URL}/login?sso_error=invalid_id_token")
    except Exception as e:
        logger.error("SSO JWKS fetch failed: %s", e)
        return RedirectResponse(f"{settings.FRONTEND_URL}/login?sso_error=jwks_failed")

    # ── 6. Extract user identity ───────────────────────────────────────────────
    user_info = _extract_user_claims(claims)
    if not user_info["external_id"]:
        return RedirectResponse(f"{settings.FRONTEND_URL}/login?sso_error=missing_oid")

    # ── 7. JIT provision — create or update user ───────────────────────────────
    result = await db.execute(
        select(User).where(User.external_id == user_info["external_id"])
    )
    user: User | None = result.scalar_one_or_none()

    role = _resolve_role(user_info["groups"], cfg)

    if user is None:
        # Check if someone with same email exists as a local account
        if user_info["email"]:
            existing = await db.execute(
                select(User).where(User.username == user_info["email"])
            )
            user = existing.scalar_one_or_none()

        if user is None:
            if not cfg.auto_create_users:
                return RedirectResponse(
                    f"{settings.FRONTEND_URL}/login?sso_error=user_not_provisioned"
                )
            name = user_info["name"]
            initials = "".join(w[0].upper() for w in name.split()[:2]) or "?"
            user = User(
                name=name,
                initials=initials,
                username=user_info["email"] or user_info["external_id"],
                hashed_password=hash_password(str(uuid.uuid4())),  # unusable local password
                role=role,
                external_id=user_info["external_id"],
                auth_provider=cfg.provider,
                is_active=True,
            )
            db.add(user)
        else:
            # Link existing local account to SSO identity
            user.external_id  = user_info["external_id"]
            user.auth_provider = cfg.provider

    elif cfg.sync_on_login:
        # Refresh name/role from latest ID token claims
        user.name = user_info["name"]
        user.role = role

    if not user.is_active:
        return RedirectResponse(f"{settings.FRONTEND_URL}/login?sso_error=account_disabled")

    await db.commit()
    await db.refresh(user)

    # ── 8. Issue our application JWT ──────────────────────────────────────────
    app_token = create_access_token(str(user.id), extra={"role": user.role.value})

    # ── 9. Redirect to frontend with token in query param ─────────────────────
    # Frontend reads this once, stores in Zustand, then strips the URL
    user_json = json.dumps({
        "id":       str(user.id),
        "name":     user.name,
        "username": user.username,
        "role":     user.role.value,
        "initials": user.initials,
        "group":    user.group,
        "is_active": user.is_active,
    })
    user_b64 = base64.urlsafe_b64encode(user_json.encode()).decode()
    redirect_url = (
        f"{settings.FRONTEND_URL}/login"
        f"?sso_token={app_token}&sso_user={user_b64}"
    )
    return RedirectResponse(redirect_url)


# ══════════════════════════════════════════════════════════════════════════════
# Admin config
# ══════════════════════════════════════════════════════════════════════════════

@admin_router.get("", response_model=SSOConfigOut, summary="Get SSO configuration")
async def get_sso_config(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    cfg = await _get_or_create_sso_config(db)
    out = SSOConfigOut.model_validate(cfg)
    out.client_secret_set = bool(cfg.client_secret)
    return out


@admin_router.put("", response_model=SSOConfigOut, summary="Save SSO configuration")
async def update_sso_config(
    body: SSOConfigUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    cfg = await _get_or_create_sso_config(db)

    cfg.enabled    = body.enabled
    cfg.provider   = body.provider
    cfg.tenant_id  = body.tenant_id or None
    cfg.client_id  = body.client_id or None
    cfg.redirect_uri = body.redirect_uri or None

    # Only overwrite secret if a new value was provided
    if body.client_secret:
        cfg.client_secret = body.client_secret

    cfg.authorization_endpoint = body.authorization_endpoint or None
    cfg.token_endpoint = body.token_endpoint or None
    cfg.jwks_uri  = body.jwks_uri  or None
    cfg.issuer    = body.issuer    or None

    cfg.default_role       = body.default_role
    cfg.admin_group_ids    = body.admin_group_ids or None
    cfg.agent_group_ids    = body.agent_group_ids or None
    cfg.auto_create_users  = body.auto_create_users
    cfg.sync_on_login      = body.sync_on_login

    await db.commit()
    await db.refresh(cfg)

    out = SSOConfigOut.model_validate(cfg)
    out.client_secret_set = bool(cfg.client_secret)
    return out


@admin_router.post("/test", summary="Test SSO connectivity — verify Azure AD discovery endpoint")
async def test_sso_config(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    cfg = await _get_or_create_sso_config(db)
    if not cfg.tenant_id and cfg.provider == "microsoft":
        raise HTTPException(status_code=400, detail="Tenant ID is required")

    if cfg.provider == "microsoft":
        discovery_url = (
            f"https://login.microsoftonline.com/{cfg.tenant_id}"
            f"/v2.0/.well-known/openid-configuration"
        )
    elif cfg.jwks_uri:
        discovery_url = cfg.jwks_uri
    else:
        raise HTTPException(status_code=400, detail="No JWKS URI configured for custom provider")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(discovery_url)
            resp.raise_for_status()
            meta = resp.json()
        return {
            "status":   "ok",
            "message":  "Successfully reached identity provider",
            "issuer":   meta.get("issuer"),
            "jwks_uri": meta.get("jwks_uri"),
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach identity provider: {e}")
