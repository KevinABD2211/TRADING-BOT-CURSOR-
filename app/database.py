"""
Async SQLAlchemy engine, session factory, and declarative base.
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


engine: AsyncEngine = create_async_engine(
    settings.database.async_url,
    pool_size=settings.database.pool_size,
    max_overflow=settings.database.max_overflow,
    pool_timeout=settings.database.pool_timeout,
    pool_pre_ping=True,
    echo=settings.database.echo_sql,
    future=True,
)


AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Shared SQLAlchemy declarative base."""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
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


async def init_db() -> None:
    """Verify database connectivity on startup."""
    try:
        async with engine.connect() as conn:
            from sqlalchemy import text

            await conn.execute(text("SELECT 1"))
        logger.info("Database connection verified successfully")
    except Exception as exc:
        logger.critical("Failed to connect to database: %s", exc)
        raise


async def dispose_db() -> None:
    """Dispose engine on shutdown."""
    await engine.dispose()
    logger.info("Database engine disposed")

