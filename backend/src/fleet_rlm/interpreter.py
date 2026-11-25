"""Daytona workspace and the synchronous DSPy interpreter boundary."""

import asyncio
import base64
import codecs
import inspect
import json
import logging
import os
import re
from collections.abc import (
    AsyncGenerator,
    Awaitable,
    Callable,
    Collection,
    Coroutine,
    Generator,
    Mapping,
)
from contextlib import aclosing, asynccontextmanager, contextmanager, suppress
from contextvars import Context, copy_context
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast
from uuid import uuid4

import mlflow
from daytona import AsyncDaytona, DaytonaConfig
from daytona.v2 import AsyncSandbox
from daytona.v2.errors import (
    ClientTimeoutError,
    DaytonaError,
    NotFoundError,
    SandboxCreateConnectionError,
    SandboxCreateRequestTimeoutError,
)
from daytona.v2.errors import ConnectionError as DaytonaConnectionError
from daytona.v2.process import AsyncProcessHandle
from daytona.v2.types import StreamLogEvent
from dspy.primitives.code_interpreter import CodeExecutionError, CodeInterpreterError, FinalOutput
from dspy.primitives.sandbox_guest import INTERPRETER_TOOL
from mlflow.entities import LiveSpan

from .config import Settings

WORKSPACE = "/workspace"
ARTIFACTS = f"{WORKSPACE}/artifacts"
_MAX_FRAME = 1024 * 1024
_LOG = logging.getLogger(__name__)

Execution = Callable[[], Awaitable[str | FinalOutput]]
ExecutionObserver = Callable[[str, Execution], Awaitable[str | FinalOutput]]
ToolObserver = Callable[[str, list, dict, Callable[[], Awaitable[Any]]], Awaitable[Any]]


@contextmanager
def workspace_span(
    name: str, *, root: bool = False, attributes: dict[str, Any] | None = None
) -> Generator[LiveSpan | None]:
    if not root and mlflow.get_current_active_span() is None:
        yield None
        return
    with mlflow.start_span(name, span_type="CHAIN", attributes=attributes) as span:
        try:
            yield span
        except BaseException as error:
            # Provider exception details can contain URLs or other request data.
            # End the span before re-raising so MLflow records only the error type.
            span.set_status("ERROR")
            span.set_attribute("error.type", type(error).__name__)
            failure = error
        else:
            return
    raise failure


@asynccontextmanager
async def make_daytona_client(settings: Settings) -> AsyncGenerator[AsyncDaytona]:
    if "DAYTONA_API_URL" in os.environ or "DAYTONA_V2_API_URL" in os.environ:
        raise ValueError("Unset DAYTONA_API_URL and DAYTONA_V2_API_URL for Daytona v2")
    async with AsyncDaytona(
        DaytonaConfig(
            api_key=settings.daytona_api_key.get_secret_value(),
            request_timeout=30,
            otel_enabled=settings.daytona_otel_enabled,
        )
    ) as daytona:
        yield daytona


class SandboxStartupError(RuntimeError):
    """The Session workspace could not be started."""


class SandboxCleanupError(CodeInterpreterError):
    """The sandbox must be retired because its worker could not be stopped."""


def sandbox_cleanup_failed(error: BaseException) -> bool:
    if isinstance(error, SandboxCleanupError):
        return True
    if isinstance(error, BaseExceptionGroup) and any(map(sandbox_cleanup_failed, error.exceptions)):
        return True
    return error.__cause__ is not None and sandbox_cleanup_failed(error.__cause__)


async def _provision_sandbox(
    daytona: AsyncDaytona, settings: Settings, *, spot: bool = False
) -> AsyncSandbox:
    name = f"fleet-{uuid4().hex}"
    sandbox = None
    try:
        async with asyncio.timeout(120):
            sandbox = await _create_sandbox(daytona, settings, name, spot=spot)
            await prepare_sandbox(sandbox)
            return sandbox
    except (Exception, asyncio.CancelledError) as error:
        # A timed-out create may have allocated a sandbox before the response arrived.
        try:
            async with asyncio.timeout(20):
                candidates = (sandbox,) if sandbox else ()
                if sandbox is None:
                    uncertain = isinstance(
                        error,
                        (
                            asyncio.CancelledError,
                            TimeoutError,
                            SandboxCreateConnectionError,
                            SandboxCreateRequestTimeoutError,
                        ),
                    )
                    delays = (0, 0.5, 1.5, 3) if uncertain else (0,)
                    for delay in delays:
                        await asyncio.sleep(delay)
                        candidates = await daytona.v2.find_by_name(
                            name, region=settings.daytona_region
                        )
                        if candidates:
                            break
                for candidate in candidates:
                    await delete_sandbox(daytona, candidate)
        except (DaytonaError, TimeoutError) as cleanup_error:
            _LOG.warning(
                "Could not clean up Daytona allocation %s (%s)", name, type(cleanup_error).__name__
            )
        if isinstance(error, (DaytonaError, TimeoutError)):
            raise SandboxStartupError("Daytona workspace startup failed") from error
        raise


