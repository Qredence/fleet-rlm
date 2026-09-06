"""RLMFactory, model bundle, and native RLM Options."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest
from dspy.utils.exceptions import LMAuthError, LMInvalidRequestError, LMServerError


class _CopyableLM:
    def __init__(self) -> None:
        self.history: list[object] = []
        self.kwargs: dict[str, object] = {"timeout": 60.0}
        self.calls: list[dict[str, object]] = []
        self.num_retries: int | None = None

    def copy(self, **kwargs: object) -> _CopyableLM:
        copied = _CopyableLM()
        copied.num_retries = kwargs.get("num_retries")  # type: ignore[assignment]
        return copied

    def forward(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        return object()


class _RetryingLM:
    """Small provider double for deadline/retry isolation contracts."""

    def __init__(self, failures: list[BaseException] | None = None) -> None:
        self.kwargs: dict[str, object] = {"timeout": 10.0}
        self.num_retries = 2
        self.failures = list(failures or [])
        self.calls: list[dict[str, object]] = []

    def copy(self, **kwargs: object) -> _RetryingLM:
        copied = _RetryingLM(self.failures)
        copied.num_retries = int(kwargs.get("num_retries", self.num_retries))
        copied.kwargs = dict(self.kwargs)
        return copied

    def forward(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        if self.failures:
            raise self.failures.pop(0)
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


def test_model_bundle_forks_isolated_deadline_bound_child_lms() -> None:
    import time

    from fleet_rlm.rlm.program import RLMModelBundle

    root = _CopyableLM()
    sub = _CopyableLM()
    bundle = RLMModelBundle(root, sub)
    deadline = time.monotonic() + 5

    first = bundle.fork_for_child(deadline=deadline)
    second = bundle.fork_for_child(deadline=deadline)

    assert first.root_lm is not root
    assert first.sub_lm is not sub
    assert first.root_lm is not second.root_lm
    assert first.sub_lm is not second.sub_lm
    assert first.root_lm.history is not second.root_lm.history
    assert first.root_lm.num_retries == 0
    assert first.sub_lm.num_retries == 0

    first.root_lm.forward(prompt="bounded child")
    timeout = first.root_lm.calls[-1]["timeout"]
    assert isinstance(timeout, float)
    assert 0 < timeout <= 5


def test_model_bundle_child_lm_rejects_calls_after_turn_deadline() -> None:
    import time

    from fleet_rlm.rlm.program import RLMModelBundle

    child = RLMModelBundle(_CopyableLM(), _CopyableLM()).fork_for_child(deadline=time.monotonic() - 1)

    with pytest.raises(TimeoutError, match="recursive child LM deadline exceeded"):
        child.root_lm.forward(prompt="late")


def test_deadline_proxy_copy_preserves_deadline_without_method_assignment() -> None:
    import time

    from fleet_rlm.rlm.program import RLMModelBundle

    bound = RLMModelBundle(_CopyableLM(), _CopyableLM()).fork_for_child(deadline=time.monotonic() - 1)
    copied = bound.root_lm.copy()
    assert "forward" not in vars(copied)
    assert "aforward" not in vars(copied)
    assert copied.wrapped is not bound.root_lm.wrapped
    with pytest.raises(TimeoutError, match="deadline exceeded"):
        copied.forward(prompt="late")


@pytest.mark.asyncio
async def test_deadline_proxy_async_retry_preserves_attempt_timeout():
    import time

    from fleet_rlm.rlm.program import RLMModelBundle

    class AsyncLM(_RetryingLM):
        def copy(self, **kwargs):
            copied = AsyncLM(self.failures)
            copied.num_retries = kwargs["num_retries"]
            return copied

        async def aforward(self, **kwargs):
            """Execute the forward operation asynchronously and return its result."""
            return self.forward(**kwargs)

    source = AsyncLM([LMServerError("retry")])
    bound = RLMModelBundle(source, source).bind_turn_deadline(deadline=time.monotonic() + 5)
    await bound.root_lm.aforward(prompt="test")
    assert len(bound.root_lm.calls) == 2
    assert source.calls == []
    assert all(0 < call["timeout"] <= 5 for call in bound.root_lm.calls)


@pytest.mark.asyncio
async def test_deadline_proxy_preserves_dspy_sync_async_usage_and_callbacks():
    import time
    from types import SimpleNamespace

    import dspy
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

    class ScriptLM(dspy.BaseLM):
        def forward(self, prompt=None, messages=None, **kwargs):
            """Generate a fixed successful language-model response.
            
            Returns:
                A response containing ``"ok"`` and fixed token usage statistics.
            """
            del prompt, messages, kwargs
            return SimpleNamespace(
                model=self.model,
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            )

        async def aforward(self, prompt=None, messages=None, **kwargs):
            """
            Process a prompt or message sequence.
            
            Parameters:
                prompt: Optional prompt to process.
                messages: Optional sequence of messages to process.
            
            Returns:
                The model response.
            """
            return self.forward(prompt, messages, **kwargs)

    callback = Callback()
    source = ScriptLM("test/script", callbacks=[callback])
    proxy = RLMModelBundle(source, source).bind_turn_deadline(deadline=time.monotonic() + 10).root_lm
    assert proxy("sync") == ["ok"]
    assert await proxy.acall("async") == ["ok"]
    assert len(proxy.history) == 2
    assert proxy.history[-1]["usage"]["total_tokens"] == 3
    assert source.history == []
    assert callback.starts == ["test/script", "test/script"]


def test_turn_binding_isolated_and_applies_role_specific_reserve() -> None:
    import time

    from fleet_rlm.rlm.program import RLMModelBundle

    root = _CopyableLM()
    sub = _CopyableLM()
    source = RLMModelBundle(root, sub)
    bound = source.bind_turn_deadline(deadline=time.monotonic() + 5, reserve_seconds=1)

    bound.root_lm.forward(prompt="root")
    bound.sub_lm.forward(prompt="sub")

    assert bound is not source
    assert bound.root_lm is not root
    assert bound.sub_lm is not sub
    assert root.calls == []
    assert sub.calls == []
    assert 0 < bound.root_lm.calls[-1]["timeout"] <= 5  # type: ignore[operator]
    assert 0 < bound.sub_lm.calls[-1]["timeout"] <= 4  # type: ignore[operator]
    assert bound.deadline is not None
    assert bound.reserve_seconds == 1


def test_sequential_and_concurrent_turn_bindings_do_not_accumulate_wrappers() -> None:
    import time

    from fleet_rlm.rlm.program import RLMModelBundle

    source = RLMModelBundle(_CopyableLM(), _CopyableLM())
    first = source.bind_turn_deadline(deadline=time.monotonic() + 1)
    second = source.bind_turn_deadline(deadline=time.monotonic() + 5)

    def call(bundle: RLMModelBundle) -> float:
        bundle.root_lm.forward(prompt="turn")
        return float(bundle.root_lm.calls[-1]["timeout"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        timeouts = list(executor.map(call, (first, second)))

    assert first.root_lm is not second.root_lm
    assert timeouts[0] <= 1
    assert timeouts[1] <= 5
    assert not hasattr(source.root_lm, "_fleet_deadline")


def test_provider_retries_recompute_remaining_and_non_retryable_errors_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    from fleet_rlm.rlm.program import RLMModelBundle

    retry_source = _RetryingLM([LMServerError("temporary")])
    bound = RLMModelBundle(retry_source, _CopyableLM()).bind_turn_deadline(deadline=110)
    ticks = [100.0, 100.0, 101.0, 101.0]

    def clock() -> float:
        """
        Return the next scripted clock value.
        
        Returns:
        	float: The next value from `ticks`, or `102.0` when no scripted values remain.
        """
        return ticks.pop(0) if ticks else 102.0

    monkeypatch.setattr("fleet_rlm.rlm.program.time.monotonic", clock)
    bound.root_lm.forward(prompt="retry")
    monkeypatch.undo()

    assert len(bound.root_lm.calls) == 2
    assert bound.root_lm.calls[0]["timeout"] == 10
    assert bound.root_lm.calls[1]["timeout"] == 9
    assert retry_source.calls == []
    assert bound.root_lm.num_retries == 0

    for error in (LMInvalidRequestError("invalid"), LMAuthError("auth")):
        source = _RetryingLM([error])
        bound = RLMModelBundle(source, _CopyableLM()).bind_turn_deadline(deadline=time.monotonic() + 5)
        with pytest.raises(type(error)):
            bound.root_lm.forward(prompt="do not retry")
        assert len(bound.root_lm.calls) == 1


def test_turn_binding_rejects_nonfinite_deadline_that_would_disable_budget() -> None:
    import math

    from fleet_rlm.rlm.program import RLMModelBundle

    with pytest.raises(ValueError, match="deadline"):
        RLMModelBundle(_CopyableLM(), _CopyableLM()).bind_turn_deadline(deadline=math.nan)
    with pytest.raises(ValueError, match="deadline"):
        RLMModelBundle(_CopyableLM(), _CopyableLM()).bind_turn_deadline(deadline=-math.inf)


def test_child_copy_cannot_extend_turn_budget_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.rlm.program import RLMModelBundle

    source = RLMModelBundle(_CopyableLM(), _CopyableLM())
    turn = source.bind_turn_deadline(deadline=101)
    child = turn.fork_for_child(deadline=110)
    monkeypatch.setattr("fleet_rlm.rlm.program.time.monotonic", lambda: 100.0)

    turn.root_lm.forward(prompt="turn")
    child.root_lm.forward(prompt="child")

    assert turn.root_lm.calls[-1]["timeout"] == 1
    assert child.root_lm.calls[-1]["timeout"] == 1
    shorter_child = turn.fork_for_child(deadline=100.5)
    shorter_child.root_lm.forward(prompt="short child")
    assert shorter_child.root_lm.calls[-1]["timeout"] == 0.5


def test_invalid_options_fail_before_construction() -> None:
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.rlm.result import RLMConfigError

    with pytest.raises(RLMConfigError):
        RLMOptions(max_iters=0)
    with pytest.raises(RLMConfigError):
        RLMOptions(max_llm_calls=-1)
    with pytest.raises(RLMConfigError):
        RLMOptions(max_output_chars=0)


def test_factory_passes_explicit_constructor_kwargs() -> None:
    import dspy

    from fleet_rlm.rlm.program import FleetRLMSignature, RLMFactory, RLMModelBundle, RLMOptions

    root = MagicMock(name="root_lm")
    sub = MagicMock(name="sub_lm")
    options = RLMOptions(
        max_iters=7,
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
    from fleet_rlm.rlm.program import RLMFactory, RLMModelBundle, RLMOptions

    factory = RLMFactory()
    models = RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock())
    options = RLMOptions()
    first = factory.create(models=models, options=options)
    second = factory.create(models=models, options=options)

    assert first is not second


def test_factory_accepts_policy_controlled_host_verbosity() -> None:
    from fleet_rlm.rlm.program import RLMFactory, RLMModelBundle, RLMOptions

    rlm = RLMFactory(verbose=False).create(
        models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
        options=RLMOptions(),
    )

    assert rlm.verbose is False


def test_dspy_contract_is_only_native_dspy_rlm_call_site_in_rlm_package() -> None:
    """Static guard: only dspy_contract.py may directly construct native dspy.RLM."""
    import ast
    from pathlib import Path

    rlm_dir = Path(__file__).resolve().parents[4] / "src" / "fleet_rlm" / "rlm"
    assert (rlm_dir / "program.py").is_file()
    offenders: list[str] = []
    for path in sorted(rlm_dir.glob("*.py")):
        if path.name in ("dspy_contract.py", "program.py"):
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
    """Static guard: only dspy_interpreter_contract.py and compat modules may import dspy.primitives."""
    import ast
    from pathlib import Path

    src_root = Path(__file__).resolve().parents[4] / "src" / "fleet_rlm"
    allowed = {"rlm/compat_3_3_1.py"}
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


def test_private_dspy_imports_are_confined_to_compat_layer() -> None:
    """Static guard: only compat modules may import private DSPy internal packages."""
    import ast
    from pathlib import Path

    src_root = Path(__file__).resolve().parents[4] / "src" / "fleet_rlm"
    allowed = {"rlm/compat_3_3_1.py"}
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
    assert offenders == [], f"Private DSPy imports found outside compat layer: {offenders}"


def test_compatibility_implementation_has_one_versioned_home() -> None:
    from pathlib import Path

    from fleet_rlm.rlm.compat_3_3_1 import FleetJSONAdapter

    root = Path(__file__).resolve().parents[4] / "src" / "fleet_rlm" / "rlm"
    assert not (root / "_dspy_compat.py").exists()
    assert FleetJSONAdapter.__module__ == "fleet_rlm.rlm.compat_3_3_1"
