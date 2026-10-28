"""The run_code transport: namespaces, bindings, bounded output, the host bridge, and cancellation."""

from __future__ import annotations

import ast
import json
import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from daytona.common.errors import DaytonaConnectionError

from fleet_rlm.sandbox import repl
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError


def no_calls(call: dict[str, Any]) -> dict[str, Any]:
    raise AssertionError(f"unexpected host call {call}")


def run(sandbox, context, code, *, on_call=no_calls, timeout=30, fence=None, on_fence=None, **payload):
    return repl.run(
        sandbox,
        context,
        {"code": code, **payload},
        on_call=on_call,
        timeout=timeout,
        fence=fence,
        on_fence=on_fence,
    )


def test_state_persists_and_the_last_expression_is_the_value(sandbox):
    context = repl.create_context(sandbox)
    reply_dir = Path(sandbox.code_interpreter.translate(repl.REPLY_DIR))
    assert reply_dir.is_dir() and reply_dir.stat().st_mode & 0o777 == 0o700
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


def test_skill_receipts_follow_actual_helper_calls_and_survive_later_errors(sandbox):
    context = repl.create_context(sandbox)
    created = run(
        sandbox,
        context,
        "save_skill('example', 'Use for example tasks', 'Read the reference', "
        "scripts={'check.py': 'print(17)'}, references={'notes.md': 'example reference'})",
    )
    assert created["skill_activity"] == [{"helper": "save_skill", "skill": "example", "completed": True}]
    # Aliases and keyword arguments still call the instrumented helper; source text alone is not evidence.
    used = run(
        sandbox, context, "activate = use_skill\nactivate(name='example')\nrun_skill_script('example', 'check.py')"
    )
    assert used["skill_activity"] == [
        {"helper": "use_skill", "skill": "example", "completed": True},
        {"helper": "run_skill_script", "skill": "example", "completed": True},
    ]
    failed = run(sandbox, context, "read_skill_reference('example', 'references/notes.md')\nuse_skill('missing')")
    assert failed["error"] is True
    assert failed["skill_activity"] == [
        {"helper": "read_skill_reference", "skill": "example", "completed": True},
        {"helper": "use_skill", "skill": "missing", "completed": False},
    ]
    empty = run(sandbox, context, "# use_skill('example')\n'run_skill_script'\nif False: use_skill('example')")
    assert empty["skill_activity"] == []
    assert not empty["skill_activity_truncated"]
    discovery = run(sandbox, context, "for _ in range(25): list_skills()\nuse_skill('example')")
    assert discovery["skill_activity"] == [{"helper": "use_skill", "skill": "example", "completed": True}]
    assert not discovery["skill_activity_truncated"]


def test_skill_receipts_are_bounded_and_preserved_by_submit(sandbox):
    context = repl.create_context(sandbox)
    run(sandbox, context, "save_skill('example', 'Use for examples', 'Example instructions')")
    result = run(sandbox, context, "for _ in range(25): use_skill('example')\nSUBMIT(answer='done')")
    assert result["final"] == {"answer": "done"}
    assert len(result["skill_activity"]) == 20
    assert result["skill_activity_truncated"] is True


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


def test_a_cancelled_turn_fences_a_late_successful_host_result(sandbox, caplog):
    fence = repl.ExecutionFence()
    fenced: list[bool] = []
    seen: dict[str, Any] = {}

    def on_call(call):
        seen.update(call)
        fence.close()
        return {"value": "late answer"}

    with caplog.at_level(logging.WARNING, logger="fleet_rlm.sandbox.repl"):
        result = run(
            sandbox,
            repl.create_context(sandbox),
            "lookup()",
            tools=["lookup"],
            on_call=on_call,
            host_call_timeout=0.3,
            fence=fence,
            on_fence=lambda: fenced.append(True),
        )

    assert sandbox.fs.uploads == []
    reply_path = Path(sandbox.code_interpreter.translate(f"/tmp/fleet/replies/{seen['id']}.json"))
    assert not reply_path.exists()
    assert fenced == [True]
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "Fenced a successful host tool result" in warnings[0].getMessage()
    assert "late answer" not in caplog.text
    # The fence publishes nothing, so the sandbox reports its own host-call bound.
    assert result["error"] is True


