"""Remote execution transport and durable workspace file contracts."""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import threading
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from typing import Any

import pytest
from daytona.common.errors import DaytonaConnectionError

from fleet_rlm.sandbox import repl
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError
from fleet_rlm.sandbox.workspace_io import WorkspaceIOMixin

# Remote execution transport


@pytest.mark.parametrize("transient", [True, False])
def test_retry_metrics_survive_exhaustion_without_recording_exception_values(monkeypatch, transient):
    attributes = {}
    delays = []
    calls = []
    span = SimpleNamespace(set_attributes=attributes.update, set_attribute=attributes.__setitem__)
    monkeypatch.setattr(repl.time, "sleep", delays.append)
    monkeypatch.setattr(repl.random, "random", lambda: 0.5)
    error = DaytonaConnectionError("private provider response") if transient else ValueError("private input")

    def fail():
        calls.append(1)
        raise error

    with pytest.raises(type(error)):
        repl.with_retries(fail, span=span)
    assert attributes == {"attempts": 3 if transient else 1, "retry_delay_seconds": 1.5 if transient else 0.0}
    assert len(calls) == (3 if transient else 1)
    assert delays == ([0.5, 1.0] if transient else [])


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


# Durable workspace files


@pytest.mark.parametrize("case", ["no_artifacts", "unchanged_input", "new_empty_file"])
def test_empty_child_promotion_preserves_success_without_parent_io(monkeypatch, case):
    events = []
    parent = DaytonaSandboxManager("key", "org", "volume", "empty-promotion", event_callback=events.append)
    child = DaytonaSandboxManager("key", "org", "volume", "empty-promotion", parent=parent)
    path = "artifacts/result.txt"
    raw = b"copied input" if case == "unchanged_input" else b""
    inputs = {path: hashlib.sha256(raw).hexdigest()} if case == "unchanged_input" else {}
    parent._child_receipts[child.agent_id] = {
        "agent_id": child.agent_id,
        "inputs": inputs,
        "artifacts": [],
        "status": "running",
    }
    published = {}

    def list_files(manager):
        if manager is child:
            return ["skills/check/SKILL.md", *([] if case == "no_artifacts" else [path])]
        assert case == "new_empty_file", "empty promotion must not inspect the parent workspace"
        return []

    def destinations(_manager, _code):
        assert case == "new_empty_file", "empty promotion must not probe parent destinations"
        return "[]"

    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", list_files)
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", lambda _self, _path: raw)
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", destinations)
    monkeypatch.setattr(DaytonaSandboxManager, "write_workspace_files", lambda _self, files: published.update(files))

    result = child.collect_child_artifacts()

    target = f"artifacts/{child.agent_id}/result.txt"
    expected = (
        [{"path": target, "sha256": hashlib.sha256(b"").hexdigest(), "agent_id": child.agent_id}]
        if case == "new_empty_file"
        else []
    )
    assert result == expected
    assert published == ({target: b""} if case == "new_empty_file" else {})
    receipt = parent.execution_evidence()["children"][0]
    assert receipt["status"] == "succeeded"
    assert receipt["artifacts"] == [{**row, "root_path": row["path"]} for row in expected]
    assert parent.execution_evidence()["children_complete"] is True
    assert events[-1]["event"] == "agent_result"
    assert events[-1]["status"] == "succeeded"
    assert events[-1]["artifacts"] == expected


def test_child_promotion_quota_preflight_performs_no_public_writes() -> None:
    writes = []
    child_files = [f"artifacts/{index:03}.txt" for index in range(101)]
    root = SimpleNamespace(
        _budget_lock=Lock(),
        _child_receipts={"child": {"inputs": {}}},
        _children={},
    )
    parent = SimpleNamespace(
        list_workspace_files=lambda: [],
        exec_remote_python=lambda _code: "[]",
        write_workspace_files=lambda files: writes.append(files),
    )
    child = SimpleNamespace(
        parent=parent,
        root=root,
        agent_id="child",
        check_turn=lambda: None,
        list_workspace_files=lambda: child_files,
        read_workspace_file=lambda _path: b"x",
        emit=lambda *_args, **_kwargs: None,
        record_child_outcome=lambda *_args: None,
    )

    with pytest.raises(ValueError, match="quota"):
        WorkspaceIOMixin.collect_child_artifacts(child)

    assert writes == []


