"""Phase 2 verification. Live services require FLEET_RUN_PHASE2_LIVE=1."""

import asyncio
import base64
import hashlib
import json
import os
import sys
from contextlib import aclosing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
from xml.etree import ElementTree

import dspy
import mlflow
import pytest
from dspy.primitives.code_interpreter import (
    CodeExecutionError,
    CodeInterpreterError,
    FinalOutput,
)
from dspy.utils.dummies import DummyLM
from mlflow.entities import SpanStatusCode
from pydantic import ValidationError

from fleet_rlm import skills
from fleet_rlm.agent import (
    _SUBMISSION_GUIDANCE,
    _TOOL_DOCS_HEADER,
    ChildCallbacks,
    DistillMemory,
    MemoryGate,
    PythonExecStartedEvent,
    ReasoningEvent,
    RunResultEvent,
    RunStartedEvent,
    SubagentCompletedEvent,
    SubagentStartedEvent,
    TurnTelemetry,
    apply_submission_guidance,
    bound_history,
    build_program,
    consolidate_memory,
    run_workspace_task,
    strip_dynamic_tool_docs,
)
from fleet_rlm.config import Settings
from fleet_rlm.interpreter import (
    ClientTimeoutError,
    DaytonaConnectionError,
    DaytonaError,
    SandboxCleanupError,
    SandboxStartupError,
    allocate_session_sandbox,
    collect_workspace_artifacts,
    delete_sandbox,
    delete_sandbox_by_id,
    download_workspace_file,
    ephemeral_sandbox,
    list_new_artifacts,
    make_daytona_client,
    make_interpreter_factory,
    prepare_sandbox,
    reusable_session_sandbox,
    stage_workspace_bytes,
    upload_workspace_file,
)
from fleet_rlm.skills import (
    DEFAULT_SELECTION_THRESHOLD,
    SkillRegistry,
    build_skill_selector,
    format_selector_task,
    select_skills,
)


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        database_url="postgresql://localhost/unused",
        database_direct_url="postgresql://localhost/unused",
        daytona_api_key="offline",
        daytona_image="offline",
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


class _LocalWorker:
    def __init__(self, process, tag, sandbox):
        self.process = process
        self.id = tag
        self.sandbox = sandbox
        self.cursor = 0

    async def write_stdin(self, message):
        frame = json.loads(message)
        if frame["kind"] == "reply":
            self.sandbox.replies.append(frame)
        else:
            self.sandbox.run_code_timeouts.append(frame["rpc_timeout"])
        self.process.stdin.write(message.encode())
        await self.process.stdin.drain()

    async def stream_logs(self, *, cursor, encoding):
        assert encoding == "base64"
        while chunk := await self.process.stdout.read(1024):
            cut = len(chunk) // 2 or 1
            for part in (chunk[:cut], chunk[cut:]):
                if part:
                    self.cursor += 1
                    yield SimpleNamespace(
                        type="log",
                        cursor=str(self.cursor),
                        frame=SimpleNamespace(channel="stdout", data=base64.b64encode(part)),
                    )

    async def delete(self):
        self.sandbox.deleted.append(self)


class _LocalSandbox:
    """Run the production worker locally through the v2 process interface."""

    def __init__(self, cwd):
        self.cwd = cwd
        self.process = self
        self.fs = self
        self.contexts = []
        self.deleted = []
        self.replies = []
        self.run_code_timeouts = []
        self.worker_timeouts = []
        self.state = "started"
        self.id = "test-sandbox"
        self.wait = AsyncMock()

    async def write_bytes(self, path, data, *, atomic):
        assert atomic
        Path(path).write_bytes(data)

    async def remove(self, path):
        Path(path).unlink(missing_ok=True)

    async def start(self, *, argv, cwd, stdin, tag, execution_timeout=None):
        assert cwd == "/workspace" and stdin == "open"
        self.worker_timeouts.append(execution_timeout)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            *argv[1:],
            cwd=self.cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        worker = _LocalWorker(process, tag, self)
        self.contexts.append(worker)
        return worker

    async def kill_all(self, *, tag, timeout):
        for worker in self.contexts:
            if worker.id == tag and worker.process.returncode is None:
                worker.process.kill()
                await worker.process.wait()

    async def run(self, *, argv, timeout, execution_timeout):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            *argv[1:],
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        return SimpleNamespace(exit_code=process.returncode, stdout=stdout.decode())


@pytest.mark.asyncio
async def test_provisioning_and_reuse(settings, tmp_path):
    sandbox = _LocalSandbox(tmp_path)
    sandbox.process = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(exit_code=0)))
    daytona = SimpleNamespace(
        v2=SimpleNamespace(
            create=AsyncMock(return_value=sandbox),
            get=AsyncMock(return_value=sandbox),
            find_by_name=AsyncMock(return_value=[sandbox]),
            delete=AsyncMock(),
        )
    )
    name = f"fleet-{uuid4().hex}"
    assert await allocate_session_sandbox(daytona, settings, name) is sandbox
    await prepare_sandbox(sandbox)
    create = daytona.v2.create
    assert create.call_args.args == (settings.daytona_image,)
    args = create.call_args.kwargs
    assert "env" not in args and "volumes" not in args
    assert args["network_policy"] == {"block_all": True, "allow_domains": []}
    assert args["lifecycle_policy"]["on_idle_timeout"] == "delete"
    assert await reusable_session_sandbox(daytona, sandbox.id) is sandbox
    assert await allocate_session_sandbox(daytona, settings, name, recovering=True) is sandbox
    create.assert_awaited_once()
    daytona.v2.find_by_name.assert_awaited_once_with(name, region=settings.daytona_region)
    sandbox.wait.assert_not_awaited()
    sandbox.state = "destroyed"
    assert await reusable_session_sandbox(daytona, sandbox.id) is None
    daytona.v2.delete.assert_awaited_once_with(sandbox.id, timeout=30)


@pytest.mark.asyncio
@pytest.mark.parametrize("during_create", [True, False])
async def test_failed_workspace_startup_deletes_new_sandbox(settings, tmp_path, during_create):
    sandbox = _LocalSandbox(tmp_path)
    sandbox.process = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(exit_code=1)))
    daytona = SimpleNamespace(
        v2=SimpleNamespace(
            create=AsyncMock(
                return_value=sandbox,
                side_effect=DaytonaError("Provider rejected startup") if during_create else None,
            ),
            find_by_name=AsyncMock(return_value=[sandbox]),
            delete=AsyncMock(),
        )
    )
    with pytest.raises(SandboxStartupError):
        async with ephemeral_sandbox(daytona, settings):
            pytest.fail("failed allocation must not yield")
    if during_create:
        name = daytona.v2.create.call_args.kwargs["name"]
        daytona.v2.find_by_name.assert_awaited_once_with(name, region=settings.daytona_region)
    else:
        daytona.v2.find_by_name.assert_not_awaited()
    daytona.v2.delete.assert_awaited_once_with(sandbox.id, timeout=30)


