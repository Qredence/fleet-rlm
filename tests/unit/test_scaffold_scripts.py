"""Regression tests for scaffolded long-context scripts."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from fleet_rlm import scaffold


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module(script_path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_orchestrate_script_runs_directly_after_scaffold_install(tmp_path: Path):
    """orchestrate.py should run via direct script execution."""
    target = tmp_path / "claude"
    scaffold.install_skills(target, force=False)

    script_path = target / "skills" / "rlm-long-context" / "scripts" / "orchestrate.py"
    result = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Orchestrate RLM workflow with optimizations" in result.stdout


def test_semantic_chunk_json_list_ranges_are_valid(tmp_path: Path):
    """chunk_json should produce valid non-negative ranges for list JSON."""
    script_path = (
        REPO_ROOT
        / "src"
        / "fleet_rlm"
        / "_scaffold"
        / "skills"
        / "rlm-long-context"
        / "scripts"
        / "semantic_chunk.py"
    )
    semantic_chunk = _load_module(script_path, "semantic_chunk_list_test")

    content = json.dumps(
        [
            {"a": 1, "label": "first"},
            {"b": 2, "label": "second"},
            {"c": 3, "label": "third"},
        ]
    )

    chunks = semantic_chunk.chunk_json(content, max_size=45)
    assert chunks
    assert all(start >= 0 and end > start for start, end, _ in chunks)

    output_dir = tmp_path / "list_chunks"
    paths = semantic_chunk.write_chunks(content, chunks, str(output_dir), prefix="list")
    assert paths

    for path in paths:
        body = Path(path).read_text().split("\n", 1)[1]
        assert body.strip() != ""


def test_semantic_chunk_json_dict_ranges_are_valid(tmp_path: Path):
    """chunk_json should produce valid non-negative ranges for dict JSON."""
    script_path = (
        REPO_ROOT
        / "src"
        / "fleet_rlm"
        / "_scaffold"
        / "skills"
        / "rlm-long-context"
        / "scripts"
        / "semantic_chunk.py"
    )
    semantic_chunk = _load_module(script_path, "semantic_chunk_dict_test")

    content = json.dumps(
        {
            "alpha": {"value": 1, "details": "first section"},
            "beta": {"value": 2, "details": "second section"},
            "gamma": {"value": 3, "details": "third section"},
        }
    )

    chunks = semantic_chunk.chunk_json(content, max_size=70)
    assert chunks
    assert all(start >= 0 and end > start for start, end, _ in chunks)

    output_dir = tmp_path / "dict_chunks"
    paths = semantic_chunk.write_chunks(content, chunks, str(output_dir), prefix="dict")
    assert paths

    for path in paths:
        body = Path(path).read_text().split("\n", 1)[1]
        assert body.strip() != ""
