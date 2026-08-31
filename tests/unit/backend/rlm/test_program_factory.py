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

    import fleet_rlm.rlm.program as factory

    retry_source = _RetryingLM([LMServerError("temporary")])
    bound = factory.RLMModelBundle(retry_source, _CopyableLM()).bind_turn_deadline(deadline=110)
    ticks = [100.0, 101.0]

    def clock() -> float:
        return ticks.pop(0) if ticks else 102.0

    monkeypatch.setattr(factory.time, "monotonic", clock)
    bound.root_lm.forward(prompt="retry")
    monkeypatch.undo()

    assert len(bound.root_lm.calls) == 2
    assert bound.root_lm.calls[0]["timeout"] == 10
    assert bound.root_lm.calls[1]["timeout"] == 9
    assert retry_source.calls == []
    assert bound.root_lm.num_retries == 0

    for error in (LMInvalidRequestError("invalid"), LMAuthError("auth")):
        source = _RetryingLM([error])
        bound = factory.RLMModelBundle(source, _CopyableLM()).bind_turn_deadline(deadline=time.monotonic() + 5)
        with pytest.raises(type(error)):
            bound.root_lm.forward(prompt="do not retry")
        assert len(bound.root_lm.calls) == 1


def test_child_copy_strips_turn_wrapper_and_uses_child_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.rlm.program as factory

    source = factory.RLMModelBundle(_CopyableLM(), _CopyableLM())
    turn = source.bind_turn_deadline(deadline=101)
    child = turn.fork_for_child(deadline=110)
    monkeypatch.setattr(factory.time, "monotonic", lambda: 100.0)

    turn.root_lm.forward(prompt="turn")
    child.root_lm.forward(prompt="child")

    assert turn.root_lm.calls[-1]["timeout"] == 1
    assert child.root_lm.calls[-1]["timeout"] == 10


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

    rlm_dir = Path(__file__).resolve().parents[3] / "src" / "fleet_rlm" / "rlm"
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
    """Static guard: only dspy_interpreter_contract.py may import dspy.primitives."""
    import ast
    from pathlib import Path

    src_root = Path(__file__).resolve().parents[3] / "src" / "fleet_rlm"
    allowed = {"rlm/dspy_interpreter_contract.py", "rlm/_dspy_compat.py"}
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
