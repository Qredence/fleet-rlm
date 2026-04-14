"""Tests for dataset CRUD functions in local_store."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    """Point local_store at a fresh temporary SQLite database."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("FLEET_RLM_LOCAL_DB_URL", f"sqlite:///{db_path}")
    # Also isolate the dataset root
    ds_root = str(tmp_path / "datasets")
    monkeypatch.setenv("FLEET_RLM_DATASET_ROOT", ds_root)
    from fleet_rlm.integrations import local_store

    local_store._engines.clear()


def test_create_dataset():
    from fleet_rlm.integrations.local_store import create_dataset

    ds = create_dataset(
        name="test-ds",
        row_count=42,
        format="jsonl",
        uri="/fake/path/test-ds.jsonl",
        module_slug="qa",
    )
    assert ds.id is not None
    assert ds.name == "test-ds"
    assert ds.row_count == 42
    assert ds.format == "jsonl"
    assert ds.module_slug == "qa"
    assert ds.uri == "/fake/path/test-ds.jsonl"


def test_create_dataset_no_module():
    from fleet_rlm.integrations.local_store import create_dataset

    ds = create_dataset(
        name="plain",
        row_count=10,
        format="json",
        uri="/fake/path/plain.json",
    )
    assert ds.id is not None
    assert ds.module_slug is None


def test_list_datasets_empty():
    from fleet_rlm.integrations.local_store import list_datasets

    items, total = list_datasets()
    assert items == []
    assert total == 0


def test_list_datasets_returns_items():
    from fleet_rlm.integrations.local_store import create_dataset, list_datasets

    create_dataset(name="a", row_count=1, format="json", uri="/a.json")
    create_dataset(name="b", row_count=2, format="jsonl", uri="/b.jsonl")
    create_dataset(
        name="c", row_count=3, format="json", uri="/c.json", module_slug="qa"
    )

    items, total = list_datasets()
    assert total == 3
    assert len(items) == 3
    # Most recent first
    assert items[0].name == "c"


def test_list_datasets_filter_by_module():
    from fleet_rlm.integrations.local_store import create_dataset, list_datasets

    create_dataset(name="a", row_count=1, format="json", uri="/a.json")
    create_dataset(
        name="b", row_count=2, format="jsonl", uri="/b.jsonl", module_slug="qa"
    )

    items, total = list_datasets(module_slug="qa")
    assert total == 1
    assert items[0].name == "b"


def test_list_datasets_pagination():
    from fleet_rlm.integrations.local_store import create_dataset, list_datasets

    for i in range(5):
        create_dataset(name=f"ds-{i}", row_count=i, format="json", uri=f"/{i}.json")

    page1, total1 = list_datasets(limit=2, offset=0)
    assert total1 == 5
    assert len(page1) == 2

    page2, total2 = list_datasets(limit=2, offset=2)
    assert total2 == 5
    assert len(page2) == 2

    page3, total3 = list_datasets(limit=2, offset=4)
    assert total3 == 5
    assert len(page3) == 1


def test_get_dataset_found():
    from fleet_rlm.integrations.local_store import create_dataset, get_dataset

    ds = create_dataset(name="x", row_count=7, format="jsonl", uri="/x.jsonl")
    assert ds.id is not None
    fetched = get_dataset(ds.id)
    assert fetched is not None
    assert fetched.name == "x"
    assert fetched.row_count == 7


def test_get_dataset_not_found():
    from fleet_rlm.integrations.local_store import get_dataset

    assert get_dataset(99999) is None


def test_get_dataset_root_creates_dir(tmp_path, monkeypatch):
    ds_root = str(tmp_path / "custom_root" / "datasets")
    monkeypatch.setenv("FLEET_RLM_DATASET_ROOT", ds_root)

    from fleet_rlm.integrations.local_store import get_dataset_root
    from pathlib import Path

    root = get_dataset_root()
    assert root == Path(ds_root).resolve()
    assert root.is_dir()


def test_get_dataset_root_default(tmp_path, monkeypatch):
    monkeypatch.delenv("FLEET_RLM_DATASET_ROOT", raising=False)

    from fleet_rlm.integrations.local_store import get_dataset_root

    root = get_dataset_root()
    assert root.name == "datasets"
    assert root.parent.name == ".data"
