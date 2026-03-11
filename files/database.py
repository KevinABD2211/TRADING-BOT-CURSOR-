"""
database.py
-----------
Async SQLAlchemy engine, session factory, and declarative base.

All database I/O in the application uses async sessions via asyncpg.
Alembic migrations use the synchronous psycopg2 URL.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

engine: AsyncEngine = create_async_engine(
    settings.database.async_url,
    pool_size=settings.database.pool_size,
    max_overflow=settings.database.max_overflow,
    pool_timeout=settings.database.pool_timeout,
    pool_pre_ping=True,          # Reconnect on stale connections
    echo=settings.database.echo_sql,
    future=True,
)

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,      # Prevents lazy-load errors after commit
)

# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """
    Shared SQLAlchemy declarative base.
    All ORM models inherit from this class.
    """
    pass


# ---------------------------------------------------------------------------
# Dependency-injectable session
# ---------------------------------------------------------------------------


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields an async database session.
    Automatically commits on success or rolls back on exception.

    Usage:
        @router.get("/example")
        async def example(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """
    Context-manager version of get_db for use outside of FastAPI
    dependency injection (e.g. background tasks, Celery workers,
    Discord bot event handlers).

    Usage:
        async with get_db_context() as db:
            result = await db.execute(...)
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("Database session rolled back due to exception")
            raise
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# Startup / shutdown helpers
# ---------------------------------------------------------------------------


async def init_db() -> None:
    """
    Called on application startup.
    Verifies database connectivity.
    Does NOT run migrations — use Alembic for schema management.
    """
    try:
        async with engine.connect() as conn:
            from sqlalchemy import text
            await conn.execute(text("SELECT 1"))
        logger.info("Database connection verified successfully")
    except Exception as exc:
        logger.critical("Failed to connect to database: %s", exc)
        raise


async def dispose_db() -> None:
    """
    Called on application shutdown.
    Gracefully closes all pooled connections.
    """
    await engine.dispose()
    logger.info("Database engine disposed")
