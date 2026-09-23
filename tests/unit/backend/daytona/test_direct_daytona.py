"""Unit tests for the direct Daytona client, filesystem operations, and models."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from fleet_rlm.daytona.interpreter import (
    FINAL_OUTPUT_MARKER,
    ExecutionResult,
    extract_final_payload,
    final_output_frame,
)
from fleet_rlm.daytona.runtime import (
    create_folder,
    delete_file,
    get_file_info,
    list_files,
    read_file,
    write_file,
)


def test_final_output_frame_and_extract():
    """Verify serialization and deserialization of SUBMIT payloads."""
    payload = {"answer": "Recursive models are efficient", "confidence": 0.95}
    frame = final_output_frame(payload)

    assert FINAL_OUTPUT_MARKER in frame
    extracted = extract_final_payload(f"Prefix log...\n{frame}\nSuffix log...")
    assert extracted == payload


def test_extract_final_payload_none():
    """Verify that absent markers return None."""
    assert extract_final_payload("Regular stdout without submit") is None


def test_execution_result_dataclass():
    """Verify ExecutionResult defaults and types."""
    res = ExecutionResult(stdout="hello", exit_code=0, final_output={"key": "val"})
    assert res.stdout == "hello"
    assert res.exit_code == 0
    assert res.final_output == {"key": "val"}
    assert res.stderr == ""


@pytest.mark.asyncio
async def test_fs_read_write_list_delete():
    """Verify async filesystem helper functions."""
    mock_sandbox = MagicMock()
    mock_fs = MagicMock()
    mock_sandbox.fs = mock_fs

    mock_fs.download_file = AsyncMock(return_value=b"test file content")
    content = await read_file(mock_sandbox, "/workspace/test.txt")
    assert content == b"test file content"
    mock_fs.download_file.assert_awaited_once_with("/workspace/test.txt")

    mock_fs.upload_file = AsyncMock(return_value=None)
    await write_file(mock_sandbox, "/workspace/test.txt", b"new content")
    mock_fs.upload_file.assert_awaited_once_with(b"new content", "/workspace/test.txt")

    mock_fs.list_files = AsyncMock(return_value=["file1.txt", "file2.txt"])
    files = await list_files(mock_sandbox, "/workspace")
    assert files == ["file1.txt", "file2.txt"]

    mock_fs.delete_file = AsyncMock(return_value=None)
    await delete_file(mock_sandbox, "/workspace/test.txt")
    mock_fs.delete_file.assert_awaited_once_with("/workspace/test.txt")


@pytest.mark.asyncio
async def test_fs_create_folder_and_get_file_info():
    """Verify async create_folder and get_file_info operations."""
    mock_sandbox = MagicMock()
    mock_fs = MagicMock()
    mock_sandbox.fs = mock_fs

    mock_fs.create_folder = AsyncMock(return_value=None)
    await create_folder(mock_sandbox, "/workspace/my_dir", mode="755")
    mock_fs.create_folder.assert_awaited_once_with("/workspace/my_dir", mode="755")

    mock_info = MagicMock(name="test.txt", size=42)
    mock_fs.get_file_info = AsyncMock(return_value=mock_info)
    info = await get_file_info(mock_sandbox, "/workspace/test.txt")
    assert info == mock_info
    mock_fs.get_file_info.assert_awaited_once_with("/workspace/test.txt")


@pytest.mark.asyncio
async def test_fs_list_files_fallback_on_type_error():
    """Verify that list_files falls back to single-argument call when depth causes TypeError."""
    mock_sandbox = MagicMock()
    mock_fs = MagicMock()
    mock_sandbox.fs = mock_fs

    async def mock_list_files(_path: str, **kwargs: object) -> list[str]:
        if "depth" in kwargs:
            raise TypeError("unexpected keyword argument 'depth'")
        return ["file_fallback.txt"]

    mock_fs.list_files = mock_list_files
    result = await list_files(mock_sandbox, "/workspace", depth=2)
    assert result == ["file_fallback.txt"]


def test_build_daytona_client():
    """Verify build_daytona_client constructs an AsyncDaytona instance with settings."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from fleet_rlm.daytona.runtime import build_daytona_client

    mock_settings = SimpleNamespace(
        daytona_api_key=SimpleNamespace(get_secret_value=lambda: "test-key"),
        daytona_org_id="test-org",
    )

    with patch("daytona.AsyncDaytona") as mock_daytona_cls, patch("daytona.DaytonaConfig") as mock_config_cls:
        client = build_daytona_client(mock_settings)
        mock_config_cls.assert_called_once_with(
            api_url="https://app.daytona.io/api",
            api_key="test-key",
            organization_id="test-org",
        )
        assert client == mock_daytona_cls.return_value


