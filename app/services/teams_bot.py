"""
Microsoft Teams bot — Bot Framework Connector auth + send, and inbound JWT
verification for the public /api/messages endpoint.

Two separate token exchanges are involved:
  • Inbound  — Teams/Bot Framework signs each request to /api/messages with a
    JWT we must verify (via Bot Framework's own OpenID metadata/JWKS).
  • Outbound — to send a message into a conversation, we exchange the bot's
    App ID + secret for a Bot Framework Connector access token (client
    credentials grant), then POST the activity to that conversation's
    serviceUrl.
"""
import logging
import time
from typing import Optional

import httpx
from jose import jwt

logger = logging.getLogger(__name__)

BOT_OPENID_METADATA_URL = "https://login.botframework.com/v1/.well-known/openidconfiguration"
BOT_CONNECTOR_ISSUER = "https://api.botframework.com"
BOT_CONNECTOR_SCOPE = "https://api.botframework.com/.default"

# ── Inbound JWT verification (Bot Framework → us) ─────────────────────────────
_jwks_cache: dict = {"keys": None, "fetched_at": 0.0}


async def _get_jwks() -> list[dict]:
    now = time.time()
    if _jwks_cache["keys"] and now - _jwks_cache["fetched_at"] < 3600:
        return _jwks_cache["keys"]
    async with httpx.AsyncClient(timeout=10) as client:
        meta = (await client.get(BOT_OPENID_METADATA_URL)).json()
        jwks = (await client.get(meta["jwks_uri"])).json()
    keys = jwks.get("keys", [])
    _jwks_cache.update(keys=keys, fetched_at=now)
    return keys


async def verify_bot_request(auth_header: Optional[str], app_id: str) -> bool:
    """Verify the Authorization header on an incoming /api/messages request
    was genuinely signed by the Bot Framework channel service for our bot."""
    if not auth_header or not auth_header.startswith("Bearer ") or not app_id:
        return False
    token = auth_header.split(" ", 1)[1]
    try:
        keys = await _get_jwks()
        header = jwt.get_unverified_header(token)
        key = next((k for k in keys if k.get("kid") == header.get("kid")), None)
        if not key:
            # Keys may have rotated — refetch once and retry.
            _jwks_cache["keys"] = None
            keys = await _get_jwks()
            key = next((k for k in keys if k.get("kid") == header.get("kid")), None)
        if not key:
            return False
        jwt.decode(
            token, key, algorithms=["RS256"],
            audience=app_id, issuer=BOT_CONNECTOR_ISSUER,
        )
        return True
    except Exception:
        logger.warning("Teams bot: inbound JWT verification failed", exc_info=True)
        return False


# ── Outbound token (us → Bot Framework Connector API) ─────────────────────────
_token_cache: dict = {"token": None, "expires_at": 0.0}


async def _get_bot_token(tenant_id: str, app_id: str, app_password: str) -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": app_id,
        "client_secret": app_password,
        "scope": BOT_CONNECTOR_SCOPE,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(token_url, data=data)
        resp.raise_for_status()
        body = resp.json()

    token = body["access_token"]
    _token_cache.update(token=token, expires_at=now + int(body.get("expires_in", 3600)))
    return token


async def send_message(
    *, tenant_id: str, app_id: str, app_password: str,
    service_url: str, conversation_id: str, text: str,
) -> None:
    """Send a plain-text message into an existing Teams conversation."""
    token = await _get_bot_token(tenant_id, app_id, app_password)
    url = f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            url,
            json={"type": "message", "text": text},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()


async def reply_to_activity(
    *, tenant_id: str, app_id: str, app_password: str,
    incoming_activity: dict, text: str,
) -> None:
    """Reply directly to an inbound Activity (used before we've persisted a
    TeamsConversation row, e.g. the very first message / bot-added greeting)."""
    service_url = incoming_activity.get("serviceUrl")
    conversation = incoming_activity.get("conversation") or {}
    conversation_id = conversation.get("id")
    if not service_url or not conversation_id:
        return
    await send_message(
        tenant_id=tenant_id, app_id=app_id, app_password=app_password,
        service_url=service_url, conversation_id=conversation_id, text=text,
    )