async def _create_sandbox(
    daytona: AsyncDaytona, settings: Settings, name: str, *, spot: bool = False
) -> AsyncSandbox:
    return await daytona.v2.create(
        settings.daytona_image,
        name=name,
        region=settings.daytona_region,
        spot=spot,
        create_timeout_seconds=110,
        wait_seconds=0,
        lifecycle_policy={
            "idle_expiry_seconds": settings.daytona_idle_timeout_minutes * 60,
            "on_idle_timeout": "delete",
            "timeout_seconds": settings.daytona_max_lifetime_minutes * 60,
            "on_timeout": "delete",
        },
        network_policy={"block_all": True, "allow_domains": settings.daytona_network_allow_domains},
        labels={"fleet-runtime": "v2"},
    )


async def prepare_sandbox(sandbox: AsyncSandbox) -> None:
    try:
        async with asyncio.timeout(120):
            if sandbox.state != "started":
                await sandbox.wait(until="started", timeout=110)
            result = await sandbox.process.run(
                argv=["mkdir", "-p", f"{WORKSPACE}/files", ARTIFACTS],
                timeout=10,
                execution_timeout=10,
            )
            if result.exit_code != 0:
                raise SandboxStartupError("Could not initialize the Daytona workspace")
    except (DaytonaError, TimeoutError) as error:
        raise SandboxStartupError("Daytona workspace startup failed") from error


async def allocate_session_sandbox(
    daytona: AsyncDaytona, settings: Settings, name: str, *, recovering: bool = False
) -> AsyncSandbox:
    """Resume a persisted allocation using the same name-derived idempotency key."""
    try:
        if recovering:
            matches = await daytona.v2.find_by_name(name, region=settings.daytona_region)
            if matches:
                return matches[0]
        return await _create_sandbox(daytona, settings, name)
    except DaytonaError as error:
        raise SandboxStartupError("Daytona workspace allocation failed") from error


@asynccontextmanager
async def ephemeral_sandbox(
    daytona: AsyncDaytona, settings: Settings
) -> AsyncGenerator[AsyncSandbox]:
    sandbox = await _provision_sandbox(daytona, settings, spot=settings.daytona_optimize_spot)
    try:
        yield sandbox
    finally:
        await delete_sandbox(daytona, sandbox)


async def get_running_sandbox(daytona: AsyncDaytona, sandbox_id: str) -> AsyncSandbox | None:
    try:
        sandbox = await daytona.v2.get(sandbox_id)
    except NotFoundError:
        return None
    return sandbox if sandbox.state == "started" else None


async def reusable_session_sandbox(daytona: AsyncDaytona, sandbox_id: str) -> AsyncSandbox | None:
    try:
        sandbox = await daytona.v2.get(sandbox_id)
        if sandbox.state in {"started", "queued", "creating", "starting"}:
            return sandbox
        if sandbox.state not in {"destroyed", "destroying", "error"}:
            raise SandboxStartupError("Daytona workspace is in an unexpected state")
        await delete_sandbox(daytona, sandbox)
    except NotFoundError:
        return None  # Daytona confirmed the binding is gone.
    except DaytonaError as error:
        raise SandboxStartupError("Daytona workspace startup failed") from error
    return None


async def delete_sandbox(daytona: AsyncDaytona, sandbox: AsyncSandbox) -> None:
    await delete_sandbox_by_id(daytona, sandbox.id)


async def delete_sandbox_by_id(daytona: AsyncDaytona, sandbox_id: str) -> None:
    with suppress(NotFoundError):
        await daytona.v2.delete(sandbox_id, timeout=30)


async def delete_pending_sandbox(daytona: AsyncDaytona, settings: Settings, name: str) -> None:
    for sandbox in await daytona.v2.find_by_name(name, region=settings.daytona_region):
        await delete_sandbox(daytona, sandbox)


async def reset_managed_workspace(sandbox: AsyncSandbox, *, timeout: int) -> None:
    # Running workers are closed before the next root can reconcile this private disk.
    result = await sandbox.process.run(
        argv=[
            "python",
            "-c",
            """from pathlib import Path
import shutil
for path in (Path('/workspace/files'), Path('/workspace/artifacts')):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)
    path.mkdir()
""",
        ],
        execution_timeout=timeout,
        timeout=timeout,
    )
    if result.exit_code != 0:
        raise SandboxStartupError("Could not reset the Daytona workspace")


