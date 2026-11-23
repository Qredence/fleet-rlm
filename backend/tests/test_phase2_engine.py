"""Phase 2 verification. Live services require FLEET_RUN_PHASE2_LIVE=1."""

import asyncio
import hashlib
import json
import os
import shutil
import sys
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
from xml.etree import ElementTree

import dspy
import pytest
from dspy.primitives.code_interpreter import (
    CodeExecutionError,
    CodeInterpreterError,
    FinalOutput,
)
from dspy.primitives.repl_types import REPLVariable
from dspy.utils.dummies import DummyLM
from pydantic import ValidationError

from fleet_rlm.agent import (
    ChildCallbacks,
    DistillMemory,
    MemoryGate,
    PythonExecStartedEvent,
    ReasoningEvent,
    RunResultEvent,
    RunStartedEvent,
    SubagentCompletedEvent,
    SubagentStartedEvent,
    bound_history,
    build_program,
    consolidate_memory,
    run_workspace_task,
)
from fleet_rlm.config import Settings
from fleet_rlm.interpreter import (
    DaytonaConnectionTimeoutError,
    DaytonaError,
    DaytonaTimeoutError,
    _is_recoverable_timeout,
    delete_sandbox,
    delete_sandbox_by_id,
    delete_workspace_file,
    download_workspace_file,
    ensure_session_sandbox,
    ephemeral_sandbox,
    list_new_artifacts,
    make_daytona_client,
    make_interpreter_factory,
    upload_workspace_file,
)
from fleet_rlm.skills import SkillRegistry, build_skill_selector, select_skills


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        database_url="postgresql://localhost/unused",
        database_direct_url="postgresql://localhost/unused",
        daytona_api_key="offline",
        daytona_volume_id="offline",
        llm_model="openai/gpt-4o-mini",
        sub_llm_model="openai/gpt-4o-mini",
        llm_api_key="offline",
        daytona_exec_timeout_seconds=20,
    )


def _skill(root, name, description="Analyze CSV data"):
    path = root / name / "SKILL.md"
    path.parent.mkdir()
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n"
        "allowed_tools: [inspect_csv, retrieve]\nmcp_servers: [catalog]\n---\n"
        "Inspect the CSV header.\n\n---\nBody after the horizontal rule.\n",
        encoding="utf-8",
    )
    return path


def test_skill_registry_and_pinned_override(settings, tmp_path, monkeypatch):
    path = _skill(tmp_path, "data-analysis")
    _skill(tmp_path, "charts")
    registry = SkillRegistry.load(tmp_path)

    def unexpected(*args, **kwargs):
        pytest.fail("Pinned skills must bypass Jev")

    monkeypatch.setattr(dspy, "LM", unexpected)
    result = select_skills(settings, registry, "Count rows", ["@data-analysis", "data-analysis"])
    (skill,) = result.active_skills
    assert skill.path == path
    assert skill.body == "Inspect the CSV header.\n\n---\nBody after the horizontal rule."
    assert skill.mcp_servers == ("catalog",)
    assert result.scores == {"data-analysis": 1.0}
    assert result.allowed_tools == ("inspect_csv", "retrieve")
    with pytest.raises(ValueError, match="Unknown pinned Skills"):
        select_skills(settings, registry, "Count rows", ["@missing"])
    path.write_text("---\nname: data-analysis\n---\n", encoding="utf-8")
    assert registry.skills[-1].description == "Analyze CSV data"
    with pytest.raises(ValueError, match="description"):
        SkillRegistry.load(tmp_path)


def test_jev_selector_shortlists_and_applies_calibration(settings, tmp_path, monkeypatch):
    _skill(tmp_path, "zz-csv")
    for index in range(41):
        _skill(tmp_path, f"skill-{index:02}", "Render an image")
    registry = SkillRegistry.load(tmp_path)
    selector = build_skill_selector(registry.skills)
    selector.fields = {
        "needs_skill": {"threshold": 0.7},
        "primary_skill": {"weights": {"skill-40": 2.0, "skill-00": 3.0}},
    }
    path = tmp_path / "selector.json"
    selector.save(path)
    settings = settings.model_copy(update={"skill_selector_path": path})

    def decide(self, *, task, lm):
        assert lm.model == "typesafe:jev-latest"
        options = self.signature.output_fields["primary_skill"].annotation.criteria()
        assert len(options) == 40 and "zz-csv" in options and "skill-40" not in options
        assert self.fields["primary_skill"]["weights"] == {"skill-00": 3.0}
        threshold = self.fields["needs_skill"]["threshold"]
        return SimpleNamespace(
            needs_skill=SimpleNamespace(value=(0.8 if "CSV" in task else 0.6) >= threshold),
            primary_skill=SimpleNamespace(
                probabilities={
                    "zz-csv": 0.37,
                    "skill-00": 0.31,
                    "skill-01": 0.24,
                    "skill-02": 0.08,
                }
            ),
        )

    monkeypatch.setattr(dspy.Predict, "forward", decide)
    result = select_skills(settings, registry, "Inspect CSV")
    assert [skill.name for skill in result.active_skills] == ["skill-00", "zz-csv"]
    assert result.scores["skill-01"] == 0.24
    assert select_skills(settings, registry, "Inspect data").active_skills == ()


def test_single_skill_selector_omits_choice_and_reloads_state(settings, tmp_path, monkeypatch):
    single_dir = tmp_path / "single"
    single_dir.mkdir()
    _skill(single_dir, "data-analysis", "Inspect tabular CSV datasets")
    registry = SkillRegistry.load(single_dir)

    selector = build_skill_selector(registry.skills)
    assert set(selector.signature.output_fields) == {"needs_skill"}
    criteria = selector.signature.output_fields["needs_skill"].annotation.criteria()
    assert "data-analysis" in criteria["true"]
    assert "Inspect tabular CSV datasets" in criteria["true"]
    assert "data-analysis" in criteria["false"]

    selector.fields = {"needs_skill": {"threshold": 0.7}}
    single_state_path = tmp_path / "single_selector.json"
    selector.save(single_state_path)

    # Also verify loading a multi-skill JSON strips primary_skill cleanly.
    multi_dir = tmp_path / "multi"
    multi_dir.mkdir()
    _skill(multi_dir, "data-analysis", "Inspect tabular CSV datasets")
    _skill(multi_dir, "charts", "Render charts")
    multi_selector = build_skill_selector(SkillRegistry.load(multi_dir).skills)
    multi_selector.fields = {
        "needs_skill": {"threshold": 0.75},
        "primary_skill": {"weights": {"data-analysis": 2.0, "charts": 1.0}},
    }
    multi_state_path = tmp_path / "multi_selector.json"
    multi_selector.save(multi_state_path)

    reloaded_from_multi = build_skill_selector(registry.skills, multi_state_path)
    assert set(reloaded_from_multi.signature.output_fields) == {"needs_skill"}
    assert reloaded_from_multi.fields == {"needs_skill": {"threshold": 0.75}}

    settings = settings.model_copy(update={"skill_selector_path": single_state_path})

    def decide(self, *, task, lm):
        assert lm.model == "typesafe:jev-latest"
        assert set(self.signature.output_fields) == {"needs_skill"}
        threshold = self.fields["needs_skill"]["threshold"]
        if task == "No probability true":
            return SimpleNamespace(needs_skill=SimpleNamespace(value=True))
        if task == "No probability false":
            return SimpleNamespace(needs_skill=SimpleNamespace(value=False))
        prob = 0.85 if "CSV" in task else 0.60
        return SimpleNamespace(
            needs_skill=SimpleNamespace(value=prob >= threshold, probability=prob)
        )

    monkeypatch.setattr(dspy.Predict, "forward", decide)
    selected = select_skills(settings, registry, "Analyze CSV totals")
    assert [skill.name for skill in selected.active_skills] == ["data-analysis"]
    assert selected.scores == {"data-analysis": 0.85}
    assert selected.allowed_tools == ("inspect_csv", "retrieve")

    skipped = select_skills(settings, registry, "Say hello")
    assert skipped.active_skills == ()
    assert skipped.scores == {"data-analysis": 0.60}
    assert skipped.allowed_tools == ()

    fallback_true = select_skills(settings, registry, "No probability true")
    assert [skill.name for skill in fallback_true.active_skills] == ["data-analysis"]
    assert fallback_true.scores == {"data-analysis": 1.0}

    fallback_false = select_skills(settings, registry, "No probability false")
    assert fallback_false.active_skills == ()
    assert fallback_false.scores == {"data-analysis": 0.0}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("value", "action", "probability", "scope", "saved_action", "approved"),
    [
        (False, "insert", 0.4, "global", "skip", False),
        (True, "skip", 0.99, "global", "skip", False),
        (True, "update", 0.899, "session", "insert", False),
        (True, "update", 0.90, "session", "update", True),
        (True, "update", 0.99, "global", "insert", True),
        (True, "insert", None, "global", "insert", False),
    ],
)
async def test_memory_gate(
    settings, tmp_path, monkeypatch, value, action, probability, scope, saved_action, approved
):
    gate = dspy.Predict(MemoryGate)
    gate.fields = {"worth_saving": {"threshold": 0.6}}
    path = tmp_path / "gate.json"
    gate.save(path)
    settings = settings.model_copy(update={"memory_gate_path": path})
    called = []

    async def predict(self, **kwargs):
        called.append(self.signature)
        assert len(kwargs["existing_memories"]) == 3
        assert len(kwargs["task"]) == 2000
        if self.signature is DistillMemory:
            assert kwargs["scope"] == scope
            return SimpleNamespace(content="The CSV uses EUR.", supersedes_id="current")
        assert self.fields["worth_saving"]["threshold"] == 0.6
        assert kwargs["lm"].model == "typesafe:jev-latest"
        return SimpleNamespace(
            worth_saving=SimpleNamespace(value=value, probability=probability),
            action=SimpleNamespace(value=action),
            scope=SimpleNamespace(value=scope),
        )

    monkeypatch.setattr(dspy.Predict, "aforward", predict)
    save = AsyncMock()
    memories = [
        {"id": item, "content": "Uses USD", "scope": "session"}
        for item in ["current", "two", "three", "four"]
    ]
    candidate = await consolidate_memory(settings, "x" * 3000, "EUR", memories, on_save=save)
    assert (candidate.action, candidate.scope, candidate.approved) == (
        saved_action,
        scope,
        approved,
    )
    assert candidate.worth_saving == value and candidate.probability == probability
    if saved_action == "skip":
        save.assert_not_awaited()
        assert len(called) == 1
    else:
        save.assert_awaited_once_with(candidate)
        assert len(called) == 2 and called[-1] is DistillMemory
        assert candidate.content == "The CSV uses EUR."
        assert candidate.supersedes_id == ("current" if saved_action == "update" else None)


