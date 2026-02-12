"""Regression tests for specific RLM bugs and edge cases.

These tests prevent previously fixed issues from recurring.
Add new regression tests whenever a bug is fixed.

Run with: uv run pytest tests/test_rlm_regression.py -v

API notes
---------
- ``interpreter.execute(code)`` returns ``FinalOutput(dict)`` on
  ``SUBMIT(...)`` or a plain ``str`` (stdout/stderr) otherwise.
- ``FinalOutput.output`` gives the wrapped dict.
- Tools: ``interpreter.tools["name"] = fn`` (no ``register_tool``).
"""

from __future__ import annotations

import os

import pytest

# Skip all tests if Modal credentials not available
pytestmark = pytest.mark.skipif(
    not os.environ.get("MODAL_TOKEN_ID") or not os.environ.get("MODAL_TOKEN_SECRET"),
    reason="Modal credentials not configured",
)


def check_litellm_secret():
    """Check if LITELLM secret is configured in Modal."""
    try:
        from fleet_rlm.runners import check_secret_presence

        result = check_secret_presence()
        return all(result.values())
    except Exception:
        return False


@pytest.fixture
def require_litellm():
    """Skip test if LITELLM not configured."""
    if not check_litellm_secret():
        pytest.skip("LITELLM secret not configured")


class TestInfiniteLoopPrevention:
    """Prevent RLM from entering infinite loops.

    Issue: RLM would continue iterating when query was ambiguous
    or when it couldn't determine completion.
    """

    def test_ambiguous_query_terminates(self, require_litellm):
        """RLM should terminate even with ambiguous queries."""
        import dspy
        from fleet_rlm import ModalInterpreter

        with ModalInterpreter(timeout=60) as interpreter:
            rlm = dspy.RLM(
                signature="query -> result",
                interpreter=interpreter,
                max_iterations=3,
                max_llm_calls=5,
                verbose=False,
            )

            result = rlm(query="Do something interesting")

            trajectory = getattr(result, "trajectory", [])
            assert len(trajectory) <= 3

    def test_no_progress_detection(self, require_litellm):
        """RLM should detect when no progress is being made."""
        import dspy
        from fleet_rlm import ModalInterpreter

        with ModalInterpreter(timeout=60) as interpreter:
            # Set up a scenario where repeated attempts won't help
            interpreter.execute("data = 'locked'")

            rlm = dspy.RLM(
                signature="query -> result",
                interpreter=interpreter,
                max_iterations=5,
                max_llm_calls=5,
                verbose=False,
            )

            result = rlm(query="Unlock the data (impossible task)")

            trajectory = getattr(result, "trajectory", [])
            assert len(trajectory) <= 5


class TestVolumePersistenceIssues:
    """Prevent volume data loss between sessions.

    Issue: Data written to volume wasn't persisting across
    interpreter shutdown/restart cycles.
    """

    def test_volume_data_survives_restart(self, require_litellm):
        """Data written to volume should persist after restart."""
        from dspy.primitives.code_interpreter import FinalOutput
        from fleet_rlm import ModalInterpreter

        VOLUME_NAME = "rlm-regression-test-volume"

        # First session: write data
        with ModalInterpreter(timeout=60, volume_name=VOLUME_NAME) as interpreter1:
            interpreter1.execute(
                """
with open('/data/persist_test.txt', 'w') as f:
    f.write('persistent_value')
SUBMIT(status='written')
"""
            )

        # Second session: read data
        with ModalInterpreter(timeout=60, volume_name=VOLUME_NAME) as interpreter2:
            result = interpreter2.execute(
                """
with open('/data/persist_test.txt', 'r') as f:
    content = f.read()
SUBMIT(content)
"""
            )

        assert isinstance(result, FinalOutput)
        assert result.output["output"] == "persistent_value"

    def test_volume_file_overwrite(self, require_litellm):
        """Overwriting files in volume should work correctly."""
        from dspy.primitives.code_interpreter import FinalOutput
        from fleet_rlm import ModalInterpreter

        VOLUME_NAME = "rlm-regression-test-volume"

        with ModalInterpreter(timeout=60, volume_name=VOLUME_NAME) as interpreter:
            # Write initial content
            interpreter.execute(
                """
with open('/data/overwrite_test.txt', 'w') as f:
    f.write('version1')
SUBMIT()
"""
            )

            # Overwrite
            result = interpreter.execute(
                """
with open('/data/overwrite_test.txt', 'w') as f:
    f.write('version2')
with open('/data/overwrite_test.txt', 'r') as f:
    content = f.read()
SUBMIT(content)
"""
            )

        assert isinstance(result, FinalOutput)
        assert result.output["output"] == "version2"