@pytest.mark.asyncio
async def test_daytona_cell_spans_are_nested_and_preserve_execution_results(
    settings, tmp_path, monkeypatch
):
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_path / 'traces.db'}")
    sandbox = _LocalSandbox(tmp_path)
    interpreter = make_interpreter_factory(sandbox, settings, asyncio.get_running_loop())()
    interpreter.output_fields = [{"name": "answer"}]
    waiting = asyncio.Event()

    async def wait_for_abort():
        waiting.set()
        await asyncio.Future()

    interpreter.tools["wait_for_abort"] = wait_for_abort
    try:
        with mlflow.start_span("rlm-test") as root:
            assert await asyncio.to_thread(interpreter.execute, "print(6 * 7)") == "42\n"
            assert await asyncio.to_thread(interpreter.execute, "SUBMIT('done')") == FinalOutput(
                {"answer": "done"}
            )
            with pytest.raises(CodeExecutionError):
                await asyncio.to_thread(interpreter.execute, "raise ValueError('test error')")
            cell = asyncio.create_task(asyncio.to_thread(interpreter.execute, "wait_for_abort()"))
            await asyncio.wait_for(waiting.wait(), timeout=5)
            await interpreter.abort()
            with pytest.raises(asyncio.CancelledError):
                await cell
    finally:
        await asyncio.to_thread(interpreter.shutdown)

    mlflow.flush_trace_async_logging()
    trace = mlflow.get_trace(root.trace_id)
    assert trace is not None
    cells = [span for span in trace.data.spans if span.name == "daytona.python.execute"]
    stdout, submitted, failure, cancelled = cells
    start, stop = [span for span in trace.data.spans if span.name.startswith("daytona.worker.")]
    assert (start.name, stop.name) == ("daytona.worker.start", "daytona.worker.stop")
    for span in (start, stop):
        assert span.parent_id == root.span_id
        assert span.attributes["daytona.worker_id"] == sandbox.contexts[0].id
    for span in (stdout, submitted, failure, cancelled):
        assert span.name == "daytona.python.execute"
        assert span.span_type == "TOOL"
        assert span.parent_id == root.span_id
        assert span.attributes["daytona.sandbox_id"] == sandbox.id
    assert stdout.inputs == {"code": "print(6 * 7)"}
    assert stdout.outputs == {"stdout": "42\n"}
    assert submitted.outputs == {"submission": {"answer": "done"}}
    assert failure.status.status_code == SpanStatusCode.ERROR
    assert cancelled.inputs == {"code": "wait_for_abort()"}
    assert cancelled.outputs is None
    assert cancelled.status.status_code == SpanStatusCode.ERROR
    assert cancelled.attributes["daytona.execution.status"] == "cancelled"


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
        replies = len(sandbox.replies)
        cell = asyncio.create_task(execute("slow()"))
        await asyncio.wait_for(waiting.wait(), timeout=5)
        await interpreter.abort()
        with pytest.raises(asyncio.CancelledError):
            await cell
        assert cancelled.is_set() and len(sandbox.replies) == replies
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
async def test_cancelled_abort_waits_for_pending_worker_start(settings, tmp_path, monkeypatch):
    sandbox = _LocalSandbox(tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()
    start = sandbox.start

    async def delayed_start(**kwargs):
        entered.set()
        await release.wait()
        return await start(**kwargs)

    monkeypatch.setattr(sandbox, "start", delayed_start)
    worker = make_interpreter_factory(sandbox, settings, asyncio.get_running_loop())()
    starting = asyncio.create_task(asyncio.to_thread(worker.start))
    await asyncio.wait_for(entered.wait(), timeout=5)
    aborting = asyncio.create_task(worker.abort())
    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        aborting.cancel()
        await asyncio.sleep(0)
        assert not aborting.done()
    finally:
        release.set()
        await starting
        with pytest.raises(asyncio.CancelledError):
            await aborting
    assert len(sandbox.contexts) == 1 and sandbox.contexts == sandbox.deleted
    assert sandbox.contexts[0].process.returncode is not None


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

        with pytest.raises(
            CodeExecutionError,
            match=r"Python execution timed out after 1s; interpreter context was reset",
        ):
            await execute("import time\ntime.sleep(5)")

        assert sandbox.deleted == [first_context]
        assert interpreter._closed is False
        assert interpreter._handle is None

        assert await execute(
            "assert 'cached' not in globals()\nSUBMIT('recovered')"
        ) == FinalOutput({"answer": "recovered"})
        assert len(sandbox.contexts) == 2
        assert interpreter._handle is sandbox.contexts[1]
        assert interpreter._closed is False
    finally:
        await asyncio.to_thread(interpreter.shutdown)
    assert sandbox.contexts == sandbox.deleted

    for failure in (
        DaytonaError("transport closed"),
        ExceptionGroup("transport group", [DaytonaError("frame error")]),
    ):
        broken = factory()
        try:
            await asyncio.to_thread(broken.start)
            handle = broken._handle
            send = AsyncMock(side_effect=failure)
            monkeypatch.setattr(handle, "write_stdin", send)
            with pytest.raises(CodeInterpreterError, match="Daytona execution transport failed"):
                await asyncio.to_thread(broken.execute, "print('unreachable')")
            send.assert_awaited_once()
            assert broken._closed is True
        finally:
            await asyncio.to_thread(broken.shutdown)


@pytest.mark.asyncio
async def test_expired_turn_does_not_start_a_worker(settings, tmp_path):
    sandbox = _LocalSandbox(tmp_path)
    loop = asyncio.get_running_loop()
    interpreter = make_interpreter_factory(sandbox, settings, loop, turn_deadline=loop.time())()
    with pytest.raises(TimeoutError, match="Turn timed out"):
        await asyncio.to_thread(interpreter.start)
    await interpreter.abort()
    assert sandbox.worker_timeouts == []


@pytest.mark.parametrize("wrapped", [False, True])
def test_benchmark_reads_only_the_rlm_subtree(monkeypatch, wrapped):
    monkeypatch.syspath_prepend(Path(__file__).resolve().parents[2] / "scripts")
    from dspy_baseline import workspace_trace_root, workspace_trace_task, workspace_trace_usage

    usage = {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
    rlm = SimpleNamespace(
        name="fleet.rlm.run",
        span_id="rlm",
        parent_id="turn" if wrapped else None,
        inputs={"task": "Synthetic task"},
        attributes={"fleet.depth": 0, "fleet.run_id": "run"},
    )
    forward = SimpleNamespace(
        name="RLM.forward",
        span_id="forward",
        parent_id="rlm",
        attributes={"mlflow.chat.tokenUsage": usage},
    )
    spans = [
        rlm,
        forward,
        SimpleNamespace(name="LM.__call__", span_id="model", parent_id="forward"),
    ]
    if wrapped:
        spans[:0] = [
            SimpleNamespace(name="fleet.turn", span_id="turn", parent_id=None),
            SimpleNamespace(name="LM.__call__", span_id="skill-selector", parent_id="turn"),
        ]
    trace = SimpleNamespace(
        info=SimpleNamespace(tags={"fleet.run_id": "run"}, token_usage=None if wrapped else usage),
        data=SimpleNamespace(spans=spans),
    )
    assert workspace_trace_root(trace) is rlm
    assert (
        workspace_trace_task(SimpleNamespace(get_trace=lambda _: trace), "trace")
        == "Synthetic task"
    )
    assert workspace_trace_usage(trace, rlm) == (usage, 1)


@pytest.mark.asyncio
async def test_native_rlm_recursion_and_history_round_trip(settings, tmp_path):
    settings = settings.model_copy(update={"rlm_max_depth": 1})
    sandbox = _LocalSandbox(tmp_path)
    loop = asyncio.get_running_loop()
    factory = make_interpreter_factory(sandbox, settings, loop, turn_deadline=loop.time() + 60)
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
    root_timeout, child_timeout, followup_timeout = sandbox.worker_timeouts
    assert 0 < followup_timeout < child_timeout < root_timeout <= 60
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
@pytest.mark.parametrize("interruption", [DaytonaConnectionError, ClientTimeoutError])
async def test_stream_resumes_at_cursor_without_repeating_host_calls(
    settings, tmp_path, monkeypatch, interruption
):
    sandbox = _LocalSandbox(tmp_path)
    interpreter = make_interpreter_factory(sandbox, settings, asyncio.get_running_loop())()
    interpreter.output_fields = [{"name": "answer"}]
    tool = AsyncMock(return_value="resume-ok")
    interpreter.tools["probe"] = tool
    await asyncio.to_thread(interpreter.start)
    handle = interpreter._handle
    stream = handle.stream_logs(cursor=None, encoding="base64")
    cursors = []

    async def interrupted(*, cursor, encoding):
        cursors.append(cursor)
        assert encoding == "base64"
        async for event in stream:
            yield event
            if len(cursors) == 1:
                # Model the provider retaining unread frames after disconnect.
                raise interruption("log stream interrupted")

    # Retain a single generator behind the simulated reconnect, including the
    # second half of a JSON frame that was split by the local provider.
    monkeypatch.setattr(handle, "stream_logs", interrupted)
    try:
        result = await asyncio.to_thread(interpreter.execute, "SUBMIT(probe())")
        assert result == FinalOutput({"answer": "resume-ok"})
        assert cursors == [None, "1"]
        tool.assert_awaited_once()
    finally:
        await stream.aclose()
        await asyncio.to_thread(interpreter.shutdown)


@pytest.mark.asyncio
async def test_unknown_worker_cleanup_fails_closed(settings, tmp_path, monkeypatch):
    sandbox = _LocalSandbox(tmp_path)
    interpreter = make_interpreter_factory(sandbox, settings, asyncio.get_running_loop())()
    await asyncio.to_thread(interpreter.start)
    failing_kill = AsyncMock(side_effect=DaytonaError("cleanup lost"))
    try:
        with monkeypatch.context() as patch:
            patch.setattr(sandbox, "kill_all", failing_kill)
            with pytest.raises(SandboxCleanupError, match="cleanup could not be verified"):
                await interpreter.abort()
            failing_kill.assert_awaited_once()
            assert interpreter._closed is True
    finally:
        await interpreter.abort()
    assert sandbox.contexts == sandbox.deleted


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
        assert interpreter._handle is None
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

    # The same size of manifest previously exceeded Linux's per-argument limit.
    known_mapping.update(
        {f"files/{index:04d}-{'x' * 246}.csv": ("a" * 64, 1) for index in range(410)}
    )
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
    sandbox.process = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(exit_code=0)))
    daytona = SimpleNamespace(
        v2=SimpleNamespace(create=AsyncMock(return_value=sandbox), delete=AsyncMock())
    )
    configured = settings.model_copy(
        update={
            "daytona_image": "custom-image",
            "daytona_region": "us-east-ash-1",
            "daytona_idle_timeout_minutes": 7,
            "daytona_optimize_spot": True,
        }
    )
    async with ephemeral_sandbox(daytona, configured) as yielded:
        assert yielded is sandbox
        assert daytona.v2.create.call_args.args == ("custom-image",)
        args = daytona.v2.create.call_args.kwargs
        assert args["region"] == "us-east-ash-1" and args["spot"] is True
        assert args["lifecycle_policy"]["idle_expiry_seconds"] == 420
        assert "volumes" not in args and "env" not in args
        daytona.v2.delete.assert_not_awaited()
    daytona.v2.delete.assert_awaited_once_with(sandbox.id, timeout=30)

    daytona.v2.delete.reset_mock()
    sandbox.process.run.return_value = SimpleNamespace(exit_code=1)
    with pytest.raises(SandboxStartupError, match="initialize the Daytona workspace"):
        async with ephemeral_sandbox(daytona, configured):
            pytest.fail("must not yield when init fails")
    daytona.v2.delete.assert_awaited_once_with(sandbox.id, timeout=30)

    daytona.v2.delete.reset_mock()
    sandbox.process.run.return_value = SimpleNamespace(exit_code=0)
    with pytest.raises(RuntimeError, match="eval crashed"):
        try:
            async with ephemeral_sandbox(daytona, configured):
                raise RuntimeError("eval crashed")
        finally:
            daytona.v2.delete.assert_awaited_once_with(sandbox.id, timeout=30)


