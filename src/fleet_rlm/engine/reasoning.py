"""Native route strategies and their input/output projections, not Turn ownership."""

from typing import Any

import dspy

from fleet_rlm.contracts import Route
from fleet_rlm.engine.signatures import DirectAnswer, FleetTask
from fleet_rlm.workspace import WorkspaceDataset


class Reasoning(dspy.Module):
    """Own the native Direct, RLM and Flex parameters under one execution contract."""

    def __init__(
        self,
        *,
        root_lm: Any,
        sub_lm: Any,
        adapter: dspy.Adapter,
        tools: list[Any],
        rlm_factory: Any,
        flex_factory: Any,
        max_llm_calls: int,
        inline_context: Any,
        emit: Any,
        check_turn: Any,
    ) -> None:
        super().__init__()
        self.direct = dspy.ChainOfThought(DirectAnswer)
        self.rlm = dspy.RLM(
            FleetTask,
            tools=tools,
            sub_lm=sub_lm,
            interpreter_factory=rlm_factory,
            max_iters=16,
            max_llm_calls=max_llm_calls,
        )
        self.flex = dspy.Flex(FleetTask, tools=tools, interpreter_factory=flex_factory)
        self._root_lm, self._adapter = root_lm, adapter
        self._inline_context, self._emit, self._check_turn = inline_context, emit, check_turn

    def forward(
        self,
        route: Route,
        workspace_manifest: str,
        memory_summary: str,
        task: str,
        skills_catalog: str,
        session_history: str,
        context: Any,
        mode: str,
    ) -> dspy.Prediction:
        """Project inputs for the selected native solver and normalize task results."""
        if route is Route.DIRECT:
            inlined, chars, truncated = task, 0, False
            if mode == "direct":
                inlined, chars, truncated = self._inline_context(task, workspace_manifest)
                self._emit("direct_context", chars=chars, truncated=truncated)
            with dspy.context(lm=self._root_lm, adapter=self._adapter):
                answer = self.direct(
                    memory_summary=memory_summary, session_history=session_history, task=inlined
                ).answer
            return dspy.Prediction(
                answer=answer,
                learned_skill_note="",
                route=route.value,
                direct_context_chars=chars,
                direct_context_truncated=truncated,
            )

        agent = self.flex if route is Route.FLEX else self.rlm
        if route is Route.FLEX and isinstance(context, WorkspaceDataset):
            context = context.flex_input()
        with dspy.context(lm=self._root_lm, adapter=self._adapter):
            prediction = agent(
                workspace_manifest=workspace_manifest,
                skills_catalog=skills_catalog,
                memory_summary=memory_summary,
                session_history=session_history,
                execution_route=route.value,
                task=task,
                context=context if context is not None else "",
            )
        self._check_turn()
        return dspy.Prediction(
            answer=prediction.answer,
            learned_skill_note=getattr(prediction, "learned_skill_note", ""),
            route=route.value,
        )