def test_a_cancelled_turn_still_delivers_cancellation_and_error_replies(sandbox, caplog):
    fence = repl.ExecutionFence()
    fence.close()

    with caplog.at_level(logging.WARNING, logger="fleet_rlm.sandbox.repl"):
        result = run(
            sandbox,
            repl.create_context(sandbox),
            "lookup()",
            tools=["lookup"],
            on_call=lambda _call: {"error": "Turn cancelled"},
            timeout=5,
            fence=fence,
        )

    assert result["error"] is True and "Turn cancelled" in result["stderr"]
    [path] = sandbox.fs.uploads
    assert path.startswith("/tmp/fleet/replies/") and path.endswith(".json")
    assert not [record for record in caplog.records if "Fenced" in record.getMessage()]


def test_cancellation_between_retry_attempts_refuses_the_successful_upload(monkeypatch, caplog):
    # A transient upload failure must not reopen publication: the retry checks the same token.
    monkeypatch.setattr(repl, "_RETRY_BASE_SECONDS", 0.0)
    fence = repl.ExecutionFence()
    attempts: list[str] = []

    class FlakyFs:
        def upload_file(self, _data, path, timeout=None):  # noqa: ARG002
            attempts.append(path)
            if len(attempts) == 1:
                fence.close()  # the originating Turn is cancelled while the first attempt is in flight
                raise DaytonaConnectionError("upload lost")

    fenced: list[bool] = []
    protocol = repl._Protocol(
        SimpleNamespace(fs=FlakyFs()),
        lambda _call: {"value": "late answer"},
        fence=fence,
        on_fence=lambda: fenced.append(True),
    )
    with caplog.at_level(logging.WARNING, logger="fleet_rlm.sandbox.repl"):
        protocol._answer({"id": "call-retry"})

    assert len(attempts) == 1, "the retry after cancellation must not attempt an upload"
    assert fenced == [True]
    refusals = [record for record in caplog.records if "Fenced" in record.getMessage()]
    assert len(refusals) == 1, "exactly one bounded warning per refused publication"
    assert "late answer" not in caplog.text


def test_an_error_reply_is_retried_and_delivered_when_the_execution_is_fenced(monkeypatch, caplog):
    monkeypatch.setattr(repl, "_RETRY_BASE_SECONDS", 0.0)
    fence = repl.ExecutionFence()
    fence.close()
    attempts: list[str] = []

    class FlakyFs:
        def __init__(self) -> None:
            self.delivered = 0

        def upload_file(self, _data, path, timeout=None):  # noqa: ARG002
            attempts.append(path)
            if len(attempts) == 1:
                raise DaytonaConnectionError("upload lost")
            self.delivered += 1

    fs = FlakyFs()
    protocol = repl._Protocol(SimpleNamespace(fs=fs), lambda _call: {"error": "Turn cancelled"}, fence=fence)
    with caplog.at_level(logging.WARNING, logger="fleet_rlm.sandbox.repl"):
        protocol._answer({"id": "call-error"})

    assert len(attempts) == 2, "an error reply goes through the retry path"
    assert fs.delivered == 1
    assert not [record for record in caplog.records if "Fenced" in record.getMessage()]


def test_a_permanently_failed_upload_never_escapes_the_worker(caplog):
    class RefusingFs:
        def upload_file(self, *_args, **_kwargs):
            raise RuntimeError("no route to the sandbox")

    protocol = repl._Protocol(
        SimpleNamespace(fs=RefusingFs()),
        lambda _call: {"value": 1},
        fence=repl.ExecutionFence(),
    )
    with caplog.at_level(logging.WARNING, logger="fleet_rlm.sandbox.repl"):
        protocol._answer({"id": "call-failed-upload"})

    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "Could not return a host tool result" in warnings[0].getMessage()


def test_an_execution_token_is_closed_once_and_never_reopened():
    fence = repl.ExecutionFence()
    assert fence.is_open()
    fence.close()
    fence.close()
    assert not fence.is_open()


def test_a_host_call_worker_converts_an_unexpected_failure_into_an_error_reply(sandbox):
    def on_call(_call):
        raise RuntimeError("bridge exploded")

    protocol = repl._Protocol(sandbox, on_call)
    protocol._answer({"id": "call-1"})

    [path] = sandbox.fs.uploads
    reply = json.loads(Path(sandbox.code_interpreter.translate(path)).read_bytes())
    assert reply == {"error": "Host tool call failed"}


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