@pytest.mark.asyncio
async def test_daytona_v2_and_workspace_file_helpers(settings, tmp_path):
    async with make_daytona_client(settings) as client:
        assert hasattr(client, "v2")

    sandbox = SimpleNamespace(
        id="sb-1",
        process=SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(exit_code=0))),
        fs=SimpleNamespace(upload_file=AsyncMock(), download_file=AsyncMock()),
    )
    daytona = SimpleNamespace(v2=SimpleNamespace(delete=AsyncMock()))
    await delete_sandbox(daytona, sandbox)
    daytona.v2.delete.assert_awaited_once_with(sandbox.id, timeout=30)
    daytona.v2.delete.reset_mock()
    await delete_sandbox_by_id(daytona, "sb-1")
    daytona.v2.delete.assert_awaited_once_with("sb-1", timeout=30)

    source = tmp_path / "a.csv"
    source.write_bytes(b"abc")
    digest = hashlib.sha256(b"abc").hexdigest()
    await upload_workspace_file(sandbox, source, "files/a.csv", sha256=digest, size=3, timeout=30)
    sandbox.fs.upload_file.assert_awaited_once_with(source, "/workspace/files/a.csv", atomic=True)
    make_directory, verify_contents = sandbox.process.run.await_args_list
    assert make_directory.kwargs["argv"] == ["mkdir", "-p", "/workspace/files"]
    assert verify_contents.kwargs["argv"][-3:] == ["/workspace/files/a.csv", digest, "3"]
    sandbox.process.run.return_value = SimpleNamespace(exit_code=1)
    with pytest.raises(SandboxStartupError, match="file directory"):
        await upload_workspace_file(
            sandbox, source, "files/b.csv", sha256=digest, size=3, timeout=15
        )
    sandbox.fs.upload_file.assert_awaited_once()
    destination = tmp_path / "out.txt"
    await download_workspace_file(sandbox, "artifacts/out.txt", destination, timeout=20)
    sandbox.fs.download_file.assert_awaited_once_with("/workspace/artifacts/out.txt", destination)


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
@pytest.mark.parametrize("max_iters", [1, 3])
async def test_preloaded_optimized_state_skips_disk_reads_and_clears_predictor_lms(
    settings, tmp_path, max_iters
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
    configured = settings.model_copy(
        update={"optimized_program_path": path, "rlm_max_iters": max_iters}
    )

    restored = build_program(configured, [], optimized_state=state)
    assert restored.generate_action.signature.instructions == "Preloaded optimized workspace prompt"
    assert all(predictor.lm is None for predictor in restored.predictors())
    assert json.dumps(state, sort_keys=True) == state_snapshot

    sandbox = _LocalSandbox(tmp_path)
    factory = make_interpreter_factory(sandbox, configured, asyncio.get_running_loop())
    actions = [{"plan": "Finish", "code": "SUBMIT('ok', [])"}]
    if max_iters == 3:
        actions.insert(0, {"plan": "Check", "code": "print('verified')"})
    root_lm = DummyLM(actions)
    sub_lm = DummyLM([{"text": "unused"}])
    telemetry = TurnTelemetry()

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
        telemetry=telemetry,
    )
    assert prediction.answer == "ok"
    prompt = json.dumps(root_lm.get_convo(0)[0])
    assert "Preloaded optimized workspace prompt" in prompt
    assert "On the penultimate" in prompt and "submit by the last action" in prompt
    assert f"1/{max_iters}" in prompt
    assert telemetry.completion == "submit"
    assert telemetry.root_actions == len(actions)


