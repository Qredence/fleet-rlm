"""Versioned Daytona image recipe and its packaged dependency lock."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from daytona import Image, Resources
from daytona.common.image import SupportedPythonSeries

# Repository-owned Turn environment; provider availability is checked by preflight.
SNAPSHOT_NAME = "fleet-rlm-python313-v14"
PYTHON_VERSION: SupportedPythonSeries = "3.13"
PYTHON_BASE_IMAGE = "python:3.13-slim-bookworm@sha256:e32be0e55814a08977501ca863271d9bd02443710db8f2afaf8372e2c4508530"
SYSTEM_PACKAGES = ("git", "nodejs", "npm", "ripgrep", "jq", "ca-certificates")
NPM_GLOBAL_PACKAGES = ("pnpm@10.18.3", "typescript@5.9.3")
LOCK_PATH = Path(__file__).with_name("snapshot-requirements.txt")


def _locked_packages(path: Path) -> tuple[tuple[str, str], ...]:
    """Read package pins from uv's multiline, hash-locked requirements format."""
    packages: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(("#", " ", "\t")) or "==" not in line:
            continue
        name, _, version = line.split()[0].partition("==")
        packages.append((name, version))
    return tuple(packages)


PACKAGE_PINS = _locked_packages(LOCK_PATH)
CPU_FLOOR = 4
MEMORY_FLOOR = 8
DISK_FLOOR = 8


@dataclass(frozen=True)
class SnapshotDefinition:
    """The declared recipe for one execution environment, including its Python lock."""

    name: str
    cpu: int
    memory: int
    disk: int
    python_version: SupportedPythonSeries
    packages: tuple[tuple[str, str], ...]
    base_image: str = PYTHON_BASE_IMAGE
    system_packages: tuple[str, ...] = SYSTEM_PACKAGES
    npm_global_packages: tuple[str, ...] = NPM_GLOBAL_PACKAGES

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical dictionary representation of the snapshot definition."""
        return {
            "name": self.name,
            "resources": {"cpu": self.cpu, "memory": self.memory, "disk": self.disk},
            "image": {"base": self.base_image, "python": self.python_version},
            "system_packages": list(self.system_packages),
            "npm_global_packages": list(self.npm_global_packages),
            "packages": dict(sorted(self.packages)),
            "build_lock_sha256": hashlib.sha256(LOCK_PATH.read_bytes()).hexdigest(),
        }

    def digest(self) -> str:
        """Return the SHA-256 hex digest of the canonical snapshot definition."""
        canonical = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def resources(self) -> Resources:
        """Return the Daytona Resources object for this snapshot definition."""
        return Resources(cpu=self.cpu, memory=self.memory, disk=self.disk)

    def image(self) -> Image:
        """Return the Daytona Image builder for this snapshot definition."""
        return (
            Image.base(self.base_image)
            .run_commands(
                "apt-get update && apt-get install -y --no-install-recommends "
                + " ".join(self.system_packages)
                + " && rm -rf /var/lib/apt/lists/*",
                "npm install --global " + " ".join(self.npm_global_packages),
            )
            .pip_install_from_requirements(
                str(LOCK_PATH), extra_options="--require-hashes --only-binary=:all: --no-cache-dir"
            )
        )


DEFAULT_DEFINITION = SnapshotDefinition(
    name=SNAPSHOT_NAME,
    cpu=CPU_FLOOR,
    memory=MEMORY_FLOOR,
    disk=DISK_FLOOR,
    python_version=PYTHON_VERSION,
    packages=PACKAGE_PINS,
)
