"""Phase 2 verification. Live services require FLEET_RUN_PHASE2_LIVE=1."""

import asyncio
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import dspy
import pytest
from dspy.primitives.code_interpreter import CodeExecutionError, FinalOutput
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
    ensure_session_sandbox,
    list_new_artifacts,
    make_daytona_client,
    make_interpreter_factory,
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
        create=AsyncMock(return_value=sandbox), get=AsyncMock(return_value=sandbox)
    )
    session_id = uuid4()
    assert await ensure_session_sandbox(daytona, settings, session_id, None) == (
        sandbox,
        sandbox.id,
        True,
    )
    params = daytona.create.call_args.args[0]
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
                "code": "assert 'sentinel' not in globals()\nSUBMIT('Still 7', [])",
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
                "except RuntimeError:\n"
                "    pass\n"
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
                    await daytona.delete(sandbox, wait=True)
        traces = mlflow.search_traces(
            locations=[experiment.experiment_id], return_type="list", flush=True
        )
        assert mlflow.__version__.startswith("3.18.")
        assert any("RLM" in span.name for trace in traces for span in trace.data.spans)
    finally:
        mlflow.dspy.autolog(disable=True)
