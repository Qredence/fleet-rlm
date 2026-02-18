"""Stdio bridge server runtime for Ink/terminal frontends."""

from __future__ import annotations

import argparse
import asyncio
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any, TextIO

import dspy

from fleet_rlm import runners
from fleet_rlm.config import AppConfig
from fleet_rlm.core.config import get_planner_lm_from_env

from .handlers_chat import cancel_chat, submit_chat
from .handlers_commands import execute_command, list_commands
from .handlers_mentions import search_mentions
from .handlers_modal import (
    memory_list,
    memory_read,
    memory_write,
    sandbox_exec,
    sandbox_list,
    volume_delete,
    volume_info,
    volume_list,
    volume_read,
    volume_write,
)
from .handlers_settings import get_settings, update_settings
from .handlers_state import (
    clear_namespace,
    delete_state,
    get_state,
    list_state,
    set_state,
)
from .handlers_status import get_status
from .protocol import (
    BridgeRPCError,
    BridgeRequest,
    TokenBatcher,
    build_error,
    build_event,
    build_response,
    parse_request_line,
    write_payload,
)

ASYNC_METHODS = {"chat.submit", "chat.cancel", "commands.execute"}
DEFAULT_BRIDGE_SECRET_NAME = "LITELLM"
DEFAULT_BRIDGE_VOLUME_NAME = "rlm-volume-dspy"


def _resolve_runtime_secret_name(
    *, config: AppConfig, cli_secret_name: str | None
) -> str:
    """Resolve secret with precedence: CLI > config > default."""
    if cli_secret_name is not None:
        explicit = cli_secret_name.strip()
        if explicit:
            return explicit

    if config.interpreter.secrets:
        configured = str(config.interpreter.secrets[0]).strip()
        if configured:
            return configured

    return DEFAULT_BRIDGE_SECRET_NAME


def _resolve_runtime_volume_name(
    *, config: AppConfig, cli_volume_name: str | None
) -> str:
    """Resolve volume with precedence: CLI > config > default."""
    if cli_volume_name is not None:
        explicit = cli_volume_name.strip()
        if explicit:
            return explicit

    configured = config.interpreter.volume_name
    if configured:
        return configured

    return DEFAULT_BRIDGE_VOLUME_NAME


