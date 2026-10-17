"""The run_code transport: namespaces, bindings, bounded output, the host bridge, and cancellation."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from fleet_rlm.sandbox import repl
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError


def no_calls(call: dict[str, Any]) -> dict[str, Any]:
    raise AssertionError(f"unexpected host call {call}")


def run(sandbox, context, code, *, on_call=no_calls, timeout=30, **payload):
    return repl.run(sandbox, context, {"code": code, **payload}, on_call=on_call, timeout=timeout)


def test_state_persists_and_the_last_expression_is_the_value(sandbox):
    context = repl.create_context(sandbox)
    assert run(sandbox, context, "x = 41")["has_value"] is False
    result = run(
        sandbox, context, "import json\nprint('captured')\njson.dumps({'answer': x + value})", variables={"value": 1}
    )
    assert result["stdout"] == "captured\n"
    assert result["value"] == '{"answer": 42}'


def test_namespaces_are_isolated_and_run_in_parallel(sandbox):
    left, right = repl.create_context(sandbox), repl.create_context(sandbox)
    results: dict[str, Any] = {}

    def execute(context, marker):
        results[marker] = run(sandbox, context, f"import time\ntime.sleep(0.2)\nprint({marker!r})\nmarker = {marker!r}")

    threads = [
        threading.Thread(target=execute, args=(left, "alpha")),
        threading.Thread(target=execute, args=(right, "beta")),
    ]
    started = time.monotonic()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert time.monotonic() - started < 0.4 + 1.0
    assert results["alpha"]["stdout"] == "alpha\n" and results["beta"]["stdout"] == "beta\n"
    assert run(sandbox, left, "marker")["value"] == "alpha"


def test_output_is_one_message_however_many_lines_are_printed(sandbox):
    # Daytona drops the websocket on bursts of ~2,000 writes; the runtime must coalesce.
    context = repl.create_context(sandbox)
    before = sandbox.code_interpreter.stdout_messages
    result = run(sandbox, context, "for i in range(3000):\n    print(i)")
    assert result["stdout"].split()[-1] == "2999"
    assert sandbox.code_interpreter.stdout_messages - before == 1


def test_output_is_bounded(sandbox):
    result = run(sandbox, repl.create_context(sandbox), "print('x' * 150000)")
    assert result["truncated"] is True and len(result["stdout"]) == 100_000


def test_errors_and_submit_are_reported(sandbox):
    context = repl.create_context(sandbox)
    error = run(sandbox, context, "raise ValueError('broken')")
    assert error["error"] is True and error["error_type"] == "ValueError" and "broken" in error["stderr"]
    final = run(sandbox, context, "SUBMIT(answer=42, note='done')")
    assert final["final"] == {"answer": 42, "note": "done"}


@pytest.mark.parametrize(
    "code,expected",
    [
        ("SUBMIT('found', 'note')", {"findings": "found", "learned": "note"}),
        ("SUBMIT(findings='found', learned='note')", {"findings": "found", "learned": "note"}),
        ("SUBMIT('found', learned='note')", None),
        ("SUBMIT(answer='found')", None),
        ("SUBMIT('a', 'b', 'c')", None),
    ],
)
def test_submit_takes_the_run_output_fields_positionally_or_by_keyword(sandbox, code, expected):
    result = run(sandbox, repl.create_context(sandbox), code, outputs=["findings", "learned"])
    if expected is None:
        assert result["error"] is True and result["error_type"] == "TypeError"
        assert "SUBMIT" in result["stderr"]
    else:
        assert result["final"] == expected


def test_tool_bindings_survive_variables_and_round_trip_through_the_host(sandbox):
    calls: list[dict[str, Any]] = []

    def on_call(call):
        calls.append(call)
        time.sleep(0.1)  # the sandbox must wait for a slow host
        return {"value": f"bridged {call['args'][0]}"}

    result = run(
        sandbox,
        repl.create_context(sandbox),
        "print(callable(sample_tool))\nprint(SUBMIT is not None)\n"
        "print(sample_tool.__name__)\nprint(sample_tool('arg'))",
        on_call=on_call,
        tools=["sample_tool"],
        variables={"sample_tool": "overwritten", "SUBMIT": "overwritten"},
    )
    assert result["stdout"] == "True\nTrue\nsample_tool\nbridged arg\n"
    assert [(call["tool"], call["args"]) for call in calls] == [("sample_tool", ["arg"])]


def test_parallel_host_calls_are_not_interleaved(sandbox):
    code = (
        "from concurrent.futures import ThreadPoolExecutor\n"
        "with ThreadPoolExecutor(8) as pool:\n"
        "    out = list(pool.map(lambda i: echo(i), range(8)))\n"
        "out"
    )
    result = run(
        sandbox, repl.create_context(sandbox), code, tools=["echo"], on_call=lambda call: {"value": call["args"][0]}
    )
    assert result["value"] == list(range(8))


def test_host_errors_raise_in_the_sandbox(sandbox):
    result = run(
        sandbox,
        repl.create_context(sandbox),
        "lookup()",
        tools=["lookup"],
        on_call=lambda _call: {"error": "no such row"},
    )
    assert result["error"] is True and "no such row" in result["stderr"]


def test_registered_dspy_builtin_tools_keep_their_bridge(sandbox):
    result = run(
        sandbox,
        repl.create_context(sandbox),
        "all(callable(tool) for tool in (sub_rlm, llm_query, llm_query_batched))",
        tools=["sub_rlm", "llm_query", "llm_query_batched"],
    )
    assert result["value"] is True


def test_invalid_bindings_are_refused_before_running(sandbox):
    result = run(sandbox, repl.create_context(sandbox), "1", tools=["SUBMIT"])
    assert result["error"] is True and "invalid execution bindings" in result["stderr"]


def test_namespace_imports_stay_bound_to_their_own_facade(sandbox):
    left, right = repl.create_context(sandbox), repl.create_context(sandbox)
    for context, marker in ((left, "left"), (right, "right")):
        setup = (
            f"import sys, types\ndspy = types.ModuleType('dspy')\ndspy.marker = {marker!r}\nsys.modules['dspy'] = dspy"
        )
        assert not run(sandbox, context, setup).get("error")
    assert run(sandbox, left, "import dspy\ndspy.marker")["value"] == "left"


def test_workspace_dataset_revisions_and_symlink_escape(sandbox):
    context = repl.create_context(sandbox)
    setup = run(
        sandbox,
        context,
        "workspace.write('files/data.csv', 'id,value\\n1,-7\\n2,11\\n3,5\\n')\n"
        "view = WorkspaceDatasetView(workspace.stat('files/data.csv'))\n"
        "[len(list(view.lines())), view.search('-7'), len(view.preview(4))]",
    )
    assert not setup.get("error") and setup["value"] == [4, [[2, "1,-7\n"]], 4]
    changed = run(
        sandbox,
        context,
        "metadata = workspace.stat('files/data.csv')\n"
        "workspace.write('files/data.csv', 'changed')\nWorkspaceDatasetView(metadata)",
    )
    assert changed["error"] is True and "dataset source changed" in changed["stderr"]
    escaped = run(
        sandbox,
        context,
        "import os\nos.symlink('/etc/hostname', workspace.resolve('files/link'))\nworkspace.read_text('files/link')",
    )
    assert escaped["error"] is True and "symbolic links" in escaped["stderr"]


def test_deleting_a_context_ends_an_execution_waiting_on_the_host(sandbox):
    context = repl.create_context(sandbox)
    seen = threading.Event()
    outcome: dict[str, Any] = {}

    def on_call(_call):
        seen.set()
        time.sleep(30)  # the host never answers in time
        return {"value": None}

    def execute():
        try:
            run(sandbox, context, "llm_query('pending')", on_call=on_call)
        except Exception as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=execute)
    worker.start()
    assert seen.wait(10)
    repl.delete_context(sandbox, context)
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert "worker process ended" in str(outcome["error"])


def test_a_timeout_interrupts_the_cell_and_keeps_the_context(sandbox):
    from daytona.common.errors import DaytonaTimeoutError

    context = repl.create_context(sandbox)
    run(sandbox, context, "x = 41")
    with pytest.raises(DaytonaTimeoutError):
        run(sandbox, context, "import time\nwhile True:\n    time.sleep(0.05)", timeout=1)
    assert run(sandbox, context, "x + 1")["value"] == 42


def test_a_context_that_reports_nothing_is_an_unknown_outcome(sandbox):
    context = repl.create_context(sandbox)
    sandbox.code_interpreter.run_code("def _fleet_exec(payload): pass", context=context, timeout=10)
    with pytest.raises(ExecutionOutcomeUnknownError):
        run(sandbox, context, "x = 1")


def test_a_broken_context_is_reported_as_an_error_not_a_hang(sandbox):
    context = repl.create_context(sandbox)
    sandbox.code_interpreter.run_code("del _fleet_exec", context=context, timeout=10)
    result = run(sandbox, context, "x = 1")
    assert result["error"] is True and result["error_type"] == "NameError"