@pytest.mark.asyncio
async def test_memory_gate_updates_pending_in_place_without_touching_approved(
    settings, monkeypatch
):
    seen_statuses: list[list[str]] = []

    async def predict(self, **kwargs):
        if self.signature is DistillMemory:
            target_id = kwargs["existing_memories"][0]["id"]
            return SimpleNamespace(content="Refined memory fact.", supersedes_id=target_id)
        seen_statuses.append([item["status"] for item in kwargs["existing_memories"]])
        return SimpleNamespace(
            worth_saving=SimpleNamespace(value=True, probability=0.75),
            action=SimpleNamespace(value="update"),
            scope=SimpleNamespace(value="global"),
        )

    monkeypatch.setattr(dspy.Predict, "aforward", predict)
    pending_candidate = await consolidate_memory(
        settings,
        "remember it",
        "Remembered.",
        [{"id": "pending-1", "content": "Old pending.", "scope": "global", "approved": False}],
        decision_lm=dspy.LM("typesafe:jev-latest"),
    )
    assert pending_candidate.action == "update"
    assert pending_candidate.supersedes_id == "pending-1"
    assert pending_candidate.approved is False

    approved_candidate = await consolidate_memory(
        settings,
        "remember it",
        "Remembered.",
        [{"id": "approved-1", "content": "Approved rule.", "scope": "global", "approved": True}],
        decision_lm=dspy.LM("typesafe:jev-latest"),
    )
    assert approved_candidate.action == "insert"
    assert approved_candidate.supersedes_id is None
    assert approved_candidate.approved is False
    assert seen_statuses == [["pending"], ["approved"]]


def test_bounded_history_and_optimized_state(settings, tmp_path):
    base = dspy.History(
        messages=[
            {"iteration": "1/20", "task": "old"},
            {"iteration": "2/20", "answer": "old answer"},
            {"iteration": "1/20", "task": "recent"},
            {"iteration": "2/20", "answer": "recent answer"},
        ]
    )
    recent = bound_history(base, 1, 1000)
    assert recent.messages == base.messages[-2:]
    bounded = bound_history(base, 8, len(recent.model_dump_json()) - 1)
    assert bounded.messages == base.messages[-1:]
    bounded.messages[-1]["answer"] = "changed"
    assert base.messages[-1]["answer"] == "recent answer"
    program = build_program(settings, [])
    program.generate_action.signature = program.generate_action.signature.with_instructions(
        "Optimized workspace prompt"
    )
    path = tmp_path / "optimized.json"
    program.save(path)
    restored = build_program(settings.model_copy(update={"optimized_program_path": path}), [])
    assert restored.generate_action.signature.instructions == "Optimized workspace prompt"


_GUEST = r"""
import json
import sys
import traceback

out = sys.stdout

def send(kind, data):
    out.write(json.dumps([kind, data]) + "\n")
    out.flush()

class Stream:
    def __init__(self, kind):
        self.kind = kind

    def write(self, value):
        send(self.kind, value)
        return len(value)

    def flush(self):
        pass

sys.stdout = Stream("stdout")
sys.stderr = Stream("stderr")
scope = {"__name__": "__main__"}
for request in sys.stdin:
    try:
        exec(json.loads(request), scope)
    except Exception as err:
        send("error", [type(err).__name__, str(err), traceback.format_exc()])
    send("done", None)
"""


class _LocalSandbox:
    """Exercise Daytona's output-only callback and filesystem APIs without remote services."""

    def __init__(self, cwd):
        self.cwd = cwd
        self.code_interpreter = self.fs = self.process = self
        self.contexts = []
        self.deleted = []
        self.uploads = []
        self.run_code_timeouts = []
        self.state = "started"
        self.id = "test-sandbox"
        self.start = AsyncMock()

    async def create_context(self, *, cwd, request_timeout):
        assert cwd == "/workspace"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "-c",
            _GUEST,
            cwd=self.cwd,
            limit=2**21,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        self.contexts.append(process)
        return process

    async def delete_context(self, context, *, request_timeout):
        self.deleted.append(context)
        context.kill()
        await context.wait()

    async def run_code(self, code, *, context, timeout, on_stdout=None, on_stderr=None):
        self.run_code_timeouts.append(timeout)
        context.stdin.write((json.dumps(code) + "\n").encode())
        await context.stdin.drain()
        error = None
        while line := await context.stdout.readline():
            kind, value = json.loads(line)
            if kind == "done":
                return SimpleNamespace(error=error)
            if kind == "error":
                error = SimpleNamespace(name=value[0], value=value[1], traceback=value[2])
            read = on_stdout if kind == "stdout" else on_stderr if kind == "stderr" else None
            if read:
                cut = len(value) // 2
                read(SimpleNamespace(output=value[:cut]))
                read(SimpleNamespace(output=value[cut:]))
        raise RuntimeError("Guest process ended unexpectedly")

    async def upload_file(self, data, path, *, timeout):
        self.uploads.append(path)
        Path(path).write_bytes(data)

    async def move_files(self, source, dest, *, request_timeout):
        Path(source).replace(dest)

    async def delete_file(self, path, *, recursive, request_timeout):
        shutil.rmtree(path, ignore_errors=True)

    async def code_run(self, code, *, timeout):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            code,
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        return SimpleNamespace(exit_code=process.returncode, result=stdout.decode())


