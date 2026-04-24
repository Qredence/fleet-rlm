"""Unit tests for database URL helpers."""

from fleet_rlm.integrations.database.engine import (
    select_database_url,
    to_async_database_url,
)


def test_select_database_url_prefers_runtime_by_default() -> None:
    assert (
        select_database_url(
            runtime_url="postgresql://runtime",
            admin_url="postgresql://admin",
        )
        == "postgresql://runtime"
    )


def test_select_database_url_prefers_admin_when_requested() -> None:
    assert (
        select_database_url(
            runtime_url="postgresql://runtime",
            admin_url="postgresql://admin",
            prefer_admin=True,
        )
        == "postgresql://admin"
    )


def test_select_database_url_falls_back_when_preferred_value_missing() -> None:
    assert (
        select_database_url(
            runtime_url="postgresql://runtime",
            admin_url=None,
            prefer_admin=True,
        )
        == "postgresql://runtime"
    )
    assert (
        select_database_url(
            runtime_url=None,
            admin_url="postgresql://admin",
            prefer_admin=False,
        )
        == "postgresql://admin"
    )


def test_select_database_url_returns_none_when_unset() -> None:
    assert select_database_url(runtime_url=None, admin_url=None) is None


def test_to_async_database_url_disables_prepared_cache_for_pooler_urls() -> None:
    assert to_async_database_url(
        "postgresql://user:pass@ep-test-pooler.us-east-2.aws.neon.tech/neondb"
        "?sslmode=require&channel_binding=require"
    ) == (
        "postgresql+asyncpg://user:pass@ep-test-pooler.us-east-2.aws.neon.tech/neondb"
        "?ssl=require&prepared_statement_cache_size=0"
    )


def test_to_async_database_url_preserves_explicit_prepared_cache_override() -> None:
    assert to_async_database_url(
        "postgresql://user:pass@ep-test-pooler.us-east-2.aws.neon.tech/neondb"
        "?sslmode=require&prepared_statement_cache_size=25"
    ) == (
        "postgresql+asyncpg://user:pass@ep-test-pooler.us-east-2.aws.neon.tech/neondb"
        "?prepared_statement_cache_size=25&ssl=require"
    )


def test_to_async_database_url_disables_cache_for_direct_neon_hosts() -> None:
    assert to_async_database_url(
        "postgresql://user:pass@ep-test.us-east-2.aws.neon.tech/neondb"
        "?sslmode=require&channel_binding=require"
    ) == (
        "postgresql+asyncpg://user:pass@ep-test.us-east-2.aws.neon.tech/neondb"
        "?ssl=require&prepared_statement_cache_size=0"
    )
