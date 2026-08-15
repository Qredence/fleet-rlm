"""Provider compatibility probe for the pinned native DSPy RLM protocol."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import dspy
from dspy.utils.exceptions import AdapterParseError

from fleet_rlm.config import Settings
from fleet_rlm.rlm.child_runtime import ChildRuntimeFactory
from fleet_rlm.rlm.dspy_contract import RLMOptions, build_native_rlm
from fleet_rlm.rlm.dspy_interpreter_contract import CodeInterpreter
from fleet_rlm.rlm.errors import RLMConfigError
from fleet_rlm.rlm.lm_factory import build_lm, resolve_role_api_key, sanitize_base_url
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.recursive_calls import RecursiveRLMExecutor, RecursiveRLMOptions

ProbeInterpreterFactory = Callable[[], CodeInterpreter]


class RLMProviderContractError(RLMConfigError):
    """The configured Root LM cannot satisfy Fleet's native RLM action contract."""


@dataclass(frozen=True, slots=True)
class RLMProviderProbeResult:
    """Bounded readiness evidence; provider payloads are never retained."""

    iterations: int
    termination_mode: str


class _ProviderProbeSignature(dspy.Signature):
    """Select a bounded value, delegate it to rlm_query, then call typed SUBMIT(answer=...)."""

    probe: str = dspy.InputField()
    answer: str = dspy.OutputField()


def _root_lm(settings: Settings) -> dspy.LM:
    role = settings.root_lm
    api_key = resolve_role_api_key(settings, role)
    if not api_key:
        raise RLMProviderContractError(f"Root LM API key is not configured ({role.api_key_env})")
    return build_lm(
        role.model,
        api_key=api_key,
        base_url=sanitize_base_url(role.base_url),
        max_tokens=role.max_tokens,
        temperature=role.temperature,
        reasoning_effort=role.reasoning_effort,
        cache=False,
        num_retries=role.num_retries,
    )


async def probe_root_lm(
    root_lm: Any,
    *,
    interpreter_factory: ProbeInterpreterFactory,
    child_runtime_factory: ChildRuntimeFactory,
) -> RLMProviderProbeResult:
    """
    Probe a root language model for compatibility with the native recursive RLM protocol.

    Parameters:
        root_lm (Any): The root language model to test.
        interpreter_factory (ProbeInterpreterFactory): Factory for a fresh provider-neutral interpreter.
        child_runtime_factory (ChildRuntimeFactory): Factory for a fresh provider-neutral child runtime.

    Returns:
        RLMProviderProbeResult: The number of RLM iterations and the termination mode.

    Raises:
        RLMProviderContractError: If the model fails the recursive RLM compatibility
            requirements or produces an invalid response.
    """

    interpreter = interpreter_factory()
    recursive = RecursiveRLMExecutor(
        models=RLMModelBundle(root_lm=root_lm, sub_lm=root_lm),
        options=RecursiveRLMOptions(max_calls=1, max_prompt_chars=2_000),
        child_runtime_factory=child_runtime_factory,
        deadline=time.monotonic() + 120,
    )
    rlm = build_native_rlm(
        signature=_ProviderProbeSignature,
        options=RLMOptions(max_iters=4, max_llm_calls=4, max_output_chars=2_000),
        tools=[recursive.tool],
    )
    try:
        with dspy.context(lm=root_lm, adapter=dspy.JSONAdapter(), track_usage=False):
            prediction = await rlm.acall(
                interpreter,
                probe=(
                    "Set marker = 'probe-slice'. On a later REPL iteration call "
                    "child = rlm_query(prompt='Classify this selected value: ' + marker), "
                    "then submit the child answer with typed SUBMIT(answer=child). "
                    "Use at least three REPL iterations and keep the prompt bounded."
                ),
            )
    except AdapterParseError as exc:
        raise RLMProviderContractError("Root LM returned an unparseable RLM action") from exc
    except Exception as exc:
        raise RLMProviderContractError("Root LM RLM compatibility probe failed") from exc
    finally:
        interpreter.shutdown()

    trajectory = getattr(prediction, "trajectory", ())
    answer = getattr(prediction, "answer", None)
    if not isinstance(trajectory, list) or len(trajectory) < 3:
        raise RLMProviderContractError("Root LM did not complete a multi-step RLM sequence")
    if not isinstance(answer, str) or not answer.strip():
        raise RLMProviderContractError("Root LM did not reach typed SUBMIT output")
    if recursive.summary().call_count < 1:
        raise RLMProviderContractError("Root LM did not exercise the recursive child Tool")
    return RLMProviderProbeResult(
        iterations=len(trajectory),
        termination_mode=(
            "native_extraction_fallback"
            if getattr(prediction, "final_reasoning", None) == "Extract forced final output"
            else "typed_submit"
        ),
    )


async def probe_configured_root_lm(
    settings: Settings,
    *,
    interpreter_factory: ProbeInterpreterFactory,
    child_runtime_factory: ChildRuntimeFactory,
) -> RLMProviderProbeResult:
    """Build only the policy-selected Root LM and probe it once."""

    return await probe_root_lm(
        _root_lm(settings),
        interpreter_factory=interpreter_factory,
        child_runtime_factory=child_runtime_factory,
    )


__all__ = [
    "ProbeInterpreterFactory",
    "RLMProviderContractError",
    "RLMProviderProbeResult",
    "probe_configured_root_lm",
    "probe_root_lm",
]