def test_direct_interpreter_code_execution_stdout():
    """Verify DaytonaCodeInterpreter directly executes Python and captures stdout."""
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend

    mock_code_interpreter = MagicMock()
    mock_code_interpreter.create_context.return_value = "ctx-1"
    mock_code_interpreter.run_code.return_value = MagicMock(
        stdout="computed result: 42\n",
        stderr="",
        error=None,
    )
    mock_sandbox = MagicMock()
    mock_sandbox.code_interpreter = mock_code_interpreter

    backend = sandbox_backend(mock_sandbox)
    interpreter = DaytonaCodeInterpreter(backend=backend)
    interpreter.start()

    result = interpreter.execute("x = 40 + 2\nprint(f'computed result: {x}')")
    assert "computed result: 42" in str(result)
    mock_code_interpreter.run_code.assert_called_once()


def test_direct_interpreter_code_execution_submit():
    """Verify DaytonaCodeInterpreter extracts SUBMIT output as FinalOutput."""
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, final_output_frame, sandbox_backend
    from fleet_rlm.rlm.compat_3_3_1 import FinalOutput

    frame = final_output_frame({"answer": "42", "reasoning": "math"})
    mock_code_interpreter = MagicMock()
    mock_code_interpreter.create_context.return_value = "ctx-1"
    mock_code_interpreter.run_code.return_value = MagicMock(
        stdout=f"Computing...\n{frame}\n",
        stderr="",
        error=None,
    )
    mock_sandbox = MagicMock()
    mock_sandbox.code_interpreter = mock_code_interpreter

    backend = sandbox_backend(mock_sandbox)
    interpreter = DaytonaCodeInterpreter(backend=backend)
    interpreter.output_fields = [{"name": "answer", "type": "str"}]
    interpreter.start()

    result = interpreter.execute("SUBMIT(answer='42')")
    assert isinstance(result, FinalOutput)
    assert result.output == {"answer": "42", "reasoning": "math"}


def test_direct_interpreter_code_execution_error():
    """Verify DaytonaCodeInterpreter raises CodeExecutionError on syntax or runtime error."""
    import pytest
    from dspy.primitives.code_interpreter import CodeExecutionError

    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend

    mock_code_interpreter = MagicMock()
    mock_code_interpreter.create_context.return_value = "ctx-1"
    mock_code_interpreter.run_code.return_value = MagicMock(
        stdout="",
        stderr="ZeroDivisionError: division by zero",
        error="division by zero",
    )
    mock_sandbox = MagicMock()
    mock_sandbox.code_interpreter = mock_code_interpreter

    backend = sandbox_backend(mock_sandbox)
    interpreter = DaytonaCodeInterpreter(backend=backend)
    interpreter.start()

    with pytest.raises(CodeExecutionError) as exc_info:
        interpreter.execute("1 / 0")
    assert "division by zero" in str(exc_info.value)


def test_direct_interpreter_via_process_code_run():
    """Verify DaytonaCodeInterpreter executes via sandbox.process.code_run when code_interpreter is absent."""
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend

    mock_process = MagicMock()
    mock_process.code_run.return_value = MagicMock(
        result="from process.code_run",
        exit_code=0,
    )
    mock_sandbox = MagicMock(spec=["process"])
    mock_sandbox.process = mock_process

    backend = sandbox_backend(mock_sandbox)
    interpreter = DaytonaCodeInterpreter(backend=backend)
    interpreter.start()

    result = interpreter.execute("print('from process.code_run')")
    assert "from process.code_run" in str(result)
    mock_process.code_run.assert_called_once()


def test_direct_interpreter_via_process_exec():
    """Verify DaytonaCodeInterpreter executes via sandbox.process.exec when code_run is absent."""
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend

    mock_process = MagicMock(spec=["exec"])
    mock_process.exec.return_value = MagicMock(
        result="from process.exec",
        exit_code=0,
    )
    mock_sandbox = MagicMock(spec=["process"])
    mock_sandbox.process = mock_process

    backend = sandbox_backend(mock_sandbox)
    interpreter = DaytonaCodeInterpreter(backend=backend)
    interpreter.start()

    result = interpreter.execute("print('from process.exec')")
    assert "from process.exec" in str(result)
    mock_process.exec.assert_called_once()
    assert mock_process.exec.call_args.kwargs.get("cwd") == "/workspace"


def test_repair_category_from_multiline_traceback():
    """Verify _repair_category parses the exception name from multi-line tracebacks."""
    from fleet_rlm.daytona.interpreter import _repair_category

    tb = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 2, in <module>\n'
        "ZeroDivisionError: division by zero\n"
    )
    assert _repair_category(tb) == "ZeroDivisionError"

    name_tb = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 1, in <module>\n'
        "NameError: name 'undefined_var' is not defined\n"
    )
    assert _repair_category(name_tb) == "NameError"


def test_direct_interpreter_rejects_brokerless_with_tools():
    """Verify DaytonaCodeInterpreter raises when brokerless mode is configured but host tools are provided."""
    from fleet_rlm.daytona.errors import DaytonaAdapterError
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend

    mock_sandbox = MagicMock()
    backend = sandbox_backend(mock_sandbox)
    interpreter = DaytonaCodeInterpreter(
        backend=backend,
        tools={"some_tool": lambda x: x},
        broker_port=0,
    )
    interpreter.start()
    with pytest.raises(DaytonaAdapterError) as exc_info:
        interpreter.execute("some_tool(1)")
    assert "brokerless mode cannot dispatch host tools" in str(exc_info.value)
