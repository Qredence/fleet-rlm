# Project: Fleet RLM Refactoring and Modernization

## Architecture
Fleet RLM is a streamlined, high-performance Recursive Language Model (RLM) platform centered on native `dspy.RLM` (DSPy 3.3.1), direct `AsyncDaytona` sandboxing, and full MLflow 3 GenAI evaluation, collapsing ~62k LOC of accidental complexity down to a clean, maintainable architecture.

### Primary Subsystems
1. **Direct Daytona Sandboxing (`src/fleet_rlm/daytona/`)**:
   - Replaces the legacy in-sandbox HTTP broker (`broker.py`), remote agent (`workspace_agent/`), and tangled lease state machines (`session_manager.py`, `sandbox_lease.py`) with a direct, clean `AsyncDaytona` client.
   - Root Sandboxes: Session-scoped, with persistent `/workspace` volume mount (`workspaces/<workspace_id>`).
   - Child Sandboxes: Ephemeral scratch environments, with `network_block_all=True` and no volume mounts.
   - Code Execution: Direct via `sandbox.process.exec` / `sandbox.code_interpreter.run_code` with standard `SUBMIT()` preamble and `__FLEET_FINAL_OUTPUT__` extraction.
   - Filesystem Operations: Direct native `sandbox.fs` APIs (`upload_file`, `download_file`, `list_files`, `delete_file`).
   - Lifecycle: Strict wall-clock execution timeouts and leak-free `finally: await sandbox.delete()`.

2. **Native DSPy 3.3.1 RLM Reasoning Core (`src/fleet_rlm/rlm/`)**:
   - Native `dspy.RLM` with process-scoped immutable `dspy.LM` templates.
   - STRICT RULE: `dspy.CodeAct` is prohibited and eliminated.
   - Context Staging: Large/messy contexts bypass LLM prompt limits via `AttachmentContextCapsule(dspy.SandboxSerializable)` and volume mounts, passing concise summaries to prompt while exposing files to sandbox REPL.
   - Semantic Extraction: Native sub-LM querying (`llm_query`, `llm_query_batched`) bound to `sub_lm`.
   - Child Delegation: `rlm_query` child sandbox delegation strictly capped at depth 1 (children cannot recurse).
   - REPL Semantics: Native DSPy `REPLHistory` and trajectory semantics preserved without manual compaction or monkey-patching.

3. **MLflow 3 GenAI Observability & Evaluation (`src/fleet_rlm/observability/`, `src/fleet_rlm/optimization/`)**:
   - OpenTelemetry tracing with spans for root turns (`fleet_turn`), REPL iterations, sub-LM queries, and child sandbox RLMs.
   - Fail-soft telemetry lifecycle ensuring tracing failures never compromise turn execution.
   - Custom `@scorer` evaluators: `rlm_groundedness_scorer`, `rlm_context_efficiency_scorer`, `rlm_task_correctness_scorer`.
   - LLM-as-a-judge scorers for trajectory and answer verification.
   - Automated evaluation runner via `mlflow.genai.evaluate`.

4. **FastAPI Transport & TUI SSE Streaming (`src/fleet_rlm/api/`)**:
   - Sessions CRUD (`/api/sessions`), turn execution (`POST /api/sessions/{id}/turns`), file management, health checks.
   - Strict adherence to `x-vercel-ai-ui-message-stream: v1` SSE protocol (24 discriminated chunk models).
   - Immediate transient prelude heartbeat (`data-status`, phase=preparation).
   - Full wire compatibility with `tools/fleet-tui` (supporting snake_case and camelCase alternatives).

5. **Code Quality & Tooling Standards**:
   - Package management: exclusively `uv` (`uv add`, `uv run`).
   - Formatting and linting: `ruff check`, `ruff format`.
   - Static type checking: `ty check src`.

---

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Direct AsyncDaytona Client | Replace broker HTTP server and remote workspace agent with direct SDK calls | M1 | R1 |
| 2 | Root Sandbox Volume Mounts | Session-scoped sandboxes with `/workspace` volume mount | M1 | R1 |
| 3 | Ephemeral Child Sandboxes | Query-scoped child sandboxes with `network_block_all=True` and no volumes | M1 | R1 |
| 4 | Direct Process Execution | Python execution via `sandbox.process.exec` / `sandbox.code_interpreter.run_code` | M1 | R1 |
| 5 | Native Sandbox FS Operations | Filesystem operations via `sandbox.fs` APIs | M1 | R1 |
| 6 | Wall-Clock Timeouts & Cleanup | Leak-free sandbox lifecycle management and timeout enforcement | M1 | R1 |
| 7 | Collapse Daytona LOC | Reduce `daytona/` from 14.6k LOC to ~1.2k LOC (~92% reduction) | M1 | R1 |
| 8 | Native dspy.RLM Core | Center reasoning around native `dspy.RLM` without dynamic prompt hacks | M2 | R2 |
| 9 | Large Context Staging | Volume / memory slice staging via `AttachmentContextCapsule` | M2 | R2 |
| 10 | Native Sub-LM Querying | Semantic extraction via built-in `llm_query` bound to `sub_lm` | M2 | R2 |
| 11 | Bounded Child RLM Delegation | Child delegation via `rlm_query` strictly bounded at depth 1 | M2 | R2 |
| 12 | Zero CodeAct Invariant | Total absence of `dspy.CodeAct` in codebase | M2 | R2 |
| 13 | Native REPLHistory Semantics | Uncompacted, unpatched native trajectory semantics | M2 | R2 |
| 14 | OpenTelemetry Tracing | Turn spans (`fleet_turn`), REPL iteration spans, and tool query spans | M3 | R3 |
| 15 | Custom MLflow Scorers | `@scorer` functions: `rlm_groundedness`, `rlm_context_efficiency`, `rlm_task_correctness` | M3 | R3 |
| 16 | LLM Judges Verification | LLM judges (`make_judge`) for trajectory and answer verification | M3 | R3 |
| 17 | Automated Evaluation Runner | Automated eval suite using `mlflow.genai.evaluate` | M3 | R3 |
| 18 | Idiomatic FastAPI Endpoints | Sessions CRUD, turn creation, files, volume endpoints | M4 | R4 |
| 19 | Vercel AI SSE Streaming | Compliant `x-vercel-ai-ui-message-stream: v1` stream (24 chunk types) | M4 | R4 |
| 20 | TUI Client Compatibility | Passing `make api-check`, `make stream-check`, `make tui-check` | M4 | R4 |
| 21 | Quality & Type Enforcement | `uv run ruff check`, `uv run ruff format --check`, `uv run ty check src` | M5 | R5 |
| 22 | End-to-End Suite Hardening | Passing fast/unit/contract tests via `uv run pytest -q` | M5 | R5 |

