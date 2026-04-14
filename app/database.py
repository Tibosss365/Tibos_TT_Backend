import urllib.parse as _urlparse

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
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
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
