"""Persistent native child action policies; allocation and execution live in the host."""

from typing import Any

import dspy

from fleet_rlm.engine.signatures import ChildTask


class ChildPolicies(dspy.Module):
    """Own two independently optimizable native action predictors, not live child agents."""

    def __init__(self, spawn_agent: Any, interpreter_factory: Any) -> None:
        super().__init__()
        for name, tools in (("leaf_action", []), ("delegating_action", [spawn_agent])):
            action = dspy.RLM(
                ChildTask, tools=tools, interpreter_factory=interpreter_factory, max_iters=8, max_llm_calls=25
            ).generate_action
            sig = action.signature
            assert sig is not None
            extra = sig.fields["code"].json_schema_extra
            base_desc = extra["desc"] if isinstance(extra, dict) and "desc" in extra else ""
            # Native GEPA attributes traces by signature equality, including role metadata.
            action.signature = sig.with_updated_fields(
                "code",
                desc=f"{base_desc} Fleet child role: {name}.",  # ty: ignore[invalid-argument-type]
            )
            setattr(self, name, action)

    def snapshot(self) -> dict[str, tuple[type[dspy.Signature], dict[str, Any]]]:
        """Export policy data for an invocation; the host copies it before using it."""
        return {name: (policy.signature, policy.dump_state()) for name, policy in self.named_predictors()}
