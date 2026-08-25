"""Distribution artifact matrix certification test suite.

Fulfills:
- VAL-PKG-009: Base distribution metadata
- VAL-PKG-010: Optional extra metadata
- VAL-PKG-011: Canonical artifact set and identity
- VAL-PKG-012: Wheel standards validation
- VAL-PKG-013: Sdist standards validation
- VAL-PKG-014: Wheel package assets
- VAL-PKG-015: Sdist package assets
- VAL-PKG-016: Forbidden distribution payload (with archive-wide secret & entropy scan)
- VAL-PKG-026: Build succeeds without Git packaging support
"""

from __future__ import annotations

import email
import math
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
PYPROJECT = ROOT / "pyproject.toml"

_REQUIRED_ASSET_PATHS = (
    "fleet_rlm/py.typed",
    "fleet_rlm/daytona/snapshot-requirements.txt",
    "fleet_rlm/skills/bundled/README.md",
    "fleet_rlm/skills/bundled/data-analysis/SKILL.md",
    "fleet_rlm/skills/bundled/dspy-rlm/SKILL.md",
    "fleet_rlm/skills/bundled/dspy-rlm/references/rlm-contract.md",
    "fleet_rlm/skills/bundled/long-context/SKILL.md",
    "fleet_rlm/skills/bundled/long-context/references/chunking-strategies.md",
    "fleet_rlm/skills/bundled/long-context/scripts/rank_chunks.py",
    "fleet_rlm/skills/bundled/long-context/scripts/semantic_chunk.py",
    "fleet_rlm/skills/bundled/workspace-files/SKILL.md",
    "fleet_rlm/skills/bundled/workspace-files/references/filesystem-contract.md",
    "fleet_rlm/skills/bundled/report-builder/SKILL.md",
)

_FORBIDDEN_NAME_PATTERNS = (
    r"frontend",
    r"fleet_rlm/ui",
    r"fleet_rlm/skills/skills",
    r"\.git",
    r"\.factory",
    r"\.scratch",
    r"\.env",
    r"__pycache__",
    r"\.pyc$",
    r"\.venv",
    r"\.pdf$",
    r"\.tmp$",
    r"\.swp$",
)

# Known secret tokens and credential patterns
_SECRET_PATTERNS = [
    re.compile(r"(?i)bearer\s+[a-z0-9_\-\.]{20,}"),
    re.compile(r"(?i)(?:api[_-]?key|secret[_-]?key|auth[_-]?token)\s*[:=]\s*['\"][a-zA-Z0-9_\-\.]{16,}['\"]"),
    re.compile(r"ghp_[0-9a-zA-Z]{36}"),
    re.compile(r"github_pat_[0-9a-zA-Z_]{82}"),
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"-----BEGIN\s+(?:RSA|OPENSSH|DSA|EC|PGP)?\s*PRIVATE KEY-----"),
]


def _shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    entropy = 0.0
    for x in range(256):
        p_x = float(data.count(x)) / len(data)
        if p_x > 0:
            entropy += -p_x * math.log(p_x, 2)
    return entropy


@pytest.fixture(scope="module")
def built_artifacts() -> tuple[Path, Path]:
    """Ensure clean build of universal wheel and sdist."""
    dist_dir = ROOT / "dist"
    # Run uv build to ensure fresh artifacts
    subprocess.run(["uv", "build"], cwd=ROOT, check=True, capture_output=True)
    wheels = sorted(dist_dir.glob("fleet_rlm-*.whl"))
    sdists = sorted(dist_dir.glob("fleet_rlm-*.tar.gz"))
    assert len(wheels) == 1, f"Expected 1 wheel in dist/, found {wheels}"
    assert len(sdists) == 1, f"Expected 1 sdist in dist/, found {sdists}"
    return wheels[0], sdists[0]


@pytest.fixture(scope="module")
def project_toml() -> dict:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


