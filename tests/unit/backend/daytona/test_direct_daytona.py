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
    """Verify brokerless mode refuses to dispatch host tools.

    `broker_port=0` selects brokerless mode, which has no host-tool transport. Running
    anyway would let generated code NameError on the first tool call, so the
    interpreter must fail fast with a typed cause instead.
    """
    from fleet_rlm.daytona.errors import DaytonaAdapterError
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend

    backend = sandbox_backend(MagicMock())
    interpreter = DaytonaCodeInterpreter(
        backend=backend,
        tools={"some_tool": lambda x: x},
        broker_port=0,
    )
    interpreter.start()
    with pytest.raises(DaytonaAdapterError) as exc_info:
        interpreter.execute("some_tool(1)")
    assert exc_info.value.cause_type == "BrokerlessToolDispatchError"
