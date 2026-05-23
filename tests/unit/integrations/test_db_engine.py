from __future__ import annotations

from types import SimpleNamespace


def test_select_database_url_prefers_admin_and_falls_back_to_runtime() -> None:
    from fleet_rlm.integrations.database.engine import select_database_url

    assert (
        select_database_url(
            runtime_url="postgresql://runtime",
            admin_url="postgresql://admin",
            prefer_admin=True,
        )
        == "postgresql://admin"
    )
    assert (
        select_database_url(
            runtime_url="postgresql://runtime",
            admin_url=None,
            prefer_admin=True,
        )
        == "postgresql://runtime"
    )
    assert select_database_url(runtime_url=None, admin_url=None) is None


def test_database_manager_creates_engine_with_expected_pool_settings(monkeypatch) -> None:
    from fleet_rlm.integrations.database.engine import DatabaseManager

    created: list[tuple[str, dict[str, object]]] = []

    def fake_create_async_engine(url: str, **kwargs: object) -> object:
        created.append((url, kwargs))
        return SimpleNamespace(url=url, kwargs=kwargs)

    monkeypatch.setattr(
        "fleet_rlm.integrations.database.engine.create_async_engine",
        fake_create_async_engine,
    )

    manager = DatabaseManager(
        "postgresql://user:pass@ep-test-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require",
        echo=True,
        connect_timeout=22,
    )

    engine = manager.engine

    assert engine is manager.engine
    assert len(created) == 1
    url, kwargs = created[0]
    assert url.startswith("postgresql+asyncpg://user:pass@ep-test-pooler.us-east-2.aws.neon.tech/neondb")
    assert "channel_binding" not in url
    assert "ssl=require" in url
    assert "prepared_statement_cache_size=0" in url
    assert kwargs["echo"] is True
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_size"] == 3
    assert kwargs["max_overflow"] == 5
    assert kwargs["pool_timeout"] == 22
    assert kwargs["pool_recycle"] == 180
    assert kwargs["future"] is True
    assert kwargs["connect_args"] == {
        "timeout": 22,
        "server_settings": {"jit": "off"},
    }