---

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Phase 1: Direct Daytona SDK Integration | Direct AsyncDaytona client, root/child sandboxes, process execution, fs ops, broker collapse | none | DONE |
| M2 | Phase 2: Native DSPy 3.3.1 RLM Core | Native `dspy.RLM`, context staging capsules, `llm_query`, depth-1 `rlm_query`, zero CodeAct | M1 | DONE |
| M3 | Phase 3: MLflow 3 GenAI Observability | OTel tracing spans, `@scorer` suite (groundedness, efficiency, correctness), LLM judges | M2 | DONE |
| M4 | Phase 4: FastAPI & TUI SSE Transport | Clean FastAPI routes, Vercel AI UI message stream SSE, TUI contracts | M1, M2 | DONE |
| M5 | Phase 5: Verification, Types & Hardening | Final verification, ruff formatting/linting, ty type-check, full test suite pass | M1, M2, M3, M4 | DONE |

---

## Interface Contracts

### Daytona Code Execution Contract (`src/fleet_rlm/daytona/`)
```python
class DaytonaExecutionBackend(Protocol):
    async def execute_code(
        self,
        code: str,
        *,
        timeout: float = 60.0,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute python code in Daytona sandbox and extract stdout, stderr, exit_code, and SUBMIT output."""
        ...

    async def read_file(self, path: str) -> bytes: ...
    async def write_file(self, path: str, data: bytes) -> None: ...
    async def delete_sandbox(self) -> None: ...
```

### RLM Execution Contract (`src/fleet_rlm/rlm/`)
```python
def build_native_rlm(
    signature: type[dspy.Signature],
    *,
    sub_lm: dspy.LM,
    tools: list[Callable],
    max_iters: int = 10,
    max_llm_calls: int = 15,
    max_output_chars: int = 8000,
) -> dspy.RLM:
    """Builds standard native DSPy RLM without monkey-patching or CodeAct."""
    ...
```

### MLflow Scorer Signatures (`src/fleet_rlm/optimization/`, `scripts/benchmarks/`)
```python
@scorer
def rlm_groundedness_scorer(inputs: dict[str, Any], outputs: dict[str, Any]) -> Score: ...

@scorer
def rlm_context_efficiency_scorer(inputs: dict[str, Any], outputs: dict[str, Any]) -> Score: ...

@scorer
def rlm_task_correctness_scorer(inputs: dict[str, Any], outputs: dict[str, Any], expectations: dict[str, Any]) -> Score: ...
```

### SSE Stream Contract (`src/fleet_rlm/api/`)
- HTTP Header: `x-vercel-ai-ui-message-stream: v1`
- Immediate prelude: `data-status` with `phase="preparation"`, `status="running"`
- Runtime chunks: `reasoning-*`, `tool-*`, `text-*`, `data-*`, `finish`, `error`, `abort`

---

## Code Layout
```
src/fleet_rlm/
├── api/                   # FastAPI routes, schemas, and SSE streamers
│   ├── routes/            # Endpoints (turns, sessions, artifacts, etc.)
│   ├── sse.py             # Event source streaming & chunk projection
│   └── ui_stream.py       # Vercel AI SDK UI chunk models
├── chat/                  # Turn preparation and lifecycle coordination
├── composition/           # Dependency injection and runtime graph setup
├── daytona/               # Direct AsyncDaytona SDK integration (collapsed)
│   ├── client.py          # Daytona async client lifecycle
│   ├── interpreter.py     # DSPy CodeInterpreter adapter
│   ├── models.py          # ExecutionResult, SandboxLease models
│   ├── session_manager.py # Lightweight session & volume manager
│   └── errors.py          # Daytona domain errors
├── observability/         # MLflow tracing, OpenTelemetry, metrics
├── optimization/          # MLflow scorers, evaluation datasets, judges
├── rlm/                   # DSPy 3.3.1 native RLM core
│   ├── program.py         # Native dspy.RLM builder
│   ├── recursion.py       # Depth-1 child delegation
│   ├── result.py          # Trajectory and outcome models
│   └── compat_3_3_1.py    # DSPy 3.3.1 certifications
└── sessions/              # Durable session state & models
```
