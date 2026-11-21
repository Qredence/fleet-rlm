"""Run migrations through the direct Postgres endpoint."""

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from fleet_rlm.config import Settings
from fleet_rlm.db import Base


def run_migrations() -> None:
    if context.is_offline_mode():
        context.configure(
            dialect_name="postgresql",
            target_metadata=Base.metadata,
            literal_binds=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = create_engine(Settings().database_direct_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=Base.metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
