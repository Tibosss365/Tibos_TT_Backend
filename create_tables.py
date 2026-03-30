"""
Create all database tables.

Run this once before starting the server for the first time, or use it to
re-create tables in a fresh database.

Usage (from the backend/ directory):
    python create_tables.py

After tables are created you can optionally seed sample data:
    python seed.py
"""
import asyncio

from app.database import Base, engine

# Import all models so Base.metadata knows about every table
import app.models  # noqa: F401


async def create_tables() -> None:
    print("Creating database tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[OK] All tables created successfully")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(create_tables())
