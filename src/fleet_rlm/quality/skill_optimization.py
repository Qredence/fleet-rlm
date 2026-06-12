"""GEPA optimization specs for Fleet markdown skills."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import dspy

from fleet_rlm.quality.module_registry import ModuleOptimizationSpec
from fleet_rlm.quality.optimization_runner import build_gepa_feedback_metric
from fleet_rlm.quality.rlm_gepa import DaytonaRLMProposalProgram, RLMInstructionProposer
from fleet_rlm.runtime.tools.skill_tools import _load_skill_impl

_SKILL_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _skill_slug(source: str) -> str:
    normalized = _SKILL_SLUG_RE.sub("-", source.lower()).strip("-")
    return normalized or "skill"


def _skill_source_label(path: Path) -> str:
    name = path.name
    for suffix in (".SKILL.md", ".skill.md", ".md"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def _resolve_skill_source(*, skill_name: str | None = None, skill_path: str | Path | None = None) -> tuple[str, str]:
    """Resolve a bundled or filesystem skill into ``(source_label, text)``."""
    if skill_name and skill_path:
        raise ValueError("Provide either skill_name or skill_path, not both.")
    if not skill_name and not skill_path:
        raise ValueError("skill_name or skill_path is required for skill optimization.")

    if skill_name:
        loaded = _load_skill_impl(skill_name)
        if loaded.status != "ok" or not loaded.instructions:
            raise ValueError(loaded.error or f"Skill not found: {skill_name}")
        return loaded.name, loaded.instructions

    path = Path(skill_path or "")
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Skill file not found: {path}")
    return _skill_source_label(path), path.read_text(encoding="utf-8")


def _example_value(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _skill_rows_to_examples(rows: list[dict[str, Any]]) -> list[Any]:
    """Convert skill optimization rows into DSPy examples."""
    examples: list[Any] = []
    for row in rows:
        inputs = row.get("inputs")
        expectations = row.get("expectations")
        if isinstance(inputs, dict) and isinstance(expectations, dict):
            expected = expectations.get("expected_response") or expectations.get("response")
            if expected in (None, ""):
                continue
            user_request = _example_value(inputs, "user_request", "request", "prompt", "query", "question")
            context = _example_value(inputs, "context", "workspace_context", "document", "trace_summary")
        else:
            expected = _example_value(
                row,
                "expected_response",
                "response",
                "answer",
                "expected_answer",
                "working_memory_summary",
                "assistant_message",
                "output",
            )
            user_request = _example_value(row, "user_request", "request", "prompt", "query", "question")
            context = _example_value(row, "context", "workspace_context", "document", "trace_summary")
        if not user_request or not expected:
            continue
        examples.append(
            dspy.Example(
                user_request=user_request,
                context=context,
                response=str(expected),
            ).with_inputs("user_request", "context")
        )
    return examples


def _make_skill_proposal_signature() -> Any:
    """Build the default proposer module used by RLMInstructionProposer."""
    class SkillProposalSignature(dspy.Signature):
        """Rewrite a Fleet markdown skill using GEPA reflective feedback.

        Return only the revised skill markdown. Preserve useful frontmatter,
        avoid inventing tools, and focus on operational guidance that prevents
        repeated failures in the reflective examples.
        """

        component_name: str = dspy.InputField(desc="Name of the skill/prompt component being optimized.")
        current_instructions: str = dspy.InputField(desc="Current skill markdown instructions.")
        reflective_dataset: str = dspy.InputField(desc="GEPA reflective examples with outputs and feedback.")
        trace_bundle_paths: list[str] = dspy.InputField(desc="Optional paths to large trace bundles available offline.")
        trace_bundle_previews: str = dspy.InputField(desc="Bounded previews of distilled trace bundles.")
        candidate_history: str = dspy.InputField(desc="Prior candidate lineage and proposal history.")
        revised_instructions: str = dspy.OutputField(desc="Revised SKILL.md-compatible markdown instructions only.")

    return SkillProposalSignature


def _make_default_proposal_program() -> Any:
    """Build the Daytona-backed proposer module used by RLMInstructionProposer."""
    return DaytonaRLMProposalProgram(signature_factory=_make_skill_proposal_signature)


def _predictor_instructions(predictor: Any) -> str:
    signature = getattr(predictor, "signature", None)
    instructions = getattr(signature, "instructions", None)
    if instructions is None:
        raise ValueError("Skill predictor does not expose signature instructions.")
    return str(instructions)


class SkillInstructionProgram(dspy.Module):
    """Tiny DSPy module whose single optimizable predictor is a skill."""

    def __init__(self, skill_text: str) -> None:
        super().__init__()

        class SkillApplicationSignature(dspy.Signature):
            """Apply the current skill instructions to the task."""

            user_request: str = dspy.InputField(desc="Task the skill should help solve.")
            context: str = dspy.InputField(desc="Optional context, trace summary, or artifact details.")
            response: str = dspy.OutputField(desc="Task response produced under the current skill instructions.")

        self._dspy = dspy
        self.skill = dspy.Predict(SkillApplicationSignature.with_instructions(skill_text))

    def named_predictors(self):
        return [("skill", self.skill)]

    def predictors(self):
        return [self.skill]

    def deepcopy(self):
        copied = SkillInstructionProgram(_predictor_instructions(self.skill))
        return copied

    def __call__(self, **kwargs: Any) -> Any:
        return super().__call__(**kwargs)

    def forward(self, **kwargs: Any) -> Any:
        return self.skill(**kwargs)

    def save(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(_predictor_instructions(self.skill), encoding="utf-8")


def _write_skill_artifact(optimized_program: Any, output_path: str) -> dict[str, Any]:
    for name, predictor in optimized_program.named_predictors():
        if name == "skill":
            text = _predictor_instructions(predictor)
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            return {
                "artifact_type": "skill",
                "optimized_skill_path": str(path),
                "loader": "markdown",
                "size_bytes": path.stat().st_size,
            }
    raise ValueError("Optimized program did not expose a 'skill' predictor.")


def spec_for_skill(
    *,
    skill_name: str | None = None,
    skill_path: str | Path | None = None,
    trace_bundle_paths: list[str] | None = None,
) -> ModuleOptimizationSpec:
    """Build an ad-hoc GEPA spec for optimizing one markdown skill."""
    source, skill_text = _resolve_skill_source(skill_name=skill_name, skill_path=skill_path)
    slug = f"skill-{_skill_slug(source)}"
    return ModuleOptimizationSpec(
        module_slug=slug,
        label=f"Skill: {source}",
        program_spec=f"skill:{source}",
        artifact_filename=f"{_skill_slug(source)}.optimized.md",
        input_keys=["user_request", "context"],
        required_dataset_keys=[],
        module_factory=lambda: SkillInstructionProgram(skill_text),
        row_converter=_skill_rows_to_examples,
        metric_builder=lambda: build_gepa_feedback_metric(output_key="response"),
        metric_name="skill_feedback_metric",
        description=f"GEPA optimization target for the {source!r} Fleet skill.",
        artifact_writer=_write_skill_artifact,
        instruction_proposer_factory=lambda: RLMInstructionProposer(
            proposal_program=_make_default_proposal_program(),
            trace_bundle_paths=trace_bundle_paths or [],
        ),
    )


__all__ = ["SkillInstructionProgram", "spec_for_skill"]