async def upload_workspace_file(
    sandbox: AsyncSandbox,
    source: Path,
    relative_path: str,
    *,
    sha256: str,
    size: int,
    timeout: int,
) -> None:
    destination = f"{WORKSPACE}/{relative_path}"
    async with asyncio.timeout(timeout):
        result = await sandbox.process.run(
            argv=["mkdir", "-p", str(PurePosixPath(destination).parent)],
            timeout=timeout,
            execution_timeout=timeout,
        )
        if result.exit_code != 0:
            raise SandboxStartupError("Could not create the Daytona file directory")
        await sandbox.fs.upload_file(source, destination, atomic=True)
        result = await sandbox.process.run(
            argv=[
                "python",
                "-c",
                """\
import hashlib
import sys

with open(sys.argv[1], 'rb') as file:
    digest = hashlib.file_digest(file, 'sha256').hexdigest()
    if (digest, file.tell()) != (sys.argv[2], int(sys.argv[3])):
        raise SystemExit(1)
""",
                destination,
                sha256,
                str(size),
            ],
            timeout=timeout,
            execution_timeout=timeout,
        )
        if result.exit_code != 0:
            raise SandboxStartupError("Daytona workspace file failed integrity verification")


async def download_workspace_file(
    sandbox: AsyncSandbox, relative_path: str, destination: Path, *, timeout: int
) -> None:
    async with asyncio.timeout(timeout):
        await sandbox.fs.download_file(f"{WORKSPACE}/{relative_path}", destination)


class WorkspaceBrowseLimit(RuntimeError):
    pass


async def _workspace_query(sandbox: AsyncSandbox, code: str, timeout: int, *args: str):
    result = await sandbox.process.run(
        argv=["python", "-c", code, *args], timeout=timeout, execution_timeout=timeout
    )
    if result.exit_code != 0:
        raise RuntimeError("Could not read the Daytona workspace")
    return json.loads(result.stdout)


async def list_workspace_files(sandbox: AsyncSandbox, *, timeout: int) -> list[dict]:
    entries = await _workspace_query(
        sandbox,
        f"root = {WORKSPACE!r}\n"
        + """
import json
import os
import stat

entries = []
for directory, folders, names, fd in os.fwalk(root, follow_symlinks=False):
    folders[:] = sorted(name for name in folders if name != ".git"
                        and not (directory == root and name == "artifacts"))
    for name in sorted(names):
        if name == ".git":
            continue
        try:
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if stat.S_ISREG(info.st_mode):
            entries.append({
                "path": os.path.relpath(os.path.join(directory, name), root),
                "size_bytes": info.st_size,
                "revision": str(info.st_mtime_ns),
            })
        if len(entries) > 5000:
            break
    if len(entries) > 5000:
        entries = None
        break
print(json.dumps(entries, separators=(",", ":")))
""",
        timeout,
    )
    if entries is None:
        raise WorkspaceBrowseLimit("Workspace browsing supports up to 5,000 files")
    return entries


async def read_browsable_workspace_file(
    sandbox: AsyncSandbox, relative_path: str, *, timeout: int
) -> bytes | None:
    # Open each component relative to an already-open directory. A symlink
    # swap cannot redirect the read to another mount or outside the volume.
    result = await _workspace_query(
        sandbox,
        f"root = {WORKSPACE!r}\nparts = {relative_path.split('/')!r}\n"
        + """
import base64
from contextlib import ExitStack
import errno
import json
import os
import stat

limit = 1024 * 1024
result = None
try:
    with ExitStack() as stack:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(root, directory_flags)
        stack.callback(os.close, fd)
        for part in parts[:-1]:
            fd = os.open(part, directory_flags, dir_fd=fd)
            stack.callback(os.close, fd)
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file_fd, "rb") as file:
            info = os.fstat(file.fileno())
            if stat.S_ISREG(info.st_mode):
                data = file.read(limit + 1) if info.st_size <= limit else None
                result = {"data": base64.b64encode(data).decode()} if (
                    data is not None and len(data) <= limit
                ) else {"too_large": True}
except OSError as error:
    if error.errno not in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.EACCES):
        raise
print(json.dumps(result))
""",
        timeout,
    )
    if result is None:
        return None
    if result.get("too_large"):
        raise WorkspaceBrowseLimit("Workspace file preview and download support files up to 1 MiB")
    return base64.b64decode(result["data"], validate=True)


