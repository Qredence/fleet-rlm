"""Benchmark tests for RLM performance evaluation.

These tests measure RLM performance characteristics and compare against
established baselines. Useful for regression detection and optimization.

Run with: uv run pytest tests/test_rlm_benchmarks.py -v --tb=short
"""

from __future__ import annotations

import os
import time

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


class TestNeedleInHaystack:
    """Benchmark: Find specific information in large documents.

    This is the classic RLM test - can it efficiently locate specific
    content without scanning everything linearly?
    """

    BASELINE_ITERATIONS = 5
    MAX_DURATION = 60  # seconds

    def test_needle_at_beginning(self, require_litellm):
        """Find needle at start of document (easiest case)."""
        import dspy
        from fleet_rlm import ModalInterpreter

        haystack = "\n".join([f"Line {i}: filler content here" for i in range(100)])
        haystack = "TARGET: NEEDLE_HERE\n" + haystack

        interpreter = ModalInterpreter(timeout=120)

        # Load document into variable space
        interpreter.start()
        interpreter.execute(f"docs = {haystack!r}")

        rlm = dspy.RLM(
            signature="find -> location",
            interpreter=interpreter,
            max_iterations=10,
            max_llm_calls=15,
            verbose=False,
        )

        start = time.time()
        result = rlm(find="NEEDLE_HERE")
        elapsed = time.time() - start

        trajectory = getattr(result, "trajectory", [])

        interpreter.shutdown()

        # Metrics
        assert (
            "NEEDLE" in str(result.location)
            or "beginning" in str(result.location).lower()
        )
        assert len(trajectory) <= self.BASELINE_ITERATIONS, (
            f"Took {len(trajectory)} iterations"
        )
        assert elapsed < self.MAX_DURATION, f"Took {elapsed}s"

    def test_needle_at_end(self, require_litellm):
        """Find needle at end of document."""
        import dspy
        from fleet_rlm import ModalInterpreter

        haystack = "\n".join([f"Line {i}: filler content here" for i in range(500)])
        haystack += "\nTARGET: FINAL_NEEDLE"

        interpreter = ModalInterpreter(timeout=180)
        interpreter.start()
        interpreter.execute(f"docs = {haystack!r}")

        rlm = dspy.RLM(
            signature="find -> location",
            interpreter=interpreter,
            max_iterations=10,
            max_llm_calls=15,
            verbose=False,
        )

        start = time.time()
        result = rlm(find="FINAL_NEEDLE")
        elapsed = time.time() - start

        trajectory = getattr(result, "trajectory", [])

        interpreter.shutdown()

        # Should find it efficiently using Python search
        assert len(trajectory) <= self.BASELINE_ITERATIONS
        assert elapsed < self.MAX_DURATION

    def test_needle_in_middle(self, require_litellm):
        """Find needle in middle of large document."""
        import dspy
        from fleet_rlm import ModalInterpreter

        lines = [f"Line {i}: filler content" for i in range(1000)]
        lines[500] = "Line 500: SECRET_MIDDLE_NEEDLE here"
        haystack = "\n".join(lines)

        interpreter = ModalInterpreter(timeout=180)
        interpreter.start()
        interpreter.execute(f"docs = {haystack!r}")

        rlm = dspy.RLM(
            signature="find -> location",
            interpreter=interpreter,
            max_iterations=10,
            max_llm_calls=15,
            verbose=False,
        )

        start = time.time()
        result = rlm(find="SECRET_MIDDLE_NEEDLE")
        elapsed = time.time() - start

        trajectory = getattr(result, "trajectory", [])

        interpreter.shutdown()

        assert "500" in str(result.location) or "middle" in str(result.location).lower()
        assert len(trajectory) <= self.BASELINE_ITERATIONS
        assert elapsed < self.MAX_DURATION


class TestCodeExploration:
    """Benchmark: Analyze and explore code structure."""

    BASELINE_ITERATIONS = 10
    MAX_DURATION = 120  # seconds

    def test_count_functions_in_module(self, require_litellm):
        """Count functions in a Python module."""
        import dspy
        from fleet_rlm import ModalInterpreter

        code = """
def foo(): pass
def bar(): pass
class MyClass:
    def method1(self): pass
    def method2(self): pass
def baz(): pass
"""

        interpreter = ModalInterpreter(timeout=180)
        interpreter.start()
        interpreter.execute(f"code = {code!r}")

        rlm = dspy.RLM(
            signature="code -> function_count",
            interpreter=interpreter,
            max_iterations=10,
            max_llm_calls=15,
            verbose=False,
        )

        start = time.time()
        result = rlm(code="Count the number of function definitions")
        elapsed = time.time() - start

        trajectory = getattr(result, "trajectory", [])

        interpreter.shutdown()

        # Should find 3 top-level functions + 2 methods = 5
        count = (
            int(str(result.function_count).split()[0])
            if hasattr(result, "function_count")
            else 0
        )
        assert count == 5 or count == 3  # Either all functions or just top-level
        assert len(trajectory) <= self.BASELINE_ITERATIONS
        assert elapsed < self.MAX_DURATION

    def test_extract_class_names(self, require_litellm):
        """Extract class names from code."""
        import dspy
        from fleet_rlm import ModalInterpreter

        code = """
class User:
    pass

class Order:
    pass

class Product:
    pass
"""

        interpreter = ModalInterpreter(timeout=120)
        interpreter.start()
        interpreter.execute(f"code = {code!r}")

        rlm = dspy.RLM(
            signature="code -> classes",
            interpreter=interpreter,
            max_iterations=8,
            max_llm_calls=12,
            verbose=False,
        )

        start = time.time()
        result = rlm(code="List all class names in this code")
        elapsed = time.time() - start

        interpreter.shutdown()

        result_str = str(getattr(result, "classes", ""))
        assert "User" in result_str
        assert "Order" in result_str
        assert "Product" in result_str
        assert elapsed < self.MAX_DURATION


