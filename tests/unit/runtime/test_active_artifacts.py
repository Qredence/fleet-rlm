from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from fleet_rlm.runtime.active_artifacts import (
    ActiveArtifact,
    ActiveArtifactError,
    load_module_state,
    resolve_skill_markdown,
)


def _artifact(path: Path, *, kind: str) -> ActiveArtifact:
    payload = path.read_bytes()
    return ActiveArtifact(  # type: ignore[arg-type]
        target_kind=kind, target_id="target", path=path, sha256=hashlib.sha256(payload).hexdigest()
    )


def test_disabled_activation_is_literal_noop() -> None:
    module = object()
    assert load_module_state(module, None) is module
    assert resolve_skill_markdown("default", None) == "default"


def test_skill_markdown_is_checksum_verified(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    path.write_text("optimized", encoding="utf-8")
    artifact = _artifact(path, kind="skill")
    assert resolve_skill_markdown("default", artifact) == "optimized"
    path.write_text("tampered", encoding="utf-8")
    with pytest.raises(ActiveArtifactError, match="checksum"):
        resolve_skill_markdown("default", artifact)


def test_module_artifact_requires_json_object(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("[]", encoding="utf-8")
    artifact = _artifact(path, kind="module")
    with pytest.raises(ActiveArtifactError, match="JSON object"):
        load_module_state(object(), artifact)
