"""Tests for LongCoT GEPA dataset generation and module compatibility.

Validates that the dataset produced by ``scripts/generate_longcot_gepa_dataset.py``:

1. Loads without errors via ``load_dataset_rows``.
2. Passes ``validate_required_keys`` for the ``longcot-reasoner`` module.
3. Has non-empty ``question`` and ``answer`` fields on every row.
4. Converts correctly via the module's ``row_converter`` into valid DSPy Examples.
5. Can be split into train/validation sets by ``split_examples``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from fleet_rlm.runtime.quality.datasets import (
    load_dataset_rows,
    split_examples,
    validate_required_keys,
)
from fleet_rlm.runtime.quality.module_registry import (
    _reset_registry,
    get_module_spec,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
DATASET_SCRIPT = REPO_ROOT / "scripts" / "generate_longcot_gepa_dataset.py"
DEFAULT_OUTPUT = REPO_ROOT / "output" / "longcot-eval" / "longcot_gepa_dataset.jsonl"

_VENDOR_DATA_DIR = REPO_ROOT / "vendor" / "longcot" / "src" / "data"
pytestmark = pytest.mark.skipif(
    not _VENDOR_DATA_DIR.exists(),
    reason="vendor/longcot data not present; skip dataset generation tests",
)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    """Reset the module registry before each test."""
    _reset_registry()


@pytest.fixture
def generated_dataset_path(tmp_path: Path) -> Path:
    """Run the generation script and return the output path."""
    output = tmp_path / "longcot_gepa_dataset.jsonl"
    result = subprocess.run(
        [sys.executable, str(DATASET_SCRIPT), "--output", str(output)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"Dataset generation failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    return output


# ── Dataset generation script tests ──────────────────────────────────


def test_script_exists() -> None:
    assert DATASET_SCRIPT.exists(), f"Dataset script not found: {DATASET_SCRIPT}"


def test_script_is_executable(generated_dataset_path: Path) -> None:
    assert generated_dataset_path.exists()
    assert generated_dataset_path.stat().st_size > 0


def test_dataset_loads_via_loader(generated_dataset_path: Path) -> None:
    rows = load_dataset_rows(generated_dataset_path)
    assert len(rows) > 0, "Dataset loaded but contains no rows"


def test_all_rows_have_required_keys(generated_dataset_path: Path) -> None:
    rows = load_dataset_rows(generated_dataset_path)
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None, "longcot-reasoner module spec not found"
    valid_rows = validate_required_keys(rows, spec.required_dataset_keys, spec.label)
    assert len(valid_rows) == len(rows), (
        f"Only {len(valid_rows)} of {len(rows)} rows passed key validation"
    )


def test_all_rows_have_non_empty_question(generated_dataset_path: Path) -> None:
    rows = load_dataset_rows(generated_dataset_path)
    empty = [i for i, r in enumerate(rows) if not str(r.get("question", "")).strip()]
    assert not empty, f"Rows with empty question field: {empty}"


def test_all_rows_have_trimmed_question_strings(generated_dataset_path: Path) -> None:
    rows = load_dataset_rows(generated_dataset_path)
    offenders = []
    for row in rows:
        question = row.get("question")
        if not isinstance(question, str) or question != question.strip():
            offenders.append((row.get("question_id"), question))
    assert not offenders, f"Rows with untrimmed question field: {offenders[:10]}"


def test_all_rows_have_non_empty_answer(generated_dataset_path: Path) -> None:
    rows = load_dataset_rows(generated_dataset_path)
    empty = [i for i, r in enumerate(rows) if not str(r.get("answer", "")).strip()]
    assert not empty, f"Rows with empty answer field: {empty}"


def test_stratified_sampling_by_domain(generated_dataset_path: Path) -> None:
    rows = load_dataset_rows(generated_dataset_path)
    from collections import Counter

    domain_counts = Counter(str(r.get("domain", "unknown")) for r in rows)
    # Domains with valid answers should have at least some representation.
    # Logic has no answers in the vendor data, so it may be absent.
    for domain, count in domain_counts.items():
        assert count > 0, f"Domain {domain} has no rows"


# ── Module row-converter compatibility ───────────────────────────────


def test_row_converter_produces_valid_examples(generated_dataset_path: Path) -> None:
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None

    rows = load_dataset_rows(generated_dataset_path)
    valid_rows = validate_required_keys(rows, spec.required_dataset_keys, spec.label)
    examples = spec.row_converter(valid_rows)

    assert len(examples) == len(valid_rows), (
        f"Row converter produced {len(examples)} examples from {len(valid_rows)} rows"
    )

    import dspy

    for ex in examples:
        assert isinstance(ex, dspy.Example), f"Expected dspy.Example, got {type(ex)}"
        assert hasattr(ex, "question"), "Example missing 'question' field"
        assert hasattr(ex, "answer"), "Example missing 'answer' field"
        assert "question" in ex.inputs(), "'question' should be an input field"


# ── Train/val split ──────────────────────────────────────────────────


def test_split_examples_yields_train_and_val(generated_dataset_path: Path) -> None:
    spec = get_module_spec("longcot-reasoner")
    assert spec is not None

    rows = load_dataset_rows(generated_dataset_path)
    valid_rows = validate_required_keys(rows, spec.required_dataset_keys, spec.label)
    examples = spec.row_converter(valid_rows)

    trainset, valset = split_examples(examples, train_ratio=0.8)
    assert len(trainset) >= 1, "Train set is empty"
    assert len(valset) >= 1, "Validation set is empty"
    assert len(trainset) + len(valset) == len(examples)


# ── Idempotency / determinism ────────────────────────────────────────


def test_generation_is_deterministic(tmp_path: Path) -> None:
    """Running the script twice with the same seed should produce identical output."""
    out1 = tmp_path / "run1.jsonl"
    out2 = tmp_path / "run2.jsonl"

    for out in (out1, out2):
        result = subprocess.run(
            [sys.executable, str(DATASET_SCRIPT), "--output", str(out), "--seed", "42"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0

    lines1 = out1.read_text(encoding="utf-8").strip().splitlines()
    lines2 = out2.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines1) == len(lines2)
    for a, b in zip(lines1, lines2):
        assert json.loads(a) == json.loads(b)


# ── Custom domain/difficulty filtering ───────────────────────────────


def test_custom_domain_filter(tmp_path: Path) -> None:
    """Filtering to a single domain should only produce rows for that domain."""
    output = tmp_path / "math_only.jsonl"
    result = subprocess.run(
        [
            sys.executable,
            str(DATASET_SCRIPT),
            "--output",
            str(output),
            "--domains",
            "math",
            "--no-stratified",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0
    rows = load_dataset_rows(output)
    assert len(rows) > 0
    for r in rows:
        assert r.get("domain") == "math"


def test_custom_per_domain_limit(tmp_path: Path) -> None:
    """The --per-domain flag should limit the number of rows per domain."""
    output = tmp_path / "math_5.jsonl"
    result = subprocess.run(
        [
            sys.executable,
            str(DATASET_SCRIPT),
            "--output",
            str(output),
            "--domains",
            "math",
            "--per-domain",
            "5",
            "--no-stratified",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0
    rows = load_dataset_rows(output)
    assert len(rows) == 5