class TestArtifactSetAndStandards:
    """VAL-PKG-011, VAL-PKG-012, VAL-PKG-013."""

    def test_canonical_artifact_filenames(self, built_artifacts: tuple[Path, Path], project_toml: dict) -> None:
        wheel, sdist = built_artifacts
        version = project_toml["project"]["version"]
        assert wheel.name == f"fleet_rlm-{version}-py3-none-any.whl"
        assert sdist.name == f"fleet_rlm-{version}.tar.gz"

    def test_twine_strict_check(self, built_artifacts: tuple[Path, Path]) -> None:
        wheel, sdist = built_artifacts
        proc = subprocess.run(
            ["uvx", "twine", "check", "--strict", str(wheel), str(sdist)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"twine check --strict failed:\n{proc.stdout}\n{proc.stderr}"
        assert "PASSED" in proc.stdout

    def test_wheel_tag_and_format(self, built_artifacts: tuple[Path, Path]) -> None:
        wheel, _ = built_artifacts
        with zipfile.ZipFile(wheel) as zf:
            names = zf.namelist()
            wheel_info = [n for n in names if n.endswith(".dist-info/WHEEL")]
            assert len(wheel_info) == 1
            wheel_meta = zf.read(wheel_info[0]).decode("utf-8")
            assert "Wheel-Version: 1.0" in wheel_meta
            assert "Root-Is-Purelib: true" in wheel_meta
            assert "Tag: py3-none-any" in wheel_meta


class TestDistributionMetadata:
    """VAL-PKG-009, VAL-PKG-010."""

    def test_wheel_and_sdist_metadata_parity(self, built_artifacts: tuple[Path, Path], project_toml: dict) -> None:
        wheel, sdist = built_artifacts
        expected_version = project_toml["project"]["version"]

        # Parse Wheel METADATA
        with zipfile.ZipFile(wheel) as zf:
            meta_name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
            wheel_msg = email.message_from_string(zf.read(meta_name).decode("utf-8"))

        # Parse Sdist PKG-INFO
        with tarfile.open(sdist, "r:gz") as tf:
            pkg_info_name = next(m.name for m in tf.getmembers() if m.name.endswith("PKG-INFO"))
            sdist_msg = email.message_from_string(tf.extractfile(pkg_info_name).read().decode("utf-8"))

        # Check basic metadata
        assert wheel_msg["Name"] == "fleet-rlm"
        assert sdist_msg["Name"] == "fleet-rlm"
        assert wheel_msg["Version"] == expected_version
        assert sdist_msg["Version"] == expected_version
        assert wheel_msg["Summary"] == project_toml["project"]["description"]
        assert sdist_msg["Summary"] == project_toml["project"]["description"]
        assert wheel_msg["License-Expression"] == "MIT"
        assert sdist_msg["License-Expression"] == "MIT"
        assert wheel_msg["Requires-Python"] in (">=3.11,<3.14", "<3.14,>=3.11")
        assert sdist_msg["Requires-Python"] in (">=3.11,<3.14", "<3.14,>=3.11")
        assert wheel_msg["Requires-Python"] == sdist_msg["Requires-Python"]

        # Check Classifiers
        wheel_classifiers = set(wheel_msg.get_all("Classifier", []))
        sdist_classifiers = set(sdist_msg.get_all("Classifier", []))
        assert "Programming Language :: Python :: 3.11" in wheel_classifiers
        assert "Programming Language :: Python :: 3.12" in wheel_classifiers
        assert "Programming Language :: Python :: 3.13" in wheel_classifiers
        assert wheel_classifiers == sdist_classifiers

        # Check Requires-Dist entries
        wheel_requires = sorted(wheel_msg.get_all("Requires-Dist", []))
        sdist_requires = sorted(sdist_msg.get_all("Requires-Dist", []))
        assert wheel_requires == sdist_requires

        # Base requirement dspy==3.3.1
        base_dspy = [r for r in wheel_requires if r.startswith("dspy==")]
        assert base_dspy == ["dspy==3.3.1"], f"expected exact dspy==3.3.1 base requirement, got {base_dspy}"

        # Extra gepa==0.1.4 under optimize
        gepa_req = [r for r in wheel_requires if "gepa" in r]
        assert len(gepa_req) == 1
        assert "gepa==0.1.4" in gepa_req[0]
        assert 'extra == "optimize"' in gepa_req[0] or "extra == 'optimize'" in gepa_req[0]

        # Check Provides-Extra
        wheel_extras = set(wheel_msg.get_all("Provides-Extra", []))
        sdist_extras = set(sdist_msg.get_all("Provides-Extra", []))
        assert "optimize" in wheel_extras
        assert wheel_extras == sdist_extras


class TestPackageAssetsAndExclusions:
    """VAL-PKG-014, VAL-PKG-015, VAL-PKG-016."""

    def test_wheel_contains_all_required_assets(self, built_artifacts: tuple[Path, Path]) -> None:
        wheel, _ = built_artifacts
        with zipfile.ZipFile(wheel) as zf:
            names = set(zf.namelist())
            for asset in _REQUIRED_ASSET_PATHS:
                assert asset in names, f"Wheel missing required asset: {asset}"

    def test_sdist_contains_all_required_assets(self, built_artifacts: tuple[Path, Path], project_toml: dict) -> None:
        _, sdist = built_artifacts
        version = project_toml["project"]["version"]
        prefix = f"fleet_rlm-{version}/src/"
        with tarfile.open(sdist, "r:gz") as tf:
            names = {m.name for m in tf.getmembers()}
            for asset in _REQUIRED_ASSET_PATHS:
                expected = f"{prefix}{asset}"
                assert expected in names, f"Sdist missing required asset: {expected}"

    def test_sdist_reproduces_identical_wheel_payload(self, built_artifacts: tuple[Path, Path]) -> None:
        direct_wheel, sdist = built_artifacts
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            # Unpack sdist safely (PEP 706 data filter guards path traversal).
            with tarfile.open(sdist, "r:gz") as tf:
                if hasattr(tarfile, "data_filter"):
                    tf.extractall(tmp_path, filter="data")
                else:
                    # Interpreters older than 3.11.4 lack extraction filters;
                    # skip rather than fall back to an unguarded extraction.
                    pytest.skip("tarfile extraction filters unavailable on this interpreter")
            sdist_root = next(tmp_path.glob("fleet_rlm-*"))

            # Build wheel from unpacked sdist
            subprocess.run(
                ["uv", "build", "--wheel", "--out-dir", str(tmp_path / "out")],
                cwd=sdist_root,
                capture_output=True,
                text=True,
                check=True,
            )
            sdist_wheel = next((tmp_path / "out").glob("*.whl"))

            # Compare files in direct wheel vs sdist-built wheel (excluding .dist-info/RECORD)
            with zipfile.ZipFile(direct_wheel) as zf1, zipfile.ZipFile(sdist_wheel) as zf2:
                files1 = {n for n in zf1.namelist() if not n.endswith(".dist-info/RECORD")}
                files2 = {n for n in zf2.namelist() if not n.endswith(".dist-info/RECORD")}
                assert files1 == files2, (
                    f"Payload divergence:\nOnly in direct: {files1 - files2}\nOnly in sdist-built: {files2 - files1}"
                )

    def test_forbidden_payload_and_secret_scan(self, built_artifacts: tuple[Path, Path]) -> None:
        wheel, sdist = built_artifacts

        # Scan Wheel
        with zipfile.ZipFile(wheel) as zf:
            for info in zf.infolist():
                name = info.filename
                # Check forbidden path patterns
                for pat in _FORBIDDEN_NAME_PATTERNS:
                    assert not re.search(pat, name), f"Wheel member {name} matches forbidden pattern {pat}"
                # Secret pattern and entropy scan
                if not name.endswith("/"):
                    content = zf.read(name)
                    # Exclude binary compiled extensions or images if any (none in purelib, but check)
                    text_candidate = content.decode("utf-8", errors="ignore")
                    for secret_pat in _SECRET_PATTERNS:
                        assert not secret_pat.search(text_candidate), (
                            f"Wheel member {name} contains secret pattern match!"
                        )
                    # Entropy check for text files (ignore compressed/binary like snapshot-requirements if small)
                    if name.endswith((".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".csv")):
                        entropy = _shannon_entropy(content)
                        # Plain text files typically have entropy < 6.0
                        assert entropy < 6.5, f"Wheel member {name} has suspiciously high entropy: {entropy:.2f}"

        # Scan Sdist
        with tarfile.open(sdist, "r:gz") as tf:
            for member in tf.getmembers():
                name = member.name
                for pat in _FORBIDDEN_NAME_PATTERNS:
                    # Remove top-level sdist prefix directory before matching
                    rel_name = "/".join(name.split("/")[1:])
                    if rel_name:
                        assert not re.search(pat, rel_name), f"Sdist member {name} matches forbidden pattern {pat}"
                if member.isfile():
                    f = tf.extractfile(member)
                    if f:
                        content = f.read()
                        text_candidate = content.decode("utf-8", errors="ignore")
                        for secret_pat in _SECRET_PATTERNS:
                            assert not secret_pat.search(text_candidate), (
                                f"Sdist member {name} contains secret pattern match!"
                            )
                        if name.endswith((".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".csv")):
                            entropy = _shannon_entropy(content)
                            assert entropy < 6.5, f"Sdist member {name} has suspiciously high entropy: {entropy:.2f}"


class TestVcsFreeBuild:
    """VAL-PKG-026: Build succeeds without Git packaging support."""

    def test_build_without_git(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            source_copy = tmp_path / "repo"
            source_copy.mkdir()

            # Copy only essential source files needed for build
            # to keep the test fast and avoid copying large databases/caches
            for item in ["pyproject.toml", "README.md", "LICENSE", "AUTHORS.md"]:
                if (ROOT / item).exists():
                    shutil.copy2(ROOT / item, source_copy / item)

            shutil.copytree(
                ROOT / "src",
                source_copy / "src",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )

            assert not (source_copy / ".git").exists()

            # Path without git executable
            env = os.environ.copy()
            paths = env.get("PATH", "").split(os.pathsep)
            vcs_free_paths = []
            for p in paths:
                if (Path(p) / "git").exists():
                    continue
                vcs_free_paths.append(p)
            env["PATH"] = os.pathsep.join(vcs_free_paths)

            # Check git is indeed inaccessible (FileNotFoundError expected)
            with pytest.raises(FileNotFoundError):
                subprocess.run(["git", "--version"], env=env, capture_output=True)

            # Run build
            proc = subprocess.run(
                ["uv", "build"],
                cwd=source_copy,
                env=env,
                capture_output=True,
                text=True,
            )
            assert proc.returncode == 0, f"uv build failed without git:\n{proc.stdout}\n{proc.stderr}"
            assert (source_copy / "dist").exists()
            built_wheels = list((source_copy / "dist").glob("*.whl"))
            built_sdists = list((source_copy / "dist").glob("*.tar.gz"))
            assert len(built_wheels) == 1
            assert len(built_sdists) == 1
