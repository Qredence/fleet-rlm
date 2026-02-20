# Database Architecture

This project uses a local SQLite database to persist application state, managed asynchronously via SQLAlchemy and SQLModel.

## Core Setup

- **Database Engine**: SQLite (`fleet_rlm.db`)
- **ORM**: [SQLModel](https://sqlmodel.tiangolo.com/) (built on Pydantic and SQLAlchemy)
- **Async Support**: `aiosqlite` with `AsyncSession`

The database configuration is defined in `src/fleet_rlm/server/database.py`.

```python
DATABASE_URL = "sqlite+aiosqlite:///./fleet_rlm.db"
```

## Initialization & Lifecycle

Unlike systems that use external migration tools like Alembic, this local setup currently initializes the schema dynamically on application startup.

During the FastAPI application lifespan (defined in `src/fleet_rlm/server/main.py`), the `init_db()` function is called:

```python
async def init_db() -> None:
    """Initialize the database by creating all tables."""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
```

This creates all tables defined by SQLModel classes that have been imported into the application registry.

## Session Management

Database sessions are provided to FastAPI endpoints via dependency injection using the `get_db_session()` generator.

```python
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from .database import get_db_session

@router.get("/example")
async def example_endpoint(session: AsyncSession = Depends(get_db_session)):
    # Use the async session
    pass
```

## Data Models

The data models representing the application state (e.g., chat sessions, tasks, memory) are defined using SQLModel. The specific tables and relationships are defined across the `models.py` files in the codebase (e.g., `src/fleet_rlm/server/models.py`).

Since this is a local, single-tenant SQLite database, complex concepts like Row-Level Security (RLS) or tenant isolation are not applicable or enforced at the database level in this environment.
