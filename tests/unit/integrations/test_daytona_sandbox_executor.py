from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from dspy.primitives import CodeInterpreterError


def test_bridge_tools_can_disable_semantic_callbacks() -> None:
    from fleet_rlm.integrations.daytona.bridge import bridge_tools

    interpreter = SimpleNamespace(
        _tools={"list_files": lambda path=".": [], "sandbox_search_files": lambda path, pattern: []},
        semantic_callbacks_enabled=False,
        sub_rlm=lambda prompt: prompt,
        llm_query=lambda prompt: prompt,
        llm_query_batched=lambda prompts: prompts,
    )

    tools = bridge_tools(interpreter)

    assert "list_files" not in tools
    assert "sandbox_search_files" not in tools
    assert "llm_query" not in tools
    assert "llm_query_batched" not in tools
    assert "sub_rlm" in tools
    assert "fetch_document_text" in tools


def test_daytona_repl_setup_keeps_native_file_helpers_fast_and_flexible(tmp_path) -> None:
    from fleet_rlm.integrations.daytona.sandbox_executor import _base_setup_code, inject_variables

    workspace = tmp_path / "workspace"
    memory = tmp_path / "memory"
    (workspace / "src").mkdir(parents=True)
    memory.mkdir()
    (workspace / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (workspace / "skills-lock.json").write_text("{}\n", encoding="utf-8")
    context_dir = workspace / ".fleet-rlm" / "context" / "ctx"
    context_dir.mkdir(parents=True)
    (workspace / ".fleet-rlm" / "context" / "manifest.json").write_text(
        '{"context_sources":[{"staged_path":".fleet-rlm/context/ctx/snapshot.md"}]}',
        encoding="utf-8",
    )

    sandbox_globals: dict[str, Any] = {}
    exec(_base_setup_code(workspace_path=str(workspace), volume_mount_path=str(memory)), sandbox_globals)
    exec(inject_variables(SimpleNamespace(), "result = list_files('src', '*.py')", {}), sandbox_globals)

    assert sandbox_globals["result"] == [str(workspace / "src" / "app.py")]
    assert sandbox_globals["sandbox_search_files"]("skills-lock.json") == [str(workspace / "skills-lock.json")]
    assert sandbox_globals["active_skills"] == {
        "selected": [],
        "catalog": {},
        "instructions": {},
        "sources": {},
    }
    assert sandbox_globals["context"]["metadata"]["sandbox_staged_paths"] == [".fleet-rlm/context/ctx/snapshot.md"]


def test_broker_start_failure_latches_and_blocks_immediate_retry() -> None:
    from fleet_rlm.integrations.daytona.sandbox_executor import (
        _BROKER_START_FAILURES,
        ExecutionCallbacks,
        run_prepared_execution,
    )

    _BROKER_START_FAILURES.clear()
    ensure_calls = 0
    session = SimpleNamespace(sandbox_id="sandbox-1")
    owner = SimpleNamespace(
        execute_timeout=30,
        timeout=30,
        _bridge_start_error=None,
        _invoke_tool=lambda name, args, kwargs: None,
    )

    def fail_ensure_bridge(**kwargs: Any) -> None:
        nonlocal ensure_calls
        _ = kwargs
        ensure_calls += 1
        raise CodeInterpreterError("Broker server failed to start within timeout")

    callbacks = ExecutionCallbacks(
        bridge_tools=lambda: {"llm_query": lambda prompt: prompt},
        reject_recursive_callbacks=lambda code: None,
        requires_bridge=lambda code, tools: True,
        ensure_bridge=fail_ensure_bridge,
        execute_direct=lambda **kwargs: pytest.fail("bridge-required code should not execute directly"),
        response_from_execution=lambda execution: pytest.fail("execution should fail before response conversion"),
    )

    with pytest.raises(CodeInterpreterError, match="Broker server failed to start"):
        run_prepared_execution(
            owner,
            session=session,
            context=object(),
            code="print(llm_query('x'))",
            callbacks=callbacks,
        )

    assert ensure_calls == 1
    assert owner._bridge_start_error == "Broker server failed to start within timeout"

    with pytest.raises(CodeInterpreterError, match="llm_query should not be retried"):
        run_prepared_execution(
            owner,
            session=session,
            context=object(),
            code="print(llm_query('again'))",
            callbacks=callbacks,
        )

    _BROKER_START_FAILURES.clear()


def test_broker_start_failure_cache_blocks_retry_across_executor_instances() -> None:
    from fleet_rlm.integrations.daytona.sandbox_executor import (
        _BROKER_START_FAILURES,
        ExecutionCallbacks,
        run_prepared_execution,
    )

    _BROKER_START_FAILURES.clear()
    ensure_calls = 0
    session = SimpleNamespace(sandbox_id="sandbox-1")
    first_owner = SimpleNamespace(
        execute_timeout=30,
        timeout=30,
        _bridge_start_error=None,
        _invoke_tool=lambda name, args, kwargs: None,
    )
    second_owner = SimpleNamespace(
        execute_timeout=30,
        timeout=30,
        _bridge_start_error=None,
        _invoke_tool=lambda name, args, kwargs: None,
    )

    def fail_ensure_bridge(**kwargs: Any) -> None:
        nonlocal ensure_calls
        _ = kwargs
        ensure_calls += 1
        raise CodeInterpreterError("Broker server failed to start within timeout")

    callbacks = ExecutionCallbacks(
        bridge_tools=lambda: {"llm_query": lambda prompt: prompt},
        reject_recursive_callbacks=lambda code: None,
        requires_bridge=lambda code, tools: True,
        ensure_bridge=fail_ensure_bridge,
        execute_direct=lambda **kwargs: pytest.fail("bridge-required code should not execute directly"),
        response_from_execution=lambda execution: pytest.fail("execution should fail before response conversion"),
    )

    with pytest.raises(CodeInterpreterError, match="Broker server failed to start"):
        run_prepared_execution(
            first_owner,
            session=session,
            context=object(),
            code="print(llm_query('x'))",
            callbacks=callbacks,
        )

    with pytest.raises(CodeInterpreterError, match="llm_query should not be retried"):
        run_prepared_execution(
            second_owner,
            session=session,
            context=object(),
            code="print(llm_query('again'))",
            callbacks=callbacks,
        )

    assert ensure_calls == 1
    _BROKER_START_FAILURES.clear()


def test_broker_start_failure_cooldown_allows_same_owner_retry() -> None:
    from fleet_rlm.integrations.daytona.sandbox_executor import (
        _BROKER_START_FAILURE_COOLDOWN_SECONDS,
        _BROKER_START_FAILURES,
        ExecutionCallbacks,
        run_prepared_execution,
    )

    class FakeBridge:
        def execute_tool_call(self, **kwargs: Any) -> dict[str, Any]:
            return {"status": "ok", "timeout": kwargs["timeout"]}

    _BROKER_START_FAILURES.clear()
    session = SimpleNamespace(sandbox_id="sandbox-1")
    owner = SimpleNamespace(
        execute_timeout=30,
        timeout=30,
        _bridge_start_error="Broker server failed to start within timeout",
        _invoke_tool=lambda name, args, kwargs: None,
    )
    _BROKER_START_FAILURES["sandbox-1"] = (
        0.0 - _BROKER_START_FAILURE_COOLDOWN_SECONDS - 1.0,
        "Broker server failed to start within timeout",
    )
    ensure_calls = 0

    def ensure_bridge(**kwargs: Any) -> FakeBridge:
        nonlocal ensure_calls
        _ = kwargs
        ensure_calls += 1
        return FakeBridge()

    callbacks = ExecutionCallbacks(
        bridge_tools=lambda: {"llm_query": lambda prompt: prompt},
        reject_recursive_callbacks=lambda code: None,
        requires_bridge=lambda code, tools: True,
        ensure_bridge=ensure_bridge,
        execute_direct=lambda **kwargs: pytest.fail("bridge-required code should not execute directly"),
        response_from_execution=lambda execution: pytest.fail("execution should not be converted here"),
    )

    result = run_prepared_execution(
        owner,
        session=session,
        context=object(),
        code="print(llm_query('again'))",
        callbacks=callbacks,
    )

    assert result == {"status": "ok", "timeout": 30}
    assert ensure_calls == 1
    assert owner._bridge_start_error is None
    assert _BROKER_START_FAILURES == {}


# --- sanitize_execution_code: sentinel-inside-string-literal regression -----


def test_sanitize_preserves_sentinels_inside_string_literals() -> None:
    """Regression: document_text containing the literal text ``[[ ## reasoning ## ]]``
    must not be truncated by the DSPy-sentinel stripper.

    ``strip_dspy_sentinel_lines`` keeps only ``line[:match.start()]`` when a
    sentinel appears on a line. When the sentinel lives INSIDE a string literal
    (e.g. a workspace snapshot whose git diff mentions ``[[ ## reasoning ## ]]``),
    that truncation drops the literal's closing quote and raises a spurious
    ``CodeSanitizationError: unterminated string literal``. The injected
    variable-assignment code is already valid Python, so ``sanitize_execution_code``
    must return it unchanged.
    """
    import ast
    import json

    from fleet_rlm.integrations.daytona.sandbox_executor import (
        inject_variables,
        sanitize_execution_code,
    )

    document_text = (
        "# qwen emits ``[[ ## reasoning ## ]]`` / ``[[ ## code ## ]]`` here\nmore content after the sentinels"
    )
    payload = json.dumps({"document_text": document_text})
    injected = inject_variables(
        None,
        "import json\ncontext = json.loads(_raw_context)",
        {"_raw_context": payload},
    )

    # The injected code is valid Python before sanitization.
    ast.parse(injected)

    out = sanitize_execution_code(injected)
    ast.parse(out)  # must still parse
    assert "context = json.loads(_raw_context)" in out
    # The sentinel text inside the string literal must survive intact.
    assert "[[ ## reasoning ## ]]" in out
    assert "[[ ## code ## ]]" in out


def test_sanitize_strips_bare_llm_sentinel_framing() -> None:
    """LLM-emitted adapter framing (sentinels as bare code, not in strings) is
    still stripped so the resulting code parses."""
    import ast

    from fleet_rlm.integrations.daytona.sandbox_executor import sanitize_execution_code

    framed = "```python\n[[ ## reasoning ## ]]\nprint(1 + 1)\n[[ ## code ## ]]\n```"
    out = sanitize_execution_code(framed)
    ast.parse(out)
    assert "print(1 + 1)" in out
    assert "[[" not in out
