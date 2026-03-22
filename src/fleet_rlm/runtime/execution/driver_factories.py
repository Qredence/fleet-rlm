"""Factory functions for sandbox driver components.

These factory functions create the protocol handlers and tool functions
used by the sandbox driver.
"""

from __future__ import annotations

import json
from typing import Any, Callable


class FinalOutput(BaseException):
    """Exception to signal final output from SUBMIT call.

    Used internally to transfer structured output from the sandboxed
    code back to the driver without using normal return mechanisms.
    """

    pass


# Reserved tool names that conflict with built-in sandbox functions
RESERVED_TOOL_NAMES = frozenset({"llm_query", "llm_query_batched", "SUBMIT", "print"})


def make_send(proto_out: Any) -> Callable[[dict], None]:
    """Create a send function for the given output stream.

    Args:
        proto_out: Output stream to write JSON messages to.

    Returns:
        Function that sends JSON objects to the output stream.
    """

    def _send(obj: dict) -> None:
        if proto_out is None:
            return
        proto_out.write(json.dumps(obj) + "\n")
        proto_out.flush()

    return _send


def make_tool_call(send: Callable[[dict], None]) -> Callable[..., Any]:
    """Create a tool_call function using the given send function.

    Args:
        send: Function to send JSON messages.

    Returns:
        Function that makes tool calls and waits for responses.
    """

    def _tool_call(name: str, *args, **kwargs) -> Any:
        send({"tool_call": {"name": name, "args": list(args), "kwargs": kwargs}})
        reply = json.loads(input())
        if reply.get("tool_error"):
            raise RuntimeError(reply["tool_error"])
        return reply.get("tool_result")

    return _tool_call


def wrap_helper(
    fn: Callable[..., Any], current_profile: list[str]
) -> Callable[..., Any]:
    """Guard helper availability by execution profile.

    Args:
        fn: Function to wrap.
        current_profile: Mutable list containing current profile name.

    Returns:
        Wrapped function that checks profile before executing.
    """
    fn_name = getattr(fn, "__name__", "unknown")

    def _wrapped(*args, **kwargs):
        if current_profile[0] == "ROOT_INTERLOCUTOR":
            raise RuntimeError(
                f"Helper '{fn_name}' is not available in ROOT_INTERLOCUTOR profile. "
                "Delegate tool-heavy work via llm_query/llm_query_batched."
            )
        return fn(*args, **kwargs)

    _wrapped.__name__ = fn_name
    _wrapped.__doc__ = getattr(fn, "__doc__", None)
    return _wrapped


def make_submit(output_names: list[str]) -> Callable[..., None]:
    """Create a SUBMIT function for the given output names.

    Args:
        output_names: Expected output names for positional arguments.

    Returns:
        SUBMIT function that raises FinalOutput with structured result.
    """

    def SUBMIT(*args, **kwargs) -> None:
        if kwargs:
            raise FinalOutput(kwargs)

        if not output_names:
            if len(args) == 1:
                raise FinalOutput({"output": args[0]})
            raise FinalOutput({"output": list(args)})

        if len(args) != len(output_names):
            raise FinalOutput(
                {
                    "error": f"SUBMIT expected {len(output_names)} positional values ({output_names}), got {len(args)}"
                }
            )

        raise FinalOutput(dict(zip(output_names, args)))

    return SUBMIT


def make_llm_query(tool_call: Callable[..., Any]) -> Callable[[str], str]:
    """Create an llm_query function.

    Args:
        tool_call: Function to make tool calls.

    Returns:
        llm_query function for single prompt queries.
    """

    def llm_query(prompt: str) -> str:
        return tool_call("llm_query", prompt)

    llm_query.__doc__ = """Query a sub-LLM for semantic analysis.

    Args:
        prompt: The prompt to send to the sub-LLM.

    Returns:
        The response text from the sub-LLM.

    Raises:
        RuntimeError: If the LLM call fails or max_llm_calls exceeded.
    """
    return llm_query


def make_llm_query_batched(
    tool_call: Callable[..., Any],
) -> Callable[[list[str]], list[str]]:
    """Create an llm_query_batched function.

    Args:
        tool_call: Function to make tool calls.

    Returns:
        llm_query_batched function for concurrent multi-prompt queries.
    """

    def llm_query_batched(prompts: list[str]) -> list[str]:
        return tool_call("llm_query_batched", prompts)

    llm_query_batched.__doc__ = """Query the sub-LLM with multiple prompts concurrently.

    Args:
        prompts: List of prompts to send to the sub-LLM.

    Returns:
        List of response texts, in the same order as prompts.

    Raises:
        RuntimeError: If any LLM call fails or max_llm_calls exceeded.
    """
    return llm_query_batched


def register_tools(
    names: list[str],
    sandbox_globals: dict[str, Any],
    dynamic_tool_names: set[str],
    tool_call: Callable[..., Any],
    current_profile: list[str],
) -> None:
    """Register tool functions in the sandbox globals.

    Creates wrapper functions for each tool name that communicate
    back to the parent process via the JSON protocol.

    Args:
        names: List of tool names to register.
        sandbox_globals: Global namespace for sandboxed code.
        dynamic_tool_names: Set to track registered dynamic tools.
        tool_call: Function to make tool calls.
        current_profile: Mutable list containing current profile name.
    """
    if current_profile[0] == "ROOT_INTERLOCUTOR":
        for dyn_name in list(dynamic_tool_names):
            sandbox_globals.pop(dyn_name, None)
        dynamic_tool_names.clear()
        return

    for name in names:
        if not name.isidentifier() or name in RESERVED_TOOL_NAMES:
            continue
        if name in sandbox_globals:
            continue

        def _make(name_: str):
            def _fn(*args, **kwargs):
                return tool_call(name_, *args, **kwargs)

            return _fn

        sandbox_globals[name] = _make(name)
        dynamic_tool_names.add(name)


def inject_sandbox_helpers(
    sandbox_globals: dict[str, Any],
    wrap_fn: Callable[[Callable], Callable],
    sandbox_tools: dict[str, Callable],
    volume_tools: dict[str, Callable],
    session_tools: dict[str, Callable],
) -> None:
    """Inject all sandbox helper functions into globals.

    Args:
        sandbox_globals: Global namespace to inject into.
        wrap_fn: Function to wrap helpers with profile guards.
        sandbox_tools: Dict of text/chunking tools.
        volume_tools: Dict of volume/workspace tools.
        session_tools: Dict of session history tools.
    """
    for name, fn in sandbox_tools.items():
        sandbox_globals[name] = wrap_fn(fn)
    for name, fn in volume_tools.items():
        sandbox_globals[name] = wrap_fn(fn)
    for name, fn in session_tools.items():
        sandbox_globals[name] = wrap_fn(fn)