@pytest.mark.parametrize(
    ("child_files", "content_size", "existing", "occupied", "reason"),
    [
        (["artifacts/a.txt"], 1, ["artifacts/child/a.txt"], [], "already exists"),
        (["artifacts/a.txt"], 1, [], ["artifacts/child/a.txt"], "already exists"),
        (["artifacts/a.txt", "artifacts/b.txt"], 13_000_001, [], [], "quota"),
        (["artifacts/a.txt", "artifacts/b.txt"], 12_500_001, [], [], "quota"),
    ],
)
def test_child_promotion_collision_and_byte_quota_preflight_have_zero_writes(
    child_files: list[str], content_size: int, existing: list[str], occupied: list[str], reason: str
) -> None:
    writes = []
    root = SimpleNamespace(_budget_lock=Lock(), _child_receipts={"child": {"inputs": {}}}, _children={})
    parent = SimpleNamespace(
        list_workspace_files=lambda: existing,
        exec_remote_python=lambda _code: json.dumps(occupied),
        # An occupied destination whose bytes differ is incompatible, never a completed retry.
        stat_workspace_file=lambda _path: {"sha256": hashlib.sha256(b"someone else's bytes").hexdigest()},
        write_workspace_files=lambda files: writes.append(files),
    )
    child = SimpleNamespace(
        parent=parent,
        root=root,
        agent_id="child",
        check_turn=lambda: None,
        list_workspace_files=lambda: child_files,
        read_workspace_file=lambda _path: b"x" * content_size,
        emit=lambda *_args, **_kwargs: None,
        record_child_outcome=lambda *_args: None,
    )

    with pytest.raises(ValueError, match=reason):
        WorkspaceIOMixin.collect_child_artifacts(child)

    assert writes == []


def test_child_promotion_retry_skips_an_already_published_identical_destination() -> None:
    """A destination holding the same bytes is a completed publication, so a retry is idempotent."""
    writes: list[dict[str, bytes]] = []
    raw = b"promoted"
    root = SimpleNamespace(_budget_lock=Lock(), _child_receipts={"child": {"inputs": {}}}, _children={})
    parent = SimpleNamespace(
        parent=None,
        list_workspace_files=lambda: ["artifacts/child/a.txt", "artifacts/child/b.txt"],
        exec_remote_python=lambda _code: json.dumps(["artifacts/child/a.txt", "artifacts/child/b.txt"]),
        stat_workspace_file=lambda _path: {"sha256": hashlib.sha256(raw).hexdigest()},
        write_workspace_files=lambda files: writes.append(files),
    )
    child = SimpleNamespace(
        parent=parent,
        root=root,
        agent_id="child",
        check_turn=lambda: None,
        list_workspace_files=lambda: ["artifacts/a.txt", "artifacts/b.txt"],
        read_workspace_file=lambda _path: raw,
        emit=lambda *_args, **_kwargs: None,
        record_child_outcome=lambda *_args: None,
    )

    results = WorkspaceIOMixin.collect_child_artifacts(child)

    assert writes == [{}]
    assert [row["path"] for row in results] == ["artifacts/child/a.txt", "artifacts/child/b.txt"]


def test_child_promotion_allows_exact_quota_followed_by_unchanged_input() -> None:
    writes = []
    paths = [f"artifacts/{index:03}.txt" for index in range(100)]
    unchanged = "artifacts/z-input.txt"
    root = SimpleNamespace(
        _budget_lock=Lock(),
        _child_receipts={"child": {"inputs": {unchanged: hashlib.sha256(b"x").hexdigest()}}},
        _children={},
    )
    parent = SimpleNamespace(
        parent=None,
        list_workspace_files=lambda: [],
        exec_remote_python=lambda _code: "[]",
        write_workspace_files=writes.append,
    )
    child = SimpleNamespace(
        parent=parent,
        root=root,
        agent_id="child",
        check_turn=lambda: None,
        list_workspace_files=lambda: [*paths, unchanged],
        read_workspace_file=lambda _path: b"x",
        emit=lambda *_args, **_kwargs: None,
        record_child_outcome=lambda *_args: None,
    )
    receipts = WorkspaceIOMixin.collect_child_artifacts(child)
    assert len(receipts) == len(writes[0]) == 100
    assert all(not receipt["path"].endswith("z-input.txt") for receipt in receipts)


