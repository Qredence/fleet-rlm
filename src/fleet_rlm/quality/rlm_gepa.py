"""GEPA instruction-proposer adapters for Fleet RLM optimization.

The adapter follows GEPA's ``ProposalFn`` contract:
``(candidate, reflective_dataset, components_to_update) -> component text map``.
It is intentionally offline-only; callers decide whether the proposal module is
a cheap ``dspy.Predict`` program, a Daytona-backed ``dspy.RLM``, or a test fake.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ProposalProgram = Callable[..., Any]


def _json_preview(value: Any, *, max_chars: int) -> str:
    """Serialize large reflective payloads into bounded, deterministic text."""
    try:
        text = json.dumps(value, indent=2, sort_keys=True, default=str)
    except TypeError:
        text = str(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 15] + "\n...[truncated]"


def _file_preview(path: str, *, max_chars: int) -> dict[str, Any]:
    candidate = Path(path)
    if not candidate.exists() or not candidate.is_file():
        return {"path": path, "status": "missing"}
    size_bytes = candidate.stat().st_size
    with candidate.open(encoding="utf-8", errors="replace") as handle:
        text = handle.read(max_chars)
    return {
        "path": str(candidate),
        "status": "ok",
        "size_bytes": size_bytes,
        "preview": text,
        "truncated": size_bytes > max_chars,
    }


def _coerce_component_text(value: Any, component_name: str) -> str:
    """Extract the revised instruction text from a proposer result."""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for key in (
            component_name,
            "revised_instructions",
            "new_instruction",
            "instructions",
            "skill_instructions",
            "text",
        ):
            candidate = value.get(key)
            if candidate:
                return str(candidate)
    for attr in (
        component_name,
        "revised_instructions",
        "new_instruction",
        "instructions",
        "skill_instructions",
        "text",
    ):
        candidate = getattr(value, attr, None)
        if candidate:
            return str(candidate)
    raise ValueError(f"RLM-GEPA proposer did not return instructions for component {component_name!r}.")


@dataclass(slots=True)
class RLMInstructionProposer:
    """GEPA ``ProposalFn`` that delegates instruction rewriting to a module.

    The wrapped module receives the current component text, GEPA's reflective
    examples, optional trace bundle paths, and candidate history.  The module is
    expected to return revised instructions only.
    """

    proposal_program: ProposalProgram
    trace_bundle_paths: Sequence[str] = ()
    candidate_history: Sequence[Mapping[str, Any]] = ()
    max_reflective_dataset_chars: int = 60_000
    max_trace_bundle_preview_chars: int = 40_000
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(
        self,
        candidate: dict[str, str],
        reflective_dataset: Mapping[str, Sequence[Mapping[str, Any]]],
        components_to_update: list[str],
    ) -> dict[str, str]:
        proposals: dict[str, str] = {}
        for component_name in components_to_update:
            current_text = candidate.get(component_name, "")
            component_dataset = list(reflective_dataset.get(component_name, ()))
            payload = {
                "component_name": component_name,
                "current_instructions": current_text,
                "reflective_dataset": _json_preview(
                    component_dataset,
                    max_chars=self.max_reflective_dataset_chars,
                ),
                "trace_bundle_paths": list(self.trace_bundle_paths),
                "trace_bundle_previews": _json_preview(
                    [
                        _file_preview(path, max_chars=self.max_trace_bundle_preview_chars)
                        for path in self.trace_bundle_paths
                    ],
                    max_chars=self.max_trace_bundle_preview_chars,
                ),
                "candidate_history": _json_preview(
                    list(self.candidate_history),
                    max_chars=12_000,
                ),
            }
            self.calls.append(payload)
            result = self.proposal_program(**payload)
            proposals[component_name] = _coerce_component_text(result, component_name)
        return proposals


@dataclass(slots=True)
class DaytonaRLMProposalProgram:
    """Daytona-backed RLM callable for GEPA instruction proposals."""

    signature_factory: Callable[[], Any]
    max_iterations: int = 8
    max_llm_calls: int = 12
    max_output_chars: int = 20_000
    verbose: bool = False
    interpreter_factory: Callable[[], Any] | None = None

    def _make_interpreter(self) -> Any:
        if self.interpreter_factory is not None:
            return self.interpreter_factory()
        from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

        return DaytonaInterpreter(
            delete_session_on_shutdown=True,
            delete_context_on_shutdown=True,
            rlm_max_iterations=self.max_iterations,
            max_llm_calls=self.max_llm_calls,
        )

    def __call__(self, **payload: Any) -> Any:
        import dspy

        signature = self.signature_factory()
        interpreter = self._make_interpreter()
        with interpreter:
            rlm = dspy.RLM(
                signature,
                interpreter=interpreter,
                max_iterations=self.max_iterations,
                max_llm_calls=self.max_llm_calls,
                max_output_chars=self.max_output_chars,
                verbose=self.verbose,
            )
            return rlm(**payload)


__all__ = ["DaytonaRLMProposalProgram", "RLMInstructionProposer"]
