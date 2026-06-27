"""Invariant test: fleet-rlm must not import or call litellm directly.

fleet-rlm interacts with language models exclusively through DSPy's normalized
LM API (``dspy.LM``, ``dspy.settings.lm``, ``dspy.configure``). litellm is a
transitive dependency of DSPy and remains DSPy's internal compatibility layer
(see https://dspy.ai/community/normalized-lm-api-migration/ — "Removing the
legacy BaseLM.forward contract does not necessitate removing LiteLLM").

This test enforces the policy mechanically so a future change cannot silently
re-introduce direct litellm coupling. Uses AST parsing so comments, docstrings,
and string literals do not trigger false positives.
"""

from __future__ import annotations

import ast
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
    """The litellm override pin in pyproject.toml must carry a documenting comment.

    litellm remains a transitive (DSPy) dependency. The override pin closes CVEs
    and must not be removed without understanding DSPy's own litellm bound.
    """
    pyproject = _SRC_ROOT.parents[1] / "pyproject.toml"
    if not pyproject.exists():
        pytest.skip("pyproject.toml not found")
    text = pyproject.read_text(encoding="utf-8")
    # The pin lives in [tool.uv] override-dependencies with a CVE rationale comment.
    assert "litellm" in text, "litellm pin missing from pyproject.toml"
    assert "CVE" in text or "cve" in text, (
        "litellm override pin should document the CVE rationale (see existing comment)"
    )
