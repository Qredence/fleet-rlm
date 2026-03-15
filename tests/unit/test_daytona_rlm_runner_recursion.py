from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_rlm.infrastructure.providers.daytona.protocol import HostCallbackRequest
from fleet_rlm.infrastructure.providers.daytona.runner import DaytonaRLMRunner
from fleet_rlm.infrastructure.providers.daytona.types import RecursiveTaskSpec, RolloutBudget
from tests.unit.fixtures_daytona import (
    FakeLmSequence,
    FakeRunSession,
    FakeRuntime,
    FakeStep,
    code_block,
    make_response,
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
    session = FakeRunSession(
        steps=[
            FakeStep(
                callbacks=[callback],
                response=make_response(
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
    main_lm = FakeLmSequence(
        [
            code_block(
                "tasks = []\n"
                "results = llm_query_batched(tasks)\n"
                'summary = "The host-side batched subqueries completed and the root '
                'loop synthesized their results."\n'
                "SUBMIT(summary=summary)"
            )
        ]
    )
    delegate_lm = FakeLmSequence(
        [
            "README summary from host sub-LLM.",
            "pyproject explanation from host sub-LLM.",
        ]
    )
    runner = DaytonaRLMRunner(
        lm=main_lm,
        delegate_lm=delegate_lm,
        runtime=FakeRuntime(session),
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
    root_session = FakeRunSession(
        steps=[
            FakeStep(
                callbacks=[callback],
                response=make_response(
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
    child_session = FakeRunSession(
        steps=[
            FakeStep(
                response=make_response(
                    final_value={
                        "output": {"summary": "Raw child payload that needs synthesis."}
                    }
                )
            )
        ]
    )
    runner = DaytonaRLMRunner(
        lm=FakeLmSequence(
            [
                code_block(
                    "child = rlm_query({'task': 'Inspect the README excerpt.', "
                    "'label': 'README child', 'source': {'kind': 'file_slice', "
                    "'path': 'README.md', 'start_line': 1, 'end_line': 2, "
                    "'preview': '# Example\\nIntro line'}})\n"
                    'summary = "The root run incorporated the recursive child summary."\n'
                    "SUBMIT(summary=summary)"
                ),
                code_block(
                    "payload = {'summary': 'Raw child payload that needs synthesis.'}\n"
                    "SUBMIT(output=payload)"
                ),
            ]
        ),
        runtime=FakeRuntime([root_session, child_session]),
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
    root_session = FakeRunSession(
        steps=[
            FakeStep(response=make_response(stdout="Need deeper evidence.")),
            FakeStep(
                response=make_response(
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
    child_session = FakeRunSession(
        steps=[
            FakeStep(
                response=make_response(
                    final_value={"output": {"summary": "Raw child evidence"}}
                )
            )
        ]
    )
    fake_lm = FakeLmSequence(
        [
            code_block("notes = 'look deeper'"),
            code_block(
                "payload = {'summary': 'Raw child evidence'}\nSUBMIT(output=payload)"
            ),
            code_block(
                'summary = "The root run consumed the recursive child summary on '
                'the next iteration."\n'
                "SUBMIT(summary=summary)"
            ),
        ]
    )
    runner = DaytonaRLMRunner(
        lm=fake_lm,
        runtime=FakeRuntime([root_session, child_session]),
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
    root_session = FakeRunSession(
        steps=[
            FakeStep(response=make_response(stdout="Need deeper evidence.")),
            FakeStep(
                response=make_response(
                    final_value={
                        "summary": "The root run finished without recursive children."
                    }
                )
            ),
        ]
    )
    fake_lm = FakeLmSequence(
        [
            code_block("notes = 'look deeper'"),
            code_block(
                'summary = "The root run finished without recursive children."\n'
                "SUBMIT(summary=summary)"
            ),
        ]
    )
    runner = DaytonaRLMRunner(
        lm=fake_lm,
        runtime=FakeRuntime(root_session),
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
    root_session = FakeRunSession(
        steps=[
            FakeStep(response=make_response(stdout="Need deeper evidence.")),
            FakeStep(
                response=make_response(
                    final_value={
                        "summary": "The root run used one child because budget capped fanout."
                    }
                )
            ),
        ]
    )
    child_session = FakeRunSession(
        steps=[
            FakeStep(
                response=make_response(
                    final_value={"output": {"summary": "Raw child evidence"}}
                )
            )
        ]
    )
    runner = DaytonaRLMRunner(
        lm=FakeLmSequence(
            [
                code_block("notes = 'look deeper'"),
                code_block(
                    "payload = {'summary': 'Raw child evidence'}\nSUBMIT(output=payload)"
                ),
                code_block(
                    'summary = "The root run used one child because budget capped fanout."\n'
                    "SUBMIT(summary=summary)"
                ),
            ]
        ),
        runtime=FakeRuntime([root_session, child_session]),
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
        runtime=FakeRuntime(FakeRunSession()),
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
        runtime=FakeRuntime(FakeRunSession()),
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
        runtime=FakeRuntime(FakeRunSession()),
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
