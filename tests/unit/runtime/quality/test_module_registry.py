"""Tests for runtime/quality/module_registry.py central module registry."""

from __future__ import annotations

from fleet_rlm.runtime.quality.module_registry import (
    _REGISTRY,
    _reset_registry,
    get_module_spec,
    list_module_metadata,
    list_module_slugs,
)


def test_list_module_slugs_returns_known_modules() -> None:
    slugs = list_module_slugs()
    assert "reflect-and-revise" in slugs
    assert "context-selection" in slugs
    assert "decomposition" in slugs
    assert "repair" in slugs
    assert "verification" in slugs


def test_get_module_spec_known() -> None:
    spec = get_module_spec("reflect-and-revise")
    assert spec is not None
    assert spec.module_slug == "reflect-and-revise"
    assert spec.label == "Reflect & Revise"
    assert callable(spec.module_factory)
    assert callable(spec.row_converter)
    assert callable(spec.metric_builder)


def test_get_module_spec_unknown() -> None:
    assert get_module_spec("nonexistent-module") is None


def test_list_module_metadata_shape() -> None:
    metadata = list_module_metadata()
    assert len(metadata) >= 5
    for entry in metadata:
        assert "slug" in entry
        assert "label" in entry
        assert "program_spec" in entry
        assert "required_dataset_keys" in entry
        assert isinstance(entry["required_dataset_keys"], list)


def test_reset_registry_clears() -> None:
    # Ensure registry is populated first
    list_module_slugs()
    _reset_registry()
    assert len(_REGISTRY) == 0


def test_registry_repopulates_after_reset() -> None:
    list_module_slugs()
    _reset_registry()

    slugs = list_module_slugs()

    assert "reflect-and-revise" in slugs
    assert "context-selection" in slugs
