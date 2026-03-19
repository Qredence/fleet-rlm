from __future__ import annotations

from pathlib import Path

from fleet_rlm.infrastructure.providers.daytona.protocol import ExecutionEventFrame
from fleet_rlm.infrastructure.providers.daytona.runner import DaytonaRLMRunner
from fleet_rlm.core.models import StreamEvent
from tests.unit.fixtures_daytona import (
    FakeLmSequence,
    FakeRunSession,
    FakeRuntime,
    FakeStep,
    code_block,
    make_response,
)


def test_runner_emits_iteration_progress_phases(tmp_path: Path):
    summary = (
        "The Daytona runner now emits richer progress phases before the final "
        "result is available to the workbench."
    )
    emitted: list[StreamEvent] = []
    session = FakeRunSession(
        steps=[FakeStep(response=make_response(final_value={"summary": summary}))]
    )
    session.phase_timings_ms = {
        "sandbox_create": 21,
        "repo_clone": 8,
        "context_stage": 3,
    }
    runner = DaytonaRLMRunner(
        lm=FakeLmSequence(
            [code_block(f"summary = {summary!r}\nSUBMIT(summary=summary)")]
        ),
        runtime=FakeRuntime(session),
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
    session = FakeRunSession(
        steps=[
            FakeStep(
                progress_frames=[
                    ExecutionEventFrame(
                        request_id="req-1",
                        stream="stdout",
                        text="loading repository metadata\n",
                    )
                ],
                response=make_response(final_value={"summary": summary}),
            )
        ]
    )
    runner = DaytonaRLMRunner(
        lm=FakeLmSequence(
            [code_block(f"summary = {summary!r}\nSUBMIT(summary=summary)")]
        ),
        runtime=FakeRuntime(session),
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
    assert output_events[0].payload.get("stream_text") == "loading repository metadata"


def test_runner_emits_live_reasoning_and_trajectory_events(tmp_path: Path):
    summary = "The Daytona runner now emits live reasoning and trajectory steps."
    emitted: list[StreamEvent] = []
    session = FakeRunSession(
        steps=[FakeStep(response=make_response(final_value={"summary": summary}))]
    )
    runner = DaytonaRLMRunner(
        lm=FakeLmSequence(
            [code_block(f"summary = {summary!r}\nSUBMIT(summary=summary)")]
        ),
        runtime=FakeRuntime(session),
        output_dir=tmp_path,
        event_callback=emitted.append,
    )

    runner.run(repo="https://github.com/example/repo.git", task="emit live trace")

    reasoning_events = [event for event in emitted if event.kind == "reasoning_step"]
    reasoning_labels = [
        event.payload.get("reasoning_label") for event in reasoning_events
    ]
    assert reasoning_labels == [
        "prompt_iter_1",
        "planner_iter_1",
        "extracted_code_iter_1",
        "observation_iter_1",
    ]
    assert "Planner prompt preview:" in reasoning_events[0].text
    assert "Planner response preview:" in reasoning_events[1].text
    assert reasoning_events[2].text.startswith("```python\n")
    assert "Duration: 12ms" in reasoning_events[3].text

    trajectory_events = [event for event in emitted if event.kind == "trajectory_step"]
    trajectory_phases = [event.payload.get("phase") for event in trajectory_events]
    assert trajectory_phases == ["iteration", "observation", "completed"]
    assert (
        trajectory_events[0].payload.get("step_data", {}).get("action") == "Iteration 1"
    )
    assert (
        trajectory_events[1].payload.get("step_data", {}).get("action")
        == "Execute sandbox code"
    )
    assert (
        trajectory_events[2].payload.get("step_data", {}).get("action")
        == "Complete run"
    )


def test_runner_includes_workspace_volume_name_in_runtime_payload(tmp_path: Path):
    emitted: list[StreamEvent] = []
    session = FakeRunSession(
        steps=[FakeStep(response=make_response(final_value={"summary": "Done."}))]
    )
    runner = DaytonaRLMRunner(
        lm=FakeLmSequence([code_block("SUBMIT(summary='Done.')")]),
        runtime=FakeRuntime(session),
        output_dir=tmp_path,
        event_callback=emitted.append,
        volume_name="tenant-a",
    )

    runner.run(repo="https://github.com/example/repo.git", task="emit runtime metadata")

    runtime_payloads = [
        event.payload.get("runtime")
        for event in emitted
        if isinstance(event.payload, dict)
        and isinstance(event.payload.get("runtime"), dict)
    ]
    assert runtime_payloads
    assert all(payload.get("volume_name") == "tenant-a" for payload in runtime_payloads)


def test_runner_retries_after_raw_intermediate_output(tmp_path: Path):
    session = FakeRunSession(
        steps=[
            FakeStep(
                response=make_response(
                    final_value={"output": ["README.md", "pyproject.toml"]}
                )
            ),
            FakeStep(
                response=make_response(
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
        lm=FakeLmSequence(
            [
                code_block(
                    'files = ["README.md", "pyproject.toml"]\nSUBMIT(output=files)'
                ),
                code_block(
                    'summary = "The final answer now synthesizes the repository findings '
                    'instead of returning raw file names."\n'
                    "SUBMIT(summary=summary)"
                ),
            ]
        ),
        runtime=FakeRuntime(session),
        output_dir=tmp_path,
    )

    result = runner.run(repo="https://github.com/example/repo.git", task="inspect repo")

    assert result.final_artifact is not None
    assert "synthesizes the repository findings" in str(result.final_artifact.value)
    root = result.nodes[result.root_id]
    assert root.iteration_count == 2


def test_runner_retries_after_short_file_like_summary(tmp_path: Path):
    session = FakeRunSession(
        steps=[
            FakeStep(response=make_response(final_value={"summary": "README.md"})),
            FakeStep(
                response=make_response(
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
        lm=FakeLmSequence(
            [
                code_block("summary = 'README.md'\nSUBMIT(summary=summary)"),
                code_block(
                    "summary = 'The repository entry point is documented in README.md.'\n"
                    "SUBMIT(summary=summary)"
                ),
            ]
        ),
        runtime=FakeRuntime(session),
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
    session = FakeRunSession(
        steps=[
            FakeStep(
                response=make_response(
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
    fake_lm = FakeLmSequence(
        [
            code_block(
                'summary = "A readable answer proves the task was externalized and '
                'the prompt referenced the handle instead of inlining it."\n'
                "SUBMIT(summary=summary)"
            )
        ]
    )
    runner = DaytonaRLMRunner(
        lm=fake_lm,
        runtime=FakeRuntime(session),
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
    session = FakeRunSession(
        steps=[
            FakeStep(
                response=make_response(
                    final_value={
                        "final_markdown": "# DSPy vs OpenAI\n\nDone.",
                    }
                )
            )
        ]
    )
    runner = DaytonaRLMRunner(
        lm=FakeLmSequence([response_text]),
        runtime=FakeRuntime(session),
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
