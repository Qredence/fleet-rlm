"""HTTP-in-sandbox host-tool broker for Daytona RLM mediation.

Transport choice for B1: Daytona-appropriate HTTP broker inside the sandbox
with host-side poll (mirrors legacy Fleet bridge semantics; it is not a JSON-RPC broker).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import inspect
import json
import keyword
import logging
import secrets
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import Thread
from typing import TYPE_CHECKING, Any

import httpx
from daytona import SessionExecuteRequest

from fleet_rlm.daytona.broker_source import (
    BROKER_SERVER_CODE,
    TOOL_WRAPPER_TEMPLATE,
    extract_final_payload,
    remote_submit_setup_code,
)
from fleet_rlm.daytona.errors import (
    DaytonaAdapterError,
    ProviderRequestError,
    is_transient_provider_failure,
    map_provider_error,
    provider_status_code,
    sanitize_provider_message,
)

if TYPE_CHECKING:
    from fleet_rlm.daytona.interpreter import BackendExecutionResult

logger = logging.getLogger(__name__)

DEFAULT_BROKER_PORT = 3000
_PREVIEW_LINK_RETRY_DELAYS = (0.25, 0.5)
_BROKER_SERVER_PATH = "/home/daytona/fleet_rlm_broker_server.py"
_BROKER_SESSION_COMMAND = f"cd /home/daytona && python {_BROKER_SERVER_PATH.rsplit('/', 1)[-1]}"
_MAX_EXECUTE_REQUEST_BYTES = 2 * 1024 * 1024
_MAX_EXECUTE_OUTPUT_CHARS = 64 * 1024


class FleetFinalOutputError(Exception):
    """Raised inside an interpreter when SUBMIT completes successfully."""

    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value
        super().__init__("Final output submitted")


class DaytonaHttpToolBroker:
    """Start/register/poll host-tool mediation against a Daytona sandbox."""

    def __init__(
        self,
        *,
        sandbox: Any,
        broker_port: int = DEFAULT_BROKER_PORT,
        poll_interval_s: float = 0.05,
        context_mount_root: str | None = None,
        context_manifest_sha256: str | None = None,
    ) -> None:
        self._sandbox = sandbox
        if not isinstance(broker_port, int) or isinstance(broker_port, bool) or not 0 < broker_port <= 65_535:
            raise ValueError(f"broker_port must be between 1 and 65535, got {broker_port!r}")
        self._broker_port = broker_port
        self._poll_interval_s = poll_interval_s
        if (context_mount_root is None) != (context_manifest_sha256 is None):
            raise ValueError("context binding must include both mount root and manifest digest")
        self._context_mount_root = context_mount_root
        self._context_manifest_sha256 = context_manifest_sha256
        self._broker_secret = secrets.token_urlsafe(32)
        self._broker_url: str | None = None
        self._broker_token: str | None = None
        self._broker_session_id: str | None = None
        self._injected_tools: set[str] = set()
        self._pending_wrappers: list[str] = []
        self._stopped = False
        # One pooled client for the whole broker lifetime: the preview proxy
        # sits behind TLS, so per-request urllib connections paid a handshake
        # on every 50 ms poll tick. Stats are per execute_with_callbacks call.
        self._client: httpx.Client | None = None
        self._poll_count = 0
        self._fulfilled_count = 0
        self.last_execution_stats: dict[str, int] = {}

    def _http(self) -> httpx.Client:
        if self._client is None:
            # Every call site runs after ensure_started set the broker URL;
            # fail loudly rather than building a client with no base URL.
            assert self._broker_url is not None
            self._client = httpx.Client(
                base_url=self._broker_url,
                headers=self._preview_headers(),
                timeout=httpx.Timeout(10.0, connect=5.0),
            )
        return self._client

    def ensure_started(self) -> None:
        if self._broker_url is not None or self._stopped:
            return
        server_code = (
            BROKER_SERVER_CODE.replace("__BROKER_SECRET__", repr(self._broker_secret))
            .replace("__BROKER_PORT__", str(self._broker_port))
            .replace("__MAX_REQUEST_BYTES__", str(_MAX_EXECUTE_REQUEST_BYTES))
            .replace("__MAX_OUTPUT_CHARS__", str(_MAX_EXECUTE_OUTPUT_CHARS))
            .replace("__CONTEXT_MOUNT_ROOT__", repr(self._context_mount_root))
            .replace("__CONTEXT_MANIFEST_SHA256__", repr(self._context_manifest_sha256))
        )
        self._sandbox.fs.upload_file(server_code.encode("utf-8"), _BROKER_SERVER_PATH)
        expected_sha = hashlib.sha256(server_code.encode("utf-8")).hexdigest()
        verify = self._sandbox.process.code_run(
            "import hashlib; print(hashlib.sha256("
            "open('/home/daytona/fleet_rlm_broker_server.py','rb').read()).hexdigest())"
        )
        actual_sha = str(getattr(verify, "result", "") or "").strip()
        if actual_sha != expected_sha:
            msg = "broker asset integrity check failed"
            raise DaytonaAdapterError(message=msg, cause_type="BrokerIntegrityError")

        session_id = f"fleet-clean-broker-{uuid.uuid4().hex[:8]}"
        self._sandbox.process.create_session(session_id)
        self._sandbox.process.execute_session_command(
            session_id,
            SessionExecuteRequest(command=_BROKER_SESSION_COMMAND, run_async=True),
        )
        # Retain the session before contacting the preview proxy so a failed
        # preview lookup can still be cleaned up by ``stop``.
        self._broker_session_id = session_id
        preview = self._get_preview_link_with_retry()
        self._broker_url = str(preview.url).rstrip("/")
        self._broker_token = str(getattr(preview, "token", "") or "")
        self._wait_health(timeout_s=60.0)

    def bind_context_manifest(self, *, trusted_mount_root: str, expected_manifest_sha256: str) -> None:
        """Bind host-authorized context before the broker process is started."""
        binding = (str(trusted_mount_root), str(expected_manifest_sha256))
        current = (self._context_mount_root, self._context_manifest_sha256)
        if current != (None, None) and current != binding:
            raise DaytonaAdapterError(
                message="context manifest binding cannot be replaced",
                cause_type="ContextIntegrityError",
            )
        if self._broker_url is not None and current != binding:
            raise DaytonaAdapterError(
                message="context manifest must be bound before broker startup",
                cause_type="ContextIntegrityError",
            )
        self._context_mount_root, self._context_manifest_sha256 = binding

    def _get_preview_link_with_retry(self) -> Any:
        last_error: DaytonaAdapterError | None = None
        attempts = 0
        for retry_delay in (0.0, *_PREVIEW_LINK_RETRY_DELAYS):
            if retry_delay:
                time.sleep(retry_delay)
            attempts += 1
            try:
                return self._sandbox.get_preview_link(self._broker_port)
            except Exception as exc:
                mapped = map_provider_error(exc)
                last_error = mapped
                if not is_transient_provider_failure(mapped):
                    break

        assert last_error is not None
        raise ProviderRequestError(
            message=sanitize_provider_message(
                f"Daytona preview link request for port {self._broker_port} failed after "
                f"{attempts} attempts: {last_error}"
            ),
            cause_type="PreviewLinkError",
            status_code=provider_status_code(last_error),
        ) from last_error

    def register_tools(self, tools: Mapping[str, Callable[..., Any]]) -> None:
        self.ensure_started()
        for name, fn in tools.items():
            if name in self._injected_tools:
                continue
            if not name.isidentifier() or keyword.iskeyword(name):
                msg = f"invalid tool name: {name}"
                raise DaytonaAdapterError(message=msg, cause_type="InvalidToolNameError")
            self._pending_wrappers.append(self._tool_wrapper_source(name, fn))
            self._injected_tools.add(name)

    def drain_wrapper_sources(self) -> str:
        wrappers = self._pending_wrappers
        self._pending_wrappers = []
        return "\n\n".join(wrappers)

    def submit_setup_code(self, output_fields: list[dict[str, Any]] | None) -> str:
        wrappers = self.drain_wrapper_sources()
        submit = remote_submit_setup_code(output_fields)
        if wrappers:
            return f"{wrappers}\n\n{submit}"
        return submit

    @staticmethod
    def _encode_value(value: Any) -> Any:
        if isinstance(value, bytes):
            return {"__fleet_type__": "bytes", "data": base64.b64encode(value).decode("ascii")}
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, (list, tuple)):
            return [DaytonaHttpToolBroker._encode_value(item) for item in value]
        if isinstance(value, Mapping):
            return {str(key): DaytonaHttpToolBroker._encode_value(item) for key, item in value.items()}
        raise DaytonaAdapterError(
            message="sandbox variable type is unsupported",
            cause_type="InterpreterVariableError",
        )

    def execute_code(
        self,
        code: str,
        variables: Mapping[str, Any] | None = None,
        *,
        timeout_s: float = 130.0,
        on_stdout: Callable[[str], None] | None = None,
    ) -> BackendExecutionResult:
        """Execute one cell and optionally forward stdout while it is produced."""
        from fleet_rlm.daytona.interpreter import BackendExecutionResult

        self.ensure_started()
        if self._stopped:
            raise DaytonaAdapterError(message="broker already stopped", cause_type="InterpreterLifecycleError")
        execution_id = uuid.uuid4().hex if on_stdout is not None else None
        payload = {
            "code": code,
            "variables": {str(key): self._encode_value(value) for key, value in (variables or {}).items()},
        }
        if execution_id is not None:
            payload["execution_id"] = execution_id
        self._http()

        response_box: list[httpx.Response] = []
        request_errors: list[BaseException] = []

        def request() -> None:
            try:
                response_box.append(self._http().post("/execute", json=payload, timeout=timeout_s))
            except BaseException as exc:
                request_errors.append(exc)

        if on_stdout is None:
            request()
        else:
            thread = Thread(target=request, daemon=True)
            thread.start()
            offset = 0
            done = False
            while thread.is_alive():
                done, offset = self._poll_output(execution_id, offset, on_stdout)
                thread.join(timeout=self._poll_interval_s)
            thread.join()
            for _ in range(20):
                done, offset = self._poll_output(execution_id, offset, on_stdout)
                if done:
                    break
                time.sleep(self._poll_interval_s)
            # Always make a final release attempt after the execution thread
            # has finished, even if transient polls never observed ``done``.
            self._poll_output(execution_id, offset, on_stdout, release=True)

        if request_errors:
            raise DaytonaAdapterError(
                message="sandbox execution request failed",
                cause_type="BrokerExecutionError",
            ) from request_errors[0]
        if not response_box:
            raise DaytonaAdapterError(
                message="sandbox execution produced no response", cause_type="BrokerExecutionError"
            )
        response = response_box[0]
        if response.status_code != 200:
            raise DaytonaAdapterError(
                message=f"sandbox execution failed with HTTP {response.status_code}",
                cause_type="BrokerExecutionError",
            )
        try:
            result = response.json()
        except ValueError as exc:
            raise DaytonaAdapterError(
                message="sandbox execution returned an invalid response",
                cause_type="BrokerExecutionError",
            ) from exc
        final = result.get("final")
        accesses = tuple(str(value) for value in result.get("context_accesses") or ())
        return BackendExecutionResult(
            stdout=str(result.get("stdout") or ""),
            stderr=str(result.get("stderr") or ""),
            final=dict(final) if isinstance(final, dict) else None,
            error=str(result.get("error") or "") or None,
            error_category=str(result.get("error_category") or "") or None,
            context_accesses=accesses,
        )

    def _poll_output(
        self,
        execution_id: str | None,
        offset: int,
        on_stdout: Callable[[str], None],
        *,
        release: bool = False,
    ) -> tuple[bool, int]:
        if execution_id is None:
            return False, offset
        try:
            response = self._http().get(
                "/output",
                params={
                    "execution_id": execution_id,
                    "offset": str(offset),
                    "release": "1" if release else "0",
                },
                timeout=5,
            )
        except (httpx.HTTPError, TimeoutError, OSError, ValueError):
            return False, offset
        if response.status_code != 200:
            return False, offset
        try:
            result = response.json()
        except ValueError:
            return False, offset
        stdout = str(result.get("stdout") or "")
        if stdout:
            on_stdout(stdout)
        try:
            next_offset = max(offset, int(result.get("next_offset", offset)))
        except (TypeError, ValueError):
            next_offset = offset + len(stdout)
        return bool(result.get("done")), next_offset

    def execute_with_callbacks(
        self,
        *,
        run_code: Callable[[], str | BackendExecutionResult],
        tool_executor: Callable[[str, list[Any], dict[str, Any]], Any],
    ) -> BackendExecutionResult:
        """
        Execute sandbox code while servicing its tool callbacks.

        Parameters:
            run_code (Callable[[], str | BackendExecutionResult]): Code execution callable.
            tool_executor (Callable[[str, list[Any], dict[str, Any]], Any]): Callback that executes a requested tool.

        Returns:
            BackendExecutionResult: The execution result, including captured output and any extracted final payload.

        Raises:
            DaytonaAdapterError: If the broker is stopped, execution fails, or produces no result.
        """
        from fleet_rlm.daytona.interpreter import BackendExecutionResult

        self.ensure_started()
        if self._stopped:
            msg = "broker already stopped"
            raise DaytonaAdapterError(message=msg, cause_type="InterpreterLifecycleError")

        bucket: list[str | BackendExecutionResult | BaseException] = []

        def _runner() -> None:
            try:
                bucket.append(run_code())
            except BaseException as exc:
                bucket.append(exc)

        poll_start = self._poll_count
        fulfilled_start = self._fulfilled_count
        thread = Thread(target=_runner, daemon=True)
        thread.start()
        while thread.is_alive():
            if self._stopped:
                break
            self._poll_once(tool_executor)
            time.sleep(self._poll_interval_s)
        thread.join(timeout=1.0)
        for _ in range(5):
            if not self._poll_once(tool_executor):
                break
            time.sleep(self._poll_interval_s)
        self.last_execution_stats = {
            "poll_count": self._poll_count - poll_start,
            "tool_call_count": self._fulfilled_count - fulfilled_start,
        }

        if not bucket:
            msg = "sandbox execution produced no result"
            raise DaytonaAdapterError(message=msg, cause_type="BrokerExecutionError")
        outcome = bucket[0]
        if isinstance(outcome, BaseException):
            if isinstance(outcome, DaytonaAdapterError):
                raise outcome
            raise DaytonaAdapterError(
                message=sanitize_provider_message(str(outcome)),
                cause_type=type(outcome).__name__,
            ) from outcome
        if isinstance(outcome, BackendExecutionResult):
            return outcome
        final = extract_final_payload(str(outcome))
        return BackendExecutionResult(stdout=str(outcome), final=final)

    def stop(self, *, strict: bool = False) -> None:
        """
        Stop the broker and release its HTTP client and Daytona session.

        Parameters:
            strict (bool): Whether to re-raise the first cleanup error after all cleanup steps complete.
        """
        self._stopped = True
        session_id = self._broker_session_id
        self._broker_session_id = None
        self._broker_url = None
        self._broker_token = None
        # Swap before closing so a concurrent late _http() caller never
        # observes the client mid-close; in-flight requests on the detached
        # client fail into the suppressed httpx error paths.
        client = self._client
        self._client = None
        # Strict mode still runs every disposal step; the first failure is
        # recorded and re-raised only after the remaining steps have run.
        first_error: BaseException | None = None
        if client is not None:
            if strict:
                try:
                    client.close()
                except BaseException as exc:
                    first_error = exc
            else:
                with contextlib.suppress(Exception):
                    client.close()
        if session_id is not None:
            if strict:
                try:
                    self._sandbox.process.delete_session(session_id)
                except BaseException as exc:
                    first_error = first_error or exc
            else:
                with contextlib.suppress(Exception):
                    self._sandbox.process.delete_session(session_id)
        if first_error is not None:
            raise first_error

    def _wait_health(self, *, timeout_s: float) -> None:
        """Wait for the broker health endpoint to become ready.

        Parameters:
            timeout_s (float): Maximum time to wait for a successful health check.

        Raises:
            DaytonaAdapterError: If authentication fails or the broker does not become healthy before the timeout.
        """
        deadline = time.monotonic() + timeout_s
        last_error = "unreachable"
        client = self._http()
        while time.monotonic() < deadline:
            try:
                resp = client.get("/health", timeout=5)
                if resp.status_code == 200:
                    return
                last_error = f"HTTP {resp.status_code}"
                # Preview-proxy auth failures are not transient; do not burn the
                # full health timeout retrying a credential/token rejection.
                if resp.status_code in {401, 403}:
                    detail = last_error
                    if not self._broker_token:
                        detail = f"{detail} (preview token missing)"
                    raise DaytonaAdapterError(
                        message=sanitize_provider_message(f"broker health check failed: {detail}"),
                        cause_type="BrokerHealthAuthError",
                    )
            except DaytonaAdapterError:
                raise
            except (httpx.HTTPError, TimeoutError, OSError) as exc:
                last_error = str(exc)
            time.sleep(0.25)
        raise DaytonaAdapterError(
            message=sanitize_provider_message(f"broker health check failed: {last_error}"),
            cause_type="BrokerHealthError",
        )

    def _poll_once(self, tool_executor: Callable[[str, list[Any], dict[str, Any]], Any]) -> bool:
        """
        Poll for pending broker requests and fulfill them concurrently.

        Parameters:
            tool_executor (Callable[[str, list[Any], dict[str, Any]], Any]): Callback that executes each requested tool.

        Returns:
            bool: `True` if pending requests were found and processed, `False` otherwise.

        Raises:
            DaytonaAdapterError: If the broker responds with a non-success, non-server-error status.
        """
        assert self._broker_url is not None
        self._poll_count += 1
        try:
            response = self._http().get("/pending", params={"max": "8"}, timeout=5)
        except (httpx.HTTPError, TimeoutError, OSError, ValueError):
            return False
        if response.status_code != 200:
            # 5xx from the preview proxy is a transient failure mode the poll
            # loops recover from, exactly like the tolerated transport errors
            # above; only non-recoverable statuses abort the execution.
            if response.status_code >= 500:
                return False
            raise DaytonaAdapterError(
                message=f"broker poll failed with HTTP {response.status_code}",
                cause_type="BrokerPollError",
            )
        try:
            payload = response.json()
        except ValueError:
            return False
        requests_out = payload.get("requests") or []
        if not requests_out:
            return False
        self._fulfilled_count += len(requests_out)
        # The broker polls on the interpreter thread, then fulfills host Tools
        # in worker threads. Copy the active Turn/MLflow context separately for
        # each request so Tool spans stay nested without sharing a Context.
        work = [(copy_context(), item) for item in requests_out]
        with ThreadPoolExecutor(max_workers=min(8, len(requests_out))) as pool:
            list(pool.map(lambda item: item[0].run(self._fulfill, item[1], tool_executor), work))
        return True

    def _fulfill(
        self,
        item: dict[str, Any],
        tool_executor: Callable[[str, list[Any], dict[str, Any]], Any],
    ) -> None:
        """
        Fulfill a pending tool request and submit its result or sanitized error to the broker.

        Parameters:
            item (dict[str, Any]): Pending tool request containing its identifier, lease token, tool name,
                arguments, and keyword arguments.
            tool_executor (Callable[[str, list[Any], dict[str, Any]], Any]): Callback that executes the requested tool.
        """
        call_id = str(item.get("id") or "")
        lease = item.get("lease_token")
        name = str(item.get("tool_name") or "")
        args = list(item.get("args") or [])
        kwargs = dict(item.get("kwargs") or {})
        try:
            result = tool_executor(name, args, kwargs)
            body: dict[str, Any] = {"id": call_id, "lease_token": lease, "result": result}
        except Exception as exc:
            message = sanitize_provider_message(str(exc))
            # One sanitized WARNING per host-tool failure; never log args,
            # kwargs, or tool content.
            logger.warning("host tool %s failed: %s", name or "<unknown>", message)
            body = {
                "id": call_id,
                "lease_token": lease,
                "error": message,
            }
        try:
            self._http().post(
                "/result",
                content=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
        except (httpx.HTTPError, TimeoutError, OSError):
            return

    def _preview_headers(self) -> dict[str, str]:
        headers = {"X-Broker-Secret": self._broker_secret}
        if self._broker_token:
            headers["X-Daytona-Preview-Token"] = self._broker_token
        return headers

    def _tool_wrapper_source(self, tool_name: str, tool_func: Callable[..., Any]) -> str:
        signature = inspect.signature(tool_func)
        params = list(signature.parameters.values())
        sig_parts: list[str] = []
        args_list: list[str] = []
        kwargs_parts: list[str] = []
        for param in params:
            if param.kind in {
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            }:
                continue
            name = param.name
            if param.default is inspect.Parameter.empty:
                sig_parts.append(name)
            else:
                sig_parts.append(f"{name}={param.default!r}")
            # Host Tools are kwargs-only callables: DSPy 3.3.x
            # ``RLM._make_interpreter_tool`` wraps every user tool in
            # ``def invoke(**kwargs)`` while spoofing this signature, so every
            # parameter must cross the wire by name. ``args`` is reserved for
            # POSITIONAL_ONLY parameters, which cannot be forwarded by name
            # (none exist on the current host tool surface).
            if param.kind == inspect.Parameter.POSITIONAL_ONLY:
                args_list.append(name)
            else:
                kwargs_parts.append(f'"{name}": {name}')
        return TOOL_WRAPPER_TEMPLATE.format(
            tool_name=tool_name,
            signature=", ".join(sig_parts),
            args_list=", ".join(args_list),
            kwargs_dict=", ".join(kwargs_parts),
            broker_port=self._broker_port,
            broker_secret=self._broker_secret,
        )