@pytest.mark.skipif(
    os.environ.get("FLEET_RUN_PHASE2_LIVE") != "1", reason="Requires authorized live services"
)
@pytest.mark.asyncio
async def test_live_daytona_enforces_worker_deadline():
    settings = Settings()
    async with make_daytona_client(settings) as daytona, ephemeral_sandbox(daytona, settings) as vm:
        loop = asyncio.get_running_loop()
        interpreter = make_interpreter_factory(vm, settings, loop, turn_deadline=loop.time() + 8)()
        try:
            output = await asyncio.to_thread(
                interpreter.execute,
                "import subprocess, sys\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                "print(child.pid)",
            )
            assert isinstance(output, str)
            pid = int(output.strip())
            handle = interpreter._handle
            assert handle is not None
            await handle.wait(timeout=15)
            info = await handle.get()
            assert info.state == "terminal" and info.reason == "timed_out"
            assert info.execution_timeout is not None and 0 < info.execution_timeout <= 8
            check_code = (
                "from pathlib import Path\n"
                "import sys\n"
                "p = Path('/proc') / sys.argv[1] / 'stat'\n"
                "assert not p.exists() or p.read_text().rsplit(') ', 1)[1].startswith('Z ')"
            )
            check = await vm.process.run(
                argv=["python", "-c", check_code, str(pid)],
                timeout=5,
                execution_timeout=5,
            )
            assert check.exit_code == 0, check.stderr
        finally:
            await interpreter.abort()


@pytest.mark.skipif(
    os.environ.get("FLEET_RUN_PHASE2_LIVE") != "1", reason="Requires authorized live services"
)
@pytest.mark.asyncio
async def test_live_streamed_worker_failure_recovery(monkeypatch):
    class LiveSettings(Settings):
        database_url: str = "postgresql://localhost/unused"
        database_direct_url: str = "postgresql://localhost/unused"

    settings = LiveSettings().model_copy(update={"daytona_exec_timeout_seconds": 3})
    if not settings.mlflow_tracking_uri:
        pytest.skip("Configure the staging MLflow tracking URI")
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(settings.mlflow_experiment)

    async with make_daytona_client(settings) as daytona, ephemeral_sandbox(daytona, settings) as vm:
        await vm.fs.write_bytes("/workspace/files/worker-canary.txt", b"fixture", atomic=True)
        factory = make_interpreter_factory(vm, settings, asyncio.get_running_loop())
        probe = factory()
        probe.output_fields = [{"name": "answer"}]

        async def slow_check():
            await asyncio.sleep(3.4)
            return "host-tool-ok"

        probe.tools["slow_check"] = slow_check
        execute = probe.execute
        with mlflow.start_span("fleet.acceptance.worker_recovery") as span:
            try:
                await asyncio.to_thread(probe.start)
                original_worker = probe._handle
                assert original_worker is not None
                open_logs = original_worker.stream_logs
                cursors = []

                async def drop_first_event(*, cursor, encoding):
                    cursors.append(cursor)
                    async with aclosing(open_logs(cursor=cursor, encoding=encoding)) as logs:
                        async for event in logs:
                            yield event
                            if len(cursors) == 1:
                                raise ClientTimeoutError("Simulated stream timeout")

                called = AsyncMock(return_value="cursor resumed")
                probe.tools["probe_resume"] = called
                with monkeypatch.context() as patch:
                    patch.setattr(original_worker, "stream_logs", drop_first_event)
                    assert await asyncio.to_thread(execute, "print(probe_resume())") == (
                        "cursor resumed\n"
                    )
                assert cursors[0] is None and len(cursors) == 2 and cursors[1]
                called.assert_awaited_once()
                assert await asyncio.to_thread(execute, "sentinel = 42; print(sentinel)") == "42\n"
                with pytest.raises(CodeExecutionError, match="ordinary failure"):
                    await asyncio.to_thread(execute, "raise ValueError('ordinary failure')")
                assert await asyncio.to_thread(execute, "print(sentinel)") == "42\n"
                assert await asyncio.to_thread(execute, "print(slow_check())") == "host-tool-ok\n"
                with pytest.raises(CodeExecutionError, match="interpreter context was reset"):
                    await asyncio.to_thread(execute, "import time; time.sleep(20)")
                result = await asyncio.to_thread(
                    execute,
                    "assert 'sentinel' not in globals()\n"
                    "SUBMIT(open('/workspace/files/worker-canary.txt').read())",
                )
                assert result == FinalOutput({"answer": "fixture"})
            finally:
                await probe.abort()
        print(f"worker recovery MLflow trace {span.trace_id}, sandbox {vm.id}")

        child_running = asyncio.Event()
        callbacks = ChildCallbacks(AsyncMock(), AsyncMock())
        callbacks.on_child_start.return_value = uuid4()

        async def emit(_run_id, _event):
            pass

        async def wait_in_child():
            child_running.set()
            await asyncio.Future()

        root_lm = DummyLM([{"plan": "Run the child", "code": "spawn_agent('Wait in the child')"}])
        sub_lm = DummyLM(
            [
                {
                    "plan": "Stage an uncommitted file and wait",
                    "code": "open('/workspace/artifacts/unpublished.txt','w').write('partial')\n"
                    "wait_in_child()",
                }
            ]
        )
        with mlflow.start_span("fleet.acceptance.stop_recursive") as span:
            executing = asyncio.create_task(
                run_workspace_task(
                    settings,
                    task="Stop while the child is active",
                    files_manifest=[],
                    memories=[],
                    interpreter_factory=factory,
                    run_id=str(uuid4()),
                    emit=emit,
                    callbacks=callbacks,
                    model_override=root_lm,
                    sub_lm=sub_lm,
                    selection=SimpleNamespace(active_skills=(), allowed_tools=("wait_in_child",)),
                    host_tools={"wait_in_child": wait_in_child},
                )
            )
            try:
                await asyncio.wait_for(child_running.wait(), 20)
            finally:
                executing.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await executing
            assert callbacks.on_child_finish.await_args.args[1] == "cancelled"
        mlflow.flush_trace_async_logging()
        trace = mlflow.get_trace(span.trace_id)
        cancelled_cells = [
            cell
            for cell in trace.data.spans
            if cell.attributes.get("daytona.execution.status") == "cancelled"
        ]
        assert len({cell.attributes["daytona.worker_id"] for cell in cancelled_cells}) == 2
        print(f"recursive stop MLflow trace {span.trace_id}, sandbox {vm.id}")


@pytest.mark.skipif(
    os.environ.get("FLEET_RUN_PHASE2_LIVE") != "1", reason="Requires authorized live services"
)
@pytest.mark.asyncio
async def test_live_two_turn_csv(tmp_path):
    """Run on an isolated Daytona v2 sandbox and inspect a local MLflow trace."""
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
            settings.llm_api_key.get_secret_value(),
            settings.llm_model,
            settings.sub_llm_model,
        )
    ):
        pytest.skip("Configure Daytona and host model credentials")
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
            sandbox = await allocate_session_sandbox(daytona, settings, f"fleet-{uuid4().hex}")
            path = "/workspace/files/sales.csv"
            uploaded = False
            try:
                await prepare_sandbox(sandbox)
                await sandbox.fs.write_bytes(path, b"value\n2\n5\n11\n", atomic=True)
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
                        await sandbox.fs.remove(path)
                finally:
                    await delete_sandbox(daytona, sandbox)
        traces = mlflow.search_traces(
            locations=[experiment.experiment_id], return_type="list", flush=True
        )
        assert mlflow.__version__.startswith("3.18.")
        assert any("RLM" in span.name for trace in traces for span in trace.data.spans)
    finally:
        mlflow.dspy.autolog(disable=True)


