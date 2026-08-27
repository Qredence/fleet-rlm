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


# RETAINED owners named by the P36 inventory (KEEP rows and retained halves of
# CONDITIONAL DELETE rows). P38 may narrow implementations but never remove
# these obligations.
RETAINED_OWNERS: tuple[tuple[str, str], ...] = (
    # P38-RLM-001/003: native kernel seam plus Fleet result trust boundary.
    ("rlm/result.py", "PredictionResult"),
    ("rlm/result.py", "prediction_result"),
    ("rlm/result.py", "validate_rlm_usage"),
    ("rlm/result.py", "observed_usage"),
    ("rlm/result.py", "normalize_prediction_trajectory"),
    ("rlm/program.py", "RLMOptions"),
    ("rlm/_dspy_compat.py", "assert_dspy_version"),
    ("rlm/program.py", "build_native_rlm"),
    # P38-RLM-004: worker/cancellation ownership.
    ("rlm/runtime.py", "RLMRunner"),
    ("rlm/runtime.py", "WorkerOwnership"),
    ("rlm/runtime.py", "invoke_native_rlm"),
    # P38-RLM-005/008: Fleet observation projection and product observers.
    ("rlm/events.py", "reconcile_trajectory"),
    ("rlm/events.py", "ObservationSession"),
    ("rlm/events.py", "DetailRelay"),
    ("rlm/events.py", "observe_tool"),
    ("rlm/events.py", "ExecutionTraceAssembler"),
    # P38-RLM-006 retained half: truthful observed usage per LM call.
    ("rlm/_dspy_compat.py", "_RLMTraceCallback"),
    ("rlm/_dspy_compat.py", "_latest_lm_telemetry"),
    ("rlm/recursion.py", "DelegationMetrics"),
    # P38-RLM-007: bounded diagnostic probe, never a second production path.
    ("rlm/runtime.py", "probe_root_lm"),
    ("daytona/diagnostics.py", "DaytonaDoctorDependencies"),
    # P38-RLM-009: shadow recorder stays, shadow-only.
    ("observability/callback_shadow.py", "CallbackShadowRecorder"),
    ("observability/callback_shadow.py", "compare_callback_records"),
    # P38-RLM-015: error taxonomy and repair classification.
    ("rlm/_dspy_compat.py", "CodeExecutionError"),
    ("rlm/_dspy_compat.py", "CodeInterpreterError"),
    ("daytona/errors.py", "sanitize_provider_message"),
    # P37-ORCH-007/010 durable settlement and coordinator ownership.
    ("chat/run_lifecycle.py", "RunLifecycleService"),
    ("chat/turn_coordinator.py", "TurnCoordinator"),
)


def test_p38_retained_owners_are_enumerated_in_the_production_tree() -> None:
    missing = [f"{relative}::{name}" for relative, name in RETAINED_OWNERS if name not in _top_level_names(relative)]
    assert missing == [], f"retained P38 owners are missing: {missing}"


def test_p38_build_native_rlm_keeps_the_execution_context_metadata_seam() -> None:
    # P38-RLM-012: DSPy 3.3.1's `_get_output_fields_info` exposes only simple
    # types, so the certified adoption condition is NOT met; the wrapper that
    # refreshes required/default output metadata must stay.
    source = (
        _source("rlm/program.py") if (PACKAGE_ROOT / "rlm/program.py").exists() else _source("rlm/dspy_contract.py")
    )
    assert "_inject_execution_context" in source
    assert "build_output_fields" in source


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
        if path == PACKAGE_ROOT / "observability" / "callback_shadow.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "callback_shadow" in node.module:
                importers.append(str(path.relative_to(REPO_ROOT)))
            if isinstance(node, ast.Import):
                importers.extend(alias.name for alias in node.names if "callback_shadow" in alias.name)
    assert importers == [], importers
    shadow_source = _source("observability/callback_shadow.py")
    assert "RuntimeEvent" not in shadow_source
    assert "fleet_rlm.rlm.events" not in shadow_source


def test_p38_decision_record_selects_the_shadow_branch() -> None:
    # VAL-RLM-056 evidence: the milestone callback decision names the selected
    # owner. P35-D is shadow-only, so no manual adapter deletion is authorized.
    text = DECISION_DOC.read_text(encoding="utf-8")
    assert "shadow-only, do not adopt for product or authoritative spans" in text
    assert "does not authorize removal" in text
    inventory = INVENTORY_DOC.read_text(encoding="utf-8")
    assert "P38-RLM-009" in inventory
    assert "Shadow evidence alone never authorizes product-path deletion." in inventory


def test_p38_benchmark_script_no_longer_consumes_provider_telemetry() -> None:
    # The latency benchmark reads engineering span outputs; after the
    # contraction it must not depend on deleted provider fields.
    script = (REPO_ROOT / "scripts/benchmarks/run_rlm_latency.py").read_text(encoding="utf-8")
    for marker in DELETED_PROVIDER_FIELDS:
        assert marker not in script, marker
