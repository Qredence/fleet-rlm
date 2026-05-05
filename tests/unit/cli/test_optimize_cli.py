"""Tests for the optimize CLI command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from fleet_rlm.cli.fleet_cli import app

runner = CliRunner()


@pytest.fixture
def ten_example_dataset(tmp_path: Path) -> Path:
    """Create a 10-example JSONL dataset for the longcot module."""
    path = tmp_path / "longcot_10.jsonl"
    rows = [
        {
            "question_id": f"cli_q_{i}",
            "domain": "math",
            "difficulty": "easy",
            "question": f"What is {i} + {i + 1}?",
            "answer": str(2 * i + 1),
        }
        for i in range(10)
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    return path


def test_optimize_list_includes_longcot_reasoner() -> None:
    """VAL-MOD-001: CLI list output contains registered module."""
    result = runner.invoke(app, ["optimize", "list"])
    assert result.exit_code == 0
    assert "longcot-reasoner" in result.output


# -- VAL-GEPA-005 / VAL-CROSS-002: CLI e2e ----------------------------------


class TestOptimizeCliEndToEnd:
    def test_optimize_longcot_reasoner_light_exits_zero(
        self,
        ten_example_dataset: Path,
        tmp_path: Path,
    ) -> None:
        """CLI: fleet-rlm optimize longcot-reasoner dataset.jsonl --auto light exits 0."""
        output_path = tmp_path / "artifact.json"
        fake_result = {
            "train_examples": 8,
            "validation_examples": 2,
            "validation_score": 0.85,
            "output_path": str(output_path),
            "manifest_path": str(output_path.with_suffix(".manifest.json")),
            "optimizer": "GEPA",
            "program_spec": "fleet_rlm.runtime.agent.signatures:LongCoTQASignature",
            "module_slug": "longcot-reasoner",
            "evaluation_results": [],
            "prompt_snapshots": [],
        }

        with patch(
            "fleet_rlm.quality.optimization_runner.run_module_optimization",
            return_value=fake_result,
        ):
            result = runner.invoke(
                app,
                [
                    "optimize",
                    "longcot-reasoner",
                    str(ten_example_dataset),
                    "--auto",
                    "light",
                ],
            )

        assert result.exit_code == 0, f"stderr: {result.output}"
        assert "longcot-reasoner" in result.output
        assert str(output_path) in result.output

    def test_optimize_longcot_reasoner_with_report_flag(
        self,
        ten_example_dataset: Path,
        tmp_path: Path,
    ) -> None:
        """CLI: --report prints markdown summary with Output Path and Validation Score."""
        output_path = tmp_path / "artifact.json"
        fake_result = {
            "train_examples": 8,
            "validation_examples": 2,
            "validation_score": 0.92,
            "output_path": str(output_path),
            "manifest_path": str(output_path.with_suffix(".manifest.json")),
            "optimizer": "GEPA",
            "program_spec": "fleet_rlm.runtime.agent.signatures:LongCoTQASignature",
            "module_slug": "longcot-reasoner",
            "evaluation_results": [],
            "prompt_snapshots": [],
        }

        with patch(
            "fleet_rlm.quality.optimization_runner.run_module_optimization",
            return_value=fake_result,
        ):
            result = runner.invoke(
                app,
                [
                    "optimize",
                    "longcot-reasoner",
                    str(ten_example_dataset),
                    "--auto",
                    "light",
                    "--report",
                ],
            )

        assert result.exit_code == 0
        assert "Optimization Report: longcot-reasoner" in result.output
        assert "Output Path:" in result.output
        assert "Manifest Path:" in result.output
        assert "Validation Score:" in result.output
        assert "0.92" in result.output

    def test_optimize_rejects_invalid_auto_value(
        self,
        ten_example_dataset: Path,
    ) -> None:
        """CLI rejects --auto values other than light/medium/heavy."""
        result = runner.invoke(
            app,
            [
                "optimize",
                "longcot-reasoner",
                str(ten_example_dataset),
                "--auto",
                "ultra",
            ],
        )
        assert result.exit_code == 1
        assert "light, medium, or heavy" in result.output

    def test_optimize_rejects_missing_dataset(self) -> None:
        """CLI exits non-zero when dataset file does not exist."""
        result = runner.invoke(
            app,
            [
                "optimize",
                "longcot-reasoner",
                "/nonexistent/dataset.jsonl",
                "--auto",
                "light",
            ],
        )
        assert result.exit_code == 1
        assert (
            "not found" in result.output.lower()
            or "Dataset file not found" in result.output
        )
