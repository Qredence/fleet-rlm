from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_rlm.daytona_rlm.diagnostics import DaytonaDiagnosticError
from fleet_rlm.daytona_rlm.protocol import RunEventFrame, RunStartRequest
from fleet_rlm.daytona_rlm.protocol import RunErrorEnvelope, encode_frame
from fleet_rlm.daytona_rlm.runner import DaytonaRLMRunner, run_daytona_rlm_pilot
from fleet_rlm.daytona_rlm.sandbox_controller import (
    SelfOrchestratedNodeRuntime,
    _ChildRunPayload,
)
from fleet_rlm.daytona_rlm.types import (
    AgentNode,
    ChildLink,
    DaytonaRunResult,
    ExecutionObservation,
    FinalArtifact,
    PromptHandle,
    RecursiveTaskSpec,
    RolloutBudget,
    RolloutSummary,
    SandboxLmRuntimeConfig,
    TaskSourceProvenance,
)
from fleet_rlm.models import StreamEvent


class _FakeLmSequence:
    def __init__(self, responses: list[str]):
        self._responses = responses
        self.prompts: list[str] = []

    def __call__(self, prompt: str):
        self.prompts.append(prompt)
        if not self._responses:
            raise AssertionError("Unexpected extra LM call.")
        return [{"text": self._responses.pop(0)}]


def _code(body: str) -> str:
    return f"```python\n{body}\n```"


def _fake_runtime_config() -> SandboxLmRuntimeConfig:
    return SandboxLmRuntimeConfig(
        model="openai/gpt-5-mini",
        api_key="test-key",
        api_base="https://example.invalid/v1",
        max_tokens=4096,
    )


def _make_run_result(
    *,
    repo: str = "https://github.com/example/repo.git",
    ref: str | None = None,
    task: str = "analyze repo",
    final_value: object | None = None,
    finalization_mode: str = "SUBMIT",
    child_links: list[ChildLink] | None = None,
    prompt_handles: list[PromptHandle] | None = None,
    termination_reason: str = "completed",
    warnings: list[str] | None = None,
) -> DaytonaRunResult:
    root_id = "root-node"
    final_artifact = None
    if final_value is not None:
        final_artifact = FinalArtifact(
            kind="markdown",
            value=final_value,
            finalization_mode=finalization_mode,
        )
    child_links = child_links or []
    child_ids = [link.child_id or "" for link in child_links if link.child_id]
    nodes = {
        root_id: AgentNode(
            node_id=root_id,
            parent_id=None,
            depth=0,
            task=task,
            repo=repo,
            ref=ref,
            sandbox_id="sbx-root",
            workspace_path="/workspace/repo",
            status="completed" if final_artifact is not None else "running",
            prompt_handles=prompt_handles or [],
            prompt_previews=["prompt preview"],
            response_previews=["response preview"],
            observations=[
                ExecutionObservation(
                    iteration=1,
                    code="SUBMIT(summary='ok')",
                    duration_ms=15,
                    callback_count=len(child_links),
                )
            ],
            child_ids=child_ids,
            child_links=child_links,
            final_artifact=final_artifact,
            iteration_count=1,
        )
    }
    for index, link in enumerate(child_links, start=1):
        child_id = link.child_id or f"child-{index}"
        nodes[child_id] = AgentNode(
            node_id=child_id,
            parent_id=root_id,
            depth=1,
            task=link.task.task,
            repo=repo,
            ref=ref,
            sandbox_id=f"sbx-child-{index}",
            workspace_path="/workspace/repo",
            status="completed",
            final_artifact=FinalArtifact(
                kind="markdown",
                value={"summary": link.result_preview or "child summary"},
                finalization_mode="SUBMIT",
            ),
            iteration_count=1,
        )
    return DaytonaRunResult(
        run_id="run-123",
        repo=repo,
        ref=ref,
        task=task,
        budget=RolloutBudget(),
        root_id=root_id,
        nodes=nodes,
        final_artifact=final_artifact,
        summary=RolloutSummary(
            duration_ms=120,
            sandboxes_used=max(1, len(nodes)),
            termination_reason=termination_reason,
            warnings=warnings or [],
        ),
    )


