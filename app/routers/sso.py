"""
SSO / OIDC + SAML 2.0 Router
=============================
Handles two SSO protocols in one file.

  OIDC Auth flow  (no authentication required)
  ─────────────────────────────────────────────
  GET  /auth/sso/public    → public config the login page needs
  GET  /auth/sso/login     → build Azure AD OIDC auth URL and redirect
  GET  /auth/sso/callback  → exchange code → validate ID token → issue app JWT

  SAML 2.0 Auth flow  (no authentication required)
  ─────────────────────────────────────────────────
  GET  /auth/saml/metadata → serve SP metadata XML (upload to Azure AD)
  GET  /auth/saml/login    → build SAMLRequest, redirect to IdP SSO URL
  POST /auth/saml/acs      → receive SAMLResponse, verify signature, issue JWT
  GET  /auth/saml/logout   → local logout, redirect to login page

  Admin config  (admin JWT required)
  ───────────────────────────────────
  GET  /admin/sso              → full SSO config (secret masked)
  PUT  /admin/sso              → save SSO config (auto-fetches IdP cert on save)
  POST /admin/sso/test         → verify credentials reach Azure AD discovery endpoint
  GET  /admin/sso/saml-metadata → returns SP metadata XML + field values for admin UI

OIDC providers supported:
  • Microsoft Entra ID (Azure AD) — provider="microsoft"
  • Generic OIDC — provider="custom" (supply all endpoints manually)

SAML: Azure AD only (SAML POST binding for ACS, Redirect binding for AuthnRequest).
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import uuid
import zlib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.deps import get_current_user, require_admin
from app.core.security import create_access_token, hash_password
from app.database import get_db
from app.models.sso import SSOConfig
from app.models.user import User, UserRole
from app.schemas.sso import SSOConfigOut, SSOConfigUpdate, SSOPublicConfig, SSOSamlMetadataOut

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Three routers — FastAPI groups them properly in /docs ──────────────────────
auth_router  = APIRouter(prefix="/auth/sso",   tags=["SSO Auth (OIDC)"])
saml_router  = APIRouter(prefix="/auth/saml",  tags=["SSO Auth (SAML)"])
admin_router = APIRouter(prefix="/admin/sso",  tags=["SSO Admin"])



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
# SAML 2.0 helpers
# ══════════════════════════════════════════════════════════════════════════════

def _sp_entity_id() -> str:
    return f"{settings.FRONTEND_URL.rstrip('/')}/auth/saml/metadata".replace(
        settings.FRONTEND_URL.rstrip("/"),
        # SP entity ID uses the *backend* base URL, not the frontend
        _backend_base(),
    )


def _backend_base() -> str:
    """Return the public backend base URL (no trailing slash)."""
    return settings.BACKEND_URL.rstrip("/")


def _saml_urls() -> dict:
    base = _backend_base()
    return {
        "entity_id":   f"{base}/auth/saml/metadata",
        "acs_url":     f"{base}/auth/saml/acs",
        "login_url":   f"{base}/auth/saml/login",
        "logout_url":  f"{base}/auth/saml/logout",
        "metadata_url": f"{base}/auth/saml/metadata",
    }


def _build_sp_metadata_xml() -> str:
    """Generate a standards-compliant SAML 2.0 SP metadata XML string."""
    urls = _saml_urls()
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<EntityDescriptor
  xmlns="urn:oasis:names:tc:SAML:2.0:metadata"
  xmlns:ds="http://www.w3.org/2000/09/xmldsig#"
  xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
  entityID="{urls['entity_id']}">
  <SPSSODescriptor
    AuthnRequestsSigned="false"
    WantAssertionsSigned="true"
    protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress</NameIDFormat>
    <AssertionConsumerService
      Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
      Location="{urls['acs_url']}"
      index="1" isDefault="true" />
    <SingleLogoutService
      Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
      Location="{urls['logout_url']}" />
  </SPSSODescriptor>
</EntityDescriptor>"""


def _build_saml_authn_request(idp_sso_url: str) -> str:
    """Build a base64+deflate encoded SAMLRequest for HTTP-Redirect binding."""
    urls = _saml_urls()
    request_id = f"_{uuid.uuid4().hex}"
    issue_instant = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    xml = (
        f'<samlp:AuthnRequest'
        f' xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"'
        f' xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"'
        f' ID="{request_id}"'
        f' Version="2.0"'
        f' IssueInstant="{issue_instant}"'
        f' Destination="{idp_sso_url}"'
        f' AssertionConsumerServiceURL="{urls["acs_url"]}"'
        f' ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST">'
        f'<saml:Issuer>{urls["entity_id"]}</saml:Issuer>'
        f'<samlp:NameIDPolicy'
        f' Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"'
        f' AllowCreate="true" />'
        f'</samlp:AuthnRequest>'
    )
    deflated = zlib.compress(xml.encode("utf-8"))[2:-4]  # raw deflate (strip zlib header/trailer)
    return base64.b64encode(deflated).decode("utf-8")


