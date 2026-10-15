"""Child agents a Turn can delegate to: spawn_agent, spawn_agents, and sub_rlm.

Each runs a fresh dspy.RLM in its own child sandbox on the metered sub LM. A child that fails
returns its failure to the parent; only deadline, cancellation, and budget exhaustion end the Turn.
"""

from __future__ import annotations

import contextvars
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import dspy
from dspy.utils.exceptions import AdapterParseError

from fleet_rlm.dspy_support import FleetRLMChatAdapter
from fleet_rlm.engine.signatures import ChildTask, ContextualChildTask
from fleet_rlm.sandbox.interpreter import DaytonaInterpreter
from fleet_rlm.sandbox.manager import DaytonaSandboxManager


def turn_is_over(exc: BaseException) -> bool:
    """Deadline, cancellation, and shared-budget exhaustion end the Turn; ordinary child failures do not."""
    return isinstance(exc, TimeoutError) or str(exc).startswith(("Turn cancelled", "Shared RLM budget exhausted"))


def child_failure(exc: Exception) -> str:
    """Bounded description of a failed child agent, returned to its parent instead of raised."""
    return f"child agent failed ({type(exc).__name__}): {str(exc)[:500]}"


class ChildAgents:
    """The delegation tools given to the Flex and RLM programs."""

    def __init__(self, sandbox_mgr: DaytonaSandboxManager, metered_sub: Any) -> None:
        self.sandbox_mgr = sandbox_mgr
        self._metered_sub = metered_sub

    def tools(self) -> list[Any]:
        return [self.sub_rlm, self.spawn_agent, self.spawn_agents]

    def spawn_agent(
        self,
        subtask: str,
        input_paths: list[str] | None = None,
        allowed_tools: list[str] | None = None,
        max_iters: int = 8,
        max_llm_calls: int = 25,
    ) -> dict[str, Any]:
        """Run a child with selected files; return findings and digest-verified promoted artifacts.

        No inputs are copied unless listed. The only optional Fleet tool is
        spawn_agent, allowing bounded further delegation. max_llm_calls is
        DSPy's query/facade limit; the shared turn ledger also meters actions.
        """
        if (
            not subtask.strip()
            or len(subtask) > 20_000
            or type(max_iters) is not int
            or not 1 <= max_iters <= 8
            or type(max_llm_calls) is not int
            or not 1 <= max_llm_calls <= 25
        ):
            raise ValueError("Invalid child task or resource slice")
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
            ChildTask,
            tools=list(tools.values()),
            interpreter_factory=create,
            sub_lm=self._metered_sub,
            max_iters=max_iters,
            max_llm_calls=max_llm_calls,
        )
        try:
            with dspy.context(lm=self._metered_sub, disable_history=True, adapter=FleetRLMChatAdapter()):
                result = child(subtask=subtask)
        except AdapterParseError as exc:
            # A child whose reply cannot be parsed must not abort the parent's Turn or promote its
            # unverified files; the parent sees the failure and can retry or use other children.
            return {"findings": "", "error": child_failure(exc), "artifacts": []}
        else:
            return {
                "findings": str(result.findings)[:20_000],
                "artifacts": interpreters[0].manager.collect_child_artifacts() if interpreters else [],
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
        other. input_paths, when given, holds one list of `files/` paths per subtask. Each result is
        spawn_agent's {findings, artifacts}, or {findings: "", error, artifacts: []} if that child failed.
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

    def sub_rlm(self, subtask: str, context_hint: str = "") -> str:
        """Spawn an isolated recursive investigation; input files and Skills are copied, not parent memory."""
        child = dspy.RLM(
            signature=ContextualChildTask,
            tools=[self.sub_rlm],
            interpreter_factory=self.sandbox_mgr.interpreter_factory(tools={"sub_rlm": self.sub_rlm}),
            sub_lm=self._metered_sub,
            max_iters=8,
            max_llm_calls=25,
        )
        try:
            with dspy.context(lm=self._metered_sub, disable_history=True, adapter=FleetRLMChatAdapter()):
                return str(child(context_hint=context_hint[:10000], subtask=subtask[:20000]).findings)[:20000]
        except AdapterParseError as exc:
            return f"ERROR: {child_failure(exc)}"
