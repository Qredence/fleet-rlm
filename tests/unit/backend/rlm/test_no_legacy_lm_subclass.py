"""Guard the deprecated DSPy LM interfaces that DSPy 3.5 removes.

Three static shapes are checked, each a distinct 3.5 removal target:

* a ``dspy.BaseLM`` subclass (its ``forward``/``aforward`` hooks are the
  deprecated interface),
* a ``forward_contract`` declaration (the legacy typed-LM opt-in; DSPy 3.4
  raises ``TypeError`` when it is anything other than ``"legacy"``),
* any reference to ``LegacyEngine`` / ``AsyncLegacyEngine`` / ``complete_legacy``
  (3.4 transition wrappers with no 3.5 replacement).

These are all static shapes a runtime warning cannot see: a class that is
defined but never exercised imports cleanly and warns about nothing. So the
checks walk the source trees rather than the call path. Only ``ast.Name``,
``ast.Attribute`` and import-alias nodes are inspected — never string
constants — so prose and docstrings may still name the removed interfaces.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_ROOTS = ("src/fleet_rlm", "tests", "scripts")
_LEGACY_ENGINE_NAMES = frozenset({"LegacyEngine", "AsyncLegacyEngine", "complete_legacy"})


def _dspy_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Return the local names bound to the ``dspy`` module and to ``BaseLM``."""
    dspy_names = {"dspy"}
    bare_baselm: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            dspy_names.update(alias.asname or alias.name for alias in node.names if alias.name == "dspy")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "dspy" or module.startswith("dspy."):
                bare_baselm.update(alias.asname or alias.name for alias in node.names if alias.name == "BaseLM")
    return dspy_names, bare_baselm


def _legacy_baselm_subclasses(path: Path) -> list[str]:
    """Return ``path:line: class Name`` for every ``dspy.BaseLM`` subclass."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    dspy_names, bare_baselm = _dspy_aliases(tree)

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            from_dspy_attribute = (
                isinstance(base, ast.Attribute)
                and base.attr == "BaseLM"
                and isinstance(base.value, ast.Name)
                and base.value.id in dspy_names
            )
            from_dspy_import = isinstance(base, ast.Name) and base.id in bare_baselm
            if from_dspy_attribute or from_dspy_import:
                offenders.append(f"{path}:{node.lineno}: class {node.name}")
    return offenders


def _forward_contract_declarations(path: Path) -> list[str]:
    """Return ``path:line: forward_contract`` for every legacy contract marker."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "forward_contract":
                offenders.append(f"{path}:{node.lineno}: forward_contract")
            elif isinstance(target, ast.Attribute) and target.attr == "forward_contract":
                offenders.append(f"{path}:{node.lineno}: .forward_contract")
    return offenders


def _legacy_engine_references(path: Path) -> list[str]:
    """Return ``path:line: name`` for every legacy engine shim reference."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name in _LEGACY_ENGINE_NAMES or (alias.asname or "") in _LEGACY_ENGINE_NAMES:
                    offenders.append(f"{path}:{node.lineno}: {alias.name}")
        elif isinstance(node, ast.Name) and node.id in _LEGACY_ENGINE_NAMES:
            offenders.append(f"{path}:{node.lineno}: {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in _LEGACY_ENGINE_NAMES:
            offenders.append(f"{path}:{node.lineno}: .{node.attr}")
    return offenders


def _scan(collector) -> list[str]:
    offenders: list[str] = []
    for root in _ROOTS:
        for path in sorted((_REPO_ROOT / root).rglob("*.py")):
            offenders.extend(collector(path))
    return offenders


@pytest.mark.parametrize("root", _ROOTS)
def test_no_module_subclasses_dspy_baselm(root: str) -> None:
    """No ``class X(dspy.BaseLM)`` may exist in source, tests, or scripts.

    Implementing ``forward``/``aforward`` on such a class is what DSPy 3.4
    deprecates and 3.5 removes; engines registered on ``dspy.LM(engine=...)``
    are the canonical form.
    """
    offenders: list[str] = []
    for path in sorted((_REPO_ROOT / root).rglob("*.py")):
        offenders.extend(_legacy_baselm_subclasses(path))

    assert offenders == [], (
        "dspy.BaseLM subclasses are deprecated in DSPy 3.4 and removed in 3.5; "
        f"pass a complete(request) -> Response engine to dspy.LM(engine=...) instead: {offenders}"
    )


def test_no_module_declares_forward_contract() -> None:
    """No module may opt into the legacy typed-LM contract.

    Declaring the marker is how a class asks DSPy for the removed 3.3 typed-LM
    path; DSPy 3.4 rejects any value other than ``"legacy"`` outright.
    """
    assert _scan(_forward_contract_declarations) == []


def test_no_module_references_legacy_engine() -> None:
    """No module may import or call the 3.4 transition engine shims.

    ``LegacyEngine`` and ``AsyncLegacyEngine`` are 3.4-only wrappers, and the
    ``complete_legacy`` shortcut is deprecated; all three go away in 3.5 with no
    replacement.
    """
    assert _scan(_legacy_engine_references) == []