@pytest.mark.asyncio
async def test_provisioning_and_reuse(settings, tmp_path):
    sandbox = _LocalSandbox(tmp_path)
    sandbox.process = SimpleNamespace(code_run=AsyncMock(return_value=SimpleNamespace(exit_code=0)))
    daytona = SimpleNamespace(
        v0=SimpleNamespace(
            create=AsyncMock(return_value=sandbox), get=AsyncMock(return_value=sandbox)
        )
    )
    session_id = uuid4()
    assert await ensure_session_sandbox(daytona, settings, session_id, None) == (
        sandbox,
        sandbox.id,
        True,
    )
    params = daytona.v0.create.call_args.args[0]
    assert params.snapshot == settings.daytona_snapshot and not params.env_vars
    (mount,) = params.volumes
    assert mount.mount_path == "/workspace" and mount.subpath == f"sessions/{session_id}"
    assert await ensure_session_sandbox(daytona, settings, session_id, sandbox.id) == (
        sandbox,
        sandbox.id,
        False,
    )
    sandbox.start.assert_not_awaited()
    sandbox.state = "stopped"
    await ensure_session_sandbox(daytona, settings, session_id, sandbox.id)
    sandbox.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_interpreter_outputs_artifacts_and_cancelled_replies(settings, tmp_path, monkeypatch):
    sandbox = _LocalSandbox(tmp_path)
    factory = make_interpreter_factory(
        sandbox, settings.model_copy(update={"rlm_max_output_chars": 8}), asyncio.get_running_loop()
    )
    interpreter = factory()
    interpreter.output_fields = [{"name": "answer"}]
    interpreter.tools["add"] = lambda x, y: x + y

    async def execute(code, values=None):
        return await asyncio.to_thread(interpreter.execute, code, values)

    try:
        assert await execute("print('ABCDEFGHIJK')") == "ABCDEFGH"
        assert await execute("result = add(x, y)\nprint(result)", {"x": 2, "y": 5}) == "7\n"
        assert await execute("SUBMIT(result)\nraise AssertionError") == FinalOutput({"answer": 7})
        with pytest.raises(CodeExecutionError) as error:
            await execute("raise ValueError('bad data')")
        assert json.loads(str(error.value))["name"] == "ValueError"

        waiting = asyncio.Event()
        cancelled = asyncio.Event()

        async def slow():
            waiting.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        interpreter.tools["slow"] = slow
        uploads = len(sandbox.uploads)
        cell = asyncio.create_task(execute("slow()"))
        await asyncio.wait_for(waiting.wait(), timeout=5)
        await interpreter.abort()
        with pytest.raises(asyncio.CancelledError):
            await cell
        assert cancelled.is_set() and len(sandbox.uploads) == uploads
    finally:
        await asyncio.to_thread(interpreter.shutdown)
    assert sandbox.contexts == sandbox.deleted

    root = tmp_path / "artifacts"
    root.mkdir()
    monkeypatch.setattr("fleet_rlm.interpreter.ARTIFACTS", str(root))
    (root / "old").touch()
    (root / "new.csv").write_bytes(b"x\n1\n")
    (root / "linked").symlink_to(root / "new.csv")
    assert await list_new_artifacts(sandbox, {str(root / "old")}) == [
        {
            "path": str(root / "new.csv"),
            "size_bytes": 4,
            "sha256": hashlib.sha256(b"x\n1\n").hexdigest(),
        }
    ]


@pytest.mark.asyncio
async def test_should_raise_recoverable_execution_error_and_reset_context_when_cell_times_out(
    settings, tmp_path, monkeypatch
):
    sandbox = _LocalSandbox(tmp_path)
    factory = make_interpreter_factory(
        sandbox,
        settings.model_copy(update={"daytona_exec_timeout_seconds": 1}),
        asyncio.get_running_loop(),
    )
    interpreter = factory()
    interpreter.output_fields = [{"name": "answer"}]

    async def execute(code, values=None):
        return await asyncio.to_thread(interpreter.execute, code, values)

    try:
        assert await execute("cached = 42\nprint(cached)") == "42\n"
        assert len(sandbox.contexts) == 1
        first_context = sandbox.contexts[0]
        assert Path(interpreter._mailbox).exists()

        with pytest.raises(
            CodeExecutionError,
            match=r"Python execution timed out after 1s; interpreter context was reset",
        ):
            await execute("import time\ntime.sleep(5)")

        assert sandbox.deleted == [first_context]
        assert not Path(interpreter._mailbox).exists()
        assert interpreter._closed is False
        assert interpreter._context is None

        assert await execute("print('cached' in globals())\nSUBMIT('recovered')") == FinalOutput(
            {"answer": "recovered"}
        )
        assert len(sandbox.contexts) == 2
        assert interpreter._context is sandbox.contexts[1]
        assert interpreter._closed is False
    finally:
        await asyncio.to_thread(interpreter.shutdown)
    assert sandbox.contexts == sandbox.deleted

    original_run_code = sandbox.run_code
    for failure in (
        DaytonaError("transport closed"),
        ExceptionGroup("transport group", [DaytonaError("frame error")]),
    ):
        broken = factory()
        try:
            await asyncio.to_thread(broken.start)
            monkeypatch.setattr(sandbox, "run_code", AsyncMock(side_effect=failure))
            with pytest.raises(
                CodeInterpreterError,
                match="Daytona execution transport failed or timed out",
            ) as exc_info:
                await asyncio.to_thread(broken.execute, "print('unreachable')")
            assert not isinstance(exc_info.value, CodeExecutionError)
            assert broken._closed is True
        finally:
            monkeypatch.setattr(sandbox, "run_code", original_run_code)
            await asyncio.to_thread(broken.shutdown)


@pytest.mark.asyncio
async def test_native_rlm_recursion_and_history_round_trip(settings, tmp_path):
    settings = settings.model_copy(update={"rlm_max_depth": 1})
    sandbox = _LocalSandbox(tmp_path)
    factory = make_interpreter_factory(sandbox, settings, asyncio.get_running_loop())
    (tmp_path / "sales.csv").write_text("value\n2\n5\n", encoding="utf-8")
    _skill(tmp_path, "csv")
    selection = select_skills(settings, SkillRegistry.load(tmp_path), "Count", ["@csv"])

    def inspect_csv() -> str:
        """Return the fixture name."""
        return "sales.csv"

    def unrelated() -> None:
        pytest.fail("An unselected tool was called")

    root_lm = DummyLM(
        [
            {
                "plan": "Delegate CSV arithmetic",
                "code": "import dspy\n"
                "sentinel = 1\n"
                "assert inspect_csv() == 'sales.csv'\n"
                "assert 'unrelated' not in globals()\n"
                "child = spawn_agent('Total sales')\n"
                "SUBMIT(child['answer'], [])",
            },
            {
                "plan": "Verify fresh context",
                "code": "assert 'sentinel' not in globals()\n"
                "assert prior_turns == [{'user': 'Delegate CSV arithmetic', 'assistant': '7'}]\n"
                "SUBMIT('Still 7', [])",
            },
        ]
    )
    sub_lm = DummyLM(
        [
            {
                "plan": "Sum the CSV",
                "code": "import csv\n"
                "import dspy\n"
                "try:\n"
                "    spawn_agent('Exceeds allowed depth')\n"
                "except RuntimeError as err:\n"
                "    assert str(err) == 'Maximum spawn_agent depth reached', str(err)\n"
                "else:\n"
                "    raise AssertionError('Depth limit was bypassed')\n"
                "with open('sales.csv') as f:\n"
                "    total = sum(int(row['value']) for row in csv.DictReader(f))\n"
                "assert 'verified' in llm_query('Check the calculation')\n"
                "SUBMIT(str(total), [])",
            },
            {"text": "verified"},
        ]
    )
    events = []

    async def emit(run_id, event):
        if isinstance(event, SubagentCompletedEvent):
            assert callbacks.on_child_finish.await_args.args[0].id == event.child_id
        events.append((run_id, event))

    persisted_id = uuid4()
    callbacks = ChildCallbacks(AsyncMock(side_effect=[persisted_id, None]), AsyncMock())
    common = dict(
        files_manifest=[{"path": "/workspace/files/sales.csv"}],
        memories=[],
        interpreter_factory=factory,
        emit=emit,
        model_override=root_lm,
        sub_lm=sub_lm,
        callbacks=callbacks,
        selection=selection,
        host_tools={"inspect_csv": inspect_csv, "unrelated": unrelated},
    )
    first = await run_workspace_task(
        settings, task="Delegate CSV arithmetic", run_id="root", **common
    )
    history = dspy.History.model_validate_json(first.history.model_dump_json())
    second = await run_workspace_task(
        settings,
        task="Recall the result",
        run_id="followup",
        history=history,
        prior_turns=[{"user": "Delegate CSV arithmetic", "assistant": "7"}],
        **common,
    )
    assert first.answer == "7" and second.answer == "Still 7"
    assert len(sandbox.contexts) == 3 and set(sandbox.contexts) == set(sandbox.deleted)
    assert len(history.messages) == 1 and len(second.history.messages) == 2
    assert "Delegate CSV arithmetic" in json.dumps(root_lm.get_convo(1)[0])
    assert callbacks.on_child_start.await_count == callbacks.on_child_finish.await_count == 2
    child, status, answer, error = callbacks.on_child_finish.await_args.args
    assert child.id == str(persisted_id)
    assert (child.depth, child.parent_id, status, answer, error) == (
        1,
        "root",
        "completed",
        "7",
        None,
    )
    nested_child = callbacks.on_child_start.await_args_list[1].args[0]
    assert (nested_child.depth, nested_child.parent_id, nested_child.kind) == (
        2,
        child.id,
        "llm_query",
    )
    assert events[0] == ("root", RunStartedEvent("workspace"))
    assert any(run_id == "root" and isinstance(event, ReasoningEvent) for run_id, event in events)
    assert any(
        run_id == child.id and isinstance(event, PythonExecStartedEvent) for run_id, event in events
    )
    assert [
        (run_id, event)
        for run_id, event in events
        if isinstance(event, (SubagentStartedEvent, SubagentCompletedEvent))
    ] == [
        ("root", SubagentStartedEvent(child)),
        (child.id, SubagentStartedEvent(nested_child)),
        (child.id, SubagentCompletedEvent(nested_child.id)),
        ("root", SubagentCompletedEvent(child.id)),
    ]
    assert (child.id, RunResultEvent("7")) in events
    assert ("root", RunResultEvent("7")) in events
    assert events[-1] == ("followup", RunResultEvent("Still 7"))


