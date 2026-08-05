"""RLMFactory, model bundle, and native RLM Options."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def host_echo(value: str = "ok") -> str:
    """Host tool with a valid Python identifier name."""
    return value


def test_model_bundle_keeps_root_and_sub_roles_distinct() -> None:
    from fleet_rlm.rlm.model_bundle import RLMModelBundle

    root = MagicMock(name="root_lm")
    sub = MagicMock(name="sub_lm")
    bundle = RLMModelBundle(root_lm=root, sub_lm=sub)

    assert bundle.root_lm is root
    assert bundle.sub_lm is sub
    assert bundle.root_lm is not bundle.sub_lm
    assert bundle.utility_lm is None


def test_model_bundle_rejects_missing_roles() -> None:
    from fleet_rlm.rlm.errors import RLMModelBundleError
    from fleet_rlm.rlm.model_bundle import RLMModelBundle

    with pytest.raises(RLMModelBundleError):
        RLMModelBundle(root_lm=None, sub_lm=MagicMock())  # type: ignore[arg-type]
    with pytest.raises(RLMModelBundleError):
        RLMModelBundle(root_lm=MagicMock(), sub_lm=None)  # type: ignore[arg-type]


def test_invalid_options_fail_before_construction() -> None:
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.errors import RLMConfigError

    with pytest.raises(RLMConfigError):
        RLMOptions(max_iterations=0)
    with pytest.raises(RLMConfigError):
        RLMOptions(max_llm_calls=-1)
    with pytest.raises(RLMConfigError):
        RLMOptions(max_output_chars=0)


def test_factory_passes_explicit_constructor_kwargs() -> None:
    import dspy

    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.factory import RLMFactory
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.rlm.signature import FleetRLMSignature

    root = MagicMock(name="root_lm")
    sub = MagicMock(name="sub_lm")
    options = RLMOptions(
        max_iterations=7,
        max_llm_calls=11,
        max_output_chars=2048,
    )
    models = RLMModelBundle(root_lm=root, sub_lm=sub)

    rlm = RLMFactory().create(
        models=models,
        options=options,
        tools=[host_echo],
    )

    assert isinstance(rlm, dspy.RLM)
    assert type(rlm) is dspy.RLM
    assert rlm.verbose is True
    assert not hasattr(rlm, "bind_observer")
    assert rlm.max_iters == 7
    assert rlm.max_llm_calls == 11
    assert rlm.max_output_chars == 2048
    assert rlm.sub_lm is sub
    assert not hasattr(rlm, "_interpreter")
    assert "host_echo" in rlm.tools
    assert rlm.signature is FleetRLMSignature
    # Root is owned by the bundle for the runner; factory does not hide it in RLM ctor.
    assert models.root_lm is root


def test_each_factory_call_returns_new_rlm_instance() -> None:
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.factory import RLMFactory
    from fleet_rlm.rlm.model_bundle import RLMModelBundle

    factory = RLMFactory()
    models = RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock())
    options = RLMOptions()
    first = factory.create(models=models, options=options)
    second = factory.create(models=models, options=options)

    assert first is not second


def test_factory_accepts_policy_controlled_host_verbosity() -> None:
    from fleet_rlm.rlm.dspy_contract import RLMOptions
    from fleet_rlm.rlm.factory import RLMFactory
    from fleet_rlm.rlm.model_bundle import RLMModelBundle

    rlm = RLMFactory(verbose=False).create(
        models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
        options=RLMOptions(),
    )

    assert rlm.verbose is False


def test_dspy_contract_is_only_native_dspy_rlm_call_site_in_rlm_package() -> None:
    """Static guard: only dspy_contract.py may directly construct native dspy.RLM."""
    import ast
    from pathlib import Path

    rlm_dir = Path(__file__).resolve().parents[3] / "src" / "fleet_rlm" / "rlm"
    offenders: list[str] = []
    for path in sorted(rlm_dir.glob("*.py")):
        if path.name == "dspy_contract.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "RLM":
                offenders.append(path.name)
            if isinstance(func, ast.Name) and func.id == "RLM":
                offenders.append(path.name)
    assert offenders == [], f"dspy.RLM constructed outside dspy_contract: {offenders}"


def test_dspy_primitives_imports_are_confined_to_interpreter_contract() -> None:
    """Static guard: only dspy_interpreter_contract.py may import dspy.primitives."""
    import ast
    from pathlib import Path

    src_root = Path(__file__).resolve().parents[3] / "src" / "fleet_rlm"
    allowed = {"rlm/dspy_interpreter_contract.py"}
    offenders: list[str] = []
    for path in sorted(src_root.rglob("*.py")):
        rel = path.relative_to(src_root).as_posix()
        if rel in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("dspy.primitives"):
                    offenders.append(rel)
                    break
                if node.module == "dspy" and any(alias.name == "primitives" for alias in node.names):
                    offenders.append(rel)
                    break
            if isinstance(node, ast.Import) and any(
                alias.name == "dspy.primitives" or alias.name.startswith("dspy.primitives.") for alias in node.names
            ):
                offenders.append(rel)
                break
    assert offenders == [], f"dspy.primitives imported outside interpreter contract: {offenders}"
