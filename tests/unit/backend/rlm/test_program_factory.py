"""Behavior contracts for native DSPy program construction."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import dspy
import pytest

from fleet_rlm.rlm.program import RLMOptions
from fleet_rlm.rlm.result import RLMConfigError
from tests.support.native_rlm import build_native_rlm_for_test


class _CopyableLM:
    """Minimal copyable role-LM double: isolates history and records call kwargs."""

    def __init__(self) -> None:
        self.history: list[object] = []
        self.calls: list[dict[str, object]] = []

    def copy(self) -> _CopyableLM:
        return _CopyableLM()

    def forward(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        return object()


def host_echo(value: str = "ok") -> str:
    """Host tool with a valid Python identifier name."""
    return value


def test_model_bundle_keeps_root_and_sub_roles_distinct() -> None:
    from fleet_rlm.rlm.program import RLMModelBundle

    root = MagicMock(name="root_lm")
    sub = MagicMock(name="sub_lm")
    bundle = RLMModelBundle(root_lm=root, sub_lm=sub)

    assert bundle.root_lm is root
    assert bundle.sub_lm is sub
    assert bundle.root_lm is not bundle.sub_lm
    assert bundle.utility_lm is None


def test_model_bundle_rejects_missing_roles() -> None:
    from fleet_rlm.rlm.program import RLMModelBundle
    from fleet_rlm.rlm.result import RLMModelBundleError

    with pytest.raises(RLMModelBundleError):
        RLMModelBundle(root_lm=None, sub_lm=MagicMock())  # type: ignore[arg-type]
    with pytest.raises(RLMModelBundleError):
        RLMModelBundle(root_lm=MagicMock(), sub_lm=None)  # type: ignore[arg-type]


def test_model_bundle_forks_isolated_child_lms() -> None:
    from fleet_rlm.rlm.program import RLMModelBundle

    root = _CopyableLM()
    sub = _CopyableLM()
    bundle = RLMModelBundle(root, sub)

    first = bundle.fork_for_child()
    second = bundle.fork_for_child()

    assert first.root_lm is not root
    assert first.sub_lm is not sub
    assert first.root_lm is not second.root_lm
    assert first.sub_lm is not second.sub_lm
    assert first.root_lm.history is not second.root_lm.history
    # Child copies may never consume the Turn's root-only finalization capacity.
    assert first.root_lm._fleet_can_finalize is False
    assert first.sub_lm._fleet_can_finalize is False
    # Forking never marks the shared role templates.
    assert not hasattr(root, "_fleet_can_finalize")
    assert not hasattr(sub, "_fleet_can_finalize")


@pytest.mark.asyncio
async def test_turn_bound_lm_preserves_dspy_sync_async_usage_and_callbacks():
    from collections.abc import AsyncIterator, Iterator
    from typing import Any

    from dspy.clients.engines.base import validate_request
    from dspy.lm15 import Request, Response, response_from_openai_chat, response_to_events
    from dspy.utils.callback import BaseCallback

    from fleet_rlm.rlm.program import RLMModelBundle

    class Callback(BaseCallback):
        def __init__(self):
            self.starts = []

        def on_lm_start(self, call_id, instance, inputs):
            """
            Record the model associated with a language-model invocation.

            Parameters:
                instance: The language-model instance whose model name is recorded.
            """
            del call_id, inputs
            self.starts.append(instance.model)

    class OkEngine:
        """Scripted engine returning one fixed ``ok`` completion per call."""

        def complete(self, request: Request) -> Response:
            validate_request(request)
            return response_from_openai_chat(
                {
                    "model": "test/script",
                    "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                }
            )

        def stream(self, request: Request) -> Iterator[Any]:
            return response_to_events(self.complete(request))

        def close(self) -> None:
            pass

    class AsyncOkEngine:
        """Async counterpart of :class:`OkEngine` over the same completion."""

        def __init__(self, sync: OkEngine) -> None:
            self.sync = sync

        async def complete(self, request: Request) -> Response:
            return self.sync.complete(request)

        async def stream(self, request: Request) -> AsyncIterator[Any]:
            for event in self.sync.stream(request):
                yield event

        async def aclose(self) -> None:
            pass

    sync_engine = OkEngine()
    callback = Callback()
    source = dspy.LM(
        "test/script",
        model_type="chat",
        cache=False,
        engine=sync_engine,
        async_engine=AsyncOkEngine(sync_engine),
        callbacks=[callback],
    )
    bound = RLMModelBundle(source, source).bind_turn().root_lm
    assert bound("sync") == ["ok"]
    assert await bound.acall("async") == ["ok"]
    assert len(bound.history) == 2
    assert bound.history[-1]["usage"]["total_tokens"] == 3
    assert source.history == []
    assert callback.starts == ["test/script", "test/script"]


def test_turn_binding_isolates_role_copies_and_marks_finalization() -> None:
    from fleet_rlm.rlm.budget import TurnBudget
    from fleet_rlm.rlm.program import RLMModelBundle

    root = _CopyableLM()
    sub = _CopyableLM()
    source = RLMModelBundle(root, sub)
    budget = TurnBudget(deadline=None)
    bound = source.bind_turn(budget=budget)

    bound.root_lm.forward(prompt="root")
    bound.sub_lm.forward(prompt="sub")

    assert bound is not source
    assert bound.root_lm is not root
    assert bound.sub_lm is not sub
    # The provider call happens on the Turn copy, never on the shared template.
    assert root.calls == []
    assert sub.calls == []
    # Only the Turn root may consume shared finalization capacity.
    assert bound.root_lm._fleet_can_finalize is True
    assert bound.sub_lm._fleet_can_finalize is False
    assert bound.budget is budget
    assert source.budget is None


def test_turn_binding_without_budget_inherits_the_source_budget() -> None:
    from fleet_rlm.rlm.budget import TurnBudget
    from fleet_rlm.rlm.program import RLMModelBundle

    budget = TurnBudget(deadline=None)
    source = RLMModelBundle(_CopyableLM(), _CopyableLM(), budget=budget)

    assert source.bind_turn().budget is budget


def test_sequential_and_concurrent_turn_bindings_return_fresh_copies() -> None:
    from fleet_rlm.rlm.program import RLMModelBundle

    source = RLMModelBundle(_CopyableLM(), _CopyableLM())

    def call(_index: int) -> RLMModelBundle:
        return source.bind_turn()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(call, (0, 1)))

    assert first.root_lm is not second.root_lm
    assert first.sub_lm is not second.sub_lm
    assert first.root_lm.history is not second.root_lm.history
    # Binding a Turn never accumulates state on the shared role templates.
    assert not hasattr(source.root_lm, "_fleet_can_finalize")
    assert not hasattr(source.sub_lm, "_fleet_can_finalize")


def test_native_builder_passes_explicit_constructor_kwargs() -> None:
    import dspy

    from fleet_rlm.rlm.program import FleetRLMSignature, RLMModelBundle, RLMOptions

    root = MagicMock(name="root_lm")
    sub = MagicMock(name="sub_lm")
    options = RLMOptions(max_iters=7, max_llm_calls=11, max_output_chars=2048)
    models = RLMModelBundle(root_lm=root, sub_lm=sub)

    rlm = build_native_rlm_for_test(options=options, tools=[host_echo], sub_lm=models.sub_lm)

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
    assert models.root_lm is root


def test_each_native_builder_call_returns_new_rlm_instance() -> None:
    from fleet_rlm.rlm.program import RLMOptions

    first = build_native_rlm_for_test(options=RLMOptions())
    second = build_native_rlm_for_test(options=RLMOptions())

    assert first is not second


def test_native_builder_accepts_policy_controlled_host_verbosity() -> None:
    from fleet_rlm.rlm.program import RLMOptions

    rlm = build_native_rlm_for_test(options=RLMOptions(), verbose=False)

    assert rlm.verbose is False


def test_program_is_only_native_dspy_rlm_call_site_in_rlm_package() -> None:
    """Static guard: program.py is the sole native dspy.RLM construction owner."""
    import ast
    from pathlib import Path

    rlm_dir = Path(__file__).resolve().parents[4] / "src" / "fleet_rlm" / "rlm"
    assert (rlm_dir / "program.py").is_file()
    offenders: list[str] = []
    for path in sorted(rlm_dir.glob("*.py")):
        if path.name == "program.py":
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
    assert offenders == [], f"dspy.RLM constructed outside program.py: {offenders}"


def test_dspy_primitives_imports_are_confined_to_interpreter_contract() -> None:
    """No production module should depend on DSPy's private primitives package."""
    import ast
    from pathlib import Path

    src_root = Path(__file__).resolve().parents[4] / "src" / "fleet_rlm"
    allowed: set[str] = set()
    assert (src_root / "rlm" / "program.py").is_file()
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


