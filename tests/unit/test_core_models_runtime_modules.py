"""Unit tests for the runtime-module registry and builders.

NOTE: All imports of registry/builders are intentionally deferred inside
each test function to avoid triggering the circular import chain:
  core.agent.chat_agent → core.models.builders
  → core.agent.signatures → core.agent

Verifies:
- RUNTIME_MODULE_REGISTRY contains all expected names
- build_runtime_module returns a dspy.Module instance
- build_runtime_module raises ValueError for unknown names
- build_recursive_subquery_rlm delegates to the correct signature
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import dspy
import pytest


EXPECTED_MODULE_NAMES = frozenset(
    {
        "summarize_long_document",
        "extract_from_logs",
        "grounded_answer",
        "triage_incident_logs",
        "plan_code_change",
        "propose_core_memory_update",
        "memory_tree",
        "memory_action_intent",
        "memory_structure_audit",
        "memory_structure_migration_plan",
        "clarification_questions",
    }
)


@pytest.fixture()
def fake_interpreter() -> MagicMock:
    return MagicMock(name="FakeInterpreter")


# ---------------------------------------------------------------------------
# Registry completeness
# ---------------------------------------------------------------------------


def test_registry_contains_all_expected_names():
    from fleet_rlm.runtime.models.registry import RUNTIME_MODULE_REGISTRY

    assert EXPECTED_MODULE_NAMES <= frozenset(RUNTIME_MODULE_REGISTRY)


def test_runtime_module_names_frozenset_matches_registry():
    from fleet_rlm.runtime.models.registry import (
        RUNTIME_MODULE_NAMES,
        RUNTIME_MODULE_REGISTRY,
    )

    assert RUNTIME_MODULE_NAMES == frozenset(RUNTIME_MODULE_REGISTRY)


def test_each_registry_entry_has_signature_and_classname():
    from fleet_rlm.runtime.models.registry import RUNTIME_MODULE_REGISTRY

    for name, defn in RUNTIME_MODULE_REGISTRY.items():
        assert defn.signature is not None, f"No signature for {name}"
        assert isinstance(defn.class_name, str) and defn.class_name, (
            f"No class_name for {name}"
        )
        assert issubclass(defn.signature, dspy.Signature), (
            f"{name} signature is not a dspy.Signature subclass"
        )


# ---------------------------------------------------------------------------
# build_runtime_module
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(EXPECTED_MODULE_NAMES))
def test_build_runtime_module_returns_module_instance(name: str, fake_interpreter: Any):
    """Every registered name should produce a dspy.Module instance."""
    from fleet_rlm.runtime.models.registry import build_runtime_module

    with patch("fleet_rlm.runtime.models.builders.dspy.RLM", MagicMock()):
        module = build_runtime_module(
            name,
            interpreter=fake_interpreter,
            max_iterations=10,
            max_llm_calls=20,
            verbose=False,
        )

    assert isinstance(module, dspy.Module)


def test_build_runtime_module_raises_for_unknown_name(fake_interpreter: Any):
    from fleet_rlm.runtime.models.registry import build_runtime_module

    with pytest.raises(ValueError, match="Unknown runtime module: nonexistent"):
        build_runtime_module(
            "nonexistent",
            interpreter=fake_interpreter,
            max_iterations=10,
            max_llm_calls=20,
            verbose=False,
        )


def test_build_runtime_module_each_call_is_new_instance(fake_interpreter: Any):
    """build_runtime_module always constructs a new instance (no shared state)."""
    from fleet_rlm.runtime.models.registry import build_runtime_module

    with patch("fleet_rlm.runtime.models.builders.dspy.RLM", MagicMock()):
        m1 = build_runtime_module(
            "grounded_answer",
            interpreter=fake_interpreter,
            max_iterations=10,
            max_llm_calls=20,
            verbose=False,
        )
        m2 = build_runtime_module(
            "grounded_answer",
            interpreter=fake_interpreter,
            max_iterations=10,
            max_llm_calls=20,
            verbose=False,
        )

    assert m1 is not m2


def test_get_or_build_runtime_module_reuses_cached_instance(fake_interpreter: Any):
    from fleet_rlm.runtime.models.builders import build_runtime_module_config
    from fleet_rlm.runtime.models.registry import get_or_build_runtime_module

    created: list[str] = []
    fake_module = MagicMock(name="RuntimeModule")

    def _fake_build_runtime_module(
        name: str,
        *,
        interpreter: Any,
        max_iterations: int,
        max_llm_calls: int,
        verbose: bool,
    ) -> MagicMock:
        created.append(name)
        assert interpreter is fake_interpreter
        assert max_iterations == 10
        assert max_llm_calls == 20
        assert verbose is False
        return fake_module

    with patch(
        "fleet_rlm.runtime.models.registry.build_runtime_module",
        side_effect=_fake_build_runtime_module,
    ):
        cache: dict[str, dspy.Module] = {}
        config = build_runtime_module_config(
            interpreter=fake_interpreter,
            max_iterations=10,
            max_llm_calls=20,
            verbose=False,
        )

        first = get_or_build_runtime_module(
            cache,
            "grounded_answer",
            config=config,
        )
        second = get_or_build_runtime_module(
            cache,
            "grounded_answer",
            config=config,
        )

    assert first is second is fake_module
    assert created == ["grounded_answer"]


# ---------------------------------------------------------------------------
# build_recursive_subquery_rlm
# ---------------------------------------------------------------------------


def test_build_recursive_subquery_rlm_uses_correct_signature(fake_interpreter: Any):
    """Verify that recursive subquery RLM uses RecursiveSubQuerySignature."""
    from fleet_rlm.runtime.agent.signatures import RecursiveSubQuerySignature
    from fleet_rlm.runtime.models.builders import build_recursive_subquery_rlm

    captured: dict[str, Any] = {}

    def _fake_rlm(**kwargs: Any) -> MagicMock:
        captured.update(kwargs)
        return MagicMock()

    with patch("fleet_rlm.runtime.models.builders.dspy.RLM", _fake_rlm):
        build_recursive_subquery_rlm(
            interpreter=fake_interpreter,
            max_iterations=5,
            max_llm_calls=10,
            verbose=True,
        )

    assert captured.get("signature") is RecursiveSubQuerySignature
    assert captured["interpreter"] is fake_interpreter
    assert captured["max_iterations"] == 5
    assert captured["verbose"] is True


def test_grounded_answer_synthesis_module_chunks_document_before_rlm(
    fake_interpreter: Any,
):
    from fleet_rlm.runtime.models.builders import GroundedAnswerSynthesisModule

    captured: dict[str, Any] = {}

    class _FakeGroundedAnswerRLM:
        def __call__(self, **kwargs: Any) -> dspy.Prediction:
            captured.update(kwargs)
            return dspy.Prediction(
                answer="ok",
                citations=[],
                confidence=75,
                coverage_notes="done",
            )

    with patch(
        "fleet_rlm.runtime.models.builders.create_runtime_rlm",
        return_value=_FakeGroundedAnswerRLM(),
    ):
        module = GroundedAnswerSynthesisModule(
            interpreter=fake_interpreter,
            max_iterations=10,
            max_llm_calls=20,
            verbose=False,
        )

    prediction = module.forward(
        document="# Header\nline1\nline2",
        query="What happened?",
        chunk_strategy="headers",
        max_chunks=4,
    )

    assert isinstance(prediction, dspy.Prediction)
    assert captured["query"] == "What happened?"
    assert captured["response_style"] == "concise"
    assert captured["evidence_chunks"]


def test_memory_migration_planning_module_composes_audit_then_migration(
    fake_interpreter: Any,
):
    from fleet_rlm.runtime.models.builders import MemoryMigrationPlanningModule

    captured: dict[str, Any] = {}

    class _FakeAuditModule:
        def __call__(self, **kwargs: Any) -> SimpleNamespace:
            captured["audit_kwargs"] = kwargs
            return SimpleNamespace(issues=["flatten archive layout"])

    class _FakeMigrationRLM:
        def __call__(self, **kwargs: Any) -> dspy.Prediction:
            captured["migration_kwargs"] = kwargs
            return dspy.Prediction(
                operations=[],
                rollback_steps=[],
                verification_checks=[],
                estimated_risk="low",
            )

    with (
        patch(
            "fleet_rlm.runtime.models.builders.MemoryStructureAuditPlanningModule",
            return_value=_FakeAuditModule(),
        ),
        patch(
            "fleet_rlm.runtime.models.builders.create_runtime_rlm",
            return_value=_FakeMigrationRLM(),
        ),
    ):
        module = MemoryMigrationPlanningModule(
            interpreter=fake_interpreter,
            max_iterations=10,
            max_llm_calls=20,
            verbose=False,
        )

    prediction = module.forward()

    assert isinstance(prediction, dspy.Prediction)
    assert captured["audit_kwargs"]["root_path"] == "/data/memory"
    assert captured["migration_kwargs"] == {
        "audit_findings": ["flatten archive layout"],
        "approved_constraints": "No destructive operation without explicit confirmation and rollback.",
    }
