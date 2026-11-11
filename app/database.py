import urllib.parse as _urlparse

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from fastapi import Request
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import get_settings

settings = get_settings()


def _build_connect_args(url: str) -> dict:
    """Return connect_args appropriate for the DB URL (SSL only for remote hosts)."""
    try:
        parsed = _urlparse.urlparse(url)
        host = parsed.hostname or ""
        needs_ssl = host not in ("localhost", "127.0.0.1", "::1") and "ssl=require" in url
        return {"ssl": True} if needs_ssl else {}
    except Exception:
        return {}


# NullPool: no connections are held between requests.
# Each DB operation opens and closes its own connection.
# This completely prevents pool-leak / TooManyConnections errors on
# low-limit hosted databases (Neon, Supabase free tier, Azure Basic, etc.)
# and is the safest choice for a single-process dev/prod server.
# The database is far from the app server, so every round trip is expensive.
# Keep a few connections warm (new ones cost several extra round trips for TLS)
# and recycle them before the server drops idle ones. The overall cap is unchanged
# (10 + 5 = the old default 5 + 10).
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_size=10,
    max_overflow=5,
    pool_recycle=1800,
)

# Same pool, AUTOCOMMIT: a plain read needs no BEGIN / COMMIT, which saves two
# round trips per request. Used for GET requests only (see get_db).
autocommit_engine = engine.execution_options(isolation_level="AUTOCOMMIT")

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    pass


AsyncSessionAutocommit = async_sessionmaker(
    autocommit_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# GET handlers that create a default row when it is missing — keep these (and the
# SSO login callback) on a normal transactional session.
_TRANSACTIONAL_GET_HANDLERS = {
    "get_email", "get_sla", "get_ticket_settings", "get_inbound_config",
    "get_log_detail", "sso_callback", "get_teams_config",
}


async def get_db(request: Request) -> AsyncSession:
    route = request.scope.get("route")
    read_only = (
        request.method in ("GET", "HEAD")
        and route is not None
        and getattr(route, "name", None) not in _TRANSACTIONAL_GET_HANDLERS
    )
    async with (AsyncSessionAutocommit if read_only else AsyncSessionLocal)() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
