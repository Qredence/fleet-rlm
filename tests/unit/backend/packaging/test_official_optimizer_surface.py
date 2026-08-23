"""Shipped-surface contracts for the official GEPA 0.1.4 optimizer adaptation.

VAL-PKG-025: shipped Fleet Python files carry zero references to the retired
``optimize_anything`` / ``OptimizeAnythingConfig`` API.
VAL-OPT-025: no USD reflection-cost-cap contract exists in source, config, or docs.
Base install: no Fleet runtime, tool-registration, or optimizer module imports
``gepa`` beyond what the official ``dspy`` 3.3.1 base import graph loads itself.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import fleet_rlm

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SRC_ROOT = Path(fleet_rlm.__file__).resolve().parent
_RETIRED_GEPA_MARKERS = ("OptimizeAnythingConfig", "optimize_anything")
_USD_COST_CAP_MARKERS = (
    "max_total_cost_usd",
    "max-total-cost-usd",
    "max_token_cost",
    "max-token-cost",
    "max_reflection_cost",
)


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def test_shipped_sources_have_no_retired_gepa_api() -> None:
    offenders = [
        f"{path.relative_to(_REPO_ROOT)}: {marker}"
        for path in _python_files(_SRC_ROOT)
        for marker in _RETIRED_GEPA_MARKERS
        if marker in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_no_usd_reflection_cost_cap_in_source_or_docs() -> None:
    scan_paths = [
        *_python_files(_SRC_ROOT),
        *sorted((_REPO_ROOT / "docs").rglob("*.md")),
        *(path for name in ("README.md", "CHANGELOG.md", "pyproject.toml") if (path := _REPO_ROOT / name).is_file()),
    ]
    offenders = [
        f"{path.relative_to(_REPO_ROOT)}: {marker}"
        for path in scan_paths
        for marker in _USD_COST_CAP_MARKERS
        if marker in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == []


def test_fleet_imports_add_no_gepa_modules_beyond_dspy_base() -> None:
    """Published dspy 3.3.1 declares gepa as a base dependency and imports it
    eagerly; the Fleet contract is that Fleet modules never add further gepa
    imports at import time (optimizer gepa usage stays function-local/lazy)."""
    probe = (
        "import sys\n"
        "import dspy\n"
        "baseline = {m for m in sys.modules if m == 'gepa' or m.startswith('gepa.')}\n"
        "import fleet_rlm.app\n"
        "import fleet_rlm.optimization\n"
        "import fleet_rlm.optimization.gepa_runner\n"
        "import fleet_rlm.optimization.mlflow_observability\n"
        "import fleet_rlm.optimization.evidence\n"
        "import fleet_rlm.optimization.dataset\n"
        "import fleet_rlm.optimization.types\n"
        "import fleet_rlm.optimization.curated_input\n"
        "extra = sorted(\n"
        "    m for m in sys.modules\n"
        "    if (m == 'gepa' or m.startswith('gepa.')) and m not in baseline\n"
        ")\n"
        "print(extra)\n"
        "raise SystemExit(1 if extra else 0)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, (
        "importing Fleet runtime/optimizer surfaces loaded new gepa modules: "
        f"{completed.stdout.strip()} {completed.stderr.strip()}"
    )
