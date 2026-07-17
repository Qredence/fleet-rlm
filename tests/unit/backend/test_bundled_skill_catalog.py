from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

CATALOG_ROOT = Path(__file__).resolve().parents[3] / "src" / "fleet_rlm" / "skills" / "skills"


def _load_script(name: str, relative_path: str):
    path = CATALOG_ROOT / relative_path
    module = ModuleType(name)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
    return module


def _frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    _, payload, _ = text.split("---", 2)
    parsed = yaml.safe_load(payload)
    assert isinstance(parsed, dict)
    return parsed


def test_bundled_catalog_contains_only_runtime_skills() -> None:
    directories = {path.name for path in CATALOG_ROOT.iterdir() if path.is_dir() and not path.name.startswith("__")}

    assert directories == {"long-context", "workspace-files"}
    assert _frontmatter(CATALOG_ROOT / "long-context" / "SKILL.md")["metadata"] == {"version": "2.0.0"}
    assert _frontmatter(CATALOG_ROOT / "workspace-files" / "SKILL.md")["metadata"] == {"version": "1.0.0"}


def test_long_context_scripts_have_no_pickle_or_hidden_state_defaults() -> None:
    scripts = tuple(sorted((CATALOG_ROOT / "long-context" / "scripts").glob("*.py")))

    assert scripts
    for script in scripts:
        text = script.read_text(encoding="utf-8")
        assert "pickle" not in text
        assert ".codex" not in text


def test_semantic_chunk_cli_reads_explicit_input_and_writes_deterministic_chunks(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("# One\nalpha\n# Two\nbeta\n", encoding="utf-8")
    output = tmp_path / "chunks"
    script = CATALOG_ROOT / "long-context" / "scripts" / "semantic_chunk.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--input",
            str(source),
            "--type",
            "markdown",
            "--max-size",
            "100",
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.splitlines() == [
        "chunk_0000.txt\t0\t12",
        "chunk_0001.txt\t12\t23",
    ]
    assert [path.read_text(encoding="utf-8") for path in sorted(output.glob("*.txt"))] == [
        "# One\nalpha\n",
        "# Two\nbeta\n",
    ]


def test_semantic_chunk_refuses_to_mix_with_an_existing_output_directory(tmp_path: Path) -> None:
    semantic_chunk = _load_script(
        "fleet_bundled_semantic_chunk",
        "long-context/scripts/semantic_chunk.py",
    )
    output = tmp_path / "chunks"
    output.mkdir()
    stale = output / "chunk_0000.txt"
    stale.write_text("stale", encoding="utf-8")

    with pytest.raises(ValueError, match="output directory must be empty"):
        semantic_chunk.write_chunks([(0, 5, "fresh")], str(output), "chunk")

    assert stale.read_text(encoding="utf-8") == "stale"


def test_semantic_chunk_applies_overlap_to_large_structural_sections() -> None:
    semantic_chunk = _load_script(
        "fleet_bundled_semantic_chunk_overlap",
        "long-context/scripts/semantic_chunk.py",
    )

    chunks = semantic_chunk.chunk_content("# Heading\nabcdefghij", "markdown", max_size=10, overlap=3)

    assert [start for start, _, _ in chunks[:2]] == [0, 7]


def test_rank_chunks_cli_reads_explicit_chunk_directory(tmp_path: Path) -> None:
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    (chunks / "chunk_0000.txt").write_text("alpha target\n", encoding="utf-8")
    (chunks / "chunk_0001.txt").write_text("beta filler\n", encoding="utf-8")
    script = CATALOG_ROOT / "long-context" / "scripts" / "rank_chunks.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--chunks-dir",
            str(chunks),
            "--query",
            "alpha target",
            "--top-k",
            "1",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout == f"{chunks / 'chunk_0000.txt'}\t153.846154\n"


def test_workspace_skill_preserves_artifact_commit_boundary() -> None:
    skill = (CATALOG_ROOT / "workspace-files" / "SKILL.md").read_text(encoding="utf-8")
    reference = (CATALOG_ROOT / "workspace-files" / "references" / "filesystem-contract.md").read_text(encoding="utf-8")

    assert "create_artifact" in skill
    assert "Turn Commit" in skill
    assert "sessions/<session_uuid>/workspace" in reference
    assert "runs/<run_uuid>/artifacts" in reference
    assert "does not publish" in reference


def test_workspace_binary_asset_is_disclosed_as_base64() -> None:
    from fleet_rlm.skills.loader import load_skill_directory

    loaded = load_skill_directory(CATALOG_ROOT / "workspace-files")
    resource = next(item for item in loaded["resources"] if item.path == "assets/skill-marker.pdf")

    assert resource.descriptor.encoding == "base64"
    assert resource.descriptor.media_type == "application/pdf"
    assert resource.descriptor.byte_size == len(resource.body)
    assert base64.b64decode(base64.b64encode(resource.body)) == resource.body
