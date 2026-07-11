"""K-001: offline framework contract tests for DSPy, FastAPI SSE, and Daytona."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path


def test_dspy_rlm_constructor_exposes_required_fields() -> None:
    """Installed dspy.RLM must expose the constructor fields Fleet depends on."""
    import dspy

    parameters = inspect.signature(dspy.RLM.__init__).parameters
    # dspy==3.3.0b1 names the iteration budget ``max_iterations`` (docs sometimes
    # say ``max_iters``). Lock the installed API so upgrades fail here.
    required = {
        "signature",
        "max_iterations",
        "max_llm_calls",
        "max_output_chars",
        "tools",
        "sub_lm",
        "interpreter",
    }
    missing = required - set(parameters)
    assert not missing, f"dspy.RLM missing constructor fields: {sorted(missing)}"


def test_fastapi_event_source_response_imports() -> None:
    """FastAPI SSE surface used by the clean kernel must import."""
    from fastapi.sse import EventSourceResponse

    assert EventSourceResponse is not None


def test_daytona_sdk_imports_without_client_construction() -> None:
    """Daytona SDK import must succeed without constructing a live client."""
    from daytona import Daytona

    assert Daytona is not None
    assert callable(Daytona)


def test_create_app_does_not_import_dspy_or_daytona() -> None:
    """The app factory must not import DSPy or Daytona at K-001."""
    app_path = Path(__file__).resolve().parents[3] / "src" / "fleet_rlm_clean" / "app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module.split(".", maxsplit=1)[0])
    assert "dspy" not in imported
    assert "daytona" not in imported
