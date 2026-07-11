"""Fail-closed loading for workspace-resolved optimization artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class ActiveArtifact:
    """An already-authorized activation resolved by the persistence boundary."""

    target_kind: Literal["module", "skill"]
    target_id: str
    path: Path
    sha256: str


class ActiveArtifactError(ValueError):
    """Raised when an activated artifact no longer matches its approved record."""


def read_verified_artifact(artifact: ActiveArtifact) -> str:
    """Read an activated artifact after path, digest, and codec validation."""
    payload = artifact.path.read_bytes()
    actual = hashlib.sha256(payload).hexdigest()
    if actual != artifact.sha256:
        raise ActiveArtifactError("Active artifact checksum does not match its approved version.")
    text = payload.decode("utf-8")
    if artifact.target_kind == "module":
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ActiveArtifactError("Module artifact must contain a JSON object.")
    return text


def load_module_state(module: object, artifact: ActiveArtifact | None) -> object:
    """Apply state to a fresh factory-built module; None is a literal no-op."""
    if artifact is None:
        return module
    if artifact.target_kind != "module":
        raise ActiveArtifactError("A Skill artifact cannot be loaded into a module.")
    read_verified_artifact(artifact)
    loader = getattr(module, "load", None)
    if not callable(loader):
        raise ActiveArtifactError("Managed module does not support DSPy state loading.")
    loader(str(artifact.path))
    return module


def resolve_skill_markdown(default_markdown: str, artifact: ActiveArtifact | None) -> str:
    """Return activated Skill Markdown; None preserves the catalog default exactly."""
    if artifact is None:
        return default_markdown
    if artifact.target_kind != "skill":
        raise ActiveArtifactError("A module artifact cannot be loaded as a Skill.")
    return read_verified_artifact(artifact)