def test_a_host_call_whose_reply_never_arrives_times_out_in_the_sandbox(sandbox, monkeypatch):
    from fleet_rlm.sandbox import repl as repl_module

    monkeypatch.setattr(repl_module, "_RETRY_BASE_SECONDS", 0.0)

    def lost_upload(*_args, **_kwargs):
        raise RuntimeError("upload failed")

    monkeypatch.setattr(sandbox.fs, "upload_file", lost_upload)
    started = time.monotonic()
    result = run(
        sandbox,
        repl.create_context(sandbox),
        "lookup()",
        tools=["lookup"],
        on_call=lambda _call: {"value": 1},
        host_call_timeout=0.3,
    )
    assert result["error"] is True and result["error_type"] == "TimeoutError"
    assert "did not reply within" in result["stderr"]
    assert time.monotonic() - started < 5


def test_the_host_call_deadline_is_bounded_by_the_execution_timeout(sandbox):
    captured: dict[str, Any] = {}
    original = sandbox.code_interpreter.run_code

    def spy(code, **kwargs):
        captured["code"] = code
        return original(code, **kwargs)

    sandbox.code_interpreter.run_code = spy
    run(sandbox, repl.create_context(sandbox), "1", timeout=7)
    assert '"host_call_timeout": 7' in captured["code"] or '"host_call_timeout":7' in captured["code"]


def _capture_execution(sandbox) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    original = sandbox.code_interpreter.run_code

    def spy(code, **kwargs):
        captured["code"] = code
        captured["kwargs"] = kwargs
        return original(code, **kwargs)

    sandbox.code_interpreter.run_code = spy
    return captured


def _executed_payload(captured: dict[str, Any]) -> dict[str, Any]:
    argument = captured["code"].split("_fleet_exec(", 1)[1].rsplit(")", 1)[0]
    return json.loads(ast.literal_eval(argument))


@pytest.mark.parametrize("remaining", [650, 240, 120])
def test_the_host_call_bound_follows_the_remaining_turn_deadline(sandbox, remaining):
    # A fixed 600 s bridge cutoff must never outrank a longer Turn deadline.
    captured = _capture_execution(sandbox)
    run(sandbox, repl.create_context(sandbox), "1", timeout=remaining)
    assert _executed_payload(captured)["host_call_timeout"] == remaining


def test_a_deadline_free_execution_sends_timeout_zero_and_no_host_call_bound(sandbox):
    captured = _capture_execution(sandbox)
    result = run(sandbox, repl.create_context(sandbox), "x = 1\nx + 1", timeout=None)
    assert result["value"] == 2
    payload = _executed_payload(captured)
    assert "host_call_timeout" in payload, "the bound must be explicit, never omitted"
    assert payload["host_call_timeout"] is None
    assert captured["kwargs"]["timeout"] == 0, "Daytona reads 0 as no execution timeout"


def test_no_fixed_host_call_cutoff_survives_in_the_transport():
    sandbox_root = Path(__file__).resolve().parents[2] / "src" / "fleet_rlm" / "sandbox"
    for relative in ("repl.py", "runtime/repl.py"):
        source = (sandbox_root / relative).read_text(encoding="utf-8")
        assert "HOST_CALL_TIMEOUT_SECONDS" not in source
        assert "or 600" not in source


def test_the_runtime_reads_zero_and_none_as_no_bound_without_a_fallback():
    # The runtime is sandbox code, not an importable host module, so exercise just its pure selector.
    source = (Path(__file__).resolve().parents[2] / "src" / "fleet_rlm" / "sandbox" / "runtime" / "repl.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    selection = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_host_call_limit"
    )
    namespace: dict[str, Any] = {}
    exec(compile(ast.Module(body=[selection], type_ignores=[]), "<runtime>", "exec"), namespace)
    limit = namespace["_host_call_limit"]

    assert limit(None) is None, "no Turn deadline means no host-call bound"
    assert limit(0) is None, "0 must not be replaced by a fixed fallback"
    assert limit(120) == 120.0, "a supplied remaining deadline is used as given"