def test_dspy_public_types_do_not_use_internal_module_paths() -> None:
    """Public DSPy types are imported from the package API."""
    import ast
    from pathlib import Path

    src_root = Path(__file__).resolve().parents[4] / "src" / "fleet_rlm"
    allowed: set[str] = set()
    assert (src_root / "rlm" / "program.py").is_file()
    offenders: list[str] = []
    for path in sorted(src_root.rglob("*.py")):
        rel = path.relative_to(src_root).as_posix()
        if rel in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and (
                    node.module.startswith("dspy.primitives")
                    or node.module.startswith("dspy.predict")
                    or node.module.startswith("dspy.adapters")
                    or node.module.startswith("dspy.clients")
                    or node.module.startswith("dspy.signatures")
                ):
                    offenders.append(f"{rel}: {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if (
                        alias.name.startswith("dspy.primitives")
                        or alias.name.startswith("dspy.predict")
                        or alias.name.startswith("dspy.adapters")
                        or alias.name.startswith("dspy.clients")
                        or alias.name.startswith("dspy.signatures")
                    ):
                        offenders.append(f"{rel}: {alias.name}")
    assert offenders == [], f"Private DSPy imports found outside direct public imports: {offenders}"


@pytest.mark.parametrize(
    ("field", "value"),
    [("max_iters", 0), ("max_llm_calls", 0), ("max_llm_calls", -1), ("max_output_chars", 0)],
)
def test_rlm_options_reject_nonpositive_values(field: str, value: int) -> None:
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.rlm.result import RLMConfigError

    with pytest.raises(RLMConfigError, match=field):
        RLMOptions(**{field: value})


def _tool(name):
    """Create a test tool with the specified name."""
    return dspy.Tool(lambda: "ok", name=name)


@pytest.mark.parametrize("name", ["llm_query", "llm_query_batched", "print", "SUBMIT", "not-valid"])
def test_native_builder_rejects_namespace_collisions(name):
    with pytest.raises(RLMConfigError):
        build_native_rlm_for_test(signature="question -> answer", options=RLMOptions(), tools=[_tool(name)])


def test_native_builder_rejects_duplicate_names_and_keeps_authorized_tools():
    rlm = build_native_rlm_for_test(signature="question -> answer", options=RLMOptions(), tools=[_tool("read_data")])

    assert set(rlm.tools) == {"read_data"}
    with pytest.raises(RLMConfigError, match="duplicate"):
        build_native_rlm_for_test(
            signature="question -> answer",
            options=RLMOptions(),
            tools=[_tool("read_data"), _tool("read_data")],
        )
