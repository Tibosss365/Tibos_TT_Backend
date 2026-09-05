"""
Login attempt throttling — Redis-backed brute-force protection.

Only guards the LOCAL username+password login endpoints (/auth/login,
/auth/login/2fa). SSO and SAML users authenticate via a completely
different flow (/auth/sso/*, /auth/saml/*) that never touches this, so it
has zero effect on Microsoft/Entra ID login either way.

Fails open: if Redis is unreachable, every check here returns "not locked
out" / silently no-ops rather than raising — a Redis outage must never
itself lock everyone out of login.
"""
import logging

from app.redis_client import get_redis

logger = logging.getLogger("uvicorn.error")

MAX_ATTEMPTS = 5
WINDOW_SECONDS = 15 * 60    # failed attempts are counted within this rolling window
LOCKOUT_SECONDS = 15 * 60   # once the threshold is hit, stay locked out this long


def _key(username: str) -> str:
    return f"login_fail:{(username or '').strip().lower()}"


async def check_locked_out(username: str) -> int | None:
    """Return remaining lockout seconds if this username is currently locked
    out, else None."""
    try:
        redis = await get_redis()
        key = _key(username)
        count_raw = await redis.get(key)
        count = int(count_raw) if count_raw else 0
        if count < MAX_ATTEMPTS:
            return None
        ttl = await redis.ttl(key)
        return ttl if ttl and ttl > 0 else None
    except Exception as e:
        logger.warning(f"[login_throttle] check_locked_out failed (failing open): {e}")
        return None


async def record_failed_attempt(username: str) -> None:
    try:
        redis = await get_redis()
        key = _key(username)
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, WINDOW_SECONDS)
        elif count == MAX_ATTEMPTS:
            # Threshold just hit — (re)start the lockout window from now.
            await redis.expire(key, LOCKOUT_SECONDS)
    except Exception as e:
        logger.warning(f"[login_throttle] record_failed_attempt failed (ignoring): {e}")


async def reset_attempts(username: str) -> None:
    try:
        redis = await get_redis()
        await redis.delete(_key(username))
    except Exception as e:
        logger.warning(f"[login_throttle] reset_attempts failed (ignoring): {e}")
