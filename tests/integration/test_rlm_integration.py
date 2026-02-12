"""Integration tests for dspy.RLM + ModalInterpreter.

These tests require Modal credentials (LITELLM secret configured).
They validate the full variable-space -> sandbox -> recursion pipeline.

Run with: uv run pytest tests/test_rlm_integration.py -v

API notes
---------
- ``interpreter.execute(code)`` returns ``FinalOutput(dict)`` when the
  sandboxed code calls ``SUBMIT(...)``, or a plain ``str`` (captured
  stdout/stderr) otherwise.
- ``FinalOutput.output`` gives the wrapped dict.
- Tools are registered via ``interpreter.tools["name"] = fn``.
- Output field names are set via ``interpreter.output_fields``.
- ``execute()`` does **not** accept ``tool_names`` or
  ``output_names`` keyword arguments.
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
def interpreter():
    """Fixture providing a started ModalInterpreter."""
    from fleet_rlm.interpreter import ModalInterpreter

    if not check_litellm_secret():
        pytest.skip("LITELLM secret not configured")

    interp = ModalInterpreter(timeout=120)
    interp.start()
    yield interp
    interp.shutdown()


@pytest.fixture
def interpreter_with_volume():
    """Fixture providing a ModalInterpreter with volume support."""
    from fleet_rlm.interpreter import ModalInterpreter

    if not check_litellm_secret():
        pytest.skip("LITELLM secret not configured")

    interp = ModalInterpreter(
        timeout=120,
        volume_name="rlm-test-volume",
    )
    interp.start()
    yield interp
    interp.shutdown()


class TestBasicExecution:
    """Test basic code execution in Modal sandbox."""

    def test_submit_returns_values(self, interpreter):
        """Test SUBMIT() returns structured values correctly."""
        from dspy.primitives.code_interpreter import FinalOutput

        result = interpreter.execute("x = 42\nSUBMIT(x)")

        assert isinstance(result, FinalOutput)
        assert result.output["output"] == 42

    def test_submit_multiple_values(self, interpreter):
        """Test SUBMIT() with multiple positional args."""
        from dspy.primitives.code_interpreter import FinalOutput

        result = interpreter.execute("a, b = 1, 2\nSUBMIT(a, b)")

        assert isinstance(result, FinalOutput)
        assert result.output["output"] == [1, 2]

    def test_submit_named_output(self, interpreter):
        """Test SUBMIT() with output_fields configuration."""
        from dspy.primitives.code_interpreter import FinalOutput

        interpreter.output_fields = [{"name": "name"}, {"name": "value"}]

        result = interpreter.execute(
            "name, value = 'test', 123\nSUBMIT(name, value)",
        )

        assert isinstance(result, FinalOutput)
        assert result.output["name"] == "test"
        assert result.output["value"] == 123

    def test_submit_kwargs(self, interpreter):
        """Test SUBMIT() with keyword arguments."""
        from dspy.primitives.code_interpreter import FinalOutput

        result = interpreter.execute("SUBMIT(result='success', count=5)")

        assert isinstance(result, FinalOutput)
        assert result.output["result"] == "success"
        assert result.output["count"] == 5

    def test_stdout_capture(self, interpreter):
        """Test stdout is captured correctly (no SUBMIT)."""
        result = interpreter.execute("print('Hello')\nprint('World')")

        assert isinstance(result, str)
        assert "Hello" in result
        assert "World" in result

    def test_stderr_capture(self, interpreter):
        """Test stderr is captured correctly (no SUBMIT)."""
        result = interpreter.execute("import sys\nprint('Warning', file=sys.stderr)")

        assert isinstance(result, str)
        assert "Warning" in result


class TestVariablePersistence:
    """Test variables persist across execute() calls."""

    def test_variable_survives_between_calls(self, interpreter):
        """Test variables set in first call are available in second."""
        from dspy.primitives.code_interpreter import FinalOutput

        # First call sets variable
        interpreter.execute("counter = 1")

        # Second call accesses and modifies it
        result = interpreter.execute("counter += 1\nSUBMIT(counter)")

        assert isinstance(result, FinalOutput)
        assert result.output["output"] == 2

    def test_complex_data_structures_persist(self, interpreter):
        """Test dicts, lists persist correctly."""
        from dspy.primitives.code_interpreter import FinalOutput

        interpreter.execute("data = {'items': [1, 2, 3], 'count': 3}")

        result = interpreter.execute("data['items'].append(4)\nSUBMIT(data)")

        assert isinstance(result, FinalOutput)
        assert result.output["output"]["items"] == [1, 2, 3, 4]
        assert result.output["output"]["count"] == 3

    def test_module_imports_persist(self, interpreter):
        """Test imported modules remain available."""
        from dspy.primitives.code_interpreter import FinalOutput

        interpreter.execute("import json\ndata = json.dumps({'key': 'value'})")

        result = interpreter.execute("parsed = json.loads(data)\nSUBMIT(parsed)")

        assert isinstance(result, FinalOutput)
        assert result.output["output"] == {"key": "value"}


class TestToolRegistration:
    """Test custom tool registration and invocation."""

    def test_tool_available_in_sandbox(self, interpreter):
        """Test registered tool is callable in sandbox."""
        from fleet_rlm.tools import regex_extract

        interpreter.tools["regex_extract"] = regex_extract

        result = interpreter.execute(
            """
