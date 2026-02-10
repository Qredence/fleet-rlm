"""Mock tests for llm_query and llm_query_batched functionality.

These tests verify the llm_query logic without requiring Modal credentials
or a live sandbox. They mock the Modal-dependent parts and focus on:
- Call counting and max_llm_calls enforcement
- ThreadPoolExecutor behavior in llm_query_batched
- sub_lm usage
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest


class TestLLMQueryMock:
    """Test llm_query functionality with mocked Modal components."""

    def test_llm_query_increments_counter(self):
        """Test that llm_query increments the call counter."""
        from fleet_rlm.interpreter import ModalInterpreter

        # Create interpreter with mocked sandbox
        with patch.object(ModalInterpreter, "start"):
            with patch.object(ModalInterpreter, "shutdown"):
                interp = ModalInterpreter(max_llm_calls=10)

                # Mock _query_sub_lm to avoid actual LLM calls
                interp._query_sub_lm = MagicMock(return_value="mocked response")

                # Initial counter should be 0
                assert interp._llm_call_count == 0

                # Call llm_query
                result = interp.llm_query("test prompt")

                # Counter should be incremented
                assert interp._llm_call_count == 1
                assert result == "mocked response"

    def test_max_llm_calls_enforced(self):
        """Test that max_llm_calls limit raises RuntimeError."""
        from fleet_rlm.interpreter import ModalInterpreter

        with patch.object(ModalInterpreter, "start"):
            with patch.object(ModalInterpreter, "shutdown"):
                interp = ModalInterpreter(max_llm_calls=3)
                interp._query_sub_lm = MagicMock(return_value="response")

                # Make 3 calls (at the limit)
                interp.llm_query("call 1")
                interp.llm_query("call 2")
                interp.llm_query("call 3")

                assert interp._llm_call_count == 3

                # 4th call should raise RuntimeError
                with pytest.raises(RuntimeError) as exc_info:
                    interp.llm_query("call 4")

                assert "LLM call limit exceeded" in str(exc_info.value)
                assert "3 + 1 > 3" in str(exc_info.value)

    def test_llm_query_empty_prompt_raises(self):
        """Test that empty prompt raises ValueError."""
        from fleet_rlm.interpreter import ModalInterpreter

        with patch.object(ModalInterpreter, "start"):
            with patch.object(ModalInterpreter, "shutdown"):
                interp = ModalInterpreter()

                with pytest.raises(ValueError) as exc_info:
                    interp.llm_query("")

                assert "prompt cannot be empty" in str(exc_info.value)

    def test_llm_query_batched_increments_counter(self):
        """Test that llm_query_batched increments counter by batch size."""
        from fleet_rlm.interpreter import ModalInterpreter

        with patch.object(ModalInterpreter, "start"):
            with patch.object(ModalInterpreter, "shutdown"):
                interp = ModalInterpreter(max_llm_calls=10)
                interp._query_sub_lm = MagicMock(return_value="response")

                prompts = ["prompt 1", "prompt 2", "prompt 3"]
                results = interp.llm_query_batched(prompts)

                # Counter should be incremented by batch size
                assert interp._llm_call_count == 3
                assert len(results) == 3
                assert all(r == "response" for r in results)

    def test_llm_query_batched_empty_list(self):
        """Test that empty list returns empty results."""
        from fleet_rlm.interpreter import ModalInterpreter

        with patch.object(ModalInterpreter, "start"):
            with patch.object(ModalInterpreter, "shutdown"):
                interp = ModalInterpreter()
                interp._query_sub_lm = MagicMock(return_value="response")

                results = interp.llm_query_batched([])

                assert results == []
                assert interp._llm_call_count == 0

    def test_llm_query_batched_exceeds_limit(self):
        """Test that batched calls that exceed limit raise RuntimeError."""
        from fleet_rlm.interpreter import ModalInterpreter

        with patch.object(ModalInterpreter, "start"):
            with patch.object(ModalInterpreter, "shutdown"):
                interp = ModalInterpreter(max_llm_calls=5)
                interp._query_sub_lm = MagicMock(return_value="response")

                # First batch uses 3 calls
                interp.llm_query_batched(["a", "b", "c"])
                assert interp._llm_call_count == 3

                # Second batch of 3 would exceed limit (3 + 3 = 6 > 5)
                with pytest.raises(RuntimeError) as exc_info:
                    interp.llm_query_batched(["d", "e", "f"])

                assert "LLM call limit exceeded" in str(exc_info.value)
                assert "3 + 3 > 5" in str(exc_info.value)

    def test_llm_query_batched_raises_on_subquery_failure(self):
        """Any failed batched sub-query should raise RuntimeError."""
        from fleet_rlm.interpreter import ModalInterpreter

        def flaky_query(prompt):
            if prompt == "bad":
                raise ValueError("bad prompt")
            return f"ok:{prompt}"

        with patch.object(ModalInterpreter, "start"):
            with patch.object(ModalInterpreter, "shutdown"):
                interp = ModalInterpreter(max_llm_calls=10)
                interp._query_sub_lm = MagicMock(side_effect=flaky_query)

                with pytest.raises(RuntimeError) as exc_info:
                    interp.llm_query_batched(["good", "bad", "also-good"])

                msg = str(exc_info.value)
                assert "llm_query_batched failed" in msg
                assert "prompt[1]: ValueError: bad prompt" in msg

    def test_sub_lm_used_when_provided(self):
        """Test that sub_lm is used for queries when provided."""
        from fleet_rlm.interpreter import ModalInterpreter

        mock_sub_lm = MagicMock()
        mock_sub_lm.return_value = [{"text": "sub lm response"}]

        with patch.object(ModalInterpreter, "start"):
            with patch.object(ModalInterpreter, "shutdown"):
                interp = ModalInterpreter(sub_lm=mock_sub_lm)

                result = interp.llm_query("test")

                # sub_lm should have been called
                mock_sub_lm.assert_called_once_with("test")
                assert result == "sub lm response"

    def test_default_lm_used_when_no_sub_lm(self):
        """Test that dspy.settings.lm is used when sub_lm not provided."""
        from fleet_rlm.interpreter import ModalInterpreter

        mock_default_lm = MagicMock()
        mock_default_lm.return_value = [{"text": "default lm response"}]

        with patch("fleet_rlm.interpreter.dspy.settings") as mock_settings:
            mock_settings.lm = mock_default_lm

            with patch.object(ModalInterpreter, "start"):
                with patch.object(ModalInterpreter, "shutdown"):
                    interp = ModalInterpreter()

                    result = interp.llm_query("test")

                    # Default LM should have been called
                    mock_default_lm.assert_called_once_with("test")
                    assert result == "default lm response"

    def test_query_sub_lm_handles_string_response(self):
        """Test _query_sub_lm handles plain string responses."""
        from fleet_rlm.interpreter import ModalInterpreter

        mock_lm = MagicMock()
        mock_lm.return_value = "plain string response"

        with patch.object(ModalInterpreter, "start"):
            with patch.object(ModalInterpreter, "shutdown"):
                interp = ModalInterpreter(sub_lm=mock_lm)

                result = interp._query_sub_lm("test")

                assert result == "plain string response"

    def test_query_sub_lm_handles_list_response(self):
        """Test _query_sub_lm handles list responses."""
        from fleet_rlm.interpreter import ModalInterpreter

        mock_lm = MagicMock()
        mock_lm.return_value = ["item1", "item2"]

        with patch.object(ModalInterpreter, "start"):
            with patch.object(ModalInterpreter, "shutdown"):
                interp = ModalInterpreter(sub_lm=mock_lm)

                result = interp._query_sub_lm("test")

                assert result == "item1"

    def test_query_sub_lm_handles_dict_response(self):
        """Test _query_sub_lm handles dict with 'text' key."""
        from fleet_rlm.interpreter import ModalInterpreter

        mock_lm = MagicMock()
        mock_lm.return_value = [{"text": "extracted text", "other": "ignored"}]

        with patch.object(ModalInterpreter, "start"):
            with patch.object(ModalInterpreter, "shutdown"):
                interp = ModalInterpreter(sub_lm=mock_lm)

                result = interp._query_sub_lm("test")

                assert result == "extracted text"

    def test_query_sub_lm_raises_when_no_lm(self):
        """Test that RuntimeError is raised when no LM is configured."""
        from fleet_rlm.interpreter import ModalInterpreter

        with patch("fleet_rlm.interpreter.dspy.settings") as mock_settings:
            mock_settings.lm = None

            with patch.object(ModalInterpreter, "start"):
                with patch.object(ModalInterpreter, "shutdown"):
                    interp = ModalInterpreter()

                    with pytest.raises(RuntimeError) as exc_info:
                        interp._query_sub_lm("test")

                    assert "No LM configured" in str(exc_info.value)

    def test_llm_query_batched_concurrent_execution(self):
        """Test that llm_query_batched executes queries concurrently."""
        from fleet_rlm.interpreter import ModalInterpreter

        execution_order = []
        execution_lock = threading.Lock()

        def slow_query(prompt):
            with execution_lock:
                execution_order.append(f"start:{prompt}")
            time.sleep(0.1)  # Simulate slow query
            with execution_lock:
                execution_order.append(f"end:{prompt}")
            return f"response:{prompt}"

        with patch.object(ModalInterpreter, "start"):
            with patch.object(ModalInterpreter, "shutdown"):
                interp = ModalInterpreter(max_llm_calls=10)
                interp._query_sub_lm = MagicMock(side_effect=slow_query)

                start_time = time.time()
                prompts = ["a", "b", "c"]
                results = interp.llm_query_batched(prompts)
                elapsed = time.time() - start_time

                # Should complete in ~0.1s (concurrent), not ~0.3s (sequential)
                assert elapsed < 0.25, f"Expected concurrent execution, took {elapsed}s"

                # All prompts should be processed
                assert len(results) == 3
                assert set(results) == {"response:a", "response:b", "response:c"}

    def test_thread_safe_call_counting(self):
        """Test that call counting is thread-safe with concurrent batches."""
        from fleet_rlm.interpreter import ModalInterpreter

        call_times = []
        call_lock = threading.Lock()

        def track_call(prompt):
            with call_lock:
                call_times.append(time.time())
            time.sleep(0.05)
            return "response"

        with patch.object(ModalInterpreter, "start"):
            with patch.object(ModalInterpreter, "shutdown"):
                interp = ModalInterpreter(max_llm_calls=20)
                interp._query_sub_lm = MagicMock(side_effect=track_call)

                # Run two batches concurrently
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    future1 = executor.submit(interp.llm_query_batched, ["a", "b", "c"])
                    future2 = executor.submit(interp.llm_query_batched, ["d", "e", "f"])

                    future1.result()
                    future2.result()

                # Total should be exactly 6
                assert interp._llm_call_count == 6

    def test_tool_names_include_llm_query(self):
        """Test that _tool_names includes built-in RLM tools."""
        from fleet_rlm.interpreter import ModalInterpreter

        with patch.object(ModalInterpreter, "start"):
            with patch.object(ModalInterpreter, "shutdown"):
                interp = ModalInterpreter()

                tool_names = interp._tool_names()

                assert "llm_query" in tool_names
                assert "llm_query_batched" in tool_names

    def test_tool_names_include_user_tools(self):
        """Test that _tool_names includes user-registered tools."""
        from fleet_rlm.interpreter import ModalInterpreter

        def custom_tool():
            return "custom"

        with patch.object(ModalInterpreter, "start"):
            with patch.object(ModalInterpreter, "shutdown"):
                interp = ModalInterpreter()
                interp.tools = {"custom_tool": custom_tool}

                tool_names = interp._tool_names()

                assert "llm_query" in tool_names
                assert "llm_query_batched" in tool_names
                assert "custom_tool" in tool_names

    def test_start_resets_llm_call_count_for_new_session(self):
        """Fresh sandbox starts should reset per-session llm call counters."""
        from fleet_rlm.interpreter import ModalInterpreter

        class _DummyStdin:
            def write(self, _value):
                return None

            def flush(self):
                return None

        class _DummyProc:
            def __init__(self):
                self.stdin = _DummyStdin()
                self.stdout = iter(())
                self.stderr = iter(())

        class _DummySandbox:
            def exec(self, *_args, **_kwargs):
                return _DummyProc()

            def terminate(self):
                return None

        with patch("fleet_rlm.interpreter.modal.Sandbox.create", return_value=_DummySandbox()):
            interp = ModalInterpreter()
            interp._llm_call_count = 7
            interp.start()
            assert interp._llm_call_count == 0
            interp.shutdown()


class TestCheckAndIncrement:
    """Test the _check_and_increment_llm_calls method directly."""

    def test_single_increment(self):
        """Test single call increment."""
        from fleet_rlm.interpreter import ModalInterpreter

        with patch.object(ModalInterpreter, "start"):
            with patch.object(ModalInterpreter, "shutdown"):
                interp = ModalInterpreter(max_llm_calls=5)

                interp._check_and_increment_llm_calls(1)
                assert interp._llm_call_count == 1

                interp._check_and_increment_llm_calls(1)
                assert interp._llm_call_count == 2

    def test_batch_increment(self):
        """Test batch call increment."""
        from fleet_rlm.interpreter import ModalInterpreter

        with patch.object(ModalInterpreter, "start"):
            with patch.object(ModalInterpreter, "shutdown"):
                interp = ModalInterpreter(max_llm_calls=10)

                interp._check_and_increment_llm_calls(5)
                assert interp._llm_call_count == 5

                interp._check_and_increment_llm_calls(3)
                assert interp._llm_call_count == 8

    def test_exactly_at_limit(self):
        """Test that exactly at limit doesn't raise."""
        from fleet_rlm.interpreter import ModalInterpreter

        with patch.object(ModalInterpreter, "start"):
            with patch.object(ModalInterpreter, "shutdown"):
                interp = ModalInterpreter(max_llm_calls=5)

                interp._check_and_increment_llm_calls(5)
                assert interp._llm_call_count == 5

    def test_one_over_limit_raises(self):
        """Test that one over limit raises RuntimeError."""
        from fleet_rlm.interpreter import ModalInterpreter

        with patch.object(ModalInterpreter, "start"):
            with patch.object(ModalInterpreter, "shutdown"):
                interp = ModalInterpreter(max_llm_calls=5)

                interp._check_and_increment_llm_calls(5)

                with pytest.raises(RuntimeError) as exc_info:
                    interp._check_and_increment_llm_calls(1)

                assert "LLM call limit exceeded" in str(exc_info.value)

    def test_batch_over_limit_raises(self):
        """Test that batch over limit raises RuntimeError."""
        from fleet_rlm.interpreter import ModalInterpreter

        with patch.object(ModalInterpreter, "start"):
            with patch.object(ModalInterpreter, "shutdown"):
                interp = ModalInterpreter(max_llm_calls=5)

                interp._check_and_increment_llm_calls(3)

                # 3 + 5 = 8 > 5, should raise
                with pytest.raises(RuntimeError) as exc_info:
                    interp._check_and_increment_llm_calls(5)

                assert "3 + 5 > 5" in str(exc_info.value)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
