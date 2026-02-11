"""Tests for StatefulSandboxManager."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fleet_rlm.stateful_sandbox import (
    ExecutionRecord,
    SandboxResult,
    StatefulSandboxManager,
)


class TestExecutionRecord:
    """Tests for ExecutionRecord dataclass."""

    def test_execution_record_creation(self):
        """Test creating an ExecutionRecord."""
        from datetime import datetime, timezone

        record = ExecutionRecord(
            timestamp=datetime.now(timezone.utc),
            code_task="Test task",
            generated_code="print('hello')",
            result="hello",
            success=True,
            execution_time_ms=100,
        )
        assert record.code_task == "Test task"
        assert record.success is True
        assert record.execution_time_ms == 100


class TestSandboxResult:
    """Tests for SandboxResult dataclass."""

    def test_sandbox_result_success(self):
        """Test creating a successful SandboxResult."""
        result = SandboxResult(
            success=True,
            output="test output",
            execution_time_ms=50,
        )
        assert result.success is True
        assert result.output == "test output"
        assert result.error is None

    def test_sandbox_result_failure(self):
        """Test creating a failed SandboxResult."""
        result = SandboxResult(
            success=False,
            output=None,
            error="Something went wrong",
        )
        assert result.success is False
        assert result.output is None
        assert result.error == "Something went wrong"


class TestStatefulSandboxManager:
    """Tests for StatefulSandboxManager."""

    def test_initialization(self):
        """Test manager initialization."""
        manager = StatefulSandboxManager(
            volume_name="test-volume",
            workspace_path="/data/test-workspace",
        )
        assert manager.volume_name == "test-volume"
        assert manager.workspace_path == "/data/test-workspace"
        assert manager._execution_history == []
        assert manager._started is False

    def test_initialization_default_values(self):
        """Test manager initialization with default values."""
        manager = StatefulSandboxManager()
        assert manager.volume_name == "rlm-workspace"
        assert manager.workspace_path == "/data/workspace"
        assert manager._started is False

    @patch("fleet_rlm.stateful_sandbox.ModalInterpreter")
    def test_start(self, mock_interpreter_class):
        """Test starting the manager."""
        mock_interpreter = MagicMock()
        mock_interpreter_class.return_value = mock_interpreter

        manager = StatefulSandboxManager(interpreter=mock_interpreter)
        manager.start()

        assert manager._started is True
        mock_interpreter.start.assert_called_once()
        mock_interpreter.execute.assert_called_once()

    @patch("fleet_rlm.stateful_sandbox.ModalInterpreter")
    def test_shutdown(self, mock_interpreter_class):
        """Test shutting down the manager."""
        mock_interpreter = MagicMock()
        mock_interpreter_class.return_value = mock_interpreter

        manager = StatefulSandboxManager(interpreter=mock_interpreter)
        manager.start()
        manager.shutdown()

        assert manager._started is False
        mock_interpreter.shutdown.assert_called_once()

    @patch("fleet_rlm.stateful_sandbox.ModalInterpreter")
    def test_context_manager(self, mock_interpreter_class):
        """Test using manager as context manager."""
        mock_interpreter = MagicMock()
        mock_interpreter_class.return_value = mock_interpreter

        with StatefulSandboxManager(interpreter=mock_interpreter) as manager:
            assert manager._started is True

        mock_interpreter.shutdown.assert_called_once()

    @patch("fleet_rlm.stateful_sandbox.ModalInterpreter")
    def test_save_to_workspace(self, mock_interpreter_class):
        """Test saving to workspace."""
        from dspy.primitives.code_interpreter import FinalOutput

        mock_interpreter = MagicMock()
        mock_interpreter.execute.side_effect = [
            "Workspace ready at /data/workspace",
            FinalOutput({"status": "ok", "path": "/data/workspace/test.txt", "chars": 13}),
        ]
        mock_interpreter_class.return_value = mock_interpreter

        manager = StatefulSandboxManager(interpreter=mock_interpreter)
        result = manager.save_to_workspace("test.txt", "Hello, World!")

        assert result["status"] == "ok"
        assert result["filename"] == "test.txt"
        assert result["chars"] == 13
        assert mock_interpreter.execute.call_count == 2

    @patch("fleet_rlm.stateful_sandbox.ModalInterpreter")
    def test_save_to_workspace_surfaces_stderr_failures(self, mock_interpreter_class):
        """String stderr payloads should be returned as errors."""
        mock_interpreter = MagicMock()
        mock_interpreter.execute.side_effect = [
            "Workspace ready at /data/workspace",
            "[Error] PermissionError: denied",
        ]
        mock_interpreter_class.return_value = mock_interpreter

        manager = StatefulSandboxManager(interpreter=mock_interpreter)
        result = manager.save_to_workspace("test.txt", "Hello, World!")

        assert result["status"] == "error"
        assert "PermissionError" in result["error"]

    @patch("fleet_rlm.stateful_sandbox.ModalInterpreter")
    def test_load_from_workspace_success(self, mock_interpreter_class):
        """Test loading from workspace - success case."""
        from dspy.primitives.code_interpreter import FinalOutput

        mock_interpreter = MagicMock()
        mock_interpreter.execute.return_value = FinalOutput(
            {"status": "ok", "content": "Hello, World!", "chars": 13}
        )
        mock_interpreter_class.return_value = mock_interpreter

        manager = StatefulSandboxManager(interpreter=mock_interpreter)
        result = manager.load_from_workspace("test.txt")

        assert result["status"] == "ok"
        assert result["content"] == "Hello, World!"
        assert result["chars"] == 13

    @patch("fleet_rlm.stateful_sandbox.ModalInterpreter")
    def test_load_from_workspace_not_found(self, mock_interpreter_class):
        """Test loading from workspace - file not found."""
        from dspy.primitives.code_interpreter import FinalOutput

        mock_interpreter = MagicMock()
        mock_interpreter.execute.return_value = FinalOutput(
            {"status": "error", "error": "File not found: /data/workspace/missing.txt"}
        )
        mock_interpreter_class.return_value = mock_interpreter

        manager = StatefulSandboxManager(interpreter=mock_interpreter)
        result = manager.load_from_workspace("missing.txt")

        assert result["status"] == "error"
        assert "File not found" in result["error"]

    @patch("fleet_rlm.stateful_sandbox.ModalInterpreter")
    def test_list_workspace_files(self, mock_interpreter_class):
        """Test listing workspace files."""
        from dspy.primitives.code_interpreter import FinalOutput

        mock_interpreter = MagicMock()
        mock_interpreter.execute.return_value = FinalOutput(
            {"files": ["file1.txt", "file2.py"], "count": 2}
        )
        mock_interpreter_class.return_value = mock_interpreter

        manager = StatefulSandboxManager(interpreter=mock_interpreter)
        files = manager.list_workspace_files()

        assert files == ["file1.txt", "file2.py"]

    @patch("fleet_rlm.stateful_sandbox.ModalInterpreter")
    def test_delete_workspace_file(self, mock_interpreter_class):
        """Test deleting a workspace file."""
        from dspy.primitives.code_interpreter import FinalOutput

        mock_interpreter = MagicMock()
        mock_interpreter.execute.return_value = FinalOutput(
            {"status": "ok", "message": "Deleted /data/workspace/old.txt"}
        )
        mock_interpreter_class.return_value = mock_interpreter

        manager = StatefulSandboxManager(interpreter=mock_interpreter)
        result = manager.delete_workspace_file("old.txt")

        assert result["status"] == "ok"
        assert "Deleted" in result["message"]

    def test_get_session_history_empty(self):
        """Test getting session history when empty."""
        manager = StatefulSandboxManager()
        history = manager.get_session_history()
        assert history == []

    def test_get_session_stats_empty(self):
        """Test getting session stats when no executions."""
        manager = StatefulSandboxManager()
        stats = manager.get_session_stats()

        assert stats["total_executions"] == 0
        assert stats["successful_executions"] == 0
        assert stats["failed_executions"] == 0
        assert stats["success_rate"] == 0

    def test_clear_history(self):
        """Test clearing execution history."""
        from datetime import datetime, timezone

        manager = StatefulSandboxManager()
        manager._execution_history.append(
            ExecutionRecord(
                timestamp=datetime.now(timezone.utc),
                code_task="Test",
                generated_code="print('test')",
                result="test",
                success=True,
            )
        )
        assert len(manager._execution_history) == 1

        manager.clear_history()
        assert len(manager._execution_history) == 0

    def test_format_execution_history_empty(self):
        """Test formatting empty execution history."""
        manager = StatefulSandboxManager()
        result = manager._format_execution_history()
        assert result == "No previous executions in this session."

    def test_format_execution_history_with_records(self):
        """Test formatting execution history with records."""
        from datetime import datetime, timezone

        manager = StatefulSandboxManager()
        manager._execution_history.append(
            ExecutionRecord(
                timestamp=datetime.now(timezone.utc),
                code_task="Calculate fibonacci numbers for analysis",
                generated_code="fib = [0, 1]",
                result="[0, 1, 1, 2, 3, 5]",
                success=True,
            )
        )
        result = manager._format_execution_history()
        assert "Calculate fibonacci" in result
        assert "✓" in result
