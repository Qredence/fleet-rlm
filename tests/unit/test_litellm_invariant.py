"""Invariant tests for fleet-rlm's litellm policy.

Two-part policy, both mechanically enforced here:

1. No source file under ``src/fleet_rlm/`` may import or call litellm directly.
   fleet-rlm interacts with language models exclusively through DSPy's
   normalized LM API (``dspy.LM``, ``dspy.settings.lm``, ``dspy.configure``).
   litellm is a transitive dependency of DSPy and remains DSPy's internal
   compatibility layer (see
   https://dspy.ai/community/normalized-lm-api-migration/ — "Removing the
   legacy BaseLM.forward contract does not necessitate removing LiteLLM").

2. litellm MUST NOT be declared as a direct dependency in
   ``[project].dependencies`` in ``pyproject.toml``. It MAY — and must —
   remain pinned under ``[tool.uv].override-dependencies`` to close the
   7 CVEs tracked in the override comment.

Uses AST parsing for the import scan and ``tomllib`` for the pyproject policy
check, so comments, docstrings, and string literals do not trigger false
positives.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

# Resolve the package source tree relative to this test file.
_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "fleet_rlm"


def _iter_python_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _directly_imports_or_uses_litellm(path: Path) -> list[str]:
    """Return a list of violations where the module touches litellm directly."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []

    violations: list[str] = []

    for node in ast.walk(tree):
        # `import litellm` or `import litellm.foo`
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "litellm" or alias.name.startswith("litellm."):
                    violations.append(f"{path}:{node.lineno}: import {alias.name}")
        # `from litellm import ...` or `from litellm.foo import ...`
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "litellm" or module.startswith("litellm."):
                violations.append(f"{path}:{node.lineno}: from {module} import {', '.join(a.name for a in node.names)}")
        # `litellm.<attr>(...)` — a call routed through a litellm module attribute.
        # This catches litellm.completion(...) / litellm.acompletion(...) even if
        # litellm was imported indirectly (e.g. via a re-exporting shim).
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "litellm":
                violations.append(f"{path}:{node.lineno}: litellm.{func.attr}(...)")

    return violations


@pytest.mark.parametrize("py_file", _iter_python_files(_SRC_ROOT), ids=lambda p: str(p.relative_to(_SRC_ROOT)))
def test_no_direct_litellm_usage(py_file: Path) -> None:
    """No source file in fleet_rlm may import or call litellm directly."""
    violations = _directly_imports_or_uses_litellm(py_file)
    assert not violations, (
        "fleet-rlm must not use litellm directly — go through dspy.LM / dspy.settings.lm. "
        "Found violations:\n  " + "\n  ".join(violations)
    )


def test_litellm_dependency_pin_is_intentional() -> None:
    """The litellm override pin in pyproject.toml must remain, and litellm must
    NOT be declared as a direct dep.

    Policy: litellm is installed only as DSPy's transitive dependency.
    ``[project].dependencies`` must not declare litellm; ``[tool.uv]
    override-dependencies`` must pin a patched floor and document the CVE
    rationale so the override is never silently removed.
    """

    pyproject = _SRC_ROOT.parents[1] / "pyproject.toml"
    if not pyproject.exists():
        pytest.skip("pyproject.toml not found")
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    project_deps: list[str] = data.get("project", {}).get("dependencies", [])
    litellm_in_direct = any(
        dep.strip().split("=")[0].strip().lower().startswith("litellm")
        or dep.strip().split()[0].lower().startswith("litellm")
        for dep in project_deps
    )
    assert not litellm_in_direct, (
        "litellm must NOT appear in [project].dependencies — it is installed only "
        "as DSPy's transitive dependency. Found it among: "
        f"{[dep for dep in project_deps if 'litellm' in dep.lower()]}"
    )

    uv_overrides: list[str] = data.get("tool", {}).get("uv", {}).get("override-dependencies", [])
    litellm_in_override = any(
        dep.strip().split("=")[0].strip().lower().startswith("litellm")
        or dep.strip().split()[0].lower().startswith("litellm")
        for dep in uv_overrides
    )
    assert litellm_in_override, (
        "litellm pin missing from [tool.uv].override-dependencies. It must remain "
        "pinned there to close the 7 documented CVEs."
    )

    # The override block must carry the CVE rationale comment, so the pin is
    # never removed quietly as part of a broader cleanup.
    pyproject_text = pyproject.read_text(encoding="utf-8")
    assert "CVE" in pyproject_text or "cve" in pyproject_text, (
        "litellm override pin should document the CVE rationale (see existing comment)"
    )