@pytest.mark.parametrize(
    ("prompt", "pins", "expected"),
    [
        (
            "Analyze this sales data and create an SVG bar chart. Reconcile all four rows.",
            None,
            ["chart-visualization", "data-analysis"],
        ),
        ("Plot the CSV as an SVG chart.", None, ["chart-visualization"]),
        (
            "Analyze this sales data and create charts.",
            None,
            ["chart-visualization", "data-analysis"],
        ),
        ("Create a chart analyzing sales data.", None, ["chart-visualization", "data-analysis"]),
        (
            "Audit workbook formulas and analyze sales data with charts.",
            None,
            ["chart-visualization", "spreadsheet-audit"],
        ),
        (
            "Analyze this sales data and create an SVG bar chart.",
            ["chart-visualization"],
            ["chart-visualization"],
        ),
    ],
)
def test_compound_analysis_and_chart_selection(settings, monkeypatch, prompt, pins, expected):
    registry = SkillRegistry.load(Path(__file__).resolve().parents[1] / "skills")

    def decide(*args, **kwargs):
        return SimpleNamespace(
            needs_skill=SimpleNamespace(value=True),
            primary_skill=SimpleNamespace(
                value="chart-visualization",
                probabilities={
                    "chart-visualization": 0.96,
                    "data-analysis": 0.03,
                    "spreadsheet-audit": 0.9 if "formulas" in prompt else 0.01,
                },
            ),
        )

    monkeypatch.setattr(dspy.Predict, "forward", decide)
    selection = select_skills(settings, registry, prompt, pins)
    assert [skill.name for skill in selection.active_skills] == expected


@pytest.mark.asyncio
async def test_production_skills_catalog_and_choice_selection(settings, tmp_path, monkeypatch):
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
        "web-research",
    )
    assert tuple(skill.name for skill in registry.skills) == expected_names

    selector = build_skill_selector(registry.skills)
    assert set(selector.signature.output_fields) == {"needs_skill", "primary_skill"}
    choice_criteria = selector.signature.output_fields["primary_skill"].annotation.criteria()
    assert tuple(sorted(choice_criteria)) == expected_names

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
                    "assert 'when it affects the result' in active_skills\n"
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
    assert "In this app, Fleet means Fleet-RLM" in prompt_dump

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
                    "assert body.strip()\n"
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


@pytest.mark.asyncio
async def test_recursive_synthesis_can_recover_a_failed_batch_item(settings, tmp_path):
    class PartiallyFailingLM(DummyLM):
        def __call__(self, prompt=None, **kwargs):
            if prompt == "failed chunk":
                raise dspy.LMError("Synthetic provider failure")
            return ["12" if prompt == "first chunk" else "17"]

    root_lm = DummyLM(
        [
            {
                "plan": "Extract figures concurrently and isolate a partial failure.",
                "code": (
                    "results = llm_query_batched(['first chunk', 'failed chunk'])\n"
                    "assert results == ['12', '[ERROR] Model query failed']\n"
                    "replacement = llm_query('retry chunk')\n"
                    "assert replacement == '17'\n"
                    "SUBMIT(str(int(results[0]) + int(replacement)), [])"
                ),
            }
        ]
    )
    registry = SkillRegistry.load(Path(__file__).resolve().parents[1] / "skills")
    selection = select_skills(settings, registry, "Synthesize chunks", ["recursive-synthesis"])

    async def emit(_run_id, _event):
        pass

    telemetry = TurnTelemetry()
    result = await run_workspace_task(
        settings,
        task="Synthesize chunks",
        files_manifest=[],
        memories=[],
        interpreter_factory=make_interpreter_factory(
            _LocalSandbox(tmp_path), settings, asyncio.get_running_loop()
        ),
        run_id="partial-batch",
        emit=emit,
        selection=selection,
        telemetry=telemetry,
        model_override=root_lm,
        sub_lm=PartiallyFailingLM([]),
    )
    assert result.answer == "29"
    assert telemetry.sub_lm_queries == 3


