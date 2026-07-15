"""impl-01: RLMFactory, model bundle, and budgets."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest


class _FakeInterpreter:
    """Minimal CodeInterpreter-compatible stub (no network)."""

    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., str]] = {}

    @property
    def tools(self) -> dict[str, Callable[..., str]]:
        return self._tools

    def start(self) -> None:
        return None

    def execute(self, code: str, variables: dict[str, Any] | None = None) -> str:
        return ""

    def shutdown(self) -> None:
        return None


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


def test_invalid_budget_fails_before_construction() -> None:
    from fleet_rlm.rlm.budgets import RunBudget
    from fleet_rlm.rlm.errors import RunBudgetError

    with pytest.raises(RunBudgetError):
        RunBudget(max_iterations=0)
    with pytest.raises(RunBudgetError):
        RunBudget(max_llm_calls=-1)
    with pytest.raises(RunBudgetError):
        RunBudget(max_output_chars=0)


def test_factory_passes_explicit_constructor_kwargs() -> None:
    import dspy

    from fleet_rlm.rlm.budgets import RunBudget
    from fleet_rlm.rlm.factory import RLMFactory
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.rlm.signature import FleetRLMSignature

    root = MagicMock(name="root_lm")
    sub = MagicMock(name="sub_lm")
    interpreter = _FakeInterpreter()
    budget = RunBudget(
        max_iterations=7,
        max_llm_calls=11,
        max_output_chars=2048,
        max_tool_calls=5,
        max_sub_lm_concurrency=3,
    )
    models = RLMModelBundle(root_lm=root, sub_lm=sub)

    rlm = RLMFactory().create(
        models=models,
        budget=budget,
        interpreter=interpreter,
        tools=[host_echo],
    )

    assert isinstance(rlm, dspy.RLM)
    assert rlm.max_iterations == 7
    assert rlm.max_llm_calls == 11
    assert rlm.max_output_chars == 2048
    assert rlm._fleet_max_tool_calls == 5  # noqa: SLF001 - factory budget contract
    assert rlm._fleet_max_sub_lm_concurrency == 3  # noqa: SLF001 - factory budget contract
    assert rlm.sub_lm is sub
    assert rlm._interpreter is interpreter
    assert "host_echo" in rlm.tools
    assert rlm.signature is FleetRLMSignature
    # Root is owned by the bundle for the runner; factory does not hide it in RLM ctor.
    assert models.root_lm is root


def test_each_factory_call_returns_new_rlm_instance() -> None:
    from fleet_rlm.rlm.budgets import RunBudget
    from fleet_rlm.rlm.factory import RLMFactory
    from fleet_rlm.rlm.model_bundle import RLMModelBundle

    factory = RLMFactory()
    models = RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock())
    budget = RunBudget()
    interpreter = _FakeInterpreter()

    first = factory.create(models=models, budget=budget, interpreter=interpreter)
    second = factory.create(models=models, budget=budget, interpreter=interpreter)

    assert first is not second


def test_compat_is_only_native_dspy_rlm_call_site_in_rlm_package() -> None:
    """Static guard: only compat.py may directly construct native dspy.RLM."""
    import ast
    from pathlib import Path

    rlm_dir = Path(__file__).resolve().parents[3] / "src" / "fleet_rlm" / "rlm"
    offenders: list[str] = []
    for path in sorted(rlm_dir.glob("*.py")):
        if path.name == "compat.py":
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
    assert offenders == [], f"dspy.RLM constructed outside compat: {offenders}"
