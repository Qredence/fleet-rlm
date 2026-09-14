"""Contracts for sealed Phase 6 promotion and rollback identities."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import phase6_promotion as promotion


def _files(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = tmp_path / "fleet.toml"
    lock = tmp_path / "uv.lock"
    manifest = tmp_path / "artifact-manifest.json"
    images = tmp_path / "images.json"
    config.write_text("[defaults]\n", encoding="utf-8")
    lock.write_text("lock", encoding="utf-8")
    manifest.write_text("{}", encoding="utf-8")
    images.write_text(json.dumps({"session": "fleet-session-v1", "semantic_child": "fleet-child-v1"}), encoding="utf-8")
    return config, lock, manifest, images


def _bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, revision: str = "a" * 40) -> dict[str, object]:
    config, lock, manifest, images = _files(tmp_path)
    monkeypatch.setattr(promotion, "_git_output", lambda *args: "" if args[0] == "status" else revision)
    return promotion.build_bundle(
        profile="daytona-recursive",
        config=config,
        lock=lock,
        artifact_manifest=manifest,
        images=images,
        database_head="20260914_01",
        dataset_digest="d" * 64,
        scorer_digest="e" * 64,
    )


def test_build_bundle_is_bound_to_clean_head_and_exact_file_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path, monkeypatch)

    assert bundle["schema"] == promotion.SCHEMA
    assert bundle["revision"] == "a" * 40
    assert bundle["config_sha256"] == hashlib.sha256((tmp_path / "fleet.toml").read_bytes()).hexdigest()
    assert promotion.validate_bundle(bundle) == bundle


def test_build_bundle_rejects_dirty_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, lock, manifest, images = _files(tmp_path)
    monkeypatch.setattr(
        promotion, "_git_output", lambda *args: " M src/fleet_rlm/runtime.py" if args[0] == "status" else "a" * 40
    )

    with pytest.raises(promotion.PromotionBundleError, match="must be clean"):
        promotion.build_bundle(
            profile="daytona-recursive",
            config=config,
            lock=lock,
            artifact_manifest=manifest,
            images=images,
            database_head="20260914_01",
            dataset_digest="d" * 64,
            scorer_digest="e" * 64,
        )


def test_rollback_pair_requires_distinct_revisions_and_same_database_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = _bundle(tmp_path / "baseline", monkeypatch, revision="a" * 40)
    candidate = _bundle(tmp_path / "candidate", monkeypatch, revision="b" * 40)

    pair = promotion.validate_rollback_pair(baseline, candidate)

    assert pair["database_head"] == "20260914_01"
    candidate["database_head"] = "20260915_01"
    with pytest.raises(promotion.PromotionBundleError, match="bundle digest"):
        promotion.validate_rollback_pair(baseline, candidate)


def test_receipts_are_write_once(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    promotion._write_once(path, {"ok": True})

    with pytest.raises(promotion.PromotionBundleError, match="overwrite"):
        promotion._write_once(path, {"ok": False})
