"""Published-dependency cutover contract (VAL-PKG-001/002/003/004/028).

The production cognitive runtime is exactly published ``dspy==3.3.1`` and the
optimizer extra is exactly official ``gepa==0.1.4``. No base, extra, group, or
override requirement may use a VCS or direct-URL source, the lock must resolve
both packages from the registry with hashed artifacts, and the retired
Git-pin CI verification must stay absent.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[4]
PYPROJECT = ROOT / "pyproject.toml"
LOCK = ROOT / "uv.lock"
GITHUB_WORKFLOWS = ROOT / ".github" / "workflows"
CIRCLECI_CONFIG = ROOT / ".circleci" / "config.yml"

DSPY_EXACT = "dspy==3.3.1"
GEPA_EXACT = "gepa==0.1.4"

_VCS_SCHEME_MARKERS = ("git+", "hg+", "svn+", "bzr+")
_GIT_URL_MARKERS = (".git@", ".git#", "github.com")
_OBSOLETE_GIT_PIN_PATTERNS = (
    r"check-dspy-pin",
    r"check_dspy_pin",
    r"git\+https://github\.com/stanfordnlp/dspy",
    r"git\+https://github\.com/gepa-ai(/|\.git)",
    r"Git source is installable",
    r"[Rr]e-lock after confirming",
    r"upstream commit",
    r"no longer installable from GitHub",
)
_ALLOWED_LOCK_SOURCE_KEYS = {"registry", "editable"}


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return _toml(PYPROJECT)


@pytest.fixture(scope="module")
def lock() -> dict:
    return _toml(LOCK)


def _lock_package(lock: dict, name: str) -> dict:
    matches = [pkg for pkg in lock["package"] if pkg["name"] == name]
    assert len(matches) == 1, f"expected exactly one lock record for {name}, found {len(matches)}"
    return matches[0]


def _declared_requirements(pyproject: dict) -> dict[str, list[str]]:
    """Every declared requirement string, labeled by owning dependency list."""
    declared: dict[str, list[str]] = {}
    project = pyproject["project"]
    declared["project.dependencies"] = list(project.get("dependencies", []))
    for extra, requirements in project.get("optional-dependencies", {}).items():
        declared[f"project.optional-dependencies.{extra}"] = list(requirements)
    for group, requirements in pyproject.get("dependency-groups", {}).items():
        declared[f"dependency-groups.{group}"] = list(requirements)
    tool_uv = pyproject.get("tool", {}).get("uv", {})
    declared["tool.uv.override-dependencies"] = list(tool_uv.get("override-dependencies", []))
    declared["build-system.requires"] = list(pyproject.get("build-system", {}).get("requires", []))
    return declared


class TestDeclaredExactPublishedRequirements:
    """VAL-PKG-001/002: declared surfaces name exactly the certified artifacts."""

    def test_base_declares_exactly_one_exact_dspy_requirement(self, pyproject: dict) -> None:
        base = pyproject["project"]["dependencies"]
        dspy_requirements = [entry for entry in base if entry.split("==")[0].split("@")[0].strip() == "dspy"]
        assert dspy_requirements == [DSPY_EXACT], f"base must declare exactly one literal {DSPY_EXACT}"

    def test_optimize_extra_declares_exactly_one_exact_gepa_requirement(self, pyproject: dict) -> None:
        optimize = pyproject["project"]["optional-dependencies"]["optimize"]
        gepa_requirements = [entry for entry in optimize if entry.split("==")[0].split(">")[0].strip() == "gepa"]
        assert gepa_requirements == [GEPA_EXACT], f"optimize extra must declare exactly one literal {GEPA_EXACT}"

    def test_dependency_injection_is_single_declared_op_per_surface(self, pyproject: dict) -> None:
        """Dependency injection stays constrained: one dspy op and one gepa op total."""
        declared = _declared_requirements(pyproject)
        dspy_owners: list[str] = []
        gepa_owners: list[str] = []
        for surface, entries in declared.items():
            for entry in entries:
                name = entry.split("==")[0].split(">")[0].split("@")[0].split(";")[0].strip()
                if name == "dspy":
                    dspy_owners.append(surface)
                if name == "gepa":
                    gepa_owners.append(surface)
        assert dspy_owners == ["project.dependencies"], (
            f"dspy must be injected exactly once into the base dependency list: {dspy_owners}"
        )
        assert gepa_owners == ["project.optional-dependencies.optimize"], (
            f"gepa must be injected exactly once into the optimize extra: {gepa_owners}"
        )


class TestNoVcsOrDirectUrlRequirements:
    """VAL-PKG-003: nothing declared or locked may reference source control."""

    def test_declared_requirements_have_no_vcs_or_direct_url(self, pyproject: dict) -> None:
        declared = _declared_requirements(pyproject)
        offenders: list[str] = []
        for surface, entries in declared.items():
            for entry in entries:
                lowered = entry.lower()
                if any(token in lowered for token in _VCS_SCHEME_MARKERS + _GIT_URL_MARKERS):
                    offenders.append(f"{surface}: {entry}")
                    continue
                requirement = Requirement(entry)
                if requirement.url is not None:
                    offenders.append(f"{surface}: direct URL {entry}")
        assert offenders == [], "VCS/direct-URL requirement found:\n" + "\n".join(offenders)

    def test_pyproject_raw_text_has_no_vcs_markers(self) -> None:
        text = PYPROJECT.read_text(encoding="utf-8")
        for token in (*_VCS_SCHEME_MARKERS, ".git@", ".git#"):
            assert token not in text, f"pyproject.toml still references {token!r}"

    def test_lock_has_only_registry_or_local_editable_sources(self, lock: dict) -> None:
        offenders: list[str] = []
        for package in lock["package"]:
            source = package.get("source", {})
            source_keys = set(source)
            if not source_keys or not source_keys <= _ALLOWED_LOCK_SOURCE_KEYS:
                offenders.append(f"{package['name']}: {source}")
                continue
            if "editable" in source and package["name"] != "fleet-rlm":
                offenders.append(f"{package['name']}: unexpected editable source {source}")
        assert offenders == [], "non-registry/VCS lock source found:\n" + "\n".join(offenders)

    def test_lock_raw_text_has_no_vcs_markers(self) -> None:
        text = LOCK.read_text(encoding="utf-8")
        assert 'source = { git = "' not in text, "uv.lock still pins a Git source"
        assert "git+" not in text, "uv.lock still references a VCS URL"
        assert "github.com" not in text, "uv.lock still references a GitHub source"


def _assert_registry_resolved_with_hashes(package: dict, *, expected_version: str) -> None:
    assert package["version"] == expected_version, (
        f"{package['name']} must lock exactly {expected_version}, found {package['version']}"
    )
    source = package.get("source", {})
    assert set(source) == {"registry"}, f"{package['name']} must resolve from a registry source: {source}"
    registry_url = source["registry"]
    assert registry_url.startswith("https://pypi.org/"), f"unexpected registry: {registry_url}"
    sdist = package.get("sdist")
    assert sdist, f"{package['name']} lock record must carry an sdist artifact"
    assert sdist["url"].startswith("https://files.pythonhosted.org/"), sdist["url"]
    assert sdist["hash"].startswith("sha256:"), sdist["hash"]
    wheels = package.get("wheels", [])
    assert wheels, f"{package['name']} lock record must carry at least one wheel"
    for wheel in wheels:
        assert wheel["url"].startswith("https://files.pythonhosted.org/"), wheel["url"]
        assert wheel["hash"].startswith("sha256:"), wheel["hash"]


class TestRegistryLockedIdentities:
    """VAL-PKG-001/002: the lock resolves exact published artifacts with hashes."""

    def test_lock_dspy_identity(self, lock: dict) -> None:
        _assert_registry_resolved_with_hashes(_lock_package(lock, "dspy"), expected_version="3.3.1")

    def test_lock_gepa_identity(self, lock: dict) -> None:
        _assert_registry_resolved_with_hashes(_lock_package(lock, "gepa"), expected_version="0.1.4")

    def test_root_package_remains_local_editable(self, lock: dict) -> None:
        root = _lock_package(lock, "fleet-rlm")
        assert root.get("source") == {"editable": "."}, root.get("source")


class TestObsoleteGitPinWorkflow:
    """VAL-PKG-028: the retired Git-pin CI verification stays absent."""

    def test_check_dspy_pin_workflow_file_removed(self) -> None:
        assert not (GITHUB_WORKFLOWS / "check-dspy-pin.yml").exists(), "obsolete Git-pin workflow must be removed"

    def test_ci_contains_no_git_pin_verification_or_guidance(self) -> None:
        ci_files = sorted(GITHUB_WORKFLOWS.glob("*.yml")) + sorted(GITHUB_WORKFLOWS.glob("*.yaml"))
        ci_files.append(CIRCLECI_CONFIG)
        offenders: list[str] = []
        for path in ci_files:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in _OBSOLETE_GIT_PIN_PATTERNS:
                match = re.search(pattern, text)
                if match:
                    offenders.append(f"{path.relative_to(ROOT)}: matched obsolete Git-pin pattern {pattern!r}")
        assert offenders == [], "obsolete Git-pin CI content remains:\n" + "\n".join(offenders)
