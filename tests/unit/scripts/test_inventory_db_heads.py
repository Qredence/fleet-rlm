from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def inventory():
    path = Path(__file__).parents[3] / "scripts" / "inventory_db_heads.py"
    spec = importlib.util.spec_from_file_location("inventory_db_heads", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_target_label_rejects_secrets_and_paths(inventory) -> None:
    assert inventory.validate_target_label("prod-pg-01") == "prod-pg-01"
    for bad in ("", "has space", "has/secret", "postgres://x", "-lead", "a" * 65):
        with pytest.raises(inventory.InventoryError):
            inventory.validate_target_label(bad)


def test_compare_heads_match_additive_and_diverged(inventory) -> None:
    repo = ["01a087800002"]
    ancestors = {"019fdb010001", "019fe0010001", "01a087800001", "01a087800002"}
    matched = inventory.compare_heads(repo, list(repo), ancestors=ancestors)
    assert matched["matches_repo_heads"] is True
    assert matched["migration_strategy"] == "none_required"
    assert matched["rewrite_history"] is False

    behind = inventory.compare_heads(repo, ["019fdb010001"], ancestors=ancestors)
    assert behind["matches_repo_heads"] is False
    assert behind["missing_heads"] == ["01a087800002"]
    assert behind["migration_strategy"] == "additive_required"

    diverged = inventory.compare_heads(repo, ["deadbeef1234"], ancestors=ancestors)
    assert diverged["extra_heads"] == ["deadbeef1234"]
    assert diverged["migration_strategy"] == "manual_review"
    assert diverged["rewrite_history"] is False


def test_repo_heads_are_linear_single_head(inventory, tmp_path: Path) -> None:
    del tmp_path
    heads = inventory.repo_heads(Path(__file__).parents[3])
    assert heads == ["01a087800002"]


def test_receipt_is_content_free(inventory) -> None:
    receipt = inventory.build_receipt(
        target="deployed-pg-01",
        repo=["01a087800002"],
        observed={
            "backend": "postgresql",
            "server_version_num": "170011",
            "server_version": None,
            "alembic_heads": ["01a087800002"],
        },
        git_sha="abc123",
        dirty=False,
        ancestors={"019fe0010001", "01a087800001", "01a087800002"},
    )
    assert receipt["schema"] == "fleet.db-head-inventory/v1"
    assert receipt["comparison"]["migration_strategy"] == "none_required"
    payload = json.dumps(receipt, sort_keys=True)
    assert "postgres://" not in payload
    assert "password" not in payload.lower()
    assert "SELECT" not in payload


def test_main_refuses_to_replace_receipt(inventory, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FLEET_TEST_INV_URL", "sqlite:///:memory:")
    receipt = tmp_path / "inventory.json"
    receipt.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(inventory, "repo_heads", lambda _root: ["01a087800002"])
    code = inventory.main(["--receipt", str(receipt), "--target", "local", "--database-url-env", "FLEET_TEST_INV_URL"])
    assert code == 2