import re
text = "# Hello\\n# World\\nContent"
headers = regex_extract(text, r'^# (.+)$', re.MULTILINE)
SUBMIT(headers=headers)
"""
        )

        # Tool returns list of matches; result is FinalOutput with .output dict
        result_str = str(result.output) if hasattr(result, "output") else str(result)
        assert "Hello" in result_str
        assert "World" in result_str

    def test_tool_with_args(self, interpreter):
        """Test tool with positional and keyword args."""

        def multiply(a, b, factor=1):
            return a * b * factor

        interpreter.tools["multiply"] = multiply

        result = interpreter.execute(
            "result = multiply(3, 4, factor=2)\nSUBMIT(result)",
        )

        assert result.output["output"] == 24


class TestErrorHandling:
    """Test error handling in sandbox execution."""

    def test_syntax_error_reported(self, interpreter):
        """Test syntax errors are captured in stderr."""
        result = interpreter.execute("if True\n    print('missing colon')")

        # execute() returns a string containing stderr when no SUBMIT
        assert isinstance(result, str)
        assert "SyntaxError" in result or "IndentationError" in result

    def test_runtime_error_reported(self, interpreter):
        """Test runtime errors are captured in stderr."""
        result = interpreter.execute("x = 1 / 0")

        assert isinstance(result, str)
        assert "ZeroDivisionError" in result

    def test_name_error_reported(self, interpreter):
        """Test NameError for undefined variables."""
        result = interpreter.execute("print(undefined_variable)")

        assert isinstance(result, str)
        assert "NameError" in result


class TestVolumeSupport:
    """Test Modal volume integration."""

    def test_volume_mounted_at_path(self, interpreter_with_volume):
        """Test volume is accessible at mount path."""
        from dspy.primitives.code_interpreter import FinalOutput

        result = interpreter_with_volume.execute(
            "import os\nfiles = os.listdir('/data')\nSUBMIT(files)"
        )

        assert isinstance(result, FinalOutput)
        assert isinstance(result.output["output"], list)

    def test_file_persistence(self, interpreter_with_volume):
        """Test files written to volume persist."""
        from dspy.primitives.code_interpreter import FinalOutput

        # Write file
        interpreter_with_volume.execute(
            """
with open('/data/test_persist.txt', 'w') as f:
    f.write('persistent data')
SUBMIT()
"""
        )

        # Read it back
        result = interpreter_with_volume.execute(
            """
with open('/data/test_persist.txt', 'r') as f:
    content = f.read()