_WORKER = r"""
import contextlib
import io
import json
import queue
import sys
import threading
import traceback
import uuid

stdin, stdout, stderr = sys.stdin, sys.stdout, sys.stderr
sys.stdin = io.StringIO("")
cells = queue.Queue()
pending = {}
pending_lock = threading.Lock()
output_lock = threading.Lock()
namespace = {"__name__": "__main__"}


def read_requests():
    for line in stdin:
        request = json.loads(line)
        if request["kind"] == "execute":
            cells.put(request)
        else:
            with pending_lock:
                reply = pending.get((request["token"], request["id"]))
                if reply is not None:
                    reply.put_nowait(request)
    cells.put(None)


class Submitted(BaseException):
    pass


class BoundedWriter:
    def __init__(self, stream, limit):
        self.stream = stream
        self.remaining = limit

    def write(self, text):
        with output_lock:
            if self.remaining > 0:
                self.stream.write(text[:self.remaining])
                self.remaining -= len(text)
        return len(text)

    def __getattr__(self, name):
        return getattr(self.stream, name)


def run(request):
    token = request["token"]
    fields = request["fields"]
    limit = request["limit"]
    frame_limit = request["frame_limit"]
    active = True
    submitted = None

    def emit(data):
        payload = json.dumps(data, allow_nan=False, separators=(",", ":"))
        if len(payload) > frame_limit:
            raise ValueError("Interpreter message exceeds the size limit")
        with output_lock:
            stdout.write("\x1e" + token + " " + payload + "\n")
            stdout.flush()

    def tool(name):
        def call(*args, **kwargs):
            if not active:
                raise RuntimeError("Tool call belongs to a finished execution")
            request_id = uuid.uuid4().hex
            key = (token, request_id)
            reply = queue.Queue()
            with pending_lock:
                pending[key] = reply
            try:
                emit({"kind": "call", "id": request_id, "name": name,
                      "args": args, "kwargs": kwargs})
                try:
                    response = reply.get(timeout=request["rpc_timeout"])
                except queue.Empty:
                    raise TimeoutError("Host tool response timed out") from None
            finally:
                with pending_lock:
                    pending.pop(key, None)
            if "error" in response:
                raise RuntimeError(response["error"])
            return response["value"]
        return call

    def submit(*args, **kwargs):
        nonlocal submitted
        if len(args) > len(fields):
            raise TypeError("Too many positional SUBMIT arguments")
        values = dict(zip(fields, args))
        if values.keys() & kwargs.keys() or kwargs.keys() - set(fields):
            raise TypeError("Invalid SUBMIT arguments")
        values.update(kwargs)
        serialized = json.dumps(values, allow_nan=False)
        if len(serialized) > frame_limit // 2:
            raise ValueError("Interpreter submission exceeds the size limit")
        submitted = json.loads(serialized)
        raise Submitted()

    namespace.update(request["variables"])
    namespace.update({name: tool(name) for name in request["tools"]})
    namespace["SUBMIT"] = submit
    terminal = {"kind": "done"}
    try:
        with (
            contextlib.redirect_stdout(BoundedWriter(stdout, limit)),
            contextlib.redirect_stderr(BoundedWriter(stderr, limit)),
        ):
            exec(compile(request["code"], "<workspace>", "exec"), namespace)
    except Submitted:
        terminal["submission"] = submitted
    except BaseException as error:
        budget = max(256, min(limit, frame_limit // 16))
        terminal["error"] = {
            "name": type(error).__name__[:128],
            "message": str(error)[:budget],
            "traceback": traceback.format_exc()[-budget:],
        }
    finally:
        active = False
    emit(terminal)


threading.Thread(target=read_requests, daemon=True).start()
while (request := cells.get()) is not None:
    run(request)
"""


class _Output:
    """Collect bounded stdout and handle private records split across SDK chunks."""

    def __init__(self, token: str, limit: int, receive: Callable[[dict], None]):
        self.prefix = f"\x1e{token} "
        self.remaining = limit
        self.receive = receive
        self.parts: list[str] = []
        self.buffer = ""
        self.received = 0

    def _append(self, value: str) -> None:
        if value and self.remaining:
            self.parts.append(value[: self.remaining])
            self.remaining -= min(len(value), self.remaining)

    def feed(self, message: str) -> None:
        self.received += len(message)
        # The SDK also keeps its own copy. Bound traffic bypassing the guest writers.
        if self.received > 8 * _MAX_FRAME:
            raise CodeExecutionError("Interpreter output exceeded the transport limit")
        self.buffer += message
        while self.buffer:
            before, marker, record = self.buffer.partition("\x1e")
            self._append(before)
            self.buffer = marker + record
            if not marker:
                return
            if self.prefix.startswith(self.buffer):
                return
            if not self.buffer.startswith(self.prefix):
                self._append(marker)
                self.buffer = record
                continue
            payload, newline, rest = self.buffer[len(self.prefix) :].partition("\n")
            if len(payload) > _MAX_FRAME:
                raise CodeExecutionError("Interpreter message exceeds the size limit")
            if not newline:
                return
            self.receive(json.loads(payload))
            self.buffer = rest

    def finish(self) -> str:
        if self.buffer.startswith(self.prefix):
            raise CodeInterpreterError("Incomplete interpreter message")
        self._append(self.buffer)
        return "".join(self.parts)


