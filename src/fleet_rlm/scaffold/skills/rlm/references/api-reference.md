# DaytonaInterpreter API Reference

## Constructor

```python
DaytonaInterpreter(
    *,
    runtime: DaytonaSandboxRuntime | None = None,  # Existing runtime (shared use)
    owns_runtime: bool = False,                     # Whether to shut down runtime on close
    timeout: int = 900,                             # Sandbox lifetime (seconds)
    execute_timeout: int | None = None,             # Per-execute() timeout (default: same as timeout)
    volume_name: str | None = None,                 # Durable volume name
    repo_url: str | None = None,                    # Repo to stage into sandbox
    repo_ref: str | None = None,                    # Branch/commit for repo staging
    context_paths: list[str] | None = None,         # Paths to stage from repo
    sandbox_spec: Any | None = None,                # Custom sandbox specification
    delete_session_on_shutdown: bool = True,         # Delete sandbox session on shutdown
    sub_lm: dspy.LM | None = None,                  # Sub-LM for recursive calls
    max_llm_calls: int = 50,                        # Max LLM sub-calls
    llm_call_timeout: int = 60,                     # Per LLM call timeout (seconds)
    default_execution_profile: ExecutionProfile = ExecutionProfile.RLM_DELEGATE,
    async_execute: bool = True,                     # Use async execution path
)
```

## Methods

| Method       | Signature                        | Returns              | Description                                       |
| ------------ | -------------------------------- | -------------------- | ------------------------------------------------- |
| `start()`    | `start() -> None`                | —                    | Create sandbox session, start driver (idempotent) |
| `execute()`  | `execute(code, variables=None)`  | `str \| FinalOutput` | Run code in sandbox (sync shim)                   |
| `aexecute()` | `aexecute(code, variables=None)` | `str \| FinalOutput` | Run code in sandbox (async-native)                |
| `shutdown()` | `shutdown() -> None`             | —                    | Terminate sandbox session (idempotent)            |

## Lifecycle

```python
from fleet_rlm.integrations.providers.daytona.interpreter import DaytonaInterpreter

interp = DaytonaInterpreter(
    repo_url="https://github.com/your-org/your-repo",
    timeout=600,
)
interp.start()
try:
    result = interp.execute("print('hello')\nSUBMIT(status='ok')")
    print(result.status)
finally:
    interp.shutdown()
```

## execute() Return Types

- **No SUBMIT**: Returns `str` (stdout + stderr)
- **With SUBMIT**: Returns `FinalOutput` — access fields as attributes:
  ```python
  result = interp.execute("SUBMIT(count=42, items=['a','b'])")
  print(result.count)   # 42
  print(result.items)   # ['a', 'b']
  ```

## Sandbox-Side Helpers

Injected automatically by the driver (`runtime/execution/sandbox_assets.py`),
available inside `interp.execute()` code:

| Helper             | Signature                                      | Returns                                    |
| ------------------ | ---------------------------------------------- | ------------------------------------------ |
| `peek`             | `peek(text, start=0, length=2000)`             | `str` — slice of text                      |
| `grep`             | `grep(text, pattern, *, context=0)`            | `list[str]` — matching lines               |
| `chunk_by_size`    | `chunk_by_size(text, size=4000, overlap=200)`  | `list[str]`                                |
| `chunk_by_headers` | `chunk_by_headers(text, pattern=r"^#{1,3}\s")` | `list[dict]` with keys `header`, `content` |
| `add_buffer`       | `add_buffer(name, value)`                      | `None` — append to named buffer            |
| `get_buffer`       | `get_buffer(name)`                             | `list` — buffer contents                   |
| `clear_buffer`     | `clear_buffer(name=None)`                      | `None` — clear one or all buffers          |
| `save_to_volume`   | `save_to_volume(path, content)`                | `str` — full path written                  |
| `load_from_volume` | `load_from_volume(path)`                       | `str` — file contents                      |
| `SUBMIT`           | `SUBMIT(**kwargs)`                             | Ends execution, returns structured output  |

## DSPy Signatures

Built-in signatures from `src/fleet_rlm/runtime/agent/signatures.py`:

| Signature                               | Inputs                                                 | Outputs                                                                                 |
| --------------------------------------- | ------------------------------------------------------ | --------------------------------------------------------------------------------------- |
| `RLMReActChatSignature`                 | `user_request, core_memory, history`                   | `assistant_response`                                                                    |
| `SummarizeLongDocument`                 | `document, focus`                                      | `summary, key_points, coverage_pct`                                                     |
| `ExtractFromLogs`                       | `logs, query`                                          | `matches, patterns, time_range`                                                         |
| `GroundedAnswerWithCitations`           | `query, evidence_chunks, response_style`               | `answer, citations, confidence, coverage_notes`                                         |
| `IncidentTriageFromLogs`                | `logs, service_context, query`                         | `severity, probable_root_causes, impacted_components, recommended_actions, time_range`  |
| `CodeChangePlan`                        | `task, repo_context, constraints`                      | `plan_steps, files_to_touch, validation_commands, risks`                                |
| `CoreMemoryUpdateProposal`              | `turn_history, current_memory`                         | `keep, update, remove, rationale`                                                       |
| `VolumeFileTreeSignature`               | `root_path, max_depth, include_hidden`                 | `nodes, total_files, total_dirs, truncated`                                             |
| `MemoryActionIntentSignature`           | `user_request, current_tree, policy_constraints`       | `action_type, target_paths, content_plan, risk_level, requires_confirmation, rationale` |
| `MemoryStructureAuditSignature`         | `tree_snapshot, usage_goals`                           | `issues, recommended_layout, naming_conventions, retention_rules, priority_fixes`       |
| `MemoryStructureMigrationPlanSignature` | `audit_findings, approved_constraints`                 | `operations, rollback_steps, verification_checks, estimated_risk`                       |
| `ClarificationQuestionSignature`        | `ambiguous_request, available_context, operation_risk` | `questions, blocking_unknowns, safe_default, proceed_without_answer`                    |
| `RecursiveSubQuerySignature`            | `prompt, context`                                      | `answer`                                                                                |
| `RLMVariableSignature`                  | `task, prompt`                                         | `answer`                                                                                |

## Volume Operations

Durable volume is mounted at `/home/daytona/memory/` inside the sandbox.

### Read from Volume Inside Sandbox

```python
result = interp.execute("""
import pathlib
doc = pathlib.Path('/home/daytona/memory/artifacts/report.txt').read_text()
print(f'Loaded {len(doc):,} chars from volume')
""")
```

### Write to Volume

```python
interp.execute("""
import json, os
os.makedirs('/home/daytona/memory/artifacts', exist_ok=True)
with open('/home/daytona/memory/artifacts/result.json', 'w') as f:
    json.dump({"answer": "extracted-text"}, f)
""")
```

## Troubleshooting

| Issue                         | Fix                                                  |
| ----------------------------- | ---------------------------------------------------- | ------------------------------------------------ |
| "Planner LM not configured"   | Set `DSPY_LM_MODEL` and `DSPY_LLM_API_KEY` in `.env` |
| "Daytona sandbox failed"      | Run `env                                             | grep DAYTONA`and`uv run fleet-rlm daytona-smoke` |
| Timeout errors                | Increase `timeout=` (Python) or `--timeout` (CLI)    |
| Volume not persisting         | Use the same `volume_name` across sessions           |
| `FinalOutput` attribute error | Access fields as `.field`, not `['field']`           |
