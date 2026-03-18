from __future__ import annotations

from pathlib import Path

import pytest

from fleet_rlm.infrastructure.providers.daytona.protocol import HostCallbackRequest
from fleet_rlm.infrastructure.providers.daytona.runner import (
    DaytonaRLMRunner,
    run_daytona_rlm_pilot,
)
from fleet_rlm.infrastructure.providers.daytona.types import (
    ContextSource,
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


def test_runner_returns_cancelled_result_before_iteration(tmp_path: Path):
    session = FakeRunSession()
    main_lm = FakeLmSequence([code_block("SUBMIT(summary='unused')")])
    runner = DaytonaRLMRunner(
        lm=main_lm,
        runtime=FakeRuntime(session),
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
    session = FakeRunSession(
        steps=[
            FakeStep(
                response=make_response(
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
    runtime = FakeRuntime(session)
    runner = DaytonaRLMRunner(
        lm=FakeLmSequence(
            [
                code_block(
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
    session = FakeRunSession(
        steps=[
            FakeStep(
                response=make_response(
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
        lm=FakeLmSequence(
            [
                code_block(
                    'summary = "A readable summary proves the run still succeeds '
                    "even when legacy depth controls are supplied, because the "
                    'host-loop Daytona path ignores child-sandbox recursion."\n'
                    "SUBMIT(summary=summary)"
                )
            ]
        ),
        runtime=FakeRuntime(session),
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
    session = FakeRunSession(
        steps=[
            FakeStep(
                callbacks=[callback],
                response=make_response(
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
        lm=FakeLmSequence(
            [
                code_block(
                    "results = llm_query_batched([])\n"
                    'summary = "The public run payload exposes trajectory, evidence, '
                    'and callback details for the analyst workbench."\n'
                    "SUBMIT(summary=summary)"
                )
            ]
        ),
        delegate_lm=FakeLmSequence(["README summary from host sub-LLM."]),
        runtime=FakeRuntime(session),
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
    session = FakeRunSession(
        steps=[FakeStep(response=make_response(final_value={"summary": summary}))]
    )
    session.phase_timings_ms = {
        "sandbox_create": 11,
        "repo_clone": 7,
        "context_stage": 2,
        "driver_start": 4,
        "first_execute_response": 9,
    }
    runner = DaytonaRLMRunner(
        lm=FakeLmSequence(
            [code_block(f"summary = {summary!r}\nSUBMIT(summary=summary)")]
        ),
        runtime=FakeRuntime(session),
        output_dir=tmp_path,
    )

    result = runner.run(repo=None, task="report timings")

    assert result.summary.phase_timings_ms == session.phase_timings_ms


def test_runner_externalizes_structured_conversation_history(tmp_path: Path):
    summary = (
        "The run used structured prior-turn history from the sandbox prompt store."
    )
    session = FakeRunSession(
        steps=[FakeStep(response=make_response(final_value={"summary": summary}))]
    )
    fake_lm = FakeLmSequence(
        [code_block(f"summary = {summary!r}\nSUBMIT(summary=summary)")]
    )
    runner = DaytonaRLMRunner(
        lm=fake_lm,
        runtime=FakeRuntime(session),
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
        lm=FakeLmSequence([code_block("summary = 'unused'\nSUBMIT(summary=summary)")]),
        runtime=FakeRuntime(FakeRunSession()),
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
    session = FakeRunSession(
        steps=[FakeStep(response=make_response(final_value={"summary": summary}))]
    )
    runtime = FakeRuntime(session)

    result = run_daytona_rlm_pilot(
        repo="https://github.com/example/repo.git",
        task="persist run",
        runtime=runtime,
        output_dir=tmp_path,
        lm=FakeLmSequence(
            [code_block(f"summary = {summary!r}\nSUBMIT(summary=summary)")]
        ),
    )

    assert result.result_path is not None
    assert Path(result.result_path).exists()
