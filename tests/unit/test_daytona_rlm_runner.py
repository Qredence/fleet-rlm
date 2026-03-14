from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_rlm.daytona_rlm.protocol import (
    ExecutionEventFrame,
    ExecutionResponse,
    HostCallbackRequest,
)
from fleet_rlm.daytona_rlm.runner import DaytonaRLMRunner, run_daytona_rlm_pilot
from fleet_rlm.daytona_rlm.system_prompt import build_system_prompt
from fleet_rlm.daytona_rlm.types import ContextSource, RecursiveTaskSpec, RolloutBudget
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


def _response(
    *,
    final_value: object | None = None,
    error: str | None = None,
    callback_count: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> ExecutionResponse:
    final_artifact = None
    if final_value is not None:
        final_artifact = {
            "kind": "markdown",
            "value": final_value,
            "finalization_mode": "SUBMIT",
        }
    return ExecutionResponse(
        request_id=uuid.uuid4().hex,
        stdout=stdout,
        stderr=stderr,
        error=error,
        final_artifact=final_artifact,
        duration_ms=12,
        callback_count=callback_count,
    )


class _FakeStep:
    def __init__(
        self,
        *,
        response: ExecutionResponse | None = None,
        callbacks: list[HostCallbackRequest] | None = None,
        progress_frames: list[ExecutionEventFrame] | None = None,
    ) -> None:
        self.response = response or _response()
        self.callbacks = list(callbacks or [])
        self.progress_frames = list(progress_frames or [])


class _FakeRunSession:
    def __init__(
        self,
        *,
        steps: list[_FakeStep] | None = None,
        context_sources: list[ContextSource] | None = None,
    ) -> None:
        self.workspace_path = "/workspace/repo"
        self.sandbox_id = "sbx-root"
        self.context_sources = list(context_sources or [])
        self.steps = list(steps or [])
        self.reset_calls = 0
        self.close_driver_calls = 0
        self.deleted = False
        self.execute_calls: list[dict[str, object]] = []
        self.callback_responses: list[object] = []
        self.prompt_handles = []
        self.store_prompt_calls: list[dict[str, object]] = []

    def reset_for_new_call(self, *, timeout: float = 5.0) -> None:
        self.reset_calls += 1

    def close_driver(self, *, timeout: float = 5.0) -> None:
        self.close_driver_calls += 1

    def delete(self) -> None:
        self.deleted = True

    def execute_code(
        self,
        *,
        code: str,
        callback_handler,
        timeout: float,
        submit_schema=None,
        cancel_check=None,
        progress_handler=None,
    ) -> ExecutionResponse:
        self.execute_calls.append(
            {
                "code": code,
                "timeout": timeout,
                "submit_schema": submit_schema,
                "cancelled": bool(cancel_check())
                if cancel_check is not None
                else False,
            }
        )
        if not self.steps:
            raise AssertionError("Unexpected execute_code call.")
        step = self.steps.pop(0)
        if progress_handler is not None:
            for frame in step.progress_frames:
                progress_handler(frame)
        for request in step.callbacks:
            response = callback_handler(request)
            self.callback_responses.append(response)
            if not response.ok:
                return _response(error=response.error, callback_count=1)
        return step.response

    def store_prompt(
        self,
        *,
        text: str,
        kind: str = "manual",
        label: str | None = None,
        timeout: float = 30.0,
    ):
        self.store_prompt_calls.append(
            {"text": text, "kind": kind, "label": label, "timeout": timeout}
        )
        handle = {
            "handle_id": f"prompt-{len(self.store_prompt_calls)}",
            "kind": kind,
            "label": label,
            "path": f".fleet-rlm/prompts/prompt-{len(self.store_prompt_calls)}.txt",
            "char_count": len(text),
            "line_count": len(text.splitlines()),
            "preview": text[:120],
        }
        self.prompt_handles.append(handle)
        from fleet_rlm.daytona_rlm.types import PromptHandle

        return PromptHandle.from_raw(handle)

    def list_prompts(self, *, timeout: float = 30.0):
        from fleet_rlm.daytona_rlm.types import PromptManifest, PromptHandle

        return PromptManifest(
            handles=[PromptHandle.from_raw(item) for item in self.prompt_handles]
        )


class _FakeRuntime:
    def __init__(self, session: _FakeRunSession | list[_FakeRunSession]):
        sessions = session if isinstance(session, list) else [session]
        self._sessions = list(sessions)
        self.workspace_calls: list[tuple[str | None, str | None, list[str] | None]] = []

    def create_workspace_session(
        self,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None,
    ):
        self.workspace_calls.append((repo_url, ref, context_paths))
        if not self._sessions:
            raise AssertionError("Unexpected extra workspace session request.")
        return self._sessions.pop(0)


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


def test_system_prompt_documents_semantic_and_recursive_helpers() -> None:
    prompt = build_system_prompt(
        workspace_path="/workspace/repo",
        budget=RolloutBudget(),
    )

    assert "Use llm_query(...) and llm_query_batched(...)" in prompt
    assert "Use rlm_query(...) and rlm_query_batched(...)" in prompt
    assert "auto-decompose successful observations" in prompt


def test_daytona_agents_docs_describe_host_loop_contract() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    expected = (
        "Daytona intentionally uses a custom recursive host-loop runner plus "
        "`dspy.Predict`-backed grounding/decomposition/synthesis modules; do not "
        "treat it as a `dspy.RLM` wrapper."
    )

    assert expected in (repo_root / "AGENTS.md").read_text(encoding="utf-8")
    assert expected in (repo_root / "src/fleet_rlm/AGENTS.md").read_text(
        encoding="utf-8"
    )


def test_runner_executes_host_loop_and_persists_result(tmp_path: Path):
    summary = (
        "The Daytona host-loop completed successfully, kept orchestration on the "
        "host, and returned a readable synthesized answer via SUBMIT."
    )
    session = _FakeRunSession(
        steps=[_FakeStep(response=_response(final_value={"summary": summary}))]
    )
    runtime = _FakeRuntime(session)
    emitted: list[StreamEvent] = []
    runner = DaytonaRLMRunner(
        lm=_FakeLmSequence([_code(f"summary = {summary!r}\nSUBMIT(summary=summary)")]),
        runtime=runtime,
        output_dir=tmp_path,
        event_callback=emitted.append,
    )

    result = runner.run(
        repo="https://github.com/example/repo.git",
        ref="main",
        task="analyze repo",
    )

    assert result.result_path is not None
    assert result.final_artifact is not None
    assert result.final_artifact.value["summary"] == summary
    assert runtime.workspace_calls == [
        ("https://github.com/example/repo.git", "main", None)
    ]
    assert session.reset_calls == 1
    assert session.close_driver_calls == 1
    assert session.deleted is True
    assert session.execute_calls[0]["cancelled"] is False
    assert [event.kind for event in emitted if event.kind == "status"]


def test_runner_accepts_short_summary_as_final_answer(tmp_path: Path):
    summary = "Hello there, it is great to meet you!"
    session = _FakeRunSession(
        steps=[_FakeStep(response=_response(final_value={"summary": summary}))]
    )
    runner = DaytonaRLMRunner(
        lm=_FakeLmSequence([_code(f"summary = {summary!r}\nSUBMIT(summary=summary)")]),
        runtime=_FakeRuntime(session),
        output_dir=tmp_path,
    )

    result = runner.run(repo=None, task="Say hello in one sentence.")

    assert result.summary.termination_reason == "completed"
    assert result.final_artifact is not None
    assert result.final_artifact.value["summary"] == summary
    assert result.nodes[result.root_id].iteration_count == 1


def test_runner_accepts_short_final_markdown_as_final_answer(tmp_path: Path):
    final_markdown = "## Greeting\n\nHello there!"
    session = _FakeRunSession(
        steps=[
            _FakeStep(
                response=_response(final_value={"final_markdown": final_markdown})
            )
        ]
    )
    runner = DaytonaRLMRunner(
        lm=_FakeLmSequence(
            [
                _code(
                    f"final_markdown = {final_markdown!r}\n"
                    "SUBMIT(final_markdown=final_markdown)"
                )
            ]
        ),
        runtime=_FakeRuntime(session),
        output_dir=tmp_path,
    )

    result = runner.run(repo=None, task="Greet the user briefly.")

    assert result.summary.termination_reason == "completed"
    assert result.final_artifact is not None
    assert result.final_artifact.value["final_markdown"] == final_markdown
    assert result.nodes[result.root_id].iteration_count == 1


def test_runner_emits_iteration_progress_phases(tmp_path: Path):
    summary = (
        "The Daytona runner now emits richer progress phases before the final "
        "result is available to the workbench."
    )
    emitted: list[StreamEvent] = []
    session = _FakeRunSession(
        steps=[_FakeStep(response=_response(final_value={"summary": summary}))]
    )
    session.phase_timings_ms = {
        "sandbox_create": 21,
        "repo_clone": 8,
        "context_stage": 3,
    }
    runner = DaytonaRLMRunner(
        lm=_FakeLmSequence([_code(f"summary = {summary!r}\nSUBMIT(summary=summary)")]),
        runtime=_FakeRuntime(session),
        output_dir=tmp_path,
        event_callback=emitted.append,
    )

    runner.run(repo="https://github.com/example/repo.git", task="emit progress")

    status_events = [event for event in emitted if event.kind == "status"]
    phases = [event.payload.get("phase") for event in status_events]

    assert phases == [
        "node_start",
        "iteration",
        "prepare_prompt",
        "llm_invoke",
        "code_extract",
        "code_execute",
        "observation_build",
        "observation",
        "completed",
    ]
    progress_events = [
        event for event in status_events if event.payload.get("phase") != "node_start"
    ]
    assert all("elapsed_ms" in event.payload for event in progress_events)
    iteration_events = [
        event for event in progress_events if event.payload.get("phase") != "completed"
    ]
    assert all(event.payload.get("iteration") == 1 for event in iteration_events)
    observation_event = next(
        event for event in status_events if event.payload.get("phase") == "observation"
    )
    assert observation_event.payload.get("duration_ms") == 12
    assert observation_event.payload.get("callback_count") == 0
    assert (
        status_events[0].payload["runtime"]["phase_timings_ms"]
        == session.phase_timings_ms
    )


def test_runner_streams_sandbox_output_as_status_events(tmp_path: Path):
    summary = (
        "The Daytona runner surfaced sandbox stdout before the final answer was "
        "available."
    )
    emitted: list[StreamEvent] = []
    session = _FakeRunSession(
        steps=[
            _FakeStep(
                progress_frames=[
                    ExecutionEventFrame(
                        request_id="req-1",
                        stream="stdout",
                        text="loading repository metadata\n",
                    )
                ],
                response=_response(final_value={"summary": summary}),
            )
        ]
    )
    runner = DaytonaRLMRunner(
        lm=_FakeLmSequence([_code(f"summary = {summary!r}\nSUBMIT(summary=summary)")]),
        runtime=_FakeRuntime(session),
        output_dir=tmp_path,
        event_callback=emitted.append,
    )

    runner.run(repo="https://github.com/example/repo.git", task="emit logs")

    output_events = [
        event
        for event in emitted
        if event.kind == "status" and event.payload.get("phase") == "sandbox_output"
    ]
    assert len(output_events) == 1
    assert output_events[0].text == "Sandbox stdout: loading repository metadata"
    assert output_events[0].payload.get("stream") == "stdout"
    assert (
        output_events[0].payload.get("stream_text") == "loading repository metadata\n"
    )


def test_runner_retries_after_raw_intermediate_output(tmp_path: Path):
    session = _FakeRunSession(
        steps=[
            _FakeStep(
                response=_response(
                    final_value={"output": ["README.md", "pyproject.toml"]}
                )
            ),
            _FakeStep(
                response=_response(
                    final_value={
                        "summary": (
                            "The final answer now synthesizes the repository findings "
                            "instead of returning raw file names."
                        )
                    }
                )
            ),
        ]
    )
    runner = DaytonaRLMRunner(
        lm=_FakeLmSequence(
            [
                _code('files = ["README.md", "pyproject.toml"]\nSUBMIT(output=files)'),
                _code(
                    'summary = "The final answer now synthesizes the repository findings '
                    'instead of returning raw file names."\n'
                    "SUBMIT(summary=summary)"
                ),
            ]
        ),
        runtime=_FakeRuntime(session),
        output_dir=tmp_path,
    )

    result = runner.run(repo="https://github.com/example/repo.git", task="inspect repo")

    assert result.final_artifact is not None
    assert "synthesizes the repository findings" in str(result.final_artifact.value)
    root = result.nodes[result.root_id]
    assert root.iteration_count == 2


def test_runner_retries_after_short_file_like_summary(tmp_path: Path):
    session = _FakeRunSession(
        steps=[
            _FakeStep(response=_response(final_value={"summary": "README.md"})),
            _FakeStep(
                response=_response(
                    final_value={
                        "summary": (
                            "The repository entry point is documented in README.md."
                        )
                    }
                )
            ),
        ]
    )
    runner = DaytonaRLMRunner(
        lm=_FakeLmSequence(
            [
                _code("summary = 'README.md'\nSUBMIT(summary=summary)"),
                _code(
                    "summary = 'The repository entry point is documented in README.md.'\n"
                    "SUBMIT(summary=summary)"
                ),
            ]
        ),
        runtime=_FakeRuntime(session),
        output_dir=tmp_path,
    )

    result = runner.run(repo=None, task="Identify the entry documentation.")

    assert result.final_artifact is not None
    assert result.final_artifact.value["summary"] == (
        "The repository entry point is documented in README.md."
    )
    assert result.nodes[result.root_id].iteration_count == 2


def test_runner_externalizes_long_task_into_prompt_store(tmp_path: Path):
    long_task = "Summarize this repository in depth. " * 250
    session = _FakeRunSession(
        steps=[
            _FakeStep(
                response=_response(
                    final_value={
                        "summary": (
                            "A readable answer proves the task was externalized and "
                            "the prompt referenced the handle instead of inlining it."
                        )
                    }
                )
            )
        ]
    )
    fake_lm = _FakeLmSequence(
        [
            _code(
                'summary = "A readable answer proves the task was externalized and '
                'the prompt referenced the handle instead of inlining it."\n'
                "SUBMIT(summary=summary)"
            )
        ]
    )
    runner = DaytonaRLMRunner(
        lm=fake_lm,
        runtime=_FakeRuntime(session),
        output_dir=tmp_path,
    )

    result = runner.run(repo="https://github.com/example/repo.git", task=long_task)

    assert result.final_artifact is not None
    assert session.store_prompt_calls
    assert session.store_prompt_calls[0]["kind"] == "task"
    assert long_task not in fake_lm.prompts[0]
    assert "externalized as prompt handle" in fake_lm.prompts[0]


def test_runner_extracts_full_python_block_with_nested_markdown_fences(tmp_path: Path):
    response_text = """Here is the completed sandbox code.
```python
final_markdown = \"\"\"# DSPy vs OpenAI

```bash
pip install dspy-ai openai
```
\"\"\"
SUBMIT(final_markdown=final_markdown)
```
"""
    session = _FakeRunSession(
        steps=[
            _FakeStep(
                response=_response(
                    final_value={
                        "final_markdown": "# DSPy vs OpenAI\n\nDone.",
                    }
                )
            )
        ]
    )
    runner = DaytonaRLMRunner(
        lm=_FakeLmSequence([response_text]),
        runtime=_FakeRuntime(session),
        output_dir=tmp_path,
    )

    result = runner.run(repo=None, task="Generate the comparison script.")

    assert result.final_artifact is not None
    assert session.execute_calls[0]["code"] == (
        'final_markdown = """# DSPy vs OpenAI\n\n'
        "```bash\n"
        "pip install dspy-ai openai\n"
        "```\n"
        '"""\n'
        "SUBMIT(final_markdown=final_markdown)"
    )


def test_runner_uses_host_llm_query_batched_and_records_child_links(tmp_path: Path):
    tasks = [
        {
            "task": "Summarize the README excerpt.",
            "label": "README",
            "source": {
                "kind": "file_slice",
                "path": "README.md",
                "start_line": 1,
                "end_line": 2,
                "preview": "# Example\nIntro line",
            },
        },
        "Explain the role of pyproject.toml.",
    ]
    callback = HostCallbackRequest(
        callback_id="cb-1",
        name="llm_query_batched",
        payload={"tasks": tasks},
    )
    session = _FakeRunSession(
        steps=[
            _FakeStep(
                callbacks=[callback],
                response=_response(
                    final_value={
                        "summary": (
                            "The host-side batched subqueries completed and the root "
                            "loop synthesized their results."
                        )
                    },
                    callback_count=1,
                ),
            )
        ]
    )
    main_lm = _FakeLmSequence(
        [
            _code(
                "tasks = []\n"
                "results = llm_query_batched(tasks)\n"
                'summary = "The host-side batched subqueries completed and the root '
                'loop synthesized their results."\n'
                "SUBMIT(summary=summary)"
            )
        ]
    )
    delegate_lm = _FakeLmSequence(
        [
            "README summary from host sub-LLM.",
            "pyproject explanation from host sub-LLM.",
        ]
    )
    runner = DaytonaRLMRunner(
        lm=main_lm,
        delegate_lm=delegate_lm,
        runtime=_FakeRuntime(session),
        budget=RolloutBudget(batch_concurrency=6),
        output_dir=tmp_path,
    )

    result = runner.run(repo="https://github.com/example/repo.git", task="inspect repo")

    assert result.final_artifact is not None
    root = result.nodes[result.root_id]
    assert len(root.child_links) == 2
    assert all(link.callback_name == "llm_query_batched" for link in root.child_links)
    assert len(delegate_lm.prompts) == 2
    assert session.callback_responses[0].value == [
        "README summary from host sub-LLM.",
        "pyproject explanation from host sub-LLM.",
    ]


def test_runner_uses_explicit_rlm_query_and_merges_child_nodes(tmp_path: Path):
    callback = HostCallbackRequest(
        callback_id="cb-1",
        name="rlm_query",
        payload={
            "task": {
                "task": "Inspect the README excerpt.",
                "label": "README child",
                "source": {
                    "kind": "file_slice",
                    "path": "README.md",
                    "start_line": 1,
                    "end_line": 2,
                    "preview": "# Example\nIntro line",
                },
            }
        },
    )
    root_session = _FakeRunSession(
        steps=[
            _FakeStep(
                callbacks=[callback],
                response=_response(
                    final_value={
                        "summary": (
                            "The root run incorporated the recursive child summary."
                        )
                    },
                    callback_count=1,
                ),
            )
        ]
    )
    child_session = _FakeRunSession(
        steps=[
            _FakeStep(
                response=_response(
                    final_value={
                        "output": {"summary": "Raw child payload that needs synthesis."}
                    }
                )
            )
        ]
    )
    runner = DaytonaRLMRunner(
        lm=_FakeLmSequence(
            [
                _code(
                    "child = rlm_query({'task': 'Inspect the README excerpt.', "
                    "'label': 'README child', 'source': {'kind': 'file_slice', "
                    "'path': 'README.md', 'start_line': 1, 'end_line': 2, "
                    "'preview': '# Example\\nIntro line'}})\n"
                    'summary = "The root run incorporated the recursive child summary."\n'
                    "SUBMIT(summary=summary)"
                ),
                _code(
                    "payload = {'summary': 'Raw child payload that needs synthesis.'}\n"
                    "SUBMIT(output=payload)"
                ),
            ]
        ),
        runtime=_FakeRuntime([root_session, child_session]),
        output_dir=tmp_path,
    )
    captured: dict[str, object] = {}

    def _fake_child_synthesizer(**kwargs):
        captured["child_evidence_json"] = kwargs["child_evidence_json"]
        return SimpleNamespace(
            answer_markdown="Synthesized child summary.",
            result_preview="Synth child",
            evidence=[{"path": "README.md", "start_line": 1, "end_line": 2}],
            follow_up_needed=False,
            confidence=0.84,
        )

    runner._child_synthesizer = _fake_child_synthesizer

    result = runner.run(repo="https://github.com/example/repo.git", task="inspect repo")

    root = result.nodes[result.root_id]
    assert len(root.child_links) == 1
    assert root.child_links[0].callback_name == "rlm_query"
    assert root.child_links[0].child_id is not None
    assert root.child_links[0].result_preview == "Synth child"
    assert root.child_links[0].child_id in result.nodes
    assert root_session.callback_responses[0].value == "Synthesized child summary."
    assert result.summary.sandboxes_used == 2
    assert json.loads(str(captured["child_evidence_json"])) == [
        {
            "kind": "file_slice",
            "source_id": root.child_links[0].task.source.source_id,
            "path": "README.md",
            "start_line": 1,
            "end_line": 2,
            "header": None,
            "preview": "# Example Intro line",
            "chunk_index": None,
            "pattern": None,
        }
    ]
    evaluation = result.node_evaluation(result.root_id)
    assert evaluation["child_synthesis"][0]["callback_name"] == "rlm_query"
    assert evaluation["child_synthesis"][0]["confidence"] == pytest.approx(0.84)
    assert evaluation["child_synthesis"][0]["follow_up_needed"] is False
    assert "evaluation" not in result.to_public_dict()
    persisted = json.loads(Path(result.result_path or "").read_text(encoding="utf-8"))
    assert (
        persisted["evaluation"]["nodes"][result.root_id]["child_synthesis"][0][
            "callback_name"
        ]
        == "rlm_query"
    )


def test_runner_auto_decomposes_successful_observation_between_iterations(
    tmp_path: Path,
):
    root_session = _FakeRunSession(
        steps=[
            _FakeStep(response=_response(stdout="Need deeper evidence.")),
            _FakeStep(
                response=_response(
                    final_value={
                        "summary": (
                            "The root run consumed the recursive child summary on "
                            "the next iteration."
                        )
                    }
                )
            ),
        ]
    )
    child_session = _FakeRunSession(
        steps=[
            _FakeStep(
                response=_response(
                    final_value={"output": {"summary": "Raw child evidence"}}
                )
            )
        ]
    )
    fake_lm = _FakeLmSequence(
        [
            _code("notes = 'look deeper'"),
            _code(
                "payload = {'summary': 'Raw child evidence'}\nSUBMIT(output=payload)"
            ),
            _code(
                'summary = "The root run consumed the recursive child summary on '
                'the next iteration."\n'
                "SUBMIT(summary=summary)"
            ),
        ]
    )
    runner = DaytonaRLMRunner(
        lm=fake_lm,
        runtime=_FakeRuntime([root_session, child_session]),
        output_dir=tmp_path,
    )
    runner._spawn_policy = lambda **kwargs: SimpleNamespace(
        should_spawn=True,
        recommended_fanout=3,
        rationale="Deeper evidence is available.",
    )
    runner._recursive_decomposer = lambda **kwargs: SimpleNamespace(
        tasks=[
            RecursiveTaskSpec.from_raw(
                {
                    "task": "Inspect the README excerpt.",
                    "label": "README child",
                    "source": {
                        "kind": "file_slice",
                        "path": "README.md",
                        "start_line": 1,
                        "end_line": 2,
                        "preview": "# Example\nIntro line",
                    },
                }
            )
        ],
        decision_summary="Need one recursive child.",
    )
    runner._child_synthesizer = lambda **kwargs: SimpleNamespace(
        answer_markdown="Synthesized child summary from auto decomposition.",
        result_preview="Auto child summary",
        evidence=[{"path": "README.md"}],
        follow_up_needed=False,
        confidence=0.67,
    )

    result = runner.run(repo="https://github.com/example/repo.git", task="inspect repo")

    root = result.nodes[result.root_id]
    assert any(link.callback_name == "recursive_auto" for link in root.child_links)
    assert "Recursive child results:" in fake_lm.prompts[2]
    assert "Synthesized child summary from auto decomposition." in fake_lm.prompts[2]
    assert result.summary.sandboxes_used == 2
    evaluation = result.node_evaluation(result.root_id)
    assert evaluation["spawn_policy"][0]["should_spawn"] is True
    assert evaluation["spawn_policy"][0]["recommended_fanout"] == 3
    assert evaluation["decomposition"][0]["selected_task_count"] == 1


def test_runner_auto_decomposition_respects_spawn_policy_false(tmp_path: Path):
    root_session = _FakeRunSession(
        steps=[
            _FakeStep(response=_response(stdout="Need deeper evidence.")),
            _FakeStep(
                response=_response(
                    final_value={
                        "summary": "The root run finished without recursive children."
                    }
                )
            ),
        ]
    )
    fake_lm = _FakeLmSequence(
        [
            _code("notes = 'look deeper'"),
            _code(
                'summary = "The root run finished without recursive children."\n'
                "SUBMIT(summary=summary)"
            ),
        ]
    )
    runner = DaytonaRLMRunner(
        lm=fake_lm,
        runtime=_FakeRuntime(root_session),
        output_dir=tmp_path,
    )
    runner._spawn_policy = lambda **kwargs: SimpleNamespace(
        should_spawn=False,
        recommended_fanout=0,
        rationale="No meaningful recursive split.",
    )

    result = runner.run(repo="https://github.com/example/repo.git", task="inspect repo")

    root = result.nodes[result.root_id]
    assert root.child_links == []
    evaluation = result.node_evaluation(result.root_id)
    assert evaluation["spawn_policy"][0]["should_spawn"] is False
    assert evaluation["spawn_policy"][0]["recommended_fanout"] == 0
    assert result.summary.sandboxes_used == 1


def test_runner_auto_decomposition_caps_recommended_fanout(tmp_path: Path):
    root_session = _FakeRunSession(
        steps=[
            _FakeStep(response=_response(stdout="Need deeper evidence.")),
            _FakeStep(
                response=_response(
                    final_value={
                        "summary": "The root run used one child because budget capped fanout."
                    }
                )
            ),
        ]
    )
    child_session = _FakeRunSession(
        steps=[
            _FakeStep(
                response=_response(
                    final_value={"output": {"summary": "Raw child evidence"}}
                )
            )
        ]
    )
    runner = DaytonaRLMRunner(
        lm=_FakeLmSequence(
            [
                _code("notes = 'look deeper'"),
                _code(
                    "payload = {'summary': 'Raw child evidence'}\n"
                    "SUBMIT(output=payload)"
                ),
                _code(
                    'summary = "The root run used one child because budget capped fanout."\n'
                    "SUBMIT(summary=summary)"
                ),
            ]
        ),
        runtime=_FakeRuntime([root_session, child_session]),
        budget=RolloutBudget(max_sandboxes=2, batch_concurrency=4),
        output_dir=tmp_path,
    )
    runner._spawn_policy = lambda **kwargs: SimpleNamespace(
        should_spawn=True,
        recommended_fanout=5,
        rationale="Many workstreams are possible.",
    )
    runner._recursive_decomposer = lambda **kwargs: SimpleNamespace(
        tasks=[
            RecursiveTaskSpec(task="first child"),
            RecursiveTaskSpec(task="second child"),
        ],
        decision_summary="Need two recursive children.",
    )
    runner._child_synthesizer = lambda **kwargs: SimpleNamespace(
        answer_markdown="Synthesized child summary.",
        result_preview="Synth child",
        evidence=[],
        follow_up_needed=False,
        confidence=0.51,
    )

    result = runner.run(repo="https://github.com/example/repo.git", task="inspect repo")

    evaluation = result.node_evaluation(result.root_id)
    assert evaluation["spawn_policy"][0]["recommended_fanout"] == 5
    assert evaluation["decomposition"][0]["proposed_task_count"] == 2
    assert evaluation["decomposition"][0]["selected_task_count"] == 1
    assert result.summary.sandboxes_used == 2


def test_run_child_task_enforces_max_depth(tmp_path: Path):
    runner = DaytonaRLMRunner(
        runtime=_FakeRuntime(_FakeRunSession()),
        budget=RolloutBudget(max_depth=0),
        output_dir=tmp_path,
    )
    runner._active_repo = "https://github.com/example/repo.git"
    runner._active_ref = None
    runner._active_context_paths = []
    runner._sandboxes_used = 1

    with pytest.raises(RuntimeError, match="Recursive depth exceeded"):
        runner.run_child_task(
            parent_id="root",
            depth=0,
            task_spec=RecursiveTaskSpec(task="inspect README"),
        )


def test_run_child_task_enforces_sandbox_budget(tmp_path: Path):
    runner = DaytonaRLMRunner(
        runtime=_FakeRuntime(_FakeRunSession()),
        budget=RolloutBudget(max_sandboxes=1),
        output_dir=tmp_path,
    )
    runner._active_repo = "https://github.com/example/repo.git"
    runner._active_ref = None
    runner._active_context_paths = []
    runner._sandboxes_used = 1

    with pytest.raises(RuntimeError, match="Sandbox budget exceeded"):
        runner.run_child_task(
            parent_id="root",
            depth=0,
            task_spec=RecursiveTaskSpec(task="inspect README"),
        )


def test_spawn_child_tasks_batched_preserves_input_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    runner = DaytonaRLMRunner(
        runtime=_FakeRuntime(_FakeRunSession()),
        output_dir=tmp_path,
    )

    def _fake_run_child_task(**kwargs):
        task_spec = kwargs["task_spec"]
        return SimpleNamespace(
            child_id=f"child-{task_spec.task}",
            task=task_spec,
            text=f"summary:{task_spec.task}",
            result_preview=f"preview:{task_spec.task}",
            status="completed",
            evidence=[],
            confidence=None,
            follow_up_needed=False,
            run_result=None,
        )

    monkeypatch.setattr(runner, "run_child_task", _fake_run_child_task)

    task_specs = [
        RecursiveTaskSpec(task="first"),
        RecursiveTaskSpec(task="second"),
    ]
    results = runner._spawn_child_tasks_batched(
        parent_id="root",
        depth=0,
        parent_task="inspect repo",
        task_specs=task_specs,
    )

    assert [result.task.task for result in results] == ["first", "second"]


def test_runner_returns_cancelled_result_before_iteration(tmp_path: Path):
    session = _FakeRunSession()
    main_lm = _FakeLmSequence([_code("SUBMIT(summary='unused')")])
    runner = DaytonaRLMRunner(
        lm=main_lm,
        runtime=_FakeRuntime(session),
        output_dir=tmp_path,
        cancel_check=lambda: True,
    )

    result = runner.run(repo="https://github.com/example/repo.git", task="cancel this")

    assert result.summary.termination_reason == "cancelled"
    assert result.summary.error == "Request cancelled."
    assert session.execute_calls == []


def test_runner_uses_workspace_session_and_emits_context_sources(tmp_path: Path):
    context_source = ContextSource(
        source_id="ctx-1",
        kind="file",
        host_path="/Users/zocho/Documents/spec.pdf",
        staged_path="/workspace/repo/.fleet-rlm/context/ctx-1/spec.pdf",
    )
    session = _FakeRunSession(
        steps=[
            _FakeStep(
                response=_response(
                    final_value={
                        "summary": (
                            "The run completed with staged local context and no "
                            "repository clone, proving the workspace bootstrap path "
                            "works when only host-provided files are available for "
                            "analysis inside the sandbox."
                        )
                    }
                )
            )
        ],
        context_sources=[context_source],
    )
    runtime = _FakeRuntime(session)
    runner = DaytonaRLMRunner(
        lm=_FakeLmSequence(
            [
                _code(
                    'summary = "The run completed with staged local context and no '
                    "repository clone, proving the workspace bootstrap path works "
                    "when only host-provided files are available for analysis "
                    'inside the sandbox."\n'
                    "SUBMIT(summary=summary)"
                )
            ]
        ),
        runtime=runtime,
        output_dir=tmp_path,
    )

    result = runner.run(
        repo=None,
        task="inspect local context",
        context_paths=["/Users/zocho/Documents/spec.pdf"],
    )

    assert runtime.workspace_calls == [
        (None, None, ["/Users/zocho/Documents/spec.pdf"])
    ]
    assert result.context_sources[0].host_path == "/Users/zocho/Documents/spec.pdf"


def test_runner_accepts_legacy_depth_budget_without_emitting_warning(
    tmp_path: Path,
):
    emitted: list[StreamEvent] = []
    session = _FakeRunSession(
        steps=[
            _FakeStep(
                response=_response(
                    final_value={
                        "summary": (
                            "A readable summary proves the run still succeeds even "
                            "when legacy depth controls are supplied, because the "
                            "host-loop Daytona path ignores child-sandbox recursion."
                        )
                    }
                )
            )
        ]
    )
    runner = DaytonaRLMRunner(
        lm=_FakeLmSequence(
            [
                _code(
                    'summary = "A readable summary proves the run still succeeds '
                    "even when legacy depth controls are supplied, because the "
                    'host-loop Daytona path ignores child-sandbox recursion."\n'
                    "SUBMIT(summary=summary)"
                )
            ]
        ),
        runtime=_FakeRuntime(session),
        budget=RolloutBudget(max_depth=3),
        output_dir=tmp_path,
        event_callback=emitted.append,
    )

    result = runner.run(repo="https://github.com/example/repo.git", task="warn me")

    assert result.summary.warnings == []
    assert not any(event.kind == "warning" for event in emitted)


def test_runner_public_result_exposes_trajectory_callbacks_and_evidence(
    tmp_path: Path,
):
    context_source = ContextSource(
        source_id="ctx-1",
        kind="file",
        host_path="/Users/zocho/Documents/spec.pdf",
        staged_path="/workspace/repo/.fleet-rlm/context/ctx-1/spec.pdf",
        source_type="pdf",
        extraction_method="pypdf",
    )
    callback = HostCallbackRequest(
        callback_id="cb-1",
        name="llm_query_batched",
        payload={
            "tasks": [
                {
                    "task": "Summarize the README excerpt.",
                    "label": "README",
                    "source": {
                        "kind": "file_slice",
                        "path": "README.md",
                        "start_line": 1,
                        "end_line": 2,
                        "preview": "# Example\nIntro line",
                    },
                }
            ]
        },
    )
    session = _FakeRunSession(
        steps=[
            _FakeStep(
                callbacks=[callback],
                response=_response(
                    final_value={
                        "summary": (
                            "The public run payload exposes trajectory, evidence, "
                            "and callback details for the analyst workbench."
                        )
                    },
                    callback_count=1,
                ),
            )
        ],
        context_sources=[context_source],
    )
    runner = DaytonaRLMRunner(
        lm=_FakeLmSequence(
            [
                _code(
                    "results = llm_query_batched([])\n"
                    'summary = "The public run payload exposes trajectory, evidence, '
                    'and callback details for the analyst workbench."\n'
                    "SUBMIT(summary=summary)"
                )
            ]
        ),
        delegate_lm=_FakeLmSequence(["README summary from host sub-LLM."]),
        runtime=_FakeRuntime(session),
        output_dir=tmp_path,
    )

    result = runner.run(repo=None, task="inspect staged diligence materials")
    public = result.to_public_dict()

    assert public["daytona_mode"] == "host_loop_rlm"
    assert public["iterations"][0]["iteration"] == 1
    assert public["iterations"][0]["callback_count"] == 1
    assert public["callbacks"][0]["callback_name"] == "llm_query_batched"
    assert public["callbacks"][0]["iteration"] == 1
    assert public["attachments"][0]["name"] == "spec.pdf"
    assert any(
        item.get("display_url") == "/Users/zocho/Documents/spec.pdf"
        for item in public["sources"]
    )
    assert any(
        item.get("quote") == "# Example Intro line" for item in public["sources"]
    )


def test_runner_summary_persists_phase_timings(tmp_path: Path):
    summary = (
        "The runner keeps bootstrap and first-execute timings in the rollout summary."
    )
    session = _FakeRunSession(
        steps=[_FakeStep(response=_response(final_value={"summary": summary}))]
    )
    session.phase_timings_ms = {
        "sandbox_create": 11,
        "repo_clone": 7,
        "context_stage": 2,
        "driver_start": 4,
        "first_execute_response": 9,
    }
    runner = DaytonaRLMRunner(
        lm=_FakeLmSequence([_code(f"summary = {summary!r}\nSUBMIT(summary=summary)")]),
        runtime=_FakeRuntime(session),
        output_dir=tmp_path,
    )

    result = runner.run(repo=None, task="report timings")

    assert result.summary.phase_timings_ms == session.phase_timings_ms


def test_runner_externalizes_structured_conversation_history(tmp_path: Path):
    summary = (
        "The run used structured prior-turn history from the sandbox prompt store."
    )
    session = _FakeRunSession(
        steps=[_FakeStep(response=_response(final_value={"summary": summary}))]
    )
    fake_lm = _FakeLmSequence(
        [_code(f"summary = {summary!r}\nSUBMIT(summary=summary)")]
    )
    runner = DaytonaRLMRunner(
        lm=fake_lm,
        runtime=_FakeRuntime(session),
        output_dir=tmp_path,
    )
    runner._ground_task_with_history = lambda **kwargs: (
        "The user is asking about the previous greeting and expects an exact quote."
    )

    result = runner.run(
        repo=None,
        task="Compare this request with what we discussed before.",
        conversation_history=[
            {
                "user_request": "Say hello in one sentence.",
                "assistant_response": "Hello there, it is great to meet you!",
            }
        ],
    )

    assert result.final_artifact is not None
    assert session.store_prompt_calls
    assert session.store_prompt_calls[0]["kind"] == "conversation_history"
    assert "history_turns" in str(session.store_prompt_calls[0]["text"])
    assert "Session grounding from DSPy history input:" in fake_lm.prompts[0]
    assert "expects an exact quote" in fake_lm.prompts[0]
    assert (
        "Conversation history: structured history is externalized as prompt handle"
        in fake_lm.prompts[0]
    )
    assert "Recent conversation recap:" in fake_lm.prompts[0]
    assert "Hello there, it is great to meet you!" in fake_lm.prompts[0]


def test_ground_task_with_history_uses_dspy_history(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    captured: dict[str, object] = {}

    class _FakePredict:
        def __init__(self, signature):
            captured["signature"] = signature

        def __call__(self, *, current_user_request, history):
            captured["current_user_request"] = current_user_request
            captured["history_messages"] = list(history.messages)
            return type(
                "_Prediction",
                (),
                {
                    "grounded_task": "Answer the follow-up using the earlier greeting context."
                },
            )()

    monkeypatch.setattr("fleet_rlm.daytona_rlm.dspy_modules.dspy.Predict", _FakePredict)

    runner = DaytonaRLMRunner(
        lm=_FakeLmSequence([_code("summary = 'unused'\nSUBMIT(summary=summary)")]),
        runtime=_FakeRuntime(_FakeRunSession()),
        output_dir=tmp_path,
    )

    grounded = runner._ground_task_with_history(
        lm=object(),
        task="What was my previous request?",
        conversation_history=[
            {
                "user_request": "Say hello in one sentence.",
                "assistant_response": "Hello there, it is great to meet you!",
            }
        ],
    )

    assert grounded == "Answer the follow-up using the earlier greeting context."
    assert captured["current_user_request"] == "What was my previous request?"
    assert captured["history_messages"] == [
        {
            "user_request": "Say hello in one sentence.",
            "assistant_response": "Hello there, it is great to meet you!",
        }
    ]


def test_run_daytona_rlm_pilot_persists_result(tmp_path: Path):
    summary = (
        "The persisted rollout proves the host-loop Daytona runner wrote the "
        "result artifact after a successful run."
    )
    session = _FakeRunSession(
        steps=[_FakeStep(response=_response(final_value={"summary": summary}))]
    )
    runtime = _FakeRuntime(session)

    result = run_daytona_rlm_pilot(
        repo="https://github.com/example/repo.git",
        task="persist run",
        runtime=runtime,
        output_dir=tmp_path,
        lm=_FakeLmSequence([_code(f"summary = {summary!r}\nSUBMIT(summary=summary)")]),
    )

    assert result.result_path is not None
    assert Path(result.result_path).exists()
