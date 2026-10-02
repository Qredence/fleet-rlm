"""Adversarial contracts for the Daytona broker and interpreter.

Targeting:
1. DaytonaCodeInterpreter.execute() with syntax errors, runtime exceptions,
   timeouts, malformed SUBMIT payloads, boundary code sizes, and large outputs.
2. Native filesystem operations (fs.py) with non-existent paths, nested folders,
   empty files, binary content with null bytes, and sync/async backends.
3. Root vs child sandbox isolation invariants (network_block_all=True, volume mount boundaries).
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from dspy import FinalOutput
from dspy.primitives.code_interpreter import CodeExecutionError

from fleet_rlm.daytona.broker import DaytonaHttpToolBroker
from fleet_rlm.daytona.errors import DaytonaAdapterError, ProviderRequestError
from fleet_rlm.daytona.interpreter import (
    FINAL_OUTPUT_MARKER,
    DaytonaCodeInterpreter,
    InProcessInterpreterBackend,
    build_submit_setup_code,
    extract_final_payload,
    final_output_frame,
    sandbox_backend,
)
from fleet_rlm.daytona.runtime import (
    DaytonaEnvironmentProfile,
    DaytonaSandboxSpec,
    LiveDaytonaPlatform,
    create_folder,
    delete_file,
    get_file_info,
    list_files,
    read_file,
    write_file,
)
from fleet_rlm.rlm.result import RunNoProgressError
from tests.support.session_manager import make_daytona_runtime

# ============================================================================
# Section 1: DaytonaCodeInterpreter.execute() Adversarial Challenge Tests
# ============================================================================


class TestInterpreterSyntaxAndExceptions:
    """Interpreter errors remain recoverable, with repeated failures bounded."""

    def test_syntax_and_runtime_categories(self) -> None:
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        for code, category in [
            ("def broken_function(", "SyntaxError"),
            ("1 / 0", "ZeroDivisionError"),
            ("print(undefined_var)", "NameError"),
        ]:
            with pytest.raises(CodeExecutionError) as error:
                interpreter.execute(code)
            assert error.value.category == category

    def test_repeated_failure_becomes_terminal(self) -> None:
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        with pytest.raises(CodeExecutionError):
            interpreter.execute("1 / 0")
        with pytest.raises(CodeExecutionError) as repeated:
            interpreter.execute("1 / 0")
        assert repeated.value.category == "no_progress"
        with pytest.raises(RunNoProgressError):
            interpreter.execute("1 / 0")


class TestInterpreterTimeoutsAndLimits:
    """Broker timeout and interpreter bounds."""

    def test_broker_timeout_is_mapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def timeout(*_args: Any, **_kwargs: Any) -> Any:
            raise TimeoutError("Daytona execution timed out")

        monkeypatch.setattr(DaytonaHttpToolBroker, "execute", timeout)
        interpreter = DaytonaCodeInterpreter(backend=sandbox_backend(MagicMock(), timeout_s=10))
        with pytest.raises(ProviderRequestError) as error:
            interpreter.execute("print('slow')")
        assert error.value.cause_type == "TimeoutError"

    @pytest.mark.parametrize("invalid_timeout", [0])
    def test_sandbox_backend_rejects_non_positive_timeout(self, invalid_timeout: int) -> None:
        with pytest.raises(DaytonaAdapterError) as error:
            sandbox_backend(MagicMock(), timeout_s=invalid_timeout)
        assert error.value.cause_type == "InterpreterConfigurationError"

    def test_timeout_is_forwarded_to_broker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        observed: list[int] = []

        def execute(_broker: Any, _code: str, _variables: Any, *, timeout_s: int) -> dict[str, Any]:
            observed.append(timeout_s)
            return {"stdout": "done\n"}

        monkeypatch.setattr(DaytonaHttpToolBroker, "execute", execute)
        interpreter = DaytonaCodeInterpreter(backend=sandbox_backend(MagicMock(), timeout_s=42))
        assert interpreter.execute("print('done')") == "done\n"
        assert observed == [42, 42]  # one-time setup, then the action

    def test_empty_and_oversized_code_are_rejected(self) -> None:
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend(), max_code_chars=100)
        for code, category in [("  \n", "empty_code"), ("#" + "x" * 200, "code_too_large")]:
            with pytest.raises(CodeExecutionError) as error:
                interpreter.execute(code)
            assert error.value.category == category

    def test_large_output_is_truncated(self) -> None:
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend(), execution_output_cap=2000)
        result = interpreter.execute("_out = 'LINE_' + '0123456789' * 5000")
        assert len(result) < 3000
        assert "characters omitted" in result
        assert result.startswith("LINE_0123456789")


class TestSubmitPayloadValidation:
    """Test malformed SUBMIT payloads, non-JSON types, and marker parsing."""

    def test_submit_preamble_rejects_nan_and_inf(self) -> None:
        """SUBMIT code preamble validates against non-finite float values."""
        code = build_submit_setup_code()
        ns: dict[str, Any] = {}
        exec(code, ns)
        submit_fn = ns["SUBMIT"]

        with pytest.raises(TypeError, match="non-finite number"):
            submit_fn(val=float("nan"))

        with pytest.raises(TypeError, match="non-finite number"):
            submit_fn(val=float("inf"))

        with pytest.raises(TypeError, match="non-finite number"):
            submit_fn(val=float("-inf"))

    def test_submit_preamble_rejects_non_string_keys(self) -> None:
        """SUBMIT code preamble rejects mappings with non-string keys."""
        code = build_submit_setup_code()
        ns: dict[str, Any] = {}
        exec(code, ns)
        submit_fn = ns["SUBMIT"]

        with pytest.raises(TypeError, match="non-string mapping key"):
            submit_fn(data={42: "integer_key"})

    def test_submit_preamble_rejects_unserializable_objects(self) -> None:
        """SUBMIT code preamble rejects unsupported arbitrary objects."""
        code = build_submit_setup_code()
        ns: dict[str, Any] = {}
        exec(code, ns)
        submit_fn = ns["SUBMIT"]

        with pytest.raises(TypeError, match="unsupported type"):
            submit_fn(func=lambda x: x)

        with pytest.raises(TypeError, match="unsupported type"):
            submit_fn(instance=object())

    def test_malformed_stdout_markers_do_not_produce_final_output(self) -> None:
        """Corrupted, truncated, or non-dict markers in stdout are not parsed into FinalOutput."""
        b64_list = base64.b64encode(b"[1, 2, 3]").decode()
        b64_str = base64.b64encode(b'"hello"').decode()
        b64_dict = base64.b64encode(b'{"key": "val"}').decode()
        b64_incomplete = base64.b64encode(b'{"broken": ').decode()

        test_cases = [
            # 1. Invalid base64
            f"{FINAL_OUTPUT_MARKER}!!!not-base64!@{FINAL_OUTPUT_MARKER}",
            # 2. Valid base64 encoding a list instead of dict
            f"{FINAL_OUTPUT_MARKER}{b64_list}{FINAL_OUTPUT_MARKER}",
            # 3. Valid base64 encoding a raw string
            f"{FINAL_OUTPUT_MARKER}{b64_str}{FINAL_OUTPUT_MARKER}",
            # 4. Unclosed marker
            f"{FINAL_OUTPUT_MARKER}{b64_dict}",
            # 5. Empty marker
            f"{FINAL_OUTPUT_MARKER}{FINAL_OUTPUT_MARKER}",
            # 6. Incomplete JSON in valid base64
            f"{FINAL_OUTPUT_MARKER}{b64_incomplete}{FINAL_OUTPUT_MARKER}",
        ]

        for stdout_content in test_cases:
            assert extract_final_payload(stdout_content) is None

    def test_valid_complex_submit_roundtrip(self) -> None:
        """Valid nested structures with unicode, emojis, booleans, and nulls parse correctly."""
        complex_payload = {
            "answer": "Calculated value: 42 \U0001f680 [\u30c6\u30b9\u30c8]",
            "metadata": {
                "tags": ["math", "dspy", "daytona"],
                "active": True,
                "score": 99.5,
                "notes": None,
            },
        }
        frame = final_output_frame(complex_payload)
        extracted = extract_final_payload(f"Leading stdout\n{frame}\nTrailing log")
        assert extracted == complex_payload

        backend = InProcessInterpreterBackend()
        interpreter = DaytonaCodeInterpreter(backend=backend)
        interpreter.start()

        result = interpreter.execute(f"SUBMIT(**{complex_payload!r})")
        assert isinstance(result, FinalOutput)
        assert result.output == complex_payload


# ============================================================================
# Section 2: Native Filesystem Operations (fs.py) Adversarial Challenge Tests
# ============================================================================


class TestFilesystemOperations:
    """Test fs.py edge cases: binary null bytes, empty files, missing files, sync/async."""

    @pytest.mark.asyncio
    async def test_read_file_binary_null_bytes(self) -> None:
        """read_file preserves arbitrary binary content with null bytes and high bytes."""
        mock_fs = MagicMock()
        binary_payload = b"\x00\xff\xfe\x80\x00\x01\x02\x03\x00\xaa\xbb\xcc"
        mock_fs.download_file = AsyncMock(return_value=binary_payload)
        mock_sandbox = MagicMock()
        mock_sandbox.fs = mock_fs

        data = await read_file(mock_sandbox, "/workspace/binary.dat")
        assert data == binary_payload
        assert b"\x00" in data

    @pytest.mark.asyncio
    async def test_read_file_empty_content(self) -> None:
        """read_file handles empty file returning b''."""
        mock_fs = MagicMock()
        mock_fs.download_file = AsyncMock(return_value=b"")
        mock_sandbox = MagicMock()
        mock_sandbox.fs = mock_fs

        data = await read_file(mock_sandbox, "/workspace/empty.txt")
        assert data == b""

    @pytest.mark.asyncio
    async def test_read_file_string_conversion(self) -> None:
        """read_file handles string return value by UTF-8 encoding it to bytes."""
        mock_fs = MagicMock()
        mock_fs.download_file = AsyncMock(return_value="text from sdk: \xe4\xf6\xfc")
        mock_sandbox = MagicMock()
        mock_sandbox.fs = mock_fs

        data = await read_file(mock_sandbox, "/workspace/text.txt")
        assert data == "text from sdk: \xe4\xf6\xfc".encode()

    @pytest.mark.asyncio
    async def test_read_file_non_existent_path_propagates_error(self) -> None:
        """read_file propagates FileNotFoundError or provider errors for missing files."""
        mock_fs = MagicMock()
        mock_fs.download_file = AsyncMock(side_effect=FileNotFoundError("file not found"))
        mock_sandbox = MagicMock()
        mock_sandbox.fs = mock_fs

        with pytest.raises(FileNotFoundError):
            await read_file(mock_sandbox, "/workspace/non_existent.txt")

    @pytest.mark.asyncio
    async def test_read_file_direct_fs_object(self) -> None:
        """read_file works when the sandbox argument is itself the filesystem client."""
        mock_fs = MagicMock(spec=["download_file"])
        mock_fs.download_file = AsyncMock(return_value=b"direct_fs_data")

        data = await read_file(mock_fs, "/workspace/file.txt")
        assert data == b"direct_fs_data"

    @pytest.mark.asyncio
    async def test_write_file_empty_and_binary(self) -> None:
        """write_file writes empty bytes and binary bytes without alteration."""
        mock_fs = MagicMock()
        mock_fs.upload_file = AsyncMock(return_value=None)
        mock_sandbox = MagicMock()
        mock_sandbox.fs = mock_fs

        # 1. Empty write
        await write_file(mock_sandbox, "/workspace/empty.bin", b"")
        mock_fs.upload_file.assert_awaited_with(b"", "/workspace/empty.bin")

        # 2. Binary write
        binary_data = b"\x00\x01\x02\x03\xff\xfe"
        await write_file(mock_sandbox, "/workspace/data.bin", binary_data)
        mock_fs.upload_file.assert_awaited_with(binary_data, "/workspace/data.bin")

    @pytest.mark.asyncio
    async def test_list_files_empty_and_sync_fallback(self) -> None:
        """list_files handles empty directories and sync fallback when depth raises TypeError synchronously."""
        mock_fs = MagicMock()
        # First call: depth supported, returns empty list
        mock_fs.list_files = AsyncMock(return_value=[])
        mock_sandbox = MagicMock()
        mock_sandbox.fs = mock_fs

        entries = await list_files(mock_sandbox, "/workspace/empty_dir")
        assert entries == []

        # Second call: synchronous function raising TypeError on depth
        def list_without_depth(_path: str, **kwargs: Any) -> list[str]:
            if "depth" in kwargs:
                raise TypeError("unexpected keyword argument 'depth'")
            return ["file_a.txt", "file_b.txt"]

        mock_fs.list_files = MagicMock(side_effect=list_without_depth)
        entries2 = await list_files(mock_sandbox, "/workspace")
        assert entries2 == ["file_a.txt", "file_b.txt"]

    @pytest.mark.asyncio
    async def test_list_files_async_coroutine_type_error_finding(self) -> None:
        """Verify fs.py properly falls back when async list_files raises TypeError for depth."""

        async def async_list_without_depth(_path: str, **kwargs: Any) -> list[str]:
            if "depth" in kwargs:
                raise TypeError("async: unexpected keyword argument 'depth'")
            return ["file_x.txt"]

        mock_fs = MagicMock()
        mock_fs.list_files = MagicMock(side_effect=async_list_without_depth)
        mock_sandbox = MagicMock()
        mock_sandbox.fs = mock_fs

        result = await list_files(mock_sandbox, "/workspace")
        assert result == ["file_x.txt"]

    @pytest.mark.asyncio
    async def test_create_folder_nested_and_mode(self) -> None:
        """create_folder passes path and custom permissions mode."""
        mock_fs = MagicMock()
        mock_fs.create_folder = AsyncMock(return_value=None)
        mock_sandbox = MagicMock()
        mock_sandbox.fs = mock_fs

        await create_folder(mock_sandbox, "/workspace/a/b/c/nested", mode="755")
        mock_fs.create_folder.assert_awaited_once_with("/workspace/a/b/c/nested", mode="755")

    @pytest.mark.asyncio
    async def test_delete_file_and_get_file_info(self) -> None:
        """delete_file and get_file_info call underlying methods accurately."""
        mock_fs = MagicMock()
        mock_fs.delete_file = AsyncMock(return_value=None)
        mock_fs.get_file_info = AsyncMock(return_value={"size": 128, "name": "f.txt"})
        mock_sandbox = MagicMock()
        mock_sandbox.fs = mock_fs

        await delete_file(mock_sandbox, "/workspace/f.txt")
        mock_fs.delete_file.assert_awaited_once_with("/workspace/f.txt")

        info = await get_file_info(mock_sandbox, "/workspace/f.txt")
        assert info == {"size": 128, "name": "f.txt"}


# ============================================================================
# Section 3: Root vs Child Sandbox Isolation Invariants
# ============================================================================


class TestSandboxIsolationInvariants:
    """Challenge isolation invariants: Root volume mount vs Child network/volume boundaries."""

    @pytest.mark.asyncio
    async def test_root_sandbox_requires_volume_when_with_volume_true(self) -> None:
        """Root sandbox (SESSION) with with_volume=True requires volume_id and mount_path."""
        mock_client = MagicMock()
        mock_client.create = AsyncMock()
        spec = DaytonaSandboxSpec(snapshot="fleet-snapshot-v1")
        platform = LiveDaytonaPlatform(mock_client, spec)

        # Missing volume_id and mount_path
        with pytest.raises(ValueError, match="volume_id and mount_path are required"):
            await platform.create(
                profile=DaytonaEnvironmentProfile.SESSION,
                with_volume=True,
                volume_id=None,
                mount_path=None,
            )

        # Missing mount_path only
        with pytest.raises(ValueError, match="volume_id and mount_path are required"):
            await platform.create(
                profile=DaytonaEnvironmentProfile.SESSION,
                with_volume=True,
                volume_id="vol-123",
                mount_path=None,
            )

    @pytest.mark.asyncio
    async def test_semantic_child_forbids_volume_mounting(self) -> None:
        """SEMANTIC_CHILD profile strictly rejects volume mounts with ValueError."""
        mock_client = MagicMock()
        child_spec = DaytonaSandboxSpec(
            snapshot="fleet-child-snapshot-v1",
            profile=DaytonaEnvironmentProfile.SEMANTIC_CHILD,
            cpu=2,
            memory_gib=4,
            disk_gib=4,
        )
        spec = DaytonaSandboxSpec(snapshot="fleet-snapshot-v1")
        platform = LiveDaytonaPlatform(
            mock_client,
            spec,
            {DaytonaEnvironmentProfile.SEMANTIC_CHILD: child_spec},
        )

        with pytest.raises(ValueError, match="SemanticChild sandboxes cannot mount a Workspace Volume"):
            await platform.create(
                profile=DaytonaEnvironmentProfile.SEMANTIC_CHILD,
                volume_id="forbidden-vol",
                mount_path="/workspace",
            )

    @pytest.mark.asyncio
    async def test_semantic_child_requests_network_block_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify the adapter requests network blocking; provider enforcement is unverified."""
        import fleet_rlm.daytona.runtime as daytona_runtime

        loop = asyncio.get_running_loop()
        mock_platform = MagicMock()
        mock_platform.create = AsyncMock()
        mock_admission = MagicMock()
        mock_admission.acquire = AsyncMock()
        mock_interpreter = MagicMock()
        monkeypatch.setattr(daytona_runtime, "DaytonaCodeInterpreter", lambda **_kwargs: mock_interpreter)
        monkeypatch.setattr(daytona_runtime, "sandbox_backend", lambda *_args, **_kwargs: MagicMock())
        monkeypatch.setattr(daytona_runtime, "_close_child_runtime_sync", MagicMock())
        monkeypatch.setattr(daytona_runtime, "cleanup_after_failed_acquire", AsyncMock())
        monkeypatch.setattr(daytona_runtime, "sandbox_id_for", lambda _sandbox: "sb-child-isolation")

        runtime = make_daytona_runtime(platform=mock_platform, admission=mock_admission)
        await runtime._acquire_child_runtime(
            volume_id=None,
            mount_path=None,
            profile=DaytonaEnvironmentProfile.SEMANTIC_CHILD,
            workspace_id=uuid4(),
            run_id=uuid4(),
            call_index=1,
            deadline=loop.time() + 10.0,
            execution_timeout_s=30,
            execution_output_cap=1000,
            retain_pending_cleanup=lambda _future: None,
        )

        mock_platform.create.assert_awaited_once()
        call_kwargs = mock_platform.create.call_args.kwargs

        assert call_kwargs.get("network_block_all") is True