def test_child_promotion_stops_reading_once_the_file_quota_is_exceeded() -> None:
    """A runaway child is refused without buffering its whole output on the host."""
    reads: list[str] = []
    root = SimpleNamespace(_budget_lock=Lock(), _child_receipts={"child": {"inputs": {}}}, _children={})
    parent = SimpleNamespace(
        list_workspace_files=lambda: [],
        exec_remote_python=lambda _code: "[]",
        stat_workspace_file=lambda _path: {},
        write_workspace_files=lambda _files: pytest.fail("no write may happen after a quota refusal"),
    )

    def read(path: str) -> bytes:
        reads.append(path)
        return b"x"

    child = SimpleNamespace(
        parent=parent,
        root=root,
        agent_id="child",
        check_turn=lambda: None,
        list_workspace_files=lambda: [f"artifacts/{index:03}.txt" for index in range(500)],
        read_workspace_file=read,
        emit=lambda *_args, **_kwargs: None,
        record_child_outcome=lambda *_args: None,
    )

    with pytest.raises(ValueError, match="quota"):
        WorkspaceIOMixin.collect_child_artifacts(child)

    assert len(reads) == 100


def test_failed_download_span_omits_private_exception_details(monkeypatch, tmp_path):
    import mlflow
    from mlflow.entities import SpanStatusCode
    from mlflow.entities.trace_location import MlflowExperimentLocation

    experiment_id = mlflow.create_experiment(f"private-transfer-failure-{tmp_path.name}")
    destination = MlflowExperimentLocation(experiment_id=experiment_id)
    sandbox = SimpleNamespace(fs=SimpleNamespace(download_files=lambda *_args: []))
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: sandbox)
    monkeypatch.setattr(DaytonaSandboxManager, "_workspace_files_metadata", lambda *_args, **_kwargs: [{"size": 1}])
    manager = DaytonaSandboxManager("key", "org", "volume", "private-transfer")
    with (
        mlflow.start_span("parent", trace_destination=destination) as parent,
        pytest.raises(RuntimeError, match=r"files/private-name\.txt"),
    ):
        manager.read_workspace_files(["files/private-name.txt"])
    mlflow.flush_trace_async_logging()
    trace = mlflow.get_trace(parent.trace_id)
    assert trace is not None
    span = next(span for span in trace.data.spans if span.name == "sandbox.workspace_download")
    assert span.status.status_code == SpanStatusCode.ERROR
    assert span.outputs == {"completed": False}
    assert span.attributes["attempts"] == 1
    assert span.events == []
    assert "private-name.txt" not in json.dumps(span.to_dict())


@pytest.mark.parametrize("retry_stage", [None, "metadata", "download", "prepare", "upload"])
def test_transfer_spans_separate_remote_checks_and_io_without_payloads(monkeypatch, retry_stage):
    import mlflow
    from daytona.common.errors import DaytonaConnectionError

    from fleet_rlm.sandbox import repl

    rows, stack, delays = [], [], []
    attempts = Counter()
    content = b"private payload"

    @contextmanager
    def start_span(name, span_type):
        assert span_type == "TOOL"
        row = {"name": name, "parent": stack[-1] if stack else None, "attributes": {}}
        rows.append(row)
        stack.append(name)
        try:
            yield SimpleNamespace(
                set_attributes=row["attributes"].update,
                set_attribute=lambda k, v: row["attributes"].__setitem__(k, v),
                set_inputs=lambda v: row.__setitem__("inputs", v),
                set_outputs=lambda v: row.__setitem__("outputs", v),
            )
        finally:
            stack.pop()

    def attempt(stage):
        attempts[stage] += 1
        if stage == retry_stage and attempts[stage] == 1:
            raise DaytonaConnectionError("transient")

    def execute(_command, *, timeout):
        assert timeout is None
        stage = "prepare" if stack[-1] == "sandbox.workspace_prepare" else "metadata"
        attempt(stage)
        return SimpleNamespace(exit_code=0, result=json.dumps([{"size": len(content)}]))

    def download(requests, timeout):
        assert timeout == 1800
        assert stack[-1] == "sandbox.workspace_download"
        attempt("download")
        return [SimpleNamespace(source=r.source, result=content, error=None) for r in requests]

    def upload(files, *, timeout):
        assert timeout == 1800
        assert stack[-1] == "sandbox.workspace_upload"
        assert files[0].source == content
        attempt("upload")

    sandbox = SimpleNamespace(
        process=SimpleNamespace(exec=execute),
        fs=SimpleNamespace(
            download_files=download,
            upload_files=upload,
        ),
    )
    monkeypatch.setattr(mlflow, "get_current_active_span", object)
    monkeypatch.setattr(mlflow, "start_span", start_span)
    monkeypatch.setattr(repl.time, "sleep", delays.append)
    monkeypatch.setattr(repl.random, "random", lambda: 0.5)
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: sandbox)
    manager = DaytonaSandboxManager("private-key", "org", "volume", "transfer")

    assert manager.read_workspace_files(["files/private.txt"]) == {"files/private.txt": content}
    manager.write_workspace_files({"files/private.txt": content})

    assert [row["name"] for row in rows] == [
        "sandbox.workspace_metadata",
        "sandbox.workspace_download",
        "sandbox.workspace_prepare",
        "sandbox.workspace_upload",
    ]
    for stage, row in zip(["metadata", "download", "prepare", "upload"], rows, strict=True):
        assert row["outputs"] == {"completed": True}
        assert row["attributes"]["files_requested"] == 1
        assert row["attributes"]["attempts"] == (2 if retry_stage == stage else 1)
        assert row["attributes"]["retry_delay_seconds"] == (0.5 if retry_stage == stage else 0)
    assert rows[1]["attributes"]["bytes_requested"] == len(content)
    assert rows[3]["attributes"]["bytes_requested"] == len(content)
    assert delays == ([0.5] if retry_stage else [])
    recorded = json.dumps(rows)
    assert all(secret not in recorded for secret in ["private-key", "private payload", "files/private.txt"])


