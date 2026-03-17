from __future__ import annotations

from pathlib import Path

from fleet_rlm.infrastructure.providers.daytona.runner import DaytonaRLMRunner
from fleet_rlm.infrastructure.providers.daytona.system_prompt import build_system_prompt
from fleet_rlm.infrastructure.providers.daytona.types import (
    RecursiveTaskSpec,
    RolloutBudget,
)
from fleet_rlm.core.models import StreamEvent
from tests.unit.fixtures_daytona import (
    FakeLmSequence,
    FakeRunSession,
    FakeRuntime,
    FakeStep,
    code_block,
    make_response,
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
    session = FakeRunSession(
        steps=[FakeStep(response=make_response(final_value={"summary": summary}))]
    )
    runtime = FakeRuntime(session)
    emitted: list[StreamEvent] = []
    runner = DaytonaRLMRunner(
        lm=FakeLmSequence(
            [code_block(f"summary = {summary!r}\nSUBMIT(summary=summary)")]
        ),
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
    session = FakeRunSession(
        steps=[FakeStep(response=make_response(final_value={"summary": summary}))]
    )
    runner = DaytonaRLMRunner(
        lm=FakeLmSequence(
            [code_block(f"summary = {summary!r}\nSUBMIT(summary=summary)")]
        ),
        runtime=FakeRuntime(session),
        output_dir=tmp_path,
    )

    result = runner.run(repo=None, task="Say hello in one sentence.")

    assert result.summary.termination_reason == "completed"
    assert result.final_artifact is not None
    assert result.final_artifact.value["summary"] == summary
    assert result.nodes[result.root_id].iteration_count == 1


def test_runner_accepts_short_final_markdown_as_final_answer(tmp_path: Path):
    final_markdown = "## Greeting\n\nHello there!"
    session = FakeRunSession(
        steps=[
            FakeStep(
                response=make_response(final_value={"final_markdown": final_markdown})
            )
        ]
    )
    runner = DaytonaRLMRunner(
        lm=FakeLmSequence(
            [
                code_block(
                    f"final_markdown = {final_markdown!r}\n"
                    "SUBMIT(final_markdown=final_markdown)"
                )
            ]
        ),
        runtime=FakeRuntime(session),
        output_dir=tmp_path,
    )

    result = runner.run(repo=None, task="Greet the user briefly.")

    assert result.summary.termination_reason == "completed"
    assert result.final_artifact is not None
    assert result.final_artifact.value["final_markdown"] == final_markdown
    assert result.nodes[result.root_id].iteration_count == 1
