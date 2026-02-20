"""Async database configuration and session management for Fleet RLM."""

import logging
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

logger = logging.getLogger(__name__)

# Defaults to local SQLite file for development
DATABASE_URL = "sqlite+aiosqlite:///./fleet_rlm.db"

# Create the async engine
# connect_args={"check_same_thread": False} is required for SQLite.
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

# Async session factory
async_session_maker = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db() -> None:
    """Initialize the database by creating all tables."""
    logger.info("Initializing database schema...")
    async with engine.begin() as conn:
        # Create all tables defined by SQLModel
        await conn.run_sync(SQLModel.metadata.create_all)
    logger.info("Database schema initialized.")


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for providing database sessions to FastAPI endpoints."""
    async with async_session_maker() as session:
        yield session