@pytest.mark.asyncio
async def test_daytona_timeout_recovery_vs_connection_timeout_fatal(
    settings, tmp_path, monkeypatch
):
    assert _is_recoverable_timeout(TimeoutError("local")) is True
    assert _is_recoverable_timeout(DaytonaTimeoutError("cell timed out")) is True
    assert _is_recoverable_timeout(DaytonaConnectionTimeoutError("connect timed out")) is False
    assert _is_recoverable_timeout(DaytonaError("generic")) is False
    assert _is_recoverable_timeout(
        ExceptionGroup("ok", [TimeoutError(), DaytonaTimeoutError("cell")])
    )
    assert not _is_recoverable_timeout(
        ExceptionGroup("mixed", [TimeoutError(), DaytonaConnectionTimeoutError("net")])
    )
    assert not _is_recoverable_timeout(ExceptionGroup("mixed", [TimeoutError(), DaytonaError("x")]))

    sandbox = _LocalSandbox(tmp_path)
    factory = make_interpreter_factory(
        sandbox,
        settings.model_copy(update={"daytona_exec_timeout_seconds": 5}),
        asyncio.get_running_loop(),
    )
    interpreter = factory()
    interpreter.output_fields = [{"name": "answer"}]
    original_run_code = sandbox.run_code

    try:
        assert await asyncio.to_thread(interpreter.execute, "x = 1\nprint(x)") == "1\n"
        first_context = sandbox.contexts[0]

        # Remote DaytonaTimeoutError resets context, keeps interpreter open, and recovers.
        monkeypatch.setattr(
            sandbox, "run_code", AsyncMock(side_effect=DaytonaTimeoutError("daemon timeout"))
        )
        with pytest.raises(
            CodeExecutionError,
            match=r"Python execution timed out after 5s; interpreter context was reset",
        ):
            await asyncio.to_thread(interpreter.execute, "print('slow')")
        assert sandbox.deleted == [first_context]
        assert interpreter._closed is False
        assert interpreter._context is None

        monkeypatch.setattr(sandbox, "run_code", original_run_code)
        assert await asyncio.to_thread(
            interpreter.execute, "assert 'x' not in globals()\nSUBMIT('after-daytona-timeout')"
        ) == FinalOutput({"answer": "after-daytona-timeout"})
        assert len(sandbox.contexts) == 2
        assert interpreter._closed is False

        # If cleanup fails during recoverable timeout handling, mark fatal CodeInterpreterError.
        second_context = sandbox.contexts[1]
        monkeypatch.setattr(
            sandbox, "run_code", AsyncMock(side_effect=DaytonaTimeoutError("daemon timeout"))
        )
        original_delete = sandbox.delete_context
        failing_delete = AsyncMock(side_effect=DaytonaError("cleanup lost"))
        monkeypatch.setattr(sandbox, "delete_context", failing_delete)
        try:
            with pytest.raises(
                CodeInterpreterError, match="Daytona execution transport failed or timed out"
            ) as cleanup_exc:
                await asyncio.to_thread(interpreter.execute, "print('cleanup-fail')")
            assert not isinstance(cleanup_exc.value, CodeExecutionError)
            assert interpreter._closed is True
            assert failing_delete.await_args.args[0] is second_context
        finally:
            monkeypatch.setattr(sandbox, "delete_context", original_delete)
            monkeypatch.setattr(sandbox, "run_code", original_run_code)
            await original_delete(second_context, request_timeout=5)
    finally:
        await asyncio.to_thread(interpreter.shutdown)
    assert set(sandbox.contexts) == set(sandbox.deleted)

    # DaytonaConnectionTimeoutError is a transport failure: closes interpreter and raises fatal.
    for fatal in (
        DaytonaConnectionTimeoutError("socket connect timed out"),
        ExceptionGroup(
            "conn group",
            [TimeoutError("cell"), DaytonaConnectionTimeoutError("transport timeout")],
        ),
    ):
        broken = factory()
        try:
            await asyncio.to_thread(broken.start)
            monkeypatch.setattr(sandbox, "run_code", AsyncMock(side_effect=fatal))
            with pytest.raises(
                CodeInterpreterError, match="Daytona execution transport failed or timed out"
            ) as exc_info:
                await asyncio.to_thread(broken.execute, "print('unreachable')")
            assert not isinstance(exc_info.value, CodeExecutionError)
            assert broken._closed is True
        finally:
            monkeypatch.setattr(sandbox, "run_code", original_run_code)
            await asyncio.to_thread(broken.shutdown)


@pytest.mark.asyncio
async def test_cell_timer_pauses_during_slow_host_rpc_and_times_out_pure_python(settings, tmp_path):
    sandbox = _LocalSandbox(tmp_path)
    short_settings = settings.model_copy(update={"daytona_exec_timeout_seconds": 1})
    factory = make_interpreter_factory(sandbox, short_settings, asyncio.get_running_loop())
    interpreter = factory()
    interpreter.output_fields = [{"name": "answer"}]

    async def slow_rpc() -> str:
        await asyncio.sleep(1.4)
        return "rpc-complete"

    interpreter.tools["slow_rpc"] = slow_rpc

    async def execute(code: str) -> str | FinalOutput:
        return await asyncio.to_thread(interpreter.execute, code)

    try:
        # Host RPC takes longer than daytona_exec_timeout_seconds (1s), yet succeeds because
        # the active-time watchdog pauses while tasks are in flight and remote_timeout >= 600.
        result = await execute("res = slow_rpc()\nprint(res)")
        assert result == "rpc-complete\n"
        assert sandbox.run_code_timeouts[-1] == 600

        # Pure Python sleep still trips the 1s active-time watchdog and resets context cleanly.
        with pytest.raises(
            CodeExecutionError,
            match=r"Python execution timed out after 1s; interpreter context was reset",
        ):
            await execute("import time\ntime.sleep(3)")
        assert interpreter._closed is False
        assert interpreter._context is None
        assert await execute("SUBMIT('alive')") == FinalOutput({"answer": "alive"})
    finally:
        await asyncio.to_thread(interpreter.shutdown)
    assert set(sandbox.contexts) == set(sandbox.deleted)

    # Also verify a multi-step spawn_agent tree whose total wall time exceeds the 1s parent
    # cell timeout completes cleanly while each child cell stays under 1s.
    root_lm = DummyLM(
        [
            {
                "plan": "Delegate to multi-step child",
                "code": "child = spawn_agent('run two steps')\nSUBMIT(child['answer'], [])",
            }
        ]
    )
    sub_lm = DummyLM(
        [
            {"plan": "First half", "code": "import time\ntime.sleep(0.6)\nprint('step-1')"},
            {
                "plan": "Second half",
                "code": "import time\ntime.sleep(0.6)\nSUBMIT('nested-ok', [])",
            },
        ]
    )

    async def emit(_run_id, _event):
        return None

    nested = await run_workspace_task(
        short_settings.model_copy(update={"rlm_max_depth": 1}),
        task="Delegate slow child",
        files_manifest=[],
        memories=[],
        interpreter_factory=factory,
        run_id="root-pause",
        emit=emit,
        model_override=root_lm,
        sub_lm=sub_lm,
    )
    assert nested.answer == "nested-ok"
    assert set(sandbox.contexts) == set(sandbox.deleted)