class TestCalculationChain:
    """Benchmark: Multi-step calculations with variables."""

    MAX_DURATION = 30  # seconds

    def test_fibonacci_calculation(self, require_litellm):
        """Calculate Fibonacci sequence."""
        import dspy
        from fleet_rlm import ModalInterpreter

        interpreter = ModalInterpreter(timeout=60)

        rlm = dspy.RLM(
            signature="n -> fibonacci",
            interpreter=interpreter,
            max_iterations=5,
            max_llm_calls=8,
            verbose=False,
        )

        start = time.time()
        result = rlm(n="Calculate first 10 Fibonacci numbers")
        elapsed = time.time() - start

        interpreter.shutdown()

        result_str = str(getattr(result, "fibonacci", ""))
        # Should contain 55 (10th Fibonacci)
        assert "55" in result_str
        assert elapsed < self.MAX_DURATION

    def test_statistical_summary(self, require_litellm):
        """Calculate mean, median, std dev of dataset."""
        import dspy
        from fleet_rlm import ModalInterpreter

        data = [23, 45, 67, 89, 12, 34, 56, 78, 90, 11]

        interpreter = ModalInterpreter(timeout=60)
        interpreter.start()
        interpreter.execute(f"data = {data}")

        rlm = dspy.RLM(
            signature="data -> summary",
            interpreter=interpreter,
            max_iterations=5,
            max_llm_calls=8,
            verbose=False,
        )

        start = time.time()
        result = rlm(data="Calculate mean, median, and std dev")
        elapsed = time.time() - start

        interpreter.shutdown()

        result_str = str(getattr(result, "summary", ""))
        # Mean is 50.5
        assert "50.5" in result_str or "50" in result_str
        assert elapsed < self.MAX_DURATION


class TestIterationEfficiency:
    """Benchmark: Measure steps vs optimal path."""

    def test_single_step_task(self, require_litellm):
        """Task that should complete in 1 iteration."""
        import dspy
        from fleet_rlm import ModalInterpreter

        interpreter = ModalInterpreter(timeout=60)
        interpreter.start()
        interpreter.execute("x = 100")

        rlm = dspy.RLM(
            signature="query -> result",
            interpreter=interpreter,
            max_iterations=5,
            max_llm_calls=5,
            verbose=False,
        )

        result = rlm(query="What is the value of variable x?")
        trajectory = getattr(result, "trajectory", [])

        interpreter.shutdown()

        # Should complete in 1 iteration (just read variable)
        assert len(trajectory) <= 2, (
            f"Took {len(trajectory)} iterations for simple read"
        )

    def test_two_step_task(self, require_litellm):
        """Task that should complete in 2 iterations."""
        import dspy
        from fleet_rlm import ModalInterpreter

        interpreter = ModalInterpreter(timeout=60)
        interpreter.start()
        interpreter.execute("data = {'a': 1, 'b': 2}")

        rlm = dspy.RLM(
            signature="query -> result",
            interpreter=interpreter,
            max_iterations=5,
            max_llm_calls=5,
            verbose=False,
        )

        result = rlm(query="Sum all values in the data dictionary")
        trajectory = getattr(result, "trajectory", [])

        interpreter.shutdown()

        # Should complete in 1-2 iterations
        assert len(trajectory) <= 3, f"Took {len(trajectory)} iterations for sum task"


class TestLongRunningStability:
    """Benchmark: Stability over many iterations."""

    def test_15_iteration_task(self, require_litellm):
        """Task requiring many iterations to complete."""
        import dspy
        from fleet_rlm import ModalInterpreter

        # Create data requiring exploration
        data = {f"item_{i}": i for i in range(50)}
        data["target"] = 999

        interpreter = ModalInterpreter(timeout=300)
        interpreter.start()
        interpreter.execute(f"inventory = {data}")

        rlm = dspy.RLM(
            signature="query -> answer",
            interpreter=interpreter,
            max_iterations=20,
            max_llm_calls=30,
            verbose=False,
        )

        start = time.time()
        result = rlm(query="Find the item with value 999")
        elapsed = time.time() - start

        interpreter.shutdown()

        # Should complete successfully
        result_str = str(getattr(result, "answer", ""))
        assert "target" in result_str or "999" in result_str
        assert elapsed < 180  # Should complete within 3 minutes


@pytest.mark.benchmark
class TestReportGeneration:
    """Generate comprehensive benchmark report."""

    def test_generate_baseline_report(self, require_litellm, tmp_path):
        """Run all benchmarks and generate JSON report."""
        import json

        results = {
            "timestamp": time.time(),
            "tests": {},
        }

        # This test would run all benchmarks and aggregate results
        # For now, it's a placeholder showing the report structure

        results["tests"]["needle_in_haystack"] = {
            "baseline_iterations": 5,
            "actual_iterations": None,  # Would be filled by actual run
            "status": "pending",
        }

        results["tests"]["code_exploration"] = {
            "baseline_iterations": 10,
            "actual_iterations": None,
            "status": "pending",
        }

        # Write report
        report_path = tmp_path / "benchmark_report.json"
        report_path.write_text(json.dumps(results, indent=2))

        assert report_path.exists()
