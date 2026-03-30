from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

import urllib.parse as _urlparse

def _build_connect_args(url: str) -> dict:
    """Return connect_args appropriate for the DB URL (SSL only for remote hosts)."""
    try:
        parsed = _urlparse.urlparse(url)
        host = parsed.hostname or ""
        # Enable SSL for any non-localhost remote host that requests it
        needs_ssl = host not in ("localhost", "127.0.0.1", "::1") and "ssl=require" in url
        return {"ssl": True} if needs_ssl else {}
    except Exception:
        return {}

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    pool_size=10,
    connect_args=_build_connect_args(settings.DATABASE_URL),
    max_overflow=20,
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
