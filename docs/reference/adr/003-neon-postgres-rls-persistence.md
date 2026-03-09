# ADR-003: Neon/Postgres with RLS for Persistence

## Status

Accepted

## Context

Fleet-RLM requires a persistence layer for:

1. **Multi-tenant data isolation**: Each tenant must only access their own data
2. **User management**: Track users within tenants
3. **Session persistence**: Store agent sessions and conversation history
4. **Memory storage**: Persist agent memory across sessions
5. **Job queue**: Background task management

Options considered:
- **SQLite**: No multi-tenant isolation, not suitable for production
- **PostgreSQL (self-hosted)**: Operational overhead, manual scaling
- **Neon (serverless Postgres)**: Auto-scaling, branch feature, built-in connection pooling
- **MongoDB**: Different consistency model, loses relational integrity

## Decision

We use **Neon (serverless PostgreSQL)** as the primary database, with **Row-Level Security (RLS)** for tenant isolation.

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Application Layer                         │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │                    SQLAlchemy ORM                            │ │
│  │  - AsyncSession with asyncpg driver                         │ │
│  │  - Repository pattern for data access                       │ │
│  │  - Typed domain models                                       │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Neon Postgres Database                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │  tenants    │  │   users     │  │   memory    │  ...         │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │              Row-Level Security Policies                     │ │
│  │  - tenant_isolation_policy on all tenant-scoped tables      │ │
│  │  - current_tenant_id() from session context                 │ │
│  └─────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

#### 1. Tenant-Scoped Models

All multi-tenant models include a `tenant_id` foreign key:

```python
class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    entra_tenant_id: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[TenantStatus]  # active, suspended, deleted

class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    entra_user_id: Mapped[str]
```

#### 2. Row-Level Security

PostgreSQL RLS policies enforce tenant isolation at the database level:

```sql
-- Enable RLS on tenant-scoped tables
ALTER TABLE users ENABLE ROW LEVEL SECURITY;

-- Policy: Users can only see rows in their tenant
CREATE POLICY tenant_isolation_policy ON users
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid);
```

This provides **defense in depth** — even if application logic fails, the database prevents cross-tenant access.

#### 3. Session Context

The `tenant_id` is set in the PostgreSQL session context for RLS enforcement:

```python
async with session.begin():
    await session.execute(
        text("SET app.current_tenant_id = :tenant_id"),
        {"tenant_id": tenant_id}
    )
    # All subsequent queries are filtered by RLS
```

#### 4. Repository Pattern

Data access is encapsulated in repository classes:

```python
class Repository:
    async def get_tenant_by_entra_id(
        self, entra_tenant_id: str
    ) -> Tenant | None: ...
    async def upsert_user(
        self, tenant_id: uuid.UUID, entra_user_id: str
    ) -> User: ...
    async def create_memory(
        self, tenant_id: uuid.UUID
    ) -> Memory: ...
```

### Database Schema Overview

| Table | Purpose | Tenant-Scoped |
|-------|---------|---------------|
| `tenants` | Tenant entities | No (root level) |
| `users` | User accounts | Yes |
| `memberships` | User-tenant relationships | Yes |
| `sandbox_sessions` | Agent session tracking | Yes |
| `runs` | Agent execution runs | Yes |
| `run_steps` | Individual run steps | Yes |
| `memory` | Agent memory storage | Yes |
| `artifacts` | Generated artifacts | Yes |
| `jobs` | Background job queue | Yes |

### Connection Management

The `DatabaseManager` class handles async connections:

```python
class DatabaseManager:
    def __init__(self, database_url: str):
        self._engine = create_async_engine(
            to_async_database_url(database_url),
            pool_pre_ping=True,
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_maker() as session:
            yield session
```

### Neon-Specific Benefits

- **Auto-scaling**: Compute scales to zero when idle
- **Connection pooling**: Built-in PgBouncer integration
- **Branching**: Database branches for development/testing
- **Point-in-time recovery**: Built-in backup and restore

## Consequences

### Positive

- **Strong isolation**: RLS provides defense-in-depth for tenant isolation
- **Relational integrity**: Foreign keys ensure data consistency
- **Type safety**: SQLAlchemy models with typed mapped columns
- **Async support**: First-class async via asyncpg driver
- **Serverless scaling**: Neon scales automatically with demand
- **Developer experience**: Database branching for dev/test

### Negative

- **Complexity**: RLS policies add database-level complexity
- **Connection management**: Must properly set session context
- **Neon dependency**: Tied to Neon-specific features (branching, pooling)

### Neutral

- `DATABASE_URL` must use `postgresql+asyncpg://` scheme for async
- `DATABASE_REQUIRED=true` enforced when `AUTH_MODE=entra`
- Migrations managed via Alembic in `migrations/`

## References

- `src/fleet_rlm/db/models.py` — SQLAlchemy model definitions
- `src/fleet_rlm/db/repository.py` — Data access repository
- `src/fleet_rlm/db/engine.py` — Connection management
- `src/fleet_rlm/server/auth/admission.py` — Tenant admission flow
- `migrations/` — Alembic migration scripts
- Neon documentation: https://neon.tech/docs
