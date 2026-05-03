"""Unit tests for the LongCoT evaluation helper script."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


_BLOCKSWORLD_PROMPT = """
Example:
Initial state: [[9], [8], []]
Goal state: [[], [8], [9]]

Puzzle instance:

Initial state: [[0], [1, 2], []]
Goal state: [[], [1], [2, 0]]
Number of blocks: 3
Number of stacks: 3

Find a sequence of moves that will transform the initial state into the goal state.
"""


def _load_longcot_script() -> ModuleType:
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "run_longcot_eval.py"
    spec = importlib.util.spec_from_file_location("run_longcot_eval", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extract_solution_candidate_balances_nested_moves() -> None:
    mod = _load_longcot_script()

    assert (
        mod._extract_solution_candidate(
            "thinking...\nsolution = [[2, 1, 2], [0, 0, 2]]\nextra"
        )
        == "solution = [[2, 1, 2], [0, 0, 2]]"
    )


def test_extract_solution_candidate_rejects_missing_literal() -> None:
    mod = _load_longcot_script()

    assert mod._extract_solution_candidate("Found valid solution with 2 moves") is None


def test_extract_solution_candidate_rejects_incomplete_literal() -> None:
    mod = _load_longcot_script()

    assert mod._extract_solution_candidate("solution = [[2, 1, 2]") is None


def test_contains_rlm_failure_text_detects_child_and_adapter_failures() -> None:
    mod = _load_longcot_script()

    assert mod._contains_rlm_failure_text(
        "{'reason': 'child_error', 'error': 'failed'}"
    )
    assert mod._contains_rlm_failure_text(
        "AdapterParseError: Expected to find output fields"
    )
    assert mod._contains_rlm_failure_text("verification_status='needs_human_review'")
    assert not mod._contains_rlm_failure_text("solution = [[2, 1, 2]]")


def test_blocksworld_validation_accepts_valid_solution() -> None:
    mod = _load_longcot_script()

    valid, error = mod._validate_blocksworld_solution(
        _BLOCKSWORLD_PROMPT,
        "solution = [[2, 1, 2], [0, 0, 2]]",
    )

    assert valid is True
    assert error == "valid"


def test_blocksworld_validation_rejects_placeholder_solution() -> None:
    mod = _load_longcot_script()

    valid, error = mod._validate_blocksworld_solution(
        _BLOCKSWORLD_PROMPT,
        "solution = []",
    )

    assert valid is False
    assert "Empty solution" in error


def test_blocksworld_validation_rejects_length_two_moves() -> None:
    mod = _load_longcot_script()

    valid, error = mod._validate_blocksworld_solution(
        _BLOCKSWORLD_PROMPT,
        "solution = [[1, 2]]",
    )

    assert valid is False
    assert "must be [block, from_stack, to_stack]" in error


def test_evaluate_rlm_answer_rejects_verifier_prose_without_literal() -> None:
    mod = _load_longcot_script()

    status, answer, error = mod._evaluate_rlm_answer(
        _BLOCKSWORLD_PROMPT,
        "Found valid solution with 2 moves",
    )

    assert status == "error"
    assert answer == "Found valid solution with 2 moves"
    assert "did not contain" in error


def test_evaluate_rlm_answer_rejects_failure_text_even_with_literal() -> None:
    mod = _load_longcot_script()

    status, answer, error = mod._evaluate_rlm_answer(
        _BLOCKSWORLD_PROMPT,
        "verification_status=needs_human_review\nsolution = [[2, 1, 2], [0, 0, 2]]",
    )

    assert status == "error"
    assert "needs_human_review" in answer
    assert "runtime failure text" in error


def test_select_questions_for_slice_preserves_manifest_order() -> None:
    mod = _load_longcot_script()

    questions = [
        {"question_id": "logic-2", "domain": "logic"},
        {"question_id": "logic-1", "domain": "logic"},
        {"question_id": "cs-1", "domain": "cs"},
    ]
    manifest = {
        "domains": {
            "logic": ["logic-1", "logic-2"],
            "cs": ["cs-1"],
            "chemistry": [],
            "chess": [],
            "math": [],
        }
    }

    selected = mod._select_questions_for_slice(questions, manifest)

    assert [question["question_id"] for question in selected] == [
        "logic-1",
        "logic-2",
        "cs-1",
    ]


def test_build_rlm_prompt_appends_tips_and_format_reminder() -> None:
    mod = _load_longcot_script()

    prompt = mod._build_rlm_prompt("Solve this.", "Avoid brute force.")

    assert prompt.startswith("Solve this.")
    assert "RLM EXECUTION TIPS" in prompt
    assert "Avoid brute force." in prompt
    assert "IMPORTANT: Your final answer MUST be submitted using SUBMIT()" in prompt


def test_configure_rlm_lm_openrouter_sets_dspy_env(monkeypatch) -> None:
    mod = _load_longcot_script()
    monkeypatch.setattr(
        mod,
        "_load_longcot_config",
        lambda config_name: (
            {
                "provider": "openrouter",
                "model": "deepseek/deepseek-v4-flash",
                "api_key": "test-openrouter-key",
                "llm_kwargs": {"max_completion_tokens": 64000},
            },
            Path("/tmp/or_deepseek_v4_flash.yaml"),
        ),
    )
    monkeypatch.delenv("OPENROUTER_API_BASE", raising=False)
    monkeypatch.delenv("DSPY_LM_MODEL", raising=False)
    monkeypatch.delenv("DSPY_LM_API_BASE", raising=False)
    monkeypatch.delenv("DSPY_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DSPY_LM_MAX_TOKENS", raising=False)

    model_label = mod._configure_rlm_lm("or_deepseek_v4_flash")

    assert model_label == "openrouter/deepseek/deepseek-v4-flash"
    assert mod.os.environ["DSPY_LM_MODEL"] == model_label
    assert mod.os.environ["DSPY_LM_API_BASE"] == "https://openrouter.ai/api/v1"
    assert mod.os.environ["DSPY_LLM_API_KEY"] == "test-openrouter-key"
    assert mod.os.environ["DSPY_LM_MAX_TOKENS"] == "64000"


def test_load_slice_manifest_reads_benchmark_file() -> None:
    mod = _load_longcot_script()
    slice_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "benchmarks"
        / "longcot_mini_stratified_100.json"
    )

    manifest = mod._load_slice_manifest(slice_path)

    assert manifest is not None
    assert manifest["name"] == "longcot-mini-stratified-100"
    assert manifest["seed"] == 42
    assert set(manifest["domains"]) == {"logic", "cs", "chemistry", "chess", "math"}
    assert sum(len(ids) for ids in manifest["domains"].values()) == 100


def test_openrouter_deepseek_v4_flash_config_exists() -> None:
    root = Path(__file__).resolve().parents[2]
    config_path = (
        root / "vendor" / "longcot" / "src" / "configs" / "or_deepseek_v4_flash.yaml"
    )

    data = config_path.read_text(encoding="utf-8")

    assert 'provider: "openrouter"' in data
    assert 'model: "deepseek/deepseek-v4-flash"' in data
    assert "${OPENROUTER_API_KEY}" in data
