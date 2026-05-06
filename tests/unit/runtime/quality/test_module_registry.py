"""Tests for runtime/quality/module_registry.py central module registry."""

from __future__ import annotations

import sys

from fleet_rlm.quality.module_registry import (
    _REGISTRY,
    ModuleOptimizationSpec,
    _reset_registry,
    get_module_spec,
    list_module_metadata,
    list_module_slugs,
    register_module,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _dummy_factory() -> None:
    return None


def _dummy_converter(rows: list[dict]) -> list[dict]:
    return rows


def _dummy_metric() -> None:
    return None


def _make_spec(slug: str, label: str) -> ModuleOptimizationSpec:
    return ModuleOptimizationSpec(
        module_slug=slug,
        label=label,
        program_spec=f"{slug} -> answer",
        artifact_filename=f"{slug}.json",
        input_keys=["user_request"],
        required_dataset_keys=["user_request", "answer"],
        module_factory=_dummy_factory,
        row_converter=_dummy_converter,
        metric_builder=_dummy_metric,
    )


# ── Tests ────────────────────────────────────────────────────────────


def test_list_module_slugs_returns_known_modules() -> None:
    _reset_registry()
    register_module(_make_spec("test-module-a", "Test A"))
    register_module(_make_spec("test-module-b", "Test B"))
    slugs = list_module_slugs()
    assert "test-module-a" in slugs
    assert "test-module-b" in slugs


def test_get_module_spec_known() -> None:
    _reset_registry()
    register_module(_make_spec("test-module-known", "Known Module"))
    spec = get_module_spec("test-module-known")
    assert spec is not None
    assert spec.module_slug == "test-module-known"
    assert spec.label == "Known Module"
    assert callable(spec.module_factory)
    assert callable(spec.row_converter)
    assert callable(spec.metric_builder)


def test_get_module_spec_unknown() -> None:
    _reset_registry()
    assert get_module_spec("nonexistent-module") is None


def test_list_module_metadata_shape() -> None:
    _reset_registry()
    register_module(_make_spec("meta-module-a", "Meta A"))
    register_module(_make_spec("meta-module-b", "Meta B"))
    metadata = list_module_metadata()
    assert len(metadata) >= 2
    for entry in metadata:
        assert "slug" in entry
        assert "label" in entry
        assert "program_spec" in entry
        assert "required_dataset_keys" in entry
        assert isinstance(entry["required_dataset_keys"], list)


def test_reset_registry_clears() -> None:
    _reset_registry()
    register_module(_make_spec("temp-module", "Temp"))
    list_module_slugs()
    _reset_registry()
    assert len(_REGISTRY) == 0


def test_registry_repopulates_after_reset() -> None:
    _reset_registry()
    register_module(_make_spec("repop-module", "Repop"))
    list_module_slugs()
    _reset_registry()

    register_module(_make_spec("repop-module", "Repop"))
    slugs = list_module_slugs()

    assert "repop-module" in slugs


def test_registry_lazy_loads_without_import_time_side_effects() -> None:
    """VAL-MOD-004: Importing module_registry does not load DSPy or entrypoints."""
    import importlib.util

    registry_name = "fleet_rlm.quality.module_registry"
    entrypoint = "fleet_rlm.quality.optimize_longcot"

    # Save original module and remove entrypoint so we can observe lazy loading
    original_module = sys.modules.get(registry_name)
    sys.modules.pop(entrypoint, None)

    # Import the registry module fresh in a clean namespace
    spec = importlib.util.find_spec(registry_name)
    assert spec is not None
    fresh_module = importlib.util.module_from_spec(spec)
    sys.modules[registry_name] = fresh_module
    spec.loader.exec_module(fresh_module)

    try:
        # _REGISTERED should be False after import (no eager loading)
        assert fresh_module._REGISTERED is False

        # The entrypoint should NOT be in sys.modules yet
        assert entrypoint not in sys.modules
    finally:
        # Restore the original module so subsequent tests use the real registry
        if original_module is not None:
            sys.modules[registry_name] = original_module
        else:
            sys.modules.pop(registry_name, None)
