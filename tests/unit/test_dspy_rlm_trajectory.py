"""Unit tests for dspy.RLM trajectory persistence.

Phase A assessment — RLM trajectory persistence criteria:
  1. ``result.trajectory`` is a list of dicts.
  2. Each entry has ``reasoning`` and ``code`` keys.
  3. The count of trajectory steps equals the number of REPL iterations.
  4. A successful SUBMIT produces a ``Prediction`` with the correct output fields.

Implementation note
-------------------
DSPy's ChatAdapter (and its JSONAdapter fallback) are both exercised by the test.
The mock LM returns responses in the ``[[ ## field ## ]]`` format so that
ChatAdapter.parse succeeds on the first try, consuming exactly one LM slot per
generate_action call.
"""

from __future__ import annotations

import dspy

from fleet_rlm.react.rlm_runtime_modules import AnalyzeLongDocumentModule
from tests.unit.fixtures_state_trajectory import (
    ScriptedLM,
    chat_response,
    make_mock_interpreter,
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRLMTrajectoryPersistence:
    """Phase A — trajectory accumulation correctness."""

    def test_deterministic_mock_three_step_execution_progression(self):
        """Deterministically simulate a 3-step RLM run with mocked LM/interpreter.

        This is the canonical fast regression for QRE-300. It validates that
        the loop performs exactly three iterations (two tool observations plus
        one submit), and that each trajectory entry carries a thought/action/
        observation style progression using DSPy's `reasoning`/`code`/`output`
        fields.
        """
        from dspy.primitives.code_interpreter import FinalOutput

        lm = ScriptedLM(
            [
                chat_response(
                    reasoning="Step 1: inspect the document header for context.",
                    code="peek(context, 0, 80)",
                ),
                chat_response(
                    reasoning="Step 2: search for the target phrase in the document.",
                    code="grep(context, 'target phrase')",
                ),
                chat_response(
                    reasoning="Step 3: I have enough evidence; submit the answer.",
                    code=(
                        "SUBMIT(findings=['target phrase found'], "
                        "answer='Target phrase found in the document', "
                        "sections_examined=2)"
                    ),
                ),
            ]
        )
        dspy.settings.configure(lm=lm)

        interpreter = make_mock_interpreter(
            side_effects=[
                "Header preview: target phrase may appear later...",
                "grep match: target phrase",
                FinalOutput(
                    {
                        "findings": ["target phrase found"],
                        "answer": "Target phrase found in the document",
                        "sections_examined": 2,
                    }
                ),
            ]
        )

        module = AnalyzeLongDocumentModule(
            interpreter=interpreter,
            max_iterations=5,
            max_llm_calls=5,
            verbose=False,
        )

        result = module(
            document="Header preview... target phrase ... footer",
            query="Find the target phrase",
        )

        assert interpreter.execute.call_count == 3, "Expected exactly 3 REPL executions"
        assert hasattr(result, "trajectory")
        assert isinstance(result.trajectory, list)
        assert len(result.trajectory) == 3

        # Treat DSPy's `reasoning`/`code`/`output` fields as thought/action/observation.
        first, second, third = result.trajectory

        assert first["reasoning"].startswith("Step 1:")
        assert "peek(" in first["code"]
        assert "Header preview" in str(first.get("output", ""))

        assert second["reasoning"].startswith("Step 2:")
        assert "grep(" in second["code"]
        assert "target phrase" in str(second.get("output", ""))

        assert third["reasoning"].startswith("Step 3:")
        assert "SUBMIT(" in third["code"]
        # Final submit may serialize the final output payload or a sentinel string.
        assert third.get("output") is not None

        assert result.findings == ["target phrase found"]
        assert result.answer == "Target phrase found in the document"
        assert result.sections_examined == 2

    def test_trajectory_is_list_with_correct_step_count(self):
        """dspy.RLM must accumulate exactly one trajectory entry per REPL
        iteration before a successful SUBMIT."""
        from dspy.primitives.code_interpreter import FinalOutput

        lm = ScriptedLM(
            [
                chat_response(
                    reasoning="Let's peek at the start of the document.",
                    code="peek(context, 0, 50)",
                ),
                chat_response(
                    reasoning="I need to find the revenue figure.",
                    code="grep(context, 'revenue')",
                ),
                chat_response(
                    reasoning="Found revenue. Submitting now.",
                    code="SUBMIT(findings=['revenue is $5M'], answer='The revenue is $5M', sections_examined=2)",
                ),
            ]
        )
        dspy.settings.configure(lm=lm)

        interpreter = make_mock_interpreter(
            side_effects=[
                "Document excerpt: Q3 revenue was $5M...",
                "Q3 revenue was $5M",
                FinalOutput(
                    {
                        "findings": ["revenue is $5M"],
                        "answer": "The revenue is $5M",
                        "sections_examined": 2,
                    }
                ),
            ]
        )

        module = AnalyzeLongDocumentModule(
            interpreter=interpreter,
            max_iterations=5,
            max_llm_calls=5,
            verbose=False,
        )

        result = module(
            document="Q3 revenue was $5M. Other irrelevant info...",
            query="What was the revenue?",
        )

        # --- trajectory list ---
        assert hasattr(result, "trajectory"), "result missing 'trajectory'"
        assert isinstance(result.trajectory, list), "trajectory must be a list"
        assert len(result.trajectory) == 3, (
            f"Expected 3 steps, got {len(result.trajectory)}"
        )

        step_0 = result.trajectory[0]
        assert "reasoning" in step_0
        assert "code" in step_0
        assert "peek" in step_0["code"]

        step_1 = result.trajectory[1]
        assert "grep" in step_1["code"]

        step_2 = result.trajectory[2]
        assert "SUBMIT" in step_2["code"] or "FINAL" in step_2.get("output", "")

        # --- output fields ---
        assert "5M" in result.answer
        assert len(result.findings) > 0
        assert result.sections_examined == 2

    def test_single_step_trajectory_on_immediate_submit(self):
        """Even a one-iteration run must produce a list with one entry."""
        from dspy.primitives.code_interpreter import FinalOutput

        lm = ScriptedLM(
            [
                chat_response(
                    reasoning="The answer is obvious.",
                    code="SUBMIT(findings=['trivial'], answer='trivial', sections_examined=0)",
                ),
            ]
        )
        dspy.settings.configure(lm=lm)

        interpreter = make_mock_interpreter(
            side_effects=[
                FinalOutput(
                    {
                        "findings": ["trivial"],
                        "answer": "trivial",
                        "sections_examined": 0,
                    }
                ),
            ]
        )

        module = AnalyzeLongDocumentModule(
            interpreter=interpreter,
            max_iterations=5,
            max_llm_calls=5,
            verbose=False,
        )

        result = module(document="Short doc.", query="Tell me anything.")

        assert isinstance(result.trajectory, list)
        assert len(result.trajectory) == 1

    def test_trajectory_step_schema(self):
        """Every trajectory entry must be a dict with at least 'reasoning'
        and 'code' keys (matching dspy.RLM's REPLEntry.model_dump())."""
        from dspy.primitives.code_interpreter import FinalOutput

        lm = ScriptedLM(
            [
                chat_response(
                    "Reasoning.",
                    "SUBMIT(findings=['x'], answer='x', sections_examined=1)",
                ),
            ]
        )
        dspy.settings.configure(lm=lm)

        interpreter = make_mock_interpreter(
            side_effects=[
                FinalOutput({"findings": ["x"], "answer": "x", "sections_examined": 1}),
            ]
        )

        module = AnalyzeLongDocumentModule(
            interpreter=interpreter,
            max_iterations=5,
            max_llm_calls=5,
            verbose=False,
        )

        result = module(document="doc", query="q")

        for i, step in enumerate(result.trajectory):
            assert isinstance(step, dict), f"step {i} is not a dict"
            assert "reasoning" in step, f"step {i} missing 'reasoning'"
            assert "code" in step, f"step {i} missing 'code'"
