from __future__ import annotations

from types import SimpleNamespace

from fleet_rlm.quality.gepa_evidence import best_candidate_index
from fleet_rlm.quality.rlm_gepa import (
    DaytonaRLMProposalProgram,
    RLMInstructionProposer,
    _file_preview,
)


def test_best_candidate_index_ignores_out_of_bounds_explicit_index() -> None:
    assert best_candidate_index([0.1, 0.9, 0.5], explicit_best_idx=99) == 1


def test_file_preview_reads_only_prefix_for_large_files(tmp_path) -> None:
    bundle = tmp_path / "bundle.jsonl"
    bundle.write_text("x" * 200, encoding="utf-8")

    preview = _file_preview(str(bundle), max_chars=50)

    assert preview["status"] == "ok"
    assert preview["size_bytes"] == 200
    assert len(preview["preview"]) == 50
    assert preview["truncated"] is True


def test_rlm_instruction_proposer_calls_program_with_reflective_payload() -> None:
    calls: list[dict] = []

    def proposal_program(**kwargs):
        calls.append(kwargs)
        return {"revised_instructions": "new skill text"}

    proposer = RLMInstructionProposer(
        proposal_program=proposal_program,
        trace_bundle_paths=["traces/bundle.jsonl"],
        candidate_history=[{"score": 0.5}],
    )

    result = proposer(
        candidate={"skill": "old skill text"},
        reflective_dataset={"skill": [{"Feedback": "Missing formula prefix", "score": 0.2}]},
        components_to_update=["skill"],
    )

    assert result == {"skill": "new skill text"}
    assert calls[0]["component_name"] == "skill"
    assert calls[0]["current_instructions"] == "old skill text"
    assert "Missing formula prefix" in calls[0]["reflective_dataset"]
    assert calls[0]["trace_bundle_paths"] == ["traces/bundle.jsonl"]
    assert "traces/bundle.jsonl" in calls[0]["trace_bundle_previews"]


def test_daytona_rlm_proposal_program_uses_interpreter_factory(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeInterpreter:
        def __enter__(self):
            calls.append({"event": "enter"})
            return self

        def __exit__(self, *args):
            calls.append({"event": "exit"})

    class FakeRLM:
        def __init__(self, signature, **kwargs):
            calls.append({"signature": signature, **kwargs})

        def __call__(self, **payload):
            calls.append(payload)
            return SimpleNamespace(revised_instructions="daytona skill")

    import types

    fake_dspy = types.SimpleNamespace(RLM=FakeRLM)
    monkeypatch.setitem(__import__("sys").modules, "dspy", fake_dspy)

    program = DaytonaRLMProposalProgram(
        signature_factory=lambda: "proposal-signature",
        interpreter_factory=FakeInterpreter,
    )

    result = program(component_name="skill", current_instructions="old")

    assert result.revised_instructions == "daytona skill"
    assert calls[0]["event"] == "enter"
    assert calls[-1]["event"] == "exit"