@pytest.mark.asyncio
async def test_two_pinned_skills_have_complete_instructions_in_first_prompt(settings, tmp_path):
    registry = SkillRegistry.load(tmp_path)
    middle_rule = "Check the synthetic label CEDAR-47 / café."
    second_rule = "Verify the row labeled ALDER-29."
    for name, body in (
        ("first-review", "Inspect the schema.\n" * 30 + middle_rule + "\nCheck edge cases." * 12),
        ("second-review", "Corroborate the totals.\n" * 24 + second_rule),
    ):
        registry.install(
            tmp_path, f"---\nname: {name}\ndescription: Review the output.\n---\n{body}\n".encode()
        )
    selection = select_skills(
        settings,
        registry.with_installed(tmp_path),
        "Review output",
        ["first-review", "second-review"],
    )
    expected = "\n\n".join(f"## {skill.name}\n{skill.body}" for skill in selection.active_skills)
    assert middle_rule not in expected[:500] + expected[-500:]
    root_lm = DummyLM(
        [
            {
                "plan": "Use both checks.",
                "code": (
                    "assert type(active_skills) is str\n"
                    f"assert active_skills == {expected!r}\n"
                    "SUBMIT('CEDAR-47 and ALDER-29', [])"
                ),
            }
        ]
    )

    async def emit(*_):
        pass

    result = await run_workspace_task(
        settings,
        task="Review output",
        files_manifest=[],
        memories=[],
        interpreter_factory=make_interpreter_factory(
            _LocalSandbox(tmp_path), settings, asyncio.get_running_loop()
        ),
        run_id="two-pinned-skills",
        emit=emit,
        selection=selection,
        model_override=root_lm,
        sub_lm=DummyLM([]),
    )
    prompt = json.dumps(root_lm.get_convo(0)[0], ensure_ascii=False)
    assert middle_rule in prompt and second_rule in prompt
    assert result.answer == "CEDAR-47 and ALDER-29"
    assert json.loads(result.history.model_dump_json())["messages"][0]["active_skills"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("pins", [None, ["short-review", "long-review"]])
async def test_long_installed_skill_is_readable_past_preview(settings, tmp_path, monkeypatch, pins):
    registry = SkillRegistry.load(tmp_path)
    rule = "Return the synthetic code GROVE-27."
    padding = "Verify each output against the fixture. " * 55
    registry.install(
        tmp_path,
        (
            "---\nname: long-review\ndescription: Review synthetic output\n---\n"
            f"{padding}\n{rule}\n{padding.strip()}\n"
        ).encode(),
    )
    if pins:
        registry.install(
            tmp_path,
            b"---\nname: short-review\ndescription: Format the audit.\n---\n"
            b"Use the audit code only.\n",
        )
    with monkeypatch.context() as scoped:
        scoped.setattr(
            dspy.Predict,
            "forward",
            lambda *a, **kw: SimpleNamespace(
                needs_skill=SimpleNamespace(value=True, probability=1.0)
            ),
        )
        selection = select_skills(
            settings, registry.with_installed(tmp_path), "Review output", pins
        )
    expected = "\n\n".join(f"## {skill.name}\n{skill.body}" for skill in selection.active_skills)
    rule_offset = expected.index(rule)
    factory = make_interpreter_factory(
        _LocalSandbox(tmp_path), settings, asyncio.get_running_loop()
    )
    root_lm = DummyLM(
        [
            {
                "plan": "Read the omitted instructions.",
                "code": f"print(active_skills[{rule_offset}:{rule_offset + len(rule)}])",
            },
            {"plan": "Follow the revealed rule.", "code": "SUBMIT('GROVE-27', [])"},
        ]
    )

    async def emit(_run_id, _event):
        pass

    result = await run_workspace_task(
        settings,
        task="Review output",
        files_manifest=[],
        memories=[],
        interpreter_factory=factory,
        run_id="long-installed-skill",
        emit=emit,
        selection=selection,
        model_override=root_lm,
        sub_lm=DummyLM([]),
    )
    first_prompt = json.dumps(root_lm.get_convo(0)[0])
    assert (
        f"Omitted. Read active_skills[{expected.index('## long-review')}:{len(expected)}]"
        in first_prompt
    )
    if pins:
        assert "Use the audit code only." in first_prompt
    assert rule not in first_prompt
    assert rule in json.dumps(root_lm.get_convo(1)[0])
    assert result.answer == "GROVE-27"


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


@pytest.mark.parametrize(
    "content,reason",
    [
        ("---\nname: broken\ndescription: [\n---\nBody", "YAML"),
        ("---\nname: bad--name\ndescription: Desc\n---\nBody", "kebab-case"),
        ("---\nname: broken\ndescription: Desc\n---\n", "instructions"),
        ("---\nname: broken\ndescription: " + "x" * 1025 + "\n---\nBody", "1024"),
        ("x" * 65537, "64 KiB"),
    ],
)
def test_skill_validation_on_load_and_install(tmp_path, monkeypatch, content, reason):
    monkeypatch.setattr(skills, "_bundled_skills_dir", lambda: tmp_path)
    registry = SkillRegistry.load(tmp_path)
    path = tmp_path / "broken" / "SKILL.md"
    path.parent.mkdir()
    path.write_text(content)
    with pytest.raises(ValueError, match=reason):
        SkillRegistry.load(tmp_path)
    with pytest.raises(ValueError, match=reason):
        registry.install(tmp_path, content.encode())


def test_skill_directory_and_legacy_description(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled_skills"
    bundled.mkdir()
    monkeypatch.setattr(skills, "_bundled_skills_dir", lambda: bundled)
    path = _skill(bundled, "old")
    path.write_text(path.read_text().replace("name: old", "name: another"))
    with pytest.raises(ValueError, match="directory"):
        SkillRegistry.load(bundled)

    configured = tmp_path / "configured"
    configured.mkdir()
    custom = _skill(configured, "old-folder")
    custom.write_text(
        custom.read_text()
        .replace("name: old-folder", "name: old_skill")
        .replace("Analyze CSV data", "L" * 1025)
    )
    installed = configured / ".installed" / "another.md"
    installed.parent.mkdir()
    installed.write_text(path.read_text().replace("Analyze CSV data", "L" * 1025))
    catalog = SkillRegistry.load(configured, fallback_to_bundled=True).with_installed(configured)
    assert [skill.name for skill in catalog.skills] == ["another", "old_skill"]
    for skill in catalog.skills:
        assert len(skill.description) == 1025
        assert skill.allowed_tools == ("inspect_csv", "retrieve")


def test_persistent_install_directory_retains_bundled_skills(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled_skills"
    bundled.mkdir()
    _skill(bundled, "web-research")
    monkeypatch.setattr(skills, "_bundled_skills_dir", lambda: bundled)
    configured = tmp_path / "persistent"
    registry = SkillRegistry.load(configured, fallback_to_bundled=True)
    registry.install(configured, b"---\nname: custom\ndescription: Custom skill\n---\nRead it.")
    reloaded = SkillRegistry.load(configured, fallback_to_bundled=True)
    assert [skill.name for skill in reloaded.with_installed(configured).skills] == [
        "custom",
        "web-research",
    ]


def test_malformed_installed_skill_is_skipped(tmp_path, monkeypatch, caplog):
    bundled = tmp_path / "bundled_skills"
    bundled.mkdir()
    _skill(bundled, "web-research")
    monkeypatch.setattr(skills, "_bundled_skills_dir", lambda: bundled)
    configured = tmp_path / "persistent"
    registry = SkillRegistry.load(configured, fallback_to_bundled=True)
    registry.install(configured, b"---\nname: custom\ndescription: Custom skill\n---\nRead it.")
    (configured / ".installed" / "broken.md").write_text("no frontmatter")
    (configured / ".installed" / "binary.md").write_bytes(b"\xff\xfe")

    with caplog.at_level("WARNING", logger="fleet_rlm.skills"):
        catalog = registry.with_installed(configured)

    assert [skill.name for skill in catalog.skills] == ["custom", "web-research"]
    assert "broken.md" in caplog.text
    assert "binary.md" in caplog.text


def test_direct_public_url_requires_web_and_routes_the_requested_deliverable(settings, monkeypatch):
    from pydantic import SecretStr

    registry = SkillRegistry.load(Path(__file__).resolve().parents[1] / "skills")

    def decide(self, *, task, lm):
        choices = self.signature.output_fields["primary_skill"].annotation.criteria()
        assert "web-research" not in choices
        assert "plot" in task or "summarize" in task
        return SimpleNamespace(
            needs_skill=SimpleNamespace(value="plot" in task),
            primary_skill=SimpleNamespace(
                value="chart-visualization", probabilities={"chart-visualization": 0.9}
            ),
        )

    monkeypatch.setattr(dspy.Predict, "forward", decide)
    prompt = "Read https://example.org and summarize what it says."
    assert not select_skills(
        settings.model_copy(update={"tavily_api_key": None}), registry, prompt
    ).active_skills
    settings = settings.model_copy(update={"tavily_api_key": SecretStr("mock")})
    assert select_skills(settings, registry, prompt).allowed_tools == ("web_search", "web_open")
    plain = select_skills(settings, registry, "Open https://example.org/report")
    assert [skill.name for skill in plain.active_skills] == ["web-research"]
    compound = select_skills(settings, registry, "Read https://example.org/report and plot revenue")
    assert [skill.name for skill in compound.active_skills] == [
        "web-research",
        "chart-visualization",
    ]
    assert compound.allowed_tools == ("web_search", "web_open")


def test_skill_selector_resolves_a_followup_and_keeps_current_guidance(
    settings, monkeypatch, tmp_path
):
    from pydantic import SecretStr

    registry = SkillRegistry.load(Path(__file__).resolve().parents[1] / "skills")
    state = build_skill_selector(registry.skills).dump_state()
    state["signature"]["instructions"] = "Treat short follow-ups as irrelevant."
    path = tmp_path / "selector.json"
    path.write_text(json.dumps(state))
    settings = settings.model_copy(
        update={"tavily_api_key": SecretStr("offline"), "skill_selector_path": path}
    )
    prompt = "Who won in Georgia?"
    earlier = [{"user": "Search for U.S. midterm results", "assistant": "Details pending."}]

    def decide(self, *, task, lm):
        assert "Treat short follow-ups as irrelevant" not in self.signature.instructions
        assert "previous exchange" in self.signature.instructions
        needs = task.startswith("{") and json.loads(task) == {
            "current_request": prompt,
            "previous_user_request": earlier[0]["user"],
            "previous_assistant_answer": earlier[0]["assistant"],
        }
        return SimpleNamespace(
            needs_skill=SimpleNamespace(value=needs),
            primary_skill=SimpleNamespace(value="web-research", probabilities={"web-research": 1}),
        )

    monkeypatch.setattr(dspy.Predict, "forward", decide)
    assert not select_skills(settings, registry, prompt).active_skills
    assert select_skills(settings, registry, prompt, prior_turns=earlier).allowed_tools
    assert not select_skills(settings, registry, "Hi", prior_turns=earlier).active_skills
    assert not select_skills(
        settings, registry, "Explain addition", prior_turns=earlier
    ).active_skills
    disabled = settings.model_copy(update={"history_max_turns": 0})
    assert not select_skills(disabled, registry, prompt, prior_turns=earlier).active_skills


@pytest.mark.asyncio
async def test_turn_telemetry_detects_real_iteration_limit(settings, tmp_path):
    telemetry = TurnTelemetry()
    model = DummyLM(
        [
            {"plan": "Inspect", "code": "print('available evidence')"},
            {"answer": "Partial", "artifact_paths": []},
        ]
    )

    async def emit(*_):
        pass

    result = await run_workspace_task(
        settings.model_copy(update={"rlm_max_iters": 1, "history_max_chars": 0}),
        task="Inspect evidence",
        files_manifest=[],
        memories=[],
        interpreter_factory=make_interpreter_factory(
            _LocalSandbox(tmp_path), settings, asyncio.get_running_loop()
        ),
        run_id="extract",
        emit=emit,
        model_override=model,
        sub_lm=model,
        telemetry=telemetry,
    )
    assert result.answer == "Partial" and result.history.messages == []
    assert telemetry.completion == "iteration_limit"
    assert telemetry.root_actions == 1 and telemetry.elapsed_ms is not None


def test_build_program_syncs_dynamic_tool_docs_on_optimized_state_load(settings, tmp_path):
    def spawn_agent(task: str) -> dict:
        """Delegate a workspace subtask."""
        return {"answer": task, "artifact_paths": []}

    def web_search(query: str) -> dict:
        """Search public web sources."""
        return {"results": [query]}

    def stale_tool(value: str) -> str:
        """Legacy compiled-only tool."""
        return value

    # strip_dynamic_tool_docs keeps plain instructions intact, strips tool docs at EOF
    # byte-for-byte, and preserves trailing guidance/GEPA text after the tool-doc block.
    assert strip_dynamic_tool_docs("Plain optimized prompt") == "Plain optimized prompt"
    with_docs = f"Plain optimized prompt{_TOOL_DOCS_HEADER}\n- `stale_tool(value: string)` - Old."
    assert strip_dynamic_tool_docs(with_docs) == "Plain optimized prompt"
    trailing_gepa = "GEPA instruction: verify units.\n- keep trailing markdown bullets"
    with_trailing = f"{with_docs}\n\n{_SUBMISSION_GUIDANCE}\n\n{trailing_gepa}"
    stripped_with_trailing = strip_dynamic_tool_docs(with_trailing)
    assert stripped_with_trailing == (
        f"Plain optimized prompt\n\n{_SUBMISSION_GUIDANCE}\n\n{trailing_gepa}"
    )
    assert _TOOL_DOCS_HEADER not in stripped_with_trailing
    assert "stale_tool" not in stripped_with_trailing

    # Artifact compiled with tools=[] preserves exact instructions when loaded with tools=[]
    # and appends current-turn tool docs when loaded with active tools.
    no_tools_program = build_program(settings, [])
    no_tools_program.generate_action.signature = (
        no_tools_program.generate_action.signature.with_instructions("Compiled base prompt")
    )
    no_tools_state = no_tools_program.dump_state()

    restored_no_tools = build_program(settings, [], optimized_state=no_tools_state)
    assert restored_no_tools.generate_action.signature.instructions == "Compiled base prompt"
    assert all(predictor.lm is None for predictor in restored_no_tools.predictors())

    restored_with_tools = build_program(
        settings, [spawn_agent, web_search], optimized_state=no_tools_state
    )
    with_tools_instructions = restored_with_tools.generate_action.signature.instructions
    assert with_tools_instructions.startswith("Compiled base prompt" + _TOOL_DOCS_HEADER)
    assert "spawn_agent(task: string)" in with_tools_instructions
    assert "web_search(query: string)" in with_tools_instructions
    assert all(predictor.lm is None for predictor in restored_with_tools.predictors())

    # Artifact compiled with stale tools strips stale tool docs and re-appends only current tools.
    stale_program = build_program(settings, [stale_tool, web_search])
    stale_base = strip_dynamic_tool_docs(stale_program.generate_action.signature.instructions)
    stale_path = tmp_path / "stale_optimized.json"
    stale_program.save(stale_path)
    stale_state = json.loads(stale_path.read_text(encoding="utf-8"))

    refreshed = build_program(settings, [spawn_agent], optimized_state=stale_state)
    refreshed_instructions = refreshed.generate_action.signature.instructions
    assert refreshed_instructions.count(_TOOL_DOCS_HEADER) == 1
    assert "spawn_agent(task: string)" in refreshed_instructions
    assert "stale_tool" not in refreshed_instructions
    assert "web_search" not in refreshed_instructions
    assert strip_dynamic_tool_docs(refreshed_instructions) == stale_base

    # Saved state with stale tool docs followed by GEPA trailing text preserves the trailing text
    # and replaces stale tool docs with current runtime tools in canonical order.
    mid_block_program = build_program(settings, [stale_tool])
    mid_block_program.generate_action.signature = (
        mid_block_program.generate_action.signature.append_instructions(
            f"{_SUBMISSION_GUIDANCE}\n\n{trailing_gepa}"
        )
    )
    mid_block_state = mid_block_program.dump_state()
    mid_restored = build_program(settings, [spawn_agent], optimized_state=mid_block_state)
    mid_instructions = mid_restored.generate_action.signature.instructions
    assert "stale_tool" not in mid_instructions
    assert _SUBMISSION_GUIDANCE.strip() in mid_instructions
    assert trailing_gepa in mid_instructions
    assert "spawn_agent(task: string)" in mid_instructions
    assert mid_instructions.index(trailing_gepa) < mid_instructions.index(_TOOL_DOCS_HEADER)

    # Disk-loaded artifact with tools=[] strips stale tool docs completely.
    disk_stripped = build_program(
        settings.model_copy(update={"optimized_program_path": stale_path}), []
    )
    assert disk_stripped.generate_action.signature.instructions == stale_base
    assert _TOOL_DOCS_HEADER not in disk_stripped.generate_action.signature.instructions


@pytest.mark.asyncio
async def test_apply_submission_guidance_is_idempotent(settings, tmp_path):
    def spawn_agent(task: str) -> dict:
        """Delegate a workspace subtask."""
        return {"answer": task, "artifact_paths": []}

    program = build_program(settings, [])
    apply_submission_guidance(program)
    once = program.generate_action.signature.instructions
    assert once.count(_SUBMISSION_GUIDANCE.strip()) == 1

    apply_submission_guidance(program)
    assert program.generate_action.signature.instructions == once
    assert program.generate_action.signature.instructions.count(_SUBMISSION_GUIDANCE.strip()) == 1

    # Canonical ordering when tools are present: _SUBMISSION_GUIDANCE comes before tool docs,
    # survives strip_dynamic_tool_docs(), and round-trips identically to unoptimized runtime.
    with_tools = build_program(settings, [spawn_agent])
    apply_submission_guidance(with_tools)
    with_tools_instructions = with_tools.generate_action.signature.instructions
    assert with_tools_instructions.index(_SUBMISSION_GUIDANCE.strip()) < (
        with_tools_instructions.index(_TOOL_DOCS_HEADER)
    )
    apply_submission_guidance(with_tools)
    assert with_tools.generate_action.signature.instructions == with_tools_instructions

    stripped_for_save = strip_dynamic_tool_docs(with_tools_instructions)
    assert _SUBMISSION_GUIDANCE.strip() in stripped_for_save
    assert _TOOL_DOCS_HEADER not in stripped_for_save

    saved_for_runtime = build_program(settings, [spawn_agent])
    saved_for_runtime.generate_action.signature = (
        saved_for_runtime.generate_action.signature.with_instructions(stripped_for_save)
    )
    reloaded_with_tools = build_program(
        settings, [spawn_agent], optimized_state=saved_for_runtime.dump_state()
    )
    apply_submission_guidance(reloaded_with_tools)
    assert reloaded_with_tools.generate_action.signature.instructions == with_tools_instructions

    # Saved state that already includes _SUBMISSION_GUIDANCE is not duplicated in run_workspace_task
    # and still receives runtime spawn_agent tool docs.
    state = program.dump_state()
    root_lm = DummyLM([{"plan": "Submit", "code": "SUBMIT('done', [])"}])
    sub_lm = DummyLM([{"text": "unused"}])

    async def emit(*_):
        pass

    prediction = await run_workspace_task(
        settings,
        task="Return done",
        files_manifest=[],
        memories=[],
        interpreter_factory=make_interpreter_factory(
            _LocalSandbox(tmp_path), settings, asyncio.get_running_loop()
        ),
        run_id="idempotent-guidance",
        emit=emit,
        model_override=root_lm,
        sub_lm=sub_lm,
        optimized_state=state,
    )
    assert prediction.answer == "done"
    prompt = json.dumps(root_lm.get_convo(0)[0])
    assert prompt.count("On the penultimate") == 1
    assert "spawn_agent(task: string)" in prompt


def test_format_selector_task_matches_single_and_multi_turn_payloads():
    assert DEFAULT_SELECTION_THRESHOLD == 0.25

    assert format_selector_task("Summarize CSV") == ("Summarize CSV", "")

    prior_turns = [
        {"user": "Old turn", "assistant": "Old answer"},
        {"user": "Café " + ("u" * 1200), "assistant": "Résumé " + ("a" * 1200)},
    ]
    assert format_selector_task("Who won in Georgia?", prior_turns, include_context=False) == (
        "Who won in Georgia?",
        "",
    )

    selector_task, context = format_selector_task("Who won in Georgia?", prior_turns)
    expected_user = prior_turns[-1]["user"][:1000]
    expected_assistant = prior_turns[-1]["assistant"][:1000]
    assert context == f"{expected_user}\n{expected_assistant}"
    assert "Café" in selector_task and "Résumé" in selector_task
    assert json.loads(selector_task) == {
        "current_request": "Who won in Georgia?",
        "previous_user_request": expected_user,
        "previous_assistant_answer": expected_assistant,
    }


@pytest.mark.asyncio
async def test_stage_workspace_bytes_and_collect_workspace_artifacts(tmp_path, monkeypatch):
    class _StageableLocalSandbox(_LocalSandbox):
        async def upload_file(self, source, destination, *, atomic=False):
            assert atomic
            dest = Path(destination)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(Path(source).read_bytes())

        async def download_file(self, source, destination):
            Path(destination).write_bytes(Path(source).read_bytes())

        async def run(self, *, argv, timeout, execution_timeout):
            if argv[:2] == ["mkdir", "-p"]:
                for directory in argv[2:]:
                    Path(directory).mkdir(parents=True, exist_ok=True)
                return SimpleNamespace(exit_code=0, stdout="")
            return await super().run(
                argv=argv, timeout=timeout, execution_timeout=execution_timeout
            )

    sandbox = _StageableLocalSandbox(tmp_path)
    monkeypatch.setattr("fleet_rlm.interpreter.WORKSPACE", str(tmp_path))
    monkeypatch.setattr("fleet_rlm.interpreter.ARTIFACTS", str(tmp_path / "artifacts"))

    # Pre-populate stale files to verify reset_managed_workspace clears them.
    (tmp_path / "files").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "files" / "stale.txt").write_bytes(b"stale")
    (tmp_path / "artifacts" / "old.txt").write_bytes(b"old")

    # Invalid paths and normalized duplicates are rejected before resetting workspace.
    for bad_files in (
        {"": b"x"},
        {"/etc/passwd": b"x"},
        {"../escape.txt": b"x"},
        {"files/../escape.txt": b"x"},
        {"files/": b"x"},
        {"files//double.csv": b"x"},
        {"files\\win.csv": b"x"},
        {"./dot.csv": b"x"},
        {"dup.csv": b"1", "files/dup.csv": b"2"},
    ):
        with pytest.raises(ValueError, match="workspace input path"):
            await stage_workspace_bytes(sandbox, bad_files, timeout=20)
    assert (tmp_path / "files" / "stale.txt").exists()

    csv_bytes = b"k,v\n1,2\n"
    txt_bytes = b"hello world"
    manifest = await stage_workspace_bytes(
        sandbox,
        {"files/nested/a.txt": txt_bytes, "b.csv": csv_bytes},
        timeout=20,
    )
    assert not (tmp_path / "files" / "stale.txt").exists()
    assert not (tmp_path / "artifacts" / "old.txt").exists()
    assert (tmp_path / "files" / "b.csv").read_bytes() == csv_bytes
    assert (tmp_path / "files" / "nested" / "a.txt").read_bytes() == txt_bytes
    assert manifest == [
        {
            "path": "files/b.csv",
            "kind": "input",
            "sha256": hashlib.sha256(csv_bytes).hexdigest(),
            "size_bytes": len(csv_bytes),
        },
        {
            "path": "files/nested/a.txt",
            "kind": "input",
            "sha256": hashlib.sha256(txt_bytes).hexdigest(),
            "size_bytes": len(txt_bytes),
        },
    ]

    # Collect newly created artifacts; skip known unchanged, oversized, and symlink entries.
    artifacts_root = tmp_path / "artifacts"
    existing = artifacts_root / "existing.txt"
    existing.write_bytes(b"v1")
    report = artifacts_root / "report.json"
    report.write_bytes(b'{"ok": true}')
    nested = artifacts_root / "sub" / "chart.svg"
    nested.parent.mkdir()
    nested.write_bytes(b"<svg/>")
    oversized = artifacts_root / "huge.bin"
    oversized.write_bytes(b"x" * 64)
    (artifacts_root / "linked.json").symlink_to(report)

    known_files = {
        "artifacts/existing.txt": (hashlib.sha256(b"v1").hexdigest(), 2),
        "files/b.csv": (hashlib.sha256(csv_bytes).hexdigest(), len(csv_bytes)),
    }
    collected = await collect_workspace_artifacts(sandbox, known_files, timeout=20, max_bytes=32)
    assert collected == {
        "artifacts/report.json": b'{"ok": true}',
        "artifacts/sub/chart.svg": b"<svg/>",
    }

    # Overwriting a known artifact causes it to be collected on the next scan.
    existing.write_bytes(b"v2-updated")
    collected_after_update = await collect_workspace_artifacts(
        sandbox, known_files, timeout=20, max_bytes=32
    )
    assert collected_after_update["artifacts/existing.txt"] == b"v2-updated"

    # Staging empty dict resets /workspace and returns [].
    assert await stage_workspace_bytes(sandbox, {}, timeout=20) == []
    assert list((tmp_path / "files").iterdir()) == []
    assert list((tmp_path / "artifacts").iterdir()) == []