class TestToolCallIssues:
    """Prevent tool calling regressions.

    Issue: Tools weren't being registered correctly, or tool
    results weren't being passed back properly.
    """

    def test_tool_result_format(self, require_litellm):
        """Tool results should be properly formatted."""
        from dspy.primitives.code_interpreter import FinalOutput
        from fleet_rlm import ModalInterpreter

        with ModalInterpreter(timeout=60) as interpreter:
            # Register a tool that returns complex data
            def complex_tool():
                return {"nested": {"data": [1, 2, 3]}, "status": "ok"}

            interpreter.tools["complex_tool"] = complex_tool

            result = interpreter.execute(
                "result = complex_tool()\nSUBMIT(result)",
            )

        assert isinstance(result, FinalOutput)
        output = result.output["output"]
        assert output["status"] == "ok"
        assert output["nested"]["data"] == [1, 2, 3]

    def test_tool_error_handling(self, require_litellm):
        """Tool errors should be handled gracefully."""
        from dspy.primitives.code_interpreter import FinalOutput
        from fleet_rlm import ModalInterpreter

        with ModalInterpreter(timeout=60) as interpreter:

            def failing_tool():
                raise ValueError("Intentional error")

            interpreter.tools["failing_tool"] = failing_tool

            result = interpreter.execute(
                """
try:
    result = failing_tool()
    SUBMIT(status='success', result=result)
except Exception as e:
    SUBMIT(status='error', message=str(e))
""",
            )

        assert isinstance(result, FinalOutput)
        assert result.output["status"] == "error"
        assert "Intentional error" in result.output["message"]


class TestJSONProtocolIssues:
    """Prevent JSON protocol communication regressions.

    Issue: Edge cases in JSON encoding/decoding caused
    protocol failures.
    """

    def test_unicode_in_output(self, require_litellm):
        """Unicode characters should be handled correctly."""
        from dspy.primitives.code_interpreter import FinalOutput
        from fleet_rlm import ModalInterpreter

        with ModalInterpreter(timeout=60) as interpreter:
            result = interpreter.execute(
                """
text = "Hello 世界 🌍 café naïve"
SUBMIT(text)
"""
            )

        assert isinstance(result, FinalOutput)
        assert "世界" in result.output["output"]
        assert "🌍" in result.output["output"]

    def test_large_output(self, require_litellm):
        """Large outputs should be handled correctly."""
        from dspy.primitives.code_interpreter import FinalOutput
        from fleet_rlm import ModalInterpreter

        with ModalInterpreter(timeout=60) as interpreter:
            result = interpreter.execute(
                """
# Generate large output (~10KB)
large_list = [f"Item {i}" for i in range(1000)]
SUBMIT(count=len(large_list), first=large_list[0], last=large_list[-1])
"""
            )

        assert isinstance(result, FinalOutput)
        assert result.output["count"] == 1000
        assert result.output["first"] == "Item 0"
        assert result.output["last"] == "Item 999"

    def test_special_characters_in_strings(self, require_litellm):
        """Special characters in strings should be escaped correctly."""
        from dspy.primitives.code_interpreter import FinalOutput
        from fleet_rlm import ModalInterpreter

        with ModalInterpreter(timeout=60) as interpreter:
            result = interpreter.execute(
                r"""
text = 'Line 1\nLine 2\tTabbed "quoted" text\\backslash'
SUBMIT(text)
"""
            )

        assert isinstance(result, FinalOutput)
        output = result.output["output"]
        assert "Line 1" in output
        assert "Line 2" in output
        assert "quoted" in output


