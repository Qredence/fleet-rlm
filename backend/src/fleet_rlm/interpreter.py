"""Daytona workspace and the synchronous DSPy interpreter boundary."""

import asyncio
import base64
import inspect
import json
import logging
import re
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Collection,
    Coroutine,
    Mapping,
)
from contextlib import asynccontextmanager, suppress
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID, uuid4

import mlflow
from daytona import AsyncDaytona, DaytonaConfig
from daytona.v0 import (
    AsyncSandbox,
    CreateSandboxFromSnapshotParams,
    DaytonaConnectionTimeoutError,
    DaytonaError,
    DaytonaFileNotFoundError,
    DaytonaTimeoutError,
    OutputMessage,
    SandboxState,
    VolumeMount,
)
from dspy.primitives.code_interpreter import CodeExecutionError, CodeInterpreterError, FinalOutput
from dspy.primitives.sandbox_guest import INTERPRETER_TOOL

from .config import Settings

WORKSPACE = "/workspace"
ARTIFACTS = f"{WORKSPACE}/artifacts"
_MAX_FRAME = 1024 * 1024
_LOG = logging.getLogger(__name__)

Execution = Callable[[], Awaitable[str | FinalOutput]]
ExecutionObserver = Callable[[str, Execution], Awaitable[str | FinalOutput]]
ToolObserver = Callable[[str, list, dict, Callable[[], Awaitable[Any]]], Awaitable[Any]]


def make_daytona_client(settings: Settings) -> AsyncDaytona:
    return AsyncDaytona(DaytonaConfig(api_key=settings.daytona_api_key.get_secret_value()))


def _is_recoverable_timeout(error: BaseException) -> bool:
    """Return True for recoverable cell timeouts, excluding transport/connection timeouts."""
    if isinstance(error, BaseExceptionGroup):
        return bool(error.exceptions) and all(
            _is_recoverable_timeout(sub) for sub in error.exceptions
        )
    if isinstance(error, DaytonaConnectionTimeoutError):
        return False
    return isinstance(error, (TimeoutError, DaytonaTimeoutError))


class SandboxStartupError(RuntimeError):
    """The Session workspace could not be started."""


async def _provision_sandbox(
    daytona: AsyncDaytona, settings: Settings, *, volumes: list[VolumeMount] | None = None
) -> AsyncSandbox:
    name = f"fleet-{uuid4().hex}"
    sandbox = None
    try:
        sandbox = await daytona.v0.create(
            CreateSandboxFromSnapshotParams(
                name=name,
                snapshot=settings.daytona_snapshot,
                language="python",
                target=settings.daytona_target,
                auto_stop_interval=settings.daytona_auto_stop_minutes,
                volumes=volumes,
            )
        )
        # Mounting a new Volume subpath can hide the image's workspace directories.
        result = await sandbox.process.code_run(
            "from pathlib import Path\n"
            "Path('/workspace/files').mkdir(parents=True, exist_ok=True)\n"
            "Path('/workspace/artifacts').mkdir(parents=True, exist_ok=True)",
            timeout=settings.daytona_exec_timeout_seconds,
        )
        if result.exit_code != 0:
            raise SandboxStartupError("Could not initialize the Daytona workspace")
        return sandbox
    except (Exception, asyncio.CancelledError):
        # Create can allocate a remote sandbox and then fail while waiting for startup.
        try:
            if sandbox is None:
                sandbox = await daytona.v0.get(name, request_timeout=10)
            await daytona.v0.delete(sandbox, timeout=15, wait=True)
        except DaytonaError as error:
            if error.status_code != 404:
                _LOG.warning(
                    "Could not clean up failed Daytona sandbox %s (%s)", name, type(error).__name__
                )
        raise


@asynccontextmanager
async def ephemeral_sandbox(
    daytona: AsyncDaytona,
    settings: Settings,
) -> AsyncGenerator[AsyncSandbox]:
    """Create a volume-free Daytona sandbox for offline optimization and always delete it."""
    sandbox = await _provision_sandbox(daytona, settings)
    try:
        yield sandbox
    finally:
        await daytona.v0.delete(sandbox, wait=True)


