"""Daytona workspace and the synchronous DSPy interpreter boundary."""

import asyncio
import inspect
import json
import re
from collections.abc import Awaitable, Callable, Collection, Coroutine
from contextlib import suppress
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID, uuid4

from daytona import (
    AsyncDaytona,
    AsyncSandbox,
    CreateSandboxFromSnapshotParams,
    DaytonaConfig,
    DaytonaError,
    DaytonaFileNotFoundError,
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

Execution = Callable[[], Awaitable[str | FinalOutput]]
ExecutionObserver = Callable[[str, Execution], Awaitable[str | FinalOutput]]
ToolObserver = Callable[[str, list, dict, Callable[[], Awaitable[Any]]], Awaitable[Any]]


def make_daytona_client(settings: Settings) -> AsyncDaytona:
    return AsyncDaytona(DaytonaConfig(api_key=settings.daytona_api_key.get_secret_value()))


async def ensure_session_sandbox(
    daytona: AsyncDaytona,
    settings: Settings,
    session_id: UUID,
    existing_sandbox_id: str | None,
) -> tuple[AsyncSandbox, str, bool]:
    if existing_sandbox_id:
        sandbox = await daytona.get(existing_sandbox_id)
        if sandbox.state != SandboxState.STARTED:
            await sandbox.start()
        return sandbox, sandbox.id, False

    sandbox = await daytona.create(
        CreateSandboxFromSnapshotParams(
            snapshot=settings.daytona_snapshot,
            language="python",
            target=settings.daytona_target,
            auto_stop_interval=settings.daytona_auto_stop_minutes,
            volumes=[
                VolumeMount(
                    volume_id=settings.daytona_volume_id,
                    mount_path=WORKSPACE,
                    subpath=f"sessions/{session_id}",
                )
            ],
        )
    )
    # Mounting a new Volume subpath can hide the image's workspace directories.
    try:
        result = await sandbox.process.code_run(
            "from pathlib import Path\n"
            "Path('/workspace/files').mkdir(exist_ok=True)\n"
            "Path('/workspace/artifacts').mkdir(exist_ok=True)",
            timeout=settings.daytona_exec_timeout_seconds,
        )
        if result.exit_code != 0:
            raise RuntimeError("Could not initialize the Daytona workspace")
    except (Exception, asyncio.CancelledError):
        await daytona.delete(sandbox, wait=True)
        raise
    return sandbox, sandbox.id, True


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

    def run(code, values, fields, tools, token, timeout, limit, frame_limit):
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
                deadline = time.monotonic() + timeout if timeout else float("inf")
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
                return await self._execute(code, variables or {})

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
        tasks: set[asyncio.Task] = set()
        seen: set[str] = set()
        submitted = None
        active = True
        stderr_chars = 0

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
                message = f"Host tool {name!r} failed ({type(error).__name__})"
                data = json.dumps({"error": message}).encode()

            if active and not self._closed:
                path = f"{self._mailbox}/{token}-{request['id']}.json"
                await self._sandbox.fs.upload_file(data, f"{path}.tmp", timeout=self._timeout)
                if active and not self._closed:
                    await self._sandbox.fs.move_files(
                        f"{path}.tmp", path, request_timeout=self._timeout
                    )

        def receive(frame: dict) -> None:
            nonlocal submitted
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
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        def receive_stderr(message: OutputMessage) -> None:
            nonlocal stderr_chars
            stderr_chars += len(message.output)
            if stderr_chars > 8 * _MAX_FRAME:
                raise CodeExecutionError("Interpreter stderr exceeded the transport limit")

        output = _Output(token, self._limit, receive)
        fields = [field["name"] for field in self.output_fields]
        values = json.dumps(variables, allow_nan=False)
        source = (
            f"__fleet_run({code!r}, {values!r}, {fields!r}, {list(tools)!r}, {token!r}, "
            f"{self._timeout!r}, {self._limit!r}, {_MAX_FRAME!r})"
        )
        try:
            async with asyncio.timeout(self._timeout or None), asyncio.TaskGroup() as group:
                try:
                    result = await self._sandbox.code_interpreter.run_code(
                        source,
                        context=self._context,
                        on_stdout=output.feed,
                        on_stderr=receive_stderr,
                        timeout=self._timeout,
                    )
                finally:
                    active = False
                    for task in tasks.copy():
                        task.cancel()
        except TimeoutError as error:
            await self._delete_context()
            raise CodeExecutionError(
                f"Python execution timed out after {self._timeout}s; interpreter context was reset "
                "(in-memory variables cleared, /workspace files preserved)."
            ) from error
        except (ExceptionGroup, DaytonaError) as error:
            self._closed = True
            raise CodeInterpreterError("Daytona execution transport failed or timed out") from error

        if result.error:
            raise CodeExecutionError(self._format_error(result.error))
        stdout = output.finish()
        return submitted if submitted is not None else stdout


def make_interpreter_factory(
    sandbox: AsyncSandbox,
    settings: Settings,
    loop: asyncio.AbstractEventLoop,
) -> Callable[..., DaytonaCodeInterpreter]:
    def factory(
        *,
        on_execution: ExecutionObserver | None = None,
        on_tool: ToolObserver | None = None,
    ) -> DaytonaCodeInterpreter:
        return DaytonaCodeInterpreter(
            sandbox,
            loop,
            timeout=settings.daytona_exec_timeout_seconds,
            max_output_chars=settings.rlm_max_output_chars,
            on_execution=on_execution,
            on_tool=on_tool,
        )

    factory.execution_instructions = DaytonaCodeInterpreter.execution_instructions
    return factory


async def list_new_artifacts(sandbox: AsyncSandbox, known_paths: Collection[str]) -> list[dict]:
    """Hash new regular files within the artifact tree without downloading their contents."""
    known = sorted({str(PurePosixPath(WORKSPACE) / path) for path in known_paths})
    result = await sandbox.process.code_run(
        f"root = {ARTIFACTS!r}\nknown = set({known!r})\n"
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
            if path in known or not stat.S_ISREG(os.stat(
                name, dir_fd=dir_fd, follow_symlinks=False
            ).st_mode):
                continue
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
            with os.fdopen(fd, "rb") as data:
                info = os.fstat(data.fileno())
                if stat.S_ISREG(info.st_mode):
                    files.append({
                        "path": path,
                        "sha256": hashlib.file_digest(data, "sha256").hexdigest(),
                        "size_bytes": info.st_size,
                    })
print(json.dumps(sorted(files, key=lambda item: item["path"])))
""",
        timeout=60,
    )
    if result.exit_code != 0:
        raise RuntimeError("Could not discover Daytona artifacts")
    return json.loads(result.result)