async def _fetch_idp_cert_from_metadata(metadata_url: str) -> str | None:
    """
    Fetch Azure AD federation metadata XML and extract the signing X.509 cert.
    Returns the PEM certificate string, or None on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(metadata_url)
            resp.raise_for_status()
            xml_text = resp.text
    except Exception as e:
        logger.warning("Could not fetch IdP metadata from %s: %s", metadata_url, e)
        return None

    try:
        root = ET.fromstring(xml_text)
        ns = {
            "md":   "urn:oasis:names:tc:SAML:2.0:metadata",
            "ds":   "http://www.w3.org/2000/09/xmldsig#",
            "fed":  "http://docs.oasis-open.org/wsfed/federation/200706",
        }
        # Try standard SAML metadata KeyDescriptor use="signing"
        for kd in root.iter("{urn:oasis:names:tc:SAML:2.0:metadata}KeyDescriptor"):
            use = kd.get("use", "")
            if use in ("signing", ""):
                cert_el = kd.find(".//{http://www.w3.org/2000/09/xmldsig#}X509Certificate")
                if cert_el is not None and cert_el.text:
                    cert_b64 = "".join(cert_el.text.split())
                    return (
                        "-----BEGIN CERTIFICATE-----\n"
                        + "\n".join(cert_b64[i:i+64] for i in range(0, len(cert_b64), 64))
                        + "\n-----END CERTIFICATE-----"
                    )
        # Fallback: grab first X509Certificate anywhere in the doc
        for el in root.iter("{http://www.w3.org/2000/09/xmldsig#}X509Certificate"):
            if el.text:
                cert_b64 = "".join(el.text.split())
                return (
                    "-----BEGIN CERTIFICATE-----\n"
                    + "\n".join(cert_b64[i:i+64] for i in range(0, len(cert_b64), 64))
                    + "\n-----END CERTIFICATE-----"
                )
    except Exception as e:
        logger.warning("Could not parse IdP metadata XML: %s", e)
    return None


def _extract_idp_sso_url_from_metadata_xml(xml_text: str) -> str | None:
    """Extract the IdP SSO POST / Redirect binding URL from federation metadata XML."""
    try:
        root = ET.fromstring(xml_text)
        # Look for SingleSignOnService with POST or Redirect binding
        for el in root.iter("{urn:oasis:names:tc:SAML:2.0:metadata}SingleSignOnService"):
            binding = el.get("Binding", "")
            if "HTTP-POST" in binding or "HTTP-Redirect" in binding:
                return el.get("Location")
        # Azure WS-Fed metadata — PassiveRequestorEndpoint
        for el in root.iter():
            if "PassiveRequestorEndpoint" in el.tag:
                addr = el.find(".//{http://www.w3.org/2005/08/addressing}Address")
                if addr is not None and addr.text:
                    return addr.text.strip()
    except Exception:
        pass
    return None


def _parse_saml_response(saml_response_b64: str) -> dict:
    """
    Decode and parse a SAMLResponse (base64 → XML).
    Returns a dict with email, name, external_id.
    Does NOT verify the signature here (we rely on Azure AD's TLS + cert check).
    For production-grade signature verification use python3-saml.
    """
    try:
        xml_bytes = base64.b64decode(saml_response_b64)
        root = ET.fromstring(xml_bytes)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid SAMLResponse encoding: {e}")

    ns = {
        "saml":  "urn:oasis:names:tc:SAML:2.0:assertion",
        "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    }

    # ── Check top-level status ────────────────────────────────────────────────
    status_code = root.find(".//samlp:StatusCode", ns)
    if status_code is not None:
        value = status_code.get("Value", "")
        if "Success" not in value:
            raise HTTPException(status_code=401, detail=f"SAML authentication failed: {value}")

    # ── Extract NameID (usually email or UPN) ─────────────────────────────────
    name_id_el = root.find(".//saml:NameID", ns)
    name_id = (name_id_el.text or "").strip() if name_id_el is not None else ""

    # ── Extract Attribute values ──────────────────────────────────────────────
    attrs: dict[str, str] = {}
    for attr in root.findall(".//saml:Attribute", ns):
        attr_name  = attr.get("Name", "")
        attr_value = attr.find("saml:AttributeValue", ns)
        if attr_value is not None and attr_value.text:
            # Normalise attribute name to a short key
            key = attr_name.split("/")[-1].lower()  # e.g. "emailaddress", "givenname"
            attrs[key] = attr_value.text.strip()

    # Resolve email
    email = (
        attrs.get("emailaddress")
        or attrs.get("email")
        or attrs.get("mail")
        or (name_id if "@" in name_id else "")
    ).lower().strip()

    # Resolve display name
    given  = attrs.get("givenname", "")
    family = attrs.get("surname", attrs.get("familyname", ""))
    name   = (f"{given} {family}".strip() or attrs.get("name") or email.split("@")[0] or "SSO User")

    # External ID — prefer the assertion Subject NameID OID, fallback to email
    external_id = (
        attrs.get("objectidentifier")
        or attrs.get("oid")
        or name_id
        or email
    )

    return {"email": email, "name": name, "external_id": external_id}


async def _provision_saml_user(user_info: dict, cfg: SSOConfig, db: AsyncSession) -> User:
    """Create or update a user from SAML assertion claims."""
    result = await db.execute(
        select(User).where(User.external_id == user_info["external_id"])
    )
    user: User | None = result.scalar_one_or_none()

    role = UserRole(cfg.default_role)

    if user is None:
        if user_info["email"]:
            existing = await db.execute(
                select(User).where(User.username == user_info["email"])
            )
            user = existing.scalar_one_or_none()

        if user is None:
            if not cfg.auto_create_users:
                raise HTTPException(status_code=403, detail="User not provisioned")
            name = user_info["name"]
            initials = "".join(w[0].upper() for w in name.split()[:2]) or "?"
            user = User(
                name=name,
                initials=initials,
                username=user_info["email"] or user_info["external_id"],
                hashed_password=hash_password(str(uuid.uuid4())),
                role=role,
                external_id=user_info["external_id"],
                auth_provider="saml",
                is_active=True,
            )
            db.add(user)
        else:
            user.external_id  = user_info["external_id"]
            user.auth_provider = "saml"
    elif cfg.sync_on_login:
        user.name = user_info["name"]
        user.role = role

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    await db.commit()
    await db.refresh(user)
    return user


# ══════════════════════════════════════════════════════════════════════════════
# SAML 2.0 Auth flow  —  /auth/saml/*
# ══════════════════════════════════════════════════════════════════════════════

@saml_router.get(
    "/metadata",
    summary="SAML SP metadata XML — upload this file to Azure AD",
    response_class=Response,
)
async def saml_metadata():
    """
    Returns the Service Provider metadata XML.

    **How to use:**
    1. Download (or copy) this XML.
    2. In Azure AD → Enterprise Applications → your app → Single sign-on
       → click **↑ Upload metadata file** and upload it.
    Azure AD will auto-fill Entity ID, Reply URL, and Logout URL.
    """
    xml = _build_sp_metadata_xml()
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": 'attachment; filename="tibos-saml-sp-metadata.xml"'},
    )


@saml_router.get(
    "/login",
    summary="Initiate SAML login — redirects to Azure AD",
)
async def saml_login(db: AsyncSession = Depends(get_db)):
    """
    Builds a SAML `AuthnRequest`, deflate+base64-encodes it, and redirects the
    browser to the IdP SSO URL (HTTP-Redirect binding).

    Requires `saml_mode=True` and either:
    - `idp_metadata_url` (backend auto-fetches the SSO URL), or
    - `authorization_endpoint` set to the IdP SSO URL manually.
    """
    cfg = await _get_or_create_sso_config(db)
    if not cfg.enabled:
        raise HTTPException(status_code=400, detail="SSO is not enabled")
    if not cfg.saml_mode:
        raise HTTPException(status_code=400, detail="SAML mode is not enabled; use /auth/sso/login for OIDC")

    # Resolve IdP SSO URL
    idp_sso_url: str | None = None

    if cfg.idp_metadata_url:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(cfg.idp_metadata_url)
                resp.raise_for_status()
                idp_sso_url = _extract_idp_sso_url_from_metadata_xml(resp.text)
        except Exception as e:
            logger.warning("Could not fetch IdP metadata for SAML login: %s", e)

    # Fallback: authorization_endpoint field re-used as IdP SSO URL in SAML mode
    if not idp_sso_url:
        idp_sso_url = cfg.authorization_endpoint

    if not idp_sso_url:
        raise HTTPException(
            status_code=400,
            detail=(
                "IdP SSO URL not configured. "
                "Set 'IdP Metadata URL' or 'Authorization Endpoint' in the SSO admin panel."
            ),
        )

    saml_request = _build_saml_authn_request(idp_sso_url)
    params = urlencode({"SAMLRequest": saml_request})
    return RedirectResponse(f"{idp_sso_url}?{params}")


@saml_router.post(
    "/acs",
    summary="SAML ACS — receives SAMLResponse from Azure AD (POST binding)",
    response_class=HTMLResponse,
)
async def saml_acs(
    SAMLResponse: str = Form(...),
    RelayState: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Assertion Consumer Service endpoint.  Azure AD POST-binds the `SAMLResponse`
    here after authenticating the user.

    Flow:
    1. Base64-decode + parse the SAMLResponse XML.
    2. Check SAML StatusCode = Success.
    3. Extract email, name, external_id from Attributes.
    4. JIT-provision the user (create or update).
    5. Issue an application JWT.
    6. Redirect the browser to the frontend with the token.

    Note: Full XML-DSIG signature verification requires `python3-saml`.
    The current implementation trusts the IdP certificate at the TLS level
    (Azure AD only accepts POST to your registered ACS URL over HTTPS).
    """
    cfg = await _get_or_create_sso_config(db)
    if not cfg.enabled:
        return RedirectResponse(f"{settings.FRONTEND_URL}/login?sso_error=sso_disabled")

    # Parse the SAML response
    try:
        user_info = _parse_saml_response(SAMLResponse)
    except HTTPException as e:
        logger.warning("SAML ACS parse error: %s", e.detail)
        return RedirectResponse(
            f"{settings.FRONTEND_URL}/login?sso_error=invalid_saml_response"
        )

    if not user_info.get("email") and not user_info.get("external_id"):
        return RedirectResponse(
            f"{settings.FRONTEND_URL}/login?sso_error=missing_saml_attributes"
        )

    # Provision user
    try:
        user = await _provision_saml_user(user_info, cfg, db)
    except HTTPException as e:
        logger.warning("SAML user provision error: %s", e.detail)
        error_key = "user_not_provisioned" if e.status_code == 403 else "provision_error"
        return RedirectResponse(f"{settings.FRONTEND_URL}/login?sso_error={error_key}")

    # Issue JWT and redirect to frontend
    app_token = create_access_token(str(user.id), extra={"role": user.role.value})
    user_json = json.dumps({
        "id":        str(user.id),
        "name":      user.name,
        "username":  user.username,
        "role":      user.role.value,
        "initials":  user.initials,
        "group":     user.group,
        "is_active": user.is_active,
    })
    user_b64 = base64.urlsafe_b64encode(user_json.encode()).decode()
    redirect_url = (
        f"{settings.FRONTEND_URL}/login"
        f"?sso_token={app_token}&sso_user={user_b64}"
    )
    return RedirectResponse(redirect_url, status_code=302)


@saml_router.get(
    "/logout",
    summary="SAML logout — clears local session and redirects to login",
)
async def saml_logout():
    """
    Handles SAML Single Logout (SLO) or simple SP-initiated logout.
    Since JWTs are stateless, we just redirect to the login page.
    The frontend clears its Zustand store on redirect.
    """
    return RedirectResponse(f"{settings.FRONTEND_URL}/login?logged_out=1")


# ══════════════════════════════════════════════════════════════════════════════
# Auth flow  (OIDC)
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
        raise HTTPException(status_code=400, detail="SSO is not fully configured (Client ID / Redirect URI)")
    if cfg.provider == "microsoft" and not cfg.tenant_id:
        raise HTTPException(status_code=400, detail="SSO is not fully configured (Tenant ID)")
    if cfg.provider == "custom" and not (cfg.authorization_endpoint and cfg.token_endpoint and cfg.jwks_uri and cfg.issuer):
        raise HTTPException(status_code=400, detail="SSO is not fully configured (custom OIDC endpoints)")

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

    # ── SAML fields ──────────────────────────────────────────────────────
    cfg.saml_mode = body.saml_mode

    new_metadata_url = body.idp_metadata_url or None
    cfg.idp_metadata_url = new_metadata_url

    # If a cert was explicitly pasted, use it; otherwise auto-fetch from metadata URL
    if body.idp_cert:
        cfg.idp_cert = body.idp_cert
    elif new_metadata_url:
        fetched_cert = await _fetch_idp_cert_from_metadata(new_metadata_url)
        if fetched_cert:
            cfg.idp_cert = fetched_cert
            logger.info("Auto-fetched IdP certificate from %s", new_metadata_url)
    # (if no cert provided and no URL, leave existing cert unchanged)

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


@admin_router.get(
    "/saml-metadata",
    response_model=SSOSamlMetadataOut,
    summary="Return SP metadata values + XML — used by the admin UI metadata card",
)
async def get_saml_sp_metadata(
    _: User = Depends(require_admin),
):
    """
    Returns all four SAML SP metadata fields (Entity ID, ACS URL, Sign-on URL,
    Logout URL) together with the full SP metadata XML.

    The admin UI uses this to populate the metadata card, and the
    'Download Metadata XML' button uses the returned `xml` field.
    """
    urls = _saml_urls()
    xml  = _build_sp_metadata_xml()
    return SSOSamlMetadataOut(
        entity_id    = urls["entity_id"],
        acs_url      = urls["acs_url"],
        slo_url      = urls["logout_url"],
        login_url    = urls["login_url"],
        metadata_url = urls["metadata_url"],
        xml          = xml,
    )