SUBMIT(content)
"""
        )

        assert isinstance(result, FinalOutput)
        assert result.output["output"] == "persistent data"


class TestDSPyRLMIntegration:
    """Test full dspy.RLM integration."""

    def test_rlm_basic_question(self):
        """Test dspy.RLM with basic question-answering."""
        import dspy
        from fleet_rlm import ModalInterpreter

        if not check_litellm_secret():
            pytest.skip("LITELLM secret not configured")

        with ModalInterpreter(timeout=120) as interpreter:
            rlm = dspy.RLM(
                signature="question -> answer",
                interpreter=interpreter,
                max_iterations=5,
                max_llm_calls=10,
                verbose=False,
            )

            result = rlm(question="What is 2 + 2? Calculate using Python.")

            assert hasattr(result, "answer")
            assert "4" in str(result.answer)

    def test_rlm_trajectory_captured(self):
        """Test dspy.RLM captures trajectory."""
        import dspy
        from fleet_rlm import ModalInterpreter

        if not check_litellm_secret():
            pytest.skip("LITELLM secret not configured")

        with ModalInterpreter(timeout=120) as interpreter:
            rlm = dspy.RLM(
                signature="question -> answer",
                interpreter=interpreter,
                max_iterations=5,
                max_llm_calls=10,
                verbose=False,
            )

            result = rlm(question="What is 2 + 2?")

            trajectory = getattr(result, "trajectory", [])
            assert len(trajectory) > 0

            for step in trajectory:
                assert "reasoning" in step or "code" in step

    def test_rlm_respects_max_iterations(self):
        """Test dspy.RLM respects iteration limit."""
        import dspy
        from fleet_rlm import ModalInterpreter

        if not check_litellm_secret():
            pytest.skip("LITELLM secret not configured")

        with ModalInterpreter(timeout=120) as interpreter:
            rlm = dspy.RLM(
                signature="question -> answer",
                interpreter=interpreter,
                max_iterations=2,
                max_llm_calls=5,
                verbose=False,
            )

            result = rlm(
                question="Analyze this very complex problem requiring many steps"
            )

            trajectory = getattr(result, "trajectory", [])
            assert len(trajectory) <= 2


class TestSandboxHelpers:
    """Test injected sandbox-side helpers."""

    def test_peek_helper(self, interpreter):
        """Test peek() helper is available and works."""
        from dspy.primitives.code_interpreter import FinalOutput

        result = interpreter.execute(
            """
text = "Hello World, this is a long text"
sliced = peek(text, 0, 5)
SUBMIT(sliced)
"""
        )

        assert isinstance(result, FinalOutput)
        assert result.output["output"] == "Hello"

    def test_grep_helper(self, interpreter):
        """Test grep() helper is available and works."""
        from dspy.primitives.code_interpreter import FinalOutput

        result = interpreter.execute(
            """
text = "line one\\nline two\\nline three\\nfour"
hits = grep(text, "line")
SUBMIT(hits)
"""
        )

        assert isinstance(result, FinalOutput)
        assert len(result.output["output"]) == 3

    def test_chunk_by_size_helper(self, interpreter):
        """Test chunk_by_size() helper is available and works."""
        from dspy.primitives.code_interpreter import FinalOutput

        result = interpreter.execute(
            """
text = "abcdefghij"
chunks = chunk_by_size(text, 4, 0)
SUBMIT(chunks)
"""
        )

        assert isinstance(result, FinalOutput)
        assert result.output["output"] == ["abcd", "efgh", "ij"]

    def test_buffer_helpers(self, interpreter):
        """Test add_buffer / get_buffer / clear_buffer helpers."""
        from dspy.primitives.code_interpreter import FinalOutput

        result = interpreter.execute(
            """
add_buffer("results", "first")
add_buffer("results", "second")
buf = get_buffer("results")
SUBMIT(buf)
"""
        )

        assert isinstance(result, FinalOutput)
        assert result.output["output"] == ["first", "second"]

    def test_chunk_by_headers_helper(self, interpreter):
        """Test chunk_by_headers() helper is available and works."""
        from dspy.primitives.code_interpreter import FinalOutput

        result = interpreter.execute(
            """
text = "# Title\\nContent\\n## Sub\\nMore"
chunks = chunk_by_headers(text)
SUBMIT(len(chunks))
"""
        )

        assert isinstance(result, FinalOutput)
        assert result.output["output"] >= 2