class _FakeRunSession:
    def __init__(
        self,
        *,
        result: DaytonaRunResult | None = None,
        events: list[RunEventFrame] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.repo_path = "/workspace/repo"
        self.sandbox_id = "sbx-root"
        self.result = result
        self.events = events or []
        self.error = error
        self.closed_controller = False
        self.deleted = False
        self.requests: list[RunStartRequest] = []
        self.timeouts: list[float] = []
        self.cancel_values: list[bool] = []

    def run_rollout(self, *, request, timeout, event_handler=None, cancel_check=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if cancel_check is not None:
            self.cancel_values.append(bool(cancel_check()))
        for event in self.events:
            if event_handler is not None:
                event_handler(event)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result

    def close_controller(self) -> None:
        self.closed_controller = True

    def delete(self) -> None:
        self.deleted = True


class _FakeRuntime:
    def __init__(self, session: _FakeRunSession):
        self.session = session
        self.calls: list[tuple[str, str | None]] = []

    def create_repo_session(self, *, repo_url: str, ref: str | None):
        self.calls.append((repo_url, ref))
        return self.session


@pytest.fixture(autouse=True)
def _stub_lm_runtime_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "fleet_rlm.daytona_rlm.runner.resolve_daytona_lm_runtime_config",
        lambda: _fake_runtime_config(),
    )


def test_recursive_task_spec_normalizes_and_derives_source_id():
    task_spec = RecursiveTaskSpec.from_raw(
        {
            "task": "  inspect README  ",
            "label": "  README intro  ",
            "source": {
                "kind": "file_slice",
                "path": "README.md",
                "line": 1,
                "preview": "  # Example   Intro line  ",
                "unknown": "ignored",
            },
            "ignored": "field",
        }
    )

    assert task_spec.task == "inspect README"
    assert task_spec.label == "README intro"
    assert task_spec.source.kind == "file_slice"
    assert task_spec.source.start_line == 1
    assert task_spec.source.end_line == 1
    assert task_spec.source.preview == "# Example Intro line"
    assert task_spec.source.source_id is not None


def test_child_run_payload_serializes_slotted_budget() -> None:
    payload = _ChildRunPayload(
        run_id="run-1",
        node_id="node-1",
        parent_id="root",
        depth=1,
        repo="https://github.com/example/repo.git",
        ref="main",
        task="inspect README",
        budget=RolloutBudget(max_depth=3, batch_concurrency=6),
        lm_config=_fake_runtime_config(),
        remaining_sandboxes=4,
        deadline_epoch_s=time.time() + 60,
        sandbox_id="sbx-child",
    ).to_dict()

    assert payload["budget"] == {
        "max_sandboxes": 50,
        "max_depth": 3,
        "max_iterations": 50,
        "global_timeout": 3600,
        "result_truncation_limit": 10_000,
        "batch_concurrency": 6,
    }


def test_runner_bootstraps_self_orchestrated_rollout_and_persists_result(
    tmp_path: Path,
):
    child_link = ChildLink(
        child_id="child-1",
        callback_name="llm_query_batched",
        task=RecursiveTaskSpec(
            task="summarize README",
            label="README summary",
            source=TaskSourceProvenance(
                kind="file_slice",
                path="README.md",
                start_line=1,
                end_line=2,
            ),
        ),
        result_preview="README summary",
        status="completed",
    )
    result = _make_run_result(
        final_value={
            "summary": (
                "The Daytona workbench rollout completed successfully, preserved the "
                "child provenance for README analysis, and produced a readable "
                "synthesized report for the final artifact."
            )
        },
        child_links=[child_link],
        prompt_handles=[
            PromptHandle(
                handle_id="prompt-1",
                kind="task",
                label="root-task",
                path=".fleet-rlm/prompts/prompt-1.txt",
                char_count=5200,
                line_count=42,
                preview="Long task preview",
            )
        ],
    )
    session = _FakeRunSession(
        result=result,
        events=[
            RunEventFrame(
                request_id="req-1",
                kind="status",
                text="Bootstrapping sandbox",
                payload={"phase": "sandbox_create"},
            ),
            RunEventFrame(
                request_id="req-1",
                kind="tool_call",
                text="Spawning child",
                payload={"node_id": "root-node", "child_id": "child-1"},
            ),
            RunEventFrame(
                request_id="req-1",
                kind="mystery",
                text="Unknown frame should downgrade to status",
                payload={"phase": "unknown"},
            ),
        ],
    )
    runtime = _FakeRuntime(session)
    emitted: list[StreamEvent] = []
    runner = DaytonaRLMRunner(
        runtime=runtime,
        budget=RolloutBudget(max_depth=3, batch_concurrency=5),
        output_dir=tmp_path,
        event_callback=emitted.append,
    )

    returned = runner.run(
        repo="https://github.com/example/repo.git",
        ref="main",
        task="analyze repo",
    )

    assert returned.result_path is not None
    persisted = json.loads(Path(returned.result_path).read_text(encoding="utf-8"))
    assert (
        persisted["nodes"][returned.root_id]["child_links"][0]["task"]["task"]
        == "summarize README"
    )
    assert runtime.calls == [("https://github.com/example/repo.git", "main")]
    assert session.closed_controller is True
    assert session.deleted is True
    assert session.timeouts == [RolloutBudget().global_timeout + 120.0]

    request = session.requests[0]
    assert request.payload["repo"] == "https://github.com/example/repo.git"
    assert request.payload["ref"] == "main"
    assert request.payload["repo_path"] == session.repo_path
    assert request.payload["sandbox_id"] == session.sandbox_id
    assert request.payload["budget"] == {
        "max_sandboxes": 50,
        "max_depth": 3,
        "max_iterations": 50,
        "global_timeout": 3600,
        "result_truncation_limit": 10000,
        "batch_concurrency": 5,
    }
    assert request.payload["lm_config"]["model"] == "openai/gpt-5-mini"
    assert request.payload["remaining_sandboxes"] == 50
    assert request.payload["deadline_epoch_s"] > time.time()

    assert [event.kind for event in emitted] == ["status", "tool_call", "status"]


def test_runner_applies_root_synthesis_guard_to_raw_intermediate_output(tmp_path: Path):
    result = _make_run_result(
        final_value=["/workspace/repo/README.md", "/workspace/repo/pyproject.toml"],
        finalization_mode="SUBMIT",
    )
    session = _FakeRunSession(result=result)
    runner = DaytonaRLMRunner(
        runtime=_FakeRuntime(session),
        output_dir=tmp_path,
    )

    returned = runner.run(
        repo="https://github.com/example/repo.git",
        task="list files",
    )

    assert returned.final_artifact is not None
    assert returned.final_artifact.finalization_mode == "error"
    assert "raw intermediate data" in str(returned.final_artifact.value)
    assert returned.summary.termination_reason == "invalid_final_artifact"
    root = returned.nodes[returned.root_id]
    assert root.status == "error"
    assert root.error is not None


def test_runner_preserves_readable_structured_root_output(tmp_path: Path):
    readable_summary = (
        "The final artifact remains valid because it includes a human-readable "
        "summary that explains the repository findings in complete prose rather "
        "than dumping raw helper output back to the user."
    )
    result = _make_run_result(
        final_value={"summary": readable_summary, "details": {"files": 2}},
        finalization_mode="FINAL_VAR",
    )
    session = _FakeRunSession(result=result)
    runner = DaytonaRLMRunner(
        runtime=_FakeRuntime(session),
        output_dir=tmp_path,
    )

    returned = runner.run(
        repo="https://github.com/example/repo.git",
        task="summarize repo",
    )

    assert returned.final_artifact is not None
    assert returned.final_artifact.finalization_mode == "FINAL_VAR"
    assert returned.final_artifact.value["summary"] == readable_summary
    assert returned.summary.termination_reason == "completed"


def test_runner_preserves_cancelled_result_with_warnings(tmp_path: Path):
    result = _make_run_result(
        final_value=None,
        termination_reason="cancelled",
        warnings=["Failed to terminate descendant sandbox sbx-child-2 cleanly."],
    )
    session = _FakeRunSession(result=result)
    runner = DaytonaRLMRunner(
        runtime=_FakeRuntime(session),
        output_dir=tmp_path,
    )

    returned = runner.run(
        repo="https://github.com/example/repo.git",
        task="cancel repo analysis",
    )

    assert returned.summary.termination_reason == "cancelled"
    assert returned.summary.warnings == [
        "Failed to terminate descendant sandbox sbx-child-2 cleanly."
    ]
    assert returned.final_artifact is None


def test_runner_passes_cancel_check_through_to_self_orchestrated_session(
    tmp_path: Path,
):
    result = _make_run_result(
        final_value={
            "summary": (
                "This synthesized answer exists only to let the thin host adapter "
                "complete successfully while we assert that cancellation state was "
                "passed through to the sandbox runtime."
            )
        }
    )
    session = _FakeRunSession(result=result)
    runner = DaytonaRLMRunner(
        runtime=_FakeRuntime(session),
        output_dir=tmp_path,
        cancel_check=lambda: True,
    )

    _ = runner.run(
        repo="https://github.com/example/repo.git",
        task="check cancellation",
    )

    assert session.cancel_values == [True]


def test_child_run_payload_serializes_budget_as_plain_dict():
    payload = _ChildRunPayload(
        run_id="run-1",
        node_id="child-1",
        parent_id="root-1",
        depth=1,
        repo="https://github.com/example/repo.git",
        ref="main",
        task="inspect child",
        budget=RolloutBudget(max_depth=3, batch_concurrency=6),
        lm_config=_fake_runtime_config(),
        remaining_sandboxes=7,
        deadline_epoch_s=time.time() + 60,
        sandbox_id="sbx-child",
    )

    assert payload.to_dict()["budget"] == {
        "max_sandboxes": 50,
        "max_depth": 3,
        "max_iterations": 50,
        "global_timeout": 3600,
        "result_truncation_limit": 10000,
        "batch_concurrency": 6,
    }


def test_runner_cleans_up_session_when_runtime_raises(tmp_path: Path):
    session = _FakeRunSession(
        error=DaytonaDiagnosticError(
            "Daytona runtime failed",
            category="runtime_error",
            phase="run",
        )
    )
    runner = DaytonaRLMRunner(
        runtime=_FakeRuntime(session),
        output_dir=tmp_path,
    )

    with pytest.raises(DaytonaDiagnosticError, match="Daytona runtime failed"):
        runner.run(
            repo="https://github.com/example/repo.git",
            task="broken run",
        )

    assert session.closed_controller is True
    assert session.deleted is True


def test_self_orchestrated_runtime_deletes_child_sandbox_on_child_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.setattr(
        "fleet_rlm.daytona_rlm.sandbox_controller.dspy.LM",
        lambda *args, **kwargs: object(),
    )

    class _FakeChildSandbox:
        def __init__(self) -> None:
            self.id = "sbx-child"
            self.deleted = False
            self._stdout = encode_frame(
                RunErrorEnvelope(
                    request_id="child-1",
                    error="child failed",
                ).to_dict()
            )
            self._command_id = "cmd-1"
            self.fs = SimpleNamespace(
                create_folder=lambda path, mode: None,
                upload_file=lambda content, remote_path: None,
            )
            self.process = SimpleNamespace(
                create_session=lambda session_id: None,
                execute_session_command=lambda session_id,
                request,
                timeout=None: SimpleNamespace(cmd_id=self._command_id),
                get_session_command_logs=lambda session_id, command_id: SimpleNamespace(
                    stdout=self._stdout + "\n",
                    stderr="",
                ),
                delete_session=lambda session_id: None,
            )

        def delete(self) -> None:
            self.deleted = True

        def get_work_dir(self) -> str:
            return "/workspace"

    fake_child_sandbox = _FakeChildSandbox()
    runtime = SelfOrchestratedNodeRuntime(
        request_id="req-1",
        run_id="run-1",
        node_id="root-node",
        parent_id=None,
        depth=0,
        repo="https://github.com/example/repo.git",
        ref="main",
        task="inspect repo",
        repo_path=str(repo_path),
        sandbox_id="sbx-root",
        budget=RolloutBudget(),
        lm_config=_fake_runtime_config(),
        remaining_sandboxes=10,
        deadline_epoch_s=time.time() + 60,
        emit_event=lambda *_args, **_kwargs: None,
        cancel_check=lambda: False,
    )
    runtime._client = SimpleNamespace(create=lambda: fake_child_sandbox)
    monkeypatch.setattr(
        runtime,
        "_clone_repo",
        lambda *, sandbox, repo_url, ref, repo_path: None,
    )
    monkeypatch.setattr(
        runtime,
        "_remaining_timeout",
        lambda: 60.0,
    )
    root_node = AgentNode(
        node_id="root-node",
        parent_id=None,
        depth=0,
        task="inspect repo",
        repo="https://github.com/example/repo.git",
        ref="main",
        sandbox_id="sbx-root",
        workspace_path=str(repo_path),
    )

    with pytest.raises(RuntimeError, match="child failed"):
        runtime._spawn_child_task(
            node=root_node,
            task_spec=RecursiveTaskSpec(task="inspect child"),
        )

    assert fake_child_sandbox.deleted is True


def test_run_daytona_rlm_pilot_uses_thin_runner_and_persists_result(tmp_path: Path):
    result = _make_run_result(
        final_value={
            "summary": (
                "The persisted rollout proves the thin host adapter delegated the "
                "actual orchestration to the sandbox runtime and then wrote the "
                "typed Daytona result tree to disk."
            )
        }
    )
    runtime = _FakeRuntime(_FakeRunSession(result=result))

    returned = run_daytona_rlm_pilot(
        repo="https://github.com/example/repo.git",
        task="persist run",
        runtime=runtime,
        output_dir=tmp_path,
    )

    assert returned.result_path is not None
    payload = json.loads(Path(returned.result_path).read_text(encoding="utf-8"))
    assert payload["root_id"] == returned.root_id
    assert payload["summary"]["termination_reason"] == "completed"
    assert payload["final_artifact"]["value"]["summary"].startswith(
        "The persisted rollout proves"
    )


def test_self_orchestrated_runtime_externalizes_long_task_before_first_lm_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "README.md").write_text("# Example\n", encoding="utf-8")
    long_task = "Summarize this repository in depth. " * 250
    fake_lm = _FakeLmSequence(
        [
            _code(
                'summary = "This readable synthesized answer confirms that the '
                "Daytona node externalized the oversized task into prompt-object "
                "metadata before planning and then finalized through SUBMIT for "
                'the user-facing report."\n'
                "SUBMIT(summary=summary)"
            )
        ]
    )
    monkeypatch.setattr(
        "fleet_rlm.daytona_rlm.sandbox_controller.dspy.LM",
        lambda *args, **kwargs: fake_lm,
    )
    runtime = SelfOrchestratedNodeRuntime(
        request_id="req-1",
        run_id="run-1",
        node_id="root-node",
        parent_id=None,
        depth=0,
        repo="https://github.com/example/repo.git",
        ref=None,
        task=long_task,
        repo_path=str(repo_path),
        sandbox_id="sbx-root",
        budget=RolloutBudget(),
        lm_config=_fake_runtime_config(),
        remaining_sandboxes=10,
        deadline_epoch_s=time.time() + 60,
        emit_event=lambda *_args, **_kwargs: None,
        cancel_check=lambda: False,
    )

    result = runtime.run()

    assert fake_lm.prompts
    first_prompt = fake_lm.prompts[0]
    assert long_task not in first_prompt
    assert "externalized as prompt handle" in first_prompt
    node = result.nodes[result.root_id]
    assert any(handle.kind == "task" for handle in node.prompt_handles)