@pytest.mark.asyncio
async def test_host_tool_code_execution_error_forwarded_and_other_errors_masked(settings, tmp_path):
    sandbox = _LocalSandbox(tmp_path)
    factory = make_interpreter_factory(sandbox, settings, asyncio.get_running_loop())
    interpreter = factory()

    def hit_depth_limit() -> None:
        raise CodeExecutionError("Maximum spawn_agent depth reached")

    def long_safe_error() -> None:
        raise CodeExecutionError("A" * 700)

    def leaky_failure() -> None:
        raise ValueError("secret_api_key=sk-live-99999")

    interpreter.tools["hit_depth_limit"] = hit_depth_limit
    interpreter.tools["long_safe_error"] = long_safe_error
    interpreter.tools["leaky_failure"] = leaky_failure

    async def execute(code: str) -> str | FinalOutput:
        return await asyncio.to_thread(interpreter.execute, code)

    try:
        out = await execute(
            "msgs = []\n"
            "for fn in (hit_depth_limit, long_safe_error, leaky_failure):\n"
            "    try:\n"
            "        fn()\n"
            "    except RuntimeError as exc:\n"
            "        msgs.append(str(exc))\n"
            "import json\n"
            "print(json.dumps(msgs))"
        )
        assert isinstance(out, str)
        messages = json.loads(out)
        assert messages[0] == "Maximum spawn_agent depth reached"
        assert messages[1] == "A" * 512
        assert messages[2] == "Host tool 'leaky_failure' failed (ValueError)"
        assert "sk-live-99999" not in messages[2]
    finally:
        await asyncio.to_thread(interpreter.shutdown)


@pytest.mark.asyncio
async def test_list_new_artifacts_detects_overwrites_and_preserves_collection_compat(
    tmp_path, monkeypatch
):
    sandbox = _LocalSandbox(tmp_path)
    root = tmp_path / "artifacts"
    root.mkdir()
    monkeypatch.setattr("fleet_rlm.interpreter.WORKSPACE", str(tmp_path))
    monkeypatch.setattr("fleet_rlm.interpreter.ARTIFACTS", str(root))

    unchanged = root / "unchanged.csv"
    unchanged.write_bytes(b"a,b\n1,2\n")
    overwritten = root / "overwritten.csv"
    overwritten.write_bytes(b"v1")
    same_size = root / "same_size.txt"
    same_size.write_bytes(b"aaaa")
    (root / "linked").symlink_to(unchanged)

    known_mapping = {
        "artifacts/unchanged.csv": (
            hashlib.sha256(b"a,b\n1,2\n").hexdigest(),
            len(b"a,b\n1,2\n"),
        ),
        "artifacts/overwritten.csv": (hashlib.sha256(b"v1").hexdigest(), 2),
        str(same_size): (hashlib.sha256(b"aaaa").hexdigest(), 4),
    }

    assert await list_new_artifacts(sandbox, known_mapping) == []

    # Mutate two existing artifacts and create one brand-new artifact.
    overwritten.write_bytes(b"version-2-longer")
    same_size.write_bytes(b"bbbb")
    brand_new = root / "fresh.json"
    brand_new.write_bytes(b"{}")

    changed = await list_new_artifacts(sandbox, known_mapping)
    assert changed == [
        {
            "path": str(brand_new),
            "sha256": hashlib.sha256(b"{}").hexdigest(),
            "size_bytes": 2,
        },
        {
            "path": str(overwritten),
            "sha256": hashlib.sha256(b"version-2-longer").hexdigest(),
            "size_bytes": len(b"version-2-longer"),
        },
        {
            "path": str(same_size),
            "sha256": hashlib.sha256(b"bbbb").hexdigest(),
            "size_bytes": 4,
        },
    ]

    # Legacy Collection[str] interface skips known paths without hashing them.
    legacy = await list_new_artifacts(
        sandbox,
        {"artifacts/unchanged.csv", "artifacts/overwritten.csv", str(same_size)},
    )
    assert legacy == [
        {
            "path": str(brand_new),
            "sha256": hashlib.sha256(b"{}").hexdigest(),
            "size_bytes": 2,
        }
    ]


@pytest.mark.asyncio
async def test_ephemeral_sandbox_creates_without_volumes_and_always_deletes(settings, tmp_path):
    sandbox = _LocalSandbox(tmp_path)
    sandbox.process = SimpleNamespace(code_run=AsyncMock(return_value=SimpleNamespace(exit_code=0)))
    daytona = SimpleNamespace(
        v0=SimpleNamespace(
            create=AsyncMock(return_value=sandbox),
            delete=AsyncMock(),
        )
    )
    configured = settings.model_copy(
        update={
            "daytona_snapshot": "custom-snap",
            "daytona_target": "us",
            "daytona_auto_stop_minutes": 7,
        }
    )

    async with ephemeral_sandbox(daytona, configured) as yielded:
        assert yielded is sandbox
        params = daytona.v0.create.call_args.args[0]
        assert params.snapshot == "custom-snap"
        assert params.language == "python"
        assert params.target == "us"
        assert params.auto_stop_interval == 7
        assert params.volumes is None
        assert params.env_vars is None
        daytona.v0.delete.assert_not_awaited()
    daytona.v0.delete.assert_awaited_once_with(sandbox, wait=True)

    # Also cleans up when the caller raises inside the context block.
    daytona.v0.delete.reset_mock()
    with pytest.raises(RuntimeError, match="eval crashed"):
        async with ephemeral_sandbox(daytona, configured):
            raise RuntimeError("eval crashed")
    daytona.v0.delete.assert_awaited_once_with(sandbox, wait=True)

    # Also cleans up if workspace directory init fails.
    daytona.v0.delete.reset_mock()
    sandbox.process = SimpleNamespace(code_run=AsyncMock(return_value=SimpleNamespace(exit_code=1)))
    with pytest.raises(RuntimeError, match="ephemeral Daytona workspace"):
        async with ephemeral_sandbox(daytona, configured):
            pytest.fail("must not yield when init fails")
    daytona.v0.delete.assert_awaited_once_with(sandbox, wait=True)


