"""P38 contraction contract: certified deletions only, retained owners intact.

Evidence for VAL-RLM-056 (shadow-only branch) and VAL-RLM-066. Every removal
cited here carries a P36 inventory ID; every retained owner is enumerated by
name from the production tree. The P35-D decision record selects the shadow
branch: callbacks stay a shadow probe, manual observers and product
projections remain the authoritative owners.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = REPO_ROOT / "src" / "fleet_rlm"
DECISION_DOC = REPO_ROOT / "docs/how-to-guides/p35d-callback-observability-decision.md"
INVENTORY_DOC = REPO_ROOT / "docs/how-to-guides/p36-ownership-deletion-inventory.md"


def _top_level_names(relative: str) -> set[str]:
    """Enumerate top-level definitions and re-exported names via AST."""
    tree = ast.parse((PACKAGE_ROOT / relative).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(str(target.id) for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def _source(relative: str) -> str:
    return (PACKAGE_ROOT / relative).read_text(encoding="utf-8")


# DELETED categories: proven duplicate or unsupported under the certified
# DSPy 3.3.1 legacy contract. Zero matches in production sources.
DELETED_TELEMETRY_SYMBOLS = (
    "_typed_response_telemetry",  # P38-RLM-006/011: unreachable typed LMResponse path
    "_provider_response_telemetry",  # P38-RLM-006: raw _hidden_params probing
)
DELETED_PROVIDER_FIELDS = (
    "provider_response_ms",  # P38-RLM-006
    "litellm_overhead_ms",  # P38-RLM-006
    "callback_duration_ms",  # P38-RLM-006
    "provider_request_id",  # P38-RLM-006
)
FORBIDDEN_SYMBOLS = (
    "_tools_registered",  # P38-RLM-013 FORBID
    "PythonInterpreter",  # P38-RLM-002: no alternate interpreter engine
    "_RepairFeedback",  # P38-RLM-016: superseded repair machinery
    "optimize_anything",  # mission directive: removed target
    "OptimizeAnythingConfig",  # mission directive: removed target
)
TYPED_LM_PATH_MARKERS = (
    "experimental",  # P38-RLM-011: typed contract entry points
    "LMRequest",
    "typed_lm",
)


def _production_python_files() -> list[Path]:
    return sorted(path for path in PACKAGE_ROOT.rglob("*.py"))


def test_p38_deleted_telemetry_helpers_and_provider_fields_are_absent() -> None:
    findings: list[str] = []
    for path in _production_python_files():
        text = path.read_text(encoding="utf-8")
        for marker in (*DELETED_TELEMETRY_SYMBOLS, *DELETED_PROVIDER_FIELDS):
            if marker in text:
                findings.append(f"{path.relative_to(REPO_ROOT)}: {marker}")
    # The benchmark suite reads engineering span outputs; it must not depend
    # on deleted provider fields either.
    for script in (REPO_ROOT / "scripts" / "benchmarks").glob("*.py"):
        text = script.read_text(encoding="utf-8")
        for marker in DELETED_PROVIDER_FIELDS:
            if marker in text:
                findings.append(f"{script.relative_to(REPO_ROOT)}: {marker}")
    assert findings == [], findings


def test_p38_forbidden_symbols_have_zero_production_matches() -> None:
    findings: list[str] = []
    for path in _production_python_files():
        text = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN_SYMBOLS:
            if marker in text:
                findings.append(f"{path.relative_to(REPO_ROOT)}: {marker}")
    assert findings == [], findings


def test_p38_typed_lm_compatility_markers_are_absent_from_production() -> None:
    # The certified DSPy 3.3.1 contract for Fleet is the legacy forward
    # contract; no production path may select the typed/experimental seam.
    findings: list[str] = []
    for path in _production_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in TYPED_LM_PATH_MARKERS:
                findings.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {node.id}")
            if isinstance(node, ast.Attribute) and node.attr in TYPED_LM_PATH_MARKERS:
                findings.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {node.attr}")
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in TYPED_LM_PATH_MARKERS:
                findings.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {node.value!r}")
    assert findings == [], findings


def test_p38_fleet_never_constructs_kernel_history_or_prediction_objects() -> None:
    # P38-RLM-001/002: DSPy owns REPLHistory/Prediction construction; Fleet
    # validates returned Predictions but never builds kernel objects itself.
    findings: list[str] = []
    for path in _production_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id in {"Prediction", "REPLHistory"}:
                findings.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {func.id}(")
            if isinstance(func, ast.Attribute) and func.attr in {"Prediction", "REPLHistory"}:
                findings.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {func.attr}(")
    assert findings == [], findings


def test_p38_hidden_params_probing_is_absent_from_production() -> None:
    # P38-RLM-006: raw litellm `_hidden_params` probing was private provider
    # telemetry; the contraction removes it entirely from production code.
    findings = [
        str(path.relative_to(REPO_ROOT))
        for path in _production_python_files()
        if "_hidden_params" in path.read_text(encoding="utf-8")
    ]
    assert findings == [], findings


def test_p38_callback_shadow_is_isolated_from_product_modules() -> None:
    # P38-RLM-009 / VAL-RLM-056 shadow branch: only the observability module
    # may define the shadow recorder; no other production module imports it,
    # and the recorder itself never touches Runtime Events.
    importers: list[str] = []
    for path in _production_python_files():
        if path == PACKAGE_ROOT / "observability" / "dspy_callbacks.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "dspy_callbacks" in node.module:
                importers.append(str(path.relative_to(REPO_ROOT)))
            if isinstance(node, ast.Import):
                importers.extend(alias.name for alias in node.names if "dspy_callbacks" in alias.name)
    assert importers == [], importers
    shadow_source = _source("observability/dspy_callbacks.py")
    assert "RuntimeEvent" not in shadow_source
    assert "fleet_rlm.rlm.events" not in shadow_source


