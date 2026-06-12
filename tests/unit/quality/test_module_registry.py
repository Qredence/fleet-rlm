from __future__ import annotations

from fleet_rlm.quality import module_registry
from fleet_rlm.quality.module_registry import ModuleOptimizationSpec


def _dummy_factory() -> None:
    return None


def _dummy_converter(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return rows


def _dummy_metric() -> None:
    return None


def _make_spec(slug: str, label: str) -> ModuleOptimizationSpec:
    return ModuleOptimizationSpec(
        module_slug=slug,
        label=label,
        program_spec=f"{slug}:Program",
        artifact_filename=f"{slug}.json",
        input_keys=["prompt"],
        required_dataset_keys=["prompt", "answer"],
        module_factory=_dummy_factory,
        row_converter=_dummy_converter,
        metric_builder=_dummy_metric,
        description=f"{label} description",
    )


def test_get_module_spec_loads_registered_entrypoint() -> None:
    module_registry._reset_registry()

    spec = module_registry.get_module_spec("longcot-reasoner")

    assert spec is not None
    assert spec.module_slug == "longcot-reasoner"
    assert spec.label == "LongCoT QA Reasoner"
    assert spec.input_keys == ["question"]
    assert spec.required_dataset_keys == ["question", "answer"]


def test_register_module_overrides_lookup(monkeypatch) -> None:
    monkeypatch.setattr(module_registry, "_MODULE_ENTRYPOINTS", ())
    module_registry._reset_registry()
    spec = _make_spec("custom-module", "Custom Module")

    module_registry.register_module(spec)

    assert module_registry.get_module_spec("custom-module") == spec


def test_listing_helpers_return_sorted_modules(monkeypatch) -> None:
    monkeypatch.setattr(module_registry, "_MODULE_ENTRYPOINTS", ())
    module_registry._reset_registry()
    module_registry.register_module(_make_spec("zeta-module", "Zeta"))
    module_registry.register_module(_make_spec("alpha-module", "Alpha"))

    assert module_registry.list_module_slugs() == ["alpha-module", "zeta-module"]
    assert module_registry.list_module_metadata() == [
        {
            "slug": "alpha-module",
            "label": "Alpha",
            "description": "Alpha description",
            "program_spec": "alpha-module:Program",
            "runtime_module_name": None,
            "signature_class_name": None,
            "input_keys": ["prompt"],
            "output_keys": [],
            "optimization_target_kind": "custom",
            "required_dataset_keys": ["prompt", "answer"],
        },
        {
            "slug": "zeta-module",
            "label": "Zeta",
            "description": "Zeta description",
            "program_spec": "zeta-module:Program",
            "runtime_module_name": None,
            "signature_class_name": None,
            "input_keys": ["prompt"],
            "output_keys": [],
            "optimization_target_kind": "custom",
            "required_dataset_keys": ["prompt", "answer"],
        },
    ]


def test_runtime_signature_targets_are_registered() -> None:
    module_registry._reset_registry()

    slugs = module_registry.list_module_slugs()
    metadata = {item["slug"]: item for item in module_registry.list_module_metadata()}

    assert "longcot-reasoner" in slugs
    assert {
        "summarize-long-document",
        "extract-from-logs",
        "triage-incident-logs",
        "plan-code-change",
        "clarification-questions",
        "memory-action-intent",
    }.issubset(slugs)
    plan_code_change = metadata["plan-code-change"]
    assert plan_code_change["runtime_module_name"] == "plan_code_change"
    assert plan_code_change["signature_class_name"] == "CodeChangePlan"
    assert plan_code_change["input_keys"] == ["task", "repo_context", "constraints"]
    assert plan_code_change["output_keys"] == ["plan_steps", "files_to_touch", "validation_commands", "risks"]
    assert plan_code_change["optimization_target_kind"] == "runtime-signature"