@pytest.mark.asyncio
async def test_daytona_v0_and_workspace_file_helpers(settings):
    client = make_daytona_client(settings)
    assert hasattr(client, "v0")
    await client.close()

    sandbox = SimpleNamespace(
        id="sb-1",
        fs=SimpleNamespace(
            upload_file_stream=AsyncMock(),
            move_files=AsyncMock(),
            delete_file=AsyncMock(),
            download_file=AsyncMock(return_value=b"payload"),
        ),
    )
    daytona = SimpleNamespace(
        v0=SimpleNamespace(
            get=AsyncMock(return_value=sandbox),
            delete=AsyncMock(),
        )
    )

    await delete_sandbox(daytona, sandbox)
    daytona.v0.delete.assert_awaited_once_with(sandbox, wait=True)

    daytona.v0.delete.reset_mock()
    await delete_sandbox_by_id(daytona, "sb-1")
    daytona.v0.get.assert_awaited_once_with("sb-1")
    daytona.v0.delete.assert_awaited_once_with(sandbox, wait=True)

    async def sample_chunks():
        yield b"abc"

    await upload_workspace_file(sandbox, sample_chunks(), "files/a.csv", timeout=30)
    temp_path = sandbox.fs.upload_file_stream.await_args.args[1]
    assert temp_path.startswith("/workspace/files/.") and temp_path.endswith(".upload")
    assert sandbox.fs.upload_file_stream.await_args.kwargs == {"timeout": 30}
    sandbox.fs.move_files.assert_awaited_once_with(
        temp_path, "/workspace/files/a.csv", request_timeout=30
    )

    # Failed move cleans up the temporary upload path before re-raising.
    sandbox.fs.move_files.side_effect = DaytonaError("move failed")
    with pytest.raises(DaytonaError, match="move failed"):
        await upload_workspace_file(sandbox, sample_chunks(), "files/b.csv", timeout=15)
    failed_temp = sandbox.fs.upload_file_stream.await_args.args[1]
    sandbox.fs.delete_file.assert_awaited_once_with(failed_temp, request_timeout=15)

    sandbox.fs.delete_file.reset_mock()
    await delete_workspace_file(sandbox, "files/b.csv", timeout=12)
    sandbox.fs.delete_file.assert_awaited_once_with("/workspace/files/b.csv", request_timeout=12)

    assert await download_workspace_file(sandbox, "artifacts/out.txt", timeout=20) == b"payload"
    sandbox.fs.download_file.assert_awaited_once_with("/workspace/artifacts/out.txt", 20)


@pytest.mark.asyncio
async def test_decision_lm_reuse_and_calibrated_primary_fallback(settings, tmp_path, monkeypatch):
    _skill(tmp_path, "rare-skill", "Handle specialized actuarial tables")
    _skill(tmp_path, "general-csv", "Analyze tabular CSV files")
    registry = SkillRegistry.load(tmp_path)

    def fail_lm(*args, **kwargs):
        pytest.fail("dspy.LM must not be instantiated when a decision LM is already available")

    monkeypatch.setattr(dspy, "LM", fail_lm)
    provided = DummyLM([{}])
    provided.model = settings.decision_model
    seen_lms = []

    def decide_skills(self, *, task, lm):
        seen_lms.append(lm)
        if task == "calibrated-low-prob":
            return SimpleNamespace(
                needs_skill=SimpleNamespace(value=True),
                primary_skill=SimpleNamespace(
                    value="rare-skill",
                    probabilities={"rare-skill": 0.12, "general-csv": 0.18},
                ),
            )
        return SimpleNamespace(
            needs_skill=SimpleNamespace(value=False),
            primary_skill=SimpleNamespace(
                value="rare-skill",
                probabilities={"rare-skill": 0.12, "general-csv": 0.18},
            ),
        )

    monkeypatch.setattr(dspy.Predict, "forward", decide_skills)

    # Explicit decision_lm + calibrated primary_skill fallback when ranked is empty.
    selected = select_skills(
        settings, registry, "calibrated-low-prob", threshold=0.25, decision_lm=provided
    )
    assert [skill.name for skill in selected.active_skills] == ["rare-skill"]
    assert selected.scores["rare-skill"] == 0.12
    assert seen_lms[-1] is provided

    # Context LM matching settings.decision_model is also reused without calling dspy.LM().
    with dspy.context(lm=provided):
        skipped = select_skills(settings, registry, "no-skill-needed", threshold=0.25)
    assert skipped.active_skills == ()
    assert seen_lms[-1] is provided

    # consolidate_memory reuses explicit decision_lm and matching context LM without dspy.LM().
    memory_lms = []

    async def decide_memory(self, **kwargs):
        memory_lms.append(kwargs["lm"])
        return SimpleNamespace(
            worth_saving=SimpleNamespace(value=False, probability=0.1),
            action=SimpleNamespace(value="skip"),
            scope=SimpleNamespace(value="session"),
        )

    monkeypatch.setattr(dspy.Predict, "aforward", decide_memory)
    first_mem = await consolidate_memory(settings, "task", "answer", [], decision_lm=provided)
    assert first_mem.action == "skip"
    assert memory_lms[-1] is provided

    with dspy.context(lm=provided):
        second_mem = await consolidate_memory(settings, "task", "answer", [])
    assert second_mem.action == "skip"
    assert memory_lms[-1] is provided


@pytest.mark.asyncio
async def test_preloaded_optimized_state_skips_disk_reads_and_clears_predictor_lms(
    settings, tmp_path
):
    base_program = build_program(settings, [])
    base_program.generate_action.signature = (
        base_program.generate_action.signature.with_instructions(
            "Preloaded optimized workspace prompt"
        )
    )
    path = tmp_path / "optimized.json"
    base_program.save(path)
    state = json.loads(path.read_text(encoding="utf-8"))
    state_snapshot = json.dumps(state, sort_keys=True)
    # Remove file on disk to prove build_program & run_workspace_task never read from disk.
    path.unlink()
    configured = settings.model_copy(update={"optimized_program_path": path})

    restored = build_program(configured, [], optimized_state=state)
    assert restored.generate_action.signature.instructions == "Preloaded optimized workspace prompt"
    assert all(predictor.lm is None for predictor in restored.predictors())
    assert json.dumps(state, sort_keys=True) == state_snapshot

    sandbox = _LocalSandbox(tmp_path)
    factory = make_interpreter_factory(sandbox, configured, asyncio.get_running_loop())
    root_lm = DummyLM([{"plan": "Finish", "code": "SUBMIT('ok', [])"}])
    sub_lm = DummyLM([{"text": "unused"}])

    async def emit(_run_id, _event):
        return None

    prediction = await run_workspace_task(
        configured,
        task="Say ok",
        files_manifest=[],
        memories=[],
        interpreter_factory=factory,
        run_id="root",
        emit=emit,
        model_override=root_lm,
        sub_lm=sub_lm,
        optimized_state=state,
    )
    assert prediction.answer == "ok"
    assert "Preloaded optimized workspace prompt" in json.dumps(root_lm.get_convo(0)[0])


@pytest.mark.skipif(
    os.environ.get("FLEET_RUN_PHASE2_LIVE") != "1", reason="Requires authorized live services"
)
@pytest.mark.asyncio
async def test_live_two_turn_csv(tmp_path):
    """Run on an isolated Daytona Volume subpath and inspect a local MLflow trace."""
    import mlflow
    import mlflow.dspy

    class LiveSettings(Settings):
        database_url: str = "postgresql://localhost/unused"
        database_direct_url: str = "postgresql://localhost/unused"

    try:
        settings = LiveSettings()
    except ValidationError:
        pytest.skip("Configure backend/.env for Daytona and the generative models")
    if not all(
        value.strip()
        for value in (
            settings.daytona_api_key.get_secret_value(),
            settings.daytona_volume_id,
            settings.llm_api_key.get_secret_value(),
            settings.llm_model,
            settings.sub_llm_model,
        )
    ):
        pytest.skip("Configure the Daytona Volume and host model credentials")
    if not os.environ.get("TYPESAFE_API_KEY", "").strip():
        pytest.skip("Configure the host TYPESAFE_API_KEY for Jev")

    registry = SkillRegistry.load(settings.skills_dir)
    selection = await asyncio.to_thread(select_skills, settings, registry, "Summarize a CSV")
    assert selection.scores["data-analysis"] >= 0.25
    selection = select_skills(settings, registry, "Summarize a CSV", ["@data-analysis"])

    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    experiment = mlflow.set_experiment(f"phase2-csv-{uuid4()}")
    mlflow.dspy.autolog()
    try:
        async with make_daytona_client(settings) as daytona:
            sandbox, _, created = await ensure_session_sandbox(daytona, settings, uuid4(), None)
            path = "/workspace/files/sales.csv"
            uploaded = False
            try:
                assert created
                await sandbox.fs.upload_file(b"value\n2\n5\n11\n", path)
                uploaded = True
                factory = make_interpreter_factory(sandbox, settings, asyncio.get_running_loop())
                events = []

                async def emit(run_id, event):
                    events.append((run_id, event))

                callbacks = ChildCallbacks(AsyncMock(return_value=None), AsyncMock())
                common = dict(
                    files_manifest=[{"path": path}],
                    memories=[],
                    interpreter_factory=factory,
                    emit=emit,
                    selection=selection,
                    callbacks=callbacks,
                )
                first = await run_workspace_task(
                    settings,
                    run_id="first",
                    **common,
                    task="Use spawn_agent exactly once to compute the CSV sum. Tell the child to "
                    "read the file itself. Set a Python global phase2_sentinel = 'first' "
                    "in the root context. Answer with only the integer sum.",
                )
                history = dspy.History.model_validate_json(first.history.model_dump_json())
                second = await run_workspace_task(
                    settings,
                    run_id="second",
                    history=history,
                    **common,
                    task="Use Python to verify phase2_sentinel is absent from globals(). "
                    "Then use the prior turn's answer and reply '<sum>; fresh'. "
                    "Do not spawn another child.",
                )
                assert first.answer.strip() == "18" and second.answer.strip() == "18; fresh"
                calls = [
                    call.args[0]
                    for call in callbacks.on_child_start.await_args_list
                    if call.args[0].kind == "subagent"
                ]
                assert len(calls) == 1 and calls[0].depth == 1
                child = calls[0]
                assert ("first", SubagentStartedEvent(child)) in events
                assert (child.id, RunStartedEvent("workspace")) in events
                assert ("first", SubagentCompletedEvent(child.id)) in events
                assert len(second.history.messages) > len(history.messages)
                assert any("phase2_sentinel" in step["code"] for step in first.trajectory)
                assert any("phase2_sentinel" in step["code"] for step in second.trajectory)
                gate = await consolidate_memory(settings, "Summarize the CSV", first.answer, [])
                assert gate.probability is not None and 0 <= gate.probability <= 1
                assert gate.action in {"insert", "update", "skip"}
            finally:
                try:
                    if uploaded:
                        await sandbox.fs.delete_file(path)
                finally:
                    await daytona.v0.delete(sandbox, wait=True)
        traces = mlflow.search_traces(
            locations=[experiment.experiment_id], return_type="list", flush=True
        )
        assert mlflow.__version__.startswith("3.18.")
        assert any("RLM" in span.name for trace in traces for span in trace.data.spans)
    finally:
        mlflow.dspy.autolog(disable=True)


