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

from unittest.mock import MagicMock

import dspy

from fleet_rlm.react.rlm_runtime_modules import AnalyzeLongDocumentModule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chat_response(reasoning: str, code: str) -> str:
    """Return a string in DSPy ChatAdapter's [[ ## field ## ]] format."""
    return (
        f"[[ ## reasoning ## ]]\n{reasoning}\n\n"
        f"[[ ## code ## ]]\n{code}\n\n"
        "[[ ## completed ## ]]"
    )


class _ScriptedLM(dspy.LM):
    """Returns a fixed sequence of strings as LM responses.

    Raises AssertionError if called more times than scripted.
    """

    def __init__(self, responses: list[str]) -> None:
        super().__init__("mock/scripted")
        self._responses = responses
        self._idx = 0

    def __call__(self, prompt=None, messages=None, **kwargs):
        if self._idx >= len(self._responses):
            snippet = messages[-1]["content"][:600] if messages else ""
            raise AssertionError(
                f"LM called {self._idx + 1} times but only "
                f"{len(self._responses)} response(s) were scripted.\n\n"
                f"Last prompt:\n{snippet}"
            )
        resp = self._responses[self._idx]
        self._idx += 1
        return [resp]


def _make_mock_interpreter(side_effects: list):
    """Return a MagicMock whose .execute() cycles through side_effects.

    dspy.RLM calls ``interpreter.tools.update(...)`` and may set
    ``interpreter.output_fields``, so we pre-configure those attributes.
    """
    interp = MagicMock()
    interp.tools = {}  # real dict so .update() works
    interp.output_fields = []  # list so assignment works
    interp._tools_registered = False
    interp.execute.side_effect = side_effects
    return interp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRLMTrajectoryPersistence:
    """Phase A — trajectory accumulation correctness."""

    def test_trajectory_is_list_with_correct_step_count(self):
        """dspy.RLM must accumulate exactly one trajectory entry per REPL
        iteration before a successful SUBMIT."""
        from dspy.primitives.code_interpreter import FinalOutput

        lm = _ScriptedLM(
            [
                _chat_response(
                    reasoning="Let's peek at the start of the document.",
                    code="peek(context, 0, 50)",
                ),
                _chat_response(
                    reasoning="I need to find the revenue figure.",
                    code="grep(context, 'revenue')",
                ),
                _chat_response(
                    reasoning="Found revenue. Submitting now.",
                    code="SUBMIT(findings=['revenue is $5M'], answer='The revenue is $5M', sections_examined=2)",
                ),
            ]
        )
        dspy.settings.configure(lm=lm)

        interpreter = _make_mock_interpreter(
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

        lm = _ScriptedLM(
            [
                _chat_response(
                    reasoning="The answer is obvious.",
                    code="SUBMIT(findings=['trivial'], answer='trivial', sections_examined=0)",
                ),
            ]
        )
        dspy.settings.configure(lm=lm)

        interpreter = _make_mock_interpreter(
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

        lm = _ScriptedLM(
            [
                _chat_response(
                    "Reasoning.",
                    "SUBMIT(findings=['x'], answer='x', sections_examined=1)",
                ),
            ]
        )
        dspy.settings.configure(lm=lm)

        interpreter = _make_mock_interpreter(
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