def test_self_orchestrated_runtime_retries_root_after_raw_intermediate_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "README.md").write_text("# Example\nIntro line\n", encoding="utf-8")
    fake_lm = _FakeLmSequence(
        [
            _code('files = ["README.md", "pyproject.toml"]\nSUBMIT(output=files)'),
            _code(
                'summary = "The final answer now synthesizes the repository '
                "inspection by explaining that README and pyproject were the first "
                "useful artifacts discovered, and that a readable summary is "
                'required instead of raw file-path output."\n'
                "SUBMIT(summary=summary)"
            ),
        ]
    )
    monkeypatch.setattr(
        "fleet_rlm.daytona_rlm.sandbox_controller.dspy.LM",
        lambda *args, **kwargs: fake_lm,
    )
    runtime = SelfOrchestratedNodeRuntime(
        request_id="req-2",
        run_id="run-2",
        node_id="root-node",
        parent_id=None,
        depth=0,
        repo="https://github.com/example/repo.git",
        ref=None,
        task="inspect repo",
        repo_path=str(repo_path),
        sandbox_id="sbx-root",
        budget=RolloutBudget(max_iterations=3),
        lm_config=_fake_runtime_config(),
        remaining_sandboxes=10,
        deadline_epoch_s=time.time() + 60,
        emit_event=lambda *_args, **_kwargs: None,
        cancel_check=lambda: False,
    )

    result = runtime.run()

    assert result.final_artifact is not None
    assert "readable summary is required" in str(result.final_artifact.value)
    root = result.nodes[result.root_id]
    assert root.iteration_count == 2
    assert any(
        "raw intermediate data" in prompt.lower() for prompt in fake_lm.prompts[1:]
    )