@pytest.mark.asyncio
async def test_production_skills_catalog_repl_preview_budget_and_choice_selection(
    settings, tmp_path, monkeypatch
):
    skills_root = Path(__file__).resolve().parents[1] / "skills"
    registry = SkillRegistry.load(skills_root)
    expected_names = (
        "chart-visualization",
        "data-analysis",
        "document-extraction",
        "recursive-synthesis",
        "scientific-computing",
        "skill-creator",
        "spreadsheet-audit",
    )
    assert tuple(skill.name for skill in registry.skills) == expected_names

    selector = build_skill_selector(registry.skills)
    assert set(selector.signature.output_fields) == {"needs_skill", "primary_skill"}
    choice_criteria = selector.signature.output_fields["primary_skill"].annotation.criteria()
    assert tuple(sorted(choice_criteria)) == expected_names

    # Verify every single skill and all 21 co-activated 2-skill pairs fit 100% inside DSPy 3.5's
    # 1,000-char REPLVariable preview without head/tail truncation.
    for skill in registry.skills:
        assert len(skill.body) <= 450, f"{skill.name} body exceeds 450 chars"
        single_text = f"## {skill.name}\n{skill.body}"
        single_var = REPLVariable.from_value("active_skills", single_text)
        assert single_var.total_length <= 1000
        assert single_var.preview == single_text

    for first_skill, second_skill in combinations(registry.skills, 2):
        combined_text = (
            f"## {first_skill.name}\n{first_skill.body}\n\n"
            f"## {second_skill.name}\n{second_skill.body}"
        )
        pair_var = REPLVariable.from_value("active_skills", combined_text)
        assert len(combined_text) <= 1000, f"{first_skill.name}+{second_skill.name} > 1000 chars"
        assert pair_var.preview == combined_text

    # Verify multi-skill co-activation via Jev and end-to-end RLM execution in _LocalSandbox.
    def fake_jev_decide(self, *, task, lm):
        return SimpleNamespace(
            needs_skill=SimpleNamespace(value=True, probability=0.91),
            primary_skill=SimpleNamespace(
                value="chart-visualization",
                probabilities={
                    "chart-visualization": 0.58,
                    "data-analysis": 0.42,
                    "document-extraction": 0.0,
                    "recursive-synthesis": 0.0,
                    "scientific-computing": 0.0,
                    "skill-creator": 0.0,
                    "spreadsheet-audit": 0.0,
                },
            ),
        )

    monkeypatch.setattr(dspy.Predict, "forward", fake_jev_decide)
    prod_settings = settings.model_copy(update={"skills_dir": skills_root})
    selection = select_skills(
        prod_settings,
        registry,
        "Analyze regional sales CSV and render a verified revenue chart PNG",
    )
    assert [skill.name for skill in selection.active_skills] == [
        "chart-visualization",
        "data-analysis",
    ]
    monkeypatch.undo()

    sandbox = _LocalSandbox(tmp_path)
    (tmp_path / "files").mkdir()
    (tmp_path / "artifacts").mkdir()
    csv_path = tmp_path / "files" / "sales.csv"
    csv_path.write_text(
        "region,status,amount_eur\nEU,completed,120\nUS,completed,80\nEU,cancelled,50\n",
        encoding="utf-8",
    )
    factory = make_interpreter_factory(sandbox, prod_settings, asyncio.get_running_loop())
    root_lm = DummyLM(
        [
            {
                "plan": "Inspect CSV headers, filter cancelled orders, assert totals, and plot PNG",
                "code": (
                    "import csv\n"
                    "assert '## chart-visualization' in active_skills\n"
                    "assert '## data-analysis' in active_skills\n"
                    "with open('files/sales.csv', encoding='utf-8') as f:\n"
                    "    rows = list(csv.DictReader(f))\n"
                    "comp = [int(r['amount_eur']) for r in rows if r['status'] == 'completed']\n"
                    "excl = [int(r['amount_eur']) for r in rows if r['status'] != 'completed']\n"
                    "assert sum(comp) + sum(excl) == sum(int(r['amount_eur']) for r in rows)\n"
                    "with open('artifacts/summary.csv', 'w', encoding='utf-8') as out:\n"
                    "    out.write(f'completed_eur,{sum(comp)}\\n')\n"
                    "print(f'total={sum(comp)} excluded={len(excl)}')\n"
                ),
            },
            {
                "plan": "Submit verified answer with units, exclusions, and artifact paths",
                "code": (
                    "SUBMIT("
                    "'Completed revenue: 200 EUR (excluded 1 cancelled row of 50 EUR).', "
                    "['artifacts/summary.csv']"
                    ")"
                ),
            },
        ]
    )
    sub_lm = DummyLM([{"text": "unused"}])
    events = []

    async def emit(run_id, event):
        events.append((run_id, event))

    prediction = await run_workspace_task(
        prod_settings,
        task="Summarize completed sales in EUR and save a summary artifact",
        files_manifest=[{"path": "files/sales.csv", "kind": "input"}],
        memories=[],
        interpreter_factory=factory,
        run_id="skill-verify",
        emit=emit,
        selection=selection,
        model_override=root_lm,
        sub_lm=sub_lm,
    )
    assert "200 EUR" in prediction.answer
    assert "excluded 1 cancelled" in prediction.answer
    assert prediction.artifact_paths == ["artifacts/summary.csv"]
    prompt_dump = json.dumps(root_lm.get_convo(0)[0])
    assert "## chart-visualization" in prompt_dump
    assert "## data-analysis" in prompt_dump
    assert "PIL.Image.open()" in prompt_dump
    assert "Never mutate input files" in prompt_dump

    # Verify @skill-creator synthesizes and validates a SKILL.md artifact from memories.
    creator_sel = select_skills(prod_settings, registry, "Create a skill", ["@skill-creator"])
    creator_lm = DummyLM(
        [
            {
                "plan": "Draft and validate SKILL.md from approved session memories",
                "code": (
                    "from pathlib import Path\n"
                    "import yaml\n"
                    "assert 'Always report revenue in EUR' in memories[0]\n"
                    "body = 'Report revenue in EUR and exclude cancelled rows.'\n"
                    "assert 0 < len(body) <= 450\n"
                    "doc = '---\\nname: eur-revenue\\ndescription: Report EUR revenue.\\n---\\n\\n'"
                    " + body + '\\n'\n"
                    "meta = yaml.safe_load(doc.split('---')[1])\n"
                    "assert meta['name'] == 'eur-revenue' and meta['description']\n"
                    "out = Path('artifacts/eur-revenue/SKILL.md')\n"
                    "out.parent.mkdir(parents=True, exist_ok=True)\n"
                    "out.write_text(doc, encoding='utf-8')\n"
                    "SUBMIT('Created eur-revenue skill.', ['artifacts/eur-revenue/SKILL.md'])\n"
                ),
            }
        ]
    )
    created = await run_workspace_task(
        prod_settings,
        task="Create a reusable skill from our session memories",
        files_manifest=[],
        memories=["Always report revenue in EUR and exclude cancelled orders."],
        interpreter_factory=factory,
        run_id="skill-creator-verify",
        emit=emit,
        selection=creator_sel,
        model_override=creator_lm,
        sub_lm=sub_lm,
    )
    assert created.artifact_paths == ["artifacts/eur-revenue/SKILL.md"]
    generated_registry = SkillRegistry.load(tmp_path / "artifacts")
    assert [s.name for s in generated_registry.skills] == ["eur-revenue"]