class BridgeRuntime:
    """Own bridge runtime state and dispatch for one stdio server process."""

    def __init__(
        self,
        *,
        config: AppConfig,
        input_stream: TextIO,
        output_stream: TextIO,
        trace_mode: str = "compact",
        docs_path: Path | None = None,
        secret_name: str = "LITELLM",
        volume_name: str | None = None,
    ) -> None:
        self.config = config
        self.input_stream = input_stream
        self.output_stream = output_stream
        self.trace_mode = trace_mode
        self.docs_path = docs_path
        self.secret_name = secret_name
        self.volume_name = volume_name

        self.session_id = "default"
        self.command_permissions: dict[str, str] = {}
        self.cancel_requested = False

        self._seq_counter = 0
        self._token_batcher = TokenBatcher()
        self._stack = ExitStack()

        self._planner_lm: Any | None = None
        self._planner_context_open = False
        self.agent: Any | None = None

    def _params_with_runtime_volume(self, params: dict[str, Any]) -> dict[str, Any]:
        merged = dict(params)
        volume_name = str(merged.get("volume_name", "")).strip()
        if not volume_name and self.volume_name:
            merged["volume_name"] = self.volume_name
        return merged

    def start(self) -> None:
        """Initialize planner context when environment/config is available."""
        self._planner_lm = get_planner_lm_from_env(model_name=self.config.agent.model)
        if self._planner_lm is not None:
            self._stack.enter_context(dspy.context(lm=self._planner_lm))
            self._planner_context_open = True

    def stop(self) -> None:
        """Release all runtime resources."""
        self._stack.close()
        self.agent = None

    def ensure_agent(self) -> None:
        """Create chat agent lazily when first required."""
        if self.agent is not None:
            return

        if self._planner_lm is None:
            self._planner_lm = get_planner_lm_from_env(
                model_name=self.config.agent.model
            )
        if self._planner_lm is None:
            raise BridgeRPCError(
                code="NOT_CONFIGURED",
                message=(
                    "Planner LM not configured. Set DSPY_LM_MODEL and "
                    "DSPY_LLM_API_KEY (or DSPY_LM_API_KEY)."
                ),
            )

        if not self._planner_context_open:
            self._stack.enter_context(dspy.context(lm=self._planner_lm))
            self._planner_context_open = True

        self.agent = self._stack.enter_context(
            runners.build_react_chat_agent(
                docs_path=self.docs_path,
                react_max_iters=self.config.rlm_settings.max_iters,
                rlm_max_iterations=self.config.agent.rlm_max_iterations,
                rlm_max_llm_calls=self.config.rlm_settings.max_llm_calls,
                max_depth=self.config.rlm_settings.max_depth,
                timeout=self.config.interpreter.timeout,
                secret_name=self.secret_name,
                volume_name=self.volume_name,
                planner_lm=self._planner_lm,
                interpreter_async_execute=self.config.interpreter.async_execute,
                guardrail_mode=self.config.agent.guardrail_mode,
                max_output_chars=self.config.rlm_settings.max_output_chars,
                min_substantive_chars=self.config.agent.min_substantive_chars,
            )
        )

    def _next_seq(self) -> int:
        self._seq_counter += 1
        return self._seq_counter

    def emit_event(self, *, method: str, params: dict[str, Any]) -> None:
        """Send one outbound event payload to frontend.

        `assistant_token` events are time-batched into `assistant_token_batch`.
        If `flush_tokens=True` is present in params, pending token batches are
        flushed *before* this event is emitted.
        """
        outgoing = dict(params)
        flush_tokens = bool(outgoing.pop("flush_tokens", False))

        if flush_tokens and self._token_batcher.has_tokens():
            batched = self._token_batcher.flush_all(self._next_seq())
            if batched is not None:
                write_payload(self.output_stream, batched)

        if method == "chat.event" and outgoing.get("kind") == "assistant_token":
            text = str(outgoing.get("text", ""))
            self._token_batcher.accumulate(text)
            if self._token_batcher.should_flush():
                batched = self._token_batcher.flush(self._next_seq())
                if batched is not None:
                    write_payload(self.output_stream, batched)
            return

        write_payload(
            self.output_stream,
            build_event(method=method, params=outgoing, seq=self._next_seq()),
        )

    def flush_token_batch(self) -> None:
        """Flush any buffered assistant tokens immediately."""
        if self._token_batcher.has_tokens():
            batched = self._token_batcher.flush_all(self._next_seq())
            if batched is not None:
                write_payload(self.output_stream, batched)

    def dispatch(self, request: BridgeRequest) -> dict[str, Any]:
        """Dispatch one synchronous request and return JSON-serializable result."""
        params = request.params

        if request.method == "session.init":
            return {
                "session_id": self.session_id,
                "trace_mode": self.trace_mode,
                "secret_name": self.secret_name,
                "volume_name": self.volume_name,
                "planner_configured": self._planner_lm is not None,
                "commands": list_commands(),
            }

        if request.method == "session.shutdown":
            return {"ok": True}

        if request.method == "session.state":
            return {
                "session_id": self.session_id,
                "trace_mode": self.trace_mode,
                "planner_configured": self._planner_lm is not None,
                "commands": list_commands(),
            }

        if request.method == "status.get":
            return get_status(self, params)

        if request.method == "settings.get":
            return get_settings(params)

        if request.method == "settings.update":
            return update_settings(params)

        if request.method == "commands.list":
            return list_commands(params)

        if request.method == "mentions.search":
            return search_mentions(params)

        if request.method == "commands.set_policy":
            cmd = str(params.get("command", "")).strip()
            if not cmd:
                raise BridgeRPCError(
                    code="INVALID_ARGS", message="`command` is required."
                )

            policy = str(params.get("policy", "allow")).strip().lower() or "allow"
            if policy not in {"allow", "deny", "ask"}:
                raise BridgeRPCError(
                    code="INVALID_ARGS",
                    message="`policy` must be one of: allow, deny, ask.",
                )

            self.command_permissions[cmd] = policy
            return {
                "command": cmd,
                "policy": policy,
                "policies": dict(sorted(self.command_permissions.items())),
            }

        if request.method == "document.load":
            docs_path = params.get("docs_path")
            if not docs_path or not isinstance(docs_path, str):
                raise BridgeRPCError(
                    code="INVALID_PARAMS",
                    message="`docs_path` must be a non-empty string.",
                )
            self.docs_path = Path(docs_path)
            return {"loaded": docs_path}

        # State persistence methods for Ink TUI stateful support
        if request.method == "state.get":
            return get_state(params)

        if request.method == "state.set":
            return set_state(params)

        if request.method == "state.delete":
            return delete_state(params)

        if request.method == "state.list":
            return list_state(params)

        if request.method == "state.clear":
            return clear_namespace(params)

        # Modal sandbox and volume access methods
        if request.method == "volume.read":
            return volume_read(params)

        if request.method == "volume.write":
            return volume_write(params)

        if request.method == "volume.list":
            return volume_list(params)

        if request.method == "volume.delete":
            return volume_delete(params)

        if request.method == "volume.info":
            return volume_info(params)

        if request.method == "memory.read":
            return memory_read(self._params_with_runtime_volume(params))

        if request.method == "memory.write":
            return memory_write(self._params_with_runtime_volume(params))

        if request.method == "memory.list":
            return memory_list(self._params_with_runtime_volume(params))

        if request.method == "sandbox.list":
            return sandbox_list(params)

        if request.method == "sandbox.exec":
            return sandbox_exec(params)

        raise BridgeRPCError(
            code="UNKNOWN_METHOD",
            message=f"Unknown method: {request.method}",
            data={"available": list_handlers()},
        )

    async def dispatch_async(self, request: BridgeRequest) -> dict[str, Any]:
        """Dispatch one async-capable request."""
        params = request.params

        if request.method == "chat.submit":
            return await submit_chat(self, params)
        if request.method == "chat.cancel":
            return cancel_chat(self, params)
        if request.method == "commands.execute":
            return await execute_command(self, params)

        # Fallback for non-async methods.
        return self.dispatch(request)


