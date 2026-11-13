"""Child agents a Turn can delegate to: spawn_agent and spawn_agents.

Each runs a fresh dspy.RLM in its own child sandbox on the metered sub LM. A child that fails
returns its failure to the parent; only deadline, cancellation, and budget exhaustion end the Turn.
"""

from __future__ import annotations

import contextvars
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from typing import Any

import dspy

from fleet_rlm.contracts import turn_is_over
from fleet_rlm.sandbox.interpreter import DaytonaInterpreter
from fleet_rlm.sandbox.manager import DaytonaSandboxManager


def child_failure(exc: Exception) -> str:
    """Bounded description of a failed child agent, returned to its parent instead of raised."""
    return f"child agent failed ({type(exc).__name__}): {str(exc)[:500]}"


def _validate_child_limits(max_iters: int, max_llm_calls: int) -> None:
    """Validate bounded child execution limits with actionable error messages."""
    if type(max_iters) is not int or not 1 <= max_iters <= 8:
        raise ValueError(f"max_iters={max_iters!r} is invalid; expected an integer in 1..8")
    if type(max_llm_calls) is not int or not 1 <= max_llm_calls <= 25:
        raise ValueError(f"max_llm_calls={max_llm_calls!r} is invalid; expected an integer in 1..25")


class ChildDelegation:
    """Execute bounded child tasks using an invocation's copied learned policy state."""

    def __init__(
        self,
        sandbox_mgr: DaytonaSandboxManager,
        metered_sub: Any,
        child_signature: type[dspy.Signature],
        sub_adapter: dspy.Adapter,
    ) -> None:
        """Bind host capabilities, not persistent DSPy parameters or a parent program."""
        self.sandbox_mgr = sandbox_mgr
        self._metered_sub = metered_sub
        self._sub_adapter = sub_adapter
        self._child_signature = child_signature
        self.policies: dict[str, tuple[type[dspy.Signature], dict[str, Any]]] = {}

    def bind_policies(self, policies: dict[str, tuple[type[dspy.Signature], dict[str, Any]]]) -> None:
        """Snapshot once at the serialized invocation boundary; never retain a predictor."""
        self.policies = deepcopy(policies)

    def tools(self) -> list[Any]:
        """Return the delegation tools, spawn_agent and spawn_agents, for the Flex and RLM programs."""
        return [self.spawn_agent, self.spawn_agents]

    def spawn_agent(
        self,
        subtask: str,
        input_paths: list[str] | None = None,
        allowed_tools: list[str] | None = None,
        max_iters: int = 8,
        max_llm_calls: int = 25,
    ) -> dict[str, Any]:
        """Run a child with selected files; return findings and digest-verified promoted artifacts.

        Skills are always copied; other inputs only when listed. input_paths may name files/,
        skills/ or earlier artifacts/ paths, and unchanged copied artifacts are not promoted back.
        artifacts/ starts empty inside a child unless specific artifacts/... paths are requested in input_paths.
        The only optional Fleet tool is spawn_agent, allowing bounded further delegation.
        Bounds: 1 <= max_iters <= 8 and 1 <= max_llm_calls <= 25.
        max_llm_calls is DSPy's query/facade limit; the shared turn ledger also meters actions.
        """
        if not isinstance(subtask, str) or not subtask.strip() or len(subtask) > 20_000:
            raise ValueError("subtask must be a non-empty string up to 20,000 characters")
        _validate_child_limits(max_iters, max_llm_calls)
        names = ["spawn_agent"] if allowed_tools is None else allowed_tools
        if not isinstance(names, list) or any(name != "spawn_agent" for name in names):
            raise ValueError("Only the approved spawn_agent Fleet tool may be delegated")
        tools = {"spawn_agent": self.spawn_agent} if names else {}
        factory = self.sandbox_mgr.interpreter_factory(tools=tools, input_paths=input_paths or [])
        interpreters: list[DaytonaInterpreter] = []

        def create() -> DaytonaInterpreter:
            interpreter = factory()
            if not interpreters:
                # The explicit task owns promotion: failure must not publish
                # the child's unverified staging files into its parent.
                interpreter.owns_manager = False
            interpreters.append(interpreter)
            return interpreter

        create.execution_instructions = getattr(factory, "execution_instructions", "")  # ty: ignore[unresolved-attribute]
        child = dspy.RLM(
            self._child_signature,
            tools=list(tools.values()),
            interpreter_factory=create,
            sub_lm=self._metered_sub,
            max_iters=max_iters,
            max_llm_calls=max_llm_calls,
        )
        signature, state = self.policies["delegating_action" if tools else "leaf_action"]
        # Copy native learned state, never the runtime/tool closures or a live RLM.
        child.generate_action = dspy.Predict(signature)
        child.generate_action.load_state(deepcopy(state))
        if max_iters != 8 or max_llm_calls != 25:
            subtask = (
                f"Host resource limits: max_iters={max_iters}, max_llm_calls={max_llm_calls}; "
                f"optional Fleet tools={list(tools)}. These limits override nominal policy ceilings.\n\n{subtask}"
            )
        try:
            with dspy.context(lm=self._metered_sub, disable_history=True, adapter=self._sub_adapter):
                result = child(subtask=subtask)
        except Exception as exc:
            if interpreters and hasattr(interpreters[0].manager, "record_child_outcome"):
                interpreters[0].manager.record_child_outcome(False)
            if turn_is_over(exc):
                raise
            # A child that fails must not abort the parent's Turn or promote its unverified files.
            # This rule is the same for a solo and a batched child, so delegation cannot change an
            # error's severity; the parent sees the failure and can retry or use other children.
            return {"findings": "", "error": child_failure(exc), "artifacts": []}
        else:
            if interpreters:
                mgr = interpreters[0].manager
                artifacts = mgr.collect_child_artifacts()
                if hasattr(mgr, "record_child_outcome"):
                    mgr.record_child_outcome(True)
            else:
                artifacts = []
            return {
                "findings": str(result.findings)[:20_000],
                "artifacts": artifacts,
            }
        finally:
            if interpreters:
                interpreters[0].manager.shutdown()

    def spawn_agents(
        self,
        subtasks: list[str],
        input_paths: list[list[str]] | None = None,
        max_iters: int = 8,
        max_llm_calls: int = 25,
    ) -> list[dict[str, Any]]:
        """Run several independent children concurrently; return one result per subtask, in order.

        Prefer this over calling spawn_agent one after another when the subtasks do not depend on each
        other. input_paths, when given, holds one list of `files/`, `skills/`, or `artifacts/` paths per
        subtask; `artifacts/` starts empty inside a child unless specific `artifacts/...` paths are requested
        in `input_paths`. Each result is spawn_agent's {findings, artifacts}, or {findings: "", error,
        artifacts: []} if that child failed.
        Bounds: 1 <= len(subtasks) <= 8, 1 <= max_iters <= 8, and 1 <= max_llm_calls <= 25.
        Values outside these bounds raise ValueError before any child starts.
        At most the active-child capacity run at once; the shared Turn budget and deadline still apply.
        """
        if (
            not isinstance(subtasks, list)
            or not 1 <= len(subtasks) <= 8
            or not all(isinstance(t, str) for t in subtasks)
        ):
            raise ValueError("subtasks must be a list of 1-8 strings")
        if input_paths is not None and (not isinstance(input_paths, list) or len(input_paths) != len(subtasks)):
            raise ValueError("input_paths must be omitted or hold one path list per subtask")
        _validate_child_limits(max_iters, max_llm_calls)

        def run_child(index: int) -> dict[str, Any]:
            try:
                return self.spawn_agent(
                    subtask=subtasks[index],
                    input_paths=input_paths[index] if input_paths else None,
                    max_iters=max_iters,
                    max_llm_calls=max_llm_calls,
                )
            except Exception as exc:
                if turn_is_over(exc):
                    raise
                return {"findings": "", "error": child_failure(exc), "artifacts": []}

        workers = min(len(subtasks), self.sandbox_mgr.root.max_active_children)

        def submit(pool: ThreadPoolExecutor, index: int) -> Future[dict[str, Any]]:
            # One context copy per child keeps the DSPy callbacks, active manager, and MLflow parent span.
            context = contextvars.copy_context()
            return pool.submit(lambda: context.run(run_child, index))

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [submit(pool, index) for index in range(len(subtasks))]
            try:
                return [future.result() for future in futures]
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