@pytest.mark.skipif(
    os.environ.get("FLEET_RUN_PHASE2_LIVE") != "1", reason="Requires authorized live services"
)
@pytest.mark.asyncio
async def test_live_skill_selection_and_ab_results():
    """Verify live Jev routing and compare correct results from both arms in Daytona."""

    class LiveSkillSettings(Settings):
        database_url: str = "postgresql://localhost/unused"
        database_direct_url: str = "postgresql://localhost/unused"

    try:
        live_settings = LiveSkillSettings()
    except ValidationError:
        pytest.skip("Configure backend/.env for Daytona and generative models")
    if not all(
        value.strip()
        for value in (
            live_settings.daytona_api_key.get_secret_value(),
            live_settings.llm_api_key.get_secret_value(),
            live_settings.llm_model,
            live_settings.sub_llm_model,
        )
    ):
        pytest.skip("Configure Daytona and host LLM credentials in backend/.env")
    if not os.environ.get("TYPESAFE_API_KEY", "").strip():
        pytest.skip("Configure TYPESAFE_API_KEY for Jev")

    skills_root = Path(__file__).resolve().parents[1] / "skills"
    live_settings = live_settings.model_copy(
        update={"skills_dir": skills_root, "rlm_max_iters": 6, "daytona_exec_timeout_seconds": 60}
    )
    registry = SkillRegistry.load(skills_root)

    # Verify live TypeSafe Jev routes each skill and co-activates both for a compound task.
    routing_cases = [
        (
            "data-analysis",
            (
                "Inspect sales_2026.csv in /workspace/files, group revenue by region excluding "
                "cancelled orders, and save a summary Parquet table."
            ),
        ),
        (
            "document-extraction",
            (
                "Read quarterly_report.pdf with pypdf and use batched sub-LM queries to extract "
                "risk factors per page."
            ),
        ),
        (
            "chart-visualization",
            (
                "Create a high-DPI matplotlib bar chart of monthly revenue saved to "
                "/workspace/artifacts/revenue.png and verify the image with Pillow."
            ),
        ),
        (
            "spreadsheet-audit",
            (
                "Audit the multi-sheet financial_model.xlsx workbook with openpyxl, check formula "
                "cells vs cached values, and reconcile sheet subtotals."
            ),
        ),
        (
            "scientific-computing",
            (
                "Run a two-sample Welch t-test and curve fit using scipy.stats and numpy, "
                "verifying the p-value with two independent calculations."
            ),
        ),
        (
            "recursive-synthesis",
            (
                "Decompose this multi-file investigation by delegating isolated workspace "
                "subtasks with spawn_agent and map-reduce chunks with llm_query_batched."
            ),
        ),
        (
            "skill-creator",
            (
                "Create a reusable Agent Skill (SKILL.md) under /workspace/artifacts from our "
                "approved session memories and conversation history."
            ),
        ),
    ]
    for expected_skill, task_prompt in routing_cases:
        sel = await asyncio.to_thread(select_skills, live_settings, registry, task_prompt)
        active_names = [s.name for s in sel.active_skills]
        assert expected_skill in active_names, (
            f"Expected {expected_skill} in {active_names} (scores={dict(sel.scores)})"
        )

    compound_prompt = (
        "Analyze the regional sales CSV dataset and also create a matplotlib chart saved to "
        "/workspace/artifacts/chart.png"
    )
    compound_sel = await asyncio.to_thread(select_skills, live_settings, registry, compound_prompt)
    compound_names = {s.name for s in compound_sel.active_skills}
    assert compound_names == {"data-analysis", "chart-visualization"}

    async def run_arm(prompt, filename, data, selection, artifact_path=None):
        async def noop_emit(_run_id, _event):
            return None

        async with (
            make_daytona_client(live_settings) as daytona,
            ephemeral_sandbox(daytona, live_settings) as sandbox,
        ):
            input_path = f"/workspace/files/{filename}"
            await sandbox.fs.upload_file(data, input_path)
            prediction = await run_workspace_task(
                live_settings,
                task=prompt,
                files_manifest=[{"path": input_path, "kind": "input"}],
                memories=[],
                interpreter_factory=make_interpreter_factory(
                    sandbox, live_settings, asyncio.get_running_loop()
                ),
                run_id=f"ab-{uuid4().hex[:8]}",
                emit=noop_emit,
                selection=selection,
            )
            answer = json.loads(prediction.answer)
            if artifact_path is None:
                return answer, None
            assert artifact_path in {
                path.removeprefix("/workspace/") for path in prediction.artifact_paths
            }
            artifact = await sandbox.fs.download_file(f"/workspace/{artifact_path}")
            return answer, artifact

    sales = (
        b"region,status,revenue_eur\n"
        b"EMEA,completed,1200\nEMEA,cancelled,450\nAMER,completed,1800\n"
        b"APAC,completed,950\nAMER,cancelled,300\n"
    )
    chart_path = "artifacts/revenue_chart.svg"
    chart_prompt = (
        "Analyze files/sales.csv by region, excluding cancelled orders. Create a labeled "
        f"Matplotlib bar chart at {chart_path} in EUR. Set matplotlib rcParams "
        "svg.fonttype to 'none' so text is readable in the SVG. Annotate each bar with "
        "its region and exact total in a single text label formatted REGION NUMBER. "
        "Return only a JSON object mapping each region to its completed revenue."
    )
    expected_totals = {"EMEA": 1200, "AMER": 1800, "APAC": 950}
    for label, selection in (("baseline", None), ("skill-guided", compound_sel)):
        totals, svg = await run_arm(chart_prompt, "sales.csv", sales, selection, chart_path)
        assert totals == expected_totals, label
        chart = ElementTree.fromstring(svg)
        assert chart.tag == "{http://www.w3.org/2000/svg}svg", label
        labels = {"".join(node.itertext()).strip() for node in chart.findall(".//{*}text")}
        assert {f"{region} {value}" for region, value in expected_totals.items()} <= labels, label

    experiment = (
        b"control_ms,treatment_ms\n"
        b"124.2,108.5\n131.0,111.2\n128.4,106.8\n126.9,109.9\n"
        b"133.5,112.4\n129.1,107.6\n127.8,110.1\n130.2,109.0\n"
    )
    stats_prompt = (
        "Compare control_ms and treatment_ms in files/experiment.csv with a two-sided "
        "Welch t-test, taking control minus treatment. At alpha 0.05, decide whether "
        "treatment is significantly faster. Return only a JSON object with numeric "
        "fields t, df, p and boolean field treatment_is_faster."
    )
    stats_selection = select_skills(live_settings, registry, stats_prompt, ["scientific-computing"])
    for label, selection in (("baseline", None), ("skill-guided", stats_selection)):
        result, _ = await run_arm(stats_prompt, "experiment.csv", experiment, selection)
        assert result["treatment_is_faster"] is True, label
        assert result["t"] == pytest.approx(16.3978, abs=0.01), label
        assert result["df"] == pytest.approx(12.1326, abs=0.01), label
        assert 0 < result["p"] < 1e-7, label