class DaytonaCodeInterpreter:
    """One isolated Daytona context, driven by a DSPy worker thread."""

    execution_instructions = (
        "Execute Python in /workspace. Inputs are under /workspace/files. "
        "Place generated files under /workspace/artifacts. Use spawn_agent for recursive RLM "
        "delegation through the host's depth limit. Keep each Python cell well within the "
        "execution timeout; once an answer is verified with a trusted library or two independent "
        "checks, call SUBMIT rather than re-implementing heavy algorithms from scratch."
    )

    def __init__(
        self,
        sandbox: AsyncSandbox,
        loop: asyncio.AbstractEventLoop,
        *,
        timeout: int,
        max_output_chars: int,
        turn_deadline: float | None = None,
        on_execution: ExecutionObserver | None = None,
        on_tool: ToolObserver | None = None,
    ):
        self.tools: dict[str, Callable] = {}
        self.output_fields: list[dict] = []
        self._sandbox = sandbox
        self._loop = loop
        self._timeout = timeout
        self._turn_deadline = turn_deadline
        self._limit = max(0, max_output_chars)
        self._on_execution = on_execution
        self._on_tool = on_tool
        self._tag: str | None = None
        self._cursor: str | None = None
        self._handle: AsyncProcessHandle | None = None
        self._worker_context: Context | None = None
        self._stdin_lock = asyncio.Lock()
        self._operation: asyncio.Task | None = None
        self._closing = asyncio.Lock()
        self._closed = False

    def _bridge[T](self, operation: Coroutine[Any, Any, T]) -> T:
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is self._loop or not self._loop.is_running():
            operation.close()
            raise CodeInterpreterError("Drive Daytona from a worker of the running caller loop")
        return asyncio.run_coroutine_threadsafe(operation, self._loop).result()

    def start(self) -> None:
        self._bridge(self._perform(self._start))

    def execute(self, code: str, variables: dict[str, Any] | None = None) -> str | FinalOutput:
        async def run() -> str | FinalOutput:
            await self._start()

            async def cell() -> str | FinalOutput:
                if mlflow.get_current_active_span() is None:
                    return await self._execute(code, variables or {})
                with mlflow.start_span(
                    name="daytona.python.execute",
                    span_type="TOOL",
                    attributes={
                        "daytona.sandbox_id": self._sandbox.id,
                        "daytona.worker_id": self._handle.id if self._handle else "",
                    },
                ) as span:
                    span.set_inputs({"code": code})
                    try:
                        output = await self._execute(code, variables or {})
                    except asyncio.CancelledError:
                        span.set_status("ERROR")
                        span.set_attribute("daytona.execution.status", "cancelled")
                        raise
                    span.set_outputs(
                        {"submission": output.output}
                        if isinstance(output, FinalOutput)
                        else {"stdout": output}
                    )
                    return output

            if self._on_execution is not None:
                return await self._on_execution(code, cell)
            return await cell()

        return self._bridge(self._perform(run))

    def shutdown(self) -> None:
        self._bridge(self.abort())

    async def _perform[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        if self._closed or self._operation is not None:
            raise CodeInterpreterError("Interpreter is closed or already executing")
        self._operation = asyncio.current_task()
        try:
            return await operation()
        finally:
            self._operation = None

    async def _start(self) -> None:
        if self._handle is not None:
            return
        remaining = None
        if self._turn_deadline is not None:
            remaining = self._turn_deadline - self._loop.time()
            if remaining <= 0:
                raise TimeoutError("Turn timed out before Daytona worker startup")
        self._tag = f"fleet-python-{uuid4().hex}"
        self._cursor = None
        if mlflow.get_current_active_span() is not None:
            self._worker_context = copy_context()
        try:
            with workspace_span(
                "daytona.worker.start", attributes={"daytona.sandbox_id": self._sandbox.id}
            ) as span:
                self._handle = await self._sandbox.process.start(
                    argv=["python", "-u", "-c", _WORKER],
                    cwd=WORKSPACE,
                    stdin="open",
                    tag=self._tag,
                    execution_timeout=remaining,
                )
                if span is not None:
                    span.set_attribute("daytona.worker_id", self._handle.id)
                    if remaining is not None:
                        span.set_attribute("daytona.worker.execution_timeout_seconds", remaining)
        except (Exception, asyncio.CancelledError) as error:
            self._closed = True
            await self._reset()
            if isinstance(error, asyncio.CancelledError):
                raise
            raise CodeInterpreterError("Daytona worker startup failed") from error

    async def abort(self) -> None:
        """Fence replies and wait for the worker and its descendants to stop."""
        context = self._worker_context
        cleanup = asyncio.create_task(self._abort(), context=context.copy() if context else None)
        cancelled = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled = True
        cleanup.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _abort(self) -> None:
        async with self._closing:
            self._closed = True
            operation = self._operation
            cleanup_error = None
            if operation is not None:
                if self._handle is not None:
                    operation.cancel()
                try:
                    with suppress(asyncio.CancelledError):
                        await asyncio.shield(operation)
                except Exception as error:
                    if sandbox_cleanup_failed(error):
                        cleanup_error = error
            await self._reset()
            if cleanup_error is not None:
                raise SandboxCleanupError("A child worker could not be stopped") from cleanup_error

    async def _reset(self) -> None:
        if self._tag is None:
            return
        with workspace_span(
            "daytona.worker.stop", attributes={"daytona.sandbox_id": self._sandbox.id}
        ) as span:
            if span is not None and self._handle is not None:
                span.set_attribute("daytona.worker_id", self._handle.id)
            try:
                with suppress(NotFoundError):
                    await self._sandbox.process.kill_all(tag=self._tag, timeout=10)
            except Exception as error:
                self._closed = True
                raise SandboxCleanupError("Daytona worker cleanup could not be verified") from error
            if self._handle is not None:
                with suppress(DaytonaError):
                    await self._handle.delete()
        self._handle = None
        self._tag = None
        self._cursor = None
        self._worker_context = None

    async def _send(self, handle: AsyncProcessHandle, frame: dict) -> None:
        message = json.dumps(frame, allow_nan=False, separators=(",", ":")) + "\n"
        if len(message) > 2 * _MAX_FRAME:
            raise CodeInterpreterError("Interpreter request exceeds the size limit")
        # Serialize stdin writes. A request with unknown delivery must never be replayed.
        async with self._stdin_lock:
            if self._closed:
                raise CodeInterpreterError("Interpreter is closed")
            await handle.write_stdin(message)

    async def _read_cell(
        self, handle: AsyncProcessHandle, output: _Output, done: asyncio.Future[dict]
    ) -> dict:
        decode = codecs.getincrementaldecoder("utf-8")(errors="replace")
        stderr_bytes = 0
        for attempt in range(3):
            try:
                log_stream = cast(
                    AsyncGenerator[StreamLogEvent],
                    handle.stream_logs(cursor=self._cursor, encoding="base64"),
                )
                async with aclosing(log_stream) as logs:
                    async for event in logs:
                        if event.type != "log":
                            raise CodeInterpreterError(
                                "Daytona log history was truncated or the worker stopped"
                            )
                        if event.cursor == self._cursor:
                            continue
                        chunk = base64.b64decode(event.frame.data, validate=True)
                        if event.frame.channel == "stdout":
                            output.feed(decode.decode(chunk))
                        else:
                            stderr_bytes += len(chunk)
                            if stderr_bytes > 8 * _MAX_FRAME:
                                raise CodeInterpreterError(
                                    "Interpreter stderr exceeded the transport limit"
                                )
                        self._cursor = event.cursor
                        if done.done():
                            return done.result()
                raise ConnectionError("Daytona log stream ended before the cell completed")
            except (ConnectionError, DaytonaConnectionError, ClientTimeoutError):
                if attempt == 2:
                    raise
        raise AssertionError("Unreachable Daytona stream retry state")

    async def _execute(self, code: str, variables: dict) -> str | FinalOutput:
        handle = self._handle
        if handle is None:
            raise CodeInterpreterError("Daytona worker is unavailable")
        token = uuid4().hex
        tools = self.tools.copy()
        tasks: set[asyncio.Task[None]] = set()
        seen: set[str] = set()
        active = True
        loop = asyncio.get_running_loop()
        done: asyncio.Future[dict] = loop.create_future()
        active_elapsed = 0.0
        active_started_at: float | None = loop.time()
        changed = asyncio.Event()
        timed_out = False

        async def reply(request: dict) -> None:
            name, args, kwargs = request["name"], request["args"], request["kwargs"]

            async def invoke() -> Any:
                if name == INTERPRETER_TOOL:
                    raise CodeExecutionError("Use spawn_agent for recursive workspace RLM calls")
                function = tools[name]
                if inspect.iscoroutinefunction(function):
                    return await function(*args, **kwargs)
                value = await asyncio.to_thread(function, *args, **kwargs)
                return await value if inspect.isawaitable(value) else value

            frame = {"kind": "reply", "token": token, "id": request["id"]}
            try:
                value = (
                    await self._on_tool(name, args, kwargs, invoke)
                    if self._on_tool
                    else await invoke()
                )
                if len(json.dumps(value, allow_nan=False)) > _MAX_FRAME:
                    raise ValueError("Host tool response exceeds the size limit")
                frame["value"] = value
            except Exception as error:
                if isinstance(error, CodeInterpreterError) and not isinstance(
                    error, CodeExecutionError
                ):
                    raise
                frame["error"] = (
                    str(error)[:512]
                    if isinstance(error, CodeExecutionError) and str(error)
                    else f"Host tool {name!r} failed ({type(error).__name__})"
                )
            if active and not self._closed:
                await self._send(handle, frame)

        def on_task_done(task: asyncio.Task[None]) -> None:
            nonlocal active_started_at
            tasks.discard(task)
            if not tasks and active_started_at is None:
                active_started_at = loop.time()
                changed.set()

        def receive(frame: dict) -> None:
            nonlocal active_elapsed, active_started_at
            if not active or self._closed:
                return
            if frame.get("kind") == "done" and not done.done():
                done.set_result(frame)
                return
            request_id = frame.get("id")
            if (
                frame.get("kind") != "call"
                or not isinstance(request_id, str)
                or not re.fullmatch(r"[a-f0-9]{32}", request_id)
                or not isinstance(frame.get("name"), str)
                or frame["name"] not in tools
                or not isinstance(frame.get("args"), list)
                or not isinstance(frame.get("kwargs"), dict)
                or done.done()
            ):
                raise CodeInterpreterError("Invalid interpreter tool request")
            if request_id in seen:
                return
            if len(tasks) >= 32 or len(seen) >= 1024:
                raise CodeInterpreterError("Interpreter tool request limit exceeded")
            seen.add(request_id)
            task = group.create_task(reply(frame))
            if active_started_at is not None:
                active_elapsed += loop.time() - active_started_at
                active_started_at = None
                changed.set()
            tasks.add(task)
            task.add_done_callback(on_task_done)

        wall_timeout = max(self._timeout * 4, 600)

        async def watch(target: asyncio.Task, deadline: float) -> None:
            nonlocal timed_out
            while not target.done():
                changed.clear()
                started = active_started_at
                now = loop.time()
                remaining = deadline - now
                if started is not None:
                    remaining = min(remaining, self._timeout - active_elapsed - (now - started))
                if remaining <= 0:
                    timed_out = True
                    target.cancel()
                    return
                with suppress(TimeoutError):
                    await asyncio.wait_for(changed.wait(), remaining)

        output = _Output(token, self._limit, receive)
        message = {
            "kind": "execute",
            "token": token,
            "code": code,
            "variables": variables,
            "fields": [field["name"] for field in self.output_fields],
            "tools": list(tools),
            "rpc_timeout": wall_timeout,
            "limit": self._limit,
            "frame_limit": _MAX_FRAME,
        }
        try:
            async with asyncio.TaskGroup() as group:
                await self._send(handle, message)
                started = active_started_at = loop.time()
                task = group.create_task(self._read_cell(handle, output, done))
                watchdog = (
                    group.create_task(watch(task, started + wall_timeout))
                    if self._timeout
                    else None
                )
                try:
                    result = await task
                except asyncio.CancelledError:
                    if not timed_out:
                        raise
                finally:
                    active = False
                    if watchdog is not None:
                        watchdog.cancel()
                    for pending in tasks.copy():
                        pending.cancel()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._closed = True
            await self._reset()
            raise CodeInterpreterError("Daytona execution transport failed or timed out") from error

        if timed_out:
            await self._reset()
            raise CodeExecutionError(
                f"Python execution timed out after {self._timeout}s; interpreter context was "
                "reset (in-memory variables cleared, /workspace files preserved)."
            )
        if "error" in result:
            raise CodeExecutionError(json.dumps(result["error"]))
        if "submission" in result:
            return FinalOutput(result["submission"])
        return output.finish()


class _DaytonaInterpreterFactory:
    execution_instructions: str = DaytonaCodeInterpreter.execution_instructions

    def __init__(
        self,
        sandbox: AsyncSandbox,
        settings: Settings,
        loop: asyncio.AbstractEventLoop,
        *,
        turn_deadline: float | None,
    ) -> None:
        self._sandbox = sandbox
        self._settings = settings
        self._loop = loop
        self._turn_deadline = turn_deadline

    def __call__(
        self,
        *,
        on_execution: ExecutionObserver | None = None,
        on_tool: ToolObserver | None = None,
    ) -> DaytonaCodeInterpreter:
        return DaytonaCodeInterpreter(
            self._sandbox,
            self._loop,
            timeout=self._settings.daytona_exec_timeout_seconds,
            max_output_chars=self._settings.rlm_max_output_chars,
            turn_deadline=self._turn_deadline,
            on_execution=on_execution,
            on_tool=on_tool,
        )


def make_interpreter_factory(
    sandbox: AsyncSandbox,
    settings: Settings,
    loop: asyncio.AbstractEventLoop,
    *,
    turn_deadline: float | None = None,
) -> Callable[..., DaytonaCodeInterpreter]:
    return _DaytonaInterpreterFactory(sandbox, settings, loop, turn_deadline=turn_deadline)


class ArtifactLimitError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArtifactLimits:
    max_files: int
    max_file_bytes: int
    max_total_bytes: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "ArtifactLimits":
        return cls(
            settings.artifact_max_files,
            settings.artifact_max_file_bytes,
            settings.artifact_max_total_bytes,
        )

    def check(self, *, files: int, file_bytes: int, total_bytes: int) -> None:
        for actual, limit, message in (
            (file_bytes, self.max_file_bytes, "Each artifact can be at most {:,} bytes"),
            (files, self.max_files, "A turn can publish at most {:,} artifacts"),
            (total_bytes, self.max_total_bytes, "A turn can publish at most {:,} artifact bytes"),
        ):
            if actual > limit:
                raise ArtifactLimitError(message.format(limit))


async def list_new_artifacts(
    sandbox: AsyncSandbox,
    known_paths: Collection[str] | Mapping[str, tuple[str, int]],
    limits: ArtifactLimits,
) -> list[dict]:
    """Hash new or modified regular files within the artifact tree without downloading contents."""
    known = {
        str(PurePosixPath(WORKSPACE) / path): (
            known_paths[path] if isinstance(known_paths, Mapping) else None
        )
        for path in known_paths
    }
    manifest = f"/tmp/fleet-manifest-{uuid4().hex}.json"
    try:
        async with asyncio.timeout(60):
            await sandbox.fs.write_bytes(manifest, json.dumps(known).encode(), atomic=True)
            result = await _workspace_query(
                sandbox,
                _SCAN_ARTIFACTS,
                60,
                ARTIFACTS,
                manifest,
                str(limits.max_files),
                str(limits.max_file_bytes),
                str(limits.max_total_bytes),
            )
    except (Exception, asyncio.CancelledError):
        with suppress(DaytonaError):
            await sandbox.fs.remove(manifest)
        raise
    if isinstance(result, dict):
        limits.check(**result)
        raise ArtifactLimitError("Artifacts exceed the publication limits")
    return result


_SCAN_ARTIFACTS = """\
import hashlib
import json
import os
import stat
import sys

root, manifest = sys.argv[1:3]
max_files, max_file_bytes, max_total_bytes = map(int, sys.argv[3:])
with open(manifest, encoding='utf-8') as file:
    known = json.load(file)
os.unlink(manifest)


def scan():
    # Stop at the first limit breach; the host turns the totals into a turn error.
    files = []
    total = 0
    if not os.path.isdir(root) or os.path.islink(root):
        return files
    for directory, _, names, dir_fd in os.fwalk(root, follow_symlinks=False):
        for name in names:
            path = os.path.join(directory, name)
            if (path in known and known[path] is None) or not stat.S_ISREG(
                os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode
            ):
                continue
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
            with os.fdopen(fd, "rb") as data:
                info = os.fstat(data.fileno())
                if not stat.S_ISREG(info.st_mode):
                    continue
                if info.st_size > max_file_bytes:
                    return {"files": len(files), "file_bytes": info.st_size, "total_bytes": total}
                digest = hashlib.file_digest(data, "sha256").hexdigest()
            if known.get(path) == [digest, info.st_size]:
                continue
            files.append({"path": path, "sha256": digest, "size_bytes": info.st_size})
            total += info.st_size
            if len(files) > max_files or total > max_total_bytes:
                return {"files": len(files), "file_bytes": info.st_size, "total_bytes": total}
    return sorted(files, key=lambda item: item["path"])


print(json.dumps(scan()))
"""