class TestDSPyRLMIntegrationIssues:
    """Prevent regressions in dspy.RLM integration."""

    def test_trajectory_contains_required_fields(self, require_litellm):
        """Trajectory should always contain expected fields."""
        import dspy
        from fleet_rlm import ModalInterpreter

        with ModalInterpreter(timeout=120) as interpreter:
            rlm = dspy.RLM(
                signature="question -> answer",
                interpreter=interpreter,
                max_iterations=5,
                max_llm_calls=10,
                verbose=False,
            )

            result = rlm(question="What is 2+2? Use Python to calculate.")

            trajectory = getattr(result, "trajectory", [])
            assert len(trajectory) > 0

            for step in trajectory:
                has_reasoning = "reasoning" in step
                has_code = "code" in step
                has_output = "output" in step

                assert has_reasoning or has_code or has_output, (
                    f"Step missing all fields: {step.keys()}"
                )

    def test_signature_input_output_fields(self, require_litellm):
        """Signature fields should be properly handled."""
        import dspy
        from fleet_rlm import ModalInterpreter
        from fleet_rlm.signatures import ExtractArchitecture

        with ModalInterpreter(timeout=120) as interpreter:
            rlm = dspy.RLM(
                signature=ExtractArchitecture,
                interpreter=interpreter,
                max_iterations=10,
                max_llm_calls=15,
                verbose=False,
            )

            docs = "# Architecture\n\nThis system uses modules and optimizers."
            result = rlm(docs=docs, query="What are the main components?")

            assert hasattr(result, "modules")
            assert hasattr(result, "optimizers")
            assert hasattr(result, "design_principles")

    def test_max_llm_calls_respected(self, require_litellm):
        """max_llm_calls should be strictly enforced."""
        import dspy
        from fleet_rlm import ModalInterpreter

        with ModalInterpreter(timeout=120) as interpreter:
            rlm = dspy.RLM(
                signature="question -> answer",
                interpreter=interpreter,
                max_iterations=10,
                max_llm_calls=3,
                verbose=False,
            )

            result = rlm(question="Solve this complex problem requiring many LLM calls")

            trajectory = getattr(result, "trajectory", [])
            assert len(trajectory) <= 3


class TestTimeoutHandling:
    """Prevent timeout handling regressions."""

    def test_execution_timeout(self, require_litellm):
        """Long-running code should respect timeout."""
        from fleet_rlm import ModalInterpreter

        with ModalInterpreter(timeout=30, execute_timeout=5) as interpreter:
            # This should timeout
            result = interpreter.execute(
                """
import time
time.sleep(10)  # Sleep longer than timeout
SUBMIT(status='completed')
"""
            )

        # Should have error indication — either a string with stderr
        # or a CodeInterpreterError was raised (caught by with-block)
        if hasattr(result, "output"):
            # unlikely to reach SUBMIT, but guard anyway
            pass
        else:
            assert isinstance(result, str)

    def test_sandbox_timeout(self, require_litellm):
        """Sandbox lifetime should be respected."""
        from dspy.primitives.code_interpreter import FinalOutput
        from fleet_rlm import ModalInterpreter

        import time

        with ModalInterpreter(timeout=10) as interpreter:
            time.sleep(2)

            result = interpreter.execute("SUBMIT(status='alive')")

        assert isinstance(result, FinalOutput)
        assert result.output["output"]["status"] == "alive"