@pytest.mark.parametrize("case", ["file", "empty", "missing", "symlink", "parent_symlink", "oversized", "changed"])
def test_remote_stat_checks_size_digest_and_paths_without_downloads(monkeypatch, tmp_path, case):
    import contextlib
    import hashlib
    import io
    import os

    root = tmp_path / "workspace"
    (root / "files").mkdir(parents=True)
    target = root / "files/data.txt"
    raw = "selected café\n".encode() if case != "empty" else b""
    target.write_bytes(raw)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    if case == "missing":
        target.unlink()
    elif case == "symlink":
        target.unlink()
        target.symlink_to(outside)
    elif case == "parent_symlink":
        (root / "skills").symlink_to(tmp_path, target_is_directory=True)
    elif case == "oversized":
        with target.open("wb") as handle:
            handle.truncate(25_000_001)
    calls = []
    fstats = []

    def stat(descriptor):
        actual = os.fstat(descriptor)
        fstats.append(actual)
        if case == "changed" and len(fstats) == 2:
            return SimpleNamespace(st_mtime_ns=actual.st_mtime_ns + 1, st_ctime_ns=actual.st_ctime_ns)
        return actual

    remote_os = SimpleNamespace(**{**vars(os), "fstat": stat})

    def execute(_self, _sandbox, code, **_kwargs):
        calls.append(code)
        out = io.StringIO()
        code = code.replace("import base64,hashlib,json,os", "import base64,hashlib,json")
        code = code.replace("os.path.realpath('/workspace')", f"os.path.realpath({str(root)!r})")
        with contextlib.redirect_stdout(out):
            exec(code, {"os": remote_os})
        return out.getvalue()

    manager = DaytonaSandboxManager("key", "org", "volume", "stat-test")
    # No file-transfer methods exist on this double: metadata must stay remote.
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: SimpleNamespace(fs=object()))
    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", execute)
    if case in {"file", "empty"}:
        assert manager.stat_workspace_file("files/data.txt") == {
            "path": "files/data.txt",
            "size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    elif case == "missing":
        with pytest.raises(FileNotFoundError):
            manager.stat_workspace_file("files/data.txt")
    else:
        message = "changed" if case == "changed" else "size limit" if case == "oversized" else "symbolic link"
        with pytest.raises(ValueError, match=message):
            manager.stat_workspace_file("skills/outside.txt" if case == "parent_symlink" else "files/data.txt")
    assert len(calls) == 1
    with pytest.raises(ValueError):
        manager.stat_workspace_file("../outside.txt")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "metadata",
    [
        {"size": 1, "sha256": "invalid"},
        {"size": True, "sha256": "a" * 64},
        {"size": -1, "sha256": "a" * 64},
        {"size": None},
    ],
)
def test_remote_stat_rejects_invalid_metadata(monkeypatch, metadata):
    import json

    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: object())
    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", lambda *_args, **_kwargs: json.dumps([metadata]))
    with pytest.raises(RuntimeError, match="Invalid workspace file metadata"):
        DaytonaSandboxManager("key", "org", "volume", "stat-test").stat_workspace_file("files/data.txt")


