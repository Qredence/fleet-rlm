"""Adversarial contracts for direct Daytona SDK integration.

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
from dspy.primitives.code_interpreter import CodeExecutionError

from fleet_rlm.daytona.errors import DaytonaAdapterError, ProviderRequestError
from fleet_rlm.daytona.interpreter import (
    FINAL_OUTPUT_MARKER,
    DaytonaCodeInterpreter,
    build_submit_setup_code,
    extract_final_payload,
    final_output_frame,
    sandbox_backend,
)
from fleet_rlm.daytona.platform import LiveDaytonaPlatform
from fleet_rlm.daytona.provisioning import (
    DaytonaEnvironmentProfile,
    DaytonaSandboxSpec,
)
from fleet_rlm.daytona.recursive_child_runtime import acquire_child_runtime
from fleet_rlm.daytona.runtime import (
    create_folder,
    delete_file,
    get_file_info,
    list_files,
    read_file,
    write_file,
)
from fleet_rlm.rlm.compat_3_3_1 import FinalOutput
from fleet_rlm.rlm.result import RunNoProgressError

# ============================================================================
# Section 1: DaytonaCodeInterpreter.execute() Adversarial Challenge Tests
# ============================================================================


class TestInterpreterSyntaxAndExceptions:
    """Test syntax errors, runtime exceptions, and repetition transitions."""

    def test_execute_syntax_error_classification(self) -> None:
        """Syntax error in user code raises CodeExecutionError categorized as SyntaxError."""
        mock_ci = MagicMock()
        mock_ci.create_context.return_value = "ctx-syntax"
        mock_ci.run_code.return_value = MagicMock(
            stdout="",
            stderr="SyntaxError: unexpected EOF while parsing (<string>, line 1)",
            error="SyntaxError: unexpected EOF while parsing",
        )
        mock_sb = MagicMock()
        mock_sb.code_interpreter = mock_ci

        backend = sandbox_backend(mock_sb)
        interpreter = DaytonaCodeInterpreter(backend=backend)
        interpreter.start()

        with pytest.raises(CodeExecutionError) as exc_info:
            interpreter.execute("def broken_function(")

        assert getattr(exc_info.value, "category", None) == "SyntaxError"
        assert "SyntaxError" in str(exc_info.value)

    def test_execute_repeated_failing_code_transitions_to_terminal_no_progress(self) -> None:
        """Repeating the exact same failing action produces no_progress warning, then RunNoProgressError."""
        mock_ci = MagicMock()
        mock_ci.create_context.return_value = "ctx-repeat"
        mock_ci.run_code.return_value = MagicMock(
            stdout="",
            stderr="ZeroDivisionError: division by zero",
            error="ZeroDivisionError: division by zero",
        )
        mock_sb = MagicMock()
        mock_sb.code_interpreter = mock_ci

        backend = sandbox_backend(mock_sb)
        interpreter = DaytonaCodeInterpreter(backend=backend)
        interpreter.start()

        code = "x = 1 / 0"

        # 1st attempt: regular execution error
        with pytest.raises(CodeExecutionError) as exc1:
            interpreter.execute(code)
        assert getattr(exc1.value, "category", None) == "ZeroDivisionError"

        # 2nd attempt (identical): recoverable no_progress warning
        with pytest.raises(CodeExecutionError) as exc2:
            interpreter.execute(code)
        assert getattr(exc2.value, "category", None) == "no_progress"
        assert "no progress" in str(exc2.value)

        # 3rd attempt (identical): terminal RunNoProgressError
        with pytest.raises(RunNoProgressError):
            interpreter.execute(code)

    @pytest.mark.parametrize(
        ("error_msg", "expected_category"),
        [
            ("ZeroDivisionError: division by zero", "ZeroDivisionError"),
            ("KeyError: 'missing_key'", "KeyError"),
            ("IndexError: list index out of range", "IndexError"),
            ("TypeError: unsupported operand type(s)", "TypeError"),
            ("ValueError: invalid literal for int()", "ValueError"),
        ],
    )
    def test_execute_runtime_exceptions_categorization(self, error_msg: str, expected_category: str) -> None:
        """Standard Python runtime exceptions are preserved and categorized correctly."""
        mock_ci = MagicMock()
        mock_ci.create_context.return_value = "ctx-err"
        mock_ci.run_code.return_value = MagicMock(
            stdout="",
            stderr=f"Traceback (most recent call last):\n  ...\n{error_msg}",
            error=error_msg,
        )
        mock_sb = MagicMock()
        mock_sb.code_interpreter = mock_ci

        backend = sandbox_backend(mock_sb)
        interpreter = DaytonaCodeInterpreter(backend=backend)
        interpreter.start()

        with pytest.raises(CodeExecutionError) as exc_info:
            interpreter.execute(f"# test {expected_category}")

        assert getattr(exc_info.value, "category", None) == expected_category

    def test_execute_fallback_process_code_run_error(self) -> None:
        """When code_interpreter is missing, process.code_run failures are categorized correctly."""
        mock_process = MagicMock()
        mock_process.code_run.return_value = MagicMock(
            result="NameError: name 'undefined_var' is not defined",
            exit_code=1,
        )
        mock_sb = MagicMock(spec=["process"])
        mock_sb.process = mock_process

        backend = sandbox_backend(mock_sb)
        interpreter = DaytonaCodeInterpreter(backend=backend)
        interpreter.start()

        with pytest.raises(CodeExecutionError) as exc_info:
            interpreter.execute("print(undefined_var)")

        assert getattr(exc_info.value, "category", None) == "NameError"
        assert "undefined_var" in str(exc_info.value)

    def test_execute_fallback_process_exec_error(self) -> None:
        """When process.code_run is missing, process.exec failures are categorized correctly."""
        mock_process = MagicMock(spec=["exec"])
        mock_process.exec.return_value = MagicMock(
            result="ValueError: bad value",
            exit_code=1,
        )
        mock_sb = MagicMock(spec=["process"])
        mock_sb.process = mock_process

        backend = sandbox_backend(mock_sb)
        interpreter = DaytonaCodeInterpreter(backend=backend)
        interpreter.start()

        with pytest.raises(CodeExecutionError) as exc_info:
            interpreter.execute("raise ValueError('bad value')")

        assert getattr(exc_info.value, "category", None) == "ValueError"

    def test_execute_process_traceback_category_classification_finding(self) -> None:
        """Verify multi-line traceback correctly extracts the exception type category."""
        mock_process = MagicMock()
        mock_process.code_run.return_value = MagicMock(
            result="Traceback (most recent call last):\n  File 'main.py', line 1\nNameError: name 'x' is not defined",
            exit_code=1,
        )
        mock_sb = MagicMock(spec=["process"])
        mock_sb.process = mock_process

        backend = sandbox_backend(mock_sb)
        interpreter = DaytonaCodeInterpreter(backend=backend)
        interpreter.start()

        with pytest.raises(CodeExecutionError) as exc_info:
            interpreter.execute("print(x)")

        assert getattr(exc_info.value, "category", None) == "NameError"


class TestInterpreterTimeoutsAndLimits:
    """Test timeout enforcement and boundary constraints."""

    def test_execute_timeout_mapped_to_daytona_adapter_error(self) -> None:
        """TimeoutError from Daytona SDK is converted into DaytonaAdapterError."""
        mock_ci = MagicMock()
        mock_ci.create_context.return_value = "ctx-timeout"
        mock_ci.run_code.side_effect = TimeoutError("Daytona execution timed out after 10.0s")
        mock_sb = MagicMock()
        mock_sb.code_interpreter = mock_ci

        backend = sandbox_backend(mock_sb, timeout_s=10)
        interpreter = DaytonaCodeInterpreter(backend=backend)
        interpreter.start()

        with pytest.raises(DaytonaAdapterError) as exc_info:
            interpreter.execute("import time; time.sleep(100)")

        assert isinstance(exc_info.value, ProviderRequestError)
        assert exc_info.value.cause_type == "TimeoutError"
        assert "timed out" in str(exc_info.value)

    @pytest.mark.parametrize("invalid_timeout", [0, -1, -60])
    def test_sandbox_backend_rejects_non_positive_timeout(self, invalid_timeout: int) -> None:
        """Non-positive timeout_s raises DaytonaAdapterError with InterpreterConfigurationError."""
        mock_sb = MagicMock()
        with pytest.raises(DaytonaAdapterError) as exc_info:
            sandbox_backend(mock_sb, timeout_s=invalid_timeout)

        assert exc_info.value.cause_type == "InterpreterConfigurationError"
        assert "must be positive" in str(exc_info.value)

    def test_timeout_s_forwarded_to_sdk_calls(self) -> None:
        """Explicit timeout_s is forwarded to code_interpreter.run_code."""
        mock_ci = MagicMock()
        mock_ci.create_context.return_value = "ctx-t"
        mock_ci.run_code.return_value = MagicMock(stdout="done\n", stderr="", error=None)
        mock_sb = MagicMock()
        mock_sb.code_interpreter = mock_ci

        backend = sandbox_backend(mock_sb, timeout_s=42)
        interpreter = DaytonaCodeInterpreter(backend=backend)
        interpreter.start()

        interpreter.execute("x = 1")
        _, kwargs = mock_ci.run_code.call_args
        assert kwargs.get("timeout") == 42

    def test_execute_empty_code_rejected(self) -> None:
        """Empty or whitespace-only code raises empty_code error."""
        mock_sb = MagicMock()
        backend = sandbox_backend(mock_sb)
        interpreter = DaytonaCodeInterpreter(backend=backend)
        interpreter.start()

        with pytest.raises(CodeExecutionError) as exc_info:
            interpreter.execute("   \n\t  \n  ")

        assert getattr(exc_info.value, "category", None) == "empty_code"

    def test_execute_code_too_large_rejected(self) -> None:
        """Code exceeding max_code_chars raises code_too_large error."""
        mock_sb = MagicMock()
        backend = sandbox_backend(mock_sb)
        interpreter = DaytonaCodeInterpreter(backend=backend, max_code_chars=100)
        interpreter.start()

        huge_code = "# " + ("x" * 200)
        with pytest.raises(CodeExecutionError) as exc_info:
            interpreter.execute(huge_code)

        assert getattr(exc_info.value, "category", None) == "code_too_large"

    def test_execute_large_output_truncated_safely(self) -> None:
        """Outputs exceeding execution_output_cap are truncated without crash or overflow."""
        mock_ci = MagicMock()
        mock_ci.create_context.return_value = "ctx-huge"
        raw_output = "LINE_" + ("0123456789" * 5000)  # 50,005 chars
        mock_ci.run_code.return_value = MagicMock(stdout=raw_output, stderr="", error=None)
        mock_sb = MagicMock()
        mock_sb.code_interpreter = mock_ci

        backend = sandbox_backend(mock_sb)
        interpreter = DaytonaCodeInterpreter(backend=backend, execution_output_cap=2000)
        interpreter.start()

        result = interpreter.execute("print('huge')")
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
        mock_ci = MagicMock()
        mock_ci.create_context.return_value = "ctx-m"
        mock_sb = MagicMock()
        mock_sb.code_interpreter = mock_ci

        backend = sandbox_backend(mock_sb)
        interpreter = DaytonaCodeInterpreter(backend=backend)
        interpreter.start()

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
            mock_ci.run_code.return_value = MagicMock(
                stdout=stdout_content,
                stderr="",
                error=None,
            )
            result = interpreter.execute("# probe malformed marker")
            assert not isinstance(result, FinalOutput), (
                f"Malformed marker was parsed into FinalOutput: {stdout_content!r}"
            )
            assert isinstance(result, str)

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

        # Now test through interpreter
        mock_ci = MagicMock()
        mock_ci.create_context.return_value = "ctx-ok"
        mock_ci.run_code.return_value = MagicMock(
            stdout=f"Log start\n{frame}\nLog finish",
            stderr="",
            error=None,
        )
        mock_sb = MagicMock()
        mock_sb.code_interpreter = mock_ci

        backend = sandbox_backend(mock_sb)
        interpreter = DaytonaCodeInterpreter(backend=backend)
        interpreter.start()

        result = interpreter.execute("SUBMIT(...)")
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
    async def test_child_sandbox_network_block_all_isolation(self) -> None:
        """EMPIRICAL CHALLENGE: Child sandboxes must execute with network_block_all=True.

        Requirement R1 & Acceptance Criteria:
        'Manage root sandboxes (session-scoped with /workspace volume mount)
         and ephemeral child sandboxes (network_block_all=True).'
        'Child sandboxes execute with network_block_all=True and clean up reliably.'
        """
        loop = asyncio.get_running_loop()
        mock_platform = MagicMock()
        mock_platform.create = AsyncMock()
        mock_admission = MagicMock()
        mock_admission.acquire = AsyncMock()
        mock_interpreter = MagicMock()

        await acquire_child_runtime(
            loop=loop,
            platform=mock_platform,
            admission=mock_admission,
            volume_id=None,
            mount_path=None,
            profile=DaytonaEnvironmentProfile.SEMANTIC_CHILD,
            workspace_id=uuid4(),
            run_id=uuid4(),
            call_index=1,
            deadline=loop.time() + 10.0,
            execution_timeout_s=30,
            execution_output_cap=1000,
            interpreter_factory=lambda **_kwargs: mock_interpreter,
            sandbox_backend_factory=lambda *_args, **_kwargs: MagicMock(),
            close_child_runtime=MagicMock(),
            cleanup_after_failed_acquire=AsyncMock(),
            sandbox_id_for_fn=lambda _s: "sb-child-isolation",
        )

        mock_platform.create.assert_awaited_once()
        call_kwargs = mock_platform.create.call_args.kwargs

        # EMPIRICAL ASSERTION: Verify that network_block_all=True was passed
        assert call_kwargs.get("network_block_all") is True, (
            f"Child sandbox created without network_block_all=True! Received: {call_kwargs.get('network_block_all')}"
        )
