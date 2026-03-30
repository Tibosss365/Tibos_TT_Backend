"""
Redis-backed cache service.

Cache keys and TTLs:
  - stats:{hash}               → dashboard/admin stats         TTL 60s
  - tickets:list:{hash}        → paginated ticket list result  TTL 30s
  - analytics:{hash}           → analytics payload             TTL 120s

All cached values are JSON strings.
Call invalidate_tickets() whenever a ticket is written so stale
list/stats caches are cleared immediately.
"""
import hashlib
import json
from typing import Any

from redis.asyncio import Redis

STATS_TTL = 60
LIST_TTL = 30
ANALYTICS_TTL = 120

_TICKET_PATTERNS = ["tickets:list:*", "stats:*", "analytics:*"]


def _make_key(prefix: str, params: Any) -> str:
    raw = json.dumps(params, sort_keys=True, default=str)
    digest = hashlib.md5(raw.encode()).hexdigest()[:12]
    return f"{prefix}:{digest}"


async def cache_get(redis: Redis, key: str) -> Any | None:
    try:
        raw = await redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        return None


async def cache_set(redis: Redis, key: str, value: Any, ttl: int) -> None:
    try:
        await redis.setex(key, ttl, json.dumps(value, default=str))
    except Exception:
        pass


async def invalidate_tickets(redis: Redis) -> None:
    """Delete all ticket-related cache keys."""
    try:
        for pattern in _TICKET_PATTERNS:
            keys = await redis.keys(pattern)
            if keys:
                await redis.delete(*keys)
    except Exception:
        pass


def ticket_list_key(params: dict) -> str:
    return _make_key("tickets:list", params)


def stats_key() -> str:
    return "stats:dashboard"


def analytics_key() -> str:
    return "analytics:main"
