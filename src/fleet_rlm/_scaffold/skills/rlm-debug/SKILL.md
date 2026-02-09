---
name: rlm-debug
description: Debug RLM execution, inspect sandbox state, and troubleshoot failures. Use when diagnosing issues with Modal sandboxes, credential problems, or RLM task errors.
---

# RLM Debug — Sandbox Diagnostics

Diagnose issues with Modal sandboxes, RLM execution failures, and credential
problems.

> **Skill + Subagent synergy**: This skill is loaded by the `rlm-specialist` and
> `modal-interpreter-agent` subagents. Use the skill directly for inline debugging,
> or delegate to those agents for isolated diagnostic sessions.

## Live Environment Status

Current Modal environment (auto-detected):

- Modal version: !`uv run python -c "import modal; print(modal.__version__)" 2>&1`
- Secret check: !`uv run fleet-rlm check-secret 2>&1 | head -5`
- Active sandboxes: !`uv run modal sandbox list 2>&1 | head -10`
- Volumes: !`uv run modal volume list 2>&1 | head -10`

## Run Full Diagnostics

For a comprehensive check, run the bundled diagnostic script:

```bash
uv run python .claude/skills/rlm-debug/scripts/diagnose.py
```

Or the project-level validator:

```bash
uv run python scripts/validate_rlm_env.py
```

## Manual Checks

```bash
# Check LITELLM secret keys
uv run fleet-rlm check-secret
uv run fleet-rlm check-secret-key --key DSPY_LLM_API_KEY

# List active Modal apps
uv run modal app list

# List volumes
uv run modal volume list
```

---

## Common Issues

### "Planner LM not configured"

Set `DSPY_LM_MODEL` and `DSPY_LLM_API_KEY` in `.env` at project root.

### "Modal sandbox process exited unexpectedly"

```bash
uv run modal token set
uv run modal volume list
```

Check stderr output — the `ModalInterpreter` redacts sensitive data but
shows the actual error.

### FinalOutput AttributeError

```python
# WRONG — treating as dict
result = interp.execute("SUBMIT(status='ok')")
status = result['status']  # AttributeError!

# CORRECT — attribute access
result = interp.execute("SUBMIT(status='ok')")
status = result.status
```

### Timeout Errors

Increase timeout:

```python
interp = ModalInterpreter(timeout=900)  # 15 minutes
```

Or via CLI:

```bash
uv run fleet-rlm run-long-context --timeout 900 ...
```

### Volume Not Persisting

Ensure you use the same `volume_name` across sessions:

```python
# Session 1
with ModalInterpreter(volume_name='rlm-volume-dspy') as interp:
    interp.execute("save_to_volume('test.txt', 'hello')")

# Session 2 — same volume_name
with ModalInterpreter(volume_name='rlm-volume-dspy') as interp:
    result = interp.execute("print(load_from_volume('test.txt'))")
    print(result)  # 'hello'
```

### Credential Issues

```bash
# Check ~/.modal.toml
cat ~/.modal.toml

# Re-authenticate
uv run modal token set

# Check environment
env | grep MODAL_TOKEN
```

---

## Inspect Sandbox State

```python
from fleet_rlm import ModalInterpreter

with ModalInterpreter(timeout=60, volume_name='rlm-volume-dspy') as interp:
    result = interp.execute(
        "import os, sys\n"
        "SUBMIT(\n"
        "    python=sys.version,\n"
        "    cwd=os.getcwd(),\n"
        "    data_exists=os.path.exists('/data'),\n"
        "    data_contents=os.listdir('/data') if os.path.exists('/data') else [],\n"
        "    env_keys=[k for k in os.environ if 'DSPY' in k or 'MODAL' in k],\n"
        ")"
    )
    print(f"Python: {result.python}")
    print(f"Volume mounted: {result.data_exists}")
    print(f"Volume contents: {result.data_contents}")
    print(f"Env vars: {result.env_keys}")
```

---

## Test Sandbox Creation

```python
from fleet_rlm import ModalInterpreter

with ModalInterpreter(timeout=30) as interp:
    result = interp.execute("SUBMIT(status='healthy', msg='Sandbox works')")
    print(f"Status: {result.status}")  # 'healthy'
```

---

## Run Test Suite

```bash
# All tests
uv run pytest tests/

# Specific test files
uv run pytest tests/test_driver_protocol.py -v
uv run pytest tests/test_context_manager.py -v
uv run pytest tests/test_volume_support.py -v
```