async def ensure_session_sandbox(
    daytona: AsyncDaytona,
    settings: Settings,
    session_id: UUID,
    existing_sandbox_id: str | None,
) -> tuple[AsyncSandbox, str, bool]:
    try:
        if existing_sandbox_id:
            sandbox = await daytona.v0.get(existing_sandbox_id)
            if sandbox.state != SandboxState.STARTED:
                await sandbox.start()
            return sandbox, sandbox.id, False

        sandbox = await _provision_sandbox(
            daytona,
            settings,
            volumes=[
                VolumeMount(
                    volume_id=settings.daytona_volume_id,
                    mount_path=WORKSPACE,
                    subpath=f"sessions/{session_id}",
                )
            ],
        )
    except DaytonaError as error:
        raise SandboxStartupError("Daytona workspace startup failed") from error
    return sandbox, sandbox.id, True


async def delete_sandbox(daytona: AsyncDaytona, sandbox: AsyncSandbox) -> None:
    """Delete a resolved Daytona v0 sandbox and wait for removal."""
    await daytona.v0.delete(sandbox, wait=True)


async def delete_sandbox_by_id(daytona: AsyncDaytona, sandbox_id: str) -> None:
    """Look up a Daytona v0 sandbox by ID and delete it."""
    sandbox = await daytona.v0.get(sandbox_id)
    await daytona.v0.delete(sandbox, wait=True)


async def upload_workspace_file(
    sandbox: AsyncSandbox,
    chunks: AsyncIterator[bytes],
    relative_path: str,
    *,
    timeout: int,
) -> None:
    """Stream chunks to a temporary file under /workspace/files and atomically rename."""
    temporary = f"{WORKSPACE}/files/.{uuid4().hex}.upload"
    try:
        await sandbox.fs.upload_file_stream(chunks, temporary, timeout=timeout)
        await sandbox.fs.move_files(
            temporary, f"{WORKSPACE}/{relative_path}", request_timeout=timeout
        )
    except (Exception, asyncio.CancelledError):
        with suppress(DaytonaError):
            await sandbox.fs.delete_file(temporary, request_timeout=timeout)
        raise


async def delete_workspace_file(
    sandbox: AsyncSandbox,
    relative_path: str,
    *,
    timeout: int,
) -> None:
    """Delete a workspace file under /workspace."""
    await sandbox.fs.delete_file(f"{WORKSPACE}/{relative_path}", request_timeout=timeout)


async def download_workspace_file(
    sandbox: AsyncSandbox,
    relative_path: str,
    *,
    timeout: int,
) -> bytes:
    """Download a workspace file under /workspace."""
    return await sandbox.fs.download_file(f"{WORKSPACE}/{relative_path}", timeout)


class WorkspaceBrowseLimit(RuntimeError):
    pass


async def _workspace_query(sandbox: AsyncSandbox, code: str, timeout: int):
    result = await sandbox.process.code_run(code, timeout=timeout)
    if result.exit_code != 0:
        raise RuntimeError("Could not read the Daytona workspace")
    return json.loads(result.result)


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