def list_handlers() -> list[str]:
    """Return all supported bridge method names."""
    return [
        "session.init",
        "session.shutdown",
        "session.state",
        "status.get",
        "settings.get",
        "settings.update",
        "mentions.search",
        "commands.list",
        "commands.execute",
        "commands.set_policy",
        "document.load",
        "chat.submit",
        "chat.cancel",
        # State persistence methods for Ink TUI stateful support
        "state.get",
        "state.set",
        "state.delete",
        "state.list",
        "state.clear",
        # Modal sandbox and volume access methods
        "volume.read",
        "volume.write",
        "volume.list",
        "volume.delete",
        "volume.info",
        "memory.read",
        "memory.write",
        "memory.list",
        "sandbox.list",
        "sandbox.exec",
    ]


def run_stdio_server(
    *,
    config: AppConfig,
    input_stream: TextIO,
    output_stream: TextIO,
    trace_mode: str = "compact",
    docs_path: Path | None = None,
    secret_name: str | None = None,
    volume_name: str | None = None,
) -> None:
    """Run stdio bridge loop, reading requests and writing responses/events."""
    resolved_secret_name = _resolve_runtime_secret_name(
        config=config, cli_secret_name=secret_name
    )
    resolved_volume_name = _resolve_runtime_volume_name(
        config=config, cli_volume_name=volume_name
    )
    runtime = BridgeRuntime(
        config=config,
        input_stream=input_stream,
        output_stream=output_stream,
        trace_mode=trace_mode,
        docs_path=docs_path,
        secret_name=resolved_secret_name,
        volume_name=resolved_volume_name,
    )
    runtime.start()

    try:
        for raw in input_stream:
            line = raw.strip()
            if not line:
                continue

            request_id = ""
            try:
                request = parse_request_line(line)
                request_id = request.request_id

                if request.method in ASYNC_METHODS:
                    result = asyncio.run(runtime.dispatch_async(request))
                else:
                    result = runtime.dispatch(request)

                write_payload(
                    output_stream,
                    build_response(request_id=request_id, result=result),
                )

                if request.method == "session.shutdown":
                    break

            except BridgeRPCError as exc:
                write_payload(
                    output_stream,
                    build_error(
                        request_id=request_id,
                        code=exc.code,
                        message=exc.message,
                        data=exc.data,
                    ),
                )

            except Exception as exc:  # pragma: no cover
                write_payload(
                    output_stream,
                    build_error(
                        request_id=request_id,
                        code="RUNTIME_ERROR",
                        message=str(exc),
                    ),
                )
    finally:
        runtime.stop()


def main(argv: list[str] | None = None) -> None:
    """CLI entrypoint for running the bridge server over stdio."""
    parser = argparse.ArgumentParser(description="Fleet RLM bridge server")
    parser.add_argument(
        "--trace-mode",
        choices=("compact", "verbose", "off"),
        default="compact",
        help="Default trace mode for chat.submit requests.",
    )
    parser.add_argument(
        "--docs-path",
        type=Path,
        default=None,
        help="Optional docs path preloaded into the bridge chat agent.",
    )
    parser.add_argument(
        "--secret-name",
        default=None,
        help="Modal secret name for LLM credentials (defaults to config or LITELLM).",
    )
    parser.add_argument(
        "--volume-name",
        default=None,
        help="Modal volume name for persistence (defaults to config or rlm-volume-dspy).",
    )

    hydra_overrides: list[str] = []
    known_argv: list[str] = []
    for token in argv or sys.argv[1:]:
        if "=" in token and not token.startswith("-"):
            hydra_overrides.append(token)
        else:
            known_argv.append(token)

    args = parser.parse_args(known_argv)

    from hydra import compose, initialize_config_module
    from omegaconf import OmegaConf

    try:
        with initialize_config_module(
            config_module="fleet_rlm.conf", version_base=None
        ):
            cfg = compose(config_name="config", overrides=hydra_overrides)
        config_dict = OmegaConf.to_container(cfg, resolve=True)
        config = AppConfig(**config_dict)  # type: ignore[arg-type]
    except Exception as exc:  # pragma: no cover
        print(f"Failed to load config: {exc}", file=sys.stderr)
        sys.exit(1)

    run_stdio_server(
        config=config,
        input_stream=sys.stdin,
        output_stream=sys.stdout,
        trace_mode=args.trace_mode,
        docs_path=args.docs_path,
        secret_name=args.secret_name,
        volume_name=args.volume_name,
    )


if __name__ == "__main__":
    main()
