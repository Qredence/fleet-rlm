"""Stateful sandbox manager using dspy.RLM for orchestrated code execution.

This module provides the StatefulSandboxManager class that wraps ModalInterpreter
and exposes it to dspy.RLM for orchestrating code generation and execution.
It provides:

    - Persistent Modal sandbox sessions with Volume-backed storage
    - Workspace operations (read/write/list) at /data/workspace/
    - Execution history tracking for iterative improvement
    - dspy.RLM integration for LLM-orchestrated code workflows
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import dspy
from dspy.primitives.code_interpreter import FinalOutput

from .interpreter import ModalInterpreter


@dataclass
class ExecutionRecord:
    """Record of a single code execution attempt."""

    timestamp: datetime
    code_task: str
    generated_code: str
    result: Any
    success: bool
    error_message: str | None = None
    execution_time_ms: int | None = None


@dataclass
class SandboxResult:
    """Result from a sandbox execution."""

    success: bool
    output: Any
    error: str | None = None
    execution_time_ms: int | None = None


class CodeGenerationSignature(dspy.Signature):
    """Generate Python code to accomplish a task."""

    code_task: str = dspy.InputField(
        desc="Description of what the code should accomplish"
    )
    workspace_files: list[str] = dspy.InputField(
        desc="List of available files in the workspace"
    )
    execution_history: str = dspy.InputField(
        desc="Recent execution history for context"
    )

    generated_code: str = dspy.OutputField(desc="Python code to execute in the sandbox")
    explanation: str = dspy.OutputField(desc="Brief explanation of what the code does")


class StatefulSandboxManager:
    """Manager for stateful sandbox sessions using dspy.RLM orchestration.

    This class wraps ModalInterpreter and provides:
        - dspy.RLM integration for LLM-orchestrated code generation
        - Persistent workspace at /data/workspace/ backed by Modal Volume
        - Execution history tracking for iterative improvement
        - Session management with automatic cleanup

    Example:
        >>> manager = StatefulSandboxManager(volume_name="my-workspace")
        >>> with manager:
        ...     # Execute code with RLM orchestration
        ...     result = manager.execute_with_rlm("Calculate fibonacci numbers")
        ...
        ...     # Save results to workspace
        ...     manager.save_to_workspace("fib.txt", str(result.output))
        ...
        ...     # Load from workspace
        ...     content = manager.load_from_workspace("fib.txt")
        ...
        ...     # Get execution history
        ...     history = manager.get_session_history()
    """

    def __init__(
        self,
        *,
        volume_name: str | None = "rlm-workspace",
        workspace_path: str = "/data/workspace",
        secret_name: str = "LITELLM",
        timeout: int = 600,
        max_llm_calls: int = 50,
        llm_call_timeout: int = 60,
        interpreter: ModalInterpreter | None = None,
    ) -> None:
        """Initialize the StatefulSandboxManager.

        Args:
            volume_name: Modal Volume name for persistent storage.
            workspace_path: Path within the volume for workspace files.
            secret_name: Modal Secret name for API keys.
            timeout: Sandbox lifetime timeout in seconds.
            max_llm_calls: Maximum LLM calls per session.
            llm_call_timeout: Timeout for individual LLM calls.
            interpreter: Optional existing ModalInterpreter to use.
        """
        self.volume_name = volume_name
        self.workspace_path = workspace_path
        self._execution_history: list[ExecutionRecord] = []
        self._session_start = datetime.now(timezone.utc)

        # Initialize or use provided interpreter
        if interpreter is not None:
            self.interpreter = interpreter
        else:
            self.interpreter = ModalInterpreter(
                timeout=timeout,
                secret_name=secret_name,
                volume_name=volume_name,
                max_llm_calls=max_llm_calls,
                llm_call_timeout=llm_call_timeout,
            )

        # Initialize RLM for code generation
        self.code_generator = dspy.ChainOfThought(CodeGenerationSignature)

        self._started = False

    def __enter__(self) -> "StatefulSandboxManager":
        """Start the manager and return it for context manager use."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Shutdown the manager on context manager exit."""
        self.shutdown()
        return False

    def start(self) -> None:
        """Start the underlying interpreter session."""
        if self._started:
            return
        self.interpreter.start()
        self._started = True

        # Initialize workspace directory in sandbox
        self._ensure_workspace_exists()

    def shutdown(self) -> None:
        """Shutdown the interpreter and clean up resources."""
        self.interpreter.shutdown()
        self._started = False

    def _ensure_workspace_exists(self) -> None:
        """Ensure the workspace directory exists in the sandbox."""
        code = f"""
import os
os.makedirs("{self.workspace_path}", exist_ok=True)
print(f"Workspace ready at {self.workspace_path}")
"""
        self.interpreter.execute(code)

    def execute_with_rlm(
        self, code_task: str, context: dict[str, Any] | None = None
    ) -> SandboxResult:
        """Execute code using dspy.RLM for orchestration.

        This method:
        1. Uses dspy.RLM to generate code for the task
        2. Executes the generated code in the sandbox
        3. Tracks execution history
        4. Returns the result

        Args:
            code_task: Description of what the code should accomplish.
            context: Optional additional context for code generation.

        Returns:
            SandboxResult with success status, output, and metadata.
        """
        self.start()
        start_time = datetime.now(timezone.utc)

        # Get workspace files for context
        workspace_files = self.list_workspace_files()

        # Get recent execution history for context
        history_str = self._format_execution_history(limit=5)

        # Generate code using RLM
        try:
            prediction = self.code_generator(
                code_task=code_task,
                workspace_files=workspace_files,
                execution_history=history_str,
            )
            generated_code = prediction.generated_code
        except Exception as e:
            record = ExecutionRecord(
                timestamp=start_time,
                code_task=code_task,
                generated_code="",
                result=None,
                success=False,
                error_message=f"Code generation failed: {e}",
            )
            self._execution_history.append(record)
            return SandboxResult(
                success=False,
                output=None,
                error=f"Code generation failed: {e}",
            )

        # Execute the generated code
        try:
            execution_start = datetime.now(timezone.utc)
            result = self.interpreter.execute(generated_code)
            execution_end = datetime.now(timezone.utc)
            execution_time_ms = int(
                (execution_end - execution_start).total_seconds() * 1000
            )

            # Extract output from FinalOutput or string result
            if isinstance(result, FinalOutput):
                output = result.output
            else:
                output = result

            # Record successful execution
            record = ExecutionRecord(
                timestamp=start_time,
                code_task=code_task,
                generated_code=generated_code,
                result=output,
                success=True,
                execution_time_ms=execution_time_ms,
            )
            self._execution_history.append(record)

            return SandboxResult(
                success=True,
                output=output,
                execution_time_ms=execution_time_ms,
            )

        except Exception as e:
            execution_end = datetime.now(timezone.utc)
            execution_time_ms = int((execution_end - start_time).total_seconds() * 1000)

            # Record failed execution
            record = ExecutionRecord(
                timestamp=start_time,
                code_task=code_task,
                generated_code=generated_code,
                result=None,
                success=False,
                error_message=str(e),
                execution_time_ms=execution_time_ms,
            )
            self._execution_history.append(record)

            return SandboxResult(
                success=False,
                output=None,
                error=str(e),
                execution_time_ms=execution_time_ms,
            )

    def save_to_workspace(self, filename: str, content: str) -> dict[str, Any]:
        """Save content to a file in the workspace.

        Args:
            filename: Name of the file to save.
            content: Content to write to the file.

        Returns:
            Dict with status and file path information.
        """
        self.start()
        file_path = f"{self.workspace_path}/{filename}"

        code = f"""
import os
try:
    # Ensure workspace directory exists
    os.makedirs("{self.workspace_path}", exist_ok=True)

    # Write content to file
    with open("{file_path}", "w") as f:
        f.write(content)

    SUBMIT(status="ok", path="{file_path}", chars=len(content))
except Exception as e:
    SUBMIT(status="error", error=str(e))
"""
        try:
            result = self.interpreter.execute(code, variables={"content": content})
            if isinstance(result, FinalOutput):
                output = result.output
                if isinstance(output, dict):
                    if output.get("status") == "ok":
                        return {
                            "status": "ok",
                            "filename": filename,
                            "path": file_path,
                            "chars": int(output.get("chars", len(content))),
                        }
                    return {
                        "status": "error",
                        "filename": filename,
                        "error": str(output.get("error", "Unknown error")),
                    }
            if isinstance(result, str) and "[Error]" in result:
                return {
                    "status": "error",
                    "filename": filename,
                    "error": result.strip(),
                }
            return {
                "status": "error",
                "filename": filename,
                "error": "Unexpected result format",
            }
        except Exception as e:
            return {
                "status": "error",
                "filename": filename,
                "error": str(e),
            }

    def load_from_workspace(self, filename: str) -> dict[str, Any]:
        """Load content from a file in the workspace.

        Args:
            filename: Name of the file to load.

        Returns:
            Dict with status, content, and metadata.
        """
        self.start()
        file_path = f"{self.workspace_path}/{filename}"

        code = f"""
try:
    with open("{file_path}", "r") as f:
        content = f.read()
    SUBMIT(status="ok", content=content, chars=len(content))
except FileNotFoundError:
    SUBMIT(status="error", error=f"File not found: {file_path}")
except Exception as e:
    SUBMIT(status="error", error=str(e))
"""
        try:
            result = self.interpreter.execute(code)
            if isinstance(result, FinalOutput):
                output = result.output
                if isinstance(output, dict):
                    if output.get("status") == "ok":
                        return {
                            "status": "ok",
                            "filename": filename,
                            "content": output.get("content", ""),
                            "chars": output.get("chars", 0),
                        }
                    else:
                        return {
                            "status": "error",
                            "filename": filename,
                            "error": output.get("error", "Unknown error"),
                        }
            return {
                "status": "error",
                "filename": filename,
                "error": "Unexpected result format",
            }
        except Exception as e:
            return {
                "status": "error",
                "filename": filename,
                "error": str(e),
            }

    def list_workspace_files(self) -> list[str]:
        """List all files in the workspace.

        Returns:
            List of filenames in the workspace.
        """
        self.start()

        code = f"""
import os

files = []
try:
    if os.path.isdir("{self.workspace_path}"):
        files = os.listdir("{self.workspace_path}")
    else:
        files = []
except Exception:
    files = []

SUBMIT(files=files, count=len(files))
"""
        try:
            result = self.interpreter.execute(code)
            if isinstance(result, FinalOutput):
                output = result.output
                if isinstance(output, dict):
                    return output.get("files", [])
            return []
        except Exception:
            return []

    def delete_workspace_file(self, filename: str) -> dict[str, Any]:
        """Delete a file from the workspace.

        Args:
            filename: Name of the file to delete.

        Returns:
            Dict with status and file path information.
        """
        self.start()
        file_path = f"{self.workspace_path}/{filename}"

        code = f"""
import os

try:
    if os.path.exists("{file_path}"):
        os.remove("{file_path}")
        SUBMIT(status="ok", message=f"Deleted {file_path}")
    else:
        SUBMIT(status="error", error=f"File not found: {file_path}")
except Exception as e:
    SUBMIT(status="error", error=str(e))
"""
        try:
            result = self.interpreter.execute(code)
            if isinstance(result, FinalOutput):
                output = result.output
                if isinstance(output, dict):
                    return {
                        "status": output.get("status", "error"),
                        "filename": filename,
                        "message": output.get("message", ""),
                        "error": output.get("error", ""),
                    }
            return {
                "status": "error",
                "filename": filename,
                "error": "Unexpected result format",
            }
        except Exception as e:
            return {
                "status": "error",
                "filename": filename,
                "error": str(e),
            }

    def get_session_history(self) -> list[dict[str, Any]]:
        """Get the execution history for this session.

        Returns:
            List of execution records as dictionaries.
        """
        return [
            {
                "timestamp": record.timestamp.isoformat(),
                "code_task": record.code_task,
                "generated_code": record.generated_code,
                "result": record.result,
                "success": record.success,
                "error_message": record.error_message,
                "execution_time_ms": record.execution_time_ms,
            }
            for record in self._execution_history
        ]

    def get_session_stats(self) -> dict[str, Any]:
        """Get statistics for the current session.

        Returns:
            Dict with session statistics.
        """
        total_executions = len(self._execution_history)
        successful_executions = sum(1 for r in self._execution_history if r.success)
        failed_executions = total_executions - successful_executions

        total_time_ms = sum((r.execution_time_ms or 0) for r in self._execution_history)

        session_duration = datetime.now(timezone.utc) - self._session_start

        return {
            "session_start": self._session_start.isoformat(),
            "session_duration_seconds": int(session_duration.total_seconds()),
            "total_executions": total_executions,
            "successful_executions": successful_executions,
            "failed_executions": failed_executions,
            "success_rate": successful_executions / total_executions
            if total_executions > 0
            else 0,
            "total_execution_time_ms": total_time_ms,
            "average_execution_time_ms": total_time_ms / total_executions
            if total_executions > 0
            else 0,
        }

    def _format_execution_history(self, limit: int = 5) -> str:
        """Format recent execution history as a string for context.

        Args:
            limit: Maximum number of records to include.

        Returns:
            Formatted history string.
        """
        if not self._execution_history:
            return "No previous executions in this session."

        recent = self._execution_history[-limit:]
        lines = []
        for i, record in enumerate(recent, 1):
            status = "✓" if record.success else "✗"
            lines.append(f"{i}. {status} {record.code_task[:60]}...")
            if record.error_message:
                lines.append(f"   Error: {record.error_message[:100]}")

        return "\n".join(lines)

    def clear_history(self) -> None:
        """Clear the execution history."""
        self._execution_history.clear()