# The interpreter socket only sends output. Upload each response to a private
# sandbox-local mailbox and atomically rename it before the guest reads it.
_PRELUDE = r"""
def __fleet_prelude(mailbox):
    import contextlib
    import json
    import os
    import sys
    import time
    import uuid

    os.makedirs(mailbox, mode=0o700)
    namespace = globals()

    class Submitted(BaseException):
        pass

    class BoundedWriter:
        def __init__(self, stream, limit):
            self.stream = stream
            self.remaining = limit

        def write(self, text):
            if self.remaining > 0:
                self.stream.write(text[:self.remaining])
                self.remaining -= len(text)
            return len(text)

        def __getattr__(self, name):
            return getattr(self.stream, name)

    def run(code, values, fields, tools, token, rpc_timeout, limit, frame_limit):
        stdout = sys.stdout
        active = True

        def emit(data):
            payload = json.dumps(data, allow_nan=False, separators=(",", ":"))
            if len(payload) > frame_limit:
                raise ValueError("Interpreter message exceeds the size limit")
            stdout.write("\x1e" + token + " " + payload + "\n")
            stdout.flush()

        def tool(name):
            def call(*args, **kwargs):
                if not active:
                    raise RuntimeError("Tool call belongs to a finished execution")
                request_id = uuid.uuid4().hex
                path = os.path.join(mailbox, token + "-" + request_id + ".json")
                emit({
                    "kind": "call", "id": request_id, "name": name, "args": args, "kwargs": kwargs,
                })
                deadline = time.monotonic() + rpc_timeout if rpc_timeout else float("inf")
                while True:
                    try:
                        with open(path, encoding="utf-8") as reply:
                            response = json.load(reply)
                    except FileNotFoundError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Host tool response timed out") from None
                        time.sleep(0.025)
                    else:
                        os.unlink(path)
                        if "error" in response:
                            raise RuntimeError(response["error"])
                        return response["value"]
            return call

        def submit(*args, **kwargs):
            if len(args) > len(fields):
                raise TypeError("Too many positional SUBMIT arguments")
            submitted = dict(zip(fields, args))
            if submitted.keys() & kwargs.keys() or kwargs.keys() - set(fields):
                raise TypeError("Invalid SUBMIT arguments")
            submitted.update(kwargs)
            emit({"kind": "submit", "value": submitted})
            raise Submitted()

        namespace.update(json.loads(values))
        namespace.update({name: tool(name) for name in tools})
        namespace["SUBMIT"] = submit
        try:
            with (
                contextlib.redirect_stdout(BoundedWriter(stdout, limit)),
                contextlib.redirect_stderr(BoundedWriter(sys.stderr, limit)),
            ):
                exec(compile(code, "<workspace>", "exec"), namespace)
        except Submitted:
            pass
        finally:
            active = False

    return run
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

    def feed(self, message: OutputMessage) -> None:
        self.received += len(message.output)
        # The SDK also keeps its own copy. Bound traffic bypassing the guest writers.
        if self.received > 8 * _MAX_FRAME:
            raise CodeExecutionError("Interpreter output exceeded the transport limit")
        self.buffer += message.output
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
        on_execution: ExecutionObserver | None = None,
        on_tool: ToolObserver | None = None,
    ):
        self.tools: dict[str, Callable] = {}
        self.output_fields: list[dict] = []
        self._sandbox = sandbox
        self._loop = loop
        self._timeout = timeout
        self._limit = max(0, max_output_chars)
        self._on_execution = on_execution
        self._on_tool = on_tool
        self._mailbox = f"/tmp/fleet-rpc-{uuid4().hex}"
        self._context = None
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
                    attributes={"daytona.sandbox_id": self._sandbox.id},
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
        if self._context is not None:
            return
        try:
            self._context = await self._sandbox.code_interpreter.create_context(
                cwd=WORKSPACE, request_timeout=self._timeout
            )
            if self._closed:
                raise CodeInterpreterError("Interpreter closed during context creation")
            result = await self._sandbox.code_interpreter.run_code(
                _PRELUDE + f"\n__fleet_run = __fleet_prelude({self._mailbox!r})",
                context=self._context,
                timeout=self._timeout,
            )
            if result.error:
                raise CodeInterpreterError(self._format_error(result.error))
        except DaytonaError as error:
            self._closed = True
            await self._delete_context()
            raise CodeInterpreterError("Daytona context setup failed") from error
        except (CodeInterpreterError, asyncio.CancelledError):
            self._closed = True
            await self._delete_context()
            raise

    async def abort(self) -> None:
        """Fence replies, wait for the active cell and its tool tasks, then delete its context."""
        async with self._closing:
            self._closed = True
            operation = self._operation
            if operation is not None:
                # Once create_context has returned, we know which remote context to delete.
                if self._context is not None:
                    operation.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await asyncio.shield(operation)
            await self._delete_context()

    async def _delete_context(self) -> None:
        if self._context is None:
            return
        try:
            await self._sandbox.code_interpreter.delete_context(
                self._context, request_timeout=self._timeout
            )
        finally:
            self._context = None
            with suppress(DaytonaFileNotFoundError):
                await self._sandbox.fs.delete_file(
                    self._mailbox, recursive=True, request_timeout=self._timeout
                )

    def _format_error(self, error: Any) -> str:
        budget = max(256, self._limit)
        return json.dumps(
            {
                "name": error.name[:128],
                "message": error.value[:budget],
                "traceback": error.traceback[-budget:],
            }
        )

    async def _execute(self, code: str, variables: dict) -> str | FinalOutput:
        token = uuid4().hex
        tools = self.tools.copy()
        tasks: set[asyncio.Task[None]] = set()
        seen: set[str] = set()
        submitted = None
        active = True
        stderr_chars = 0
        loop = asyncio.get_running_loop()
        active_elapsed = 0.0
        active_started_at: float | None = loop.time()
        sandbox_active = asyncio.Event()
        sandbox_active.set()
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

            try:
                if self._on_tool is None:
                    value = await invoke()
                else:
                    value = await self._on_tool(name, args, kwargs, invoke)
                data = json.dumps({"value": value}, allow_nan=False).encode()
                if len(data) > _MAX_FRAME:
                    raise ValueError("Host tool response exceeds the size limit")
            except Exception as error:
                if isinstance(error, CodeInterpreterError) and not isinstance(
                    error, CodeExecutionError
                ):
                    raise
                if isinstance(error, CodeExecutionError):
                    message = (
                        str(error)[:512] or f"Host tool {name!r} failed ({type(error).__name__})"
                    )
                else:
                    message = f"Host tool {name!r} failed ({type(error).__name__})"
                data = json.dumps({"error": message}).encode()

            if active and not self._closed:
                path = f"{self._mailbox}/{token}-{request['id']}.json"
                await self._sandbox.fs.upload_file(data, f"{path}.tmp", timeout=self._timeout)
                if active and not self._closed:
                    await self._sandbox.fs.move_files(
                        f"{path}.tmp", path, request_timeout=self._timeout
                    )

        def on_task_done(done_task: asyncio.Task[None]) -> None:
            nonlocal active_started_at
            tasks.discard(done_task)
            if not tasks and active_started_at is None:
                active_started_at = loop.time()
                sandbox_active.set()

        def receive(frame: dict) -> None:
            nonlocal submitted, active_elapsed, active_started_at
            if not active or self._closed:
                return
            if frame["kind"] == "submit":
                submitted = FinalOutput(frame["value"])
                return
            request_id = frame["id"]
            if (
                frame["kind"] != "call"
                or not re.fullmatch(r"[a-f0-9]{32}", request_id)
                or frame["name"] not in tools
                or request_id in seen
                or len(tasks) >= 32
                or len(seen) >= 1024
            ):
                raise CodeExecutionError("Invalid interpreter tool request")
            seen.add(request_id)
            task = group.create_task(reply(frame))
            if not tasks and active_started_at is not None:
                active_elapsed += loop.time() - active_started_at
                active_started_at = None
                sandbox_active.clear()
            tasks.add(task)
            task.add_done_callback(on_task_done)

        def receive_stderr(message: OutputMessage) -> None:
            nonlocal stderr_chars
            stderr_chars += len(message.output)
            if stderr_chars > 8 * _MAX_FRAME:
                raise CodeExecutionError("Interpreter stderr exceeded the transport limit")

        async def watch(target: asyncio.Task[Any]) -> None:
            nonlocal timed_out
            budget = float(self._timeout)
            while not target.done():
                await sandbox_active.wait()
                if target.done():
                    return
                elapsed = active_elapsed + (
                    loop.time() - active_started_at if active_started_at is not None else 0.0
                )
                remaining = budget - elapsed
                if remaining <= 0:
                    timed_out = True
                    target.cancel()
                    return
                await asyncio.sleep(remaining)

        output = _Output(token, self._limit, receive)
        fields = [field["name"] for field in self.output_fields]
        values = json.dumps(variables, allow_nan=False)
        remote_timeout = max(self._timeout * 4, 600) if self._timeout else 0
        source = (
            f"__fleet_run({code!r}, {values!r}, {fields!r}, {list(tools)!r}, {token!r}, "
            f"{remote_timeout!r}, {self._limit!r}, {_MAX_FRAME!r})"
        )
        try:
            async with asyncio.TaskGroup() as group:
                run_task = asyncio.create_task(
                    self._sandbox.code_interpreter.run_code(
                        source,
                        context=self._context,
                        on_stdout=output.feed,
                        on_stderr=receive_stderr,
                        timeout=remote_timeout,
                    )
                )
                watchdog = asyncio.create_task(watch(run_task)) if self._timeout else None
                try:
                    result = await run_task
                except asyncio.CancelledError:
                    current = asyncio.current_task()
                    if timed_out and not (current is not None and current.cancelling()):
                        raise TimeoutError from None
                    raise
                finally:
                    active = False
                    if watchdog is not None:
                        watchdog.cancel()
                        with suppress(asyncio.CancelledError):
                            await watchdog
                    if not run_task.done():
                        run_task.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await run_task
                    for task in tasks.copy():
                        task.cancel()
        except (TimeoutError, DaytonaError, ExceptionGroup) as error:
            if _is_recoverable_timeout(error):
                try:
                    await self._delete_context()
                except DaytonaError as cleanup_error:
                    self._closed = True
                    raise CodeInterpreterError(
                        "Daytona execution transport failed or timed out"
                    ) from cleanup_error
                raise CodeExecutionError(
                    f"Python execution timed out after {self._timeout}s; interpreter context was "
                    "reset (in-memory variables cleared, /workspace files preserved)."
                ) from error
            self._closed = True
            raise CodeInterpreterError("Daytona execution transport failed or timed out") from error

        if result.error:
            raise CodeExecutionError(self._format_error(result.error))
        stdout = output.finish()
        return submitted if submitted is not None else stdout


class _DaytonaInterpreterFactory:
    execution_instructions: str = DaytonaCodeInterpreter.execution_instructions

    def __init__(
        self,
        sandbox: AsyncSandbox,
        settings: Settings,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._sandbox = sandbox
        self._settings = settings
        self._loop = loop

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
            on_execution=on_execution,
            on_tool=on_tool,
        )


def make_interpreter_factory(
    sandbox: AsyncSandbox,
    settings: Settings,
    loop: asyncio.AbstractEventLoop,
) -> Callable[..., DaytonaCodeInterpreter]:
    return _DaytonaInterpreterFactory(sandbox, settings, loop)


async def list_new_artifacts(
    sandbox: AsyncSandbox,
    known_paths: Collection[str] | Mapping[str, tuple[str, int]],
) -> list[dict]:
    """Hash new or modified regular files within the artifact tree without downloading contents."""
    if isinstance(known_paths, Mapping):
        known_hashes: dict[str, tuple[str, int]] = {}
        for path, metadata in known_paths.items():
            if not isinstance(metadata, tuple) or len(metadata) != 2:
                raise TypeError("Expected (sha256, size_bytes) tuple for known artifact metadata")
            sha256, size_bytes = metadata
            known_hashes[str(PurePosixPath(WORKSPACE) / path)] = (str(sha256), int(size_bytes))
        header = f"root = {ARTIFACTS!r}\nknown_paths = set()\nknown_hashes = {known_hashes!r}\n"
    else:
        known = sorted({str(PurePosixPath(WORKSPACE) / path) for path in known_paths})
        header = f"root = {ARTIFACTS!r}\nknown_paths = set({known!r})\nknown_hashes = None\n"
    result = await sandbox.process.code_run(
        header
        + """
import hashlib
import json
import os
import stat

files = []
if os.path.isdir(root) and not os.path.islink(root):
    for directory, _, names, dir_fd in os.fwalk(root, follow_symlinks=False):
        for name in names:
            path = os.path.join(directory, name)
            if path in known_paths or not stat.S_ISREG(
                os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode
            ):
                continue
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
            with os.fdopen(fd, "rb") as data:
                info = os.fstat(data.fileno())
                if stat.S_ISREG(info.st_mode):
                    digest = hashlib.file_digest(data, "sha256").hexdigest()
                    if known_hashes is not None and known_hashes.get(path) == (
                        digest,
                        info.st_size,
                    ):
                        continue
                    files.append({
                        "path": path,
                        "sha256": digest,
                        "size_bytes": info.st_size,
                    })
print(json.dumps(sorted(files, key=lambda item: item["path"])))
""",
        timeout=60,
    )
    if result.exit_code != 0:
        raise RuntimeError("Could not discover Daytona artifacts")
    return json.loads(result.result)