def test_daytona_path_transfer_overloads_and_25mb_provider_limits(tmp_path, monkeypatch) -> None:
    manager = DaytonaSandboxManager(api_key="key", org_id="org", volume_id="volume", session_id="session-a")
    calls: list[tuple[str, ...]] = []

    class FileSystemDouble:
        def upload_file(self, source, destination, timeout):
            calls.append(("upload", source, destination, str(timeout)))

        def download_file(self, source, destination, timeout):
            calls.append(("download", source, destination, str(timeout)))
            Path(destination).write_bytes(b"data")

    sandbox = SimpleNamespace(fs=FileSystemDouble())
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: sandbox)

    def workspace_metadata(_self, _sandbox, _path, *, max_bytes=None, **_kwargs):
        if max_bytes is not None and max_bytes < 4:
            raise ValueError("workspace file exceeds size limit")
        return {"size": 4}

    monkeypatch.setattr(
        DaytonaSandboxManager,
        "_workspace_file_metadata",
        workspace_metadata,
    )
    local_source = tmp_path / "source"
    local_source.write_bytes(b"data")
    local_destination = tmp_path / "destination"

    assert manager.write_workspace_file_from_path("files/input.bin", local_source) == "/workspace/files/input.bin"
    assert manager.read_workspace_file_to_path("files/input.bin", local_destination) == 4
    assert calls == [
        ("upload", str(local_source), "/workspace/files/input.bin", "1800"),
        ("download", "/workspace/files/input.bin", str(local_destination), "1800"),
    ]
    with local_source.open("wb") as oversized_source:
        oversized_source.truncate(25_000_001)
    with pytest.raises(ValueError, match="25 MB limit"):
        manager.write_workspace_file_from_path("files/too-large.bin", local_source)
    with pytest.raises(ValueError, match="size limit"):
        manager.read_workspace_file_to_path("files/too-large.bin", local_destination, max_bytes=3)
    assert len(calls) == 2


def test_delete_checks_the_path_remotely_before_using_the_file_api(monkeypatch) -> None:
    manager = DaytonaSandboxManager(api_key="key", org_id="org", volume_id="volume", session_id="session-a")
    calls: list[tuple[str, str]] = []
    sandbox = SimpleNamespace(fs=SimpleNamespace(delete_file=lambda path: calls.append(("delete", path))))
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: sandbox)

    def workspace_metadata(_self, _sandbox, path, **_kwargs):
        calls.append(("check", path))
        if path.endswith("link.txt"):
            raise ValueError("workspace target may not be a symbolic link")
        return {"size": 1}

    monkeypatch.setattr(DaytonaSandboxManager, "_workspace_file_metadata", workspace_metadata)
    manager.delete_workspace_file("files/a.txt")
    with pytest.raises(ValueError, match="symbolic link"):
        manager.delete_workspace_file("files/link.txt")
    with pytest.raises(ValueError):
        manager.delete_workspace_file("../escape")
    assert calls == [("check", "files/a.txt"), ("delete", "/workspace/files/a.txt"), ("check", "files/link.txt")]


@pytest.mark.parametrize("value", [-1, 25_000_001, True, 1.5, "1000"])
def test_byte_bounds_are_rejected_before_any_provider_transfer(value):
    """The shared 25 MB ceiling rejects a malformed bound without touching Daytona."""
    from fleet_rlm.sandbox.workspace_io import _checked_max_bytes
    from fleet_rlm.workspace import MAX_WORKSPACE_FILE_BYTES

    with pytest.raises(ValueError, match=f"between 0 and {MAX_WORKSPACE_FILE_BYTES}"):
        _checked_max_bytes(value)


@pytest.mark.parametrize("value", [0, 1, 1024, 25_000_000])
def test_byte_bounds_accept_every_integer_in_range(value):
    from fleet_rlm.sandbox.workspace_io import _checked_max_bytes

    assert _checked_max_bytes(value) == value
