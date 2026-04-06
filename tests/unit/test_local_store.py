from __future__ import annotations

from fleet_rlm.integrations.database import local_store


def test_get_engine_caches_by_resolved_db_path(monkeypatch, tmp_path):
    monkeypatch.delenv("FLEET_RLM_LOCAL_DB_URL", raising=False)

    original_engines = local_store._engines
    monkeypatch.setattr(local_store, "_engines", {})

    try:
        db_a = tmp_path / "a" / "local.db"
        db_b = tmp_path / "b" / "local.db"

        engine_a1 = local_store.get_engine(str(db_a))
        engine_a2 = local_store.get_engine(str(db_a))
        engine_b = local_store.get_engine(str(db_b))

        assert engine_a1 is engine_a2
        assert engine_a1 is not engine_b
        assert db_a.exists()
        assert db_b.exists()
    finally:
        for engine in local_store._engines.values():
            engine.dispose()
        monkeypatch.setattr(local_store, "_engines", original_engines)
