"""Fleet's learned native DSPy graph, independent of artifact and Turn ownership."""

from typing import Any

import dspy

from fleet_rlm.engine.children import ChildPolicies
from fleet_rlm.engine.decisions import Routing, SkillSelection
from fleet_rlm.engine.reasoning import Reasoning


class FleetProgram(dspy.Module):
    """Compose learned decisions and native reasoning; host capabilities enforce policy."""

    def __init__(
        self,
        routing: Routing,
        skill_selection: SkillSelection,
        reasoning: Reasoning,
        child_policies: ChildPolicies,
        load_skills: Any,
    ) -> None:
        super().__init__()
        self.routing = routing
        self.skill_selection = skill_selection
        self.reasoning = reasoning
        self.child_policies = child_policies
        self._load_skills = load_skills
        # Native constructors define the contract, not a second signature implementation.
        self._state_contract = self.dump_state()

    def load_state(self, state: dict[str, Any], *, allow_unsafe_lm_state: bool = False) -> None:
        """Accept complete native state only; model selection and field contracts are host-owned."""
        if not isinstance(state, dict) or set(state) != set(self._state_contract):
            raise ValueError("FleetProgram requires exactly the fleet-program-a1 parameter paths")
        for name, default in self._state_contract.items():
            value = state[name]
            if not isinstance(value, dict) or value.get("lm") is not None:
                raise ValueError(f"{name}: models are bound by the runtime, not candidate state")
            if name == "reasoning.flex":
                if (
                    set(value) != {"module_src", "lm"}
                    or not isinstance(value["module_src"], str)
                    or not value["module_src"]
                ):
                    raise ValueError("Flex requires complete native source state")
            else:
                required = {"traces", "train", "demos", "signature", "lm"}
                if not required <= set(value) or set(value) - required - {"fields"}:
                    raise ValueError(f"{name}: incomplete or unknown native predictor state")
                if any(not isinstance(value[key], list) for key in ("traces", "train", "demos")):
                    raise ValueError(f"{name}: native predictor examples must be lists")
                signature = value["signature"]
                if (
                    not isinstance(signature, dict)
                    or set(signature) != {"instructions", "fields"}
                    or not isinstance(signature["instructions"], str)
                    or signature["fields"] != default["signature"]["fields"]
                ):
                    raise ValueError(f"{name}: incompatible native signature fields")
        # DSPy validates decisions/Flex on a trial copy before touching the incumbent.
        super().load_state(state, allow_unsafe_lm_state=allow_unsafe_lm_state)

    def forward(
        self,
        workspace_manifest: str,
        memory_summary: str,
        task: str,
        skills_catalog: str = "<available_skills></available_skills>",
        session_history: str = "",
        context: Any = None,
        execution_mode: str = "hybrid",
    ) -> dspy.Prediction:
        """Compose effective-route policy, optional Skill selection and native reasoning."""
        route = self.routing(
            task=task,
            workspace_manifest=workspace_manifest,
            skills_catalog=skills_catalog,
            mode=execution_mode,
            has_context=context is not None,
        )
        selected = self.skill_selection(task=task, skills_catalog=skills_catalog, route=route)
        return self.reasoning(
            route=route,
            workspace_manifest=workspace_manifest,
            memory_summary=memory_summary,
            task=task,
            skills_catalog=self._load_skills(skills_catalog, selected),
            session_history=session_history,
            context=context,
            mode=execution_mode,
        )
