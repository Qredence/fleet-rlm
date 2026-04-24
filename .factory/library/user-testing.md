# User Testing

## Validation Surface
Backend-only. No browser/UI testing needed.

**Primary validation:** `make test` (pytest excluding live_llm and benchmark)
**Secondary:** `make lint`, `make typecheck`
**Milestone-specific lanes:**
- Daytona upgrade: `uv run pytest tests/unit/integrations/daytona/ -q`
- Daytona focused: `uv run pytest -q tests/unit/integrations/daytona/test_config.py tests/unit/integrations/daytona/test_smoke.py tests/unit/integrations/daytona/test_runtime.py tests/unit/integrations/daytona/test_interpreter.py tests/unit/runtime/agent/test_agent.py`
- Observability: `uv run pytest tests/unit/integrations/observability/ -q`
- Database integration: `uv run pytest tests/integration/test_db_repository.py -v`

## Validation Concurrency
Not applicable — no concurrent browser/CLI validators needed.
All validation is via pytest which handles its own parallelism.

## Testing Tools
- pytest with pytest-asyncio
- rg (ripgrep) for import checking
- make commands for lint/typecheck
- Subprocess-isolated Python probes for import-time side effect verification (never use `importlib.reload`)
- AST static analysis for banned top-level import regression guards

## Import-Time Side Effect Testing

**Required approach:** Spawn a fresh CPython subprocess for every import probe.

Example subprocess probe:
```python
import subprocess, sys

def _assert_import_does_not_load(module_name: str, banned: set[str]) -> None:
    code = (
        f"import sys, {module_name}; "
        f"loaded = {{m for m in sys.modules if any(m.startswith(b) for b in {banned!r})}}; "
        f"assert not loaded, f'Importing {module_name} loaded: {{loaded}}'"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
```

**Banned top-level imports:** `dspy`, `mlflow`, `posthog`, `litellm`, `daytona`

**Allow-listed modules** (where these imports are permitted):
- `src/fleet_rlm/runtime/agent/*.py`
- `src/fleet_rlm/runtime/models/*.py`
- `src/fleet_rlm/runtime/quality/*.py`

## Daytona SDK 0.168.0 Notes
- Package name: `daytona` on PyPI (not `daytona_sdk`)
- **BREAKING CHANGE:** Python SDK enforces snake_case for all class attributes. Previously camelCase was accepted.
- Key imports: `AsyncDaytona`, `DaytonaConfig`, `CreateSandboxFromImageParams`, `CreateSandboxFromSnapshotParams`, `Resources`, `VolumeMount`
- Current codebase already uses snake_case kwargs; verify no camelCase attribute access patterns
- If API drift found, prefer defensive `try/except` or compatibility shims
